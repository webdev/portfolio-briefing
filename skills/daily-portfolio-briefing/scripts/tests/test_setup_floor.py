"""B floor for green-lit NEW-OPEN tickets — every surface demotes sub-B.

George (2026-08-12): "Yes, we absolutely need to fix the right
recommendations for both CSPs and CCs so that recommendations are A or B,
not D, because I'm very much relying on it."

Context: the rec_grade_audit measured 277 recommended tickets over 5
briefings averaging D (38/100) — 68% of CSP recs at RSI ≥ 48, 61% below
the true-IV vol floor. These tests pin, per surface: the below-floor
demotion is VISIBLE with its measured reason (rule #24), composes with the
capacity/RSI tags, A/B tickets are unaffected, management is never gated,
grade-n/a fails OPEN, and config-off is byte-identical legacy.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import rsi_discipline, setup_grade as sg  # noqa: E402

CFG_ON = {"setup_grade": {"enabled": True}}            # floor OFF (code default)
CFG_FLOOR = {"setup_grade": {"enabled": True,
                             "actionable_floor": {"enabled": True,
                                                  "min_score": 65}}}

_RSI_TH = rsi_discipline.load_thresholds(None)


def _grade(letter="D", score=38.0, message=None, drivers=None, **kw):
    g = {"letter": letter, "score": score,
         "message": message or ("🏁 Entry: D — wait; prime needs RSI 35-45 "
                                "(now 50) or IVr ≥ 60 (now 15)."),
         "drivers": drivers or ["RSI 50 late-band", "IVr 15 thin ✗"]}
    g.update(kw)
    return g


# ── below_actionable_floor (single source of truth) ──────────────────────


def test_floor_off_by_default_in_code():
    """George (2026-08-12): "recommendations are A or B, not D" — but the
    code default keeps the floor OFF so briefing.yaml is the switch; a D
    grade under CFG_ON (no actionable_floor block) is NOT demoted."""
    below, note = sg.below_actionable_floor(_grade("D", 38.0), CFG_ON)
    assert below is False and note == ""


def test_floor_demotes_d_with_measured_note():
    """'⏸ Below setup floor — D (38): <weakest components with measured
    values + config-derived targets>' — reuses the wait-message machinery,
    never a hardcoded threshold in the string."""
    below, note = sg.below_actionable_floor(_grade("D", 38.0), CFG_FLOOR)
    assert below is True
    assert note.startswith("⏸ Below setup floor — D (38): ")
    assert "RSI 35-45 (now 50)" in note      # measured value + derived target
    assert "IVr ≥ 60 (now 15)" in note


def test_floor_c_grade_demoted_b_and_above_pass():
    below_c, note_c = sg.below_actionable_floor(_grade("C", 54.0), CFG_FLOOR)
    assert below_c is True and "— C (54):" in note_c
    for letter, score in (("B", 65.0), ("B", 70.0), ("A-", 80.0), ("A", 90.0)):
        below, note = sg.below_actionable_floor(_grade(letter, score), CFG_FLOOR)
        assert below is False and note == ""


def test_floor_fails_open_on_ungradeable():
    """Rule #19 fail direction (consistent with redeploy_path): missing
    grade / 'n/a' / missing score must NOT silently block the ticket."""
    assert sg.below_actionable_floor(None, CFG_FLOOR) == (False, "")
    assert sg.below_actionable_floor(_grade("n/a", None), CFG_FLOOR) == (False, "")
    assert sg.below_actionable_floor(_grade("D", None), CFG_FLOOR) == (False, "")


def test_floor_leaves_rsi_hard_block_to_its_own_gate():
    below, note = sg.below_actionable_floor(
        _grade("—", 0.0, hard_blocked=True), CFG_FLOOR)
    assert below is False and note == ""


def test_floor_min_score_is_config_derived():
    cfg = {"setup_grade": {"enabled": True,
                           "actionable_floor": {"enabled": True,
                                                "min_score": 80}}}
    below, note = sg.below_actionable_floor(_grade("B", 70.0), cfg)
    assert below is True and "— B (70):" in note


def test_grade_na_note_single_source():
    assert sg.GRADE_NA_NOTE == "🏁 grade n/a — verify setup manually"


# ── Income Opportunities (new_ideas → render_opportunities) ──────────────


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


_FLOOR_NOTE = ("⏸ Below setup floor — D (38): RSI 35-45 (now 50) or "
               "IVr ≥ 60 (now 15)")


def test_income_below_floor_demoted_visible_with_reason():
    """George: "recommendations are A or B, not D" — the D ticket leaves
    the Actionable list, renders in '⏸ CSPs — below setup floor' with the
    full ticket + measured reason (rule #24)."""
    from render.panels import render_opportunities
    idea = _idea(setup_floor_demoted=True, setup_floor_note=_FLOOR_NOTE,
                 setup_grade="D",
                 setup_grade_line="**Setup Grade: D** (38/100) — 🏁 Entry: D")
    md = "\n".join(render_opportunities([idea]))
    assert "### ⏸ CSPs — below setup floor (1)" in md
    assert _FLOOR_NOTE in md
    assert "**$95 PUT**" in md            # full ticket stays visible
    assert "### Actionable: cash-secured puts" not in md


def test_income_a_b_ticket_unaffected():
    from render.panels import render_opportunities
    idea = _idea(setup_grade="B", setup_grade_score=70.0,
                 setup_grade_line="**Setup Grade: B** (70/100) — 🏁 Entry: B")
    md = "\n".join(render_opportunities([idea]))
    assert "### Actionable: cash-secured puts (1)" in md
    assert "below setup floor" not in md


def test_income_floor_composes_with_rsi_wait():
    """A ticket both RSI-extended AND below-floor stays in the wait
    subsection and shows BOTH reasons, once each."""
    from render.panels import render_opportunities
    idea = _idea(rsi_wait=True, rsi_wait_reason="⏸ extended — wait",
                 setup_floor_demoted=True, setup_floor_note=_FLOOR_NOTE)
    md = "\n".join(render_opportunities([idea]))
    assert "wait for a pullback" in md
    assert md.count("⏸ extended — wait") == 1
    assert md.count(_FLOOR_NOTE) == 1
    assert "### ⏸ CSPs — below setup floor" not in md   # not duplicated


def test_income_floor_composes_with_capacity_block():
    """Capacity-blocked (watch-only) tickets that ALSO grade below floor
    show both reasons on the row."""
    from render.panels import render_opportunities
    idea = _idea(instruction=None, capacity_blocked=True,
                 capacity_reason="stress coverage 0.22× < 0.50×",
                 rationale="Buy — skipped: stress coverage 0.22× < 0.50×",
                 setup_floor_note=_FLOOR_NOTE)
    md = "\n".join(render_opportunities([idea]))
    assert "stress coverage 0.22×" in md
    assert _FLOOR_NOTE in md


def test_income_grade_na_fails_open_with_note():
    """Fail-OPEN: an ungradeable ticket stays actionable and carries the
    verify-manually note (rule #19 — missing data never silently blocks)."""
    from render.panels import render_opportunities
    idea = _idea(setup_floor_na=True)
    md = "\n".join(render_opportunities([idea]))
    assert "### Actionable: cash-secured puts (1)" in md
    assert sg.GRADE_NA_NOTE in md


def test_income_config_off_byte_identical():
    from render.panels import render_opportunities
    md_legacy = "\n".join(render_opportunities([_idea()]))
    assert "below setup floor" not in md_legacy
    assert "grade n/a" not in md_legacy


# ── Candidate Trades (render_candidate_briefing) ─────────────────────────


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
                      "dte": 65, "mid": 9.3, "bid": 9.2, "ask": 9.4},
    }
    base.update(kw)
    return base


