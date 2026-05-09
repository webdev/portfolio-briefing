"""
Yield formulas for every options trade type.

This module is the single source of truth for "% return on capital" calculations.
It is consulted by the briefing renderer so every action item can show a yield
alongside its dollar amounts.
"""

from __future__ import annotations

from typing import Optional

ANNUAL_DAYS = 365


def _safe_div(num: float, den: float) -> float:
    """Avoid divide-by-zero; return 0 instead of inf/NaN."""
    if not den or den == 0:
        return 0.0
    return num / den


def _annualize(pct: float, dte: int) -> float:
    """Convert a point-in-time yield to annualized using calendar days."""
    if not dte or dte <= 0:
        return 0.0
    return pct * (ANNUAL_DAYS / dte)


def compute_csp_yield(
    premium: float,
    strike: float,
    contracts: int,
    dte: int,
) -> dict:
    """Cash-secured put yield. Capital = strike * 100 * contracts."""
    qty = abs(int(contracts))
    collateral = strike * 100 * qty
    total_premium = premium * 100 * qty
    static = _safe_div(total_premium, collateral) * 100
    static_ann = _annualize(static, dte)
    # If assigned, effective cost basis = strike - premium
    effective_basis = max(strike - premium, 0.01)
    if_assigned = _safe_div(premium, effective_basis) * 100
    if_assigned_ann = _annualize(if_assigned, dte)
    return {
        "kind": "csp",
        "headline_yield_pct": static_ann,
        "all_yields": {
            "static_yield_pct": static,
            "static_yield_ann_pct": static_ann,
            "if_assigned_yield_pct": if_assigned,
            "if_assigned_yield_ann_pct": if_assigned_ann,
        },
        "collateral_dollars": collateral,
        "premium_dollars": total_premium,
        "effective_basis": effective_basis,
        "notes": "Yield on collateral. If assigned, basis = strike − premium.",
    }


def compute_cc_yield(
    premium: float,
    strike: float,
    spot: float,
    contracts: int,
    dte: int,
    delta: Optional[float] = None,
) -> dict:
    """Covered call yield. Capital = spot * 100 * contracts (share market value)."""
    qty = abs(int(contracts))
    position_value = spot * 100 * qty
    total_premium = premium * 100 * qty
    static = _safe_div(total_premium, position_value) * 100
    static_ann = _annualize(static, dte)
    # If called: cap upside at strike + collect premium
    if_called_per_share = premium + max(strike - spot, 0)
    if_called = _safe_div(if_called_per_share, spot) * 100
    if_called_ann = _annualize(if_called, dte)
    return {
        "kind": "cc",
        "headline_yield_pct": static_ann,
        "all_yields": {
            "static_yield_pct": static,
            "static_yield_ann_pct": static_ann,
            "if_called_yield_pct": if_called,
            "if_called_yield_ann_pct": if_called_ann,
        },
        "position_value": position_value,
        "premium_dollars": total_premium,
        "assignment_proxy_delta": delta,
        "notes": (
            "Static yield assumes OTM expiration (keep shares + premium). "
            "If-called yield assumes assignment: includes both premium and "
            "any in-the-money component (strike − spot)."
        ),
    }


