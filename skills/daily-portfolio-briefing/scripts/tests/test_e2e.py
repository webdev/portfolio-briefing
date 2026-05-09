"""
End-to-end test: run full briefing pipeline with mock fixture.
"""

import json
import pytest
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

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


@pytest.fixture
def temp_config(tmp_path):
    """Create a minimal config file."""
    import yaml

    config = {
        "enabled_strategies": ["wheel"],
        "accounts": ["E123456"],
        "max_position_pct": 0.10,
        "max_sector_pct": 0.35,
    }

    config_file = tmp_path / "briefing.yaml"
    with open(config_file, "w") as f:
        yaml.dump(config, f)

    return config_file


def test_e2e_full_pipeline(tmp_path, temp_config):
    """Run full briefing pipeline with mock fixture."""

    # Setup: mock fixture path
    fixture_path = Path(__file__).parent.parent.parent / "assets" / "etrade_mock_fixture.json"
    assert fixture_path.exists(), f"Mock fixture not found at {fixture_path}"

    snapshot_dir = tmp_path / "briefing_snapshots" / "2026-05-07"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Pre-flight
    config, yesterday = run_preflight(str(temp_config), etrade_fixture=str(fixture_path))
    assert config is not None
    assert "enabled_strategies" in config

    # Step 1.5: Directives
    directives_active, directives_expired = load_directives(snapshot_dir)
    assert isinstance(directives_active, list)

    # Step 1.6: Recommendations
    recommendations_list = fetch_recommendations(snapshot_dir)
    assert isinstance(recommendations_list, list)

    # Step 2: Snapshot inputs
    snapshot_data = snapshot_inputs(config, snapshot_dir, etrade_fixture=str(fixture_path))
    assert snapshot_data["positions"] is not None
    assert len(snapshot_data["positions"]) == 5  # 3 equity + 2 option

    # Step 3: Regime
    regime_data = classify_regime(snapshot_dir, snapshot_data)
    # Regime must be a valid enum value; the actual classification depends on live VIX
    assert regime_data["regime"] in {"RISK_ON", "NORMAL", "CAUTION", "RISK_OFF"}
    assert regime_data["valid"] is True

    # Step 4: Equity reviews
    equity_reviews = review_equities(
        snapshot_data, regime_data, directives_active, recommendations_list, snapshot_dir
    )
    assert len(equity_reviews) == 3  # AAPL, MSFT, NVDA
    assert all(r.get("recommendation") for r in equity_reviews)

    # Step 5: Options reviews
    options_reviews = review_options(snapshot_data, regime_data, directives_active, snapshot_dir)
    assert len(options_reviews) == 2

    # Step 6: New ideas
    new_ideas = generate_new_ideas(
        snapshot_data, regime_data, directives_active, config, snapshot_dir
    )
    assert isinstance(new_ideas, list)

    # Step 7: Consistency check
    consistency_report, inconsistencies = check_consistency(
        None, equity_reviews, options_reviews, snapshot_dir
    )
    assert "note" in consistency_report

    # Step 8: Aggregate
    briefing_md, briefing_json = aggregate_briefing(
        "2026-05-07",
        config,
        snapshot_data,
        regime_data,
        equity_reviews,
        options_reviews,
        new_ideas,
        consistency_report,
        inconsistencies,
        directives_active,
        directives_expired,
        snapshot_dir,
    )
    assert len(briefing_md) > 100
    assert "# Daily Briefing" in briefing_md
    assert briefing_json["regime"] in {"RISK_ON", "NORMAL", "CAUTION", "RISK_OFF"}

    # Step 9: Quality gate
    issues = run_quality_gate(briefing_md)
    assert len(issues) == 0, f"Quality gate issues: {issues}"

    print(f"\nE2E Test Passed: Generated briefing with {len(equity_reviews)} equities, {len(options_reviews)} options")


def test_briefing_sections_present(tmp_path, temp_config):
    """Verify all required briefing sections are rendered."""

    fixture_path = Path(__file__).parent.parent.parent / "assets" / "etrade_mock_fixture.json"
    snapshot_dir = tmp_path / "briefing_snapshots" / "2026-05-07"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Run minimal pipeline
    config, _ = run_preflight(str(temp_config), etrade_fixture=str(fixture_path))
    snapshot_data = snapshot_inputs(config, snapshot_dir, etrade_fixture=str(fixture_path))
    regime_data = classify_regime(snapshot_dir, snapshot_data)
    equity_reviews = review_equities(snapshot_data, regime_data, [], [], snapshot_dir)
    options_reviews = review_options(snapshot_data, regime_data, [], snapshot_dir)
    new_ideas = generate_new_ideas(snapshot_data, regime_data, [], config, snapshot_dir)
    consistency_report, inconsistencies = check_consistency(
        None, equity_reviews, options_reviews, snapshot_dir
    )

    briefing_md, _ = aggregate_briefing(
        "2026-05-07",
        config,
        snapshot_data,
        regime_data,
        equity_reviews,
        options_reviews,
        new_ideas,
        consistency_report,
        inconsistencies,
        [],
        [],
        snapshot_dir,
    )

    required_sections = [
        "# Daily Briefing",
        "## Market Context",
        "## Health",
        "## Stress Test",
        "## Hedge Book",
        "## Risk Alerts",
        "## Today's Action List",
        "## Watch / Portfolio Review",
        "## Recommendation Changes Since Last Briefing",
        "## Inconsistencies Flagged",
        "## Appendix: Snapshot Manifest",
    ]

    for section in required_sections:
        assert section in briefing_md, f"Missing section: {section}"

    print(f"\nAll {len(required_sections)} required sections present")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
