"""Sector Exposure (look-through) panel — 2026-08-07.

George: "It seems like I'm pretty heavily invested in tech. Is it the right
thing?" — renders the three views computed by ``analysis.sector_exposure``:
equity MV by sector (ETFs decomposed), the assignment-adjusted book ("if all
puts assign"), and the net tech-correlated share of NLV.

The bold ``**🧭 …**`` summary line is the digest's one-line Health-zone
pull (render/digest.py keeps it verbatim — pure subset). Every number is
measured this cycle; ETF look-throughs render with '~' and are labelled
estimates (rule #19). Unclassified tickers are surfaced, never guessed.
"""

from __future__ import annotations


def _fmt_usd(v: float) -> str:
    return f"${v:,.0f}"


def summary_line(exposure: dict) -> str | None:
    """The bold one-liner (also reused by the digest). None when the
    tech-correlated % is unmeasurable (no NLV) — never a fabricated read."""
    tech = exposure.get("tech_pct_nlv")
    tech_a = exposure.get("tech_assignment_pct_nlv")
    if tech is None or tech_a is None:
        return None
    cap = exposure.get("cap_pct")
    over = exposure.get("over_cap") or []
    if over:
        worst = max(over, key=lambda o: o.get("pct_nlv") or 0)
        cap_txt = (f" — {worst['sector']} at {worst['pct_nlv']:.0f}% NLV "
                   f"is over the {cap:.0f}% sector cap")
    else:
        cap_txt = (f" — all sectors within the {cap:.0f}% cap"
                   if cap else "")
    return (f"**🧭 Net tech-correlated: ~{tech:.0f}% of NLV "
            f"(assignment-adjusted ~{tech_a:.0f}%){cap_txt}**")


def render_sector_panel(exposure: dict | None) -> list[str]:
    """'## 🧭 Sector Exposure (look-through)' section. Empty list when there
    is nothing measured (fail-open — the briefing simply omits the panel)."""
    if not isinstance(exposure, dict) or not exposure.get("equity_by_sector"):
        return []

    nlv = exposure.get("nlv") or 0.0
    eq = exposure.get("equity_by_sector") or {}
    asg = exposure.get("assignment_by_sector") or {}
    cap = exposure.get("cap_pct")
    over_sectors = {o["sector"] for o in (exposure.get("over_cap") or [])}

    lines = ["## 🧭 Sector Exposure (look-through)", ""]
    s = summary_line(exposure)
    if s:
        lines.append(s)
        lines.append("")

    lines.append("| Sector | Equity MV | % NLV | If-all-puts-assign | % NLV |")
    lines.append("|--------|-----------|-------|--------------------|-------|")
    all_sectors = sorted(set(eq) | set(asg), key=lambda x: -asg.get(x, 0.0))
    for sec in all_sectors:
        mv = eq.get(sec, 0.0)
        ax = asg.get(sec, 0.0)
        if nlv > 0:
            mv_pct = f"{mv / nlv * 100:.1f}%"
            ax_pct = f"{ax / nlv * 100:.1f}%"
        else:
            mv_pct = ax_pct = "n/a"
        flag = " ⚠" if sec in over_sectors else ""
        lines.append(f"| {sec}{flag} | {_fmt_usd(mv)} | {mv_pct} "
                     f"| {_fmt_usd(ax)} | {ax_pct} |")
    total_eq = exposure.get("equity_mv_total") or 0.0
    total_ax = total_eq + (exposure.get("put_obligation_total") or 0.0)
    if nlv > 0:
        lines.append(f"| **Total** | **{_fmt_usd(total_eq)}** "
                     f"| **{total_eq / nlv * 100:.1f}%** "
                     f"| **{_fmt_usd(total_ax)}** "
                     f"| **{total_ax / nlv * 100:.1f}%** |")
    lines.append("")
    if cap:
        lines.append(f"_⚠ = over the {cap:.0f}% per-sector cap "
                     f"(wheelhouz sector rule). 'If-all-puts-assign' adds each "
                     f"short put's strike×100 obligation to its sector._")
    if exposure.get("approx_lookthrough"):
        lines.append("_ETF look-through uses approximate index sector weights "
                     "('~') — estimates, not exact holdings._")
    uncl = exposure.get("unclassified_tickers") or []
    if uncl:
        lines.append(f"_Unclassified: {', '.join(uncl)} — no curated mapping "
                     f"and no FMP profile this cycle (never guessed)._")
    lines.append("")
    return lines