def compute_roll_yield(
    new_premium: float,
    new_strike: float,
    new_dte: int,
    contracts: int,
    spot: float,
    net_credit_dollars: float,
    position_value: float,
    old_strike: Optional[float] = None,
) -> dict:
    """
    Yield for a calendar or diagonal roll.

    new_premium: per-share premium received on the new short
    new_strike: strike of the new short
    new_dte: DTE of the new short
    contracts: spread count
    spot: current underlying price
    net_credit_dollars: net cash flow from the roll (positive = credit, negative = debit)
    position_value: dollars at risk on the underlying (e.g. shares × spot for CC)
    old_strike: the strike being rolled FROM (for cap-buffer math)
    """
    qty = abs(int(contracts))
    new_collateral = new_strike * 100 * qty
    new_total_premium = new_premium * 100 * qty
    new_leg_yield = _safe_div(new_total_premium, new_collateral) * 100
    new_leg_yield_ann = _annualize(new_leg_yield, new_dte)

    # Net-cash yield: just the trade's incremental cash impact
    net_cash_yield = _safe_div(net_credit_dollars, position_value) * 100
    net_cash_yield_ann = _annualize(net_cash_yield, new_dte)

    # Headroom (new strike vs spot)
    cap_buffer_pct = _safe_div(new_strike - spot, spot) * 100

    # Roll archetype detection
    if old_strike is not None and abs(new_strike - old_strike) < 0.01:
        kind = "calendar_roll"
        cap_buffer_change_pct = 0.0
        cost_per_dollar_of_protection = None
    else:
        kind = "diagonal_roll"
        cap_buffer_change_pct = _safe_div(new_strike - (old_strike or new_strike), spot) * 100
        if old_strike is not None and new_strike > old_strike and net_credit_dollars < 0:
            # Diagonal up with debit: cost per $1 of new strike room
            new_room = (new_strike - old_strike) * 100 * qty
            cost_per_dollar_of_protection = abs(net_credit_dollars) / new_room if new_room else None
        else:
            cost_per_dollar_of_protection = None

    return {
        "kind": kind,
        "headline_yield_pct": new_leg_yield_ann,
        "all_yields": {
            "new_leg_yield_ann_pct": new_leg_yield_ann,
            "net_cash_yield_ann_pct": net_cash_yield_ann,
            "cap_buffer_pct": cap_buffer_pct,
            "cap_buffer_change_pct": cap_buffer_change_pct,
        },
        "new_collateral": new_collateral,
        "new_premium_dollars": new_total_premium,
        "net_credit_dollars": net_credit_dollars,
        "cost_per_dollar_of_protection": cost_per_dollar_of_protection,
        "notes": (
            "new_leg_yield = forward-looking yield if held to new expiration. "
            "net_cash_yield = today's incremental return from doing the roll."
        ),
    }


def compute_collar_yield(
    call_premium: float,
    put_premium: float,
    call_strike: float,
    put_strike: float,
    spot: float,
    contracts: int,
    dte: int,
) -> dict:
    """
    Collar = long stock + short call + long put.
    Net premium = call_premium received - put_premium paid.
    """
    qty = abs(int(contracts))
    position_value = spot * 100 * qty
    net_premium_per_share = call_premium - put_premium
    net_premium_dollars = net_premium_per_share * 100 * qty
    combined_static = _safe_div(net_premium_dollars, position_value) * 100
    combined_static_ann = _annualize(combined_static, dte)

    cap_ceiling_pct = _safe_div(call_strike - spot, spot) * 100
    floor_pct = _safe_div(spot - put_strike, spot) * 100

    insurance_cost_dollars = put_premium * 100 * qty
    insurance_cost_pct = _safe_div(insurance_cost_dollars, position_value) * 100
    insurance_cost_ann_pct = _annualize(insurance_cost_pct, dte)

    return {
        "kind": "collar",
        "headline_yield_pct": combined_static_ann,
        "all_yields": {
            "combined_static_yield_pct": combined_static,
            "combined_static_yield_ann_pct": combined_static_ann,
            "cap_ceiling_pct": cap_ceiling_pct,
            "floor_pct": floor_pct,
            "insurance_cost_ann_pct": insurance_cost_ann_pct,
        },
        "net_premium_dollars": net_premium_dollars,
        "insurance_cost_dollars": insurance_cost_dollars,
        "position_value": position_value,
        "notes": (
            "Combined yield = (call premium − put premium) / position value. "
            "Floor% = max downside loss before put pays. "
            "Cap% = max upside before call assigns."
        ),
    }


def compute_hedge_yield(
    put_cost_dollars: float,
    contracts: int,
    strike: float,
    spot: float,
    dte: int,
    nlv: float,
    delta: float = -0.20,
) -> dict:
    """
    Standalone long-put hedge (e.g., SPY puts on portfolio).
    Yield-style metrics: protection ratio (insurance leverage), cost as %% of NLV.
    """
    qty = abs(int(contracts))
    # Protected notional = |delta| × 100 × contracts × strike (effective short delta-shares × spot)
    protected_notional = abs(delta) * 100 * qty * strike
    protection_ratio = _safe_div(protected_notional, put_cost_dollars)
    cost_pct_nlv = _safe_div(put_cost_dollars, nlv) * 100
    cost_pct_nlv_ann = _annualize(cost_pct_nlv, dte)
    otm_pct = _safe_div(spot - strike, spot) * 100  # positive = strike below spot (OTM put)

    return {
        "kind": "hedge",
        "headline_yield_pct": -cost_pct_nlv_ann,  # negative because it's a cost
        "all_yields": {
            "protection_ratio": protection_ratio,
            "cost_pct_nlv": cost_pct_nlv,
            "cost_pct_nlv_ann": cost_pct_nlv_ann,
            "strike_otm_pct": otm_pct,
        },
        "protected_notional": protected_notional,
        "put_cost_dollars": put_cost_dollars,
        "notes": (
            "Hedges are net cost (negative yield). Protection ratio = "
            "$ of effective downside coverage per $ of premium paid."
        ),
    }


