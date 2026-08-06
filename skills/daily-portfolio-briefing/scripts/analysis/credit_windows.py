"""Briefing-side credit-window plumbing (task #38, Part 2).

Wraps the wheel-roll-advisor's ``credit_window.assess_credit_window`` for the
briefing pipeline: classify every held short put's credit-roll window from
the roll candidates the advisor already priced, render the state one line
above the ROLL ANALYSIS table, persist per-contract states in the daily
snapshot, and emit a Risk Alert when a contract's window transitions
open→closing or open→debit_only since the last briefing.

Why: the MU $950P went from credit-roll territory at the strike test
(2026-07-22) to a $4,700 roll debit one week later — in silence. The window
closing IS the event; this makes it loud.

Everything here is fail-open: a missing wheel module, unreadable state file,
or malformed review must never break the briefing (and never fabricate a
state — CLAUDE.md #19).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_WHEEL_SCRIPTS = (Path(__file__).resolve().parents[3]
                  / "wheel-roll-advisor" / "scripts")

_STATE_FILENAME = "credit_windows.json"


def _assess(position: dict, candidates, config: dict):
    """Call the wheel skill's assess_credit_window; None on any failure."""
    try:
        if str(_WHEEL_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_WHEEL_SCRIPTS))
        from credit_window import assess_credit_window  # type: ignore
        return assess_credit_window(position, candidates, config)
    except Exception:
        return None


def credit_window_for_review(review: dict, config: dict | None = None) -> Optional[dict]:
    """Classify one options-review row (short puts only) → state dict or None.

    Returns {"state", "best_credit", "best_candidate_desc", "threshold_used"}
    for a held SHORT PUT; None for calls / long positions / failures.
    """
    try:
        if (review.get("type") or "").upper() != "PUT":
            return None
        qty = float(review.get("qty", 0) or 0)
        if qty >= 0:
            return None
        position = {"strikePrice": review.get("strike"),
                    "quantity": abs(int(qty))}
        cw = _assess(position, review.get("roll_candidates") or [],
                     (config or {}))
        if cw is None:
            return None
        return {
            "state": cw.state,
            "best_credit": cw.best_credit,
            "best_candidate_desc": cw.best_candidate_desc,
            "threshold_used": cw.threshold_used,
        }
    except Exception:
        return None


def build_credit_windows(options_reviews: list, config: dict | None = None) -> dict:
    """Map contract → credit-window state dict for every held short put."""
    out: dict = {}
    for review in (options_reviews or []):
        contract = review.get("contract")
        if not contract:
            continue
        cw = credit_window_for_review(review, config)
        if cw is not None:
            out[contract] = cw
    return out


def format_credit_window_line(cw: dict) -> Optional[str]:
    """One markdown line for the Watch panel. Measured values only."""
    state = (cw or {}).get("state")
    best = (cw or {}).get("best_credit")
    desc = (cw or {}).get("best_candidate_desc")
    if state == "open" and best is not None:
        tail = f" ({desc})" if desc else ""
        return (f"**Credit window: 🟢 OPEN** — best credit roll: "
                f"${best:.2f}/share{tail}")
    if state == "closing" and best is not None:
        tail = f" ({desc})" if desc else ""
        return (f"**Credit window: 🟡 CLOSING** — ${best:.2f}/share "
                f"left{tail} — act while a credit remains")
    if state == "debit_only":
        return ("**Credit window: 🔴 DEBIT-ONLY** — rolls now cost money; "
                "the decision is close / accept assignment / pay for cushion")
    if state == "unknown":
        return ("**Credit window: ⚪ UNKNOWN** — no priced roll candidates "
                "this cycle (verify roll economics at the broker)")
    return None


# ── State persistence + transition alerts ──────────────────────────────────

def persist_credit_windows(snapshot_dir: Path, cw_map: dict,
                           date_str: str | None = None) -> None:
    """Write today's per-contract states to <snapshot_dir>/credit_windows.json."""
    try:
        snapshot_dir = Path(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        payload = {"date": date_str or datetime.now().strftime("%Y-%m-%d"),
                   "windows": cw_map or {}}
        (snapshot_dir / _STATE_FILENAME).write_text(
            json.dumps(payload, indent=2))
    except Exception:
        pass  # fail-open — state is an enhancement, never a blocker


def load_previous_credit_windows(snapshot_root: Path, today_iso: str,
                                 lookback_days: int = 7) -> tuple[dict, Optional[str]]:
    """Most recent prior day's credit_windows.json (≤ lookback_days back).

    Returns ({} , None) on first run / missing history — no alert fires
    (fail-open per spec).
    """
    try:
        today = datetime.strptime(today_iso, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {}, None
    root = Path(snapshot_root)
    for offset in range(1, lookback_days + 1):
        d = (today - timedelta(days=offset)).isoformat()
        p = root / d / _STATE_FILENAME
        if not p.exists():
            continue
        try:
            payload = json.loads(p.read_text())
            windows = payload.get("windows")
            if isinstance(windows, dict):
                return windows, d
        except Exception:
            continue
    return {}, None


def transition_alerts(today_map: dict, prev_map: dict,
                      prev_date: str | None = None) -> list[str]:
    """Risk-Alert strings for contracts whose window went open→closing or
    open→debit_only since the last briefing. First-run / no history → []."""
    alerts: list[str] = []
    if not today_map or not prev_map:
        return alerts
    was = f"was open on {prev_date}" if prev_date else "was open yesterday"
    for contract in sorted(today_map):
        prev_state = ((prev_map.get(contract) or {}).get("state"))
        state = ((today_map.get(contract) or {}).get("state"))
        if prev_state != "open":
            continue
        if state == "closing":
            best = (today_map[contract] or {}).get("best_credit")
            left = f" — ${best:.2f}/share left" if best is not None else ""
            alerts.append(
                f"⏰ {contract}: credit-roll window CLOSING ({was}){left} — "
                f"act while a credit remains")
        elif state == "debit_only":
            alerts.append(
                f"⏰ {contract}: credit-roll window now DEBIT-ONLY ({was}) — "
                f"rolls cost money from here; decide close / accept "
                f"assignment / pay for cushion")
    return alerts