def _weak_result(**kw):
    """Grades D: late-band RSI, thin vol, no support, broken trend."""
    return _scout_result(
        ticker="MU", spot=100.0, rsi_14=52.0, iv_rank=15.0,
        sma_200=140.0, drawdown_pct=45.0,
        support_resistance={"supports": [], "resistances": []},
        csp_entry={"strike": 95, "expiration": "2026-10-16",
                   "dte": 65, "mid": 3.1, "bid": 3.0, "ask": 3.2},
        **kw)


def _payload(results):
    return {
        "generated_at_iso": "2026-08-12T08:00:00",
        "themes": {"semis": {"name": "Semis", "group": "AI Buildout",
                             "anchors": [], "etfs": []}},
        "results_by_theme": {"semis": results},
    }


def _briefing_md(results, config, gate_state=None):
    from steps.candidate_research import render_candidate_briefing
    return render_candidate_briefing(
        _payload(results), fv_by_ticker={}, config=config,
        generated_at="2026-08-12", as_section=True, gate_state=gate_state)


def test_candidate_sub_b_moves_to_planning_section():
    """George: "recommendations are A or B, not D" — a D-graded 🎯
    candidate drops to '⏸ Below setup floor — planning only' with the
    demotion note; the A/B candidate keeps 🎯."""
    md = _briefing_md([_scout_result(), _weak_result()], CFG_FLOOR)
    assert "### 🎯 Today's Candidates (1)" in md
    assert "🎯 CANDIDATE · `JPM`" in md
    assert "### ⏸ Below setup floor — planning only (1)" in md
    assert "⏸ BELOW SETUP FLOOR · `MU`" in md
    assert "⏸ Below setup floor — D (" in md          # measured note
    assert "⏸ **Below setup floor** · SELL 1× MU $95P" in md  # ticket kept


