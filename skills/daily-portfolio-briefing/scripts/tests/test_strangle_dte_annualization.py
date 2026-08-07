"""Regression tests — covered-strangle / collar DTE labels and annualized
yields must come from the ACTUAL selected contract expiration (rule #19).

Observed bug (George's briefing, 2026-08-06) — the Covered Strangles section
of Strategy Upgrades rendered:

    SMH — 1.0x $710.0C exp 2026-11-20 → add 1x $480P ✅ OK
    - Add 1× $480P exp Fri Nov 20 '26 (28d) @ $20.80 mid (δ-0.20)
    - New premium: $2,080 (53% ann on $48,000)

Today was 2026-08-06: Nov 20 2026 is 106 days out, NOT 28d. The premium and
delta matched the real Nov 20 contract, but the DTE label "(28d)" was a
hardcoded literal in the renderer's format string, and the annualized yield
was computed in the composer with a hardcoded `365 / 30` ("~30 DTE") —
2080/48000 × 365/30 ≈ 53% — while the correct figure from the real 106d DTE
is 2080/48000 × 365/106 ≈ 14.9%. That inflated the trade's apparent yield
~3.5x. The collar renderer shared the same hardcoded "(28d)" (and labeled a
one-period put cost as "annual drag", and rendered a constant
"(0% below current)" floor distance).
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from steps.strategy_upgrades import compute_strategy_upgrades, _dte_from_expiration
from render.strategy_upgrades_panel import render_strategy_upgrades


# ─── fixtures ────────────────────────────────────────────────────────────────

def _strangle_snapshot(dte_days: int, *, put_bid: float = 20.60, put_ask: float = 21.00):
    """Snapshot with one SMH covered call whose expiry is `dte_days` out and a
    put chain at that same expiration — mirrors the observed SMH case
    ($480P @ $20.80 mid, δ-0.20, $48,000 collateral)."""
    exp = (date.today() + timedelta(days=dte_days)).isoformat()
    return {
        "positions": [
            {
                "symbol": "SMH",
                "assetType": "EQUITY",
                "qty": 100,
                "price": 500.00,
                "costBasis": 400.00,
            },
            {
                "symbol": "SMH_SHORT_CALL",
                "assetType": "OPTION",
                "underlying": "SMH",
                "type": "CALL",
                "strike": 710.00,
                "expiration": exp,
                "qty": -1,
                "currentMid": 5.00,
                "premiumReceived": 6.00,
            },
        ],
        "balance": {"accountValue": 2_000_000, "cash": 200_000},
        "chains": {
            f"SMH_{exp}": {
                "underlying": "SMH",
                "expiration": exp,
                "puts": [
                    {
                        "strike": 480.00,
                        "bid": put_bid,
                        "ask": put_ask,
                        "lastPrice": (put_bid + put_ask) / 2,
                        "delta": -0.20,
                        "openInterest": 500,
                    },
                ],
                "calls": [],
            },
        },
        "earnings_calendar": {},
        "quotes": {"SMH": {"last": 500.00}},
    }


def _collar_snapshot(dte_days: int):
    """Snapshot producing a GOOG collar with a put chain `dte_days` out
    (big gain, >5% weight, no existing puts, no covered call)."""
    exp = (date.today() + timedelta(days=dte_days)).isoformat()
    return {
        "positions": [
            {
                "symbol": "GOOG",
                "assetType": "EQUITY",
                "qty": 400,
                "price": 200.00,
                "costBasis": 120.00,
            },
        ],
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "chains": {
            f"GOOG_{exp}": {
                "underlying": "GOOG",
                "expiration": exp,
                "puts": [
                    {
                        "strike": 170.00,
                        "bid": 1.40,
                        "ask": 1.80,
                        "lastPrice": 1.60,
                        "delta": -0.09,
                        "openInterest": 400,
                    },
                ],
                "calls": [],
            },
        },
        "earnings_calendar": {},
        "quotes": {"GOOG": {"last": 200.00}},
    }


def _compute(snapshot):
    return compute_strategy_upgrades(
        snapshot, equity_reviews=[], options_reviews=[],
        params={"max_position_pct": 0.10, "max_sector_pct": 0.35},
    )


def _strangle_of(upgrades, symbol="SMH"):
    xs = [u for u in upgrades if u.get("type") == "covered_strangle"
          and u.get("underlying") == symbol]
    assert len(xs) == 1
    return xs[0]


# ─── composer: DTE and yield from the ACTUAL expiration ─────────────────────

def test_strangle_dte_is_actual_days_to_expiration_not_28():
    """Pin (a): the put leg's DTE equals the actual days to the rendered
    expiration (the observed SMH case: Nov 20 '26 was 106 days out, but the
    briefing said '(28d)')."""
    upgrades = _compute(_strangle_snapshot(106))
    strangle = _strangle_of(upgrades)
    assert strangle["proposed"]["dte"] == 106


def test_strangle_annualized_yield_uses_actual_dte_106d_is_15pct_never_53pct():
    """Pin (b): annualized yield is computed from the SAME actual DTE.
    Observed: 'New premium: $2,080 (53% ann on $48,000)' — the 53% came from
    a hardcoded 365/30; the real 106d contract annualizes to ~14.9%."""
    upgrades = _compute(_strangle_snapshot(106))
    strangle = _strangle_of(upgrades)
    # $20.80 mid × 100 × 1 contract on $48,000 collateral, 106d DTE
    expected = (20.80 * 100 / 48_000.0) * (365 / 106)
    assert strangle["yield_annualized"] == pytest.approx(expected, abs=5e-5)
    assert strangle["yield_annualized"] == pytest.approx(0.1492, abs=1e-3)
    # Never the 365/30-inflated figure
    assert strangle["yield_annualized"] < 0.20


def test_strangle_genuine_28d_contract_still_renders_28d_and_matching_yield():
    """Pin (c): a genuine 28d contract still computes DTE 28 and annualizes
    with 365/28 — the fix must not break the case where ~28d is real."""
    upgrades = _compute(_strangle_snapshot(28))
    strangle = _strangle_of(upgrades)
    assert strangle["proposed"]["dte"] == 28
    expected = (20.80 * 100 / 48_000.0) * (365 / 28)
    assert strangle["yield_annualized"] == pytest.approx(expected, abs=5e-5)

    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "(28d)" in md  # real this time, not hardcoded


def test_strangle_unparseable_expiration_fails_closed_no_fabricated_yield():
    """Rule #19 fail-closed: if the expiration can't be parsed, no DTE and no
    annualized yield are invented — the renderer shows n/a instead."""
    snapshot = _strangle_snapshot(106)
    # Corrupt the call's expiration AND re-key the chain to match, so the
    # strangle is still composed but the DTE is uncomputable.
    exp = snapshot["positions"][1]["expiration"]
    snapshot["positions"][1]["expiration"] = "garbage-date"
    snapshot["chains"]["SMH_garbage-date"] = snapshot["chains"].pop(f"SMH_{exp}")
    snapshot["chains"]["SMH_garbage-date"]["expiration"] = "garbage-date"
    upgrades = _compute(snapshot)
    strangle = _strangle_of(upgrades)
    assert strangle["proposed"]["dte"] is None
    assert strangle["yield_annualized"] is None
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "DTE n/a" in md
    assert "ann yield n/a" in md


# ─── renderer: label and yield trace to the same real date ──────────────────

def test_rendered_strangle_line_shows_real_dte_and_real_annualized_yield():
    """The rendered lines for the 106d case must read '(106d)' and '15% ann'
    — never the observed '(28d)' / '53% ann on $48,000'."""
    upgrades = _compute(_strangle_snapshot(106))
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "(106d)" in md
    assert "(28d)" not in md
    assert "15% ann on $48,000" in md
    assert "53% ann" not in md


def test_renderer_derives_dte_from_expiration_when_composer_omits_it():
    """Legacy strangle dicts without a composer-provided 'dte' derive it from
    the SAME expiration date being rendered — never a hardcoded default."""
    exp = (date.today() + timedelta(days=106)).isoformat()
    upgrades = [{
        "type": "covered_strangle",
        "underlying": "SMH",
        "current_calls": "1x $710.0C exp " + exp,
        "proposed": {
            "action": "SELL_TO_OPEN", "qty": 1, "strike": 480.0,
            "expiration": exp, "delta": -0.20,
            "premium_per_contract": 20.80, "total_premium": 2080.0,
            # note: no "dte" key
        },
        "yield_annualized": 0.1492,
        "collateral_required": 48_000.0,
        "concentration_check": {"current_pct": 2.5, "post_action_pct": 4.9,
                                "blocked": False, "reason": None},
        "combined_income": {"calls": 500.0, "puts": 2080.0, "total": 2580.0},
        "rationale": "Convert SMH covered call to strangle",
    }]
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "(106d)" in md
    assert "(28d)" not in md


# ─── sibling audit: collar shared the hardcoded "(28d)" ─────────────────────

def test_collar_put_leg_renders_real_dte_not_hardcoded_28d():
    """The collar renderer shared the same hardcoded '(28d)' literal
    (observed: 'Buy 2× $435P exp Fri Sep 18 '26 (28d)' on 2026-08-06 —
    Sep 18 '26 was 43 days out). The put leg must show the actual DTE."""
    upgrades = _compute(_collar_snapshot(134))
    collars = [u for u in upgrades if u.get("type") == "collar"]
    assert len(collars) == 1
    assert collars[0]["proposed_put"]["dte"] == 134

    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "(134d)" in md
    assert "(28d)" not in md


def test_collar_annualized_drag_uses_actual_dte():
    """The collar cost line labeled a one-period put cost as 'annual drag'
    (implicitly assuming a 365d contract). It must annualize with the real
    DTE of the selected put."""
    upgrades = _compute(_collar_snapshot(134))
    md = "\n".join(render_strategy_upgrades(upgrades))
    # 4 contracts × $1.60 mid × 100 = $640 on 400 × $170 = $68,000 strike
    # value, annualized over 134d: 640/68000 × 365/134 × 100 ≈ 2.6%
    expected = 640.0 / 68_000.0 * (365 / 134) * 100
    assert f"({expected:.1f}% annualized drag on position)" in md


def test_collar_floor_distance_is_measured_not_zero():
    """The floor line rendered '(0% below current)' on every collar (observed
    2026-08-06: 'Floor: $435 (0% below current)') because the old expression
    subtracted the strike from itself. It must measure strike vs the real
    current price: $170 floor on a $200 stock is 15% below current."""
    upgrades = _compute(_collar_snapshot(134))
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "(15% below current)" in md
    assert "(0% below current)" not in md


# ─── helper unit coverage ───────────────────────────────────────────────────

def test_dte_helper_future_past_and_garbage():
    future = (date.today() + timedelta(days=106)).isoformat()
    past = (date.today() - timedelta(days=5)).isoformat()
    assert _dte_from_expiration(future) == 106
    assert _dte_from_expiration(date.today().isoformat()) is None  # not future
    assert _dte_from_expiration(past) is None
    assert _dte_from_expiration("garbage-date") is None
    assert _dte_from_expiration(None) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
