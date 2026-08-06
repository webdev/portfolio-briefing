#!/usr/bin/env python3
"""One-time migration — recompute option-mark-inclusive NLV for historical
snapshots (2026-08-05 defect 1).

Background: through 2026-08-03 the pipeline persisted
``balance.json → accountValue = cash + longMarketValue`` (equities only —
short-option liabilities were NOT subtracted), inflating stored NLV. The
2026-08-04 fix made the live pipeline broker-true, but the benchmark /
attribution history then MIXED conventions: pre-correction inflated NLVs
against broker-true NLVs. Observed output:

    Unattributed (residual): -$65,345 ⚠️ (large residual — balance vs
    positions disagree; investigate)

and an alpha table whose 30d/90d/inception rows were poisoned by the
inflated starting NLVs.

This migration recomputes, for each old-era snapshot:

    corrected NLV = accountValue (= cash + long equity MV)
                  + Σ signed option marks from positions.json
                    (short options negative, long options positive)

and writes it as ``accountValue_corrected`` ALONGSIDE the original — no
original field is ever overwritten — plus an ``nlv_correction`` audit block
{original, corrected, delta, corrected_at, method}. Readers
(analysis/benchmark_tracker.py::balance_nlv, analysis/pnl_attribution.py)
prefer the corrected figure when present.

Skip rules (fail closed — never fabricate a correction):
  - already-corrected snapshots (accountValue_corrected present);
  - broker-true era (optionMarketValue / nlv_reconciliation present);
  - positions.json missing/unreadable;
  - any option position with no usable mark (no marketValue AND no
    currentMid×qty) — e.g. the degenerate 2026-05-08 first pull;
  - balance shape unexpected (accountValue ≠ cash + longMarketValue beyond
    tolerance — the old-era invariant that makes the recompute valid).

Skipped-prefix snapshots are handled by the existing discontinuity detector
plus ``benchmark_tracking.nlv_rebase_dates`` in briefing.yaml (belt and
suspenders).

Usage:
    python3 scripts/recompute_nlv_history.py [--snapshot-root PATH]
                                             [--dry-run] [--quiet]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Old-era invariant: accountValue == cash + longMarketValue (to the cent in
# practice). A larger gap means the balance shape is not the one this
# recompute understands → skip rather than guess.
BALANCE_SHAPE_TOLERANCE = 100.0

METHOD = "accountValue + signed option marks from positions.json"


def _f(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def option_mark(pos: dict) -> float | None:
    """Signed market value of an option position; None when unusable."""
    mv = _f(pos.get("marketValue"))
    if mv is not None:
        return mv
    mid = _f(pos.get("currentMid"))
    qty = _f(pos.get("qty"))
    if mid is not None and qty is not None:
        return mid * 100.0 * qty
    return None


def correct_snapshot_dir(snap_dir: Path, dry_run: bool = False) -> tuple[str, str]:
    """Correct one dated snapshot dir. Returns (status, detail) where status
    is one of: corrected / already_corrected / broker_true / skipped."""
    bal_path = snap_dir / "balance.json"
    pos_path = snap_dir / "positions.json"
    try:
        bal = json.loads(bal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return "skipped", f"balance.json unreadable: {e}"
    if not isinstance(bal, dict):
        return "skipped", "balance.json is not a dict"
    if bal.get("accountValue_corrected") is not None:
        return "already_corrected", ""
    if bal.get("optionMarketValue") is not None or bal.get("nlv_reconciliation"):
        return "broker_true", "NLV already option-inclusive (post-2026-08-04 era)"

    original = _f(bal.get("accountValue"))
    cash = _f(bal.get("cash"))
    long_mv = _f(bal.get("longMarketValue"))
    if original is None or cash is None or long_mv is None:
        return "skipped", "balance missing accountValue/cash/longMarketValue"
    if abs(original - (cash + long_mv)) > BALANCE_SHAPE_TOLERANCE:
        return "skipped", (
            f"balance shape unexpected: accountValue {original:,.2f} != "
            f"cash + longMarketValue {cash + long_mv:,.2f}")

    try:
        positions = json.loads(pos_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return "skipped", f"positions.json unreadable: {e}"
    if not isinstance(positions, list):
        return "skipped", "positions.json is not a list"

    opt_total = 0.0
    for pos in positions:
        if not isinstance(pos, dict) or pos.get("assetType") != "OPTION":
            continue
        mark = option_mark(pos)
        if mark is None:
            return "skipped", (
                f"option {pos.get('symbol', '?')} has no usable mark — "
                f"cannot correct this snapshot (fail closed)")
        opt_total += mark

    corrected = original + opt_total
    if corrected <= 0:
        return "skipped", f"corrected NLV non-positive ({corrected:,.2f})"

    bal["accountValue_corrected"] = round(corrected, 2)
    bal["nlv_correction"] = {
        "original": round(original, 2),
        "corrected": round(corrected, 2),
        "delta": round(corrected - original, 2),
        "corrected_at": datetime.now(timezone.utc).isoformat(),
        "method": METHOD,
    }
    if not dry_run:
        bal_path.write_text(json.dumps(bal, indent=1), encoding="utf-8")
    return "corrected", (
        f"{original:,.0f} -> {corrected:,.0f} (Δ {corrected - original:+,.0f})")


def migrate(snapshot_root: Path, dry_run: bool = False,
            quiet: bool = False) -> dict:
    """Run the migration across every dated snapshot dir. Returns a summary
    {status: [dir names]}."""
    summary: dict[str, list[str]] = {
        "corrected": [], "already_corrected": [], "broker_true": [], "skipped": [],
    }
    root = Path(snapshot_root)
    if not root.exists():
        print(f"[error] snapshot root not found: {root}", file=sys.stderr)
        return summary
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not _DATE_DIR_RE.match(child.name):
            continue  # skips .test dirs and non-date dirs
        status, detail = correct_snapshot_dir(child, dry_run=dry_run)
        summary[status].append(child.name)
        if not quiet and (status in ("corrected", "skipped") or detail):
            print(f"  {child.name}: {status}" + (f" — {detail}" if detail else ""))
    if not quiet:
        print(
            f"\n{'[dry-run] ' if dry_run else ''}corrected "
            f"{len(summary['corrected'])} · already corrected "
            f"{len(summary['already_corrected'])} · broker-true "
            f"{len(summary['broker_true'])} · skipped {len(summary['skipped'])}")
    return summary


def main() -> int:
    default_root = Path(__file__).resolve().parent.parent / "state" / "briefing_snapshots"
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--snapshot-root", type=Path, default=default_root)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    migrate(args.snapshot_root, dry_run=args.dry_run, quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
