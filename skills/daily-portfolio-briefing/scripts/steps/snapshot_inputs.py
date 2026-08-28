"""
Step 2: Snapshot inputs

Loads holdings from a portfolio fixture (positions/quantities/cost basis) and
refreshes everything else with live data via yfinance:
- Current prices for held tickers + watchlist (SPY, QQQ, VIX)
- Option chains for held option underlyings
- Daily change, IV rank approximation from historical volatility

This is the "real data" path. Holdings are user-supplied (no E*TRADE OAuth in v1);
prices, chains, and market context are pulled live every run.
"""

import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yfinance as yf  # noqa: E402  (already a dependency)

from .fetch_earnings import fetch_earnings_dates
from .fetch_ytd_pnl import fetch_ytd_options_pnl_auto
from analysis import support_resistance as sr_mod
from analysis import technical_indicators as ti_mod
from analysis.json_utils import json_default  # belt-and-suspenders: Decimal/date/Path/set-safe dumps (2026-08-04)

# Persistent OHLC cache (task #9) — optional import; any failure means the
# uncached yfinance path below is used, exactly as before. Fail-open.
try:
    from analysis import ohlc_cache as _ohlc_cache
except Exception:  # noqa: BLE001 - never let a cache module block a briefing
    _ohlc_cache = None


WATCHLIST = ["SPY", "QQQ", "^VIX"]

# Parallelism cap for yfinance calls. yfinance hammering the same backend with
# >32 concurrent calls starts triggering rate limits, so 16 is the sweet spot
# for our typical 22-underlying universe.
_PARALLEL_FETCH_WORKERS = int(os.getenv("PORTFOLIO_BRIEFING_FETCH_WORKERS", "16"))


def load_portfolio_fixture(fixture_path: str) -> dict:
    """Load the user's holdings from JSON fixture (replaces E*TRADE positions API in v1)."""
    with open(fixture_path) as f:
        return json.load(f)


def _safe_float(v) -> float:
    """NaN/None-safe float coercion — non-finite or unparseable values → 0.0."""
    try:
        f = float(v) if v is not None else 0.0
        return f if math.isfinite(f) else 0.0
    except (TypeError, ValueError):
        return 0.0


# Reconciliation threshold: computed-vs-broker NLV divergence above this
# fraction fires a 🔴 data-integrity warning (rendered under the header NLV
# line and in the Live-Data policer panel).
_NLV_RECONCILIATION_PCT = 0.01


def _coerce_jsonable(v):
    """Recursively coerce adapter numerics to JSON-native types.

    The E*TRADE adapter (pyetrade) returns ``Decimal`` for balance fields
    (totalAccountValue, cash, buying power, ...). Any Decimal that leaks
    into snapshot_data eventually lands in briefing_json where a plain
    ``json.dump`` raises ``TypeError`` — the 2026-08-04 live-run failure.
    Coerce at the boundary so snapshot_data is JSON-native throughout.
    """
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _coerce_jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_coerce_jsonable(x) for x in v]
    return v


def _compose_balance(
    base_balance: dict,
    long_market_value: float,
    option_market_value: float,
    positions: list | None = None,
    equity_exclusions: list | None = None,
) -> dict:
    """Compose the snapshot balance dict, preferring the broker's own NLV.

    Root cause of the 2026-08-04 bug: `accountValue` was recomputed as
    long_market_value + cash — EQUITY longs only — which silently dropped
    short-option mark-to-market liabilities (~$65K of short marks). The
    briefing rendered "Portfolio NLV: $1,149,562" while the broker's own
    Portfolios page showed Net Account Value $1,082,940.74 in the same
    session. Broker truth beats reconstruction (rules #10/#19):

    - When E*TRADE's `totalAccountValue` is present (live adapter always
      returns it), USE IT as `accountValue`.
    - The computed value (cash + long MV + SIGNED option marks — short
      options carry negative marketValue in the broker payload) is kept as
      `computedAccountValue` for cross-checking, and is the fallback when
      the broker figure is unavailable (fixtures without it). Fail-open.
    - When both are available and diverge by more than 1%, a
      `nlv_reconciliation` record with `warning: True` is attached so the
      render layer surfaces both numbers — never silently ship a
      reconstructed NLV that disagrees with broker truth.

    2026-08-28 extension (rule #19 — name what's missing, never a bare Δ):

    - **Ledger cash**: the adapter's `cash` is E*TRADE's
      `cashAvailableForInvestment` — a buying-power figure that EXCLUDES
      unsettled same-day trade proceeds. On 2026-08-28 (five option opens +
      the MSFT $510C→$530C roll) it understated real cash by $37,697 and
      fired a false 🔴 warning. When the adapter provides `netCash` (total
      ledger cash), the computed NLV uses it, with an itemized note.
    - **Missing marks**: any OPTION position with nonzero qty and a
      missing mark (`marketValue` None, or 0 with a live mid available)
      gets its mark ESTIMATED from the snapshot's own mid (or bid/ask
      midpoint); if no estimate is possible it is EXCLUDED with a named
      note ("computed excludes MSFT Mar 19 '27 $530 Call — no mark this
      snapshot"). Equity positions skipped upstream for no usable price
      arrive via `equity_exclusions` and are itemized the same way.
    - **Classification**: when the warning still fires and every position
      carries a mark, the record says so — the Δ is by elimination a
      cash-ledger gap (broker NLV implies more cash than the cash field
      reports), so the operator sees "data gap on known cash leg", not an
      unknown disagreement. All notes ride in
      `nlv_reconciliation["itemized"]` for the header + attribution panel.
    """
    # Boundary coercion: the live adapter returns Decimal for SEVERAL
    # balance fields (totalAccountValue, cash, netCash, buying power, ...).
    # Coerce the whole dict before it's spread into the snapshot balance —
    # a Decimal here killed the 2026-08-04 run at the Step 10 json.dump.
    base_balance = {k: _coerce_jsonable(v) for k, v in (base_balance or {}).items()}

    cash = _safe_float(base_balance.get("cash", 0))
    itemized: list[str] = []

    # ── Ledger cash: prefer netCash (total, incl. unsettled) when the
    # adapter provided it — cashAvailableForInvestment excludes unsettled
    # same-day trade proceeds (the 2026-08-28 $37,697 false alarm).
    ledger_cash = cash
    if "netCash" in base_balance:
        net_cash = _safe_float(base_balance.get("netCash"))
        if abs(net_cash) > 1e-9:  # present-but-zero with cash>0 → distrust
            ledger_cash = net_cash
            if abs(net_cash - cash) > 0.01:
                itemized.append(
                    f"ledger cash (netCash) ${net_cash:,.0f} used for the "
                    f"computed NLV — available-for-investment cash "
                    f"${cash:,.0f} excludes ${net_cash - cash:,.0f} of "
                    f"unsettled/withheld funds"
                )

    # ── Missing option marks: estimate from the snapshot's own mid where
    # available; otherwise exclude WITH a named note (rule #19).
    effective_option_mv = option_market_value
    mark_exclusions: list[str] = []
    for pos in positions or []:
        if pos.get("assetType") != "OPTION":
            continue
        qty = _safe_float(pos.get("qty"))
        if qty == 0:
            continue
        mv = pos.get("marketValue")
        mv_f = _safe_float(mv)
        name = pos.get("symbolDescription") or pos.get("symbol") or "?"
        mid = _safe_float(pos.get("currentMid"))
        if mid == 0:
            bid, ask = _safe_float(pos.get("bid")), _safe_float(pos.get("ask"))
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
        if mv is None:
            if mid > 0:
                est = round(mid * 100.0 * qty, 2)
                effective_option_mv += est
                itemized.append(
                    f"{name} — mark estimated at ${est:,.0f} from the "
                    f"snapshot mid ${mid:,.2f} (no marketValue this snapshot)"
                )
            else:
                mark_exclusions.append(name)
                itemized.append(
                    f"computed excludes {name} — no mark this snapshot"
                )
        elif mv_f == 0 and mid > 0:
            # A zero mark with a live mid is a stale/absent mark on a real
            # contract (new open / fresh roll); a zero mark with no mid is a
            # genuinely worthless option — no note (no noise on clean days).
            est = round(mid * 100.0 * qty, 2)
            effective_option_mv += est
            itemized.append(
                f"{name} — mark estimated at ${est:,.0f} from the "
                f"snapshot mid ${mid:,.2f} (marketValue $0 this snapshot)"
            )

    for sym in equity_exclusions or []:
        itemized.append(
            f"computed excludes {sym} — no usable price this snapshot"
        )

    computed_nlv = round(long_market_value + effective_option_mv + ledger_cash, 2)
    broker_nlv = _safe_float(base_balance.get("totalAccountValue", 0))

    balance = {
        **base_balance,
        "longMarketValue": round(long_market_value, 2),
        "optionMarketValue": round(effective_option_mv, 2),
        "computedAccountValue": computed_nlv,
    }

    if broker_nlv > 0:
        # Broker truth wins.
        balance["accountValue"] = round(broker_nlv, 2)
        delta = round(computed_nlv - broker_nlv, 2)
        pct = abs(delta) / broker_nlv
        warning = pct > _NLV_RECONCILIATION_PCT
        if warning and not mark_exclusions and not (equity_exclusions or []):
            # Every position carries a mark → by elimination the Δ sits on
            # the cash leg. Name it (rule #19): the broker's own components
            # imply a different cash figure than the cash field reports —
            # the 2026-08-28 signature (unsettled same-day trade proceeds
            # missing from cashAvailableForInvestment).
            implied_cash = round(
                broker_nlv - long_market_value - effective_option_mv, 2)
            itemized.append(
                f"all positions carry marks — Δ is a cash-ledger gap: "
                f"broker NLV implies cash ${implied_cash:,.0f} vs "
                f"${ledger_cash:,.0f} in the cash field (likely unsettled "
                f"same-day trade proceeds; verify at broker)"
            )
        balance["nlv_reconciliation"] = {
            "broker_nlv": round(broker_nlv, 2),
            "computed_nlv": computed_nlv,
            "delta": delta,
            "pct": round(pct * 100, 2),
            "warning": warning,
            "using": "broker",
            **({"itemized": itemized} if itemized else {}),
        }
    else:
        # No broker figure (fixture without totalAccountValue) — fall back
        # to the computed value, which now includes signed option marks.
        balance["accountValue"] = computed_nlv

    return balance


