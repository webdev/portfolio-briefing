"""Strike-tested guardrail (task #38, Part 1) — fire AT the strike, not through it.

User symptom (real money, 2026-07-22 → 2026-07-29): "MU $950P on Jul 22
(spot $959, 0.9% above strike, +12% capture, 121 DTE) matched NO matrix
cell → silent DEFAULT_HOLD; one week later the put was $211 ITM and the
same roll cost a $4,700 debit. VRT $290P same story. The at-the-money
moment is when extrinsic peaks and credit rolls are biggest — the system
watched both cross in silence."
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import advise as advise_mod
from guardrails import check_strike_tested


def _params(**overrides):
    p = {
        "enabled": True,
        "delta_trigger": 0.45,
        "price_band_pct": 0.03,
        "min_dte": 21,
        "max_profit_to_fire": 0.30,
        "deep_itm_pct": 0.10,
    }
    p.update(overrides)
    return p


def _mu_position(**overrides):
    """MU Jul-22 shape: $950P, spot $959 (0.9% above strike), +12% capture,
    121 DTE, delta 0.48."""
    pos = {
        "symbol": "MU",
        "positionType": "SHORT_PUT",
        "optionType": "PUT",
        "strikePrice": 950.0,
        "expirationDate": "2026-11-20",
        "quantity": 1,
        "entryPrice": 20.00,
        "currentMid": 17.60,       # +12% captured
        "delta": -0.48,
        "daysToExpiry": 121,
        "underlyingPrice": 959.0,
    }
    pos.update(overrides)
    return pos


# ── Part 1: the guardrail itself ───────────────────────────────────────────

def test_strike_tested_fires_on_mu_shape():
    """The exact Jul-22 miss: spot 959 / strike 950 / δ 0.48 / +12% / 121 DTE
    → GUARDRAIL_STRIKE_TESTED with a down-and-out roll decision."""
    r = check_strike_tested(_mu_position(), _params())
    assert r.fired is True
    assert r.decision.matrix_cell == "GUARDRAIL_STRIKE_TESTED"
    assert r.decision.decision == "ROLL_OUT_AND_DOWN"
    assert "credit-roll window" in r.decision.rationale


def test_strike_tested_respects_profit_gate():
    """+40% captured → the take-profit logic owns the decision, no fire."""
    pos = _mu_position(currentMid=12.00)  # (20-12)/20 = 40%
    r = check_strike_tested(pos, _params())
    assert r.fired is False


def test_strike_tested_skips_deep_itm():
    """Spot 739 vs strike 950 (22% ITM) → the ITM matrix cells own it."""
    pos = _mu_position(underlyingPrice=739.0, delta=-0.85,
                       currentMid=35.0)
    r = check_strike_tested(pos, _params())
    assert r.fired is False


def test_strike_tested_price_band_fallback_no_delta():
    """No chain delta → fire on spot within 3% of strike (either side);
    outside the band → no fire."""
    pos = _mu_position(delta=None)
    r = check_strike_tested(pos, _params())
    assert r.fired is True
    assert "no chain delta" in r.decision.rationale

    # Same shape but spot 5% above strike → outside the band, no fire
    pos_far = _mu_position(delta=None, underlyingPrice=997.5)
    r2 = check_strike_tested(pos_far, _params())
    assert r2.fired is False


def test_strike_tested_measured_low_delta_beats_price_band():
    """A MEASURED delta below the trigger means the strike is NOT being
    tested — the price band is a fallback for missing data only, never an
    override of a real measurement (rule #19)."""
    pos = _mu_position(delta=-0.30)   # measured, below 0.45
    r = check_strike_tested(pos, _params())
    assert r.fired is False


def test_strike_tested_respects_min_dte():
    """DTE below min_dte (21) → Part 3's forced-decision item owns it."""
    pos = _mu_position(daysToExpiry=14)
    r = check_strike_tested(pos, _params())
    assert r.fired is False


def test_strike_tested_disabled_config():
    r = check_strike_tested(_mu_position(), _params(enabled=False))
    assert r.fired is False


def test_strike_tested_underwater_fires():
    """Negative profit (underwater at the test) fires — the less profit at
    test, the more urgent the credit roll."""
    pos = _mu_position(currentMid=26.00)  # -30%
    r = check_strike_tested(pos, _params())
    assert r.fired is True


def test_strike_tested_short_puts_only():
    """Covered calls have their own CALL_* cells — never fire on them."""
    pos = _mu_position(positionType="SHORT_CALL_COVERED", optionType="CALL",
                       underlyingPrice=941.0, delta=0.48)
    r = check_strike_tested(pos, _params())
    assert r.fired is False


# ── Backtest sanity: the Jul-22 shapes through the full advise() path ──────

def test_advise_mu_jul22_shape_now_rolls_not_holds():
    """End-to-end: the MU Jul-22 shape must produce a down-and-out roll via
    the strike-tested guardrail — never a silent HOLD."""
    result = advise_mod.advise(
        position=_mu_position(),
        underlying={"symbol": "MU", "lastPrice": 959.0, "outlook": "NEUTRAL"},
        context={"ivRank": 55, "regime": "NORMAL"},
        chain={"expirations": [], "candidates": []},
    )
    assert result["decision"] == "ROLL_OUT_AND_DOWN"
    assert result["matrixCell"] == "GUARDRAIL_STRIKE_TESTED"


def test_advise_vrt_jul22_shape_hits_underwater_cell():
    """VRT Jul-22 shape (spot 301 / strike 290 / +13% / 58 DTE) with a delta
    below the guardrail trigger still lands on the new NEAR_ATM underwater
    matrix cell — the matrix backstop works even when the guardrail doesn't
    fire."""
    pos = {
        "symbol": "VRT", "positionType": "SHORT_PUT", "optionType": "PUT",
        "strikePrice": 290.0, "expirationDate": "2026-09-25", "quantity": 1,
        "entryPrice": 10.0, "currentMid": 8.70,   # +13%
        "delta": -0.40,                            # below the 0.45 trigger
        "daysToExpiry": 58, "underlyingPrice": 301.0,
    }
    result = advise_mod.advise(
        position=pos,
        underlying={"symbol": "VRT", "lastPrice": 301.0, "outlook": "NEUTRAL"},
        context={"ivRank": 60, "regime": "NORMAL"},
        chain={"expirations": [], "candidates": []},
    )
    assert result["decision"] == "ROLL_OUT_AND_DOWN"
    assert result["matrixCell"] == "PUT_NORMAL_NEAR_ATM_UNDERWATER_ROLL"


def test_advise_jul29_deep_itm_routes_to_itm_cells_no_double_fire():
    """Jul-29 marks (MU spot 739 vs strike 950, 22% ITM) must route to the
    ITM matrix cells, NOT the strike-tested guardrail."""
    pos = _mu_position(underlyingPrice=739.0, delta=-0.85,
                       currentMid=35.0, daysToExpiry=114)
    result = advise_mod.advise(
        position=pos,
        underlying={"symbol": "MU", "lastPrice": 739.0, "outlook": "NEUTRAL"},
        context={"ivRank": 70, "regime": "NORMAL"},
        chain={"expirations": [], "candidates": []},
    )
    assert result["matrixCell"] == "PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT"
    assert result["decision"] == "ROLL_OUT_AND_DOWN"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
