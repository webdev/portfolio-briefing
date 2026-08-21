"""Watch-panel ROLL ANALYSIS menu gate (George 2026-08-21).

George: "Sometimes I see recommendations way early when I'm out of the
money." The numbered action list was fully conformant that day (only
tested-strike rolls actionable; all OTM underwater puts HOLD) — the
"way early" illusion came from the Watch panel rendering a full priced
ROLL ANALYSIS candidate table under EVERY option position, including
deep-OTM HOLDs whose own headline said "strike not genuinely tested
(|δ| < 0.40) → HOLD".

This module is the single source of truth for WHEN the Watch panel's
priced roll menu renders. The menu appears only when the position's roll
machinery is actually engaged:

  1. strike tested — side-aware moneyness within 3% (put: spot ≤ 1.03 ×
     strike; call: spot ≥ 0.97 × strike) OR measured |δ| ≥ 0.40 (the same
     rule-#3 thresholds as render.panels);
  2. a live (non-HOLD, non-demoted) advisor recommendation exists this
     cycle — URGENT / DEFENSIVE / EXECUTE-ROLL / CLOSE headlines (a
     rule-#3-gate- or churn-guard-demoted roll is explicitly NOT engaged);
  3. an 🚨 URGENT commentary item fired for the position this cycle;
  4. the advisor's own candidate table recommends an actual roll
     (recommended_candidate_id other than A = HOLD);
  5. the credit window is 🟡 CLOSING (or debit-only) — "act while a
     credit remains" is a live decision.

Untriggered positions replace the table with ONE measured italic line
that says exactly when the menu returns (rule #24 — nothing hidden;
rule #19 — measured values only, δ n/a when unmeasured).

Fail-open on missing data: when NEITHER moneyness nor delta is
measurable this cycle, the menu is KEPT (never hide information on
missing data).

Config: briefing.yaml → roll.watch_menu_on_trigger_only.enabled.
Code default OFF = legacy byte-identical rendering.
"""

from __future__ import annotations

# Same thresholds as render.panels._ROLL_GATE_MONEYNESS / _ROLL_GATE_DELTA
# (duplicated as module constants to avoid a steps→render import cycle;
# both trace to CLAUDE.md rule #3).
MONEYNESS_BAND_PCT = 3.0     # within 3% of the strike = tested
DELTA_TESTED = 0.40          # measured |δ| at/above this = tested

_HOLD_FAMILY = {"HOLD", "WAIT", "LET_EXPIRE", ""}


def enabled(config: dict | None) -> bool:
    """True when roll.watch_menu_on_trigger_only.enabled is set in config.
    Code default OFF = legacy behavior (the full menu on every position)."""
    try:
        return bool(((config or {}).get("roll") or {})
                    .get("watch_menu_on_trigger_only", {})
                    .get("enabled", False))
    except (TypeError, AttributeError):
        return False


def _measured_delta(review: dict) -> float | None:
    """|δ| from the broker-measured per-position delta, or None."""
    try:
        d = review.get("delta")
        return abs(float(d)) if d is not None else None
    except (TypeError, ValueError):
        return None


def _moneyness(review: dict, snapshot_data: dict | None):
    """(moneyness, spot) where moneyness = spot / strike; (None, None)
    when spot or strike is unmeasurable this cycle (never fabricated)."""
    try:
        contract = str(review.get("contract") or "")
        und = review.get("underlying") or (
            contract.split("_")[0] if "_" in contract else "")
        spot = float((((snapshot_data or {}).get("quotes", {}) or {})
                      .get(und) or {}).get("last") or 0)
        strike = float(review.get("strike") or 0)
        if spot > 0 and strike > 0:
            return spot / strike, spot
    except (TypeError, ValueError, AttributeError):
        pass
    return None, None


