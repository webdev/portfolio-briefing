"""Tests for ideas.build_merged_ideas — CLAUDE.md hard rule #32.

When `briefing.new_ideas` is empty or just contains a capacity placeholder,
the merged list MUST include actionable kinds from
`long_term_opportunities` (LONG_DATED_CSP, BUY, ADD, LT_ADD, PULLBACK_CSP)
so the user can see real ideas even when the broker capacity is gated.

Pin the contract here so future template changes don't quietly drop ideas.
"""

from __future__ import annotations

from datetime import date as _date

from app.ideas import (
    DEFERRED_OPPORTUNITY_KINDS,
    IDEA_OPPORTUNITY_KINDS,
    build_merged_ideas,
    merged_ideas_count,
)
from app.models import load_briefing


def _fake_briefing(*, new_ideas, ltos):
    """Build a minimal Briefing-shaped object from raw dicts."""
    raw = {
        "date": "2026-06-30",
        "regime": "ATTACK",
        "nlv": 1_000_000,
        "cash": 50_000,
        "new_ideas": new_ideas,
        "long_term_opportunities": ltos,
        "equity_reviews": [],
        "options_reviews": [],
        "actions": [],
    }
    return load_briefing(raw)


def test_only_capacity_placeholder_in_new_ideas_still_surfaces_ltos():
    """The bug we're fixing: new_ideas = 1 capacity placeholder, but
    long_term_opportunities has actionable ideas. The merge must
    surface them, never hide opportunities (hard rule #32)."""
    briefing = _fake_briefing(
        new_ideas=[{
            "ticker": "CAPACITY",
            "source": "capacity_gates_blocked",
            "rationale": "New CSP entries blocked: coverage 0.07x < 0.50x",
            "capacity_blocked": True,
        }],
        ltos=[
            {"kind": "LONG_DATED_CSP", "ticker": "AMZN", "concrete_trade": "SELL AMZN $230P Jan 16 '27"},
            {"kind": "BUY", "ticker": "META", "concrete_trade": "BUY ~$5K META"},
            {"kind": "EXIT", "ticker": "TSLA", "concrete_trade": "SELL TSLA — exit"},  # excluded
        ],
    )
    merged = build_merged_ideas(briefing)
    tickers = {m["ticker"] for m in merged}
    # AMZN + META from LTOs must appear
    assert "AMZN" in tickers
    assert "META" in tickers
    # CAPACITY placeholder is still shown (as blocked)
    assert "CAPACITY" in tickers
    # EXIT/TRIM is not an "idea" — must NOT appear
    assert "TSLA" not in tickers


def test_actionable_ideas_appear_first():
    """Ordering contract: actionable → deferred → blocked/skipped."""
    briefing = _fake_briefing(
        new_ideas=[{
            "ticker": "CAPACITY",
            "source": "capacity_gates_blocked",
            "rationale": "blocked",
            "capacity_blocked": True,
        }],
        ltos=[
            {"kind": "LONG_DATED_CSP", "ticker": "AMZN", "concrete_trade": "SELL"},
            {"kind": "DEFERRED_ADD_HAS_CSP", "ticker": "MU", "concrete_trade": "BUY"},
            {"kind": "SKIPPED_RSI", "ticker": "ASML", "concrete_trade": "BUY"},
        ],
    )
    merged = build_merged_ideas(briefing)
    statuses = [m["status"] for m in merged]
    # The first row must be actionable
    assert statuses[0] == "actionable"
    # capacity_blocked and skipped must come AFTER actionable
    actionable_idx = [i for i, s in enumerate(statuses) if s == "actionable"]
    blocked_idx = [i for i, s in enumerate(statuses) if s == "capacity_blocked"]
    if actionable_idx and blocked_idx:
        assert max(actionable_idx) < min(blocked_idx)


