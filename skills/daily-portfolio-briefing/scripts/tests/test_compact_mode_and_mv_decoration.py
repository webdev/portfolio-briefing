"""Regression tests — 2026-08-06 briefing feedback batch.

Two user symptoms (quoted verbatim in the test docstrings per hard rule #33):

1. "From this briefing, it's unclear what is Moneyvest information and what
   is not. I think we should decorate it so it's clear."
   → ONE MV chip grammar (`💰 MV: FV $X · LB $Y · HB $Z · M N.N`) from a
   single formatter, `💰 Moneyvest — ` prefix on wholly-MV blocks, a one-line
   source legend, and never double-decorating a line already carrying 💰.

2. "it seems like it became really long. I just have to scroll and scroll
   and scroll and scroll." (briefing_2026-08-06.md = 2530 lines; LTO 842,
   Candidate Trades 381, Technical Read 315, Watch 227)
   → render.compact (default ON) turns the four longest surfaces into
   compact decision-document views. Rule #24: every demoted item STAYS
   visible as one row/line with its reason. render.compact: false keeps the
   legacy rendering.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import moneyvest_chip as mv  # noqa: E402
from steps import candidate_research as cr  # noqa: E402
from steps.long_term_opportunities import (  # noqa: E402
    actionable_ungated_tickers,
    render_long_term_opportunities,
)
from steps.per_option_commentary import render_watch_with_commentary  # noqa: E402
from steps.technical_read import render_technical_read_sections  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# (a) Moneyvest attribution decoration
# ─────────────────────────────────────────────────────────────────────────────

_MV_PAYLOAD = {
    "shopping_list": [
        {"ticker": "NVDA", "section": "MAG7", "fair_value": 152.0,
         "light_buy": 196.0, "heavy_buy": 160.0, "no_brainer": 130.0},
    ],
    "index": {"sp500": {"value": 3.80, "label": "OPTIMISTIC"}},
    "m_scores": {"NVDA": 4.05},
}


def test_mv_chip_unified_grammar():
    """User: 'unclear what is Moneyvest information and what is not' — the
    chip must self-identify as MV: `💰 MV: FV $152 · LB $196 · HB $160 ·
    M 4.05`, with missing fields omitted (rule #19, never a placeholder)."""
    chip = mv.format_mv_chip(_MV_PAYLOAD["shopping_list"][0], 4.05)
    assert chip == "💰 MV: FV $152 · LB $196 · HB $160 · M 4.05"
    # Missing fields omitted, never placeholder-filled.
    assert mv.format_mv_chip({"section": "x", "light_buy": 200.0}) == "💰 MV: LB $200"
    assert mv.format_mv_chip(None) == ""


def test_mv_anchor_uses_single_formatter_grammar():
    """Strike-anchor notes ('anchored to 💰 Light Buy') must use the ONE
    grammar: `💰 MV Light Buy $182`."""
    assert mv.format_mv_anchor("Light Buy", 182.0) == "💰 MV Light Buy $182"


def test_mv_wholly_sourced_blocks_carry_moneyvest_prefix():
    """Blocks that are WHOLLY Moneyvest-sourced (index sentiment, regime
    advisory) must start with `💰 Moneyvest — `."""
    lines = mv.index_context_lines(_MV_PAYLOAD)
    assert lines[0].startswith("- 💰 Moneyvest — ")
    note = mv.regime_advisory(_MV_PAYLOAD)
    assert note.startswith("💰 Moneyvest — ")


def test_mv_never_double_decorates():
    """A line already carrying 💰 is left alone by the chip annotator."""
    md = "**`NVDA`** · 🅿️ BUY · 💰 MV: FV $152\n"
    assert mv.annotate_mv_chips(md, _MV_PAYLOAD) == md


def test_source_legend_never_gets_annotated_by_chip_passes():
    """Observed on the 2026-08-06 compact re-render: the legend line
    `_Sources: 🅿️ Parkev rec · 🤖 CP-held ... _` came out with a spurious
    `· 🔵 Tier C` — the tier-badge annotator extracted 'CP' as a ticker
    from the italic legend. Italic prose must never be chip-annotated."""
    from analysis.position_tiers import annotate_tier_badges
    legend = mv.source_legend_lines()[0]
    cfg = {"position_tiers": {"NVDA": "A"}}
    out = annotate_tier_badges(legend, cfg)
    assert "Tier" not in out and out == legend
    # MV chip annotator must also leave italic prose alone (a 🅿️-carrying
    # italic footer without a 💰 mark would otherwise get chipped).
    italic = "_Sources: 🅿️ Parkev rec — NVDA legend text_"
    assert mv.annotate_mv_chips(italic + "\n", _MV_PAYLOAD) == italic + "\n"


def test_source_legend_is_one_line_and_names_every_mark():
    legend = mv.source_legend_lines()
    body = [ln for ln in legend if ln.strip()]
    assert len(body) == 1
    line = body[0]
    assert line.startswith("_Sources:") and line.endswith("_")
    for mark in ("🅿️", "🤖", "💰 MV", "💵 FV", "Moneyvest", "Parkev", "FMP"):
        assert mark in line, f"legend missing {mark}"


# ─────────────────────────────────────────────────────────────────────────────
# (b) Compact Long-Term Opportunities
# ─────────────────────────────────────────────────────────────────────────────

_GATE_TAG = ("⏸ Deferred (capacity gated) — stress coverage 0.22× < 0.50× "
             "floor; shown for planning, not a green light (rule #41)")


def _op(ticker, kind="ADD", gated=False, rsi=52):
    trig = [f"✅ RSI favourable · RSI {rsi}; drawdown 15% from 52w high"]
    if gated:
        trig.insert(0, _GATE_TAG)
    return {
        "kind": kind, "ticker": ticker,
        "concrete_trade": f"BUY ~$5,000 of {ticker} (~50 shares @ ~$100.00)",
        "trigger_reasons": trig,
        "rationale": "Pullback in a third-party BUY name.",
        "yield_or_cost": "$5K initial",
        "source": "test",
    }


_COMPACT_CFG = {"render": {"compact": True, "max_lto_cards": 3}}


def test_lto_compact_caps_full_cards_and_keeps_overflow_visible():
    """User: 'I just have to scroll and scroll' — LTO was 842 lines. Compact
    mode caps full cards at render.max_lto_cards; beyond-cap actionables
    STILL appear as one table row each with a reason (rule #24)."""
    ops = [_op(f"TK{i}") for i in range(6)]  # TK0..TK5, all actionable
    md = "\n".join(render_long_term_opportunities(ops, config=_COMPACT_CFG))
    cards = [ln for ln in md.splitlines() if ln.startswith("### ") and "`TK" in ln]
    assert len(cards) == 3
    # Overflow tickers demoted to table rows, present with the cap reason.
    for t in ("TK3", "TK4", "TK5"):
        assert f"| `{t}` |" in md, f"{t} hidden — violates rule #24"
    assert "beyond the top-3 card cap" in md


def test_lto_compact_gated_rows_carry_measured_gate_reason():
    """Capacity-gated ADDs (the 2026-08-06 briefing had ~50 of them, 7 lines
    each) collapse to one row each but keep the MEASURED gate reason."""
    ops = [_op("GOODCO"), _op("GATED", gated=True)]
    md = "\n".join(render_long_term_opportunities(ops, config=_COMPACT_CFG))
    assert "### " in md and "`GOODCO`" in md          # ungated → full card
    assert "| `GATED` |" in md                        # gated → row, visible
    assert "stress coverage 0.22× < 0.50× floor" in md  # measured, not generic
    # gated op never holds a numbered card
    assert not any(ln.startswith("### ") and "GATED" in ln
                   for ln in md.splitlines())


def test_lto_compact_skipped_and_deferred_kinds_still_present():
    ops = [
        _op("ACT"),
        {"kind": "SKIPPED_ADD", "kind_when_skipped": "ADD", "ticker": "SKIP1",
         "quality_notes": ["RSI 44 in band", "broken trend"],
         "concrete_trade": "BUY ~$5,000 of SKIP1"},
        {"kind": "DEFERRED_ADD_HAS_CSP", "kind_when_skipped": "ADD",
         "ticker": "DEFC", "quality_notes": ["RSI 41; you already have a short put"],
         "concrete_trade": "BUY ~$5,000 of DEFC"},
    ]
    md = "\n".join(render_long_term_opportunities(ops, config=_COMPACT_CFG))
    assert "| `SKIP1` |" in md and "broken trend" in md
    assert "| `DEFC` |" in md and "ADD (deferred: held CSP)" in md


def test_lto_compact_management_cards_never_demoted():
    """EXIT/TRIM are position management — full cards even when the
    actionable cap is tiny (trade-validator asymmetry)."""
    cfg = {"render": {"compact": True, "max_lto_cards": 1}}
    ops = [{"kind": "EXIT", "ticker": "TSLA",
            "concrete_trade": "REVIEW TSLA — consider exit",
            "trigger_reasons": ["RSI 37; drawdown 35%"],
            "rationale": "thesis check", "source": "test"},
           _op("AAA"), _op("BBB")]
    md = "\n".join(render_long_term_opportunities(ops, config=cfg))
    assert any(ln.startswith("### ") and "TSLA" in ln for ln in md.splitlines())
    assert "| `BBB` |" in md  # second actionable demoted (cap 1), visible


def test_lto_compact_rows_carry_rsi_read():
    """Every compact table row carries a measured RSI (or explicit n/a) so
    the RSI-coverage audit finds a read inside its ±3-line window."""
    ops = [_op(f"T{i}", gated=True, rsi=40 + i) for i in range(4)]
    md = "\n".join(render_long_term_opportunities(ops, config=_COMPACT_CFG))
    rows = [ln for ln in md.splitlines() if ln.startswith("| `T")]
    assert len(rows) == 4
    assert all("RSI" in r for r in rows)


def test_lto_compact_false_preserves_legacy_rendering():
    """render.compact: false (or no config) → the legacy path, full cards
    for everything, no compact table."""
    ops = [_op(f"TK{i}", gated=(i % 2 == 0)) for i in range(6)]
    legacy = render_long_term_opportunities(ops)
    off = render_long_term_opportunities(
        ops, config={"render": {"compact": False}})
    assert legacy == off
    md = "\n".join(legacy)
    assert "| Ticker |" not in md
    cards = [ln for ln in md.splitlines() if ln.startswith("### ") and "`TK" in ln]
    assert len(cards) == 6


def test_lto_actionable_ungated_ticker_helper():
    ops = [_op("GOOD"), _op("GATED", gated=True),
           {"kind": "SKIPPED_ADD", "ticker": "SKIP"},
           {"kind": "EXIT", "ticker": "MGMT"}]
    got = actionable_ungated_tickers(ops)
    assert got == {"GOOD"}


# ─────────────────────────────────────────────────────────────────────────────
# (b) Compact Watch panel
# ─────────────────────────────────────────────────────────────────────────────

def _hold_review():
    return {"contract": "AMZN_PUT_245_20261016", "recommendation": "HOLD",
            "type": "PUT", "strike": 245.0, "expiration": "2026-10-16",
            "days_to_expiry": 71, "entry_price": 5.0, "current_mid": 4.7,
            "qty": -1, "underlying": "AMZN",
            "rationale": "MODERATE OTM with low profit. Hold for more decay."}


def _roll_review():
    return {"contract": "IREN_PUT_47_20261218", "recommendation": "ROLL_OUT_AND_DOWN",
            "type": "PUT", "strike": 47.0, "expiration": "2026-12-18",
            "days_to_expiry": 134, "entry_price": 18.64, "current_mid": 15.35,
            "qty": -1, "underlying": "IREN",
            "recommended_candidate_id": "D",
            "roll_candidates": [
                {"id": "A", "description": "HOLD (don't roll)", "netDollars": 0,
                 "notes": "Wait for theta"},
                {"id": "D", "description": "1× $37P Jan 15 '27 @ $9.52 +28d",
                 "netDollars": -600, "notes": "Lower strike (−$10)"},
            ]}


_WATCH_SNAP = {"technicals": {"AMZN": {"rsi_14": 63.0}, "IREN": {"rsi_14": 46.0}},
               "quotes": {}}


def test_watch_compact_hold_position_is_one_line_but_present():
    """Watch was 227 lines. A HOLD position collapses to ONE summary line —
    position, P&L, capture %, verdict + one-phrase why — but is NEVER
    dropped (a missing position is a data gap)."""
    md_lines = render_watch_with_commentary([], [_hold_review()], _WATCH_SNAP,
                                            compact=True)
    contract_lines = [ln for ln in md_lines if "AMZN_PUT_245_20261016" in ln]
    assert len(contract_lines) == 1
    line = contract_lines[0]
    assert "→ **HOLD**" in line
    assert "captured" in line and "P&L" in line
    assert "Hold for more decay" in line          # one-phrase why
    assert "RSI 63" in line
    # No multi-line block for the HOLD position.
    assert not any(ln.startswith("  P&L:") for ln in md_lines)


def test_watch_compact_action_position_keeps_roll_analysis():
    """A position whose advisor recommends an action keeps the FULL block
    including the ROLL ANALYSIS candidate table."""
    md = "\n".join(render_watch_with_commentary([], [_roll_review()],
                                                _WATCH_SNAP, compact=True))
    assert "ROLL ANALYSIS" in md
    assert "| D ✅ recommended |" in md


def test_watch_compact_demotion_note_forces_full_block():
    """Demoted rolls surface ONLY in the Watch panel — a HOLD position
    carrying a demotion note must keep the full block."""
    rev = _hold_review()
    rev["_debit_cap_demotion"] = ("Roll demoted (debit cap): $600 debit is "
                                  "16% of collateral")
    md = "\n".join(render_watch_with_commentary([], [rev], _WATCH_SNAP,
                                                compact=True))
    assert "Roll demoted (debit cap)" in md
    assert "  P&L:" in md  # full block, not the one-liner


def test_watch_compact_false_is_legacy():
    legacy = render_watch_with_commentary([], [_hold_review()], _WATCH_SNAP)
    assert any(ln.startswith("  P&L:") for ln in legacy)


def test_watch_compact_no_position_dropped():
    md = "\n".join(render_watch_with_commentary(
        [], [_hold_review(), _roll_review()], _WATCH_SNAP, compact=True))
    assert "AMZN_PUT_245_20261016" in md
    assert "IREN_PUT_47_20261218" in md


# ─────────────────────────────────────────────────────────────────────────────
# (b) Compact Technical Read
# ─────────────────────────────────────────────────────────────────────────────

def _tech_snapshot():
    from analysis import technical_indicators as ti

    def _df(n=500):
        base = 100 * (1.003 ** np.arange(n))
        wobble = 1 + 0.004 * np.sin(np.arange(n) / 5.0)
        closes = pd.Series(base * wobble, dtype=float)
        idx = pd.bdate_range(end="2026-07-02", periods=n)
        return pd.DataFrame({"Open": closes.values,
                             "High": closes.values * 1.01,
                             "Low": closes.values * 0.99,
                             "Close": closes.values,
                             "Volume": 1_000_000.0}, index=idx)

    deep = ti.compute_technicals("AAA", _df(), rsi_14=55.0).to_dict()
    return {
        "technicals": {
            "AAA": {"rsi_14": 55.0, "deep": dict(deep)},
            "BBB": {"rsi_14": 48.0, "deep": dict(deep, rsi_14=48.0)},
        },
        "positions": [
            {"assetType": "EQUITY", "symbol": "AAA", "qty": 100},
            {"assetType": "EQUITY", "symbol": "BBB", "qty": 100},
        ],
    }


def test_technical_read_compact_collapses_only_no_action_tickers():
    """Technical Read was 315 lines. Full card ONLY for tickers with an
    action this cycle; all other holdings collapse to one measured line
    (RSI · trend · verdict)."""
    snap = _tech_snapshot()
    md = "\n".join(render_technical_read_sections(
        snap, {}, compact=True, action_tickers={"AAA"}))
    assert "### AAA — $" in md                      # action name: full card
    assert "### BBB" not in md                       # no-action name: no card
    one_liners = [ln for ln in md.splitlines() if ln.startswith("- **BBB**")]
    assert len(one_liners) == 1
    assert "RSI 48" in one_liners[0]
    assert "Compact view" in md


def test_technical_read_compact_fails_open_without_action_set():
    """action_tickers=None (computation failed upstream) → full legacy
    cards, never silently collapsed."""
    snap = _tech_snapshot()
    md = "\n".join(render_technical_read_sections(
        snap, {}, compact=True, action_tickers=None))
    assert "### AAA — $" in md and "### BBB — $" in md


def test_technical_read_compact_false_is_legacy():
    snap = _tech_snapshot()
    legacy = render_technical_read_sections(snap, {})
    off = render_technical_read_sections(snap, {}, compact=False,
                                         action_tickers={"AAA"})
    assert legacy == off
    assert "### BBB — $" in "\n".join(legacy)


# ─────────────────────────────────────────────────────────────────────────────
# (b) Compact Candidate Trades
# ─────────────────────────────────────────────────────────────────────────────

def _cand_payload():
    return {
        "themes": {"semis": {"name": "Semis", "group": "AI",
                             "anchors": ["AMD", "INTC"], "etfs": []}},
        "results_by_theme": {
            "semis": [
                # ticketed CSP candidate — the actionable ticket
                {"ticker": "AMD", "spot": 150.0, "rsi_14": 40, "iv_rank": 60,
                 "sma_200": 140, "drawdown_pct": 12, "fivedayret_pct": -1.2,
                 "verdict": "CSP ENTRY (fat premium)", "rationale": ["fat premium"],
                 "csp_entry": {"strike": 135, "mid": 2.1, "bid": 2.0, "ask": 2.2,
                               "expiration": "2026-06-19", "dte": 29}},
                # ticketless BUY candidate — collapses to a table row
                {"ticker": "INTC", "spot": 24.0, "rsi_14": 45, "iv_rank": 40,
                 "sma_200": 26, "drawdown_pct": 20, "fivedayret_pct": -2.0,
                 "verdict": "BUY (pullback)", "rationale": ["drawdown + BUY"]},
            ]
        },
    }


def test_candidate_compact_keeps_ticket_collapses_buy_rows():
    """Candidate Trades was 381 lines (42 cards). Compact keeps the live
    CSP entry ticket cards; ticketless BUY candidates become one table row
    each — visible with RSI + verdict (rule #24)."""
    md = cr.render_candidate_briefing(_cand_payload(), fv_by_ticker={},
                                      config={}, generated_at="2026-08-06",
                                      compact=True)
    assert "$135P" in md and "Entry (CSP):" in md   # ticket never demoted
    assert "| `INTC` |" in md                        # BUY candidate → row
    intc_row = [ln for ln in md.splitlines() if ln.startswith("| `INTC`")][0]
    assert "RSI 45" in intc_row and "BUY on pullback" in intc_row
    # Compact cards drop the Verdict/FV elaboration (lives in companion).
    amd_block = md.split("`AMD`")[1].split("| `INTC`")[0]
    assert "Verdict:" not in amd_block
    # Pointer to the companion report.
    assert "candidates_2026-08-06.md" in md


def test_candidate_compact_false_is_legacy():
    legacy = cr.render_candidate_briefing(_cand_payload(), fv_by_ticker={},
                                          config={}, generated_at="T")
    off = cr.render_candidate_briefing(_cand_payload(), fv_by_ticker={},
                                       config={}, generated_at="T",
                                       compact=False)
    assert legacy == off
    assert "| `INTC` |" not in legacy
    assert "Verdict: CSP ENTRY (fat premium)" in legacy
