"""Momentum-hold overlay — don't close a winner while the underlying rips.

George (2026-08-17): "I feel like we're trying to get out of our options a
little too early. It feels like IREN is ripping. SNDK was ripping... If
something is ripping and it didn't get out to RSI, say, to 70 or something.
Do I really need to close? Validate it, and let's make sure we squeeze as
much as possible out of these options."

Validated evidence: Friday's briefing said close SNDK at +33%; held through
the rip, Monday +62% (+$1,280 in 3 sessions).

The contract — ``momentum_ride(review, snapshot_data, config)`` returns
``(ride: bool, reason: str)``. A yield-motivated close / take-profit
recommendation on a SHORT PUT defers to "🏇 RIDE — momentum hold" when ALL
of:

  (a) underlying trend UP — live/day move ≥ 0 AND 3-session move > 0,
      measured from the live quote (yfinance quote, broker-position price
      fallback — the rule-#46 vintage machinery) against the technicals'
      close series. Unmeasurable momentum fails OPEN to ``(False, ...)`` —
      no fabricated momentum ever rides (rule #19).
  (b) vintage-resolved RSI < ``rsi_stall`` (default 65) — not yet extended.
      The RSI comes from ``vintage_guard.resolve_new_open_rsi`` (live
      recompute on >5% drift); an unresolvable RSI → no ride.
  (c) OTM cushion ≥ ``min_otm_pct`` (default 10%) — the gamma-safety floor.
  (d) capture < ``hard_ceiling_pct`` (default 85%) — at/above always closes.
  (e) NOT risk-driven — callers consult :func:`risk_exempt` FIRST. Over-cap
      concentration (the 2026-08-17 SNDK/SOXX fix), loss stops / crash /
      tail-risk cells, CLOSE NOW / CLOSE_URGENT verdicts, earnings inside
      the contract window, and the gamma escape (DTE ≤ 10 @ ≥ 30%) all keep
      closing exactly as today; the over-cap risk exemption ALWAYS overrides
      momentum.

The EXIT TRIGGER is the ride's own reason string — explicit and measured:
first red day, RSI ≥ stall, or the capture ceiling. When a previously
riding-class position prints red (or RSI reaches the stall) while the other
ride conditions still hold, ``momentum_ride`` returns ``(False,
"stalled — ...")`` and the caller appends "(momentum stalled — take it)"
to the returning close recommendation.

EARNINGS BASIS-CUSHION nuance (the IREN case): :func:`earnings_hold`
answers the CLOSE BEFORE EARNINGS path's question — when the position's
assignment-basis cushion (spot vs basis = strike − entry premium) is ≥
``earnings_hold_min_cushion_pct`` (default 25%), the willing-owner fortress
holds THROUGH the print: a -25% print still assigns above basis
(spot × (1 − 25%) ≥ basis ⟺ cushion ≥ 25% — the claim is mathematically
backed by the threshold). Cushion below threshold / unmeasurable → None,
close-before-earnings exactly as today. Measured numbers only (rule #19).

Config: ``briefing.yaml`` → ``momentum_hold`` (enabled, rsi_stall,
min_otm_pct, hard_ceiling_pct, earnings_hold_min_cushion_pct).
``enabled: false`` / block missing → callers skip entirely (byte-identical
legacy).
"""

from __future__ import annotations

try:
    from analysis.vintage_guard import (
        _live_price,
        broker_price_map,
        resolve_new_open_rsi,
    )
except ImportError:  # pragma: no cover — standalone/test path fallback
    from vintage_guard import (
        _live_price,
        broker_price_map,
        resolve_new_open_rsi,
    )

_DEFAULT_RSI_STALL = 65.0
_DEFAULT_MIN_OTM_PCT = 0.10
_DEFAULT_HARD_CEILING = 0.85
_DEFAULT_EARNINGS_HOLD_MIN_CUSHION_PCT = 25.0
# Same risk-cell family the redeploy-aware TP gate exempts — these closes
# are about RISK, never about yield, and always fire.
_RISK_CELL_TOKENS = ("LOSS_STOP", "HARD_CEILING", "GAMMA_ESCAPE",
                     "EARNINGS_IMMINENT", "CRASH_STOP", "TAIL_RISK")


def _cfg(config: dict | None) -> dict:
    if not isinstance(config, dict):
        return {}
    block = config.get("momentum_hold")
    return block if isinstance(block, dict) else {}


def enabled(config: dict | None) -> bool:
    """True only when briefing.yaml opts in (block missing → legacy off)."""
    return bool(_cfg(config).get("enabled"))


def _float_cfg(config: dict | None, key: str, default: float,
               lo: float = 0.0, hi: float = float("inf")) -> float:
    try:
        v = float(_cfg(config).get(key, default))
    except (TypeError, ValueError):
        return default
    return v if lo < v <= hi else default


def rsi_stall(config: dict | None) -> float:
    """RSI at/above which the ride stalls (default 65)."""
    return _float_cfg(config, "rsi_stall", _DEFAULT_RSI_STALL, 0.0, 100.0)


