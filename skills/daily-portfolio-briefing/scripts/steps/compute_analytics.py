"""Compute analytics — stress coverage, concentration, expiration ladder, hedges."""

import sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.stress_coverage import compute_stress_coverage
from analysis.concentration_drift import detect_concentration_drift
from analysis.expiration_ladder import analyze_expiration_ladder
from analysis.hedge_book import build_hedge_book


def _enrich_positions(positions: list) -> list:
    """Enrich positions with derived fields for analysis."""
    for p in positions:
        # Add market_value for concentration analysis
        if p.get("assetType") == "EQUITY":
            price = p.get("price", 0.0)
            qty = p.get("qty", 0)
            p["market_value"] = float(price * qty)
            p["position_type"] = "long_stock"
            p["underlying_price"] = price

        # For options: use provided fields, parse if needed
        elif p.get("assetType") == "OPTION":
            # Fields should already be in the position dict
            # If not, try to parse from symbol
            if not p.get("strike"):
                symbol = p.get("symbol", "")
                parts = symbol.split("_")
                if len(parts) >= 2:
                    try:
                        p["strike"] = float(parts[-1]) if parts[-1].replace(".", "").isdigit() else None
                    except (ValueError, IndexError):
                        pass

            if not p.get("expiration"):
                symbol = p.get("symbol", "")
                parts = symbol.split("_")
                if len(parts) >= 2:
                    try:
                        exp_str = parts[-2]
                        if len(exp_str) == 8:
                            year = int("20" + exp_str[0:2])
                            month = int(exp_str[2:4])
                            day = int(exp_str[4:6])
                            p["expiration"] = datetime(year, month, day).date()
                    except (ValueError, IndexError):
                        pass

            if not p.get("option_type"):
                p["option_type"] = p.get("type", "").upper()

            # Determine position type from qty and option type
            qty = p.get("qty", 0)
            opt_type = p.get("option_type", "").lower()
            if qty < 0:
                p["position_type"] = f"short_{opt_type}"
            else:
                p["position_type"] = f"long_{opt_type}"

            # Get underlying if not already set
            if not p.get("underlying"):
                symbol = p.get("symbol", "")
                parts = symbol.split("_")
                if parts:
                    p["underlying"] = parts[0]

            # Price from quote or currentMid
            p["underlying_price"] = p.get("currentMid", p.get("price", 0.0))
            p["current_price"] = p.get("currentMid", p.get("price", 0.0))

            # Compute profit_pct for short options (used by stress coverage close-ranking).
            # For shorts: capture = (entry - current) / entry; positive when contract has decayed.
            qty_p = p.get("qty", 0)
            if qty_p < 0:
                entry = (
                    p.get("entry_price")
                    or p.get("entryPrice")
                    or p.get("premiumReceived")
                    or 0
                )
                cur = p.get("currentMid") or p.get("price") or 0
                try:
                    entry_f = float(entry)
                    cur_f = float(cur)
                except (TypeError, ValueError):
                    entry_f, cur_f = 0.0, 0.0
                if entry_f > 0:
                    p["profit_pct"] = (entry_f - cur_f) / entry_f
                else:
                    p["profit_pct"] = 0.0

    return positions


def compute_analytics(
    snapshot_data: dict,
    config: dict,
    macro_caution: str = "none",
) -> dict:
    """Compute all portfolio analytics.

    snapshot_data: {
      "balance": {"accountValue": float, "cash": float},
      "positions": [list of position dicts with assetType: EQUITY|OPTION],
      "quotes": {"SPY": {...}, "VIX": {...}, ...}
    }

    Returns: {
      "stress_coverage": StressCoverage,
      "concentration": [ConcentrationAlert],
      "expirations": [ExpirationCluster],
      "hedge_book": HedgeBook,
    }
    """
    positions = snapshot_data.get("positions", [])
    balance = snapshot_data.get("balance", {})
    quotes = snapshot_data.get("quotes", {})

    nlv = Decimal(str(balance.get("accountValue", 0)))
    cash = Decimal(str(balance.get("cash", 0)))
    spy_quote = quotes.get("SPY", {})
    spy_price = Decimal(str(spy_quote.get("last", 0)))

    # Enrich positions with derived fields
    enriched = _enrich_positions(positions)

    # Update underlying prices from quotes
    for p in enriched:
        if p.get("assetType") == "EQUITY":
            sym = p.get("symbol", "")
            if sym in quotes:
                p["underlying_price"] = quotes[sym].get("last", p.get("price", 0.0))
        elif p.get("assetType") == "OPTION":
            underlying = p.get("underlying", "")
            if underlying in quotes:
                p["underlying_price"] = quotes[underlying].get("last", p.get("price", 0.0))

    # Compute stress coverage (only for short puts)
    stress_coverage = compute_stress_coverage(enriched, cash, nlv)

    # Compute concentration drift (only for long stocks)
    concentration = detect_concentration_drift(enriched, float(nlv))

    # Compute expiration ladder (for options)
    expirations = analyze_expiration_ladder(enriched, float(nlv))

    # Compute hedge book.
    # Long delta = sum of long-equity shares (each share = 1 delta-share)
    # plus contributions from any short calls (which reduce effective long delta).
    equity_long_shares = sum(
        float(p.get("qty", 0) or 0)
        for p in enriched
        if p.get("assetType") == "EQUITY" and float(p.get("qty", 0) or 0) > 0
    )
    short_call_delta_offset = sum(
        float(p.get("delta") or 0) * float(p.get("qty", 0) or 0) * 100
        for p in enriched
        if p.get("assetType") == "OPTION"
        and p.get("type") == "CALL"
        and float(p.get("qty", 0) or 0) < 0
    )
    # short_call_delta_offset is already negative (qty<0) — adding it to equity_long_shares
    # gives the effective long delta after covered calls
    long_delta = equity_long_shares + short_call_delta_offset
    hedge_book = build_hedge_book(
        enriched,
        nlv,
        long_delta=long_delta,
        macro_caution=macro_caution,
        spy_price=spy_price if spy_price > 0 else None,
    )

    return {
        "stress_coverage": stress_coverage,
        "concentration": concentration,
        "expirations": expirations,
        "hedge_book": hedge_book,
        "nlv": nlv,
        "cash": cash,
        "spy_price": spy_price,
    }
