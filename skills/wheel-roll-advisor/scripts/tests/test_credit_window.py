"""Credit-window indicator (task #38, Part 2).

User symptom (2026-07-22 → 2026-07-29): "the MU $950P went from
credit-roll territory at the strike test to a $4,700 roll debit one week
later — in silence. Make the credit window visible and alert on the
transition."
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from credit_window import CreditWindow, assess_credit_window
from roll_target import RollCandidate


def _pos(strike=950.0, qty=1):
    return {"strikePrice": strike, "quantity": qty}


def _cand(cid, sell_strike, net_dollars, dte_ext=30, desc=None):
    return RollCandidate(
        id=cid,
        description=desc or f"1× ${sell_strike:.0f}P",
        instruction={"sell_strike": sell_strike, "sell_expiration": "2026-12-18",
                     "sell_mid": 1.0, "sell_bid": 0.9, "sell_ask": 1.1},
        close_cost=0.0, new_credit=0.0, net_dollars=net_dollars,
        dte_extension=dte_ext, delta_change=-0.30, notes="",
    )


def _hold():
    return RollCandidate(id="A", description="HOLD (don't roll)",
                         instruction=None, close_cost=0.0, new_credit=0.0,
                         net_dollars=0.0, dte_extension=0, delta_change=0.0,
                         notes="")


def test_credit_window_open_closing_debit():
    """Three candidate sets → three states, thresholded at $0.50/share."""
    cfg = {"closing_threshold_per_share": 0.50}

    # OPEN: best same-strike roll nets $185 on 1 contract = $1.85/share
    open_cw = assess_credit_window(
        _pos(), [_hold(), _cand("B", 950.0, 185.0), _cand("C", 900.0, -50.0)], cfg)
    assert open_cw.state == "open"
    assert open_cw.best_credit == pytest.approx(1.85)
    assert open_cw.best_candidate_desc == "1× $950P"
    assert open_cw.threshold_used == 0.50

    # CLOSING: best net $30 = $0.30/share, under the threshold
    closing_cw = assess_credit_window(
        _pos(), [_hold(), _cand("B", 950.0, 30.0), _cand("C", 900.0, -400.0)], cfg)
    assert closing_cw.state == "closing"
    assert closing_cw.best_credit == pytest.approx(0.30)

    # DEBIT_ONLY: every roll costs money
    debit_cw = assess_credit_window(
        _pos(), [_hold(), _cand("B", 950.0, -470.0), _cand("C", 900.0, -1200.0)], cfg)
    assert debit_cw.state == "debit_only"
    assert debit_cw.best_credit == pytest.approx(-4.70)


def test_credit_window_never_counts_roll_ups():
    """A big-credit put roll-UP (higher strike = deeper ITM = more risk) must
    never open the window (rule #42). Only the same-or-lower-strike economics
    count."""
    cands = [
        _hold(),
        _cand("B", 1000.0, 4200.0),   # bullish roll-up, huge credit — ignored
        _cand("C", 950.0, -120.0),    # same strike costs a debit
    ]
    cw = assess_credit_window(_pos(), cands, {})
    assert cw.state == "debit_only"
    assert cw.best_credit == pytest.approx(-1.20)


def test_credit_window_unknown_on_no_chain():
    """No priced candidates / only the HOLD row → unknown, never fabricated."""
    assert assess_credit_window(_pos(), [], {}).state == "unknown"
    assert assess_credit_window(_pos(), None, {}).state == "unknown"
    only_hold = assess_credit_window(_pos(), [_hold()], {})
    assert only_hold.state == "unknown"
    assert only_hold.best_credit is None
    assert only_hold.best_candidate_desc is None


def test_credit_window_accepts_advise_dict_candidates():
    """The briefing consumes advise()'s camelCase dict form — same result."""
    cands = [
        {"id": "A", "description": "HOLD", "instruction": None,
         "netDollars": 0, "dteExtension": 0},
        {"id": "B", "description": "2× $900P Dec 18 '26",
         "instruction": {"sell_strike": 900.0, "sell_expiration": "2026-12-18"},
         "netDollars": 260.0, "dteExtension": 28},
    ]
    cw = assess_credit_window({"strike": 950.0, "qty": -2}, cands,
                              {"credit_window": {"closing_threshold_per_share": 0.50}})
    # $260 across 2 contracts = $1.30/share → open
    assert cw.state == "open"
    assert cw.best_credit == pytest.approx(1.30)


def test_credit_window_tenor_cap_excludes_multi_year_calendars():
    """A credit harvested by locking the strike for years is duration, not a
    window — candidates beyond max_tenor_days are excluded."""
    cands = [_hold(),
             _cand("B", 950.0, 900.0, dte_ext=791),   # 2.2-year calendar
             _cand("C", 900.0, -100.0, dte_ext=28)]
    cw = assess_credit_window(_pos(), cands, {"max_tenor_days": 365})
    assert cw.state == "debit_only"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
