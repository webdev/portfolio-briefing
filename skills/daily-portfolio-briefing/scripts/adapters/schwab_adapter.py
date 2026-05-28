"""Charles Schwab adapter — read-only portfolio snapshot via the Trader API.

Emits the exact EtradeSnapshot shape (reused from etrade_adapter) so the rest
of the pipeline is unchanged. Greeks/IV/bid/ask are NOT available on the
accounts endpoint — those fields are None and the pipeline refetches live
quotes through the chain fetcher (Schwab backend). Fail-closed: missing creds
or tokens raise; no fabricated values.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import schwab_auth  # type: ignore  # noqa: E402
from adapters.etrade_adapter import EtradeSnapshot  # type: ignore  # noqa: E402

_ACCOUNTS_URL = "https://api.schwabapi.com/trader/v1/accounts"
_ACCOUNT_NUMBERS_URL = "https://api.schwabapi.com/trader/v1/accounts/accountNumbers"


def _parse_osi(osi: str) -> tuple[str, str, float, str]:
    """Parse an OSI option symbol 'ROOT  YYMMDD[C/P]XXXXXXXX' into parts.

    Returns (underlying, "CALL"|"PUT", strike, "YYYY-MM-DD").
    """
    root = osi[:6].strip()
    tail = osi[6:]
    yy, mm, dd = tail[0:2], tail[2:4], tail[4:6]
    cp = "CALL" if tail[6] == "C" else "PUT"
    strike = int(tail[7:]) / 1000.0
    exp = f"20{yy}-{mm}-{dd}"
    return root, cp, strike, exp


def _normalize_position(p: Dict[str, Any], acct_desc: str) -> Dict[str, Any]:
    inst = p.get("instrument", {})
    long_q = float(p.get("longQuantity", 0) or 0)
    short_q = float(p.get("shortQuantity", 0) or 0)
    qty = long_q - short_q  # short positions are negative
    market_value = float(p.get("marketValue", 0) or 0)
    avg = float(p.get("averagePrice", 0) or 0)
    gain = float(p.get("longOpenProfitLoss", 0) or 0)

    if inst.get("assetType") == "OPTION":
        underlying, cp, strike, exp = _parse_osi(inst.get("symbol", ""))
        per_share = abs(market_value / qty) / 100 if qty else 0.0
        return {
            "symbol": f"{underlying}_{cp}_{int(strike)}_{exp}".replace("-", ""),
            "assetType": "OPTION",
            "underlying": underlying,
            "type": cp,
            "strike": strike,
            "expiration": exp,
            "qty": qty,
            "marketValue": market_value,
            "premiumReceived": round(avg, 4),
            "costPerShare": round(avg, 4),
            "currentMid": round(per_share, 4),
            "bid": None,
            "ask": None,
            "totalGain": gain,
            "totalGainPct": 0.0,
            "delta": None, "gamma": None, "theta": None, "vega": None, "rho": None,
            "ivPct": None,
            "openInterest": None,
            "symbolDescription": inst.get("description"),
            "accountDesc": acct_desc,
            "positionType": "SHORT" if qty < 0 else "LONG",
        }

    price = market_value / qty if qty else 0.0
    return {
        "symbol": inst.get("symbol"),
        "assetType": "EQUITY",
        "qty": qty,
        "price": round(price, 2),
        "marketValue": market_value,
        "costBasis": avg,
        "totalGain": gain,
        "totalGainPct": 0.0,
        "accountDesc": acct_desc,
    }


def _build_snapshot(
    raw_accounts: List[Dict[str, Any]],
    account_labels: Optional[Dict[str, str]] = None,
    account_desc_whitelist: Optional[List[str]] = None,
) -> EtradeSnapshot:
    account_labels = account_labels or {}
    warnings: List[str] = []
    fetched_at = datetime.utcnow().isoformat() + "Z"
    whitelist = ({d.strip().upper() for d in account_desc_whitelist}
                 if account_desc_whitelist else None)

    flat_accounts: List[Dict[str, Any]] = []
    all_positions: List[Dict[str, Any]] = []
    agg = {"totalAccountValue": 0.0, "cash": 0.0, "longMarketValue": 0.0}

    for entry in raw_accounts:
        sa = entry.get("securitiesAccount", {})
        acct_num = str(sa.get("accountNumber", ""))
        masked = ("…" + acct_num[-4:]) if len(acct_num) >= 4 else acct_num
        acct_desc = account_labels.get(acct_num, masked)
        if whitelist is not None and acct_desc.strip().upper() not in whitelist:
            continue

        bal = sa.get("currentBalances", {}) or {}
        nlv = float(bal.get("liquidationValue", 0) or 0)
        cash = float(bal.get("cashBalance", 0) or 0)
        lmv = float(bal.get("longMarketValue", 0) or 0)
        agg["totalAccountValue"] += nlv
        agg["cash"] += cash
        agg["longMarketValue"] += lmv

        flat_accounts.append({
            "accountIdKey": acct_num,
            "accountId": acct_num,
            "accountType": sa.get("type"),
            "accountDesc": acct_desc,
            "institutionType": "BROKERAGE",
            "accountStatus": "ACTIVE",
            "nlv": nlv,
            "cash": cash,
        })

        for pos in sa.get("positions", []) or []:
            try:
                all_positions.append(_normalize_position(pos, acct_desc))
            except Exception as e:
                warnings.append(f"position normalize failed in {acct_desc}: {str(e)[:80]}")

    return EtradeSnapshot(
        accounts=flat_accounts,
        positions=all_positions,
        balance={
            "totalAccountValue": round(agg["totalAccountValue"], 2),
            "cash": round(agg["cash"], 2),
            "longMarketValue": round(agg["longMarketValue"], 2),
            "accountValue": round(agg["totalAccountValue"], 2),
        },
        open_orders=[],
        source="schwab",
        fetched_at=fetched_at,
        warnings=warnings,
    )


def _get(url: str, token: str, params: dict | None = None, session=None) -> Any:
    sess = session or requests
    r = sess.get(url, headers={"Authorization": f"Bearer {token}"},
                 params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_schwab_snapshot(
    accounts_filter: Optional[List[str]] = None,
    account_desc_whitelist: Optional[List[str]] = None,
    account_labels: Optional[Dict[str, str]] = None,
    session=None,
) -> EtradeSnapshot:
    """Pull a real read-only portfolio snapshot from Schwab.

    account_labels maps Schwab account numbers to friendly descs so the
    existing account_desc_whitelist scoping works (Schwab exposes only
    CASH/MARGIN as the account 'type', not a nickname).
    """
    token = schwab_auth.get_access_token(session=session)
    if not token:
        raise RuntimeError(
            "Schwab tokens missing or expired. Run the interactive auth flow: "
            "`python3 scripts/schwab_auth.py` (or send a code over Telegram)."
        )
    raw_accounts = _get(f"{_ACCOUNTS_URL}?fields=positions", token, session=session)
    if isinstance(raw_accounts, dict):
        raw_accounts = [raw_accounts]
    if accounts_filter:
        raw_accounts = [
            a for a in raw_accounts
            if str(a.get("securitiesAccount", {}).get("accountNumber")) in accounts_filter
        ]
    return _build_snapshot(raw_accounts, account_labels, account_desc_whitelist)
