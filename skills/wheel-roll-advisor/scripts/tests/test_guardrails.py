"""Tests for guardrails.py."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import guardrails


def test_loss_stop_monthly_triggers():
    """Test loss stop for monthly (DTE > 10) at 2.0x."""
    position = {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "strikePrice": 170.00,
        "entryPrice": 2.00,
        "currentMid": 4.50,  # 2.25x loss
        "daysToExpiry": 42,  # Monthly
    }
    
    params = {
        "loss_stop_monthly": 2.0,
        "weekly_put_dte_threshold": 10,
    }
    
    result = guardrails.check_loss_stop(position, params)
    
    assert result.fired is True
    assert result.decision.decision == "CLOSE"


def test_loss_stop_weekly_triggers():
    """Test loss stop for weekly (DTE <= 10) at 1.5x."""
    position = {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "strikePrice": 170.00,
        "entryPrice": 2.00,
        "currentMid": 3.10,  # 1.55x loss (> 1.5x threshold)
        "daysToExpiry": 5,  # Weekly
    }
    
    params = {
        "loss_stop_weekly": 1.5,
        "weekly_put_dte_threshold": 10,
    }
    
    result = guardrails.check_loss_stop(position, params)
    
    assert result.fired is True


def test_loss_stop_does_not_fire_on_profit():
    """Test that loss stop doesn't fire when profitable."""
    position = {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "strikePrice": 170.00,
        "entryPrice": 4.85,
        "currentMid": 1.50,  # Profitable (current < entry)
        "daysToExpiry": 42,
    }
    
    params = {
        "loss_stop_monthly": 2.0,
        "weekly_put_dte_threshold": 10,
    }
    
    result = guardrails.check_loss_stop(position, params)
    
    assert result.fired is False


def test_crash_stop_triggers():
    """Test crash stop on >15% intraday drop."""
    position = {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "entryPrice": 2.00,
        "currentMid": 1.50,
        "dayChange": -18.0,  # 18% drop
    }
    
    result = guardrails.check_crash_stop(position)
    
    assert result.fired is True
    assert result.decision.decision in ["CLOSE", "CLOSE_FOR_PROFIT"]


def test_open_order_wait():
    """Test that open order returns WAIT."""
    context = {
        "existingOpenOrder": True,
    }
    
    result = guardrails.check_open_order(context)
    
    assert result.fired is True
    assert result.decision.decision == "WAIT"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
