#!/usr/bin/env python3
"""Recommendation Grade Audit CLI — thin wrapper over
``analysis/rec_audit.py`` (mirrors entry_timing_audit.py).

George (2026-08-12): "Can you look at briefings, say, for the last five
different briefings, and see what the recommendations make sense? ...
Now that you have this entry time audit table, see how recommendations
make sense and grade those recommendations."

Usage:
    python3 scripts/rec_grade_audit.py [--last 5] [--dates D1 D2 ...]
        [--briefings-dir DIR] [--output-dir reports/] [--no-delivery]

Reads only data already on disk (delivered briefing artifacts, snapshot
archive, chain-IV history, OHLC cache, entry-grade ledger) — no live
fetches. Writes ``rec_grade_audit_<last-date>.md`` to the output dir
and, unless ``--no-delivery``, copies it next to the briefings (same
resolution as steps/deliver.py).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

from analysis import rec_audit  # noqa: E402
from steps.deliver import _resolve_delivery_dir  # noqa: E402

_SKILL_ROOT = _SCRIPTS.parent


def _load_config(path: Path) -> dict | None:
    try:
        import yaml  # noqa: PLC0415
        with open(path) as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None  # fail-open: module defaults apply


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--last", type=int, default=5,
                    help="How many recent briefing dates to audit")
    ap.add_argument("--dates", nargs="*", default=None,
                    help="Explicit briefing dates (YYYY-MM-DD) to audit")
    ap.add_argument("--briefings-dir", default=None,
                    help="Where the delivered briefing_<date>.json / "
                         "briefing_full_<date>.md live (default: the "
                         "standard delivery dir)")
    ap.add_argument("--output-dir", default=str(_SKILL_ROOT / "reports"))
    ap.add_argument("--snapshots-root",
                    default=str(_SKILL_ROOT / "state" / "briefing_snapshots"))
    ap.add_argument("--ledger",
                    default=str(_SKILL_ROOT / "state"
                                / "entry_grade_ledger.json"))
    ap.add_argument("--delivery-dir", default=None)
    ap.add_argument("--no-delivery", action="store_true")
    args = ap.parse_args(argv)

    briefings_dir = (Path(args.briefings_dir) if args.briefings_dir
                     else _resolve_delivery_dir(args.delivery_dir))
    config = _load_config(_SKILL_ROOT / "config" / "briefing.yaml")
    # OHLC cache lives at the REPO root (ohlc_cache.py convention).
    ohlc_dir = _SKILL_ROOT.parents[1] / "state" / "cache" / "ohlc"
    result = rec_audit.run_audit(
        briefings_dir, Path(args.snapshots_root),
        limit=args.last, dates=args.dates or None,
        iv_history_path=_SKILL_ROOT / "state" / "chain_iv_history.json",
        ohlc_cache_dir=ohlc_dir if ohlc_dir.is_dir() else None,
        ledger_path=Path(args.ledger) if args.ledger else None,
        config=config,
    )
    if result.get("error"):
        print(f"ERROR: {result['error']}", file=sys.stderr)
        return 1

    md = rec_audit.render_markdown(result, config=config)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"rec_grade_audit_{result['dates'][-1]}.md"
    out_path.write_text(md)
    agg = result["aggregate"]
    print(f"Rec grade audit: {out_path} ({agg['total_recs']} recs across "
          f"{len(result['dates'])} briefings, {agg['graded']} graded)")

    if not args.no_delivery:
        try:
            dest = _resolve_delivery_dir(args.delivery_dir)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_path, dest / out_path.name)
            print(f"Delivered to: {dest / out_path.name}")
        except Exception as e:  # delivery is best-effort, never load-bearing
            print(f"WARNING: delivery failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
