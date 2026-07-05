"""Expiration ladder — bar chart of short-put obligation by expiration date.

For the LATEST snapshot only (the briefing's "what's expiring when?" view).
Color intensity encodes % NLV at each bucket.
Threshold annotations at 20% NLV (warning) and 30% NLV (critical) per
CLAUDE.md hard rule #21.
"""

from __future__ import annotations

from typing import Any

import duckdb

from ._layout import AMBER, RED, base_layout, empty_figure


WARNING_PCT = 0.20   # 20% NLV: WARNING (briefing fires 📊 alert)
CRITICAL_PCT = 0.30  # 30% NLV: CRITICAL (briefing fires ⚠️ alert)


def build_expiration_ladder_figure(
    conn: duckdb.DuckDBPyConnection,
) -> dict:
    """Build the expiration-ladder bar chart from the LATEST snapshot's positions.

    Returns a Plotly figure spec. Empty state if no short puts found.
    """
    latest_row = conn.execute(
        "SELECT date, nlv FROM portfolio_timeseries ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not latest_row:
        return empty_figure("No briefing snapshots loaded yet.")

    latest_date, nlv = latest_row
    if isinstance(latest_date, str):
        from datetime import datetime as _dt
        latest_date = _dt.fromisoformat(latest_date).date()

    rows = conn.execute(
        """
        SELECT expiration,
               SUM(ABS(qty) * strike * 100) AS obligation,
               COUNT(*) AS contracts,
               LIST(underlying) AS tickers
        FROM positions_timeseries
        WHERE date = ?
          AND assetType = 'OPTION'
          AND opt_type = 'PUT'
          AND qty < 0
          AND strike IS NOT NULL
          AND expiration IS NOT NULL
        GROUP BY expiration
        ORDER BY expiration
        """,
        [latest_date.isoformat()],
    ).fetchall()

    if not rows:
        return empty_figure(f"No short puts in latest snapshot ({latest_date.isoformat()}).")

    exps: list[str] = []
    obls: list[float] = []
    pcts: list[float] = []
    counts: list[int] = []
    tickers_per: list[str] = []
    colors: list[str] = []

    nlv_safe = float(nlv) if nlv and nlv > 0 else 0.0

    for exp, obl, n, tk_list in rows:
        if obl is None:
            continue
        pct = (float(obl) / nlv_safe) if nlv_safe else 0.0
        exps.append(str(exp))
        obls.append(float(obl))
        pcts.append(pct * 100)
        counts.append(int(n))
        # Deduplicate tickers per bucket
        unique_tk = sorted({str(t) for t in (tk_list or []) if t})
        tickers_per.append(", ".join(unique_tk))
        # Traffic-light coloring tied to gate thresholds
        if pct >= CRITICAL_PCT:
            colors.append(RED)
        elif pct >= WARNING_PCT:
            colors.append(AMBER)
        else:
            colors.append("#22D3EE")  # cyan

    layout = base_layout()
    layout["yaxis"]["title"] = {"text": "Short put obligation ($)"}
    layout["yaxis"]["tickprefix"] = "$"
    layout["yaxis"]["tickformat"] = ",.0f"
    layout["xaxis"]["title"] = ""
    layout["xaxis"]["type"] = "category"
    layout["bargap"] = 0.25

    # Threshold annotations (relative to NLV)
    if nlv_safe:
        layout["shapes"] = [
            {
                "type": "line",
                "xref": "paper",
                "x0": 0, "x1": 1,
                "y0": nlv_safe * WARNING_PCT, "y1": nlv_safe * WARNING_PCT,
                "line": {"color": AMBER, "width": 1.5, "dash": "dot"},
            },
            {
                "type": "line",
                "xref": "paper",
                "x0": 0, "x1": 1,
                "y0": nlv_safe * CRITICAL_PCT, "y1": nlv_safe * CRITICAL_PCT,
                "line": {"color": RED, "width": 2, "dash": "dash"},
            },
        ]
        layout["annotations"] = [
            {
                "xref": "paper", "yref": "y",
                "x": 0.99, "y": nlv_safe * WARNING_PCT,
                "text": f"20% NLV (${nlv_safe * WARNING_PCT:,.0f}) — warning",
                "xanchor": "right",
                "yshift": -10,
                "showarrow": False,
                "font": {"color": AMBER, "size": 11},
            },
            {
                "xref": "paper", "yref": "y",
                "x": 0.99, "y": nlv_safe * CRITICAL_PCT,
                "text": f"30% NLV (${nlv_safe * CRITICAL_PCT:,.0f}) — critical",
                "xanchor": "right",
                "yshift": -10,
                "showarrow": False,
                "font": {"color": RED, "size": 11},
            },
        ]

    return {
        "data": [{
            "type": "bar",
            "x": exps,
            "y": obls,
            "marker": {"color": colors},
            "customdata": list(zip(pcts, counts, tickers_per)),
            "hovertemplate": (
                "<b>Exp %{x}</b><br>"
                "Obligation $%{y:,.0f}<br>"
                "%{customdata[0]:.1f}% NLV · %{customdata[1]} contracts<br>"
                "<span style='font-size:11px'>%{customdata[2]}</span>"
                "<extra></extra>"
            ),
        }],
        "layout": layout,
    }
