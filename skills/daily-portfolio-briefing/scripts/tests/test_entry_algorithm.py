"""THE ENTRY ALGORITHM (CLAUDE.md hard rule #48) — canonical six-step
evaluator + conformance verifier.

George (2026-08-14): "Let's make sure we definitely encode this in the
recommendation: the exact algorithm that ensures that the entry is as good
as possible. I definitely don't want a coin-flip algorithm. It really
needs to work, so use all the right steps to validate entries."

Pinned here:
  (a) step ordering — a ticket failing multiple steps reports the FIRST
      hard block as primary while ALL findings ride in ordered_reasons;
  (b) each step's block / wait / enter path with measured reasons;
  (c) the conformance audit flags a fabricated green-lit-but-rejected
      ticket and stays silent on a compliant render;
  (d) candidate/Best-Setups parity — the evaluator's verdict matches the
      surface behavior on shared fixtures (incl. the SNDK RSI-76 and the
      11%-of-NLV cap cases);
  (e) fail-open directions preserved (missing NLV, missing RSI with no
      measurable drift, empty snapshot, evaluator absence).
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import entry_algorithm as ea  # noqa: E402
from analysis import setup_grade as sg  # noqa: E402
from steps import candidate_research as cr  # noqa: E402

AS_OF = date(2026, 8, 14)

# ─────────────────────────────────────────────────────────────────────────
# Fixtures — the clean APP entry and the 2026-08-14 SNDK shapes
# ─────────────────────────────────────────────────────────────────────────


def _app_snapshot():
    """A clean, fully-measured book: APP RSI 42 pullback, IVr 70, 3-touch
    support at the strike, uptrend, earnings well past expiry."""
    return {
        "technicals": {"APP": {
            "spot": 300.0, "rsi_14": 42.0, "sma_200": 250.0,
            "recent_closes": [290.0 + i for i in range(20)],
            "support_resistance": {
                "supports": [{"price": 280.0, "touches": 3, "strength": 4.0}],
                "resistances": []},
            "deep": {"long_term_verdict": "uptrend"},
        }},
        "iv_ranks": {"APP": 70.0},
        "quotes": {"APP": {"last": 301.0}},
        "positions": [],
        "balance": {"accountValue": 1_000_000},
        "earnings_calendar": {"APP": "2026-12-01"},
    }


SNDK_PRE_MOVE = 1367.0
SNDK_LIVE = 1625.0


def _sndk_closes():
    return [1300.0 + 3.0 * i + (7.0 if i % 2 else -7.0)
            for i in range(24)] + [SNDK_PRE_MOVE]


def _sndk_snapshot(quotes=None, nlv=1_100_000):
    return {
        "technicals": {"SNDK": {
            "spot": SNDK_PRE_MOVE, "rsi_14": 56.0, "sma_200": 1100.0,
            "recent_closes": _sndk_closes(),
            "support_resistance": {
                "supports": [{"price": 1230.0, "touches": 3,
                              "strength": 4.0}],
                "resistances": []},
            "deep": {"long_term_verdict": "uptrend"},
        }},
        "iv_ranks": {"SNDK": 70.0},
        "quotes": quotes if quotes is not None else {},
        "positions": [],
        "balance": {"accountValue": nlv},
        "earnings_calendar": {},
    }


def _eval(side="csp", ticker="APP", strike=280.0, exp="2026-09-18",
          mid=6.5, snap=None, analytics=None, config=None, **kw):
    kw.setdefault("as_of", AS_OF)
    return ea.evaluate_entry(side, ticker, strike, exp, mid,
                             snap if snap is not None else _app_snapshot(),
                             analytics, config or {}, **kw)


def _finding(dec, check):
    for f in dec.ordered_reasons:
        if f["check"] == check:
            return f
    return None


# ─────────────────────────────────────────────────────────────────────────
# The clean path — all six steps pass
# ─────────────────────────────────────────────────────────────────────────


def test_clean_entry_passes_all_six_steps():
    """"use all the right steps to validate entries" — a fully-measured
    clean setup walks every step and lands ENTER with the graded one-line."""
    cfg = {"setup_grade": {"enabled": True,
                           "actionable_floor": {"enabled": True}}}
    dec = _eval(config=cfg, analytics={"stress_coverage": {"ratio": 0.8}})
    assert dec.verdict == "ENTER" and dec.entered
    assert dec.primary is None
    steps_seen = sorted({f["step"] for f in dec.ordered_reasons})
    assert steps_seen == [1, 2, 3, 4, 5, 6]      # every step reported
    assert "all six steps pass" in dec.one_line
    assert dec.one_line.startswith("✅ ENTER — A")
    # Findings ride in evaluation order (steps nondecreasing — auditable).
    nums = [f["step"] for f in dec.ordered_reasons]
    assert nums == sorted(nums)


# ─────────────────────────────────────────────────────────────────────────
# (a) Step ordering — first hard block is primary, all findings recorded
# ─────────────────────────────────────────────────────────────────────────


def test_multi_failure_reports_first_hard_block_as_primary():
    """"I definitely don't want a coin-flip algorithm" — a ticket failing
    RSI (78), earnings-inside-contract, held-put overlap AND the tier cap
    reports the RSI hard block (the first step-2 check) as primary while
    every other block still rides in ordered_reasons."""
    snap = _app_snapshot()
    snap["technicals"]["APP"]["rsi_14"] = 78.0
    snap["earnings_calendar"] = {"APP": "2026-09-01"}   # inside Sep 18 exp
    snap["positions"] = [{"assetType": "OPTION", "type": "PUT",
                          "underlying": "APP", "qty": -1, "strike": 280.0}]
    snap["balance"] = {"accountValue": 200_000}         # 280×100 = 14% NLV
    dec = _eval(snap=snap)
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "rsi_hard_block"
    blocked_checks = {f["check"] for f in dec.ordered_reasons
                      if f["status"] == "block"}
    assert {"rsi_hard_block", "earnings_window", "held_put_overlap",
            "name_concentration"} <= blocked_checks
    assert dec.one_line.startswith("⛔ BLOCKED")
    assert "step 2: rsi_hard_block" in dec.one_line


# ─────────────────────────────────────────────────────────────────────────
# (b) Each step's path, with measured reasons
# ─────────────────────────────────────────────────────────────────────────


def test_step1_stale_up_move_blocks_new_put():
    """STEP 1 fail-safe (rule #44): spot +10% with the live RSI not
    computable (no close series) → BLOCKED(data), never a trusted entry."""
    snap = _app_snapshot()
    del snap["technicals"]["APP"]["recent_closes"]
    snap["quotes"] = {"APP": {"last": 330.0}}           # +10% drift
    dec = _eval(snap=snap)
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "vintage" and dec.primary["step"] == 1
    assert "rule #44" in dec.primary_detail


def test_step1_live_recompute_blocks_at_live_rsi():
    """STEP 1 → STEP 2: the SNDK shape — +18.9% drift recomputes a live
    Wilder RSI past the >70 hard block; the pre-move RSI 56 is void."""
    dec = _eval(ticker="SNDK", strike=1230.0, mid=29.35,
                snap=_sndk_snapshot(quotes={"SNDK": {"last": SNDK_LIVE}}))
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "rsi_hard_block"
    assert "overbought" in dec.primary_detail
    v = _finding(dec, "vintage")
    assert v["status"] == "pass" and "recomputed live" in v["detail"]


def test_step1_unverified_warns_and_caps_grade_never_ab():
    """STEP 1 unverified (rule #46): no live quote at all → the entry stays
    actionable (fail-open) but the grade caps — never A/B on unverified
    RSI — and the step-5 finding says so."""
    snap = _app_snapshot()
    snap["quotes"] = {}
    cfg = {"setup_grade": {"enabled": True,
                           "actionable_floor": {"enabled": True}}}
    dec = _eval(snap=snap, config=cfg)
    assert dec.verdict == "ENTER"                       # fail-open
    v = _finding(dec, "vintage")
    assert v["status"] == "warn" and "rule #46" in v["detail"]
    assert dec.grade["letter"] not in ("A", "A-", "B")
    fl = _finding(dec, "actionable_floor")
    assert fl["status"] == "warn" and "not A/B eligible" in fl["detail"]


def test_step2_earnings_inside_contract_blocks():
    snap = _app_snapshot()
    snap["earnings_calendar"] = {"APP": "2026-09-01"}
    dec = _eval(snap=snap)
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "earnings_window"
    assert "EARNINGS_WINDOW" in dec.primary_detail


def test_step2_unknown_earnings_warns_not_blocks():
    """Unknown earnings date → WARN per the existing rule (rule #43
    EARNINGS_DATE_UNKNOWN) — demoted-and-annotated, never silently passed,
    never a block."""
    snap = _app_snapshot()
    snap["earnings_calendar"] = {}
    dec = _eval(snap=snap)
    assert dec.verdict == "ENTER"
    f = _finding(dec, "earnings_window")
    assert f["status"] == "warn" and "unknown" in f["detail"]


def test_step2_held_put_overlap_blocks():
    snap = _app_snapshot()
    snap["positions"] = [{"assetType": "OPTION", "type": "PUT",
                          "underlying": "APP", "qty": -1, "strike": 275.0}]
    dec = _eval(snap=snap)      # $280 vs held $275 = 1.8% — inside 5%
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "held_put_overlap"
    assert "$275P" in dec.primary_detail and "rule #40" in dec.primary_detail


def test_step2_concentration_over_tier_cap_blocks():
    """The 11%-cap case: 1× $1230P = $123,000 on a $1.1M NLV Tier C name
    (8% cap) → BLOCKED with the measured size warning."""
    dec = _eval(ticker="SNDK", strike=1230.0, mid=29.35,
                snap=_sndk_snapshot(quotes={"SNDK": {"last": SNDK_PRE_MOVE}}))
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "name_concentration"
    assert "11.2% of NLV" in dec.primary_detail
    assert "Tier C cap 8%" in dec.primary_detail


def test_step2_lt_verdict_gate_blocks_and_override_warns():
    snap = _app_snapshot()
    snap["technicals"]["APP"]["deep"] = {"long_term_verdict": "broken",
                                         "vs_sma200_pct": -25.0}
    snap["technicals"]["APP"]["sma_200"] = 400.0
    snap["quotes"] = {"APP": {"last": 300.0}}
    dec = _eval(snap=snap)
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "lt_verdict"
    assert "rule #39" in dec.primary_detail
    # Fresh tier-5 BUY override → warn (visible contradiction), not block.
    dec2 = _eval(snap=snap, parkev_rec={"rating_tier": 5, "age_days": 2,
                                        "recommendation": "BUY"})
    assert dec2.verdict != "BLOCKED"
    f = _finding(dec2, "lt_verdict")
    assert f["status"] == "warn" and "override" in f["detail"]


def test_step2_tail_risk_list_blocks_when_configured():
    dec = _eval(config={"tail_risk": ["APP"]})
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "tail_risk"
    # Absent config → n/a, never a block.
    dec2 = _eval()
    assert _finding(dec2, "tail_risk")["status"] == "n/a"


def test_step2_tenor_cap_blocks_long_dated_new_open_core_x3_allows():
    snap = _app_snapshot()
    snap["earnings_calendar"] = {}      # isolate the tenor check
    dec = _eval(snap=snap, exp=None, dte=200)
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "tenor_cap"
    assert "200d" in dec.primary_detail and "120d" in dec.primary_detail
    dec2 = _eval(snap=snap, exp=None, dte=200,
                 config={"core_positions": ["APP"]})
    assert _finding(dec2, "tenor_cap")["status"] == "pass"   # 120×3 = 360


def test_step2_closing_today_blocks_reopening_the_same_contract():
    """Rule #43 (SNDK): never re-open a same/near-strike contract the same
    briefing's action list closes."""
    dec = _eval(action_close_idents={"APP_PUT_280_20260918"})
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "closing_today"


def test_step3_thin_premium_waits_with_measured_numbers():
    dec = _eval(mid=0.55, exp=None, dte=90)      # 0.55/280 ann ≈ 0.8%
    assert dec.verdict == "WAIT"
    assert dec.primary["check"] == "yield_floor"
    assert "12% floor" in dec.primary_detail
    assert "rule #44" in dec.primary_detail


def test_step3_vol_source_is_labeled():
    dec = _eval()
    f = _finding(dec, "vol_source")
    assert "RVr (realized-vol proxy) 70" in f["detail"]
    snap = _app_snapshot()
    snap["chain_iv"] = {"APP": {"iv_rank": 55.0}}
    dec2 = _eval(snap=snap)
    assert "chain IVr (true implied) 55" in _finding(dec2, "vol_source")["detail"]


def test_step5_b_floor_demotes_weak_setup_to_wait():
    """George (2026-08-12): "recommendations are A or B, not D" — a weak
    measured setup (late-band RSI, thin vol, no support) grades below the
    floor and the verdict is WAIT with the graded reason."""
    snap = _app_snapshot()
    t = snap["technicals"]["APP"]
    t["rsi_14"] = 54.0
    t["support_resistance"] = {"supports": [], "resistances": []}
    t["sma_200"] = 295.0
    snap["iv_ranks"] = {"APP": 45.0}
    snap["quotes"] = {"APP": {"last": 300.0}}
    cfg = {"setup_grade": {"enabled": True,
                           "actionable_floor": {"enabled": True}}}
    dec = _eval(snap=snap, config=cfg)
    assert dec.verdict == "WAIT"
    assert dec.primary["check"] == "actionable_floor"
    assert "Below setup floor" in dec.primary_detail


def test_step6_capacity_gate_waits_with_measured_ratio():
    dec = _eval(analytics={"stress_coverage": {"ratio": 0.16}})
    assert dec.verdict == "WAIT"
    assert dec.primary["check"] == "capacity"
    assert "0.16×" in dec.primary_detail and "rule #41" in dec.primary_detail


def test_step6_sector_context_annotates_never_blocks():
    dec = _eval(sector_pcts={"Information Technology": 62.0},
                config={"sector_exposure": {"enabled": True}})
    f = _finding(dec, "sector_context")
    assert f is not None
    assert dec.verdict in ("ENTER", "WAIT")     # never BLOCKED by sector
    assert not any(x["check"] == "sector_context"
                   and x["status"] == "block" for x in dec.ordered_reasons)


def test_cc_side_oversold_blocks_and_midrange_waits():
    snap = _app_snapshot()
    snap["technicals"]["APP"]["rsi_14"] = 30.0
    snap["quotes"] = {"APP": {"last": 300.0}}
    dec = _eval(side="cc", snap=snap)
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "rsi_hard_block"
    snap["technicals"]["APP"]["rsi_14"] = 50.0
    dec2 = _eval(side="cc", snap=snap)
    assert dec2.verdict == "WAIT"
    assert dec2.primary["check"] == "rsi_midrange_wait"
    assert "wait for strength" in dec2.primary_detail


def test_unknown_side_raises():
    with pytest.raises(ValueError):
        _eval(side="strangle")


# ─────────────────────────────────────────────────────────────────────────
# (c) Conformance audit
# ─────────────────────────────────────────────────────────────────────────


def _green_lit_md(ticker="SNDK", strike=1230, exp="2026-09-18"):
    return "\n".join([
        "## ✅ Action List",
        "",
        f"1. **PULLBACK CSP** `{ticker}` — SELL 1× {ticker} "
        f"${strike}P exp {exp} @ $29.35 (36 DTE) · RSI 48 🟢 pullback",
        "  - Why: elevated premium on a pullback",
        "",
    ])


def test_conformance_flags_green_lit_ticket_the_algorithm_rejects():
    """"It really needs to work" — a fabricated green-lit SNDK card (the
    RSI-76 shape) is flagged by the conformance audit with the algorithm's
    verdict + first failing step."""
    offenders = ea.audit_conformance(
        _green_lit_md(),
        _sndk_snapshot(quotes={"SNDK": {"last": SNDK_LIVE}}),
        None, {}, as_of=AS_OF)
    assert len(offenders) == 1
    o = offenders[0]
    assert o["ticker"] == "SNDK" and o["verdict"] == "BLOCKED"
    assert o["check"] == "rsi_hard_block"
    panel = "\n".join(ea.render_conformance_panel(offenders))
    assert "🧮 Entry Algorithm Conformance" in panel
    assert "do NOT place these as rendered" in panel
    assert "SNDK" in panel


def test_conformance_silent_on_compliant_render():
    md = "\n".join([
        "## ✅ Action List",
        "",
        "1. **PULLBACK CSP** `APP` — SELL 1× APP $280P exp 2026-09-18 "
        "@ $6.50 (35 DTE) · RSI 42 🟢 pullback",
        "",
    ])
    offenders = ea.audit_conformance(md, _app_snapshot(), None, {},
                                     as_of=AS_OF)
    assert offenders == []
    panel = "\n".join(ea.render_conformance_panel(offenders))
    assert "✅ Entry Algorithm conformance" in panel


def test_conformance_skips_already_demoted_tickets():
    """A ticket carrying a ⏸/⛔ demotion tag is NOT green-lit — the audit
    never double-flags what a surface already demoted (rule #24 keeps the
    full ticket visible in wait sections by design)."""
    md = "\n".join([
        "## ✅ Action List",
        "",
        "1. **PULLBACK CSP** `SNDK` — SELL 1× SNDK $1230P exp 2026-09-18 "
        "@ $29.35 (36 DTE)",
        "  - ⏸ Deferred (capacity gated) — stress coverage 0.16× < 0.50× "
        "floor; shown for planning, not a green light (rule #41)",
        "",
    ])
    offenders = ea.audit_conformance(
        md, _sndk_snapshot(quotes={"SNDK": {"last": SNDK_LIVE}}),
        None, {}, as_of=AS_OF)
    assert offenders == []


def test_conformance_skips_management_and_footer_lines():
    md = "\n".join([
        "## Watch",
        "",
        "1. **EXECUTE ROLL** SNDK_PUT_1230_20260918 — Buy-to-Close 1× @ "
        "$29.35 + STO 1× SNDK $1150P exp 2026-10-16 (ROLL ANALYSIS)",
        "_footer: SELL 1× SNDK $1230P exp 2026-09-18 (transparency)._",
        "| menu | SELL 1× SNDK $1230P exp 2026-09-18 |",
        "",
    ])
    offenders = ea.audit_conformance(
        md, _sndk_snapshot(quotes={"SNDK": {"last": SNDK_LIVE}}),
        None, {}, as_of=AS_OF)
    assert offenders == []


# ─────────────────────────────────────────────────────────────────────────
# (d) Parity — the surfaces and the evaluator speak with one voice
# ─────────────────────────────────────────────────────────────────────────


def _sndk_scout_result():
    return {
        "ticker": "SNDK", "rsi_14": 48.0, "iv_rank": 70.0,
        "spot": SNDK_PRE_MOVE, "sma_200": 1100.0, "drawdown_pct": 5.0,
        "days_to_earnings": 40,
        "support_resistance": {
            "supports": [{"price": 1230.0, "touches": 3, "strength": 4.0}],
            "resistances": []},
        "csp_entry": {"strike": 1230.0, "mid": 29.35, "dte": 36,
                      "expiration": "2026-09-18"},
    }


def test_parity_best_setups_sndk_rsi76_matches_evaluator():
    """The SNDK RSI-76 case: the Best Setups pool excludes it AND the
    canonical evaluator independently says BLOCKED — identical outcome,
    one algorithm."""
    snap = _sndk_snapshot(quotes={"SNDK": {"last": SNDK_LIVE}})
    best = sg.collect_best_setups(scout_results=[_sndk_scout_result()],
                                  snapshot_data=snap, config={})
    assert [e["ticker"] for e in best["csp"]] == []
    assert "SNDK" in {e["ticker"] for e in best["excluded_csp"]}
    dec = _eval(ticker="SNDK", strike=1230.0, mid=29.35, snap=snap)
    assert dec.verdict == "BLOCKED"          # surface exclusion ⟺ BLOCKED


def test_parity_best_setups_11pct_cap_matches_evaluator():
    """The 11%-cap case: over the Tier C obligation-inclusive cap → the
    spotlight excludes with the cap reason AND the evaluator BLOCKS on the
    same projected math."""
    snap = _sndk_snapshot(quotes={"SNDK": {"last": SNDK_PRE_MOVE}})
    best = sg.collect_best_setups(scout_results=[_sndk_scout_result()],
                                  snapshot_data=snap, config={})
    assert [e["ticker"] for e in best["csp"]] == []
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert "SNDK" in ex and "cap" in ex["SNDK"]["reason"]
    dec = _eval(ticker="SNDK", strike=1230.0, mid=29.35, snap=snap)
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "name_concentration"


def test_parity_best_setups_blocked_verdict_moves_entry_to_exclusions():
    """The evaluator IS the spotlight's decision path: a pooled entry whose
    contract spans earnings (a check the pool's inline logic never ran) is
    moved to the visible exclusions by the rule-#48 backstop."""
    # Dates pinned relative to the REAL run date — collect_best_setups'
    # rule-#48 backstop evaluates with as_of = today.
    snap = _sndk_snapshot(quotes={"SNDK": {"last": SNDK_PRE_MOVE}},
                          nlv=10_000_000)      # cap clears — isolate earnings
    snap["earnings_calendar"] = {"SNDK": (
        date.today() + timedelta(days=20)).isoformat()}
    r = _sndk_scout_result()
    r["csp_entry"]["dte"] = 35
    r["csp_entry"]["expiration"] = (
        date.today() + timedelta(days=35)).isoformat()
    best = sg.collect_best_setups(scout_results=[r], snapshot_data=snap,
                                  config={})
    assert [e["ticker"] for e in best["csp"]] == []
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert "SNDK" in ex
    assert "EARNINGS_WINDOW" in ex["SNDK"]["reason"]


def test_parity_candidate_briefing_earnings_blocked_card_demotes():
    """Candidate surface parity: a scout candidate whose contract spans
    earnings demotes to '⛔ Held back by the entry algorithm' — the card is
    visible with the first failing step, never a green-lit 🎯 slot — and
    the evaluator independently returns BLOCKED on the same fixture."""
    exp = (date.today() + timedelta(days=30)).isoformat()
    earn = (date.today() + timedelta(days=10)).isoformat()
    payload = {
        "themes": {"semis": {"name": "Semis", "group": "g",
                             "anchors": ["AMD"], "etfs": []}},
        "results_by_theme": {"semis": [
            {"ticker": "AMD", "spot": 150.0, "rsi_14": 40, "iv_rank": 60,
             "sma_200": 140, "drawdown_pct": 12, "fivedayret_pct": -1.2,
             "verdict": "CSP ENTRY (fat premium)",
             "rationale": ["fat premium"],
             "csp_entry": {"strike": 135, "mid": 2.1, "bid": 2.0,
                           "ask": 2.2, "expiration": exp, "dte": 30}},
        ]},
    }
    snap = {"technicals": {}, "quotes": {}, "positions": [],
            "balance": {"accountValue": 1_000_000},
            "earnings_calendar": {"AMD": earn}}
    md = cr.render_candidate_briefing(
        payload, fv_by_ticker={}, config={}, generated_at="T",
        snapshot_data=snap)
    assert "⛔ Held back by the entry algorithm" in md
    assert "**BLOCKED** (step 2: earnings_window)" in md
    assert "🎯 Today's Candidates (0)" in md     # no green-lit card remains
    assert "Entry (CSP):" not in md
    dec = ea.evaluate_entry("csp", "AMD", 135, exp, 2.1, snap, None, {},
                            dte=30)
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "earnings_window"


def test_parity_candidate_briefing_clean_candidate_stays_green_lit():
    """A clean candidate keeps its 🎯 card AND the evaluator agrees with
    ENTER — the conductor changes nothing on a compliant surface."""
    exp = (date.today() + timedelta(days=30)).isoformat()
    payload = {
        "themes": {"semis": {"name": "Semis", "group": "g",
                             "anchors": ["AMD"], "etfs": []}},
        "results_by_theme": {"semis": [
            {"ticker": "AMD", "spot": 150.0, "rsi_14": 40, "iv_rank": 60,
             "sma_200": 140, "drawdown_pct": 12, "fivedayret_pct": -1.2,
             "verdict": "CSP ENTRY (fat premium)",
             "rationale": ["fat premium"],
             "csp_entry": {"strike": 135, "mid": 2.1, "bid": 2.0,
                           "ask": 2.2, "expiration": exp, "dte": 30}},
        ]},
    }
    snap = {"technicals": {}, "quotes": {}, "positions": [],
            "balance": {"accountValue": 1_000_000},
            "earnings_calendar": {"AMD": (
                date.today() + timedelta(days=90)).isoformat()}}
    md = cr.render_candidate_briefing(
        payload, fv_by_ticker={}, config={}, generated_at="T",
        snapshot_data=snap)
    assert "🎯 Today's Candidates (1)" in md
    assert "⛔ Held back by the entry algorithm" not in md
    dec = ea.evaluate_entry("csp", "AMD", 135, exp, 2.1, snap, None, {},
                            dte=30)
    assert dec.verdict == "ENTER"


# ─────────────────────────────────────────────────────────────────────────
# (e) Fail-open directions preserved (rule #19)
# ─────────────────────────────────────────────────────────────────────────


def test_fail_open_missing_nlv_never_blocks_concentration():
    snap = _app_snapshot()
    snap["balance"] = {}
    dec = _eval(snap=snap)
    assert dec.verdict == "ENTER"
    assert _finding(dec, "name_concentration")["status"] == "n/a"


def test_fail_open_missing_rsi_with_no_drift_never_blocks():
    """No RSI and no measurable drift → no RSI gate applied (the existing
    fail-open direction) — the entry is never blocked on missing data."""
    snap = _app_snapshot()
    del snap["technicals"]["APP"]["rsi_14"]
    snap["quotes"] = {}
    dec = _eval(snap=snap)
    assert dec.verdict == "ENTER"
    f = _finding(dec, "rsi_hard_block")
    assert f["status"] == "pass" and "fail-open" in f["detail"]


def test_fail_open_empty_snapshot_enters_with_na_findings():
    dec = ea.evaluate_entry("csp", "ZZZZ", None, None, None, {}, None, {},
                            as_of=AS_OF)
    assert dec.verdict == "ENTER"
    assert all(f["status"] in ("pass", "n/a", "warn")
               for f in dec.ordered_reasons)
    assert "grade n/a" in dec.one_line       # GRADE_NA_NOTE — verify manually


def test_fail_open_missing_premium_floors_not_testable():
    dec = _eval(mid=None)
    assert _finding(dec, "yield_floor")["status"] == "n/a"
    assert dec.verdict == "ENTER"


def test_decision_is_deterministic():
    """"I definitely don't want a coin-flip algorithm" — same inputs, same
    verdict, byte-identical ordered findings."""
    a = _eval()
    b = _eval()
    assert a.verdict == b.verdict == "ENTER"
    assert a.ordered_reasons == b.ordered_reasons
    assert a.one_line == b.one_line
