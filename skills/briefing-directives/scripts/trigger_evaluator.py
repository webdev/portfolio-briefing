"""Trigger evaluation for briefing directives.

Each trigger type gets its own evaluator. Evaluators return (fired: bool, reason: str)
where fired indicates whether the trigger condition has been met.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


def evaluate_trigger(
    trigger_config: dict, directive: dict, current_state: dict
) -> tuple[bool, str]:
    """Evaluate a trigger and return (fired, reason).

    Args:
        trigger_config: The 'expires' dict from the directive
        directive: The full directive (for context)
        current_state: Current portfolio/market state

    Returns:
        (fired: bool, reason: str) - reason explains why or why not
    """
    trigger_type = trigger_config.get("trigger")

    if trigger_type == "time_elapsed":
        return _time_elapsed(trigger_config, directive, current_state)
    elif trigger_type == "earnings_passed":
        return _earnings_passed(trigger_config, directive, current_state)
    elif trigger_type == "position_closed":
        return _position_closed(trigger_config, directive, current_state)
    elif trigger_type == "price_above":
        return _price_above(trigger_config, directive, current_state)
    elif trigger_type == "price_below":
        return _price_below(trigger_config, directive, current_state)
    elif trigger_type == "screener_drops":
        return _screener_drops(trigger_config, directive, current_state)
    elif trigger_type == "manual_override":
        return _manual_override(trigger_config, directive, current_state)
    elif trigger_type == "open_ended":
        return _open_ended(trigger_config, directive, current_state)
    else:
        logger.warning(f"Unknown trigger type: {trigger_type}")
        return (False, f"unknown trigger type: {trigger_type}")


def _time_elapsed(trigger_config: dict, directive: dict, current_state: dict) -> tuple[bool, str]:
    """Trigger: time_elapsed. Fires when current_date >= until_date."""
    until_date_str = trigger_config.get("until_date")
    if not until_date_str:
        return (False, "missing until_date")

    try:
        until_date = datetime.fromisoformat(until_date_str).date()
    except (ValueError, TypeError) as e:
        return (False, f"invalid until_date format: {e}")

    current_date = current_state.get("current_date")
    if current_date is None:
        current_date = date.today()
    elif isinstance(current_date, str):
        try:
            current_date = datetime.fromisoformat(current_date).date()
        except (ValueError, TypeError):
            current_date = date.today()

    if current_date >= until_date:
        return (True, f"date reached: today is {current_date}, until_date was {until_date}")

    return (False, f"waiting: {(until_date - current_date).days} days remaining")


def _earnings_passed(
    trigger_config: dict, directive: dict, current_state: dict
) -> tuple[bool, str]:
    """Trigger: earnings_passed. Fires when next_earnings for symbol has passed."""
    symbol = trigger_config.get("symbol")
    if not symbol:
        return (False, "missing symbol")

    earnings_calendar = current_state.get("earnings_calendar", {})
    if symbol not in earnings_calendar:
        return (False, f"data_unavailable: earnings_calendar missing for {symbol}")

    next_earnings = earnings_calendar[symbol]
    if isinstance(next_earnings, str):
        try:
            next_earnings = datetime.fromisoformat(next_earnings).date()
        except (ValueError, TypeError):
            return (False, f"invalid earnings date for {symbol}")

    current_date = current_state.get("current_date")
    if current_date is None:
        current_date = date.today()
    elif isinstance(current_date, str):
        try:
            current_date = datetime.fromisoformat(current_date).date()
        except (ValueError, TypeError):
            current_date = date.today()

    if current_date > next_earnings:
        return (True, f"{symbol} earnings passed on {next_earnings}")

    days_until = (next_earnings - current_date).days
    return (False, f"waiting: {days_until} days until {symbol} earnings on {next_earnings}")


def _position_closed(trigger_config: dict, directive: dict, current_state: dict) -> tuple[bool, str]:
    """Trigger: position_closed. Fires when position no longer exists."""
    identifier = trigger_config.get("position_identifier")
    if not identifier:
        return (False, "missing position_identifier")

    positions = current_state.get("positions", [])

    # Check if position exists
    for pos in positions:
        if pos.get("identifier") == identifier:
            return (False, f"position still open: {identifier}")

    # Position not found in current list (either closed or data missing)
    # If we get here, position is not in the list
    return (True, f"position closed: {identifier} no longer in portfolio")


def _price_above(trigger_config: dict, directive: dict, current_state: dict) -> tuple[bool, str]:
    """Trigger: price_above. Fires when last_close >= level."""
    symbol = trigger_config.get("symbol")
    level = trigger_config.get("level")

    if not symbol or level is None:
        return (False, "missing symbol or level")

    last_close = current_state.get("last_close", {})
    if symbol not in last_close:
        return (False, f"data_unavailable: price missing for {symbol}")

    current_price = last_close[symbol]

    try:
        level = float(level)
        current_price = float(current_price)
    except (ValueError, TypeError):
        return (False, f"invalid price data for {symbol}")

    if current_price >= level:
        return (True, f"{symbol} closed at {current_price}, above trigger {level}")

    return (False, f"waiting: {symbol} at {current_price}, need {level}")


def _price_below(trigger_config: dict, directive: dict, current_state: dict) -> tuple[bool, str]:
    """Trigger: price_below. Fires when last_close <= level."""
    symbol = trigger_config.get("symbol")
    level = trigger_config.get("level")

    if not symbol or level is None:
        return (False, "missing symbol or level")

    last_close = current_state.get("last_close", {})
    if symbol not in last_close:
        return (False, f"data_unavailable: price missing for {symbol}")

    current_price = last_close[symbol]

    try:
        level = float(level)
        current_price = float(current_price)
    except (ValueError, TypeError):
        return (False, f"invalid price data for {symbol}")

    if current_price <= level:
        return (True, f"{symbol} closed at {current_price}, below trigger {level}")

    return (False, f"waiting: {symbol} at {current_price}, need <= {level}")


def _screener_drops(trigger_config: dict, directive: dict, current_state: dict) -> tuple[bool, str]:
    """Trigger: screener_drops. Fires when symbol no longer in screener output."""
    symbol = trigger_config.get("symbol")
    screener_name = trigger_config.get("screener_name")

    if not symbol or not screener_name:
        return (False, "missing symbol or screener_name")

    screener_outputs = current_state.get("screener_outputs", {})
    if screener_name not in screener_outputs:
        return (False, f"data_unavailable: screener output missing for {screener_name}")

    candidates = screener_outputs[screener_name]
    if not isinstance(candidates, list):
        candidates = list(candidates)

    if symbol in candidates:
        return (False, f"{symbol} still in {screener_name}")

    return (True, f"{symbol} dropped from {screener_name}")


def _manual_override(
    trigger_config: dict, directive: dict, current_state: dict
) -> tuple[bool, str]:
    """Trigger: manual_override. Never fires automatically."""
    return (False, "manual_override: never auto-fires, requires user override")


def _open_ended(trigger_config: dict, directive: dict, current_state: dict) -> tuple[bool, str]:
    """Trigger: open_ended. Never fires; auto-prompts after 30 days."""
    created_at_str = directive.get("created_at")
    if not created_at_str:
        return (False, "open_ended: no created_at")

    try:
        # Handle ISO format with timezone
        created_at_str_normalized = created_at_str.replace("Z", "+00:00")
        created_at = datetime.fromisoformat(created_at_str_normalized).date()
    except (ValueError, TypeError, AttributeError):
        return (False, "open_ended: invalid created_at format")

    current_date = current_state.get("current_date")
    if current_date is None:
        current_date = date.today()
    elif isinstance(current_date, str):
        try:
            current_date_str_normalized = current_date.replace("Z", "+00:00")
            current_date = datetime.fromisoformat(current_date_str_normalized).date()
        except (ValueError, TypeError):
            current_date = date.today()
    elif isinstance(current_date, datetime):
        current_date = current_date.date()

    days_old = (current_date - created_at).days
    if days_old >= 30:
        return (False, f"open_ended: created {days_old} days ago — prompt for renewal")

    return (False, f"open_ended: {30 - days_old} days until renewal prompt")


def evaluate_all_active(state_dir: str, current_state: dict) -> list[dict]:
    """Evaluate all ACTIVE directives and transition expired ones.

    Args:
        state_dir: Path to state/directives/
        current_state: Current portfolio/market state

    Returns:
        List of directives that expired during this evaluation
    """
    from directive_store import list as list_directives
    from directive_store import transition

    expired_today = []

    active_directives = list_directives(state_dir, status="ACTIVE")
    for directive in active_directives:
        trigger_config = directive.get("expires", {})
        trigger_type = trigger_config.get("trigger")

        fired, reason = evaluate_trigger(trigger_config, directive, current_state)

        if fired:
            directive_id = directive["directive_id"]
            try:
                updated = transition(
                    state_dir,
                    directive_id,
                    "EXPIRED",
                    reason,
                )
                expired_today.append(updated)
                logger.info(f"Expired {directive_id}: {reason}")
            except Exception as e:
                logger.error(f"Failed to transition {directive_id}: {e}")
        else:
            # Log debug info
            if "data_unavailable" in reason:
                logger.debug(f"Cannot evaluate {directive.get('directive_id')}: {reason}")

    return expired_today