def min_otm_pct(config: dict | None) -> float:
    """Gamma-safety OTM cushion floor (fraction, default 0.10)."""
    return _float_cfg(config, "min_otm_pct", _DEFAULT_MIN_OTM_PCT, 0.0, 1.0)


def hard_ceiling_pct(config: dict | None) -> float:
    """Capture at/above which a winner ALWAYS closes (fraction, 0.85)."""
    return _float_cfg(config, "hard_ceiling_pct", _DEFAULT_HARD_CEILING,
                      0.0, 1.0)


def earnings_hold_min_cushion_pct(config: dict | None) -> float:
    """Assignment-basis cushion (percent of spot) at/above which the
    willing-owner fortress holds through the print (default 25)."""
    return _float_cfg(config, "earnings_hold_min_cushion_pct",
                      _DEFAULT_EARNINGS_HOLD_MIN_CUSHION_PCT, 0.0, 100.0)


def _underlying(review: dict) -> str:
    return (review.get("underlying")
            or str(review.get("contract", "")).split("_")[0] or "").upper()


def _capture_pct(review: dict) -> float | None:
    try:
        entry = float(review.get("entry_price") or 0)
        mid = float(review.get("current_mid") or 0)
    except (TypeError, ValueError):
        return None
    if entry <= 0:
        return None
    return (entry - mid) / entry * 100.0


def _live_spot(und: str, snapshot_data: dict | None) -> float | None:
    """Live drift reference: yfinance quote first, E*TRADE position price
    fallback (rule #46). None → momentum is unmeasurable this cycle."""
    snap = snapshot_data or {}
    live = _live_price((snap.get("quotes") or {}).get(und))
    if live is None:
        live = broker_price_map(snap.get("positions")).get(und)
    return live


def risk_exempt(review: dict | None, snapshot_data: dict | None,
                config: dict | None, *,
                capture_pct: float | None = None,
                days_to_earnings: int | None = None,
                dte: int | None = None,
                anatomy_verdict: str | None = None,
                gamma_escape: bool = False) -> str | None:
    """The reason a close is RISK-driven (momentum never holds it), or None.

    Reuses the existing exemption lists — the over-cap concentration
    exemption (rule #43, 2026-08-17 SNDK/SOXX: ``redeploy_path.
    over_cap_risk_exemption``) ALWAYS overrides momentum; loss-stop /
    crash / tail-risk / earnings-imminent guardrail cells, an explicit
    CLOSE NOW / CLOSE_URGENT verdict, earnings inside the contract window
    (``exit_cost.gtc_min_days_to_earnings``, default 30, and before
    expiry), the gamma escape (DTE ≤ 10 @ capture ≥ 30%), and the hard
    capture ceiling all keep closing exactly as today.
    """
    if not isinstance(review, dict):
        return None
    # Over-cap obligation-inclusive concentration — the strongest override.
    try:
        try:
            from analysis.redeploy_path import over_cap_risk_exemption
        except ImportError:  # pragma: no cover — standalone/test fallback
            from redeploy_path import over_cap_risk_exemption
        overcap = over_cap_risk_exemption(review, snapshot_data, config)
        if overcap:
            return overcap
    except Exception:
        pass  # advisory — missing tier data never blocks the other checks
    cell = (review.get("matrix_cell_id") or "").upper()
    if any(t in cell for t in _RISK_CELL_TOKENS):
        return f"risk guardrail cell `{review.get('matrix_cell_id')}`"
    if anatomy_verdict == "CLOSE_URGENT" \
            or (review.get("recommendation") or "").upper() == "CLOSE_NOW":
        return "urgent close verdict"
    if capture_pct is None:
        capture_pct = _capture_pct(review)
    if gamma_escape or (dte is not None and 0 <= int(dte) <= 10
                        and (capture_pct or 0) >= 30.0):
        return f"gamma escape (DTE {int(dte) if dte is not None else '?'} ≤ 10)"
    if capture_pct is not None \
            and capture_pct >= hard_ceiling_pct(config) * 100.0:
        return (f"capture {capture_pct:.0f}% ≥ "
                f"{hard_ceiling_pct(config) * 100:.0f}% hard ceiling")
    try:
        gtc_min = int(((config or {}).get("exit_cost") or {})
                      .get("gtc_min_days_to_earnings", 30))
    except (TypeError, ValueError):
        gtc_min = 30
    if days_to_earnings is not None and 0 <= days_to_earnings <= gtc_min \
            and (dte is None or days_to_earnings <= dte):
        return (f"earnings in {int(days_to_earnings)}d inside the contract "
                f"window")
    return None


