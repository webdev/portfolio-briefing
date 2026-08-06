"""Program-edge gap sparkline — cumulative $ gap (real NAV − ghost NAV)
over time, for the Home page's 💪 Program Edge card.

Takes the normalized point list from ``app.program_edge.series_points``.
Short/missing series → empty_figure placeholder (no 500s, no fabricated
data — house rules #10/#19).
"""

from __future__ import annotations

from typing import Any

from ._layout import EMERALD, base_layout, empty_figure


def build_program_edge_figure(points: list[dict[str, Any]]) -> dict:
    """Compact filled line of the cumulative gap series."""
    if not points or len(points) < 2:
        return empty_figure("Program-edge history still building.")

    xs = [p["date"].isoformat() if hasattr(p["date"], "isoformat") else str(p["date"])
          for p in points]
    ys = [p["gap"] for p in points]

    layout = base_layout()
    layout["margin"] = {"l": 56, "r": 8, "t": 8, "b": 24}
    layout["showlegend"] = False
    layout["yaxis"]["tickprefix"] = "$"
    layout["yaxis"]["tickformat"] = ",.0f"
    layout["xaxis"]["tickfont"] = {"size": 10}
    layout["yaxis"]["tickfont"] = {"size": 10}
    layout["hovermode"] = "x unified"

    return {
        "data": [{
            "type": "scatter",
            "mode": "lines",
            "name": "Program edge",
            "x": xs,
            "y": ys,
            "line": {"color": EMERALD, "width": 2},
            "fill": "tozeroy",
            "fillcolor": "rgba(52, 211, 153, 0.10)",
            "hovertemplate": "%{x}<br>Edge: $%{y:,.0f}<extra></extra>",
        }],
        "layout": layout,
    }
