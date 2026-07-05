"""Tests for the M2 ingest-layer additions: briefing_mentions
materialization + search helpers."""

from __future__ import annotations

from app import ingest


def test_briefing_mentions_table_populated():
    conn = ingest.connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM briefing_mentions").fetchone()[0]
        # Two fixture briefings × multiple mention sources should produce
        # at least 6 rows (equity_reviews + options_reviews + actions).
        assert count >= 6
    finally:
        conn.close()


def test_briefing_mentions_for_ticker_finds_all_dates():
    conn = ingest.connect()
    try:
        nvda = ingest.briefing_mentions_for_ticker(conn, "NVDA")
        # NVDA appears in equity_review + options_review on both dates,
        # plus 1 action on 2026-06-30 + 1 strategy_upgrade.
        assert len(nvda) >= 4
        dates = {str(m["date"]) for m in nvda}
        assert "2026-05-10" in dates
        assert "2026-06-30" in dates
        # Action with parsed ticker
        sources = {m["source"] for m in nvda}
        assert "equity_review" in sources
        assert "action" in sources
    finally:
        conn.close()


def test_search_mentions_matches_ticker():
    conn = ingest.connect()
    try:
        rows = ingest.search_mentions(conn, "AMZN")
        assert len(rows) >= 1
        for r in rows:
            assert r["ticker"] == "AMZN" or "AMZN" in (r.get("ident") or "") or "AMZN" in (r.get("detail") or "")
    finally:
        conn.close()


def test_search_mentions_filter_by_source():
    conn = ingest.connect()
    try:
        rows = ingest.search_mentions(conn, "NVDA", source_filter="action")
        # On 2026-06-30 there's a CLOSE:NVDA_PUT_180_20260918 action.
        assert len(rows) >= 1
        for r in rows:
            assert r["source"] == "action"
    finally:
        conn.close()


def test_search_mentions_matches_date_prefix():
    conn = ingest.connect()
    try:
        rows = ingest.search_mentions(conn, "2026-05")
        # All rows should be from the 2026-05-10 fixture
        assert len(rows) >= 1
        for r in rows:
            assert str(r["date"]).startswith("2026-05")
    finally:
        conn.close()


def test_search_distinct_tickers():
    conn = ingest.connect()
    try:
        tickers = ingest.search_distinct_tickers(conn, "M")  # AMZN, NVDA both have M
        # Should at least include AMZN
        assert "AMZN" in tickers
    finally:
        conn.close()


def test_search_empty_query_returns_empty():
    conn = ingest.connect()
    try:
        assert ingest.search_mentions(conn, "") == []
        assert ingest.search_distinct_tickers(conn, "") == []
    finally:
        conn.close()


def test_refresh_projections_explicit_call():
    """Calling refresh_projections() directly re-materializes everything
    without needing connect() to do it for us."""
    ingest.refresh_projections()
    status = ingest.get_last_status()
    assert status is not None
    assert status.snapshot_count == 2