def _fetch_price_history(ticker: str, days: int = 252) -> "yf.Ticker | None":
    """Get yfinance Ticker with recent history loaded. Returns None on failure."""
    try:
        t = yf.Ticker(ticker)
        # Force history pull so subsequent .info / .history calls don't refetch
        t.history(period=f"{days}d")
        return t
    except Exception as e:
        print(f"    [warn] yfinance failed for {ticker}: {e}", file=sys.stderr)
        return None


def _live_quote(ticker: str) -> dict:
    """Fetch live quote + day change + 5d change. Returns {} on failure.

    NaN-safe: on US market holidays (Juneteenth, MLK Day, etc.) or pre-open
    sessions, yfinance's ``history(period="10d")`` can include today as a
    partial bar with NaN Close. Dropping NaN rows first ensures ``last``
    is always the most recent REAL close, not a partial/holiday bar.
    (Symptom 2026-06-19 Juneteenth: every quote came back ``last=nan``,
    poisoning the entire snapshot's prices.)
    """
    yf_ticker = ticker.replace("^", "^") if ticker.startswith("^") else ticker
    t = _fetch_price_history(yf_ticker, days=10)
    if t is None:
        return {}
    try:
        hist = t.history(period="10d")
        if hist.empty:
            return {}
        # Drop rows where Close is NaN — handles holiday/pre-open partial bars.
        closes = hist["Close"].dropna()
        if closes.empty:
            print(f"    [warn] quote for {ticker}: history has no usable closes",
                  file=sys.stderr)
            return {}
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else last
        day_change_pct = (last - prev) / prev if prev else 0.0
        if len(closes) >= 6:
            five_d_ago = float(closes.iloc[-6])
            five_d_change_pct = (last - five_d_ago) / five_d_ago if five_d_ago else 0.0
        else:
            five_d_change_pct = day_change_pct
        # Final NaN guard — if any of the computed values somehow non-finite,
        # bail out cleanly so downstream callers don't see NaN.
        if not (math.isfinite(last) and math.isfinite(day_change_pct)):
            return {}
        return {
            "last": round(last, 2),
            "previousClose": round(prev, 2),
            "dayChangePct": round(day_change_pct, 4),
            "fiveDayChangePct": round(five_d_change_pct, 4),
            "asOf": datetime.utcnow().isoformat() + "Z",
        }
    except Exception as e:
        print(f"    [warn] quote fetch for {ticker}: {e}", file=sys.stderr)
        return {}


def _historical_volatility_rank(ticker: str) -> float | None:
    """Approximate IV rank using 252-day historical vol percentile of current vol."""
    tech = _full_technicals(ticker)
    return tech.get("iv_rank") if tech else None


