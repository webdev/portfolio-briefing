"""
Step 6b: Strategy Upgrades — actionable enhancements to existing positions.

Three types of recommendations:
1. Covered Strangle Add-On: existing covered call → add complementary short put (same expiration)
2. Collar: equity holding with big unrealized gain → buy protective put to floor downside
3. Sub-Lot Completion: equity position with 1-99 shares → buy enough to reach 100-share lot

All recommendations must:
- Use real chain data (no fabricated strikes/expirations)
- Respect concentration cap (10% NLV hard limit)
- Check earnings guard (no puts through earnings window)
- Show real-dollar math backed by snapshot data
"""

import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict


@dataclass
class StrategyUpgrade:
    """A single upgrade recommendation."""
    upgrade_type: str  # "covered_strangle", "collar", "sublot_completion"
    underlying: str
    current_shares: int | None = None
    current_weight_pct: float | None = None
    recommended_action: str | None = None
    shares_to_buy: int | None = None
    position_details: dict = None  # varies by type
    concentration_check: dict = None  # blocked, reason
    collateral_or_cost: float | None = None
    rationale: str | None = None


def _find_position_by_symbol(positions: list, symbol: str, asset_type: str = "EQUITY") -> dict | None:
    """Find a position by symbol and asset type."""
    for p in positions:
        if p.get("symbol") == symbol and p.get("assetType") == asset_type:
            return p
    return None


def _find_options_for_underlying(positions: list, underlying: str) -> list[dict]:
    """Find all open option positions for a given underlying."""
    return [
        p for p in positions
        if p.get("assetType") == "OPTION"
        and p.get("underlying") == underlying
        and p.get("qty") != 0  # exclude closed
    ]


def _find_short_call(positions: list, underlying: str) -> dict | None:
    """Find an open short call position for an underlying."""
    for p in positions:
        if (p.get("assetType") == "OPTION"
            and p.get("underlying") == underlying
            and p.get("type") == "CALL"
            and p.get("qty", 0) < 0):  # short
            return p
    return None


def _get_chain_for_expiration(chains: dict, underlying: str, expiration: str) -> dict | None:
    """Look up a live option chain by underlying and expiration."""
    key = f"{underlying}_{expiration}"
    return chains.get(key)


def _find_put_strike_by_delta(chain: dict, target_delta: float = 0.20, tolerance: float = 0.05) -> dict | None:
    """Find a put strike close to target delta from the chain.

    If delta data unavailable, returns None (skip recommendation).
    """
    if not chain or not chain.get("puts"):
        return None

    puts = chain["puts"]
    candidates = []

    for put in puts:
        delta = put.get("delta")
        if delta is None:
            continue
        # For puts, delta is negative; we want absolute value
        abs_delta = abs(float(delta))
        if abs(abs_delta - target_delta) <= tolerance:
            candidates.append(put)

    if not candidates:
        return None

    # Return the one closest to target
    candidates.sort(key=lambda p: abs(abs(float(p["delta"])) - target_delta))
    return candidates[0]


def _check_concentration(existing_weight_pct: float, new_collateral: float, nlv: float, cap: float = 0.10) -> dict:
    """Check if adding new collateral would breach concentration cap."""
    if nlv <= 0:
        return {"blocked": False, "reason": None}

    new_weight_pct = (new_collateral / nlv) + existing_weight_pct

    if new_weight_pct > cap:
        return {
            "blocked": True,
            "reason": f"would push to {new_weight_pct:.1%} (cap {cap:.0%})"
        }
    return {"blocked": False, "reason": None}


def _earnings_conflict(earnings_calendar: dict, underlying: str, expiration_str: str) -> bool:
    """Check if earnings occur before/at expiration date."""
    if not earnings_calendar.get(underlying):
        return False

    try:
        earnings_date = datetime.strptime(earnings_calendar[underlying], "%Y-%m-%d").date()
        exp_date = datetime.strptime(expiration_str, "%Y-%m-%d").date()
        # Block if earnings within expiration window (earnings_date <= expiration)
        return earnings_date <= exp_date
    except (ValueError, TypeError):
        return False


def _is_tail_risk_name(symbol: str) -> bool:
    """Curated list of names that are not suitable for put-sale strategies."""
    tail_risk = {
        "BABA", "NIO", "XPE", "DDOG", "COIN",  # Chinese ADRs + crypto-proxies
        "AMRS", "EDIT", "GILD",  # Single-binary biotech
        "GME", "AMC", "TSLA",  # High-borrow, high-short memes (conservative)
    }
    return symbol in tail_risk


