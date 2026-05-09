"""
Step 6: Generate new ideas with concrete actionable contracts.

For each BUY recommendation from the third-party list (that we don't already hold),
pick a concrete cash-secured put entry from the live option chain via E*TRADE:
- Target delta ~0.25-0.30 (30 delta is the wheel sweet spot)
- Next monthly expiration (3rd Friday) 25-50 DTE
- Compute premium, collateral, yield, annualized yield
- Filter by liquidity (open interest >= 50, bid-ask spread <= 40%)

Uses pyetrade for fast chain fetching (1-2 seconds vs 10-15s via yfinance).
E*TRADE chains return real delta, so we pick directly by delta when available.
"""

import json
import math
import sys
import time as _time
from datetime import date, datetime, timedelta
from pathlib import Path

from adapters.etrade_market import get_option_chain, get_option_expirations

# How many top recs to fetch chains for (one chain fetch per ticker is slow)
MAX_CONCRETE_IDEAS = 3
PER_TICKER_TIMEOUT_S = 8
TOTAL_BUDGET_S = 30  # don't spend more than this in step 6 total
# Cash-secured put target delta range (negative for puts; we'll match by abs value)
TARGET_PUT_DELTA = 0.30
DELTA_TOLERANCE = 0.10
# When delta data isn't available (yfinance.option_chain rarely returns it),
# fall back to a moneyness target: ~5-7% OTM for normal IV, scaled higher for high-IV
TARGET_OTM_PCT_NORMAL = 0.05
TARGET_OTM_PCT_HIGH_IV = 0.10
HIGH_IV_THRESHOLD = 0.40  # 40% implied vol
# Minimum liquidity floor — relaxed for after-hours / pre-market when bid often = 0
MIN_OPEN_INTEREST = 50
MAX_SPREAD_PCT = 0.40  # generous; after-hours can have wide spreads on otherwise-liquid options
# Target DTE range
MIN_DTE = 25
MAX_DTE = 50


def _next_monthly_expiration(available: list[date]) -> date | None:
    """Pick the closest expiration in MIN_DTE..MAX_DTE. Returns date or None.

    Args:
        available: list of date objects (from pyetrade get_option_expirations)
    """
    if not available:
        return None
    today = date.today()
    candidates = []
    for d in available:
        if not isinstance(d, date):
            continue
        dte = (d - today).days
        if MIN_DTE <= dte <= MAX_DTE:
            candidates.append((d, dte))
    if not candidates:
        # Fall back to anything in 15..60
        for d in available:
            if not isinstance(d, date):
                continue
            dte = (d - today).days
            if 15 <= dte <= 60:
                candidates.append((d, dte))
    if not candidates:
        return None
    # Prefer one closest to 35 DTE (sweet spot)
    candidates.sort(key=lambda c: abs(c[1] - 35))
    return candidates[0][0]


def _pick_csp_strike(put_rows: list, spot: float, target_delta: float = TARGET_PUT_DELTA) -> dict | None:
    """Pick a cash-secured put strike with adequate liquidity.

    Args:
        put_rows: list of OptionChainRow objects from pyetrade (with real delta)
        spot: current stock price
        target_delta: absolute value of delta to target (e.g. 0.30)

    Strategy:
    1. Filter by liquidity (OI >= MIN_OPEN_INTEREST, spread <= MAX_SPREAD_PCT)
    2. E*TRADE returns real delta, so pick matching target_delta within tolerance
    3. Fallback: if no delta rows match, pick by OTM% (rare with E*TRADE)
    """
    if not put_rows or spot <= 0:
        return None

    rows = []
    for row in put_rows:
        strike_val = row.strike
        bid = row.bid
        ask = row.ask
        last = row.last
        oi = row.open_interest
        delta_val = row.delta
        iv = row.iv or 0.0

        # Liquidity checks
        if oi < MIN_OPEN_INTEREST:
            continue

        # Compute mid and spread
        if ask > 0 and bid > 0:
            mid = (bid + ask) / 2
            spread_pct = (ask - bid) / mid if mid > 0 else 1.0
        elif ask > 0:
            mid = (last + ask) / 2 if last > 0 else ask * 0.7
            spread_pct = MAX_SPREAD_PCT
        elif last > 0:
            mid = last
            spread_pct = 0
        else:
            continue

        if mid <= 0:
            continue
        if spread_pct > MAX_SPREAD_PCT:
            continue

        # Only consider OTM puts (strike below spot)
        if strike_val >= spot:
            continue

        otm_pct = (spot - strike_val) / spot
        abs_delta = abs(delta_val) if delta_val is not None else None

        rows.append({
            "strike": strike_val,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "openInterest": oi,
            "spread_pct": spread_pct,
            "iv": iv,
            "abs_delta": abs_delta,
            "otm_pct": otm_pct,
        })

    if not rows:
        return None

    # Prefer rows with real delta (E*TRADE always provides it)
    rows_with_delta = [r for r in rows if r["abs_delta"] is not None]
    if rows_with_delta:
        rows_with_delta.sort(key=lambda r: abs(r["abs_delta"] - target_delta))
        return rows_with_delta[0]

    # Fallback: pick by OTM% (shouldn't reach this with E*TRADE)
    avg_iv = sum(r["iv"] for r in rows) / len(rows) if rows else 0.30
    target_otm = TARGET_OTM_PCT_HIGH_IV if avg_iv > HIGH_IV_THRESHOLD else TARGET_OTM_PCT_NORMAL
    rows.sort(key=lambda r: abs(r["otm_pct"] - target_otm))
    return rows[0]


