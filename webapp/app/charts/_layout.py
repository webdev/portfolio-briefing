"""Shared Plotly layout helpers — dark theme, slate background, tabular numerics.

Matches the design language in docs/webapp-ux-design.md §5.2 (Bloomberg-inspired
dark slate, traffic-light discipline colors, mono numerics).
"""

from __future__ import annotations

# Color tokens — mirror webapp/static/app.css design tokens.
# Charts share the slate palette so they sit cleanly on .chart-card surfaces.
BG_PRIMARY = "#0F172A"     # Slate 900 — matches --color-bg
SURFACE = "#1E293B"        # Slate 800 — matches --color-surface
BORDER = "#334155"         # Slate 700 — matches --color-border
GRID_SUBTLE = "#1E293B"    # interior grid lines (less noise than full border)
TEXT_PRIMARY = "#F1F5F9"   # Slate 100
TEXT_SECONDARY = "#94A3B8" # Slate 400
TEXT_MUTED = "#64748B"     # Slate 500

# Data series colorway (max ~8 distinct)
CYAN = "#22D3EE"
AMBER = "#FBBF24"
EMERALD = "#34D399"
VIOLET = "#A78BFA"
PINK = "#F472B6"
BLUE = "#60A5FA"
ORANGE = "#FB923C"
SLATE_300 = "#CBD5E1"

# Discipline traffic-light (gate thresholds only — never on data series)
RED = "#EF4444"
YELLOW = "#FBBF24"
GREEN = "#34D399"
THRESHOLD = "#475569"  # generic dashed-line threshold (non-traffic-light)


def base_layout(title: str | None = None) -> dict:
    """Return a layout dict suitable for any chart in this app."""
    layout = {
        "paper_bgcolor": BG_PRIMARY,
        "plot_bgcolor": BG_PRIMARY,  # match container — gridlines provide the structure
        "font": {
            "family": "Inter, -apple-system, BlinkMacSystemFont, sans-serif",
            "color": TEXT_PRIMARY,
            "size": 12,
        },
        "margin": {"l": 56, "r": 24, "t": 40 if title else 12, "b": 40},
        "hovermode": "x unified",
        "hoverlabel": {
            "bgcolor": SURFACE,
            "bordercolor": BORDER,
            "font": {"family": "JetBrains Mono, SF Mono, Menlo, monospace",
                     "color": TEXT_PRIMARY},
        },
        "colorway": [CYAN, PINK, AMBER, VIOLET, EMERALD, BLUE, ORANGE, SLATE_300],
        "xaxis": {
            "gridcolor": GRID_SUBTLE,
            "zerolinecolor": BORDER,
            "linecolor": BORDER,
            "tickfont": {"color": TEXT_SECONDARY, "size": 11},
        },
        "yaxis": {
            "gridcolor": GRID_SUBTLE,
            "zerolinecolor": BORDER,
            "linecolor": BORDER,
            "tickfont": {"color": TEXT_SECONDARY, "size": 11},
            "tickformat": ",",
        },
        "legend": {
            "bgcolor": "rgba(0,0,0,0)",
            "bordercolor": BORDER,
            "borderwidth": 0,
            "font": {"color": TEXT_SECONDARY, "size": 12},
        },
    }
    if title:
        layout["title"] = {
            "text": title,
            "font": {"color": TEXT_PRIMARY, "size": 14, "weight": 600},
            "x": 0.02,
            "xanchor": "left",
        }
    return layout


def empty_figure(message: str = "No data yet.") -> dict:
    """Plotly spec for an empty-state placeholder."""
    return {
        "data": [],
        "layout": {
            **base_layout(),
            "annotations": [{
                "text": message,
                "showarrow": False,
                "xref": "paper",
                "yref": "paper",
                "x": 0.5,
                "y": 0.5,
                "font": {"color": TEXT_MUTED, "size": 14},
            }],
            "xaxis": {"visible": False},
            "yaxis": {"visible": False},
        },
    }
