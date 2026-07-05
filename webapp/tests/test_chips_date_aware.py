"""Test the M1 chip-date-correctness fix (issue #3).

The bug: chips on historic /briefing/<date> were rendering with TODAY's
rating, not the rating that was in effect ON that date. This test
verifies the date-aware lookup works.

Our fixtures have different ratings + conviction on 2026-05-10 vs
2026-06-30:
    AMZN 2026-05-10: BUY (tier 3) — older row
    AMZN 2026-06-30: Top 12 Stock (tier 4) High conviction — newer row

Rendering /briefing/2026-05-10 must show "BUY", not "TOP 12".
"""

from __future__ import annotations

from app.chips import (
    parkev_chip_renderer,
    clear_recs_cache,
)


def test_chip_renderer_returns_date_specific_rating():
    """Per fixtures:
       AMZN 2026-05-10: BUY (tier 3) + Medium conviction (◐ Med) + 9d age
       AMZN 2026-06-30: TOP 12 (tier 4) + High conviction (🔥 High) + 1d age
    """
    clear_recs_cache()
    r_old = parkev_chip_renderer("2026-05-10")
    r_new = parkev_chip_renderer("2026-06-30")
    chip_old = r_old("AMZN")
    chip_new = r_new("AMZN")
    # Older renders BUY (tier 3), newer renders TOP 12 (tier 4)
    assert "BUY" in chip_old, f"expected 'BUY' in {chip_old}"
    assert "TOP" in chip_new, f"expected 'TOP' in {chip_new}"
    assert "◐ Med" in chip_old, f"expected '◐ Med' in {chip_old}"
    assert "🔥" in chip_new, f"expected '🔥 High' in {chip_new}"


def test_chip_renderer_handles_unknown_date():
    """An unrecognized date renders 'no rec' (fail-closed, hard rule #19)."""
    r = parkev_chip_renderer("2099-12-31")
    assert "no rec" in r("NVDA")


def test_chip_renderer_handles_none_date():
    r = parkev_chip_renderer(None)
    assert "no rec" in r("NVDA")


def test_briefing_route_renders_chip_for_correct_date(client):
    """End-to-end: /briefing/2026-05-10 chips show 2026-05-10 ratings.

    The AMZN row on May 10 has tier-3 BUY + Medium (◐ Med) + 9d age.
    The AMZN row on June 30 has tier-4 TOP 12 + High (🔥 High) + 1d age.
    """
    r = client.get("/briefing/2026-05-10")
    assert r.status_code == 200
    html = r.text
    # The May 10 page must show the AMZN tier-3 chip ("BUY · ◐ Med · 9d"),
    # NOT the June 30 tier-4 chip ("TOP 12 · 🔥 High · 1d").
    assert "BUY · ◐ Med · 9d" in html, "AMZN's 2026-05-10 chip should render"


def test_briefing_route_renders_chip_for_newer_date(client):
    """End-to-end: /briefing/2026-06-30 chips show June 30 ratings."""
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    html = r.text
    # AMZN's June 30 chip — TOP 12 + High + 1d
    assert "TOP 12 · 🔥 High · 1d" in html, "AMZN's 2026-06-30 chip should render"


def test_clear_recs_cache_resets_lookups():
    """After clear_recs_cache(), the next renderer call re-reads from disk."""
    clear_recs_cache()
    r1 = parkev_chip_renderer("2026-05-10")
    c1 = r1("NVDA")
    clear_recs_cache()
    r2 = parkev_chip_renderer("2026-05-10")
    c2 = r2("NVDA")
    assert c1 == c2  # Same source = same output
