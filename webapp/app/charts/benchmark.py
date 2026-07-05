"""Portfolio-vs-SPY cumulative return chart (task #16).

Reads the normalized series the pipeline embeds in the briefing JSON at
``benchmark_report.benchmark.series`` — {dates, portfolio_pct, spy_pct},
both rebased to 0% at the window start — so the webapp never refetches
market data. Empty/missing series → empty_figure placeholder (no 500s).
"""

from __future__ import annotations

from typing import Any

from ._layout import AMBER, CYAN, base_layout, empty_figure


def build_benchmark_figure(series: dict[str, Any] | None,
                           benchmark_ticker: str = "SPY") -> dict:
    """Two-line cumulative-% chart: portfolio (cyan) vs benchmark (amber)."""
    if not series or not series.get("dates"):
        return empty_figure("No benchmark series for this briefing yet.")

    dates = series.get("dates") or []
    port = series.get("portfolio_pct") or []
    spy = series.get("spy_pct") or []
    if len(dates) < 2 or len(port) != len(dates):
        return empty_figure("Not enough benchmark history to chart.")

    layout = base_layout()
    layout["yaxis"]["title"] = {"text": "Cumulative return (%)"}
    layout["yaxis"]["ticksuffix"] = "%"
    layout["xaxis"]["title"] = ""

    traces = [
        {
            "type": "scatter",
            "mode": "lines",
            "name": "Portfolio",
            "x": dates,
            "y": port,
            "line": {"color": CYAN, "width": 2},
        }
    ]
    if len(spy) == len(dates) and any(v is not None for v in spy):
        traces.append({
            "type": "scatter",
            "mode": "lines",
            "name": benchmark_ticker,
            "x": dates,
            "y": spy,
            "line": {"color": AMBER, "width": 2, "dash": "dot"},
        })
    return {"data": traces, "layout": layout}