def _full_technicals(ticker: str) -> dict | None:
    """Pull 730d history once and compute IV-rank, RSI(14), 200-SMA, drawdown,
    S/R, and the deep technical read (BB/MACD/ATR/slopes/verdicts).

    The window was widened from 300d → 730d so the deep read has real 1-year
    returns + a ~2y drawdown reference (same single fetch — no extra network).
    Existing fields keep their formulas; the longer window only makes the
    252-obs metrics (drawdown, IV-rank percentile) use their full documented
    lookback instead of being truncated at ~205 bars.

    Returns dict with keys (any may be None on insufficient data):
      - iv_rank: percentile of 20d realized vol over past 252 obs
      - rsi_14: Wilder's RSI(14)
      - sma_50: 50-day simple moving average (used for S/R confluence)
      - sma_200: 200-day simple moving average
      - drawdown_pct: % off rolling 252-day high (positive number = below high)
      - spot: latest close
      - support_resistance: dict from SupportResistance.to_dict() — fail-closed
        when there's insufficient history (kept under one key for downstream wiring)
      - deep: dict from TechnicalSnapshot.to_dict() (technical_indicators.py) —
        None when history is insufficient; renderers then surface
        "chart data unavailable" instead of fabricated indicators
    Returns None on fetch failure.
    """
    # OHLC via the persistent cache when available (task #9). cached_history
    # is a drop-in for yf.Ticker(...).history(period="730d") and internally
    # falls back to the raw call on any cache problem. If the cache module
    # itself is unavailable or errors, use the original uncached path.
    hist = None
    if _ohlc_cache is not None:
        try:
            hist = _ohlc_cache.cached_history(ticker, days=730)
        except Exception as e:
            print(f"    [warn] ohlc cache for {ticker}: {e} — uncached path",
                  file=sys.stderr)
            hist = None
    if hist is None:
        t = _fetch_price_history(ticker, days=730)
        if t is None:
            return None
    try:
        if hist is None:
            hist = t.history(period="730d")
        if hist.empty:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 60:
            return None

        spot = float(closes.iloc[-1])

        # IV rank via 20d realized vol percentile — canonical 252-obs window
        # implementation in analysis/technical_indicators.iv_rank_252 (shared
        # with the thematic scout and broad-universe screener; Task #12).
        iv_rank: float | None = ti_mod.iv_rank_252(closes)

        # RSI(14) — Wilder's smoothing, canonical implementation in
        # analysis/technical_indicators.py (identical formula/rounding to the
        # inline block this replaced; the >= 30 bar guard is kept caller-side
        # so behavior is byte-identical for every reachable input).
        rsi_14: float | None = None
        if len(closes) >= 30:
            rsi_14 = ti_mod.wilder_rsi(closes)

        # 50-SMA (used for S/R confluence)
        sma_50: float | None = None
        if len(closes) >= 50:
            sma_50 = round(float(closes.tail(50).mean()), 2)

        # 200-SMA
        sma_200: float | None = None
        if len(closes) >= 200:
            sma_200 = round(float(closes.tail(200).mean()), 2)

        # Drawdown vs trailing 252d high
        drawdown_pct: float | None = None
        window = closes.tail(252)
        if not window.empty:
            high = float(window.max())
            if high > 0:
                drawdown_pct = round((high - spot) / high * 100.0, 1)

        # Support/Resistance — computed from the same OHLC DataFrame (no extra fetch).
        # Fail-closed: missing data → SupportResistance with note + empty levels,
        # which serializes to a dict downstream consumers can render or skip.
        sr_data: dict | None = None
        try:
            sr_result = sr_mod.compute_sr(
                hist,
                spot=float(spot),
                sma_50=sma_50,
                sma_200=sma_200,
            )
            sr_data = sr_result.to_dict()
        except Exception as e:
            print(f"    [warn] S/R compute for {ticker}: {e}", file=sys.stderr)

        # Deep technical read (BB/MACD/ATR/SMA-slopes/52w/ATH/returns +
        # short/long-term verdicts) — computed from the SAME OHLC pull, no
        # extra fetch. Fail-closed: insufficient history → deep=None and the
        # Technical Read section renders "chart data unavailable, verify
        # manually" for this ticker (never fabricated indicators).
        deep_data: dict | None = None
        try:
            deep_snap = ti_mod.compute_technicals(ticker, hist, rsi_14=rsi_14)
            if deep_snap is not None:
                deep_data = deep_snap.to_dict()
        except Exception as e:
            print(f"    [warn] deep technicals for {ticker}: {e}", file=sys.stderr)

        return {
            "iv_rank": iv_rank,
            "rsi_14": rsi_14,
            "sma_50": sma_50,
            "sma_200": sma_200,
            "drawdown_pct": drawdown_pct,
            "spot": round(spot, 2),
            "support_resistance": sr_data,
            "deep": deep_data,
            # Last ~6 closes (chronological) from the SAME OHLC pull — lets
            # analysis/iv_honesty.detect_recent_gap flag an earnings gap that
            # inflates the realized-vol IV-rank proxy (rule #43). No extra fetch.
            "recent_closes": [round(float(c), 4) for c in closes.tail(6).tolist()],
            # Last ~60 closes (chronological), same OHLC pull — threads the
            # close series the vintage guard needs to RECOMPUTE Wilder's RSI
            # live (rules #46/#47). 6 closes can never seed a 14-period RSI,
            # so the 2026-08-17 briefing excluded WDC/SNDK/MU with "stale RSI
            # on a +9.4% up-move — live RSI not computable" even though the
            # snapshot RSI was computed from these very closes. 60 bars give
            # the Wilder smoothing enough warm-up to converge (<0.5 pt vs
            # the full series). No extra fetch, no extra network.
            "rsi_closes": [round(float(c), 4) for c in closes.tail(60).tolist()],
        }
    except Exception as e:
        print(f"    [warn] technicals fetch for {ticker}: {e}", file=sys.stderr)
        return None


def _deduplicate_positions(positions: list) -> list:
    """Aggregate positions by symbol across accounts.

    For each unique symbol, sum quantities and compute weighted-average cost basis.
    Preserve account breakdown in a new field.
    """
    by_symbol = {}

    for pos in positions:
        sym = pos.get("symbol")
        if not sym:
            continue

        if sym not in by_symbol:
            by_symbol[sym] = {
                "position": dict(pos),
                "accounts": [],
                "total_qty": 0,
                "total_cost": 0,
            }

        entry = by_symbol[sym]
        qty = float(pos.get("qty", 0))
        cost_basis = float(pos.get("costBasis", 0))
        acct = pos.get("accountDesc", "unknown")

        entry["accounts"].append(f"{acct}: {qty:.0f} sh")
        entry["total_qty"] += qty
        entry["total_cost"] += cost_basis * qty

    # Build deduplicated position list
    dedup = []
    for sym, entry in by_symbol.items():
        dedup_pos = entry["position"]
        dedup_pos["qty"] = entry["total_qty"]

        # Weighted-average cost basis
        if entry["total_qty"] != 0:
            dedup_pos["costBasis"] = entry["total_cost"] / entry["total_qty"]

        # Preserve account breakdown
        dedup_pos["accountsBreakdown"] = entry["accounts"]

        dedup.append(dedup_pos)

    return dedup


def _snap_expiration(target: str, available: list[str]) -> str | None:
    """Pick the closest valid expiration ≥ target. Returns None if none after target."""
    if target in available:
        return target
    if not available:
        return None
    # Pick the smallest available date ≥ target
    later = sorted(d for d in available if d >= target)
    if later:
        return later[0]
    # Otherwise the closest earlier
    return sorted(available, reverse=True)[0]


