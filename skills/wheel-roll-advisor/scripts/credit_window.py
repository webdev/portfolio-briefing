"""Credit-window indicator for short puts (task #38, Part 2).

The state that actually matters on a tested short put is not "how underwater
am I" but "can I still roll for a CREDIT". While the strike is being tested,
extrinsic is at its peak and same-or-lower-strike rolls collect real money;
once the put goes meaningfully ITM the same rolls flip to debits (the MU
$950P went from credit-roll territory on Jul 22 to a $4,700 roll debit one
week later — in silence). This module classifies that window from the
ALREADY-GENERATED roll candidates (task #34's enumerate_roll_candidates
output — same-strike-out and roll-downs only) so renderers can surface it
and alert on transitions.

States:
  open       — best same-or-LOWER-strike roll nets ≥ closing_threshold_per_share
               (default $0.50/share) credit
  closing    — best net is between $0 and the threshold
  debit_only — every candidate costs money to roll
  unknown    — no priced candidates / no chain (fail-open, never fabricated)

Roll-UP candidates (higher strike) are NEVER counted — on a short put a
higher strike is deeper ITM, and a credit harvested by increasing risk is
not a credit window (CLAUDE.md rule #42).
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class CreditWindow:
    """Classification of the credit-roll window for one short put."""
    state: str                       # "open" | "closing" | "debit_only" | "unknown"
    best_credit: Optional[float]     # best net credit PER SHARE (same-or-lower strike)
    best_candidate_desc: Optional[str]
    threshold_used: float


def _cand_field(cand: Any, *names: str, default=None):
    """Read a field from a RollCandidate dataclass OR an advise()-output dict
    (camelCase keys). First present name wins."""
    for name in names:
        if isinstance(cand, dict):
            if name in cand:
                return cand[name]
        elif hasattr(cand, name):
            return getattr(cand, name)
    return default


def assess_credit_window(
    position: Dict[str, Any],
    candidates: Optional[List[Any]],
    config: Optional[Dict[str, Any]] = None,
) -> CreditWindow:
    """Classify the credit-roll window from already-priced roll candidates.

    Args:
        position: dict carrying the current strike (``strikePrice`` or
            ``strike``) and contract count (``quantity`` or ``qty``; sign
            ignored).
        candidates: RollCandidate dataclasses (enumerate_roll_candidates) or
            the dict form advise() emits (``netDollars`` / ``instruction`` /
            ``dteExtension`` keys). The HOLD candidate (no instruction) is
            ignored.
        config: optional dict; recognized keys (flat or nested under
            ``credit_window``): ``closing_threshold_per_share`` (default
            0.50), ``max_tenor_days`` (default 365 — a "credit" harvested by
            locking the strike for years is duration, not a window).

    Returns:
        CreditWindow. ``unknown`` when there are no eligible candidates —
        never a fabricated state (CLAUDE.md #19).
    """
    cfg = dict(config or {})
    nested = cfg.get("credit_window")
    if isinstance(nested, dict):
        cfg = {**cfg, **nested}
    threshold = float(cfg.get("closing_threshold_per_share", 0.50))
    max_tenor = int(cfg.get("max_tenor_days", 365))

    strike = position.get("strikePrice", position.get("strike", 0))
    try:
        strike = float(strike or 0)
    except (TypeError, ValueError):
        strike = 0.0
    qty_raw = position.get("quantity", position.get("qty", 1))
    try:
        qty = abs(int(qty_raw)) or 1
    except (TypeError, ValueError):
        qty = 1

    best_per_share: Optional[float] = None
    best_desc: Optional[str] = None

    for cand in (candidates or []):
        instruction = _cand_field(cand, "instruction")
        if not instruction:
            continue  # HOLD row
        sell_strike = instruction.get("sell_strike") if isinstance(instruction, dict) else None
        try:
            sell_strike = float(sell_strike) if sell_strike is not None else None
        except (TypeError, ValueError):
            sell_strike = None
        # Same-or-LOWER strike only — roll-ups never count (rule #42).
        if sell_strike is None or (strike > 0 and sell_strike > strike + 1e-9):
            continue
        dte_ext = _cand_field(cand, "dte_extension", "dteExtension", default=0) or 0
        try:
            if int(dte_ext) > max_tenor:
                continue
        except (TypeError, ValueError):
            pass
        net_dollars = _cand_field(cand, "net_dollars", "netDollars")
        try:
            net_dollars = float(net_dollars)
        except (TypeError, ValueError):
            continue
        per_share = net_dollars / (100.0 * qty)
        if best_per_share is None or per_share > best_per_share:
            best_per_share = per_share
            best_desc = _cand_field(cand, "description", default=None)

    if best_per_share is None:
        return CreditWindow(state="unknown", best_credit=None,
                            best_candidate_desc=None, threshold_used=threshold)

    if best_per_share >= threshold:
        state = "open"
    elif best_per_share >= 0:
        state = "closing"
    else:
        state = "debit_only"

    return CreditWindow(state=state, best_credit=best_per_share,
                        best_candidate_desc=best_desc, threshold_used=threshold)
