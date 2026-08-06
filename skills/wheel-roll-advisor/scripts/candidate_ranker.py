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
    max_tenor_days: Optional[int] = None,
    rollup_bonus_per_pct: float = 250.0,
    option_type: str = "CALL",
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
        max_tenor_days: if set, reject any candidate that extends the expiration by
                        MORE than this many days. Stops the ranker from picking an
                        absurd multi-year, same-strike calendar just because long-dated
                        time value maximizes the raw credit (which caps a name for years).
        rollup_bonus_per_pct: wheel-mode bonus per 1% of cap headroom on the new
                              strike, so a roll-UP (higher strike, preserves upside) is
                              preferred between comparable-credit candidates rather than
                              always defaulting to the max-credit same-strike roll.
                              CALL-side only — never applied to puts.
        option_type: "CALL" (default — preserves legacy behavior for existing
                     callers) or "PUT". Short-PUT rolls are DEFENSIVE and rank
                     by RISK REDUCTION, not credit: (a) strike reduction
                     (distance OTM gained) dominates, (b) credit must be ≥ 0
                     unless the strike reduction is large (≥5% of spot —
                     never pay to stay in a losing trade otherwise),
                     (c) shorter tenor preferred. A candidate with a HIGHER
                     strike than the current one is never picked for a put —
                     deeper ITM = more assignment risk (the 2026-07-29
                     NVDA/VRT/MU max-credit roll-up bug). Max-credit ranking
                     is only valid for call-side income rolls.

    Returns:
        (best_candidate_dict, [CandidateScore for each non-HOLD candidate])
        best_candidate_dict is None if no candidate beats HOLD.
    """
    scored: list[CandidateScore] = []
    best = None
    best_score = -float("inf")
    is_put = (option_type or "CALL").upper() == "PUT"

    for c in candidates:
        if c.get("id") == "A":  # HOLD
            continue
        instruction = c.get("instruction") or {}
        new_strike = float(instruction.get("sell_strike") or 0)
        dte_ext = int(c.get("dteExtension") or 0)
        net_d = float(c.get("netDollars") or 0)
        cap_buf = _cap_buffer_pct(new_strike, spot)

        # Tenor cap — never surface a roll that locks the position for longer
        # than the configured horizon (a 2.5-year same-strike calendar caps a
        # bullish name for years just to harvest time-value premium).
        if max_tenor_days is not None and dte_ext > max_tenor_days:
            continue

        # Reverse-tenor guard — reject rolls that SHORTEN the expiration on
        # an already-long-dated position. A `ROLL_OUT` that moves from
        # +500d to +90d = dte_ext -410 is a downgrade, not a roll-out.
        # Only allow negative dte_ext when the position is short-dated
        # to begin with (current DTE < 30) — those are legit
        # roll-forward-and-close-early moves. (Task #13.)
        if dte_ext < 0:
            current_dte = int(c.get("current_dte") or c.get("currentDte") or 0)
            if current_dte >= 30:
                continue

        if is_put:
            # ── Defensive short-PUT mode: rank by RISK REDUCTION ──────────
            cur_strike = float(c.get("current_strike")
                               or instruction.get("current_strike") or 0)
            # A higher strike on a short put = deeper ITM = MORE assignment
            # risk. NEVER pick it (defensive roll-up bug, 2026-07-29).
            if cur_strike and new_strike > cur_strike + 0.01:
                continue
            # STO-leg viability belt (task #37 fix 3, NOK $1P @ $0.00 bug):
            # when the candidate carries its own quote, a dead leg (no bid
            # AND mid under $0.10) is not a tradeable roll. Fail-open when
            # the quote keys are absent (test fixtures / legacy callers).
            if "sell_bid" in instruction or "sell_mid" in instruction:
                _sb = float(instruction.get("sell_bid") or 0)
                _sm = float(instruction.get("sell_mid") or 0)
                if _sb <= 0 and _sm < 0.10:
                    continue
            # A put strike below 40% of spot collects nothing and reduces
            # nothing meaningful — reject outright.
            if spot and new_strike and new_strike < 0.40 * spot:
                continue
            strike_reduction = max(0.0, cur_strike - new_strike) if cur_strike else 0.0
            reduction_pct = (strike_reduction / spot * 100) if spot else 0.0
            # Credit ≥ 0 unless the strike reduction is large: a small debit
            # for a big strike reduction can be the right defensive trade,
            # but never pay just to extend a losing same-strike position.
            if net_d < 0 and reduction_pct < 5.0:
                continue
            # Strike reduction dominates; credit is secondary; shorter tenor
            # preferred (smallest commitment that achieves the reduction).
            composite = reduction_pct * 1000 + net_d * 0.1 - dte_ext * 1.0
            cap_buf = ((spot - new_strike) / spot * 100) if spot else 0.0
            explanation = (
                f"put-defensive: strike −${strike_reduction:,.0f} "
                f"({reduction_pct:.1f}% of spot), net=${net_d:+,.0f}, "
                f"dte+={dte_ext}d"
            )
        elif is_core:
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
            # Wheel mode: credit-driven, but prefer a roll-UP. Reject debits.
            if net_d < min_credit_threshold:
                continue
            # Roll-up preference: add a bonus for cap headroom on the new strike,
            # so between comparable-credit candidates we raise the strike (keep
            # upside) instead of always re-capping at the same strike for max
            # credit. Credit still dominates large differences.
            buffer_score = max(0.0, cap_buf) * rollup_bonus_per_pct
            dte_score = 0
            credit_score = net_d
            composite = net_d + buffer_score
            explanation = (
                f"wheel: net=${net_d:+,.0f} + rollup_bonus={buffer_score:.0f} "
                f"(cap_buf {cap_buf:+.1f}%)"
            )

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
        if is_put:
            # Put-defensive candidates were already gated per-candidate
            # (no roll-ups, no small-reduction debits). A $0-credit roll-down
            # is a valid defensive trade — do NOT apply the wheel-mode
            # min-credit gate here. Require SOME benefit vs HOLD though:
            # strike reduction, credit, or time.
            cur_strike = float(best.get("current_strike")
                               or (best.get("instruction") or {}).get("current_strike") or 0)
            new_strike = float((best.get("instruction") or {}).get("sell_strike") or 0)
            reduced = bool(cur_strike and new_strike and new_strike < cur_strike - 0.01)
            if not reduced and net_d <= 0 and dte_ext <= 0:
                best = None
        elif is_core:
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
