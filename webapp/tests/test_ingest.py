"""Test the DuckDB ingest layer against the fixture briefings."""

from __future__ import annotations

import pytest

from app import ingest


def test_connect_creates_views_and_projections():
    conn = ingest.connect()
    try:
        # All three projections exist
        for table in ("portfolio_timeseries", "positions_timeseries", "parkev_history"):
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()
            assert row[0] >= 0


        # portfolio_timeseries should have our 2 fixture briefings (V1 + V2)
        rows = conn.execute(
            "SELECT date, nlv, cash FROM portfolio_timeseries ORDER BY date"
        ).fetchall()
        assert len(rows) == 2
        assert str(rows[0][0]) == "2026-05-10"
        assert str(rows[1][0]) == "2026-06-30"
        assert rows[1][1] == 1000000.00
    finally:
        conn.close()


def test_positions_timeseries_populated():
    conn = ingest.connect()
    try:
        # NVDA appears as equity on both dates
        rows = conn.execute(
            "SELECT COUNT(*) FROM positions_timeseries WHERE symbol = 'NVDA'"
        ).fetchone()
        assert rows[0] == 2

        # 2026-06-30 has 2 short puts (NVDA + AMZN)
        rows = conn.execute(
            """
            SELECT COUNT(*) FROM positions_timeseries
            WHERE date = '2026-06-30' AND assetType = 'OPTION' AND opt_type = 'PUT' AND qty < 0
            """
        ).fetchone()
        assert rows[0] == 2
    finally:
        conn.close()


def test_parkev_history_populated():
    conn = ingest.connect()
    try:
        # Both NVDA + AMZN observed on both dates → 4 rows
        rows = conn.execute("SELECT COUNT(*) FROM parkev_history").fetchone()
        assert rows[0] == 4

        # NVDA stayed at tier 4 on both dates
        rows = conn.execute(
            "SELECT date, rating_tier, conviction FROM parkev_history WHERE ticker = 'NVDA' ORDER BY date"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][1] == 4
        assert rows[1][1] == 4
    finally:
        conn.close()


def test_status_after_ingest():
    ingest.connect().close()
    status = ingest.get_last_status()
    assert status is not None
    assert status.snapshot_count == 2
    assert status.expected_count == 2


def test_latest_briefing_date():
    conn = ingest.connect()
    try:
        assert ingest.latest_briefing_date(conn) == "2026-06-30"
    finally:
        conn.close()


def test_load_briefing_json_v1_and_v2():
    v1 = ingest.load_briefing_json("2026-05-10")
    assert v1 is not None
    assert "actions" not in v1   # V1 fixture has no actions

    v2 = ingest.load_briefing_json("2026-06-30")
    assert v2 is not None
    assert len(v2["actions"]) == 2

    assert ingest.load_briefing_json("2099-01-01") is None
