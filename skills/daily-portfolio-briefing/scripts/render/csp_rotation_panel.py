"""Render the CSP Rotations panel (task #20).

Mirrors the "Top 3 Closes to Raise Coverage" style: compact per-rotation
blocks, every number measured this cycle (rule #19 — no boilerplate),
dates rendered with weekday + year (rule #6).
"""

from __future__ import annotations

from datetime import date as _date


def _fmt_exp(exp_iso: str | None) -> str:
    """Weekday + year date format per hard rule #6; raw string if unparseable."""
    if not exp_iso:
        return "?"
    try:
        y, m, d = str(exp_iso)[:10].split("-")
        return _date(int(y), int(m), int(d)).strftime("%a %b %d '%y")
    except (ValueError, TypeError):
        return str(exp_iso)


def _fmt_strike(strike: float) -> str:
    return f"${strike:g}"


_BLOCK_LABELS = {
    "coverage_floor": "Coverage floor",
    "directive_hold": "Directive hold",
    "overlap": "Strike overlap (rule #40)",
    "earnings": "Earnings window",
    "stacking": "Single-name stacking",
    "bucket_concentration": "Expiration-bucket concentration",
}


def render_csp_rotations(rotations: list, nlv: float | None = None) -> list[str]:
    """Render the '🔄 CSP Rotations — Better Return on Capital' section.

    ``rotations``: a CSPRotationReport (qualified list + ``.near_miss``
    attribute), a plain list of CSPRotation dataclasses, or their to_dict()
    dicts. Task #21: near-misses (blocked by exactly one gate) always render
    so the panel stays informative — the bare empty note only appears when
    BOTH qualified and near-miss are empty (never silently hidden, rule #24).
    """
    lines = ["## 🔄 CSP Rotations — Better Return on Capital", ""]

    rows = []
    for r in rotations or []:
        rows.append(r.to_dict() if hasattr(r, "to_dict") else r)
    near = []
    for n in getattr(rotations, "near_miss", None) or []:
        near.append(n.to_dict() if hasattr(n, "to_dict") else n)

    if not rows and not near:
        lines.append(
            "_No coverage-neutral CSP rotations available today — every open-side "
            "candidate requires more collateral than the freeable closes provide "
            "(or no held CSP clears the ≥30% capture close bar). Consider single "
            "closes to raise coverage first (see 'Top 3 Closes to Raise Coverage')._"
        )
        lines.append("")
        return lines

    if not rows:
        lines.append(
            "_No rotation passes every discipline gate today — but the "
            "near-misses below were each blocked by a single constraint you "
            "could choose to revisit._"
        )
        lines.append("")
        lines.extend(_render_near_misses(near))
        return lines

    lines.append(
        f"_{len(rows)} opportunit{'y' if len(rows) == 1 else 'ies'} where closing a "
        f"lower-yield held CSP funds a higher-yield new entry. Each rotation is "
        f"coverage-neutral or better — no material new net obligation._"
    )
    lines.append("")

    for n, r in enumerate(rows, 1):
        closes = r.get("close_positions") or []
        op = r.get("open_position") or {}
        close_names = " + ".join(
            f"{c.get('ticker')} {_fmt_strike(c.get('strike', 0))}P {_fmt_exp(c.get('expiration'))}"
            for c in closes
        )
        open_name = (
            f"{op.get('ticker')} {_fmt_strike(op.get('strike', 0))}P "
            f"{_fmt_exp(op.get('expiration'))}"
        )
        lines.append(f"### {n}. Close {close_names} → Open {open_name}")
        lines.append("")
        for c in closes:
            qty = int(c.get("qty", 1) or 1)
            qty_str = f"{qty}× " if qty > 1 else ""
            lines.append(
                f"**Close:** {qty_str}{c.get('ticker')} {_fmt_strike(c.get('strike', 0))}P "
                f"{_fmt_exp(c.get('expiration'))} — "
                f"+${c.get('banked_profit_dollars', 0):,.0f} banked "
                f"({c.get('capture_pct', 0) * 100:.0f}% capture), "
                f"{c.get('days_remaining', 0)} DTE remaining, "
                f"{c.get('remaining_yield_ann', 0):.0f}% ann yield on remaining theta, "
                f"frees ${c.get('collateral', 0):,.0f}"
            )
        rsi = op.get("rsi")
        rsi_str = f"RSI {rsi:.0f}" if isinstance(rsi, (int, float)) else "RSI n/a — verify"
        lines.append(
            f"**Open:** {open_name} — {op.get('dte', 0)} DTE, "
            f"{op.get('yield_ann', 0):.0f}% ann yield, "
            f"${op.get('premium_dollars', 0):,.0f} premium, "
            f"${op.get('collateral', 0):,.0f} collateral · {rsi_str}"
        )
        net = r.get("net_collateral_delta", 0) or 0
        net_str = (f"−${abs(net):,.0f} ✅ (coverage-improving)" if net > 0
                   else ("$0 (coverage-neutral)" if net == 0
                         else f"+${abs(net):,.0f} ⚠"))
        lines.append(
            f"**Yield delta:** +{r.get('yield_delta_pct', 0):.0f} pts ann · "
            f"**Freed collateral:** ${r.get('freed_collateral', 0):,.0f} · "
            f"**Required:** ${r.get('required_collateral', 0):,.0f} · "
            f"**Net obligation:** {net_str}"
        )
        fb = r.get("freed_bucket_pct", 0) or 0
        if fb > 0:
            lines.append(
                f"**Bucket decompression:** frees {fb:.1f}% NLV from an overweight "
                f"single-expiration bucket (rule #21) on top of the yield upgrade."
            )
        for w in r.get("warnings") or []:
            lines.append(f"- ⚠ {w}")
        lines.append("")

    lines.append(
        "_Each rotation swaps remaining theta on a mostly-banked winner for a "
        "fresh premium clock on the same-or-less collateral — better return on "
        "capital without raising the put obligation the stress test measures. "
        "Closes realize short-term gains; confirm fills leg-by-leg (close first, "
        "then open)._"
    )
    lines.append("")
    if near:
        lines.extend(_render_near_misses(near))
    return lines


