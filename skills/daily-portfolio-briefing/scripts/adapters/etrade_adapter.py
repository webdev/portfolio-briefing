"""
E*TRADE adapter — real broker connection via pyetrade.

OAuth tokens and consumer credentials are managed by the in-repo
`etrade_auth` module (no longer coupled to the wheelhouz repo). Tokens
live at $PORTFOLIO_BRIEFING_TOKEN_FILE (default
~/.config/portfolio-briefing/etrade_tokens.json); consumer credentials
come from $PORTFOLIO_BRIEFING_REPO/.env or the environment.

If tokens are missing or expired, raise so the caller can decide between
running the interactive auth flow or aborting the briefing.
"""

import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pyetrade  # noqa: E402

# Pull our self-contained auth helpers
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etrade_auth import load_tokens, _consumer_credentials, token_file_path  # type: ignore  # noqa: E402


@dataclass
class EtradeSnapshot:
    """Result of pulling a portfolio snapshot from E*TRADE."""
    accounts: List[Dict[str, Any]]
    positions: List[Dict[str, Any]]
    balance: Dict[str, Any]
    open_orders: List[Dict[str, Any]]
    source: str  # "etrade" if real, "fixture" if fallback
    fetched_at: str
    warnings: List[str]


def _load_credentials() -> tuple[Optional[str], Optional[str], Optional[Dict]]:
    """Load consumer key/secret + OAuth tokens via the in-repo auth module.

    Returns (None, None, None) if either consumer credentials or saved tokens
    are missing. The caller decides how to surface the failure.
    """
    try:
        ck, cs = _consumer_credentials()
    except Exception:
        return None, None, None
    tokens = load_tokens()
    if not tokens:
        return ck, cs, None
    return ck, cs, tokens