def _fetch_option_chain(underlying: str, expiration: str) -> dict | None:
    """Fetch option chain for one expiration. Snaps to nearest valid date if needed."""
    t = _fetch_price_history(underlying, days=5)
    if t is None:
        return None
    try:
        # yfinance: get the available expiration list and snap if needed
        try:
            available = list(t.options or [])
        except Exception:
            available = []

        actual = _snap_expiration(expiration, available)
        if actual is None:
            print(f"    [warn] no expirations available for {underlying}", file=sys.stderr)
            return None
        snapped = (actual != expiration)
        if snapped:
            print(f"    [info] {underlying}: snapped expiration {expiration} → {actual}")

        opt = t.option_chain(actual)
        calls = opt.calls.to_dict("records") if not opt.calls.empty else []
        puts = opt.puts.to_dict("records") if not opt.puts.empty else []

        # Strip pandas / numpy types so json.dump works cleanly
        import pandas as pd  # already pulled in by yfinance
        def _coerce(v):
            if v is None:
                return None
            if isinstance(v, float) and math.isnan(v):
                return None
            if isinstance(v, (pd.Timestamp, datetime)):
                return v.isoformat()
            if hasattr(v, "item"):
                try:
                    return v.item()
                except Exception:
                    return str(v)
            return v

        def _clean(rows):
            return [{k: _coerce(v) for k, v in r.items()} for r in rows]

        return {
            "underlying": underlying,
            "expiration": actual,
            "requested_expiration": expiration,
            "snapped": snapped,
            "calls": _clean(calls),
            "puts": _clean(puts),
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            # Per-item source truth (task #36): this producer is yfinance.
            # Provenance labels are derived from these per-chain fields,
            # never from the run-mode flag.
            "source": "yfinance",
        }
    except Exception as e:
        print(f"    [warn] chain fetch failed for {underlying} {expiration}: {e}", file=sys.stderr)
        return None


def _parallel_market_data_fetch(
    quote_symbols: list[str],
    technical_symbols: list[str],
    earnings_symbols: list[str],
) -> tuple[dict, dict, dict, dict]:
    """Run quotes / technicals / earnings yfinance pulls concurrently.

    Returns (quotes, iv_ranks, technicals, earnings_calendar) where:
      - quotes[sym] = {last, dayChangePct, ...}
      - iv_ranks[sym] = float (extracted from technicals for backward compat)
      - technicals[sym] = {iv_rank, rsi_14, sma_200, drawdown_pct, spot}
      - earnings_calendar[sym] = "YYYY-MM-DD"

    Failures on individual symbols are logged but do not abort the batch — a
    missing technical or earnings date is degraded data, not a fatal error. The
    surrounding pre-flight gates decide whether the briefing can still ship.
    """
    quotes: dict = {}
    iv_ranks: dict = {}
    technicals: dict = {}
    earnings_calendar: dict = {}

    # Wrap fetch_earnings's per-ticker function so we can dispatch it like the
    # other workers. fetch_earnings already runs the underlying yfinance calls
    # inside a watchdog thread, so re-wrapping it in another thread is fine
    # (the inner watchdog still bounds latency to ~5s).
    from .fetch_earnings import _fetch_earnings_for_ticker  # local import: heavy

    tasks: list = []
    with ThreadPoolExecutor(
        max_workers=_PARALLEL_FETCH_WORKERS,
        thread_name_prefix="yf-fetch",
    ) as ex:
        for sym in quote_symbols:
            tasks.append(("quote", sym, ex.submit(_live_quote, sym)))
        for sym in technical_symbols:
            tasks.append(("tech", sym, ex.submit(_full_technicals, sym)))
        for sym in earnings_symbols:
            tasks.append(("earn", sym, ex.submit(_fetch_earnings_for_ticker, sym, 5)))

        for kind, sym, fut in tasks:
            try:
                value = fut.result(timeout=30)
            except Exception as e:
                print(f"    [warn] {kind} fetch failed for {sym}: {e}", file=sys.stderr)
                continue
            if not value:
                continue
            if kind == "quote":
                quotes[sym] = value
            elif kind == "tech":
                technicals[sym] = value
                if value.get("iv_rank") is not None:
                    iv_ranks[sym] = value["iv_rank"]
            elif kind == "earn":
                earnings_calendar[sym] = value

    # Rule #43 (RDDT 2026-07-31): yfinance returning NO earnings date for a
    # single-stock name is common (recent IPOs, sparse coverage) — try FMP
    # as a secondary source before the date is declared unknown downstream.
    # Fail-closed inside fmp_next_earnings (no FMP_API_KEY / error → None,
    # memoized per process); ETFs skipped (no print to look up).
    missing = [s for s in earnings_symbols if s not in earnings_calendar]
    if missing:
        try:
            from analysis.earnings_unknown import (
                fmp_next_earnings, is_earnings_exempt)
            filled = 0
            for sym in missing:
                if is_earnings_exempt(sym):
                    continue
                d = fmp_next_earnings(sym)
                if d:
                    earnings_calendar[sym] = d
                    filled += 1
            if filled:
                print(f"    [info] FMP earnings fallback filled {filled} "
                      f"date(s) missing from yfinance", file=sys.stderr)
        except Exception as e:
            print(f"    [warn] FMP earnings fallback failed: {e}",
                  file=sys.stderr)

    return quotes, iv_ranks, technicals, earnings_calendar


def _parallel_chain_fetch(pairs: list[tuple[str, str]]) -> dict:
    """Fetch (underlying, expiration) chain pairs concurrently.

    Returns {f"{underlying}_{expiration}": chain_dict}.

    Each yfinance option-chain call is its own network round-trip; with a
    typical book of 11 underlyings × 4 expirations = 44 chains, this drops
    from ~90s sequential to ~8s with 16 workers.
    """
    chains: dict = {}
    if not pairs:
        return chains

    def _job(underlying: str, expiration: str):
        return _fetch_option_chain(underlying, expiration)

    with ThreadPoolExecutor(
        max_workers=_PARALLEL_FETCH_WORKERS,
        thread_name_prefix="yf-chain",
    ) as ex:
        future_to_key = {
            ex.submit(_job, underlying, expiration): f"{underlying}_{expiration}"
            for underlying, expiration in pairs
        }
        for fut in as_completed(future_to_key):
            key = future_to_key[fut]
            try:
                chain = fut.result(timeout=45)
            except Exception as e:
                print(f"    [warn] chain fetch failed for {key}: {e}", file=sys.stderr)
                continue
            if chain:
                chains[key] = chain

    return chains


