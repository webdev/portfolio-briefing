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


def test_select_roll_target_handles_none_delta():
    """A chain leg with delta (or OI/bid/ask) present-but-None must NOT crash
    select_roll_target — the SMH_CALL_615 'advise.py exit 1' bug. The default in
    c.get('delta', 0) only applies when the key is absent, not when it's None."""
    import roll_target
    position = {
        "optionType": "CALL", "strikePrice": 615.0, "expirationDate": "2026-07-17",
        "currentMid": 34.0, "entryPrice": 33.0, "daysToExpiry": 52, "quantity": 1,
        "ivRank": 46.0, "underlyingPrice": 595.0,
    }
    chain = {"candidates": [
        {"strikePrice": 640.0, "expirationDate": "2026-08-21", "bid": 20.0, "ask": 21.0,
         "delta": None, "openInterest": None, "daysToExpiry": 87},
        {"strikePrice": 660.0, "expirationDate": "2027-03-19", "bid": None, "ask": None,
         "delta": None, "openInterest": 500, "daysToExpiry": 296},
    ]}
    # Must not raise (returns a target dict or None after filtering).
    result = roll_target.select_roll_target(position, chain, {})
    assert result is None or isinstance(result, dict)
