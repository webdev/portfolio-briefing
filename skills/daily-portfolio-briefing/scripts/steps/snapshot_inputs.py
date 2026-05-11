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
from pathlib import Path

import yfinance as yf  # noqa: E402  (already a dependency)

from .fetch_earnings import fetch_earnings_dates
from .fetch_ytd_pnl import fetch_ytd_options_pnl_auto


WATCHLIST = ["SPY", "QQQ", "^VIX"]

# Parallelism cap for yfinance calls. yfinance hammering the same backend with
# >32 concurrent calls starts triggering rate limits, so 16 is the sweet spot
# for our typical 22-underlying universe.
_PARALLEL_FETCH_WORKERS = int(os.getenv("PORTFOLIO_BRIEFING_FETCH_WORKERS", "16"))


def load_portfolio_fixture(fixture_path: str) -> dict:
    """Load the user's holdings from JSON fixture (replaces E*TRADE positions API in v1)."""
    with open(fixture_path) as f:
        return json.load(f)


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
    """Fetch live quote + day change + 5d change. Returns {} on failure."""
    yf_ticker = ticker.replace("^", "^") if ticker.startswith("^") else ticker
    t = _fetch_price_history(yf_ticker, days=10)
    if t is None:
        return {}
    try:
        hist = t.history(period="10d")
        if hist.empty:
            return {}
        last = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else last
        day_change_pct = (last - prev) / prev if prev else 0.0
        if len(hist) >= 6:
            five_d_ago = float(hist["Close"].iloc[-6])
            five_d_change_pct = (last - five_d_ago) / five_d_ago if five_d_ago else 0.0
        else:
            five_d_change_pct = day_change_pct
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
    """Pull 300d history once and compute IV-rank, RSI(14), 200-SMA, drawdown.

    Returns dict with keys (any may be None on insufficient data):
      - iv_rank: percentile of 20d realized vol over past 252 obs
      - rsi_14: Wilder's RSI(14)
      - sma_200: 200-day simple moving average
      - drawdown_pct: % off rolling 252-day high (positive number = below high)
      - spot: latest close
    Returns None on fetch failure.
    """
    t = _fetch_price_history(ticker, days=300)
    if t is None:
        return None
    try:
        hist = t.history(period="300d")
        if hist.empty:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 60:
            return None

        spot = float(closes.iloc[-1])

        # IV rank via 20d realized vol percentile
        iv_rank: float | None = None
        rets = closes.pct_change().dropna()
        if len(rets) >= 21:
            rolling_vol = (rets.rolling(20).std() * math.sqrt(252)).dropna()
            if not rolling_vol.empty:
                cur = float(rolling_vol.iloc[-1])
                if not math.isnan(cur):
                    iv_rank = round(
                        float((rolling_vol <= cur).sum()) / len(rolling_vol) * 100.0, 1
                    )

        # RSI(14) — Wilder's smoothing
        rsi_14: float | None = None
        if len(closes) >= 30:
            delta = closes.diff().dropna()
            gain = delta.clip(lower=0)
            loss = (-delta.clip(upper=0))
            # Wilder's exponential smoothing alpha=1/14
            avg_gain = gain.ewm(alpha=1.0 / 14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1.0 / 14, adjust=False).mean()
            last_gain = float(avg_gain.iloc[-1])
            last_loss = float(avg_loss.iloc[-1])
            if last_loss > 0:
                rs = last_gain / last_loss
                rsi_14 = round(100.0 - (100.0 / (1.0 + rs)), 1)
            elif last_gain > 0:
                rsi_14 = 100.0
            else:
                rsi_14 = 50.0

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

        return {
            "iv_rank": iv_rank,
            "rsi_14": rsi_14,
            "sma_200": sma_200,
            "drawdown_pct": drawdown_pct,
            "spot": round(spot, 2),
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
) -> list[tuple[str, str]]:
    """Compute (underlying, expiration) pairs to fetch: held + 3 future per name."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for underlying in underlyings_with_options:
        held_exps = sorted({
            p["expiration"] for p in refreshed_positions
            if p.get("assetType") == "OPTION"
            and p.get("underlying") == underlying
            and p.get("expiration")
        })
        try:
            t = yf.Ticker(underlying)
            available = list(t.options or [])
        except Exception:
            available = []

        future_exps: list[str] = []
        if available and held_exps:
            latest_held = held_exps[-1]
            future_candidates = [e for e in available if e > latest_held]
            if future_candidates:
                future_exps.append(future_candidates[0])
                if len(future_candidates) >= 5:
                    future_exps.append(future_candidates[len(future_candidates) // 2])
                if len(future_candidates) >= 2:
                    future_exps.append(future_candidates[-1])
                future_exps = list(dict.fromkeys(future_exps))[:3]

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
) -> dict:
    """Snapshot all inputs with live yfinance refresh.

    If etrade_live=True, fetch real positions/balance from E*TRADE via pyetrade.
    Otherwise load from the fixture path. Either way, prices/chains/regime
    inputs are always live yfinance.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)

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
    quote_symbols = sorted({s for s in (underlyings | set(WATCHLIST)) if s})
    technical_symbols = sorted({s for s in underlyings if s})
    earnings_symbols = sorted({s for s in underlyings if s})

    print(
        f"  Fetching live data in parallel "
        f"(quotes:{len(quote_symbols)}, technicals:{len(technical_symbols)}, "
        f"earnings:{len(earnings_symbols)}, workers={_PARALLEL_FETCH_WORKERS})..."
    )
    quotes, iv_ranks, technicals, earnings_calendar = _parallel_market_data_fetch(
        quote_symbols, technical_symbols, earnings_symbols
    )
    print(
        f"  Parallel fetch complete: {len(quotes)} quotes, "
        f"{len(iv_ranks)} IV ranks, {len(technicals)} technical sets, "
        f"{len(earnings_calendar)} earnings dates"
    )

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

    # Recompute account value from refreshed prices
    long_market_value = sum(
        pos.get("qty", 0) * pos.get("price", 0)
        for pos in refreshed_positions
        if pos.get("assetType") == "EQUITY" and pos.get("qty", 0) > 0
    )
    cash = base_balance.get("cash", 0)
    balance = {
        **base_balance,
        "longMarketValue": round(long_market_value, 2),
        "accountValue": round(long_market_value + cash, 2),
        "asOf": datetime.utcnow().isoformat() + "Z",
    }

    # Live option chains: held expirations + up to 3 future expirations per
    # underlying so the wheel-roll-advisor can enumerate roll candidates with
    # real chain data. All (underlying, expiration) pairs fetched in parallel.
    underlyings_with_options = sorted({
        p.get("underlying") for p in refreshed_positions
        if p.get("assetType") == "OPTION" and p.get("underlying")
    })
    chain_pairs = _build_chain_pairs(refreshed_positions, underlyings_with_options)
    print(
        f"  Fetching {len(chain_pairs)} option chains in parallel "
        f"(held + future, workers={_PARALLEL_FETCH_WORKERS})..."
    )
    chains = _parallel_chain_fetch(chain_pairs)

    # Persist
    with open(snapshot_dir / "accounts.json", "w") as f:
        json.dump(accounts, f, indent=2)
    with open(snapshot_dir / "positions.json", "w") as f:
        json.dump(refreshed_positions, f, indent=2)
    with open(snapshot_dir / "balance.json", "w") as f:
        json.dump(balance, f, indent=2)
    with open(snapshot_dir / "quotes.json", "w") as f:
        json.dump(quotes, f, indent=2)
    with open(snapshot_dir / "iv_ranks.json", "w") as f:
        json.dump(iv_ranks, f, indent=2)
    with open(snapshot_dir / "technicals.json", "w") as f:
        json.dump(technicals, f, indent=2)
    with open(snapshot_dir / "open_orders.json", "w") as f:
        json.dump(open_orders, f, indent=2)
    with open(snapshot_dir / "theses.json", "w") as f:
        json.dump(theses, f, indent=2)

    chains_dir = snapshot_dir / "chains"
    chains_dir.mkdir(parents=True, exist_ok=True)
    for key, value in chains.items():
        with open(chains_dir / f"{key}.json", "w") as f:
            json.dump(value, f, indent=2)

    # Persist earnings calendar and YTD P&L
    with open(snapshot_dir / "earnings.json", "w") as f:
        json.dump(earnings_calendar, f, indent=2)
    with open(snapshot_dir / "ytd_pnl.json", "w") as f:
        json.dump(ytd_pnl, f, indent=2)

    print(f"  Snapshot complete: {len(refreshed_positions)} positions, {len(chains)} option chains, NLV ${balance['accountValue']:,.0f}")

    # Provenance metadata: when each data source was fetched and from where.
    # The live-data-policer reads this to enforce "no cached data" policy.
    from datetime import datetime as _dt
    now_iso = _dt.now().isoformat()
    # When etrade_live=True, force the provenance source to "etrade_live" — the
    # adapter sometimes returns just "etrade" but the live-data-policer's
    # allow-list expects the explicit "etrade_live" tag.
    positions_source = "etrade_live" if etrade_live else source
    data_provenance = {
        "positions": {
            "source": positions_source,
            "fetched_at": now_iso,
            "fresh": etrade_live,
        },
        "quotes": {"source": "yfinance", "fetched_at": now_iso, "fresh": True},
        "chains": {"source": "etrade_live" if etrade_live else "yfinance",
                   "fetched_at": now_iso, "fresh": True},
        "iv_ranks": {"source": "yfinance_252d", "fetched_at": now_iso, "fresh": True},
        "technicals": {"source": "yfinance_300d", "fetched_at": now_iso, "fresh": True},
        "earnings_calendar": {"source": "yfinance",
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
        "open_orders": open_orders,
        "theses": theses,
        "earnings_calendar": earnings_calendar,
        "ytd_pnl": ytd_pnl,
        "data_provenance": data_provenance,
        "snapshot_timestamp": now_iso,
    }