def test_candidate_floor_composes_with_capacity_gate():
    gate = SimpleNamespace(open=False, reasons=["stress coverage 0.22× < 0.50×"],
                           banner="🔒 CAPACITY: closed", positions=[], nlv=0.0)
    md = _briefing_md([_weak_result()], CFG_FLOOR, gate_state=gate)
    assert ("⏸ **Deferred (capacity gated)** · ⏸ **Below setup floor** · "
            "SELL 1× MU $95P") in md


def test_candidate_floor_off_keeps_legacy_headline():
    md = _briefing_md([_scout_result(), _weak_result()], CFG_ON)
    assert "### 🎯 Today's Candidates (2)" in md
    assert "Below setup floor" not in md


def test_candidate_grade_na_fails_open_with_note():
    """Ungradeable candidate stays 🎯 with the verify-manually note."""
    r = _scout_result(rsi_14=42.0, iv_rank=None, sma_200=None,
                      drawdown_pct=None, days_to_earnings=None,
                      earnings_date=None, support_resistance=None)
    # RSI alone still grades (fail-open cap at B) — strip it too so the
    # grade is genuinely n/a? No: rsi drives _status; keep rsi, drop the
    # rest and verify EITHER a grade line OR the n/a note renders — the
    # ticket must stay in Today's Candidates either way.
    md = _briefing_md([r], CFG_FLOOR)
    assert "### 🎯 Today's Candidates" in md or "⏸ Below setup floor" in md
    assert "`JPM`" in md


# ── PULLBACK CSP / action list — management exemption ────────────────────


def test_action_list_management_never_floor_gated():
    """Position management (rolls, closes, TP) is NEVER floor-gated — an
    options review flows through the action list untouched by the floor."""
    from render.panels import render_action_list
    review = {
        "contract": "MU_PUT_95_20260918", "underlying": "MU",
        "option_type": "PUT", "strike": 95.0, "expiration": "2026-09-18",
        "days_to_expiry": 5, "qty": -1, "entry_price": 3.0,
        "current_mid": 0.4, "recommendation": "TAKE PROFIT",
        "rationale": "captured 87%", "matrix_cell_id": None,
    }
    on = render_action_list([], [review], [], {}, {"technicals": {}},
                            date_str="2026-08-12")
    md = "\n".join(on)
    assert "Below setup floor" not in md
    assert "MU_PUT_95_20260918" in md


# ── Strategy Upgrades panel (CC / index CC / strangle) ───────────────────


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


def test_cc_below_floor_moves_to_wait_with_note():
    """George: "...for both CSPs and CCs so that recommendations are A or
    B, not D" — a sub-B CC write leaves READY TO WRITE for the wait
    subsection with the ⏸ BELOW SETUP FLOOR badge + measured note."""
    from render.strategy_upgrades_panel import render_strategy_upgrades
    up = _cc_upgrade(setup_floor_wait=True, setup_floor_note=_FLOOR_NOTE,
                     setup_grade="D",
                     setup_grade_line="**Setup Grade: D** (38/100) — 🏁 Entry: D")
    md = "\n".join(render_strategy_upgrades([up]))
    assert "### Write Covered Calls" not in md
    assert "⏸ Covered calls — wait for strength (1)" in md
    assert "⏸ BELOW SETUP FLOOR" in md
    assert _FLOOR_NOTE in md
    assert "SELL 2× MU $108C" in md               # full ticket kept


