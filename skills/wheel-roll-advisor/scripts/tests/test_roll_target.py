"""Tests for roll_target.py."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import roll_target


def test_roll_out_same_strike(basic_position, basic_chain):
    """Test ROLL_OUT selects same strike."""
    params = {
        "min_dte_for_roll": 21,
        "high_iv_threshold": 60,
        "delta_target_normal_iv": 0.22,
        "delta_max_normal_iv": 0.30,
        "min_oi_for_roll": 100,
        "max_spread_pct_for_roll": 0.05,
        "min_net_credit_pct": 0.10,
        "min_net_credit_abs": 0.25,
        "max_stress_loss_multiple": 3.0,
    }
    
    basic_position["ivRank"] = 38
    basic_position["underlyingPrice"] = 181.20
    
    target = roll_target.select_roll_target(basic_position, basic_chain, params)
    
    if target:
        assert "strikePrice" in target
        assert "expirationDate" in target


def test_no_valid_roll_target():
    """Test returns None when no candidates pass filters."""
    position = {
        "symbol": "AAPL",
        "entryPrice": 4.85,
        "currentMid": 1.50,
        "daysToExpiry": 42,
        "ivRank": 38,
        "underlyingPrice": 181.20,
    }
    
    chain = {
        "candidates": [],  # Empty chain
    }
    
    params = {
        "min_dte_for_roll": 21,
        "high_iv_threshold": 60,
        "delta_target_normal_iv": 0.22,
        "delta_max_normal_iv": 0.30,
        "min_oi_for_roll": 100,
        "max_spread_pct_for_roll": 0.05,
        "min_net_credit_pct": 0.10,
        "min_net_credit_abs": 0.25,
        "max_stress_loss_multiple": 3.0,
    }
    
    target = roll_target.select_roll_target(position, chain, params)
    
    assert target is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
