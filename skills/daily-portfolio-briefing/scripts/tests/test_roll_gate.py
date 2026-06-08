"""Action-list roll gate (CLAUDE.md #14): a covered call that's still OTM has no
real assignment risk, so the action list must NOT surface a roll that just
re-caps the bullish name or contradicts the advisor's HOLD. Real assignment risk
(spot at/through the strike) still surfaces the roll directive.

This is the SMH/SOXX/SPY bug: NEAR_ATM-but-OTM covered calls were surfacing
EXECUTE ROLL / ROLL_OUT_AND_UP even though their own advisor recommended HOLD and
the only credit-positive candidate was a same-strike re-cap.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from render.panels import render_action_list  # noqa: E402


def _snap(spot: float):
    return {
        "quotes": {"SMH": {"last": spot}},
        "technicals": {"SMH": {"rsi_14": 73.0}},
        "_config": {"core_positions": [], "accounts": []},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }


def _cc_review(spot_relation: str, with_candidates: bool):
    """A covered-call review with a ROLL_OUT_AND_UP decision and advisor HOLD."""
    rev = {
        "contract": "SMH_CALL_615_20260717",
        "underlying": "SMH",
        "type": "CALL",
        "strike": 615.0,
        "expiration": "2026-07-17",
        "qty": -1,
        "entry_price": 33.75,
        "current_mid": 35.0,
        "days_to_expiry": 52,
        "recommendation": "ROLL_OUT_AND_UP",
        "matrix_cell_id": "CALL_NORMAL_NEAR_ATM_ROLL_UP",
        "recommended_candidate_id": "A",  # advisor says HOLD
    }
    if with_candidates:
        # Best credit-positive candidate is a SAME-STRIKE calendar (a re-cap),
        # not a genuine roll-up — the SMH bug.
        rev["roll_candidates"] = [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "same-strike calendar",
             "instruction": {"sell_strike": 615.0, "sell_expiration": "2026-08-21",
                             "sell_mid": 48.0, "sell_bid": 47.9, "sell_ask": 48.1},
             "netDollars": 1300, "dteExtension": 35},
        ]
    return rev


def test_otm_covered_call_roll_suppressed_block4():
    """OTM covered call, no priced candidate → block #4 suppresses the directive."""
    md = "\n".join(render_action_list([], [_cc_review("otm", with_candidates=False)], [],
                                      snapshot_data=_snap(595.0),  # spot < strike → OTM
                                      date_str="2026-05-27"))
    assert "SMH_CALL_615" not in md          # no roll surfaced at all
    assert "ROLL_OUT_AND_UP" not in md


def test_otm_covered_call_roll_suppressed_block3():
    """OTM covered call whose best candidate is a same-strike re-cap → block #3
    suppresses (don't present a same-strike calendar as up-and-out)."""
    md = "\n".join(render_action_list([], [_cc_review("otm", with_candidates=True)], [],
                                      snapshot_data=_snap(595.0),
                                      date_str="2026-05-27"))
    assert "EXECUTE ROLL" not in md
    assert "SMH_CALL_615" not in md


def test_itm_covered_call_roll_still_surfaces():
    """A genuinely ITM covered call (real assignment risk) still surfaces the
    roll directive — the guard is narrow."""
    md = "\n".join(render_action_list([], [_cc_review("itm", with_candidates=False)], [],
                                      snapshot_data=_snap(640.0),  # spot > strike → ITM
                                      date_str="2026-05-27"))
    assert "SMH_CALL_615" in md
    assert "ROLL_OUT_AND_UP" in md


def test_block4_roll_renders_honest_unavailable_when_chain_unreachable():
    """When block #4 fires for an ITM call but the live chain is unreachable
    (sandbox / CI / weekends), the order line must say 'live chain unavailable,
    verify the STO leg at the broker' — NOT 'pick from the ROLL ANALYSIS table'
    (the old hand-wavy wording the user called out). Never fabricate strike /
    bid / mid / ask for the STO leg."""
    md = "\n".join(render_action_list([], [_cc_review("itm", with_candidates=False)], [],
                                      snapshot_data=_snap(640.0),
                                      date_str="2026-05-29"))
    assert "ROLL_OUT_AND_UP" in md
    # Either we got a real chain value (path A) OR we got the honest fallback
    # (path B). Both are valid; the OLD vague language must be absent either way.
    assert "pick the target from the **ROLL ANALYSIS** table" not in md
    assert "pick the target from the ROLL ANALYSIS table" not in md
    # BTC leg still shows its real mid in either path.
    assert "$35.00" in md  # current_mid set in _cc_review fixture
    # If chain is unreachable in this test env, we get the honest unavailable note.
    # If it's reachable somehow, we get a real STO quote with strike + bid/mid/ask.
    assert ("live chain unavailable, verify the STO leg at the broker" in md
            or "Live E*TRADE chain" in md)