def momentum_ride(review: dict | None, snapshot_data: dict | None,
                  config: dict | None) -> tuple[bool, str]:
    """Should this yield-motivated short-put close defer to the ride?

    Returns ``(True, measured ride reason with the explicit exit trigger)``
    when every condition holds; ``(False, "stalled — ...")`` when a
    riding-class position printed red / hit the RSI stall (the caller
    appends "(momentum stalled — take it)"); ``(False, other reason)``
    otherwise. Fail-open FALSE on any unmeasurable input — momentum is
    never fabricated (rule #19).
    """
    if not enabled(config) or not isinstance(review, dict):
        return False, ""
    if (review.get("type") or "").upper() != "PUT":
        return False, "call side — overlay is put-only"
    und = _underlying(review)
    if not und:
        return False, "no underlying"
    capture = _capture_pct(review)
    if capture is None:
        return False, "capture unmeasurable"
    ceiling = hard_ceiling_pct(config) * 100.0
    if capture >= ceiling:
        return False, f"capture {capture:.0f}% ≥ {ceiling:.0f}% ceiling"

    snap = snapshot_data or {}
    tech = (snap.get("technicals") or {}).get(und) or {}
    tech = tech if isinstance(tech, dict) else {}
    live = _live_spot(und, snap)
    try:
        tech_close = float(tech.get("spot"))
    except (TypeError, ValueError):
        tech_close = 0.0
    if live is None or tech_close <= 0:
        return False, "momentum unmeasurable (no live quote this cycle)"
    day_pct = (live - tech_close) / tech_close * 100.0

    closes = tech.get("rsi_closes") or tech.get("recent_closes") or []
    try:
        ref3 = float(closes[-3]) if len(closes) >= 3 else 0.0
    except (TypeError, ValueError):
        ref3 = 0.0
    if ref3 <= 0:
        return False, "momentum unmeasurable (no 3-session close series)"
    three_pct = (live - ref3) / ref3 * 100.0

    # OTM cushion at LIVE spot — the gamma-safety floor.
    try:
        strike = float(review.get("strike") or 0)
    except (TypeError, ValueError):
        strike = 0.0
    if strike <= 0:
        return False, "no strike — cushion unmeasurable"
    otm_pct = (live - strike) / live * 100.0
    min_otm = min_otm_pct(config) * 100.0

    # Vintage-resolved RSI (rule #47 machinery — live recompute on drift).
    resolved = resolve_new_open_rsi(
        und, snap.get("technicals"), snap.get("quotes"),
        snap.get("positions"), config)
    rsi = resolved.get("rsi")
    try:
        rsi = float(rsi) if rsi is not None else None
    except (TypeError, ValueError):
        rsi = None
    if rsi is None:
        return False, "RSI unverifiable this cycle — no ride on stale RSI"
    stall = rsi_stall(config)

    if otm_pct < min_otm:
        return False, (f"OTM cushion {otm_pct:.0f}% < {min_otm:.0f}% "
                       f"gamma-safety floor")

    # Stall detection: a riding-class trend (3-session up, cushion/capture
    # fine) that printed red today or reached the RSI stall — the caller
    # appends "(momentum stalled — take it)" to the returning close.
    if three_pct > 0 and day_pct < 0:
        return False, f"stalled — red day ({und} {day_pct:+.1f}% today)"
    if three_pct > 0 and rsi >= stall:
        return False, f"stalled — RSI {rsi:.0f} ≥ {stall:g} stall"
    if not (day_pct >= 0 and three_pct > 0):
        return False, (f"trend not up ({und} {day_pct:+.1f}% today, "
                       f"{three_pct:+.1f}% 3-session)")

    return True, (
        f"{und} {day_pct:+.1f}% today / RSI {rsi:.0f} rising "
        f"(3-session {three_pct:+.1f}%) · {otm_pct:.0f}% OTM · close on "
        f"FIRST RED DAY or RSI ≥ {stall:g} or {ceiling:.0f}% capture"
    )


def earnings_hold(review: dict | None, snapshot_data: dict | None,
                  config: dict | None) -> dict | None:
    """The willing-owner fortress case for the CLOSE BEFORE EARNINGS path.

    Returns measured numbers when the assignment-basis cushion (spot vs
    basis = strike − entry premium) is ≥ ``earnings_hold_min_cushion_pct``:
    ``{"basis", "spot", "cushion_pct", "threshold_pct"}``. None when below
    threshold or unmeasurable — close-before-earnings exactly as today
    (rule #19: no fabricated cushion ever holds through a print).
    """
    if not enabled(config) or not isinstance(review, dict):
        return None
    if (review.get("type") or "").upper() != "PUT":
        return None
    try:
        strike = float(review.get("strike") or 0)
        entry = float(review.get("entry_price") or 0)
    except (TypeError, ValueError):
        return None
    if strike <= 0 or entry <= 0:
        return None
    basis = strike - entry
    if basis <= 0:
        return None
    und = _underlying(review)
    spot = _live_spot(und, snapshot_data)
    if spot is None:
        tech = ((snapshot_data or {}).get("technicals") or {}).get(und) or {}
        try:
            spot = float(tech.get("spot")) if isinstance(tech, dict) else None
        except (TypeError, ValueError):
            spot = None
    if spot is None or spot <= 0:
        return None
    cushion_pct = (spot - basis) / spot * 100.0
    threshold = earnings_hold_min_cushion_pct(config)
    if cushion_pct < threshold:
        return None
    return {
        "basis": basis,
        "spot": spot,
        "cushion_pct": cushion_pct,
        "threshold_pct": threshold,
    }
