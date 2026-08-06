"""Rule #43 regression tests — fully-gated LTO cards must not hold numbered
"Trade:" slots (2026-08-03 briefing).

Observed defect: "### 💎 6. LONG DATED CSP · GOOG — Trade: SELL 1× GOOG
$330P..." rendered as a numbered opportunity while carrying FOUR
disqualifiers: ⛔ equity-stacking hard-skip (16.2% NLV), ⏸ capacity gated,
"RSI 48 ⚠ pre-gap — do not trust a favourable read" (vintage guard
invalidated the qualifying RSI on a +8.8% move), and the thin-premium
reconsider note. George read it as a recommendation to sell a put on a
+8.8% green day — the annotations don't overcome the numbered-Trade-card
framing.

Fix: `_mark_reference_demotions` + the renderer's "📎 Shown for reference —
not actionable today" subsection. Demotion fires on (a) equity-stacking
hard-skip, (b) stale qualifying RSI (vintage guard), or (c) ≥2 independent
hard gates. Capacity gate alone NEVER demotes (rules #24 / #41). Numbered
slots renumber cleanly with no gaps. Config kill switch:
`lto_reference_demotion.enabled: false`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps.long_term_opportunities import (  # noqa: E402
    _annotate_equity_stacking,
    _mark_reference_demotions,
    render_long_term_opportunities,
)

_NUMBERED_HEADER_RE = re.compile(r"^### \S+ (\d+)\. ", re.MULTILINE)


# ── Fixture builders ──────────────────────────────────────────────────────


def _goog_op(**kw):
    """The observed GOOG card shape (2026-08-03)."""
    op = {
        "kind": "LONG_DATED_CSP",
        "ticker": "GOOG",
        "concrete_trade": "SELL 1× GOOG $330P exp Fri Oct 16 '26 (74 DTE)",
        "trigger_reasons": ["✅ RSI favourable · RSI 48 (pullback band)"],
        "rationale": "74-DTE horizon below a tested support shelf",
        "yield_or_cost": "premium ~$610 on $33,000 collateral",
        "source": "third-party BUY + technical setup",
    }
    op.update(kw)
    return op


def _clean_op(ticker, strike=200.0):
    return {
        "kind": "LONG_DATED_CSP",
        "ticker": ticker,
        "concrete_trade": f"SELL 1× {ticker} ${strike:.0f}P exp Fri Oct 16 '26",
        "trigger_reasons": [f"RSI 45 (pullback band)"],
        "rationale": "clean setup",
        "yield_or_cost": "premium ~$400",
    }


def _snapshot(
    goog_equity_pct=0.0,
    goog_tech_close=300.0,
    goog_live=None,
    nlv=1_000_000.0,
):
    """Snapshot with optional GOOG equity stake and optional live-quote gap."""
    positions = []
    if goog_equity_pct:
        positions.append({"assetType": "EQUITY", "symbol": "GOOG",
                          "price": 100.0, "qty": goog_equity_pct * nlv / 100.0})
    snap = {
        "balance": {"accountValue": nlv, "cash": 300_000.0},
        "positions": positions,
        "technicals": {"GOOG": {"spot": goog_tech_close, "rsi_14": 48.0}},
        "quotes": {},
    }
    if goog_live is not None:
        snap["quotes"]["GOOG"] = {"last": goog_live}
    return snap


# ── Tests ─────────────────────────────────────────────────────────────────


def test_hard_skip_card_demotes_to_reference():
    """Observed: '### 💎 6. LONG DATED CSP · GOOG — Trade: SELL 1× GOOG
    $330P...' held a numbered slot while carrying '⛔ equity-stacking
    hard-skip zone — you hold 16.2% NLV in GOOG equity'. The ⛔ hard-skip
    alone must demote the card out of the numbered list into the
    '📎 Shown for reference — not actionable today' subsection."""
    op = _goog_op()
    snap = _snapshot(goog_equity_pct=16.2)
    _annotate_equity_stacking([op], snap, {})
    assert any("⛔ equity-stacking hard-skip" in str(t)
               for t in op["trigger_reasons"])
    _mark_reference_demotions([op], snap, {})
    assert op.get("reference_demoted") is True
    assert "equity-stacking hard-skip" in op["reference_reason"]

    md = "\n".join(render_long_term_opportunities([op]))
    # No numbered slot anywhere on the GOOG card.
    for m in _NUMBERED_HEADER_RE.finditer(md):
        line = md[m.start():md.index("\n", m.start())]
        assert "GOOG" not in line, f"GOOG still holds a numbered slot: {line}"
    assert "### 📎 Shown for reference — not actionable today" in md
    assert "LONG DATED CSP · `GOOG` — reference only" in md
    assert "NOT a recommendation to place today" in md


def test_stale_qualifying_rsi_demotes():
    """Observed: the GOOG card's qualifying trigger read 'RSI 48 ⚠ pre-gap
    — do not trust a favourable read' — the vintage guard invalidated the
    RSI on a +8.8% move, yet the card kept its numbered Trade slot. A
    stale qualifying RSI (move > vintage_guard threshold) must demote."""
    op = _goog_op()
    # Tech close 300 → live 326.4 = +8.8% (past the 5% vintage threshold).
    snap = _snapshot(goog_tech_close=300.0, goog_live=326.4)
    _mark_reference_demotions([op], snap, {})
    assert op.get("reference_demoted") is True
    assert "stale qualifying RSI" in op["reference_reason"]
    assert "+8.8%" in op["reference_reason"]

    md = "\n".join(render_long_term_opportunities([op]))
    assert "reference only" in md
    assert not any("GOOG" in md[m.start():md.index("\n", m.start())]
                   for m in _NUMBERED_HEADER_RE.finditer(md))


def test_capacity_only_deferred_card_stays_numbered():
    """Capacity gate alone does NOT demote — a '⏸ Deferred (capacity
    gated)' card keeps its numbered planning value (rules #24 / #41).
    Only stacked gates or the (a)/(b) hard disqualifiers demote."""
    op = _goog_op(capacity_deferred=True)
    op["trigger_reasons"].insert(0, "⏸ Deferred (capacity gated) — stress "
                                    "coverage 0.16× < 0.50× floor")
    snap = _snapshot()  # no equity stake, no quote gap
    _mark_reference_demotions([op], snap, {})
    assert not op.get("reference_demoted")

    md = "\n".join(render_long_term_opportunities([op]))
    assert "### 💎 1. LONG DATED CSP · `GOOG`" in md   # numbered, actionable slot
    assert "Shown for reference" not in md

    # But capacity + a second hard gate (equity-stacking ⛔) = ≥2 gates → demote.
    op2 = _goog_op(capacity_deferred=True)
    snap2 = _snapshot(goog_equity_pct=16.2)
    _annotate_equity_stacking([op2], snap2, {})
    _mark_reference_demotions([op2], snap2, {})
    assert op2.get("reference_demoted") is True
    assert "capacity gated" in op2["reference_reason"]


def test_reference_section_preserves_ticket():
    """Hard rule #24 — demotion, never suppression. The reference card
    keeps the FULL ticket (trade, triggers incl. the ⛔ annotation, yield,
    source) plus the one-line 'Why not actionable' reason, e.g.
    'equity-stacking hard-skip + stale qualifying RSI'."""
    op = _goog_op(capacity_deferred=True)
    snap = _snapshot(goog_equity_pct=16.2, goog_tech_close=300.0,
                     goog_live=326.4)
    _annotate_equity_stacking([op], snap, {})
    _mark_reference_demotions([op], snap, {})
    assert op["reference_reason"] == (
        "equity-stacking hard-skip + stale qualifying RSI "
        "(spot moved +8.8% since computation) + capacity gated")

    md = "\n".join(render_long_term_opportunities([op]))
    assert "**Why not actionable:** equity-stacking hard-skip + stale qualifying RSI" in md
    assert "**Trade (reference only):** SELL 1× GOOG $330P exp Fri Oct 16 '26" in md
    assert "⛔ equity-stacking hard-skip zone" in md
    assert "premium ~$610 on $33,000 collateral" in md
    assert "third-party BUY + technical setup" in md


def test_numbering_no_gaps_after_demotion():
    """Numbered slots must renumber cleanly — demoting the observed
    '💎 6.' GOOG card must never leave a gap in the sequence."""
    ops = [_clean_op("MSFT"), _goog_op(), _clean_op("NVDA")]
    snap = _snapshot(goog_equity_pct=16.2)
    _annotate_equity_stacking(ops, snap, {})
    _mark_reference_demotions(ops, snap, {})
    md = "\n".join(render_long_term_opportunities(ops))
    numbers = [int(n) for n in _NUMBERED_HEADER_RE.findall(md)]
    assert numbers == [1, 2], f"numbered slots must be contiguous, got {numbers}"
    assert "### 💎 1. LONG DATED CSP · `MSFT`" in md
    assert "### 💎 2. LONG DATED CSP · `NVDA`" in md
    assert "LONG DATED CSP · `GOOG` — reference only" in md
    # The count line reflects only the numbered signals.
    assert "_2 signal(s)" in md


def test_config_kill_switch():
    """`lto_reference_demotion: {enabled: false}` disables the demotion
    entirely (legacy rendering); the per-gate toggles disable their
    single-gate paths."""
    snap = _snapshot(goog_equity_pct=16.2, goog_tech_close=300.0,
                     goog_live=326.4)

    # Master kill switch.
    op = _goog_op()
    _annotate_equity_stacking([op], snap, {})
    _mark_reference_demotions([op], snap,
                              {"lto_reference_demotion": {"enabled": False}})
    assert not op.get("reference_demoted")
    md = "\n".join(render_long_term_opportunities([op]))
    assert "### 💎 1. LONG DATED CSP · `GOOG`" in md
    assert "Shown for reference" not in md

    # hard_skip_demotes: false — hard-skip alone no longer demotes…
    op2 = _goog_op()
    snap_hard_only = _snapshot(goog_equity_pct=16.2)
    _annotate_equity_stacking([op2], snap_hard_only, {})
    _mark_reference_demotions(
        [op2], snap_hard_only,
        {"lto_reference_demotion": {"hard_skip_demotes": False}})
    assert not op2.get("reference_demoted")

    # …but stale_rsi still does on its own toggle.
    op3 = _goog_op()
    _mark_reference_demotions(
        [op3], _snapshot(goog_tech_close=300.0, goog_live=326.4),
        {"lto_reference_demotion": {"hard_skip_demotes": False}})
    assert op3.get("reference_demoted") is True

    # stale_rsi_demotes: false symmetric case.
    op4 = _goog_op()
    _mark_reference_demotions(
        [op4], _snapshot(goog_tech_close=300.0, goog_live=326.4),
        {"lto_reference_demotion": {"stale_rsi_demotes": False}})
    assert not op4.get("reference_demoted")
