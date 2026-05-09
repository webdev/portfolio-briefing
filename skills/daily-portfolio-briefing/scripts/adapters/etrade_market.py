"""E*TRADE market data adapter — pyetrade-based option chain fetching (fast).

OAuth tokens and consumer credentials come from the in-repo etrade_auth
module (no longer coupled to the wheelhouz repo). Fetches chains 10-20x
faster than yfinance (1-2 seconds vs 10-15 seconds).
"""

import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pyetrade

# Pull our self-contained auth helpers
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etrade_auth import load_tokens, _consumer_credentials  # type: ignore  # noqa: E402


@dataclass
class OptionChainRow:
    """Single option contract from E*TRADE chain."""
    strike: float
    option_type: str  # "PUT" or "CALL"
    bid: float
    ask: float
    last: float
    open_interest: int
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    iv: Optional[float] = None


def _load_credentials() -> tuple[Optional[str], Optional[str], Optional[Dict]]:
    """Load consumer key/secret + OAuth tokens via the in-repo auth module."""
    try:
        ck, cs = _consumer_credentials()
    except Exception:
        return None, None, None
    tokens = load_tokens()
    if not tokens:
        return ck, cs, None
    return ck, cs, tokens


def _build_market_client() -> Optional[pyetrade.ETradeMarket]:
    """Build an authenticated ETradeMarket client. Returns None on failure."""
    ck, cs, tokens = _load_credentials()
    if not (ck and cs and tokens):
        return None
    try:
        return pyetrade.ETradeMarket(
            client_key=ck,
            client_secret=cs,
            resource_owner_key=tokens["oauth_token"],
            resource_owner_secret=tokens["oauth_secret"],
            dev=tokens.get("sandbox", False),
        )
    except Exception:
        return None


def get_option_expirations(symbol: str, timeout_s: float = 3.0) -> Optional[List[date]]:
    """Get available option expirations for a symbol.

    Args:
        symbol: e.g. "AAPL"
        timeout_s: hard wall-clock timeout

    Returns:
        List of date objects, or None on error/timeout.
    """
    client = _build_market_client()
    if client is None:
        return None

    result = {"data": None, "error": None}

    def _do_fetch():
        try:
            start = time.monotonic()
            exp_resp = client.get_option_expire_date(symbol, resp_format="json")
            elapsed = time.monotonic() - start
            if elapsed > timeout_s:
                result["error"] = f"timeout after {elapsed:.2f}s"
                return

            if not exp_resp or "OptionExpireDateResponse" not in exp_resp:
                result["error"] = "missing_response"
                return

            raw_exps = exp_resp["OptionExpireDateResponse"].get("ExpirationDate", [])
            if isinstance(raw_exps, dict):
                raw_exps = [raw_exps]

            exps = []
            for exp in raw_exps:
                try:
                    y = int(exp.get("year", 0))
                    m = int(exp.get("month", 0))
                    d = int(exp.get("day", 0))
                    if y > 0 and m > 0 and d > 0:
                        exps.append(date(y, m, d))
                except (ValueError, TypeError):
                    continue

            result["data"] = exps
        except Exception as e:
            result["error"] = str(e)[:80]

    # Run with timeout in a thread
    import threading
    thread = threading.Thread(target=_do_fetch, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)

    if thread.is_alive():
        return None  # timed out
    if result["error"]:
        return None
    return result["data"]


def find_put_strike_near(
    symbol: str,
    target_otm_pct: float = 12.0,
    target_dte_min: int = 25,
    target_dte_max: int = 45,
    spot: float = None,
) -> Optional[Dict[str, any]]:
    """Find a real put strike near the target OTM % in the specified DTE band.

    Args:
        symbol: e.g. "AAPL"
        target_otm_pct: target OTM percent (e.g. 12 for 12% below spot)
        target_dte_min: minimum DTE to consider
        target_dte_max: maximum DTE to consider
        spot: current spot price (required for OTM calculation)

    Returns:
        Dict with keys: strike, expiration, bid, ask, mid, delta
        Or None if no suitable contract found or no chain data available.
    """
    if not spot or spot <= 0:
        return None

    # Get available expirations
    expirations = get_option_expirations(symbol, timeout_s=3.0)
    if not expirations:
        return None

    # Find expirations in the DTE band
    today = date.today()
    candidates = []
    for exp in expirations:
        dte = (exp - today).days
        if target_dte_min <= dte <= target_dte_max:
            candidates.append((dte, exp))

    if not candidates:
        return None

    # Sort by proximity to target DTE (middle of band)
    target_dte = (target_dte_min + target_dte_max) / 2
    candidates.sort(key=lambda x: abs(x[0] - target_dte))
    chosen_dte, chosen_exp = candidates[0]

    # Fetch the chain centered around target strike
    target_strike = spot * (1 - target_otm_pct / 100)
    chain = get_option_chain(
        symbol, chosen_exp, strike_near=target_strike,
        no_of_strikes=20, chain_type="PUT", timeout_s=5.0
    )
    if not chain or "put" not in chain:
        return None

    # Find put strike closest to target
    puts = chain["put"]
    best_put = None
    best_distance = float("inf")

    for put in puts:
        distance = abs(put.strike - target_strike)
        if distance < best_distance:
            best_distance = distance
            best_put = put

    if not best_put:
        return None

    # Compute mid price
    bid = best_put.bid or 0
    ask = best_put.ask or 0
    mid = (bid + ask) / 2 if bid and ask else ask or bid or 0

    return {
        "strike": best_put.strike,
        "expiration": chosen_exp.isoformat(),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "delta": best_put.delta,
    }


