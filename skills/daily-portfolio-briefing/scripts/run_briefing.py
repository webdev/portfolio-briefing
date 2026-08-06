#!/usr/bin/env python3
"""
Daily Portfolio Briefing Orchestrator

Entry point. Orchestrates all 10 steps to produce a daily briefing markdown file.
- Step 1: Pre-flight (config, auth check, load yesterday)
- Step 1.5: Load and evaluate directives
- Step 1.6: Fetch third-party recommendations
- Step 2: Snapshot inputs
- Step 3: Classify regime
- Step 4: Review equities
- Step 5: Review options
- Step 6: New ideas
- Step 7: Day-over-day consistency check
- Step 8: Aggregate and render
- Step 9: Quality gate
- Step 10: Surface to user

Usage:
  python3 run_briefing.py --config config/briefing.yaml --output reports/daily/briefing_YYYY-MM-DD.md
  python3 run_briefing.py --config config/briefing.yaml --etrade-fixture assets/etrade_mock_fixture.json
  python3 run_briefing.py --dry-run --force
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Add scripts dir to path so we can import from steps/ and render/
sys.path.insert(0, str(Path(__file__).parent))

from steps.preflight import run_preflight
from steps.load_directives import load_directives
from steps.fetch_recommendations import fetch_recommendations
from steps.snapshot_inputs import snapshot_inputs
from steps.classify_regime import classify_regime
from steps.review_equities import review_equities
from steps.review_options import review_options
from steps.new_ideas import generate_new_ideas
from steps.long_term_opportunities import generate_long_term_opportunities_step
from steps.thematic_research import run_thematic_research
from steps.capital_plan import build_capital_plan_step
from steps.consistency_check import check_consistency
from steps.aggregate import aggregate_briefing
from steps.quality_gate import run_quality_gate
from steps.deliver import deliver_briefing
from analysis.capacity_gates import evaluate_gates
from analysis.json_utils import json_default  # belt-and-suspenders: Decimal/date/Path/set-safe dumps (2026-08-04)


def main():
    parser = argparse.ArgumentParser(
        description="Daily Portfolio Briefing Orchestrator"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config/briefing.yaml",
        help="Path to briefing_config.yaml",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output markdown file path (default: reports/daily/briefing_YYYY-MM-DD.md)",
    )
    parser.add_argument(
        "--etrade-fixture",
        type=str,
        default=None,
        help="Mock E*TRADE fixture JSON (for testing)",
    )
    parser.add_argument(
        "--etrade-live",
        action="store_true",
        help="Pull REAL positions/balance from E*TRADE via pyetrade. Requires "
             "tokens at $PORTFOLIO_BRIEFING_TOKEN_FILE (default "
             "~/.config/portfolio-briefing/etrade_tokens.json). Run "
             "scripts/etrade_auth.py first to authenticate.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write files, print to stdout",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-run, overwriting today's briefing",
    )
    parser.add_argument(
        "--delivery-dir",
        type=str,
        default=None,
        help="Override delivery directory (default: ~/Documents/briefings/)",
    )
    parser.add_argument(
        "--no-delivery",
        action="store_true",
        help="Skip the delivery copy step (default: deliver to ~/Documents/briefings/)",
    )
    parser.add_argument(
        "--refresh-scout",
        action="store_true",
        help="Force a fresh thematic-scout research run (default uses 24h cache)",
    )

    args = parser.parse_args()

    # Step 1: Pre-flight
    print("[Step 1] Pre-flight check...")
    try:
        config, yesterday_briefing_path = run_preflight(
            args.config, etrade_fixture=args.etrade_fixture
        )
    except Exception as e:
        print(f"FATAL: Pre-flight failed: {e}", file=sys.stderr)
        return 1

    today_date_str = datetime.now().strftime("%Y-%m-%d")
    # Fixture and dry-run modes must NEVER overwrite the canonical dated
    # snapshot — a test run would clobber the real morning run's snapshot,
    # breaking next-day reconciliation/diffs (this happened on 2026-06-11).
    snapshot_subdir = today_date_str
    if args.etrade_fixture or args.dry_run:
        snapshot_subdir = f"{today_date_str}.test"
    snapshot_dir = Path("state/briefing_snapshots") / snapshot_subdir
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1.5: Load directives
        print("[Step 1.5] Loading directives...")
        directives_active, directives_expired = load_directives(snapshot_dir)

        # Step 1.6: Fetch recommendations
        print("[Step 1.6] Fetching third-party recommendations...")
        recommendations_list = fetch_recommendations(snapshot_dir)
        # Stash on the snapshot so downstream skills (capital-planner) can read it
        # without re-loading from disk.
        # Stashed after snapshot_inputs runs; see Step 2.

        # Step 1.7: Fetch FINVIZ analyst targets (public scrape mode).
        # Second source for analyst PT + normalized 1-5 recommendation
        # score. Extends hard rule #12 (intrinsic value). Fail-open: any
        # network error / block returns None per ticker; the briefing
        # still renders without the FINVIZ chip. Cached 24h in
        # <snapshot_dir>/.finviz_target_cache.json — hitting FINVIZ at
        # most once per day for held + Parkev tier-3+ tickers (~50
        # requests, well under the 60/day soft cap).
        print("[Step 1.7] Fetching FINVIZ analyst targets (public scrape)...")
        try:
            _finviz_fetcher_dir = (Path(__file__).resolve().parent.parent.parent
                                   / "finviz-target-fetcher" / "scripts")
            sys.path.insert(0, str(_finviz_fetcher_dir))
            from fetch_finviz_targets import fetch_targets as _fetch_finviz
            _finviz_tickers = set()
            for r in (recommendations_list or []):
                tier = r.get("rating_tier") or 0
                if tier >= 3 and r.get("ticker"):
                    _finviz_tickers.add(str(r["ticker"]).upper())
            # Also include currently-held tickers via a peek at accounts.json
            # (positions haven't been fetched yet at this stage — cheap file read)
            _accounts_p = snapshot_dir / "accounts.json"
            if _accounts_p.exists():
                import json as _json
                try:
                    _accts = _json.loads(_accounts_p.read_text(encoding="utf-8"))
                    for _acct in _accts if isinstance(_accts, list) else _accts.get("accounts", []):
                        for _pos in _acct.get("positions", []) or []:
                            _tk = (_pos.get("symbol") or _pos.get("underlying") or "").upper()
                            if _tk and "_" not in _tk:  # skip option contract symbols
                                _finviz_tickers.add(_tk)
                except Exception:
                    pass
            if _finviz_tickers:
                _fv_targets, _fv_stats = _fetch_finviz(
                    _finviz_tickers, cache_dir=snapshot_dir,
                )
                print(f"[Step 1.7]   {_fv_stats.successful} fetched · "
                      f"{len([v for v in _fv_targets.values() if v])} with data · "
                      f"{_fv_stats.blocked} blocked · daily_cap_hit={_fv_stats.daily_cap_hit}")
            else:
                print("[Step 1.7]   no tickers to fetch (no Parkev tier-3+ recs)")
        except Exception as _e:
            print(f"[Step 1.7] FINVIZ fetch failed (non-fatal): {_e}")

        # Step 1.8: Claude/Autopilot portfolio — SECOND rec source (task
        # #45). Live page fetch → 24h cache → manual seed, provenance-
        # labeled; deliberately low weight downstream (CP chip + the
        # Parkev-agreement playbook bonus only). Fail-open like Parkev's.
        print("[Step 1.8] Fetching Claude/Autopilot portfolio...")
        claude_portfolio: dict = {}
        try:
            from steps.fetch_claude_portfolio import fetch_claude_portfolio_step
            claude_portfolio = fetch_claude_portfolio_step(snapshot_dir, config)
        except Exception as _cpe:
            print(f"[Step 1.8] Claude-portfolio fetch failed (non-fatal): {_cpe}")

        # Step 2: Snapshot inputs
        print("[Step 2] Snapshotting inputs...")
        # Build candidate chain-fetch list from Parkev BUY+ tickers so every
        # actionable recommendation downstream has a live CSP ticket
        # (CLAUDE.md rules #19 + #24). Without this, candidates like UBER
        # show "E*TRADE chain unavailable — no CSP ticket" because the
        # snapshot only fetched chains for currently-held option underlyings.
        # Scope: tier ≥ 3 (BUY, Top 25/15/12, Top Stock) — about 95-100 names
        # from Parkev's 191-rec sheet. Adds ~30-80 chain fetches; with 16
        # parallel workers, ~3-4s extra runtime. Worth it for chain coverage
        # on every actionable rec.
        extra_chain_tickers: set[str] = set()
        for r in (recommendations_list or []):
            tier = r.get("rating_tier") or 0
            if tier >= 3:  # BUY (3), Top 25/15/12 (4), Top Stock (5)
                tk = r.get("ticker")
                if tk:
                    extra_chain_tickers.add(str(tk).upper())

        try:
            snapshot_data = snapshot_inputs(
                config, snapshot_dir,
                etrade_fixture=args.etrade_fixture,
                etrade_live=args.etrade_live,
                extra_chain_underlyings=sorted(extra_chain_tickers),
            )
        except Exception as _snap_err:
            # 401 from E*TRADE = token hard-expired past midnight ET.
            # Surface the fix instead of just re-raising the raw traceback.
            _err_str = str(_snap_err)
            if "401" in _err_str or "Unauthorized" in _err_str:
                print("\n" + "=" * 72)
                print("E*TRADE TOKEN EXPIRED (401 Unauthorized)")
                print("=" * 72)
                print("Your OAuth 1.0a token has hit its midnight-ET hard expiry.")
                print("This needs a fresh browser handshake — a renewal call won't fix it.")
                print("")
                print("To re-authenticate, run in a terminal:")
                print("")
                print("  cd ~/workspace/portfolio-briefing")
                print("  uv run python skills/daily-portfolio-briefing/scripts/etrade_auth.py --authenticate")
                print("")
                print("This opens the E*TRADE authorize URL in your browser, you approve,")
                print("paste the verifier code back, and the pipeline resumes.")
                print("=" * 72 + "\n")
            raise
        # Make recommendations available to downstream skills via snapshot_data
        snapshot_data["recommendations_list"] = recommendations_list
        # Task #45 — Claude/Autopilot portfolio rides along for the CP chip
        # (parkev_chip) and the playbook agreement bonus (rotation_playbook).
        snapshot_data["claude_portfolio"] = claude_portfolio

        # Step 3: Classify regime
        print("[Step 3] Classifying regime...")
        regime_data = classify_regime(snapshot_dir, snapshot_data)

        # Step 4: Review equities
        print("[Step 4] Reviewing equity positions...")
        equity_reviews = review_equities(
            snapshot_data,
            regime_data,
            directives_active,
            recommendations_list,
            snapshot_dir,
        )

        # Step 5: Review options
        print("[Step 5] Reviewing options book...")
        options_reviews = review_options(
            snapshot_data, regime_data, directives_active, snapshot_dir
        )

        # Step 5.5: Portfolio capacity gates — hard blocks on new short-put
        # entries (06-wheel-parameters.md §7A). Evaluated BEFORE any new entry
        # is proposed anywhere in the pipeline (new-ideas panel, candidates
        # file, when-to-enter file).
        print("[Step 5.5] Evaluating portfolio capacity gates...")
        gate_state = evaluate_gates(
            snapshot_data.get("positions", []) or [],
            (snapshot_data.get("balance", {}) or {}).get("cash", 0) or 0,
            (snapshot_data.get("balance", {}) or {}).get("accountValue", 0) or 0,
            config,
        )
        print(f"  {gate_state.banner}")

        # Step 6: New ideas
        print("[Step 6] Generating new ideas...")
        new_ideas = generate_new_ideas(
            snapshot_data,
            regime_data,
            directives_active,
            config,
            snapshot_dir,
            recommendations_list=recommendations_list,
            gate_state=gate_state,
        )

        # Step 6.5: Long-term opportunities (3-12mo horizon)
        print("[Step 6.5] Generating long-term opportunities...")
        long_term_ops = generate_long_term_opportunities_step(
            snapshot_data,
            recommendations_list,
            config,
            gate_state=gate_state,
        )
        print(f"  Surfaced {len(long_term_ops)} long-term opportunity signal(s)")

        # Step 6.6: Thematic scout (cached 24h to keep the daily briefing fast)
        print("[Step 6.6] Running thematic scout (cached 24h)...")
        recs_map = {}
        held_weights = {}
        existing_short_puts: dict = {}
        nlv = float(snapshot_data.get("balance", {}).get("accountValue", 0) or 0)
        # Build recs_map carrying the FULL rec dict (recommendation + rating_tier
        # + aging) so the scout can surface tier-5 "Top Stock to Buy" distinct
        # from tier-3 "Buy" rather than collapsing both into the same BUY signal.
        # The scout accepts either {ticker: "BUY"} (legacy) or {ticker: {...full
        # dict...}} (preferred) for backward compatibility.
        for r in (recommendations_list or []):
            t = r.get("ticker")
            rec = r.get("recommendation")
            if t and rec:
                recs_map[str(t).upper()] = {
                    "recommendation": str(rec).upper(),
                    "rating_tier": r.get("rating_tier"),
                    "raw_recommendation": r.get("raw_recommendation"),
                    # CLAUDE.md hard rule #26 — Parkev's conviction modulates
                    # both badge promotion and sizing guidance downstream.
                    "conviction": r.get("conviction"),
                    "conviction_score": r.get("conviction_score"),
                    "aging": bool(r.get("aging")),
                    "age_days": r.get("age_days"),
                    "date_updated": r.get("date_updated"),
                }
        for p in (snapshot_data.get("positions") or []):
            if p.get("assetType") == "EQUITY":
                sym = (p.get("symbol") or "").upper()
                qty = float(p.get("qty", 0) or 0)
                price = float(p.get("price", 0) or 0)
                if sym and qty > 0 and price > 0 and nlv > 0:
                    held_weights[sym] = held_weights.get(sym, 0) + (qty * price / nlv * 100)
            elif p.get("assetType") == "OPTION" and (p.get("type") or "").upper() == "PUT":
                # Short-put tally for the scout's put-stack guard
                qty = float(p.get("qty", 0) or 0)
                if qty >= 0:
                    continue
                t = (p.get("underlying") or "").upper()
                if not t:
                    continue
                entry = existing_short_puts.setdefault(t, {"count": 0, "strikes": []})
                entry["count"] += abs(qty)
                entry["strikes"].append(float(p.get("strike", 0) or 0))
        # thematic_scout knobs (task #9): parallel defaults ON (pure speedup,
        # same per-ticker logic); flip parallel:false in briefing.yaml to fall
        # back to the sequential loop if yfinance ever rate-limits.
        _ts_cfg = config.get("thematic_scout") or {}
        scout_payload = run_thematic_research(
            snapshot_dir=snapshot_dir,
            recs_map=recs_map,
            held_weights=held_weights,
            existing_short_puts=existing_short_puts,
            refresh=args.refresh_scout,
            ttl_hours=24,
            parallel=bool(_ts_cfg.get("parallel", True)),
            max_workers=int(_ts_cfg.get("max_workers", 8)),
        )

        # Step 7.4: Fill reconciliation + recommendation aging (spec Step 7.5).
        # Reconcile YESTERDAY's action list against actual position diffs
        # between daily snapshots (etrade-mcp has no list_transactions; the
        # position diff + open_orders.json are the broker evidence). The
        # resulting context is threaded into aggregate_briefing → the action
        # list renderer ages each item (⏳ tags at 3 days, binary prompt at 5,
        # ⛔ Stalled panel at 6+) and the state file is persisted after render.
        print("[Step 7.4] Fill reconciliation + recommendation aging...")
        aging_info = None
        # Test/fixture runs use a separate aging-state file so they can't
        # advance the real day counters (same rationale as the .test snapshot dir).
        rec_aging_state_path = (
            Path("state/rec_aging.test.yaml")
            if (args.etrade_fixture or args.dry_run)
            else Path("state/rec_aging.yaml")
        )
        try:
            from analysis import rec_aging
            aging_info = rec_aging.build_aging_context(
                today_date_str,
                snapshot_root=snapshot_dir.parent,
                state_path=rec_aging_state_path,
                today_positions=snapshot_data.get("positions"),
                open_orders=snapshot_data.get("open_orders"),
            )
            _recon = aging_info.get("reconciliation") or {}
            if _recon:
                _counts: dict = {}
                for _st in _recon.values():
                    _counts[_st] = _counts.get(_st, 0) + 1
                _summary = ", ".join(f"{v} {k}" for k, v in sorted(_counts.items()))
                print(f"  Reconciled {len(_recon)} prior action(s) vs "
                      f"{aging_info.get('prev_date')}: {_summary}")
            else:
                print("  No prior actions to reconcile (first run or no prior briefing).")
        except Exception as _ae:
            print(f"  WARNING: recommendation aging unavailable: {_ae}", file=sys.stderr)
            aging_info = None

        # Step 7: Consistency check
        print("[Step 7] Day-over-day consistency check...")
        consistency_report, flagged_inconsistencies = check_consistency(
            yesterday_briefing_path,
            equity_reviews,
            options_reviews,
            snapshot_dir,
        )

        # Step 7.5: Capital plan — aggregate every recommendation's cash flow,
        # rank by tier, filter long-term ideas by concentration. Runs after
        # all advisors have produced their recommendations and before render.
        # The analytics dict comes from inside aggregate_briefing's compute_analytics
        # call, so we build a minimal one here. (Future refactor: hoist the
        # analytics call out of aggregate_briefing.)
        print("[Step 7.5] Building capital plan...")
        capital_plan_dict = build_capital_plan_step(
            balance=snapshot_data.get("balance", {}),
            positions=snapshot_data.get("positions", []),
            equity_reviews=equity_reviews,
            options_reviews=options_reviews,
            new_ideas=new_ideas,
            long_term_opportunities=long_term_ops,
            analytics=None,  # populated inside aggregate_briefing — re-runs there
            recommendations_list=recommendations_list,
        )
        if capital_plan_dict:
            print(
                f"  Capital plan: starting ${capital_plan_dict['starting_cash']:,.0f} → "
                f"projected ${capital_plan_dict['ending_cash_projected']:,.0f} "
                f"({capital_plan_dict['active_actions']} active, "
                f"{capital_plan_dict['skipped_actions']} skipped)"
            )

        # Step 7.5: Rotation Advisor — find better entries than what user holds.
        # Cross-references current holdings against theme peers + Parkev
        # + FMP FV + FINVIZ target to surface "close SOFI, buy UBER" swaps.
        # Fail-open: any missing input → skip that flavor, still render the
        # rotation panel with the flavors that had data.
        print("[Step 7.5] Computing rotation opportunities...")
        rotation_opportunities: dict = {"equity": [], "options": [], "capital_plan": [], "stats": {}}
        try:
            from analysis import rotation_advisor
            import yaml as _yaml
            # Load theme universes from the thematic-scout skill
            _theme_path = (Path(__file__).resolve().parent.parent.parent
                           / "thematic-scout" / "references" / "theme_universes.yaml")
            _theme_universes = {}
            if _theme_path.exists():
                with open(_theme_path) as _f:
                    _theme_universes = _yaml.safe_load(_f) or {}
            # Assemble holdings dict from equity_reviews
            _holdings = []
            for r in equity_reviews or []:
                # Handle both dict rows and pydantic-like objects
                _tk = r.get("ticker") if isinstance(r, dict) else getattr(r, "ticker", None)
                _w = r.get("weight") if isinstance(r, dict) else getattr(r, "weight", None)
                if not _tk:
                    continue
                _tier = None
                try:
                    from analysis.position_tiers import tier_for
                    _tier = tier_for(_tk, config)
                except Exception:
                    pass
                _holdings.append({
                    "ticker": _tk,
                    "weight_pct": (_w or 0) * 100,
                    "tier": _tier or "C",
                })
            # Options list from options_reviews
            _options_raw = [
                (r if isinstance(r, dict) else r.__dict__)
                for r in (options_reviews or [])
            ]
            _technicals = snapshot_data.get("technicals") or {}
            _recs_map = {(r.get("ticker") or "").upper(): r
                        for r in (recommendations_list or []) if r.get("ticker")}
            # FMP fair values — best-effort read from the cache
            # (snap_root = the snapshot ROOT dir, parent of the dated dir —
            #  regression 2026-08-04: this name was never defined here and
            #  the whole rotation advisor died with NameError every run.)
            snap_root = snapshot_dir.parent if snapshot_dir else Path("state/briefing_snapshots")
            _fv_by_ticker = {}
            _fv_cache = snap_root / "intrinsic_value_cache.json"
            if _fv_cache.exists():
                try:
                    _fv_by_ticker = {
                        k.upper(): v for k, v in
                        (json.loads(_fv_cache.read_text()).items())
                    }
                except Exception:
                    pass
            # FINVIZ cache
            _finviz_targets = {}
            _fv_finviz_cache = snap_root / ".finviz_target_cache.json"
            if _fv_finviz_cache.exists():
                try:
                    _raw = json.loads(_fv_finviz_cache.read_text())
                    _finviz_targets = {
                        k.upper(): (v if v and not v.get("_null") else None)
                        for k, v in _raw.items()
                    }
                except Exception:
                    pass
            _stress_cov = None
            try:
                _stress_cov = (snapshot_data.get("stress_coverage") or {}).get("coverage_ratio")
            except Exception:
                pass
            rotation_opportunities = rotation_advisor.build_rotation_opportunities(
                holdings=_holdings,
                options=_options_raw,
                theme_universes=_theme_universes,
                technicals=_technicals,
                recommendations=_recs_map,
                finviz_targets=_finviz_targets,
                fv_by_ticker=_fv_by_ticker,
                stress_coverage=_stress_cov,
            )
            print(f"[Step 7.5]   {rotation_opportunities['stats']['equity_count']} equity · "
                  f"{rotation_opportunities['stats']['options_count']} option · "
                  f"{rotation_opportunities['stats']['capital_moves']} capital moves")
        except Exception as _e:
            print(f"[Step 7.5] rotation advisor failed (non-fatal): {_e}")

        # Step 8: Aggregate and render
        print("[Step 8] Aggregating and rendering briefing...")
        briefing_markdown, briefing_json = aggregate_briefing(
            today_date_str,
            config,
            snapshot_data,
            regime_data,
            equity_reviews,
            options_reviews,
            new_ideas,
            consistency_report,
            flagged_inconsistencies,
            directives_active,
            directives_expired,
            snapshot_dir,
            long_term_opportunities=long_term_ops,
            capital_plan=capital_plan_dict,
            scout_payload=scout_payload,
            gate_state=gate_state,
            aging_info=aging_info,
        )

        # Attach rotation opportunities to the briefing JSON so the web
        # app + downstream consumers can render the Rotations tab.
        # Attached AFTER aggregate_briefing since aggregate never sees it.
        if isinstance(briefing_json, dict):
            briefing_json["rotation_opportunities"] = rotation_opportunities

        # Step 8.8: CSP rotation recommender (task #20). The computation runs
        # INSIDE aggregate_briefing (it needs the full analytics dict for
        # coverage + expiration-bucket math) and lands in
        # briefing_json["csp_rotations"]. Here we guarantee the key exists for
        # downstream consumers (webapp Rotations tab) and log the result.
        # Fail-open: any error leaves csp_rotations = [] and never blocks.
        try:
            csp_rotations = []
            if isinstance(briefing_json, dict):
                csp_rotations = briefing_json.get("csp_rotations")
                if not isinstance(csp_rotations, list):
                    csp_rotations = []
                briefing_json["csp_rotations"] = csp_rotations
            print(f"[Step 8.8] CSP rotations: {len(csp_rotations)} "
                  f"coverage-neutral swap(s) surfaced")
        except Exception as e:
            csp_rotations = []
            print(f"[csp-rotation] failed (non-fatal): {e}", file=sys.stderr)

        # Step 9: Quality gate
        print("[Step 9] Running quality gate...")
        quality_issues = run_quality_gate(briefing_markdown)
        if quality_issues:
            print(f"WARNING: Quality gate found {len(quality_issues)} issues:")
            for issue in quality_issues[:5]:
                print(f"  - {issue}")
            # In v1, we don't fail on quality issues, just warn
            # In v2, we'd mark as DRAFT
            is_draft = True
        else:
            is_draft = False

        # Step 10: Surface to user
        print("[Step 10] Surfacing to user...")

        if args.dry_run:
            print("\n=== DRY RUN OUTPUT ===\n")
            print(briefing_markdown[:1000])
            print("\n... (truncated for brevity)\n")
            print(f"Would write to: {args.output or f'reports/daily/briefing_{today_date_str}.md'}")
            return 0

        # Write files
        if not args.output:
            output_path = Path("reports/daily") / f"briefing_{today_date_str}.md"
        else:
            output_path = Path(args.output)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        suffix = ".DRAFT" if is_draft else ""
        actual_output_path = output_path.parent / f"{output_path.stem}{suffix}{output_path.suffix}"

        with open(actual_output_path, "w") as f:
            f.write(briefing_markdown)

        json_output_path = actual_output_path.with_suffix(".json")
        with open(json_output_path, "w") as f:
            json.dump(briefing_json, f, indent=2, default=json_default)

        # Persist the recommendation-aging state (skipped on --dry-run above,
        # so re-renders never double-increment days_flagged).
        if aging_info is not None and aging_info.get("updated_state") is not None:
            try:
                from analysis import rec_aging as _ra_save
                _ra_save.save_state(
                    rec_aging_state_path,
                    aging_info["updated_state"],
                    updated=today_date_str,
                )
                print(f"Recommendation-aging state: {rec_aging_state_path}")
            except Exception as _se:
                print(f"  WARNING: failed to persist rec_aging state: {_se}",
                      file=sys.stderr)

        print(f"\nBriefing written to: {actual_output_path}")
        print(f"Machine-readable JSON: {json_output_path}")

        # Step 8.5: Per-company Candidate Research report (separate dated file).
        # Reuses the scout's research; adds FMP valuation (cached) + RSI-gated
        # entries. Fail-soft — never blocks the briefing.
        try:
            import os as _os_c
            import shutil as _shutil_c
            from datetime import datetime as _dt_c
            from steps import candidate_research as _cand
            from analysis import intrinsic_value as _iv_c

            _cand_tickers = _cand.single_stock_tickers(scout_payload, config)
            _cand_fv: dict = {}
            _fmp_c = _os_c.getenv("FMP_API_KEY")
            if _cand_tickers and _fmp_c:
                _cand_fv = _iv_c.get_fair_values(
                    _cand_tickers,
                    cache_path=snapshot_dir.parent / "intrinsic_value_cache.json",
                    api_key=_fmp_c,
                )
            # Test/fixture runs write verdict state to a side file so the
            # flip-audit history of real runs is never polluted.
            _verdict_path = None
            if args.etrade_fixture or args.dry_run:
                _verdict_path = Path("state/scout_verdicts.test.yaml")
            _cand_md = _cand.render_candidate_report(
                scout_payload, fv_by_ticker=_cand_fv, config=config,
                generated_at=_dt_c.now().strftime("%A, %B %d, %Y · %I:%M %p"),
                gate_state=gate_state,
                **({"verdict_state_path": _verdict_path} if _verdict_path else {}),
            )
            # S/R annotation pass — appends "S: $X · R: $Y" to the candidate
            # research cards. Merges snapshot technicals (held names) WITH the
            # scout cache (all theme companies) so every card gets a level read.
            # Fail-closed when SR is unavailable for a ticker.
            try:
                from analysis import support_resistance as _sr_cand
                _sr_by_cand: dict = {}
                if scout_payload:
                    for _theme_key, _results in (scout_payload.get("results_by_theme") or {}).items():
                        for _r in (_results or []):
                            if not isinstance(_r, dict):
                                continue
                            _tk_s = (_r.get("ticker") or "").upper()
                            _sr_s = _r.get("support_resistance")
                            if _tk_s and isinstance(_sr_s, dict):
                                _sr_by_cand[_tk_s] = _sr_s
                _tech_cand = snapshot_data.get("technicals") or {}
                for _sym_c, _t_c in _tech_cand.items():
                    if isinstance(_t_c, dict) and isinstance(_t_c.get("support_resistance"), dict):
                        _sr_by_cand[str(_sym_c).upper()] = _t_c["support_resistance"]
                if _sr_by_cand:
                    _cand_md = _sr_cand.annotate_briefing(_cand_md, _sr_by_cand)
            except Exception as _se:
                print(f"  WARNING: S/R annotation on candidate report failed: {_se}", file=sys.stderr)
            # Task #43 — honest IV labels on the standalone report too: legacy
            # "IV rank N" tokens become "IV 34% · IVrank 62 · RVrank N" (true
            # chain IV) or "RVrank N (realized-vol proxy)". Fail-open.
            try:
                from analysis import chain_iv as _civ_cand
                if _civ_cand.load_chain_iv_config(config)["enabled"]:
                    _cand_md, _ = _civ_cand.annotate_briefing(
                        _cand_md, snapshot_data.get("chain_iv") or {},
                        snapshot_data.get("iv_ranks") or {}, config)
            except Exception as _cie:
                print(f"  WARNING: chain-IV annotation on candidate report failed: {_cie}", file=sys.stderr)
            _cand_path = output_path.parent / f"candidates_{today_date_str}.md"
            _cand_path.write_text(_cand_md)
            print(f"Candidate research: {_cand_path}")
            if not args.no_delivery and not is_draft:
                _deliv = (Path(args.delivery_dir).expanduser() if args.delivery_dir
                          else Path(_os_c.getenv("PORTFOLIO_BRIEFING_DELIVERY_DIR",
                                                 str(Path.home() / "Documents" / "briefings"))).expanduser())
                _deliv.mkdir(parents=True, exist_ok=True)
                _shutil_c.copy(_cand_path, _deliv / _cand_path.name)
        except Exception as _ce:
            print(f"  WARNING: candidate research failed: {_ce}", file=sys.stderr)

        # Step 8.6: When-To-Enter report — every theme company classified into
        # ENTRY NOW / WAIT (with explicit RSI+pullback trigger) / WATCH / AVOID.
        # Reuses the same scout cache; no extra fetches. Fail-soft.
        try:
            import os as _os_w
            import shutil as _shutil_w
            from datetime import datetime as _dt_w
            from steps import when_to_enter as _wte

            # Build sr_by_sym from BOTH the snapshot's technicals AND the scout
            # cache, so every theme company (~120 tickers, not just held names)
            # gets explicit support/resistance levels in its WTE trigger text
            # instead of generic "X-Y% pullback" boilerplate. Snapshot wins on
            # conflict (it's the freshest read for held positions); scout fills
            # in everyone else.
            _tech_wte = snapshot_data.get("technicals") or {}
            _sr_wte: dict = {}
            # Scout first (lower priority).
            if scout_payload:
                for _theme_key, _results in (scout_payload.get("results_by_theme") or {}).items():
                    for _r in (_results or []):
                        if not isinstance(_r, dict):
                            continue
                        _tk_s = (_r.get("ticker") or "").upper()
                        _sr_s = _r.get("support_resistance")
                        if _tk_s and isinstance(_sr_s, dict):
                            _sr_wte[_tk_s] = _sr_s
            # Snapshot second (higher priority, overrides scout).
            for _sym_w, _t_w in _tech_wte.items():
                if isinstance(_t_w, dict) and isinstance(_t_w.get("support_resistance"), dict):
                    _sr_wte[str(_sym_w).upper()] = _t_w["support_resistance"]
            _wte_verdict_path = None
            if args.etrade_fixture or args.dry_run:
                _wte_verdict_path = Path("state/scout_verdicts.test.yaml")
            _wte_md = _wte.render_when_to_enter_report(
                scout_payload, config=config,
                generated_at=_dt_w.now().strftime("%A, %B %d, %Y · %I:%M %p"),
                sr_by_sym=_sr_wte,
                gate_state=gate_state,
                **({"verdict_state_path": _wte_verdict_path} if _wte_verdict_path else {}),
            )
            # Task #43 — honest IV labels (same pass as the candidate report).
            try:
                from analysis import chain_iv as _civ_wte
                if _wte_md and _civ_wte.load_chain_iv_config(config)["enabled"]:
                    _wte_md, _ = _civ_wte.annotate_briefing(
                        _wte_md, snapshot_data.get("chain_iv") or {},
                        snapshot_data.get("iv_ranks") or {}, config)
            except Exception as _cie_w:
                print(f"  WARNING: chain-IV annotation on when-to-enter report failed: {_cie_w}", file=sys.stderr)
            if _wte_md:
                _wte_path = output_path.parent / f"when_to_enter_{today_date_str}.md"
                _wte_path.write_text(_wte_md)
                print(f"When-to-enter report: {_wte_path}")
                if not args.no_delivery and not is_draft:
                    _deliv_w = (Path(args.delivery_dir).expanduser() if args.delivery_dir
                                else Path(_os_w.getenv("PORTFOLIO_BRIEFING_DELIVERY_DIR",
                                                       str(Path.home() / "Documents" / "briefings"))).expanduser())
                    _deliv_w.mkdir(parents=True, exist_ok=True)
                    _shutil_w.copy(_wte_path, _deliv_w / _wte_path.name)
        except Exception as _we:
            print(f"  WARNING: when-to-enter report failed: {_we}", file=sys.stderr)

        # Step 8.7: Broad-universe screener — daily wheel setups on names NOT
        # already covered by Parkev's list or the Scout themes. Opt-in via
        # briefing.yaml → screeners.broad_universe.enabled. Fail-open: any
        # screener error is a warning, never blocks the briefing.
        try:
            _bu_cfg = (config.get("screeners") or {}).get("broad_universe") or {}
            if _bu_cfg.get("enabled"):
                print("[Step 8.7] Broad-universe screener...")
                import importlib.util as _ilu_bu
                import os as _os_bu
                import shutil as _shutil_bu
                _bu_path = (Path(__file__).resolve().parents[2]
                            / "broad-universe-screener" / "scripts" / "screen_universe.py")
                _bu_spec = _ilu_bu.spec_from_file_location("broad_universe_screener", _bu_path)
                _bu_mod = _ilu_bu.module_from_spec(_bu_spec)
                sys.modules["broad_universe_screener"] = _bu_mod
                _bu_spec.loader.exec_module(_bu_mod)

                _bu_config = _bu_mod.load_config(None)
                # Output cache (task #9): reuse a same-day, same-config result
                # younger than cache_ttl_hours instead of re-running the whole
                # FMP scan + yfinance deep-dive. The banner + fair values below
                # are ALWAYS re-computed fresh; fail-open (any cache problem →
                # full run). Disable via screeners.broad_universe.cache_enabled.
                _bu_result, _bu_from_cache = _bu_mod.run_screener_cached(
                    _bu_config,
                    date_str=today_date_str,
                    cache_dir=_bu_mod.REPO_ROOT / "state" / "cache",
                    cache_enabled=bool(_bu_cfg.get("cache_enabled", True)),
                    ttl_hours=float(_bu_cfg.get("cache_ttl_hours", 6)),
                )
                if _bu_from_cache:
                    print("  Broad-universe screener: cache hit — reused "
                          f"today's result ({len(_bu_result.hits)} setups, "
                          "FMP + yfinance calls skipped)")
                _bu_mod.annotate_fair_values(
                    _bu_result.hits, api_key=_os_bu.getenv("FMP_API_KEY"),
                    cache_path=snapshot_dir.parent / "intrinsic_value_cache.json",
                )
                _bu_md = _bu_mod.render_report(
                    _bu_result, date_str=today_date_str,
                    capacity_banner=(gate_state.banner if gate_state is not None else None),
                    capacity_open=(gate_state.open if gate_state is not None else True),
                )
                _bu_out = output_path.parent / f"daily_screener_{today_date_str}.md"
                _bu_out.write_text(_bu_md)
                print(f"Broad-universe screener: {_bu_out} "
                      f"({len(_bu_result.hits)} setups)")
                if not args.no_delivery and not is_draft:
                    _deliv_bu = (Path(args.delivery_dir).expanduser() if args.delivery_dir
                                 else Path(_os_bu.getenv("PORTFOLIO_BRIEFING_DELIVERY_DIR",
                                                         str(Path.home() / "Documents" / "briefings"))).expanduser())
                    _deliv_bu.mkdir(parents=True, exist_ok=True)
                    _shutil_bu.copy(_bu_out, _deliv_bu / _bu_out.name)
        except Exception as _bue:
            print(f"  WARNING: broad-universe screener failed: {_bue}", file=sys.stderr)

        if is_draft:
            print(f"\nWARNING: Briefing marked as DRAFT due to quality gate issues.")

        # Step 11: Delivery — copy released briefing to ~/Documents/briefings/
        if not args.no_delivery and not is_draft:
            print("[Step 11] Delivering briefing...")
            delivery_result = deliver_briefing(
                actual_output_path,
                json_path=json_output_path,
                delivery_dir=args.delivery_dir,
            )
            if delivery_result.get("error"):
                print(f"  WARNING: delivery failed: {delivery_result['error']}", file=sys.stderr)
            else:
                print(f"  Delivered to: {delivery_result.get('latest', delivery_result.get('dated'))}")
                if delivery_result.get("dated"):
                    print(f"  Dated copy: {delivery_result['dated']}")
        elif is_draft:
            print("[Step 11] Skipping delivery — DRAFT briefings are not delivered.")

        return 0

    except Exception as e:
        print(f"FATAL: Briefing failed at step: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
