"""Regression tests — the $37.7K NLV false alarm of 2026-08-28.

Observed in the real 2026-08-28 briefing, TWO unlinked mysteries that were
the SAME number:

    "🔴 NLV data-integrity check: computed NLV $1,077,578 vs broker
    $1,115,357 — Δ $-37,779 (3.4%); using the broker figure."

    "Unattributed (residual): +$37,778 ⚠️ (large residual — balance vs
    positions disagree; investigate)"

Root cause (from the 2026-08-28 snapshot itself): NOT a missing position,
NOT a duplicated MSFT roll leg, NOT a stale mark — every one of the 43
positions carried a real mark, and the day-over-day diff showed exactly the
five known opens (MSFT $530C ×2, META $700C, SMH $600C, SNDK $1030P,
ZS $210C) replacing the closed MSFT $510C ×2. The whole delta sat on the
CASH leg: the adapter reads E*TRADE's `cashAvailableForInvestment`
($46,500.95), a buying-power figure that EXCLUDES unsettled same-day trade
proceeds, while the broker's own components implied ledger cash of
$1,110,221.74 − $1,114,599.87 − (−$88,576.50) = $84,198.37 — a gap of
exactly $37,697.42 = the reconciliation delta.

Fix (rule #19 — name what's missing, never a bare Δ):
- the adapter also captures `netCash` (total ledger cash) and
  `_compose_balance` prefers it for the computed NLV;
- missing option marks are estimated from the snapshot's own mid, or
  excluded WITH a named note ("computed excludes ... — no mark this
  snapshot");
- when the warning still fires with every position marked, the record says
  so: the Δ is by elimination a cash-ledger gap, with implied vs reported
  cash both named;
- the attribution's unattributed-residual flag cross-references the same
  itemization instead of a bare "investigate".
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps.snapshot_inputs import _compose_balance  # noqa: E402
from render.panels import render_header  # noqa: E402
from render.benchmark_panel import _render_attribution  # noqa: E402


# The real 2026-08-28 snapshot numbers.
_LONG_MV = 1_114_599.87
_OPT_MV = -88_576.50
_CASH_AVAILABLE = 46_500.95   # cashAvailableForInvestment — understated
_NET_CASH = 84_198.37         # ledger cash implied by broker components
_BROKER_NLV = 1_110_221.74


def _marked_positions():
    """A fully-marked book (every position carries a real mark)."""
    return [
        {"assetType": "OPTION", "symbol": "MSFT_CALL_530_20270319",
         "symbolDescription": "MSFT Mar 19 '27 $530 Call", "qty": -2.0,
         "marketValue": -9020.0, "currentMid": 45.10},
        {"assetType": "EQUITY", "symbol": "MSFT", "qty": 200.0,
         "price": 512.0, "marketValue": 102_400.0},
    ]


def test_net_cash_ledger_reconciles_the_2026_08_28_false_alarm():
    """With the adapter's netCash present, the computed NLV must use ledger
    cash and RECONCILE — the 2026-08-28 '🔴 NLV data-integrity check:
    computed NLV $1,077,578 vs broker $1,115,357 — Δ $-37,779 (3.4%)' line
    must not fire, and the netCash-vs-available gap is itemized.
    """
    base = {"cash": _CASH_AVAILABLE, "netCash": _NET_CASH,
            "totalAccountValue": _BROKER_NLV}
    bal = _compose_balance(base, _LONG_MV, _OPT_MV,
                           positions=_marked_positions())
    rec = bal["nlv_reconciliation"]
    assert rec["warning"] is False
    assert rec["computed_nlv"] == round(_LONG_MV + _OPT_MV + _NET_CASH, 2)
    assert abs(rec["delta"]) < 1.0
    # The substitution is named, never silent (rule #19)
    notes = " | ".join(rec.get("itemized") or [])
    assert "netCash" in notes
    assert f"${_NET_CASH:,.0f}" in notes
    assert f"${_CASH_AVAILABLE:,.0f}" in notes


def test_net_cash_present_but_zero_falls_back_to_cash():
    """A netCash of exactly 0 alongside positive available cash is a payload
    inconsistency (available cash cannot exceed total cash) — fall back to
    the `cash` field rather than computing NLV with $0 cash.
    """
    base = {"cash": 50_000.0, "netCash": 0.0,
            "totalAccountValue": 1_000_000.0}
    bal = _compose_balance(base, 960_000.0, -10_000.0)
    assert bal["computedAccountValue"] == round(
        960_000.0 - 10_000.0 + 50_000.0, 2)


def test_cash_ledger_gap_is_named_when_all_positions_carry_marks():
    """The exact 2026-08-28 shape (no netCash captured, every position
    marked): the warning fires, but instead of the bare '🔴 ... Δ $-37,779
    (3.4%); using the broker figure.' the record must CLASSIFY the delta —
    all positions carry marks, so the Δ is a cash-ledger gap with implied
    ($84,198) vs reported ($46,501) cash both named. Not an 'unknown
    disagreement'.
    """
    base = {"cash": _CASH_AVAILABLE, "totalAccountValue": _BROKER_NLV}
    bal = _compose_balance(base, _LONG_MV, _OPT_MV,
                           positions=_marked_positions())
    rec = bal["nlv_reconciliation"]
    assert rec["warning"] is True
    assert rec["delta"] == round(
        (_LONG_MV + _OPT_MV + _CASH_AVAILABLE) - _BROKER_NLV, 2)
    notes = " | ".join(rec.get("itemized") or [])
    assert "cash-ledger gap" in notes
    assert "$84,198" in notes  # implied cash, named
    assert "$46,501" in notes  # reported cash, named
    assert "unsettled" in notes

    # Header renders the 🔴 line AND the itemization underneath it
    lines = render_header("2026-08-28", "NORMAL", bal["accountValue"],
                          bal["cash"], 3, balance=bal)
    md = "\n".join(lines)
    assert "🔴" in md
    assert "↳" in md
    assert "cash-ledger gap" in md


def test_missing_mark_contract_gets_named_exclusion_line():
    """A new/rolled contract with NO mark and NO mid must be EXCLUDED from
    the computed figure with a named note — 'computed excludes MSFT Mar 19
    '27 $530 Call — no mark this snapshot' — so the check reads 'data gap
    on 1 known position', never a bare Δ (rule #19). The cash-ledger
    classification must NOT fire (the book is not fully marked).
    """
    positions = [
        {"assetType": "OPTION", "symbol": "MSFT_CALL_530_20270319",
         "symbolDescription": "MSFT Mar 19 '27 $530 Call", "qty": -2.0,
         "marketValue": None, "currentMid": None, "bid": None, "ask": None},
    ]
    # Broker includes the -$40K liability the computed figure can't see.
    base = {"cash": 50_000.0, "totalAccountValue": 1_000_000.0}
    bal = _compose_balance(base, 1_000_000.0, -10_000.0, positions=positions)
    rec = bal["nlv_reconciliation"]
    assert rec["warning"] is True
    notes = rec.get("itemized") or []
    assert any(
        "computed excludes MSFT Mar 19 '27 $530 Call — no mark this snapshot"
        in n for n in notes)
    assert not any("cash-ledger gap" in n for n in notes)

    lines = render_header("2026-08-28", "NORMAL", bal["accountValue"],
                          bal["cash"], 3, balance=bal)
    md = "\n".join(lines)
    assert "computed excludes MSFT Mar 19 '27 $530 Call" in md


def test_missing_mark_estimated_from_snapshot_mid_reconciles():
    """A missing mark WITH a live mid must be estimated from the snapshot's
    own quote (mid $200.00 × 100 × -2 = -$40,000), reconciling the check —
    and the estimate is named, never silent.
    """
    positions = [
        {"assetType": "OPTION", "symbol": "MSFT_CALL_530_20270319",
         "symbolDescription": "MSFT Mar 19 '27 $530 Call", "qty": -2.0,
         "marketValue": None, "currentMid": 200.0},
    ]
    base = {"cash": 50_000.0, "totalAccountValue": 1_000_000.0}
    bal = _compose_balance(base, 1_000_000.0, -10_000.0, positions=positions)
    rec = bal["nlv_reconciliation"]
    assert rec["warning"] is False
    assert bal["computedAccountValue"] == 1_000_000.0
    assert bal["optionMarketValue"] == -50_000.0
    notes = " | ".join(rec.get("itemized") or [])
    assert "mark estimated" in notes
    assert "MSFT Mar 19 '27 $530 Call" in notes


def test_clean_snapshot_no_noise():
    """A clean snapshot (computed matches broker within 1%, all marks real,
    one genuinely worthless $0-mark option with no mid) must render NO 🔴
    line, NO itemization, NO exclusion note — the 2026-08-27 baseline
    (Δ -$51.71) stays silent.
    """
    positions = [
        {"assetType": "OPTION", "symbol": "AMAT_PUT_470_20260925",
         "symbolDescription": "AMAT Sep 25 '26 $470 Put", "qty": -1.0,
         "marketValue": -2655.0, "currentMid": 26.55},
        # Genuinely worthless: $0 mark, no mid — must NOT produce a note.
        {"assetType": "OPTION", "symbol": "XYZ_PUT_5_20260904",
         "symbolDescription": "XYZ Sep 4 '26 $5 Put", "qty": -1.0,
         "marketValue": 0.0, "currentMid": 0.0, "bid": 0.0, "ask": 0.0},
    ]
    base = {"cash": 71_760.18, "totalAccountValue": 1_107_347.07}
    bal = _compose_balance(base, 1_112_382.37, -76_786.50,
                           positions=positions)
    rec = bal["nlv_reconciliation"]
    assert rec["warning"] is False
    assert "itemized" not in rec
    lines = render_header("2026-08-27", "NORMAL", bal["accountValue"],
                          bal["cash"], 3, balance=bal)
    md = "\n".join(lines)
    assert "🔴" not in md
    assert "↳" not in md
    assert "excludes" not in md


def _attribution_with_residual(unattr: float):
    return {
        "status": "ok",
        "periods": [{
            "name": "daily",
            "start_date": "2026-08-27",
            "nlv_change": 2_874.67,
            "nlv_start": 1_107_347.07,
            "nlv_end": 1_110_221.74,
            "buckets": {},
            "unattributed": unattr,
        }],
    }


def test_attribution_residual_cross_references_integrity_itemization():
    """When the header's integrity check carries an itemization, the
    attribution flag must cross-reference it — never render the observed
    'Unattributed (residual): +$37,778 ⚠️ (large residual — balance vs
    positions disagree; investigate)' as an unlinked second mystery.
    """
    rec = {
        "warning": True, "delta": -37_697.42,
        "itemized": ["all positions carry marks — Δ is a cash-ledger gap: "
                     "broker NLV implies cash $84,198 vs $46,501 in the "
                     "cash field (likely unsettled same-day trade "
                     "proceeds; verify at broker)"],
    }
    lines: list[str] = []
    _render_attribution(lines, _attribution_with_residual(37_778.0),
                        nlv_reconciliation=rec)
    md = "\n".join(lines)
    assert "Unattributed (residual): +$37,778" in md
    assert "NLV data-integrity" in md
    assert "cash-ledger gap" in md
    assert "$-37,697" in md
    assert "balance vs positions disagree; investigate" not in md


def test_attribution_residual_without_itemization_keeps_legacy_flag():
    """No integrity itemization this cycle → the legacy flag text is
    unchanged (and small residuals stay unflagged either way).
    """
    lines: list[str] = []
    _render_attribution(lines, _attribution_with_residual(37_778.0))
    md = "\n".join(lines)
    assert "large residual — balance vs positions disagree; investigate" in md

    lines2: list[str] = []
    _render_attribution(lines2, _attribution_with_residual(120.0),
                        nlv_reconciliation={"warning": True,
                                            "itemized": ["x"]})
    md2 = "\n".join(lines2)
    assert "⚠️ (matches" not in md2
    assert "investigate" not in md2
