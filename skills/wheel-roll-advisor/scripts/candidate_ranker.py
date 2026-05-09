"""
Tax-aware roll candidate ranker.

For each enumerated roll candidate (A=HOLD, B/C/D/E=alternatives), produce a
ranking score that depends on whether the underlying position is `core`
(long-term holding with embedded gain → assignment is taxable event) or
`wheel` (income-focused, assignment is acceptable).

Two ranking modes:

- **core_mode**: rank by (cap_buffer_pct, dte_extension, net_dollars).
  Prefer candidates that move the strike further from spot (more headroom).
  Penalize same-strike LEAPs that lock in the cap for years.

- **wheel_mode** (default): rank by net_dollars descending. Maximum credit wins.
  Same-strike longer-date is fine.

This logic is invoked by:
1. wheel-roll-advisor itself (when it sets `recommendedCandidateId`)
2. daily-portfolio-briefing (in render_action_list when picking the roll to surface)

Both should produce the same recommendation given the same inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CandidateScore:
    """Score breakdown for a roll candidate."""
    candidate_id: str
    composite_score: float
    cap_buffer_pct: float
    dte_extension: int
    net_dollars: float
    rank_explanation: str


def _cap_buffer_pct(new_strike: float, spot: float) -> float:
    """How far the new strike sits above spot, as a percent."""
    if not spot or spot <= 0:
        return 0.0
    return (new_strike - spot) / spot * 100


def rank_candidates(
    candidates: list[dict],
    spot: float,
    is_core: bool = False,
    embedded_tax_dollars: float = 0.0,
    min_credit_threshold: float = 1000.0,
) -> tuple[Optional[dict], list[CandidateScore]]:
    """
    Rank roll candidates and return the best non-HOLD pick.

    Args:
        candidates: list of candidate dicts from wheel-roll-advisor (id, netDollars,
                    instruction.sell_strike, instruction.sell_expiration, dteExtension, etc.)
        spot: current underlying price
        is_core: True if position is a tax-sensitive core long-term holding
        embedded_tax_dollars: estimated tax cost on assignment (used to bias against
                               calendar rolls on core positions)
        min_credit_threshold: minimum net credit to qualify (used in wheel mode);
                               core mode allows debits if they raise the cap meaningfully

    Returns:
        (best_candidate_dict, [CandidateScore for each non-HOLD candidate])
        best_candidate_dict is None if no candidate beats HOLD.
    """
    scored: list[CandidateScore] = []
    best = None
    best_score = -float("inf")

    for c in candidates:
        if c.get("id") == "A":  # HOLD
            continue
        instruction = c.get("instruction") or {}
        new_strike = float(instruction.get("sell_strike") or 0)
        dte_ext = int(c.get("dteExtension") or 0)
        net_d = float(c.get("netDollars") or 0)
        cap_buf = _cap_buffer_pct(new_strike, spot)

        if is_core:
            # Core mode: prefer cap buffer, then dte extension, then net dollars.
            # Heavy penalty on calendar rolls (same strike) when embedded tax is large.
            cur_strike = float(c.get("current_strike") or instruction.get("current_strike") or 0)
            is_calendar = (cur_strike == 0) or (abs(new_strike - cur_strike) < 0.01)

            # Score components (each normalized to a comparable range)
            buffer_score = cap_buf * 100  # +100 per 1% of headroom — dominant signal
            dte_score = min(dte_ext, 365) * 0.5  # cap dte contribution at 365 days
            credit_score = net_d / 100  # $1 per $100 of credit

            # Tax-bomb penalty: calendar rolls on heavily-taxed positions are bad
            calendar_penalty = 0
            if is_calendar and embedded_tax_dollars > 5000:
                # Penalty grows with embedded tax; calendar at $25K tax = -2500 penalty
                calendar_penalty = -embedded_tax_dollars / 10
            # Acceptable-debit gate: only if buffer gain > 10%
            allow_debit = (cap_buf - 0) > 10  # require at least 10% headroom

            composite = buffer_score + dte_score + credit_score + calendar_penalty
            if not allow_debit and net_d < 0:
                composite -= 10000  # exclude debit candidates with no buffer gain
            explanation = (
                f"core: buffer={cap_buf:+.1f}%, dte+={dte_ext}d, "
                f"net=${net_d:+,.0f}, calendar_penalty={calendar_penalty:.0f}"
            )
        else:
            # Wheel mode: max-credit wins. Reject debits.
            if net_d < min_credit_threshold:
                continue
            buffer_score = 0
            dte_score = 0
            credit_score = net_d
            composite = net_d
            explanation = f"wheel: net=${net_d:+,.0f} (max-credit rule)"

        cs = CandidateScore(
            candidate_id=c.get("id", "?"),
            composite_score=composite,
            cap_buffer_pct=cap_buf,
            dte_extension=dte_ext,
            net_dollars=net_d,
            rank_explanation=explanation,
        )
        scored.append(cs)

        if composite > best_score:
            best_score = composite
            best = c

    # Final sanity gate: even in core mode, if best candidate has no real benefit
    # (no buffer gain AND no credit AND no time), recommend HOLD.
    if best is not None:
        cap_buf = _cap_buffer_pct(float((best.get("instruction") or {}).get("sell_strike") or 0), spot)
        dte_ext = int(best.get("dteExtension") or 0)
        net_d = float(best.get("netDollars") or 0)
        if is_core:
            # Core: require either cap-buffer improvement >5% OR sizeable credit OR dte ext >60
            if cap_buf <= _cap_buffer_pct_of_existing_strike(candidates, spot) + 1 \
                    and net_d < min_credit_threshold and dte_ext < 60:
                best = None
        else:
            if net_d < min_credit_threshold:
                best = None

    return best, scored


def _cap_buffer_pct_of_existing_strike(candidates: list[dict], spot: float) -> float:
    """Approximate the existing strike's buffer by looking at any candidate with a
    'current_strike' annotation — fallback to 0."""
    for c in candidates:
        cur = (c.get("instruction") or {}).get("current_strike") or c.get("current_strike")
        if cur:
            return _cap_buffer_pct(float(cur), spot)
    return 0.0
