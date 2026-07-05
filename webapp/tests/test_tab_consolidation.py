"""Tests for the tab consolidation on /briefing/{date} (task #17).

User decision (option B): kill the Long-term tab (its data already merges
into Ideas per task #32 / hard rule #32) and redistribute the Strategy
tab by concept — covered-call / collar proposals attach to the Options
tab, sub-lot completions move to Ideas. Result: 8 tabs instead of 10.

Constraint (user, eighth reminder): "STABILITY AND NO BUGS" — this is a
UX refactor only. Nothing may be dropped: every strategy upgrade must
still be visible somewhere (hard rule #24 — never hide, always surface).

These tests are regressions against re-introducing the retired tabs AND
against silently dropping redistributed content.
"""

from __future__ import annotations

import re

import pytest

from app import unified_card
from app.ideas import SUB_LOT_KIND, build_merged_ideas
from app.models import load_briefing


DATE = "2026-06-30"


# ─── Fixture augmentation: CC / collar / sub-lot strategy upgrades ─────
# The bundled 2026-06-30 fixture only carries a tier_a_no_cc upgrade, so
# tests that need the redistributed types inject them via the same
# monkeypatch pattern test_unified_card uses for rotations.

_EXTRA_UPGRADES = [
    {
        "type": "write_covered_call",
        "underlying": "NVDA",  # NVDA HAS an open option review → attaches
        "tier": "C",
        "shares_held": 500,
        "current_price": 200.0,
        "current_weight_pct": 10.0,
        "rationale": "IV rank 72, RSI 68 — write the $230C Aug 21 '26.",
    },
    {
        "type": "collar",
        "underlying": "NVDA",
        "tier": "C",
        "shares_held": 500,
        "current_price": 200.0,
        "current_weight_pct": 10.0,
        "rationale": "Protect the lot: long $180P / short $230C.",
    },
    {
        "type": "write_covered_call",
        "underlying": "AMZN",  # AMZN has NO option review → standalone
        "tier": "C",
        "shares_held": 100,
        "current_price": 240.0,
        "current_weight_pct": 2.4,
        "rationale": "Write the $260C Sep 18 '26.",
    },
    {
        "type": "sublot_completion",
        "underlying": "MU",
        "shares_held": 60,
        "shares_to_buy": 40,
        "current_price": 150.0,
        "cost": 6000.0,
        "current_weight_pct": 0.9,
        "discipline_deferred": False,
        "rsi_blocked": False,
        "rationale": "Complete 100-share lot @ $150.00 -> enable covered calls",
    },
]


@pytest.fixture
def client_with_upgrades(client, monkeypatch):
    """Client whose 2026-06-30 briefing carries CC/collar/sub-lot upgrades."""
    from app import ingest

    real_load = ingest.load_briefing_json

    def _augmented(date):
        raw = real_load(date)
        if raw and date == DATE:
            raw = dict(raw)
            raw["strategy_upgrades"] = list(raw.get("strategy_upgrades") or []) + [
                dict(u) for u in _EXTRA_UPGRADES
            ]
        return raw

    monkeypatch.setattr(ingest, "load_briefing_json", _augmented)
    return client


# ─── Retired tabs are GONE (regression against re-introduction) ────────


def test_long_term_tab_absent(client):
    """Task #17: the Long-term tab must NOT render — its data already
    surfaces on the Ideas tab via merged_ideas (task #32)."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'panel="long-term"' not in body
    assert '<sl-tab-panel name="long-term"' not in body


def test_strategy_tab_absent(client):
    """Task #17: the Strategy tab must NOT render — CC/collar proposals
    live on the Options tab, sub-lots on Ideas."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'panel="strategy"' not in body
    assert '<sl-tab-panel name="strategy"' not in body


