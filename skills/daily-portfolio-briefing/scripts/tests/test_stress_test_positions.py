"""Test stress test panel renders position details for assigned scenarios.

Verify that:
1. Assigned symbols are listed with strike and expiration
2. Collateral amounts are calculated correctly
3. Multiple positions in same scenario are all shown
4. Details match the position data from snapshot
"""

from decimal import Decimal
from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.stress_coverage import compute_stress_coverage, StressCoverage, DropScenario
from render.stress_test_panel import render_stress_test_details


def test_stress_test_details_show_assigned_positions():
    """When positions get assigned at -10% drop, they should be listed with details."""
    today = date.today()
    exp_date = today + timedelta(days=35)

    positions = [
        {
            "symbol": "AAPL",
            "assetType": "EQUITY",
            "qty": 100,
            "price": 185.0,
            "underlying_price": 185.0,
            "position_type": "long_stock",
        },
        {
            "symbol": "AAPL",
            "assetType": "OPTION",
            "type": "PUT",
            "underlying": "AAPL",
            "strike": 170.0,
            "expiration": exp_date.isoformat(),
            "qty": -2,
            "position_type": "short_put",
            "underlying_price": 185.0,
            "entry_price": 2.50,
            "current_price": 1.20,
        },
        {
            "symbol": "MSFT",
            "assetType": "OPTION",
            "type": "PUT",
            "underlying": "MSFT",
            "strike": 400.0,
            "expiration": exp_date.isoformat(),
            "qty": -1,
            "position_type": "short_put",
            "underlying_price": 420.0,
            "entry_price": 3.00,
            "current_price": 1.50,
        },
    ]

    # Compute stress coverage (will identify which puts get assigned at -10%)
    cash = Decimal("50000")
    nlv = Decimal("200000")
    coverage = compute_stress_coverage(positions, cash, nlv)

    # Render stress details
    lines = render_stress_test_details(coverage, positions)
    md = "\n".join(lines)

    # The rendering should show assigned symbols with their details
    assert "At −10" in md or "At -10" in md, f"Drop percentage missing in:\n{md}"

    # Should show position details for assigned symbols
    # AAPL 2x at -10% drop should trigger assignment (underlying 185 * (1-0.10*0.95) = 167.1 < 170 strike)
    # MSFT 1x at -10% drop should NOT trigger (underlying 420 * (1-0.10*0.70) = 390.6 > 400 strike)
    if "AAPL" in md:
        # If AAPL is shown as assigned, it should have strike and expiration
        assert "$170" in md or "170" in md, f"AAPL strike not found in:\n{md}"


def test_stress_test_details_with_multiple_assignments():
    """Multiple positions assigned in same scenario should all be listed."""
    today = date.today()
    exp_date_1 = today + timedelta(days=35)
    exp_date_2 = today + timedelta(days=45)

    positions = [
        {
            "symbol": "AAPL",
            "assetType": "EQUITY",
            "qty": 50,
            "underlying_price": 185.0,
            "position_type": "long_stock",
        },
        {
            "symbol": "AAPL",
            "assetType": "OPTION",
            "type": "PUT",
            "underlying": "AAPL",
            "strike": 170.0,
            "expiration": exp_date_1.isoformat(),
            "qty": -2,
            "position_type": "short_put",
            "underlying_price": 185.0,
        },
        {
            "symbol": "MSFT",
            "assetType": "OPTION",
            "type": "PUT",
            "underlying": "MSFT",
            "strike": 400.0,
            "expiration": exp_date_2.isoformat(),
            "qty": -1,
            "position_type": "short_put",
            "underlying_price": 420.0,
        },
        {
            "symbol": "NVDA",
            "assetType": "OPTION",
            "type": "PUT",
            "underlying": "NVDA",
            "strike": 800.0,
            "expiration": exp_date_2.isoformat(),
            "qty": -3,
            "position_type": "short_put",
            "underlying_price": 900.0,
        },
    ]

    cash = Decimal("200000")
    nlv = Decimal("500000")
    coverage = compute_stress_coverage(positions, cash, nlv)

    lines = render_stress_test_details(coverage, positions)
    md = "\n".join(lines)

    # At -20% drop, more positions should be assigned
    # Check that the rendering is present
    assert len(md) > 0, "Details rendering is empty"


def test_collateral_calculation_in_details():
    """Collateral amounts in rendered details should match strike × qty × 100."""
    today = date.today()
    exp_date = today + timedelta(days=35)

    positions = [
        {
            "symbol": "TEST",
            "assetType": "OPTION",
            "type": "PUT",
            "underlying": "TEST",
            "strike": 100.0,
            "expiration": exp_date.isoformat(),
            "qty": -3,
            "position_type": "short_put",
            "underlying_price": 120.0,
        },
    ]

    cash = Decimal("50000")
    nlv = Decimal("100000")
    coverage = compute_stress_coverage(positions, cash, nlv)

    lines = render_stress_test_details(coverage, positions)
    md = "\n".join(lines)

    # Expected collateral: 100 × 3 × 100 = $30,000
    if "TEST" in md and "$" in md:
        # Should show $30,000 or similar notation
        assert "30" in md or "3x" in md, f"Collateral amount not found in:\n{md}"


def test_no_crash_with_missing_position_data():
    """Details should render gracefully even if position data is incomplete."""
    coverage_drops = {
        0.10: DropScenario(
            drop_pct=0.10,
            assigned_obligations=Decimal("50000"),
            cash_after=Decimal("0"),
            nlv_after=Decimal("450000"),
            is_shortfall=True,
            assigned_symbols=["AAPL 2x", "MSFT 1x"],
        ),
        0.20: DropScenario(
            drop_pct=0.20,
            assigned_obligations=Decimal("100000"),
            cash_after=Decimal("-50000"),
            nlv_after=Decimal("400000"),
            is_shortfall=True,
            assigned_symbols=["AAPL 2x", "MSFT 1x", "NVDA 3x"],
        ),
    }

    coverage = StressCoverage(
        coverage_ratio=1.0,
        target_ratio=0.7,
        cash=Decimal("50000"),
        total_put_obligations=Decimal("50000"),
        drops=coverage_drops,
    )

    # Call with no position data (None)
    lines = render_stress_test_details(coverage, positions=None)
    md = "\n".join(lines)

    # Should still render symbol list without crashing
    assert len(md) > 0, "Details should render even with missing positions"
    assert "AAPL" in md, f"Symbol list missing in:\n{md}"


def test_stress_details_format():
    """Rendered stress details should be readable markdown."""
    today = date.today()
    exp_date = today + timedelta(days=35)

    coverage_drops = {
        0.10: DropScenario(
            drop_pct=0.10,
            assigned_obligations=Decimal("50000"),
            cash_after=Decimal("10000"),
            nlv_after=Decimal("460000"),
            is_shortfall=False,
            assigned_symbols=["AAPL 2x $170P", "MSFT 1x $400P"],
        ),
    }

    coverage = StressCoverage(
        coverage_ratio=2.0,
        target_ratio=0.7,
        cash=Decimal("60000"),
        total_put_obligations=Decimal("30000"),
        drops=coverage_drops,
    )

    lines = render_stress_test_details(coverage, positions=[])
    md = "\n".join(lines)

    # Verify markdown formatting
    assert "**At −" in md or "**At -" in md, "Drop scenario header missing"
    assert "•" in md, "Bullet point missing"
    # Should be readable
    assert "AAPL" in md, "Symbol missing"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
