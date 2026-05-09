"""
Trade-validator — probability-weighted EV check on every options action.

For diagonal rolls, calendar rolls, CSPs, and collars: compute expected value,
break-even price, implied assignment probability, cost per dollar of protection,
and rank against alternatives. Returns a verdict plus reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class TradeValidation:
    verdict: str  # GOOD | MARGINAL | POOR | BLOCK
    expected_value_dollars: float
    break_even_price: Optional[float]
    implied_assignment_probability: Optional[float]
    cost_per_dollar_of_protection: Optional[float]
    alternatives_ranked: list = field(default_factory=list)
    reasoning: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _verdict_from_ev(ev: float, cost: float) -> str:
    """Map EV-to-cost ratio to a verdict."""
    if cost <= 0:
        # Credit trades: any positive EV is GOOD
        return "GOOD" if ev > 0 else "MARGINAL"
    ratio = ev / cost
    if ratio >= 1.5:
        return "GOOD"
    if ratio >= 0.8:
        return "MARGINAL"
    if ratio >= 0:
        return "POOR"
    return "BLOCK"


def validate_diagonal_up_roll(
    spot: float,
    current_strike: float,
    new_strike: float,
    new_premium: float,
    debit_per_share: float,
    contracts: int,
    new_dte: int,
    new_delta: float = 0.10,
    current_delta: float = 0.30,
) -> TradeValidation:
    """
    Validate a diagonal-up debit roll (raises cap, costs premium).

    The economic question: am I paying a fair price for the cap headroom?
    """
    shares = contracts * 100
    debit_total = abs(debit_per_share) * shares
    protection_dollars = (new_strike - current_strike) * shares
    cost_pct = debit_total / protection_dollars if protection_dollars > 0 else 1.0

    # Implied probability the roll's cap-buffer is USEFUL:
    # P(spot ends between current and new strike) ≈ current_delta - new_delta
    # because delta ≈ P(ITM at expiration)
    prob_buffer_useful = max(0.0, current_delta - new_delta)
    prob_above_new_strike = new_delta  # P(spot above new strike — both caps hit)

    # Expected value calculation:
    # - If spot < current_strike: original would have expired worthless. Roll wasted.
    # - If current_strike < spot < new_strike: protection saved. Gain ≈ shares × (spot - current_strike)
    # - If spot > new_strike: both capped. Saved exactly (new_strike - current_strike) × shares.
    # Approximate the middle region by its midpoint.
    mid_save_per_share = (new_strike - current_strike) / 2  # avg save in the middle band
    ev_middle = prob_buffer_useful * shares * mid_save_per_share
    ev_above = prob_above_new_strike * protection_dollars
    ev_gross = ev_middle + ev_above
    ev_net = ev_gross - debit_total

    break_even_price = current_strike + (debit_total / shares) if shares > 0 else None

    verdict = _verdict_from_ev(ev_net + debit_total, debit_total)

    # Generate alternatives
    alternatives = [
        {
            "name": "HOLD (do nothing)",
            "ev": 0,  # baseline
            "tradeoff": (
                f"Save ${debit_total:,.0f} debit. Risk losing shares to assignment "
                f"if spot > ${current_strike:.0f} by exp ({current_delta*100:.0f}% chance per delta)."
            ),
        },
        {
            "name": f"Diagonal up to ${new_strike:.0f} (current proposal)",
            "ev": ev_net,
            "tradeoff": (
                f"Pay ${debit_total:,.0f} now. Expected gain ~${ev_gross:,.0f} from cap raise; "
                f"net EV = ${ev_net:+,.0f}."
            ),
        },
    ]

    # If the protection cost > 30% of protection value, suggest a cheaper alternative
    if cost_pct > 0.30:
        alternatives.append({
            "name": "Wider diagonal (e.g., higher new strike, lower premium)",
            "ev": None,
            "tradeoff": (
                f"Current cost-per-dollar-of-protection is {cost_pct*100:.0f}%. "
                f"Try a higher new strike for cheaper insurance (lower hit-probability, "
                f"smaller debit)."
            ),
        })

    alternatives.sort(key=lambda a: (a.get("ev") or 0), reverse=True)

    reasoning = (
        f"Diagonal up-and-out roll: pay ${debit_total:,.0f} debit to lift cap from "
        f"${current_strike:.0f} → ${new_strike:.0f}. Break-even at ${break_even_price:.2f}. "
        f"Market-implied probability cap-buffer is useful: {prob_buffer_useful*100:.0f}%. "
        f"EV = ${ev_net:+,.0f}. Cost-per-$1-protection: ${cost_pct:.2f}."
    )

    return TradeValidation(
        verdict=verdict,
        expected_value_dollars=ev_net,
        break_even_price=break_even_price,
        implied_assignment_probability=new_delta,
        cost_per_dollar_of_protection=cost_pct,
        alternatives_ranked=alternatives,
        reasoning=reasoning,
    )


def validate_calendar_roll(
    spot: float,
    strike: float,
    new_premium: float,
    credit_per_share: float,
    contracts: int,
    new_dte: int,
    delta: float = 0.30,
) -> TradeValidation:
    """
    Validate a calendar roll (same strike, longer DTE) — typically a credit trade.

    Question: is the forward yield attractive vs alternatives?
    """
    shares = contracts * 100
    credit_total = credit_per_share * shares
    collateral = strike * shares
    forward_yield = (new_premium * shares / collateral) * (365 / new_dte) if new_dte else 0
    ev_if_otm = credit_total  # if OTM at exp, collect full premium
    prob_otm = 1 - delta
    ev_expected = prob_otm * credit_total - delta * (credit_total * 0.5)  # rough
    verdict = "GOOD" if ev_expected > 0 and forward_yield > 0.08 else "MARGINAL"
    reasoning = (
        f"Calendar roll: ${credit_total:,.0f} credit; forward yield {forward_yield*100:.1f}% ann. "
        f"Implied OTM probability {prob_otm*100:.0f}%."
    )
    alternatives = [
        {"name": "HOLD", "ev": 0, "tradeoff": "Let original expire; no incremental income but no commitment."},
        {"name": "This calendar roll", "ev": ev_expected, "tradeoff": f"Earn ${credit_total:,.0f} over {new_dte}d."},
    ]
    return TradeValidation(
        verdict=verdict,
        expected_value_dollars=ev_expected,
        break_even_price=None,
        implied_assignment_probability=delta,
        cost_per_dollar_of_protection=None,
        alternatives_ranked=alternatives,
        reasoning=reasoning,
    )


def validate_csp(
    spot: float,
    strike: float,
    premium: float,
    contracts: int,
    dte: int,
    delta: float = 0.20,
    drawdown_basis: Optional[float] = None,
) -> TradeValidation:
    """
    Validate a cash-secured put — typically a credit trade with assignment risk.

    Question: is the premium worth the assignment exposure?
    """
    shares = contracts * 100
    credit_total = premium * shares
    collateral = strike * shares
    prob_otm = 1 - abs(delta)  # rough proxy
    if_assigned_loss = max(0, (strike - premium) - (drawdown_basis or strike * 0.95)) * shares
    ev_expected = prob_otm * credit_total - (1 - prob_otm) * if_assigned_loss

    static_yield = (premium / strike) * (365 / dte) if dte else 0
    break_even_price = strike - premium  # below this, you lose money on assignment

    if ev_expected > credit_total * 0.5:
        verdict = "GOOD"
    elif ev_expected > 0:
        verdict = "MARGINAL"
    else:
        verdict = "POOR"

    reasoning = (
        f"CSP: ${credit_total:,.0f} premium; static yield {static_yield*100:.1f}% ann.; "
        f"break-even at ${break_even_price:.2f} ({((break_even_price/spot)-1)*100:+.1f}% from spot). "
        f"Implied probability of OTM expiry: {prob_otm*100:.0f}%."
    )

    alternatives = [
        {"name": "HOLD cash (no CSP)", "ev": 0, "tradeoff": "Keep cash idle; no income."},
        {"name": "This CSP", "ev": ev_expected, "tradeoff": f"Earn ${credit_total:,.0f} or buy at ${strike:.0f}."},
    ]

    return TradeValidation(
        verdict=verdict,
        expected_value_dollars=ev_expected,
        break_even_price=break_even_price,
        implied_assignment_probability=abs(delta),
        cost_per_dollar_of_protection=None,
        alternatives_ranked=alternatives,
        reasoning=reasoning,
    )


def validate_collar(
    spot: float,
    call_strike: float,
    put_strike: float,
    call_premium: float,
    put_premium: float,
    contracts: int,
    dte: int,
    call_delta: float = 0.20,
    put_delta: float = -0.15,
) -> TradeValidation:
    """
    Validate a collar (long stock + short call + long put).
    Question: is the floor worth the cap?
    """
    shares = contracts * 100
    net_premium_per_share = call_premium - put_premium  # negative = collar costs money
    net_premium_dollars = net_premium_per_share * shares

    floor_distance = spot - put_strike  # how far before put pays
    cap_distance = call_strike - spot  # how far before call assigns

    # EV: P(put pays) × put_strike_protection + P(call caps) × cap_loss + P(neither) × net_premium
    prob_put_pays = abs(put_delta)
    prob_call_caps = call_delta
    prob_neither = max(0, 1 - prob_put_pays - prob_call_caps)
    ev_neither = prob_neither * net_premium_dollars
    ev_put_pays = prob_put_pays * (floor_distance * 0.5 * shares)  # rough
    ev_call_caps = -prob_call_caps * (cap_distance * 0.5 * shares)
    ev_total = ev_neither + ev_put_pays + ev_call_caps

    verdict = "GOOD" if ev_total > abs(net_premium_dollars) * 0.5 else "MARGINAL"

    reasoning = (
        f"Collar: floor at ${put_strike:.0f} ({floor_distance/spot*100:.0f}% below spot), "
        f"cap at ${call_strike:.0f} ({cap_distance/spot*100:.0f}% above spot). "
        f"Net premium ${net_premium_dollars:+,.0f}; EV ${ev_total:+,.0f}."
    )

    return TradeValidation(
        verdict=verdict,
        expected_value_dollars=ev_total,
        break_even_price=spot,
        implied_assignment_probability=call_delta,
        cost_per_dollar_of_protection=None,
        alternatives_ranked=[
            {"name": "HOLD shares unhedged", "ev": 0, "tradeoff": f"Full upside, full downside."},
            {"name": "This collar", "ev": ev_total, "tradeoff": f"Floor + cap for ${net_premium_dollars:+,.0f}."},
        ],
        reasoning=reasoning,
    )


def format_validation_line(v: TradeValidation) -> str:
    """One-line summary suitable for embedding in a briefing action."""
    badge = {
        "GOOD": "✅ GOOD TRADE",
        "MARGINAL": "⚠️ MARGINAL",
        "POOR": "🔴 POOR EV",
        "BLOCK": "🚫 BLOCKED — negative EV",
    }.get(v.verdict, "?")

    parts = [f"**Trade-validator:** {badge}"]
    if v.expected_value_dollars is not None:
        parts.append(f"EV ${v.expected_value_dollars:+,.0f}")
    if v.break_even_price is not None:
        parts.append(f"break-even ${v.break_even_price:.2f}")
    if v.implied_assignment_probability is not None:
        parts.append(f"P(assignment) {v.implied_assignment_probability*100:.0f}%")
    if v.cost_per_dollar_of_protection is not None:
        parts.append(f"cost-per-$1-protection ${v.cost_per_dollar_of_protection:.2f}")
    return " — ".join(parts)
