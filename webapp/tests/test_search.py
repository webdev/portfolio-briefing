"""Cross-snapshot search route tests."""

from __future__ import annotations


def test_search_empty_query_renders_page(client):
    r = client.get("/search")
    assert r.status_code == 200
    assert "Search" in r.text
    # Empty-state should show examples
    assert "NVDA" in r.text


def test_search_by_ticker(client):
    r = client.get("/search?q=NVDA")
    assert r.status_code == 200
    html = r.text
    # NVDA appears in equity_reviews + options_reviews + actions on multiple dates
    assert "NVDA" in html
    assert "equity_review" in html


def test_search_by_action_kind_with_filter(client):
    r = client.get("/search?q=CLOSE&type=action")
    assert r.status_code == 200
    html = r.text
    # 2026-06-30 fixture has a CLOSE action
    assert "CLOSE" in html
    assert "NVDA_PUT_180_20260918" in html


def test_search_by_date_prefix(client):
    r = client.get("/search?q=2026-05")
    assert r.status_code == 200
    html = r.text
    # Should find the 2026-05-10 fixture's mentions
    assert "2026-05-10" in html


def test_search_no_results(client):
    r = client.get("/search?q=NONEXISTENT_TOKEN_QWERTY")
    assert r.status_code == 200
    assert "No mentions found" in r.text
