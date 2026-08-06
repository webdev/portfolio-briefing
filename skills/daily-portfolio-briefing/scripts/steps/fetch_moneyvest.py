"""Step 1.9 — Moneyvest morning scan (task #46).

Runs the moneyvest-fetcher skill (Playwright scrape of shopping list +
sentiment index + bounded M-Scores) on every briefing run, BEFORE snapshot
inputs — the "morning scan then briefing" ordering. Config-gated by
``briefing.yaml → moneyvest.enabled``; 20h cache means the network fetch
runs at most once a day. Fail-open at every layer: any error returns the
prior cache (staleness-labeled) or an empty payload — the briefing never
blocks on Moneyvest.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _load_fetcher_module():
    """Import the moneyvest-fetcher skill's fetch module (fail-open)."""
    try:
        import importlib.util as ilu
        target = (Path(__file__).resolve().parents[3]
                  / "moneyvest-fetcher" / "scripts" / "fetch_moneyvest.py")
        if not target.exists():
            return None
        mod = sys.modules.get("moneyvest_fetcher")
        if mod is not None:
            return mod
        spec = ilu.spec_from_file_location("moneyvest_fetcher", target)
        if spec is None or spec.loader is None:
            return None
        mod = ilu.module_from_spec(spec)
        sys.modules["moneyvest_fetcher"] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def fetch_moneyvest_step(config: dict | None,
                         m_score_tickers: list[str] | None = None) -> dict:
    """Run the morning scan. Returns the Moneyvest payload dict ({} when
    disabled/unavailable)."""
    cfg = ((config or {}).get("moneyvest") or {}) if isinstance(config, dict) else {}
    if not cfg.get("enabled", False):
        return {}
    mod = _load_fetcher_module()
    if mod is None:
        print("[Step 1.9] moneyvest-fetcher unavailable — skipping",
              file=sys.stderr)
        return {}
    try:
        kwargs: dict = {"tickers": sorted({str(t).upper()
                                           for t in (m_score_tickers or []) if t})}
        if cfg.get("cache_path"):
            kwargs["output"] = cfg["cache_path"]
        if cfg.get("ttl_hours"):
            kwargs["ttl_hours"] = float(cfg["ttl_hours"])
        data = mod.run(**kwargs)
        n = len(data.get("shopping_list") or [])
        sp = ((data.get("index") or {}).get("sp500") or {})
        print(f"[Step 1.9]   {n} shopping-list rows · "
              f"index S&P {sp.get('value')} {sp.get('label')} · "
              f"{len(data.get('m_scores') or {})} M-Scores · "
              f"provenance: {data.get('provenance')}")
        return data or {}
    except Exception as e:
        print(f"[Step 1.9] Moneyvest scan failed (non-fatal): {e}",
              file=sys.stderr)
        return {}