def test_status_chips_classified_correctly():
    """Each merged row carries one of {actionable, capacity_blocked,
    deferred, skipped} with a matching label + css class hint."""
    briefing = _fake_briefing(
        new_ideas=[{
            "ticker": "CAPACITY",
            "source": "capacity_gates_blocked",
            "rationale": "blocked",
            "capacity_blocked": True,
        }],
        ltos=[
            {"kind": "LONG_DATED_CSP", "ticker": "AMZN"},
            {"kind": "DEFERRED_ADD_HAS_CSP", "ticker": "MU"},
            {"kind": "SKIPPED_RSI", "ticker": "ASML"},
        ],
    )
    merged = build_merged_ideas(briefing)
    by_ticker = {m["ticker"]: m for m in merged}
    assert by_ticker["CAPACITY"]["status"] == "capacity_blocked"
    assert by_ticker["CAPACITY"]["status_class"] == "warn"
    assert by_ticker["AMZN"]["status"] == "actionable"
    assert by_ticker["AMZN"]["status_class"] == "ok"
    assert by_ticker["MU"]["status"] == "deferred"
    assert by_ticker["MU"]["status_class"] == "warn"
    assert by_ticker["ASML"]["status"] == "skipped"
    assert by_ticker["ASML"]["status_class"] == "muted"


def test_empty_briefing_returns_empty_list():
    briefing = _fake_briefing(new_ideas=[], ltos=[])
    assert build_merged_ideas(briefing) == []
    assert merged_ideas_count(briefing) == 0


def test_exit_and_trim_excluded():
    """EXIT, TRIM, _FUNDING_HINT are NOT ideas — they're position
    management or capital plan notes. Confirm they're filtered out."""
    briefing = _fake_briefing(
        new_ideas=[],
        ltos=[
            {"kind": "EXIT", "ticker": "TSLA"},
            {"kind": "TRIM", "ticker": "PLTR"},
            {"kind": "_FUNDING_HINT", "ticker": "CASH"},
            {"kind": "LONG_DATED_CSP", "ticker": "AMZN"},
        ],
    )
    merged = build_merged_ideas(briefing)
    tickers = {m["ticker"] for m in merged}
    assert tickers == {"AMZN"}


def test_ideas_panel_shows_lto_when_only_placeholder_in_new_ideas(client):
    """End-to-end: render the briefing page on the fixture date (where
    new_ideas has only a placeholder). The HTML MUST contain at least
    one ticker from long_term_opportunities."""
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    html = r.text
    # The merged tab badge label
    assert "Ideas" in html
    # The fixture's LTO list — at least one ticker must appear.
    # Pull tickers actually present in the fixture's LTO list and assert
    # at least one ends up in the rendered HTML.
    from app import ingest
    raw = ingest.load_briefing_json("2026-06-30")
    assert raw is not None
    lto_tickers = {
        (o.get("ticker") or "").upper()
        for o in raw.get("long_term_opportunities", [])
        if (o.get("kind") or "").upper() in IDEA_OPPORTUNITY_KINDS
        or (o.get("kind") or "").upper() in DEFERRED_OPPORTUNITY_KINDS
    }
    lto_tickers.discard("")
    # At least one of those tickers must be in the rendered HTML
    found = [t for t in lto_tickers if t in html]
    assert found, (
        f"Hard rule #32 violation — no LTO tickers from {sorted(lto_tickers)} "
        f"appear in the Ideas tab HTML."
    )


def test_humanize_action_no_raw_machine_identifiers(client):
    """Hard rule #31 — raw snake_case / SCREAMING_SNAKE identifiers must
    NOT appear as bare text in the rendered briefing.

    The Ideas tab renders `n.kind | humanize_action` so kinds like
    `DEFERRED_ADD_HAS_CSP` come out as "Deferred (held put)" — not the
    raw identifier."""
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    html = r.text
    # If any of these raw strings appear, the humanize_action filter
    # wasn't applied somewhere. They're allowed inside title="..." and
    # CSS class attributes, so we strip those before searching.
    import re
    # Drop all attribute values (anything in single or double quotes)
    body_text = re.sub(r'"[^"]*"', '""', html)
    body_text = re.sub(r"'[^']*'", "''", body_text)
    # tier_a_no_cc is the bug-bait identifier from the user report
    assert "tier_a_no_cc" not in body_text
    # DEFERRED_ADD_HAS_CSP must be humanized in user-visible text
    assert "DEFERRED_ADD_HAS_CSP" not in body_text
    assert "SKIPPED_ADD" not in body_text