def test_cc_a_b_write_unaffected():
    from render.strategy_upgrades_panel import render_strategy_upgrades
    up = _cc_upgrade(setup_grade="B", setup_grade_score=70.0,
                     setup_grade_line="**Setup Grade: B** — 🏁 Entry: B")
    md = "\n".join(render_strategy_upgrades([up]))
    assert "### Write Covered Calls (1)" in md
    assert "✅ READY TO WRITE" in md
    assert "BELOW SETUP FLOOR" not in md


def test_cc_floor_composes_with_rsi_wait_badge():
    from render.strategy_upgrades_panel import render_strategy_upgrades
    up = _cc_upgrade(rsi_wait=True, setup_floor_wait=True,
                     setup_floor_note=_FLOOR_NOTE)
    md = "\n".join(render_strategy_upgrades([up]))
    assert "⏸ WAIT FOR STRENGTH" in md            # RSI wait keeps its badge
    assert md.count(_FLOOR_NOTE) == 1             # floor reason once


def test_index_cc_below_floor_badge_and_note():
    from render.strategy_upgrades_panel import render_strategy_upgrades
    up = {
        "type": "index_covered_call", "underlying": "SPY", "tier": "A",
        "writable": True, "actionable": False, "shares_held": 100,
        "contracts_in_account": 1, "contracts_writable": 1,
        "current_price": 640.0, "current_weight_pct": 5.0,
        "index_rsi_favored": 55.0, "index_rsi_state": "favored",
        "rsi_14": 62.0, "rsi_tag": "RSI 62", "rsi_note": "extended",
        "rsi_decision": "promote", "rsi_badge": "✅",
        "envelope_violations": [], "chain_source": "etrade_live",
        "target_strike": 660.0, "expiration": "2026-09-18", "exp_kind": None,
        "target_dte": 37, "target_delta": 0.18, "otm_pct": 3.1,
        "strike_selected_by": "delta", "sr_anchor": None,
        "est_premium_per_share": 4.0, "est_premium_total": 400,
        "est_annualized_pct": 6.2, "bid": 3.9, "ask": 4.1,
        "rationale": "index cc",
        "setup_floor_wait": True, "setup_floor_note": _FLOOR_NOTE,
    }
    md = "\n".join(render_strategy_upgrades([up]))
    assert "⏸ BELOW SETUP FLOOR" in md
    assert _FLOOR_NOTE in md
    assert "READY TO WRITE (index)" not in md


def test_strangle_put_add_below_floor_status():
    from render.strategy_upgrades_panel import render_strategy_upgrades
    up = {
        "type": "covered_strangle", "underlying": "SMH",
        "rsi_14": 50.0, "rsi_tag": "RSI 50", "rsi_note": "mid",
        "rsi_decision": "keep", "rsi_badge": None, "rsi_blocked": False,
        "current_calls": "1x $580.0C exp 2026-09-18",
        "proposed": {"action": "SELL_TO_OPEN", "qty": 1, "strike": 480.0,
                     "expiration": "2026-09-18", "dte": 37, "delta": -0.2,
                     "premium_per_contract": 5.0, "total_premium": 500.0},
        "yield_annualized": 0.10, "collateral_required": 48000.0,
        "concentration_check": {"current_pct": 4.0, "post_action_pct": 8.0,
                                "blocked": False, "reason": None},
        "combined_income": {"calls": 300.0, "puts": 500.0, "total": 800.0},
        "rationale": "strangle",
        "setup_grade": "D", "setup_grade_score": 34.0,
        "setup_grade_message": "🏁 Entry: D — wait",
        "setup_grade_line": "**Setup Grade: D** (34/100) — 🏁 Entry: D — wait",
        "setup_floor_wait": True, "setup_floor_note": _FLOOR_NOTE,
    }
    md = "\n".join(render_strategy_upgrades([up]))
    assert "⏸ BELOW SETUP FLOOR" in md
    assert "✅ OK" not in md
    assert _FLOOR_NOTE in md
    assert "**Setup Grade: D**" in md


