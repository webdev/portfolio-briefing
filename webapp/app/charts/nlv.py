"""NLV + cash over time. Two-line chart with cyan NLV (primary y-axis,
left) and amber cash (secondary y-axis, right)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ._layout import AMBER, CYAN, base_layout, empty_figure


def build_nlv_figure(rows: list[dict[str, Any]], days: int = 90) -> dict:
    """Build the NLV chart from portfolio_timeseries rows.

    `rows` shape: [{date, nlv, cash, cash_pct, regime}, ...] ascending.
    """
    if not rows:
        return empty_figure("No briefing snapshots loaded yet.")

    # Trim by `days` window relative to most recent date
    rows_sorted = sorted(rows, key=lambda r: r["date"])
    cutoff: date | None = None
    if days and days > 0:
        latest = rows_sorted[-1]["date"]
        if isinstance(latest, str):
            from datetime import datetime as _dt
            latest = _dt.fromisoformat(latest).date()
        cutoff = latest - timedelta(days=days)

    filtered = []
    for r in rows_sorted:
        d = r["date"]
        if isinstance(d, str):
            from datetime import datetime as _dt
            d = _dt.fromisoformat(d).date()
        if cutoff is None or d >= cutoff:
            filtered.append({**r, "date": d.isoformat()})

    if not filtered:
        return empty_figure("No data in the selected window.")

    dates = [r["date"] for r in filtered]
    nlvs = [r["nlv"] for r in filtered]
    cashes = [r["cash"] for r in filtered]

    layout = base_layout()
    layout["yaxis"]["title"] = {"text": "NLV ($)", "font": {"color": CYAN}}
    layout["yaxis"]["tickprefix"] = "$"
    layout["yaxis"]["tickformat"] = ",.0f"
    layout["yaxis2"] = {
        "title": {"text": "Cash ($)", "font": {"color": AMBER}},
        "overlaying": "y",
        "side": "right",
        "tickprefix": "$",
        "tickformat": ",.0f",
        "showgrid": False,
        "tickfont": {"color": AMBER},
    }
    layout["xaxis"]["title"] = ""

    return {
        "data": [
            {
                "type": "scatter",
                "mode": "lines+markers",
                "x": dates,
                "y": nlvs,
                "name": "NLV",
                "line": {"color": CYAN, "width": 2},
                "marker": {"size": 5, "color": CYAN},
                "yaxis": "y",
                "hovertemplate": "<b>%{x}</b><br>NLV $%{y:,.0f}<extra></extra>",
            },
            {
                "type": "scatter",
                "mode": "lines",
                "x": dates,
                "y": cashes,
                "name": "Cash",
                "line": {"color": AMBER, "width": 1.5, "dash": "dot"},
                "yaxis": "y2",
                "hovertemplate": "<b>%{x}</b><br>Cash $%{y:,.0f}<extra></extra>",
            },
        ],
        "layout": layout,
    }
