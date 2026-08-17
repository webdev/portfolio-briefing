"""Setup Grade on EVERY setups/report card + per-letter filter pills.

George (2026-08-17): "Setups I have to have a grade so I can know whether
it's an A entry or B or D. Also, I need to have a filter little pill that
I can click on so I can see A, B, C, D. Without it, it's really hard to
know which one is a good one, which one is not."

Coverage:
  (a) markdown grade extraction — all three observed line forms
      ('**Setup Grade: B** (66/100) · …', '**Setup Grade: A 💎 PRIME**',
      '**Setup Grade: —** · RSI 71 hard block', and the below-floor
      '⏸ Below setup floor — D (44): …')
  (b) letter chip renders on rep-cards / absent when ungraded
  (c) per-letter pill bar with counts on every wired surface
      (/setups, /candidates, /when-to-enter, briefing Ideas + Strategy)
  (d) A groups A-, blocked/ungraded buckets
  (e) AND-composition with the RSI zone filter (both attributes on one
      card, class-based sg-hidden engine in the served JS)
  (f) drift guard vs pipeline letters (chips derive from the config bridge)
  plus the grade-first sort assist within report sections.
"""

from __future__ import annotations

import re

import pytest

from app import report_parser, setup_grade_ui

DATE = "2026-06-30"


# ─── (a) Markdown grade extraction ───────────────────────────────────────


def _card_from(md: str, kind: str = "candidates") -> dict:
    report = report_parser.parse_report(md, kind)
    cards = [c for s in report["sections"] for c in s["cards"]]
    assert cards, "fixture snippet parsed no cards"
    return cards[0]


def test_parser_extracts_standard_grade_line():
    """'**Setup Grade: B** (66/100) · RSI 48 late-band · RVr 97 ✓ …' —
    the exact form the pipeline's format_grade_note emits — must land as
    letter/score fields on the card, so George "can know whether it's an
    A entry or B or D"."""
    md = (
        "## 🔭 Semis\n\n"
        "**🎯 CANDIDATE · `MU` · $890.00**\n"
        "  - RSI 48 · above 200-SMA (+7%) · IV rank 79 · 16% pullback\n"
        "  - **Setup Grade: B** (66/100) · RSI 48 late-band · RVr 97 ✓ · "
        "no support under strike ✗ — 🏁 Entry: B — good setup; RVr 97 ✓. "
        "Enter per plan.\n"
    )
    c = _card_from(md)
    assert c["setup_grade"] == "B"
    assert c["setup_grade_score"] == 66.0
    assert c["setup_grade_prime"] is False
    assert c["setup_grade_below_floor"] is False
    assert "🏁 Entry: B" in c["setup_grade_note"]
    assert "**" not in c["setup_grade_note"]  # tooltip-ready plain text
    # the grade line is extracted, not duplicated into extras
    assert not any("Setup Grade" in x for x in c["extras"])


def test_parser_extracts_prime_grade():
    """'**Setup Grade: A 💎 PRIME** (92/100)' → prime flag True."""
    md = (
        "## 🔭 Semis\n\n"
        "**🎯 CANDIDATE · `NVDA` · $198.82**\n"
        "  - **Setup Grade: A 💎 PRIME** (92/100) · RSI 43 prime · "
        "RVr 88 ✓ — 🏁 Entry: A — strong setup. Enter per plan.\n"
    )
    c = _card_from(md)
    assert c["setup_grade"] == "A"
    assert c["setup_grade_score"] == 92.0
    assert c["setup_grade_prime"] is True


def test_parser_extracts_hard_block_grade():
    """'**Setup Grade: —** · RSI 71 hard block ✗ — 🏁 Entry: — —
    blocked: …' → letter '—', no score, no fabricated number."""
    md = (
        "## 🔭 Semis\n\n"
        "**👀 WATCH · `AMAT` · $736.47**\n"
        "  - **Setup Grade: —** · RSI 71 hard block ✗ — 🏁 Entry: — — "
        "blocked: RSI 71 is overbought (>70). New put-sale blocked.\n"
    )
    c = _card_from(md)
    assert c["setup_grade"] == "—"
    assert c["setup_grade_score"] is None


