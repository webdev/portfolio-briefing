"""Unit tests for strategy upgrades computation."""

import pytest
from datetime import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from steps.strategy_upgrades import (
    compute_strategy_upgrades,
    _check_concentration,
    _earnings_conflict,
    _find_short_call,
    _is_tail_risk_name,
)


@pytest.fixture
def mock_snapshot_data():
    """Create a mock snapshot with positions, chains, earnings, quotes."""
    return {
        "positions": [
            # Equity: PLTR (755 shares @ $185, 22% weight)
            {
                "symbol": "PLTR",
                "assetType": "EQUITY",
                "qty": 755,
                "price": 185.00,
                "costBasis": 120.00,
            },
            # Equity: GOOG (415 shares @ $200, 8.3% weight, big gain)
            {
                "symbol": "GOOG",
                "assetType": "EQUITY",
                "qty": 415,
                "price": 200.00,
                "costBasis": 120.00,
            },
            # Equity: META (46 shares, sub-100 lot)
            {
                "symbol": "META",
                "assetType": "EQUITY",
                "qty": 46,
                "price": 615.59,
                "costBasis": 500.00,
            },
            # Equity: AMZN (100 shares, exact lot)
            {
                "symbol": "AMZN",
                "assetType": "EQUITY",
                "qty": 100,
                "price": 325.00,
                "costBasis": 300.00,
            },
            # Option: PLTR short call (7 contracts, 145C June 5)
            {
                "symbol": "PLTR_SHORT_CALL",
                "assetType": "OPTION",
                "underlying": "PLTR",
                "type": "CALL",
                "strike": 145.00,
                "expiration": "2026-06-05",
                "qty": -7,
                "currentMid": 2.50,
                "premiumReceived": 3.00,
            },
            # Option: AMZN short call (1 contract, 325C June 5)
            {
                "symbol": "AMZN_SHORT_CALL",
                "assetType": "OPTION",
                "underlying": "AMZN",
                "type": "CALL",
                "strike": 325.00,
                "expiration": "2026-06-05",
                "qty": -1,
                "currentMid": 2.00,
                "premiumReceived": 2.50,
            },
        ],
        "balance": {
            "accountValue": 1000000,  # $1M NLV
            "cash": 50000,
        },
        "chains": {
            # PLTR 2026-06-05 chain with short put at 0.20 delta
            "PLTR_2026-06-05": {
                "underlying": "PLTR",
                "expiration": "2026-06-05",
                "puts": [
                    {
                        "strike": 125.00,
                        "bid": 3.00,
                        "ask": 3.25,
                        "lastPrice": 3.12,
                        "delta": -0.20,
                        "openInterest": 500,
                    },
                    {
                        "strike": 110.00,
                        "bid": 1.50,
                        "ask": 1.75,
                        "lastPrice": 1.62,
                        "delta": -0.10,
                        "openInterest": 800,
                    },
                ],
                "calls": [],
            },
            # AMZN 2026-06-05 chain
            "AMZN_2026-06-05": {
                "underlying": "AMZN",
                "expiration": "2026-06-05",
                "puts": [
                    {
                        "strike": 255.00,
                        "bid": 2.40,
                        "ask": 2.70,
                        "lastPrice": 2.56,
                        "delta": -0.19,
                        "openInterest": 600,
                    },
                ],
                "calls": [],
            },
            # GOOG 2026-06-05 chain (for collar)
            "GOOG_2026-06-05": {
                "underlying": "GOOG",
                "expiration": "2026-06-05",
                "puts": [
                    {
                        "strike": 350.00,
                        "bid": 1.40,
                        "ask": 1.80,
                        "lastPrice": 1.59,
                        "delta": -0.09,
                        "openInterest": 400,
                    },
                ],
                "calls": [],
            },
        },
        "earnings_calendar": {
            # No earnings conflict for these dates
            "PLTR": "2026-07-15",
            "GOOG": "2026-07-20",
            "AMZN": "2026-07-25",
        },
        "quotes": {
            "PLTR": {"last": 185.00},
            "GOOG": {"last": 200.00},
            "META": {"last": 615.59},
            "AMZN": {"last": 325.00},
        },
    }


@pytest.fixture
def mock_config():
    return {
        "max_position_pct": 0.10,
        "max_sector_pct": 0.35,
    }


def test_concentration_check_blocks_when_would_exceed_cap():
    """Test that concentration check correctly blocks when new position would breach 10% cap."""
    # Current weight 8%, adding 3% collateral would push to 11% → should block
    conc = _check_concentration(existing_weight_pct=0.08, new_collateral=30000, nlv=1000000)
    assert conc["blocked"] is True
    assert "10%" in conc["reason"]


