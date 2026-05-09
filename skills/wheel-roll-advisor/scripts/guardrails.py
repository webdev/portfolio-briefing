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
    
    checks = [
        check_loss_stop(position, params),
        check_crash_stop(position),
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
