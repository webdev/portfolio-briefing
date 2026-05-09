# Earnings Guard Module

Helper module for detecting and surfacing earnings-window conflicts when proposing options trades (rolls, CSPs, short calls).

## Usage

### Import

```python
from analysis.earnings_guard import (
    parse_earnings_date,
    days_until_earnings,
    check_earnings_conflict,
    format_earnings_badge,
)
from datetime import date
```

### Function Signatures

#### `parse_earnings_date(s: str | None) -> date | None`

Parse an earnings date string (ISO format YYYY-MM-DD) to a date object.

```python
>>> parse_earnings_date("2026-05-20")
datetime.date(2026, 5, 20)
>>> parse_earnings_date(None)
None
>>> parse_earnings_date("invalid")
None
```

#### `days_until_earnings(ticker: str, earnings_calendar: dict[str, str] | None, as_of: date) -> int | None`

Calculate days from a reference date to the next known earnings.

```python
>>> earnings_cal = {"AAPL": "2026-05-20"}
>>> days_until_earnings("AAPL", earnings_cal, date(2026, 5, 8))
12
>>> days_until_earnings("MISSING", earnings_cal, date(2026, 5, 8))
None
```

**Returns:** int (days, negative if already passed) or None if not found or malformed.

#### `check_earnings_conflict(ticker: str, expiration: str, earnings_calendar: dict[str, str] | None, as_of: date) -> dict`

Check if an option's expiration overlaps with a known earnings date.

**Conflict Levels:**
- **"none"**: No earnings known OR earnings already passed OR earnings > 5 days after expiration
- **"warn"**: Earnings is 1-5 days before expiration AND earnings is >14 days away (IV-crush risk, non-imminent)
- **"block"**: Earnings within option's life AND earnings <=14 days from `as_of` (imminent binary event)

**Returns:** dict with keys:
- `conflict` (bool): True if earnings creates warning or block
- `level` (str): "none", "warn", or "block"
- `days_to_earnings` (int | None): days from `as_of` to earnings
- `message` (str): human-readable explanation

```python
>>> earnings_cal = {"NVDA": "2026-05-13"}
>>> result = check_earnings_conflict("NVDA", "2026-05-22", earnings_cal, date(2026, 5, 8))
>>> result
{
    'conflict': True,
    'level': 'block',
    'days_to_earnings': 5,
    'message': 'BLOCK: Imminent earnings 5d away, expires 14d'
}
```

#### `format_earnings_badge(check_result: dict) -> str`

Format a one-line markdown badge from `check_earnings_conflict()` output.

**Returns:**
- `""` (empty string) if no conflict
- `"🔴 ..."` for `level="block"`
- `"⚠️ ..."` for `level="warn"`

```python
>>> result = check_earnings_conflict("NVDA", "2026-05-22", {"NVDA": "2026-05-13"}, date(2026, 5, 8))
>>> format_earnings_badge(result)
'🔴 BLOCK: Imminent earnings 5d away, expires 14d'

>>> result = check_earnings_conflict("AAPL", "2026-05-22", {"AAPL": "2026-06-15"}, date(2026, 5, 8))
>>> format_earnings_badge(result)
''
```

## Integration with Panels

When rendering a recommendation (roll, CSP, short call) in `panels.py`:

```python
from analysis.earnings_guard import check_earnings_conflict, format_earnings_badge
from datetime import date

# In your recommendation rendering code:
earnings_calendar = snapshot.get("earnings_calendar", {})
as_of = date.fromisoformat(snapshot.get("date"))

# For each recommended trade:
check = check_earnings_conflict(
    ticker=trade_ticker,
    expiration=trade_expiration,  # YYYY-MM-DD format
    earnings_calendar=earnings_calendar,
    as_of=as_of
)

# If blocking level, skip the actionable trade:
if check.get("level") == "block":
    print(f"Skipping {trade_ticker}: {check['message']}")
    continue

# Otherwise, optionally append the badge to the trade line:
badge = format_earnings_badge(check)
if badge:
    trade_line += f"  {badge}"
```

## Test Coverage

39 unit tests covering:
- Date parsing (valid, None, empty, malformed)
- Days calculation (future, past, missing, edge cases)
- Conflict detection (6 conflict scenarios + edge cases)
- Badge formatting (all levels)
- Integration workflows
- Edge cases (leap years, year boundaries, very far dates)

Run tests:
```bash
cd /path/to/daily-portfolio-briefing
python3 -m pytest scripts/tests/test_earnings_guard.py -p no:cacheprovider -v
```

**All 39 tests pass.**

## Notes

- All dates are `datetime.date` objects internally; strings are ISO format (YYYY-MM-DD)
- Uses stdlib only (datetime, re modules)
- Pure-Python, no I/O — all inputs passed as arguments
- Earnings calendar is optional; None is safe (no conflict)
- Conflict logic is deterministic and testable