def _build_chain_pairs(
    refreshed_positions: list,
    underlyings_with_options: list[str],
    list_expirations_fn=None,
) -> list[tuple[str, str]]:
    """Compute (underlying, expiration) pairs to fetch.

    For HELD underlyings (user already has options open): include each held
    expiration plus up to 3 future expirations so the wheel-roll-advisor can
    enumerate roll candidates.

    For UN-HELD underlyings (new candidate tickers — Parkev BUYs, scout
    candidates): pick a short-term CSP expiration (~30 DTE), a medium-term
    one (~60-90 DTE for LT_CSP), and a long-dated one (~120+ DTE for patient
    capital). This is the fix for the UBER case (2026-06-30): TOP CONVICTION
    candidates that the user doesn't currently hold options on must still
    have a live chain ticket so they can decide whether to act on the signal.
    """
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    from datetime import date as _date

    for underlying in underlyings_with_options:
        held_exps = sorted({
            p["expiration"] for p in refreshed_positions
            if p.get("assetType") == "OPTION"
            and p.get("underlying") == underlying
            and p.get("expiration")
        })
        # Expiration listing: E*TRADE-backed lister when routing (task #36),
        # yfinance otherwise. If the lister returns nothing for a symbol,
        # fall through to yfinance so pair-building never degrades below the
        # pre-routing behavior.
        available: list[str] = []
        if list_expirations_fn is not None:
            try:
                available = list(list_expirations_fn(underlying) or [])
            except Exception:
                available = []
        if not available:
            try:
                t = yf.Ticker(underlying)
                available = list(t.options or [])
            except Exception:
                available = []

        future_exps: list[str] = []
        if available and held_exps:
            # Held-ticker path: future expirations beyond the latest held one
            latest_held = held_exps[-1]
            future_candidates = [e for e in available if e > latest_held]
            if future_candidates:
                future_exps.append(future_candidates[0])
                if len(future_candidates) >= 5:
                    future_exps.append(future_candidates[len(future_candidates) // 2])
                if len(future_candidates) >= 2:
                    future_exps.append(future_candidates[-1])
                future_exps = list(dict.fromkeys(future_exps))[:3]
        elif available and not held_exps:
            # Un-held-ticker path: pick short-term (~30 DTE) + medium (~60-90 DTE)
            # + long-dated (~120+ DTE) so candidate_research and
            # long_term_opportunities can render real CSP tickets.
            today = _date.today()
            def _days(exp: str) -> int:
                try:
                    return (_date.fromisoformat(exp) - today).days
                except (ValueError, TypeError):
                    return -1
            with_dte = sorted([(e, _days(e)) for e in available if _days(e) > 0],
                              key=lambda x: x[1])
            if with_dte:
                target_dtes = (30, 75, 120)  # short / medium / long
                chosen: list[str] = []
                for tgt in target_dtes:
                    # Pick the expiration closest to the target DTE that hasn't
                    # already been selected. Falls back gracefully when fewer
                    # expirations are available than targets.
                    best = min(with_dte, key=lambda x: (abs(x[1] - tgt),
                                                         1 if x[0] in chosen else 0))
                    if best[0] not in chosen:
                        chosen.append(best[0])
                future_exps = chosen[:3]

        for expiration in held_exps + future_exps:
            key = (underlying, expiration)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)

    return pairs


def snapshot_inputs(
    config: dict,
    snapshot_dir: Path,
    etrade_fixture: str = None,
    etrade_live: bool = False,
    extra_chain_underlyings: list[str] | None = None,
) -> dict:
    """Snapshot all inputs with live yfinance refresh.

    If etrade_live=True, fetch real positions/balance from E*TRADE via pyetrade.
    Otherwise load from the fixture path. Either way, prices/chains/regime
    inputs are always live yfinance.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Apply briefing.yaml → ohlc_cache overrides (enabled/dir/max_stale_days).
    # Fail-open: a bad config block just leaves the module defaults in place.
    if _ohlc_cache is not None:
        try:
            _ohlc_cache.configure(config.get("ohlc_cache"))
        except Exception as e:
            print(f"    [warn] ohlc_cache configure failed: {e}", file=sys.stderr)

    accounts: list = []
    positions: list = []
    base_balance: dict = {}
    open_orders: list = []
    theses: dict = {}
    source = "fixture"
    snapshot_warnings: list = []

    if etrade_live:
        # Real E*TRADE pull via the adapter
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from adapters.etrade_adapter import fetch_etrade_snapshot
        # Honor an account-scope filter from briefing.yaml. Default scope is
        # the single INDIVIDUAL brokerage account — the user has multiple
        # accounts at E*TRADE (Joint, Individual Brokerage, INDIVIDUAL, IRAs)
        # but the briefing pipeline is currently aligned only to INDIVIDUAL.
        # See CLAUDE.md for the canonical rule.
        account_desc_whitelist = config.get("account_desc_whitelist") or ["INDIVIDUAL"]
        print(f"  Pulling REAL holdings from E*TRADE (scoped to: {account_desc_whitelist})...")
        snap = fetch_etrade_snapshot(account_desc_whitelist=account_desc_whitelist)
        accounts = snap.accounts
        positions = snap.positions
        base_balance = snap.balance
        open_orders = snap.open_orders
        source = snap.source
        snapshot_warnings = snap.warnings

        # Deduplicate positions by ticker across all accounts
        # Aggregate qty and cost basis, preserve account breakdown
        positions = _deduplicate_positions(positions)

        print(f"  E*TRADE: {len(accounts)} accounts, {len(positions)} unique symbols, NLV ${base_balance.get('accountValue', 0):,.0f}")
        for w in snapshot_warnings:
            print(f"    [warn] {w}")
    elif etrade_fixture:
        fixture = load_portfolio_fixture(etrade_fixture)
        accounts = fixture.get("accounts", [])
        positions = fixture.get("positions", [])
        base_balance = fixture.get("balance", {})
        open_orders = fixture.get("open_orders", [])
        theses = fixture.get("theses", {})
    else:
        raise RuntimeError(
            "Either --etrade-live or --etrade-fixture must be specified."
        )

    # ---------------------------------------------------------------------
    # Broker-truth positions for reconciliation.
    # The pre-flight position-reconciler compares the snapshot positions
    # (potentially built from a stale fixture) against current broker truth.
    # Sources, in priority order:
    #   1. If etrade_live=True, use the same fetched positions as broker truth.
    #   2. Otherwise, look for a manually-provided override at
    #      state/broker_positions.json — the user can paste their broker
    #      screenshot's positions there to force a check.
    #   3. If neither, broker_positions remains None and the reconciler will
    #      surface a "Position Reconciler Skipped" warning panel.
    # ---------------------------------------------------------------------
    broker_positions: list | None = None
    if etrade_live:
        broker_positions = list(positions)  # same as snapshot — trivially passes
    else:
        # Try to load a manual override
        try:
            override_path = Path("state/broker_positions.json")
            if override_path.exists():
                with open(override_path) as f:
                    override = json.load(f)
                broker_positions = override if isinstance(override, list) else override.get("positions")
                if broker_positions:
                    print(f"  Loaded {len(broker_positions)} broker positions from {override_path} (manual override)")
        except Exception as e:
            snapshot_warnings.append(f"broker_positions override read failed: {e}")

    # Identify unique tickers we need quotes for
    underlyings = set()
    for pos in positions:
        if pos.get("assetType") == "EQUITY":
            underlyings.add(pos["symbol"])
        elif pos.get("assetType") == "OPTION":
            underlyings.add(pos.get("underlying", ""))

    # ------------------------------------------------------------------
    # Parallel yfinance fetches: quotes + technicals (IV+RSI+SMA+drawdown) +
    # earnings. Each ticker fetch is independent so we run them in a thread
    # pool. Empirically, 22 underlyings drops from ~60s sequential to ~3-6s.
    # ------------------------------------------------------------------
    # Technicals cover held underlyings PLUS candidate underlyings
    # (extra_chain_underlyings — Parkev BUYs, scout candidates) so the
    # Technical Read section can render a deep card for every candidate too.
    _extra_tech = {str(u).upper() for u in (extra_chain_underlyings or []) if u}
    technical_symbols = sorted({s for s in (underlyings | _extra_tech) if s})
    earnings_symbols = sorted({s for s in underlyings if s})

    # ------------------------------------------------------------------
    # Task #36 — quote/chain routing through E*TRADE (hard rule #2).
    # Routing is ON when the run is live OR config chains.source == "etrade".
    # Fail-open at every layer: no tokens / adapter missing → yfinance,
    # LABELED per item; the pipeline never blocks on E*TRADE.
    # ------------------------------------------------------------------
    chains_cfg = (config.get("chains") or {}) if isinstance(config, dict) else {}
    etrade_routing = bool(etrade_live) or (
        str(chains_cfg.get("source", "")).lower() == "etrade")

    # Quote universe (2026-08-06 regression): when routing is ON, quotes
    # cover the FULL technicals/candidate universe — the E*TRADE batch API
    # handles ~130 symbols in ~6 throttled calls, and this is what actually
    # erases the "111 name(s) had no live quote this cycle" vintage-guard
    # gap. The 2026-08-05 build routed quotes but left the requested set at
    # held ∪ WATCHLIST (~23 names), so the 111 candidate names were never
    # ASKED for — routing "engaged" while the coverage goal silently failed.
    # Without routing, keep the legacy narrow set (yfinance can't absorb
    # 130 quote calls without throttling — that was the original gap).
    if etrade_routing:
        quote_symbols = sorted(
            {s for s in (underlyings | set(WATCHLIST) | _extra_tech) if s})
    else:
        quote_symbols = sorted({s for s in (underlyings | set(WATCHLIST)) if s})
    _routing = None
    if etrade_routing:
        try:
            from analysis import chain_routing as _routing  # noqa: N813
        except Exception as e:
            print(f"    [warn] chain_routing unavailable ({e}) — "
                  f"yfinance path for this run", file=sys.stderr)
            _routing = None

    # E*TRADE batch quotes FIRST (25 symbols/call, ≤4 req/s) — this is what
    # erases the yfinance-throttle quote gap (111/134 missing, 2026-07-30).
    # yfinance covers only the E*TRADE misses (index symbols, failures).
    etrade_quotes: dict = {}
    _quote_report: dict = {}
    if _routing is not None:
        try:
            etrade_quotes = _routing.fetch_quotes_etrade(
                quote_symbols, report=_quote_report)
        except Exception as e:
            print(f"    [warn] E*TRADE batch quotes failed: {e} — "
                  f"yfinance fallback for all symbols", file=sys.stderr)
            etrade_quotes = {}
            _quote_report.setdefault("fallback_reason", f"router raised: {e}")
    yf_quote_symbols = [s for s in quote_symbols if s not in etrade_quotes]

    print(
        f"  Fetching live data in parallel "
        f"(quotes:{len(yf_quote_symbols)}"
        f"{' yf + ' + str(len(etrade_quotes)) + ' etrade' if etrade_quotes else ''}, "
        f"technicals:{len(technical_symbols)}, "
        f"earnings:{len(earnings_symbols)}, workers={_PARALLEL_FETCH_WORKERS})..."
    )
    quotes, iv_ranks, technicals, earnings_calendar = _parallel_market_data_fetch(
        yf_quote_symbols, technical_symbols, earnings_symbols
    )
    # Per-item source labels (task #36): yfinance quotes labeled at merge,
    # E*TRADE quotes carry source="etrade" from the router.
    for _q in quotes.values():
        _q.setdefault("source", "yfinance")
    quotes.update(etrade_quotes)
    # Backfill fiveDayChangePct on E*TRADE quotes from the technicals' own
    # OHLC pull (recent_closes) — no extra network; absent when unavailable.
    for _sym, _q in quotes.items():
        if _q.get("source") == "etrade" and "fiveDayChangePct" not in _q:
            _rc = ((technicals.get(_sym) or {}).get("recent_closes") or [])
            if len(_rc) >= 6 and _rc[0]:
                _q["fiveDayChangePct"] = round(
                    (_q["last"] - _rc[0]) / _rc[0], 4)
    print(
        f"  Parallel fetch complete: {len(quotes)} quotes, "
        f"{len(iv_ranks)} IV ranks, {len(technicals)} technical sets, "
        f"{len(earnings_calendar)} earnings dates"
    )
    if _routing is not None:
        print("  " + _routing.split_line(
            "quotes", _routing.quote_source_counts(quotes)))

    # Fetch YTD options P&L from E*TRADE if available
    ytd_pnl: dict = {}
    if etrade_live:
        print("  Fetching YTD options P&L from E*TRADE...")
        try:
            ytd_pnl = fetch_ytd_options_pnl_auto()
            if ytd_pnl.get("error"):
                print(f"    [warn] YTD P&L fetch error: {ytd_pnl['error']}", file=sys.stderr)
            else:
                print(f"    [info] YTD: ${ytd_pnl.get('premium_collected', 0):,.0f} collected, " +
                      f"${ytd_pnl.get('realized_losses', 0):,.0f} losses")
        except Exception as e:
            print(f"    [warn] YTD P&L fetch failed: {e}", file=sys.stderr)

    # Refresh equity prices on positions with live quotes
    refreshed_positions = []
    for pos in positions:
        new_pos = dict(pos)
        sym = pos["symbol"] if pos.get("assetType") == "EQUITY" else pos.get("underlying")
        if sym in quotes:
            if pos.get("assetType") == "EQUITY":
                new_pos["price"] = quotes[sym]["last"]
                new_pos["dayChangePct"] = quotes[sym].get("dayChangePct", 0)
        refreshed_positions.append(new_pos)

    # Recompute account value from refreshed prices.
    # NaN-safe: yfinance/E*TRADE quote fetches can return None or NaN
    # (e.g., 2026-06-18 had SPY/SMH/SOXX/VOO 404s). A single NaN in the
    # sum propagates to NLV=$nan, which then crashes capacity_gates with
    # `decimal.InvalidOperation` on `nlv_d > 0`. Skip non-finite prices
    # with a warning so the NLV computes from the resolvable positions —
    # callers downstream still see a real number to gate on.
    _safe = _safe_float

    skipped: list[str] = []
    long_market_value = 0.0
    for pos in refreshed_positions:
        if pos.get("assetType") != "EQUITY":
            continue
        qty = _safe(pos.get("qty"))
        if qty <= 0:
            continue
        price = _safe(pos.get("price"))
        if price <= 0:
            skipped.append(pos.get("symbol", "?"))
            continue
        long_market_value += qty * price
    if skipped:
        print(f"    [warn] NLV excludes {len(skipped)} position(s) with no usable price: {', '.join(skipped)}",
              file=sys.stderr)

    # Signed option market value — short options carry NEGATIVE marketValue
    # in the E*TRADE payload (the broker screen's negative "Value $" rows).
    # Dropping these was the 2026-08-04 NLV bug: $1,149,562 rendered vs the
    # broker's own $1,082,940.74 (Δ ≈ sum of |short option marks|).
    option_market_value = 0.0
    for pos in refreshed_positions:
        if pos.get("assetType") == "OPTION":
            option_market_value += _safe(pos.get("marketValue"))

    balance = _compose_balance(
        base_balance, long_market_value, option_market_value,
        positions=refreshed_positions, equity_exclusions=skipped,
    )
    balance["asOf"] = datetime.utcnow().isoformat() + "Z"
    _rec = balance.get("nlv_reconciliation") or {}
    if _rec.get("warning"):
        print(
            f"    [warn] NLV reconciliation: computed ${_rec['computed_nlv']:,.0f} "
            f"vs broker ${_rec['broker_nlv']:,.0f} — Δ ${_rec['delta']:+,.0f} "
            f"({_rec['pct']:.1f}%); using the broker figure",
            file=sys.stderr,
        )
        for _note in _rec.get("itemized") or []:
            print(f"    [warn]   ↳ {_note}", file=sys.stderr)

    # Live option chains: held expirations + up to 3 future expirations per
    # underlying so the wheel-roll-advisor can enumerate roll candidates with
    # real chain data. Plus: extra_chain_underlyings (typically Parkev BUY+
    # tickers + scout candidates) get default 30/75/120 DTE expirations so
    # un-held candidate tickers (e.g., UBER, AAOI) also have live CSP tickets.
    # Without this, TOP CONVICTION candidates show "E*TRADE chain unavailable"
    # — the user can't make an override decision without complete data
    # (CLAUDE.md rules #19 fail-closed + #24 never hide opportunities).
    held_underlyings = sorted({
        p.get("underlying") for p in refreshed_positions
        if p.get("assetType") == "OPTION" and p.get("underlying")
    })
    extra_set = {u.upper() for u in (extra_chain_underlyings or []) if u}
    held_set = set(held_underlyings)
    new_candidates = sorted(extra_set - held_set)
    all_underlyings = sorted(held_set | extra_set)

    # E*TRADE-backed expiration listing when routing (falls back to yfinance
    # per-symbol inside _build_chain_pairs). None → pure yfinance listing.
    _exp_lister = None
    if _routing is not None:
        try:
            _exp_lister = _routing.make_expiration_lister()
        except Exception:
            _exp_lister = None

    chain_pairs = _build_chain_pairs(refreshed_positions, all_underlyings,
                                     list_expirations_fn=_exp_lister)
    if new_candidates:
        print(
            f"  Chain coverage: {len(held_underlyings)} held + "
            f"{len(new_candidates)} candidate underlyings "
            f"(new: {', '.join(new_candidates[:8])}"
            f"{', ...' if len(new_candidates) > 8 else ''})"
        )
    _chain_report: dict = {}
    if _routing is not None:
        # Task #36: priority-tiered E*TRADE fetch (held + near-expiry first),
        # per-run budget cap, ≤4 req/s throttle, labeled yfinance fallback.
        _max_chains = int(chains_cfg.get(
            "etrade_max_chains", _routing.DEFAULT_MAX_CHAINS))
        _spots: dict = {}
        for _s, _q in quotes.items():
            if isinstance(_q, dict) and _q.get("last"):
                _spots[_s] = _q["last"]
        for _s, _t in technicals.items():
            if isinstance(_t, dict) and _t.get("spot"):
                _spots.setdefault(_s, _t["spot"])
        _plan = _routing.build_chain_plan(
            chain_pairs, refreshed_positions, max_chains=_max_chains)
        print(f"  Fetching {len(chain_pairs)} option chains via E*TRADE "
              f"(budget {_max_chains}, throttle "
              f"{_routing.ETRADE_RATE_PER_SEC:.0f} req/s, "
              f"yfinance fallback labeled)...")
        chains = _routing.fetch_chains_routed(
            _plan, _spots, yf_fetch_many=_parallel_chain_fetch,
            report=_chain_report)
        print("  " + _routing.split_line(
            "chains", _routing.chain_source_counts(chains)))
    else:
        print(
            f"  Fetching {len(chain_pairs)} option chains in parallel "
            f"(held + future + candidates, workers={_PARALLEL_FETCH_WORKERS})..."
        )
        chains = _parallel_chain_fetch(chain_pairs)

    # Task #43 — TRUE chain-implied vol (ATM IV / 25Δ skew / term slope) from
    # the chains just fetched, plus the true IV rank vs the persisted rolling
    # history (state/chain_iv_history.json). The legacy 252d realized-vol
    # percentile stays available as a LABELED companion (RVrank) — it is
    # backward-looking and has claimed "IV rank 100 = fat premium" on names
    # whose implied premium had crushed (AMZN post-gap 4% ann). Fail-open:
    # any error → empty map, briefing falls back to the labeled RV proxy.
    chain_iv_map: dict = {}
    try:
        from analysis import chain_iv as _civ
        _civ_cfg = _civ.load_chain_iv_config(config)
        if _civ_cfg["enabled"] and chains:
            _hist_path = _civ.resolve_history_path(config, snapshot_dir)
            # One-time seed from stored snapshot chains (idempotent — dates
            # already seeded are skipped, so steady-state cost is ~0).
            _seeded = _civ.backfill_history(
                snapshot_dir.parent, _hist_path, config=config)
            if _seeded:
                print(f"  Chain-IV history: backfilled {_seeded} day(s) "
                      f"from stored snapshot chains")
            _civ_spots: dict = {}
            for _s, _t in technicals.items():
                if isinstance(_t, dict) and _t.get("spot"):
                    _civ_spots.setdefault(str(_s).upper(), _t["spot"])
            for _s, _q in quotes.items():
                if isinstance(_q, dict) and _q.get("last"):
                    _civ_spots.setdefault(str(_s).upper(), _q["last"])
            from datetime import date as _civ_date
            try:
                _civ_as_of = _civ_date.fromisoformat(snapshot_dir.name)
            except ValueError:
                _civ_as_of = _civ_date.today()
            chain_iv_map = _civ.compute_metrics_for_snapshot(
                chains, _civ_spots, as_of=_civ_as_of)
            _hist = _civ.load_history(_hist_path)
            _civ.update_history(_hist, _civ_as_of.isoformat(), chain_iv_map)
            if _civ_as_of.isoformat() not in (_hist.get("dates_seeded") or []):
                _hist.setdefault("dates_seeded", []).append(
                    _civ_as_of.isoformat())
                _hist["dates_seeded"] = sorted(_hist["dates_seeded"])
            _civ.save_history(_hist_path, _hist)
            _civ.attach_ranks(chain_iv_map, _hist, config)
            _ranked = sum(1 for m in chain_iv_map.values()
                          if m.get("iv_rank") is not None)
            print(f"  Chain-IV: true implied vol for {len(chain_iv_map)} "
                  f"underlyings ({_ranked} with enough history for a true "
                  f"IVrank)")
    except Exception as e:
        print(f"    [warn] chain-IV computation failed (falling back to the "
              f"labeled realized-vol proxy): {e}", file=sys.stderr)
        chain_iv_map = {}

    # Persist
    with open(snapshot_dir / "accounts.json", "w") as f:
        json.dump(accounts, f, indent=2, default=json_default)
    with open(snapshot_dir / "positions.json", "w") as f:
        json.dump(refreshed_positions, f, indent=2, default=json_default)
    with open(snapshot_dir / "balance.json", "w") as f:
        json.dump(balance, f, indent=2, default=json_default)
    with open(snapshot_dir / "quotes.json", "w") as f:
        json.dump(quotes, f, indent=2, default=json_default)
    with open(snapshot_dir / "iv_ranks.json", "w") as f:
        json.dump(iv_ranks, f, indent=2, default=json_default)
    with open(snapshot_dir / "technicals.json", "w") as f:
        json.dump(technicals, f, indent=2, default=json_default)
    with open(snapshot_dir / "open_orders.json", "w") as f:
        json.dump(open_orders, f, indent=2, default=json_default)
    with open(snapshot_dir / "theses.json", "w") as f:
        json.dump(theses, f, indent=2, default=json_default)

    chains_dir = snapshot_dir / "chains"
    chains_dir.mkdir(parents=True, exist_ok=True)
    for key, value in chains.items():
        with open(chains_dir / f"{key}.json", "w") as f:
            json.dump(value, f, indent=2, default=json_default)

    if chain_iv_map:
        with open(snapshot_dir / "chain_iv.json", "w") as f:
            json.dump(chain_iv_map, f, indent=2, default=json_default)

    # Persist earnings calendar and YTD P&L
    with open(snapshot_dir / "earnings.json", "w") as f:
        json.dump(earnings_calendar, f, indent=2, default=json_default)
    with open(snapshot_dir / "ytd_pnl.json", "w") as f:
        json.dump(ytd_pnl, f, indent=2, default=json_default)

    print(f"  Snapshot complete: {len(refreshed_positions)} positions, {len(chains)} option chains, NLV ${balance['accountValue']:,.0f}")

    # Provenance metadata: when each data source was fetched and from where.
    # The live-data-policer reads this to enforce "no cached data" policy.
    from datetime import datetime as _dt
    now_iso = _dt.now().isoformat()
    # When etrade_live=True, force the provenance source to "etrade_live" — the
    # adapter sometimes returns just "etrade" but the live-data-policer's
    # allow-list expects the explicit "etrade_live" tag.
    positions_source = "etrade_live" if etrade_live else source
    # Task #36 — provenance labels come from MEASURED per-item sources, never
    # from the run-mode flag. The old code stamped chains "etrade_live" on any
    # live run even though every chain was fetched via yfinance (the line-813
    # mislabel). source_split_label reports the honest split, and the counts
    # ride along so the policer / panel can render "112 etrade · 38
    # yfinance-fallback".
    try:
        from analysis import chain_routing as _prov_routing
        _chain_counts = _prov_routing.chain_source_counts(chains)
        _quote_counts = _prov_routing.quote_source_counts(quotes)
        _chains_label = _prov_routing.source_split_label(
            _chain_counts, routing_attempted=etrade_routing)
        _quotes_label = _prov_routing.source_split_label(
            _quote_counts, routing_attempted=etrade_routing)
    except Exception:
        _chain_counts = {}
        _quote_counts = {}
        _chains_label = "yfinance"
        _quotes_label = "yfinance"

    # 2026-08-06 — silent wholesale fallback is a bug class, not a mode.
    # When routing was ATTEMPTED but zero items came from E*TRADE, print a
    # loud one-line reason to stderr AND record it in provenance so the
    # live-data policer renders "⚠ E*TRADE routing fell back wholesale" in
    # the briefing. The reason comes from the router's report dict; a
    # missing reason still gets a generic label — never silence.
    _chain_wholesale = None
    _quote_wholesale = None
    if etrade_routing:
        if not _chain_counts.get("etrade", 0):
            _chain_wholesale = (_chain_report.get("fallback_reason")
                                or "all E*TRADE chain fetches failed or "
                                   "router unavailable (unlabeled)")
            print(f"    [warn] ⚠ E*TRADE routing fell back wholesale "
                  f"(chains): {_chain_wholesale}", file=sys.stderr)
        if not _quote_counts.get("etrade", 0):
            _quote_wholesale = (_quote_report.get("fallback_reason")
                                or "all E*TRADE quote batches failed or "
                                   "router unavailable (unlabeled)")
            print(f"    [warn] ⚠ E*TRADE routing fell back wholesale "
                  f"(quotes): {_quote_wholesale}", file=sys.stderr)

    data_provenance = {
        "positions": {
            "source": positions_source,
            "fetched_at": now_iso,
            "fresh": etrade_live,
        },
        # requested/fetched: quote-fetch coverage (rule #46 — the 2026-08-04
        # PLTR bug shipped on a 22/36 cycle; the live-data policer surfaces
        # coverage < 90% so silent quote gaps are visible in the briefing).
        # etrade/yfinance: per-item source split (task #36).
        "quotes": {"source": _quotes_label, "fetched_at": now_iso, "fresh": True,
                   "requested": len(quote_symbols), "fetched": len(quotes),
                   "etrade": _quote_counts.get("etrade", 0),
                   "yfinance": _quote_counts.get("yfinance", 0),
                   "routing_attempted": etrade_routing,
                   **({"wholesale_fallback_reason": _quote_wholesale}
                      if _quote_wholesale else {})},
        "chains": {"source": _chains_label,
                   "fetched_at": now_iso, "fresh": True,
                   "etrade": _chain_counts.get("etrade", 0),
                   "yfinance": _chain_counts.get("yfinance", 0),
                   "routing_attempted": etrade_routing,
                   **({"wholesale_fallback_reason": _chain_wholesale}
                      if _chain_wholesale else {})},
        "iv_ranks": {"source": "yfinance_252d", "fetched_at": now_iso, "fresh": True},
        # Task #43: true implied vol measured from this cycle's chains
        # (ATM 30d IV, 25Δ skew, term slope + rank vs rolling history).
        "chain_iv": {"source": "chains_atm_iv", "fetched_at": now_iso,
                     "fresh": bool(chain_iv_map),
                     "tickers": len(chain_iv_map)},
        "technicals": {"source": "yfinance_730d", "fetched_at": now_iso, "fresh": True},
        "earnings_calendar": {"source": "yfinance+fmp_fallback",
                              "fetched_at": now_iso, "fresh": True},
        "broker_positions": {
            "source": "etrade_live" if etrade_live else (
                "manual_override" if broker_positions else "missing"),
            "fetched_at": now_iso if broker_positions else None,
            "fresh": etrade_live,
        },
    }

    return {
        "accounts": accounts,
        "positions": refreshed_positions,
        "broker_positions": broker_positions,
        "balance": balance,
        "quotes": quotes,
        "iv_ranks": iv_ranks,
        "technicals": technicals,
        "chains": chains,
        "chain_iv": chain_iv_map,
        "open_orders": open_orders,
        "theses": theses,
        "earnings_calendar": earnings_calendar,
        "ytd_pnl": ytd_pnl,
        "data_provenance": data_provenance,
        "snapshot_timestamp": now_iso,
    }
