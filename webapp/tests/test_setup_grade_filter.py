"""Setup Grade filter + badge — drift guard, tokens, route smoke, JS contract.

George (2026-08-10): "We need a very clear message as to when I should get
in on every transaction." (2026-08-17: the grouped 'CSP setup A/B' chips
became per-letter pills — see test_grade_letter_pills.py.) The webapp
surfaces the pipeline's side-aware Setup Grade as card badges plus a
filter bar next to the RSI zone bar. These tests pin: (a) the webapp NEVER
redefines grading — letters come from the pipeline via the config bridge;
(b) token/badge derivation from briefing-JSON shapes (new_ideas fields,
strategy-upgrade fields, LTO trigger-reason notes); (c) the rendered pages
carry the filter bar + data attributes; (d) the served app.js actually
contains the grade filter engine (the stale-JS lesson — cache-bust guard).
"""

import re

from app import setup_grade_ui

DATE = "2026-06-30"


# ── Drift guard — webapp letters == pipeline letters ─────────────────────


def test_letters_match_pipeline_setup_grade():
    """The webapp NEVER redefines grade floors — letters() must equal the
    values the pipeline's setup_grade resolves from briefing.yaml."""
    from app.config import briefing_config
    from analysis import setup_grade as sg  # pipeline module (config bridge)

    cfg = sg.load_setup_grade_config(briefing_config())
    assert setup_grade_ui.letters() == {
        k: float(v) for k, v in cfg["letters"].items()}


def test_pipeline_letter_floors_are_pinned():
    """A≥85, A-≥78, B≥65, C≥50 — the UI chip titles and this suite's
    expectations assume these. If the pipeline changes them, this fails
    loudly so the grade semantics get revisited together."""
    from analysis import setup_grade as sg
    assert sg.DEFAULTS["letters"] == {
        "a": 85.0, "a_minus": 78.0, "b": 65.0, "c": 50.0}


def test_good_letters_match_pipeline_grade_vocabulary():
    from analysis import setup_grade as sg
    # every GOOD letter must be producible by the pipeline's letter_for
    for letter in setup_grade_ui.GOOD_LETTERS:
        assert letter in ("A", "A-", "B")
    assert sg.letter_for(90.0) in setup_grade_ui.GOOD_LETTERS
    assert sg.letter_for(40.0) not in setup_grade_ui.GOOD_LETTERS


# ── Token / badge derivation ─────────────────────────────────────────────


def test_tokens_csp_idea_with_grade():
    base = {"kind": "NEW_CSP", "raw": {"setup_grade": "B",
                                       "setup_grade_score": 70.0}}
    tokens = setup_grade_ui.grade_tokens(base).split()
    assert "csp_b" in tokens and "csp_good" in tokens


def test_tokens_cc_strategy_upgrade():
    base = {"type": "write_covered_call", "setup_grade": "A"}
    tokens = setup_grade_ui.grade_tokens(base).split()
    assert "cc_a" in tokens and "cc_good" in tokens


def test_tokens_lto_parsed_from_trigger_reasons():
    """LTO ops carry the grade INSIDE trigger_reasons (advisor-dataclass
    compatibility on the pipeline side) — the UI parses it back out."""
    base = {"kind": "LONG_DATED_CSP", "raw": {"trigger_reasons": [
        "✅ RSI favourable",
        "**Setup Grade: A-** (80/100) · RSI 41 prime — 🏁 Entry: A-"]}}
    tokens = setup_grade_ui.grade_tokens(base).split()
    assert "csp_a_minus" in tokens and "csp_good" in tokens


def test_tokens_c_and_blocked_are_not_good():
    assert "csp_good" not in setup_grade_ui.grade_tokens(
        {"kind": "NEW_CSP", "raw": {"setup_grade": "C"}})
    assert "csp_good" not in setup_grade_ui.grade_tokens(
        {"kind": "NEW_CSP", "raw": {"setup_grade": "—"}})


def test_tokens_ungraded_is_none():
    assert setup_grade_ui.grade_tokens({"kind": "NEW_CSP", "raw": {}}) == "none"
    assert setup_grade_ui.grade_tokens(None) == "none"


def test_badge_carries_drivers_and_message_in_tooltip():
    base = {"kind": "NEW_CSP", "raw": {
        "setup_grade": "B",
        "setup_grade_drivers": ["RSI 41 prime", "RVr 78 ✓"],
        "setup_grade_message": "🏁 Entry: B — good setup; enter per plan."}}
    b = setup_grade_ui.grade_badge(base)
    assert b is not None
    assert b["label"] == "🏁 CSP setup B"
    assert "RSI 41 prime" in b["title"]
    assert "🏁 Entry: B" in b["title"]
    assert b["rep_tone"] == "green"


