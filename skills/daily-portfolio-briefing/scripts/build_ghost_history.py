#!/usr/bin/env python3
"""Backfill the 👻 ghost-portfolio series from snapshot history (task #41).

Full idempotent recompute: reads every dated snapshot dir under
``state/briefing_snapshots/``, rebuilds the options-stripped counterfactual
NAV series, and atomically overwrites ``state/ghost_portfolio.json``.
Re-running is always safe — the series is a pure function of the snapshots.

Usage:
    python3 build_ghost_history.py [--snapshot-root PATH] [--dry-run] [-v]

See analysis/ghost_portfolio.py for the construction rules (the module
docstring is the assumptions contract).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis import ghost_portfolio as gp  # noqa: E402

DEFAULT_ROOT = (Path(__file__).resolve().parents[1]
                / "state" / "briefing_snapshots")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot-root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--dry-run", action="store_true",
                    help="compute + print, don't write ghost_portfolio.json")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every mirrored event and note")
    args = ap.parse_args()

    report = gp.build_ghost_report(args.snapshot_root,
                                   persist_state=not args.dry_run)
    if report.status != "ok":
        print(f"ghost backfill: {report.status} — "
              f"{'; '.join(report.notes) or 'no detail'}", file=sys.stderr)
        return 1

    figs = gp.summary_figures(report)
    latest = report.latest
    print(f"👻 Ghost portfolio — {len(report.days)} days, "
          f"{report.inception} → {report.as_of}")
    print(f"  Real NAV:  ${latest.real_nav:,.0f}")
    print(f"  Ghost NAV: ${latest.ghost_nav:,.0f}")
    print(f"  Options program net (gap): ${figs['total_gap']:+,.0f} "
          f"since {figs['inception']}")
    if figs["month_gap"] is not None:
        print(f"  This month: ${figs['month_gap']:+,.0f}")
    if figs["week_gap"] is not None:
        print(f"  Past 7d:    ${figs['week_gap']:+,.0f}")
    if args.verbose:
        for ev in report.events:
            print(f"  event: {ev}")
    for note in report.notes:
        print(f"  note: {note}")
    if not args.dry_run:
        print(f"  → {gp.state_path(args.snapshot_root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
