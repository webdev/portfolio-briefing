"""Stress coverage — correlation-aware drawdown scenarios for short-put exposure.

Computes cash coverage of put obligations across -10/-20/-30% market drops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


_HIGH_CORR = {  # 0.95 — tech/semi
    "AAPL", "MSFT", "GOOGL", "GOOG", "META", "AMZN", "ADBE", "CRM", "NOW", "ORCL",
    "NVDA", "AMD", "AVGO", "QCOM", "MU", "TSM", "INTC", "MRVL",
    "TSLA", "NFLX",
}
_CYCLICAL = {  # 0.85
    "GE", "CAT", "DE", "BA", "F", "GM", "DAL", "UAL", "AAL", "JPM", "BAC",
    "GS", "MS", "C", "WFC",
}
_DEFENSIVE = {  # 0.5 — energy/staples
    "XOM", "CVX", "COP", "OXY", "SLB",
    "PG", "KO", "PEP", "WMT", "COST", "MCD", "MO", "PM",
}
_LOW_CORR = {  # 0.3 — gold/utilities
    "GLD", "SLV", "GDX", "IAU",
    "NEE", "DUK", "SO", "AEP", "XEL",
    "TLT", "IEF",
}


def correlation_factor(symbol: str) -> float:
    """SPY-correlation multiplier for a name in a market drawdown."""
    s = symbol.upper()
    if s in _HIGH_CORR:
        return 0.95
    if s in _CYCLICAL:
        return 0.85
    if s in _DEFENSIVE:
        return 0.5
    if s in _LOW_CORR:
        return 0.3
    return 0.7


@dataclass
class DropScenario:
    """Outcome of a hypothetical SPY drop applied with per-name correlation."""
    drop_pct: float
    assigned_obligations: Decimal
    cash_after: Decimal
    nlv_after: Decimal
    is_shortfall: bool
    assigned_symbols: list[str] = field(default_factory=list)
    stock_loss: Decimal = Decimal(0)


@dataclass
class CloseRecommendation:
    """A profitable short option ranked by efficiency at lifting coverage."""
    symbol: str
    position_id: str
    collateral_freed: Decimal
    cost_to_close: Decimal
    profit_pct_captured: float
    profit_dollars: Decimal
    efficiency: float
    reason: str


@dataclass
class StressCoverage:
    """Portfolio-level stress coverage report."""
    coverage_ratio: float
    target_ratio: float
    cash: Decimal
    total_put_obligations: Decimal
    drops: dict[float, DropScenario] = field(default_factory=dict)
    recommended_closes: list[CloseRecommendation] = field(default_factory=list)


def compute_stress_coverage(
    positions: list[dict],
    cash: Decimal,
    nlv: Decimal,
    target_ratio: float = 0.7,
) -> StressCoverage:
    """Compute correlation-aware stress coverage.

    positions: list of position dicts with:
      - symbol, position_type, quantity, strike, underlying_price, entry_price, current_price, expiration
    """
    short_puts = [
        p for p in positions
        if p.get("position_type") == "short_put" and p.get("strike", 0) > 0
    ]
    long_stocks = [p for p in positions if p.get("position_type") == "long_stock"]

    total_obligation = Decimal(0)
    for p in short_puts:
        strike = Decimal(str(p.get("strike", 0)))
        qty = abs(p.get("qty", p.get("quantity", 0)))  # Handle both "qty" and "quantity" keys
        total_obligation += strike * Decimal(qty) * Decimal(100)

    coverage_ratio = float(cash / total_obligation) if total_obligation > 0 else float("inf")

    drops: dict[float, DropScenario] = {}
    for drop_pct in (0.10, 0.20, 0.30):
        assigned_cost = Decimal(0)
        assigned_syms: list[str] = []

        for p in short_puts:
            underlying_price = Decimal(str(p.get("underlying_price", 0)))
            strike = Decimal(str(p.get("strike", 0)))
            symbol = p.get("symbol", "?")

            if underlying_price <= 0:
                effective_underlying = strike
            else:
                corr = correlation_factor(symbol)
                effective_underlying = underlying_price * Decimal(str(1.0 - drop_pct * corr))

            if effective_underlying <= strike:
                qty = abs(p.get("qty", p.get("quantity", 0)))  # Handle both "qty" and "quantity" keys
                assigned_cost += strike * Decimal(qty) * Decimal(100)
                assigned_syms.append(f"{symbol} {qty}x")

        # Stock losses
        stock_loss = Decimal(0)
        for p in long_stocks:
            underlying_price = Decimal(str(p.get("underlying_price", 0)))
            qty = p.get("qty", p.get("quantity", 0))  # Handle both "qty" and "quantity" keys
            symbol = p.get("symbol", "?")
            corr = correlation_factor(symbol)
            stock_loss += underlying_price * Decimal(qty) * Decimal(str(drop_pct * corr))

        cash_after = cash - assigned_cost
        overdraft = -cash_after if cash_after < 0 else Decimal(0)
        nlv_after = nlv - stock_loss - overdraft

        drops[drop_pct] = DropScenario(
            drop_pct=drop_pct,
            assigned_obligations=assigned_cost,
            cash_after=cash_after,
            nlv_after=nlv_after,
            is_shortfall=cash_after < 0,
            assigned_symbols=assigned_syms,
            stock_loss=stock_loss,
        )

    recommended_closes = _rank_closes(short_puts)

    return StressCoverage(
        coverage_ratio=coverage_ratio,
        target_ratio=target_ratio,
        cash=cash,
        total_put_obligations=total_obligation,
        drops=drops,
        recommended_closes=recommended_closes,
    )


def _rank_closes(short_puts: list[dict]) -> list[CloseRecommendation]:
    """Rank profitable shorts by efficiency."""
    recs: list[CloseRecommendation] = []
    for p in short_puts:
        profit_pct = p.get("profit_pct", 0.0)
        if profit_pct < 0.30:
            continue

        symbol = p.get("symbol", "?")
        strike = Decimal(str(p.get("strike", 0)))
        qty = abs(p.get("qty", p.get("quantity", 0)))  # Handle both "qty" and "quantity" keys
        current_price = Decimal(str(p.get("current_price", 0)))
        entry_price = Decimal(str(p.get("entry_price", p.get("premiumReceived", 0))))  # Try premiumReceived too
        expiration = p.get("expiration")

        contracts = Decimal(qty)
        cost_to_close = current_price * contracts * Decimal(100)
        collateral = strike * contracts * Decimal(100)
        profit_dollars = (entry_price - current_price) * contracts * Decimal(100)

        denom = max(cost_to_close, Decimal("1"))
        efficiency = float(collateral / denom) * profit_pct
        if expiration is None:
            exp = "perp"
        elif hasattr(expiration, "isoformat"):
            exp = expiration.isoformat()
        else:
            exp = str(expiration)

        recs.append(CloseRecommendation(
            symbol=symbol,
            position_id=f"{symbol}|{strike}|{exp}",
            collateral_freed=collateral,
            cost_to_close=cost_to_close,
            profit_pct_captured=profit_pct,
            profit_dollars=profit_dollars,
            efficiency=efficiency,
            reason=(
                f"+{profit_pct:.0%} captured (${profit_dollars:,.0f}), "
                f"frees ${collateral:,.0f} for ${cost_to_close:,.0f} buy-back"
            ),
        ))
    recs.sort(key=lambda r: r.efficiency, reverse=True)
    return recs
