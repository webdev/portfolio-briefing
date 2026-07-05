"""Hardening: ingest a fixture set with intentionally heterogeneous
schemas and verify every route returns 200.

This is the M2 spec's smoke test for schema robustness. We construct a
temporary fixture directory with 5 briefings of mixed shapes — some V1
(no `actions`), some V2 (with `actions`), some missing fields — and
assert no route crashes.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def heterogeneous_fixture(monkeypatch):
    """Build a temp fixture dir with 5 briefings of different shapes."""
    tmp = Path(tempfile.mkdtemp(prefix="webapp-schema-"))
    delivery = tmp / "deliv"
    snaps = tmp / "snaps"
    delivery.mkdir()
    snaps.mkdir()
    duckdb_path = tmp / "test.duckdb"

    # 5 briefings — vary fields
    briefings = [
        # V1 — no `actions`, no `strategy_upgrades`, no consistency_report
        {"date": "2026-01-01", "nlv": 800000, "cash": 80000, "regime": "RISK_OFF",
         "equity_reviews": [{"ticker": "AAPL", "recommendation": "HOLD"}],
         "options_reviews": []},
        # V2 — with actions and red flags as a list (no key)
        {"date": "2026-01-02", "nlv": 820000, "cash": 75000, "regime": "RISK_ON",
         "equity_reviews": [{"ticker": "NVDA", "recommendation": "BUY"}],
         "options_reviews": [],
         "actions": [{"kind": "HEDGE", "ident": "SPY", "summary": "add hedge",
                      "days_flagged": 1}]},
        # V2 — actions with key field
        {"date": "2026-01-03", "nlv": 830000, "cash": 70000, "regime": "RISK_ON",
         "equity_reviews": [], "options_reviews": [],
         "actions": [{"key": "CLOSE:NVDA_PUT_180", "kind": "CLOSE",
                      "ident": "NVDA_PUT_180", "summary": "close it",
                      "days_flagged": 5}]},
        # Schema with extra unknown field
        {"date": "2026-01-04", "nlv": 850000, "cash": 65000, "regime": "RISK_ON",
         "equity_reviews": [], "options_reviews": [], "actions": [],
         "some_future_field": {"foo": "bar"}},
        # Minimum viable briefing
        {"date": "2026-01-05", "nlv": 900000, "cash": 60000, "regime": "RISK_ON",
         "equity_reviews": [], "options_reviews": []},
    ]
    for b in briefings:
        (delivery / f"briefing_{b['date']}.json").write_text(json.dumps(b))
        (snaps / b["date"]).mkdir()
        # positions for each date (some empty)
        (snaps / b["date"] / "positions.json").write_text(json.dumps([
            {"symbol": "NVDA", "assetType": "EQUITY", "qty": 100,
             "price": 200, "marketValue": 20000, "accountDesc": "INDIVIDUAL"}
        ]))
        (snaps / b["date"] / "balance.json").write_text(json.dumps({
            "accountValue": b["nlv"], "cash": b["cash"], "longMarketValue": 0
        }))
        (snaps / b["date"] / "recommendations_list.json").write_text(json.dumps({
            "recommendations": [
                {"ticker": "NVDA", "recommendation": "BUY",
                 "raw_recommendation": "Top 12 Stock", "rating_tier": 4,
                 "conviction": "High", "age_days": 5, "aging": False,
                 "date_updated": b["date"]}
            ]
        }))
        (snaps / b["date"] / "regime.json").write_text(json.dumps({"regime": b["regime"]}))

    monkeypatch.setenv("PORTFOLIO_BRIEFING_DELIVERY_DIR", str(delivery))
    monkeypatch.setenv("PORTFOLIO_BRIEFING_SNAPSHOTS_DIR", str(snaps))
    monkeypatch.setenv("PORTFOLIO_BRIEFING_DUCKDB", str(duckdb_path))

    # Force a fresh app to pick up the env vars
    from app.main import create_app
    from app import ingest
    ingest._PROJECTIONS_READY = False
    app = create_app()
    with TestClient(app) as client:
        yield client


def test_all_routes_200_with_heterogeneous_fixtures(heterogeneous_fixture):
    """Every route should return 200 (or a documented redirect) when
    fed 5 briefings with mixed schemas."""
    client = heterogeneous_fixture

    # Health
    assert client.get("/health").status_code == 200

    # Dashboard
    assert client.get("/").status_code == 200

    # Each briefing page should render
    for d in ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"):
        r = client.get(f"/briefing/{d}")
        assert r.status_code == 200, f"briefing/{d} failed: {r.status_code}"

    # Latest redirect
    r = client.get("/briefing/latest", follow_redirects=False)
    assert r.status_code in (302, 307)

    # Positions
    assert client.get("/positions").status_code == 200
    assert client.get("/positions/NVDA").status_code == 200

    # Parkev
    assert client.get("/parkev/NVDA").status_code == 200

    # History
    assert client.get("/history").status_code == 200

    # Charts
    for url in [
        "/charts/nlv.json", "/charts/coverage.json",
        "/charts/expiration-ladder.json",
        "/charts/parkev/NVDA.json",
        "/charts/sparkline/NVDA.json",
    ]:
        r = client.get(url)
        assert r.status_code == 200, f"{url} failed"

    # Search empty + with query
    assert client.get("/search").status_code == 200
    assert client.get("/search?q=NVDA").status_code == 200

    # Diff pairs
    r = client.get("/diff/2026-01-01/2026-01-05")
    assert r.status_code == 200

    # Diff redirect
    r = client.get("/diff/yesterday/latest", follow_redirects=False)
    assert r.status_code in (302, 307)

    # Fragments
    assert client.get("/fragment/ticker_card/NVDA").status_code == 200
    assert client.get("/fragment/counterpoint/2026-01-01/CLOSE:X").status_code == 200
    assert client.get("/fragment/refresh-status").status_code == 200