def _fetch_chain_with_timeout(target_underlying: str, timeout_s: float) -> tuple | None:
    """Fetch (expirations, exp_date, put_rows) via pyetrade with timeout.

    Returns:
        (expirations_list, selected_exp_date, put_rows) on success
        ("ERROR", error_message, None) on error
        None on timeout
    """
    # Fetch expirations
    expirations = get_option_expirations(target_underlying, timeout_s=min(3.0, timeout_s))
    if expirations is None or not expirations:
        return ("ERROR", "no_expirations", None)

    exp_date = _next_monthly_expiration(expirations)
    if exp_date is None:
        return ("ERROR", "no_acceptable_expiration", None)

    # Fetch chain for that expiration
    chain_data = get_option_chain(
        target_underlying,
        expiry_date=exp_date,
        strike_near=0,  # pyetrade will auto-center
        no_of_strikes=20,
        chain_type="CALLPUT",
        timeout_s=timeout_s,
    )
    if chain_data is None:
        return ("ERROR", "chain_fetch_failed_or_timeout", None)

    put_rows = chain_data.get("put", [])
    if not put_rows:
        return ("ERROR", "no_puts_in_chain", None)

    return (expirations, exp_date, put_rows)


def _build_concrete_idea(rec: dict, spot: float, target_underlying: str) -> dict | None:
    """Fetch the chain for the recommendation's ticker and pick a concrete CSP.

    Returns:
        Complete idea dict with strike/expiration/premium/yield on success
        None if no tradable contract found
    """
    fetched = _fetch_chain_with_timeout(target_underlying, PER_TICKER_TIMEOUT_S)
    if fetched is None:
        return None  # timeout; skip this candidate
    if isinstance(fetched, tuple) and len(fetched) == 3 and fetched[0] == "ERROR":
        return None  # error; skip this candidate

    expirations, exp_date, put_rows = fetched
    pick = _pick_csp_strike(put_rows, spot=spot)
    if not pick:
        return None  # no strike matched filters

    strike = pick["strike"]
    mid = pick["mid"]
    bid = pick["bid"]
    abs_delta = pick["abs_delta"]
    contracts = 1  # default — sizer can scale
    collateral = strike * 100 * contracts
    premium = mid * 100 * contracts

    today = date.today()
    dte = (exp_date - today).days

    # Yield = premium / collateral; annualized = scaled to 365
    period_yield = premium / collateral if collateral else 0
    annualized = period_yield * (365 / dte) if dte else 0
    otm_pct = (spot - strike) / spot if spot else 0

    # Format expiration nicely
    exp_pretty = exp_date.strftime("%a %b %d '%y").replace(" 0", " ")
    exp_iso = exp_date.isoformat()

    delta_str = f"~{abs(abs_delta):.2f}" if abs_delta is not None else "N/A"

    return {
        "ticker": target_underlying,
        "name": rec.get("name", ""),
        "source": "recommendation_list_csp",
        "score": rec.get("rating_tier", 0),

        # The actionable contract
        "instruction": "SELL_TO_OPEN",
        "type": "PUT",
        "strike": round(strike, 2),
        "expiration": exp_iso,
        "expiration_pretty": exp_pretty,
        "dte": dte,
        "contracts": contracts,
        "bid": round(bid, 2),
        "mid": round(mid, 2),
        "collateral": round(collateral, 2),
        "premium": round(premium, 2),
        "yield_pct": round(period_yield * 100, 2),
        "annualized_pct": round(annualized * 100, 1),
        "otm_pct": round(otm_pct * 100, 1),
        "delta": abs_delta,
        "open_interest": pick["openInterest"],
        "spread_pct": round(pick["spread_pct"] * 100, 1),
        "iv": round(pick["iv"] * 100, 1) if pick["iv"] else None,
        "spot": round(spot, 2),

        # Original recommendation context
        "raw_recommendation": rec.get("raw_recommendation", ""),
        "rec_age_days": rec.get("age_days", 0),
        "price_target_2026": rec.get("price_target_2026"),
        "rationale": (
            f"{rec.get('raw_recommendation', '')}, sell ${strike:.0f}P "
            f"({otm_pct*100:.1f}% OTM, delta {delta_str}) for ${mid:.2f} → "
            f"{period_yield*100:.2f}% yield over {dte}d = {annualized*100:.1f}% annualized"
        ),
    }


