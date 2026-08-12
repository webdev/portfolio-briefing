#!/usr/bin/env python3
"""Entry Timing Audit CLI — thin wrapper over ``analysis/entry_audit.py``.

George (2026-08-12): "figure out whether my current options were sold or
bought at the best time. Is there any way to tell that?"

Usage:
    python3 scripts/entry_timing_audit.py [--as-of YYYY-MM-DD]
        [--output-dir reports/] [--no-delivery]

Reads only data already on disk (snapshot archive, chain-IV history, OHLC
cache) — no live fetches. Writes ``entry_timing_audit_<date>.md`` to the
output dir and, unless ``--no-delivery``, copies it to the standard
delivery dir (same resolution as steps/deliver.py: ``--delivery-dir`` >
``PORTFOLIO_BRIEFING_DELIVERY_DIR`` > ``~/Documents/briefings``).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

from analysis import entry_audit  # noqa: E402
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
    ap.add_argument("--as-of", default=None,
                    help="Snapshot date to audit (default: latest)")
    ap.add_argument("--output-dir", default=str(_SKILL_ROOT / "reports"),
                    help="Report output dir (default: <skill>/reports/)")
    ap.add_argument("--snapshots-root",
                    default=str(_SKILL_ROOT / "state" / "briefing_snapshots"))
    ap.add_argument("--delivery-dir", default=None)
    ap.add_argument("--no-delivery", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.snapshots_root)
    config = _load_config(_SKILL_ROOT / "config" / "briefing.yaml")
    # OHLC cache lives at the REPO root (ohlc_cache.py convention).
    ohlc_dir = _SKILL_ROOT.parents[1] / "state" / "cache" / "ohlc"
    result = entry_audit.run_audit(
        root,
        as_of=args.as_of,
        iv_history_path=_SKILL_ROOT / "state" / "chain_iv_history.json",
        ohlc_cache_dir=ohlc_dir if ohlc_dir.is_dir() else None,
        config=config,
    )
    if result.get("error"):
        print(f"ERROR: {result['error']}", file=sys.stderr)
        return 1

    md = entry_audit.render_markdown(result, config=config)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"entry_timing_audit_{result['as_of']}.md"
    out_path.write_text(md)
    print(f"Entry timing audit: {out_path} "
          f"({len(result['cards'])} positions, "
          f"{result['aggregate']['graded']} graded)")

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
