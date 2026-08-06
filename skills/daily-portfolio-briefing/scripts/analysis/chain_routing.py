"""Task #36 — route snapshot chains + quotes through E*TRADE (hard rule #2).

The snapshot's option chains feed the Watch-panel ROLL ANALYSIS, exit-cost
anatomy, credit windows, and chain-IV — all tradeable-price surfaces, which
means they MUST come from E*TRADE (CLAUDE.md hard rule #2). yfinance remains
the LABELED fallback: beyond the per-run budget cap, or on any E*TRADE
failure (missing OAuth tokens, rate-limit exhaustion, per-symbol errors),
each chain/quote record carries ``source: "yfinance"`` so provenance is
per-item truthful — never inferred from the run mode flag (the line-813
mislabel this module exists to fix).

Components
----------
- ``RateLimiter``     — thread-safe request spacing at ≤4 req/s aggregate
                        (E*TRADE market-data limit).
- ``build_chain_plan``— priority-tiered fetch plan: held (u, exp) pairs
                        near-expiry-first (tier 0), held-underlying future
                        expirations (tier 1), candidate-universe chains
                        (tier 2). First ``max_chains`` route to E*TRADE,
                        the remainder are planned yfinance fallback.
- ``fetch_chains_routed`` — executes the plan through the canonical
                        etrade-chain-fetcher surface with a throttled worker
                        pool; failed E*TRADE items fall back to yfinance,
                        labeled. Never blocks the pipeline (fail-open).
- ``fetch_quotes_etrade`` — E*TRADE batch quotes (25 symbols/call) for the
                        full symbol set; index symbols (^VIX) and any
                        E*TRADE misses stay on yfinance, labeled.
- ``source_split_label`` / ``chain_source_counts`` — honest provenance
                        ("etrade+yfinance-fallback", per-source counts).

Everything here is fail-open: no tokens / no pyetrade / adapter errors →
empty results or yfinance fallback; the briefing never blocks on E*TRADE.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# E*TRADE market-data rate limit: 4 req/s (CLAUDE.md wheelhouz section).
ETRADE_RATE_PER_SEC = 4.0
# Per-run E*TRADE chain budget (config: chains.etrade_max_chains).
DEFAULT_MAX_CHAINS = 150
# E*TRADE batch-quote API: max 25 symbols per call.
QUOTE_BATCH_SIZE = 25
# Circuit breaker: this many consecutive failures with ZERO successes →
# E*TRADE is down for the run (token absence, auth expiry); stop burning
# the throttle and send everything to yfinance.
_BREAKER_THRESHOLD = 3

# Chain window sizes (strikes per side around spot). Held chains get a wider
# window so the held contract's own strike + roll candidates are covered.
_N_STRIKES_HELD = 60
_N_STRIKES_CANDIDATE = 40


# --------------------------------------------------------------------------
# Throttle
# --------------------------------------------------------------------------

class RateLimiter:
    """Thread-safe request spacing: call starts ≥ 1/rate seconds apart.

    ``clock``/``sleeper`` are injectable for deterministic tests. ``slots``
    records every granted start time so tests can verify the schedule never
    exceeds the rate in any sliding window.
    """

    def __init__(
        self,
        rate_per_sec: float = ETRADE_RATE_PER_SEC,
        clock=time.monotonic,
        sleeper=time.sleep,
    ):
        self.rate_per_sec = float(rate_per_sec)
        self._interval = 1.0 / self.rate_per_sec
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next_slot = 0.0
        self.slots: list[float] = []

    def acquire(self) -> float:
        """Block until the next request slot; returns the slot time."""
        with self._lock:
            now = self._clock()
            slot = max(self._next_slot, now)
            self._next_slot = slot + self._interval
            self.slots.append(slot)
        wait = slot - self._clock()
        if wait > 0:
            self._sleeper(wait)
        return slot


# Shared per-process limiter so expiration listing (pair-building) and chain
# fetching draw from the SAME 4 req/s aggregate budget.
SHARED_LIMITER = RateLimiter()


# --------------------------------------------------------------------------
# Fetcher loading (canonical etrade-chain-fetcher surface)
# --------------------------------------------------------------------------

_FETCHER_MODULE = None
_FETCHER_CACHE = None
_FETCHER_TRIED = False
_FETCHER_LOCK = threading.Lock()


def _load_chain_fetcher():
    """Load and cache the etrade-chain-fetcher skill module (fail-open)."""
    global _FETCHER_MODULE, _FETCHER_CACHE, _FETCHER_TRIED
    with _FETCHER_LOCK:
        if _FETCHER_TRIED:
            return _FETCHER_MODULE, _FETCHER_CACHE
        _FETCHER_TRIED = True
        try:
            mod = sys.modules.get("etrade_chain_fetcher")
            if mod is None:
                import importlib.util as _ilu
                target = (
                    Path(__file__).resolve().parents[3]
                    / "etrade-chain-fetcher" / "scripts" / "fetch.py"
                )
                if not target.exists():
                    return None, None
                spec = _ilu.spec_from_file_location("etrade_chain_fetcher", target)
                if spec is None or spec.loader is None:
                    return None, None
                mod = _ilu.module_from_spec(spec)
                sys.modules["etrade_chain_fetcher"] = mod
                spec.loader.exec_module(mod)
            if not mod.is_available():
                return None, None
            _FETCHER_MODULE = mod
            _FETCHER_CACHE = mod.ChainCache()
        except Exception:
            _FETCHER_MODULE = None
            _FETCHER_CACHE = None
        return _FETCHER_MODULE, _FETCHER_CACHE


class _Breaker:
    """Trips after N consecutive failures with zero successes ever."""

    def __init__(self, threshold: int = _BREAKER_THRESHOLD):
        self._threshold = threshold
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._successes = 0

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self._successes += 1
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1

    @property
    def open(self) -> bool:
        with self._lock:
            return (self._successes == 0
                    and self._consecutive_failures >= self._threshold)


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------

@dataclass
class ChainFetchItem:
    underlying: str
    expiration: str  # ISO date the caller keyed the pair by
    tier: int        # 0 = held contract, 1 = held-underlying future, 2 = candidate
    dte: int
    planned_source: str = "etrade"  # "etrade" | "yfinance"


@dataclass
class ChainFetchPlan:
    items: list = field(default_factory=list)
    max_chains: int = DEFAULT_MAX_CHAINS
    rate_per_sec: float = ETRADE_RATE_PER_SEC

    @property
    def etrade_items(self) -> list:
        return [i for i in self.items if i.planned_source == "etrade"]

    @property
    def yfinance_items(self) -> list:
        return [i for i in self.items if i.planned_source == "yfinance"]

    def estimated_requests(self) -> int:
        """Chain requests + one expirations listing per E*TRADE underlying."""
        return len(self.etrade_items) + len({i.underlying for i in self.etrade_items})

    def throttle_schedule(self) -> list[float]:
        """Ideal request-start offsets (seconds) for the E*TRADE requests."""
        interval = 1.0 / self.rate_per_sec
        return [round(i * interval, 3) for i in range(self.estimated_requests())]

    def describe(self) -> str:
        """Human-readable plan summary (used by the dry-run script)."""
        tiers = Counter(i.tier for i in self.items)
        sched = self.throttle_schedule()
        dur = sched[-1] if sched else 0.0
        lines = [
            f"chains planned: {len(self.items)} "
            f"(tier0 held: {tiers.get(0, 0)} · tier1 held-future: {tiers.get(1, 0)} "
            f"· tier2 candidate: {tiers.get(2, 0)})",
            f"budget: {len(self.etrade_items)} etrade (cap {self.max_chains}) · "
            f"{len(self.yfinance_items)} yfinance-fallback",
            f"throttle: {self.rate_per_sec:.0f} req/s → "
            f"{self.estimated_requests()} E*TRADE requests "
            f"(incl. per-underlying expirations) over ≥{dur:.1f}s",
        ]
        return "\n".join(lines)


def _dte(expiration: str, today: date) -> int:
    try:
        return (date.fromisoformat(str(expiration)) - today).days
    except (ValueError, TypeError):
        return 9999


def build_chain_plan(
    pairs: list[tuple[str, str]],
    positions: list,
    max_chains: int = DEFAULT_MAX_CHAINS,
    rate_per_sec: float = ETRADE_RATE_PER_SEC,
    today: date | None = None,
) -> ChainFetchPlan:
    """Tiered plan over (underlying, expiration) pairs.

    Tier 0: pairs matching a HELD option (underlying + expiration) — these
            back the ROLL ANALYSIS / exit-cost surfaces and go first,
            near-expiry first.
    Tier 1: other expirations on held underlyings (roll candidates).
    Tier 2: candidate-universe chains — fill the remaining budget.
    Beyond ``max_chains``, items are planned as labeled yfinance fallback.
    """
    today = today or date.today()
    held_pairs = {
        (p.get("underlying"), p.get("expiration"))
        for p in (positions or [])
        if p.get("assetType") == "OPTION" and p.get("underlying") and p.get("expiration")
    }
    held_unds = {u for u, _ in held_pairs}

    items = []
    for u, exp in pairs:
        if (u, exp) in held_pairs:
            tier = 0
        elif u in held_unds:
            tier = 1
        else:
            tier = 2
        items.append(ChainFetchItem(u, exp, tier, _dte(exp, today)))

    items.sort(key=lambda i: (i.tier, i.dte, i.underlying, i.expiration))
    for idx, it in enumerate(items):
        it.planned_source = "etrade" if idx < max_chains else "yfinance"
    return ChainFetchPlan(items=items, max_chains=max_chains,
                          rate_per_sec=rate_per_sec)


# --------------------------------------------------------------------------
# Chain execution
# --------------------------------------------------------------------------

def _row_to_record(row) -> dict:
    """OptionChainRow → yfinance-record-compatible dict.

    Downstream consumers (chain_iv, roll_target, exit_cost, new_ideas) read
    strike/bid/ask/lastPrice/openInterest/impliedVolatility — keep those key
    names. Greeks are additive extras E*TRADE gives us for free.
    """
    return {
        "strike": float(row.strike),
        "bid": float(row.bid or 0),
        "ask": float(row.ask or 0),
        "lastPrice": float(row.last or 0),
        "openInterest": int(row.open_interest or 0),
        "impliedVolatility": float(row.iv) if row.iv is not None else None,
        "delta": float(row.delta) if row.delta is not None else None,
        "gamma": float(row.gamma) if row.gamma is not None else None,
        "theta": float(row.theta) if row.theta is not None else None,
        "vega": float(row.vega) if row.vega is not None else None,
    }


def _snap_expiration_iso(target: str, available: list[str]) -> str | None:
    """Closest listed expiration ≥ target; else the latest earlier one."""
    if not available:
        return None
    if target in available:
        return target
    later = sorted(d for d in available if d >= target)
    if later:
        return later[0]
    return sorted(available, reverse=True)[0]


class _ExpirationLister:
    """Per-underlying E*TRADE expiration listing, throttled + deduplicated."""

    def __init__(self, mod, cache, limiter: RateLimiter, breaker: _Breaker):
        self._mod = mod
        self._cache = cache
        self._limiter = limiter
        self._breaker = breaker
        self._results: dict[str, list[str] | None] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._meta = threading.Lock()

    def get(self, underlying: str) -> list[str] | None:
        u = underlying.upper()
        with self._meta:
            if u in self._results:
                return self._results[u]
            lock = self._locks.setdefault(u, threading.Lock())
        with lock:
            if u in self._results:
                return self._results[u]
            if self._breaker.open:
                self._results[u] = None
                return None
            self._limiter.acquire()
            try:
                exps = self._mod.list_expirations(u, cache=self._cache)
            except Exception:
                exps = None
            iso = sorted(d.isoformat() for d in exps) if exps else None
            self._breaker.record(iso is not None)
            self._results[u] = iso
            return iso


def make_expiration_lister(limiter: RateLimiter | None = None):
    """Callable(sym) → [ISO expirations] via E*TRADE, or None (fail-open).

    For _build_chain_pairs: replaces the per-underlying yfinance
    ``t.options`` listing when routing is on. Returns None when the fetcher
    is unavailable (no tokens / no adapter) so the caller can keep its
    yfinance path.
    """
    mod, cache = _load_chain_fetcher()
    if mod is None:
        return None
    lister = _ExpirationLister(mod, cache, limiter or SHARED_LIMITER, _Breaker())
    return lister.get


def fetch_chains_routed(
    plan: ChainFetchPlan,
    spots: dict,
    yf_fetch_many,
    limiter: RateLimiter | None = None,
    max_workers: int = 4,
    fetcher=None,
    fetcher_cache=None,
    report: dict | None = None,
) -> dict:
    """Execute the plan: E*TRADE first (throttled pool), yfinance fallback.

    Args:
        plan: from build_chain_plan.
        spots: {symbol: last price} to center the E*TRADE strike window.
               Items with no spot fall back to yfinance (a mis-centered
               window would silently miss the tradeable strikes).
        yf_fetch_many: callable(list[(underlying, expiration)]) → {key: chain}
               (the existing yfinance parallel fetcher). Chains it returns
               must be labeled source="yfinance" by the producer.
        fetcher/fetcher_cache: injectable for tests.
        report: optional dict the router fills with a wholesale-fallback
               reason (``report["fallback_reason"]``) when NOTHING routed
               through E*TRADE — the caller uses it for the loud provenance
               label (silent wholesale fallback is the bug class this kills).

    Returns {f"{underlying}_{expiration}": chain_dict} where every chain
    carries a truthful per-item ``source`` field.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    limiter = limiter or SHARED_LIMITER
    if fetcher is None:
        fetcher, fetcher_cache = _load_chain_fetcher()

    chains: dict = {}
    yf_pairs: list[tuple[str, str]] = [
        (i.underlying, i.expiration) for i in plan.yfinance_items
    ]

    if fetcher is None:
        # Token absence / adapter missing → everything to yfinance, cleanly.
        if report is not None:
            report["fallback_reason"] = (
                "etrade-chain-fetcher unavailable "
                "(OAuth tokens or pyetrade adapter missing)")
        yf_pairs = [(i.underlying, i.expiration) for i in plan.items]
    else:
        breaker = _Breaker()
        lister = _ExpirationLister(fetcher, fetcher_cache, limiter, breaker)

        def _one(it: ChainFetchItem):
            if breaker.open:
                return it, None
            spot = spots.get(it.underlying)
            if not spot or spot <= 0:
                return it, None
            exps = lister.get(it.underlying)
            actual = _snap_expiration_iso(it.expiration, exps or [])
            if actual is None:
                return it, None
            limiter.acquire()
            try:
                raw = fetcher.get_chain(
                    symbol=it.underlying,
                    expiration=date.fromisoformat(actual),
                    strike_near=float(spot),
                    n_strikes=(_N_STRIKES_HELD if it.tier == 0
                               else _N_STRIKES_CANDIDATE),
                    chain_type="CALLPUT",
                    cache=fetcher_cache,
                )
            except Exception:
                raw = None
            breaker.record(raw is not None)
            if not raw:
                return it, None
            snapped = actual != it.expiration
            if snapped:
                print(f"    [info] {it.underlying}: E*TRADE snapped expiration "
                      f"{it.expiration} → {actual}")
            return it, {
                "underlying": it.underlying,
                "expiration": actual,
                "requested_expiration": it.expiration,
                "snapped": snapped,
                "calls": [_row_to_record(r) for r in (raw.get("calls") or [])],
                "puts": [_row_to_record(r) for r in (raw.get("puts") or [])],
                "fetched_at": datetime.utcnow().isoformat() + "Z",
                "source": "etrade",
            }

        with ThreadPoolExecutor(max_workers=max_workers,
                                thread_name_prefix="et-chain") as ex:
            futs = [ex.submit(_one, it) for it in plan.etrade_items]
            for fut in as_completed(futs):
                try:
                    it, chain = fut.result()
                except Exception as e:  # never block the pipeline
                    print(f"    [warn] etrade chain worker error: {e}",
                          file=sys.stderr)
                    continue
                if chain:
                    chains[f"{it.underlying}_{it.expiration}"] = chain
                else:
                    yf_pairs.append((it.underlying, it.expiration))
        if breaker.open:
            print("    [warn] E*TRADE chain circuit breaker tripped "
                  "(no successes) — remaining chains via yfinance fallback",
                  file=sys.stderr)
            if report is not None:
                report["fallback_reason"] = (
                    "circuit breaker tripped — consecutive E*TRADE chain "
                    "failures with zero successes (auth expiry / rate limit "
                    "/ API outage)")

    if yf_pairs:
        try:
            fallback = yf_fetch_many(yf_pairs) or {}
        except Exception as e:
            print(f"    [warn] yfinance chain fallback failed: {e}",
                  file=sys.stderr)
            fallback = {}
        for key, chain in fallback.items():
            chain.setdefault("source", "yfinance")
            chains.setdefault(key, chain)

    return chains


