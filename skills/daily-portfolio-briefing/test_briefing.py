#!/usr/bin/env python3
"""Simple test to verify briefing pipeline works."""

import sys
import json
import tempfile
from pathlib import Path

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent / "scripts"))

from steps.preflight import run_preflight
from steps.load_directives import load_directives
from steps.fetch_recommendations import fetch_recommendations
from steps.snapshot_inputs import snapshot_inputs
from steps.classify_regime import classify_regime
from steps.review_equities import review_equities
from steps.review_options import review_options
from steps.new_ideas import generate_new_ideas
from steps.consistency_check import check_consistency
from steps.aggregate import aggregate_briefing
from steps.quality_gate import run_quality_gate


def test_full_pipeline():
    """Run full briefing pipeline with mock fixture."""

    print("=" * 70)
    print("DAILY PORTFOLIO BRIEFING — E2E TEST")
    print("=" * 70)

    skill_dir = Path(__file__).parent
    fixture_path = skill_dir / "assets" / "etrade_mock_fixture.json"
    config_path = skill_dir / "assets" / "briefing_config_template.yaml"

    assert fixture_path.exists(), f"Fixture not found: {fixture_path}"
    print(f"\n✓ Mock fixture found: {fixture_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        snapshot_dir = tmpdir / "briefing_snapshots" / "2026-05-07"
        snapshot_dir.mkdir(parents=True)

        print("\n[Step 1] Pre-flight")
        config, yesterday = run_preflight(str(config_path), etrade_fixture=str(fixture_path))
        print(f"  Config loaded, yesterday: {yesterday}")

        print("\n[Step 1.5] Load directives")
        directives_active, directives_expired = load_directives(snapshot_dir)
        print(f"  Active: {len(directives_active)}, Expired: {len(directives_expired)}")

        print("\n[Step 1.6] Fetch recommendations")
        recommendations = fetch_recommendations(snapshot_dir)
        print(f"  Fetched: {len(recommendations)} recommendations")

        print("\n[Step 2] Snapshot inputs")
        snapshot_data = snapshot_inputs(config, snapshot_dir, etrade_fixture=str(fixture_path))
        positions = snapshot_data.get("positions", [])
        equity_count = len([p for p in positions if p.get("assetType") == "EQUITY"])
        option_count = len([p for p in positions if p.get("assetType") == "OPTION"])
        print(f"  Snapshotted: {equity_count} equities, {option_count} options")

        print("\n[Step 3] Classify regime")
        regime = classify_regime(snapshot_dir, snapshot_data)
        print(f"  Regime: {regime['regime']} (confidence: {regime['confidence']})")

        print("\n[Step 4] Review equities")
        equity_reviews = review_equities(
            snapshot_data, regime, directives_active, recommendations, snapshot_dir
        )
        print(f"  Reviewed: {len(equity_reviews)} positions")

        print("\n[Step 5] Review options")
        options_reviews = review_options(snapshot_data, regime, directives_active, snapshot_dir)
        print(f"  Reviewed: {len(options_reviews)} contracts")

        print("\n[Step 6] New ideas")
        new_ideas = generate_new_ideas(
            snapshot_data, regime, directives_active, config, snapshot_dir
        )
        print(f"  Generated: {len(new_ideas)} ideas")

        print("\n[Step 7] Consistency check")
        consistency, inconsistencies = check_consistency(
            None, equity_reviews, options_reviews, snapshot_dir
        )
        print(f"  Inconsistencies: {len(inconsistencies)}")

        print("\n[Step 8] Aggregate and render")
        briefing_md, briefing_json = aggregate_briefing(
            "2026-05-07",
            config,
            snapshot_data,
            regime,
            equity_reviews,
            options_reviews,
            new_ideas,
            consistency,
            inconsistencies,
            directives_active,
            directives_expired,
            snapshot_dir,
        )
        print(f"  Markdown: {len(briefing_md)} chars")
        print(f"  JSON keys: {list(briefing_json.keys())}")

        print("\n[Step 9] Quality gate")
        issues = run_quality_gate(briefing_md)
        if issues:
            print(f"  ⚠ {len(issues)} issues found:")
            for issue in issues[:3]:
                print(f"    - {issue}")
        else:
            print(f"  ✓ No quality issues")

        print("\n" + "=" * 70)
        print("TEST RESULTS")
        print("=" * 70)

        # Verify key sections
        required_sections = [
            "# Daily Briefing",
            "## Health",
            "## Performance",
            "## Watch / Portfolio Review",
        ]

        missing_sections = [s for s in required_sections if s not in briefing_md]
        if missing_sections:
            print(f"\n✗ FAILED: Missing sections: {missing_sections}")
            return False

        print("\n✓ All required sections present")
        print(f"  - {len(equity_reviews)} equity recommendations")
        print(f"  - {len(options_reviews)} options recommendations")
        print(f"  - {len(new_ideas)} new ideas")
        print(f"\n✓ Briefing generation successful!")

        # Print first part of markdown
        print("\n" + "=" * 70)
        print("BRIEFING PREVIEW (first 50 lines)")
        print("=" * 70)
        lines = briefing_md.split("\n")
        for line in lines[:50]:
            print(line)

        if len(lines) > 50:
            print(f"... ({len(lines) - 50} more lines)")

        return True


if __name__ == "__main__":
    try:
        success = test_full_pipeline()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