def _render_near_misses(near: list[dict]) -> list[str]:
    """Render the '🔍 Near-miss rotations' subsection (task #21).

    Every number comes from NearMissRotation.to_dict() — measured this
    cycle, never fabricated (rule #19). Informational only, never an
    automatic recommendation.
    """
    lines = [
        "### 🔍 Near-miss rotations (blocked by one gate)",
        "",
        "_Rotations that would have been recommended if a single constraint "
        "were relaxed. Shown so you can decide whether to override — not "
        "automatic recommendations._",
        "",
    ]
    for n, r in enumerate(near, 1):
        closes = r.get("close_positions") or []
        op = r.get("open_position") or {}
        close_names = " + ".join(
            f"{c.get('ticker')} {_fmt_strike(c.get('strike', 0))}P"
            for c in closes
        )
        open_name = (
            f"{op.get('ticker')} {_fmt_strike(op.get('strike', 0))}P "
            f"{_fmt_exp(op.get('expiration'))}"
        )
        freed = r.get("freed_collateral", 0) or 0
        req = r.get("required_collateral", 0) or 0
        net = freed - req
        net_str = (f"Obligation would reduce ${net:,.0f}" if net > 0
                   else ("Obligation unchanged" if net == 0
                         else f"Obligation would grow ${-net:,.0f}"))
        label = _BLOCK_LABELS.get(r.get("block_reason") or "",
                                  str(r.get("block_reason") or "gate"))
        lines.append(f"**{n}. Close {close_names} → Open {open_name}**")
        lines.append(
            f"- Yield delta: **+{r.get('yield_delta_pct', 0):.0f} pts ann** · "
            f"Freed ${freed:,.0f} vs required ${req:,.0f} · {net_str}"
        )
        lines.append(f"- 🚫 **Blocked by:** {label} — {r.get('block_detail') or ''}")
        lines.append(f"- 🔓 **To unlock:** {r.get('unblock_path') or ''}")
        warnings = r.get("warnings") or []
        if warnings:
            lines.append(f"- ⚠ Warnings: {'; '.join(warnings)}.")
        lines.append("")
    return lines