def compute_strategy_upgrades(
    snapshot_data: dict,
    equity_reviews: list,
    options_reviews: list,
    params: dict,
) -> list[dict]:
    """
    Compute strategy upgrade recommendations for existing positions.

    Args:
        snapshot_data: dict with "positions", "balance", "chains", "earnings_calendar", "quotes"
        equity_reviews: list of equity position reviews
        options_reviews: list of option position reviews
        params: dict with config (max_position_pct, etc.)

    Returns:
        list of upgrade recommendation dicts (one per recommendation)
    """

    positions = snapshot_data.get("positions", [])
    balance = snapshot_data.get("balance", {})
    chains = snapshot_data.get("chains", {})
    earnings_calendar = snapshot_data.get("earnings_calendar", {})
    quotes = snapshot_data.get("quotes", {})

    nlv = balance.get("accountValue", 0)
    if nlv <= 0:
        return []

    upgrades: list[dict] = []
    concentration_cap = params.get("max_position_pct", 0.10)

    # === Type A: Covered Strangles ===
    # For each equity with existing short calls, propose adding short puts

    for equity_pos in positions:
        if equity_pos.get("assetType") != "EQUITY":
            continue

        symbol = equity_pos.get("symbol")
        if not symbol:
            continue

        if _is_tail_risk_name(symbol):
            continue

        qty = equity_pos.get("qty", 0)
        price = equity_pos.get("price", 0)
        if qty <= 0 or price <= 0:
            continue

        # Find existing short call
        short_call = _find_short_call(positions, symbol)
        if not short_call:
            continue

        call_exp = short_call.get("expiration")
        if not call_exp:
            continue

        # Check earnings conflict
        if _earnings_conflict(earnings_calendar, symbol, call_exp):
            continue

        # Find put chain at same expiration
        chain = _get_chain_for_expiration(chains, symbol, call_exp)
        if not chain:
            continue

        # Find ~0.20 delta put
        put_strike_data = _find_put_strike_by_delta(chain, target_delta=0.20, tolerance=0.08)
        if not put_strike_data:
            continue

        # Compute collateral required
        strike = put_strike_data.get("strike")
        call_qty = abs(short_call.get("qty", 0))
        put_collateral = strike * 100 * call_qty

        current_weight_pct = (qty * price) / nlv
        conc_check = _check_concentration(current_weight_pct, put_collateral, nlv, concentration_cap)

        # Compute premium
        mid = (put_strike_data.get("bid", 0) + put_strike_data.get("ask", 0)) / 2
        if mid <= 0:
            mid = put_strike_data.get("lastPrice", 0)
        if mid <= 0:
            continue

        total_premium = mid * 100 * call_qty
        ann_yield = (total_premium / put_collateral * 365 / 30) if put_collateral > 0 else 0  # ~30 DTE

        # Compute call premium for reference
        call_price = short_call.get("currentMid") or short_call.get("premiumReceived", 0)
        call_total = call_price * 100 * call_qty

        upgrade = {
            "type": "covered_strangle",
            "underlying": symbol,
            "current_calls": f"{call_qty}x ${short_call.get('strike')}C exp {call_exp}",
            "proposed": {
                "action": "SELL_TO_OPEN",
                "qty": int(call_qty),
                "strike": float(strike),
                "expiration": call_exp,
                "delta": float(put_strike_data.get("delta", 0)),
                "premium_per_contract": round(mid, 2),
                "total_premium": round(total_premium, 2),
            },
            "yield_annualized": round(ann_yield, 4),
            "collateral_required": round(put_collateral, 2),
            "concentration_check": {
                "current_pct": round(current_weight_pct * 100, 1),
                "post_action_pct": round((current_weight_pct + put_collateral / nlv) * 100, 1),
                "blocked": conc_check["blocked"],
                "reason": conc_check["reason"],
            },
            "combined_income": {
                "calls": round(call_total, 2),
                "puts": round(total_premium, 2),
                "total": round(call_total + total_premium, 2),
            },
            "rationale": f"Convert {symbol} covered call to strangle; add {call_qty}x ${strike:.0f}P @ ${mid:.2f} premium",
        }
        upgrades.append(upgrade)

    # === Type B: Collars ===
    # For equity positions with big unrealized gains (>30% gain, >5% weight), buy protective puts

    for equity_pos in positions:
        if equity_pos.get("assetType") != "EQUITY":
            continue

        symbol = equity_pos.get("symbol")
        if not symbol:
            continue

        if _is_tail_risk_name(symbol):
            continue

        qty = equity_pos.get("qty", 0)
        price = equity_pos.get("price", 0)
        cost_basis = equity_pos.get("costBasis", 0)

        if qty <= 0 or price <= 0 or cost_basis <= 0:
            continue

        current_value = qty * price
        cost_value = qty * cost_basis
        unrealized_gain = current_value - cost_value
        gain_pct = unrealized_gain / cost_value if cost_value > 0 else 0
        weight_pct = current_value / nlv

        # Threshold: gain >30% AND weight >5%
        if gain_pct <= 0.30 or weight_pct <= 0.05:
            continue

        # Check for existing collars (short put + long put on same underlying)
        # For simplicity, skip if already has open puts
        existing_puts = [
            p for p in positions
            if p.get("assetType") == "OPTION"
            and p.get("underlying") == symbol
            and p.get("type") == "PUT"
        ]
        if existing_puts:
            continue

        # Find next monthly expiration from any available chain
        available_chains = [
            c for k, c in chains.items()
            if k.startswith(f"{symbol}_")
        ]
        if not available_chains:
            continue

        # Use the first available (nearest)
        chain = available_chains[0]
        exp_str = chain.get("expiration")
        if not exp_str:
            continue

        # Find ~0.10 delta put (deep protection)
        put_strike_data = _find_put_strike_by_delta(chain, target_delta=0.10, tolerance=0.08)
        if not put_strike_data:
            continue

        strike = put_strike_data.get("strike")
        put_bid = put_strike_data.get("bid", 0)
        put_ask = put_strike_data.get("ask", 0)
        put_mid = (put_bid + put_ask) / 2 if (put_bid and put_ask) else put_strike_data.get("lastPrice", 0)

        if put_mid <= 0:
            continue

        # Number of 100-share contracts needed
        contracts = max(1, qty // 100)
        put_cost = put_mid * 100 * contracts

        # Check for existing call premium offset (if there's a covered call)
        short_call = _find_short_call(positions, symbol)
        call_offset = 0.0
        if short_call:
            call_price = short_call.get("currentMid") or short_call.get("premiumReceived", 0)
            call_qty = abs(short_call.get("qty", 0))
            call_offset = call_price * 100 * call_qty

        net_cost = max(0, put_cost - call_offset)

        # Scenario: 20% drop from current price
        drop_price = price * 0.80
        gain_at_drop = (drop_price - cost_basis) * qty
        with_collar_gain = max(gain_at_drop, (strike - cost_basis) * qty)
        saved = max(0, with_collar_gain - gain_at_drop)

        upgrade = {
            "type": "collar",
            "underlying": symbol,
            "shares_held": int(qty),
            "current_unrealized_gain": round(unrealized_gain, 2),
            "gain_pct": round(gain_pct * 100, 1),
            "position_value": round(current_value, 2),
            "proposed_put": {
                "action": "BUY_TO_OPEN",
                "qty": int(contracts),
                "strike": float(strike),
                "expiration": exp_str,
                "delta": float(put_strike_data.get("delta", 0)),
                "cost_per_contract": round(put_mid, 2),
                "total_cost": round(put_cost, 2),
            },
            "call_offset": round(call_offset, 2),
            "net_collar_cost": round(net_cost, 2),
            "floor_strike": float(strike),
            "max_loss_from_current": round(max(0, (price - strike) * qty), 2),
            "scenario_minus_20pct": {
                "price_at_drop": round(drop_price, 2),
                "gain_without_collar": round(gain_at_drop, 2),
                "gain_with_collar": round(with_collar_gain, 2),
                "saves": round(saved, 2),
            },
            "rationale": f"Protect {symbol} ${unrealized_gain:,.0f} unrealized gain; floor @ ${strike:.0f}",
        }
        upgrades.append(upgrade)

    # === Type C: Sub-Lot Completions ===
    # For equity positions with 1-99 shares, buy to reach 100-share lot

    for equity_pos in positions:
        if equity_pos.get("assetType") != "EQUITY":
            continue

        symbol = equity_pos.get("symbol")
        if not symbol:
            continue

        qty = equity_pos.get("qty", 0)
        price = equity_pos.get("price", 0)

        if qty <= 0 or qty >= 100 or price <= 0:
            continue

        if _is_tail_risk_name(symbol):
            continue

        shares_to_buy = 100 - qty
        cost = shares_to_buy * price
        post_buy_weight = (qty + shares_to_buy) * price / nlv if nlv > 0 else 0

        # Skip if would breach concentration cap
        if post_buy_weight > concentration_cap:
            continue

        upgrade = {
            "type": "sublot_completion",
            "underlying": symbol,
            "shares_held": int(qty),
            "shares_to_buy": int(shares_to_buy),
            "current_price": round(price, 2),
            "cost": round(cost, 2),
            "post_buy_weight_pct": round(post_buy_weight * 100, 1),
            "rationale": f"Complete 100-share lot @ ${price:.2f} -> enable covered calls",
        }
        upgrades.append(upgrade)

    return upgrades
