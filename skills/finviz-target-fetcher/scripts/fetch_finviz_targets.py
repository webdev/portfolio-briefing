#!/usr/bin/env python3
"""CLI entry point for the FINVIZ public target-price fetcher.

Wraps `public_fetcher.fetch_one` with:
- 24h disk cache (`<cache-dir>/.finviz_target_cache.json`)
- daily request cap (default 60 — soft fail-closed when exceeded)
- batch mode that reads tickers from a JSON file
- ETF-aware filtering (skips known basket tickers)

Usage:
    # Single ticker
    python3 fetch_finviz_targets.py --ticker NVDA

    # Batch from a file (one ticker per line OR JSON array)
    python3 fetch_finviz_targets.py --tickers-file held.json \
        --cache-dir state/ --output state/finviz_targets.json

    # Force refresh
    python3 fetch_finviz_targets.py --ticker NVDA --refresh

Exit codes:
    0   all requested tickers either fetched OR served from cache
    1   partial — some tickers missing data (fail-closed: None entries)
    2   all blocked / network down
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow this file to be imported as `finviz_target_fetcher` from anywhere on PYTHONPATH
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from public_fetcher import FetchStats, fetch_one  # noqa: E402


# ─── Config ────────────────────────────────────────────────────────────────


def _load_config() -> dict[str, Any]:
    """Load YAML config; env-var overrides for any top-level key
    (PB_FINVIZ_<UPPERCASE_KEY>)."""
    try:
        import yaml  # noqa: WPS433
    except ImportError:
        yaml = None  # type: ignore

    cfg_path = _HERE.parent / "config" / "finviz_config.yaml"
    cfg: dict[str, Any] = {
        "cache_ttl_hours": 24,
        "rate_limit_seconds": 2.0,
        "daily_request_cap": 60,
        "request_timeout_seconds": 15,
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "divergence_threshold_pct": 20.0,
        "known_etfs": [],
    }
    if yaml is not None and cfg_path.exists():
        try:
            loaded = yaml.safe_load(cfg_path.read_text()) or {}
            cfg.update(loaded)
        except Exception as e:  # noqa: BLE001
            print(f"WARN: bad finviz_config.yaml ({e}) — using defaults", file=sys.stderr)

    # Env-var overrides
    for k in list(cfg.keys()):
        env_key = "PB_FINVIZ_" + k.upper()
        v = os.environ.get(env_key)
        if v is None:
            continue
        try:
            cfg[k] = type(cfg[k])(v) if cfg[k] is not None else v
        except (ValueError, TypeError):
            cfg[k] = v
    return cfg


# ─── Cache ─────────────────────────────────────────────────────────────────


def _cache_path(cache_dir: Path) -> Path:
    return cache_dir / ".finviz_target_cache.json"


def _load_cache(cache_dir: Path) -> dict[str, Any]:
    p = _cache_path(cache_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache_dir: Path, cache: dict[str, Any]) -> None:
    p = _cache_path(cache_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via tempfile + rename
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    tmp.replace(p)


def _cache_fresh(entry: dict, ttl_hours: float) -> bool:
    """Is a cached entry's `fetched_at` within TTL?"""
    raw = entry.get("fetched_at")
    if not raw:
        return False
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
    return age_h < ttl_hours


# ─── Batch fetch ──────────────────────────────────────────────────────────


