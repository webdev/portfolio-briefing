"""Tests for the trade-validator skill."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from validate import (
    validate_diagonal_up_roll,
    validate_calendar_roll,
    validate_csp,
    validate_collar,
    format_validation_line,
)


def test_diagonal_up_roll_nvda_scenario():
    """The exact NVDA scenario from the briefing."""
    v = validate_diagonal_up_roll(
        spot=215.38, current_strike=245, new_strike=295,
        new_premium=10.62, debit_per_share=9.55, contracts=7,
        new_dte=252, new_delta=0.10, current_delta=0.30,
    )
    # Cost per dollar of protection
    # debit = 9.55 × 700 = $6,685; protection = 50 × 700 = $35,000; ratio ≈ 19%
    assert abs(v.cost_per_dollar_of_protection - 0.191) < 0.01
    # Break-even: $245 + 6,685/700 = $254.55
    assert abs(v.break_even_price - 254.55) < 0.5
    assert v.verdict in ("GOOD", "MARGINAL", "POOR")
    assert "NVDA" not in v.reasoning  # ticker-agnostic
    assert "$" in v.reasoning  # has dollar amounts
    # Crucial: NVDA's actual roll math should compute MARGINAL, not GOOD.
    # The $6,685 debit barely beats the probability-weighted protection ($35K × 20% useful = $7K)
    assert v.verdict == "MARGINAL", (
        f"NVDA roll EV ratio is borderline; expected MARGINAL, got {v.verdict} "
        f"(EV ${v.expected_value_dollars:.0f}, debit $6,685)"
    )


def test_diagonal_up_roll_break_even_math():
    """Break-even = current strike + debit_per_share."""
    v = validate_diagonal_up_roll(
        spot=200, current_strike=210, new_strike=230,
        new_premium=5, debit_per_share=4, contracts=1,
        new_dte=60, new_delta=0.15, current_delta=0.30,
    )
    # Break-even = 210 + (4 × 100) / 100 = $214
    assert abs(v.break_even_price - 214) < 0.1


def test_diagonal_roll_alternatives_ranked():
    """Should always include HOLD as an alternative."""
    v = validate_diagonal_up_roll(
        spot=215, current_strike=245, new_strike=295,
        new_premium=10, debit_per_share=10, contracts=7,
        new_dte=252, new_delta=0.10, current_delta=0.30,
    )
    names = [a["name"] for a in v.alternatives_ranked]
    assert any("HOLD" in n for n in names)


def test_calendar_roll_credit():
    """Calendar roll yields a credit; should be GOOD or MARGINAL."""
    v = validate_calendar_roll(
        spot=400, strike=400, new_premium=8, credit_per_share=5,
        contracts=1, new_dte=45, delta=0.30,
    )
    assert v.expected_value_dollars > 0
    assert v.verdict in ("GOOD", "MARGINAL")


def test_csp_break_even_below_strike():
    """CSP break-even = strike - premium."""
    v = validate_csp(
        spot=275, strike=240, premium=3.28, contracts=1, dte=35, delta=0.20,
    )
    # break-even = 240 - 3.28 = $236.72
    assert abs(v.break_even_price - 236.72) < 0.1


def test_csp_low_assignment_probability_is_good():
    """Far OTM CSP with delta 0.10 should be GOOD."""
    v = validate_csp(
        spot=300, strike=240, premium=2.5, contracts=1, dte=30, delta=0.10,
    )
    assert v.verdict in ("GOOD", "MARGINAL")
    assert v.implied_assignment_probability == 0.10


def test_collar_includes_floor_and_cap():
    v = validate_collar(
        spot=215, call_strike=250, put_strike=195,
        call_premium=8, put_premium=6, contracts=7, dte=180,
        call_delta=0.20, put_delta=-0.15,
    )
    assert v.expected_value_dollars is not None
    assert "Floor" in v.reasoning or "floor" in v.reasoning


def test_format_line_carries_verdict():
    v = validate_diagonal_up_roll(
        spot=215, current_strike=245, new_strike=295,
        new_premium=10, debit_per_share=10, contracts=7,
        new_dte=252, new_delta=0.10, current_delta=0.30,
    )
    line = format_validation_line(v)
    assert "Trade-validator" in line
    # Should include either GOOD/MARGINAL/POOR/BLOCK badge
    assert any(b in line for b in ["GOOD TRADE", "MARGINAL", "POOR EV", "BLOCKED"])


def test_negative_ev_blocks():
    """A trade where debit exceeds protection × probability → BLOCK or POOR."""
    v = validate_diagonal_up_roll(
        spot=200, current_strike=400, new_strike=410,  # tiny cap raise
        new_premium=2, debit_per_share=20, contracts=1,  # huge debit
        new_dte=30, new_delta=0.05, current_delta=0.10,
    )
    assert v.verdict in ("POOR", "BLOCK")


def test_verdict_uses_ev_to_cost_ratio():
    """Trade with EV / cost > 1.5 = GOOD; < 0 = BLOCK."""
    # Construct a clearly-good case: small debit, large protection, high prob useful
    v_good = validate_diagonal_up_roll(
        spot=100, current_strike=105, new_strike=130,
        new_premium=2, debit_per_share=1, contracts=1,
        new_dte=60, new_delta=0.15, current_delta=0.45,
    )
    # Construct a clearly-bad case: huge debit, narrow band
    v_bad = validate_diagonal_up_roll(
        spot=100, current_strike=200, new_strike=205,
        new_premium=0.5, debit_per_share=50, contracts=1,
        new_dte=60, new_delta=0.01, current_delta=0.02,
    )
    assert v_good.verdict in ("GOOD", "MARGINAL")
    assert v_bad.verdict in ("POOR", "BLOCK")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
