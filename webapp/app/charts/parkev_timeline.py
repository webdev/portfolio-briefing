"""Parkev rating + conviction timeline for one ticker.

Y-axis = rating tier (0..5). Marker color = conviction (orange High,
slate Medium, dark-slate Low). Markers placed on the date the rating
was OBSERVED in the briefing (one row per snapshot date).

This is killer-feature #7.1 — surfaces conviction changes that are
invisible in the single-day briefing chip.
"""

from __future__ import annotations

from typing import Any

from ._layout import (
    ORANGE,
    SLATE_300,
    TEXT_MUTED,
    TEXT_SECONDARY,
    base_layout,
    empty_figure,
)


# Conviction color (mirror ux-design.md §5.2)
_CONV_COLOR = {
    "High": ORANGE,
    "Medium": SLATE_300,
    "Low": TEXT_MUTED,
}

# Tier ladder labels for the y-axis ticks
_TIER_LABEL = {
    0: "SELL (0)",
    1: "HOLD (1)",
    2: "BDL BUY (2)",
    3: "BUY (3)",
    4: "TOP 12/15/25 (4)",
    5: "TOP STOCK (5)",
}


def build_parkev_timeline(history: list[dict[str, Any]], ticker: str) -> dict:
    """Build the Parkev rating timeline from a per-ticker history.

    `history` shape (from ingest.parkev_history_for_ticker):
        [{date, rating_tier, conviction, age_days, recommendation,
          raw_recommendation, ...}, ...]
    """
    if not history:
        return empty_figure(f"No Parkev rating history for {ticker.upper()}.")

    dates = [str(h["date"]) for h in history]
    tiers = [
        int(h["rating_tier"]) if h.get("rating_tier") is not None else None
        for h in history
    ]
    colors = [
        _CONV_COLOR.get(h.get("conviction") or "Medium", SLATE_300)
        for h in history
    ]
    hover_text = []
    for h in history:
        tier = h.get("rating_tier")
        raw = h.get("raw_recommendation") or h.get("recommendation") or "—"
        conv = h.get("conviction") or "—"
        age = h.get("age_days")
        age_str = f"{age}d" if age is not None else "—"
        hover_text.append(
            f"<b>{h['date']}</b><br>"
            f"Rating: {raw} (tier {tier if tier is not None else '—'})<br>"
            f"Conviction: {conv}<br>"
            f"Sheet age: {age_str}"
        )

    layout = base_layout()
    layout["yaxis"]["title"] = {"text": "Rating tier"}
    layout["yaxis"]["tickmode"] = "array"
    layout["yaxis"]["tickvals"] = [0, 1, 2, 3, 4, 5]
    layout["yaxis"]["ticktext"] = [_TIER_LABEL[i] for i in range(6)]
    layout["yaxis"]["range"] = [-0.5, 5.5]
    layout["xaxis"]["title"] = ""
    layout["showlegend"] = False
    layout["hovermode"] = "closest"

    return {
        "data": [{
            "type": "scatter",
            "mode": "lines+markers",
            "x": dates,
            "y": tiers,
            "marker": {
                "size": 11,
                "color": colors,
                "line": {"color": "#0B1220", "width": 1},
            },
            "line": {"color": TEXT_SECONDARY, "width": 1.5, "dash": "dot"},
            "text": hover_text,
            "hovertemplate": "%{text}<extra></extra>",
        }],
        "layout": layout,
    }
