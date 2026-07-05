"""Stress coverage timeline.

Coverage ratio = cash / short_put_obligation, where short_put_obligation
is sum(|qty| * strike * 100) across all short PUT positions for the day.

The 0.50× red floor and 0.70× yellow line are drawn as horizontal
threshold bands so the user can see when historical coverage crossed
each gate. Hard rule (briefing-side): new CSP entries blocked when
coverage < 0.50×.

Per CLAUDE.md fail-closed rule (#19): if positions.json is missing
for a date, that date's coverage is null and silently skipped.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import duckdb

from ._layout import CYAN, RED, YELLOW, base_layout, empty_figure


# Floor: below this, new short-put entries are blocked.
COVERAGE_FLOOR = 0.50
# Yellow band: rebuilding target.
COVERAGE_TARGET = 0.70


def _coverage_for_date(
    conn: duckdb.DuckDBPyConnection, snapshot_date: date | str, cash: float | None
) -> float | None:
    """Compute coverage = cash / short_put_obligation for a single date.

    Returns None when no short puts exist (undefined) or when cash
    is missing.
    """
    if cash is None or cash <= 0:
        return None
    d = snapshot_date if isinstance(snapshot_date, str) else snapshot_date.isoformat()
    row = conn.execute(
        """
        SELECT SUM(ABS(qty) * strike * 100) AS obl
        FROM positions_timeseries
        WHERE date = ?
          AND assetType = 'OPTION'
          AND opt_type = 'PUT'
          AND qty < 0
          AND strike IS NOT NULL
        """,
        [d],
    ).fetchone()
    obl = row[0] if row else None
    if obl is None or obl <= 0:
        return None
    return float(cash) / float(obl)


def build_coverage_figure(
    conn: duckdb.DuckDBPyConnection, days: int = 90
) -> dict:
    """Build the stress coverage chart from portfolio_timeseries + positions_timeseries."""

    rows = conn.execute(
        "SELECT date, nlv, cash FROM portfolio_timeseries ORDER BY date"
    ).fetchall()
    if not rows:
        return empty_figure("No briefing snapshots loaded yet.")

    # Trim by `days` window
    cutoff: date | None = None
    if days and days > 0:
        latest = rows[-1][0]
        if isinstance(latest, str):
            latest = datetime.fromisoformat(latest).date()
        cutoff = latest - timedelta(days=days)

    dates: list[str] = []
    coverages: list[float | None] = []
    for d, _nlv, cash in rows:
        if isinstance(d, str):
            d = datetime.fromisoformat(d).date()
        if cutoff is not None and d < cutoff:
            continue
        cov = _coverage_for_date(conn, d, cash)
        if cov is None:
            continue
        dates.append(d.isoformat())
        coverages.append(cov)

    if not dates:
        return empty_figure("No coverage data in the selected window.")

    layout = base_layout()
    layout["yaxis"]["title"] = {"text": "Coverage ratio (cash ÷ short-put obligation)",
                                 "font": {"color": CYAN}}
    layout["yaxis"]["tickformat"] = ".2f"
    layout["yaxis"]["range"] = [0, max(1.0, max(coverages) * 1.1)]
    layout["xaxis"]["title"] = ""

    # Threshold shapes (ghosted horizontal bands)
    layout["shapes"] = [
        # Red floor at 0.50x
        {
            "type": "line",
            "xref": "paper",
            "x0": 0, "x1": 1,
            "y0": COVERAGE_FLOOR, "y1": COVERAGE_FLOOR,
            "line": {"color": RED, "width": 2, "dash": "dash"},
        },
        # Yellow target at 0.70x
        {
            "type": "line",
            "xref": "paper",
            "x0": 0, "x1": 1,
            "y0": COVERAGE_TARGET, "y1": COVERAGE_TARGET,
            "line": {"color": YELLOW, "width": 1.5, "dash": "dot"},
        },
    ]
    layout["annotations"] = [
        {
            "xref": "paper", "yref": "y",
            "x": 0.99, "y": COVERAGE_FLOOR,
            "text": "0.50× floor (new CSPs blocked below)",
            "xanchor": "right",
            "showarrow": False,
            "font": {"color": RED, "size": 11},
            "yshift": -12,
        },
        {
            "xref": "paper", "yref": "y",
            "x": 0.99, "y": COVERAGE_TARGET,
            "text": "0.70× rebuild target",
            "xanchor": "right",
            "showarrow": False,
            "font": {"color": YELLOW, "size": 11},
            "yshift": -12,
        },
    ]

    return {
        "data": [{
            "type": "scatter",
            "mode": "lines+markers",
            "x": dates,
            "y": coverages,
            "name": "Coverage",
            "line": {"color": CYAN, "width": 2},
            "marker": {"size": 5, "color": CYAN},
            "hovertemplate": "<b>%{x}</b><br>Coverage %{y:.2f}×<extra></extra>",
        }],
        "layout": layout,
    }
