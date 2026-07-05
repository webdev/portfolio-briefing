"""Mini 30-day price sparkline for the ticker hover card.

Pulls the per-day equity rows from ``positions_timeseries`` and draws
a compact line+marker chart. Returns ``empty_figure()`` when no equity
rows exist (option-only tickers or never-held names).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import duckdb

from ._layout import CYAN, base_layout, empty_figure


def build_ticker_sparkline(
    conn: duckdb.DuckDBPyConnection, ticker: str, days: int = 30
) -> dict[str, Any]:
    """Build a tiny price sparkline for one ticker over the last `days`."""
    if not ticker:
        return empty_figure("Specify a ticker.")
    # Pull the equity rows only — option strikes pollute the y-axis.
    rows = conn.execute(
        """
        SELECT date, price
        FROM positions_timeseries
        WHERE upper(symbol) = upper(?)
          AND assetType != 'OPTION'
          AND price IS NOT NULL
        ORDER BY date
        """,
        [ticker],
    ).fetchall()

    if not rows:
        return empty_figure(f"No equity history for {ticker}.")

    cutoff: date | None = None
    if days and days > 0:
        latest = rows[-1][0]
        if isinstance(latest, str):
            latest = datetime.fromisoformat(latest).date()
        cutoff = latest - timedelta(days=days)

    xs: list[str] = []
    ys: list[float] = []
    for d, p in rows:
        if isinstance(d, str):
            d = datetime.fromisoformat(d).date()
        if cutoff and d < cutoff:
            continue
        if p is None:
            continue
        xs.append(d.isoformat())
        ys.append(float(p))

    if not xs:
        return empty_figure(f"No {ticker} data in last {days} days.")

    layout = base_layout()
    # Compact: tiny margins, no legend, light gridlines.
    layout["margin"] = {"l": 40, "r": 8, "t": 8, "b": 24}
    layout["yaxis"]["tickformat"] = ".2f"
    layout["xaxis"]["showticklabels"] = True
    layout["xaxis"]["tickfont"] = {"size": 10}
    layout["yaxis"]["tickfont"] = {"size": 10}
    layout["showlegend"] = False
    layout["hovermode"] = "x unified"

    return {
        "data": [{
            "type": "scatter",
            "mode": "lines+markers",
            "x": xs,
            "y": ys,
            "line": {"color": CYAN, "width": 1.5},
            "marker": {"size": 3, "color": CYAN},
            "name": ticker,
            "hovertemplate": "<b>%{x}</b> · $%{y:.2f}<extra></extra>",
        }],
        "layout": layout,
    }
