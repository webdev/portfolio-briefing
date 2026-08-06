"""Pre-matrix guardrails that override any matrix decision."""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Set
from datetime import datetime, date

try:
    from decision_walker import Decision
except ImportError:
    from .decision_walker import Decision


@dataclass
class GuardrailResult:
    """Result of guardrail check."""
    fired: bool
    decision: Optional[Decision] = None


def check_loss_stop(
    position: Dict[str, Any],
    params: Dict[str, Any],
) -> GuardrailResult:
    """Check loss-stop guardrail: 2.0x for monthly, 1.5x for weekly.
    
    Loss ratio = current_mid / entry (so 2.0 means loss of 2x the premium).
    If current_mid > 2x entry, position is at max loss.
    """
    
    dte = int(position.get("daysToExpiry", 0))
    weekly_dte_threshold = float(params.get("weekly_put_dte_threshold", 10))
    
    entry = float(position.get("entryPrice", 0))
    current_mid = float(position.get("currentMid", 0))
    
    if entry <= 0 or current_mid <= entry:
        # Not in a loss or no entry price
        return GuardrailResult(fired=False)
    
    loss_ratio = current_mid / entry
    
    if dte <= weekly_dte_threshold:
        threshold = float(params.get("loss_stop_weekly", 1.5))
    else:
        threshold = float(params.get("loss_stop_monthly", 2.0))
    
    if loss_ratio >= threshold:
        return GuardrailResult(
            fired=True,
            decision=Decision(
                decision="CLOSE",
                matrix_cell="GUARDRAIL_LOSS_STOP",
                rationale=f"Loss stop triggered: loss ratio {loss_ratio:.2f}x >= {threshold}x",
                warnings=["loss_stop_fired"],
            ),
        )
    
    return GuardrailResult(fired=False)


def check_crash_stop(
    position: Dict[str, Any],
) -> GuardrailResult:
    """Check for intraday crash (>15% drop)."""
    
    day_change_pct = float(position.get("dayChange", 0))
    
    if day_change_pct <= -15.0:
        entry = float(position.get("entryPrice", 0))
        current_mid = float(position.get("currentMid", 0))
        profit_pct = max(0, (entry - current_mid) / entry) if entry > 0 else 0.0
        decision_type = "CLOSE_FOR_PROFIT" if profit_pct >= 0.3 else "CLOSE"
        
        return GuardrailResult(
            fired=True,
            decision=Decision(
                decision=decision_type,
                matrix_cell="GUARDRAIL_CRASH_STOP",
                rationale=f"Crash stop: intraday drop {day_change_pct:.1f}%",
                warnings=["crash_stop_fired"],
            ),
        )
    
    return GuardrailResult(fired=False)