def test_badge_none_when_ungraded():
    assert setup_grade_ui.grade_badge({"kind": "NEW_CSP", "raw": {}}) is None


def test_badge_carries_prime_token_when_pipeline_flags_it():
    """George (2026-08-14): "we should clearly see all of the good entries
    based on this algorithm" — a card the pipeline marks
    ``setup_grade_prime`` (the card-strict conjunction: RSI 35-45, chain
    IVr ≥ 60, ≥2-touch support under strike, trend intact, red day +
    earnings clear) carries the 💎 PRIME token next to the grade. The flag
    flows through the existing JSON fields — the webapp never re-derives
    it (rule #19)."""
    base = {"kind": "NEW_CSP", "raw": {
        "setup_grade": "A", "setup_grade_score": 90.0,
        "setup_grade_prime": True}}
    b = setup_grade_ui.grade_badge(base)
    assert b["label"] == "🏁 CSP setup A · 💎 PRIME"


def test_badge_no_prime_token_when_not_flagged():
    base = {"kind": "NEW_CSP", "raw": {
        "setup_grade": "B", "setup_grade_score": 70.0,
        "setup_grade_prime": False}}
    assert "💎" not in setup_grade_ui.grade_badge(base)["label"]


def test_badge_hard_block_is_red():
    b = setup_grade_ui.grade_badge(
        {"type": "write_covered_call", "setup_grade": "—"})
    assert b["rep_tone"] == "red"
    assert "CC setup —" in b["label"]


def test_filter_chips_are_per_letter_pills():
    """George (2026-08-17): "I need to have a filter little pill that I
    can click on so I can see A, B, C, D." The grouped 'CSP setup A/B'
    chips are replaced by per-letter pills (All · 💎 Prime · A · B · C ·
    D · ⛔ Blocked · Ungraded); the tooltips explain the reading guide
    and carry the config-derived letter floors."""
    chips = setup_grade_ui.grade_filter_chips()
    grades = [c["grade"] for c in chips]
    assert grades == ["all", "prime", "a", "b", "c", "d", "blocked", "none"]
    labels = " ".join(c["label"] for c in chips)
    assert "💎 Prime" in labels and "⛔ Blocked" in labels
    assert "Ungraded" in labels
    # titles carry the config-derived B floor, never a hardcoded number
    b_floor = setup_grade_ui.letters()["b"]
    assert any(f"{b_floor:.0f}/100" in c["title"] for c in chips)
    # tooltips explain the guide: A/B = enter quality · C/D = wait
    titles = " ".join(c["title"] for c in chips)
    assert "A/B = enter quality" in titles and "C/D = wait" in titles


# ── Route smoke — the briefing page renders the bar + attributes ─────────


def test_briefing_page_renders_grade_filter_bar_with_counts(client):
    """George (2026-08-17): per-letter pills [A] [B] [C] [D] replace the
    grouped A/B chips on the briefing's Ideas + Strategy filter bars."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'class="uc-sort grade-filter-bar"' in body
    assert 'data-grade-cards=".uc-card"' in body
    for grade in ("prime", "a", "b", "c", "d", "blocked", "none"):
        assert f'data-grade="{grade}"' in body, f"missing {grade} pill"
    assert '<span class="grade-count">' in body


def test_every_uc_card_carries_data_setup_grades(client):
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    cards = re.findall(r'<article class="uc-card[^>]*>', r.text)
    assert cards, "expected uc-cards on the fixture briefing page"
    assert all("data-setup-grades=" in c for c in cards)


def test_no_raw_grade_tokens_in_visible_text(client):
    """Rule #31 — machine identifiers never leak as page text."""
    body = client.get(f"/briefing/{DATE}").text
    for ident in ("csp_good", "cc_good", "csp_a_minus", "cc_blocked"):
        assert f">{ident}<" not in body, f"{ident} leaked as text"


# ── Template–asset contract (the stale-JS lesson) ────────────────────────


def test_static_app_js_contains_grade_filter_engine(client):
    """Fresh HTML (grade chips) + stale cached app.js was the 2026-08-10
    RSI-filter bug; assert the SERVED JS carries the grade engine so the
    contract can't silently split again."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    body = r.text
    assert "data-setup-grades" in body
    assert "grade-filter-chip" in body
    assert "gradeFilter:" in body
    assert "sg-hidden" in body          # class-based hide → composes with RSI bar


def test_app_css_has_sg_hidden_rule(client):
    r = client.get("/static/app.css")
    assert r.status_code == 200
    assert ".sg-hidden" in r.text
    assert ".grade-filter-bar" in r.text
