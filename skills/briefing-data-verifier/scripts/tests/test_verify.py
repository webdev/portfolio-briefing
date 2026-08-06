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


# ── Task #43 fix 3 (2026-07-31): non-executable kinds are exempt ──────────

def test_hold_for_basis_not_flagged():
    """Observed (2026-07-31): the Live-Data Verification header listed
    'HOLD FOR BASIS QCOM / HOLD FOR BASIS PLTR / HOLD FOR BASIS PLTR' as
    'lacking live E*TRADE chain attribution'. A HOLD item carries no order
    — there is nothing to verify at a broker."""
    md = """
## Today's Action List

6. **HOLD FOR BASIS** QCOM_PUT_185_20261218 — assignment acceptable (exit-cost verdict); no roll ticket
   - _Roll skipped: assignment basis $144.37 is 4.3% below market — holding for basis._
7. **HOLD FOR BASIS** PLTR_PUT_130_20270115 — assignment acceptable (exit-cost verdict); no roll ticket
"""
    r = verify_live_data(md)
    assert r.verified is True
    assert r.stubbed_actions == 0
    assert not any("HOLD FOR BASIS" in f for f in r.flagged_lines)


def test_roll_deferred_and_demoted_not_flagged():
    """ROLL DEFERRED (earnings block) and ⏸-tagged demotions are analysis
    only — 'analysis only, no order' — never an executable ticket."""
    md = """
## Today's Action List

3. ⏸ **ROLL DEFERRED (earnings block)** LITE_PUT_700_20260918 — Diagonal down-and-out: −$2,100 net debit — analysis only, no order
   - Reference legs (NOT an order): BTC 2× $700P ...
4. 🚨⏸ **URGENT — ROLL DEFERRED (earnings block)** QCOM_PUT_185_20261218 — analysis only, no order
"""
    r = verify_live_data(md)
    assert r.verified is True
    assert r.stubbed_actions == 0


def test_close_into_recovery_not_flagged():
    """CLOSE variants (CLOSE INTO RECOVERY etc.) price off the held
    position's own quote — the CLOSE-family exemption covers them."""
    md = """
## Today's Action List

2. **CLOSE INTO RECOVERY** LITE_PUT_700_20260918 — recovered to ~breakeven (+2.0% of premium) before the Aug 12 print; buy-to-close 2× limit $34.10 (≈ $6,820), GTC
"""
    r = verify_live_data(md)
    assert r.verified is True
    assert r.stubbed_actions == 0


def test_executable_roll_without_chain_still_flagged():
    """The exemption widening must NOT leak to executable tickets: an
    EXECUTE ROLL (incl. the URGENT — EXECUTE ROLL variant) without live
    chain attribution stays flagged."""
    md = """
## Today's Action List

1. **EXECUTE ROLL** GOOG_CALL_450 — Diagonal up-and-out: +$1,200 net credit
   - Order: Buy-to-Close 4×; Sell-to-Open 4× (no chain quotes rendered)
2. 🚨 **URGENT — EXECUTE ROLL** MU_PUT_950_20261218 — Diagonal down-and-out
   - Order: Buy-to-Close 1×; Sell-to-Open 1× (no chain quotes rendered)
"""
    r = verify_live_data(md)
    assert r.verified is False
    assert r.stubbed_actions == 2


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
