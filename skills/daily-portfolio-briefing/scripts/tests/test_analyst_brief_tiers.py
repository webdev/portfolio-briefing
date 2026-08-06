"""Bug #24 (2026-07-22) — Analyst Brief section 5 CONCENTRATION TRIM must be
tier-aware (CLAUDE.md hard rule #29).

User symptom: "**GOOG**: 14.3% → trim to 9% NLV / **NVDA**: 14.3% → trim to
9% NLV" in the analyst brief while the Risk Alerts panel on the SAME day
correctly said "within Tier A bounds (cap 22% NLV, tracked)" for the same
names. Tier A conviction compounders must not get an auto-trim inside their
22% cap; Tier C keeps the legacy 10% → 9% behavior.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from render.analyst_brief import render_analyst_brief  # noqa: E402


_TIER_CFG = {
    "position_tiers": {
        "tier_a_core": ["GOOG", "NVDA", "MSFT"],
        "tier_b_income": ["MU"],
    },
    # concentration_caps omitted → canonical fallbacks (A 22 / B 12 / C 8).
}


def _snap(config=None):
    return {
        "balance": {"accountValue": 1_000_000.0, "cash": 100_000.0},
        "technicals": {},
        "_config": config if config is not None else _TIER_CFG,
    }


def _eq(ticker, weight, price=100.0, qty=100):
    return {"ticker": ticker, "weight": weight, "price": price, "qty": qty}


def _brief(equities, config=None):
    return "\n".join(render_analyst_brief(
        equities, [], _snap(config), {}, {"regime": "HOLD"}))


def test_analyst_brief_tier_a_skips_trim_within_cap():
    """GOOG at 14.3% (Tier A, cap 22%) → NO trim line, no section-5 entry."""
    md = _brief([_eq("GOOG", 0.143), _eq("NVDA", 0.143)])
    assert "CONCENTRATION TRIM" not in md
    assert "trim to 9% NLV" not in md


def test_analyst_brief_tier_a_trims_only_over_cap():
    """A hypothetical Tier A name at 24% (> 22% cap) trims TO the cap —
    never to the legacy 9%."""
    md = _brief([_eq("GOOG", 0.24, price=200.0, qty=1200)])
    assert "CONCENTRATION TRIM" in md
    assert "**GOOG**: 24.0% → trim to 22% NLV (Tier A cap)" in md
    assert "trim to 9% NLV" not in md


def test_analyst_brief_tier_b_uses_tier_b_cap():
    """Tier B (MU, cap 12%): 11% is silent, 14% trims to the 12% cap."""
    md_ok = _brief([_eq("MU", 0.11)])
    assert "CONCENTRATION TRIM" not in md_ok
    md_over = _brief([_eq("MU", 0.14, price=120.0, qty=1200)])
    assert "**MU**: 14.0% → trim to 12% NLV (Tier B cap)" in md_over


def test_analyst_brief_tier_c_uses_10pct_default():
    """Legacy behavior unchanged for Tier C / unconfigured names: fires
    above 10%, trims to 9%."""
    # TSLA is not in the tier config → Tier C → legacy bands.
    md = _brief([_eq("TSLA", 0.143, price=250.0, qty=572)])
    assert "CONCENTRATION TRIM" in md
    assert "**TSLA**: 14.3% → trim to 9% NLV" in md
    # Under the legacy 10% threshold → silent.
    md2 = _brief([_eq("TSLA", 0.095)])
    assert "CONCENTRATION TRIM" not in md2


def test_analyst_brief_no_tier_config_is_fully_legacy():
    """Without a position_tiers block every name (even GOOG) uses the
    legacy 10% → 9% bands — backward compatible."""
    md = _brief([_eq("GOOG", 0.143)], config={})
    assert "**GOOG**: 14.3% → trim to 9% NLV" in md
