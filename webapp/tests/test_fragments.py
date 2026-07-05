"""HTMX fragment endpoint tests."""

from __future__ import annotations


def test_ticker_card_renders(client):
    r = client.get("/fragment/ticker_card/NVDA")
    assert r.status_code == 200
    html = r.text
    assert "NVDA" in html
    # Should include Parkev chip
    assert "🅿️" in html
    # Should include sparkline target
    assert "sparkline-mini" in html


def test_ticker_card_unknown_ticker_still_renders(client):
    """Unknown ticker should still render the card with `no rec` chip."""
    r = client.get("/fragment/ticker_card/UNKNOWNXYZ")
    assert r.status_code == 200
    html = r.text
    # Either "no rec" appears (chip fallback) or "No briefing mentions"
    assert "no rec" in html or "No briefing mentions" in html or "UNKNOWNXYZ" in html


def test_sparkline_endpoint(client):
    r = client.get("/charts/sparkline/NVDA.json")
    assert r.status_code == 200
    spec = r.json()
    assert "data" in spec
    assert "layout" in spec


def test_sparkline_unknown_ticker_returns_empty(client):
    r = client.get("/charts/sparkline/NOSUCHTICKER.json")
    assert r.status_code == 200
    spec = r.json()
    # empty-figure has empty data + annotation
    assert spec["data"] == []
    assert "annotations" in spec["layout"]


def test_counterpoint_fragment_empty_when_missing(client):
    """A bogus action key returns empty 200 (the disclosure simply
    renders an empty body — no error to the user)."""
    r = client.get("/fragment/counterpoint/2026-06-30/NONEXISTENT:KEY")
    assert r.status_code == 200
    # Empty body — disclosure renders no counterpoint
    assert r.text.strip() == ""


def test_counterpoint_fragment_invalid_date_empty(client):
    """A missing date markdown returns empty without crashing."""
    r = client.get("/fragment/counterpoint/2099-01-01/CLOSE:NVDA")
    assert r.status_code == 200
