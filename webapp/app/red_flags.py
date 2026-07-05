"""Compute portfolio-level red flags from the briefing model.

The pipeline emits a "Red Flags & Priorities" panel inside the briefing
markdown body, but the briefing JSON doesn't expose those structurally.
This helper recomputes the most important flags from already-available
fields so the dashboard can surface a banner without parsing markdown.

Mirrors the thresholds in `portfolio-briefing/skills/daily-portfolio-briefing/
config/briefing.yaml` (hard rules #13, #21):
- stress coverage < 0.50× → CRITICAL (new short puts blocked)
- stress coverage < 0.70× → WARNING (rebuilding zone)
- cash_pct < 0.05 → CRITICAL (cash floor — post-margin-call discipline)
- single-Friday short-put obligation ≥ 30% NLV → CRITICAL (concentration)
- single-Friday short-put obligation ≥ 20% NLV → WARNING

Output shape matches what `dashboard.html` renders:

    [
        {"severity": "critical", "icon": "🚨", "title": "Stress coverage 0.07×",
         "detail": "Below 0.50× critical threshold — new short puts blocked.",
         "jump_to": "#red-flags"},
        ...
    ]
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


# Severity → render hint (CSS tone + icon)
_SEVERITY_META = {
    "critical": {"icon": "🚨", "tone": "bad"},
    "warning":  {"icon": "📊", "tone": "warn"},
    "info":     {"icon": "ℹ️", "tone": "info"},
}


def _flag(severity: str, title: str, detail: str, jump_to: str | None = None) -> dict[str, Any]:
    meta = _SEVERITY_META.get(severity, _SEVERITY_META["info"])
    return {
        "severity": severity,
        "tone": meta["tone"],
        "icon": meta["icon"],
        "title": title,
        "detail": detail,
        "jump_to": jump_to,
    }


def compute_red_flags(
    briefing: Any,
    coverage: float | None,
    *,
    coverage_critical: float = 0.50,
    coverage_warning: float = 0.70,
    cash_floor: float = 0.05,
    bucket_critical_pct: float = 0.30,
    bucket_warning_pct: float = 0.20,
    stale_action_days: int = 3,
) -> list[dict[str, Any]]:
    """Compute the ordered list of red flags from a Briefing model.

    Returns empty list when there's nothing to flag (good state).
    Flags are returned in severity order: critical > warning > info.
    """
    flags: list[dict[str, Any]] = []

    if briefing is None:
        return flags

    # ─── Stress coverage ──────────────────────────────────────────────────
    if coverage is not None:
        if coverage < coverage_critical:
            flags.append(_flag(
                "critical",
                f"Stress coverage {coverage:.2f}×",
                f"Below {coverage_critical:.2f}× critical threshold — new short puts blocked (hard rule #13). "
                f"Defensive mode active.",
                jump_to=f"/briefing/{briefing.date}",
            ))
        elif coverage < coverage_warning:
            flags.append(_flag(
                "warning",
                f"Stress coverage {coverage:.2f}× (rebuilding)",
                f"Below {coverage_warning:.2f}× rebuild target — favor closes over opens "
                f"until coverage returns to target.",
                jump_to=f"/briefing/{briefing.date}",
            ))

    # ─── Cash floor ───────────────────────────────────────────────────────
    cash_pct = briefing.cash_pct if hasattr(briefing, "cash_pct") else None
    if cash_pct is not None and cash_pct < cash_floor:
        flags.append(_flag(
            "critical",
            f"Cash {cash_pct * 100:.1f}% NLV (below floor)",
            f"Below {cash_floor * 100:.0f}% cash floor — post-margin-call discipline. "
            f"No new equity adds; prioritize closes.",
            jump_to=f"/briefing/{briefing.date}",
        ))

    # ─── Single-Friday short-put bucket concentration (hard rule #21) ────
    bucket_critical, bucket_warning = _expiration_bucket_flags(
        briefing,
        critical_pct=bucket_critical_pct,
        warning_pct=bucket_warning_pct,
    )
    flags.extend(bucket_critical)
    flags.extend(bucket_warning)

    # ─── Stale actions (UX critique #8) ──────────────────────────────────
    # Any actionable item that has been on the queue ≥ 3 days is a signal
    # the user's blocked, deferring, or forgot. Info-severity by default;
    # ≥ 6 days escalates to warning.
    for a in getattr(briefing, "actions", []) or []:
        days = getattr(a, "days_flagged", None)
        if days is None or days < stale_action_days:
            continue
        kind = getattr(a, "kind", "action")
        ident = getattr(a, "ident", "?")
        if days >= 6:
            flags.append(_flag(
                "warning",
                f"Stale action: {kind} {ident} ({days}d)",
                f"On the queue for {days} days without resolution — either close it, "
                f"reject it, or add a note about why it's blocked.",
                jump_to=f"/briefing/{briefing.date}#actions",
            ))
        else:
            flags.append(_flag(
                "info",
                f"Aging action: {kind} {ident} ({days}d)",
                f"On the queue for {days} days — review whether it's still relevant.",
                jump_to=f"/briefing/{briefing.date}#actions",
            ))

    # Sort: critical → warning → info
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    flags.sort(key=lambda f: severity_order.get(f["severity"], 3))
    return flags


def _expiration_bucket_flags(
    briefing: Any,
    *,
    critical_pct: float,
    warning_pct: float,
) -> tuple[list[dict], list[dict]]:
    """Find expiration dates where short-put collateral ≥ thresholds % NLV.

    A short put's "collateral" is strike × 100 × |qty| (assignment cost).
    Bucket by expiration date; flag dates where the bucket exceeds the
    threshold.
    """
    nlv = (briefing.nlv or 0) if hasattr(briefing, "nlv") else 0
    if not nlv:
        return [], []

    buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"collateral": 0.0, "tickers": []}
    )
    for o in getattr(briefing, "options_reviews", []) or []:
        # Only short puts (qty < 0 AND type PUT) contribute to obligation
        qty = getattr(o, "qty", None) or getattr(o, "quantity", None)
        opt_type = (getattr(o, "type", None) or getattr(o, "opt_type", "")).upper()
        strike = getattr(o, "strike", None)
        expiration = getattr(o, "expiration", None)
        if not (qty and strike and expiration):
            continue
        if qty >= 0 or "PUT" not in opt_type:
            continue
        coll = abs(qty) * float(strike) * 100
        exp_str = str(expiration)
        buckets[exp_str]["collateral"] += coll
        buckets[exp_str]["tickers"].append(getattr(o, "underlying", "?"))

    critical: list[dict] = []
    warning: list[dict] = []
    for exp_date, data in sorted(buckets.items()):
        pct = data["collateral"] / nlv
        top = ", ".join(sorted(set(data["tickers"]))[:4])
        if pct >= critical_pct:
            critical.append(_flag(
                "critical",
                f"Expiration cluster {exp_date}: {pct * 100:.0f}% NLV",
                f"Short-put obligation ≥ {critical_pct * 100:.0f}% NLV on a single date "
                f"({top}{'…' if len(set(data['tickers'])) > 4 else ''}). "
                f"Hard rule #21 — close or roll to de-concentrate.",
                jump_to=f"/history#expiration-ladder",
            ))
        elif pct >= warning_pct:
            warning.append(_flag(
                "warning",
                f"Expiration cluster {exp_date}: {pct * 100:.0f}% NLV",
                f"Approaching {critical_pct * 100:.0f}% CRITICAL threshold "
                f"({top}{'…' if len(set(data['tickers'])) > 4 else ''}). "
                f"Hard rule #21.",
                jump_to=f"/history#expiration-ladder",
            ))
    return critical, warning


def summary_counts(flags: list[dict]) -> dict[str, int]:
    """Quick aggregate for the topline strip ('🚨 1 critical · 📊 3 warnings')."""
    out: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
    for f in flags:
        sev = f.get("severity", "info")
        out[sev] = out.get(sev, 0) + 1
    return out
