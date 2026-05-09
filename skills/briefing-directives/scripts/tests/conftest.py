"""Test fixtures for briefing directives."""

import pytest
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


@pytest.fixture
def state_dir():
    """Temporary state directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_directive_defer():
    """Sample DEFER directive."""
    return {
        "type": "DEFER",
        "target": {
            "kind": "option_position",
            "identifier": "AAPL  260619P00170000",
        },
        "reason": "Wait until earnings clear.",
        "expires": {
            "trigger": "earnings_passed",
            "symbol": "AAPL",
        },
        "created_via": "test",
    }


@pytest.fixture
def sample_directive_manual():
    """Sample MANUAL directive."""
    return {
        "type": "MANUAL",
        "target": {
            "kind": "position_scope",
            "symbol": "MSFT",
            "position_type": "short_call",
        },
        "reason": "Managing manually.",
        "expires": {
            "trigger": "open_ended",
        },
        "created_via": "test",
    }


@pytest.fixture
def sample_directive_override():
    """Sample OVERRIDE directive."""
    return {
        "type": "OVERRIDE",
        "target": {
            "kind": "option_position",
            "identifier": "AMD  260516P00180000",
        },
        "parameter": "take_profit_threshold",
        "new_value": 0.80,
        "old_value": 0.50,
        "reason": "High conviction.",
        "expires": {
            "trigger": "position_closed",
            "position_identifier": "AMD  260516P00180000",
        },
        "created_via": "test",
    }


@pytest.fixture
def sample_directive_watch():
    """Sample WATCH_ONLY directive."""
    return {
        "type": "WATCH_ONLY",
        "target": {
            "kind": "new_idea",
            "symbol": "NVDA",
            "source_screener": "vcp-screener",
        },
        "reason": "Waiting for breakout.",
        "expires": {
            "trigger": "price_above",
            "symbol": "NVDA",
            "level": 185.00,
        },
        "created_via": "test",
    }


@pytest.fixture
def sample_directive_suppress():
    """Sample SUPPRESS directive."""
    return {
        "type": "SUPPRESS",
        "target": {
            "kind": "symbol",
            "symbol": "BABA",
            "scope": "long_only",
        },
        "reason": "Tail risk.",
        "expires": {
            "trigger": "open_ended",
        },
        "created_via": "test",
    }


@pytest.fixture
def current_state_base():
    """Base current_state dict for trigger evaluation."""
    return {
        "current_date": date.today(),
        "positions": [
            {"identifier": "AAPL  260619P00170000", "status": "open"},
            {"identifier": "MSFT  260516C00400000", "status": "open"},
            {"identifier": "AMD  260516P00180000", "status": "open"},
        ],
        "last_close": {
            "AAPL": 175.50,
            "MSFT": 420.00,
            "NVDA": 182.00,
            "AMD": 185.00,
        },
        "earnings_calendar": {
            "AAPL": (date.today() + timedelta(days=14)),
            "MSFT": (date.today() + timedelta(days=7)),
            "NVDA": (date.today() + timedelta(days=21)),
        },
        "screener_outputs": {
            "vcp-screener": ["AAPL", "MSFT", "NVDA"],
            "earnings-trade-analyzer": ["AMD"],
        },
    }


@pytest.fixture
def sample_recommendations():
    """Sample recommendations for apply_directives tests."""
    return [
        {
            "ticker": "AAPL",
            "kind": "option_position",
            "identifier": "AAPL  260619P00170000",
            "action": "ROLL",
            "recommendation": "EXECUTE",
        },
        {
            "ticker": "MSFT",
            "kind": "position_scope",
            "symbol": "MSFT",
            "position_type": "short_call",
            "action": "CLOSE",
            "recommendation": "EXECUTE",
        },
        {
            "ticker": "NVDA",
            "kind": "new_idea",
            "symbol": "NVDA",
            "source_screener": "vcp-screener",
            "action": "ENTRY",
            "recommendation": "EXECUTE",
        },
        {
            "ticker": "BABA",
            "kind": "new_idea",
            "symbol": "BABA",
            "source_screener": "earnings-trade-analyzer",
            "action": "ENTRY",
            "recommendation": "EXECUTE",
        },
        {
            "ticker": "AMD",
            "kind": "option_position",
            "identifier": "AMD  260516P00180000",
            "action": "TAKE_PROFIT",
            "recommendation": "EXECUTE",
        },
    ]
