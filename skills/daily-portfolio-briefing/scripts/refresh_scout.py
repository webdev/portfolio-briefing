#!/usr/bin/env python3
"""Standalone scout-cache refresher (pre-warm entry point).

Runs ONLY the thematic-scout research and rewrites
``state/briefing_snapshots/scout_cache.json`` — the exact cache the briefing
pipeline (run_briefing.py Step 6.6) reads. It calls the SAME function the
pipeline uses (``steps.thematic_research.run_thematic_research`` with
``refresh=True``) so the cached payload shape is identical.

OPTIONAL by design: the pipeline self-refreshes whenever the cache is older
than briefing.yaml ``scout.cache_ttl_hours`` (or predates today with
``scout.force_refresh_morning``). This script exists only to pre-warm the
cache (e.g. the 6:30 AM launchd job) so the scheduled briefing run starts
hot instead of spending 30-60s on research.

Third-party recs: a fresh Parkev fetch is attempted first (same fetcher as
the pipeline's Step 1.6) so the cached verdicts carry third-party catalysts.
Fail-soft: on fetch failure the scout runs with an empty recs_map — the same
degradation the pipeline itself has on a fetch failure.

Position context: the pre-warm has no live E*TRADE snapshot, so
held_weights / existing_short_puts are empty. That is safe — the
position-aware guards (put-stack, already-positioned, long-put cancellation)
are applied at RENDER time by the pipeline from the live snapshot, not baked
into the cache (CLAUDE.md rule #17).

Usage:
    python3 scripts/refresh_scout.py [--config config/briefing.yaml]

Exit codes: 0 = cache refreshed, 1 = refresh failed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from steps.fetch_recommendations import fetch_recommendations  # noqa: E402
from steps.thematic_research import run_thematic_research  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-warm the thematic-scout cache (scout refresh only)"
    )
    parser.add_argument(
        "--config",
        default=str(_SCRIPTS_DIR.parent / "config" / "briefing.yaml"),
        help="briefing.yaml path (for thematic_scout / expiration_policy knobs)",
    )
    args = parser.parse_args()

    config: dict = {}
    try:
        import yaml

        cfg_path = Path(args.config)
        if cfg_path.exists():
            config = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception as e:
        print(f"[warn] config load failed ({e}); using defaults", file=sys.stderr)

    # Same snapshot layout as run_briefing.py: cache lands at
    # state/briefing_snapshots/scout_cache.json (snapshot_dir.parent).
    snapshot_dir = (
        _SCRIPTS_DIR.parent / "state" / "briefing_snapshots" / date.today().isoformat()
    )
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Fresh Parkev recs so cached verdicts carry third-party catalysts.
    recs_map: dict = {}
    try:
        for r in fetch_recommendations(snapshot_dir) or []:
            t = r.get("ticker")
            rec = r.get("recommendation")
            if t and rec:
                recs_map[str(t).upper()] = {
                    "recommendation": str(rec).upper(),
                    "rating_tier": r.get("rating_tier"),
                    "raw_recommendation": r.get("raw_recommendation"),
                    "conviction": r.get("conviction"),
                    "conviction_score": r.get("conviction_score"),
                    "aging": bool(r.get("aging")),
                    "age_days": r.get("age_days"),
                    "date_updated": r.get("date_updated"),
                }
    except Exception as e:
        print(f"[warn] recommendation fetch failed ({e}); scout runs without recs",
              file=sys.stderr)

    _ts_cfg = config.get("thematic_scout") or {}
    payload = run_thematic_research(
        snapshot_dir=snapshot_dir,
        recs_map=recs_map,
        refresh=True,  # pre-warm ALWAYS refreshes — that's the whole point
        parallel=bool(_ts_cfg.get("parallel", True)),
        max_workers=int(_ts_cfg.get("max_workers", 8)),
        prefer_monthly=bool(
            (config.get("expiration_policy") or {}).get("prefer_monthly")
        ),
    )

    meta = (payload or {}).get("_scout_meta") or {}
    if payload is None or not meta.get("fresh"):
        print("[error] scout refresh did not produce a fresh cache", file=sys.stderr)
        return 1
    total = (payload.get("summary") or {}).get("total", 0)
    print(f"Scout cache refreshed: {total} tickers analyzed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
