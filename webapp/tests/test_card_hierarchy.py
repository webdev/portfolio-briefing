"""Card hierarchy v2 — WHAT / WHY / everything-else, one-voice RSI.

George (2026-08-13): "unified cards are hard to read now. I don't even
know what to pay attention to. It feels like some of the indicators are
wrong as far as CSP zone and CC zone. It just doesn't feel very
actionable."

Two root causes, both pinned here:

  1. Perceived contradictions — the RSI zone chip (RSI-only read, e.g.
     '🎯 CSP zone') rendered at full weight BESIDE the Setup Grade (full
     five-component read, e.g. 'CC setup D'). Real 2026-08-13 cases:
     NFLX/SOFI/SOXL/SOXX/VRT CC-write cards graded D each carried a
     primary '🎯 CSP zone' chip; SMH's covered-strangle card said
     'CSP setup D' AND '🎯 CSP zone'. Neither indicator is wrong (zone =
     RSI leg only; grade = whole setup) but the equal-weight presentation
     invites the misreading. Fix: the zone chip DEMOTES to a small
     secondary detail whenever a Setup Grade is present on the card;
     zone chips stay primary on grade-less cards (Technical Read tab).

  2. No hierarchy — cards accumulated chips (zone badge, grade badge,
     Parkev chip, tier badge, entry token, metrics, S/R, BB/MACD) with
     no visual order. Fix: strict order — (1) primary action line
     (grade letter + verdict), (2) WHY driver chips, (3) everything
     else collapsed behind <details class="uc-more">. BLOCKING flags
     (warnings, idea status chips, gate reasons) stay OUTSIDE the
     collapse because they change the action.

Filter bars are untouched — they key on the data-rsi-zones /
data-setup-grades attributes, which are emitted exactly as before.
"""

from __future__ import annotations

import re

import pytest

from app import rsi_zones, tech_card, unified_card


DATE = "2026-06-30"

_GRADED_IDEA = {
    "ticker": "NVDA",
    "name": "NVIDIA",
    "source": "screener",
    "instruction": "SELL 1× NVDA $180P exp Fri Sep 18 '26",
    "rationale": "Pullback entry at tested support.",
    "setup_grade": "B",
    "setup_grade_score": 70.0,
    "setup_grade_drivers": ["RSI 44 prime", "IVr 78 ✓", "support ✓"],
    "setup_grade_message": "🏁 Entry: B — good setup; enter per plan.",
}


_GRADED_CC_WRITE = {
    "type": "write_covered_call",
    "underlying": "NVDA",
    "rationale": "Extended into resistance; rich premium.",
    "setup_grade": "D",
    "setup_grade_score": 20.0,
    "setup_grade_drivers": ["RSI 44 weak for CC", "no resistance test"],
    "setup_grade_message": "⛔ Entry: D — poor CC timing; wait for strength.",
}

_GRADED_STRANGLE = {
    "type": "covered_strangle",
    "underlying": "NVDA",
    "rationale": "Covered strangle proposal.",
    "setup_grade": "D",
    "setup_grade_score": 22.0,
    "setup_grade_drivers": ["RSI 44 weak for CC"],
    "setup_grade_message": "⛔ Entry: D — poor timing.",
}


@pytest.fixture
def client_hierarchy(client, monkeypatch):
    """Fixture briefing augmented with a graded NVDA idea (NVDA carries a
    full deep tech read → embedded tech card with a zone chip) and a
    blocking warning on the NVDA option review."""
    from app import ingest

    real_load = ingest.load_briefing_json

    def _augmented(date):
        raw = real_load(date)
        if raw and date == DATE:
            raw = dict(raw)
            raw["new_ideas"] = [dict(_GRADED_IDEA)]
            reviews = [dict(o) for o in (raw.get("options_reviews") or [])]
            for o in reviews:
                if o.get("underlying") == "NVDA":
                    o["warnings"] = ["earnings within expiration window"]
            raw["options_reviews"] = reviews
            # Graded strategy upgrades — the surface the real 2026-08-13
            # complaint named (CC-writes graded D). One attaches to the
            # NVDA option card (write_covered_call), one renders as a
            # standalone strategy card (covered_strangle is not an
            # affordance type). Both become Pydantic StrategyUpgrade
            # models in the route — the grade must survive that.
            raw["strategy_upgrades"] = list(raw.get("strategy_upgrades") or []) + [
                dict(_GRADED_CC_WRITE), dict(_GRADED_STRANGLE)]
        return raw

    monkeypatch.setattr(ingest, "load_briefing_json", _augmented)
    return client


