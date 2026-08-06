"""Rule #43 UX — the Ideas tab must present gated ideas at LOWER visual
weight than actionable ones.

Observed (2026-08-04): George saw "SELL 1× GOOG $345P" inside
"💎 Ideas & Candidates (19)" and read it as a recommendation — while the
pipeline had correctly demoted it (wait-for-pullback subsection,
⛔ equity-stacking, ⏸ capacity, live RSI 61 extended, playbook
exclusion). The tab merged all idea kinds per hard rule #32 (never
hide), but the badge counted everything and all cards rendered at equal
weight.

The fix: badge counts only actionable ideas (gated count muted
alongside); the tab splits into ✅ Actionable / ⏸ Waiting for setup /
⛔ Gated sections; waiting/gated cards carry `uc-muted` and their trade
line is prefixed "when conditions clear:" / "excluded:".
"""

from __future__ import annotations

import re

from app.ideas import (
    IDEA_GROUP_ACTIONABLE,
    IDEA_GROUP_GATED,
    IDEA_GROUP_WAITING,
    build_merged_ideas,
    group_merged_ideas,
    idea_group,
    ideas_badge_counts,
)
from app.models import load_briefing


def _fake_briefing(*, new_ideas, ltos):
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


_GOOG_REFERENCE_ROW = {
    "kind": "LONG_DATED_CSP",
    "ticker": "GOOG",
    "concrete_trade": "SELL 1× GOOG $345P exp Fri Oct 16 '26 — collateral $34,500",
    "reference_demoted": True,
    "reference_reason": "equity-stacking hard-skip + stale qualifying RSI "
                        "(live RSI 61 extended)",
}


def test_ideas_badge_counts_only_actionable():
    """The observed bug: '💎 Ideas & Candidates (19)' counted GOOG's
    demoted 'SELL 1× GOOG $345P' ticket the same as live recs. The badge
    count must include ONLY actionable ideas; everything else rolls into
    the gated count."""
    briefing = _fake_briefing(
        new_ideas=[{
            "ticker": "CAPACITY",
            "source": "capacity_gates_blocked",
            "rationale": "blocked",
            "capacity_blocked": True,
        }],
        ltos=[
            {"kind": "LONG_DATED_CSP", "ticker": "AMZN",
             "concrete_trade": "SELL AMZN $230P Jan 16 '27"},
            _GOOG_REFERENCE_ROW,
            {"kind": "DEFERRED_ADD_HAS_CSP", "ticker": "META"},
            {"kind": "SKIPPED_RSI", "ticker": "ASML"},
            {"kind": "SKIPPED_LT_CSP", "ticker": "MU"},
        ],
    )
    merged = build_merged_ideas(briefing)
    actionable, gated = ideas_badge_counts(merged)
    assert actionable == 1        # only AMZN passed every gate
    assert gated == 5             # GOOG + META + ASML + MU + CAPACITY
    assert actionable + gated == len(merged)


def test_ideas_tab_three_groups_order():
    """group_merged_ideas splits into actionable / waiting / gated and
    the rendered tab keeps that section order. GOOG's reference-demoted
    $345P card lands in WAITING (a setup card, not a rec today);
    SKIPPED_RSI is a wait-for-band, not a hard gate; capacity + LT-CSP
    discipline skips are GATED."""
    briefing = _fake_briefing(
        new_ideas=[{
            "ticker": "CAPACITY",
            "source": "capacity_gates_blocked",
            "rationale": "blocked",
            "capacity_blocked": True,
        }],
        ltos=[
            {"kind": "LONG_DATED_CSP", "ticker": "AMZN"},
            _GOOG_REFERENCE_ROW,
            {"kind": "DEFERRED_ADD_HAS_CSP", "ticker": "META"},
            {"kind": "SKIPPED_RSI", "ticker": "ASML"},
            {"kind": "SKIPPED_LT_CSP", "ticker": "MU"},
        ],
    )
    merged = build_merged_ideas(briefing)
    groups = group_merged_ideas(merged)
    assert [m["ticker"] for m in groups[IDEA_GROUP_ACTIONABLE]] == ["AMZN"]
    assert {m["ticker"] for m in groups[IDEA_GROUP_WAITING]} == {"GOOG", "META", "ASML"}
    assert {m["ticker"] for m in groups[IDEA_GROUP_GATED]} == {"MU", "CAPACITY"}
    # Every merged idea appears in exactly one group — nothing hidden
    # (hard rules #24/#32), nothing double-counted.
    total = sum(len(v) for v in groups.values())
    assert total == len(merged)


