"""
Step 3: Classify market regime

Implements the deterministic regime classifier from references/regime_framework.md.
Inputs come from Step 2's snapshot (live yfinance data for VIX/SPY) plus yesterday's
regime label loaded for stickiness. Returns one of: RISK_ON / NORMAL / CAUTION / RISK_OFF.

The 11-rule version is documented in docs/09-regime-framework.md. v1 here implements
the subset that's grounded in data we actually have (VIX, SPY day change, SPY 5d
cumulative). News/breadth/distribution-day rules will plug in once those feeds are
wired (v1.1) — they're optional inputs the framework already supports.
"""

import json
from datetime import datetime
from pathlib import Path


# Thresholds from docs/06-wheel-parameters.md section 12 (verbatim from wheelhouz)
VIX_ATTACK_MAX = 18.0       # < this: RISK_ON / ATTACK
VIX_HOLD_MAX = 25.0         # < this: NORMAL / HOLD
VIX_DEFEND_MAX = 35.0       # < this: CAUTION / DEFEND; ≥ this: RISK_OFF / CRISIS
SPY_ELEVATED_DROP = -0.02   # -2%
SPY_SEVERE_DROP = -0.03     # -3%
SPY_CRISIS_DROP = -0.05     # -5%
SPY_EXTREME_DROP = -0.08    # -8%
VIX_RAPID_SPIKE_PCT = 0.25  # +25% intraday on VIX = caution


def _load_yesterday_regime(snapshot_dir: Path) -> str:
    """Try to load yesterday's regime from prior snapshot for stickiness."""
    parent = snapshot_dir.parent
    if not parent.exists():
        return "NORMAL"
    candidates = sorted(
        [p for p in parent.iterdir() if p.is_dir() and p.name < snapshot_dir.name],
        reverse=True,
    )
    for cand in candidates[:5]:
        regime_file = cand / "regime.json"
        if regime_file.exists():
            try:
                with open(regime_file) as f:
                    return json.load(f).get("regime", "NORMAL")
            except Exception:
                continue
    return "NORMAL"