def strike_tested(review: dict, snapshot_data: dict | None) -> bool | None:
    """True/False when measurable; None when NEITHER delta nor moneyness is
    measurable (callers fail open — keep the menu, never gate on a guess).

    Side-aware: short put tested when spot ≤ 1.03 × strike; short call
    tested when spot ≥ 0.97 × strike. Measured |δ| ≥ 0.40 tests either side.
    """
    d = _measured_delta(review)
    if d is not None and d >= DELTA_TESTED:
        return True
    m, _spot = _moneyness(review, snapshot_data)
    if m is None:
        return None if d is None else False
    opt_type = (review.get("type") or "").upper()
    band = MONEYNESS_BAND_PCT / 100.0
    if opt_type == "CALL":
        return m >= (1.0 - band)
    # PUT (and unknown type defaults to the put read — short puts dominate)
    return m <= (1.0 + band)


def menu_trigger(review: dict, commentary: list[str] | None,
                 snapshot_data: dict | None) -> dict:
    """Is this position's roll machinery actually engaged this cycle?

    Returns {"triggered": bool, "reason": str}. Fail-open: unmeasurable
    strike test (no spot AND no delta) → triggered ("menu kept — strike
    test unmeasurable this cycle").
    """
    tested = strike_tested(review, snapshot_data)
    if tested is None:
        return {"triggered": True,
                "reason": "strike test unmeasurable this cycle (menu kept)"}
    if tested:
        return {"triggered": True, "reason": "strike tested"}

    # Live advisor action (URGENT / DEFENSIVE / EXECUTE ROLL / CLOSE).
    # A rule-#3-gate- or churn-guard-demoted roll is explicitly NOT a live
    # action — the gate/guard already said "not engaged".
    rec = (review.get("recommendation") or "").upper()
    if (rec not in _HOLD_FAMILY
            and not review.get("_roll_gate_demotion")
            and not review.get("_churn_guard_demotion")):
        return {"triggered": True, "reason": f"live action ({rec})"}

    # 🚨 URGENT commentary fired for this position this cycle.
    if any("🚨" in (n or "") for n in commentary or []):
        return {"triggered": True, "reason": "urgent item this cycle"}

    # The advisor's own table recommends an actual roll (not A = HOLD).
    rec_id = review.get("recommended_candidate_id")
    if (review.get("roll_candidates") and rec_id
            and str(rec_id).upper() != "A"):
        return {"triggered": True,
                "reason": f"advisor recommends candidate {rec_id}"}

    # Credit window closing / debit-only — a live "act while a credit
    # remains" decision (same states _watch_needs_full_block treats as
    # action-class).
    try:
        _cw = (((snapshot_data or {}).get("_credit_windows") or {})
               .get(review.get("contract")) or {})
        if _cw.get("state") in ("closing", "debit_only"):
            return {"triggered": True,
                    "reason": f"credit window {_cw.get('state')}"}
    except (TypeError, AttributeError):
        pass

    return {"triggered": False, "reason": "not triggered"}


def not_triggered_line(review: dict, snapshot_data: dict | None) -> str | None:
    """The ONE measured italic line that replaces the table (rule #24 —
    nothing hidden; the line says exactly when the menu returns).

    Example: "_roll menu: not triggered — spot 6.2% above strike, δ 0.29;
    the priced menu appears when the strike is tested (within 3% /
    δ ≥ 0.40)_". Measured values only (rule #19): δ n/a when the broker
    supplied no delta; when spot is unquoted this cycle the line leans on
    the measured delta alone. Returns None when nothing is measurable
    (callers should have failed open and kept the menu)."""
    d = _measured_delta(review)
    m, _spot = _moneyness(review, snapshot_data)
    opt_type = (review.get("type") or "").upper()
    if m is not None:
        if opt_type == "CALL":
            dist_bit = f"spot {(1.0 - m) * 100:.1f}% below strike"
        else:
            dist_bit = f"spot {(m - 1.0) * 100:.1f}% above strike"
    elif d is not None:
        dist_bit = "spot unquoted this cycle"
    else:
        return None  # nothing measurable — fail open, keep the menu
    delta_bit = f"δ {d:.2f}" if d is not None else "δ n/a"
    return (
        f"_roll menu: not triggered — {dist_bit}, {delta_bit}; the priced "
        f"menu appears when the strike is tested (within "
        f"{MONEYNESS_BAND_PCT:g}% / δ ≥ {DELTA_TESTED:g})_"
    )
