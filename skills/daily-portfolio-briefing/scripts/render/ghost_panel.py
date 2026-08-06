"""Render the 👻 Ghost Portfolio panel + Money Plan line (task #41).

The ghost is the options-stripped counterfactual NAV (see
analysis/ghost_portfolio.py — the module docstring is the assumptions
contract). This renderer surfaces:

  - a Benchmark-section block: real vs ghost at inception / 30d / 7d /
    today, with the honest-assumptions footnote;
  - a single compact 💰 Money Plan bullet ("Wheel vs Ghost").

Every figure is measured from the persisted series — a missing window
renders "n/a", never an estimate (hard rule #19). Fail-open: any exception
returns [] / None and the briefing ships without the surface.
"""

from __future__ import annotations

from datetime import date, timedelta


def _usd(v: float | None) -> str:
    if v is None:
        return "n/a"
    if abs(v) < 0.5:                     # float dust → clean $0, never "-$0"
        return "$0"
    return f"-${abs(v):,.0f}" if v < 0 else f"+${v:,.0f}"


def _usd_plain(v: float | None) -> str:
    return "n/a" if v is None else f"${v:,.0f}"


def _day_at_or_before(days: list, target: date):
    hit = None
    for d in days:
        if d.date <= target:
            hit = d
    return hit


def _day_at_or_after(days: list, target: date):
    for d in days:
        if d.date >= target:
            return d
    return None


def render_ghost_panel(report) -> list[str]:
    """The Benchmark-section block. [] when the report isn't usable."""
    try:
        if report is None or report.status != "ok" or not report.days:
            return []
        latest = report.days[-1]
        lines = [
            "### 👻 Ghost Portfolio (no-options counterfactual)",
            "",
            (f"Is it the market or the moves? Ghost NAV = the same equities "
             f"+ mirrored trades/flows with EVERY option stripped since "
             f"{report.inception.isoformat()}. Real − Ghost = the options "
             f"program's cumulative net contribution."),
            "",
            "| As of | Real NAV | Ghost NAV | Options net (gap) |",
            "|---|---|---|---|",
        ]
        first = report.days[0]
        rows = [(f"Inception ({first.date.isoformat()})", first)]
        for label, back in (("~30d ago", 30), ("~7d ago", 7)):
            d = _day_at_or_before(report.days, latest.date - timedelta(days=back))
            if d is not None and d.date not in (first.date, latest.date):
                rows.append((f"{label} ({d.date.isoformat()})", d))
        rows.append((f"Today ({latest.date.isoformat()})", latest))
        seen: set = set()
        for label, d in rows:
            if d.date in seen:
                continue
            seen.add(d.date)
            gap = (f"**{_usd(d.gap)}**" if d.date == latest.date
                   else _usd(d.gap))
            lines.append(f"| {label} | {_usd_plain(d.real_nav)} | "
                         f"{_usd_plain(d.ghost_nav)} | {gap} |")
        lines.append("")

        try:
            from analysis.ghost_portfolio import summary_figures
        except ImportError:
            from ghost_portfolio import summary_figures  # type: ignore
        figs = summary_figures(report)
        bits = [f"**Options program net since {figs['inception']}: "
                f"{_usd(figs['total_gap'])}**"]
        if figs["month_gap"] is not None:
            bits.append(f"this month {_usd(figs['month_gap'])}")
        if figs["week_gap"] is not None:
            bits.append(f"past 7d {_usd(figs['week_gap'])}")
        lines.append("- " + " · ".join(bits))
        lines.append(f"- _Assumptions: {report.footnote}_")
        for note in report.notes:
            lines.append(f"- _{note}_")
        lines.append("")
        return lines
    except Exception:  # noqa: BLE001 — fail-open
        return []


def ghost_money_plan_line(report) -> str | None:
    """The compact 💰 Money Plan bullet. None when unmeasurable."""
    try:
        if report is None or report.status != "ok" or not report.days:
            return None
        from analysis.ghost_portfolio import summary_figures
        figs = summary_figures(report)
        if figs["total_gap"] is None:
            return None
        bits = [f"{_usd(figs['total_gap'])} since {figs['inception']} "
                f"(options program net)"]
        if figs["month_gap"] is not None:
            bits.append(f"{_usd(figs['month_gap'])} this month")
        if figs["week_gap"] is not None:
            bits.append(f"{_usd(figs['week_gap'])} past 7d")
        return "- **Wheel vs Ghost:** " + " · ".join(bits)
    except Exception:  # noqa: BLE001
        return None