def classify_regime(snapshot_dir: Path, snapshot_data: dict) -> dict:
    """Classify market regime from live VIX/SPY snapshot."""

    quotes = snapshot_data.get("quotes", {})

    vix_quote = quotes.get("^VIX") or quotes.get("VIX") or {}
    spy_quote = quotes.get("SPY") or {}

    vix_last = vix_quote.get("last")
    vix_day_change_pct = vix_quote.get("dayChangePct")
    spy_day_change_pct = spy_quote.get("dayChangePct")
    spy_5d_change_pct = spy_quote.get("fiveDayChangePct")

    yesterday_regime = _load_yesterday_regime(snapshot_dir)

    inputs = {
        "vix_last": vix_last,
        "vix_day_change_pct": vix_day_change_pct,
        "spy_day_change_pct": spy_day_change_pct,
        "spy_5d_change_pct": spy_5d_change_pct,
        "yesterday_regime": yesterday_regime,
    }

    # If VIX is unavailable, we can't classify — abort upstream rather than guess
    if vix_last is None:
        regime_output = {
            "regime": yesterday_regime or "NORMAL",
            "confidence": "LOW",
            "inputs_at_evaluation": inputs,
            "triggered_rules": [
                {"rule_id": "VIX_UNAVAILABLE_FALLBACK", "rationale": "VIX quote missing; held yesterday's regime"}
            ],
            "stickiness_applied": True,
            "evaluation_time": datetime.utcnow().isoformat() + "Z",
            "valid": False,
        }
        with open(snapshot_dir / "regime.json", "w") as f:
            json.dump(regime_output, f, indent=2)
        print(f"  Regime: {regime_output['regime']} (LOW confidence — VIX unavailable)")
        return regime_output

    # Classification rules in priority order — first match wins
    fired_rule = None
    target_regime = None

    # Rule 1: VIX extreme high → RISK_OFF
    if vix_last >= VIX_DEFEND_MAX:
        fired_rule = ("VIX_EXTREME_HIGH", f"VIX {vix_last} ≥ {VIX_DEFEND_MAX}")
        target_regime = "RISK_OFF"
    # Rule 2: SPY extreme drop → RISK_OFF (circuit breaker)
    elif spy_day_change_pct is not None and spy_day_change_pct <= SPY_EXTREME_DROP:
        fired_rule = ("SPY_EXTREME_DROP", f"SPY {spy_day_change_pct:.2%} ≤ {SPY_EXTREME_DROP:.0%}")
        target_regime = "RISK_OFF"
    # Rule 3: SPY crisis drop → RISK_OFF
    elif spy_day_change_pct is not None and spy_day_change_pct <= SPY_CRISIS_DROP:
        fired_rule = ("SPY_CRISIS_DROP", f"SPY {spy_day_change_pct:.2%} ≤ {SPY_CRISIS_DROP:.0%}")
        target_regime = "RISK_OFF"
    # Rule 4: VIX rapid spike intraday → CAUTION
    elif (
        vix_day_change_pct is not None
        and vix_day_change_pct >= VIX_RAPID_SPIKE_PCT
        and vix_last >= VIX_HOLD_MAX
    ):
        fired_rule = ("VIX_RAPID_SPIKE", f"VIX +{vix_day_change_pct:.1%} intraday and ≥ {VIX_HOLD_MAX}")
        target_regime = "CAUTION"
    # Rule 5: SPY severe drop → CAUTION
    elif spy_day_change_pct is not None and spy_day_change_pct <= SPY_SEVERE_DROP:
        fired_rule = ("SPY_SEVERE_DROP", f"SPY {spy_day_change_pct:.2%} ≤ {SPY_SEVERE_DROP:.0%}")
        target_regime = "CAUTION"
    # Rule 6: VIX in defend zone (25-35) → CAUTION
    elif vix_last >= VIX_HOLD_MAX:
        fired_rule = ("VIX_DEFEND_ZONE", f"VIX {vix_last} in defend zone (≥ {VIX_HOLD_MAX})")
        target_regime = "CAUTION"
    # Rule 7: SPY elevated drop → NORMAL with watch flag
    elif spy_day_change_pct is not None and spy_day_change_pct <= SPY_ELEVATED_DROP:
        fired_rule = ("SPY_ELEVATED_DROP", f"SPY {spy_day_change_pct:.2%} ≤ {SPY_ELEVATED_DROP:.0%} (watch)")
        target_regime = "NORMAL"
    # Rule 8: 5d cumulative weakness on SPY → CAUTION even if today is fine
    elif spy_5d_change_pct is not None and spy_5d_change_pct <= -0.06:
        fired_rule = ("SPY_5D_WEAKNESS", f"SPY 5d {spy_5d_change_pct:.2%} ≤ -6%")
        target_regime = "CAUTION"
    # Rule 9: VIX below ATTACK threshold → RISK_ON
    elif vix_last < VIX_ATTACK_MAX:
        fired_rule = ("VIX_ATTACK_ZONE", f"VIX {vix_last} < {VIX_ATTACK_MAX}")
        target_regime = "RISK_ON"
    # Rule 10: default
    else:
        fired_rule = ("DEFAULT_NORMAL", f"VIX {vix_last} in normal range; no defensive triggers")
        target_regime = "NORMAL"

    # Stickiness: gating the down-step from defensive regimes
    sticky_carry = False
    sticky_reason = None
    final_regime = target_regime
    severity = {"RISK_OFF": 3, "CAUTION": 2, "NORMAL": 1, "RISK_ON": 0}
    if (severity.get(target_regime, 1) < severity.get(yesterday_regime, 1)):
        # Stepping down — apply hysteresis
        if yesterday_regime == "RISK_OFF" and target_regime != "RISK_OFF":
            # Need 2 normal sessions; for v1 we approximate with "stay in CAUTION on first non-RISK_OFF day"
            if target_regime == "NORMAL" or target_regime == "RISK_ON":
                sticky_carry = True
                sticky_reason = "First normal session after RISK_OFF — held in CAUTION pending second confirmation"
                final_regime = "CAUTION"
        elif yesterday_regime == "CAUTION" and target_regime == "RISK_ON":
            # Need 1 normal session before stepping all the way down
            sticky_carry = True
            sticky_reason = "Stepping CAUTION → RISK_ON in one day; held in NORMAL"
            final_regime = "NORMAL"

    confidence = "HIGH" if (vix_last is not None and spy_day_change_pct is not None) else "MEDIUM"

    regime_output = {
        "regime": final_regime,
        "confidence": confidence,
        "inputs_at_evaluation": inputs,
        "triggered_rules": [
            {"rule_id": fired_rule[0], "rationale": fired_rule[1]}
        ],
        "stickiness_applied": sticky_carry,
        "sticky_hold_reason": sticky_reason,
        "evaluation_time": datetime.utcnow().isoformat() + "Z",
        "valid": True,
    }

    with open(snapshot_dir / "regime.json", "w") as f:
        json.dump(regime_output, f, indent=2)

    print(f"  Regime: {regime_output['regime']} (confidence: {confidence}) — {fired_rule[1]}")
    if sticky_carry:
        print(f"  Stickiness applied: {sticky_reason}")

    return regime_output
