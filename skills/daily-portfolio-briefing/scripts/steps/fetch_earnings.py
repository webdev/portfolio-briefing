"""
Fetch upcoming earnings dates for tickers.

Uses yfinance to get next earnings date for each symbol.
Handles both newer yfinance (calendar dict) and older yfinance
(DataFrame via get_earnings_dates).
"""

import sys
import threading
from datetime import datetime, timedelta
from typing import Optional


def _fetch_earnings_for_ticker(ticker: str, timeout: int = 5) -> Optional[str]:
    """
    Fetch next earnings date for a single ticker via yfinance.
    Returns None if not found or on error.
    Returns date string as YYYY-MM-DD.
    """
    result = [None]
    error = [None]

    def _worker():
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)

            # Try the newer .calendar property first (dict with Earnings Date list)
            try:
                if hasattr(t, "calendar") and t.calendar:
                    cal = t.calendar
                    # cal is sometimes a dict with key 'Earnings Date'
                    if isinstance(cal, dict):
                        earnings_list = cal.get("Earnings Date", [])
                        if earnings_list and len(earnings_list) > 0:
                            # earnings_list is a list of datetime.date or timestamp
                            earnings_date = earnings_list[0]
                            if hasattr(earnings_date, "strftime"):
                                result[0] = earnings_date.strftime("%Y-%m-%d")
                            else:
                                result[0] = str(earnings_date)
                            return
            except Exception:
                pass

            # Fallback: use .get_earnings_dates() which returns DataFrame
            try:
                earnings_df = t.get_earnings_dates(limit=4)
                if earnings_df is not None and not earnings_df.empty:
                    # earnings_df is indexed by date; filter to future dates
                    now = datetime.now().date()
                    for earnings_date_idx in earnings_df.index:
                        # Convert timestamp to date if needed
                        if hasattr(earnings_date_idx, "date"):
                            earnings_date = earnings_date_idx.date()
                        else:
                            earnings_date = earnings_date_idx
                        if earnings_date >= now:
                            result[0] = earnings_date.strftime("%Y-%m-%d")
                            return
            except Exception:
                pass

        except Exception as e:
            error[0] = str(e)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if result[0]:
        return result[0]
    return None


def fetch_earnings_dates(symbols: list[str], timeout: int = 5) -> dict[str, str]:
    """
    Fetch next earnings dates for a list of symbols.

    Args:
        symbols: list of ticker symbols
        timeout: seconds to wait per ticker fetch (default 5)

    Returns:
        dict mapping symbol -> earnings_date (YYYY-MM-DD).
        Omits symbols where earnings date could not be fetched.
    """
    earnings_dates = {}

    for sym in symbols:
        if not sym:
            continue
        try:
            date_str = _fetch_earnings_for_ticker(sym, timeout=timeout)
            if date_str:
                earnings_dates[sym] = date_str
                print(f"    [info] {sym}: earnings {date_str}")
            else:
                print(f"    [info] {sym}: no earnings date found", file=sys.stderr)
        except Exception as e:
            print(f"    [warn] {sym}: earnings fetch error: {e}", file=sys.stderr)

    return earnings_dates
