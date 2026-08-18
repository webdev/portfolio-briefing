"""Willing-owner roll downgrade (hard rule #51 extension — the NOK case).

George (2026-08-18), on the NOK 10-lot card "🚨 URGENT — EXECUTE ROLL
NOK_PUT_11_20261218 — Calendar roll... +$115 net credit" with 122 DTE and
assignment basis $8.44 vs spot $10.29: "Why should I roll Nokia if it's in
December? It's kind of hard."

The strike-tested URGENT roll tier protects UNWILLING owners — the trader
who must defend the strike. For a WILLING owner (deep assignment-basis
cushion, no near event, long DTE) a same-strike/down CREDIT roll is an
optional income optimization, not an urgent defensive act: assignment at a
basis well below spot is a win. The full combo ticket stays on the card
(rule #24) for the days he feels like taking the credit; only the urgency
downgrades — "🚨 URGENT — EXECUTE ROLL" becomes "🔧 OPTIONAL — credit
extension (no action required)", sorted with the hold-class group.

Config: briefing.yaml → roll.willing_owner_downgrade
    enabled: false          # code default OFF — legacy byte-identical
    min_cushion_pct: 15     # (spot − basis)/spot floor. 15, not 20: the
                            # motivating NOK card measures (10.29 − 8.44)
                            # / 10.29 = 18.0% — a 20 floor would have left
                            # the very card that prompted the rule urgent.
    min_dte: 60
    earnings_clear_days: 21

Risk-driven rolls are NEVER downgraded: loss-stop / crash / tail-risk /
earnings-window guardrail cells and DEFENSIVE matrix cells stay urgent.
Debit-only willing-owner shapes are NOT this module's job — the existing
machinery (exit-cost HOLD_FOR_BASIS verdict, debit-to-collateral cap) is
already the GTC/hold voice there; a debit roll simply never downgrades to
"optional income" (``credit_dollars < 0`` → None → legacy handling).

Fail direction (rule #19): any unmeasurable input (spot, entry premium,
strike, DTE) → None — a downgrade is never fabricated on missing data; the
roll stays urgent.
"""

from __future__ import annotations

from typing import Optional

_DEFAULTS = {
    "enabled": False,
    "min_cushion_pct": 15.0,
    "min_dte": 60,
    "earnings_clear_days": 21,
}

# Matrix/guardrail cell tokens that mark a RISK-driven roll — these protect
# capital, not income, and are never downgraded regardless of cushion.
_RISK_CELL_TOKENS = ("LOSS_STOP", "CRASH", "TAIL", "EARNINGS", "DEFENSIVE")

OPTIONAL_PREFIX = "🔧 **OPTIONAL — credit extension (no action required)**"


def wo_settings(config: dict | None) -> dict:
    """Resolve roll.willing_owner_downgrade settings over the code defaults."""
    cfg = (((config or {}).get("roll") or {})
           .get("willing_owner_downgrade") or {})
    out = dict(_DEFAULTS)
    if isinstance(cfg, dict):
        for k in _DEFAULTS:
            if k in cfg:
                out[k] = cfg[k]
    return out


def assess_willing_owner_roll(
    *,
    option_type: str | None,
    strike: float | None,
    entry_premium: float | None,
    spot: float | None,
    dte: float | int | None,
    days_to_earnings: float | int | None,
    matrix_cell_id: str | None,
    credit_dollars: float | None,
    new_strike: float | None,
    config: dict | None = None,
) -> Optional[dict]:
    """Return the downgrade payload when the roll is optional income, else None.

    ALL of the following must hold (George's agreed design, 2026-08-18):
      * config ``roll.willing_owner_downgrade.enabled`` (default OFF)
      * short PUT side
      * NOT a risk-driven cell (loss-stop / crash / tail-risk / earnings /
        defensive) — those stay urgent, always
      * assignment-basis cushion (spot vs basis = strike − entry premium)
        ≥ ``min_cushion_pct``
      * DTE ≥ ``min_dte``
      * no KNOWN earnings within ``earnings_clear_days`` (a measured date
        inside the window keeps the roll urgent; no known date inside the
        window satisfies the condition)
      * the roll is a same-strike/down CREDIT extension (roll-UPs and debit
        rolls never read as optional income)

    Payload: {"basis", "cushion_pct", "spot", "gtc_half_price"} — every
    value measured from the inputs (rule #19).
    """
    s = wo_settings(config)
    if not s.get("enabled"):
        return None
    if (option_type or "").upper() != "PUT":
        return None
    cell = (matrix_cell_id or "").upper()
    if any(tok in cell for tok in _RISK_CELL_TOKENS):
        return None
    try:
        strike_f = float(strike or 0)
        entry_f = float(entry_premium or 0)
        spot_f = float(spot or 0)
        credit_f = float(credit_dollars if credit_dollars is not None else 0)
        new_strike_f = float(new_strike or 0) or strike_f
        dte_f = float(dte) if dte is not None else None
        min_cushion = float(s.get("min_cushion_pct", 15.0))
        min_dte = float(s.get("min_dte", 60))
        clear_days = float(s.get("earnings_clear_days", 21))
    except (TypeError, ValueError):
        return None
    # Unmeasurable position → stay urgent (rule #19 — never fabricate).
    if strike_f <= 0 or entry_f <= 0 or spot_f <= 0 or dte_f is None:
        return None
    # Same-strike / roll-down CREDIT extension only.
    if credit_f < 0 or new_strike_f > strike_f + 0.01:
        return None
    basis = strike_f - entry_f
    if basis <= 0:
        return None
    cushion_pct = (spot_f - basis) / spot_f * 100.0
    if cushion_pct < min_cushion or dte_f < min_dte:
        return None
    if days_to_earnings is not None:
        try:
            d2e = float(days_to_earnings)
        except (TypeError, ValueError):
            d2e = None
        if d2e is not None and 0 <= d2e <= clear_days:
            return None
    return {
        "basis": basis,
        "cushion_pct": cushion_pct,
        "spot": spot_f,
        "gtc_half_price": entry_f / 2.0,
    }


def format_willing_owner_line(
    payload: dict,
    credit_dollars: float | None,
    dte_added: float | int | None,
    exp_month: str | None = None,
) -> str:
    """The honest optional framing — measured numbers only (rule #19).

    e.g. "willing owner: basis $8.44 is 18% below spot $10.29; assignment
    in Dec is a win. Roll adds +$115/+28d if convenient; otherwise park
    the GTC at 50% ($1.28) or let it run."
    """
    ext = ""
    try:
        if dte_added:
            ext = f"/+{int(dte_added)}d"
    except (TypeError, ValueError):
        ext = ""
    when = f"in {exp_month}" if exp_month else "at expiry"
    return (
        f"willing owner: basis ${payload['basis']:,.2f} is "
        f"{payload['cushion_pct']:.0f}% below spot "
        f"${payload['spot']:,.2f}; assignment {when} is a win. Roll adds "
        f"+${float(credit_dollars or 0):,.0f}{ext} if convenient; "
        f"otherwise park the GTC at 50% "
        f"(${payload['gtc_half_price']:,.2f}) or let it run."
    )
