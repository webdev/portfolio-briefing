"""Tests for the unified ticker card on /briefing/{date} (task #10).

User request: "EVERY tab on the briefing page should use the same
infographic-style ticker card as the Technical Read tab, with
tab-specific info overlaid on top of the base tech card" — and the
constraint: "stability + no bugs; preserve every existing UX affordance
(sortable views, HTMX counterpoint disclosures, tab badges); every card
path must fail-open on missing tech data."

Fixtures:
  - 2026-06-30 — V2 briefing with actions / equities / options /
    long-term / strategy / merged ideas, AND technicals.json where NVDA
    has a full deep read and AMZN has deep: null (the skeleton path).
  - 2026-05-10 — V1 briefing with NO technicals.json at all: every card
    must render the compact "chart data unavailable" skeleton, never 500.
"""

from __future__ import annotations

import re

import pytest

from app import unified_card
from app.models import load_briefing


DATE = "2026-06-30"
V1_DATE = "2026-05-10"


# ─── Route smoke: tech data available ─────────────────────────────────


def test_briefing_route_200_with_tech_data(client):
    """/briefing/2026-06-30 renders 200 with unified cards wrapping the
    infographic tech card on the data-backed tickers (NVDA has deep)."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "uc-card" in body
    # NVDA's deep read renders the full tech card INSIDE a unified card
    assert "uc-tech" in body
    assert "tc-bb-track" in body


def test_briefing_route_200_without_tech_data(client):
    """/briefing/2026-05-10 has NO technicals.json — every unified card
    must fall back to the compact skeleton, and the page must be 200
    (fail-open, hard rule #19 / #10)."""
    r = client.get(f"/briefing/{V1_DATE}")
    assert r.status_code == 200
    body = r.text
    assert "uc-card" in body
    assert "tc-skeleton-compact" in body
    assert "Chart data unavailable" in body
    # The V1 fixture still shows its section headings
    assert "Equity reviews" in body


# ─── Every tab renders unified cards when its data exists ─────────────


@pytest.mark.parametrize(
    "marker",
    ["uc-action", "uc-equity", "uc-option", "uc-strategy", "uc-idea"],
)
def test_each_tab_renders_unified_cards(client, marker):
    """The 2026-06-30 fixture has data in actions, equities, options,
    strategy upgrades (now rendered inside the Options tab, task #17) and
    ideas — each surface must render at least one unified card of its
    kind. uc-longterm is gone: the Long-term tab was retired (task #17)
    and its data surfaces as uc-idea cards via merged_ideas."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    assert f'uc-card {marker}"' in r.text or f"uc-card {marker} " in r.text or marker in r.text
    # Strict form: class attribute contains the tab-kind marker
    assert re.search(rf'class="uc-card {marker}[" ]', r.text), (
        f"expected at least one unified card with tab-kind {marker!r}"
    )


def test_missing_tech_on_one_ticker_renders_skeleton_not_500(client):
    """AMZN has deep: null in the 2026-06-30 technicals — its equity
    card must carry the compact skeleton (explicit unavailable), while
    the page as a whole still renders NVDA's full tech card."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'data-ticker="AMZN"' in body
    assert "tc-skeleton-compact" in body
    assert "Chart data unavailable" in body   # skeleton text
    assert "tc-bb-track" in body              # NVDA's real card still there


# ─── Preserved affordances ────────────────────────────────────────────


def test_actions_tab_counterpoint_disclosure_still_present(client, monkeypatch):
    """The HTMX counterpoint pattern (hard rule #14) must survive the
    card refactor: <sl-details class="counterpoint-disclosure"> with the
    lazy hx-get to /fragment/counterpoint/... .

    The bundled fixture markdown carries no ⚖️ counterpoint bullets, so
    we stub the parser to flag the fixture's CLOSE action — exactly what
    a real briefing does — and assert the disclosure renders."""
    from app import counterpoints

    key = "CLOSE:NVDA_PUT_180_20260918"
    monkeypatch.setattr(
        counterpoints, "counterpoints_for_date", lambda d: {key: "counter-case text"}
    )
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "counterpoint-disclosure" in body
    assert f"/fragment/counterpoint/{DATE}/{key}" in body
    # Fixed trigger: sl-after-show + intersect fallback (was 'sl-show once' —
    # had a race with Shoelace autoloader, user reported stuck 'Loading...').
    assert "sl-after-show" in body
    assert "intersect once" in body


def test_no_counterpoint_disclosure_when_none_exists(client):
    """Fixture has no counterpoint markdown — no empty disclosures."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    assert "counterpoint-disclosure" not in r.text


def test_tab_badges_still_show_correct_counts(client):
    """Tab pills keep their badge counts: 2 actions, 2 equities,
    1 option on the 2026-06-30 fixture."""
    r = client.get(f"/briefing/{DATE}")
    body = r.text

    def _badge_after(tab_label: str) -> int:
        m = re.search(
            re.escape(tab_label) + r".*?<sl-badge[^>]*>(\d+)</sl-badge>",
            body,
            re.DOTALL,
        )
        assert m, f"no badge found after tab {tab_label!r}"
        return int(m.group(1))

    assert _badge_after('panel="actions">Actions') == 2
    assert _badge_after('panel="equities">Equities') == 2
    assert _badge_after('panel="options">Options') == 1


def test_sort_strip_rendered_for_card_grids(client):
    """The sortable-table affordance is preserved as a sort chip strip
    (P&L / Weight / Ticker) above each card grid."""
    r = client.get(f"/briefing/{DATE}")
    body = r.text
    assert "uc-sort" in body
    assert 'ucSort(' in body                       # JS hook wired
    assert "uc-grid-equities" in body
    assert "P&amp;L" in body                       # sort chip label


def test_action_summary_still_rendered_via_summary_html(client):
    """Action summaries must keep going through the summary_html filter —
    no raw '**CLOSE**' markdown literals in the body (hard rule #30)."""
    r = client.get(f"/briefing/{DATE}")
    body = r.text
    # Strip attribute values (raw identifiers are allowed there)
    text = re.sub(r'"[^"]*"', '""', body)
    assert "**CLOSE**" not in text
    # The action verb + close ticket still render
    assert "buy-to-close limit" in body


def test_ideas_tab_keeps_humanized_kinds_and_status_chips(client):
    """Hard rules #31/#32 survive the refactor: machine identifiers are
    humanized and deferred ideas carry their status chip."""
    r = client.get(f"/briefing/{DATE}")
    body = r.text
    text = re.sub(r'"[^"]*"', '""', body)
    text = re.sub(r"'[^']*'", "''", text)
    assert "DEFERRED_ADD_HAS_CSP" not in text     # humanized, not raw
    assert "status-chip" in body                  # status chip preserved
    assert "deferred" in body.lower()


def test_v1_briefing_has_no_actions_tab(client):
    """V1 fixture has no actions array — the Actions tab must stay
    suppressed (existing contract from test_routes preserved)."""
    r = client.get(f"/briefing/{V1_DATE}")
    assert r.status_code == 200
    assert "Action queue" not in r.text


# ─── resolve_ticker (pure helper) ─────────────────────────────────────


def test_resolve_ticker_action_contract_ident():
    row = {"ident": "NVDA_PUT_180_20260918", "kind": "CLOSE"}
    assert unified_card.resolve_ticker(row, "action") == "NVDA"


def test_resolve_ticker_action_plain_ticker_ident():
    assert unified_card.resolve_ticker({"ident": "SPY"}, "action") == "SPY"


def test_resolve_ticker_action_garbage_ident_is_none():
    assert unified_card.resolve_ticker({"ident": "not a ticker!!"}, "action") is None


def test_resolve_ticker_option_uses_underlying():
    assert unified_card.resolve_ticker({"underlying": "nvda"}, "option") == "NVDA"


def test_resolve_ticker_equity_and_longterm_use_ticker():
    assert unified_card.resolve_ticker({"ticker": "amzn"}, "equity") == "AMZN"
    assert unified_card.resolve_ticker({"ticker": "META"}, "longterm") == "META"


def test_resolve_ticker_idea_capacity_placeholder_is_none():
    """The screener's capacity placeholder row (ticker='CAPACITY') is not
    a market symbol — no tech lookup, no skeleton."""
    assert unified_card.resolve_ticker({"ticker": "CAPACITY"}, "idea") is None
    assert unified_card.resolve_ticker({"ticker": "—"}, "idea") is None
    assert unified_card.resolve_ticker({"ticker": ""}, "idea") is None


def test_resolve_ticker_rotation_prefers_target_leg():
    row = {"from": {"ticker": "TSLA"}, "to": {"ticker": "VRT"}}
    assert unified_card.resolve_ticker(row, "rotation") == "VRT"


def test_resolve_ticker_rotation_option_parses_from_symbol():
    row = {"from": {"symbol": "SMH_CALL_300_20260821", "captured_pct": 42.0}}
    assert unified_card.resolve_ticker(row, "rotation") == "SMH"


def test_resolve_ticker_never_raises_on_junk():
    assert unified_card.resolve_ticker(None, "equity") is None
    assert unified_card.resolve_ticker({}, "rotation") is None
    assert unified_card.resolve_ticker({"ident": None}, "action") is None
    assert unified_card.resolve_ticker(object(), "option") is None


def test_resolve_ticker_works_on_pydantic_rows():
    """Briefing tab rows are Pydantic models, not dicts — attribute
    access must work too."""
    briefing = load_briefing(
        {
            "date": "2026-06-30",
            "options_reviews": [{"underlying": "NVDA", "contract": "NVDA_PUT_180_20260918"}],
            "actions": [{"kind": "CLOSE", "ident": "NVDA_PUT_180_20260918"}],
        }
    )
    assert unified_card.resolve_ticker(briefing.options_reviews[0], "option") == "NVDA"
    assert unified_card.resolve_ticker(briefing.actions[0], "action") == "NVDA"


# ─── option_pl_pct (pure helper) ──────────────────────────────────────


def test_option_pl_pct_short_put_profit():
    """Fixture case: short NVDA put entry $6.00, mid $3.20 → +46.7%."""
    row = {"qty": -1.0, "entry_price": 6.0, "current_mid": 3.2}
    pl = unified_card.option_pl_pct(row)
    assert pl == pytest.approx((6.0 - 3.2) / 6.0)


def test_option_pl_pct_short_loss_is_negative():
    row = {"qty": -2.0, "entry_price": 3.0, "current_mid": 5.4}
    assert unified_card.option_pl_pct(row) == pytest.approx(-0.8)


def test_option_pl_pct_long_side():
    row = {"qty": 1.0, "entry_price": 4.0, "current_mid": 5.0}
    assert unified_card.option_pl_pct(row) == pytest.approx(0.25)


def test_option_pl_pct_fails_open_on_missing_data():
    """Missing entry/mid → None (template renders '—', never fabricated)."""
    assert unified_card.option_pl_pct({"qty": -1.0, "current_mid": 3.2}) is None
    assert unified_card.option_pl_pct({"qty": -1.0, "entry_price": 6.0}) is None
    assert unified_card.option_pl_pct({"qty": -1.0, "entry_price": 0, "current_mid": 1}) is None
    assert unified_card.option_pl_pct({"entry_price": "junk", "current_mid": 3}) is None
    assert unified_card.option_pl_pct(None) is None


# ─── extras_tone (pure helper) ────────────────────────────────────────


def test_extras_tone_bands():
    assert unified_card.extras_tone("CLOSE") == "close"
    assert unified_card.extras_tone("EXIT") == "close"
    assert unified_card.extras_tone("DEFENSIVE_ROLL") == "roll"
    assert unified_card.extras_tone("HEDGE") == "roll"
    assert unified_card.extras_tone("TAKE_PROFIT") == "ok"
    assert unified_card.extras_tone("LONG_DATED_CSP") == "ok"
    assert unified_card.extras_tone("HOLD") == "neutral"
    assert unified_card.extras_tone(None) == "neutral"
    assert unified_card.extras_tone("some_new_pipeline_kind") == "neutral"


def test_rotations_tab_renders_unified_cards(client, monkeypatch):
    """No fixture briefing carries rotation_opportunities, but the tab is
    the riskiest template path (raw dict rows with `from`/`to` legs) —
    inject a rotation payload into the fixture briefing and assert both
    the equity-swap card and the option-reset card render without 500."""
    from app import ingest

    real_load = ingest.load_briefing_json

    def _with_rotations(date):
        raw = real_load(date)
        if raw and date == DATE:
            raw = dict(raw)
            raw["rotation_opportunities"] = {
                "stats": {"equity_count": 1, "options_count": 1},
                "equity": [
                    {
                        "from": {"ticker": "TSLA", "weight_pct": 4.2, "tier": "C", "score": 38},
                        "to": {
                            "ticker": "NVDA", "score": 61, "improvement": 23,
                            "parkev_label": "TOP 12", "reasons": ["RSI 44 pullback", "FV +18%"],
                        },
                    }
                ],
                "options": [
                    {
                        "from": {"symbol": "NVDA_PUT_180_20260918", "captured_pct": 47.0},
                        "to": {"hint": "Reset at $170P Oct 16 '26 for fresh premium."},
                    }
                ],
                "capital_plan": [],
            }
        return raw

    monkeypatch.setattr(ingest, "load_briefing_json", _with_rotations)
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'panel="rotations"' in body
    assert re.search(r'class="uc-card uc-rotation[" ]', body)
    # Equity swap card: from → to with delta score
    assert "TSLA" in body and "+23" in body
    # Option reset card: pretty contract + capture %
    assert "47% capture" in body
    # The target leg (NVDA) gets the real tech card inside the swap card
    assert 'data-ticker="NVDA"' in body


# ─── Technical Read tab unchanged ─────────────────────────────────────


def test_technical_read_tab_unchanged(client):
    """The Technical Read tab keeps its grouped tc-grid layout (no uc
    overlay) — it IS the base card."""
    r = client.get(f"/briefing/{DATE}")
    body = r.text
    assert 'panel="technical"' in body
    assert "Equity holdings" in body
    assert "tc-grid" in body