def check_smart_take_profit(
    position: Dict[str, Any],
    params: Dict[str, Any],
) -> GuardrailResult:
    """Smart take-profit rule — the "optimize gains without crazy risk" gate.

    Task #49 answer to user (2026-07-01): "closing at 32% is wasteful,
    optimize for gains, but don't want crazy risk."

    Three-layer logic — each layer is a decision hook:

    1. **Time-adjusted early close** — close a trade that's going
       exceptionally fast. Rule: close when captured >= 2× the linear
       decay expected for time elapsed. E.g., 40% capture in 10 days of
       a 60-DTE trade (17% duration elapsed) means 40 / (17 × 2) = 1.18×
       expected — fire CLOSE. Rewards fast wins; ignores slow trades.

    2. **Gamma-escape** — if DTE < gamma_escape_dte (default 10 days)
       AND captured >= gamma_escape_min_profit (default 0.30), CLOSE.
       Locks in the win before the last-week gamma-risk zone eats it.

    3. **Hard ceiling** — if captured >= hard_ceiling_pct (default 0.85),
       CLOSE unconditionally. The remaining premium isn't worth waiting
       for; gamma risk grows exponentially near max profit.

    All three fire as CLOSE_FOR_PROFIT so the summary line reads
    "💰 CLOSE (profit)" in the UI. Returns HOLD when none fire — the
    matrix walker then applies the standard cells.
    """
    entry = float(position.get("entryPrice", 0))
    current_mid = float(position.get("currentMid", 0))
    dte = int(position.get("daysToExpiry", 0))
    initial_dte = int(position.get("initialDte", 0)) or int(position.get("totalDte", 0))

    if entry <= 0 or current_mid < 0:
        return GuardrailResult(fired=False)

    profit_pct = (entry - current_mid) / entry
    if profit_pct <= 0:
        return GuardrailResult(fired=False)

    # Layer 3: HARD CEILING — always close near max profit
    hard_ceiling = float(params.get("hard_ceiling_pct", 0.85))
    if profit_pct >= hard_ceiling:
        return GuardrailResult(
            fired=True,
            decision=Decision(
                decision="CLOSE_FOR_PROFIT",
                matrix_cell="GUARDRAIL_HARD_CEILING",
                rationale=(
                    f"Hard-ceiling close — captured {profit_pct*100:.0f}% "
                    f"(≥{hard_ceiling*100:.0f}%). Remaining premium not worth "
                    f"gamma risk."
                ),
                warnings=["hard_ceiling_fired"],
            ),
        )

    # Layer 2: GAMMA ESCAPE — close in the last week
    gamma_dte = int(params.get("gamma_escape_dte", 10))
    gamma_min = float(params.get("gamma_escape_min_profit", 0.30))
    if dte <= gamma_dte and profit_pct >= gamma_min:
        return GuardrailResult(
            fired=True,
            decision=Decision(
                decision="CLOSE_FOR_PROFIT",
                matrix_cell="GUARDRAIL_GAMMA_ESCAPE",
                rationale=(
                    f"Gamma-escape close — DTE {dte} ≤ {gamma_dte}, "
                    f"captured {profit_pct*100:.0f}%. Lock the win before "
                    f"the gamma-risk zone."
                ),
                warnings=["gamma_escape_fired"],
            ),
        )

    # Layer 1: TIME-ADJUSTED — close fast winners
    if initial_dte > 0 and dte < initial_dte:
        days_elapsed = initial_dte - dte
        pct_elapsed = days_elapsed / initial_dte
        # Multiplier — fire when profit ≥ multiplier × linear expected decay
        multiplier = float(params.get("time_adjusted_multiplier", 2.0))
        expected_linear = pct_elapsed * multiplier
        # Only relevant when the trade has been alive long enough to matter
        min_days_elapsed = int(params.get("time_adjusted_min_days_elapsed", 3))
        min_profit_floor = float(params.get("time_adjusted_min_profit", 0.30))
        if (days_elapsed >= min_days_elapsed
            and profit_pct >= min_profit_floor
            and profit_pct >= expected_linear):
            return GuardrailResult(
                fired=True,
                decision=Decision(
                    decision="CLOSE_FOR_PROFIT",
                    matrix_cell="GUARDRAIL_TIME_ADJUSTED",
                    rationale=(
                        f"Time-adjusted close — captured {profit_pct*100:.0f}% "
                        f"in {days_elapsed}d of {initial_dte}d "
                        f"({pct_elapsed*100:.0f}% duration elapsed). Trade is "
                        f"{profit_pct/max(pct_elapsed, 0.01):.1f}× faster than "
                        f"linear — lock the win, redeploy the collateral."
                    ),
                    warnings=["time_adjusted_fired"],
                ),
            )

    return GuardrailResult(fired=False)


