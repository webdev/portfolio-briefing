"""Tests for briefing_diff.py — yesterday-vs-today action diff."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from analysis.briefing_diff import (
    parse_action_signatures,
    render_diff_panel,
)


def test_parse_extracts_action_signatures():
    md = """
# Daily Briefing

## Today's Action List

1. **CLOSE** ABNB_PUT_131_20260605 — +38% profit
   - Yield: 10%
2. **EXECUTE ROLL** GOOG_CALL_450 — Diagonal
   - Order: ...
3. **TRIM** NVDA — over 10% NLV

## Watch / Portfolio Review
"""
    sigs = parse_action_signatures(md)
    # Should pick up CLOSE/EXECUTE/TRIM action types and tickers
    assert any("CLOSE" in s for s in sigs)
    assert any("TRIM" in s for s in sigs)


def test_diff_renders_added_and_removed():
    today = """
## Today's Action List

1. **CLOSE** ABNB_PUT_131_20260605 — +38% profit
2. **EXECUTE ROLL** GOOG_CALL_450 — Diagonal

## Watch
"""
    yesterday = """
## Today's Action List

1. **CLOSE** ABNB_PUT_131_20260605 — +30%
2. **CLOSE** PLTR_PUT_135_20260605 — +30%

## Watch
"""
    panel = render_diff_panel(today, yesterday)
    text = "\n".join(panel)
    assert "Since Yesterday" in text
    assert "PLTR" in text  # removed
    assert "GOOG" in text  # added


def test_same_contract_changed_price_is_not_added_and_removed():
    """The MU bug: a recommendation whose only day-over-day change is the
    buy-to-close limit price must NOT be reported as both executed and new."""
    today = (
        "## Today's Action List\n\n"
        "1. **CLOSE** MU_PUT_700_20261218 — +33% ($+5,522); buy-to-close limit $115.34\n"
        "## Watch\n"
    )
    yesterday = (
        "## Today's Action List\n\n"
        "1. **CLOSE** MU_PUT_700_20261218 — +33% ($+5,399); buy-to-close limit $116.63\n"
        "## Watch\n"
    )
    panel = render_diff_panel(today, yesterday)
    text = "\n".join(panel)
    # No net change → the whole panel is suppressed (added/removed both empty).
    assert panel == []
    # And explicitly: MU is not double-counted.
    assert "Resolved or Executed" not in text
    assert "New Today" not in text


def test_signature_stable_across_price_and_profit_changes():
    from analysis.briefing_diff import _signature_for_action
    a = _signature_for_action("5. **CLOSE** MU_PUT_700_20261218 — +33% ($+5,522); buy-to-close limit $115.34")
    b = _signature_for_action("3. **CLOSE** MU_PUT_700_20261218 — +31% ($+5,399); buy-to-close limit $116.63")
    assert a == b == "CLOSE|MU_PUT_700_20261218"


def test_signature_handles_equity_and_hedge():
    from analysis.briefing_diff import _signature_for_action
    assert _signature_for_action("2. **EXIT** TSLA — SELL TSLA — exit position") == "EXIT|TSLA"
    # Hedge keys on the underlying, stable as strike/qty drift day to day.
    sig = _signature_for_action("9. **HEDGE** Buy 13× SPY put $711P Fri Jul 03 '26 (~$9,740)")
    assert sig == "HEDGE|SPY"


def test_diff_returns_empty_when_no_changes():
    md = "## Today's Action List\n\n1. **CLOSE** ABNB_PUT — +30%\n## Watch"
    panel = render_diff_panel(md, md)
    assert panel == []


def test_diff_returns_empty_when_no_yesterday():
    md = "## Today's Action List\n\n1. **CLOSE** ABNB — +30%\n## Watch"
    panel = render_diff_panel(md, None)
    assert panel == []


def test_handles_briefing_without_action_list():
    sigs = parse_action_signatures("# Just a header, no actions")
    assert sigs == set()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