def test_parser_extracts_below_floor_only():
    """'⏸ Below setup floor — D (44): …' with no separate grade line —
    the below-floor note supplies letter D + score 44 and flags the
    floor demotion (George quoted exactly this D case)."""
    md = (
        "## 🔭 Semis\n\n"
        "**🎯 CANDIDATE · `XYZ` · $100.00**\n"
        "  - **⏸ Below setup floor — D (44): a ≥2-touch support under "
        "the strike or RSI 35-45 (now 54)**\n"
    )
    c = _card_from(md)
    assert c["setup_grade"] == "D"
    assert c["setup_grade_score"] == 44.0
    assert c["setup_grade_below_floor"] is True
    # the demotion note stays visible on the card (extras), never hidden
    assert any("Below setup floor" in x for x in c["extras"])


def test_parser_grade_line_wins_over_floor_note():
    """A card carrying BOTH the grade line and the floor note keeps the
    grade line's letter/score; the floor flag is still set."""
    md = (
        "## 🔭 Semis\n\n"
        "**🎯 CANDIDATE · `ABC` · $50.00**\n"
        "  - **Setup Grade: C** (52/100) · RSI 54 off-band ✗ — "
        "🏁 Entry: C — wait; prime needs RSI 35-45 (now 54).\n"
        "  - **⏸ Below setup floor — C (52): RSI 35-45 (now 54)**\n"
    )
    c = _card_from(md)
    assert c["setup_grade"] == "C"
    assert c["setup_grade_score"] == 52.0
    assert c["setup_grade_below_floor"] is True


def test_parser_when_to_enter_form():
    """The when_to_enter report renders the same grade line as an
    unindented bullet under its own card header — same extraction."""
    md = (
        "## Semis (1)\n\n"
        "#### `NVDA` · $198.82 · **🟢 ENTRY NOW — CSP**\n"
        "- **Setup Grade: A-** (80/100) · RSI 41 prime — 🏁 Entry: A- — "
        "strong setup. Enter per plan.\n"
    )
    c = _card_from(md, kind="when_to_enter")
    assert c["setup_grade"] == "A-"
    assert c["setup_grade_score"] == 80.0


def test_parser_ungraded_card_fails_closed():
    """No grade line on the card → all grade fields stay None/False —
    never a fabricated grade (rule #19)."""
    md = (
        "## 🔭 Semis\n\n"
        "**👀 WATCH · `TXN` · $180.00**\n"
        "  - RSI 55 · IV rank 40 · drawdown 5%\n"
    )
    c = _card_from(md)
    assert c["setup_grade"] is None
    assert c["setup_grade_score"] is None
    assert c["setup_grade_prime"] is False


# ─── (d) Tokens: A groups A-, blocked / ungraded buckets ─────────────────


def test_report_tokens_carry_side_and_plain_letter():
    toks = setup_grade_ui.report_card_tokens(
        {"setup_grade": "B", "setup_grade_score": 66.0}).split()
    assert "csp_b" in toks       # side-aware grammar (backward compatible)
    assert "b" in toks           # plain per-letter pill token
    assert "csp_good" in toks    # grouped-good token still emitted


def test_report_tokens_a_minus_groups_into_a_pill():
    """The [A] pill groups A and A- — George asked for four letters, not
    five."""
    toks = setup_grade_ui.report_card_tokens({"setup_grade": "A-"}).split()
    assert "a" in toks and "csp_a_minus" in toks
    toks_a = setup_grade_ui.report_card_tokens({"setup_grade": "A"}).split()
    assert "a" in toks_a


def test_report_tokens_blocked_prime_and_ungraded_buckets():
    assert "blocked" in setup_grade_ui.report_card_tokens(
        {"setup_grade": "—"}).split()
    toks = setup_grade_ui.report_card_tokens(
        {"setup_grade": "A", "setup_grade_prime": True}).split()
    assert "prime" in toks
    assert setup_grade_ui.report_card_tokens({}) == "none"
    assert setup_grade_ui.report_card_tokens({"setup_grade": "n/a"}) == "none"
    assert setup_grade_ui.report_card_tokens(None) == "none"