def check_strike_tested(
    position: Dict[str, Any],
    params: Dict[str, Any],
) -> GuardrailResult:
    """STRIKE TESTED trigger (task #38) — fire AT the strike, not through it.

    The gap this closes (real money, 2026-07-22): MU $950P with spot $959
    (0.9% above strike, +12% capture, 121 DTE) matched NO matrix cell —
    the NEAR_ATM roll cell required 30-50% profit — and fell to
    DEFAULT_HOLD. One week later the put was $211 ITM and the same roll
    cost a $4,700 debit. The at-the-money moment is when extrinsic peaks
    and credit rolls are biggest; this guardrail makes that moment loud.

    Fires for SHORT PUTs when ALL of:
      - |delta| ≥ delta_trigger (default 0.45), OR delta unavailable AND
        spot within price_band_pct (default 3%) of strike, either side
      - DTE ≥ min_dte (default 21) — below that, the briefing's forced-
        decision item (Part 3) owns the moment
      - profit captured < max_profit_to_fire (default 30%) — at ≥30%
        capture the take-profit logic owns the decision
      - NOT already deep ITM (spot more than deep_itm_pct below strike,
        default 10%) — deep ITM is the existing ITM cells' territory

    Emits ROLL_OUT_AND_DOWN (the canonical down-and-out tag every
    downstream surface already recognizes) with cell
    GUARDRAIL_STRIKE_TESTED. Config block: wheel_parameters.yaml →
    strike_tested (flattened by matrix_loader like every other block).
    """
    st = params.get("strike_tested") or {}
    lookup = {**params, **st} if isinstance(st, dict) else params

    if not bool(lookup.get("enabled", True)):
        return GuardrailResult(fired=False)

    # SHORT PUTs only — covered calls have their own CALL_* cells.
    position_type = str(position.get("positionType", "")).upper()
    option_type = str(position.get("optionType", "")).upper()
    if "SHORT" not in position_type or "PUT" not in (option_type or position_type):
        return GuardrailResult(fired=False)

    strike = float(position.get("strikePrice", 0) or 0)
    spot = float(position.get("underlyingPrice", 0) or 0)
    dte = int(position.get("daysToExpiry", 0) or 0)
    entry = float(position.get("entryPrice", 0) or 0)
    current_mid = float(position.get("currentMid", 0) or 0)

    min_dte = int(lookup.get("min_dte", 21))
    if dte < min_dte:
        return GuardrailResult(fired=False)

    # Profit gate — take-profit logic owns ≥ max_profit_to_fire captures.
    # Underwater (negative profit) positions DO fire — the less profit at
    # test, the more urgent the credit roll.
    max_profit = float(lookup.get("max_profit_to_fire", 0.30))
    profit_pct = (entry - current_mid) / entry if entry > 0 else 0.0
    if profit_pct >= max_profit:
        return GuardrailResult(fired=False)

    # Deep-ITM gate — spot more than deep_itm_pct below strike belongs to
    # the ITM matrix cells (roll-down economics are different there).
    deep_itm_pct = float(lookup.get("deep_itm_pct", 0.10))
    if strike > 0 and spot > 0 and (strike - spot) / strike > deep_itm_pct:
        return GuardrailResult(fired=False)

    # Trigger: measured delta first; price-band fallback ONLY when the
    # delta is unavailable (never fabricate — CLAUDE.md #19).
    delta_trigger = float(lookup.get("delta_trigger", 0.45))
    band = float(lookup.get("price_band_pct", 0.03))
    raw_delta = position.get("delta")
    tested = False
    trigger_desc = ""
    if raw_delta is not None:
        try:
            abs_delta = abs(float(raw_delta))
        except (TypeError, ValueError):
            abs_delta = None
        if abs_delta is not None and abs_delta >= delta_trigger:
            tested = True
            trigger_desc = f"δ {abs_delta:.2f} ≥ {delta_trigger:.2f}"
    elif strike > 0 and spot > 0 and abs(spot - strike) / strike <= band:
        tested = True
        trigger_desc = (
            f"spot ${spot:,.2f} within {band*100:.0f}% of strike "
            f"${strike:,.2f} (no chain delta)"
        )

    if not tested:
        return GuardrailResult(fired=False)

    return GuardrailResult(
        fired=True,
        decision=Decision(
            decision="ROLL_OUT_AND_DOWN",
            matrix_cell="GUARDRAIL_STRIKE_TESTED",
            rationale=(
                f"🎯 Strike tested ({trigger_desc}) with {profit_pct*100:.0f}% "
                f"captured and {dte} DTE — credit-roll window open; roll "
                f"down-and-out while extrinsic is at its peak. The less "
                f"profit at test, the more urgent."
            ),
            warnings=["strike_tested_fired"],
        ),
    )


def check_open_order(context: Dict[str, Any]) -> GuardrailResult:
    """Check if there's already an open order pending."""
    
    if context.get("existingOpenOrder", False):
        return GuardrailResult(
            fired=True,
            decision=Decision(
                decision="WAIT",
                matrix_cell="GUARDRAIL_OPEN_ORDER",
                rationale="Open order already pending. Wait for execution or cancellation.",
                warnings=["open_order_pending"],
            ),
        )
    
    return GuardrailResult(fired=False)


def check_earnings_imminent(
    position: Dict[str, Any],
    underlying: Dict[str, Any],
    params: Dict[str, Any],
) -> GuardrailResult:
    """Check earnings imminent: if earnings within window AND DTE <= max AND profit >= threshold."""
    
    next_earnings = underlying.get("nextEarnings")
    if not next_earnings:
        return GuardrailResult(fired=False)
    
    dte = int(position.get("daysToExpiry", 0))
    entry = float(position.get("entryPrice", 0))
    current_mid = float(position.get("currentMid", 0))
    
    earnings_guard_dte_max = int(params.get("earnings_guard_dte_max", 30))
    earnings_guard_window_days = int(params.get("earnings_guard_window_days", 7))
    earnings_guard_profit_pct = float(params.get("earnings_guard_profit_pct", 0.50))
    
    if dte > earnings_guard_dte_max:
        return GuardrailResult(fired=False)
    
    # Parse earnings date
    try:
        if isinstance(next_earnings, str):
            earnings_date = datetime.strptime(next_earnings, "%Y-%m-%d").date()
        else:
            earnings_date = next_earnings
        
        today = date.today()
        days_to_earnings = (earnings_date - today).days
        
        if 0 < days_to_earnings <= earnings_guard_window_days:
            profit_pct = max(0, (entry - current_mid) / entry) if entry > 0 else 0.0
            
            if profit_pct >= earnings_guard_profit_pct:
                return GuardrailResult(
                    fired=True,
                    decision=Decision(
                        decision="CLOSE_FOR_PROFIT",
                        matrix_cell="GUARDRAIL_EARNINGS_IMMINENT",
                        rationale=f"Earnings in {days_to_earnings}d, DTE={dte}, profit={profit_pct:.0%}. Close.",
                        warnings=["earnings_imminent"],
                    ),
                )
    except:
        pass
    
    return GuardrailResult(fired=False)