def test_long_term_data_still_reaches_ideas_tab(client):
    """Killing the tab drops nothing: the fixture's LONG_DATED_CSP (AMZN)
    still renders — as an idea card."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert re.search(r'class="uc-card uc-idea[" ]', body)
    assert "AMZN" in body
    # The old long-term card grid is gone
    assert "uc-grid-longterm" not in body


# ─── Strategy redistribution: Options tab ──────────────────────────────


def test_covered_call_proposal_renders_inside_options_card(client_with_upgrades):
    """A write_covered_call upgrade on NVDA (which has an open option
    review) renders as an inline WRITE CC affordance inside the Options
    card — not on a separate tab."""
    r = client_with_upgrades.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "uc-strategy-affordances" in body
    assert "uc-affordance-write_covered_call" in body
    assert "WRITE CC" in body
    # The affordance rationale came through
    assert "$230C Aug 21" in body


def test_collar_proposal_renders_inside_options_card(client_with_upgrades):
    """A collar upgrade attaches to the same underlying's option card
    with a COLLAR affordance."""
    r = client_with_upgrades.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "uc-affordance-collar" in body
    assert "COLLAR" in body


def test_unattached_strategy_upgrades_render_as_standalone_cards(client_with_upgrades):
    """A CC proposal on AMZN (no open option position) and the fixture's
    tier_a_no_cc policy note still render — as strategy cards in the
    Options tab's 'Strategy proposals' subsection (hard rule #24: never
    hide)."""
    r = client_with_upgrades.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "Strategy proposals" in body
    assert "uc-grid-strategy" in body
    assert re.search(r'class="uc-card uc-strategy[" ]', body)
    assert "$260C Sep 18" in body           # AMZN standalone CC
    assert "Tier A" in body                  # tier_a_no_cc humanized


def test_tier_a_note_still_visible_without_augmentation(client):
    """The un-augmented fixture's tier_a_no_cc upgrade must not vanish
    with the Strategy tab — it renders in the Options panel."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "Strategy proposals" in body
    assert re.search(r'class="uc-card uc-strategy[" ]', body)


# ─── Strategy redistribution: sub-lots → Ideas ─────────────────────────


def test_sublot_completion_appears_in_ideas_tab(client_with_upgrades):
    """Sub-lot completions ARE new-open recommendations — they surface on
    the Ideas tab with kind SUB_LOT."""
    r = client_with_upgrades.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'data-idea-kind="SUB_LOT"' in body
    assert "MU" in body
    # Ticket composed from the real pipeline fields
    assert "BUY 40" in body


def test_build_merged_ideas_includes_sublots():
    """Pure merger contract: sublot_completion upgrades become SUB_LOT
    ideas; other upgrade types (tier_a_no_cc, write_covered_call) do NOT
    leak into Ideas."""
    briefing = load_briefing(
        {
            "date": DATE,
            "new_ideas": [],
            "long_term_opportunities": [],
            "strategy_upgrades": [
                _EXTRA_UPGRADES[3],           # MU sub-lot
                _EXTRA_UPGRADES[0],           # NVDA write_covered_call
                {"type": "tier_a_no_cc", "underlying": "NVDA"},
            ],
        }
    )
    merged = build_merged_ideas(briefing)
    kinds = {(m["ticker"], m["kind"]) for m in merged}
    assert ("MU", SUB_LOT_KIND) in kinds
    assert len(merged) == 1  # CC + tier note stay off the Ideas surface
    row = merged[0]
    assert row["status"] == "actionable"
    assert row["source"] == "strategy_upgrade"
    assert "BUY 40 × MU @ $150.00" in row["concrete_trade"]
    assert "(~$6,000)" in row["concrete_trade"]


