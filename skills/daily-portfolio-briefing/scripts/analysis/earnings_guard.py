"""
Earnings-window guard for options recommendations.

Detects when an options trade (roll, CSP, short call) would overlap
with a known earnings date, and surfaces warnings or blocks recommendations.
"""

from datetime import date, datetime
from typing import Optional


def parse_earnings_date(s: str | None) -> date | None:
    """
    Parse an earnings date string to a date object.

    Args:
        s: earnings date string in ISO format (YYYY-MM-DD) or None

    Returns:
        date object, or None if s is None, empty, or malformed
    """
    if not s:
        return None

    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _coerce_date(value) -> date | None:
    """Coerce a date | datetime | YYYY-MM-DD string to a date. None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return parse_earnings_date(value)
    return None


def days_until_earnings(
    ticker: str,
    earnings_calendar: dict[str, str] | None,
    as_of: date | str,
) -> int | None:
    """
    Calculate days from as_of to the next known earnings date for a ticker.

    Args:
        ticker: stock symbol (e.g., "AAPL")
        earnings_calendar: dict mapping ticker -> earnings_date (YYYY-MM-DD string)
                          or None if unavailable
        as_of: reference date (typically today). Accepts date, datetime, or
               YYYY-MM-DD string — call sites in render/panels.py pass strings.

    Returns:
        int: number of days from as_of to earnings (negative if already passed)
        None: if ticker has no earnings date or earnings_calendar is None
    """
    if not earnings_calendar:
        return None

    earnings_str = earnings_calendar.get(ticker)
    if not earnings_str:
        return None

    earnings_date = parse_earnings_date(earnings_str)
    if not earnings_date:
        return None

    as_of_date = _coerce_date(as_of)
    if as_of_date is None:
        return None

    delta = earnings_date - as_of_date
    return delta.days


def check_earnings_conflict(
    ticker: str,
    expiration: str,  # YYYY-MM-DD format
    earnings_calendar: dict[str, str] | None,
    as_of: date | str,
) -> dict:
    """
    Check if an options expiration overlaps with a known earnings date.

    Conflict levels:
    - "none": no earnings or earnings > 5 days after expiration
    - "warn": earnings is 1-5 days before expiration (premium may face IV crush)
    - "block": earnings within option's life AND earnings <= 14 days from as_of (imminent)

    Args:
        ticker: stock symbol
        expiration: option expiration date (YYYY-MM-DD)
        earnings_calendar: dict mapping ticker -> earnings_date (YYYY-MM-DD string)
        as_of: reference date (typically today)

    Returns:
        dict with keys:
        - conflict (bool): True if earnings creates a warning or block
        - level (str): "none", "warn", or "block"
        - days_to_earnings (int | None): days from as_of to earnings
        - message (str): human-readable explanation
    """
    # Parse expiration date
    exp_date = parse_earnings_date(expiration)
    if not exp_date:
        return {
            "conflict": False,
            "level": "none",
            "days_to_earnings": None,
            "message": "Invalid expiration date format",
        }

    # Normalize as_of to a date — call sites in render/panels.py pass strings.
    as_of_date = _coerce_date(as_of)
    if as_of_date is None:
        return {
            "conflict": False,
            "level": "none",
            "days_to_earnings": None,
            "message": "Invalid as_of date",
        }
    as_of = as_of_date  # rest of this function expects a date

    # Get earnings date for ticker
    days_to_earnings = days_until_earnings(ticker, earnings_calendar, as_of)

    if days_to_earnings is None:
        return {
            "conflict": False,
            "level": "none",
            "days_to_earnings": None,
            "message": "No earnings date found",
        }

    # Parse the actual earnings date
    earnings_str = earnings_calendar.get(ticker)
    earnings_date = parse_earnings_date(earnings_str)

    # Case 1: Earnings have already passed
    if days_to_earnings < 0:
        return {
            "conflict": False,
            "level": "none",
            "days_to_earnings": days_to_earnings,
            "message": f"Earnings already passed ({days_to_earnings} days ago)",
        }

    # Case 2: Earnings is well after option expires (>5 days after expiration)
    days_after_exp = days_to_earnings - (exp_date - as_of).days
    if days_after_exp > 5:
        return {
            "conflict": False,
            "level": "none",
            "days_to_earnings": days_to_earnings,
            "message": f"Earnings {days_to_earnings} days away, after expiration",
        }

    # Case 3: Earnings within 14 days of as_of AND before/on expiration
    # This is an imminent binary event → BLOCK
    if days_to_earnings <= 14 and earnings_date <= exp_date:
        return {
            "conflict": True,
            "level": "block",
            "days_to_earnings": days_to_earnings,
            "message": f"BLOCK: Imminent earnings {days_to_earnings}d away, expires {(exp_date - as_of).days}d",
        }

    # Case 4: Earnings before expiration but >14 days away AND within 1-5 days before exp
    # This is IV-crush risk but not imminent → WARN
    if 1 <= days_to_earnings <= (exp_date - as_of).days and earnings_date <= exp_date:
        return {
            "conflict": True,
            "level": "warn",
            "days_to_earnings": days_to_earnings,
            "message": f"⚠️ Earnings {days_to_earnings}d away, {days_after_exp}d before expiration",
        }

    # Case 5: Earnings well after expiration
    return {
        "conflict": False,
        "level": "none",
        "days_to_earnings": days_to_earnings,
        "message": f"Earnings {days_to_earnings} days away, after expiration",
    }


def format_earnings_badge(check_result: dict) -> str:
    """
    Format a one-line markdown badge from a check_earnings_conflict result.

    Args:
        check_result: dict from check_earnings_conflict()

    Returns:
        markdown string, e.g., "⚠️ Earnings 2026-05-20 (12d before exp)"
        Returns empty string if no conflict
    """
    if not check_result.get("conflict"):
        return ""

    level = check_result.get("level")
    msg = check_result.get("message", "")

    if level == "block":
        return f"🔴 {msg}"
    elif level == "warn":
        return f"⚠️ {msg}"
    else:
        return ""
