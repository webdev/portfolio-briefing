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

try:
    from analysis import rsi_discipline
except ImportError:  # pragma: no cover - path fallback for standalone runs
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis import rsi_discipline


def _safe_price(v) -> float:
    """Coerce a price to a safe float; NaN / None / non-finite / negative → 0.

    Strategy-upgrade math (`target_strike = round(price * 1.06 / 5) * 5`,
    `premium_per_share = price * 0.015`, etc.) crashes with
    `ValueError: cannot convert float NaN to integer` on NaN inputs.
    yfinance/E*TRADE quote failures can leave equity positions with
    price=None or NaN. Coercing to 0 here lets the existing `price <= 0`
    guards (already present at every site) skip those positions cleanly
    — no control-flow changes needed. (Symptom 2026-06-18: crash at
    strategy_upgrades.py:672 in sub-lot completion path.)
    """
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) and f > 0 else 0.0


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


def _is_likely_mutual_fund(symbol: str) -> bool:
    """Mutual funds use 5-letter tickers ending in X (no options trade on them).

    We skip these in covered-call recommendations — there's no listed
    options chain to write against.
    """
    if not symbol:
        return False
    s = symbol.upper().strip()
    return len(s) == 5 and s.endswith("X") and s.isalpha()


# Cached module reference for the etrade-chain-fetcher skill
_ETRADE_FETCHER_MODULE = None
_ETRADE_FETCHER_CACHE = None


def _load_chain_fetcher():
    """Load and cache the etrade-chain-fetcher skill module."""
    global _ETRADE_FETCHER_MODULE, _ETRADE_FETCHER_CACHE
    if _ETRADE_FETCHER_MODULE is not None:
        return _ETRADE_FETCHER_MODULE, _ETRADE_FETCHER_CACHE

    import importlib.util as _ilu
    target = (
        Path(__file__).resolve().parents[3]
        / "etrade-chain-fetcher" / "scripts" / "fetch.py"
    )
    if not target.exists():
        return None, None
    spec = _ilu.spec_from_file_location("etrade_chain_fetcher", target)
    if spec is None or spec.loader is None:
        return None, None
    mod = _ilu.module_from_spec(spec)
    sys.modules["etrade_chain_fetcher"] = mod
    spec.loader.exec_module(mod)
    if not mod.is_available():
        return None, None
    _ETRADE_FETCHER_MODULE = mod
    _ETRADE_FETCHER_CACHE = mod.ChainCache()
    return mod, _ETRADE_FETCHER_CACHE


