"""Morning-briefing home page — the "read once, act, close" surface.

The user's primary workflow is: open in the morning, understand the
portfolio's state in 30 seconds, act on 1-3 urgent items, close the app.
This module derives everything the new Home page needs:

  - hero_sentence: one-line portfolio state ("🚨 Defensive mode — coverage
    0.07× critical, 1 urgent action")
  - urgent_actions: max 3 highest-priority action items
  - kpi_strip: flat NLV / Cash / Cov / Regime (no cards, no chrome)
  - diff_summary: one-line "since yesterday" answer
  - top_opportunities: top 3 actionable setups (from setups module)

All logic in this module is pure — takes injected data, returns dicts.
No I/O.
"""

from __future__ import annotations

from typing import Any


# ─── Hero sentence ─────────────────────────────────────────────────────────


def derive_hero_sentence(
    red_flags: list[dict],
    urgent_action_count: int,
    briefing: Any = None,
) -> dict[str, str]:
    """Build the one-sentence portfolio state that leads the Home page.

    Priority:
      1. If any critical flag → lead with it + action count
      2. If warnings present → "cautious mode" + count
      3. Else → "Portfolio healthy" + action count

    Returns dict with:
      - tone: "critical" | "warning" | "ok"
      - icon: emoji
      - text: the full sentence
    """
    criticals = [f for f in red_flags or [] if f.get("severity") == "critical"]
    warnings = [f for f in red_flags or [] if f.get("severity") == "warning"]

    action_suffix = ""
    if urgent_action_count == 1:
        action_suffix = ", 1 urgent action to review"
    elif urgent_action_count > 1:
        action_suffix = f", {urgent_action_count} urgent actions to review"

    if criticals:
        top = criticals[0]
        return {
            "tone": "critical",
            "icon": "🚨",
            "text": f"Defensive mode — {top['title'].lower()}{action_suffix}.",
        }
    if warnings:
        top = warnings[0]
        return {
            "tone": "warning",
            "icon": "📊",
            "text": f"Cautious posture — {top['title'].lower()}{action_suffix}.",
        }
    if urgent_action_count > 0:
        return {
            "tone": "warning",
            "icon": "⚡",
            "text": f"Portfolio healthy — {urgent_action_count} action{'s' if urgent_action_count != 1 else ''} pending review.",
        }
    return {
        "tone": "ok",
        "icon": "✓",
        "text": "Portfolio healthy — no urgent items today.",
    }


# ─── Urgent actions (top 3 by priority) ───────────────────────────────────


# Action kinds that are TIME-SENSITIVE (must-act-today priority)
_URGENT_KINDS = {
    "CLOSE", "DEFENSIVE_ROLL", "DEFENSIVE_COLLAR",
    "EXECUTE_ROLL", "HEDGE", "CLOSE_FOR_PROFIT",
    "TAKE_PROFIT", "ROLL_OUT_AND_UP",
}


def _action_priority(action: Any) -> int:
    """Lower = more urgent. Used to sort the action queue."""
    kind = (getattr(action, "kind", None) or "").upper()
    days = getattr(action, "days_flagged", None) or 0

    # Urgency tiers
    if kind == "CLOSE":
        # CLOSE with high P&L% = take-profit (less urgent than defensive)
        return 1 if days == 0 else 0
    if kind in ("DEFENSIVE_ROLL", "DEFENSIVE_COLLAR", "HEDGE"):
        return 0
    if kind in ("EXECUTE_ROLL", "ROLL_OUT_AND_UP"):
        return 2
    if kind in ("TAKE_PROFIT", "CLOSE_FOR_PROFIT"):
        return 3
    if kind in _URGENT_KINDS:
        return 4
    # Stale non-urgent actions get bumped up
    if days >= 6:
        return 5
    if days >= 3:
        return 6
    return 10


def top_urgent_actions(actions: list, limit: int = 3) -> list:
    """Return the top N most-urgent actions from the briefing."""
    if not actions:
        return []
    sorted_actions = sorted(actions, key=_action_priority)
    return sorted_actions[:limit]


# ─── Diff summary ─────────────────────────────────────────────────────────


def build_diff_summary(diff_result: dict) -> dict[str, Any] | None:
    """One-line "since yesterday" summary.

    Takes the same shape diff.compute_diff returns. Returns None when the
    diff is empty (no prior day to compare against).
    """
    if not diff_result:
        return None
    s = diff_result.get("scalars") or {}
    positions = diff_result.get("positions") or {}
    actions = diff_result.get("actions") or {}
    parts: dict[str, Any] = {}
    nlv = s.get("nlv") or {}
    if nlv.get("delta") is not None:
        parts["nlv_delta"] = nlv.get("delta")
        parts["nlv_pct"] = nlv.get("pct")
    parts["added"] = len(positions.get("added") or [])
    parts["removed"] = len(positions.get("removed") or [])
    parts["new_actions"] = len(actions.get("new") or [])
    parts["done_actions"] = len(actions.get("completed") or [])
    parts["pending_actions"] = len(actions.get("still_pending") or [])
    parts["dates"] = diff_result.get("dates") or {}
    return parts


# ─── Top opportunities (from setups) ──────────────────────────────────────


def top_actionable_from_setups(structured: dict, limit: int = 3) -> list[dict]:
    """Pull the top N ACTIONABLE cards from a parsed setups report.

    Filters to `ok`/`info` tones, sorts putting top-conviction and
    high-value markers first, returns the top N.
    """
    if not structured:
        return []
    all_actionable: list[dict] = []
    for sec in structured.get("sections") or []:
        for card in sec.get("cards") or []:
            if card.get("status_tone") in ("ok", "info"):
                all_actionable.append(card)

    def _sort_key(c: dict):
        flags = c.get("flags") or {}
        # Top conviction first, then RSI-favorable, then everything else
        priority = 0
        if flags.get("top_conviction"):
            priority -= 2
        if flags.get("rsi_favourable"):
            priority -= 1
        # Prefer ok (green) over info (deferred violet)
        if c.get("status_tone") == "ok":
            priority -= 3
        return (priority, c.get("ticker") or "")

    all_actionable.sort(key=_sort_key)
    return all_actionable[:limit]
