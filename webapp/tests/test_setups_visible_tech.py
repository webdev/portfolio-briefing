"""Regression tests for task #18 — Setups ticker cards use the unified
card design: the technical read renders VISIBLE inline, not collapsed.

User's symptom: "On /setups/{date}, the tech data lives in a collapsed
<sl-details> disclosure at the bottom of each card. You have to click
'📈 Technical read — click to expand' to see the same signals that render
prominently on /briefing/{date}. Inconsistent."

Fixtures: candidates_2026-06-30.md + when_to_enter_2026-06-30.md, with
snapshots/2026-06-30/technicals.json carrying NVDA (full deep read) and
AMAT/SMCI absent from the deep map (skeleton path).
"""

from __future__ import annotations


DATE = "2026-06-30"


def test_setups_renders_tech_card_inline_not_collapsed(client):
    """/setups/{date} returns 200 with the tech card markers directly in
    the document — NOT hidden inside a <sl-details class="tc-details">."""
    r = client.get(f"/setups/{DATE}")
    assert r.status_code == 200
    body = r.text
    # Full tech card markers present inline
    assert "tech-card" in body
    assert "tc-bb-track" in body       # Bollinger visualization
    assert "tc-sr-track" in body       # Support/Resistance visualization
    assert "tc-verdicts" in body       # ST/LT verdicts visible
    # The old collapsed disclosure is gone
    assert "tc-details" not in body
    assert "Technical read — BB" not in body  # old sl-details summary text
    # The new wrapper is present
    assert 'class="rep-tech"' in body


def test_setups_card_without_tech_renders_compact_skeleton(client):
    """AMAT/SMCI have no deep read in the fixture snapshot — their cards
    must render the explicit compact skeleton (hard rule #19), never a
    fabricated read and never a 500."""
    r = client.get(f"/setups/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "tc-skeleton-compact" in body
    assert "Chart data unavailable" in body
    # Skeleton is attributed to a real ticker from the fixture
    assert 'data-ticker="AMAT"' in body or 'data-ticker="SMCI"' in body


def test_setups_actionable_hero_carries_visible_tech(client):
    """The 🎯 Actionable-today hero reuses the same card macro — its
    preview cards must also carry the visible tech data (NVDA is the
    fixture's actionable candidate with a deep read)."""
    r = client.get(f"/setups/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'class="rep-actionable-hero"' in body
    hero_start = body.find('class="rep-actionable-hero"')
    hero_end = body.find('class="rep-section"', hero_start)
    hero_html = body[hero_start:hero_end if hero_end != -1 else None]
    assert "rep-tech" in hero_html
    assert "tech-card" in hero_html or "tc-skeleton-compact" in hero_html


def test_setups_filter_chip_strip_still_present(client):
    """The status filter strip (Actionable / Deferred / Wait / Watch /
    Avoid / All) survives the redesign, and cards keep the
    .rep-card-{tone} classes the filterCards() JS keys off."""
    r = client.get(f"/setups/{DATE}")
    body = r.text
    assert 'class="rep-filter"' in body
    assert 'data-filter="actionable"' in body
    assert 'data-filter="all"' in body
    assert "function filterCards" in body
    # Tone classes on cards — the JS matches .rep-card-{tone}
    assert "rep-card-" in body


def test_candidates_deep_link_shows_no_false_skeleton(client):
    """/candidates/{date} never fetched a tech map — its cards must NOT
    claim 'Chart data unavailable' (that would be a false statement;
    the route simply doesn't ask for tech data). Hard rule #19 cuts both
    ways: no fabricated data AND no fabricated absence-of-data."""
    r = client.get(f"/candidates/{DATE}")
    assert r.status_code == 200
    assert "tc-skeleton-compact" not in r.text
    assert "Chart data unavailable" not in r.text


def test_setups_legend_still_present(client):
    """The task #13 indicator legend stays at the top of the page."""
    r = client.get(f"/setups/{DATE}")
    assert 'class="tc-legend-details"' in r.text
    assert "How to read the technical signals" in r.text
