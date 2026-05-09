"""Apply active directives to recommendations.

Filters and modifies recommendation lists based on active directives.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def apply_directives(
    candidates: list[dict],
    active_directives: list[dict],
) -> list[dict]:
    """Apply active directives to recommendation candidates.

    Modifies candidates in-place by:
    - Setting recommendation='DEFERRED' for DEFER directives
    - Setting recommendation='MANUAL' for MANUAL directives
    - Setting recommendation='WATCH_ONLY' with trigger condition for WATCH_ONLY
    - Dropping entirely for SUPPRESS directives
    - Adding override_params for OVERRIDE directives

    Args:
        candidates: List of recommendation dicts, each with 'kind' and target fields
        active_directives: List of active directive dicts from directive_store.list()

    Returns:
        Modified list of candidates (some may be removed, some may have modified fields)
    """
    if not active_directives:
        return candidates

    result = []

    for candidate in candidates:
        # Find all directives that match this candidate
        matching_directives = []
        for directive in active_directives:
            if _targets_match(directive["target"], candidate):
                matching_directives.append(directive)

        if not matching_directives:
            # No directives affect this candidate
            result.append(candidate)
            continue

        # Apply directives in priority order (most specific first)
        # SUPPRESS completely removes the candidate
        suppress_directive = next(
            (d for d in matching_directives if d["type"] == "SUPPRESS"), None
        )
        if suppress_directive:
            logger.debug(
                f"Suppressed {candidate.get('ticker', '?')}: {suppress_directive['directive_id']}"
            )
            continue  # Don't add to result

        # Other directives modify the candidate
        modified_candidate = candidate.copy()

        # Apply DEFER → recommendation becomes DEFERRED
        defer_directive = next((d for d in matching_directives if d["type"] == "DEFER"), None)
        if defer_directive:
            modified_candidate["recommendation"] = "DEFERRED"
            modified_candidate["deferred_reason"] = defer_directive.get("reason", "")
            modified_candidate["deferred_until"] = defer_directive.get("expires", {}).get(
                "earliest_resurface"
            )
            modified_candidate["directive_id"] = defer_directive["directive_id"]
            logger.debug(
                f"Deferred {candidate.get('ticker', '?')}: {defer_directive['directive_id']}"
            )
            result.append(modified_candidate)
            continue

        # Apply MANUAL → recommendation becomes MANUAL
        manual_directive = next((d for d in matching_directives if d["type"] == "MANUAL"), None)
        if manual_directive:
            modified_candidate["recommendation"] = "MANUAL"
            modified_candidate["manual_reason"] = manual_directive.get("reason", "")
            modified_candidate["directive_id"] = manual_directive["directive_id"]
            logger.debug(
                f"Manual {candidate.get('ticker', '?')}: {manual_directive['directive_id']}"
            )
            result.append(modified_candidate)
            continue

        # Apply WATCH_ONLY → recommendation becomes WATCH_ONLY
        watch_directive = next(
            (d for d in matching_directives if d["type"] == "WATCH_ONLY"), None
        )
        if watch_directive:
            modified_candidate["recommendation"] = "WATCH_ONLY"
            modified_candidate["watch_reason"] = watch_directive.get("reason", "")
            trigger = watch_directive.get("expires", {})
            modified_candidate["watch_trigger"] = _format_trigger(trigger)
            modified_candidate["directive_id"] = watch_directive["directive_id"]
            logger.debug(
                f"Watching {candidate.get('ticker', '?')}: {watch_directive['directive_id']}"
            )
            result.append(modified_candidate)
            continue

        # Apply OVERRIDE → add override_params
        override_directive = next(
            (d for d in matching_directives if d["type"] == "OVERRIDE"), None
        )
        if override_directive:
            modified_candidate["override_params"] = {
                "parameter": override_directive.get("parameter"),
                "new_value": override_directive.get("new_value"),
                "old_value": override_directive.get("old_value"),
            }
            modified_candidate["directive_id"] = override_directive["directive_id"]
            logger.debug(
                f"Override {candidate.get('ticker', '?')}: {override_directive['directive_id']}"
            )
            result.append(modified_candidate)
            continue

        # If we get here, no directive applied (shouldn't happen if matching_directives exists)
        result.append(modified_candidate)

    return result


def _targets_match(directive_target: dict, candidate: dict) -> bool:
    """Check if directive_target matches a recommendation candidate.

    This mirrors the logic in directive_store._targets_match but operates on
    the candidate dict from the briefing, which may have different key names.
    """
    directive_kind = directive_target.get("kind")

    # Option position match
    if directive_kind == "option_position":
        candidate_identifier = candidate.get("identifier")
        if not candidate_identifier:
            return False
        directive_identifier = directive_target.get("identifier")
        if candidate_identifier != directive_identifier:
            return False
        return True

    # Position scope match
    if directive_kind == "position_scope":
        candidate_symbol = candidate.get("ticker") or candidate.get("symbol")
        if not candidate_symbol:
            return False
        directive_symbol = directive_target.get("symbol")
        if candidate_symbol != directive_symbol:
            return False
        # Optional position_type filter
        directive_pos_type = directive_target.get("position_type")
        if directive_pos_type:
            candidate_pos_type = candidate.get("position_type")
            if candidate_pos_type != directive_pos_type:
                return False
        return True

    # New idea match
    if directive_kind == "new_idea":
        candidate_symbol = candidate.get("ticker") or candidate.get("symbol")
        if not candidate_symbol:
            return False
        directive_symbol = directive_target.get("symbol")
        if candidate_symbol != directive_symbol:
            return False
        # Optional source_screener filter
        directive_screener = directive_target.get("source_screener")
        if directive_screener:
            candidate_screener = candidate.get("source_screener") or candidate.get("screener")
            if candidate_screener != directive_screener:
                return False
        return True

    # Symbol match (broad)
    if directive_kind == "symbol":
        candidate_symbol = candidate.get("ticker") or candidate.get("symbol")
        directive_symbol = directive_target.get("symbol")
        if not candidate_symbol:
            return False
        if candidate_symbol != directive_symbol:
            return False
        # Scope filter: only match if scope matches
        scope = directive_target.get("scope", "all")
        if scope == "long_only":
            # Only match if candidate is long (heuristic: not marked as short)
            if candidate.get("position_type", "").startswith("short"):
                return False
        return True

    return False


def _format_trigger(trigger: dict) -> str:
    """Format a trigger dict into a human-readable string."""
    trigger_type = trigger.get("trigger", "unknown")

    if trigger_type == "time_elapsed":
        until_date = trigger.get("until_date", "?")
        return f"time passes until {until_date}"
    elif trigger_type == "earnings_passed":
        symbol = trigger.get("symbol", "?")
        return f"earnings for {symbol} pass"
    elif trigger_type == "position_closed":
        identifier = trigger.get("position_identifier", "?")
        return f"position {identifier} closes"
    elif trigger_type == "price_above":
        symbol = trigger.get("symbol", "?")
        level = trigger.get("level", "?")
        return f"{symbol} closes above ${level}"
    elif trigger_type == "price_below":
        symbol = trigger.get("symbol", "?")
        level = trigger.get("level", "?")
        return f"{symbol} closes below ${level}"
    elif trigger_type == "screener_drops":
        symbol = trigger.get("symbol", "?")
        screener = trigger.get("screener_name", "?")
        return f"{symbol} drops from {screener}"
    elif trigger_type == "open_ended":
        return "open-ended (30-day renewal prompt)"
    elif trigger_type == "manual_override":
        return "manual override"
    else:
        return f"unknown trigger: {trigger_type}"