def _uc_chunks(body: str) -> list[str]:
    """Split the page into one chunk per top-level unified card. The
    nested tech card uses class 'tech-card', so splitting on the uc-card
    article-open keeps each card's full markup in one chunk."""
    parts = body.split('<article class="uc-card')
    return parts[1:]


def _chunk(body: str, kind: str, ticker_attr: str) -> str:
    """The unified card chunk whose OWN article tag matches the tab kind
    and ticker (both live in the article-open attributes, before the
    first '>') — loose containment would match neighboring chunks whose
    tails include section markup like the 'uc-idea-filter' bar."""
    for c in _uc_chunks(body):
        head = c[:c.index(">")]
        if head.lstrip().startswith(kind) and ticker_attr in head:
            return c
    raise AssertionError(f"no uc-card chunk for {kind!r} + {ticker_attr!r}")


# ─── (a) Zone chip demotes under a grade; primary without one ─────────


def test_zone_chip_demoted_when_setup_grade_present(client_hierarchy):
    """The graded NVDA idea card (CSP setup B) embeds NVDA's tech card
    (RSI 44 → '🎯 CSP zone'). The zone chip must render as a SECONDARY
    detail (tc-zone-secondary) so it can't visually contradict the grade
    — the exact 2026-08-13 'CSP zone beside a D' misreading."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    idea = _chunk(r.text, "uc-idea", 'data-ticker="NVDA"')
    assert "tc-zone-chip" in idea
    assert "tc-zone-secondary" in idea
    # The demoted chip explains itself instead of shouting a verdict.
    assert "RSI leg only" in idea


def test_zone_chip_primary_when_no_grade(client_hierarchy):
    """Cards WITHOUT a Setup Grade keep the zone chip primary: the
    Technical Read tab's NVDA card and the ungraded equity card must
    render tc-zone-chip at full weight (no tc-zone-secondary)."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    body = r.text
    # Technical Read tab renders bare tech cards (no grade → primary).
    tech_articles = re.findall(
        r'<article class="tech-card[^"]*"[^>]*>.*?</article>', body, re.S)
    nvda_bare = [a for a in tech_articles
                 if 'data-ticker="NVDA"' in a and "tc-zone-secondary" not in a]
    assert nvda_bare, "expected a primary (non-demoted) zone chip on the "\
        "grade-less Technical Read card"
    assert any("tc-zone-chip" in a for a in nvda_bare)
    # Ungraded equity card: embedded tech card keeps the primary chip.
    equity = _chunk(body, "uc-equity", 'data-ticker="NVDA"')
    assert "tc-zone-chip" in equity
    assert "tc-zone-secondary" not in equity


