"""Persistent OHLC cache — transparent daily-bar cache in front of yfinance.

Task #9 (cache-based optimizations). The briefing pipeline pulls 730d of OHLC
for ~200 tickers every run (snapshot technicals), plus 300d for the ~133 scout
tickers and up to 800 broad-screener survivors. Between two same-day runs
(or day-to-day) almost none of that history changes — only the last bar does.

``cached_history(ticker, days=730)`` is a drop-in replacement for
``yf.Ticker(ticker).history(period=f"{days}d")``:

  - Warm hit (cache covers the window and the last cached bar is at/after the
    last market close): serve from disk, zero network.
  - Slightly stale (≤ ``max_stale_days``): fetch just the gap (+5d overlap)
    from yfinance and upsert, deduped by date. ~1 small request per ticker.
  - Cold miss / too stale / window not covered: full download of the
    requested period (identical to the uncached path), then store.
  - ANY exception anywhere (corrupt file, missing parquet engine, disk full,
    tz weirdness): warn to stderr and fall back to the raw yfinance call.
    A cache problem must never block a briefing (user rule: stability > speed).

Adjustment safety: yfinance ``history()`` returns split/dividend-ADJUSTED
prices (auto_adjust default). A new dividend retroactively changes *old*
adjusted bars, so on every incremental fetch we compare the overlapping bars'
closes against the cache; drift > 0.1% → discard the cache and refetch the
full window. This keeps cached deltas clean (never mixes two adjustment
bases).

Backing store: parquet when a pandas parquet engine (pyarrow/fastparquet) is
importable, else CSV (UTC-normalized index, converted back to the original
timezone on load). Files live in ``state/cache/ohlc/<TICKER>.{parquet,csv}``
with a ``<TICKER>.meta.json`` sidecar recording the deepest window ever
requested (so short-history tickers don't refetch forever). Row count is
capped (`_MAX_ROWS`) so files can't grow without bound.

Config (briefing.yaml → ``ohlc_cache``): ``enabled`` (default true), ``dir``,
``max_stale_days`` (default 30). Callers outside the briefing (thematic-scout,
broad-universe-screener) load this module by path and get the same defaults.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]

_DEFAULTS = {
    "enabled": True,
    "dir": str(_REPO_ROOT / "state" / "cache" / "ohlc"),
    "max_stale_days": 30,
}

# Live module config — mutated only via configure().
_CONFIG = dict(_DEFAULTS)

# Keep at most this many rows per ticker on disk (a 730d request needs ~505
# trading bars; 800 gives headroom without runaway growth).
_MAX_ROWS = 800

# Adjusted-close drift beyond this (relative) on overlapping bars means a
# retroactive dividend/split re-adjustment happened → full refetch.
_ADJUST_DRIFT_TOL = 0.001

# Overlap bars pulled on incremental refresh (dedupe + drift detection).
_INCREMENTAL_OVERLAP_DAYS = 5

_PARQUET_OK: bool | None = None  # lazily probed


def configure(cfg: dict | None) -> None:
    """Apply briefing.yaml → ohlc_cache overrides. None/partial → defaults.

    A relative ``dir`` is anchored to the repo root (not the cwd) so the
    cache lands in the same place no matter where the pipeline is launched.
    """
    _CONFIG.update(_DEFAULTS)
    for k in ("enabled", "dir", "max_stale_days"):
        v = (cfg or {}).get(k)
        if v is not None:
            _CONFIG[k] = v
    d = Path(str(_CONFIG["dir"])).expanduser()
    if not d.is_absolute():
        d = _REPO_ROOT / d
    _CONFIG["dir"] = str(d)


def _warn(msg: str) -> None:
    print(f"    [warn] ohlc-cache: {msg}", file=sys.stderr)


def _parquet_available() -> bool:
    global _PARQUET_OK
    if _PARQUET_OK is None:
        try:
            import pyarrow  # noqa: F401
            _PARQUET_OK = True
        except ImportError:
            try:
                import fastparquet  # noqa: F401
                _PARQUET_OK = True
            except ImportError:
                _PARQUET_OK = False
    return _PARQUET_OK


def _safe_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", (ticker or "").upper())


def _cache_dir() -> Path:
    return Path(str(_CONFIG["dir"])).expanduser()


def _paths(ticker: str) -> tuple[Path, Path, Path]:
    base = _cache_dir() / _safe_name(ticker)
    return (base.with_suffix(".parquet"), base.with_suffix(".csv"),
            base.with_suffix(".meta.json"))


def last_market_close_date(now: datetime | None = None) -> date:
    """Most recent US-market close date (weekend-aware; holidays are handled
    by the incremental fetch simply returning no new rows)."""
    if now is None:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            now = datetime.now()
    d = now.date()
    if now.time() < dtime(16, 0):
        d -= timedelta(days=1)
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------------------
# Raw yfinance fetch (the fallback / source of truth) with 429 backoff
# ---------------------------------------------------------------------------

def _raw_history(ticker: str, days: int):
    """yf.Ticker(ticker).history(period=f"{days}d") with one rate-limit retry."""
    import yfinance as yf
    try:
        return yf.Ticker(ticker).history(period=f"{days}d")
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__.lower()
        if "ratelimit" in name or "429" in str(e) or "too many requests" in str(e).lower():
            _warn(f"{ticker}: yfinance rate limit — backing off 2s and retrying once")
            time.sleep(2.0)
            return yf.Ticker(ticker).history(period=f"{days}d")
        raise


# ---------------------------------------------------------------------------
# Load / store (fail-open everywhere)
# ---------------------------------------------------------------------------

def _load(ticker: str):
    """Read the cached frame. None on any problem (corrupt file is deleted)."""
    import pandas as pd
    pq, csv, _meta = _paths(ticker)
    path = pq if pq.exists() else (csv if csv.exists() else None)
    if path is None:
        return None
    try:
        if path.suffix == ".parquet":
            df = pd.read_parquet(path)
        else:
            df = pd.read_csv(path, index_col=0)
            df.index = pd.to_datetime(df.index, utc=True, format="ISO8601")
            try:
                df.index = df.index.tz_convert("America/New_York")
            except Exception:  # noqa: BLE001
                pass
        if df is None or df.empty or "Close" not in df.columns:
            raise ValueError("cached frame empty or missing Close")
        df.index.name = "Date"
        return df.sort_index()
    except Exception as e:  # noqa: BLE001
        _warn(f"{ticker}: unreadable cache ({e}) — deleting, falling back")
        try:
            path.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return None


def _store(ticker: str, df, requested_days: int) -> None:
    """Atomically persist the frame (row-capped) + sidecar meta. Fail-open."""
    try:
        if df is None or df.empty:
            return
        out = df.sort_index().tail(_MAX_ROWS)
        pq, csv, meta = _paths(ticker)
        _cache_dir().mkdir(parents=True, exist_ok=True)
        if _parquet_available():
            tmp = pq.with_suffix(".parquet.tmp")
            out.to_parquet(tmp)
            os.replace(tmp, pq)
            csv.unlink(missing_ok=True)
        else:
            tmp = csv.with_suffix(".csv.tmp")
            to_write = out.copy()
            try:
                if getattr(to_write.index, "tz", None) is not None:
                    to_write.index = to_write.index.tz_convert("UTC")
            except Exception:  # noqa: BLE001
                pass
            to_write.index = to_write.index.map(lambda t: t.isoformat())
            to_write.to_csv(tmp)
            os.replace(tmp, csv)
        # Sidecar: deepest window ever requested — lets short-history tickers
        # (IPOs) serve from cache instead of refetching forever.
        prev = _read_meta(ticker)
        meta_payload = {
            "requested_days": max(int(requested_days), int(prev.get("requested_days", 0))),
            "stored_at": datetime.now().isoformat(),
        }
        tmp_m = meta.with_suffix(".meta.json.tmp")
        tmp_m.write_text(json.dumps(meta_payload))
        os.replace(tmp_m, meta)
    except Exception as e:  # noqa: BLE001
        _warn(f"{ticker}: cache write failed ({e}) — continuing uncached")


def _read_meta(ticker: str) -> dict:
    _pq, _csv, meta = _paths(ticker)
    try:
        return json.loads(meta.read_text())
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# The drop-in entry point
# ---------------------------------------------------------------------------

def _trim(df, days: int, now: datetime):
    """Slice the frame to the requested lookback window (tz-safe copy)."""
    cutoff = now - timedelta(days=days)
    idx_tz = getattr(df.index, "tz", None)
    if idx_tz is not None and cutoff.tzinfo is None:
        import pandas as pd
        cutoff = pd.Timestamp(cutoff).tz_localize(idx_tz)
    elif idx_tz is None and cutoff.tzinfo is not None:
        cutoff = cutoff.replace(tzinfo=None)
    return df[df.index >= cutoff].copy()


def cached_history(ticker: str, days: int = 730, *,
                   fetch_fn=None, now: datetime | None = None):
    """Drop-in for ``yf.Ticker(ticker).history(period=f"{days}d")``.

    Returns a DataFrame (possibly empty — same contract as yfinance) or
    whatever the raw fetch returns when the cache is bypassed. Never raises
    on cache problems; only the raw fetch's own exceptions propagate on the
    final fallback (identical to the uncached behavior callers already
    handle). ``fetch_fn(ticker, days)`` and ``now`` are injectable for tests.
    """
    fetch = fetch_fn or _raw_history
    if not _CONFIG.get("enabled", True):
        return fetch(ticker, days)

    try:
        return _cached_history_inner(ticker, days, fetch, now)
    except Exception as e:  # noqa: BLE001
        _warn(f"{ticker}: cache path failed ({e}) — raw yfinance fallback")
        return fetch(ticker, days)


def _cached_history_inner(ticker: str, days: int, fetch, now: datetime | None):
    import pandas as pd

    if now is None:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("America/New_York"))
        except Exception:  # noqa: BLE001
            now = datetime.now()

    cached = _load(ticker)
    if cached is None or cached.empty:
        full = fetch(ticker, days)
        if full is not None and not getattr(full, "empty", True):
            _store(ticker, full, days)
        return full

    last_cached: date = cached.index[-1].date()
    last_close = last_market_close_date(now)

    # Window coverage: a cache seeded by a 300d request can't serve a 730d
    # one. The sidecar meta records the deepest window ever requested, so a
    # short-history ticker (IPO) that simply HAS no more data isn't refetched
    # forever.
    first_cached: date = cached.index[0].date()
    window_start = (now - timedelta(days=days)).date()
    covered = (first_cached <= window_start + timedelta(days=7)
               or int(_read_meta(ticker).get("requested_days", 0)) >= days)
    if not covered:
        full = fetch(ticker, days)
        if full is None or getattr(full, "empty", True):
            return _trim(cached, days, now)  # keep what we have over nothing
        merged = _upsert(cached, full)
        _store(ticker, merged, days)
        return _trim(merged, days, now)

    if last_cached >= last_close:
        return _trim(cached, days, now)  # fresh — zero network

    staleness = (now.date() - last_cached).days
    if staleness > int(_CONFIG.get("max_stale_days", 30)):
        full = fetch(ticker, days)
        if full is not None and not getattr(full, "empty", True):
            _store(ticker, full, days)
            return _trim(full, days, now)
        return _trim(cached, days, now)

    # Incremental refresh: fetch the gap plus a few overlap bars.
    span = min(staleness + _INCREMENTAL_OVERLAP_DAYS,
               int(_CONFIG.get("max_stale_days", 30)) + _INCREMENTAL_OVERLAP_DAYS)
    recent = fetch(ticker, span)
    if recent is None or getattr(recent, "empty", True):
        # Holiday / weekend / transient failure — serve the cache we have.
        return _trim(cached, days, now)

    # Adjustment-drift guard: overlapping bars must agree, else the whole
    # adjusted series shifted (new dividend/split) → full refetch.
    overlap = cached.index.intersection(recent.index)
    if len(overlap) > 0:
        old_c = cached.loc[overlap, "Close"].astype(float)
        new_c = recent.loc[overlap, "Close"].astype(float)
        denom = new_c.abs().clip(lower=1e-9)
        if bool(((old_c - new_c).abs() / denom > _ADJUST_DRIFT_TOL).any()):
            _warn(f"{ticker}: adjusted-close drift on overlap — full refetch")
            full = fetch(ticker, days)
            if full is not None and not getattr(full, "empty", True):
                _store(ticker, full, days)
                return _trim(full, days, now)
            return _trim(cached, days, now)

    merged = _upsert(cached, recent)
    _store(ticker, merged, days)
    return _trim(merged, days, now)


def _upsert(cached, recent):
    """Concat + dedupe by date, keeping the newest row for duplicates."""
    import pandas as pd
    merged = pd.concat([cached, recent])
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()