def compute_close_yield(
    entry_price: float,
    current_mid: float,
    strike: float,
    contracts: int,
    days_held: int,
    days_to_expiry: int,
    is_short: bool = True,
) -> dict:
    """
    Yield on an early close (taking profit before expiration).
    Captures % of max profit and annualizes the return on collateral.
    """
    qty = abs(int(contracts))
    collateral = strike * 100 * qty
    if is_short:
        profit_per_share = entry_price - current_mid  # short profits when price drops
    else:
        profit_per_share = current_mid - entry_price  # long profits when price rises
    profit_dollars = profit_per_share * 100 * qty
    realized_pct_of_max = _safe_div(profit_per_share, entry_price) * 100 if entry_price > 0 else 0
    annualized_capture = _safe_div(profit_dollars, collateral) * 100
    annualized_capture_ann = _annualize(annualized_capture, max(days_held, 1))

    return {
        "kind": "close",
        "headline_yield_pct": annualized_capture_ann,
        "all_yields": {
            "realized_pct_of_max": realized_pct_of_max,
            "annualized_capture_pct": annualized_capture_ann,
        },
        "profit_dollars": profit_dollars,
        "collateral_freed": collateral,
        "days_held": days_held,
        "days_to_expiry_remaining": days_to_expiry,
        "notes": (
            f"{realized_pct_of_max:.0f}% of max profit captured in {days_held}d. "
            f"Annualized return on collateral = {annualized_capture_ann:.1f}%."
        ),
    }


def format_yield_line(yield_result: dict, prefix: str = "Yield") -> str:
    """
    One-line yield summary suitable for embedding in a briefing action item.
    Picks the most important yield variants for each trade kind.
    """
    kind = yield_result.get("kind", "?")
    if kind == "csp":
        y = yield_result["all_yields"]
        return (
            f"{prefix}: **{y['static_yield_ann_pct']:.1f}%** ann. on collateral "
            f"(${yield_result['collateral_dollars']:,.0f}); if assigned, "
            f"{y['if_assigned_yield_ann_pct']:.1f}% on actual basis."
        )
    elif kind == "cc":
        y = yield_result["all_yields"]
        return (
            f"{prefix}: **{y['static_yield_ann_pct']:.1f}%** ann. on share value "
            f"(${yield_result['position_value']:,.0f}); if called, "
            f"{y['if_called_yield_ann_pct']:.1f}% total return."
        )
    elif kind in ("calendar_roll", "diagonal_roll"):
        y = yield_result["all_yields"]
        line = (
            f"{prefix}: **{y['new_leg_yield_ann_pct']:.1f}%** ann. on new collateral "
            f"(${yield_result['new_collateral']:,.0f}); "
            f"net-cash {y['net_cash_yield_ann_pct']:+.1f}% ann. on position."
        )
        if y.get("cap_buffer_pct") is not None:
            line += f" Cap buffer: {y['cap_buffer_pct']:+.1f}% above spot."
        return line
    elif kind == "collar":
        y = yield_result["all_yields"]
        return (
            f"{prefix}: **{y['combined_static_yield_ann_pct']:+.1f}%** ann. combined "
            f"(call premium net of put cost). Floor at {y['floor_pct']:.1f}% downside, "
            f"cap at {y['cap_ceiling_pct']:+.1f}% upside."
        )
    elif kind == "hedge":
        y = yield_result["all_yields"]
        return (
            f"{prefix}: cost **{y['cost_pct_nlv']:.2f}% of NLV** ({y['cost_pct_nlv_ann']:.1f}% ann.). "
            f"Protection ratio: **{y['protection_ratio']:.1f}×** on tail-event payoff."
        )
    elif kind == "close":
        y = yield_result["all_yields"]
        return (
            f"{prefix}: **{y['annualized_capture_pct']:.1f}%** ann. on collateral "
            f"({y['realized_pct_of_max']:.0f}% of max profit captured)."
        )
    return f"{prefix}: (unknown trade type {kind})"
