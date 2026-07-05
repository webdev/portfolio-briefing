"""Smoke-test every route: 200 + expected key content."""

from __future__ import annotations


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["snapshots_loaded"] >= 1


def test_dashboard(client):
    """Post UX-Tier2 redesign: `/` now serves the morning-briefing home
    (home.html), not the old dense dashboard.html. The dense view moved
    to `/dashboard`. Both must render 200 with today's date."""
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # New home carries the hero + KPI strip
    assert "home-hero" in html
    assert "home-kpi-strip" in html
    assert "2026-06-30" in html   # latest fixture date


def test_legacy_dashboard_still_works(client):
    """Users who prefer the old dense view can access it at /dashboard."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "Morning Briefing" in r.text
    assert "NVDA" in r.text


def test_briefing_latest_redirects(client):
    r = client.get("/briefing/latest", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/briefing/2026-06-30" in r.headers["location"]


def test_briefing_detail_v1(client):
    """V1 briefing renders without crashing — actions panel skipped silently."""
    r = client.get("/briefing/2026-05-10")
    assert r.status_code == 200
    html = r.text
    assert "2026-05-10" in html
    assert "Equity reviews" in html
    # V1 has no actions block — make sure we don't render one
    assert "Action queue" not in html


def test_briefing_detail_v2(client):
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    html = r.text
    assert "Action queue" in html
    assert "CLOSE" in html
    assert "HEDGE" in html


def test_briefing_detail_404(client):
    r = client.get("/briefing/2099-01-01")
    assert r.status_code == 404


def test_positions_page(client):
    r = client.get("/positions")
    assert r.status_code == 200
    html = r.text
    assert "NVDA" in html
    assert "AMZN" in html
    # Both equities and options sections render
    assert "Equities" in html
    assert "Options" in html


def test_position_detail(client):
    r = client.get("/positions/NVDA")
    assert r.status_code == 200
    html = r.text
    assert "NVDA" in html
    # The detail page should show history rows
    assert "Position history" in html
    # Should pick up the equity card for the latest snapshot
    assert "Current position" in html


def test_position_detail_case_insensitive(client):
    r = client.get("/positions/nvda")
    assert r.status_code == 200
    assert "NVDA" in r.text


def test_parkev_page(client):
    r = client.get("/parkev/NVDA")
    assert r.status_code == 200
    html = r.text
    assert "Parkev rating timeline" in html
    assert "NVDA" in html
    # 2 historical observations → table should render them
    assert "Full history" in html


def test_history_page(client):
    r = client.get("/history")
    assert r.status_code == 200
    html = r.text
    assert "NLV" in html
    assert "Stress coverage" in html
    assert "Expiration ladder" in html


def test_refresh_status_fragment(client):
    r = client.get("/fragment/refresh-status")
    assert r.status_code == 200
    # Should contain "ago" or "never"
    assert "ago" in r.text or "never" in r.text
