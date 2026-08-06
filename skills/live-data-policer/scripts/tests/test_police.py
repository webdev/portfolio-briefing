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


def test_fmp_fallback_earnings_source_passes():
    """Task #43 defect 3 (2026-08-03 briefing header): '🟡 earnings_calendar
    (source `yfinance+fmp_fallback`): source 'yfinance+fmp_fallback' not in
    allow-list ['yfinance']' — the earnings-unknown fix tags the combined
    live source, but the policer's allow-list wasn't updated. Both the
    combined tag and plain 'fmp' must PASS."""
    for src in ("yfinance+fmp_fallback", "fmp"):
        snapshot = {
            "data_provenance": {
                "positions": _provenance("etrade_live", 5),
                "broker_positions": _provenance("etrade_live", 5),
                "quotes": _provenance("yfinance", 2),
                "chains": _provenance("etrade_live", 10),
                "iv_ranks": _provenance("yfinance_252d", 60),
                "earnings_calendar": _provenance(src, 60),
            }
        }
        result = police_data_freshness(snapshot)
        assert result.verdict == "PASS", src
        assert not result.stale_sources, src


def test_task36_etrade_routed_sources_pass_allow_list():
    """Task #36: snapshot chains/quotes now route through E*TRADE with a
    labeled yfinance fallback — provenance carries the MEASURED split label
    ('etrade', 'etrade+yfinance-fallback'). Both must PASS the allow-list on
    both chains and quotes; the legacy 'etrade_live' stamp stays allowed for
    older snapshots."""
    for src in ("etrade", "etrade+yfinance-fallback", "etrade_live"):
        snapshot = {
            "data_provenance": {
                "positions": _provenance("etrade_live", 5),
                "broker_positions": _provenance("etrade_live", 5),
                "quotes": {**_provenance(src, 2), "requested": 150,
                           "fetched": 149, "etrade": 146, "yfinance": 3},
                "chains": {**_provenance(src, 10), "etrade": 112,
                           "yfinance": 38},
                "iv_ranks": _provenance("yfinance_252d", 60),
                "earnings_calendar": _provenance("yfinance", 60),
            }
        }
        result = police_data_freshness(snapshot)
        assert result.verdict == "PASS", src
        assert not result.stale_sources, src


def test_task36_quote_coverage_line_reports_etrade_yfinance_split():
    """Task #36: when coverage dips below the 90% floor, the advisory line
    reports the measured etrade/yfinance split so the reader can see WHICH
    backend degraded (the old line only gave a total: 'only 23/134 symbols
    (17%) received live quotes')."""
    snapshot = {
        "data_provenance": {
            "positions": _provenance("etrade_live", 5),
            "broker_positions": _provenance("etrade_live", 5),
            "quotes": {**_provenance("etrade+yfinance-fallback", 2),
                       "requested": 134, "fetched": 100,
                       "etrade": 97, "yfinance": 3},
            "chains": _provenance("etrade", 10),
            "iv_ranks": _provenance("yfinance_252d", 60),
            "earnings_calendar": _provenance("yfinance", 60),
        }
    }
    result = police_data_freshness(snapshot)
    assert result.verdict == "WARN"
    cov = [s for s in result.stale_sources if s["source"] == "quote_coverage"]
    assert cov, "coverage line missing"
    assert "97 etrade" in cov[0]["issue"]
    assert "3 yfinance" in cov[0]["issue"]


def test_task36_coverage_line_without_split_counts_still_renders():
    """Older snapshots without etrade/yfinance counts keep the plain
    coverage line (fail-open on missing metadata)."""
    snapshot = {
        "data_provenance": {
            "positions": _provenance("etrade_live", 5),
            "broker_positions": _provenance("etrade_live", 5),
            "quotes": {**_provenance("yfinance", 2),
                       "requested": 36, "fetched": 22},
            "chains": _provenance("etrade_live", 10),
            "iv_ranks": _provenance("yfinance_252d", 60),
            "earnings_calendar": _provenance("yfinance", 60),
        }
    }
    result = police_data_freshness(snapshot)
    cov = [s for s in result.stale_sources if s["source"] == "quote_coverage"]
    assert cov
    assert "only 22/36" in cov[0]["issue"]
    assert "split" not in cov[0]["issue"]


def test_wholesale_fallback_renders_loud_line():
    """2026-08-06: silent wholesale fallback is the bug class — when
    routing was attempted and provenance carries a wholesale reason, the
    Live-Data panel MUST render '⚠ E*TRADE routing fell back wholesale'."""
    snapshot = {
        "data_provenance": {
            "positions": _provenance("etrade_live", 5),
            "broker_positions": _provenance("etrade_live", 5),
            "quotes": _provenance("yfinance", 2),
            "chains": {**_provenance("yfinance", 10),
                       "routing_attempted": True,
                       "etrade": 0, "yfinance": 420,
                       "wholesale_fallback_reason":
                           "circuit breaker tripped"},
            "iv_ranks": _provenance("yfinance_252d", 60),
            "earnings_calendar": _provenance("yfinance", 60),
        }
    }
    result = police_data_freshness(snapshot)
    routed = [s for s in result.stale_sources
              if s["source"] == "chains_routing"]
    assert routed, "wholesale-fallback line missing from policer"
    assert "fell back wholesale" in routed[0]["issue"]
    assert "circuit breaker tripped" in routed[0]["issue"]
    assert "fell back wholesale" in result.panel_md


def test_no_wholesale_line_when_routing_delivered():
    """A mixed etrade+yfinance cycle (routing worked) renders NO wholesale
    line — the flag keys off the recorded reason, not the split."""
    snapshot = {
        "data_provenance": {
            "positions": _provenance("etrade_live", 5),
            "broker_positions": _provenance("etrade_live", 5),
            "quotes": _provenance("yfinance", 2),
            "chains": {**_provenance("etrade+yfinance-fallback", 10),
                       "routing_attempted": True,
                       "etrade": 150, "yfinance": 270},
            "iv_ranks": _provenance("yfinance_252d", 60),
            "earnings_calendar": _provenance("yfinance", 60),
        }
    }
    result = police_data_freshness(snapshot)
    assert not [s for s in result.stale_sources
                if s["source"].endswith("_routing")]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
