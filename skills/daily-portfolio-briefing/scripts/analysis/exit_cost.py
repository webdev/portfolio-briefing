"""Exit-cost anatomy — decompose the cost of closing a short option into
intrinsic / extrinsic / spread, and say whether closing, rolling, or taking
assignment is the cheaper exit.

Origin (2026-07-29, market down hard): the user almost paid a $15,570 ask to
buy back a LITE $700P. Only $10,081 of that was intrinsic; ~$5,200 was
panic-IV extrinsic and ~$550 was bid/ask spread. The right move was a
roll-down-and-out (IV-neutral — you sell inflated premium while you buy it),
not a market-order close that pays away the panic premium. This module turns
that manual analysis into deterministic pipeline logic.

Hard constraints honored:
  - CLAUDE.md #10 (fail closed): no chain quote fetched this cycle → no
    anatomy. The renderer surfaces "chain unavailable — verify exit cost at
    broker", never a fabricated number.
  - CLAUDE.md #19 (no fabricated numbers): every figure comes from the live
    chain leg, the position's own entry price, or the snapshot spot.
  - Verdicts NEVER override the loss-stop guardrail: when GUARDRAIL_LOSS_STOP
    fired, the verdict may guide timing/pricing (CLOSE_CLEAN /
    CLOSE_AFTER_CRUSH) or pivot to a defensive roll (ROLL_DONT_CLOSE, the
    CLAUDE.md core-override pattern) — but never a bare hold (HOLD_FOR_BASIS
    is suppressed).

Config: briefing.yaml → `exit_cost` (every key has an in-code default).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime


DEFAULTS = {
    "enabled": True,
    # Task #37 fix 1: when True (default) the action list CONSUMES the
    # verdict — CLOSE_CLEAN / CLOSE_AFTER_CRUSH / CLOSE_URGENT convert a
    # composed EXECUTE ROLL into a plain CLOSE ticket. Kill switch for the
    # old annotate-only behavior.
    "verdict_drives_action": True,
    # 2026-08-04 fix 1: ONE-VOICE rule — a profitable take-profit CLOSE whose
    # verdict is ROLL_DONT_CLOSE resolves to a single recommendation (TAKE
    # PROFIT VIA ROLL-DOWN, or HOLD — GTC at 50% when no credit-positive
    # roll-down is priced) instead of a CLOSE headline contradicted by an
    # inline ROLL verdict (the VRT $280P card). Kill switch.
    "one_voice": True,
    "extrinsic_clean_pct": 0.15,    # extrinsic < 15% of mid → closing is cheap
    "extrinsic_pumped_pct": 0.30,   # extrinsic > 30% of mid → closing pays panic premium
    "iv_elevated_rank": 60,         # IV rank at/above this = "elevated"
    "iv_crushed_rank": 30,          # IV rank at/below this = "crushed"
    "wide_spread_pct": 0.08,        # spread > 8% of mid → work a limit
    "wide_spread_abs": 2.00,        # spread > $2.00/share → work a limit
    "min_dte_for_roll": 21,         # roll-don't-close needs runway to work
    "pre_print_window_days": 7,     # tag pre-earnings state inside this window
    "printed_recent_days": 3,       # tag post-earnings state inside this window
    # HOLD_FOR_DECAY (TSM 2026-07-30): a fully-OTM short option with no
    # event in the contract window and no genuine strike test needs NO exit
    # — theta works entirely for the holder. Fires ahead of ROLL_DONT_CLOSE
    # so panic-IV extrinsic on an untested OTM put never buys a debit roll.
    "hold_for_decay_delta": 0.40,   # |delta| at/above this = strike tested
    "hold_for_decay_band_pct": 0.03,  # price fallback when delta unknown
}

_VERDICT_HEADLINES = {
    "ROLL_DONT_CLOSE": "ROLL, don't close",
    "CLOSE_CLEAN": "CLOSE — clean exit",
    "CLOSE_AFTER_CRUSH": "CLOSE — after the crush",
    "CLOSE_URGENT": "CLOSE — before the binary",
    "HOLD_FOR_DECAY": "HOLD — extrinsic decaying in your favor",
    "HOLD_FOR_BASIS": "Assignment acceptable — hold for basis",
    "NEUTRAL": "No directive",
}


@dataclass
class ExitCostAnatomy:
    """Decomposition of what buying back a short option actually pays for."""

    contract: str
    spot: float
    strike: float
    intrinsic_per_share: float          # puts: max(strike - spot, 0); calls: max(spot - strike, 0)
    extrinsic_per_share: float          # mid - intrinsic
    extrinsic_total: float              # × 100 × qty
    spread_per_share: float             # ask - bid
    spread_pct_of_mid: float
    premium_received_per_share: float | None
    assignment_basis: float | None      # strike - premium_received (puts)
    basis_vs_spot_pct: float | None     # (assignment_basis - spot) / spot
    iv_context: str | None              # "elevated" / "crushed" / None
    earnings_state: str | None          # "pre_print_Nd" (N may be 0) / "printed_today" / "printed_recent" / None
    verdict: str
    verdict_reason: str
    # Extra measured context (not in the render spec but useful downstream)
    btc_mid: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    intrinsic_total: float = 0.0
    qty: float = 0.0


def _cfg(config: dict | None) -> dict:
    """Merge briefing.yaml → exit_cost over the in-code defaults."""
    merged = dict(DEFAULTS)
    section = ((config or {}).get("exit_cost") or {}) if isinstance(config, dict) else {}
    for k, v in section.items():
        merged[k] = v
    return merged


def _as_date(d) -> date | None:
    """Coerce str/date/datetime to a date; None on anything unparseable."""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def classify_earnings_state(
    earnings_date, today, cfg: dict | None = None,
    *, session: str | None = None, now=None,
) -> str | None:
    """Return "pre_print_Nd" (N may be 0) / "printed_today" / "printed_recent" / None.

    Task #43 (PLTR $130P, 2026-08-03): a SAME-DAY earnings date is IMMINENT,
    not printed. The 11:37 AM briefing rendered "earnings printed — let the
    IV crush work" on a put whose print was AFTER THE CLOSE that evening —
    advising the user to buy back at PEAK pre-print IV. delta == 0 now
    classifies as "pre_print_0d" unless there is AFFIRMATIVE evidence the
    print already happened:

      - ``session`` = "BMO" (before market open) AND ``now`` (a datetime,
        ET) is at/after 10:00 → "printed_today" (the crush is underway);
      - ``session`` = "AMC" or unknown / no ``now`` → NOT printed.

    Fail-safe direction: a wrong "imminent" costs nothing; a wrong "printed"
    pays peak IV. "printed_today" (fresh crush) also fires on the day AFTER
    the earnings date (delta == -1) — by then the print has definitely
    happened regardless of session (AMC prints crush the next trading day).
    """
    c = cfg or DEFAULTS
    e = _as_date(earnings_date)
    t = _as_date(today) or date.today()
    if e is None:
        return None
    delta = (e - t).days
    if delta == 0:
        sess = str(session).strip().upper() if session else None
        hour = getattr(now, "hour", None)
        if sess == "BMO" and hour is not None and hour >= 10:
            return "printed_today"
        return "pre_print_0d"
    if delta == -1:
        return "printed_today"
    if delta < 0 and -delta <= int(c.get("printed_recent_days", 3)):
        return "printed_recent"
    if 0 < delta <= int(c.get("pre_print_window_days", 7)):
        return f"pre_print_{delta}d"
    return None


def chain_quote_for_position(review: dict, chains: dict) -> dict | None:
    """Find the held contract's own leg in this cycle's snapshot chains.

    Returns {"bid", "ask", "mid"} or None (fail closed — CLAUDE.md #10).
    A quote with a missing/zero bid or ask is treated as unavailable rather
    than approximated.
    """
    try:
        underlying = review.get("underlying") or str(review.get("contract", "")).split("_")[0]
        expiration = review.get("expiration")
        strike = float(review.get("strike") or 0)
        if not (underlying and expiration and strike):
            return None
        chain = (chains or {}).get(f"{underlying}_{expiration}")
        if not chain:
            return None
        legs = chain.get("puts" if (review.get("type") or "").upper() == "PUT" else "calls") or []
        for leg in legs:
            try:
                if abs(float(leg.get("strike", 0) or 0) - strike) > 0.005:
                    continue
            except (TypeError, ValueError):
                continue
            bid = float(leg.get("bid", 0) or 0)
            ask = float(leg.get("ask", 0) or 0)
            if bid > 0 and ask > 0 and ask >= bid:
                return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2.0}
            return None  # leg found but quote unusable — fail closed
        return None
    except Exception:
        return None


def assignment_concentration_ok(
    review: dict,
    snapshot_data: dict | None,
    equity_reviews: list | None = None,
) -> bool | None:
    """Would taking assignment keep the single name under its tier cap?

    Returns True/False when measurable, None when NLV (or sizing inputs) are
    unavailable — None means HOLD_FOR_BASIS is NOT eligible (we never bless
    assignment on missing data).
    """
    try:
        nlv = float(((snapshot_data or {}).get("balance") or {}).get("accountValue") or 0)
        strike = float(review.get("strike") or 0)
        qty = abs(float(review.get("qty", 0) or 0))
        if nlv <= 0 or strike <= 0 or qty <= 0:
            return None
        underlying = review.get("underlying") or str(review.get("contract", "")).split("_")[0]
        existing = 0.0
        for er in equity_reviews or []:
            if er.get("ticker") == underlying:
                existing = float(er.get("qty", 0) or 0) * float(er.get("price", 0) or 0)
                if not existing:
                    existing = float(er.get("weight", 0) or 0) * nlv
                break
        projected_pct = (existing + strike * 100.0 * qty) / nlv
        cap_pct = 0.10
        try:
            from analysis.position_tiers import concentration_cap_for_tier, tier_for
            cfg = (snapshot_data or {}).get("_config") or {}
            cap_pct = concentration_cap_for_tier(tier_for(underlying, cfg), cfg) / 100.0
        except Exception:
            pass
        return projected_pct <= cap_pct
    except Exception:
        return None


def analyze_exit_cost(
    position: dict,
    chain_quote: dict | None,
    spot: float,
    iv_rank: float | None = None,
    earnings_date=None,
    today=None,
    *,
    config: dict | None = None,
    loss_stop_fired: bool = False,
    concentration_ok: bool | None = None,
    earnings_session: str | None = None,
    now=None,
) -> ExitCostAnatomy | None:
    """Decompose the buy-to-close cost of a SHORT option and pick a verdict.

    Args:
        position: options-review-shaped dict (type, qty, strike, entry_price,
            days_to_expiry, contract). qty must be negative (short).
        chain_quote: {"bid", "ask"[, "mid"]} for the HELD contract, fetched
            this cycle. None → return None (fail closed, no anatomy).
        spot: live underlying price from this cycle's snapshot.
        iv_rank: portfolio-level IV rank for the underlying (NOT the
            contract's raw IV%). None → extrinsic test alone drives the
            roll verdict (per spec).
        earnings_date / today: for the earnings-state read.
        config: full briefing config (reads the `exit_cost` section).
        loss_stop_fired: True when GUARDRAIL_LOSS_STOP fired — suppresses
            HOLD_FOR_BASIS (a verdict may retime the exit, never undo it).
        concentration_ok: whether assignment keeps the name under its tier
            cap. Only True enables HOLD_FOR_BASIS.
        earnings_session / now: best-effort print-session evidence ("BMO" /
            "AMC") and the generation time — see classify_earnings_state.
            Without evidence a same-day earnings date is treated as NOT yet
            printed (fail-safe).

    Returns ExitCostAnatomy, or None when the position isn't a short option
    or the chain quote is missing/unusable.
    """
    c = _cfg(config)
    try:
        qty = float(position.get("qty", 0) or 0)
        strike = float(position.get("strike") or 0)
        spot_f = float(spot or 0)
    except (TypeError, ValueError):
        return None
    if qty >= 0 or strike <= 0 or spot_f <= 0:
        return None
    if not chain_quote:
        return None
    try:
        bid = float(chain_quote.get("bid", 0) or 0)
        ask = float(chain_quote.get("ask", 0) or 0)
    except (TypeError, ValueError):
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = float(chain_quote.get("mid") or 0) or (bid + ask) / 2.0

    opt_type = (position.get("type") or "").upper()
    is_put = opt_type == "PUT"
    contracts = abs(qty)

    intrinsic = max(strike - spot_f, 0.0) if is_put else max(spot_f - strike, 0.0)
    extrinsic = mid - intrinsic
    extrinsic_pct = (extrinsic / mid) if mid > 0 else 0.0
    spread = ask - bid
    spread_pct = (spread / mid) if mid > 0 else 0.0

    premium = position.get("entry_price")
    try:
        premium = float(premium) if premium else None
    except (TypeError, ValueError):
        premium = None
    if premium is not None and premium <= 0:
        premium = None
    basis = (strike - premium) if (is_put and premium is not None) else None
    basis_vs_spot = ((basis - spot_f) / spot_f) if (basis is not None and spot_f) else None

    iv_elevated_rank = float(c.get("iv_elevated_rank", 60))
    iv_crushed_rank = float(c.get("iv_crushed_rank", 30))
    iv_context = None
    if iv_rank is not None:
        if iv_rank >= iv_elevated_rank:
            iv_context = "elevated"
        elif iv_rank <= iv_crushed_rank:
            iv_context = "crushed"

    earnings_state = classify_earnings_state(
        earnings_date, today, c, session=earnings_session, now=now)
    printed = earnings_state in ("printed_today", "printed_recent")
    printed_fresh = earnings_state == "printed_today"

    underwater = premium is not None and mid > premium
    dte = int(position.get("days_to_expiry") or 0)

    clean_pct = float(c.get("extrinsic_clean_pct", 0.15))
    pumped_pct = float(c.get("extrinsic_pumped_pct", 0.30))
    min_dte_roll = int(c.get("min_dte_for_roll", 21))

    # ── HOLD_FOR_DECAY inputs (TSM 2026-07-30) ────────────────────────────
    # Measured position delta (broker/chain) when the review carries one.
    try:
        _raw_delta = position.get("delta")
        abs_delta = abs(float(_raw_delta)) if _raw_delta is not None else None
    except (TypeError, ValueError):
        abs_delta = None
    # Earnings inside the CONTRACT window (not just the 7d pre-print band).
    _e_date = _as_date(earnings_date)
    _t_date = _as_date(today) or date.today()
    earnings_in_window = (
        _e_date is not None and 0 <= (_e_date - _t_date).days <= dte
    )
    decay_delta = float(c.get("hold_for_decay_delta", 0.40))
    decay_band = float(c.get("hold_for_decay_band_pct", 0.03))
    # "Not tested": measured |delta| below the trigger; when the delta is
    # unavailable, fall back to price — spot beyond the 3% band on the OTM
    # side (puts: spot > 1.03 × strike; calls: spot < 0.97 × strike).
    if abs_delta is not None:
        strike_untested = abs_delta < decay_delta
    elif is_put:
        strike_untested = spot_f > strike * (1 + decay_band)
    else:
        strike_untested = spot_f < strike * (1 - decay_band)

    ext_total = extrinsic * 100.0 * contracts

    verdict = "NEUTRAL"
    reason = "anatomy shown for context — no single exit dominates."

    days_to_print = None
    if earnings_state and earnings_state.startswith("pre_print_"):
        try:
            days_to_print = int(earnings_state[len("pre_print_"):-1])
        except ValueError:
            days_to_print = None

    if (days_to_print is not None and days_to_print <= 2
            and underwater
            and (extrinsic_pct < pumped_pct or days_to_print == 0)):
        # Task #43: on the DAY of the print (days_to_print == 0) an
        # underwater position exits before the binary even when the
        # extrinsic is pumped — there is no later, cheaper pre-print window.
        verdict = "CLOSE_URGENT"
        if days_to_print == 0 and extrinsic_pct >= pumped_pct:
            reason = (
                f"earnings print is imminent (today) and the position is "
                f"underwater — exit before the binary. "
                f"{extrinsic_pct * 100:.0f}% of the buyback is time value at "
                f"peak pre-print IV; work a limit at mid, don't pay the ask."
            )
        else:
            reason = (
                f"earnings in {days_to_print}d, position underwater, and only "
                f"{extrinsic_pct * 100:.0f}% of the buyback is time value — exit "
                f"before the binary."
            )
    elif extrinsic_pct < clean_pct or (printed and iv_context == "crushed"):
        verdict = "CLOSE_CLEAN"
        reason = (
            f"mostly intrinsic — cheap exit (extrinsic "
            f"${ext_total:,.0f} = {extrinsic_pct * 100:.0f}% of the buyback)."
        )
    elif printed_fresh and extrinsic_pct > 0.20:
        verdict = "CLOSE_AFTER_CRUSH"
        reason = (
            f"earnings printed — {extrinsic_pct * 100:.0f}% of the buyback is "
            f"still time value. Let the IV crush work for the first hour, "
            f"then work a limit at mid."
        )
    elif (intrinsic <= 0.0
          and not loss_stop_fired
          and not earnings_in_window
          and strike_untested):
        # Fully OTM, no event in the window, strike not genuinely tested —
        # there is no exit need; ROLL_DONT_CLOSE only applies when an exit
        # is actually warranted (ITM or tested). Priority: URGENT → CLEAN →
        # CRUSH → HOLD_FOR_DECAY → ROLL → BASIS → NEUTRAL. The loss-stop
        # guardrail still overrides (a loss-stopped position never gets a
        # bare hold).
        #
        # Task #40 fix 4 precedence note: at RENDER time (panels.py block
        # #3), an earnings-blocked roll on a position that has RECOVERED to
        # better than recovery_close.max_loss_pct with earnings ≤ 14d
        # surfaces CLOSE INTO RECOVERY — and that OUTRANKS a ROLL_DONT_CLOSE
        # verdict from here. This verdict optimizes premium mechanics
        # (swap inflated IV for inflated IV); it does not price the binary
        # event risk of holding through a print. Removing the binary at
        # ~zero cost wins (the LITE $700P 2026-07-30 case).
        verdict = "HOLD_FOR_DECAY"
        reason = (
            "100% extrinsic decaying in your favor; no exit needed. "
            "Re-evaluate if the strike is tested (δ ≥ 0.40) or an event "
            "enters the window."
        )
    elif (extrinsic_pct > pumped_pct
          and (iv_rank is None or iv_rank >= iv_elevated_rank)
          and dte >= min_dte_roll):
        verdict = "ROLL_DONT_CLOSE"
        iv_bit = f"at IV rank {iv_rank:.0f}" if iv_rank is not None else "at IV highs"
        reason = (
            f"closing pays ${ext_total:,.0f} of panic premium {iv_bit}; a "
            f"roll-down-and-out (see ROLL ANALYSIS) swaps inflated premium "
            f"for inflated premium instead."
        )
    elif (not loss_stop_fired
          and concentration_ok is True
          and is_put
          and basis is not None
          and basis <= spot_f * 1.01):
        verdict = "HOLD_FOR_BASIS"
        reason = (
            f"assignment basis ${basis:,.2f} vs spot ${spot_f:,.2f} — you'd "
            f"own it {abs(basis_vs_spot or 0) * 100:.1f}% "
            f"{'below' if (basis_vs_spot or 0) <= 0 else 'above'} market, and "
            f"the name stays under its concentration cap."
        )
    elif loss_stop_fired:
        reason = (
            "loss stop stands — anatomy shown to time and price the exit "
            "(no bare hold)."
        )

    # Task #43: whatever the verdict, a same-day UNPRINTED earnings date is
    # surfaced explicitly — the old code called this state "printed_today"
    # and told the user to wait for an IV crush that hadn't happened yet.
    if earnings_state == "pre_print_0d" and verdict != "CLOSE_URGENT":
        reason += (
            " Earnings print is imminent (today, NOT yet printed) — IV is at "
            "its pre-print peak; do not treat this as post-print."
        )

    return ExitCostAnatomy(
        contract=str(position.get("contract") or ""),
        spot=spot_f,
        strike=strike,
        intrinsic_per_share=intrinsic,
        extrinsic_per_share=extrinsic,
        extrinsic_total=ext_total,
        spread_per_share=spread,
        spread_pct_of_mid=spread_pct,
        premium_received_per_share=premium,
        assignment_basis=basis,
        basis_vs_spot_pct=basis_vs_spot,
        iv_context=iv_context,
        earnings_state=earnings_state,
        verdict=verdict,
        verdict_reason=reason,
        btc_mid=mid,
        bid=bid,
        ask=ask,
        intrinsic_total=intrinsic * 100.0 * contracts,
        qty=contracts,
    )


def format_exit_cost_note(anatomy: ExitCostAnatomy) -> str | None:
    """Demoted, no-action exit-cost read for an UNTRIGGERED HOLD card
    (George 2026-08-21: "Sometimes I see recommendations way early when
    I'm out of the money.").

    On a position whose headline is plain HOLD with an untested strike,
    a full-strength "⚖️ Verdict: ROLL, don't close" sentence reads like a
    recommendation — one card, two voices. This note keeps the measured
    exit-cost information (rule #19) without leading with ROLL: the
    ROLL-vs-close phrasing appears only when a close/roll decision is
    actually live. Returns None when the extrinsic is unmeasurable
    (never a fabricated number)."""
    try:
        ext_total = float(anatomy.extrinsic_total)
        mid = float(anatomy.btc_mid or 0)
    except (TypeError, ValueError, AttributeError):
        return None
    if mid <= 0:
        return None
    ext_pct = anatomy.extrinsic_per_share / mid * 100.0
    return (
        f"⚖️ exit-cost note: closing today would pay ${ext_total:,.0f} of "
        f"extrinsic ({ext_pct:.0f}%); no action triggered"
    )


def spread_guidance(anatomy: ExitCostAnatomy, config: dict | None = None) -> str | None:
    """Wide-spread execution guidance, or None when the spread is fine."""
    c = _cfg(config)
    if (anatomy.spread_pct_of_mid > float(c.get("wide_spread_pct", 0.08))
            or anatomy.spread_per_share > float(c.get("wide_spread_abs", 2.00))):
        return (
            f"wide spread (${anatomy.spread_per_share:.2f}/share) — work a GTC "
            f"limit at mid ${anatomy.btc_mid:.2f}, don't pay the ask."
        )
    return None


def format_basis_line(strike: float, premium: float, spot: float) -> str | None:
    """Assignment-basis header line for the ROLL ANALYSIS table (ITM puts).

    All inputs are measured position/snapshot values — returns None when any
    is missing rather than fabricating (CLAUDE.md #19).
    """
    try:
        strike_f = float(strike or 0)
        premium_f = float(premium or 0)
        spot_f = float(spot or 0)
    except (TypeError, ValueError):
        return None
    if strike_f <= 0 or premium_f <= 0 or spot_f <= 0:
        return None
    basis = strike_f - premium_f
    pct = (basis - spot_f) / spot_f * 100.0
    rel = "below" if pct <= 0 else "above"
    return (
        f"**Assignment basis:** ${basis:,.2f} (strike ${strike_f:g} − premium "
        f"received ${premium_f:,.2f}) vs spot ${spot_f:,.2f} "
        f"({pct:+.1f}% — {rel} market)."
    )


def format_anatomy_lines(
    anatomy: ExitCostAnatomy,
    config: dict | None = None,
    indent: str = "   ",
    prior_entry=None,
    dte=None,
    iv_rank=None,
    verdict_context: str | None = None,
) -> list[str]:
    """Render the anatomy + verdict as action-list sub-bullets.

    ``prior_entry`` (2026-08-06 defect 2, QCOM whiplash): yesterday's
    persisted verdict entry for this contract (str or dict from
    exit_verdicts.json). When today's verdict differs, an italic
    "Verdict changed vs yesterday" line renders under the verdict with the
    measured driver(s) — or the honest not-fully-attributable fallback.
    ``dte`` / ``iv_rank`` feed the driver comparison (the anatomy dataclass
    doesn't carry them).

    ``verdict_context`` (2026-08-13, IREN $47P one-voice fix): when the
    CARD's headline action deliberately differs from the raw verdict (e.g.
    headline "HOLD — GTC AT 50%" while the verdict engine reads
    ROLL_DONT_CLOSE because no in-tenor roll-down is priced, or "CLOSE
    BEFORE EARNINGS" overriding ROLL_DONT_CLOSE on event risk), the caller
    passes the reconciliation string. The verdict then renders as an
    explicitly SUBORDINATE context read — "⚖️ Context (verdict engine …)" —
    followed by the resolution, instead of a second full-strength
    "**⚖️ Verdict:**" voice contradicting the headline. Observed bug: the
    2026-08-13 IREN card carried "**HOLD — GTC AT 50%** … +44% captured"
    AND "**⚖️ Verdict: ROLL, don't close**" with no reconciliation —
    three voices on one card."""
    c = _cfg(config)
    clean_pct = float(c.get("extrinsic_clean_pct", 0.15))
    pumped_pct = float(c.get("extrinsic_pumped_pct", 0.30))

    ext_pct = (anatomy.extrinsic_per_share / anatomy.btc_mid * 100.0
               if anatomy.btc_mid else 0.0)
    if anatomy.extrinsic_per_share / max(anatomy.btc_mid, 0.01) > pumped_pct and \
            anatomy.iv_context != "crushed":
        ext_tag = f"{ext_pct:.0f}% — IV-pumped"
    elif anatomy.extrinsic_per_share / max(anatomy.btc_mid, 0.01) < clean_pct:
        ext_tag = f"{ext_pct:.0f}% — mostly intrinsic"
    else:
        ext_tag = f"{ext_pct:.0f}%"

    guidance = spread_guidance(anatomy, config)
    spread_bit = (
        f"spread ${anatomy.spread_per_share:.2f}"
        f"{' wide' if guidance else ''} "
        f"({anatomy.spread_pct_of_mid * 100:.1f}% of mid)"
    )
    lines = [
        f"{indent}- **Exit cost anatomy:** BTC mid ${anatomy.btc_mid:.2f} = "
        f"${anatomy.intrinsic_total:,.0f} intrinsic + "
        f"${anatomy.extrinsic_total:,.0f} extrinsic ({ext_tag}) · {spread_bit}"
    ]

    basis_bit = ""
    if anatomy.assignment_basis is not None and anatomy.basis_vs_spot_pct is not None:
        rel = "below" if anatomy.basis_vs_spot_pct <= 0 else "above"
        basis_bit = (
            f" Assignment basis ${anatomy.assignment_basis:,.2f} vs spot "
            f"${anatomy.spot:,.2f} ({anatomy.basis_vs_spot_pct * 100:+.1f}% — "
            f"{rel} market)."
        )

    headline = _VERDICT_HEADLINES.get(anatomy.verdict, anatomy.verdict)
    if anatomy.verdict == "NEUTRAL":
        lines.append(
            f"{indent}- ⚖️ Exit read: {anatomy.verdict_reason}{basis_bit}"
        )
    elif verdict_context:
        # One card, one voice: the headline action already resolved this
        # verdict — render it as marked-subordinate context, never a second
        # full-strength recommendation.
        lines.append(
            f"{indent}- ⚖️ Context (verdict engine, subordinate to the "
            f"headline): {headline} — {anatomy.verdict_reason}{basis_bit} "
            f"→ {verdict_context}"
        )
    else:
        lines.append(
            f"{indent}- **⚖️ Verdict: {headline}** — "
            f"{anatomy.verdict_reason}{basis_bit}"
        )

    # 2026-08-06 defect 2 (QCOM_PUT_185_20261218): HOLD FOR BASIS (Aug 4) →
    # churn-guarded (Aug 5) → "ROLL, don't close" (Aug 6) with no explanation
    # of what changed. Day-over-day verdict transitions are never silent.
    change = verdict_change_line(
        anatomy, prior_entry, config=config, dte=dte, iv_rank=iv_rank,
        indent=indent)
    if change:
        lines.append(change)

    if guidance:
        lines.append(f"{indent}- ⚠ {guidance[0].upper()}{guidance[1:]}")
    return lines


# ---------------------------------------------------------------------------
# Verdict persistence (2026-08-05 defect 2) — the take-profit CLOSE card must
# be able to say "yesterday's read was ROLL, don't close" when today's
# anatomy can't be computed. Mirrors the credit_windows.json pattern: one
# small JSON per snapshot dir, read back on the next run.
# ---------------------------------------------------------------------------

EXIT_VERDICTS_FILENAME = "exit_verdicts.json"
_DATE_DIR_RE_XC = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def verdict_headline(verdict: str) -> str:
    """Human headline for a verdict id ('ROLL_DONT_CLOSE' → 'ROLL, don't
    close'). Unknown ids pass through unchanged (never fabricate)."""
    return _VERDICT_HEADLINES.get(verdict, verdict)


# Compact labels for the not-fully-attributable fallback line
# ("Verdict changed vs yesterday (BASIS → ROLL) — …").
_VERDICT_SHORT = {
    "HOLD_FOR_BASIS": "BASIS",
    "ROLL_DONT_CLOSE": "ROLL",
    "HOLD_FOR_DECAY": "DECAY",
    "CLOSE_CLEAN": "CLOSE",
    "CLOSE_AFTER_CRUSH": "CRUSH",
    "CLOSE_URGENT": "URGENT",
    "NEUTRAL": "NEUTRAL",
}


def verdict_of(entry) -> str | None:
    """Verdict id from a persisted exit_verdicts.json entry — accepts both
    the legacy bare-string shape and the rich dict shape (2026-08-06)."""
    if isinstance(entry, str):
        return entry or None
    if isinstance(entry, dict):
        v = entry.get("verdict")
        return str(v) if v else None
    return None


def anatomy_persist_entry(
    anatomy: ExitCostAnatomy,
    dte=None,
    iv_rank=None,
) -> dict:
    """Rich per-contract entry for exit_verdicts.json (2026-08-06 defect 2):
    verdict PLUS the anatomy fields tomorrow's run needs to attribute a
    verdict change (extrinsic pct, IV rank, intrinsic, DTE, earnings state).
    Missing inputs are omitted, never fabricated."""
    entry: dict = {"verdict": anatomy.verdict}
    if anatomy.btc_mid:
        entry["extrinsic_pct"] = round(
            anatomy.extrinsic_per_share / anatomy.btc_mid, 4)
    if iv_rank is not None:
        try:
            entry["iv_rank"] = round(float(iv_rank), 1)
        except (TypeError, ValueError):
            pass
    entry["intrinsic_per_share"] = round(anatomy.intrinsic_per_share, 4)
    if dte is not None:
        try:
            entry["dte"] = int(dte)
        except (TypeError, ValueError):
            pass
    if anatomy.earnings_state:
        entry["earnings_state"] = anatomy.earnings_state
    return entry


def build_today_verdicts(
    options_reviews: list,
    prior_map: dict | None,
    iv_ranks: dict | None,
    anatomy_fn,
) -> dict:
    """Today's {contract: rich entry} for exit_verdicts.json (2026-08-06
    defect 2). ``anatomy_fn(rev) -> ExitCostAnatomy | None`` computes the
    anatomy for one review. Side effect: reviews whose verdict differs from
    yesterday's persisted one get ``rev["_verdict_change"] = "A → B"`` (the
    ⏰ Risk Alerts prefix). Churn-gap carry-forward: a contract whose anatomy
    isn't computable today keeps yesterday's entry — a gap must never read
    as (or mask) a change tomorrow."""
    today: dict = {}
    prior_map = prior_map or {}
    for rev in options_reviews or []:
        contract = rev.get("contract")
        if not contract:
            continue
        an = anatomy_fn(rev)
        if an is not None:
            today[contract] = anatomy_persist_entry(
                an, dte=rev.get("days_to_expiry"),
                iv_rank=(iv_ranks or {}).get(
                    rev.get("underlying") or str(contract).split("_")[0]))
            prev = verdict_of(prior_map.get(contract))
            if prev and prev != an.verdict:
                rev["_verdict_change"] = f"{prev} → {an.verdict}"
        elif contract in prior_map:
            today[contract] = prior_map[contract]
    return today


def verdict_change_line(
    anatomy: ExitCostAnatomy,
    prior_entry,
    *,
    config: dict | None = None,
    dte=None,
    iv_rank=None,
    indent: str = "   ",
) -> str | None:
    """Day-over-day verdict-change transparency (2026-08-06 defect 2, the
    QCOM whiplash: HOLD FOR BASIS → churn-guarded → EXECUTE ROLL with no
    explanation). Returns an italic sub-bullet when today's verdict differs
    from yesterday's persisted one, attributing the change to the measured
    anatomy drivers — or the honest fallback when the drivers can't be
    identified from the persisted fields. None when there is no prior
    verdict or no change (fail-open, never fabricates a driver)."""
    try:
        prev = verdict_of(prior_entry)
        if not prev or prev == anatomy.verdict:
            return None
        c = _cfg(config)
        drivers: list[str] = []
        if isinstance(prior_entry, dict):
            # Extrinsic % of the buyback — the pumped/clean thresholds are
            # what flip ROLL_DONT_CLOSE / CLOSE_CLEAN.
            today_ext = (anatomy.extrinsic_per_share / anatomy.btc_mid
                         if anatomy.btc_mid else None)
            prev_ext = prior_entry.get("extrinsic_pct")
            if today_ext is not None and prev_ext is not None:
                try:
                    prev_ext = float(prev_ext)
                    pumped = float(c.get("extrinsic_pumped_pct", 0.30))
                    clean = float(c.get("extrinsic_clean_pct", 0.15))
                    crossed = None
                    if (prev_ext < pumped) != (today_ext < pumped):
                        crossed = (f"crossed the {pumped * 100:.0f}% pumped "
                                   f"threshold")
                    elif (prev_ext < clean) != (today_ext < clean):
                        crossed = (f"crossed the {clean * 100:.0f}% clean "
                                   f"threshold")
                    if crossed:
                        drivers.append(
                            f"extrinsic {prev_ext * 100:.0f}%→"
                            f"{today_ext * 100:.0f}% ({crossed})")
                    elif abs(today_ext - prev_ext) >= 0.05:
                        drivers.append(
                            f"extrinsic {prev_ext * 100:.0f}%→"
                            f"{today_ext * 100:.0f}%")
                except (TypeError, ValueError):
                    pass
            prev_iv = prior_entry.get("iv_rank")
            if prev_iv is not None and iv_rank is not None:
                try:
                    prev_iv = float(prev_iv)
                    today_iv = float(iv_rank)
                    elevated = float(c.get("iv_elevated_rank", 60))
                    if (prev_iv < elevated) != (today_iv < elevated):
                        drivers.append(
                            f"IV rank {prev_iv:.0f}→{today_iv:.0f} (crossed "
                            f"the {elevated:.0f} elevated threshold)")
                    elif abs(today_iv - prev_iv) >= 5:
                        drivers.append(
                            f"IV rank {prev_iv:.0f}→{today_iv:.0f}")
                except (TypeError, ValueError):
                    pass
            prev_int = prior_entry.get("intrinsic_per_share")
            if prev_int is not None:
                try:
                    prev_int = float(prev_int)
                    if prev_int <= 0 < anatomy.intrinsic_per_share:
                        drivers.append(
                            f"went ITM — intrinsic $0→"
                            f"${anatomy.intrinsic_per_share:.2f}/sh")
                    elif anatomy.intrinsic_per_share <= 0 < prev_int:
                        drivers.append(
                            f"back OTM — intrinsic ${prev_int:.2f}→$0/sh")
                except (TypeError, ValueError):
                    pass
            prev_dte = prior_entry.get("dte")
            if prev_dte is not None and dte is not None:
                try:
                    prev_dte = int(prev_dte)
                    today_dte = int(dte)
                    min_roll = int(c.get("min_dte_for_roll", 21))
                    if (prev_dte >= min_roll) != (today_dte >= min_roll):
                        side = ("left" if today_dte < min_roll
                                else "entered")
                        drivers.append(
                            f"DTE {prev_dte}→{today_dte} ({side} the "
                            f"≥{min_roll}d roll window)")
                except (TypeError, ValueError):
                    pass
            prev_es = prior_entry.get("earnings_state")
            if (prev_es and anatomy.earnings_state
                    and prev_es != anatomy.earnings_state):
                drivers.append(
                    f"earnings state {prev_es}→{anatomy.earnings_state}")
        if drivers:
            return (f"{indent}- _Verdict changed vs yesterday: {prev} → "
                    f"{anatomy.verdict} — {', '.join(drivers)}._")
        ps = _VERDICT_SHORT.get(prev, prev)
        ns = _VERDICT_SHORT.get(anatomy.verdict, anatomy.verdict)
        return (f"{indent}- _Verdict changed vs yesterday ({ps} → {ns}) — "
                f"drivers not fully attributable; treat both as defensible "
                f"and decide by intent._")
    except Exception:  # noqa: BLE001 — advisory layer, never break a card
        return None


def persist_exit_verdicts(snapshot_dir, verdicts: dict, date_str: str) -> None:
    """Write {contract: entry} for this run to <snapshot_dir>/exit_verdicts.json.
    Entries are rich dicts from anatomy_persist_entry (2026-08-06) — the
    legacy bare-string shape is still accepted by every reader (verdict_of).
    Fail-open: any error is swallowed (advisory persistence)."""
    try:
        import json
        from pathlib import Path
        p = Path(snapshot_dir) / EXIT_VERDICTS_FILENAME
        p.write_text(json.dumps({"date": date_str, "verdicts": verdicts},
                                indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001 — advisory layer
        pass


def load_prior_exit_verdicts(snapshot_root, date_str: str) -> dict | None:
    """Latest prior day's {"date", "verdicts"} from the snapshot store, or
    None. Skips .test dirs; fail-open on any error."""
    try:
        import json
        from pathlib import Path
        root = Path(snapshot_root)
        if not root.exists():
            return None
        candidates = sorted(
            (c for c in root.iterdir()
             if c.is_dir() and _DATE_DIR_RE_XC.match(c.name)
             and c.name < str(date_str)
             and (c / EXIT_VERDICTS_FILENAME).exists()),
            key=lambda c: c.name, reverse=True)
        for c in candidates:
            try:
                data = json.loads(
                    (c / EXIT_VERDICTS_FILENAME).read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("verdicts"), dict):
                    return {"date": data.get("date") or c.name,
                            "verdicts": data["verdicts"]}
            except Exception:  # noqa: BLE001
                continue
        return None
    except Exception:  # noqa: BLE001
        return None


def build_fable_context(
    options_reviews: list,
    snapshot_data: dict | None,
    config: dict | None = None,
    equity_reviews: list | None = None,
    today=None,
) -> str:
    """Compact exit-cost anatomy summary for the Fable advisor prompt.

    One line per ITM/underwater short put with a live chain quote this
    cycle. Empty string when nothing qualifies (fail-open).
    """
    c = _cfg(config)
    if not c.get("enabled", True):
        return ""
    lines: list[str] = []
    quotes = (snapshot_data or {}).get("quotes", {}) or {}
    chains = (snapshot_data or {}).get("chains", {}) or {}
    iv_ranks = (snapshot_data or {}).get("iv_ranks", {}) or {}
    earnings_cal = (snapshot_data or {}).get("earnings_calendar", {}) or {}
    for rev in options_reviews or []:
        try:
            if (rev.get("type") or "").upper() != "PUT":
                continue
            if float(rev.get("qty", 0) or 0) >= 0:
                continue
            und = rev.get("underlying") or str(rev.get("contract", "")).split("_")[0]
            spot = float((quotes.get(und) or {}).get("last") or 0)
            strike = float(rev.get("strike") or 0)
            entry = float(rev.get("entry_price") or 0)
            mid = float(rev.get("current_mid") or 0)
            if not (spot and strike):
                continue
            if not (spot < strike or (entry > 0 and mid > entry)):
                continue
            quote = chain_quote_for_position(rev, chains)
            if quote is None:
                continue
            anatomy = analyze_exit_cost(
                rev, quote, spot,
                iv_rank=iv_ranks.get(und),
                earnings_date=earnings_cal.get(und),
                today=today,
                config=config,
                loss_stop_fired="GUARDRAIL_LOSS_STOP" in (rev.get("matrix_cell_id") or ""),
                concentration_ok=assignment_concentration_ok(
                    rev, snapshot_data, equity_reviews),
            )
            if anatomy is None:
                continue
            lines.append(
                f"- {anatomy.contract}: BTC mid ${anatomy.btc_mid:.2f} = "
                f"${anatomy.intrinsic_total:,.0f} intrinsic + "
                f"${anatomy.extrinsic_total:,.0f} extrinsic "
                f"({anatomy.extrinsic_total / max(anatomy.btc_mid * 100 * anatomy.qty, 0.01) * 100:.0f}%); "
                f"spread ${anatomy.spread_per_share:.2f} "
                f"({anatomy.spread_pct_of_mid * 100:.1f}%); "
                f"verdict {anatomy.verdict} — {anatomy.verdict_reason}"
            )
        except Exception:
            continue
    return "\n".join(lines)