def _etrade_call_quote(
    symbol: str,
    spot: float,
    target_otm_pct: float = 6.0,
    target_dte: int = 35,
    target_delta: float = 0.25,
    delta_tolerance: float = 0.12,
    sr_resistances: list | None = None,
) -> dict | None:
    """Pull a covered-call quote via the canonical E*TRADE chain fetcher.

    Strike selection is **delta-first** (volatility-adaptive): we target a
    ~``target_delta`` call, so the SAME delta sits further OTM on a high-IV name
    (more headroom) and closer on a low-IV name. A flat %OTM target does the
    opposite — it caps a volatile name too tight (the SOFI $17-on-$16 case).

    When ``sr_resistances`` is provided AND a strong resistance cluster sits
    inside the delta-band's strike neighborhood, we prefer that strike — it
    caps the position at a real chartist level rather than a delta-anchored
    one (hard rule #20 — S/R discipline). The selected delta is then the
    MEASURED delta at the resistance strike, never a fabricated one.

    We fall back to ``target_otm_pct`` ONLY when the chain carries no usable
    deltas (e.g. E*TRADE didn't populate Greeks for the legs). In that case the
    returned ``delta`` is None and callers must render "δ n/a" — never a
    fabricated delta.

    Returns {strike, bid, mid, ask, delta, iv, open_interest, expiration,
    source, selected_by, sr_anchor} or None if E*TRADE is unavailable.
    ``selected_by`` is "delta", "otm_pct", or "sr_anchor". ``sr_anchor`` is
    the Level-shaped dict the strike was snapped to (or None).
    """
    mod, cache = _load_chain_fetcher()
    if mod is None:
        return None
    exp_date = mod.choose_expiration(
        symbol=symbol, target_dte=target_dte, tolerance_days=14, cache=cache
    )
    if exp_date is None:
        return None
    # Delta-band first — adapts strike distance to each name's volatility.
    q = mod.find_strike_near_delta(
        symbol=symbol,
        expiration=exp_date,
        target_delta=target_delta,
        opt_type="CALL",
        spot=spot,
        tolerance=delta_tolerance,
        cache=cache,
    )
    if q:
        q["selected_by"] = "delta"
        q["sr_anchor"] = None
        # S/R refinement: when a strong resistance sits within ±cluster of the
        # delta-selected strike, snap to it. The chain fetcher's quote_contract
        # call returns the *measured* delta at the snapped strike — never an
        # invented one. If the snap quote fails (chain doesn't list that exact
        # strike), keep the delta pick.
        if sr_resistances:
            chosen_strike = float(q.get("strike", 0) or 0)
            if chosen_strike > 0:
                # Same neighborhood as the delta tolerance, mapped to price:
                # ±3% of the delta strike. The chain's strike granularity bounds
                # how close we can actually snap.
                band_lo = chosen_strike * 0.97
                band_hi = chosen_strike * 1.03
                in_band = [
                    lv for lv in sr_resistances
                    if isinstance(lv, dict)
                    and lv.get("side") == "resistance"
                    and band_lo <= float(lv.get("price", 0)) <= band_hi
                    and float(lv.get("strength", 0)) >= 1.5
                ]
                if in_band:
                    anchor = max(in_band, key=lambda lv: float(lv.get("strength", 0)))
                    anchor_price = float(anchor["price"])
                    # Try to snap the quote to the resistance strike. Use the
                    # chain's quote_contract to fetch the real bid/mid/ask/delta
                    # at that strike.
                    snapped = mod.quote_contract(
                        symbol=symbol,
                        strike=anchor_price,
                        expiration=exp_date,
                        opt_type="CALL",
                        cache=cache,
                    )
                    if snapped:
                        snapped["selected_by"] = "sr_anchor"
                        snapped["sr_anchor"] = anchor
                        return snapped
                    # Chain didn't list that exact strike — keep the delta pick
                    # but record that we considered an anchor for transparency.
                    q["sr_anchor"] = None
        return q
    # Fallback: nearest strike ~N% OTM (chain had no usable deltas). delta=None.
    q = mod.find_strike_at_otm_pct(
        symbol=symbol,
        expiration=exp_date,
        otm_pct=target_otm_pct,
        opt_type="CALL",
        spot=spot,
        cache=cache,
    )
    if q:
        q["selected_by"] = "otm_pct"
        q["sr_anchor"] = None
    return q


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
    technicals = snapshot_data.get("technicals", {}) or {}

    # RSI discipline thresholds. params IS the full briefing config here.
    rsi_th = rsi_discipline.load_thresholds(params)
    rsi_gate_on = rsi_th.get("enabled", True)

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
        price = _safe_price(equity_pos.get("price"))
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

        # RSI discipline — the strangle leg being ADDED is a short PUT, so route
        # through the central hook (overbought RSI > 70 → removed). The existing
        # covered call is unaffected (management, not a new open).
        rsi_val = rsi_discipline.rsi_for(symbol, technicals)
        rv = rsi_discipline.hook("put", rsi_val, rsi_th)

        upgrade = {
            "type": "covered_strangle",
            "underlying": symbol,
            "rsi_14": rsi_val,
            "rsi_tag": rv.tag,
            "rsi_note": rv.reason,
            "rsi_decision": rv.decision,
            "rsi_badge": rv.badge,
            "rsi_blocked": bool(rsi_gate_on and rv.removed),
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
        price = _safe_price(equity_pos.get("price"))
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

        # Collar buys a protective put — defensive management, not a new short
        # open, so RSI is shown for context but never gates.
        _collar_rsi = rsi_discipline.rsi_for(symbol, technicals)
        upgrade = {
            "type": "collar",
            "underlying": symbol,
            "rsi_14": _collar_rsi,
            "rsi_tag": rsi_discipline.tag(_collar_rsi),
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

    # === Type D: Write Covered Call (NEW position) ===
    # For equity positions with >= 100 shares and NO existing short call,
    # propose writing a CC. Without this, holdings transition from "sub-lot
    # completion in progress" → 100+ shares → silently drop out of the panel.
    # The user has unused income capacity in those shares; surface it.

    for equity_pos in positions:
        if equity_pos.get("assetType") != "EQUITY":
            continue

        symbol = equity_pos.get("symbol")
        if not symbol:
            continue

        qty = equity_pos.get("qty", 0)
        price = _safe_price(equity_pos.get("price"))
        if qty < 100 or price <= 0:
            continue

        if _is_tail_risk_name(symbol):
            continue

        # Skip mutual funds — no options chains listed
        if _is_likely_mutual_fund(symbol):
            continue

        # Skip if a short call already exists on this name
        if _find_short_call(positions, symbol):
            continue

        # Per-account share check. A CC must be written against a 100-share
        # round lot held WITHIN A SINGLE ACCOUNT (the broker can't combine
        # lots across accounts for short-call coverage). The aggregate qty
        # might be ≥100 across two accounts but neither has a writeable lot.
        accounts_breakdown = equity_pos.get("accountsBreakdown") or []
        max_qty_in_one_account = qty  # default when no breakdown available
        if accounts_breakdown:
            import re as _re
            per_account_qtys = []
            for entry in accounts_breakdown:
                m = _re.search(r":\s*(\d+(?:\.\d+)?)\s*sh", entry)
                if m:
                    per_account_qtys.append(float(m.group(1)))
            if per_account_qtys:
                max_qty_in_one_account = max(per_account_qtys)

        if max_qty_in_one_account < 100:
            # Aggregate qty looked like 100+ but no single account holds a lot
            # → CC isn't writeable. Skip silently.
            continue

        # Number of contracts we COULD write — capped by the largest single-
        # account lot, NOT the aggregate. Avoids proposing a 2-contract CC
        # when the largest single-account lot only holds 105 shares.
        contracts_writable = int(max_qty_in_one_account // 100)
        if contracts_writable < 1:
            continue

        # Target window + covered-call strike-selection config (delta-band).
        target_dte = 35
        target_exp_date = date.today() + timedelta(days=target_dte)
        cc_cfg = (params.get("covered_call") or {})
        cc_target_delta = float(cc_cfg.get("target_delta", 0.25))
        cc_delta_tol = float(cc_cfg.get("delta_tolerance", 0.12))
        cc_fallback_otm = float(cc_cfg.get("fallback_otm_pct", 6.0))

        # S/R-aware strike anchoring (hard rule #20): when the snapshot has a
        # resistance cluster, the chain fetcher will prefer snapping the CC
        # strike to it if a strong resistance sits in the delta-band's
        # neighborhood. Fail-closed when SR is missing — falls back to pure
        # delta-band selection.
        sr_resistances = None
        sr_payload = (technicals.get(symbol) or {}).get("support_resistance")
        if isinstance(sr_payload, dict):
            sr_resistances = sr_payload.get("resistances") or None

        # Pull real E*TRADE chain via the canonical fetcher. NEVER yfinance.
        # Delta-first selection (falls back to %OTM only when chain has no deltas).
        chain_quote = _etrade_call_quote(
            symbol=symbol, spot=price, target_otm_pct=cc_fallback_otm,
            target_dte=target_dte, target_delta=cc_target_delta,
            delta_tolerance=cc_delta_tol,
            sr_resistances=sr_resistances,
        )

        # Real, measured values — never a hardcoded/fabricated delta. delta may
        # be None (then rendered "δ n/a"); otm_pct is computed from the actual
        # selected strike vs. spot.
        cc_delta = None
        strike_selected_by = "estimate"
        if chain_quote:
            target_strike = chain_quote["strike"]
            premium_per_share = chain_quote["mid"] or chain_quote.get("bid") or 0
            bid = chain_quote.get("bid", 0)
            ask = chain_quote.get("ask", 0)
            cc_delta = chain_quote.get("delta")
            strike_selected_by = chain_quote.get("selected_by", "etrade")
            actual_exp = chain_quote.get("expiration") or target_exp_date.isoformat()
            try:
                actual_exp_date = date.fromisoformat(actual_exp)
                actual_dte = (actual_exp_date - date.today()).days
            except ValueError:
                actual_dte = target_dte
            chain_source = "etrade_live"
        else:
            # E*TRADE unavailable — emit rule-of-thumb estimate AND flag
            target_strike = round(price * 1.06 / 5) * 5
            if target_strike <= price:
                target_strike = round(price * 1.08 / 5) * 5
            premium_per_share = price * 0.015  # ~1.5% of spot
            bid = ask = 0
            actual_dte = target_dte
            chain_source = "estimate_broker_unreachable"

        otm_pct_actual = ((target_strike - price) / price * 100.0) if price > 0 else None

        premium_total = premium_per_share * 100 * contracts_writable

        # Earnings check (still useful even if chain fetched OK)
        earnings_str = earnings_calendar.get(symbol)
        earnings_blocked = False
        if earnings_str:
            try:
                earn_date = date.fromisoformat(earnings_str)
                if date.today() <= earn_date <= target_exp_date:
                    earnings_blocked = True
            except ValueError:
                pass

        annualized = (
            (premium_per_share / price) * (365 / max(actual_dte, 1)) * 100
            if price > 0 else 0
        )

        # RSI discipline — a brand-new covered call written into an OVERSOLD
        # tape (RSI < 35) caps the name right before a likely bounce. Route
        # through the central hook (removed → footer; favored → promoted).
        # (Rolling an existing CC is management and never gated here.)
        rsi_val = rsi_discipline.rsi_for(symbol, technicals)
        rv = rsi_discipline.hook("call", rsi_val, rsi_th)
        rsi_cc_blocked = bool(rsi_gate_on and rv.removed)
        # Mid-range RSI (35-60) is NOT a hard block, but writing a new covered
        # call here caps the name for thin premium ("prefer waiting for
        # strength"). Hold it out of the actionable READY TO WRITE list into a
        # wait-for-strength section — the same discipline that pulls overbought
        # new puts/buys off the action list. Only RSI-favored (≥60) or
        # RSI-unknown writes stay actionable. (caution zone → keep + badge.)
        rsi_cc_wait = bool(
            rsi_gate_on and not rsi_cc_blocked
            and rv.decision == "keep" and rv.badge
        )

        # When the chain fetcher snapped to an S/R cluster, surface it so the
        # rendered card can say "δ 0.24 · at $230 resistance (Mar high + 50-SMA,
        # 3 touches)". Always carries the *measured* delta — never a fabrication.
        sr_anchor_payload = chain_quote.get("sr_anchor") if chain_quote else None

        upgrade = {
            "type": "write_covered_call",
            "underlying": symbol,
            "rsi_14": rsi_val,
            "rsi_tag": rv.tag,
            "rsi_note": rv.reason,
            "rsi_decision": rv.decision,
            "rsi_badge": rv.badge,
            "rsi_blocked": rsi_cc_blocked,
            "rsi_wait": rsi_cc_wait,
            "shares_held": int(qty),
            "contracts_writable": contracts_writable,
            "current_price": round(price, 2),
            "target_strike": float(target_strike),
            "target_dte": actual_dte,
            "target_delta": (round(abs(float(cc_delta)), 2) if cc_delta is not None else None),
            "otm_pct": (round(otm_pct_actual, 1) if otm_pct_actual is not None else None),
            "strike_selected_by": strike_selected_by,
            "sr_anchor": sr_anchor_payload,
            "est_premium_per_share": round(premium_per_share, 2),
            "est_premium_total": round(premium_total, 0),
            "est_annualized_pct": round(annualized, 1),
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "chain_source": chain_source,
            "current_weight_pct": round(qty * price / nlv * 100, 1) if nlv else 0,
            "earnings_blocked": earnings_blocked,
            "earnings_date": earnings_str if earnings_blocked else None,
            "rationale": (
                f"Hold {int(qty)} shares of {symbol} with no covered call open. "
                f"Writing {contracts_writable}× ${target_strike:g}C ({actual_dte} DTE, "
                f"~6% OTM) generates ~${premium_total:,.0f} premium "
                f"(~{annualized:.0f}% annualized). "
                + ("⚠ Earnings inside the contract window — defer until after print."
                   if earnings_blocked else
                   "No earnings inside window — safe to write.")
            ),
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
        price = _safe_price(equity_pos.get("price"))

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

        # RSI discipline — completing a sub-lot BUYS shares, so route through
        # the buy gate: overbought (RSI > 70) → removed (don't chase); pullback
        # → promoted. Mutual funds (no RSI) just pass through un-annotated.
        _buy_rsi = rsi_discipline.rsi_for(symbol, technicals)
        rv = rsi_discipline.hook("buy", _buy_rsi, rsi_th)

        upgrade = {
            "type": "sublot_completion",
            "underlying": symbol,
            "shares_held": int(qty),
            "shares_to_buy": int(shares_to_buy),
            "current_price": round(price, 2),
            "cost": round(cost, 2),
            "post_buy_weight_pct": round(post_buy_weight * 100, 1),
            "rsi_14": _buy_rsi,
            "rsi_tag": rv.tag,
            "rsi_note": rv.reason,
            "rsi_decision": rv.decision,
            "rsi_badge": rv.badge,
            "rsi_blocked": bool(rsi_gate_on and rv.removed),
            "rationale": f"Complete 100-share lot @ ${price:.2f} -> enable covered calls",
        }
        upgrades.append(upgrade)

    return upgrades
