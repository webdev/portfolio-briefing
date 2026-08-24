"""Rule #19 regression — the 2026-08-24 debit-annualization bug.

Observed on the real 2026-08-24 briefing (SOXX and AMAT roll cards):

    "net-cash -291.1% ann. on position over the +7d extension"
    "net-cash -200.7% ann. on position over the +7d extension"

Annualizing a ONE-TIME debit over a 7-day extension window produces
absurd numbers that look like data. A debit is a cost, not a yield
stream: the formatter renders the plain measured form —
"net cost: $2,790 = 6.6% of new collateral" — while credits keep the
existing annualized net-cash form.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from yield_formulas import compute_roll_yield, format_yield_line  # noqa: E402


def test_debit_roll_renders_net_cost_of_new_collateral():
    """The SOXX shape: $2,790 debit, $42,000 new collateral, +7d
    extension → 'net cost: $2,790 = 6.6% of new collateral', never
    '-291.1% ann.'."""
    y = compute_roll_yield(
        new_premium=2.50, new_strike=420.0, new_dte=25, contracts=1,
        spot=499.69, net_credit_dollars=-2790.0, position_value=49_969.0,
        old_strike=520.0, option_type="PUT", extension_days=7)
    line = format_yield_line(y)
    assert "net cost: $2,790 = 6.6% of new collateral" in line
    assert "ann. on position" not in line
    assert "-291" not in line
    # the forward-looking new-leg yield keeps its labeled window
    assert "over the full 25d new leg" in line


def test_credit_roll_keeps_annualized_net_cash():
    """The NOK $11P credit calendar keeps the legacy labeled form:
    'net-cash +12.5% ann. on position over the +28d extension'."""
    y = compute_roll_yield(
        new_premium=2.17, new_strike=11.0, new_dte=144, contracts=10,
        spot=9.89, net_credit_dollars=95.0, position_value=9_890.0,
        old_strike=11.0, option_type="PUT", extension_days=28)
    line = format_yield_line(y)
    assert "net-cash" in line and "ann. on position" in line
    assert "over the +28d extension" in line
    assert "net cost" not in line


def test_debit_without_collateral_renders_plain_dollar_cost():
    """No new collateral resolvable → the plain dollar cost, never a
    fabricated percentage (rule #19)."""
    y = compute_roll_yield(
        new_premium=2.50, new_strike=0.0, new_dte=25, contracts=1,
        spot=499.69, net_credit_dollars=-2790.0, position_value=49_969.0,
        old_strike=520.0, option_type="PUT", extension_days=7)
    line = format_yield_line(y)
    assert "net cost: $2,790" in line
    assert "% of new collateral" not in line
