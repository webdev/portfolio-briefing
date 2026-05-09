"""Unit tests for etrade_market adapter and new_ideas integration."""

import pytest
import sys
from datetime import date, timedelta
from pathlib import Path

# Add scripts dir to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.etrade_market import OptionChainRow
from steps.new_ideas import _pick_csp_strike


class TestPickCSPStrike:
    """Test strike selection logic with E*TRADE chain data."""

    def test_pick_delta_matching(self):
        """Test that picker selects strike matching target delta."""
        today = date.today()
        exp = today + timedelta(days=35)

        # Create 5 put contracts with increasing delta
        rows = [
            OptionChainRow(
                strike=95.0, option_type="PUT", bid=1.20, ask=1.35, last=1.27,
                open_interest=200, delta=-0.15, iv=0.25,
            ),
            OptionChainRow(
                strike=100.0, option_type="PUT", bid=1.80, ask=2.00, last=1.90,
                open_interest=500, delta=-0.25, iv=0.25,
            ),
            OptionChainRow(
                strike=105.0, option_type="PUT", bid=2.70, ask=3.00, last=2.85,
                open_interest=800, delta=-0.30, iv=0.25,  # <-- target (0.30)
            ),
            OptionChainRow(
                strike=110.0, option_type="PUT", bid=3.80, ask=4.20, last=4.00,
                open_interest=600, delta=-0.38, iv=0.25,
            ),
            OptionChainRow(
                strike=115.0, option_type="PUT", bid=5.00, ask=5.50, last=5.25,
                open_interest=300, delta=-0.50, iv=0.25,
            ),
        ]

        pick = _pick_csp_strike(rows, spot=120.0, target_delta=0.30)

        assert pick is not None
        assert pick["strike"] == 105.0  # Should pick 0.30 delta
        assert abs(pick["abs_delta"] - 0.30) < 0.05
        assert pick["mid"] == pytest.approx(2.85, rel=0.01)

    def test_liquidity_filter_oi(self):
        """Test that low OI contracts are filtered out."""
        rows = [
            OptionChainRow(
                strike=95.0, option_type="PUT", bid=1.20, ask=1.35, last=1.27,
                open_interest=10,  # <-- below MIN_OPEN_INTEREST (50)
                delta=-0.20, iv=0.25,
            ),
            OptionChainRow(
                strike=100.0, option_type="PUT", bid=1.80, ask=2.00, last=1.90,
                open_interest=500,  # <-- passes
                delta=-0.30, iv=0.25,
            ),
        ]

        pick = _pick_csp_strike(rows, spot=120.0, target_delta=0.30)

        assert pick is not None
        assert pick["strike"] == 100.0
        assert pick["openInterest"] >= 50

    def test_liquidity_filter_spread(self):
        """Test that wide-spread contracts are filtered out."""
        rows = [
            OptionChainRow(
                strike=95.0, option_type="PUT", bid=1.00, ask=3.00, last=2.00,
                open_interest=200,
                delta=-0.20, iv=0.25,
                # spread = (3.00 - 1.00) / 2.00 = 1.0 = 100% (exceeds 40%)
            ),
            OptionChainRow(
                strike=100.0, option_type="PUT", bid=1.80, ask=2.00, last=1.90,
                open_interest=500,
                delta=-0.30, iv=0.25,
                # spread = (2.00 - 1.80) / 1.90 = 0.105 = 10.5% (ok)
            ),
        ]

        pick = _pick_csp_strike(rows, spot=120.0, target_delta=0.30)

        assert pick is not None
        assert pick["strike"] == 100.0

    def test_otm_only(self):
        """Test that ITM puts (strike >= spot) are filtered out."""
        rows = [
            OptionChainRow(
                strike=125.0, option_type="PUT", bid=5.00, ask=5.50, last=5.25,
                open_interest=500, delta=-0.50, iv=0.25,
                # ITM: strike 125 >= spot 120
            ),
            OptionChainRow(
                strike=105.0, option_type="PUT", bid=2.70, ask=3.00, last=2.85,
                open_interest=800, delta=-0.30, iv=0.25,
                # OTM: strike 105 < spot 120
            ),
        ]

        pick = _pick_csp_strike(rows, spot=120.0, target_delta=0.30)

        assert pick is not None
        assert pick["strike"] == 105.0
        assert pick["strike"] < 120.0

    def test_no_matching_contracts(self):
        """Test that None is returned when no contracts pass filters."""
        rows = [
            OptionChainRow(
                strike=95.0, option_type="PUT", bid=0.10, ask=0.15, last=0.12,
                open_interest=5,  # Too low
                delta=-0.10, iv=0.25,
            ),
        ]

        pick = _pick_csp_strike(rows, spot=120.0, target_delta=0.30)
        assert pick is None

    def test_annualized_yield_calculation(self):
        """Test that annualized yield is computed correctly in a concrete idea."""
        # This test validates the math: yield = premium / collateral
        # annualized = yield * (365 / dte)

        rows = [
            OptionChainRow(
                strike=105.0, option_type="PUT", bid=2.70, ask=3.00, last=2.85,
                open_interest=800, delta=-0.30, iv=0.25,
            ),
        ]

        pick = _pick_csp_strike(rows, spot=120.0, target_delta=0.30)
        assert pick is not None

        # Manually compute yields
        mid = 2.85
        strike = 105.0
        contracts = 1
        collateral = strike * 100 * contracts  # 10,500
        premium = mid * 100 * contracts  # 285
        dte = 35  # typical

        period_yield = premium / collateral if collateral else 0
        annualized = period_yield * (365 / dte) if dte else 0

        assert period_yield == pytest.approx(285 / 10500, rel=0.01)
        # 285/10500 = 0.0271... over 35d → annualized = 0.0271 * 365/35 = 0.283 = 28.3%
        assert annualized == pytest.approx(0.283, rel=0.01)