def generate_new_ideas(
    snapshot_data: dict,
    regime_data: dict,
    directives_active: list,
    config: dict,
    snapshot_dir: Path,
    recommendations_list: list = None,
) -> list:
    """Generate concrete actionable ideas from the recommendation list + live chains."""
    ideas: list = []
    regime = (regime_data or {}).get("regime", "NORMAL")
    suppress_longs = regime in ("RISK_OFF", "CAUTION")

    suppressed = set()
    for d in (directives_active or []):
        if d.get("type") == "SUPPRESS":
            sym = (d.get("target", {}) or {}).get("symbol")
            if sym:
                suppressed.add(sym.upper())

    held = set()
    for pos in (snapshot_data.get("positions") or []):
        sym = pos.get("symbol", "").upper()
        if sym:
            held.add(sym.split()[0] if " " in sym else sym)
        # Also block if we already have an option on this underlying
        if pos.get("assetType") == "OPTION":
            u = (pos.get("underlying") or "").upper()
            if u:
                held.add(u)

    # Rank recommendations by tier desc, freshness asc
    ranked = sorted(
        recommendations_list or [],
        key=lambda r: (-int(r.get("rating_tier", 1)), int(r.get("age_days", 99))),
    )

    # Fetch chains via E*TRADE pyetrade (1-2s per ticker, total budget 30s)
    started = _time.monotonic()
    candidate_count = 0
    quotes = snapshot_data.get("quotes", {})

    for rec in ranked:
        if candidate_count >= MAX_CONCRETE_IDEAS:
            break
        if _time.monotonic() - started > TOTAL_BUDGET_S:
            print(f"  [new_ideas] hit {TOTAL_BUDGET_S}s budget; stopping")
            break

        ticker = (rec.get("ticker") or "").upper()
        if not ticker or ticker in held or ticker in suppressed:
            continue
        if rec.get("recommendation") not in ("BUY", "STRONG_BUY", "WEAK_BUY"):
            continue
        if suppress_longs:
            continue

        # Need spot price to compute OTM%
        spot_quote = quotes.get(ticker)
        spot = None
        if spot_quote:
            spot = spot_quote.get("last")
        if spot is None or spot <= 0:
            continue

        # Fetch chain and build concrete idea
        ticker_start = _time.monotonic()
        idea = _build_concrete_idea(rec, spot, ticker)
        ticker_elapsed = _time.monotonic() - ticker_start

        if idea is None:
            # No tradable contract found — surface as watch-only idea so the user knows
            ideas.append({
                "ticker": ticker,
                "name": rec.get("name", ""),
                "source": "recommendation_list_watch_only",
                "instruction": None,
                "rationale": f"{rec.get('raw_recommendation', '')} — no acceptable CSP found in chain",
                "raw_recommendation": rec.get("raw_recommendation", ""),
                "rec_age_days": rec.get("age_days", 0),
                "score": rec.get("rating_tier", 0),
            })
        else:
            ideas.append(idea)

        candidate_count += 1

    # Persist
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with open(snapshot_dir / "new_ideas.json", "w") as f:
        json.dump(ideas, f, indent=2, default=str)

    rec_count = len(recommendations_list) if recommendations_list else 0
    actionable = sum(1 for i in ideas if i.get("instruction"))
    if suppress_longs:
        print(f"  Generated {len(ideas)} new ideas ({actionable} actionable; regime={regime} suppresses longs; from {rec_count} recs)")
    else:
        print(f"  Generated {len(ideas)} new ideas ({actionable} actionable CSPs) from {rec_count} recommendations")
    return ideas
