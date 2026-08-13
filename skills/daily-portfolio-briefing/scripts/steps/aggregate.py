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
    position_context: str | None = None,
    executed_rolls: list | None = None,
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
                position_context=position_context,
                executed_rolls=executed_rolls,
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
    # George 2026-08-13 — the Watch panel's 🎓 entry-grade tokens resolve
    # the ledger from here when aggregate runs standalone (tests /
    # re-render) and the Step 7.6 maintenance stash is absent.
    snapshot_data["_snapshot_dir"] = str(snapshot_dir)

    # 2026-08-06 length diet — render.compact (default ON) turns the four
    # longest surfaces (LTO, Candidate Trades, Technical Read, Watch) into
    # compact decision-document views. Demoted items ALWAYS stay visible as
    # one-liners with their reason (rule #24). render.compact: false keeps
    # every legacy path byte-identical.
    _render_cfg = (config.get("render") or {}) if isinstance(config, dict) else {}
    render_compact = bool(_render_cfg.get("compact", True))

    # Task #43 — make the true chain-IV map available to every downstream
    # consumer (rotation playbook gate battery, LT-opportunity honesty checks,
    # the Vol Surface panel and the IV-label annotation pass). snapshot_inputs
    # computes it live; when aggregate is driven from a re-loaded snapshot,
    # fall back to the persisted chain_iv.json. Fail-open: no map → the
    # labeled realized-vol proxy carries the briefing.
    if not snapshot_data.get("chain_iv") and snapshot_dir is not None:
        try:
            _civ_file = snapshot_dir / "chain_iv.json"
            if _civ_file.exists():
                import json as _civ_json
                _loaded = _civ_json.loads(_civ_file.read_text(encoding="utf-8"))
                if isinstance(_loaded, dict):
                    snapshot_data["chain_iv"] = _loaded
        except Exception as _civ_e:
            import sys as _sys
            print(f"[aggregate] chain_iv.json load failed (non-fatal): "
                  f"{_civ_e}", file=_sys.stderr)

    # Bug #25: parse contract-level standing directives from
    # state/fable_advisor_memory.md so the CLOSE recommender + rec-aging can
    # respect them (a directive-held CLOSE is suppressed with a transparency
    # footer instead of escalating "IGNORED N DAYS"). Fail-open.
    if "_advisor_directives" not in snapshot_data:
        try:
            from analysis.advisor_directives import parse_directives
            # Canonical location first (<repo>/state/, same resolver Fable
            # uses) — the snapshot-relative guess is a fallback only, since
            # snapshot_dir may live under skills/.../state/ while the memory
            # file lives at the repo root.
            from analysis.fable_advisor import _default_memory_path
            _adv_mem = _default_memory_path()
            if not _adv_mem.exists():
                _adv_mem = (snapshot_dir.parent.parent if snapshot_dir
                            else Path("state")) / "fable_advisor_memory.md"
            if _adv_mem.exists():
                snapshot_data["_advisor_directives"] = parse_directives(
                    _adv_mem.read_text())
        except Exception as _adv_e:
            import sys as _sys
            print(f"[aggregate] advisor directives parse failed (non-fatal): "
                  f"{_adv_e}", file=_sys.stderr)

    # Task #38 Part 2: credit-window states per held short put. Computed once
    # here (from the advisor's already-priced roll candidates) BEFORE the
    # action list so both the forced-decision items and the Watch panel read
    # the same states; persisted to the daily snapshot and diffed against the
    # previous day for open→closing / open→debit_only transition alerts.
    # Fail-open: any error → no line, no alert, no crash.
    credit_window_alerts: list = []
    try:
        from analysis import credit_windows as _cwm
        _cw_map = _cwm.build_credit_windows(options_reviews, config)
        if _cw_map:
            snapshot_data["_credit_windows"] = _cw_map
            _cw_root = (snapshot_dir.parent if snapshot_dir
                        else Path("state/briefing_snapshots"))
            _prev_cw, _prev_cw_date = _cwm.load_previous_credit_windows(
                _cw_root, date_str)
            credit_window_alerts = _cwm.transition_alerts(
                _cw_map, _prev_cw, _prev_cw_date)
            if snapshot_dir:
                _cwm.persist_credit_windows(snapshot_dir, _cw_map, date_str)
    except Exception as _cw_e:
        import sys as _sys
        print(f"[aggregate] credit windows failed (non-fatal): {_cw_e}",
              file=_sys.stderr)

    # Task #40 fix 2: churn guard — measure each option position's age (in
    # trading days) from prior snapshots' position lists, so the action list
    # can withhold roll recommendations on contracts the user just opened
    # (roll.min_position_age_days, default 5). Fail-open: no history → {}.
    try:
        from analysis import churn_guard as _cg
        _pa_root = (snapshot_dir.parent if snapshot_dir
                    else Path("state/briefing_snapshots"))
        snapshot_data.setdefault("_position_ages", _cg.build_position_ages(
            snapshot_data.get("positions") or [], _pa_root, date_str))
    except Exception as _cg_e:
        import sys as _sys
        print(f"[aggregate] churn-guard ages failed (non-fatal): {_cg_e}",
              file=_sys.stderr)

    # 2026-08-05 defect 2: exit-cost verdict persistence. Load yesterday's
    # per-contract verdicts (so a CLOSE card whose anatomy can't be computed
    # today can say "yesterday's read was ROLL, don't close"), and persist
    # today's verdicts for tomorrow. Fail-open: advisory layer.
    try:
        from analysis import exit_cost as _xvc
        from render.panels import _exit_cost_anatomy as _xv_anatomy
        _xv_root = (snapshot_dir.parent if snapshot_dir
                    else Path("state/briefing_snapshots"))
        _xv_prior = _xvc.load_prior_exit_verdicts(_xv_root, date_str)
        if _xv_prior:
            snapshot_data.setdefault("_prior_exit_verdicts", _xv_prior)
        # 2026-08-06 defect 2: rich entries (verdict + anatomy drivers) so
        # tomorrow can attribute a change; churn-gap carry-forward; and
        # per-review "_verdict_change" flags for the ⏰ Risk Alerts prefix.
        _xv_today = _xvc.build_today_verdicts(
            options_reviews or [],
            ((snapshot_data.get("_prior_exit_verdicts") or {})
             .get("verdicts") or {}),
            snapshot_data.get("iv_ranks") or {},
            lambda _r: _xv_anatomy(_r, snapshot_data, equity_reviews,
                                   date_str, include_near_money=True)[0])
        if snapshot_dir and _xv_today:
            _xvc.persist_exit_verdicts(snapshot_dir, _xv_today, date_str)
    except Exception as _xv_e:
        import sys as _sys
        print(f"[aggregate] exit-verdict persistence failed (non-fatal): "
              f"{_xv_e}", file=_sys.stderr)

    # Redeploy-aware take-profit (George 2026-08-10: "I'm happy to exit
    # options and close it if we have a path to redeployment.") — pre-collect
    # the graded CSP setups pool BEFORE the action list renders, so the
    # winner-close gate can ask "is at least one A/B setup waiting for the
    # freed collateral?". The full 🏆 spotlight (incl. the CC side from
    # strategy upgrades) is still collected later; this pre-pass covers the
    # CSP side, which is what freed put collateral redeploys into. Fail-open:
    # any error → no pool stashed → the gate falls back to coverage-only
    # logic (and to legacy behavior when coverage itself is unresolvable).
    try:
        from analysis import redeploy_path as _rdp_pre
        if _rdp_pre.enabled(config):
            from analysis import setup_grade as _sg_pre
            if _sg_pre.setup_grade_enabled(config):
                _pre_scout: list = []
                for _rows in ((scout_payload or {}).get(
                        "results_by_theme") or {}).values():
                    _pre_scout.extend(
                        [r for r in (_rows or []) if isinstance(r, dict)])
                snapshot_data["_redeploy_best_setups"] = \
                    _sg_pre.collect_best_setups(
                        new_ideas=new_ideas,
                        long_term_opportunities=long_term_opportunities,
                        strategy_upgrades=None,
                        scout_results=_pre_scout,
                        snapshot_data=snapshot_data,
                        capacity_tag=None,
                        gates_closed=(gate_state is not None
                                      and not getattr(gate_state, "open",
                                                      True)),
                        config=config)
    except Exception as _rdp_e:
        import sys as _sys
        print(f"[aggregate] redeploy best-setups pre-pass failed "
              f"(non-fatal): {_rdp_e}", file=_sys.stderr)

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
            lines.extend(render_stalled_panel(
                aging_info["aged"],
                suppressed=aging_info.get("directive_suppressed")))
        except Exception as _stall_e:
            import sys as _sys
            print(f"[aggregate] stalled panel failed: {_stall_e}", file=_sys.stderr)
    lines.extend(render_header(date_str, regime, nlv, cash, action_count, confidence, regime_rationale, ytd_pnl, gate_state=gate_state, balance=balance))
    lines.extend(render_market_context(regime_data, quotes))

    # 2026-08-06 attribution decoration — ONE source legend, rendered once
    # near the top so every chip mark (🅿️ / 🤖 / 💰 MV / 💵 FV) is
    # explained. Fail-open.
    try:
        from analysis.moneyvest_chip import source_legend_lines
        lines.extend(source_legend_lines())
    except Exception as _leg_e:
        import sys as _sys
        print(f"[aggregate] source legend failed (non-fatal): {_leg_e}",
              file=_sys.stderr)

    # Task #46 — Moneyvest sentiment gauge in Market Context. Advisory
    # only (flags crowded optimism, never predicts direction / changes the
    # regime). Fail-open: no cache → no line.
    try:
        from analysis.moneyvest_chip import index_context_lines
        lines.extend(index_context_lines(snapshot_data.get("moneyvest")))
    except Exception as _mv_e:
        import sys as _sys
        print(f"[aggregate] moneyvest context failed (non-fatal): {_mv_e}",
              file=_sys.stderr)

    # Task #43 — Vol Surface subsection (true chain-implied vol). The map is
    # computed by snapshot_inputs from this cycle's chains; when aggregate is
    # driven from a re-loaded snapshot, fall back to the persisted
    # chain_iv.json. Skew-blowout / term-inversion are FLAGS, not forecasts
    # (wheelhouz signals #7/#8) — informational only. Fail-open: no chain IV
    # → no section, the labeled RVrank proxy carries the briefing.
    try:
        from analysis import chain_iv as _civ_mod
        if _civ_mod.load_chain_iv_config(config)["enabled"]:
            lines.extend(_civ_mod.render_vol_surface(
                snapshot_data.get("chain_iv") or {}, config,
                rv_ranks=snapshot_data.get("iv_ranks") or {}))
    except Exception as _civ_e:
        import sys as _sys
        print(f"[aggregate] vol-surface panel failed: {_civ_e}",
              file=_sys.stderr)
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

    # 2026-08-07 — Sector Exposure (look-through) panel, right after Health
    # (George: "It seems like I'm pretty heavily invested in tech. Is it the
    # right thing?"). Config-gated (sector_exposure.enabled). The computed
    # exposure is stashed on analytics so Red Flags reads the SAME numbers.
    # Fail-open: any error → no panel, no crash.
    try:
        from analysis.sector_exposure import (
            sector_exposure_enabled as _sx_enabled,
            compute_sector_exposure as _sx_compute,
        )
        if _sx_enabled(config):
            import os as _sx_os
            from render.sector_panel import render_sector_panel as _sx_render
            _sx_exposure = _sx_compute(
                snapshot_data.get("positions") or [], nlv, config,
                api_key=_sx_os.getenv("FMP_API_KEY"),
                cache_path=(snapshot_dir.parent if snapshot_dir
                            else Path("state/briefing_snapshots"))
                / "sector_cache.json",
            )
            analytics["sector_exposure"] = _sx_exposure
            lines.extend(_sx_render(_sx_exposure))
    except Exception as _sx_e:
        import sys as _sys
        print(f"[aggregate] sector-exposure panel failed (non-fatal): "
              f"{_sx_e}", file=_sys.stderr)

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
        credit_window_alerts=credit_window_alerts,
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
                config=bt_cfg,
            )
            lines.extend(render_benchmark_panel(_bench, _attrib))
            benchmark_report_json = {
                "benchmark": _bench.to_dict(),
                "attribution": _attrib.to_dict(),
            }
    except Exception as _bte:
        import sys as _sys
        print(f"[aggregate] benchmark panel failed (non-fatal): {_bte}", file=_sys.stderr)

    # Task #44 — 🤖 vs 🧠 ignored-rec forward ledger. Every prior action the
    # reconciliation resolved EXECUTED or IGNORED is appended to
    # state/rec_outcome_ledger.json with its rec-day mark, forward-marked at
    # +7d/+30d from snapshot marks (labeled estimates), and summarized here
    # in the benchmark section. Fail-open: any error → no panel.
    try:
        from analysis import ignored_ledger as _il
        lines.extend(_il.update_and_render(
            snapshot_dir, aging_info,
            snapshot_data.get("positions") or [], date_str))
    except Exception as _ile:
        import sys as _sys
        print(f"[aggregate] ignored-rec ledger failed (non-fatal): {_ile}",
              file=_sys.stderr)

    # George 2026-08-12 — 🎓 Entry Scorecard: the RUNNING score of option
    # entries ("i want to make sure we have a running score of our
    # entries"). The ledger maintenance ran in run_briefing Step 7.6
    # (stashed on snapshot_data["entry_ledger_update"]); when aggregate is
    # driven standalone (tests / re-render) the scorecard is recomputed
    # READ-ONLY from the persisted ledger — aggregate never writes it.
    # Config-gated (entry_scorecard.enabled); fail-open: any error → no
    # panel, JSON key stays {}, briefing ships.
    entry_scorecard_json: dict = {}
    _entry_open_grades: dict | None = None
    try:
        from analysis import entry_ledger as _el
        if _el.entry_scorecard_enabled(config):
            _el_update = snapshot_data.get("entry_ledger_update")
            if isinstance(_el_update, dict):
                _el_score = _el_update.get("scorecard")
                _entry_open_grades = _el_update.get("new_open_grades") or None
            else:
                _el_score = _el.compute_scorecard(
                    _el.load_ledger(_el.default_ledger_path(snapshot_dir)),
                    date_str, config)
            if _el_score and _el_score.get("graded"):
                lines.extend(_el.render_scorecard_panel(_el_score))
                entry_scorecard_json = _el_score
    except Exception as _ele:
        import sys as _sys
        print(f"[aggregate] entry scorecard failed (non-fatal): {_ele}",
              file=_sys.stderr)

    # Task #41 — 👻 Ghost portfolio: options-stripped counterfactual NAV
    # ("is it the market or my moves?"). Full idempotent recompute from
    # snapshot history each cycle (also persists state/ghost_portfolio.json).
    # Fail-open: any exception → no panel, JSON key stays {}, briefing ships.
    ghost_report_json: dict = {}
    _ghost_report = None
    try:
        from analysis import ghost_portfolio as _gp
        from render.ghost_panel import render_ghost_panel
        _g_root = snapshot_dir.parent if snapshot_dir else Path("state/briefing_snapshots")
        _ghost_report = _gp.build_ghost_report(_g_root, persist_state=True)
        if _ghost_report.status == "ok":
            lines.extend(render_ghost_panel(_ghost_report))
            ghost_report_json = _ghost_report.to_dict()
    except Exception as _ge:
        import sys as _sys
        print(f"[aggregate] ghost portfolio failed (non-fatal): {_ge}", file=_sys.stderr)

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

    # 🏆 Best Setups Today (George 2026-08-10: "a very clear message as to
    # when I should get in on every transaction") — the spotlight renders
    # RIGHT HERE, after the Action List, but its CC side needs the strategy
    # upgrades computed further down. Remember the insertion index; the
    # section is spliced in after compute_strategy_upgrades runs.
    _best_setups_at = len(lines)
    best_setups_json: dict = {}

    # MODIFIED: Use render_watch_with_commentary instead of plain render_watch
    lines.extend(render_watch_with_commentary(
        equity_reviews, options_reviews, snapshot_data,
        compact=render_compact))

    lines.extend(render_opportunities(new_ideas))

    # Task #42 — Put credit spreads, reference-first pilot. For every
    # ACTIONABLE Tier-C CSP candidate above, compose the defined-risk spread
    # variant (short leg = the CSP's gated strike; long leg from the same
    # snapshot chain) and render the side-by-side paper-watch card. Daily
    # outcomes are tracked in state/spread_paper_ledger.json. Reference mode
    # changes NO gates; the Money Plan carries the informational BP line.
    # Fail-open: any error → no subsection, briefing ships.
    spreads_json: dict = {}
    spread_reference_rollup: dict | None = None
    try:
        from analysis import spread_composer as _spc
        _sp_cfg = _spc.load_spread_config(config)
        if _sp_cfg["enabled"]:
            _sp_chains = snapshot_data.get("chains") or {}
            _sp_composed = _spc.compose_spreads(new_ideas, _sp_chains, config)
            lines.extend(_spc.render_spreads_section(_sp_composed, config))
            # Paper ledger: entry marks today + estimated close-outs for
            # expired paper spreads (latest spot, labeled estimate).
            _sp_spots: dict = {}
            for _sym, _q in (snapshot_data.get("quotes") or {}).items():
                if isinstance(_q, dict):
                    _px = _q.get("lastTrade") or _q.get("last") or _q.get("price")
                    if _px:
                        _sp_spots[str(_sym).upper()] = _px
            _sp_ledger_stats = _spc.update_paper_ledger(
                _spc.default_ledger_path(snapshot_dir),
                _sp_composed.get("spreads"), date_str,
                spot_by_ticker=_sp_spots)
            # Informational Money-Plan rollup: spread-mode BP for today's
            # capacity-gated entries vs their cash-secured collateral.
            _sp_gated = [
                op for op in (list(long_term_opportunities or [])
                              + list(new_ideas or []))
                if isinstance(op, dict)
                and (str(op.get("kind") or "").upper().startswith(("SKIPPED",
                                                                   "DEFERRED"))
                     or op.get("capacity_blocked"))
            ]
            spread_reference_rollup = _spc.spread_reference_summary(
                _sp_gated, _sp_chains, config)
            spreads_json = {
                "mode": _sp_cfg["mode"],
                "spreads": _sp_composed.get("spreads") or [],
                "skips": _sp_composed.get("skips") or [],
                "tier_excluded": _sp_composed.get("tier_excluded") or [],
                "paper_ledger": _sp_ledger_stats,
                "gated_reference": spread_reference_rollup,
            }
    except Exception as _sp_e:
        import sys as _sys
        print(f"[aggregate] spread composer failed (non-fatal): {_sp_e}",
              file=_sys.stderr)

    # NEW (Wave 22): Long-term opportunities — ADD/TRIM/EXIT/HOLD + LEAPs + long-dated CSPs
    if long_term_opportunities:
        from steps.long_term_opportunities import render_long_term_opportunities
        # config carries render.compact / render.max_lto_cards (2026-08-06
        # length diet); config=None or render.compact: false → legacy cards.
        lines.extend(render_long_term_opportunities(
            long_term_opportunities,
            config=config if render_compact else None))

    # NEW (task #20): CSP Rotations — close a lower-yield held CSP to fund a
    # higher-yield new CSP at same-or-lower total obligation. The open-side
    # candidates are the LT_CSP / PULLBACK_CSP recs the capacity gate defers;
    # the close-side is the held short-put book at ≥30% capture. Fail-open:
    # ANY exception → no section, empty JSON list, briefing ships regardless.
    csp_rotations_json: list = []
    csp_rotation_near_json: list = []
    try:
        from analysis import csp_rotation as _csp_rot
        from render.csp_rotation_panel import render_csp_rotations as _render_csp_rot

        _held_puts = [
            p for p in (snapshot_data.get("positions") or [])
            if p.get("assetType") == "OPTION"
            and (p.get("type") or "").upper() == "PUT"
            and float(p.get("qty", 0) or 0) < 0
        ]
        _cand_pool = list(long_term_opportunities or []) + list(new_ideas or [])
        if _held_puts and _cand_pool:
            # Standing hold directives from the fable-advisor memory — same
            # parse the entry/exit recommender uses (close-side skip list).
            _dir_holds: set = set()
            _dir_notes: dict = {}
            try:
                from analysis.entry_exit_recommender import directive_hold_tickers as _dht
                # Canonical <repo>/state/ location first (bug #25 follow-up:
                # snapshot_dir may live under skills/.../state/ while the
                # memory file lives at the repo root — the old relative guess
                # silently loaded nothing in production).
                from analysis.fable_advisor import _default_memory_path as _dmp
                _mem_p = _dmp()
                if not _mem_p.exists():
                    _mem_p = (snapshot_dir.parent.parent if snapshot_dir else Path("state")) \
                        / "fable_advisor_memory.md"
                if _mem_p.exists():
                    _mem_txt = _mem_p.read_text()
                    _dir_holds = _dht(_mem_txt)
                    # Task #21 — quotable first line of each hold directive,
                    # for the near-miss "blocked by" detail.
                    _dir_notes = _csp_rot.parse_directive_hold_notes(_mem_txt)
            except Exception:
                pass
            _recs_map_rot = {}
            for _r in (snapshot_data.get("recommendations_list") or []):
                if isinstance(_r, dict) and _r.get("ticker"):
                    _recs_map_rot[str(_r["ticker"]).upper()] = _r
            _rot_analytics = {
                "nlv": float(nlv or 0),
                "stress_coverage": analytics.get("stress_coverage"),
                "snapshot_data": snapshot_data,
                "technicals": snapshot_data.get("technicals") or {},
                "earnings_calendar": snapshot_data.get("earnings_calendar") or {},
                "recs_map": _recs_map_rot,
                "directive_holds": _dir_holds,
                "directive_hold_notes": _dir_notes,
            }
            _rots = _csp_rot.compute_csp_rotations(
                held_csps=_held_puts,
                candidate_csps=_cand_pool,
                analytics=_rot_analytics,
                config=config,
            )
            csp_rotations_json = [r.to_dict() for r in _rots]
            # Task #21 — near-misses (blocked by exactly one gate) ship in a
            # SEPARATE key so csp_rotations stays qualified-only for every
            # existing consumer.
            csp_rotation_near_json = [
                n.to_dict() for n in getattr(_rots, "near_miss", None) or []]
            lines.extend(_render_csp_rot(_rots, nlv=float(nlv or 0)))
    except Exception as _csre:
        import sys as _sys
        print(f"[aggregate] csp rotation failed (non-fatal): {_csre}", file=_sys.stderr)

    # NEW (task #22): Actionable Rotation Playbook — the whole composed
    # trade: close ALL freeable winner CSPs → redeploy the freed collateral
    # into conviction-ranked deferred candidates. Distinct from task #20:
    # partial deployment allowed (cushion kept), ranked by Parkev
    # conviction × freshness, not strict coverage-neutral swaps. Fail-open:
    # ANY exception → no section, empty JSON dict, briefing ships regardless.
    rotation_playbook_json: dict = {}
    try:
        if ((config or {}).get("rotation_playbook") or {}).get("enabled", True):
            from analysis import rotation_playbook as _rpb
            from render.rotation_playbook_panel import (
                render_rotation_playbook as _render_rpb,
            )

            _pb_held = [
                p for p in (snapshot_data.get("positions") or [])
                if p.get("assetType") == "OPTION"
                and (p.get("type") or "").upper() == "PUT"
                and float(p.get("qty", 0) or 0) < 0
            ]
            # Candidate pool: LT opportunities + new ideas + the scout's
            # per-theme CSP entries (live E*TRADE tickets on the deferred
            # Candidate Trades — the same tickets the manual Scenario A
            # ranked). De-duped by ticker, first occurrence wins.
            _pb_cands = list(long_term_opportunities or []) + list(new_ideas or [])
            _seen_scout: set = set()
            for _results in ((scout_payload or {}).get("results_by_theme")
                             or {}).values():
                for _r in _results or []:
                    if not isinstance(_r, dict):
                        continue
                    _q = _r.get("csp_entry") or {}
                    _tk = str(_r.get("ticker") or "").upper()
                    if not _tk or _tk in _seen_scout or not _q.get("strike") \
                            or not (_q.get("mid") or _q.get("bid")):
                        continue
                    _seen_scout.add(_tk)
                    _pb_cands.append({
                        "kind": "SCOUT_CSP", "ticker": _tk,
                        "strike": _q.get("strike"),
                        "expiration": _q.get("expiration"),
                        "dte": _q.get("dte"),
                        "premium": _q.get("mid") or _q.get("bid"),
                        "rsi_14": _r.get("rsi_14"),
                        "iv_rank": _r.get("iv_rank"),
                        "drawdown_pct": _r.get("drawdown_pct"),
                        "verdict": _r.get("verdict"),
                        "earnings_date": _r.get("earnings_date"),
                        "days_to_earnings": _r.get("days_to_earnings"),
                    })
            if _pb_held:
                _pb_holds: set = set()
                try:
                    from analysis.entry_exit_recommender import (
                        directive_hold_tickers as _pb_dht,
                    )
                    # Canonical <repo>/state/ location first (see bug #25
                    # follow-up note in the csp-rotation block above).
                    from analysis.fable_advisor import (
                        _default_memory_path as _pb_dmp,
                    )
                    _pb_mem = _pb_dmp()
                    if not _pb_mem.exists():
                        _pb_mem = (snapshot_dir.parent.parent if snapshot_dir
                                   else Path("state")) / "fable_advisor_memory.md"
                    if _pb_mem.exists():
                        _pb_holds = _pb_dht(_pb_mem.read_text())
                except Exception:
                    pass
                _pb_recs = {}
                for _r in (snapshot_data.get("recommendations_list") or []):
                    if isinstance(_r, dict) and _r.get("ticker"):
                        _pb_recs[str(_r["ticker"]).upper()] = _r
                _pb = _rpb.compute_playbook(
                    held_csps=_pb_held,
                    candidate_csps=_pb_cands,
                    parkev_recs=_pb_recs,
                    directive_holds=_pb_holds,
                    analytics={
                        "nlv": float(nlv or 0),
                        "stress_coverage": analytics.get("stress_coverage"),
                        "snapshot_data": snapshot_data,
                        "technicals": snapshot_data.get("technicals") or {},
                        "iv_ranks": snapshot_data.get("iv_ranks") or {},
                        "earnings_calendar":
                            snapshot_data.get("earnings_calendar") or {},
                    },
                    config=config,
                )
                if _pb is not None:
                    rotation_playbook_json = _pb.to_dict()
                    lines.extend(_render_rpb(_pb))
    except Exception as _rpbe:
        import sys as _sys
        print(f"[aggregate] rotation playbook failed (non-fatal): {_rpbe}",
              file=_sys.stderr)

    # NEW (Wave 26): Thematic Scout — research across themes (semis/nuclear/etc)
    if scout_payload:
        from steps.thematic_research import render_scout_section
        # Market Pulse + theme-by-theme read only — the actionable picks are
        # carried by the richer Candidate Trades section that follows.
        lines.extend(render_scout_section(scout_payload, max_per_theme=4, config=config,
                                          include_shortlist=False,
                                          # One-voice vol (George 2026-08-12):
                                          # pulse reads the same effective vol
                                          # the setup grade scores.
                                          chain_iv_map=snapshot_data.get("chain_iv")))
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
                compact=render_compact,
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
            # 2026-08-06 length diet: in compact mode only names with an
            # action somewhere in THIS cycle's briefing (action list item,
            # actionable un-gated LTO, candidate with a live CSP ticket)
            # keep the full technical card; the rest collapse to one line.
            # Fail-open: any error → None → full legacy cards.
            _ta_action_tickers = None
            if render_compact:
                try:
                    _ta_action_tickers = set()
                    from analysis.rsi_discipline import first_known_ticker as _fkt
                    _ta_known: set = set()
                    for _tk_k in (snapshot_data.get("technicals") or {}).keys():
                        _ta_known.add(str(_tk_k).upper())
                    for _p_k in (snapshot_data.get("positions") or []):
                        _sym_k = _p_k.get("underlying") or _p_k.get("symbol")
                        if _sym_k:
                            _ta_known.add(str(_sym_k).upper())
                    _ta_sorted = sorted(_ta_known, key=len, reverse=True)
                    for _al_line in action_list_lines:
                        _al_s = _al_line.lstrip()
                        if _al_s[:1].isdigit() and "." in _al_s[:5]:
                            _al_tk = _fkt(_al_line, _ta_sorted)
                            if _al_tk:
                                _ta_action_tickers.add(_al_tk.upper())
                    from steps.long_term_opportunities import (
                        actionable_ungated_tickers as _aut)
                    _ta_action_tickers |= _aut(long_term_opportunities or [])
                    # Candidates with a live CSP ticket keep their card.
                    for _c_results in ((scout_payload or {})
                                       .get("results_by_theme") or {}).values():
                        for _c_r in _c_results or []:
                            if not isinstance(_c_r, dict):
                                continue
                            if ((_c_r.get("csp_entry") or {}).get("strike")
                                    and _c_r.get("ticker")):
                                _ta_action_tickers.add(
                                    str(_c_r["ticker"]).upper())
                except Exception as _ta_e:
                    import sys as _sys
                    print(f"[aggregate] compact action-ticker set failed "
                          f"(fail-open to full cards): {_ta_e}",
                          file=_sys.stderr)
                    _ta_action_tickers = None
            lines.extend(render_technical_read_sections(
                snapshot_data, config,
                scout_payload=scout_payload,
                gate_state=gate_state,
                recommendations_list=snapshot_data.get("recommendations_list"),
                compact=render_compact,
                action_tickers=_ta_action_tickers,
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

    # 🏆 Best Setups Today — top-3 CSP + top-3 CC across the graded
    # universe (income opportunities + LT_CSPs + CC-writable holdings +
    # scout candidates), spliced in at the remembered index right after
    # the Action List. Only names past their side's RSI hard block and
    # the delivered-yield floor; capacity-gated names carry the ⏸ tag
    # (rules #24/#41 — the aggregate retag pass re-syncs it). Never
    # padded. Config-gated; fail-open: any error → no section.
    try:
        from analysis import setup_grade as _sg_best
        if (_sg_best.setup_grade_enabled(config)
                and _sg_best.spotlight_enabled(config)):
            _bs_scout: list = []
            for _bs_rows in ((scout_payload or {}).get(
                    "results_by_theme") or {}).values():
                _bs_scout.extend(
                    [r for r in (_bs_rows or []) if isinstance(r, dict)])
            try:
                from analysis import capacity_gate as _bs_cap
                _bs_tag = _bs_cap.capacity_deferred_tag(analytics, config)
            except ImportError:
                _bs_tag = None
            best_setups_json = _sg_best.collect_best_setups(
                new_ideas=new_ideas,
                long_term_opportunities=long_term_opportunities,
                strategy_upgrades=upgrades,
                scout_results=_bs_scout,
                snapshot_data=snapshot_data,
                capacity_tag=_bs_tag,
                gates_closed=(gate_state is not None
                              and not getattr(gate_state, "open", True)),
                config=config)
            _bs_lines = _sg_best.render_best_setups(best_setups_json,
                                                    config=config)
            if _bs_lines:
                lines[_best_setups_at:_best_setups_at] = _bs_lines
    except Exception as _bs_e:
        import sys as _sys
        print(f"[aggregate] best setups failed (non-fatal): {_bs_e}",
              file=_sys.stderr)

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

    # Setup Grade coverage hook (George 2026-08-12: "Let's also add a grade
    # to every recommendation that you're giving so that I know it's a good
    # recommendation.") — every NEW-OPEN option ticket must carry a Setup
    # Grade token within its card; management lines must carry a decision
    # token (⚖️ Verdict / capture % / advisor rec). Mirrors the RSI hook.
    try:
        from analysis import setup_grade as _sg_cov
        if _sg_cov.setup_grade_enabled(config):
            _gr = _sg_cov.audit_missing_grade("\n".join(lines))
            _gr_open = _gr.get("new_open") or []
            _gr_mgmt = _gr.get("management") or []
            if _gr_open or _gr_mgmt:
                lines.append("## 🏁 Setup Grade Coverage")
                lines.append("")
                if _gr_open:
                    lines.append(
                        f"_{len(_gr_open)} new-open ticket(s) are missing a Setup "
                        f"Grade (every new-open recommendation must be graded):_"
                    )
                    for _o in _gr_open[:10]:
                        lines.append(f"- `{_o[:120]}`")
                    lines.append("")
                if _gr_mgmt:
                    lines.append(
                        f"_{len(_gr_mgmt)} management line(s) carry no decision "
                        f"token (⚖️ Verdict / capture % / advisor rec):_"
                    )
                    for _o in _gr_mgmt[:10]:
                        lines.append(f"- `{_o[:120]}`")
                    lines.append("")
            else:
                lines.append(
                    "_✅ Setup Grade coverage: every new-open ticket is graded; "
                    "management lines carry verdicts._")
                lines.append("")
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] Setup Grade coverage check failed: {_e}",
              file=_sys.stderr)

    # Tenor-cap sweep (rule #14 backstop, 2026-08-04): no actionable ticket
    # anywhere may carry an STO leg past the applicable tenor cap vs the
    # position's current expiry (the "STO 1× VRT $240P Fri Dec 15 '28" bug —
    # ~700d past a Jan '27 expiry via the take-profit composer). Menu-table
    # reference rows / transparency footers are exempt inside the guard.
    try:
        from analysis.tenor_guard import ticket_tenor_violations
        try:
            from analysis.position_tiers import core_union as _tg_core_union
            _tg_core = _tg_core_union(config or {})
        except Exception:
            _tg_core = set((config or {}).get("core_positions") or [])
        try:
            _tg_cap = int(((config or {}).get("roll") or {})
                          .get("max_action_tenor_days", 120))
        except (TypeError, ValueError):
            _tg_cap = 120
        _tg_offenders = ticket_tenor_violations(
            "\n".join(lines), max_action_tenor_days=_tg_cap,
            core_tickers=_tg_core)
        if _tg_offenders:
            lines.append("## 🔴 Tenor-Cap Violation Check")
            lines.append("")
            lines.append(
                f"_{len(_tg_offenders)} actionable ticket(s) carry an STO leg "
                f"past the {_tg_cap}d action tenor cap (core ×3) — rule #14; "
                f"do NOT place these as rendered:_"
            )
            for _o in _tg_offenders[:10]:
                lines.append(f"- `{_o[:160]}`")
            lines.append("")
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] tenor-cap sweep failed: {_e}", file=_sys.stderr)

    # Price-consistency sweep (2026-08-07 TEAM bug): a briefing must never
    # show two spot prices >2% apart for the same ticker (TEAM rendered
    # $109.73 / $110.17 / $144.41 across three surfaces after a +31% earnings
    # gap — the scout cache and the OHLC close were both stale vs the live
    # E*TRADE quote). Conservative scan: header/action spot patterns only —
    # strikes, targets and FV notes are never matched.
    try:
        from analysis import price_consistency as _pc
        _pc_offenders = _pc.price_disagreements("\n".join(lines))
        lines.extend(_pc.render_panel(_pc_offenders))
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] price-consistency sweep failed: {_e}", file=_sys.stderr)

    lines.extend(render_inconsistencies(flagged_inconsistencies))

    # Task #40 fix 9 / task #43 fix 1: detect user-executed rolls from the
    # position diff ONCE — shared by the "Since Yesterday" panel (ready-to-
    # paste directive templates) and the Fable advisor prompt (falsification
    # evidence against stale "never rolls" pattern notes). Fail-open:
    # missing snapshots → None, both consumers degrade gracefully.
    _executed_rolls = None
    try:
        from analysis.briefing_diff import detect_executed_rolls
        _executed_rolls = detect_executed_rolls(
            (aging_info or {}).get("prev_positions"),
            snapshot_data.get("positions"))
    except Exception:
        _executed_rolls = None

    # 2026-08-10 bug 1a: user-executed OPENS were invisible (the MELI
    # $1460P Jun '27 — a $146K obligation, 13.3% of NLV — appeared with no
    # 'Since Yesterday' mention and an unexplained coverage drop). Detect
    # fresh short opens from the same position diff, with measured
    # obligation / % NLV / coverage counterfactual. Fail-open.
    _executed_opens = None
    try:
        from analysis.briefing_diff import detect_executed_opens
        _bal_for_opens = snapshot_data.get("balance", {}) or {}
        _executed_opens = detect_executed_opens(
            (aging_info or {}).get("prev_positions"),
            snapshot_data.get("positions"),
            nlv=_bal_for_opens.get("accountValue"),
            cash=_bal_for_opens.get("cash"))
    except Exception:
        _executed_opens = None

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
        diff_panel = render_diff_panel(today_so_far, yesterday_md,
                                       recon_status=recon_for_diff,
                                       executed_rolls=_executed_rolls,
                                       today_iso=date_str,
                                       executed_opens=_executed_opens,
                                       entry_grades=_entry_open_grades)
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
    #
    # snap_root is shared by this block AND the FINVIZ annotation block below,
    # so it MUST be defined unconditionally here. It used to be assigned inside
    # `if rec_tickers and fmp_key:` — a latent NameError in the FINVIZ block
    # whenever the FMP branch didn't run (no key / no rec tickers).
    snap_root = snapshot_dir.parent if snapshot_dir else Path("state/briefing_snapshots")
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
            # 2026-08-04: Tier A names are "core" too (position_tiers union).
            try:
                from analysis.position_tiers import core_union as _cu_known
                known.update(_cu_known(config))
            except Exception:
                pass
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
        # Task #45 — Autopilot Claude-portfolio membership rides the same
        # pass as a ` · 🤖 CP-held` suffix (corroboration only).
        _cp_payload = snapshot_data.get("claude_portfolio") or {}
        _cp_tickers = {str(t).upper()
                       for t in (_cp_payload.get("tickers") or []) if t}
        md_text = "\n".join(lines)
        md_text = annotate_parkev_chips(md_text, recs_map,
                                        cp_tickers=_cp_tickers)
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

    # ── Moneyvest chip annotation (task #46) ─────────────────────────────
    # Append ` · 💰 FV $152 · LB $196 · M 4.05` to every line already
    # carrying a 🅿️ Parkev chip (same targeting discipline as tier badges),
    # plus the FV-divergence warning when MV fair value and the FMP DCF on
    # the same line diverge > 40% relative. n/a-safe, ETFs index-only,
    # fail-open.
    try:
        from analysis.moneyvest_chip import annotate_mv_chips
        md_text = "\n".join(lines)
        md_text = annotate_mv_chips(md_text, snapshot_data.get("moneyvest"))
        lines = md_text.split("\n")
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] moneyvest-chip annotation failed: {_e}",
              file=_sys.stderr)

    # ── True-IV label annotation (task #43) ───────────────────────────────
    # Every legacy "IV rank N" token in the briefing IS the 252d realized-vol
    # proxy — backward-looking, gap-inflatable (the AMZN "IV rank 100 / 4%
    # ann" case). Rewrite each occurrence honestly: names with TRUE chain-
    # implied vol this cycle render "IV 34% · IVrank 62 · RVrank N" (plus
    # skew-blowout / term-inversion tags), names without chains render
    # "RVrank N (realized-vol proxy)". Idempotent; italic footers exempt.
    try:
        from analysis import chain_iv as _civ_ann
        if _civ_ann.load_chain_iv_config(config)["enabled"]:
            md_text = "\n".join(lines)
            md_text, _civ_stats = _civ_ann.annotate_briefing(
                md_text, snapshot_data.get("chain_iv") or {},
                snapshot_data.get("iv_ranks") or {}, config)
            lines = md_text.split("\n")
            if any(_civ_stats.values()):
                lines.append(
                    f"_📈 IV labels: {_civ_stats['true']} true chain-IV "
                    f"read(s) · {_civ_stats['building']} building-history · "
                    f"{_civ_stats['rv_only']} realized-vol fallback(s) "
                    f"(RVrank, labeled — chains unavailable for those "
                    f"names)._")
                lines.append("")
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] true-IV label annotation failed: {_e}",
              file=_sys.stderr)

    # ── Same-cycle vintage guard ──────────────────────────────────────────
    # Never mix pre-gap technicals with live quotes on one card. When a name's
    # live quote has moved > vintage_guard.max_intraday_move_pct (default 5%)
    # from the close the technicals were computed at, strip stale "✅ RSI
    # favourable" promotions (tag the RSI pre-gap instead — never fabricate a
    # live RSI) and recompute 200-SMA distances at live spot on LT-verdict
    # reads. Fail-open: any error → briefing ships untagged.
    try:
        from analysis import vintage_guard as _vg
        # Rule #46 (PLTR 2026-08-04): positions provide the broker-price
        # fallback when a name's yfinance quote is missing — a missing quote
        # must never fail-open into a trusted favourable RSI badge.
        _vg_flags = _vg.compute_flags(
            snapshot_data.get("quotes") or {},
            snapshot_data.get("technicals") or {},
            config if isinstance(config, dict) else None,
            positions=snapshot_data.get("positions") or [],
        )
        if _vg_flags:
            md_text = "\n".join(lines)
            md_text, _vg_stats = _vg.annotate_briefing(md_text, _vg_flags)
            lines = md_text.split("\n")
            _vg_footer = _vg.footer(_vg_flags, config if isinstance(config, dict) else None)
            if _vg_footer:
                lines.append(_vg_footer)
                lines.append("")
    except Exception as _e:
        import sys as _sys
        print(f"[aggregate] vintage-guard annotation failed: {_e}", file=_sys.stderr)

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
        try:
            from analysis.position_tiers import core_union as _cu_ch
            _known_ch |= _cu_ch(config)
        except Exception:
            pass
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

    # ── 💰 Money Plan (2026-08-04) — "what makes me money today" ─────────
    # Rendered at the VERY TOP of the briefing (above Stalled Items): banks,
    # deploys, net cash, coverage-after, month-to-date pace, blocked money.
    # Only actionable composed items count; MTD is MATCHED per-contract
    # realized P/L from snapshot position diffs (never the raw option
    # cash-flow — rule #43 follow-up) or "n/a (ledger pending)" (rule #19).
    # Fail-open: any error → no panel, briefing ships.
    money_plan_json: dict = {}
    try:
        from render.money_plan import build_money_plan
        _mp_lines, money_plan_json = build_money_plan(
            date_str=date_str,
            action_list_lines=action_list_lines,
            options_reviews=options_reviews,
            new_ideas=new_ideas,
            playbook=rotation_playbook_json,
            analytics=analytics,
            snapshot_data=snapshot_data,
            config=config,
            attribution=(benchmark_report_json or {}).get("attribution"),
            long_term_opportunities=long_term_opportunities,
            aging_info=aging_info,
            snapshot_dir=snapshot_dir,
            spread_reference=spread_reference_rollup,
        )
        # Task #41 — 👻 "Wheel vs Ghost" bullet: the options program's
        # measured net contribution (real − ghost NAV). Appended before the
        # panel's trailing blank; skipped when unmeasurable (fail-open).
        try:
            from render.ghost_panel import ghost_money_plan_line
            _gl = ghost_money_plan_line(_ghost_report)
            if _gl and _mp_lines:
                _g_idx = len(_mp_lines)
                while _g_idx > 0 and _mp_lines[_g_idx - 1] == "":
                    _g_idx -= 1
                _mp_lines.insert(_g_idx, _gl)
                if isinstance(money_plan_json.get("lines"), list):
                    money_plan_json["lines"].append(_gl[2:])
        except Exception:
            pass
        if _mp_lines:
            lines[0:0] = _mp_lines
    except Exception as _mp_e:
        import sys as _sys
        print(f"[aggregate] money plan failed (non-fatal): {_mp_e}",
              file=_sys.stderr)

    lines.extend(render_manifest(str(snapshot_dir),
                                 snapshot_data.get("data_provenance")))

    briefing_markdown = "\n".join(lines)

    # 2026-08-05 defect 4: capacity tags are PRESENTATION, not data — cached
    # candidates carry generation-time coverage ratios that go stale. Re-tag
    # every rendered DEFERRED tag from the CURRENT run's gate state.
    try:
        from analysis.capacity_gate import retag_capacity_lines
        briefing_markdown = retag_capacity_lines(
            briefing_markdown, analytics, config)
    except Exception as _rt_e:
        import sys as _sys
        print(f"[aggregate] capacity re-tag failed (non-fatal): {_rt_e}",
              file=_sys.stderr)

    # 2026-08-10 bug 4: capacity-gated/deferred planning cards must sort
    # BELOW executable actions — the digest's action #1 was a Deferred
    # PULLBACK CSP ranked above the executable CLOSE. Reorder (stable),
    # renumber; the deferred card stays fully visible (rule #24/#41).
    try:
        from render.panels import sort_deferred_actions
        briefing_markdown = sort_deferred_actions(briefing_markdown)
    except Exception as _sd_e:
        import sys as _sys
        print(f"[aggregate] deferred-action sort failed (non-fatal): {_sd_e}",
              file=_sys.stderr)

    # 2026-08-05 defect 3: the header count must reflect the FINAL composed
    # action list — recount numbered items after every composer/post-pass.
    # 2026-08-10 bug 4: the count renders executable vs deferred separately
    # ("Action Items: 1 (+1 deferred)").
    try:
        from render.panels import sync_action_item_count
        briefing_markdown = sync_action_item_count(briefing_markdown)
    except Exception as _sc_e:
        import sys as _sys
        print(f"[aggregate] action-count sync failed (non-fatal): {_sc_e}",
              file=_sys.stderr)

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
    # Exit-cost anatomy summary (analysis/exit_cost.py) rides along as
    # position context so the advisor reasons about intrinsic vs panic-IV
    # extrinsic vs spread on underwater short puts, not headline P&L.
    # Fail-open: any error → no context, review runs unchanged.
    _exit_ctx = None
    try:
        from analysis.exit_cost import build_fable_context
        _exit_ctx = build_fable_context(
            options_reviews, snapshot_data, config=config,
            equity_reviews=equity_reviews, today=date_str,
        ) or None
    except Exception as _e:
        print(f"[aggregate] exit-cost fable context failed (non-fatal): {_e}",
              file=sys.stderr)
    _review_lines = _run_fable_review_cascade(briefing_markdown, snapshot_dir,
                                              config, position_context=_exit_ctx,
                                              executed_rolls=_executed_rolls)
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
        # Task #41 — 👻 ghost portfolio (options-stripped counterfactual
        # NAV). GhostReport.to_dict(); {} when unavailable this cycle.
        "ghost_portfolio": ghost_report_json,
        # Task #20 — CSP rotations (close lower-yield held CSP → open
        # higher-yield candidate, coverage-neutral or better). [] when none
        # qualify or the step failed (fail-open).
        "csp_rotations": csp_rotations_json,
        # Task #21 — rotations blocked by exactly ONE gate, with the gate,
        # detail, and unblock path. Informational only (never qualified).
        "csp_rotation_near_misses": csp_rotation_near_json,
        # Task #22 — Actionable Rotation Playbook (close all freeable
        # winners → conviction-ranked redeploy). {} when below the freed
        # floor, disabled, or the step failed (fail-open).
        "rotation_playbook": rotation_playbook_json,
        # 2026-08-04 — 💰 Money Plan rollup (banks/deploys/net cash/coverage
        # after/MTD/blocked money). {} when nothing was measurable or the
        # panel failed this cycle (fail-open). The webapp renders this at
        # the top of the briefing page.
        "money_plan": money_plan_json,
        # Task #42 — put-credit-spread reference pilot (paper-watch cards,
        # ledger stats, gated-entry BP rollup). {} when disabled/failed.
        "put_credit_spreads": spreads_json,
        # Setup Grade spotlight (George 2026-08-10) — top-3 CSP + top-3 CC
        # entry-timing setups ({"csp": [...], "cc": [...]}). {} when the
        # setup_grade flag is off or nothing qualified this cycle.
        "best_setups": best_setups_json,
        # George 2026-08-12 — 🎓 Entry Scorecard (running entry-grade
        # ledger: averages / distribution / trend / last-5 / callouts /
        # capture-by-grade). {} when disabled or nothing graded yet.
        "entry_scorecard": entry_scorecard_json,
    }
    # Step 7.5: per-action aging — tomorrow's run reads this back for
    # reconciliation (each: {key, kind, ident, summary, first_flagged,
    # days_flagged, recon_status}).
    if aging_info and aging_info.get("actions_export") is not None:
        briefing_json["actions"] = aging_info["actions_export"]

    return briefing_markdown, briefing_json
