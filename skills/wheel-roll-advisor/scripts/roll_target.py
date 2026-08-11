"""6-step roll target selection pipeline with multi-candidate enumeration."""

from datetime import datetime
from typing import Any, Dict, Optional, List
from dataclasses import dataclass


def _tenor_phrase(dte_ext: int) -> str:
    """Human-friendly tenor phrase derived from the ACTUAL DTE extension —
    never a hardcoded '4-6 weeks' / '10-15 months' label divorced from data.
    """
    if dte_ext <= 0:
        return "same date"
    if dte_ext <= 10:
        return f"+{dte_ext}d"
    if dte_ext <= 60:
        return f"+{dte_ext}d (~{max(1, dte_ext // 7)}w)"
    months = dte_ext / 30.44
    if months < 18:
        return f"+{dte_ext}d (~{months:.0f}mo)"
    return f"+{dte_ext}d (~{months / 12:.1f}yr)"


def _fmt_exp_short(exp: str) -> str:
    """Format YYYY-MM-DD as 'Jul 17 '26' — includes year so a 2-year-out
    candidate can't be confused with a near-term one (e.g. '1215' ambiguous
    between 2026 and 2028 in the old MMDD format)."""
    try:
        return datetime.strptime(exp, "%Y-%m-%d").strftime("%b %d '%y")
    except (ValueError, TypeError):
        return str(exp)


