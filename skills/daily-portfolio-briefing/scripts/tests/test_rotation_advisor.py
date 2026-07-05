"""Tests for the rotation advisor (analysis/rotation_advisor.py).

Pure unit tests — feed synthesized holdings + theme + technicals dicts,
assert the scoring composite + rotation selection behave per the design.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS))

from analysis.rotation_advisor import (  # noqa: E402
    _same_theme_candidates,
    _ticker_theme,
    build_rotation_opportunities,
    find_capital_reallocations,
    find_equity_rotations,
    find_option_rotations,
    score_candidate,
)


# ─── score_candidate ───────────────────────────────────────────────────────


def test_score_candidate_perfect_setup():
    """A fresh entry with all signals aligned should score high."""
    score, reasons = score_candidate(
        rsi=45,              # favorable band → 30
        iv_rank=70,          # rich → 20
        drawdown_pct=15,     # pullback zone → 15
        parkev_tier=5,       # TOP STOCK → 20
        fair_value_upside_pct=30,  # >15% → 10
        at_support=True,     # confluence → 5
    )
    assert score == 100.0
    assert "🅿️ TOP STOCK" in reasons
    assert "at support" in reasons


def test_score_candidate_rsi_overbought_gets_zero_from_rsi():
    """Hard rule #11 asymmetric: RSI >70 → 0 points from RSI."""
    score, _ = score_candidate(
        rsi=75, iv_rank=None, drawdown_pct=None,
        parkev_tier=None, fair_value_upside_pct=None,
    )
    assert score == 0.0


def test_score_candidate_missing_inputs_returns_zero():
    """Fail-open: all None → 0 score, empty reasons."""
    score, reasons = score_candidate(
        rsi=None, iv_rank=None, drawdown_pct=None,
        parkev_tier=None, fair_value_upside_pct=None,
    )
    assert score == 0.0
    assert reasons == []


# ─── _ticker_theme / _same_theme_candidates ───────────────────────────────


@pytest.fixture
def theme_universes():
    return {
        "themes": {
            "semis": {
                "name": "Semis",
                "anchors": ["NVDA", "AMD", "AVGO", "INTC"],
            },
            "cloud": {
                "name": "Cloud",
                "anchors": ["GOOG", "MSFT", "AMZN"],
            },
        }
    }


def test_ticker_theme_finds_membership(theme_universes):
    assert _ticker_theme("NVDA", theme_universes) == "semis"
    assert _ticker_theme("GOOG", theme_universes) == "cloud"
    assert _ticker_theme("XYZ", theme_universes) is None


def test_same_theme_candidates_excludes_held(theme_universes):
    cands = _same_theme_candidates("NVDA", theme_universes, exclude={"NVDA", "AVGO"})
    assert set(cands) == {"AMD", "INTC"}


# ─── find_equity_rotations ─────────────────────────────────────────────────


def test_equity_rotation_surfaces_higher_scoring_alternative(theme_universes):
    """Given NVDA held (mediocre setup) vs AMD available (perfect setup),
    the rotation table should surface AMD as a swap candidate."""
    holdings = [
        {"ticker": "NVDA", "weight_pct": 8.0, "tier": "C"},
    ]
    technicals = {
        "NVDA": {"rsi_14": 62, "iv_rank": 40, "drawdown_pct": 5},   # extended, meh IV
        "AMD":  {"rsi_14": 45, "iv_rank": 75, "drawdown_pct": 18},  # perfect
    }
    recs = {
        "NVDA": {"rating_tier": 3},
        "AMD": {"rating_tier": 4, "recommendation": "BUY"},
    }
    rotations = find_equity_rotations(
        holdings=holdings,
        theme_universes=theme_universes,
        technicals=technicals,
        recommendations=recs,
        finviz_targets={},
        fv_by_ticker={},
    )
    assert len(rotations) >= 1
    r = rotations[0]
    assert r["from"]["ticker"] == "NVDA"
    assert r["to"]["ticker"] == "AMD"
    assert r["to"]["improvement"] > 15


def test_equity_rotation_skips_tier_a_holdings(theme_universes):
    """Hard rule #29: Tier A conviction compounders never surfaced for rotation."""
    holdings = [{"ticker": "NVDA", "weight_pct": 15.0, "tier": "A"}]
    technicals = {"NVDA": {"rsi_14": 62, "iv_rank": 40, "drawdown_pct": 5}}
    rotations = find_equity_rotations(
        holdings=holdings,
        theme_universes=theme_universes,
        technicals=technicals,
        recommendations={},
        finviz_targets={},
        fv_by_ticker={},
    )
    assert rotations == []


def test_equity_rotation_skips_small_positions(theme_universes):
    """Positions below 5% NLV aren't worth rotating."""
    holdings = [{"ticker": "NVDA", "weight_pct": 2.0, "tier": "C"}]
    technicals = {
        "NVDA": {"rsi_14": 65},
        "AMD":  {"rsi_14": 45, "iv_rank": 75, "drawdown_pct": 18},
    }
    rotations = find_equity_rotations(
        holdings=holdings,
        theme_universes=theme_universes,
        technicals=technicals,
        recommendations={"AMD": {"rating_tier": 4}},
        finviz_targets={},
        fv_by_ticker={},
    )
    assert rotations == []


