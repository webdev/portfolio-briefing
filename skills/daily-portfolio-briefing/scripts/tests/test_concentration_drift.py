"""Tests for concentration_drift — tier-aware caps (CLAUDE.md hard rule #29).

The legacy path (no `position_tiers` config) keeps the original 6%/8%/10%
bands. The tier-aware path uses per-tier caps from `concentration_caps`,
so a Tier A name at 18% NLV is "within bounds" (cap 22%) rather than a
breach.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.concentration_drift import (  # noqa: E402
    detect_concentration_drift,
    ConcentrationAlert,
)


_TIER_CFG = {
    "position_tiers": {
        "tier_a_core": ["NVDA", "GOOG", "MSFT"],
        "tier_b_income": ["MU"],
    },
    "concentration_caps": {
        "tier_a_max_pct": 22.0,
        "tier_b_max_pct": 12.0,
        "tier_c_max_pct": 8.0,
        "default_max_pct": 10.0,
    },
}


def _pos(symbol, value):
    return {"position_type": "long_stock", "symbol": symbol, "market_value": value}


# ─── Legacy path (no config) — original behavior ──────────────────────────


def test_legacy_breach_at_12pct():
    alerts = detect_concentration_drift([_pos("XYZ", 120_000)], nlv=1_000_000)
    assert len(alerts) == 1
    assert alerts[0].severity == "breach"
    assert "BREACH of 10%" in alerts[0].message


def test_legacy_warning_at_9pct():
    alerts = detect_concentration_drift([_pos("XYZ", 90_000)], nlv=1_000_000)
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"


def test_legacy_drift_at_7pct():
    alerts = detect_concentration_drift([_pos("XYZ", 70_000)], nlv=1_000_000)
    assert len(alerts) == 1
    assert alerts[0].severity == "drift"


def test_legacy_silent_below_6pct():
    alerts = detect_concentration_drift([_pos("XYZ", 30_000)], nlv=1_000_000)
    assert alerts == []


# ─── Tier-aware path ──────────────────────────────────────────────────────


def test_tier_a_nvda_at_14pct_is_within_bounds_not_breach():
    """NVDA at 14% NLV is below the Tier A cap (22%) AND below the 80%-of-cap
    warning band (= 17.6%), but above the 50%-of-cap informational band
    (= 11%) → render `within_bounds` so the user sees it tracked."""
    alerts = detect_concentration_drift(
        [_pos("NVDA", 140_000)], nlv=1_000_000, config=_TIER_CFG,
    )
    assert len(alerts) == 1
    a = alerts[0]
    assert a.severity == "within_bounds"
    assert a.tier == "A"
    assert a.tier_cap_pct == 22.0
    assert "Tier A cap 22%" in a.message


def test_tier_a_nvda_at_19pct_is_warning():
    """NVDA at 19% NLV is above 80% of cap (17.6%) but below the 22% cap →
    warning (approaching the Tier A cap)."""
    alerts = detect_concentration_drift(
        [_pos("NVDA", 190_000)], nlv=1_000_000, config=_TIER_CFG,
    )
    assert len(alerts) == 1
    a = alerts[0]
    assert a.severity == "warning"
    assert a.tier == "A"


def test_tier_a_nvda_at_23pct_breaches_22_cap():
    """NVDA at 23% NLV exceeds even the raised Tier A cap → breach."""
    alerts = detect_concentration_drift(
        [_pos("NVDA", 230_000)], nlv=1_000_000, config=_TIER_CFG,
    )
    assert len(alerts) == 1
    a = alerts[0]
    assert a.severity == "breach"
    assert "Tier A cap (22%)" in a.message


def test_tier_b_mu_at_11pct_below_12_cap_warning():
    """MU at 11% NLV is approaching the Tier B cap (12%) — warning band
    is > 80% of cap (= 9.6%) → fires WARNING."""
    alerts = detect_concentration_drift(
        [_pos("MU", 110_000)], nlv=1_000_000, config=_TIER_CFG,
    )
    assert len(alerts) == 1
    a = alerts[0]
    assert a.severity == "warning"
    assert a.tier == "B"


def test_tier_c_unknown_at_9pct_above_8_cap_breaches():
    """A Tier C ticker (default) at 9% NLV exceeds the 8% Tier C cap → breach."""
    alerts = detect_concentration_drift(
        [_pos("XYZ", 90_000)], nlv=1_000_000, config=_TIER_CFG,
    )
    assert len(alerts) == 1
    assert alerts[0].severity == "breach"
    assert alerts[0].tier == "C"


def test_tier_a_at_5pct_silent():
    """NVDA at 5% NLV is below 50% of the Tier A cap → no alert."""
    alerts = detect_concentration_drift(
        [_pos("NVDA", 50_000)], nlv=1_000_000, config=_TIER_CFG,
    )
    assert alerts == []


def test_sorted_by_pct_desc():
    """Multiple positions sort by current_pct descending (existing contract)."""
    alerts = detect_concentration_drift(
        [
            _pos("NVDA", 140_000),  # 14% Tier A — within_bounds
            _pos("XYZ", 90_000),    # 9% Tier C — breach
            _pos("MU", 110_000),    # 11% Tier B — warning
        ],
        nlv=1_000_000,
        config=_TIER_CFG,
    )
    pcts = [a.current_pct for a in alerts]
    assert pcts == sorted(pcts, reverse=True)