def get_option_chain(
    symbol: str,
    expiry_date: date,
    strike_near: float,
    no_of_strikes: int = 20,
    chain_type: str = "CALLPUT",
    timeout_s: float = 5.0,
) -> Optional[Dict[str, List[OptionChainRow]]]:
    """Fetch a single-expiration chain via pyetrade.

    Args:
        symbol: e.g. "AAPL"
        expiry_date: exact expiration (e.g. date(2026, 6, 19))
        strike_near: center strike for fetching (e.g. 150.0)
        no_of_strikes: number of strikes to fetch per side (default 20)
        chain_type: "PUT", "CALL", or "CALLPUT" (default)
        timeout_s: hard wall-clock timeout

    Returns:
        Dict with "put" and "call" keys, each a list of OptionChainRow objects.
        Returns None on error/timeout.
    """
    client = _build_market_client()
    if client is None:
        return None

    result = {"data": None, "error": None}

    def _do_fetch():
        try:
            start = time.monotonic()
            # pyetrade signature per wheelhouz/src/data/broker.py:
            # get_option_chains(symbol, expiry_date, strike_price_near, no_of_strikes,
            #                   option_category, chain_type, price_type, resp_format)
            chain = client.get_option_chains(
                symbol,
                expiry_date=expiry_date,
                strike_price_near=int(strike_near),
                no_of_strikes=no_of_strikes,
                option_category="STANDARD",
                chain_type=chain_type,
                price_type="all",
                resp_format="json",
            )
            elapsed = time.monotonic() - start
            if elapsed > timeout_s:
                result["error"] = f"timeout after {elapsed:.2f}s"
                return

            if not chain or "OptionChainResponse" not in chain:
                result["error"] = "missing_response"
                return

            option_pairs = chain["OptionChainResponse"].get("OptionPair", [])
            if isinstance(option_pairs, dict):
                option_pairs = [option_pairs]

            puts = []
            calls = []

            for pair in option_pairs:
                for side_name, side_list in [("Put", puts), ("Call", calls)]:
                    opt = pair.get(side_name)
                    if not opt:
                        continue
                    greeks = opt.get("OptionGreeks", {}) or {}

                    strike = float(opt.get("strikePrice", 0))
                    bid = float(opt.get("bid", 0))
                    ask = float(opt.get("ask", 0))
                    last = float(opt.get("lastPrice", 0))
                    oi = int(opt.get("openInterest", 0))
                    delta = greeks.get("delta")
                    gamma = greeks.get("gamma")
                    theta = greeks.get("theta")
                    vega = greeks.get("vega")
                    iv = greeks.get("iv")

                    if delta is not None:
                        delta = float(delta)
                    if gamma is not None:
                        gamma = float(gamma)
                    if theta is not None:
                        theta = float(theta)
                    if vega is not None:
                        vega = float(vega)
                    if iv is not None:
                        iv = float(iv)

                    row = OptionChainRow(
                        strike=strike,
                        option_type=side_name.upper(),
                        bid=bid,
                        ask=ask,
                        last=last,
                        open_interest=oi,
                        delta=delta,
                        gamma=gamma,
                        theta=theta,
                        vega=vega,
                        iv=iv,
                    )
                    side_list.append(row)

            result["data"] = {"put": puts, "call": calls}
        except Exception as e:
            result["error"] = str(e)[:80]

    # Run with timeout in a thread
    import threading
    thread = threading.Thread(target=_do_fetch, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)

    if thread.is_alive():
        return None  # timed out
    if result["error"]:
        return None
    return result["data"]