def test_zone_data_attributes_unchanged_by_demotion(client_hierarchy):
    """Demotion is VISUAL only — the graded idea card still carries the
    measured data-rsi-zones and data-setup-grades attributes the filter
    bars key on."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    idea = _chunk(r.text, "uc-idea", 'data-ticker="NVDA"')
    head = idea[:idea.index(">")]
    assert 'data-rsi-zones="csp_zone"' in head
    assert "csp_b" in head and "csp_good" in head  # data-setup-grades


# ─── (b) One-voice RSI — zone badge == displayed RSI, same source ─────


def test_one_voice_rsi_chip_and_zone_badge_same_value():
    """'It feels like some of the indicators are wrong as far as CSP zone
    and CC zone' — the RSI chip and the zone badge on a tech card MUST
    derive from the same measured rsi_14. Pin: identical rounded number
    in both, and the zones attribute classifies the same value."""
    entry = {"deep": {"spot": 100.0, "rsi_14": 47.4, "bb_position_pct": 50.0,
                      "macd_hist": 0.1, "macd_hist_5d_ago": 0.05}}
    t = tech_card.build_card("NVDA", entry)
    assert t["rsi"] == 47.4
    assert t["chips"][0]["label"] == "RSI 47"
    assert t["rsi_zone_badge"]["text"].startswith("RSI 47 ·")
    assert t["rsi_zone_badge"]["label"] == "🎯 CSP zone"
    assert t["rsi_zones"] == rsi_zones.zones_attr(47.4) == "csp_zone"


def test_one_voice_rsi_string_value_does_not_split_voices():
    """Regression: the RSI chip used to read the RAW deep value while the
    zone badge read the float-coerced copy. A string '54.3' from a
    hand-edited/older snapshot would crash the chip (TypeError on >=)
    while the badge rendered fine — a literal two-voices bug. Both now
    consume the same coerced value."""
    entry = {"deep": {"spot": 100.0, "rsi_14": "54.3", "bb_position_pct": 50.0,
                      "macd_hist": 0.1, "macd_hist_5d_ago": 0.05}}
    t = tech_card.build_card("SMH", entry)
    assert t["rsi"] == 54.3
    assert t["chips"][0]["label"] == "RSI 54"
    assert t["rsi_zone_badge"]["text"].startswith("RSI 54 ·")
    assert t["rsi_zones"] == "csp_zone"


def test_one_voice_report_card_badge_matches_card_rsi():
    """Report-card surfaces: the zone badge number must equal the RSI
    card_rsi resolves for that card — measured tech value first, else the
    card's own RSI metric chip. Never a third number."""
    tech = {"available": True, "rsi": 44.0}
    card = {"metrics": [{"label": "RSI", "value": "62"}]}
    # tech present → badge voice is the measured 44, same as the embedded
    # tech card's RSI chip.
    assert rsi_zones.card_rsi(tech, card) == 44.0
    assert rsi_zones.card_zone_badge(tech, card)["text"].startswith("RSI 44")
    # no tech → badge voice is the card's own displayed metric.
    assert rsi_zones.card_rsi(None, card) == 62.0
    assert rsi_zones.card_zone_badge(None, card)["text"].startswith("RSI 62")
    # no RSI anywhere → no badge, never fabricated (rule #19).
    assert rsi_zones.card_zone_badge(None, {}) is None


# ─── (c) Primary action line ──────────────────────────────────────────


def test_primary_line_renders_grade_letter_on_graded_idea(client_hierarchy):
    """WHAT TO DO, line 1: the graded NVDA idea renders the grade letter
    chip (B, good tone) on the primary line plus the concrete ticket."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    idea = _chunk(r.text, "uc-idea", 'data-ticker="NVDA"')
    assert "uc-primary" in idea
    assert 'uc-primary-letter uc-letter-good' in idea
    assert ">B</span>" in idea
    assert "SELL 1× NVDA $180P" in idea


def test_primary_line_renders_verdict_and_capture_on_option(client_hierarchy):
    """Held positions: the advisor verdict + capture is the primary line.
    Fixture NVDA short put: TAKE_PROFIT at (6.00-3.20)/6.00 = +47%."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    opt = _chunk(r.text, "uc-option", 'data-ticker="NVDA"')
    assert "uc-primary" in opt
    assert "uc-primary-capture" in opt
    assert "+47% captured" in opt
    assert "Take profit" in opt or "TAKE_PROFIT" in opt.upper()


def test_why_row_carries_grade_drivers(client_hierarchy):
    """WHY, line 2: the graded card's driver chips are the grade's OWN
    measured components (never invented)."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    idea = _chunk(r.text, "uc-idea", 'data-ticker="NVDA"')
    assert "uc-why" in idea
    assert "RSI 44 prime" in idea
    assert "IVr 78 ✓" in idea


def test_grade_letter_info_and_why_chips_pure_helpers():
    g = unified_card.grade_letter_info({"kind": "NEW_CSP",
                                        "raw": dict(_GRADED_IDEA)})
    assert g == {
        "letter": "B", "side": "CSP", "tone": "good",
        "title": g["title"],
    }
    assert "RSI 44 prime" in g["title"]
    # D → low (amber), hard-block → bad (red), ungraded → None.
    assert unified_card.grade_letter_info(
        {"type": "write_covered_call", "setup_grade": "D"})["tone"] == "low"
    assert unified_card.grade_letter_info(
        {"type": "write_covered_call", "setup_grade": "—"})["tone"] == "bad"
    assert unified_card.grade_letter_info({"kind": "NEW_CSP", "raw": {}}) is None

    # why_chips: graded → drivers, capped at 3.
    chips = unified_card.why_chips(
        {"kind": "NEW_CSP", "raw": dict(_GRADED_IDEA)}, "idea")
    assert chips == ["RSI 44 prime", "IVr 78 ✓", "support ✓"]
    # ungraded → measured facts only (RSI from tech, IVr/DTE from the row).
    chips = unified_card.why_chips(
        {"iv_rank": 78.6, "days_to_expiry": 80}, "option", {"rsi": 44.0})
    assert chips == ["RSI 44", "IVr 79", "80 DTE"]
    # nothing measured → empty, never fabricated (rule #19).
    assert unified_card.why_chips({}, "option", None) == []


# ─── (d) Blocking flags outside the collapse, context inside ──────────


def test_blocking_option_warning_outside_collapse(client_hierarchy):
    """The injected earnings warning changes the action — it must render
    BEFORE the <details> collapse on the option card."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    opt = _chunk(r.text, "uc-option", 'data-ticker="NVDA"')
    assert "uc-warnings" in opt
    assert "earnings within expiration window" in opt
    assert opt.index("uc-warnings") < opt.index('<details class="uc-more"')


