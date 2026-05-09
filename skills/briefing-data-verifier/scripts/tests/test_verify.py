"""Tests for briefing-data-verifier."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from verify import verify_live_data


def test_close_actions_dont_need_chain_attribution():
    md = """
## Today's Action List

1. **CLOSE** ABNB_PUT_131_20260605 — +38% ($+113); buy-to-close limit $1.96
   - Yield: 10.5% ann.
"""
    r = verify_live_data(md)
    assert r.verified is True


def test_roll_with_bid_ask_tuple_passes():
    md = """
## Today's Action List

6. **EXECUTE ROLL** GOOG_CALL_450 — Diagonal up
   - Order: Buy-to-Close 4× $450C Fri Sep 17 '27 (current mid ~$53.54);
     Sell-to-Open 4× $500C Fri Dec 17 '27 (current bid $44.70 / mid $45.48 / ask $46.25).
"""
    r = verify_live_data(md)
    assert r.verified is True
    assert r.live_actions == 1


def test_pullback_csp_without_source_marker_flagged():
    md = """
## Today's Action List

15. **PULLBACK CSP** AMZN — sell $240P ~35d for ~$3.28 premium (would re-acquire 100 shares @ -12% below spot)
   - Yield: 14.2% ann.
   - Why: Core holding income trade.
"""
    r = verify_live_data(md)
    assert r.verified is False
    assert r.stubbed_actions == 1
    assert any("PULLBACK CSP" in f for f in r.flagged_lines)


def test_pullback_csp_with_source_marker_passes():
    md = """
## Today's Action List

15. **PULLBACK CSP** AMZN — sell $240P 30d for $3.28 premium
   - **Source:** Live E*TRADE chain
   - Yield: 14.2% ann.
"""
    r = verify_live_data(md)
    assert r.verified is True


def test_panel_lists_flagged_actions():
    md = """
## Today's Action List

15. **PULLBACK CSP** AMZN — ~$3.28 premium (would re-acquire 100 shares @ -12% below spot)
   - Yield: 14%
"""
    r = verify_live_data(md)
    assert "Live-Data Verification" in r.panel_md
    assert "PULLBACK CSP AMZN" in r.panel_md


def test_strict_mode_announces_suppression():
    md = """
## Today's Action List

15. **PULLBACK CSP** META — ~$7.31 premium (would re-acquire 100 shares @ -12% below spot)
"""
    r = verify_live_data(md, strict_mode=True)
    assert "SUPPRESSED" in r.panel_md.upper()


def test_handles_briefing_without_action_list():
    r = verify_live_data("# Daily Briefing\n\nNo actions today.\n")
    assert r.verified is True
    assert r.live_actions == 0
    assert r.stubbed_actions == 0


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
