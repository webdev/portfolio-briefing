"""Matrix housekeeping (task #38, Part 4) — no short put falls through to
DEFAULT_HOLD silently in a state that demands a decision.

User symptom (2026-07-22): "a put being TESTED while underwater or at low
profit matches NO cell → DEFAULT_HOLD. MU $950P (spot $959, +12%) →
silent HOLD; one week later $211 ITM, roll = $4,700 debit."
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from decision_walker import derive_state, walk_matrix
from matrix_loader import load_all


@pytest.fixture(scope="module")
def loaded():
    return load_all()


def _cell(loaded, cell_id):
    for row in loaded.matrix:
        if row.get("id") == cell_id:
            return row
    raise AssertionError(f"cell {cell_id} not found in decision_matrix.yaml")


# ── Part 4a: ITM NEUTRAL cell is down-and-out, not keep-strike ─────────────

def test_itm_neutral_cell_says_down_and_out(loaded):
    """'Roll out for time, keep strike' was stale vs rule #42 — a short-put
    defensive roll is down-and-out; same-strike-out only if you want the
    assignment at this basis."""
    row = _cell(loaded, "PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT")
    assert row["decision"] == "ROLL_OUT_AND_DOWN"
    assert "down-and-out" in row["rationale"]
    assert "keep strike" not in row["rationale"].lower()
    assert "same-strike-out only if you want the assignment" in row["rationale"]


# ── Part 4b: the MU Jul-22 shape hits the new underwater NEAR_ATM cell ─────

def test_near_atm_underwater_no_longer_default_hold(loaded):
    """MU Jul-22: spot 959 / strike 950 (NEAR_ATM) / +12% / 121 DTE — the
    matrix walk alone (guardrail aside) must land on the new cell, never
    DEFAULT_HOLD."""
    position = {
        "symbol": "MU", "positionType": "SHORT_PUT", "optionType": "PUT",
        "strikePrice": 950.0, "entryPrice": 20.0, "currentMid": 17.60,
        "delta": -0.48, "daysToExpiry": 121,
    }
    underlying = {"symbol": "MU", "lastPrice": 959.0, "outlook": "NEUTRAL"}
    context = {"ivRank": 55, "regime": "NORMAL"}
    state = derive_state(position, underlying, context, loaded.parameters)
    assert state.moneyness == "NEAR_ATM"
    decision = walk_matrix(loaded.matrix, state)
    assert decision.matrix_cell == "PUT_NORMAL_NEAR_ATM_UNDERWATER_ROLL"
    assert decision.decision == "ROLL_OUT_AND_DOWN"


# ── Part 4c: sweep — every moneyness × dte × profit combination either
#    matches a non-default cell or is an explicitly documented acceptable
#    hold. ───────────────────────────────────────────────────────────────────

# Documented-acceptable DEFAULT_HOLD combinations (moneyness, dte_band).
# Consulted only when the walk actually lands on DEFAULT_HOLD:
#
#   * DEEP_OTM × MID/LONG_DTE (profit < 60%): >15% OTM with months of
#     runway — no assignment risk, holding for decay is the strategy. The
#     0.60+ profit band already routes to CLOSE_FOR_PROFIT.
#   * MODERATE_OTM × EXPIRY_WEEK: 8-15% OTM with ≤7 days left — expiring
#     worthless is the plan; a forced action would only pay the spread.
#   * anything × LEAP_DTE (>180d): LEAP short puts carry months of
#     extrinsic; the LEAP-OTM override and the strike-tested guardrail
#     (which has no upper DTE bound) cover the tested cases, and the
#     credit-window line keeps the roll economics visible.
_ACCEPTABLE_DEFAULT_HOLD = lambda moneyness, dte_band: (  # noqa: E731
    dte_band == "LEAP_DTE"
    or (moneyness == "DEEP_OTM" and dte_band in ("MID_DTE", "LONG_DTE"))
    or (moneyness == "MODERATE_OTM" and dte_band == "EXPIRY_WEEK")
)

# Spot levels vs a $100 strike that land in each moneyness band
_SPOT_FOR = {
    "DEEP_OTM": 120.0,       # 20% above strike
    "MODERATE_OTM": 110.0,   # 10% above
    "NEAR_ATM": 104.0,       # 4% above
    "ITM": 97.0,             # spot below strike (not deep)
}
_DTE_FOR = {
    "EXPIRY_WEEK": 5,
    "SHORT_DTE": 14,
    "MID_DTE": 60,
    "LONG_DTE": 150,
    "LEAP_DTE": 300,
}
# Task-specified profit sweep. Negative profits clamp to 0.0 in
# derive_state (max(0, ...)) — i.e. "underwater" walks the matrix as 0%
# captured, which is exactly the band the new cells cover.
_PROFITS = [-0.5, -0.1, 0.1, 0.35, 0.6]


def test_put_sweep_no_silent_default_hold(loaded):
    """Enumerate moneyness × dte_band × profit (outlook NEUTRAL, regime
    NORMAL) and assert every combination matches a non-default cell OR is in
    the documented-acceptable set above."""
    failures = []
    for moneyness, spot in _SPOT_FOR.items():
        for dte_band, dte in _DTE_FOR.items():
            for profit in _PROFITS:
                position = {
                    "symbol": "SWEEP", "positionType": "SHORT_PUT",
                    "optionType": "PUT", "strikePrice": 100.0,
                    "entryPrice": 1.0, "currentMid": round(1.0 - profit, 4),
                    "delta": None, "daysToExpiry": dte,
                }
                underlying = {"symbol": "SWEEP", "lastPrice": spot,
                              "outlook": "NEUTRAL"}
                context = {"ivRank": 45, "regime": "NORMAL"}
                state = derive_state(position, underlying, context,
                                     loaded.parameters)
                # Sanity: the constructed shape landed in the intended bands
                assert state.moneyness == moneyness, (moneyness, spot)
                assert state.dte_band == dte_band, (dte_band, dte)
                decision = walk_matrix(loaded.matrix, state)
                if decision.matrix_cell in ("DEFAULT_HOLD", "NO_MATCH"):
                    if not _ACCEPTABLE_DEFAULT_HOLD(moneyness, dte_band):
                        failures.append(
                            (moneyness, dte_band, profit, decision.matrix_cell))
    assert not failures, (
        "silent DEFAULT_HOLD / NO_MATCH on short-put states that demand a "
        f"decision: {failures}")


def test_sweep_acceptable_set_is_actually_exercised(loaded):
    """Guard against the whitelist rotting: at least one swept combination
    must land on DEFAULT_HOLD via the documented-acceptable set (if none do,
    the whitelist is stale and should shrink)."""
    hits = 0
    for moneyness, spot in _SPOT_FOR.items():
        for dte_band, dte in _DTE_FOR.items():
            position = {
                "symbol": "SWEEP", "positionType": "SHORT_PUT",
                "optionType": "PUT", "strikePrice": 100.0,
                "entryPrice": 1.0, "currentMid": 0.9, "delta": None,
                "daysToExpiry": dte,
            }
            underlying = {"symbol": "SWEEP", "lastPrice": spot,
                          "outlook": "NEUTRAL"}
            state = derive_state(position, underlying,
                                 {"ivRank": 45, "regime": "NORMAL"},
                                 loaded.parameters)
            decision = walk_matrix(loaded.matrix, state)
            if (decision.matrix_cell == "DEFAULT_HOLD"
                    and _ACCEPTABLE_DEFAULT_HOLD(moneyness, dte_band)):
                hits += 1
    assert hits > 0


# ── TSM 2026-07-30: the underwater NEAR_ATM cell requires a GENUINE test ──
# User symptom, verbatim: "EXECUTE ROLL TSM_PUT_380_20260828 — diagonal
# down-and-out, −$838 debit … spot is 6% ABOVE the strike (moneyness
# 1.0603), delta -0.10 … $0 intrinsic + $1,132 extrinsic (100%) … This is
# an OTM put where theta works entirely for the holder — correct action is
# HOLD, not a debit roll."

def _put_walk(loaded, spot, delta, strike=380.0, entry=10.15, mid=11.175,
              dte=29):
    position = {
        "symbol": "TSM", "positionType": "SHORT_PUT", "optionType": "PUT",
        "strikePrice": strike, "entryPrice": entry, "currentMid": mid,
        "delta": delta, "daysToExpiry": dte,
    }
    underlying = {"symbol": "TSM", "lastPrice": spot, "outlook": "NEUTRAL"}
    state = derive_state(position, underlying,
                         {"ivRank": 55, "regime": "NORMAL"},
                         loaded.parameters)
    return walk_matrix(loaded.matrix, state)


def test_underwater_cell_requires_test(loaded):
    """delta_min gate on PUT_NORMAL_NEAR_ATM_UNDERWATER_ROLL:
    6% above strike + measured δ 0.10 → NO match (explicit HOLD cell);
    0.9% above (MU Jul-22 shape) → matches; 5% above + δ 0.45 → matches
    via the delta path."""
    # TSM shape — 6% above, measured low delta → HOLD, never the roll cell
    d = _put_walk(loaded, spot=402.92, delta=-0.10)
    assert d.matrix_cell == "PUT_NORMAL_NEAR_ATM_UNDERWATER_HOLD"
    assert d.decision == "HOLD"

    # MU Jul-22 shape — 0.9% above with measured δ 0.48 → the roll cell
    d2 = _put_walk(loaded, spot=959.0, delta=-0.48, strike=950.0,
                   entry=20.0, mid=17.60, dte=121)
    assert d2.matrix_cell == "PUT_NORMAL_NEAR_ATM_UNDERWATER_ROLL"
    assert d2.decision == "ROLL_OUT_AND_DOWN"

    # 5% above but measured δ 0.45 (high-IV name) → delta path matches
    d3 = _put_walk(loaded, spot=399.0, delta=-0.45)
    assert d3.matrix_cell == "PUT_NORMAL_NEAR_ATM_UNDERWATER_ROLL"


def test_underwater_cell_price_fallback_when_no_delta(loaded):
    """No chain delta → price fallback (≤3% above strike), mirroring the
    strike-tested guardrail. Never gate on the fabricated delta estimate."""
    # 2% above strike, no delta → tested via price band → roll cell
    d = _put_walk(loaded, spot=387.0, delta=None)
    assert d.matrix_cell == "PUT_NORMAL_NEAR_ATM_UNDERWATER_ROLL"
    # 6% above strike, no delta → not tested → explicit HOLD
    d2 = _put_walk(loaded, spot=402.92, delta=None)
    assert d2.matrix_cell == "PUT_NORMAL_NEAR_ATM_UNDERWATER_HOLD"
    assert d2.decision == "HOLD"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
