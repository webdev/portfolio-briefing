"""Rule #49 — day-color WAIT, same-theme stacking WAIT, proxy-vol honesty.

George (2026-08-14), after the briefing rendered "A- (80) WDC — SELL 1×
$450P … RSI 48 late-band · RVr 84 ✓ — 🏁 Entry: A- — strong setup… Enter
per plan" on a GREEN day (+1.5%) while he held the SNDK $1230P (WDC =
SanDisk's former parent, same Memory & Storage theme): "make that change
so that the briefing is not telling me to do it today. It's misleading."

Pinned here:
  (a) day color — green-day CSP → WAIT with the measured move; red-day
      CSP unaffected; the ±0.3% neutral band gates neither; no drift
      reference → fail-open (no fabricated color); the CC mirror;
  (b) same-theme stacking — a held same-theme put → WAIT naming the held
      contract + theme; no-theme names unaffected; multi-theme overlap
      detected; same-ticker excluded (rule #40's territory); CC exempt;
  (c) proxy-vol honesty — an RVr-proxy ENTER message carries the
      broker-verify caveat; a true-chain-IVr message is unchanged;
  (d) the 🏆 spotlight demotes day-color/theme WAITs into the visible ⏸
      exclusions; the candidate surface renders the wait bucket; the
      conformance panel flags a green-lit wrong-day ticket;
  (e) config-off = byte-identical legacy per flag.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import entry_algorithm as ea  # noqa: E402
from analysis import setup_grade as sg  # noqa: E402
from analysis import theme_stacking as ts  # noqa: E402
from steps import candidate_research as cr  # noqa: E402

AS_OF = date(2026, 8, 14)

DAY_CFG = {"entry_algorithm": {"day_color": {"enabled": True}}}
THEME_CFG = {"entry_algorithm": {"theme_stacking": {"enabled": True,
                                                    "mode": "wait"}}}

TECH_CLOSE = 495.88
GREEN_LIVE = round(TECH_CLOSE * 1.015, 2)     # +1.5% — the WDC day
RED_LIVE = round(TECH_CLOSE * 0.985, 2)       # -1.5%

SNDK_PUT = {"assetType": "OPTION", "type": "PUT", "underlying": "SNDK",
            "qty": -1.0, "strike": 1230.0}


def _snap(ticker="WDC", last=None, rsi=48.0, positions=None):
    """A clean, fully-measured book for one ticker (the WDC card shape)."""
    return {
        "technicals": {ticker: {
            "spot": TECH_CLOSE, "rsi_14": rsi, "sma_200": 400.0,
            "recent_closes": [470.0 + i for i in range(20)],
            "support_resistance": {
                "supports": [{"price": 450.0, "touches": 3,
                              "strength": 4.0}],
                "resistances": [{"price": 520.0, "touches": 3,
                                 "strength": 4.0}]},
            "deep": {"long_term_verdict": "uptrend"},
        }},
        "iv_ranks": {ticker: 84.0},
        "quotes": ({ticker: {"last": last}} if last is not None else {}),
        "positions": list(positions or []),
        "balance": {"accountValue": 2_000_000},
        "earnings_calendar": {ticker: "2026-12-01"},
    }


def _eval(side="csp", ticker="WDC", strike=450.0, exp="2026-09-18",
          mid=9.0, snap=None, config=None, **kw):
    kw.setdefault("as_of", AS_OF)
    return ea.evaluate_entry(side, ticker, strike, exp, mid,
                             snap if snap is not None else _snap(),
                             None, config or {}, **kw)


def _finding(dec, check):
    for f in dec.ordered_reasons:
        if f["check"] == check:
            return f
    return None


# ─────────────────────────────────────────────────────────────────────────
# (a) Day-color WAIT — rule #44 made binding on the entry verdict
# ─────────────────────────────────────────────────────────────────────────


def test_green_day_csp_waits_with_measured_move():
    """"make that change so that the briefing is not telling me to do it
    today. It's misleading." — the WDC card: a new CSP on a measured
    +1.5% green day is a hard WAIT with the measured move, never ENTER."""
    dec = _eval(snap=_snap(last=GREEN_LIVE), config=DAY_CFG)
    assert dec.verdict == "WAIT"
    assert dec.primary["check"] == "day_color_wait"
    assert "🟥 wait for a red day" in dec.primary_detail
    assert "WDC +1.5% today" in dec.primary_detail
    assert "sell puts into weakness (card rule)" in dec.primary_detail
    assert "⏸ WAIT" in dec.one_line


def test_red_day_csp_unaffected():
    """Selling puts INTO weakness is the whole point — a -1.5% red day
    passes the day-color gate and the clean setup stays ENTER."""
    dec = _eval(snap=_snap(last=RED_LIVE), config=DAY_CFG)
    assert dec.verdict == "ENTER"
    f = _finding(dec, "day_color_wait")
    assert f["status"] == "pass" and "-1.5% today" in f["detail"]


def test_neutral_band_gates_neither_side():
    """Moves inside ±0.3% are NEUTRAL — neither the CSP nor the CC side
    waits on a flat-ish tape (config green_min_pct / red_min_pct)."""
    up = round(TECH_CLOSE * 1.002, 2)          # +0.2%
    dec = _eval(snap=_snap(last=up), config=DAY_CFG)
    assert _finding(dec, "day_color_wait")["status"] == "pass"
    down = round(TECH_CLOSE * 0.998, 2)        # -0.2%
    dec2 = _eval(side="cc", snap=_snap(last=down, rsi=65.0),
                 config=DAY_CFG)
    assert _finding(dec2, "day_color_wait")["status"] == "pass"


def test_no_drift_reference_fails_open_no_fabricated_color():
    """No live quote AND no broker price → the day color is UNMEASURED —
    fail-open n/a (rule #19), never a fabricated color, verdict unchanged
    from the legacy path."""
    dec = _eval(snap=_snap(last=None), config=DAY_CFG)
    f = _finding(dec, "day_color_wait")
    assert f["status"] == "n/a" and "unmeasured" in f["detail"]
    assert dec.verdict == "ENTER"              # unverified warns, never waits


def test_red_day_cc_waits_green_day_cc_unaffected():
    """The CC mirror: covered calls are sold into STRENGTH — a -1.5% red
    day is a hard WAIT with the measured move; a green day passes."""
    dec = _eval(side="cc", snap=_snap(last=RED_LIVE, rsi=65.0),
                config=DAY_CFG)
    assert dec.verdict == "WAIT"
    assert dec.primary["check"] == "day_color_wait"
    assert "🟩 wait for a green day" in dec.primary_detail
    assert "WDC -1.5% today" in dec.primary_detail
    assert "sell calls into strength (card rule)" in dec.primary_detail
    dec2 = _eval(side="cc", snap=_snap(last=GREEN_LIVE, rsi=65.0),
                 config=DAY_CFG)
    assert _finding(dec2, "day_color_wait")["status"] == "pass"


def test_day_color_config_off_is_legacy():
    """enabled off / block absent → n/a finding, byte-identical legacy
    verdict — the WDC green-day entry ENTERs exactly as before."""
    for cfg in ({}, {"entry_algorithm": {"day_color": {"enabled": False}}}):
        dec = _eval(snap=_snap(last=GREEN_LIVE), config=cfg)
        assert dec.verdict == "ENTER"
        assert _finding(dec, "day_color_wait")["status"] == "n/a"


# ─────────────────────────────────────────────────────────────────────────
# (b) Same-theme stacking — the SNDK/WDC gap
# ─────────────────────────────────────────────────────────────────────────


def test_same_theme_held_put_waits_with_named_contract_and_theme():
    """The origin gap: a WDC CSP while the SNDK $1230P is HELD (both
    Memory & Storage anchors in theme_universes.yaml) → WAIT naming the
    held contract and the shared theme. "It's misleading." otherwise."""
    dec = _eval(snap=_snap(last=TECH_CLOSE, positions=[SNDK_PUT]),
                config=THEME_CFG)
    assert dec.verdict == "WAIT"
    assert dec.primary["check"] == "theme_stacking"
    assert "same-theme put already held" in dec.primary_detail
    assert "SNDK $1230P" in dec.primary_detail
    assert "Memory & Storage" in dec.primary_detail
    assert "stacking correlated assignment risk" in dec.primary_detail


def test_no_theme_name_unaffected():
    """A ticker in NO mapped theme (KO) fails open — no check, the held
    SNDK put is irrelevant to it (rule #19 direction)."""
    dec = _eval(ticker="KO", strike=60.0, mid=1.5, exp=None, dte=35,
                snap=_snap(ticker="KO", last=TECH_CLOSE,
                           positions=[SNDK_PUT]),
                config=THEME_CFG)
    f = _finding(dec, "theme_stacking")
    assert f["status"] == "pass"
    assert not any(x["check"] == "theme_stacking" and x["status"] == "wait"
                   for x in dec.ordered_reasons)


def test_multi_theme_overlap_detected_pure_helper():
    """A ticker sitting in MULTIPLE themes matches on ANY shared theme;
    all shared theme names ride in the reason (never just the first)."""
    tm = {"WDC": {"Memory & Storage", "Semis"},
          "SNDK": {"Memory & Storage"},
          "NVDA": {"Semis"},
          "KO": set()}
    hit = ts.check_theme_stacking(
        "WDC", {"SNDK": [1230.0], "NVDA": [200.0]}, theme_map=tm)
    assert hit is not None
    matched = {m["ticker"] for m in hit["matches"]}
    assert matched == {"NVDA", "SNDK"}
    assert "SNDK $1230P (Memory & Storage)" in hit["detail"]
    assert "NVDA $200P (Semis)" in hit["detail"]
    both = ts.check_theme_stacking(
        "WDC", {"X": [10.0]},
        theme_map={"WDC": {"T1", "T2"}, "X": {"T1", "T2"}})
    assert "T1, T2" in both["detail"]


def test_same_ticker_held_put_is_rule40_territory_not_theme():
    """A held put on the SAME ticker never matches the theme guard —
    same-name risk is governed by the rule-#40 5% overlap check."""
    assert ts.check_theme_stacking(
        "WDC", {"WDC": [400.0]},
        theme_map={"WDC": {"Memory & Storage"}}) is None


def test_cc_writes_exempt_from_theme_stacking():
    """CC writes are share-backed — the theme guard never gates them even
    with a same-theme put on the book."""
    dec = _eval(side="cc", snap=_snap(last=TECH_CLOSE, rsi=65.0,
                                      positions=[SNDK_PUT]),
                config=THEME_CFG)
    f = _finding(dec, "theme_stacking")
    assert f["status"] == "n/a" and "share-backed" in f["detail"]


def test_theme_stacking_config_off_is_legacy():
    dec = _eval(snap=_snap(last=TECH_CLOSE, positions=[SNDK_PUT]),
                config={})
    f = _finding(dec, "theme_stacking")
    assert f["status"] == "n/a" and "off (config)" in f["detail"]
    assert dec.verdict == "ENTER"


def test_theme_stacking_block_mode_blocks():
    """mode: block escalates the same measured reason to a hard block."""
    cfg = {"entry_algorithm": {"theme_stacking": {"enabled": True,
                                                  "mode": "block"}}}
    dec = _eval(snap=_snap(last=TECH_CLOSE, positions=[SNDK_PUT]),
                config=cfg)
    assert dec.verdict == "BLOCKED"
    assert dec.primary["check"] == "theme_stacking"


# ─────────────────────────────────────────────────────────────────────────
# (c) Proxy-vol honesty in the entry message
# ─────────────────────────────────────────────────────────────────────────


def _graded(iv_source):
    return sg.csp_setup(
        rsi=40.0, iv_rank=84.0, iv_rank_source=iv_source,
        support_resistance={"supports": [{"price": 440.0, "touches": 3,
                                          "strength": 4.0}],
                            "resistances": []},
        strike=450.0, spot=TECH_CLOSE, sma_200=400.0,
        day_change_pct=-0.01, days_to_earnings=60, config={})


def test_proxy_vol_enter_message_carries_broker_verify_caveat():
    """The WDC card said "RVr 84 ✓ — … Enter per plan" unqualified — "It's
    misleading." An RVr-proxy vol read may not green-light without the
    broker-verify caveat. Message-level only: the letter is unchanged."""
    g = _graded("rv")
    assert g["letter"] in ("A", "A-", "B")
    assert ("Enter per plan — verify true IVr at the broker "
            "(vol measured by RVr proxy)") in g["message"]


def test_true_chain_ivr_message_unchanged():
    g = _graded("chain")
    assert g["message"].rstrip().endswith("Enter per plan.")
    assert "RVr proxy" not in g["message"]
    # Same inputs, same letter either way — the caveat never regrades.
    assert g["letter"] == _graded("rv")["letter"]


# ─────────────────────────────────────────────────────────────────────────
# (d) Surface inheritance — spotlight, candidate cards, conformance panel
# ─────────────────────────────────────────────────────────────────────────


def _wdc_scout_result(exp=None, dte=36):
    return {
        "ticker": "WDC", "rsi_14": 48.0, "iv_rank": 84.0,
        "spot": TECH_CLOSE, "sma_200": 400.0, "drawdown_pct": 8.0,
        "days_to_earnings": 60,
        "support_resistance": {
            "supports": [{"price": 450.0, "touches": 3, "strength": 4.0}],
            "resistances": []},
        "csp_entry": {"strike": 450.0, "mid": 9.0, "dte": dte,
                      "expiration": exp or (
                          date.today() + timedelta(days=dte)).isoformat()},
    }


def test_spotlight_demotes_day_color_wait_to_visible_exclusions():
    """"the briefing is not telling me to do it today" — the 🏆 spotlight
    pulls a green-day CSP out of the ENTER list into the visible ⏸
    exclusions with the measured move (rule #24 — never silently dropped,
    never a green-lit slot)."""
    snap = _snap(last=GREEN_LIVE)
    snap["earnings_calendar"] = {"WDC": (
        date.today() + timedelta(days=90)).isoformat()}
    best = sg.collect_best_setups(scout_results=[_wdc_scout_result()],
                                  snapshot_data=snap, config=DAY_CFG)
    assert [e["ticker"] for e in best["csp"]] == []
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert "WDC" in ex
    assert "wait for a red day" in ex["WDC"]["reason"]
    md = "\n".join(sg.render_best_setups(best))
    assert "⏸ WDC" in md and "wait for a red day" in md


def test_spotlight_demotes_theme_stacking_wait_to_visible_exclusions():
    """The SNDK/WDC gap on the spotlight surface: a same-theme held put
    demotes the WDC entry to a visible ⏸ exclusion naming the contract."""
    snap = _snap(last=TECH_CLOSE, positions=[SNDK_PUT])
    snap["earnings_calendar"] = {"WDC": (
        date.today() + timedelta(days=90)).isoformat()}
    best = sg.collect_best_setups(scout_results=[_wdc_scout_result()],
                                  snapshot_data=snap, config=THEME_CFG)
    assert [e["ticker"] for e in best["csp"]] == []
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert "WDC" in ex
    assert "same-theme put already held" in ex["WDC"]["reason"]
    assert "SNDK $1230P" in ex["WDC"]["reason"]


def test_candidate_surface_renders_wait_bucket_not_green_slot():
    """The candidate surface inherits through evaluate_entry: a green-day
    CSP card demotes to the visible '⏸ Held back by the entry algorithm —
    wait' bucket with the measured reason — no 🎯 slot."""
    exp = (date.today() + timedelta(days=36)).isoformat()
    payload = {
        "themes": {"memory_storage": {"name": "Memory & Storage",
                                      "group": "g", "anchors": ["WDC"],
                                      "etfs": []}},
        "results_by_theme": {"memory_storage": [
            {"ticker": "WDC", "spot": TECH_CLOSE, "rsi_14": 48,
             "iv_rank": 84, "sma_200": 400.0, "drawdown_pct": 8,
             "fivedayret_pct": 1.2,
             "verdict": "CSP ENTRY (fat premium)",
             "rationale": ["fat premium"],
             "csp_entry": {"strike": 450.0, "mid": 9.0, "bid": 8.8,
                           "ask": 9.2, "expiration": exp, "dte": 36}},
        ]},
    }
    snap = _snap(last=GREEN_LIVE)
    snap["earnings_calendar"] = {"WDC": (
        date.today() + timedelta(days=90)).isoformat()}
    md = cr.render_candidate_briefing(
        payload, fv_by_ticker={}, config=DAY_CFG, generated_at="T",
        snapshot_data=snap)
    assert "⏸ Held back by the entry algorithm — wait" in md
    assert "wait for a red day" in md
    assert "🎯 Today's Candidates (0)" in md
    assert "Entry (CSP):" not in md


def test_conformance_panel_flags_green_lit_wrong_day_ticket():
    """Conformance agreement: a surface that still green-lights the WDC
    ticket on a green day is flagged WAIT by the panel — no surface may
    say 'do it today' when the canonical evaluator says wait."""
    md = "\n".join([
        "## ✅ Action List",
        "",
        "1. **PULLBACK CSP** `WDC` — SELL 1× WDC $450P exp 2026-09-18 "
        "@ $9.00 (35 DTE) · RSI 48 🟢 pullback",
        "",
    ])
    offenders = ea.audit_conformance(md, _snap(last=GREEN_LIVE), None,
                                     DAY_CFG, as_of=AS_OF)
    assert len(offenders) == 1
    o = offenders[0]
    assert o["ticker"] == "WDC" and o["verdict"] == "WAIT"
    assert o["check"] == "day_color_wait"
    assert "wait for a red day" in o["reason"]
    # Legacy config → the same render is conformant (flag-gated).
    assert ea.audit_conformance(md, _snap(last=GREEN_LIVE), None, {},
                                as_of=AS_OF) == []


def test_capacity_wait_keeps_existing_tagged_presentation():
    """Only day-color/theme WAITs demote to exclusions — a capacity WAIT
    keeps its existing ⏸-tagged slot presentation (rules #24/#41)."""
    snap = _snap(last=RED_LIVE)          # red day — day color passes
    snap["earnings_calendar"] = {"WDC": (
        date.today() + timedelta(days=90)).isoformat()}
    best = sg.collect_best_setups(
        scout_results=[_wdc_scout_result()], snapshot_data=snap,
        config=DAY_CFG, gates_closed=True,
        capacity_tag="⏸ Deferred (capacity gated)")
    assert [e["ticker"] for e in best["csp"]] == ["WDC"]
    assert best["csp"][0]["deferred_tag"] == "⏸ Deferred (capacity gated)"
    assert best["excluded_csp"] == []
