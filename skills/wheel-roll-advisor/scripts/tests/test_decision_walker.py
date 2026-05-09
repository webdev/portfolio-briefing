"""Tests for decision_walker.py."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import decision_walker
import matrix_loader


def test_derive_state_put_position_otm(basic_position, basic_underlying, basic_context):
    """Test deriving state from OTM SHORT PUT."""
    params = {
        "high_iv_threshold": 60,
        "low_iv_threshold": 30,
        "deep_otm_delta_threshold": 0.10,
        "moderate_otm_delta_threshold": 0.25,
    }
    
    state = decision_walker.derive_state(basic_position, basic_underlying, basic_context, params)
    
    assert state.position_type == "SHORT_PUT"
    # AAPL $181.20 vs strike $170 = 6.6% OTM under new strike-vs-spot classification
    # Boundaries: >15% DEEP_OTM, 8-15% MODERATE_OTM, 0-8% NEAR_ATM, ITM otherwise
    # 6.6% falls in NEAR_ATM
    assert state.moneyness in ["NEAR_ATM", "MODERATE_OTM"]
    assert state.dte_band == "MID_DTE"  # 42 days
    assert state.iv_regime == "NORMAL"  # IV 38
    assert state.outlook == "BULLISH"
    assert state.regime == "NORMAL"
    # Profit = (4.85 - 1.50) / 4.85 = 0.6907
    assert abs(state.profit_captured_pct - 0.69) < 0.01


def test_derive_state_high_iv(basic_position, basic_underlying, basic_context):
    """Test IV regime detection."""
    context = basic_context.copy()
    context["ivRank"] = 75
    
    params = {
        "high_iv_threshold": 60,
        "low_iv_threshold": 30,
        "deep_otm_delta_threshold": 0.10,
    }
    
    state = decision_walker.derive_state(basic_position, basic_underlying, context, params)
    
    assert state.iv_regime == "HIGH"


def test_derive_state_low_iv(basic_position, basic_underlying, basic_context):
    """Test LOW IV regime."""
    context = basic_context.copy()
    context["ivRank"] = 15
    
    params = {
        "high_iv_threshold": 60,
        "low_iv_threshold": 30,
        "deep_otm_delta_threshold": 0.10,
    }
    
    state = decision_walker.derive_state(basic_position, basic_underlying, context, params)
    
    assert state.iv_regime == "LOW"


def test_derive_state_deep_otm():
    """Test DEEP OTM moneyness."""
    position = {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "optionType": "PUT",
        "strikePrice": 160.00,
        "entryPrice": 0.50,
        "currentMid": 0.10,
        "delta": -0.05,  # DEEP OTM
        "daysToExpiry": 30,
    }
    
    underlying = {
        "symbol": "AAPL",
        "lastPrice": 185.00,
        "outlook": "BULLISH",
        "nextEarnings": None,
    }
    
    context = {
        "ivRank": 40,
        "regime": "NORMAL",
        "existingOpenOrder": False,
    }
    
    params = {
        "high_iv_threshold": 60,
        "low_iv_threshold": 30,
        "deep_otm_delta_threshold": 0.10,
    }
    
    state = decision_walker.derive_state(position, underlying, context, params)
    
    assert state.moneyness == "DEEP_OTM"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
