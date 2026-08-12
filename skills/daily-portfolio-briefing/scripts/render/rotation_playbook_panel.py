"""Render the Actionable Rotation Playbook panel (task #22).

Mirrors the manual "Scenario A" structure the user asked to see daily:
Phase 1 close table (winner sweeps) → Phase 2 open table (conviction-ranked
re-deployment) → playbook impact → warnings → order sequence. Every number
comes from RotationPlaybook fields measured this cycle (rule #19 — no
boilerplate), dates render with weekday + year (rule #6).
"""

from __future__ import annotations

from datetime import date as _date


def _fmt_exp(exp_iso: str | None) -> str:
    """Weekday-free compact date with year (table cell), e.g. "Aug 21 '26".
    Raw string when unparseable — never fabricated."""
    if not exp_iso:
        return "?"
    try:
        y, m, d = str(exp_iso)[:10].split("-")
        return _date(int(y), int(m), int(d)).strftime("%b %d '%y")
    except (ValueError, TypeError):
        return str(exp_iso)


def _stars(score: float) -> str:
    if score >= 18:
        return "⭐⭐⭐"
    if score >= 12:
        return "⭐⭐"
    if score >= 6:
        return "⭐"
    return ""


def _conviction_cell(o: dict) -> str:
    """Badge + descriptor from real measured data only.

    Rule #44/#38 (INTC 2026-08-05 — the observed cell "Parkev HOLD Medi ·
    27d · pullback zone" showed NO RSI and a truncated conviction word):
    - EVERY cell renders the RSI token the gate battery actually judged —
      "RSI NN✓" (verified against a live quote) or "RSI NN (snapshot)";
      "RSI n/a" when unmeasured (never fabricated, never absent).
    - Conviction labels render in full (High/Medium/Low) — no fixed-width
      slice ("Medi" was a bug).
    - A non-BUY candidate that qualified via the independent-setup bar
      renders its precise rule-#38 badge instead of the bare rating segment.
    """
    parts = []
    # Setup Grade (George 2026-08-10) — entry-timing letter leads the cell
    # so the WHEN read is scannable next to the WHAT (conviction) read.
    # Absent (flag off / ungraded) → byte-identical legacy cell.
    if o.get("setup_grade"):
        parts.append(f"🏁 {o['setup_grade']}")
    stars = _stars(o.get("conviction_score") or 0)
    if stars:
        parts.append(stars)
    rsi = o.get("rsi")
    if isinstance(rsi, (int, float)):
        parts.append(f"RSI {rsi:.0f}✓" if o.get("rsi_verified")
                     else f"RSI {rsi:.0f} (snapshot)")
    else:
        parts.append("RSI n/a")
    badge = o.get("independent_setup_badge")
    rating = o.get("parkev_rating")
    if badge:
        seg = badge
        age = o.get("parkev_age_days")
        if isinstance(age, (int, float)):
            seg += f" · {int(age)}d"
        parts.append(seg)
    elif rating:
        seg = f"Parkev {rating}"
        conv = o.get("parkev_conviction")
        if conv:
            seg += f" {conv}"
        age = o.get("parkev_age_days")
        if isinstance(age, (int, float)):
            seg += f" · {int(age)}d"
        parts.append(seg)
    for flag in o.get("setup_flags") or []:
        if flag.startswith("RSI "):
            continue                  # already covered by the RSI token
        parts.append(flag)
    return " · ".join(parts)


