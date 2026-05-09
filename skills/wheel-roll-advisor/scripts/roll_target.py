"""6-step roll target selection pipeline with multi-candidate enumeration."""

from typing import Any, Dict, Optional, List
from dataclasses import dataclass


@dataclass
class RollCandidate:
    """Single roll candidate with pricing and strategy."""
    id: str  # A, B, C, D, E or descriptive
    description: str  # "HOLD", "Same strike +4mo", etc.
    instruction: Optional[Dict[str, Any]]  # None for HOLD; else {sell_strike, sell_exp, sell_mid}
    close_cost: float  # debit to buy back existing (positive = cost)
    new_credit: float  # premium received (positive = credit)
    net_dollars: float  # net after close+open (positive = credit, negative = debit)
    dte_extension: int  # days extended (0 for HOLD)
    delta_change: float  # how delta changes with this roll
    notes: str  # one-line rationale


def _get_all_expirations_from_chain(chain: Dict[str, Any]) -> List[str]:
    """Extract all unique expirations from chain candidates."""
    expirations = set()
    for candidate in chain.get("candidates", []):
        exp = candidate.get("expirationDate")
        if exp:
            expirations.add(exp)
    return sorted(list(expirations))


def enumerate_roll_candidates(
    position: Dict[str, Any],
    chain: Dict[str, Any],
    params: Dict[str, Any],
) -> List[RollCandidate]:
    """Enumerate 4-5 roll candidate strategies with concrete pricing."""

    # Options contract multiplier: 1 contract = 100 shares
    OPTION_MULTIPLIER = 100

    current_dte = int(position.get("daysToExpiry", 0))
    current_strike = float(position.get("strikePrice", 0))
    entry_premium = float(position.get("entryPrice", 0))
    close_cost = float(position.get("currentMid", 0))  # cost to buy back
    iv_rank = float(position.get("ivRank", 45))
    underlying_price = float(position.get("underlyingPrice", 0))
    qty = int(position.get("quantity", 1))
    option_type = position.get("optionType", "PUT")

    candidates = []

    # Always add HOLD as candidate A
    candidates.append(RollCandidate(
        id="A",
        description="HOLD (don't roll)",
        instruction=None,
        close_cost=0.0,
        new_credit=0.0,
        net_dollars=0.0,
        dte_extension=0,
        delta_change=0.0,
        notes="Wait for theta/IV mean-reversion to work",
    ))

    # Get all available expirations
    all_expirations = _get_all_expirations_from_chain(chain)
    if not all_expirations:
        return candidates

    # Identify candidate expirations: next +4mo, +12mo, same date
    current_exp = position.get("expirationDate")
    current_exp_idx = all_expirations.index(current_exp) if current_exp in all_expirations else -1

    candidate_exps = []
    # Same strike, next expiration after current
    if current_exp_idx >= 0 and current_exp_idx + 1 < len(all_expirations):
        candidate_exps.append(("same_next", all_expirations[current_exp_idx + 1], current_strike))
    # Same strike, ~12mo later
    if current_exp_idx >= 0 and current_exp_idx + 3 < len(all_expirations):
        candidate_exps.append(("same_later", all_expirations[current_exp_idx + 3], current_strike))

    # Up $50 (or +5% for puts), same expiration
    strike_up = current_strike + 50.0
    if option_type == "CALL":
        strike_up = max(current_strike + 50.0, current_strike * 1.05)
    else:  # PUT
        strike_up = current_strike + 50.0

    if current_exp in all_expirations:
        candidate_exps.append(("up_same", current_exp, strike_up))

    # Up strike, later expiration
    if current_exp_idx >= 0 and current_exp_idx + 1 < len(all_expirations):
        candidate_exps.append(("up_later", all_expirations[current_exp_idx + 1], strike_up))

    # Build candidates B-E by searching for matching chain entries
    for idx, (strategy, exp, strike) in enumerate(candidate_exps[:4], start=1):
        cand_letter = chr(ord('A') + idx)  # B, C, D, E

        # Find matching candidate in chain
        matching = None
        for chain_cand in chain.get("candidates", []):
            if (abs(float(chain_cand.get("strikePrice", 0)) - strike) < 0.01 and
                chain_cand.get("expirationDate") == exp):
                matching = chain_cand
                break

        if not matching:
            continue  # Skip if no chain data

        bid = float(matching.get("bid") or 0)
        ask = float(matching.get("ask") or 0)
        mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
        new_dte = int(matching.get("daysToExpiry") or 0)
        delta_raw = matching.get("delta")
        delta = float(delta_raw) if delta_raw is not None else 0.0

        # Calculate net P&L (multiplied by contract size)
        new_credit = bid * qty * OPTION_MULTIPLIER  # what we'd receive for selling to open
        close_cost_total = close_cost * qty * OPTION_MULTIPLIER  # what we'd pay to buy to close
        net_dollars = new_credit - close_cost_total
        dte_ext = max(0, new_dte - current_dte)

        # Format description
        days_str = f"+{dte_ext}d" if dte_ext > 0 else ""
        if strike == current_strike:
            desc = f"4× ${current_strike:.0f}C {exp.split('-')[1]}{exp.split('-')[2]} @ ${mid:.2f} {days_str}"
        else:
            desc = f"4× ${strike:.0f}C {exp.split('-')[1]}{exp.split('-')[2]} @ ${mid:.2f} {days_str}"

        # Build instruction (sell to open)
        instruction = {
            "sell_strike": strike,
            "sell_expiration": exp,
            "sell_mid": mid,
            "sell_bid": bid,
            "sell_ask": ask,
        }

        # Determine notes based on strategy
        if strategy == "same_next":
            notes = "Same strike, extend 4-6 weeks (low delta risk)"
        elif strategy == "same_later":
            notes = "Same strike, extend 10-15 months (high theta decay)"
        elif strategy == "up_same":
            notes = "Higher strike, same date (reduces concentration)"
        elif strategy == "up_later":
            notes = "Higher strike, later date (max time + concentration fix)"
        else:
            notes = ""

        candidates.append(RollCandidate(
            id=cand_letter,
            description=desc,
            instruction=instruction,
            close_cost=close_cost_total,
            new_credit=new_credit,
            net_dollars=net_dollars,
            dte_extension=dte_ext,
            delta_change=delta,
            notes=notes,
        ))

    return candidates