def _flatten_account(acct: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce E*TRADE account dict into our canonical shape."""
    return {
        "accountIdKey": acct.get("accountIdKey"),
        "accountId": acct.get("accountId"),
        "accountType": acct.get("accountType"),
        "accountDesc": acct.get("accountDesc"),
        "institutionType": acct.get("institutionType"),
        "accountStatus": acct.get("accountStatus"),
    }


def _flatten_equity_position(p: Dict[str, Any], acct_desc: str) -> Dict[str, Any]:
    product = p.get("Product", {})
    qty = float(p.get("quantity", 0))
    market_value = float(p.get("marketValue", 0))
    price = market_value / qty if qty else 0
    cost_basis = float(p.get("pricePaid", 0))
    return {
        "symbol": product.get("symbol"),
        "assetType": "EQUITY",
        "qty": qty,
        "price": round(price, 2),
        "marketValue": market_value,
        "costBasis": cost_basis,
        "totalGain": float(p.get("totalGain", 0)),
        "totalGainPct": float(p.get("totalGainPct", 0)),
        "accountDesc": acct_desc,
    }


def _flatten_option_position(p: Dict[str, Any], acct_desc: str) -> Dict[str, Any]:
    """Map E*TRADE COMPLETE-view position into our canonical option shape.

    Critical fields pulled when view=COMPLETE:
    - pricePaid: actual premium per contract at entry (real)
    - Complete.{delta,gamma,theta,vega,rho}: real Greeks
    - Complete.ivPct: implied volatility (decimal, e.g. 0.4324 = 43.24%)
    - Complete.{bid,ask,lastTrade}: real live quotes
    - Complete.openInterest: real OI
    - totalGain / totalGainPct: real P&L since open
    """
    product = p.get("Product", {})
    complete = p.get("Complete", {}) or {}

    underlying = product.get("symbol")
    call_put = product.get("callPut", "PUT").upper()
    qty = float(p.get("quantity", 0))
    market_value = float(p.get("marketValue", 0))
    strike = float(product.get("strikePrice", 0))

    yr = product.get("expiryYear")
    mo = product.get("expiryMonth")
    dy = product.get("expiryDay")
    expiry = None
    if yr and mo and dy:
        expiry = f"{int(yr):04d}-{int(mo):02d}-{int(dy):02d}"

    entry_price = float(p.get("pricePaid", 0))
    cost_per_share = float(p.get("costPerShare", entry_price))
    total_gain = float(p.get("totalGain", 0))
    total_gain_pct = float(p.get("totalGainPct", 0))

    # Current mid: prefer Complete.lastTrade, fall back to derived from marketValue
    last_trade = complete.get("lastTrade")
    if last_trade is None:
        # marketValue / qty / 100 — sign cancels out via abs()
        last_trade = abs(market_value / qty) / 100 if qty else 0
    current_mid = float(last_trade)

    # Bid/ask if available
    bid = complete.get("bid")
    ask = complete.get("ask")

    # Greeks — only meaningful with view=COMPLETE
    delta = complete.get("delta")
    gamma = complete.get("gamma")
    theta = complete.get("theta")
    vega = complete.get("vega")
    rho = complete.get("rho")

    iv_pct_decimal = complete.get("ivPct")  # 0.4324 = 43.24%
    iv_pct_real = float(iv_pct_decimal) * 100 if iv_pct_decimal is not None else None

    return {
        "symbol": f"{underlying}_{call_put}_{int(strike)}_{expiry}".replace("-", ""),
        "assetType": "OPTION",
        "underlying": underlying,
        "type": call_put,
        "strike": strike,
        "expiration": expiry,
        "qty": qty,
        "marketValue": market_value,

        # Real entry vs current
        "premiumReceived": round(entry_price, 4),
        "costPerShare": round(cost_per_share, 4),
        "currentMid": round(current_mid, 4),
        "bid": float(bid) if bid is not None else None,
        "ask": float(ask) if ask is not None else None,

        # Real P&L
        "totalGain": total_gain,
        "totalGainPct": total_gain_pct,

        # Real Greeks (per contract)
        "delta": float(delta) if delta is not None else None,
        "gamma": float(gamma) if gamma is not None else None,
        "theta": float(theta) if theta is not None else None,
        "vega": float(vega) if vega is not None else None,
        "rho": float(rho) if rho is not None else None,

        # Real implied vol (percentage points)
        "ivPct": iv_pct_real,

        # Real liquidity
        "openInterest": complete.get("openInterest"),

        "symbolDescription": p.get("symbolDescription"),
        "accountDesc": acct_desc,
        "positionType": p.get("positionType"),  # SHORT / LONG
    }


def fetch_etrade_snapshot(
    accounts_filter: Optional[List[str]] = None,
) -> EtradeSnapshot:
    """Pull a real portfolio snapshot from E*TRADE.

    accounts_filter: if provided, only include accounts whose accountIdKey is
    in this list. Default: all ACTIVE accounts.
    """
    from datetime import datetime
    warnings = []
    fetched_at = datetime.utcnow().isoformat() + "Z"

    ck, cs, tokens = _load_credentials()
    if not (ck and cs):
        raise RuntimeError(
            "E*TRADE consumer credentials missing. Set ETRADE_CONSUMER_KEY and "
            "ETRADE_CONSUMER_SECRET in the environment or in "
            "$PORTFOLIO_BRIEFING_REPO/.env."
        )
    if not tokens:
        raise RuntimeError(
            f"E*TRADE OAuth tokens missing at {token_file_path()}. "
            "Run the interactive auth flow once: "
            "`python3 scripts/etrade_auth.py` from the daily-portfolio-briefing dir."
        )

    kwargs = dict(
        client_key=ck,
        client_secret=cs,
        resource_owner_key=tokens["oauth_token"],
        resource_owner_secret=tokens["oauth_secret"],
        dev=tokens.get("sandbox", False),
    )
    accounts_client = pyetrade.ETradeAccounts(**kwargs)

    # 1. List accounts
    raw = accounts_client.list_accounts(resp_format="json")
    raw_acct_list = raw["AccountListResponse"]["Accounts"]["Account"]
    if not isinstance(raw_acct_list, list):
        raw_acct_list = [raw_acct_list]

    flat_accounts = []
    aggregate_balance = {
        "totalAccountValue": 0.0,
        "cash": 0.0,
        "longMarketValue": 0.0,
    }
    all_positions = []

    for raw_acct in raw_acct_list:
        if raw_acct.get("accountStatus") != "ACTIVE":
            continue
        acct_key = raw_acct["accountIdKey"]
        if accounts_filter and acct_key not in accounts_filter:
            continue
        acct = _flatten_account(raw_acct)
        flat_accounts.append(acct)
        acct_type = raw_acct.get("accountType")
        acct_desc = raw_acct.get("accountDesc", acct_key[:8])

        # 2. Balance
        try:
            bal = accounts_client.get_account_balance(
                acct_key, account_type=acct_type, resp_format="json"
            )
            bd = bal["BalanceResponse"]
            cmp_data = bd.get("Computed", {})
            rtv = cmp_data.get("RealTimeValues", {}) or {}
            nlv = float(rtv.get("totalAccountValue", 0))
            cash = float(cmp_data.get("cashAvailableForInvestment", 0))
            lmv = float(rtv.get("totalLongValue", 0))
            aggregate_balance["totalAccountValue"] += nlv
            aggregate_balance["cash"] += cash
            aggregate_balance["longMarketValue"] += lmv
            acct["nlv"] = nlv
            acct["cash"] = cash
        except Exception as e:
            warnings.append(f"balance fetch failed for {acct_desc}: {str(e)[:100]}")

        # 3. Positions — view=COMPLETE returns Greeks, IV, real entry prices
        try:
            pf = accounts_client.get_account_portfolio(
                acct_key, resp_format="json", count=200, view="COMPLETE"
            )
            port = pf.get("PortfolioResponse", {}).get("AccountPortfolio", [])
            if not isinstance(port, list):
                port = [port]
            for ap in port:
                positions_raw = ap.get("Position", [])
                if not isinstance(positions_raw, list):
                    positions_raw = [positions_raw]
                for p in positions_raw:
                    sec_type = p.get("Product", {}).get("securityType", "")
                    if sec_type == "OPTN":
                        all_positions.append(_flatten_option_position(p, acct_desc))
                    elif sec_type in ("EQ", "MF", "INDEX", "ETF"):
                        all_positions.append(_flatten_equity_position(p, acct_desc))
        except Exception as e:
            msg = str(e)
            if "no positions" not in msg.lower() and "204" not in msg:
                warnings.append(f"positions fetch failed for {acct_desc}: {msg[:100]}")

        # 4. Open orders (best effort — if it fails the briefing still works)
    open_orders: List[Dict[str, Any]] = []  # v1 leaves this empty; orders API has different signature

    return EtradeSnapshot(
        accounts=flat_accounts,
        positions=all_positions,
        balance={
            "totalAccountValue": round(aggregate_balance["totalAccountValue"], 2),
            "cash": round(aggregate_balance["cash"], 2),
            "longMarketValue": round(aggregate_balance["longMarketValue"], 2),
            "accountValue": round(aggregate_balance["totalAccountValue"], 2),  # alias
        },
        open_orders=open_orders,
        source="etrade",
        fetched_at=fetched_at,
        warnings=warnings,
    )
