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
    snapshot_dir = Path("state/briefing_snapshots") / today_date_str
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

        # Step 2: Snapshot inputs
        print("[Step 2] Snapshotting inputs...")
        snapshot_data = snapshot_inputs(
            config, snapshot_dir,
            etrade_fixture=args.etrade_fixture,
            etrade_live=args.etrade_live,
        )
        # Make recommendations available to downstream skills via snapshot_data
        snapshot_data["recommendations_list"] = recommendations_list

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

        # Step 6: New ideas
        print("[Step 6] Generating new ideas...")
        new_ideas = generate_new_ideas(
            snapshot_data,
            regime_data,
            directives_active,
            config,
            snapshot_dir,
            recommendations_list=recommendations_list,
        )

        # Step 6.5: Long-term opportunities (3-12mo horizon)
        print("[Step 6.5] Generating long-term opportunities...")
        long_term_ops = generate_long_term_opportunities_step(
            snapshot_data,
            recommendations_list,
            config,
        )
        print(f"  Surfaced {len(long_term_ops)} long-term opportunity signal(s)")

        # Step 6.6: Thematic scout (cached 24h to keep the daily briefing fast)
        print("[Step 6.6] Running thematic scout (cached 24h)...")
        recs_map = {}
        held_weights = {}
        nlv = float(snapshot_data.get("balance", {}).get("accountValue", 0) or 0)
        for r in (recommendations_list or []):
            t = r.get("ticker")
            rec = r.get("recommendation")
            if t and rec:
                recs_map[str(t).upper()] = str(rec).upper()
        for p in (snapshot_data.get("positions") or []):
            if p.get("assetType") != "EQUITY":
                continue
            sym = (p.get("symbol") or "").upper()
            qty = float(p.get("qty", 0) or 0)
            price = float(p.get("price", 0) or 0)
            if sym and qty > 0 and price > 0 and nlv > 0:
                held_weights[sym] = held_weights.get(sym, 0) + (qty * price / nlv * 100)
        scout_payload = run_thematic_research(
            snapshot_dir=snapshot_dir,
            recs_map=recs_map,
            held_weights=held_weights,
            refresh=args.refresh_scout,
            ttl_hours=24,
        )

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
        )

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
            json.dump(briefing_json, f, indent=2)

        print(f"\nBriefing written to: {actual_output_path}")
        print(f"Machine-readable JSON: {json_output_path}")

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
