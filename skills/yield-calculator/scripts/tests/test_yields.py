"""Tests for yield_formulas.py — the canonical yield calculations."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from yield_formulas import (
    compute_csp_yield,
    compute_cc_yield,
    compute_roll_yield,
    compute_collar_yield,
    compute_hedge_yield,
    compute_close_yield,
    format_yield_line,
)


def test_csp_yield_basic():
    # Sell 1 GOOG $400 put for $5 with 30 DTE
    # collateral = $40,000, premium = $500
    # static yield = 1.25%, annualized = 1.25 × 365/30 = 15.2%
    r = compute_csp_yield(premium=5.0, strike=400, contracts=1, dte=30)
    assert r["kind"] == "csp"
    assert abs(r["all_yields"]["static_yield_pct"] - 1.25) < 0.01
    assert abs(r["all_yields"]["static_yield_ann_pct"] - 15.21) < 0.1
    assert r["collateral_dollars"] == 40000
    assert r["effective_basis"] == 395  # strike - premium


def test_cc_yield_basic():
    # Cover 200 shares @ $416 with $450C @ $28.55 over 224d
    # position value = $83,200; premium = $5,710
    # static = 6.86%, annualized = 11.18%
    r = compute_cc_yield(premium=28.55, strike=450, spot=416, contracts=2, dte=224)
    assert r["kind"] == "cc"
    assert abs(r["all_yields"]["static_yield_pct"] - 6.86) < 0.05
    assert abs(r["all_yields"]["static_yield_ann_pct"] - 11.18) < 0.1
    # If called: premium 28.55 + (450-416) = 62.55 / 416 = 15.04% point-in-time
    # Annualized: 15.04 × 365/224 = 24.5%
    assert abs(r["all_yields"]["if_called_yield_ann_pct"] - 24.5) < 0.5


def test_calendar_roll_yield():
    # MSFT: roll $450C → $450C extending 728 days
    # New premium $83.80 × 200 = $16,760, collateral $90,000
    # New leg yield = 18.6%, annualized = 18.6 × 365/952 = 7.1%
    r = compute_roll_yield(
        new_premium=83.80, new_strike=450, new_dte=952, contracts=2,
        spot=416, net_credit_dollars=10610, position_value=83200,
        old_strike=450,
    )
    assert r["kind"] == "calendar_roll"
    assert abs(r["all_yields"]["new_leg_yield_ann_pct"] - 7.14) < 0.1
    # Net cash yield = $10,610 / $83,200 × 365/952 = 4.89%
    assert abs(r["all_yields"]["net_cash_yield_ann_pct"] - 4.89) < 0.1
    # Cap buffer: (450-416)/416 = 8.17%
    assert abs(r["all_yields"]["cap_buffer_pct"] - 8.17) < 0.05


def test_diagonal_roll_with_debit():
    # MSFT: roll $450C → $500C (diagonal up), 252d new DTE, debit $2,370
    r = compute_roll_yield(
        new_premium=16.82, new_strike=500, new_dte=252, contracts=2,
        spot=416, net_credit_dollars=-2370, position_value=83200,
        old_strike=450,
    )
    assert r["kind"] == "diagonal_roll"
    # New leg: 16.82 × 200 / 100000 × 365/252 = 4.87%
    assert abs(r["all_yields"]["new_leg_yield_ann_pct"] - 4.87) < 0.1
    # Cap buffer change: (500-450)/416 = 12.0%
    assert abs(r["all_yields"]["cap_buffer_change_pct"] - 12.02) < 0.05
    # Debit-cost-per-protection: $2,370 / (50 × 100 × 2) = $0.237 per dollar of new room
    assert r["cost_per_dollar_of_protection"] is not None
    assert abs(r["cost_per_dollar_of_protection"] - 0.237) < 0.005


def test_collar_yield():
    # NVDA collar: 700 shares @ $215; sell $245C @ $20.05; buy $200P @ $10
    # position value = $150,500; net premium = (20.05 - 10) × 700 = $7,035
    r = compute_collar_yield(
        call_premium=20.05, put_premium=10.0, call_strike=245, put_strike=200,
        spot=215, contracts=7, dte=224,
    )
    assert r["kind"] == "collar"
    assert abs(r["all_yields"]["combined_static_yield_pct"] - 4.67) < 0.05
    # cap_ceiling = (245-215)/215 = 13.95%
    assert abs(r["all_yields"]["cap_ceiling_pct"] - 13.95) < 0.05
    # floor = (215-200)/215 = 6.98%
    assert abs(r["all_yields"]["floor_pct"] - 6.98) < 0.05


def test_hedge_yield():
    # 19× SPY $570P for $11,400; NLV $1.094M; spot $600
    r = compute_hedge_yield(
        put_cost_dollars=11400, contracts=19, strike=570, spot=600,
        dte=35, nlv=1094000, delta=-0.20,
    )
    assert r["kind"] == "hedge"
    # protected = 0.20 × 100 × 19 × 570 = $216,600
    assert abs(r["protected_notional"] - 216600) < 1
    # protection ratio = 216,600 / 11,400 = 19×
    assert abs(r["all_yields"]["protection_ratio"] - 19.0) < 0.5
    # cost_pct_nlv = 11,400 / 1,094,000 = 1.04%
    assert abs(r["all_yields"]["cost_pct_nlv"] - 1.04) < 0.05


def test_close_yield():
    # Closed MU short put for $2,061 profit (entry $58, mid $37.40, strike $605, qty 1, held 30d)
    r = compute_close_yield(
        entry_price=58.00, current_mid=37.40, strike=605, contracts=1,
        days_held=30, days_to_expiry=49, is_short=True,
    )
    assert r["kind"] == "close"
    # profit_per_share = 58 - 37.40 = $20.60; total = $2,060
    assert abs(r["profit_dollars"] - 2060) < 1
    # realized_pct_of_max = 20.60 / 58 = 35.5%
    assert abs(r["all_yields"]["realized_pct_of_max"] - 35.52) < 0.1
    # collateral = $60,500; pct = 2,060/60,500 = 3.41%; annualized = 3.41 × 365/30 = 41.4%
    assert abs(r["all_yields"]["annualized_capture_pct"] - 41.42) < 0.5


def test_format_yield_line_csp():
    r = compute_csp_yield(premium=5.0, strike=400, contracts=1, dte=30)
    line = format_yield_line(r)
    assert "%" in line
    assert "$40,000" in line
    assert "ann" in line


def test_format_yield_line_collar():
    r = compute_collar_yield(
        call_premium=20.05, put_premium=10.0, call_strike=245, put_strike=200,
        spot=215, contracts=7, dte=224,
    )
    line = format_yield_line(r)
    assert "Floor" in line
    assert "cap" in line.lower()


def test_zero_protection_division_safe():
    """Edge case: same strike (no protection added) shouldn't divide by zero."""
    r = compute_roll_yield(
        new_premium=10, new_strike=400, new_dte=30, contracts=1,
        spot=395, net_credit_dollars=500, position_value=39500,
        old_strike=400,
    )
    # cost_per_dollar_of_protection should be None when no strike change
    assert r["cost_per_dollar_of_protection"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