def render_rotation_playbook(playbook) -> list[str]:
    """Render '## 🎯 Actionable Rotation Playbook'.

    ``playbook``: a RotationPlaybook dataclass or its to_dict() dict.
    Returns [] when playbook is None (below the freed-collateral floor —
    the section is simply absent, mirroring compute_playbook's contract).
    """
    if playbook is None:
        return []
    pb = playbook.to_dict() if hasattr(playbook, "to_dict") else playbook
    if not isinstance(pb, dict):
        return []
    closes = pb.get("closes") or []
    opens = pb.get("opens") or []
    if not closes:
        return []

    lines = ["## 🎯 Actionable Rotation Playbook", ""]
    lines.append(
        f"_Composed rotation that would close {len(closes)} winner CSP(s) to "
        f"fund {len(opens)} new entr{'y' if len(opens) == 1 else 'ies'}, "
        f"ranked by Parkev conviction. Not a \"must execute\" — a \"here's "
        f"the whole trade if you want it.\"_"
    )
    lines.append("")

    # ── Phase 1: closes ──────────────────────────────────────────────────
    total_freed = pb.get("total_freed") or 0
    total_banked = pb.get("total_realized_profit") or 0
    lines.append(
        f"### PHASE 1 — Close {len(closes)} winner(s) "
        f"(bank ${total_banked:,.0f} realized, free ${total_freed:,.0f})"
    )
    lines.append("")
    lines.append("| # | Order | Type | Qty | Limit | Frees | Banks |")
    lines.append("|---|---|---|---|---|---|---|")
    for n, c in enumerate(closes, 1):
        lines.append(
            f"| {n} | **{c.get('ticker')} ${c.get('strike', 0):g}P "
            f"{_fmt_exp(c.get('expiration'))}** | Buy-to-Close | "
            f"{int(c.get('qty', 1) or 1)} | ${c.get('buy_to_close_mid', 0):.2f} GTC | "
            f"${c.get('freed_collateral', 0):,.0f} | "
            f"{c.get('realized_profit', 0):+,.0f} |"
        )
    lines.append(
        f"| | **TOTAL** | | | | **${total_freed:,.0f}** | "
        f"**{total_banked:+,.0f}** |"
    )
    lines.append("")

    # ── Phase 2: opens ───────────────────────────────────────────────────
    deployed = pb.get("total_collateral_deployed") or 0
    cushion = pb.get("cash_cushion_kept") or 0
    total_prem = pb.get("total_premium_collected") or 0
    # Task #28 — coverage-adaptive deployment cap banner. Only rendered
    # when a REDUCED band is active (deploy_cap_next_note present); the
    # healthy top band and the flat legacy path stay banner-free.
    cap_pct = pb.get("deploy_cap_pct")
    cap_label = pb.get("deploy_cap_label") or ""
    cap_cov = pb.get("deploy_cap_coverage")
    cap_next = pb.get("deploy_cap_next_note") or ""
    if opens:
        lines.append(
            f"### PHASE 2 — Open {len(opens)} new CSP(s) ranked by conviction "
            f"(${deployed:,.0f} deployed, ${cushion:,.0f} cushion held)"
        )
        lines.append("")
        if isinstance(cap_pct, (int, float)) and cap_label and cap_next:
            cov_seg = (f"projected post-close coverage {cap_cov:.2f}× "
                       f"({cap_label})"
                       if isinstance(cap_cov, (int, float))
                       else f"({cap_label})")
            lines.append(
                f"⚠️ **Deployment capped at {cap_pct * 100:.0f}% of freed** — "
                f"{cov_seg}; saving ${cushion:,.0f} to actually improve "
                f"stress coverage rather than cycle it into new obligation. "
                f"{cap_next}"
            )
            lines.append("")
        lines.append("| # | Order | Type | Qty | Limit | Premium | Collateral "
                     "| Ann yield | Conviction |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for i, o in enumerate(opens, 1):
            n = len(closes) + i
            lines.append(
                f"| {n} | **{o.get('ticker')} ${o.get('strike', 0):g}P "
                f"{_fmt_exp(o.get('expiration'))}** | Sell-to-Open | 1 | "
                f"${o.get('mid_price', 0):.2f} GTD | "
                f"+${o.get('premium', 0):,.0f} | "
                f"${o.get('collateral_required', 0):,.0f} | "
                f"{o.get('annualized_yield_pct', 0):.0f}% | "
                f"{_conviction_cell(o)} |"
            )
        wy = pb.get("weighted_yield_pct") or 0
        lines.append(
            f"| | **TOTAL** | | | | **+${total_prem:,.0f}** | "
            f"**${deployed:,.0f}** | avg {wy:.0f}% | |"
        )
        lines.append("")
    else:
        lines.append(
            "### PHASE 2 — no qualified re-deployment today"
        )
        lines.append("")
        lines.append(
            "_Close for coverage: every open-side candidate failed a "
            "discipline gate or the conviction floor this cycle. The freed "
            "collateral raises stress coverage until candidates qualify._"
        )
        lines.append("")

    # ── Impact ───────────────────────────────────────────────────────────
    lines.append("**Playbook impact:**")
    cb = pb.get("coverage_before")
    ca = pb.get("coverage_after")
    if isinstance(cb, (int, float)) and isinstance(ca, (int, float)):
        lines.append(
            f"- Coverage: {cb:.2f}× → {ca:.2f}× ({ca - cb:+.2f}×, "
            f"first-order estimate)"
        )
    lines.append(f"- Realized profit today: {total_banked:+,.0f}")
    if isinstance(cap_pct, (int, float)) and cap_label:
        lines.append(
            f"- Deployment cap: {cap_pct * 100:.0f}% of freed ({cap_label}) "
            f"— ${cushion:,.0f} held back"
        )
    if opens:
        max_dte = max(int(o.get("dte") or 0) for o in opens)
        lines.append(
            f"- Ongoing premium: ${total_prem:,.0f} over {max_dte} DTE")
        for exp, coll in sorted((pb.get("bucket_concentrations") or {}).items()):
            lines.append(
                f"- {_fmt_exp(exp)} bucket load: +${coll:,.0f}")
    lines.append("")

    # ── Warnings ─────────────────────────────────────────────────────────
    warnings = pb.get("warnings") or []
    if warnings:
        lines.append("**Warnings:**")
        for w in warnings:
            lines.append(f"- ⚠️ {w}" if not str(w).startswith("⚠") else f"- {w}")
        lines.append("")

    # ── Order sequence ───────────────────────────────────────────────────
    if opens:
        seq = " → ".join(o.get("ticker") or "?" for o in opens)
        lines.append(
            f"**Order sequence:** Fire closes first with GTC limits. Once 2+ "
            f"close, verify broker coverage, then fire STOs in conviction "
            f"order ({seq})."
        )
        lines.append("")
    return lines
