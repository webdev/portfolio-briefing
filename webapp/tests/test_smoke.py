"""End-to-end smoke tests covering the 7-step user flow from the M2 spec.

These tests exercise the full request lifecycle (without actually running
the briefing pipeline) so we can catch any wiring issues that the
per-route unit tests don't expose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import jobs


@pytest.fixture(autouse=True)
def _isolated_job_state():
    jobs.reset_all()
    jobs.reset_command_builder()
    yield
    jobs.reset_all()
    jobs.reset_command_builder()


def test_step1_dashboard_loads(client):
    """Step 1: user opens http://localhost:17776 — new morning-briefing
    home page (UX Tier 2 redesign) loads with hero + KPI strip + refresh."""
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    # New home carries the hero + KPI strip (not "Morning Briefing" title anymore)
    assert "home-hero" in html
    assert "home-kpi-strip" in html
    assert "Refresh" in html  # M2: refresh button is in the topbar


def test_step2_refresh_button_kicks_off_job(client):
    """Step 2: clicking Refresh fires POST /refresh."""
    def _echo():
        return (["printf", "%s", "stage 1\nstage 2\ndone\n"], Path("/tmp"))
    jobs.set_command_builder(_echo)

    r = client.post("/refresh")
    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "started"

    final = jobs.wait_for_job(body["job_id"], timeout=3)
    assert final is not None
    assert final.status == "success"


def test_step3_diff_yesterday_latest(client):
    """Step 3: /diff/yesterday/latest redirects to the two-most-recent."""
    r = client.get("/diff/yesterday/latest", follow_redirects=True)
    assert r.status_code == 200
    html = r.text
    assert "Diff" in html
    assert "Action queue diff" in html or "Position diff" in html


def test_step4_ticker_hover_fragment(client):
    """Step 4: hover any ticker → /fragment/ticker_card/NVDA returns a card."""
    r = client.get("/fragment/ticker_card/NVDA")
    assert r.status_code == 200
    html = r.text
    assert "NVDA" in html
    assert "🅿️" in html
    assert "sparkline-mini" in html


def test_step5_counterpoint_disclosure(client):
    """Step 5: 'Show counterpoint' fetches /fragment/counterpoint/...

    Our fixture has no counterpoint markdown, so the endpoint returns
    empty 200 — which is the documented empty-state.
    """
    r = client.get("/fragment/counterpoint/2026-06-30/CLOSE:NVDA_PUT_180_20260918")
    assert r.status_code == 200


def test_step6_cross_snapshot_search(client):
    """Step 6: /search?q=NVDA finds every briefing mention."""
    r = client.get("/search?q=NVDA")
    assert r.status_code == 200
    html = r.text
    assert "NVDA" in html
    # Must show results from both fixture dates
    assert "2026-05-10" in html
    assert "2026-06-30" in html


def test_step7_historic_chip_uses_historic_rating(client):
    """Step 7: /briefing/2026-05-10 chips show May 10's ratings (NOT today's).

    On 2026-05-10 the AMZN chip should show "BUY · ◐ Med · 9d" (tier 3,
    Medium, 9-day-old rec), not the 2026-06-30 chip "TOP 12 · 🔥 High · 1d".
    """
    r = client.get("/briefing/2026-05-10")
    assert r.status_code == 200
    html = r.text
    assert "BUY · ◐ Med · 9d" in html
    # Verify the page is actually the 2026-05-10 briefing
    assert "2026-05-10" in html


def test_search_finds_actions_across_snapshots(client):
    """Sanity: search for 'CLOSE' with type=action finds the close action."""
    r = client.get("/search?q=CLOSE&type=action")
    assert r.status_code == 200
    html = r.text
    assert "NVDA_PUT_180_20260918" in html


def test_diff_page_handles_picker_selection(client):
    """The diff page lets user pick any (a, b) date pair via the URL."""
    r = client.get("/diff/2026-05-10/2026-06-30")
    assert r.status_code == 200
    # Both date pickers should be populated with all dates
    assert "2026-05-10" in r.text
    assert "2026-06-30" in r.text


def test_sparkline_renders_for_known_ticker(client):
    r = client.get("/charts/sparkline/NVDA.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["data"]   # not empty
    assert spec["data"][0]["type"] == "scatter"
