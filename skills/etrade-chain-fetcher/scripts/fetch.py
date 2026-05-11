"""
E*TRADE-only chain access for the briefing pipeline.

This module wraps `adapters/etrade_market.py` and exposes a clean, cacheable
interface for the rest of the codebase. yfinance MUST NOT be used as a
fallback — if E*TRADE is unavailable, the calling skill must suppress
chain-dependent recommendations.

Public surface
--------------
- list_expirations(symbol)                 -> [date] or None
- get_chain(symbol, expiration, strike_near, n_strikes, chain_type) -> dict or None
- find_strike_near_delta(symbol, expiration, target_delta, opt_type) -> dict or None
- find_strike_at_otm_pct(symbol, expiration, otm_pct, opt_type, spot) -> dict or None
- quote_contract(symbol, strike, expiration, opt_type) -> dict or None
- choose_expiration(symbol, target_dte, tolerance_days, prefer_friday) -> date or None

All shapes are documented in the docstrings. None always means "not available;
caller must NOT substitute another source."

ChainCache
----------
Pass a ChainCache through a briefing run so repeated lookups of the same
(symbol, expiration) tuple only hit E*TRADE once.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# Wire to the in-repo adapter
_ADAPTER_DIR = (
    Path(__file__).resolve().parents[3]
    / "daily-portfolio-briefing" / "scripts"
)
if str(_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTER_DIR))

try:
    from adapters.etrade_market import (  # type: ignore
        get_option_chain as _adapter_get_chain,
        get_option_expirations as _adapter_get_expirations,
        OptionChainRow,
    )
except Exception as _e:
    _adapter_get_chain = None
    _adapter_get_expirations = None
    OptionChainRow = None
    _import_error = _e
else:
    _import_error = None


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

@dataclass
class ChainCache:
    """Per-run cache so multiple skills can share one fetch per (sym, exp).

    Thread-safe. Pass a single instance through the orchestrator.
    """
    _by_key: dict = field(default_factory=dict)
    _exp_by_symbol: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, key: str) -> Any | None:
        with self._lock:
            return self._by_key.get(key)

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._by_key[key] = value

    def get_expirations(self, symbol: str) -> list | None:
        with self._lock:
            return self._exp_by_symbol.get(symbol.upper())

    def put_expirations(self, symbol: str, expirations: list) -> None:
        with self._lock:
            self._exp_by_symbol[symbol.upper()] = expirations


_GLOBAL_CACHE = ChainCache()


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def is_available() -> bool:
    """Whether the E*TRADE chain adapter loaded successfully."""
    return _adapter_get_chain is not None and _adapter_get_expirations is not None


def availability_reason() -> str | None:
    """Human-readable reason why is_available() is False, or None if it's True."""
    if _import_error:
        return f"etrade_market adapter import failed: {_import_error}"
    if not is_available():
        return "etrade_market adapter not loadable"
    return None


def list_expirations(
    symbol: str,
    cache: ChainCache | None = None,
    timeout_s: float = 4.0,
) -> list[date] | None:
    """Return all listed expirations for `symbol`, or None if unavailable."""
    if not is_available():
        return None
    cache = cache or _GLOBAL_CACHE
    cached = cache.get_expirations(symbol)
    if cached is not None:
        return cached
    try:
        exps = _adapter_get_expirations(symbol, timeout_s=timeout_s)
    except Exception:
        return None
    if exps is None:
        return None
    cache.put_expirations(symbol, exps)
    return exps


def choose_expiration(
    symbol: str,
    target_dte: int,
    tolerance_days: int = 14,
    prefer_friday: bool = True,
    cache: ChainCache | None = None,
) -> date | None:
    """Pick the listed expiration closest to today + target_dte.

    Returns None if no expiration is within tolerance_days of target.
    """
    exps = list_expirations(symbol, cache=cache)
    if not exps:
        return None
    target = date.today() + timedelta(days=target_dte)

    def _score(d: date) -> tuple[int, int]:
        distance = abs((d - target).days)
        if distance > tolerance_days:
            return (10_000 + distance, 0)
        weekday_penalty = 0 if (d.weekday() == 4 or not prefer_friday) else 3
        return (distance + weekday_penalty, 0)

    future_only = [d for d in exps if d >= date.today()]
    if not future_only:
        return None
    best = min(future_only, key=_score)
    if abs((best - target).days) > tolerance_days:
        return None
    return best