def fetch_targets(
    tickers: list[str] | set[str],
    *,
    cache_dir: str | Path,
    refresh: bool = False,
    config: dict | None = None,
) -> tuple[dict[str, dict | None], FetchStats]:
    """Fetch FINVIZ targets for a batch of tickers.

    Returns ({ticker: target_dict_or_None}, stats).

    The returned dict ALWAYS contains every requested ticker as a key — value
    is None for ETFs (filtered before fetch), blocked tickers, and parse
    failures. Fail-closed (hard rule #19).
    """
    cfg = config or _load_config()
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = _load_cache(cache_dir)
    ttl_h = float(cfg.get("cache_ttl_hours", 24))
    daily_cap = int(cfg.get("daily_request_cap", 60))
    rate_limit = float(cfg.get("rate_limit_seconds", 2.0))
    timeout = float(cfg.get("request_timeout_seconds", 15))
    ua = cfg.get("user_agent", "Mozilla/5.0")
    known_etfs = {t.upper() for t in cfg.get("known_etfs", [])}

    out: dict[str, dict | None] = {}
    stats = FetchStats()
    last_t = [0.0]
    fetched_today = 0

    null_envelope = lambda: {
        "_null": True,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    for ticker in sorted({t.upper() for t in tickers if t}):
        # Skip non-US tickers — FINVIZ only covers US-listed. Foreign
        # exchange suffixes (.L / .IL / .HM / .PA / .DE etc.) or leading
        # digits (European exchange codes) get 404s and burn our rate
        # budget for nothing. Match: any `.` followed by 1-3 letters, OR
        # a ticker that starts with a digit.
        import re as _re
        if _re.search(r"\.[A-Z]{1,3}$", ticker) or ticker[:1].isdigit():
            out[ticker] = None
            continue

        # ETF filter
        if ticker in known_etfs:
            out[ticker] = None
            continue

        # Cache check — both real entries and null envelopes carry fetched_at,
        # so the TTL gate works the same for both.
        if not refresh and ticker in cache:
            entry = cache[ticker] or {}
            if _cache_fresh(entry, ttl_h):
                out[ticker] = None if entry.get("_null") else entry
                continue

        # Daily cap
        if fetched_today >= daily_cap:
            stats.daily_cap_hit = True
            out[ticker] = None
            continue

        result = fetch_one(
            ticker,
            user_agent=ua,
            timeout_seconds=timeout,
            last_request_time=last_t,
            rate_limit_seconds=rate_limit,
            stats=stats,
        )
        fetched_today += 1
        out[ticker] = result
        # Cache the result OR a null envelope (so we don't re-probe within TTL)
        cache[ticker] = result if result else null_envelope()

    _save_cache(cache_dir, cache)
    return out, stats


# ─── CLI ──────────────────────────────────────────────────────────────────


def _read_ticker_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8").strip()
    # JSON array OR one ticker per line
    if text.startswith("["):
        return [str(t) for t in json.loads(text)]
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fetch FINVIZ analyst targets (public mode).")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--ticker", help="Single ticker to fetch")
    group.add_argument("--tickers-file", type=Path, help="File of tickers (JSON array or one per line)")
    p.add_argument("--cache-dir", type=Path, default=Path("state"), help="Where to store .finviz_target_cache.json")
    p.add_argument("--output", type=Path, help="Write JSON result here (default: stdout)")
    p.add_argument("--refresh", action="store_true", help="Ignore cache, re-fetch")
    args = p.parse_args(argv)

    tickers = [args.ticker] if args.ticker else _read_ticker_file(args.tickers_file)
    if not tickers:
        print("No tickers requested", file=sys.stderr)
        return 2

    targets, stats = fetch_targets(
        tickers,
        cache_dir=args.cache_dir,
        refresh=args.refresh,
    )

    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": {
            "requested": len(tickers),
            "with_data": sum(1 for v in targets.values() if v is not None),
            "without_data": sum(1 for v in targets.values() if v is None),
            "requests_made": stats.requests_made,
            "successful": stats.successful,
            "blocked": stats.blocked,
            "parse_failures": stats.parse_failures,
            "daily_cap_hit": stats.daily_cap_hit,
        },
        "targets": targets,
    }

    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote {args.output} ({payload['stats']['with_data']}/{len(tickers)} with data)", file=sys.stderr)
    else:
        print(text)

    if stats.successful == 0 and stats.requests_made > 0:
        return 2
    if payload["stats"]["without_data"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
