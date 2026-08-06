"""Recently-opened churn guard (task #40 fix 2, 2026-07-30).

Observed: VRT_PUT_280_20270115 was filled at ~2:23 PM and the 2:54 PM run
recommended re-rolling it for -$2,740; QCOM_PUT_185_20261218 opened the same
morning and got a same-day re-roll recommendation. Rolling a contract the
user JUST opened is churn — the position hasn't had a single session of
theta, and the "roll" would realize the entry spread twice.

Rule: no roll recommendation on a contract opened within
``roll.min_position_age_days`` (default 5 trading days) UNLESS a
loss-stop / crash guardrail fires — safety always wins (callers exempt
GUARDRAIL_LOSS_STOP / GUARDRAIL_CRASH_STOP before consulting this guard).

Age detection: the broker payload carries no open date, so age comes from
prior snapshots' position lists (``state/briefing_snapshots/<date>/
positions.json``) — a contract absent yesterday = opened today. Fail-open
everywhere: no prior snapshots → no measurable age → the guard never fires
(CLAUDE.md #10: never block on missing data, and never fabricate an age).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

DEFAULT_MIN_AGE_DAYS = 5           # trading days
_DEFAULT_LOOKBACK_DAYS = 15        # calendar days of snapshot history to scan

# Guardrail cells that exempt a position from the churn guard — safety wins.
SAFETY_CELLS = ("GUARDRAIL_LOSS_STOP", "GUARDRAIL_CRASH_STOP")


def min_age_days(config: dict | None) -> int:
    """``roll.min_position_age_days`` from briefing.yaml (default 5)."""
    try:
        return int(((config or {}).get("roll") or {})
                   .get("min_position_age_days", DEFAULT_MIN_AGE_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_MIN_AGE_DAYS


def trading_days_between(d0: date, d1: date) -> int:
    """Weekday count in (d0, d1] — a position first seen today ages 0."""
    if d1 <= d0:
        return 0
    n, d = 0, d0
    while d < d1:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def build_position_ages(
    positions: list | None,
    snapshot_root: Path | str,
    today_iso: str | None,
    max_lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
) -> dict:
    """Map option contract symbol → age in TRADING days since first seen.

    Scans prior ``positions.json`` snapshots newest-first. A contract absent
    from a prior day's list was opened after that day; its age is the
    trading-day distance from the most recent snapshot that DOES contain it
    (or 0 when no prior snapshot contains it — opened today). A contract
    present in every available snapshot gets the age to the oldest scanned
    snapshot — a lower bound that is sufficient once ≥ the guard threshold.

    Returns {} when today's date is unparseable or no prior snapshots exist
    (fail-open: unmeasurable age never fires the guard).
    """
    try:
        today = date.fromisoformat(str(today_iso)[:10])
    except (ValueError, TypeError):
        return {}
    root = Path(snapshot_root)
    history: list[tuple[date, set]] = []  # newest first
    for offset in range(1, max_lookback_days + 1):
        d = today - timedelta(days=offset)
        p = root / d.isoformat() / "positions.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text())
            syms = {
                str(x.get("symbol"))
                for x in data
                if (x.get("assetType") or "").upper() == "OPTION"
                and x.get("symbol")
            }
        except Exception:
            continue
        history.append((d, syms))
    if not history:
        return {}

    ages: dict = {}
    for pos in positions or []:
        if (pos.get("assetType") or "").upper() != "OPTION":
            continue
        sym = pos.get("symbol")
        if not sym:
            continue
        first_seen = today
        for d, syms in history:  # newest → oldest
            if sym in syms:
                first_seen = d
            else:
                break  # absent on this prior day → opened after it
        ages[str(sym)] = trading_days_between(first_seen, today)
    return ages


def churn_note(age_days: int) -> str:
    """The Watch-panel demotion note for a churn-guarded roll."""
    return (
        f"opened {int(age_days)} day(s) ago — letting the new position "
        f"settle (churn guard); guardrails still monitor it."
    )


def check_churn_guard(
    contract: str,
    matrix_cell_id: str | None,
    position_ages: dict | None,
    config: dict | None,
) -> str | None:
    """Return the demotion note when the churn guard blocks a roll on this
    contract, else None.

    Fires only when the age is MEASURED (present in position_ages) and below
    ``roll.min_position_age_days``. Loss-stop / crash guardrails exempt —
    safety always wins.
    """
    try:
        cell = (matrix_cell_id or "").upper()
        if any(s in cell for s in SAFETY_CELLS):
            return None
        age = (position_ages or {}).get(contract)
        if age is None:
            return None  # unmeasurable → fail-open
        if int(age) < min_age_days(config):
            return churn_note(int(age))
        return None
    except Exception:
        return None