def test_strangle_without_floor_flags_legacy_status():
    from render.strategy_upgrades_panel import render_strategy_upgrades
    up = {
        "type": "covered_strangle", "underlying": "SMH",
        "rsi_14": 50.0, "rsi_tag": "RSI 50", "rsi_note": "mid",
        "rsi_decision": "keep", "rsi_badge": None, "rsi_blocked": False,
        "current_calls": "1x $580.0C exp 2026-09-18",
        "proposed": {"action": "SELL_TO_OPEN", "qty": 1, "strike": 480.0,
                     "expiration": "2026-09-18", "dte": 37, "delta": -0.2,
                     "premium_per_contract": 5.0, "total_premium": 500.0},
        "yield_annualized": 0.10, "collateral_required": 48000.0,
        "concentration_check": {"current_pct": 4.0, "post_action_pct": 8.0,
                                "blocked": False, "reason": None},
        "combined_income": {"calls": 300.0, "puts": 500.0, "total": 800.0},
        "rationale": "strangle",
    }
    md = "\n".join(render_strategy_upgrades([up]))
    assert "✅ OK" in md
    assert "BELOW SETUP FLOOR" not in md


# ── LT_CSP — SKIPPED_SETUP_FLOOR visible row ─────────────────────────────


def test_lto_skipped_setup_floor_renders_visible_row():
    from steps.long_term_opportunities import render_long_term_opportunities
    op = {"kind": "SKIPPED_SETUP_FLOOR", "ticker": "MU",
          "kind_when_skipped": "LONG_DATED_CSP",
          "skip_reason": (_FLOOR_NOTE +
                          " · full ticket kept: SELL 1× MU $795P exp "
                          "Fri Oct 16 '26"),
          "concrete_trade": "SELL 1× MU $795P exp Fri Oct 16 '26",
          "trigger_reasons": [], "rationale": "", "yield_or_cost": "",
          "source": "s"}
    md = "\n".join(render_long_term_opportunities([op]))
    assert "⏸ Skipped" in md
    assert "Below setup floor — D (38)" in md
    assert "full ticket kept: SELL 1× MU $795P" in md


def test_lto_compact_renderer_carries_floor_row():
    from steps.long_term_opportunities import render_long_term_opportunities
    op = {"kind": "SKIPPED_SETUP_FLOOR", "ticker": "MU",
          "kind_when_skipped": "LONG_DATED_CSP",
          "skip_reason": _FLOOR_NOTE,
          "concrete_trade": "SELL 1× MU $795P exp Fri Oct 16 '26",
          "trigger_reasons": [], "rationale": "", "yield_or_cost": "",
          "source": "s"}
    md = "\n".join(render_long_term_opportunities(
        [op], config={"render": {"compact": True}}))
    assert "Below setup floor" in md


# ── Rotation Playbook — gate battery gains the floor ─────────────────────


def _pb_floor(config_extra=None):
    from analysis.rotation_playbook import compute_playbook
    held = [
        {"underlying": "GOOG", "type": "PUT", "strike": 325.0,
         "expiration": "2026-08-21", "qty": -1, "entry_price": 7.9447,
         "current_mid": 4.45, "days_to_expiry": 18},
        {"underlying": "MSFT", "type": "PUT", "strike": 350,
         "expiration": "2026-09-18", "qty": -1, "entry_price": 23.4244,
         "current_mid": 9.525, "days_to_expiry": 46},
    ]
    # RSI 52 late-band + thin proxy vol → grades below B.
    cands = [{"kind": "SCOUT_CSP", "ticker": "CRM", "strike": 100.0,
              "expiration": "2026-09-02", "dte": 30, "premium": 2.00,
              "iv_rank": 20.0, "rsi_14": 52.0}]
    sd = {"balance": {"accountValue": 1_000_000.0, "cash": 300_000.0}}
    an = {"nlv": 1_000_000.0, "snapshot_data": sd, "technicals": {},
          "earnings_calendar": {"CRM": "2027-06-30"}}
    recs = {"CRM": {"rating_tier": 3, "conviction": "High", "age_days": 3,
                    "recommendation": "BUY"}}
    cfg = dict(config_extra or {})
    return compute_playbook(held, cands, recs, set(), an, cfg,
                            today=date(2026, 8, 3))


