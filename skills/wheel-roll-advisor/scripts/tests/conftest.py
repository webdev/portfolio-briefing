"""Test fixtures for wheel-roll-advisor."""

import pytest
from pathlib import Path


@pytest.fixture
def basic_position():
    """Basic SHORT PUT position fixture."""
    return {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "optionType": "PUT",
        "strikePrice": 170.00,
        "expirationDate": "2026-06-19",
        "quantity": 1,
        "entryPrice": 4.85,
        "currentMid": 1.50,
        "delta": -0.22,
        "daysToExpiry": 42,
        "dayChange": -0.8,
    }


@pytest.fixture
def basic_underlying():
    """Basic underlying data fixture."""
    return {
        "symbol": "AAPL",
        "lastPrice": 181.20,
        "outlook": "BULLISH",
        "nextEarnings": None,
    }


@pytest.fixture
def basic_context():
    """Basic context fixture."""
    return {
        "ivRank": 38,
        "regime": "NORMAL",
        "existingOpenOrder": False,
    }


@pytest.fixture
def basic_chain():
    """Basic options chain fixture."""
    return {
        "expirations": ["2026-06-26", "2026-07-17", "2026-08-21"],
        "candidates": [
            {
                "expirationDate": "2026-07-17",
                "strikePrice": 170.00,
                "optionType": "PUT",
                "bid": 1.85,
                "ask": 1.95,
                "delta": -0.20,
                "openInterest": 145,
                "volume": 32,
                "daysToExpiry": 71,
            },
            {
                "expirationDate": "2026-07-17",
                "strikePrice": 165.00,
                "optionType": "PUT",
                "bid": 1.45,
                "ask": 1.55,
                "delta": -0.16,
                "openInterest": 98,
                "volume": 18,
                "daysToExpiry": 71,
            },
        ],
    }


@pytest.fixture
def full_input(basic_position, basic_underlying, basic_context, basic_chain):
    """Complete input JSON structure."""
    return {
        "position": basic_position,
        "underlying": basic_underlying,
        "context": basic_context,
        "chain": basic_chain,
    }
