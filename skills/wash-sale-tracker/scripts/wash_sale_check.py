"""
Wash sale tracker — IRS Pub 550 compliance checker.

This module tracks closed-at-loss trades and prevents wash-sale violations
by checking if a ticker is within the 30-day re-entry window.

Public interface:
  - record_trade_close(ticker, close_date, realized_pl) → None
  - is_wash_sale_blocked(ticker, as_of_date) → tuple[bool, str]
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple


def _get_ledger_path(ledger_path: Optional[str] = None) -> Path:
    """
    Determine the ledger file path.

    If ledger_path is provided, use it (for testing).
    Otherwise, use default: state/wash_sale_ledger.json (relative to this script's parent).
    """
    if ledger_path:
        return Path(ledger_path)
    # Default: state/wash_sale_ledger.json in the project root
    # (assumed to be parent of parent of this script)
    script_dir = Path(__file__).parent
    return script_dir.parent.parent.parent / "state" / "wash_sale_ledger.json"


def _load_ledger(ledger_path: Optional[str] = None) -> dict:
    """Load the wash-sale ledger from disk. Return empty ledger if file doesn't exist."""
    path = _get_ledger_path(ledger_path)
    if not path.exists():
        return {"version": 1, "records": []}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"version": 1, "records": []}


def _save_ledger(ledger: dict, ledger_path: Optional[str] = None) -> None:
    """Save the wash-sale ledger to disk."""
    path = _get_ledger_path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(ledger, f, indent=2)


def record_trade_close(
    ticker: str,
    close_date: str,
    realized_pl: float,
    ledger_path: Optional[str] = None,
) -> None:
    """
    Record a closed trade in the ledger.

    Only losses (realized_pl < 0) are recorded. Profitable closes are ignored.

    Args:
        ticker: Stock symbol (uppercase, e.g., "MU", "AAPL")
        close_date: Close date as ISO string "YYYY-MM-DD"
        realized_pl: Realized P&L in dollars; only < 0 triggers wash-sale window
        ledger_path: Optional path to ledger file (for testing)

    Returns:
        None
    """
    if realized_pl >= 0:
        # Profitable close; no wash-sale rule applies
        return

    ledger = _load_ledger(ledger_path)
    # Append the loss record
    record = {
        "ticker": ticker.upper(),
        "close_date": close_date,
        "loss_dollars": float(realized_pl),
    }
    ledger["records"].append(record)
    _save_ledger(ledger, ledger_path)


def is_wash_sale_blocked(
    ticker: str,
    as_of_date: str,
    ledger_path: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Check if a ticker is blocked from re-entry due to a recent loss close.

    Args:
        ticker: Stock symbol (uppercase)
        as_of_date: Check date as ISO string "YYYY-MM-DD"
        ledger_path: Optional path to ledger file (for testing)

    Returns:
        (blocked: bool, reason: str)
        If blocked, reason includes loss amount, close date, days remaining, and unblock date.
        If not blocked, reason is empty string.

    Example:
        blocked, reason = is_wash_sale_blocked("MU", "2026-05-14")
        # Returns (True, "MU closed at -$340 on 2026-04-15, 29 days ago — re-entry blocked until 2026-05-15")
    """
    ledger = _load_ledger(ledger_path)
    ticker_upper = ticker.upper()

    # Find all loss records for this ticker
    losses_for_ticker = [
        r for r in ledger["records"]
        if r["ticker"] == ticker_upper
    ]

    if not losses_for_ticker:
        return (False, "")

    # Use the most recent loss
    most_recent_loss = max(losses_for_ticker, key=lambda r: r["close_date"])
    loss_date_str = most_recent_loss["close_date"]
    loss_amount = most_recent_loss["loss_dollars"]

    # Parse dates
    try:
        loss_date = datetime.strptime(loss_date_str, "%Y-%m-%d").date()
        check_date = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    except ValueError:
        # Invalid date format; assume not blocked (safe fallback)
        return (False, "")

    # Calculate days since loss
    days_since_loss = (check_date - loss_date).days

    # Blocked if 0 < days_since_loss <= 30
    if 0 < days_since_loss <= 30:
        unblock_date = loss_date + timedelta(days=31)
        reason = (
            f"{ticker_upper} closed at ${loss_amount:.0f} on {loss_date_str}, "
            f"{days_since_loss} days ago — re-entry blocked until {unblock_date.strftime('%Y-%m-%d')}"
        )
        return (True, reason)

    # Not blocked
    return (False, "")


if __name__ == "__main__":
    # Simple CLI for testing
    import sys

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python wash_sale_check.py record <ticker> <date> <pl>")
        print("  python wash_sale_check.py check <ticker> <date>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "record":
        if len(sys.argv) != 5:
            print("Usage: python wash_sale_check.py record <ticker> <date> <pl>")
            sys.exit(1)
        ticker, date, pl = sys.argv[2], sys.argv[3], float(sys.argv[4])
        record_trade_close(ticker, date, pl)
        print(f"Recorded: {ticker} closed at {pl} on {date}")

    elif cmd == "check":
        if len(sys.argv) != 4:
            print("Usage: python wash_sale_check.py check <ticker> <date>")
            sys.exit(1)
        ticker, date = sys.argv[2], sys.argv[3]
        blocked, reason = is_wash_sale_blocked(ticker, date)
        if blocked:
            print(f"BLOCKED: {reason}")
        else:
            print(f"OK: {ticker} is clear on {date}")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
