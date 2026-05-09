"""Tests for the tax-aware candidate ranker."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from candidate_ranker import rank_candidates


def _nvda_candidates():
    """The actual NVDA roll candidates from briefing_w6 (qty=-7, strike $245, current $215)."""
    return [
        {"id": "A", "description": "HOLD", "netDollars": 0, "dteExtension": 0,
         "instruction": None, "current_strike": 245},
        # B: same strike, ~28 day extension
        {"id": "B", "description": "$245C +28d", "netDollars": 1330, "dteExtension": 28,
         "instruction": {"sell_strike": 245, "sell_expiration": "2027-01-15"},
         "current_strike": 245},
        # C: same strike, 728-day LEAP — current "winning" pick by net $
        {"id": "C", "description": "$245C +728d", "netDollars": 27055, "dteExtension": 728,
         "instruction": {"sell_strike": 245, "sell_expiration": "2028-12-15"},
         "current_strike": 245},
        # D: roll up to $295, same date
        {"id": "D", "description": "$295C same date", "netDollars": -7805, "dteExtension": 0,
         "instruction": {"sell_strike": 295, "sell_expiration": "2026-12-18"},
         "current_strike": 245},
        # E: roll up to $295, +28 days
        {"id": "E", "description": "$295C +28d", "netDollars": -6685, "dteExtension": 28,
         "instruction": {"sell_strike": 295, "sell_expiration": "2027-01-15"},
         "current_strike": 245},
    ]


def test_wheel_mode_picks_highest_credit():
    """Old behavior: pure max-credit wins."""
    cands = _nvda_candidates()
    best, scores = rank_candidates(cands, spot=215.38, is_core=False)
    assert best["id"] == "C", f"wheel mode should pick C (highest credit), got {best['id']}"


def test_core_mode_avoids_calendar_with_tax_exposure():
    """Core position with $25K embedded tax: calendar LEAP should LOSE to diagonal-up."""
    cands = _nvda_candidates()
    best, scores = rank_candidates(
        cands, spot=215.38, is_core=True, embedded_tax_dollars=25000,
    )
    assert best is not None
    assert best["id"] in ("D", "E"), (
        f"core mode with tax should pick D or E (diagonal up), got {best['id']}; "
        f"scores: {[(s.candidate_id, s.composite_score) for s in scores]}"
    )


def test_core_mode_prefers_cap_buffer_over_credit():
    """Core mode: buffer dominates over net credit when tax is on the line."""
    cands = _nvda_candidates()
    best, scores = rank_candidates(
        cands, spot=215.38, is_core=True, embedded_tax_dollars=25000,
    )
    # Both D and E have +37% cap buffer; C has +14%
    e_score = next(s for s in scores if s.candidate_id == "E")
    c_score = next(s for s in scores if s.candidate_id == "C")
    assert e_score.composite_score > c_score.composite_score, (
        "diagonal-up E must outrank calendar LEAP C in core mode"
    )


def test_core_mode_with_low_tax_can_pick_calendar():
    """If embedded tax is small, calendar isn't penalized as heavily."""
    cands = _nvda_candidates()
    best_low, _ = rank_candidates(
        cands, spot=215.38, is_core=True, embedded_tax_dollars=1000,
    )
    # With low tax exposure, calendar LEAP penalty is small but D/E still win on buffer
    # This is an OK outcome — the test just verifies the function runs without error
    # and picks something defensible (any non-A candidate).
    assert best_low is not None
    assert best_low["id"] != "A"


def test_returns_none_when_no_candidate_meets_threshold():
    """If all candidates are bad, return None (= HOLD)."""
    bad_cands = [
        {"id": "A", "description": "HOLD", "netDollars": 0, "instruction": None,
         "current_strike": 245},
        {"id": "B", "description": "weak roll", "netDollars": 50, "dteExtension": 7,
         "instruction": {"sell_strike": 245}, "current_strike": 245},
    ]
    best, _ = rank_candidates(bad_cands, spot=215.38, is_core=False, min_credit_threshold=1000)
    assert best is None


def test_score_explanations_populated():
    cands = _nvda_candidates()
    _, scores = rank_candidates(cands, spot=215.38, is_core=True, embedded_tax_dollars=25000)
    for s in scores:
        assert s.rank_explanation
        assert "core" in s.rank_explanation


def test_msft_diagonal_up_beats_leap_in_core_mode():
    """MSFT scenario: $450 cap, spot $416, candidate C is LEAP +728d, candidate E is $500 +28d."""
    cands = [
        {"id": "A", "netDollars": 0, "dteExtension": 0, "instruction": None,
         "current_strike": 450},
        {"id": "B", "netDollars": 470, "dteExtension": 28,
         "instruction": {"sell_strike": 450}, "current_strike": 450},
        {"id": "C", "netDollars": 10610, "dteExtension": 728,
         "instruction": {"sell_strike": 450}, "current_strike": 450},
        {"id": "D", "netDollars": -2810, "dteExtension": 0,
         "instruction": {"sell_strike": 500}, "current_strike": 450},
        {"id": "E", "netDollars": -2370, "dteExtension": 28,
         "instruction": {"sell_strike": 500}, "current_strike": 450},
    ]
    best, _ = rank_candidates(cands, spot=416.34, is_core=True, embedded_tax_dollars=15000)
    # We expect D or E (the diagonal-up rolls) to win for tax-sensitive bullish hold
    assert best["id"] in ("D", "E"), f"expected D or E, got {best['id']}"


def test_vrt_diagonal_up_with_credit_beats_calendar():
    """VRT: diagonal up D pays a credit AND raises cap — should beat calendar B."""
    cands = [
        {"id": "A", "netDollars": 0, "dteExtension": 0, "instruction": None,
         "current_strike": 360},
        # B: same strike, +308d for $3128 credit
        {"id": "B", "netDollars": 3128, "dteExtension": 308,
         "instruction": {"sell_strike": 360}, "current_strike": 360},
        # D: roll up to $410, +308d for $1478 credit
        {"id": "D", "netDollars": 1478, "dteExtension": 308,
         "instruction": {"sell_strike": 410}, "current_strike": 360},
    ]
    best, _ = rank_candidates(cands, spot=343.83, is_core=True, embedded_tax_dollars=4000)
    assert best["id"] == "D", "VRT diagonal-up D dominates calendar B in core mode"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
