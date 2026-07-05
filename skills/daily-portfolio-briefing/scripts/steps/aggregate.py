"""
Step 8: Aggregate and render

Combines all step outputs into final markdown and JSON.
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from render.panels import (
    render_header,
    render_market_context,
    render_health,
    render_risk_alerts,
    render_action_list,
    render_watch,
    render_opportunities,
    render_diffs,
    render_inconsistencies,
    render_manifest,
)
from render.stress_test_panel import render_stress_test, render_stress_test_details
from render.expiration_panel import render_expiration_clusters
from render.hedge_book_panel import render_hedge_book
from render.strategy_upgrades_panel import render_strategy_upgrades
from render.analyst_brief import render_analyst_brief
from steps.compute_analytics import compute_analytics
from steps.per_option_commentary import render_watch_with_commentary
from steps.strategy_upgrades import compute_strategy_upgrades
from analysis.briefing_diff import render_diff_panel, load_yesterday_briefing

# Wire in the briefing-quality-gate skill (deterministic structural validator)
_GATE_PATH = Path(__file__).resolve().parents[3] / "briefing-quality-gate" / "scripts"
sys.path.insert(0, str(_GATE_PATH))
try:
    from run_quality_gate import gate_and_render  # type: ignore
except ImportError:
    def gate_and_render(md: str) -> str:
        return md  # no-op fallback

# Wire in the pre-flight-verifier (master gate orchestrator)
_PREFLIGHT_PATH = Path(__file__).resolve().parents[3] / "pre-flight-verifier" / "scripts"
sys.path.insert(0, str(_PREFLIGHT_PATH))
try:
    from preflight import run_pre_flight  # type: ignore
except ImportError:
    def run_pre_flight(md, snapshot_positions=None, broker_positions=None, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(verdict="RELEASE", rendered_briefing=md,
                               consolidated_panel_md="", gate_results={})


def _run_fable_review_cascade(
    briefing_markdown: str,
    snapshot_dir,
    config: dict | None,
) -> list[str]:
    """LLM second-opinion pass — cascades v2 (advisor) → v1 (critic).

    Runs AFTER all deterministic rules have shaped the briefing.
    Observations only, no trade recommendations. Fail-open: any API
    error is silently caught, briefing still ships.

    Two flavors, cascading:

      1. v2 advisor (task #54, fable_advisor.py) — persona + memory.
         Preferred when briefing.yaml → fable_advisor.enabled: true.
         Reads state/fable_advisor_memory.md for continuity.
      2. v1 review (task #51, fable_review.py) — locked-down critic,
         no memory. Fallback when v2 is disabled OR errors (belt and
         suspenders — TDD hard rule #33).

    Returns the review markdown lines ready to append (empty list when
    both disabled or both errored).
    """
    review_lines: list[str] = []
    v2_used = False

    # Attempt v2 first if enabled
    v2_cfg = ((config or {}).get("fable_advisor") or {})
    if v2_cfg.get("enabled"):
        try:
            from analysis import fable_advisor as _fa
            fa_result = _fa.generate_advisor_review(
                briefing_markdown, snapshot_dir, config=config,
            )
            if fa_result.get("status") == "ok":
                review_lines = _fa.render_review_section(fa_result)
                v2_used = True
            else:
                # v2 ran but didn't produce a usable review — fall
                # through to v1 (no key, empty briefing, api error).
                print(f"[aggregate] fable advisor v2 status="
                      f"{fa_result.get('status')}, falling back to v1",
                      file=sys.stderr)
        except Exception as e:
            print(f"[aggregate] fable advisor v2 raised (falling back to v1): {e}",
                  file=sys.stderr)

    # v1 fallback (also default when v2 disabled)
    if not v2_used:
        try:
            from analysis import fable_review as _fr
            fr_result = _fr.generate_review(
                briefing_markdown, snapshot_dir, config=config,
            )
            review_lines = _fr.render_review_section(fr_result)
        except Exception as e:
            print(f"[aggregate] fable review v1 failed (non-fatal): {e}",
                  file=sys.stderr)

    return review_lines


def aggregate_briefing(
    date_str: str,
    config: dict,
    snapshot_data: dict,
    regime_data: dict,
    equity_reviews: list,
    options_reviews: list,
    new_ideas: list,
    consistency_report: dict,
    flagged_inconsistencies: list,
    directives_active: list,
    directives_expired: list,
    snapshot_dir: Path,
    long_term_opportunities: list | None = None,
    capital_plan: dict | None = None,
    scout_payload: dict | None = None,
    gate_state=None,
    aging_info: dict | None = None,
) -> tuple:
    """
    Aggregate all step outputs into final briefing markdown and JSON.

    Returns:
        (briefing_markdown_str, briefing_json_dict)
    """

    balance = snapshot_data.get("balance", {})
    quotes = snapshot_data.get("quotes", {})
    ytd_pnl = snapshot_data.get("ytd_pnl", {})
    nlv = balance.get("accountValue", 0)
    cash = balance.get("cash", 0)

    regime = regime_data.get("regime", "UNKNOWN")
    confidence = regime_data.get("confidence", "MEDIUM")
    triggered = regime_data.get("triggered_rules") or []
    regime_rationale = triggered[0].get("rationale", "") if triggered else ""

    # Compute analytics: stress coverage, concentration, expirations, hedges
    macro_caution = "high" if regime in ("CAUTION", "RISK_OFF") else "none"
    analytics = compute_analytics(snapshot_data, config, macro_caution=macro_caution)

    # Make config visible to render layer (used for core_positions/ltcg_rate/etc.)
    snapshot_data["_config"] = config

    # Generate action list to count items (must happen before header render).
    # Step 7.5: aging_info (fill reconciliation + recommendation aging) is
    # threaded through render_action_list, which mutates it in place with
    # "aged" / "updated_state" / "actions_export".
    action_list_lines = render_action_list(equity_reviews, options_reviews, new_ideas, analytics, snapshot_data, date_str=date_str, aging_info=aging_info)
    # Count actual numbered items in action list (lines starting with "N."; strip whitespace first)
    action_count = sum(1 for line in action_list_lines if line and line.lstrip() and line.lstrip()[0].isdigit() and "." in line.lstrip()[:5])

    # Build markdown
    lines = []
    # Step 7.5: stalled items (≥6 consecutive IGNORED days) render at the VERY
    # TOP — above the header — so they're the first thing the operator sees.
    if aging_info and aging_info.get("aged"):
        try:
            from analysis.rec_aging import render_stalled_panel
            lines.extend(render_stalled_panel(aging_info["aged"]))
        except Exception as _stall_e:
            import sys as _sys
            print(f"[aggregate] stalled panel failed: {_stall_e}", file=_sys.stderr)
    lines.extend(render_header(date_str, regime, nlv, cash, action_count, confidence, regime_rationale, ytd_pnl, gate_state=gate_state))
    lines.extend(render_market_context(regime_data, quotes))
    # Pass option positions (with real Greeks from E*TRADE) for the net-Greeks aggregate.
    # If theta is missing on positions, estimate it from current_mid + days_to_expiry as a
    # last-resort proxy (theta ≈ -mid / dte for short-dated options) so the briefing surfaces
    # a non-zero theta number rather than $0.
    options_positions = []
    for p in (snapshot_data.get("positions") or []):
        if p.get("assetType") != "OPTION":
            continue
        # Estimate theta if not provided
        if p.get("theta") is None:
            mid = p.get("currentMid") or p.get("current_price") or 0
            dte = p.get("days_to_expiry") or 30
            try:
                exp = p.get("expiration")
                if exp and not p.get("days_to_expiry"):
                    from datetime import datetime as _dt, date as _d
                    exp_d = _dt.strptime(exp, "%Y-%m-%d").date() if isinstance(exp, str) else exp
                    if isinstance(exp_d, _d):
                        dte = max(1, (exp_d - _d.today()).days)
            except (ValueError, TypeError):
                pass
            if mid and dte > 0:
                # Per-share theta (negative, decay): roughly -mid / dte for short-dated
                p["theta"] = -float(mid) / max(dte, 1)
        options_positions.append(p)
    lines.extend(render_health(equity_reviews, nlv, options_positions))

    # NEW: Render stress test panel
    lines.extend(render_stress_test(analytics["stress_coverage"], analytics["nlv"]))

    # NEW: Render stress test details (which positions get assigned)
    all_positions = snapshot_data.get("positions", [])
    lines.extend(render_stress_test_details(analytics["stress_coverage"], all_positions))

    # NEW: Render expiration clusters
    lines.extend(render_expiration_clusters(analytics["expirations"]))

    # NEW: Render hedge book
    lines.extend(render_hedge_book(analytics["hedge_book"], analytics["nlv"], analytics["spy_price"]))

    lines.extend(render_risk_alerts(
        equity_reviews, options_reviews, regime_data,
        put_buckets=analytics.get("put_buckets") or [],
        config=config,
    ))

    # NEW (task #16): Benchmark & Attribution — am I beating SPY, and where
    # did the return come from? Reads NLV history from prior snapshot dirs +
    # SPY via the OHLC cache. Fail-open: ANY exception here must never crash
    # the briefing — the panel is simply omitted and the JSON key stays {}.
    benchmark_report_json: dict = {}
    try:
        bt_cfg = (config.get("benchmark_tracking") or {}) if isinstance(config, dict) else {}
        if bt_cfg.get("enabled", True):
            from analysis import benchmark_tracker as _bt
            from analysis import pnl_attribution as _pa
            from render.benchmark_panel import render_benchmark_panel
            _bt_root = snapshot_dir.parent if snapshot_dir else Path("state/briefing_snapshots")
            _bench = _bt.compute_benchmark_from_root(
                current_nlv=nlv, snapshot_root=_bt_root,
                as_of=date_str, config=bt_cfg,
            )
            # Reuse the benchmark closes for attribution's cash-drag math
            # (one fetch per cycle, served from the OHLC cache).
            _spy_closes = _bt.fetch_benchmark_closes(
                str(bt_cfg.get("benchmark_ticker") or "SPY"), days=400)
            _attrib = _pa.build_attribution_report(
                snapshot_root=_bt_root, as_of=date_str, spy_closes=_spy_closes,
            )
            lines.extend(render_benchmark_panel(_bench, _attrib))
            benchmark_report_json = {
                "benchmark": _bench.to_dict(),
                "attribution": _attrib.to_dict(),
            }
    except Exception as _bte:
        import sys as _sys
        print(f"[aggregate] benchmark panel failed (non-fatal): {_bte}", file=_sys.stderr)

    # Open-orders audit — every pending GTC order on E*TRADE gets run through
    # the pre-trade validator so stale or rule-violating orders surface BEFORE
    # they fill. Motivating case: MU $960P Aug 21 SELL_OPEN @ $154 GTC that
    # would have triggered 3 discipline rules.
    try:
        from analysis import open_orders_audit as _ooa
        audits = _ooa.audit_open_orders(
            snapshot_data, config=config,
            analytics=analytics,
            recommendations_list=snapshot_data.get("recommendations_list"),
        )
        if audits:
            lines.extend(_ooa.render_audit_panel(audits))
    except Exception as _ooe:
        import sys as _sys
        print(f"[aggregate] open orders audit failed: {_ooe}", file=_sys.stderr)

    lines.extend(action_list_lines)
    
    # MODIFIED: Use render_watch_with_commentary instead of plain render_watch
    lines.extend(render_watch_with_commentary(equity_reviews, options_reviews, snapshot_data))

    lines.extend(render_opportunities(new_ideas))

    # NEW (Wave 22): Long-term opportunities — ADD/TRIM/EXIT/HOLD + LEAPs + long-dated CSPs
    if long_term_opportunities:
        from steps.long_term_opportunities import render_long_term_opportunities
        lines.extend(render_long_term_opportunities(long_term_opportunities))

    # NEW (Wave 26): Thematic Scout — research across themes (semis/nuclear/etc)
    if scout_payload:
        from steps.thematic_research import render_scout_section
        # Market Pulse + theme-by-theme read only — the actionable picks are
        # carried by the richer Candidate Trades section that follows.
        lines.extend(render_scout_section(scout_payload, max_per_theme=4, config=config,
                                          include_shortlist=False))
        # Candidate Trades — actionable candidates (+ on-deck) embedded inline.
        try:
            import os as _os_cb
            from steps import candidate_research as _cr_cb
            from analysis import intrinsic_value as _iv_cb
            _cand_tks = _cr_cb.briefing_candidate_tickers(scout_payload, config)
            _cand_fv: dict = {}
            _fmp_cb = _os_cb.getenv("FMP_API_KEY")
            if _cand_tks and _fmp_cb:
                _cand_fv = _iv_cb.get_fair_values(
                    _cand_tks,
                    cache_path=(snapshot_dir.parent if snapshot_dir else Path("state/briefing_snapshots"))
                    / "intrinsic_value_cache.json",
                    api_key=_fmp_cb,
                )
            # Position-aware: cross-check candidates against the user's CURRENT
            # short puts AND long puts (live snapshot), so we never recommend a
            # contract that's already open (the LITE $820P duplicate bug) AND
            # never recommend a short put that cancels a protective long put
            # (the META $570P collar-floor bug). Independent of the 24h scout
            # cache.
            _esp_cb = _cr_cb.short_puts_by_ticker(snapshot_data.get("positions", []))
            _elp_cb = _cr_cb.long_puts_by_ticker(snapshot_data.get("positions", []))
            _cand_section = _cr_cb.render_candidate_briefing(
                scout_payload, fv_by_ticker=_cand_fv, config=config,
                generated_at=date_str, as_section=True,
                existing_short_puts=_esp_cb,
                existing_long_puts=_elp_cb,
                gate_state=gate_state,
                # NEW: thread snapshot + analytics so each candidate gets a
                # pre-trade validation pass (earnings window, bucket impact,
                # roll-from-safer, etc.) inline below its entry ticket.
                snapshot_data=snapshot_data,
                analytics=analytics,
                recommendations_list=snapshot_data.get("recommendations_list"),
            )
            lines.extend(_cand_section.splitlines())
        except Exception as _cbe:
            import sys as _sys
            print(f"[aggregate] candidate section failed: {_cbe}", file=_sys.stderr)

    # NEW: Technical Read + Per-Ticker Actions — deep per-ticker technical
    # cards (BB/MACD/ATR/SMA-slopes/52w/ATH + ST/LT verdicts) for every
    # holding + candidate, followed by the entry/exit recommender's calls
    # (EXIT_URGENT → TRIM → ENTRY_STRONG → ENTRY_WATCH; HOLD silent).
    # Driven by snapshot technicals[tk]["deep"] computed this cycle;
    # fail-closed per ticker. Config: briefing.yaml → technical_analysis.
    try:
        _ta_cfg = (config.get("technical_analysis") or {}) if isinstance(config, dict) else {}
        if _ta_cfg.get("enabled", True):
            from steps.technical_read import render_technical_read_sections
            lines.extend(render_technical_read_sections(
                snapshot_data, config,
                scout_payload=scout_payload,
                gate_state=gate_state,
                recommendations_list=snapshot_data.get("recommendations_list"),
            ))
    except Exception as _tre:
        import sys as _sys
        print(f"[aggregate] technical read section failed: {_tre}", file=_sys.stderr)

    # NEW (Wave 24): Capital plan — re-run capital-planner here with full
    # analytics (hedge_book + stress coverage) AND the rendered action list
    # so it can extract closes/rolls/hedges/CSPs/trims as authored by the
    # renderer. This supersedes the upstream pass in run_briefing.py.
    try:
        from steps.capital_plan import build_capital_plan_step
        full_capital_plan = build_capital_plan_step(
            balance=snapshot_data.get("balance", {}),
            positions=snapshot_data.get("positions", []),
            equity_reviews=equity_reviews,
            options_reviews=options_reviews,
            new_ideas=new_ideas,
            long_term_opportunities=long_term_opportunities or [],
            analytics=analytics,
            recommendations_list=snapshot_data.get("recommendations_list"),
            action_list_lines=action_list_lines,
        )
    except Exception as e:
        import sys as _sys, traceback as _tb
        print(f"[aggregate] capital plan re-run failed: {e}", file=_sys.stderr)
        _tb.print_exc()
        full_capital_plan = capital_plan
    if full_capital_plan and full_capital_plan.get("markdown"):
        lines.extend(full_capital_plan["markdown"])

    # NEW (Wave 30): Red Flags & Priorities synthesis — cross-cutting risks
    # the user should think about, in plain English with "Do" / "Don't" lists.
    try:
        from steps.red_flags import compute_red_flags, render_red_flags_md
        red_flags = compute_red_flags(
            snapshot_data=snapshot_data,
            analytics=analytics,
            options_reviews=options_reviews,
            equity_reviews=equity_reviews,
            capital_plan=full_capital_plan,
            recommendations_list=snapshot_data.get("recommendations_list"),
        )
        lines.extend(render_red_flags_md(red_flags))
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] red-flag synthesis failed: {_e}", file=_sys.stderr)

    # NEW: Add Strategy Upgrades panel
    upgrades = compute_strategy_upgrades(snapshot_data, equity_reviews, options_reviews, config, analytics=analytics)
    lines.extend(render_strategy_upgrades(upgrades))

    # NEW: Add Analyst Brief before diffs
    lines.extend(render_analyst_brief(equity_reviews, options_reviews, snapshot_data, analytics, regime_data))
    
    lines.extend(render_diffs(consistency_report))

    # Support/Resistance annotation pass — append an "S: $X · R: $Y" note to
    # every actionable single-stock line that doesn't already carry one. Merges
    # snapshot technicals (held names) WITH the scout cache (all theme
    # companies) so every actionable ticker in the briefing gets a level read,
    # not just held positions. Fail-closed when SR is missing for a ticker.
    try:
        from analysis import support_resistance as _sr_mod
        sr_by_sym: dict = {}
        # Scout cache first (lower priority — scout fills in non-held tickers).
        if scout_payload:
            for _theme_key, _results in (scout_payload.get("results_by_theme") or {}).items():
                for _r in (_results or []):
                    if not isinstance(_r, dict):
                        continue
                    _tk_s = (_r.get("ticker") or "").upper()
                    _sr_s = _r.get("support_resistance")
                    if _tk_s and isinstance(_sr_s, dict):
                        sr_by_sym[_tk_s] = _sr_s
        # Snapshot technicals second (higher priority — freshest for held names).
        tech_for_sr = snapshot_data.get("technicals") or {}
        for _sym, _t in tech_for_sr.items():
            if isinstance(_t, dict) and isinstance(_t.get("support_resistance"), dict):
                sr_by_sym[str(_sym).upper()] = _t["support_resistance"]
        if sr_by_sym:
            md_text = "\n".join(lines)
            annotated = _sr_mod.annotate_briefing(md_text, sr_by_sym)
            lines = annotated.split("\n")
            _sr_stats = _sr_mod.coverage_stats(annotated, sr_by_sym)
            lines.append("## 📐 Support / Resistance Coverage")
            lines.append("")
            lines.append(
                f"_S/R annotation: {_sr_stats['annotated']} actionable line(s) carry a level read · "
                f"{len(_sr_stats['covered_tickers'])} ticker(s) covered._"
            )
            if _sr_stats["missing"]:
                lines.append(
                    f"_⚠️ {len(_sr_stats['missing'])} actionable line(s) had S/R data but no read was rendered "
                    f"(annotation may not match this surface — file a bug):_"
                )
                for _m in _sr_stats["missing"][:5]:
                    lines.append(f"- `{_m[:120]}`")
            lines.append("")
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] S/R annotation pass failed: {_e}", file=_sys.stderr)

    # RSI coverage hook — every recommendation must carry an RSI read. Scan the
    # assembled briefing and surface any recommendation line that's missing one.
    try:
        from analysis import rsi_discipline as _rsi
        _rsi_offenders = _rsi.audit_missing_rsi("\n".join(lines))
        if _rsi_offenders:
            lines.append("## ⚠️ RSI Coverage Check")
            lines.append("")
            lines.append(
                f"_{len(_rsi_offenders)} recommendation line(s) are missing an RSI read "
                f"(every recommendation must include RSI):_"
            )
            for _o in _rsi_offenders[:10]:
                lines.append(f"- `{_o[:120]}`")
            lines.append("")
        else:
            lines.append("_✅ RSI coverage: every recommendation carries an RSI read._")
            lines.append("")
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] RSI coverage check failed: {_e}", file=_sys.stderr)

    lines.extend(render_inconsistencies(flagged_inconsistencies))

    # Insert "Since Yesterday" diff panel (best-effort — silent on first run)
    try:
        snapshot_root = snapshot_dir.parent if snapshot_dir else Path("state/briefing_snapshots")
        yesterday_md = load_yesterday_briefing(date_str, snapshot_root)
        # Build the today_md from what we have so far so we can diff.
        # When Step 7.5 reconciliation ran, removed items get definitive
        # statuses (✅ EXECUTED / ◐ PARTIAL / ✗ IGNORED / ❔ UNVERIFIED)
        # instead of "likely executed".
        today_so_far = "\n".join(lines)
        recon_for_diff = (aging_info or {}).get("reconciliation") if aging_info else None
        diff_panel = render_diff_panel(today_so_far, yesterday_md, recon_status=recon_for_diff)
        if diff_panel:
            # Insert near the top, after the header but before market context
            # For simplicity, append at the end before manifest
            lines.extend(diff_panel)
    except Exception:
        pass  # diff is best-effort; never break the briefing

    # Intrinsic value — attach a fair-value read (FMP DCF + analyst price
    # targets) to every single-stock recommendation. ETFs are marked baskets;
    # fail-closed when no FMP key is configured (no fabricated values). Runs as a
    # post-pass over the assembled briefing, mirroring the RSI annotate pattern.
    # See scripts/analysis/intrinsic_value.py.
    try:
        import os as _os
        from analysis import intrinsic_value as _iv
        from analysis import rsi_discipline as _rsi_d

        iv_cfg = (config.get("intrinsic_value") or {}) if isinstance(config, dict) else {}
        if iv_cfg.get("enabled", True):
            etf_set = _iv.default_etf_set(config)

            # Known ticker universe (for line→ticker matching).
            known: set[str] = set()
            tech = snapshot_data.get("technicals") or {}
            known.update(str(t).upper() for t in tech.keys() if t)
            for p in (snapshot_data.get("positions") or []):
                # Prefer the UNDERLYING for options — `symbol` is the full option
                # contract (e.g. META_PUT_530_20260717), which would otherwise
                # win the longest-match in first_known_ticker and break the
                # FV lookup (the option symbol isn't a key in the price map).
                sym = p.get("underlying") or p.get("symbol")
                if sym:
                    known.add(str(sym).upper())
            for r in (snapshot_data.get("recommendations_list") or []):
                if isinstance(r, dict) and r.get("ticker"):
                    known.add(str(r["ticker"]).upper())
            for t in (config.get("core_positions") or []):
                known.add(str(t).upper())
            if scout_payload:
                for th in (scout_payload.get("themes") or {}).values():
                    for a in (th.get("anchors") or []):
                        known.add(str(a).upper())

            # Spot prices (best-effort) for the "% vs spot" delta.
            spot_by: dict[str, float] = {}

            def _put_spot(sym, px):
                if not sym or px in (None, ""):
                    return
                key = str(sym).upper()
                if key in spot_by:
                    return
                try:
                    spot_by[key] = float(px)
                except (TypeError, ValueError):
                    pass

            for sym, q in (snapshot_data.get("quotes") or {}).items():
                if isinstance(q, dict):
                    _put_spot(sym, q.get("lastTrade") or q.get("last") or q.get("price"))
            for sym, t in tech.items():
                if isinstance(t, dict):
                    _put_spot(sym, t.get("spot") or t.get("price") or t.get("last"))
            for p in (snapshot_data.get("positions") or []):
                _put_spot(p.get("underlying") or p.get("symbol"),
                          p.get("underlying_price") or p.get("price"))

            # Pre-scan: which single stocks actually appear on a rec header?
            # (Bounds the FMP fetch to exactly the recommended single stocks.)
            tickers_sorted = sorted(known, key=len, reverse=True)
            rec_tickers: set[str] = set()
            for line in lines:
                s = line.strip()
                if s.startswith("_") or not _iv._is_rec_header(line):
                    continue
                tk = _rsi_d.first_known_ticker(line, tickers_sorted)
                if tk and not _iv.is_etf(tk, etf_set):
                    rec_tickers.add(tk)

            fmp_key = _os.getenv("FMP_API_KEY")
            fv_map: dict = {}
            if rec_tickers and fmp_key:
                snap_root = snapshot_dir.parent if snapshot_dir else Path("state/briefing_snapshots")
                fv_map = _iv.get_fair_values(
                    sorted(rec_tickers),
                    cache_path=snap_root / "intrinsic_value_cache.json",
                    ttl_hours=int(iv_cfg.get("ttl_hours", 24)),
                    api_key=fmp_key,
                )

            # Annotate single stocks ONLY when we actually got data back. A key
            # that's set but returns nothing for every ticker (free-tier/endpoint
            # gating) should NOT spam "n/a (no FMP data)" on every rec — treat it
            # like FMP-unavailable: mark ETFs as baskets, leave single stocks
            # clean, and explain in one footer.
            fmp_data_ok = bool(fv_map)
            lines, iv_stats = _iv.annotate_intrinsic(
                lines, fv_by_ticker=fv_map, spot_by_ticker=spot_by,
                known_tickers=known, etf_set=etf_set,
                fmp_available=fmp_data_ok,
            )

            if not fmp_key:
                lines.append(
                    "_💵 Intrinsic value unavailable this cycle — FMP_API_KEY not "
                    "configured; values are not fabricated (fail-closed). Set the key "
                    "to enable DCF + analyst-target reads on each recommendation._"
                )
            elif not fmp_data_ok:
                lines.append(
                    "_💵 Intrinsic value omitted this cycle — FMP key is set but "
                    "returned no data for any recommended ticker (likely free-tier / "
                    "endpoint gating; run `scripts/diagnose_fmp.py` to confirm). No "
                    "values fabricated._"
                )
            else:
                lines.append(
                    f"_💵 Intrinsic value (FMP DCF + analyst price targets): "
                    f"{iv_stats['annotated']} single-stock rec(s) priced · "
                    f"{iv_stats['etf']} ETF basket(s) · "
                    f"{iv_stats['unavailable']} unavailable._"
                )
            lines.append("")
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] intrinsic-value annotation failed: {_e}", file=_sys.stderr)

    # ── FINVIZ analyst-target annotation (FINVIZ Layer 2) ───────────────
    # Extend hard rule #12 with a second analyst-target source. FINVIZ
    # covers tickers FMP misses AND provides a normalized 1-5 analyst
    # recommendation score. When FMP + FINVIZ both have a target and
    # disagree by > 20%, the chip carries a ⚠ divergence flag.
    # Fetches from state/.finviz_target_cache.json (24h TTL) — never
    # blocks the pipeline on network. Fail-closed: no FINVIZ data → no
    # chip. Never invents a value (hard rule #19).
    try:
        from analysis import finviz_targets as _fvt
        # Read from the fetcher's cache — no network I/O in the aggregate step
        import json as _json
        cache_paths = [
            snap_root / ".finviz_target_cache.json",
            (snap_root.parent / ".finviz_target_cache.json") if snap_root else None,
        ]
        finviz_map: dict[str, dict] = {}
        for p in cache_paths:
            if p and p.exists():
                try:
                    raw = _json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        finviz_map = {
                            k.upper(): (v if v and not v.get("_null") else None)
                            for k, v in raw.items()
                        }
                        break
                except (OSError, _json.JSONDecodeError):
                    continue
        # FMP targets — reuse the fv_map we just built
        fmp_targets = {}
        if 'fv_map' in dir():
            fmp_targets = {
                k: (v.get("analyst_target") if isinstance(v, dict) else None)
                for k, v in (fv_map or {}).items()
                if v
            }
        if finviz_map:
            lines, fv_stats = _fvt.annotate_finviz(
                lines,
                finviz_by_ticker=finviz_map,
                fmp_targets_by_ticker=fmp_targets,
                known_tickers=list(known) if 'known' in dir() else [],
                etf_set=etf_set if 'etf_set' in dir() else set(),
            )
            if fv_stats["annotated"]:
                footer = (f"_📈 FINVIZ analyst targets: {fv_stats['annotated']} rec(s) "
                          f"stamped · {fv_stats['diverged']} diverge from FMP · "
                          f"{fv_stats['no_data']} without data._")
                lines.append(footer)
                lines.append("")
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] finviz-target annotation failed: {_e}", file=_sys.stderr)

    # ── Parkev chip annotation (CLAUDE.md hard rule #27) ──────────────────
    # Append 🅿️ {RATING} · {CONV} · {AGE} to every ticker-specific header
    # in the rendered briefing. Single-pass post-process — never
    # double-annotates lines that already carry the 🅿️ marker (e.g., Watch
    # panel rows populated by review_equities). Fails closed on missing
    # data (renders `🅿️ no rec` rather than fabricating).
    try:
        from analysis.parkev_chip import annotate_parkev_chips
        recs = (snapshot_data.get("recommendations_list") or [])
        recs_map = {(r.get("ticker") or "").upper(): r
                    for r in recs if r.get("ticker")}
        md_text = "\n".join(lines)
        md_text = annotate_parkev_chips(md_text, recs_map)
        lines = md_text.split("\n")
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] parkev-chip annotation failed: {_e}", file=_sys.stderr)

    # ── Tier badge annotation (CLAUDE.md hard rule #29) ───────────────────
    # Append ` · 🟢 Tier A` / ` · 🟡 Tier B` / ` · 🔵 Tier C` to every line
    # that already carries a Parkev chip. Single-pass post-process — never
    # double-annotates a line that already has a tier badge. Fails closed
    # when position_tiers config is missing: every ticker → Tier C (the
    # legacy default) so the briefing keeps rendering and the framework
    # stays a no-op until config opts in.
    try:
        from analysis.position_tiers import annotate_tier_badges
        md_text = "\n".join(lines)
        md_text = annotate_tier_badges(md_text, config)
        lines = md_text.split("\n")
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] tier-badge annotation failed: {_e}", file=_sys.stderr)

    # Challenge / Counterpoint layer — the briefing's built-in devil's advocate.
    # Stress-tests every Action List item from multiple perspectives (consistency
    # vs the position's own advisor, opportunity cost, tenor, tax, concentration,
    # valuation) and surfaces objections inline + in a consolidated panel. Reads
    # fair values from the cache the intrinsic step just wrote (no refetch).
    try:
        from analysis import recommendation_challenger as _rc

        nlv_v = float((snapshot_data.get("balance") or {}).get("accountValue", 0) or 0)
        _weights: dict[str, float] = {}
        if nlv_v > 0:
            for p in (snapshot_data.get("positions") or []):
                if p.get("assetType") == "EQUITY":
                    sym = (p.get("symbol") or "").upper()
                    mv = float(p.get("qty", 0) or 0) * float(p.get("price", 0) or 0)
                    if sym:
                        _weights[sym] = _weights.get(sym, 0.0) + mv / nlv_v * 100.0

        _options_by_contract = {
            o.get("contract"): o for o in (options_reviews or []) if o.get("contract")
        }
        _rsi_by = {
            str(s).upper(): t.get("rsi_14")
            for s, t in ((snapshot_data.get("technicals") or {}).items())
            if isinstance(t, dict) and t.get("rsi_14") is not None
        }
        _spot_by_c: dict[str, float] = {}
        for s, q in (snapshot_data.get("quotes") or {}).items():
            if isinstance(q, dict):
                px = q.get("lastTrade") or q.get("last") or q.get("price")
                if px:
                    try:
                        _spot_by_c[str(s).upper()] = float(px)
                    except (TypeError, ValueError):
                        pass
        for s, t in (snapshot_data.get("technicals") or {}).items():
            if isinstance(t, dict) and str(s).upper() not in _spot_by_c:
                px = t.get("spot") or t.get("price") or t.get("last")
                if px:
                    try:
                        _spot_by_c[str(s).upper()] = float(px)
                    except (TypeError, ValueError):
                        pass

        # Fair values from the intrinsic cache (written by the step above; no refetch).
        _fv_by: dict = {}
        try:
            import json as _json
            _cache_p = (snapshot_dir.parent if snapshot_dir else Path(".")) / "intrinsic_value_cache.json"
            if _cache_p.exists():
                _fv_by = {k.upper(): v for k, v in _json.loads(_cache_p.read_text()).items()}
        except Exception:
            pass

        _sc = (analytics or {}).get("stress_coverage") if isinstance(analytics, dict) else None
        _coverage = (_sc.get("coverage_ratio") if isinstance(_sc, dict)
                     else getattr(_sc, "coverage_ratio", None))

        _known_ch = (set(_rsi_by) | set(_weights)
                     | {(o.get("underlying") or "").upper() for o in (options_reviews or [])}
                     | {str(t).upper() for t in (config.get("core_positions") or [])})
        _known_ch.discard("")

        _ctx = {
            "known_tickers": _known_ch,
            "options_by_contract": _options_by_contract,
            "fv_by_ticker": _fv_by,
            "spot_by_ticker": _spot_by_c,
            "weights": _weights,
            "rsi_by_ticker": _rsi_by,
            "coverage_ratio": _coverage,
        }
        lines, _cp_panel = _rc.challenge_action_list(lines, _ctx)
        if _cp_panel:
            lines.extend(_cp_panel)
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] recommendation challenger failed: {_e}", file=_sys.stderr)

    lines.extend(render_manifest(str(snapshot_dir)))

    briefing_markdown = "\n".join(lines)

    # Pre-flight verifier — master gate orchestrator. Runs:
    #   1. Broker-position reconciler (catches stale snapshot data)
    #   2. Live-data verifier (catches stub-derived prices)
    #   3. Quality gate (4-persona structural checks)
    # Returns RELEASE / WARN / BLOCK and prepends consolidated warning panels.
    snapshot_positions = snapshot_data.get("positions", []) or []
    broker_positions = snapshot_data.get("broker_positions")  # optional — None if not provided
    pf = run_pre_flight(
        briefing_markdown,
        snapshot_positions=snapshot_positions,
        broker_positions=broker_positions,
        snapshot_data=snapshot_data,  # for the live-data policer
    )
    briefing_markdown = pf.rendered_briefing

    # ── Fable review — LAST STEP before returning ─────────────────────
    _review_lines = _run_fable_review_cascade(briefing_markdown, snapshot_dir, config)
    if _review_lines:
        briefing_markdown = briefing_markdown.rstrip() + "\n" + "\n".join(_review_lines)

    # Build JSON companion
    briefing_json = {
        "date": date_str,
        "regime": regime,
        "nlv": nlv,
        "cash": cash,
        "equity_reviews": equity_reviews,
        "options_reviews": options_reviews,
        "new_ideas": new_ideas,
        "long_term_opportunities": long_term_opportunities or [],
        "strategy_upgrades": upgrades,
        "consistency_report": consistency_report,
        "directives_active_count": len(directives_active),
        "directives_expired_count": len(directives_expired),
        "snapshot_dir": str(snapshot_dir),
        # Task #16 — benchmark tracking + P/L attribution (webapp Benchmark
        # tab reads this). {} when disabled or unavailable this cycle.
        "benchmark_report": benchmark_report_json,
    }
    # Step 7.5: per-action aging — tomorrow's run reads this back for
    # reconciliation (each: {key, kind, ident, summary, first_flagged,
    # days_flagged, recon_status}).
    if aging_info and aging_info.get("actions_export") is not None:
        briefing_json["actions"] = aging_info["actions_export"]

    return briefing_markdown, briefing_json