def _is_monthly_expiration(exp: str) -> bool:
    """True iff ``exp`` is a standard equity/ETF monthly (3rd Friday).

    Canonical policy lives in daily-portfolio-briefing
    ``analysis/expiration_policy.py``; this advisor may run as a standalone
    subprocess, so the identical 3rd-Friday math is applied inline when the
    canonical module isn't importable.
    """
    try:
        from analysis.expiration_policy import is_monthly  # type: ignore
        return bool(is_monthly(exp))
    except Exception:
        pass
    from datetime import timedelta
    try:
        d = datetime.strptime(str(exp)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    first = d.replace(day=1)
    days_to_first_friday = (4 - first.weekday()) % 7  # Friday = weekday 4
    return d == first + timedelta(days=days_to_first_friday + 14)


def _prefer_monthly_roll_exp(
    default_exp: Optional[str],
    all_expirations: List[str],
    dte_by_exp: Dict[str, int],
    current_exp: Optional[str],
    current_dte: int,
    max_tenor_days: Optional[int],
) -> Optional[str]:
    """Snap a roll STO-leg target expiration to a nearby standard monthly.

    Expiration policy (2026-08-10): institutional OI/liquidity concentrates
    on 3rd-Friday monthlies → tighter spreads, easier future rolls. Rules:
    - only among the chain's REAL listed expirations (rule #6);
    - never before/at the current expiration (a roll must extend);
    - never past ``max_tenor_days`` over the current DTE (tenor cap — the
      monthly preference NEVER violates it);
    - stay near the legacy pick (within −21d/+35d) so the roll's tenor
      character is preserved;
    - a default that is already monthly is untouched.
    Falls back to ``default_exp`` when no qualifying monthly exists.
    """
    if not default_exp or _is_monthly_expiration(default_exp):
        return default_exp
    default_dte = dte_by_exp.get(default_exp)
    if default_dte is None:
        return default_exp
    best = None
    best_dist = None
    for e in all_expirations:
        if current_exp and e <= current_exp:  # ISO strings sort by date
            continue
        if not _is_monthly_expiration(e):
            continue
        dte = dte_by_exp.get(e)
        if dte is None:
            continue
        if dte < default_dte - 21 or dte > default_dte + 35:
            continue
        if max_tenor_days is not None and (dte - current_dte) > max_tenor_days:
            continue
        dist = abs(dte - default_dte)
        if best_dist is None or dist < best_dist:
            best, best_dist = e, dist
    return best or default_exp


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


def _put_down_step(strike: float) -> float:
    """Chain-appropriate strike-reduction increment for a put roll-down."""
    if strike >= 500:
        return 50.0
    if strike >= 200:
        return 25.0
    if strike >= 50:
        return 10.0
    return 5.0


def _nearest_strike_at_or_below(strikes, target: float) -> Optional[float]:
    """Largest available chain strike at or below target (None if none)."""
    below = [s for s in (strikes or ()) if s <= target + 1e-9]
    return max(below) if below else None


def _nearest_strike_at_or_above(strikes, target: float) -> Optional[float]:
    """Smallest available chain strike at or above target (None if none)."""
    above = [s for s in (strikes or ()) if s >= target - 1e-9]
    return min(above) if above else None


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

    is_call = (option_type or "PUT").upper() == "CALL"
    next_exp = (all_expirations[current_exp_idx + 1]
                if current_exp_idx >= 0 and current_exp_idx + 1 < len(all_expirations) else None)
    later_exp = (all_expirations[current_exp_idx + 3]
                 if current_exp_idx >= 0 and current_exp_idx + 3 < len(all_expirations) else None)

    # Expiration policy (2026-08-10, threaded via advise from the briefing's
    # expiration_policy.prefer_monthly config): snap the OUT-leg targets to a
    # nearby standard monthly (3rd-Friday) among the chain's REAL expirations.
    # Bounded by monthly_snap_max_tenor_days (the same cap the ranker
    # enforces) so the preference never extends past the tenor cap. Absent
    # config → byte-identical legacy selection.
    _ep_cfg = (params.get("expiration_policy") or {}) if isinstance(params, dict) else {}
    if _ep_cfg.get("prefer_monthly"):
        _dte_by_exp: Dict[str, int] = {}
        for _cc in chain.get("candidates", []):
            _e = _cc.get("expirationDate")
            _dd = _cc.get("daysToExpiry")
            if _e and _dd is not None and _e not in _dte_by_exp:
                _dte_by_exp[_e] = int(_dd)
        _cap = params.get("monthly_snap_max_tenor_days")
        _cap = int(_cap) if _cap is not None else None
        next_exp = _prefer_monthly_roll_exp(
            next_exp, all_expirations, _dte_by_exp,
            current_exp, current_dte, _cap)
        later_exp = _prefer_monthly_roll_exp(
            later_exp, all_expirations, _dte_by_exp,
            current_exp, current_dte, _cap)

    candidate_exps = []
    if is_call:
        # ── SHORT CALL (income roll) — unchanged menu ─────────────────────
        # Same strike, next expiration after current
        if next_exp:
            candidate_exps.append(("same_next", next_exp, current_strike))
        # Same strike, ~12mo later
        if later_exp:
            candidate_exps.append(("same_later", later_exp, current_strike))

        # Up $50 (or +5%), same expiration — raising a CALL strike raises the
        # cap and preserves upside (genuinely defensive for the shares).
        strike_up = max(current_strike + 50.0, current_strike * 1.05)

        if current_exp in all_expirations:
            candidate_exps.append(("up_same", current_exp, strike_up))

        # Up strike, later expiration
        if next_exp:
            candidate_exps.append(("up_later", next_exp, strike_up))
    else:
        # ── SHORT PUT — defensive menu: same-strike OUT (time) and
        # DOWN-and-out (strike reduction). A HIGHER strike on a short put is
        # DEEPER ITM = MORE assignment risk and MORE capital at risk — the
        # opposite of defensive (2026-07-29 bug: NVDA $200P spot $190 →
        # "$250P ✅ recommended", VRT $290P spot $223 → "$340P"). Roll-up is
        # generated ONLY behind params["include_bullish_roll_up"] (default
        # off) and labeled as a bullish conviction trade, never a repair.
        step = _put_down_step(current_strike)
        strikes_by_exp: Dict[str, set] = {}
        for chain_cand in chain.get("candidates", []):
            e = chain_cand.get("expirationDate")
            s = chain_cand.get("strikePrice")
            if e and s is not None:
                strikes_by_exp.setdefault(e, set()).add(float(s))

        if next_exp:
            candidate_exps.append(("same_next", next_exp, current_strike))
            # Roll-DOWN-and-out: one and two increments of strike reduction,
            # snapped to strikes that actually exist on the chain.
            down_1 = _nearest_strike_at_or_below(
                strikes_by_exp.get(next_exp), current_strike - step)
            down_2 = _nearest_strike_at_or_below(
                strikes_by_exp.get(next_exp), current_strike - 2 * step)
            if down_1 is not None and down_1 < current_strike:
                candidate_exps.append(("down_next", next_exp, down_1))
            if (down_2 is not None and down_2 < current_strike
                    and (down_1 is None or down_2 < down_1)):
                candidate_exps.append(("down_next_2", next_exp, down_2))
        if later_exp:
            down_later = _nearest_strike_at_or_below(
                strikes_by_exp.get(later_exp), current_strike - step)
            if down_later is not None and down_later < current_strike:
                candidate_exps.append(("down_later", later_exp, down_later))
        if params.get("include_bullish_roll_up") and next_exp:
            up = _nearest_strike_at_or_above(
                strikes_by_exp.get(next_exp), current_strike + step)
            if up is not None and up > current_strike:
                candidate_exps.append(("up_bullish", next_exp, up))

    # Build candidates B-F by searching for matching chain entries
    type_letter = "C" if is_call else "P"
    for idx, (strategy, exp, strike) in enumerate(candidate_exps[:5], start=1):
        cand_letter = chr(ord('A') + idx)  # B, C, D, E, F

        # Find matching candidate in chain
        matching = None
        for chain_cand in chain.get("candidates", []):
            if (abs(float(chain_cand.get("strikePrice") or 0) - strike) < 0.01 and
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

        # STO-leg viability floor on PUT candidates (task #37 fix 3 — the
        # NOK bug: the roll-down selector walked to a $1P at bid $0.00 /
        # mid $0.00, a garbage ticket worse than no ticket, rule #19).
        # Requirements: bid > 0, mid ≥ $0.10, |delta| ≥ 0.05 (when the
        # chain carries deltas), and strike ≥ 40% of spot. A strike that
        # deep below spot collects nothing and hedges nothing.
        if not is_call:
            if bid <= 0 or mid < 0.10:
                continue
            if delta_raw is not None and abs(delta) < 0.05:
                continue
            if underlying_price > 0 and strike < 0.40 * underlying_price:
                continue

        # Calculate net P&L (multiplied by contract size)
        new_credit = bid * qty * OPTION_MULTIPLIER  # what we'd receive for selling to open
        close_cost_total = close_cost * qty * OPTION_MULTIPLIER  # what we'd pay to buy to close
        net_dollars = new_credit - close_cost_total
        dte_ext = max(0, new_dte - current_dte)

        # Description — REAL values from the snapshot, never hardcoded boilerplate:
        #   - qty: actual contract count from the position (was hardcoded "4×")
        #   - type_letter: C/P from the actual option type (was hardcoded "C")
        #   - exp_short: year-disambiguated date (was MMDD which collided across years)
        days_str = f"+{dte_ext}d" if dte_ext > 0 else ""
        exp_short = _fmt_exp_short(exp)
        desc = (
            f"{qty}× ${strike:.0f}{type_letter} {exp_short} @ ${mid:.2f}"
            + (f" {days_str}" if days_str else "")
        )

        # Build instruction (sell to open)
        instruction = {
            "sell_strike": strike,
            "sell_expiration": exp,
            "sell_mid": mid,
            "sell_bid": bid,
            "sell_ask": ask,
        }

        # Notes — phrased from the ACTUAL tenor extension, never a hardcoded
        # "4-6 weeks" / "10-15 months" label divorced from the data — and
        # SIDE-AWARE: call language ("raises the cap, preserves upside") must
        # never render on a put row, where a higher strike means MORE
        # assignment risk, not more upside.
        tenor = _tenor_phrase(dte_ext)
        if strategy == "same_next":
            if is_call:
                notes = f"Same strike, extend {tenor} — low delta risk"
            else:
                notes = (f"Same strike, extend {tenor} — adds recovery time, "
                         f"keeps full assignment risk at ${strike:.0f}")
        elif strategy == "same_later":
            notes = f"Same strike, extend {tenor} — re-caps the name for that long; check if you're OK with the duration"
        elif strategy == "up_same":
            diff = strike - current_strike
            notes = f"Higher strike (+${diff:.0f}), same expiration — raises the cap, preserves more upside"
        elif strategy == "up_later":
            diff = strike - current_strike
            notes = f"Higher strike (+${diff:.0f}), extend {tenor} — raises the cap and adds more time"
        elif strategy in ("down_next", "down_next_2", "down_later"):
            diff = current_strike - strike
            obligation_cut = diff * qty * OPTION_MULTIPLIER
            notes = (f"Lower strike (−${diff:.0f}), extend {tenor} — reduces "
                     f"assignment risk and obligation by ${obligation_cut:,.0f}")
        elif strategy == "up_bullish":
            diff = strike - current_strike
            notes = (f"⚠ BULLISH repair (+${diff:.0f} strike) — increases "
                     f"assignment risk & obligation; only if you actively want "
                     f"more exposure at a higher basis")
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

    DEPRECATED (rule #43, 2026-07-31): this legacy single-target path is
    side-blind (no put/call ranking discipline) and its output keys
    (strikePrice/expirationDate) were misread as strike/expiration by the
    Analyst Brief, rendering "IREN ? $? PUT for $0.40 net credit". Renderers
    now source the TARGET from the ranked ✅ recommended candidate
    (enumerate_roll_candidates + candidate_ranker); this function survives
    only as a last-resort fallback. Do not add new consumers.
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
        cand_dte = c.get("daysToExpiry") or 0
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
        cand_delta = abs(float(c.get("delta") or 0))  # delta may be present-but-None on a chain leg
        if cand_delta <= delta_max:
            delta_filtered.append(c)

    if not delta_filtered:
        return None

    # Step 3: Liquidity filter (OI >= 100, spread <= 5%)
    min_oi = int(params.get("min_oi_for_roll", 100))
    max_spread_pct = float(params.get("max_spread_pct_for_roll", 0.05))

    liquidity_filtered = []
    for c in delta_filtered:
        oi = int(c.get("openInterest") or 0)
        bid = float(c.get("bid") or 0)
        ask = float(c.get("ask") or 0)

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
        bid = float(c.get("bid") or 0)
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
        strike = float(c.get("strikePrice") or 0)
        stress_price = underlying_price * 0.90
        loss_at_stress = max(0, strike - stress_price)
        bid_premium = float(c.get("bid") or 0)
        new_cost = abs(float(position.get("currentMid", 0))) - bid_premium
        net_loss = loss_at_stress + new_cost

        loss_multiple = net_loss / entry_premium if entry_premium > 0 else 0.0
        c["stressTestLossPremiumMultiple"] = loss_multiple

        if loss_multiple <= max_stress_loss_multiple:
            stress_filtered.append(c)

    if not stress_filtered:
        return None

    # Step 6: Strike selection
    target = min(stress_filtered, key=lambda x: abs(abs(x.get("delta") or 0) - delta_target))

    return {
        "strikePrice": target.get("strikePrice"),
        "expirationDate": target.get("expirationDate"),
        "expectedDelta": target.get("delta"),
        "bidAsk": {
            "bid": target.get("bid"),
            "ask": target.get("ask"),
            "mid": (float(target.get("bid") or 0) + float(target.get("ask") or 0)) / 2,
        },
        "expectedNetCredit": target.get("expectedNetCredit"),
        "expectedNetCreditPct": target.get("expectedNetCreditPct"),
        "stressTestLossPremiumMultiple": target.get("stressTestLossPremiumMultiple"),
        "rationale": "Passed all 6 filters: expiration, delta, liquidity, net credit, stress test.",
    }