def test_equity_rotation_skips_when_no_improvement(theme_universes):
    """If the alternative doesn't score meaningfully higher, no swap surfaced."""
    holdings = [{"ticker": "NVDA", "weight_pct": 8.0, "tier": "C"}]
    # NVDA is already in a good spot; AMD is worse
    technicals = {
        "NVDA": {"rsi_14": 45, "iv_rank": 70, "drawdown_pct": 15},   # score 65+
        "AMD":  {"rsi_14": 72, "iv_rank": 30, "drawdown_pct": 3},    # score low
    }
    rotations = find_equity_rotations(
        holdings=holdings,
        theme_universes=theme_universes,
        technicals=technicals,
        recommendations={},
        finviz_targets={},
        fv_by_ticker={},
    )
    assert rotations == []


def test_equity_rotation_excludes_already_held_alternatives(theme_universes):
    """Position-aware: if user already holds AMD, don't surface AMD as a swap target
    for another same-theme position."""
    holdings = [
        {"ticker": "NVDA", "weight_pct": 8.0, "tier": "C"},
        {"ticker": "AMD", "weight_pct": 6.0, "tier": "C"},
    ]
    technicals = {
        "NVDA": {"rsi_14": 62, "iv_rank": 40, "drawdown_pct": 5},
        "AMD":  {"rsi_14": 45, "iv_rank": 75, "drawdown_pct": 18},
        "INTC": {"rsi_14": 68},  # weak
    }
    rotations = find_equity_rotations(
        holdings=holdings,
        theme_universes=theme_universes,
        technicals=technicals,
        recommendations={},
        finviz_targets={},
        fv_by_ticker={},
    )
    # NVDA's rotation candidates must NOT include AMD (already held)
    for r in rotations:
        if r["from"]["ticker"] == "NVDA":
            assert r["to"]["ticker"] != "AMD"


# ─── find_option_rotations ────────────────────────────────────────────────


def test_option_rotation_surfaces_high_capture_with_favorable_technicals():
    """A short put at 55% capture on a name with RSI 45 + IV 70 → surface."""
    options = [
        {"symbol": "NVDA_PUT_180_20260918", "underlying": "NVDA",
         "strike": 180, "expiration": "2026-09-18", "captured_pct": 55},
    ]
    technicals = {"NVDA": {"rsi_14": 45, "iv_rank": 70}}
    rotations = find_option_rotations(options=options, technicals=technicals)
    assert len(rotations) == 1
    assert rotations[0]["from"]["symbol"] == "NVDA_PUT_180_20260918"


def test_option_rotation_skips_low_capture():
    options = [
        {"symbol": "NVDA_PUT_180", "underlying": "NVDA", "captured_pct": 10},
    ]
    technicals = {"NVDA": {"rsi_14": 45, "iv_rank": 70}}
    assert find_option_rotations(options=options, technicals=technicals) == []


def test_option_rotation_skips_when_rsi_unfavorable():
    """Even at high capture, if the name is overbought → no rotation, only close."""
    options = [
        {"symbol": "NVDA_PUT_180", "underlying": "NVDA", "captured_pct": 60},
    ]
    technicals = {"NVDA": {"rsi_14": 75, "iv_rank": 70}}  # overbought
    assert find_option_rotations(options=options, technicals=technicals) == []


# ─── find_capital_reallocations ────────────────────────────────────────────


def test_capital_plan_synthesizes_moves_from_rotations():
    eq = [
        {"from": {"ticker": "NVDA", "weight_pct": 8, "tier": "C"},
         "to": {"ticker": "AMD", "improvement": 30}},
        {"from": {"ticker": "SOFI", "weight_pct": 6, "tier": "C"},
         "to": {"ticker": "PLTR", "improvement": 25}},
    ]
    opts = [
        {"from": {"symbol": "NVDA_PUT_180", "captured_pct": 55},
         "to": {"hint": "..."}},
    ]
    plan = find_capital_reallocations(
        equity_rotations=eq,
        option_rotations=opts,
        stress_coverage=0.07,
    )
    assert len(plan) == 1
    assert plan[0]["summary"]["equity_swaps"] == 2
    assert plan[0]["summary"]["option_swaps"] == 1
    assert plan[0]["summary"]["freed_pct_nlv"] == 14.0


def test_capital_plan_empty_when_no_rotations():
    assert find_capital_reallocations(
        equity_rotations=[], option_rotations=[], stress_coverage=1.0,
    ) == []


# ─── build_rotation_opportunities (top-level) ─────────────────────────────


def test_build_rotation_opportunities_end_to_end(theme_universes):
    """End-to-end: both equity + option rotations fire, capital plan
    synthesizes from both.

    NVDA held with favorable RSI 45 (option rotation eligible: high capture
    + tech favorable → reset the premium clock). AMD scores materially
    higher on the equity composite (tier 4 Parkev + rich IV + deeper
    pullback + at support), so it surfaces as a same-theme swap even
    though NVDA's technicals are already OK.
    """
    holdings = [{"ticker": "NVDA", "weight_pct": 8.0, "tier": "C"}]
    technicals = {
        "NVDA": {"rsi_14": 45, "iv_rank": 55, "drawdown_pct": 5},
        "AMD":  {"rsi_14": 45, "iv_rank": 75, "drawdown_pct": 18},
    }
    options = [
        {"symbol": "NVDA_PUT_180", "underlying": "NVDA", "captured_pct": 55,
         "strike": 180, "expiration": "2026-09-18"},
    ]
    result = build_rotation_opportunities(
        holdings=holdings,
        options=options,
        theme_universes=theme_universes,
        technicals=technicals,
        recommendations={"AMD": {"rating_tier": 4, "recommendation": "BUY"}},
        finviz_targets={},
        fv_by_ticker={},
        stress_coverage=0.07,
    )
    assert "equity" in result
    assert "options" in result
    assert "capital_plan" in result
    assert result["stats"]["equity_count"] >= 1
    assert result["stats"]["options_count"] >= 1