def get_chain(
    symbol: str,
    expiration: date,
    strike_near: float | None = None,
    n_strikes: int = 20,
    chain_type: str = "CALLPUT",
    cache: ChainCache | None = None,
    timeout_s: float = 5.0,
) -> dict | None:
    """Fetch the chain for one (symbol, expiration). Cached per run.

    Returns a dict shaped:
        {
            "symbol": "AAPL",
            "expiration": date(2026, 6, 19),
            "calls": [OptionChainRow, ...],   # sorted ascending by strike
            "puts":  [OptionChainRow, ...],
            "source": "etrade_live",
            "fetched_at": "2026-05-11T14:32:00Z",
        }
    """
    if not is_available():
        return None
    cache = cache or _GLOBAL_CACHE
    key = f"{symbol.upper()}_{expiration.isoformat()}_{chain_type}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    # Need a strike-near to center the request; use anchor if not provided
    if strike_near is None:
        # Without a centerline, fetch a wider window. Adapter clamps internally.
        strike_near = 100.0
    try:
        raw = _adapter_get_chain(
            symbol=symbol,
            expiry_date=expiration,
            strike_near=strike_near,
            no_of_strikes=n_strikes,
            chain_type=chain_type,
            timeout_s=timeout_s,
        )
    except Exception:
        return None
    if raw is None:
        return None

    out = {
        "symbol": symbol.upper(),
        "expiration": expiration,
        "calls": sorted(raw.get("call") or [], key=lambda r: r.strike),
        "puts": sorted(raw.get("put") or [], key=lambda r: r.strike),
        "source": "etrade_live",
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }
    cache.put(key, out)
    return out


def find_strike_at_otm_pct(
    symbol: str,
    expiration: date,
    otm_pct: float,
    opt_type: str,
    spot: float,
    cache: ChainCache | None = None,
) -> dict | None:
    """Find the listed strike closest to N% OTM and return its quote.

    For a PUT, OTM = strike below spot, so target = spot * (1 - otm_pct/100).
    For a CALL, OTM = strike above spot, so target = spot * (1 + otm_pct/100).

    Returns {strike, bid, mid, ask, delta, iv, open_interest, expiration, source}
    or None if no usable strike found.
    """
    if not spot or spot <= 0:
        return None
    opt_type = opt_type.upper()
    if opt_type == "PUT":
        target = spot * (1 - otm_pct / 100.0)
    elif opt_type == "CALL":
        target = spot * (1 + otm_pct / 100.0)
    else:
        return None

    chain = get_chain(
        symbol=symbol,
        expiration=expiration,
        strike_near=target,
        n_strikes=20,
        chain_type=opt_type,
        cache=cache,
    )
    if not chain:
        return None
    rows = chain["puts"] if opt_type == "PUT" else chain["calls"]
    if not rows:
        return None
    best = min(rows, key=lambda r: abs(r.strike - target))
    return _row_to_dict(best, expiration=expiration, opt_type=opt_type)


def find_strike_near_delta(
    symbol: str,
    expiration: date,
    target_delta: float,
    opt_type: str,
    spot: float,
    tolerance: float = 0.10,
    cache: ChainCache | None = None,
) -> dict | None:
    """Find the strike whose delta is closest to target_delta.

    Conventions: target_delta is given as a POSITIVE number (e.g., 0.30 for
    a 30-delta option). For puts, internal delta is negative; we compare
    absolute values.

    Returns the same dict shape as find_strike_at_otm_pct.
    """
    chain = get_chain(
        symbol=symbol,
        expiration=expiration,
        strike_near=spot,
        n_strikes=30,
        chain_type=opt_type.upper(),
        cache=cache,
    )
    if not chain:
        return None
    rows = chain["puts"] if opt_type.upper() == "PUT" else chain["calls"]
    candidates = [r for r in rows if r.delta is not None]
    if not candidates:
        return None
    best = min(candidates, key=lambda r: abs(abs(float(r.delta)) - target_delta))
    if abs(abs(float(best.delta)) - target_delta) > tolerance:
        return None
    return _row_to_dict(best, expiration=expiration, opt_type=opt_type.upper())


def quote_contract(
    symbol: str,
    strike: float,
    expiration: date,
    opt_type: str,
    cache: ChainCache | None = None,
) -> dict | None:
    """Look up the bid/mid/ask/Greeks for one specific contract."""
    chain = get_chain(
        symbol=symbol,
        expiration=expiration,
        strike_near=strike,
        n_strikes=10,
        chain_type=opt_type.upper(),
        cache=cache,
    )
    if not chain:
        return None
    rows = chain["puts"] if opt_type.upper() == "PUT" else chain["calls"]
    exact = [r for r in rows if abs(r.strike - strike) < 0.01]
    if not exact:
        return None
    return _row_to_dict(exact[0], expiration=expiration, opt_type=opt_type.upper())


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _row_to_dict(row, *, expiration: date, opt_type: str) -> dict:
    """Convert an OptionChainRow into a stable dict shape."""
    bid = float(row.bid or 0)
    ask = float(row.ask or 0)
    last = float(row.last or 0)
    mid = (bid + ask) / 2 if (bid and ask) else (last or max(bid, ask))
    return {
        "strike": float(row.strike),
        "bid": bid,
        "mid": round(mid, 4),
        "ask": ask,
        "last": last,
        "delta": float(row.delta) if row.delta is not None else None,
        "gamma": float(row.gamma) if row.gamma is not None else None,
        "theta": float(row.theta) if row.theta is not None else None,
        "vega": float(row.vega) if row.vega is not None else None,
        "iv": float(row.iv) if row.iv is not None else None,
        "open_interest": int(row.open_interest or 0),
        "expiration": expiration.isoformat() if isinstance(expiration, date) else str(expiration),
        "type": opt_type,
        "source": "etrade_live",
    }