def test_uc_grade_tokens_also_carry_plain_letter():
    """The briefing's uc-cards share the pill grammar: side token AND the
    plain letter token, so ONE pill bar filters both surfaces."""
    toks = setup_grade_ui.grade_tokens(
        {"kind": "NEW_CSP", "raw": {"setup_grade": "A-"}}).split()
    assert "csp_a_minus" in toks and "a" in toks
    toks_cc = setup_grade_ui.grade_tokens(
        {"type": "write_covered_call", "setup_grade": "D"}).split()
    assert "cc_d" in toks_cc and "d" in toks_cc
    toks_blocked = setup_grade_ui.grade_tokens(
        {"kind": "NEW_CSP", "raw": {"setup_grade": "—"}}).split()
    assert "blocked" in toks_blocked
    toks_prime = setup_grade_ui.grade_tokens(
        {"kind": "NEW_CSP", "raw": {"setup_grade": "A",
                                    "setup_grade_prime": True}}).split()
    assert "prime" in toks_prime


# ─── (b) Letter chip on rep-cards ────────────────────────────────────────


def test_report_badge_renders_letter_prominently():
    b = setup_grade_ui.report_card_badge(
        {"setup_grade": "B", "setup_grade_score": 66.0,
         "setup_grade_note": "Setup Grade: B (66/100) · RSI 48 late-band "
                             "— 🏁 Entry: B — good setup."})
    assert b is not None
    assert b["label"].startswith("🏁 B")
    assert "66" in b["label"]
    assert b["rep_tone"] == "green"
    assert "🏁 Entry: B" in b["title"]
    assert "A/B = enter quality" in b["title"]  # reading guide in tooltip


def test_report_badge_prime_blocked_and_floor_variants():
    prime = setup_grade_ui.report_card_badge(
        {"setup_grade": "A", "setup_grade_score": 92.0,
         "setup_grade_prime": True})
    assert "💎" in prime["label"] and prime["rep_tone"] == "green"
    blocked = setup_grade_ui.report_card_badge({"setup_grade": "—"})
    assert blocked["label"] == "⛔ blocked" and blocked["rep_tone"] == "red"
    floor = setup_grade_ui.report_card_badge(
        {"setup_grade": "D", "setup_grade_score": 44.0,
         "setup_grade_below_floor": True})
    assert floor["rep_tone"] == "amber"
    assert "Below the actionable B floor" in floor["title"]


def test_report_badge_none_when_ungraded():
    """Ungraded → no chip, never fabricated (rule #19)."""
    assert setup_grade_ui.report_card_badge({}) is None
    assert setup_grade_ui.report_card_badge({"setup_grade": None}) is None
    assert setup_grade_ui.report_card_badge({"setup_grade": "n/a"}) is None