def test_blocking_idea_status_chip_outside_collapse(client_hierarchy):
    """Idea status chips (deferred / capacity blocked) change the action:
    the fixture GOOG waiting card renders its status chip and its wait
    reason BEFORE the collapse; the same card's Parkev source chip lives
    INSIDE the collapse."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    goog = _chunk(r.text, "uc-idea", 'data-ticker="GOOG"')
    assert "status-chip" in goog
    assert "when conditions clear:" in goog
    di = goog.index('<details class="uc-more"')
    assert goog.index("status-chip") < di
    assert goog.index("uc-trade-prefix") < di
    # Non-blocking context (sources row) is inside the collapse.
    assert goog.index("uc-sources") > di


def test_nonblocking_sources_metrics_tech_inside_collapse(client_hierarchy):
    """EVERYTHING ELSE: Parkev chip, metrics, and the tech card (S/R ·
    BB/MACD) sit inside <details class="uc-more"> on the option card."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    opt = _chunk(r.text, "uc-option", 'data-ticker="NVDA"')
    di = opt.index('<details class="uc-more"')
    assert opt.index("uc-sources") > di
    assert opt.index("uc-extras-metrics") > di
    assert opt.index("uc-tech") > di
    assert opt.index("tc-bb-track") > di


# ─── (e) Details collapse contains the sources row ────────────────────


def test_details_collapse_contains_source_chips_row(client_hierarchy):
    """One 'Sources' row inside the collapse per card — the Parkev chip
    (🅿️ …) renders there, not scattered across the card head."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    equity = _chunk(r.text, "uc-equity", 'data-ticker="NVDA"')
    di = equity.index('<details class="uc-more"')
    si = equity.index("uc-sources")
    assert si > di
    # The Parkev chip marker appears after the sources row opens.
    assert "🅿️" in equity[si:]


# ─── (f) Filter bars keep working (data attributes intact) ────────────


def test_filter_bars_and_data_attributes_survive_redesign(client_hierarchy):
    """RSI zone + Setup Grade filter bars render, and EVERY unified card
    still carries both data attributes (the bars filter on data, not on
    the visual chips — demotion must not break them)."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'class="uc-sort rsi-filter-bar"' in body
    assert 'class="uc-sort grade-filter-bar"' in body
    cards = re.findall(r'<article class="uc-card[^>]*>', body)
    assert cards
    assert all("data-rsi-zones=" in c for c in cards)
    assert all("data-setup-grades=" in c for c in cards)


