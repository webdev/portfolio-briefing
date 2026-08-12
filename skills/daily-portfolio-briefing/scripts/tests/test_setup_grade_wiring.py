"""Setup Grade wiring — every transaction surface renders the grade line,
the spotlight ranks/never-pads/keeps ⏸ tags, and the digest keeps it.

George (2026-08-10): "We need a very clear message as to when I should get
in on every transaction." These tests pin: (a) each wired surface renders
the grade + entry message when the flag is on and stays byte-identical
when off; (b) '## 🏆 Best Setups Today' composition (top-N per side,
RSI-hard-block + yield-floor filtering, deferred tag preserved, never
padded); (c) the digest selects the spotlight (pure-subset contract).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import rsi_discipline, setup_grade as sg  # noqa: E402


CFG_ON = {"setup_grade": {"enabled": True}}
CFG_OFF: dict = {}

_RSI_TH = rsi_discipline.load_thresholds(None)

SNAPSHOT = {
    "technicals": {
        "MU": {
            "rsi_14": 52.0, "iv_rank": 79.0, "sma_200": 59.2,
            "spot": 100.0, "drawdown_pct": 3.0,
            "support_resistance": {"supports": [], "resistances": []},
            "deep": {"long_term_verdict": "uptrend"},
        },
        "JPM": {
            "rsi_14": 42.0, "iv_rank": 70.0, "sma_200": 280.0,
            "spot": 300.0, "drawdown_pct": 8.0,
            "support_resistance": {"supports": [
                {"price": 292.0, "touches": 3, "strength": 4.0}],
                "resistances": []},
            "deep": {"long_term_verdict": "uptrend"},
        },
    },
    "iv_ranks": {"MU": 79.0, "JPM": 70.0},
    "quotes": {"MU": {"last": 100.0, "dayChangePct": -0.012},
               "JPM": {"last": 300.0, "dayChangePct": -0.005}},
    "earnings_calendar": {},
}


# ── grade_for_new_open (the shared snapshot resolver) ────────────────────


def test_grade_for_new_open_pulls_measured_inputs_from_snapshot():
    g = sg.grade_for_new_open("JPM", "csp", snapshot_data=SNAPSHOT,
                              strike=290.0, config=CFG_ON)
    assert g is not None
    assert g["letter"] in ("A", "A-", "B")
    assert any(d.startswith("RVr 70") for d in g["drivers"])
    assert any("support $292" in d for d in g["drivers"])
    assert any(d == "red day ✓" for d in g["drivers"])


def test_grade_for_new_open_rsi_override_wins():
    """A surface that already resolved a LIVE RSI (rule #46) passes it in;
    the stale snapshot value is never used."""
    g = sg.grade_for_new_open("JPM", "csp", snapshot_data=SNAPSHOT,
                              strike=290.0, rsi=75.0, config=CFG_ON)
    assert g["letter"] == "—"          # live RSI hard-blocks


# ── Income Opportunities (new_ideas renderer) ────────────────────────────


def _idea(**kw):
    base = {
        "ticker": "MU", "name": "Micron", "source": "recommendation_list_csp",
        "instruction": "SELL 1x MU $95P", "type": "CSP", "strike": 95.0,
        "expiration": "2026-09-18", "expiration_pretty": "Fri Sep 18 '26",
        "dte": 37, "contracts": 1, "bid": 1.8, "mid": 1.9,
        "collateral": 9500, "premium": 190, "yield_pct": 2.0,
        "annualized_pct": 19.7, "otm_pct": 5.0, "delta": 0.2,
        "open_interest": 500, "spread_pct": 5.0, "iv": 40, "spot": 100.0,
        "raw_recommendation": "Buy", "rec_age_days": 3, "score": 3,
        "rationale": "test", "rsi_14": 52.0, "rsi_tag": "RSI 52",
        "rsi_note": "mid-range",
    }
    base.update(kw)
    return base


def test_income_opportunity_card_renders_grade_line():
    from render.panels import render_opportunities
    g = sg.grade_for_new_open("MU", "csp", snapshot_data=SNAPSHOT,
                              strike=95.0, config=CFG_ON)
    idea = _idea(setup_grade=g["letter"], setup_grade_score=g["score"],
                 setup_grade_message=g["message"],
                 setup_grade_drivers=g["drivers"],
                 setup_grade_line=sg.format_grade_note(g))
    md = "\n".join(render_opportunities([idea]))
    assert "**Setup Grade:" in md
    assert "🏁 Entry:" in md


def test_income_opportunity_without_grade_is_byte_identical():
    from render.panels import render_opportunities
    md = "\n".join(render_opportunities([_idea()]))
    assert "Setup Grade" not in md


def test_rsi_wait_income_ticket_carries_grade_line():
    from render.panels import render_opportunities
    idea = _idea(rsi_wait=True, rsi_wait_reason="⏸ extended — wait",
                 setup_grade="C", setup_grade_line="**Setup Grade: C** — 🏁 Entry: C — wait")
    md = "\n".join(render_opportunities([idea]))
    assert "wait for a pullback" in md
    assert "**Setup Grade: C**" in md


# ── Candidate cards (candidate_research._format_card) ────────────────────


def _scout_result(**kw):
    base = {
        "ticker": "JPM", "spot": 300.0, "verdict": "CSP ENTRY",
        "rsi_14": 42.0, "iv_rank": 70.0, "drawdown_pct": 8.0,
        "sma_200": 280.0, "fivedayret_pct": -1.0,
        "third_party_rec": "BUY", "rating_tier": 3, "rationale": [],
        "days_to_earnings": 42, "earnings_date": "2026-09-23",
        "support_resistance": {"supports": [
            {"price": 292.0, "touches": 3, "strength": 4.0}],
            "resistances": []},
        "csp_entry": {"strike": 290, "expiration": "2026-10-16",
                      "dte": 65, "mid": 4.0, "bid": 3.9, "ask": 4.1},
    }
    base.update(kw)
    return base


def test_candidate_card_renders_grade_and_message():
    from steps.candidate_research import _format_card
    card = "\n".join(_format_card(_scout_result(), {}, set(), _RSI_TH,
                                  config=CFG_ON))
    assert "**Setup Grade:" in card
    assert "🏁 Entry:" in card
    assert "RVr 70" in card            # RV proxy labeled honestly


def test_candidate_card_flag_off_is_byte_identical():
    from steps.candidate_research import _format_card
    on = _format_card(_scout_result(), {}, set(), _RSI_TH, config=CFG_OFF)
    legacy = _format_card(_scout_result(), {}, set(), _RSI_TH, config=None)
    assert on == legacy
    assert not any("Setup Grade" in ln for ln in on)


def test_candidate_card_wait_status_still_graded():
    """The WAIT names need the entry message most — held-by-RSI cards
    still carry the grade ('—' when hard-blocked)."""
    from steps.candidate_research import _format_card
    r = _scout_result(rsi_14=72.0)
    card = "\n".join(_format_card(r, {}, set(), _RSI_TH, config=CFG_ON))
    assert "**Setup Grade: —**" in card
    assert "blocked" in card


# ── When-To-Enter cards ──────────────────────────────────────────────────


def _wte_payload(results):
    return {
        "generated_at_iso": "2026-08-12T08:00:00",
        "themes": {"semis": {"name": "Semis", "group": "AI Buildout",
                             "anchors": [], "etfs": []}},
        "results_by_theme": {"semis": results},
    }


def test_when_to_enter_card_carries_grade_both_statuses():
    from steps.when_to_enter import render_when_to_enter_report
    enter = _scout_result()                      # RSI 42 → ENTRY-side
    wait = _scout_result(ticker="MU", rsi_14=65.0, spot=100.0,
                         sma_200=59.2, csp_entry=None)
    md = render_when_to_enter_report(
        _wte_payload([enter, wait]), config=CFG_ON, generated_at="test")
    assert md.count("**Setup Grade:") >= 2
    assert "🏁 Entry:" in md


def test_when_to_enter_flag_off_has_no_grade():
    from steps.when_to_enter import render_when_to_enter_report
    md = render_when_to_enter_report(
        _wte_payload([_scout_result()]), config=CFG_OFF, generated_at="t")
    assert "Setup Grade" not in md


# ── Strategy Upgrades renderer (CC side) ─────────────────────────────────


def _cc_upgrade(**kw):
    base = {
        "type": "write_covered_call", "underlying": "MU", "tier": "C",
        "tier_violations": [], "tier_envelope_wait": False,
        "shares_held": 200, "contracts_writable": 2,
        "current_price": 100.0, "target_strike": 108.0, "target_dte": 35,
        "target_delta": 0.24, "otm_pct": 8.0, "strike_selected_by": "delta",
        "sr_anchor": None, "est_premium_per_share": 1.5,
        "est_premium_total": 300, "est_annualized_pct": 15.6,
        "bid": 1.4, "ask": 1.6, "chain_source": "etrade_live",
        "current_weight_pct": 2.0, "earnings_blocked": False,
        "earnings_date": None, "rsi_14": 66.0, "rsi_tag": "RSI 66",
        "rsi_note": "extended", "rsi_decision": "promote",
        "rsi_badge": "✅ RSI favourable", "rsi_blocked": False,
        "rsi_wait": False, "lt_secular_wait": False, "lt_secular_note": None,
        "rationale": "test",
    }
    base.update(kw)
    return base


def test_cc_write_card_renders_grade_line():
    from render.strategy_upgrades_panel import render_strategy_upgrades
    up = _cc_upgrade(setup_grade="B", setup_grade_score=70.0,
                     setup_grade_message="🏁 Entry: B — good setup",
                     setup_grade_line="**Setup Grade: B** (70/100) — 🏁 Entry: B — good setup")
    md = "\n".join(render_strategy_upgrades([up]))
    assert "**Setup Grade: B**" in md


def test_index_cc_card_renders_grade_line():
    from render.strategy_upgrades_panel import render_strategy_upgrades
    up = {
        "type": "index_covered_call", "underlying": "SPY", "tier": "A",
        "writable": True, "actionable": True, "shares_held": 100,
        "contracts_in_account": 1, "contracts_writable": 1,
        "current_price": 640.0, "current_weight_pct": 5.0,
        "index_rsi_favored": 55.0, "index_rsi_block_below": 40.0,
        "rsi_14": 62.0, "rsi_tag": "RSI 62", "rsi_note": "extended",
        "rsi_decision": "promote", "rsi_badge": "✅", "index_rsi_state": "favored",
        "envelope_violations": [], "chain_source": "etrade_live",
        "target_strike": 660.0, "expiration": "2026-09-18", "exp_kind": None,
        "target_dte": 37, "target_delta": 0.18, "otm_pct": 3.1,
        "strike_selected_by": "delta", "sr_anchor": None,
        "est_premium_per_share": 4.0, "est_premium_total": 400,
        "est_annualized_pct": 6.2, "bid": 3.9, "ask": 4.1,
        "rationale": "index cc",
        "setup_grade": "C", "setup_grade_line": "**Setup Grade: C** — 🏁 Entry: C — wait",
    }
    md = "\n".join(render_strategy_upgrades([up]))
    assert "**Setup Grade: C**" in md


def test_cc_card_without_grade_is_byte_identical():
    from render.strategy_upgrades_panel import render_strategy_upgrades
    md = "\n".join(render_strategy_upgrades([_cc_upgrade()]))
    assert "Setup Grade" not in md


# ── Rotation Playbook conviction cell ────────────────────────────────────


def test_playbook_conviction_cell_leads_with_grade():
    from render.rotation_playbook_panel import _conviction_cell
    cell = _conviction_cell({"conviction_score": 7, "rsi": 45,
                             "rsi_verified": True, "parkev_rating": "BUY",
                             "setup_grade": "B"})
    assert cell.startswith("🏁 B")
    assert "RSI 45✓" in cell


def test_playbook_conviction_cell_ungraded_unchanged():
    from render.rotation_playbook_panel import _conviction_cell
    cell = _conviction_cell({"conviction_score": 7, "rsi": 45,
                             "rsi_verified": True, "parkev_rating": "BUY"})
    assert "🏁" not in cell


def test_playbook_candidate_to_dict_carries_grade_fields():
    from analysis.rotation_playbook import ConvictionScoredCandidate
    cand = ConvictionScoredCandidate(
        ticker="MU", strike=95.0, expiration="2026-09-18", dte=37,
        collateral_required=9500.0, premium=190.0,
        annualized_yield_pct=19.7, mid_price=1.9, parkev_rating="BUY",
        parkev_tier=3, parkev_conviction="High", parkev_age_days=3,
        conviction_score=7.0, setup_grade="B", setup_grade_score=70.0,
        setup_grade_message="🏁 Entry: B — good setup")
    d = cand.to_dict()
    assert d["setup_grade"] == "B"
    assert d["setup_grade_score"] == 70.0


# ── LT_CSP trigger note (advisor-dataclass-safe carrier) ────────────────


def test_lto_fallback_renderer_prints_grade_from_triggers():
    from steps.long_term_opportunities import _lto_card_lines
    op = {"kind": "LONG_DATED_CSP", "ticker": "JPM",
          "concrete_trade": "SELL 1× JPM $290P exp Fri Oct 16 '26",
          "trigger_reasons": [
              "✅ RSI favourable · RSI 42 🟢 pullback",
              "**Setup Grade: B** (72/100) · RSI 42 prime — 🏁 Entry: B"],
          "rationale": "r", "yield_or_cost": "y", "source": "s"}
    md = "\n".join(_lto_card_lines(op, 1, None, None))
    assert "**Setup Grade: B**" in md


# ── 🏆 Best Setups Today spotlight ────────────────────────────────────────


def _graded_idea(ticker, letter, score, ann, **kw):
    g_msg = f"🏁 Entry: {letter} — test"
    idea = _idea(ticker=ticker, annualized_pct=ann, setup_grade=letter,
                 setup_grade_score=score, setup_grade_message=g_msg,
                 setup_grade_drivers=["RSI 41 prime", "RVr 78 ✓"])
    idea.update(kw)
    return idea


def test_spotlight_ranks_by_score_and_takes_top_n():
    ideas = [
        _graded_idea("AAA", "B", 66.0, 18.0),
        _graded_idea("BBB", "A", 88.0, 20.0),
        _graded_idea("CCC", "A-", 80.0, 16.0),
        _graded_idea("DDD", "C", 55.0, 15.0),
    ]
    best = sg.collect_best_setups(new_ideas=ideas, config=CFG_ON)
    tickers = [e["ticker"] for e in best["csp"]]
    assert tickers == ["BBB", "CCC", "AAA"]     # top 3 by score, DDD out


def test_spotlight_filters_rsi_hard_block_and_yield_floor():
    ideas = [
        _graded_idea("OKY", "B", 70.0, 18.0),
        _graded_idea("BLK", "—", 0.0, 25.0),      # RSI hard block → out
        _graded_idea("THN", "A", 90.0, 8.0),      # below 12% floor → out
    ]
    best = sg.collect_best_setups(new_ideas=ideas, config=CFG_ON)
    assert [e["ticker"] for e in best["csp"]] == ["OKY"]


def test_spotlight_excludes_extended_band_puts():
    """Rule #43/#14 consistency (the CRWV 2026-08-10 replay case: RSI 64,
    RVr 100 graded B) — a put the briefing itself demotes to '⏸ wait for
    a pullback' (RSI 60-70 extended band, or rsi_wait on the idea) never
    green-lights in the spotlight, no matter its composite score."""
    ideas = [
        _graded_idea("CRWV", "B", 68.0, 60.0, rsi_14=64.0),
        _graded_idea("WTD", "B", 70.0, 18.0, rsi_wait=True),
        _graded_idea("OKY", "B", 66.0, 18.0, rsi_14=44.0),
    ]
    best = sg.collect_best_setups(new_ideas=ideas, config=CFG_ON)
    assert [e["ticker"] for e in best["csp"]] == ["OKY"]


def test_spotlight_never_pads():
    best = sg.collect_best_setups(
        new_ideas=[_graded_idea("ONE", "B", 70.0, 18.0)], config=CFG_ON)
    assert len(best["csp"]) == 1
    md = "\n".join(sg.render_best_setups(best, config=CFG_ON))
    assert md.count("`ONE`") == 1
    assert "_none qualify today_" in md          # CC side shows what exists: none


def test_spotlight_keeps_deferred_tag_verbatim():
    tag = ("⏸ Deferred (capacity gated) — stress coverage 0.22× < 0.50× "
           "floor; shown for planning, not a green light (rule #41)")
    ideas = [_graded_idea("GTD", "B", 70.0, 18.0, capacity_blocked=True)]
    best = sg.collect_best_setups(new_ideas=ideas, capacity_tag=tag,
                                  config=CFG_ON)
    assert best["csp"][0]["deferred_tag"] == tag
    md = "\n".join(sg.render_best_setups(best, config=CFG_ON))
    assert tag in md


def test_spotlight_cc_side_from_strategy_upgrades():
    ups = [_cc_upgrade(setup_grade="B", setup_grade_score=70.0,
                       setup_grade_message="🏁 Entry: B — good setup",
                       setup_grade_drivers=["RSI 66 building"])]
    best = sg.collect_best_setups(strategy_upgrades=ups, config=CFG_ON)
    assert [e["ticker"] for e in best["cc"]] == ["MU"]
    assert best["cc"][0]["letter"] == "B"


def test_spotlight_excludes_earnings_blocked_cc():
    """Hard gates stand — an earnings-blocked write never spotlights."""
    ups = [_cc_upgrade(earnings_blocked=True, setup_grade="A",
                       setup_grade_score=90.0)]
    best = sg.collect_best_setups(strategy_upgrades=ups, config=CFG_ON)
    assert best["cc"] == []


def test_spotlight_scout_candidates_graded_inline():
    r = _scout_result()   # RSI 42, RVr 70, support at 292, ann ≈ 7.7%…
    # bump the mid so the delivered yield clears the 12% floor
    r["csp_entry"]["mid"] = 9.3
    best = sg.collect_best_setups(scout_results=[r],
                                  snapshot_data=SNAPSHOT, config=CFG_ON)
    assert [e["ticker"] for e in best["csp"]] == ["JPM"]
    assert best["csp"][0]["annualized_pct"] >= 12.0


def test_spotlight_yield_floor_derives_from_config():
    ideas = [_graded_idea("OKY", "B", 70.0, 18.0)]
    cfg = dict(CFG_ON)
    cfg["rotation_playbook"] = {"playbook_min_annualized_yield": 0.25}
    best = sg.collect_best_setups(new_ideas=ideas, config=cfg)
    assert best["csp"] == []                     # 18% < 25% floor
    assert best["yield_floor_pct"] == 25.0


def test_render_best_setups_one_line_per_entry():
    best = sg.collect_best_setups(
        new_ideas=[_graded_idea("MU", "B", 70.0, 18.0)], config=CFG_ON)
    lines = sg.render_best_setups(best, config=CFG_ON)
    assert lines[0] == "## 🏆 Best Setups Today"
    entry_lines = [ln for ln in lines if ln.startswith("- **")]
    assert len(entry_lines) == 1
    ln = entry_lines[0]
    assert "**B** (70)" in ln and "`MU`" in ln and "18% ann" in ln
    assert "🏁 Entry: B" in ln
    assert "RSI 41 prime" in ln                  # top-2 drivers on the line


# ── Digest keeps the spotlight ───────────────────────────────────────────


def test_digest_keeps_best_setups_after_action_list():
    from render.digest import build_digest
    full = "\n".join([
        "# Daily Briefing — 2026-08-12", "",
        "## Today's Action List", "", "1. **CLOSE** MU $95P — take profit", "",
        "## 🏆 Best Setups Today", "",
        "- **B** (70) `MU` — SELL $95P Fri Sep 18 '26 · 18% ann — 🏁 Entry: B", "",
        "## Watch", "", "- stuff", "",
        "## Red Flags & Priorities", "", "- none", "",
    ])
    digest = build_digest(full, config={"render": {"digest": True}},
                          extras={"date": "2026-08-12"})
    assert "## 🏆 Best Setups Today" in digest
    a = digest.index("Today's Action List")
    b = digest.index("Best Setups Today")
    c = digest.index("Red Flags")
    assert a < b < c


def test_digest_without_spotlight_unchanged():
    from render.digest import build_digest
    full = "\n".join([
        "# Daily Briefing — 2026-08-12", "",
        "## Today's Action List", "", "1. item", "",
        "## Red Flags & Priorities", "", "- none", "",
    ])
    digest = build_digest(full, config={"render": {"digest": True}},
                          extras={"date": "2026-08-12"})
    assert "Best Setups" not in digest
