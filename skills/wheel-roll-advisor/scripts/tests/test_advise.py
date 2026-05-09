"""Tests for advise.py."""

import pytest
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import advise as advise_module


def test_advise_moderate_otm_bullish(full_input):
    """Test advise on MODERATE OTM BULLISH position with 69% profit."""
    result = advise_module.advise(
        position=full_input["position"],
        underlying=full_input["underlying"],
        context=full_input["context"],
        chain=full_input["chain"],
    )
    
    assert isinstance(result, dict)
    assert "decision" in result
    assert "matrixCell" in result
    assert "rationale" in result
    # Position has 69% profit, MODERATE OTM, MID_DTE, BULLISH, NORMAL IV
    # Should match PUT_NORMAL_MOD_OTM_TAKEPROFIT or similar
    assert result["decision"] in ["CLOSE_FOR_PROFIT", "GTC_LIMIT_75", "HOLD"]


def test_advise_output_schema(full_input):
    """Test that output has required fields."""
    result = advise_module.advise(
        position=full_input["position"],
        underlying=full_input["underlying"],
        context=full_input["context"],
        chain=full_input["chain"],
    )
    
    required_fields = [
        "decision",
        "matrixCell",
        "rationale",
        "rollTarget",
        "warnings",
        "nextReviewDate",
        "position",
        "state",
    ]
    
    for field in required_fields:
        assert field in result, f"Missing field: {field}"


def test_advise_deep_otm_high_profit():
    """Test DEEP OTM position with 80%+ profit."""
    position = {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "optionType": "PUT",
        "strikePrice": 170.00,
        "expirationDate": "2026-05-23",
        "entryPrice": 1.00,
        "currentMid": 0.20,  # 80% profit
        "delta": -0.08,  # DEEP OTM
        "daysToExpiry": 15,
        "dayChange": 0.0,
    }
    
    underlying = {
        "symbol": "AAPL",
        "lastPrice": 185.00,
        "outlook": "BULLISH",
        "nextEarnings": None,
    }
    
    context = {
        "ivRank": 25,
        "regime": "NORMAL",
        "existingOpenOrder": False,
    }
    
    chain = {"expirations": [], "candidates": []}
    
    result = advise_module.advise(position, underlying, context, chain)
    
    # DEEP OTM with 80%+ should close for profit or hold/wait
    assert result["decision"] in ["CLOSE_FOR_PROFIT", "HOLD", "LET_EXPIRE"]


def test_advise_with_loss_stop():
    """Test that loss stop guardrail fires."""
    position = {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "optionType": "PUT",
        "strikePrice": 170.00,
        "expirationDate": "2026-06-19",
        "entryPrice": 2.00,
        "currentMid": 4.50,  # 2.25x loss (> 2.0x threshold)
        "delta": -0.45,
        "daysToExpiry": 42,  # Monthly
        "dayChange": 0.0,
    }
    
    underlying = {
        "symbol": "AAPL",
        "lastPrice": 165.00,
        "outlook": "BEARISH",
        "nextEarnings": None,
    }
    
    context = {
        "ivRank": 45,
        "regime": "NORMAL",
        "existingOpenOrder": False,
    }
    
    chain = {"expirations": [], "candidates": []}
    
    result = advise_module.advise(position, underlying, context, chain)
    
    # Loss stop should fire and return CLOSE
    assert result["decision"] == "CLOSE"
    assert result["matrixCell"] == "GUARDRAIL_LOSS_STOP"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
