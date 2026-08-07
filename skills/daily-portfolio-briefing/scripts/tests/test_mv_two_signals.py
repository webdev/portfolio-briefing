"""USER DECISION 2026-08-06 — the TWO approved Moneyvest signals.

George: "Do you take into consideration money-vested fair value prices?
What are you actually using it for, other than just displaying it?"

He approved wiring exactly TWO Moneyvest signals into recommendation
logic (and explicitly DECLINED M-Score gating and sentiment gating):

  Feature 1 — FV conviction modulation (``analysis/mv_conviction.py``,
  mirroring the CP agreement-bonus architecture).
  Feature 2 — Heavy-Buy scale-in ladder (trigger text + entry-card
  ladder + LT_CSP deeper-rung anchoring).

Both are config-gated; flags off → byte-identical legacy behavior.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import rsi_discipline  # noqa: E402
from analysis.moneyvest_chip import format_hb_ladder  # noqa: E402
from analysis.mv_conviction import (  # noqa: E402
    DIVERGENCE_NOTE,
    add_demotion_reason,
    apply_mv_fv_gate_to_lto,
    fv_sources_diverge,
    mv_fair_value_for,
    mv_fv_adjustment,
)
from steps.candidate_research import _format_card  # noqa: E402
from steps.when_to_enter import classify  # noqa: E402


CFG_ON = {"moneyvest": {"fv_conviction": {"enabled": True,
                                          "min_discount_pct": 10},
                        "hb_ladder": {"enabled": True}}}
CFG_OFF = {"moneyvest": {"fv_conviction": {"enabled": False},
                         "hb_ladder": {"enabled": False}}}


# ── Feature 1 — pure function (the CP-bonus pattern) ──────────────────────

def test_bonus_fires_at_discount_with_catalyst():
    """(a) spot ≥ min_discount below MV FV AND a buy catalyst → +1 with the
    visible note '💰 MV FV $X — spot Y% below fair value'."""
    delta, note = mv_fv_adjustment(80.0, 100.0, has_buy_catalyst=True,
                                   config=CFG_ON)
    assert delta == 1.0
    assert note == "💰 MV FV $100 — spot 20% below fair value"


def test_no_bonus_without_catalyst():
    """(b) a discount ALONE never qualifies — corroboration only, same
    discipline as the CP agreement bonus."""
    delta, note = mv_fv_adjustment(80.0, 100.0, has_buy_catalyst=False,
                                   config=CFG_ON)
    assert delta == 0.0 and note is None


def test_discount_below_threshold_no_bonus():
    """A 5% discount with a 10% threshold is not a signal."""
    delta, note = mv_fv_adjustment(95.0, 100.0, has_buy_catalyst=True,
                                   config=CFG_ON)
    assert delta == 0.0 and note is None


def test_above_fv_caps_csp_conviction_one_notch():
    """(c) spot > MV FV → new-CSP conviction capped: −1 at most, with the
    visible cap reason (the CSP itself stays actionable — assignment
    happens below spot; callers clamp at their floor)."""
    delta, note = mv_fv_adjustment(110.0, 100.0, has_buy_catalyst=True,
                                   config=CFG_ON)
    assert delta == -1.0
    assert note == "💰 spot above MV FV $100 — conviction capped (no FV bonus)"


def test_divergence_forfeits_bonus_and_cap():
    """(d) FMP-vs-MV divergence > 40% → forfeit entirely with the note
    'FV sources disagree — no FV conviction adjustment' (uncertainty ≠
    signal) — on BOTH sides (bonus and cap), and the ADD demotion too."""
    d1, n1 = mv_fv_adjustment(80.0, 100.0, fmp_divergence_flag=True,
                              has_buy_catalyst=True, config=CFG_ON)
    assert d1 == 0.0 and n1 == DIVERGENCE_NOTE
    d2, n2 = mv_fv_adjustment(110.0, 100.0, fmp_divergence_flag=True,
                              config=CFG_ON)
    assert d2 == 0.0 and n2 == DIVERGENCE_NOTE
    assert add_demotion_reason(110.0, 100.0, fmp_divergence_flag=True,
                               config=CFG_ON) is None
    # The measurement itself matches the moneyvest_chip 40% threshold.
    assert fv_sources_diverge(100.0, 30.0)
    assert not fv_sources_diverge(100.0, 90.0)
    assert not fv_sources_diverge(100.0, None)  # unmeasurable → no flag


def test_fail_closed_missing_fv_and_etf():
    """(e) missing MV FV / ETF row (no company FV) / missing spot → delta 0,
    no note — never a fabricated number (rule #19)."""
    assert mv_fv_adjustment(100.0, None, has_buy_catalyst=True,
                            config=CFG_ON) == (0.0, None)
    assert mv_fv_adjustment(None, 100.0, has_buy_catalyst=True,
                            config=CFG_ON) == (0.0, None)
    # ETF rows carry no company fair value.
    assert mv_fair_value_for({"section": "etf_watch", "fair_value": 500}) is None
    assert mv_fair_value_for({"section": "stocks", "fair_value": 500}) == 500.0
    assert add_demotion_reason(110.0, None, config=CFG_ON) is None


def test_flags_off_is_legacy_identical_pure():
    """(i) fv_conviction disabled (or config absent) → the function is a
    no-op even on a textbook discount + catalyst."""
    assert mv_fv_adjustment(80.0, 100.0, has_buy_catalyst=True,
                            config=CFG_OFF) == (0.0, None)
    assert mv_fv_adjustment(80.0, 100.0, has_buy_catalyst=True,
                            config=None) == (0.0, None)
    assert add_demotion_reason(110.0, 100.0, config=None) is None


# ── Feature 1 — LTO surface (ADD demote / CSP cap / management untouched) ─

def _lto_ops():
    return [
        {"kind": "ADD", "ticker": "RICH", "trigger_reasons": [],
         "concrete_trade": "BUY ~$5,000 of RICH"},
        {"kind": "LONG_DATED_CSP", "ticker": "RICH", "trigger_reasons": [],
         "concrete_trade": "SELL 1× RICH $95P ~75 DTE"},
        {"kind": "ADD", "ticker": "CHEAP", "trigger_reasons": [],
         "concrete_trade": "BUY ~$5,000 of CHEAP"},
        {"kind": "TRIM", "ticker": "RICH", "trigger_reasons": []},
        {"kind": "EXIT", "ticker": "RICH", "trigger_reasons": []},
    ]


_MV_ROWS = {"RICH": {"section": "stocks", "fair_value": 100.0},
            "CHEAP": {"section": "stocks", "fair_value": 100.0}}
_SPOTS = {"RICH": 110.0, "CHEAP": 80.0}
_RECS = {"RICH": "BUY", "CHEAP": "BUY"}


def test_lto_above_fv_demotes_add_with_visible_reason_and_caps_csp():
    """(c) NEW equity ADD above MV FV → kind SKIPPED_MV_FV with a visible
    'spot above 💰 MV FV $X' reason (rule #24 — demoted, never hidden);
    the LONG_DATED_CSP on the same rich name STAYS actionable and carries
    the conviction-cap annotation instead."""
    ops = apply_mv_fv_gate_to_lto(_lto_ops(), mv_rows=_MV_ROWS, spots=_SPOTS,
                                  recs_normalized=_RECS, config=CFG_ON)
    add_rich = next(o for o in ops if o["ticker"] == "RICH"
                    and o.get("kind_when_skipped") == "ADD")
    assert add_rich["kind"] == "SKIPPED_MV_FV"
    assert "above 💰 MV FV $100" in add_rich["skip_reason"]
    csp_rich = next(o for o in ops if o["kind"] == "LONG_DATED_CSP")
    assert csp_rich["ticker"] == "RICH"  # NOT demoted — still actionable
    assert any("conviction capped" in t for t in csp_rich["trigger_reasons"])


def test_lto_bonus_note_on_discounted_add():
    """(a) the CHEAP name (20% below FV, BUY catalyst) gets the +1 note."""
    ops = apply_mv_fv_gate_to_lto(_lto_ops(), mv_rows=_MV_ROWS, spots=_SPOTS,
                                  recs_normalized=_RECS, config=CFG_ON)
    add_cheap = next(o for o in ops if o["ticker"] == "CHEAP")
    assert add_cheap["kind"] == "ADD"
    assert any("💰 MV FV $100 — spot 20% below fair value" in t
               for t in add_cheap["trigger_reasons"])
    assert add_cheap.get("mv_fv_bonus") == 1.0


def test_lto_never_fires_on_position_management():
    """(f) TRIM / EXIT (position management) are untouched even on a name
    trading above MV FV — the FV signal is conviction-on-new-opens only."""
    ops = apply_mv_fv_gate_to_lto(_lto_ops(), mv_rows=_MV_ROWS, spots=_SPOTS,
                                  recs_normalized=_RECS, config=CFG_ON)
    trim = next(o for o in ops if o["kind"] == "TRIM")
    exit_ = next(o for o in ops if o["kind"] == "EXIT")
    assert trim["trigger_reasons"] == [] and "skip_reason" not in trim
    assert exit_["trigger_reasons"] == [] and "skip_reason" not in exit_


def test_lto_flags_off_identity():
    """(i) fv_conviction disabled → apply_mv_fv_gate_to_lto is a byte-for-
    byte identity pass."""
    baseline = _lto_ops()
    ops = apply_mv_fv_gate_to_lto(_lto_ops(), mv_rows=_MV_ROWS, spots=_SPOTS,
                                  recs_normalized=_RECS, config=CFG_OFF)
    assert ops == baseline


# ── Feature 1+2 — candidate card surface ──────────────────────────────────

_RSI_TH = rsi_discipline.load_thresholds(None)


def _csp_result(spot=80.0, verdict="CSP ENTRY"):
    return {"ticker": "TST", "spot": spot, "verdict": verdict,
            "rsi_14": 45.0, "iv_rank": 60.0, "drawdown_pct": 12.0,
            "third_party_rec": "BUY", "rating_tier": 3,
            "csp_entry": {"strike": 75, "expiration": "2026-10-16",
                          "dte": 71, "mid": 2.5, "bid": 2.4, "ask": 2.6}}


def _card(r, mv_row, config, fv=None):
    return "\n".join(_format_card(r, {"TST": fv} if fv else {}, set(),
                                  _RSI_TH, mv_row=mv_row, config=config))


def test_card_bonus_note_and_ladder():
    """(a)+(h) a CSP candidate 20% below MV FV with a BUY catalyst shows the
    FV note AND the one-line LB→HB ladder with real values."""
    mv_row = {"section": "stocks", "fair_value": 100.0,
              "light_buy": 78.0, "heavy_buy": 66.0}
    out = _card(_csp_result(spot=80.0), mv_row, CFG_ON)
    assert "💰 MV FV $100 — spot 20% below fair value" in out
    assert "Ladder: 💰 LB $78 (first tranche) → HB $66 (scale-in)" in out
    assert "Entry (CSP):" in out  # ticket untouched


def test_card_above_fv_caps_csp_but_keeps_ticket():
    """(c) CSP candidate above MV FV → cap note, ticket STAYS actionable
    (assignment happens below spot)."""
    mv_row = {"section": "stocks", "fair_value": 70.0}
    out = _card(_csp_result(spot=80.0), mv_row, CFG_ON)
    assert "💰 spot above MV FV $70 — conviction capped" in out
    assert "Entry (CSP):" in out


def test_card_above_fv_demotes_equity_buy_visibly():
    """(c) NEW equity BUY above MV FV → the entry line demotes to a visible
    ⏸ Deferred row with the 'spot above 💰 MV FV $X' reason — never
    hidden (rule #24)."""
    r = _csp_result(spot=80.0, verdict="BUY the pullback")
    r.pop("csp_entry")
    out = _card(r, {"section": "stocks", "fair_value": 70.0}, CFG_ON)
    assert "⏸ **Deferred — spot $80.00 above 💰 MV FV $70" in out
    assert "Entry (equity):" not in out


def test_card_divergence_forfeits():
    """(d) MV FV $100 vs FMP DCF $30 (>40% divergence) → no bonus, only the
    'FV sources disagree' note."""
    mv_row = {"section": "stocks", "fair_value": 100.0}
    out = _card(_csp_result(spot=80.0), mv_row, CFG_ON, fv={"dcf": 30.0})
    assert DIVERGENCE_NOTE in out
    assert "below fair value" not in out


def test_card_flags_off_identical():
    """(i) both flags off → the card is byte-identical with or without the
    Moneyvest row."""
    mv_row = {"section": "stocks", "fair_value": 100.0,
              "light_buy": 78.0, "heavy_buy": 66.0}
    assert _card(_csp_result(), mv_row, CFG_OFF) == _card(_csp_result(), None,
                                                          CFG_OFF)


def test_card_etf_row_fail_closed():
    """(e) an ETF shopping-list row has no company FV → no note, no ladder
    fabricated from an index row."""
    mv_row = {"section": "etf_watch", "fair_value": 500.0}
    assert _card(_csp_result(), mv_row, CFG_ON) == _card(_csp_result(), None,
                                                         CFG_ON)


# ── Feature 2 — when_to_enter HB trigger text ─────────────────────────────

def _wte_result(rsi=75.0):
    return {"ticker": "TST", "rsi_14": rsi, "verdict": "WATCH",
            "spot": 100.0, "sma_200": 90.0, "drawdown_pct": 5.0,
            "fivedayret_pct": 2.0, "iv_rank": 50.0}


def test_wte_hb_trigger_cites_real_number():
    """(g) overbought WAIT trigger cites the REAL Heavy Buy below spot:
    'Scale in at $82 (💰 MV Heavy Buy).'"""
    _, label, _, trigger = classify(_wte_result(),
                                    mv_row={"heavy_buy": 82.0})
    assert "WAIT" in label
    assert "Scale in at $82 (💰 MV Heavy Buy)." in trigger


def test_wte_no_hb_no_mention():
    """(g) no Heavy Buy (or HB above spot) → the trigger is byte-identical
    to legacy — no fabricated level."""
    base = classify(_wte_result())
    assert classify(_wte_result(), mv_row=None) == base
    assert classify(_wte_result(), mv_row={}) == base
    assert classify(_wte_result(), mv_row={"heavy_buy": 120.0}) == base
    assert "Heavy Buy" not in base[3]


def test_wte_extended_band_gets_hb_too():
    """(g) the 60-70 'extended' WAIT band also cites the HB rung."""
    _, _, _, trigger = classify(_wte_result(rsi=64.0),
                                mv_row={"heavy_buy": 82.0})
    assert "Scale in at $82 (💰 MV Heavy Buy)." in trigger


def test_wte_buy_entry_shows_support_and_hb():
    """(g) ENTRY-BUY scale guidance merges the chart support AND the MV
    Heavy Buy when both are known (prefer phrasing that shows both)."""
    r = {"ticker": "TST", "rsi_14": 45.0, "verdict": "BUY the pullback",
         "spot": 100.0, "sma_200": 95.0, "drawdown_pct": 12.0,
         "fivedayret_pct": -3.0, "iv_rank": 55.0}
    sr = {"supports": [{"price": 92.0, "strength": 3.0, "touches": 3,
                        "side": "support", "source": "swing"}],
          "resistances": []}
    _, _, _, trigger = classify(r, sr=sr, mv_row={"heavy_buy": 85.0})
    assert "$92" in trigger and "scale in at $85 (💰 MV Heavy Buy)" in trigger


# ── Feature 2 — ladder grammar (single source of truth) ───────────────────

def test_hb_ladder_grammar_real_values_only():
    """(h) ladder renders only when BOTH rungs exist and HB < LB."""
    assert format_hb_ladder(196.0, 160.0) == \
        "Ladder: 💰 LB $196 (first tranche) → HB $160 (scale-in)"
    assert format_hb_ladder(None, 160.0) is None
    assert format_hb_ladder(196.0, None) is None
    assert format_hb_ladder(160.0, 196.0) is None  # inverted → not a ladder