def test_build_merged_ideas_sublot_gates_respected():
    """Pipeline gate flags map to status — deferred/skipped, never
    re-derived, never hidden."""
    briefing = load_briefing(
        {
            "date": DATE,
            "new_ideas": [],
            "long_term_opportunities": [],
            "strategy_upgrades": [
                {
                    "type": "sublot_completion",
                    "underlying": "MU",
                    "discipline_deferred": True,
                    "discipline_reason": "you already hold a short put on MU at $890",
                    "rationale": "Complete 100-share lot",
                },
                {
                    "type": "sublot_completion",
                    "underlying": "SOFI",
                    "rsi_blocked": True,
                    "rationale": "Complete 100-share lot",
                },
            ],
        }
    )
    merged = build_merged_ideas(briefing)
    by_ticker = {m["ticker"]: m for m in merged}
    assert by_ticker["MU"]["status"] == "deferred"
    assert "short put on MU" in by_ticker["MU"]["rationale"]
    assert by_ticker["SOFI"]["status"] == "skipped"


# ─── Ideas filter chips ────────────────────────────────────────────────


def test_ideas_filter_chips_render(client):
    """The Ideas panel carries the six filter chips."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "uc-idea-filter" in body
    for label in ["All", "Short-dated", "Long-dated", "Sub-lot", "Deferred", "Skipped"]:
        assert label in body, f"missing filter chip label {label!r}"


def test_ideas_cards_carry_filter_data_attributes(client):
    """Every idea card carries data-idea-kind / data-idea-dte (plus
    status + bucket) so the chips can filter client-side."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "data-idea-kind=" in body
    assert "data-idea-dte=" in body
    assert "data-idea-status=" in body
    assert "data-idea-bucket=" in body
    # The fixture's LONG_DATED_CSP gets the semantic long bucket
    assert 'data-idea-bucket="long"' in body


def test_idea_filter_script_included(client):
    """The client-side filter function is wired into the page."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "function ucIdeaFilter" in body
    assert "ucIdeaFilter('sublot'" in body


# ─── split_strategy_upgrades (pure helper) ─────────────────────────────


def test_split_attaches_cc_and_collar_to_open_option_underlyings():
    briefing = load_briefing(
        {
            "date": DATE,
            "options_reviews": [{"underlying": "NVDA", "contract": "NVDA_PUT_180_20260918"}],
            "strategy_upgrades": _EXTRA_UPGRADES,
        }
    )
    attached, standalone = unified_card.split_strategy_upgrades(briefing)
    assert set(attached.keys()) == {"NVDA"}
    assert len(attached["NVDA"]) == 2  # CC + collar
    # AMZN CC has no open option → standalone; sub-lot excluded entirely
    standalone_types = [(u.underlying, u.type) for u in standalone]
    assert ("AMZN", "write_covered_call") in standalone_types
    assert all(t != "sublot_completion" for _, t in standalone_types)


def test_split_never_drops_unknown_types():
    """Unknown future upgrade types land in standalone (hard rule #24)."""
    briefing = load_briefing(
        {
            "date": DATE,
            "options_reviews": [],
            "strategy_upgrades": [
                {"type": "some_future_type", "underlying": "XYZ"},
                {"type": None, "underlying": None},
            ],
        }
    )
    attached, standalone = unified_card.split_strategy_upgrades(briefing)
    assert attached == {}
    assert len(standalone) == 2


def test_strategy_affordance_label():
    assert unified_card.strategy_affordance_label("write_covered_call") == "WRITE CC"
    assert unified_card.strategy_affordance_label("collar") == "COLLAR"
    assert unified_card.strategy_affordance_label("tier_a_no_cc") == ""
    assert unified_card.strategy_affordance_label(None) == ""


# ─── Tab count sanity ──────────────────────────────────────────────────


def test_tab_count_is_reduced(client):
    """8-tab layout: Actions · Equities · Options · Ideas · Technical ·
    Rotations · Benchmark · Fable (rotations/fable only when data exists).
    On the fixture: no rotations, no fable → 6 nav tabs, and definitely
    no long-term / strategy entries."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    panels = re.findall(r'<sl-tab slot="nav" panel="([^"]+)"', r.text)
    assert "long-term" not in panels
    assert "strategy" not in panels
    assert panels == ["actions", "equities", "options", "ideas", "technical", "benchmark"]