def test_idea_group_unknown_status_fails_toward_gated():
    """Fail toward LESS prominence: an unknown status must never render
    as actionable."""
    assert idea_group("banana") == IDEA_GROUP_GATED
    assert idea_group("") == IDEA_GROUP_GATED


def test_ideas_tab_sections_render_in_order(client):
    """E2E on the fixture date: the three section headers render in
    Actionable → Waiting → Gated order, and the tab badge shows the
    actionable count with a separate '.. gated' companion badge."""
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    html = r.text
    i_act = html.find("✅ Actionable today")
    i_wait = html.find("⏸ Waiting for setup")
    i_gate = html.find("⛔ Gated")
    assert i_act != -1 and i_wait != -1 and i_gate != -1
    assert i_act < i_wait < i_gate
    # The muted companion badge on the tab: "N gated"
    assert re.search(r"\d+ gated</sl-badge>", html)
    # Section headers carry the rule-#24 explanation (whitespace-tolerant:
    # template line wrapping may split the phrase).
    assert len(re.findall(r"why it's\s+blocked", html)) >= 2


def test_gated_cards_muted_class_and_prefix(client):
    """The GOOG $345P case: the fixture carries the demoted
    'SELL 1× GOOG $345P exp Fri Oct 16 '26' ticket (reference_demoted:
    equity-stacking hard-skip + stale qualifying RSI). Its card must
    render with the `uc-muted` class and its trade line prefixed
    'when conditions clear:' — never as a bare ticket at full weight
    (which is exactly how George misread it as a recommendation).
    The hard-gated MU SKIPPED_LT_CSP ticket gets 'excluded:'."""
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    html = r.text
    # Muted cards exist and carry group data attributes.
    assert "uc-muted" in html
    assert 'data-idea-group="waiting"' in html
    assert 'data-idea-group="gated"' in html
    # The GOOG card: muted article + prefixed trade line.
    goog = re.search(r'<article class="uc-card uc-idea uc-muted"[^>]*data-ticker="GOOG"', html)
    assert goog, "GOOG reference-demoted card must carry uc-muted"
    assert "when conditions clear:" in html
    assert "SELL 1× GOOG $345P" in html
    # The gated MU ticket is labeled excluded, not rendered bare.
    assert "excluded:" in html
    # Actionable cards are NOT muted: AMZN's article tag has no uc-muted.
    amzn = re.search(r'<article class="uc-card uc-idea"[^>]*data-ticker="AMZN"', html)
    assert amzn, "actionable AMZN card must NOT carry uc-muted"


def test_zero_actionable_tab_still_renders():
    """Hard rule #32: a zero-actionable day still surfaces every gated
    idea — badge reads '0 · N gated', groups carry everything."""
    briefing = _fake_briefing(
        new_ideas=[{
            "ticker": "CAPACITY",
            "source": "capacity_gates_blocked",
            "rationale": "blocked",
            "capacity_blocked": True,
        }],
        ltos=[
            _GOOG_REFERENCE_ROW,
            {"kind": "SKIPPED_LT_CSP", "ticker": "MU"},
        ],
    )
    merged = build_merged_ideas(briefing)
    actionable, gated = ideas_badge_counts(merged)
    assert actionable == 0
    assert gated == 3
    groups = group_merged_ideas(merged)
    assert groups[IDEA_GROUP_ACTIONABLE] == []
    # Nothing dropped.
    assert sum(len(v) for v in groups.values()) == 3