def check_tail_risk(
    position: Dict[str, Any],
    decision: Decision,
    tail_risk_names: Dict[str, Set[str]],
) -> GuardrailResult:
    """Override ROLL_* decisions to CLOSE for tail-risk names."""

    symbol = position.get("symbol", "").upper()

    # Check if symbol is in any tail-risk category
    for category, names in tail_risk_names.items():
        if symbol in names:
            if decision.decision.startswith("ROLL_"):
                return GuardrailResult(
                    fired=True,
                    decision=Decision(
                        decision="CLOSE",
                        matrix_cell="GUARDRAIL_TAIL_RISK",
                        rationale=f"Tail-risk name ({category}): don't roll, take profits or close.",
                        warnings=["tail_risk_override"],
                    ),
                )
            break

    return GuardrailResult(fired=False)


def check_leap_otm_hold_override(
    position: Dict[str, Any],
    decision: Decision,
    state: Any = None,  # Optional DerivedState for context
) -> GuardrailResult:
    """Override ROLL_* decisions to HOLD for LEAP OTM covered calls.

    LEAP (DTE > 180) OTM options should wait for theta + IV mean-reversion,
    not force a roll that locks in unrealized losses.
    """

    dte = int(position.get("daysToExpiry", 0))
    if dte <= 180:
        return GuardrailResult(fired=False)

    # Check if position is OTM via state or position fields
    if state and hasattr(state, 'moneyness'):
        is_otm = state.moneyness in ["DEEP_OTM", "MODERATE_OTM"]
    else:
        # Fallback: use delta-based heuristic
        option_type = position.get("optionType", "")
        delta = float(position.get("delta", 0))
        if option_type == "CALL" and delta < 0.35:
            is_otm = True
        elif option_type == "PUT" and delta > -0.35:
            is_otm = True
        else:
            is_otm = False

    if is_otm and decision.decision.startswith("ROLL_"):
        entry_price = float(position.get("entryPrice", 0))
        current_mid = float(position.get("currentMid", 0))
        unrealized_pct = max(0, (entry_price - current_mid) / entry_price) if entry_price > 0 else 0.0

        return GuardrailResult(
            fired=True,
            decision=Decision(
                decision="HOLD",
                matrix_cell="GUARDRAIL_LEAP_OTM_HOLD_OVERRIDE",
                rationale=f"LEAP OTM ({dte} DTE): let theta & IV mean-reversion work. "
                         f"Don't lock in {unrealized_pct:.1%} unrealized loss.",
                warnings=["leap_override_applied"],
            ),
        )

    return GuardrailResult(fired=False)


def run_pre_matrix_guardrails(
    position: Dict[str, Any],
    underlying: Dict[str, Any],
    context: Dict[str, Any],
    params: Dict[str, Any],
) -> Optional[Decision]:
    """Run pre-matrix guardrails in order. Return first that fires."""
    
    # Task #49: smart take-profit params live in a nested "smart_take_profit"
    # block under wheel_parameters. Flatten them into the params dict the
    # guardrail expects (falls back to sensible defaults if unset).
    smart_tp_params = {**params, **(params.get("smart_take_profit") or {})}

    checks = [
        check_loss_stop(position, params),
        check_crash_stop(position),
        # Task #38: strike-tested trigger — after loss-stop/crash-stop,
        # before the matrix walk. Fires only at < 30% capture, so it never
        # collides with the smart take-profit layers (all require ≥ 30%).
        check_strike_tested(position, params),
        check_smart_take_profit(position, smart_tp_params),  # ← task #49
        check_open_order(context),
        check_earnings_imminent(position, underlying, params),
    ]

    for result in checks:
        if result.fired:
            return result.decision

    return None


def run_post_matrix_guardrails(
    decision: Decision,
    position: Dict[str, Any],
    underlying: Dict[str, Any],
    context: Dict[str, Any],
    params: Dict[str, Any],
    tail_risk_names: Dict[str, Set[str]],
    state: Any = None,  # Optional DerivedState
) -> Optional[Decision]:
    """Run post-matrix guardrails. Return override decision if guardrail fires."""

    # LEAP OTM hold override (check first, before tail risk)
    result = check_leap_otm_hold_override(position, decision, state)
    if result.fired:
        return result.decision

    # Tail-risk check
    result = check_tail_risk(position, decision, tail_risk_names)
    if result.fired:
        return result.decision

    # TODO: Add stress test, net credit validation, ex-dividend checks

    return None
