# skills/daily-portfolio-briefing/scripts/adapters/schwab_market.py
"""Charles Schwab market-data adapter — option chains via the Trader API.

Mirrors adapters/etrade_market.py's public surface so the canonical chain
fetcher can swap backends with no other changes. Fail-closed: any error or
missing data returns None; never substitute another source.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import schwab_auth  # type: ignore  # noqa: E402

_CHAINS_URL = "https://api.schwabapi.com/marketdata/v1/chains"


@dataclass
class OptionChainRow:
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


def _f(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        # Schwab uses sentinel -999.0 for missing greeks/iv.
        return None if f <= -998 else f
    except (TypeError, ValueError):
        return None


def _rows_from_exp_map(exp_map: dict, opt_type: str) -> List[OptionChainRow]:
    rows: List[OptionChainRow] = []
    for _exp_key, strikes in (exp_map or {}).items():
        for _strike_key, contracts in strikes.items():
            for c in contracts:
                rows.append(OptionChainRow(
                    strike=float(c.get("strikePrice", 0)),
                    option_type=opt_type,
                    bid=float(c.get("bid", 0) or 0),
                    ask=float(c.get("ask", 0) or 0),
                    last=float(c.get("last", 0) or 0),
                    open_interest=int(c.get("openInterest", 0) or 0),
                    delta=_f(c.get("delta")),
                    gamma=_f(c.get("gamma")),
                    theta=_f(c.get("theta")),
                    vega=_f(c.get("vega")),
                    iv=_f(c.get("volatility")),
                ))
    return rows


def _parse_chain_payload(payload: dict) -> Dict[str, List[OptionChainRow]]:
    return {
        "call": _rows_from_exp_map(payload.get("callExpDateMap", {}), "CALL"),
        "put": _rows_from_exp_map(payload.get("putExpDateMap", {}), "PUT"),
    }


def _parse_expirations(payload: dict) -> List[date]:
    exps: set[date] = set()
    for key_map in (payload.get("callExpDateMap", {}), payload.get("putExpDateMap", {})):
        for exp_key in key_map.keys():
            # exp_key looks like "2026-06-19:22" (date:daysToExp)
            iso = exp_key.split(":", 1)[0]
            try:
                y, m, d = (int(x) for x in iso.split("-"))
                exps.add(date(y, m, d))
            except ValueError:
                continue
    return sorted(exps)


def _get(symbol: str, params: dict, timeout_s: float, session=None) -> Optional[dict]:
    token = schwab_auth.get_access_token()
    if not token:
        return None
    sess = session or requests
    try:
        r = sess.get(
            _CHAINS_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"symbol": symbol, **params},
            timeout=timeout_s + 2,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def get_option_expirations(symbol: str, timeout_s: float = 3.0, session=None) -> Optional[List[date]]:
    payload = _get(symbol, {"contractType": "ALL", "strikeCount": 1}, timeout_s, session)
    if not payload:
        return None
    exps = _parse_expirations(payload)
    return exps or None


def get_option_chain(
    symbol: str,
    expiry_date: date,
    strike_near: float,
    no_of_strikes: int = 20,
    chain_type: str = "CALLPUT",
    timeout_s: float = 5.0,
    session=None,
) -> Optional[Dict[str, List[OptionChainRow]]]:
    contract_type = {"PUT": "PUT", "CALL": "CALL", "CALLPUT": "ALL"}.get(chain_type, "ALL")
    iso = expiry_date.isoformat()
    payload = _get(
        symbol,
        {
            "contractType": contract_type,
            "strikeCount": no_of_strikes,
            "fromDate": iso,
            "toDate": iso,
        },
        timeout_s,
        session,
    )
    if not payload:
        return None
    return _parse_chain_payload(payload)


def find_put_strike_near(symbol, target_otm_pct=12.0, target_dte_min=25,
                         target_dte_max=45, spot=None, session=None):
    """Parity shim for render/panels.py:172 (etrade_market.find_put_strike_near).

    Mirrors the E*TRADE adapter's behavior over Schwab chains. Returns
    {strike, expiration, bid, ask, mid, delta} or None.
    """
    if not spot or spot <= 0:
        return None
    exps = get_option_expirations(symbol, session=session)
    if not exps:
        return None
    today = date.today()
    band = [(abs((e - today).days - (target_dte_min + target_dte_max) / 2), e)
            for e in exps if target_dte_min <= (e - today).days <= target_dte_max]
    if not band:
        return None
    band.sort()
    chosen = band[0][1]
    target = spot * (1 - target_otm_pct / 100)
    chain = get_option_chain(symbol, chosen, strike_near=target,
                             no_of_strikes=20, chain_type="PUT", session=session)
    if not chain or not chain.get("put"):
        return None
    best = min(chain["put"], key=lambda r: abs(r.strike - target))
    bid, ask = best.bid or 0, best.ask or 0
    mid = (bid + ask) / 2 if bid and ask else ask or bid or 0
    return {"strike": best.strike, "expiration": chosen.isoformat(),
            "bid": bid, "ask": ask, "mid": mid, "delta": best.delta}
