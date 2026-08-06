"""Program Edge — real portfolio vs no-options ghost, for the Home page.

Reads the pipeline's ghost-portfolio state file
(``skills/daily-portfolio-briefing/state/ghost_portfolio.json``) and derives
the "💪 Program Edge" card data: cumulative gap since inception, 1-day delta,
month-to-date, and 7d/30d/inception real-vs-ghost window rows.

Fail-open by design (house rule: state corruption recovers, never 500):
  - missing file / corrupt JSON / short series → status "building" with the
    day count, so the card renders "building history (N days)" instead of
    numbers it doesn't have.

All computation is pure; the only I/O is ``load_state()``.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import REPO_ROOT

# Fewer points than this → the card shows "building history (N days)".
MIN_DAYS = 5

DEFAULT_GHOST_FILE = (
    REPO_ROOT / "skills" / "daily-portfolio-briefing" / "state" / "ghost_portfolio.json"
)


def ghost_portfolio_path() -> Path:
    """State-file location. Env override: PORTFOLIO_BRIEFING_GHOST_FILE
    (resolved per call so tests can repoint it without re-importing)."""
    return Path(os.environ.get(
        "PORTFOLIO_BRIEFING_GHOST_FILE",
        str(DEFAULT_GHOST_FILE),
    )).expanduser()


def load_state() -> dict[str, Any] | None:
    """Load the ghost-portfolio JSON. Any failure → None (fail-open)."""
    path = ghost_portfolio_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def series_points(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalize the per-day series into a date-sorted list of points:
    [{date, ghost_nav, real_nav, gap, gap_delta_1d}, ...]. Rows missing a
    numeric gap/real/ghost are dropped."""
    if not state or not isinstance(state.get("series"), dict):
        return []
    points: list[dict[str, Any]] = []
    for day, row in sorted(state["series"].items()):
        if not isinstance(row, dict):
            continue
        try:
            d = datetime.fromisoformat(str(day)).date()
            gap = float(row["gap"])
            real = float(row["real_nav"])
            ghost = float(row["ghost_nav"])
        except (KeyError, TypeError, ValueError):
            continue
        delta = row.get("gap_delta_1d")
        points.append({
            "date": d,
            "gap": gap,
            "real_nav": real,
            "ghost_nav": ghost,
            "gap_delta_1d": float(delta) if isinstance(delta, (int, float)) else None,
        })
    return points


def _fmt_usd(v: float | None) -> str:
    """Signed whole-dollar string: +$182,878 / -$1,204. None → n/a
    (never fabricate a number — hard rule #19)."""
    if v is None:
        return "n/a"
    sign = "+" if v >= 0 else "-"
    return f"{sign}${abs(v):,.0f}"


def _baseline(points: list[dict], days_back: int) -> dict:
    """Last point dated at-or-before (latest - days_back); falls back to
    the first point when the series is shorter than the window."""
    cutoff = points[-1]["date"] - timedelta(days=days_back)
    base = points[0]
    for p in points:
        if p["date"] <= cutoff:
            base = p
        else:
            break
    return base


def _window_row(points: list[dict], label: str, base: dict) -> dict:
    last = points[-1]
    real = last["real_nav"] - base["real_nav"]
    ghost = last["ghost_nav"] - base["ghost_nav"]
    edge = last["gap"] - base["gap"]
    return {
        "label": label,
        "real": _fmt_usd(real),
        "ghost": _fmt_usd(ghost),
        "edge": _fmt_usd(edge),
        "real_raw": real,
        "ghost_raw": ghost,
        "edge_raw": edge,
    }


def build_card(state: dict[str, Any] | None) -> dict[str, Any]:
    """Derive everything the Program Edge card renders. Always returns a
    dict — status "ok" with numbers, or "building" with a day count."""
    points = series_points(state)
    if len(points) < MIN_DAYS:
        return {"status": "building", "days": len(points)}

    last = points[-1]
    summary = (state or {}).get("summary") or {}

    total_gap = last["gap"]

    # 1-day delta: prefer the pipeline's own number; else derive.
    delta_1d = last.get("gap_delta_1d")
    if delta_1d is None and len(points) >= 2:
        delta_1d = last["gap"] - points[-2]["gap"]

    # Month-to-date: prefer summary.month_gap; else derive from the last
    # point dated before the 1st of the current month.
    month_gap = summary.get("month_gap")
    if not isinstance(month_gap, (int, float)):
        month_start = last["date"].replace(day=1)
        prior = [p for p in points if p["date"] < month_start]
        base = prior[-1] if prior else points[0]
        month_gap = last["gap"] - base["gap"]

    inception_dt = points[0]["date"]
    inception_label = f"{inception_dt.strftime('%b')} {inception_dt.day}"

    windows = [
        _window_row(points, "7 days", _baseline(points, 7)),
        _window_row(points, "30 days", _baseline(points, 30)),
        _window_row(points, f"Inception ({inception_label})", points[0]),
    ]

    return {
        "status": "ok",
        "days": len(points),
        "as_of": last["date"].isoformat(),
        "inception": inception_dt.isoformat(),
        "inception_label": inception_label,
        "total_gap": total_gap,
        "headline": _fmt_usd(total_gap),
        "delta_1d": _fmt_usd(delta_1d) if delta_1d is not None else "n/a",
        "mtd": _fmt_usd(float(month_gap)),
        "windows": windows,
        "footnote": (state or {}).get("footnote") or "",
    }
