"""Regression tests — the cash-ledger gap that PERSISTED across the
2026-08-28 → 2026-08-31 settlement weekend.

Observed in the real 2026-08-31 briefing / snapshot:

    "all positions carry marks — Δ is a cash-ledger gap: broker NLV
    implies cash $83,847 vs $37,220 in the cash field (likely unsettled
    same-day trade proceeds; verify at broker)"

    "**CAPACITY: coverage 0.05x | cash 3.4% | obligation 72% NLV | ENTRY
    GATES: CLOSED (coverage 0.05x < 0.50x)**"

The gap was Fri Δ$37.7K → Mon Δ$46.6K — it PERSISTED across a settlement
weekend, so "likely unsettled same-day trade proceeds" was not an honest
read. And the real 2026-08-31 balance.json shows `netCash: 37219.91` —
present but IDENTICAL to `cash` (cashAvailableForInvestment), so the
preferred netCash mapping does not carry the gap either. Fixes pinned:

1. broadened ledger-cash capture (netCash → cashBalance →
   settledCashForInvestment → totalCash, first present nonzero wins);
2. diagnostic provenance — the itemization names which cash fields WERE
   present with values, and which candidates were absent (rule #19);
3. honest wording — "collateral holds, unsettled proceeds, or a
   balance-field mapping gap; verify Balances at the broker";
4. capacity dual-read — when the gap is positive, the CAPACITY header
   carries both measured coverage reads, never silently adopting the
   higher figure.
"""

from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps.snapshot_inputs import _compose_balance  # noqa: E402
from render.panels import render_header  # noqa: E402


# The real 2026-08-31 snapshot numbers (balance.json).
_LONG_MV = 1_108_294.88
_OPT_MV = -86_461.50
_CASH = 37_219.91          # cashAvailableForInvestment
_NET_CASH = 37_219.91      # present but IDENTICAL to cash — carries no gap
_BROKER_NLV = 1_105_679.97
_IMPLIED_CASH = 83_846.59  # broker NLV − long MV − option MV


def _marked_positions():
    return [
        {"assetType": "OPTION", "symbol": "MELI_PUT_1460_20270617",
         "symbolDescription": "MELI Jun 17 '27 $1460 Put", "qty": -1.0,
         "marketValue": -7380.0, "currentMid": 73.80},
        {"assetType": "EQUITY", "symbol": "NVDA", "qty": 500.0,
         "price": 180.0, "marketValue": 90_000.0},
    ]


def _real_2026_08_31_balance():
    base = {"cash": _CASH, "netCash": _NET_CASH,
            "totalAccountValue": _BROKER_NLV}
    return _compose_balance(base, _LONG_MV, _OPT_MV,
                            positions=_marked_positions())


def _gate_state(coverage=0.05):
    return SimpleNamespace(
        open=False,
        coverage_ratio=coverage,
        banner=("CAPACITY: coverage 0.05x | cash 3.4% | obligation 72% NLV "
                "| ENTRY GATES: CLOSED (coverage 0.05x < 0.50x)"),
    )


# ── Fix 3: honest wording ──────────────────────────────────────────────────

def test_persistent_gap_wording_names_measured_alternatives():
    """The observed '(likely unsettled same-day trade proceeds; verify at
    broker)' must be replaced — the gap survived a settlement weekend, so
    the note must name the honest alternatives while keeping the measured
    computed-vs-broker numbers."""
    rec = _real_2026_08_31_balance()["nlv_reconciliation"]
    assert rec["warning"] is True
    notes = " | ".join(rec.get("itemized") or [])
    assert "likely unsettled same-day trade proceeds" not in notes
    assert ("collateral holds, unsettled proceeds, or a balance-field "
            "mapping gap; verify Balances at the broker") in notes
    # Measured numbers stay named (rule #19)
    assert "$83,847" in notes
    assert "$37,220" in notes


# ── Fix 2: diagnostic provenance ───────────────────────────────────────────

def test_gap_itemization_lists_present_and_absent_cash_fields():
    """When the gap fires, the itemization must say which cash fields WERE
    present ('cash fields seen: …') and which candidates were absent, so
    the next occurrence identifies the right field immediately. On the real
    2026-08-31 payload: cash + netCash present (identical), the other
    candidates absent."""
    rec = _real_2026_08_31_balance()["nlv_reconciliation"]
    notes = rec.get("itemized") or []
    prov = next(n for n in notes if n.startswith("cash fields seen:"))
    assert "cash (availableForInvestment) $37,220" in prov
    assert "netCash $37,220" in prov
    assert "cashBalance" in prov and "absent" in prov
    assert "settledCashForInvestment" in prov
    assert "totalCash" in prov