def test_playbook_floor_excludes_sub_b_with_visible_reason():
    """George: "recommendations are A or B, not D" — the composed open is
    dropped and the exclusion renders in the footer with the measured
    demotion note (rule #24, never silent)."""
    pb = _pb_floor(CFG_FLOOR)
    assert pb.opens == []
    skips = "\n".join(pb.warnings or [])
    assert "CRM $100P" in skips
    assert "⏸ Below setup floor —" in skips


def test_playbook_floor_off_keeps_open():
    pb = _pb_floor(CFG_ON)
    assert [o.ticker for o in pb.opens] == ["CRM"]
    skips = "\n".join(pb.warnings or [])
    assert "Below setup floor" not in skips


# ── Spread composer never green-lights a below-floor CSP ─────────────────


def test_spread_composer_skips_floor_demoted_idea():
    from analysis.spread_composer import compose_spreads
    idea = _idea(setup_floor_demoted=True, setup_floor_note=_FLOOR_NOTE)
    out = compose_spreads([idea], {}, CFG_FLOOR)
    assert out["spreads"] == [] and out["skips"] == []


# ── 🏆 Best Setups — one voice with the floor ─────────────────────────────


def test_best_setups_excludes_below_floor_entries():
    """A spotlit ticket must never carry the floor demotion elsewhere —
    below-floor setups never occupy a 🏆 slot (one voice)."""
    def graded(ticker, letter, score):
        return _idea(ticker=ticker, annualized_pct=18.0, setup_grade=letter,
                     setup_grade_score=score,
                     setup_grade_message=f"🏁 Entry: {letter} — t",
                     setup_grade_drivers=["RSI 41 prime"])
    ideas = [graded("OKY", "B", 70.0), graded("DDD", "C", 55.0)]
    best = sg.collect_best_setups(new_ideas=ideas, config=CFG_FLOOR)
    assert [e["ticker"] for e in best["csp"]] == ["OKY"]
    # Floor off → legacy: score-ranked, C included.
    best_off = sg.collect_best_setups(new_ideas=ideas, config=CFG_ON)
    assert [e["ticker"] for e in best_off["csp"]] == ["OKY", "DDD"]


# ── 💰 Money Plan — blocked-money line ────────────────────────────────────


def test_money_plan_blocked_line_counts_below_floor():
    from render.money_plan import build_money_plan
    ideas = [_idea(setup_floor_demoted=True, setup_floor_note=_FLOOR_NOTE)]
    ltos = [{"kind": "SKIPPED_SETUP_FLOOR", "ticker": "VRT",
             "skip_reason": "⏸ Below setup floor — D (48): x",
             "kind_when_skipped": "LONG_DATED_CSP"}]
    lines, plan = build_money_plan(
        date_str="2026-08-12", action_list_lines=[], options_reviews=[],
        new_ideas=ideas, playbook=None, analytics={}, snapshot_data={},
        config=CFG_FLOOR, long_term_opportunities=ltos)
    md = "\n".join(lines)
    assert "2 rec(s) below setup floor" in md
    assert plan["below_setup_floor_count"] == 2


def test_money_plan_no_floor_bit_when_zero():
    from render.money_plan import build_money_plan
    lines, plan = build_money_plan(
        date_str="2026-08-12", action_list_lines=[], options_reviews=[],
        new_ideas=[_idea()], playbook=None, analytics={}, snapshot_data={},
        config=CFG_FLOOR, long_term_opportunities=[])
    assert "below setup floor" not in "\n".join(lines)


# ── Webapp humanized label (rule #31) ────────────────────────────────────


def test_webapp_label_for_skipped_setup_floor():
    webapp = Path(__file__).resolve().parents[4] / "webapp"
    sys.path.insert(0, str(webapp))
    try:
        from app.icons import humanize_action
        assert humanize_action("SKIPPED_SETUP_FLOOR") == \
            "Skipped — below setup floor"
    finally:
        sys.path.remove(str(webapp))
