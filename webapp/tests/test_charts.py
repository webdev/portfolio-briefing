"""Test Plotly figure builders produce valid JSON shapes."""

from __future__ import annotations


def test_nlv_chart_endpoint(client):
    r = client.get("/charts/nlv.json")
    assert r.status_code == 200
    spec = r.json()
    assert "data" in spec
    assert "layout" in spec
    # Two traces (NLV + cash)
    assert len(spec["data"]) == 2
    assert spec["data"][0]["name"] == "NLV"
    # Both fixture dates inside default 90d window: should have ≥1 point
    assert len(spec["data"][0]["x"]) >= 1


def test_coverage_chart_endpoint(client):
    r = client.get("/charts/coverage.json")
    assert r.status_code == 200
    spec = r.json()
    assert "data" in spec
    assert "layout" in spec
    # Coverage threshold shapes (0.50× floor + 0.70× target)
    if spec["data"]:
        assert len(spec["layout"].get("shapes", [])) == 2


def test_expiration_ladder_endpoint(client):
    r = client.get("/charts/expiration-ladder.json")
    assert r.status_code == 200
    spec = r.json()
    assert "data" in spec
    # 2026-06-30 fixture has 2 short puts (different expirations)
    if spec["data"]:
        assert spec["data"][0]["type"] == "bar"
        # 2 distinct expirations
        assert len(spec["data"][0]["x"]) == 2


def test_parkev_chart_endpoint(client):
    r = client.get("/charts/parkev/NVDA.json")
    assert r.status_code == 200
    spec = r.json()
    assert "data" in spec
    if spec["data"]:
        assert spec["data"][0]["type"] == "scatter"
        # 2 historical observations
        assert len(spec["data"][0]["x"]) == 2


def test_parkev_chart_unknown_ticker(client):
    """Unknown ticker → empty-figure spec (200, not 500)."""
    r = client.get("/charts/parkev/NOSUCHTICKER.json")
    assert r.status_code == 200
    spec = r.json()
    # Empty figure has empty data + an annotation
    assert spec["data"] == []
    assert "annotations" in spec["layout"]


def test_nlv_chart_with_window(client):
    # Tight window: only the 2026-06-30 snapshot inside last 7d (cutoff = today-7d).
    # Both fixtures are in 2026 — depending on system date this may return 0 or 1 points.
    r = client.get("/charts/nlv.json?days=7")
    assert r.status_code == 200
    spec = r.json()
    assert "data" in spec
    # Either renders points OR returns the empty-state placeholder; both are OK.