def test_served_js_filter_engines_unchanged(client):
    """The served app.js still carries both filter engines keyed on the
    data attributes (template–asset contract)."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "data-rsi-zones" in r.text
    assert "data-setup-grades" in r.text


def test_app_css_has_hierarchy_and_demotion_rules(client):
    r = client.get("/static/app.css")
    assert r.status_code == 200
    css = r.text
    for cls in (".uc-primary", ".uc-primary-letter", ".uc-why",
                ".uc-more", ".uc-sources", ".tc-zone-secondary",
                ".rep-more"):
        assert cls in css, f"missing {cls} in app.css"


# ─── Report cards inherit the collapse; blocking stays out ────────────


def test_report_cards_collapse_tech_keep_blocking_visible(client):
    """/setups report cards: the technical read + FV + earnings collapse
    behind <details class="rep-more">; the status chip, metrics and any
    deferred (blocking) note stay visible above the collapse."""
    r = client.get(f"/setups/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert '<details class="rep-more">' in body
    chunks = body.split('<article class="rep-card')
    tech_chunks = [c for c in chunks[1:] if "rep-tech" in c]
    assert tech_chunks, "expected at least one report card with tech content"
    for c in tech_chunks:
        assert c.index('<details class="rep-more">') < c.index("rep-tech")
        assert "rep-status" in c  # status chip present (visible zone)
        assert c.index("rep-status") < c.index('<details class="rep-more">')


# ─── (g) Grades survive the Pydantic route — the real-page gap ────────
# Real-render check on the LIVE 2026-08-13 briefing found every one of
# the 53 cards carrying data-setup-grades="none" while the briefing JSON
# had eight graded strategy_upgrades (SMH strangle D, NFLX/SOFI/SOXL/
# SOXX/VRT CC-writes D, MSFT C, SPY A) — the exact cards George's
# complaint named. Root cause: the briefing route parses upgrades into
# Pydantic StrategyUpgrade models (extra="allow"), and _grade_of bailed
# on anything that wasn't a dict, so the grade never surfaced and the
# zone chip never demoted on the one surface that needed it most.


def test_grade_of_reads_pydantic_strategy_upgrade():
    """setup_grade_ui.grade_of / grade_tokens accept the route's Pydantic
    StrategyUpgrade models — the grade rides in the extras and must
    survive: ('cc', 'D') for a graded CC-write, tokens 'cc_d'."""
    from app import setup_grade_ui
    from app.models.briefing import StrategyUpgrade

    su = StrategyUpgrade.model_validate({
        "type": "write_covered_call", "underlying": "SOXX",
        "setup_grade": "D", "setup_grade_score": 20.0,
        "setup_grade_drivers": ["RSI 44 weak for CC"],
        "setup_grade_message": "⛔ Entry: D — poor CC timing.",
    })
    side, letter, raw = setup_grade_ui.grade_of(su)
    assert (side, letter) == ("cc", "D")
    assert raw.get("setup_grade_drivers") == ["RSI 44 weak for CC"]
    # Tokens: side-aware slug + the plain per-letter pill token (George
    # 2026-08-17 per-letter filter pills).
    assert set(setup_grade_ui.grade_tokens(su).split()) == {"cc_d", "d"}
    badge = setup_grade_ui.grade_badge(su)
    assert badge is not None and "CC setup D" in badge["label"]
    # Ungraded model → fail closed, no fabricated grade (rule #19).
    bare = StrategyUpgrade.model_validate({"type": "collar",
                                           "underlying": "MSFT"})
    side, letter, _ = setup_grade_ui.grade_of(bare)
    assert (side, letter) == (None, None)
    assert setup_grade_ui.grade_tokens(bare) == "none"


def _strategy_chunk_with_grade(body: str, token: str) -> str:
    """The uc-strategy chunk whose data-setup-grades carries `token`."""
    for c in _uc_chunks(body):
        head = c[:c.index(">")]
        if head.lstrip().startswith("uc-strategy") and token in head:
            return c
    raise AssertionError(f"no graded uc-strategy chunk with {token!r}")


def test_standalone_strategy_card_renders_grade_and_demotes_zone(
        client_hierarchy):
    """The graded covered-strangle strategy card (Pydantic model in the
    route) renders the grade letter on the primary line, the grade badge
    in the Sources row, tokens on data-setup-grades — and its embedded
    tech card demotes the zone chip (the 2026-08-13 SMH case: 'CSP setup
    D' AND a primary '🎯 CSP zone' on the same card)."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    card = _strategy_chunk_with_grade(r.text, "csp_d")
    assert "uc-primary-letter" in card
    assert ">D</span>" in card
    assert "uc-grade-badge" in card
    assert "tc-zone-secondary" in card
    assert "tc-zone-chip" in card  # demoted, not dropped


def test_option_card_affordance_shows_grade_and_demotes_zone(
        client_hierarchy):
    """A graded WRITE CC attached to the NVDA option card surfaces its
    grade letter on the affordance line, and the option card's embedded
    tech card demotes the zone chip — a grade is 'present on the card'
    whether it rides on the card's own base or an attached affordance
    (the 2026-08-13 SOXL/SOXX case)."""
    r = client_hierarchy.get(f"/briefing/{DATE}")
    opt = _chunk(r.text, "uc-option", 'data-ticker="NVDA"')
    ai = opt.index("uc-strategy-affordances")
    di = opt.index('<details class="uc-more"')
    assert ai < di  # the affordance is a recommendation — visible
    affordance = opt[ai:di]
    assert "uc-primary-letter" in affordance
    assert ">D</span>" in affordance
    assert "tc-zone-secondary" in opt