def test_concentration_check_allows_within_cap():
    """Test that concentration check allows when total stays under cap."""
    # Current weight 8%, adding 1% collateral → 9% total → should allow
    conc = _check_concentration(existing_weight_pct=0.08, new_collateral=10000, nlv=1000000)
    assert conc["blocked"] is False


def test_earnings_conflict_blocks_when_earnings_before_expiry():
    """Test earnings guard blocks puts when earnings occur before expiration."""
    # Earnings on 2026-06-01, expiration 2026-06-05 → conflict
    conflict = _earnings_conflict(
        {"TSLA": "2026-06-01"},
        "TSLA",
        "2026-06-05"
    )
    assert conflict is True


def test_earnings_conflict_allows_when_earnings_after_expiry():
    """Test earnings guard allows when earnings after expiration."""
    # Earnings on 2026-07-15, expiration 2026-06-05 → no conflict
    conflict = _earnings_conflict(
        {"PLTR": "2026-07-15"},
        "PLTR",
        "2026-06-05"
    )
    assert conflict is False


def test_is_tail_risk_name():
    """Test tail risk name detection."""
    assert _is_tail_risk_name("BABA") is True
    assert _is_tail_risk_name("GME") is True
    assert _is_tail_risk_name("AAPL") is False
    assert _is_tail_risk_name("MSFT") is False


def test_find_short_call():
    """Test finding short call on underlying."""
    positions = [
        {
            "symbol": "TEST_CALL",
            "assetType": "OPTION",
            "underlying": "TEST",
            "type": "CALL",
            "qty": -1,
            "strike": 150.00,
        },
        {
            "symbol": "TEST_PUT",
            "assetType": "OPTION",
            "underlying": "TEST",
            "type": "PUT",
            "qty": -1,
        },
    ]
    call = _find_short_call(positions, "TEST")
    assert call is not None
    assert call["type"] == "CALL"
    assert call["qty"] == -1


def test_covered_strangle_proposed(mock_snapshot_data, mock_config):
    """Test that covered strangle is proposed for PLTR (existing call + available put chain)."""
    upgrades = compute_strategy_upgrades(
        mock_snapshot_data,
        equity_reviews=[],
        options_reviews=[],
        params=mock_config,
    )

    # Find the PLTR strangle recommendation
    pltr_strangles = [u for u in upgrades if u.get("type") == "covered_strangle" and u.get("underlying") == "PLTR"]
    assert len(pltr_strangles) >= 1

    strangle = pltr_strangles[0]
    assert strangle["proposed"]["strike"] == 125.00
    assert strangle["proposed"]["qty"] == 7
    assert strangle["concentration_check"]["blocked"] is True  # PLTR at 22% already


def test_covered_strangle_amzn_allowed(mock_snapshot_data, mock_config):
    """Test that AMZN strangle is allowed (not blocked by concentration)."""
    upgrades = compute_strategy_upgrades(
        mock_snapshot_data,
        equity_reviews=[],
        options_reviews=[],
        params=mock_config,
    )

    amzn_strangles = [u for u in upgrades if u.get("type") == "covered_strangle" and u.get("underlying") == "AMZN"]
    assert len(amzn_strangles) >= 1

    strangle = amzn_strangles[0]
    assert strangle["concentration_check"]["blocked"] is False  # AMZN at 3.25%, collateral only 3%


def test_collar_proposed_for_goog(mock_snapshot_data, mock_config):
    """Test that collar is proposed for GOOG (big gain + good weight)."""
    upgrades = compute_strategy_upgrades(
        mock_snapshot_data,
        equity_reviews=[],
        options_reviews=[],
        params=mock_config,
    )

    goog_collars = [u for u in upgrades if u.get("type") == "collar" and u.get("underlying") == "GOOG"]
    assert len(goog_collars) >= 1

    collar = goog_collars[0]
    assert collar["proposed_put"]["strike"] == 350.00
    assert collar["floor_strike"] == 350.00
    assert collar["scenario_minus_20pct"]["saves"] > 0


def test_sublot_completion_meta(mock_snapshot_data, mock_config):
    """Test that sub-lot completion is proposed for META (46 shares)."""
    upgrades = compute_strategy_upgrades(
        mock_snapshot_data,
        equity_reviews=[],
        options_reviews=[],
        params=mock_config,
    )

    meta_subs = [u for u in upgrades if u.get("type") == "sublot_completion" and u.get("underlying") == "META"]
    assert len(meta_subs) >= 1

    sub = meta_subs[0]
    assert sub["shares_held"] == 46
    assert sub["shares_to_buy"] == 54
    assert sub["cost"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
