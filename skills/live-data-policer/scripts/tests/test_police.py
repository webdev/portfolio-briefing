"""Tests for live-data-policer."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from police import police_data_freshness


def _provenance(source: str, age_minutes: float = 0) -> dict:
    fetched_at = (datetime.now() - timedelta(minutes=age_minutes)).isoformat()
    return {"source": source, "fetched_at": fetched_at, "fresh": age_minutes < 30}


def test_all_live_passes():
    snapshot = {
        "data_provenance": {
            "positions": _provenance("etrade_live", 5),
            "broker_positions": _provenance("etrade_live", 5),
            "quotes": _provenance("yfinance", 2),
            "chains": _provenance("etrade_live", 10),
            "iv_ranks": _provenance("yfinance_252d", 60),
            "earnings_calendar": _provenance("yfinance", 60),
        }
    }
    result = police_data_freshness(snapshot)
    assert result.verdict == "PASS"
    assert result.live is True


def test_fixture_source_blocks():
    snapshot = {
        "data_provenance": {
            "positions": _provenance("fixture", 0),
            "broker_positions": _provenance("etrade_live", 5),
            "quotes": _provenance("yfinance", 2),
            "chains": _provenance("etrade_live", 10),
            "iv_ranks": _provenance("yfinance_252d", 60),
            "earnings_calendar": _provenance("yfinance", 60),
        }
    }
    result = police_data_freshness(snapshot)
    assert result.verdict == "BLOCK"
    assert "positions" in result.blocking_sources
    assert "DO NOT TRADE" in result.panel_md


def test_very_stale_quotes_blocks():
    """Quotes 9+ hours old — exceeds even the overnight threshold (480 min)."""
    snapshot = {
        "data_provenance": {
            "positions": _provenance("etrade_live", 5),
            "broker_positions": _provenance("etrade_live", 5),
            "quotes": _provenance("yfinance", 600),  # 10 hours
            "chains": _provenance("etrade_live", 10),
            "iv_ranks": _provenance("yfinance_252d", 60),
            "earnings_calendar": _provenance("yfinance", 60),
        }
    }
    result = police_data_freshness(snapshot)
    assert result.verdict == "BLOCK"
    assert "quotes" in result.blocking_sources


def test_missing_provenance_blocks():
    snapshot = {}  # no data_provenance at all
    result = police_data_freshness(snapshot)
    assert result.verdict == "BLOCK"
    assert "provenance" in result.blocking_sources or any(
        "provenance" in s.get("source", "") for s in result.stale_sources
    )


def test_replay_source_blocks():
    snapshot = {
        "data_provenance": {
            "positions": _provenance("replay", 5),
            "broker_positions": _provenance("etrade_live", 5),
            "quotes": _provenance("yfinance", 2),
            "chains": _provenance("etrade_live", 10),
            "iv_ranks": _provenance("yfinance_252d", 60),
            "earnings_calendar": _provenance("yfinance", 60),
        }
    }
    result = police_data_freshness(snapshot)
    assert result.verdict == "BLOCK"


def test_advisory_only_for_stale_iv_ranks():
    """IV ranks 25 hours old → WARN not BLOCK (not in critical sources)."""
    snapshot = {
        "data_provenance": {
            "positions": _provenance("etrade_live", 5),
            "broker_positions": _provenance("etrade_live", 5),
            "quotes": _provenance("yfinance", 2),
            "chains": _provenance("etrade_live", 10),
            "iv_ranks": _provenance("yfinance_252d", 1500),  # 25h, max 24h
            "earnings_calendar": _provenance("yfinance", 60),
        }
    }
    result = police_data_freshness(snapshot)
    assert result.verdict in ("WARN", "PASS")  # iv_ranks isn't critical
    assert result.live is True or result.verdict == "PASS"


def test_disallowed_source_for_positions_blocks():
    """positions source must be etrade_live ONLY — not even yfinance."""
    snapshot = {
        "data_provenance": {
            "positions": _provenance("yfinance", 5),  # yfinance for positions = wrong
            "broker_positions": _provenance("etrade_live", 5),
            "quotes": _provenance("yfinance", 2),
            "chains": _provenance("etrade_live", 10),
            "iv_ranks": _provenance("yfinance_252d", 60),
            "earnings_calendar": _provenance("yfinance", 60),
        }
    }
    result = police_data_freshness(snapshot)
    assert result.verdict == "BLOCK"


def test_panel_lists_all_stale_sources():
    snapshot = {
        "data_provenance": {
            "positions": _provenance("fixture", 0),
            "broker_positions": _provenance("missing", 0),
            "quotes": _provenance("yfinance", 2),
            "chains": _provenance("etrade_live", 10),
            "iv_ranks": _provenance("yfinance_252d", 60),
            "earnings_calendar": _provenance("yfinance", 60),
        }
    }
    result = police_data_freshness(snapshot)
    assert "positions" in result.panel_md
    assert "broker_positions" in result.panel_md


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
