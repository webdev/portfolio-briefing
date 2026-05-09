"""Test theta aggregation in the health panel.

Verify that:
1. Short options with negative theta produce positive total theta (income to seller)
2. Long options with negative theta produce negative total theta (cost to holder)
3. Mixed positions aggregate correctly
4. Missing theta values are handled gracefully
"""

from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from render.panels import render_health


def test_short_put_positive_theta():
    """Short put with theta=-0.02/share should contribute +$4/day per contract."""
    equity_reviews = []
    options_positions = [
        {
            "assetType": "OPTION",
            "underlying": "AAPL",
            "symbol": "AAPL_PUT_170_20260618",
            "type": "PUT",
            "qty": -2,  # SHORT 2 contracts
            "delta": -0.25,
            "gamma": 0.005,
            "theta": -0.02,  # per-share, per-day (E*TRADE convention)
            "vega": -0.10,
        }
    ]

    # Render the health panel
    lines = render_health(equity_reviews, nlv=100000.0, options_positions=options_positions)
    md = "\n".join(lines)

    # Check that theta is positive: theta(-0.02) × qty(-2) × 100 = 4.0
    # "Theta: $+4 / day"
    assert "Theta: $" in md, f"Theta line missing in:\n{md}"
    # Extract the theta value
    import re
    theta_match = re.search(r"Theta: \$([+-]?[\d,]+)", md)
    assert theta_match, f"Theta value not found in:\n{md}"
    theta_str = theta_match.group(1).replace(",", "")
    theta_val = float(theta_str)

    # Should be positive (at least close to +4)
    assert theta_val > 0, f"Expected positive theta for short premium, got {theta_val}: {md}"
    assert theta_val >= 3 and theta_val <= 5, f"Expected ~4, got {theta_val}"


def test_long_call_negative_theta():
    """Long call with theta=-0.03/share should contribute -$3/day per contract (time decay)."""
    equity_reviews = []
    options_positions = [
        {
            "assetType": "OPTION",
            "underlying": "MSFT",
            "symbol": "MSFT_CALL_450_20260718",
            "type": "CALL",
            "qty": 1,  # LONG 1 contract
            "delta": 0.60,
            "gamma": 0.008,
            "theta": -0.03,  # negative for long option (time decay is a cost)
            "vega": 0.15,
        }
    ]

    lines = render_health(equity_reviews, nlv=100000.0, options_positions=options_positions)
    md = "\n".join(lines)

    import re
    theta_match = re.search(r"Theta: \$([+-]?[\d,]+)", md)
    assert theta_match, f"Theta value not found in:\n{md}"
    theta_str = theta_match.group(1).replace(",", "")
    theta_val = float(theta_str)

    # Should be negative: theta(-0.03) × qty(1) × 100 = -3.0
    assert theta_val < 0, f"Expected negative theta for long option, got {theta_val}: {md}"
    assert theta_val <= -2 and theta_val >= -4, f"Expected ~-3, got {theta_val}"


def test_mixed_short_and_long_theta():
    """Portfolio with short puts and long calls should aggregate net theta correctly."""
    equity_reviews = []
    options_positions = [
        {
            "assetType": "OPTION",
            "underlying": "AAPL",
            "symbol": "AAPL_PUT_170_20260618",
            "type": "PUT",
            "qty": -1,  # SHORT 1 put
            "delta": -0.20,
            "gamma": 0.004,
            "theta": -0.02,  # Short premium seller receives theta
            "vega": -0.08,
        },
        {
            "assetType": "OPTION",
            "underlying": "MSFT",
            "symbol": "MSFT_CALL_450_20260718",
            "type": "CALL",
            "qty": 2,  # LONG 2 calls
            "delta": 0.55,
            "gamma": 0.010,
            "theta": -0.01,  # Long option theta decay
            "vega": 0.12,
        },
    ]

    lines = render_health(equity_reviews, nlv=100000.0, options_positions=options_positions)
    md = "\n".join(lines)

    import re
    theta_match = re.search(r"Theta: \$([+-]?[\d,]+)", md)
    assert theta_match, f"Theta value not found in:\n{md}"
    theta_str = theta_match.group(1).replace(",", "")
    theta_val = float(theta_str)

    # Expected: short put (+2) + long calls (-2) = 0
    # theta(-0.02) × (-1) × 100 = 2.0
    # theta(-0.01) × 2 × 100 = -2.0
    # Net = 0.0
    expected_net = 2.0 - 2.0
    assert abs(theta_val - expected_net) < 1, f"Expected ~0, got {theta_val}: {md}"


def test_missing_theta_values_handled():
    """Options with missing theta should not crash and be skipped in calculation."""
    equity_reviews = []
    options_positions = [
        {
            "assetType": "OPTION",
            "underlying": "AAPL",
            "symbol": "AAPL_PUT_170_20260618",
            "type": "PUT",
            "qty": -1,
            "delta": -0.20,
            "gamma": 0.004,
            # theta is missing
            "vega": -0.08,
        },
        {
            "assetType": "OPTION",
            "underlying": "MSFT",
            "symbol": "MSFT_CALL_450_20260718",
            "type": "CALL",
            "qty": 1,
            "delta": 0.60,
            "gamma": 0.008,
            "theta": -0.02,  # This one has theta
            "vega": 0.12,
        },
    ]

    lines = render_health(equity_reviews, nlv=100000.0, options_positions=options_positions)
    md = "\n".join(lines)

    # Should render without error and show theta line
    assert "Theta:" in md, f"Theta line missing in:\n{md}"

    import re
    theta_match = re.search(r"Theta: \$([+-]?[\d,]+)", md)
    assert theta_match, f"Theta value not found in:\n{md}"
    theta_str = theta_match.group(1).replace(",", "")
    theta_val = float(theta_str)

    # Only the MSFT call contributes: theta(-0.02) × qty(1) × 100 = -2.0
    assert theta_val == -2.0, f"Expected -2.0 (only MSFT call), got {theta_val}"


def test_large_short_premium_portfolio():
    """Large short premium portfolio with multiple contracts should show strongly positive theta."""
    equity_reviews = []
    options_positions = [
        {
            "assetType": "OPTION",
            "underlying": "AAPL",
            "type": "PUT",
            "qty": -5,
            "theta": -0.025,
        },
        {
            "assetType": "OPTION",
            "underlying": "MSFT",
            "type": "PUT",
            "qty": -3,
            "theta": -0.020,
        },
        {
            "assetType": "OPTION",
            "underlying": "NVDA",
            "type": "CALL",
            "qty": -2,
            "theta": -0.015,  # Short call also has negative theta (premium seller)
        },
    ]

    lines = render_health(equity_reviews, nlv=500000.0, options_positions=options_positions)
    md = "\n".join(lines)

    import re
    theta_match = re.search(r"Theta: \$([+-]?[\d,]+)", md)
    assert theta_match, f"Theta value not found in:\n{md}"
    theta_str = theta_match.group(1).replace(",", "")
    theta_val = float(theta_str)

    # Expected:
    # AAPL: (-0.025) × (-5) × 100 = 12.5
    # MSFT: (-0.020) × (-3) × 100 = 6.0
    # NVDA: (-0.015) × (-2) × 100 = 3.0
    # Total = 21.5
    expected_net = 12.5 + 6.0 + 3.0
    assert abs(theta_val - expected_net) < 1, f"Expected ~{expected_net}, got {theta_val}"
    assert theta_val > 20, f"Expected large positive theta for short premium portfolio, got {theta_val}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