# --------------------------------------------------------------------------
# Quotes
# --------------------------------------------------------------------------

def plan_quote_batches(symbols: list[str],
                       batch_size: int = QUOTE_BATCH_SIZE) -> list[list[str]]:
    """Split E*TRADE-quotable symbols into ≤25-symbol batches.

    Index symbols (^VIX etc.) are not quotable on the E*TRADE batch API —
    they stay on yfinance.
    """
    quotable = [s for s in symbols if s and not s.startswith("^")]
    return [quotable[i:i + batch_size] for i in range(0, len(quotable), batch_size)]


def _load_quote_fn():
    """adapters.etrade_market.get_quotes, or None (fail-open)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from adapters.etrade_market import get_quotes  # type: ignore
        return get_quotes
    except Exception:
        return None


def fetch_quotes_etrade(
    symbols: list[str],
    limiter: RateLimiter | None = None,
    quote_fn=None,
    batch_size: int = QUOTE_BATCH_SIZE,
    report: dict | None = None,
) -> dict:
    """E*TRADE batch quotes for the full symbol set (25/call, throttled).

    Returns {sym: {last, previousClose, dayChangePct, asOf, source:"etrade"}}.
    Empty dict on any total failure (no tokens, sandbox) — the caller sends
    the misses to yfinance, labeled. Never raises. ``report`` (optional dict)
    is filled with ``fallback_reason`` when NOTHING came back from E*TRADE,
    so the caller can label the wholesale fallback loudly.
    """
    limiter = limiter or SHARED_LIMITER
    if quote_fn is None:
        quote_fn = _load_quote_fn()
    if quote_fn is None:
        if report is not None:
            report["fallback_reason"] = (
                "adapters.etrade_market.get_quotes unavailable "
                "(OAuth tokens or pyetrade adapter missing)")
        return {}

    out: dict = {}
    breaker = _Breaker()
    for batch in plan_quote_batches(symbols, batch_size):
        if breaker.open:
            break
        limiter.acquire()
        try:
            res = quote_fn(batch)
        except Exception:
            res = None
        breaker.record(bool(res))
        if not res:
            continue
        now_iso = datetime.utcnow().isoformat() + "Z"
        for sym, q in res.items():
            last = q.get("last")
            if not last or last <= 0:
                continue  # never ship a zero/absent price as a quote
            prev = q.get("previousClose") or 0
            rec = {
                "last": round(float(last), 2),
                "previousClose": round(float(prev), 2),
                "dayChangePct": round((float(last) - float(prev)) / float(prev), 4)
                if prev else 0.0,
                "asOf": now_iso,
                "source": "etrade",
            }
            out[sym.upper()] = rec
    if not out and report is not None and "fallback_reason" not in report:
        report["fallback_reason"] = (
            "E*TRADE batch quotes returned nothing "
            "(auth expiry / rate limit / API outage)")
    return out


# --------------------------------------------------------------------------
# Provenance helpers (per-item truth — the line-813 mislabel fix)
# --------------------------------------------------------------------------

def chain_source_counts(chains: dict) -> Counter:
    """Count per-chain sources; unlabeled legacy records count as yfinance."""
    return Counter((c.get("source") or "yfinance") for c in (chains or {}).values())


def quote_source_counts(quotes: dict) -> Counter:
    return Counter((q.get("source") or "yfinance") for q in (quotes or {}).values())


def source_split_label(counts: Counter, routing_attempted: bool = False) -> str:
    """Honest provenance label from measured per-item counts.

    NEVER derived from the run-mode flag: "etrade" only when every item came
    from E*TRADE, "etrade+yfinance-fallback" for a mixed cycle, "yfinance"
    when nothing came from E*TRADE. Empty counts label the ATTEMPTED path.
    """
    e = counts.get("etrade", 0)
    y = counts.get("yfinance", 0)
    if e and y:
        return "etrade+yfinance-fallback"
    if e:
        return "etrade"
    if y:
        return "yfinance"
    return "etrade" if routing_attempted else "yfinance"


def split_line(kind: str, counts: Counter) -> str:
    """Console/provenance line, e.g. 'chains: 112 etrade · 38 yfinance-fallback'."""
    return (f"{kind}: {counts.get('etrade', 0)} etrade · "
            f"{counts.get('yfinance', 0)} yfinance-fallback")