def select_roll_target(
    position: Dict[str, Any],
    chain: Dict[str, Any],
    params: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """6-step pipeline: expiration, delta, liquidity, net credit, stress test, strike.

    Returns the single best target (for backward compatibility).
    For multi-candidate enumeration, use enumerate_roll_candidates().
    """

    current_dte = int(position.get("daysToExpiry", 0))
    entry_premium = float(position.get("entryPrice", 0))
    iv_rank = float(position.get("ivRank", 45))

    candidates = chain.get("candidates", [])
    if not candidates:
        return None

    # Step 1: Filter by expiration (>= min DTE from current)
    min_dte_for_roll = int(params.get("min_dte_for_roll", 21))

    filtered = []
    for c in candidates:
        cand_dte = c.get("daysToExpiry", 0)
        if cand_dte >= min_dte_for_roll:
            filtered.append(c)

    if not filtered:
        return None

    # Step 2: Delta filter (IV-adaptive)
    high_iv_threshold = float(params.get("high_iv_threshold", 60))
    delta_target_high_iv = float(params.get("delta_target_high_iv", 0.16))
    delta_target_normal_iv = float(params.get("delta_target_normal_iv", 0.22))
    delta_max_high_iv = float(params.get("delta_max_high_iv", 0.22))
    delta_max_normal_iv = float(params.get("delta_max_normal_iv", 0.30))

    if iv_rank >= high_iv_threshold:
        delta_target = delta_target_high_iv
        delta_max = delta_max_high_iv
    else:
        delta_target = delta_target_normal_iv
        delta_max = delta_max_normal_iv

    delta_filtered = []
    for c in filtered:
        cand_delta = abs(float(c.get("delta", 0)))
        if cand_delta <= delta_max:
            delta_filtered.append(c)

    if not delta_filtered:
        return None

    # Step 3: Liquidity filter (OI >= 100, spread <= 5%)
    min_oi = int(params.get("min_oi_for_roll", 100))
    max_spread_pct = float(params.get("max_spread_pct_for_roll", 0.05))

    liquidity_filtered = []
    for c in delta_filtered:
        oi = int(c.get("openInterest", 0))
        bid = float(c.get("bid", 0))
        ask = float(c.get("ask", 0))

        if oi >= min_oi and bid > 0 and ask > 0:
            mid = (bid + ask) / 2
            spread_pct = (ask - bid) / mid if mid > 0 else 1.0
            if spread_pct <= max_spread_pct:
                liquidity_filtered.append(c)

    if not liquidity_filtered:
        return None

    # Step 4: Net credit filter (>= 10% of original or >= $0.25)
    min_net_credit_pct = float(params.get("min_net_credit_pct", 0.10))
    min_net_credit_abs = float(params.get("min_net_credit_abs", 0.25))

    credit_filtered = []
    for c in liquidity_filtered:
        bid = float(c.get("bid", 0))
        close_cost = abs(float(position.get("currentMid", 0)))
        net_credit = bid - close_cost
        net_credit_pct = net_credit / entry_premium if entry_premium > 0 else 0.0

        if net_credit >= min_net_credit_abs or net_credit_pct >= min_net_credit_pct:
            c["expectedNetCredit"] = net_credit
            c["expectedNetCreditPct"] = net_credit_pct
            credit_filtered.append(c)

    if not credit_filtered:
        return None

    # Step 5: Stress test (loss at -10% <= 3.0x original premium)
    max_stress_loss_multiple = float(params.get("max_stress_loss_multiple", 3.0))
    underlying_price = float(position.get("underlyingPrice", 0))

    stress_filtered = []
    for c in credit_filtered:
        strike = float(c.get("strikePrice", 0))
        stress_price = underlying_price * 0.90
        loss_at_stress = max(0, strike - stress_price)
        bid_premium = float(c.get("bid", 0))
        new_cost = abs(float(position.get("currentMid", 0))) - bid_premium
        net_loss = loss_at_stress + new_cost

        loss_multiple = net_loss / entry_premium if entry_premium > 0 else 0.0
        c["stressTestLossPremiumMultiple"] = loss_multiple

        if loss_multiple <= max_stress_loss_multiple:
            stress_filtered.append(c)

    if not stress_filtered:
        return None

    # Step 6: Strike selection
    target = min(stress_filtered, key=lambda x: abs(abs(x.get("delta", 0)) - delta_target))

    return {
        "strikePrice": target.get("strikePrice"),
        "expirationDate": target.get("expirationDate"),
        "expectedDelta": target.get("delta"),
        "bidAsk": {
            "bid": target.get("bid"),
            "ask": target.get("ask"),
            "mid": (float(target.get("bid", 0)) + float(target.get("ask", 0))) / 2,
        },
        "expectedNetCredit": target.get("expectedNetCredit"),
        "expectedNetCreditPct": target.get("expectedNetCreditPct"),
        "stressTestLossPremiumMultiple": target.get("stressTestLossPremiumMultiple"),
        "rationale": "Passed all 6 filters: expiration, delta, liquidity, net credit, stress test.",
    }