def test_candidates_page_renders_grade_chip_on_graded_card(client):
    """The fixture NVDA card carries '**Setup Grade: A 💎 PRIME**
    (92/100)' — the rendered rep-card must show the letter chip."""
    r = client.get(f"/candidates/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "rep-grade-chip" in body
    assert "🏁 A (92) 💎" in body
    assert "⛔ blocked" in body  # AMAT hard-block chip


def test_ungraded_card_shows_no_chip(client):
    """The candidates fixture leaves TXN ungraded — its card carries
    data-setup-grades="none" and NO grade chip (never fabricate a grade,
    rule #19)."""
    r = client.get(f"/candidates/{DATE}")
    assert r.status_code == 200
    m = re.search(r'<article class="rep-card[^>]*id="card-TXN"[^>]*>',
                  r.text)
    assert m, "TXN rep-card missing"
    assert 'data-setup-grades="none"' in m.group(0)
    # chip absent inside the TXN card chunk
    chunk = r.text[m.start():r.text.index("</article>", m.start())]
    assert "rep-grade-chip" not in chunk


# ─── (c) Per-letter pill bar on every wired surface ──────────────────────


def _assert_pill_bar(body: str) -> None:
    assert 'class="uc-sort grade-filter-bar"' in body
    assert 'data-grade-cards=".rep-card"' in body
    for grade in ("prime", "a", "b", "c", "d", "blocked", "none"):
        assert f'data-grade="{grade}"' in body, f"missing {grade} pill"
    assert '<span class="grade-count">' in body


def test_setups_page_renders_per_letter_pill_bar(client):
    r = client.get(f"/setups/{DATE}")
    assert r.status_code == 200
    _assert_pill_bar(r.text)
    # every rep-card carries the data attribute
    cards = re.findall(r'<article class="rep-card[^"]*"[^>]*>', r.text)
    assert cards
    assert all("data-setup-grades=" in c for c in cards)


def test_candidates_page_renders_per_letter_pill_bar(client):
    r = client.get(f"/candidates/{DATE}")
    assert r.status_code == 200
    _assert_pill_bar(r.text)


def test_when_to_enter_page_renders_per_letter_pill_bar(client):
    r = client.get(f"/when-to-enter/{DATE}")
    assert r.status_code == 200
    _assert_pill_bar(r.text)


def test_briefing_tabs_render_per_letter_pills(client):
    """Ideas + Strategy tabs replace the grouped 'CSP setup A/B' chips
    with the per-letter bar (the same shared macro)."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    for grade in ("a", "b", "c", "d", "blocked", "none", "prime"):
        assert f'data-grade="{grade}"' in body
    # grouped chips retired from the bar
    assert 'data-grade="csp_good"' not in body
    assert 'data-grade="cc_good"' not in body


# ─── (e) AND-composition with the RSI zone filter ────────────────────────


def test_rep_cards_carry_both_rsi_and_grade_attributes(client):
    """Both filter engines key off the SAME article tag — every rep-card
    carries data-rsi-zones AND data-setup-grades so the bars compose."""
    r = client.get(f"/setups/{DATE}")
    assert r.status_code == 200
    cards = re.findall(r'<article class="rep-card[^"]*"[^>]*>', r.text)
    assert cards
    for c in cards:
        assert "data-rsi-zones=" in c and "data-setup-grades=" in c


def test_served_js_composes_grade_filter_with_rsi(client):
    """The grade engine hides via the sg-hidden CLASS (not
    style.display) so it ANDs with the RSI bar; it also collapses report
    sections it empties without fighting the RSI engine's section
    toggle."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    body = r.text
    assert "sg-hidden" in body
    assert "data-setup-grades" in body
    # grade engine's class-based section collapse for report pages
    assert 'classList.toggle("sg-hidden", visible === 0)' in body


# ─── (f) Drift guard — pills derive from the pipeline config bridge ──────


def test_pill_titles_derive_from_pipeline_letter_floors():
    """Letters/floors come from the pipeline's setup_grade via the config
    bridge — the pill tooltips must carry the SAME floors (never a
    hardcoded threshold drifting from briefing.yaml)."""
    from app.config import briefing_config
    from analysis import setup_grade as sg

    cfg = sg.load_setup_grade_config(briefing_config())
    floors = {k: float(v) for k, v in cfg["letters"].items()}
    assert setup_grade_ui.letters() == floors
    chips = {c["grade"]: c for c in setup_grade_ui.grade_filter_chips()}
    assert f"{floors['a_minus']:.0f}/100" in chips["a"]["title"]
    assert f"{floors['b']:.0f}/100" in chips["b"]["title"]
    assert f"{floors['c']:.0f}/100" in chips["c"]["title"]
    assert f"{floors['c']:.0f}/100" in chips["d"]["title"]


# ─── Sort assist — grade-first within sections ───────────────────────────


def test_sort_cards_grade_first_unit():
    report = {"sections": [{"cards": [
        {"ticker": "U1"},                      # ungraded
        {"ticker": "BL", "setup_grade": "—"},
        {"ticker": "D1", "setup_grade": "D"},
        {"ticker": "A1", "setup_grade": "A"},
        {"ticker": "AM", "setup_grade": "A-"},
        {"ticker": "B1", "setup_grade": "B"},
        {"ticker": "C1", "setup_grade": "C"},
    ]}]}
    report_parser.sort_cards_grade_first(report)
    order = [c["ticker"] for c in report["sections"][0]["cards"]]
    assert order == ["A1", "AM", "B1", "C1", "D1", "BL", "U1"]


def test_candidates_page_orders_cards_grade_first(client):
    """Fixture Semis section: NVDA (A) → SMCI (D) → AMAT (blocked). The
    rendered card order must follow the grade, so the good ones lead."""
    r = client.get(f"/candidates/{DATE}")
    assert r.status_code == 200
    body = r.text
    i_nvda = body.find('id="card-NVDA"')
    i_smci = body.find('id="card-SMCI"')
    i_amat = body.find('id="card-AMAT"')
    i_txn = body.find('id="card-TXN"')
    assert -1 not in (i_nvda, i_smci, i_amat, i_txn)
    assert i_nvda < i_smci < i_amat < i_txn  # A → D → blocked → ungraded


# ─── Tech cards: graded ONLY when the briefing JSON pools grade the
#     ticker (research-not-recs; never fabricated) ────────────────────────


@pytest.fixture
def client_graded_idea(client, monkeypatch):
    """Fixture briefing augmented with ONE graded NVDA idea so the tech
    card for NVDA (which has a deep read) can inherit the grade tokens."""
    from app import ingest

    real_load = ingest.load_briefing_json

    def _augmented(date):
        raw = real_load(date)
        if raw and date == DATE:
            raw = dict(raw)
            raw["new_ideas"] = [{
                "ticker": "NVDA",
                "source": "screener",
                "instruction": "SELL 1× NVDA $180P exp Fri Sep 18 '26",
                "setup_grade": "B",
                "setup_grade_score": 70.0,
                "setup_grade_message": "🏁 Entry: B — good setup.",
            }]
        return raw

    monkeypatch.setattr(ingest, "load_briefing_json", _augmented)
    return client


def test_tech_card_stamped_only_when_briefing_pools_grade_the_ticker(
        client_graded_idea):
    """Technical Read cards are research, not recs — they carry
    data-setup-grades ONLY when a grade for that ticker exists in the
    briefing JSON pools; every other tech card stays unstamped (no
    fabricated grade, rule #19)."""
    r = client_graded_idea.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    techs = re.findall(r'<article class="tech-card[^>]*>', r.text)
    assert techs, "expected tech cards on the fixture briefing"
    nvda = [t for t in techs if 'data-ticker="NVDA"' in t]
    others = [t for t in techs if 'data-ticker="NVDA"' not in t]
    # The Technical Read tab's NVDA card is stamped with the pooled grade
    # tokens. (NVDA also renders embedded tech views inside uc-cards —
    # those stay unstamped because the ENCLOSING uc-card already carries
    # data-setup-grades; stamping both would double-tag one surface.)
    stamped = [t for t in nvda if "data-setup-grades=" in t]
    assert stamped, f"no stamped NVDA tech card in {nvda}"
    attr = re.search(r'data-setup-grades="([^"]*)"', stamped[0]).group(1)
    tokens = attr.split()
    assert "csp_b" in tokens and "b" in tokens  # per-letter pill token too
    assert others and all("data-setup-grades=" not in t for t in others)


# ─── Setups merge keeps the grade ────────────────────────────────────────


def test_setups_merge_carries_grade_fields():
    """The consolidated /setups merge must not drop the grade — WTE's
    grade (fresher entry doc) wins when both sources carry one; a WTE
    card without a grade never erases the candidates grade."""
    from app import setups

    cand = {"ticker": "NVDA", "setup_grade": "A", "setup_grade_score": 92.0,
            "setup_grade_prime": True, "setup_grade_below_floor": False,
            "setup_grade_note": "cand note", "extras": [], "metrics": [],
            "flags": {}}
    wte_graded = {"ticker": "NVDA", "setup_grade": "B",
                  "setup_grade_score": 66.0, "setup_grade_prime": False,
                  "setup_grade_below_floor": False,
                  "setup_grade_note": "wte note", "extras": [],
                  "metrics": [], "flags": {}}
    merged = setups._merge_cards(dict(cand), wte_graded)
    assert merged["setup_grade"] == "B"
    assert merged["setup_grade_prime"] is False  # moves as one block

    wte_ungraded = {"ticker": "NVDA", "setup_grade": None, "extras": [],
                    "metrics": [], "flags": {}}
    merged2 = setups._merge_cards(dict(cand), wte_ungraded)
    assert merged2["setup_grade"] == "A"
    assert merged2["setup_grade_prime"] is True