def test_no_provenance_noise_when_reconciled():
    """A reconciled snapshot (ledger cash carries the gap) renders NO
    'cash fields seen' line and NO cash_gap record — the diagnostics fire
    only when there is something to diagnose."""
    base = {"cash": _CASH, "netCash": _IMPLIED_CASH,
            "totalAccountValue": _BROKER_NLV}
    bal = _compose_balance(base, _LONG_MV, _OPT_MV,
                           positions=_marked_positions())
    rec = bal["nlv_reconciliation"]
    assert rec["warning"] is False
    assert "cash_gap" not in rec
    assert not any(str(n).startswith("cash fields seen:")
                   for n in rec.get("itemized") or [])


# ── Fix 1: broadened ledger-cash capture ───────────────────────────────────

def test_cash_balance_field_used_when_net_cash_absent():
    """When the adapter's cashFields carries `cashBalance` (and netCash is
    absent), the computed NLV must use it as ledger cash, with the
    substitution named — never fall back to the understated
    cashAvailableForInvestment while a better field sits in the payload."""
    base = {"cash": _CASH, "cashFields": {"cashBalance": _IMPLIED_CASH},
            "totalAccountValue": _BROKER_NLV}
    bal = _compose_balance(base, _LONG_MV, _OPT_MV,
                           positions=_marked_positions())
    rec = bal["nlv_reconciliation"]
    assert rec["warning"] is False
    notes = " | ".join(rec.get("itemized") or [])
    assert "ledger cash (cashBalance)" in notes


def test_field_priority_net_cash_first_then_fallbacks():
    """Priority order is netCash → cashBalance → settledCashForInvestment →
    totalCash; a present-but-zero candidate is skipped (distrusted), the
    next nonzero one wins."""
    base = {"cash": 50_000.0,
            "netCash": 0.0,  # present-but-zero → distrust
            "cashFields": {"netCash": 0.0,
                           "settledCashForInvestment": 80_000.0},
            "totalAccountValue": 1_000_000.0}
    bal = _compose_balance(base, 950_000.0, -30_000.0)
    assert bal["computedAccountValue"] == round(
        950_000.0 - 30_000.0 + 80_000.0, 2)
    notes = " | ".join(
        (bal["nlv_reconciliation"].get("itemized")) or [])
    assert "ledger cash (settledCashForInvestment)" in notes


def test_adapter_collects_only_fields_the_payload_carries():
    """`_collect_cash_fields` must return ONLY fields present in the
    payload (Computed first, RealTimeValues fallback) — absence stays
    explicit (rule #19), non-numeric values are skipped."""
    from adapters.etrade_adapter import _collect_cash_fields
    cmp_data = {"netCash": "37219.91", "cashBalance": 84_198.37,
                "cashAvailableForInvestment": 37_219.91,
                "settledCashForInvestment": None}
    rtv = {"totalCash": 84_000.0}
    out = _collect_cash_fields(cmp_data, rtv)
    assert out == {"netCash": 37_219.91, "cashBalance": 84_198.37,
                   "totalCash": 84_000.0}
    assert "settledCashForInvestment" not in out


# ── Fix 4: capacity dual-read ──────────────────────────────────────────────

def test_capacity_header_carries_dual_coverage_read_on_positive_gap():
    """With the observed '**CAPACITY: coverage 0.05x | … ENTRY GATES:
    CLOSED (coverage 0.05x < 0.50x)**' banner and a positive cash-ledger
    gap, the header must append the honesty line: 'coverage may read low —
    broker NLV implies cash $83,847 (coverage ~0.11×) vs the $37,220 field
    (0.05×); verify at broker' — measured both ways."""
    bal = _real_2026_08_31_balance()
    lines = render_header("2026-08-31", "NORMAL", bal["accountValue"],
                          bal["cash"], 3, gate_state=_gate_state(0.05),
                          balance=bal)
    md = "\n".join(lines)
    assert "CAPACITY: coverage 0.05x" in md
    assert "coverage may read low" in md
    assert "$83,847" in md
    assert "~0.11×" in md
    assert "(0.05×)" in md
    assert "verify at broker" in md


def test_no_dual_read_when_reconciled():
    """When the ledger cash reconciles (no cash_gap in the record), the
    CAPACITY header must NOT carry the dual-read line."""
    base = {"cash": _CASH, "netCash": _IMPLIED_CASH,
            "totalAccountValue": _BROKER_NLV}
    bal = _compose_balance(base, _LONG_MV, _OPT_MV,
                           positions=_marked_positions())
    lines = render_header("2026-08-31", "NORMAL", bal["accountValue"],
                          bal["cash"], 3, gate_state=_gate_state(0.11),
                          balance=bal)
    md = "\n".join(lines)
    assert "coverage may read low" not in md
