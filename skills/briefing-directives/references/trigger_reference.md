# Trigger Reference

Complete documentation of all expiry trigger types and how they evaluate.

## Trigger Evaluation Behavior

Each trigger type has specific evaluation logic:

### 1. time_elapsed

**Description:** Fires on an absolute date.

**Configuration:**
```yaml
expires:
  trigger: time_elapsed
  until_date: "2026-05-22"  # ISO date (YYYY-MM-DD)
```

**Evaluation Logic:**
```
fired = current_date >= until_date
```

**Example:** "Defer rolling AAPL put until May 22, when earnings are released."
- Created: 2026-05-07
- Expires: 2026-05-22
- Fires: On 2026-05-22 and every day after
- Once fired: Directive transitions to EXPIRED; recommendation re-surfaces

**Use case:** Fixed-date expirations (after earnings, after earnings report, after known event).

---

### 2. earnings_passed

**Description:** Fires after earnings for a stock have been released.

**Configuration:**
```yaml
expires:
  trigger: earnings_passed
  symbol: AAPL
```

**Evaluation Logic:**
```
next_earnings_date = earnings_calendar[symbol]
fired = current_date > next_earnings_date
```

**Data requirements:**
- `current_state["earnings_calendar"]` must contain `{symbol: date}`
- If missing: trigger returns `data_unavailable` and directive stays ACTIVE

**Example:** "Defer rolling AAPL put until after earnings."
- AAPL earnings: 2026-05-21
- Directive created: 2026-05-07
- Fires: 2026-05-22 (day after earnings)
- Once fired: Position re-surfaces in briefing

**Use case:** Event-driven expiries tied to earnings calendar.

---

### 3. position_closed

**Description:** Fires when a position is closed (removed from portfolio).

**Configuration:**
```yaml
expires:
  trigger: position_closed
  position_identifier: "AAPL  260619P00170000"
```

**Evaluation Logic:**
```
for pos in current_state["positions"]:
    if pos["identifier"] == position_identifier:
        return (False, "position still open")
return (True, "position closed")
```

**Data requirements:**
- `current_state["positions"]` list of position dicts with `identifier` field
- If empty/missing: Assumes all positions closed (fires)

**Example:** "Defer close recommendation until position actually closes."
- Directive created on open position
- When position is closed (manually or via briefing action)
- Fires: On next briefing run when position is no longer in portfolio

**Use case:** Conditional deferrals that end when action completes.

---

### 4. price_above

**Description:** Fires when stock price crosses above a threshold.

**Configuration:**
```yaml
expires:
  trigger: price_above
  symbol: NVDA
  level: 185.00
```

**Evaluation Logic:**
```
current_price = current_state["last_close"][symbol]
fired = current_price >= level
```

**Data requirements:**
- `current_state["last_close"]` dict: `{symbol: price}`
- If missing: trigger returns `data_unavailable` and directive stays ACTIVE

**Example:** "Watch NVDA but don't enter until it closes above $185."
- Directive created: NVDA at 182.00
- Trigger: 185.00
- Fires: When last_close >= 185.00
- Once fired: Recommendation re-surfaces with "trigger met, entry condition cleared"

**Use case:** Watch-and-wait on breakout levels, entry confirmation.

---

### 5. price_below

**Description:** Fires when stock price drops below a threshold.

**Configuration:**
```yaml
expires:
  trigger: price_below
  symbol: TSLA
  level: 240.00
```

**Evaluation Logic:**
```
current_price = current_state["last_close"][symbol]
fired = current_price <= level
```

**Data requirements:**
- `current_state["last_close"]` dict: `{symbol: price}`
- If missing: trigger returns `data_unavailable` and directive stays ACTIVE

**Example:** "Watch TSLA short setup but only if it drops to $240."
- Directive created: TSLA at 250.00
- Trigger: 240.00
- Fires: When last_close <= 240.00

**Use case:** Conditional entry on support/oversold levels.

---

### 6. screener_drops

**Description:** Fires when a symbol stops appearing in a screener's output.

**Configuration:**
```yaml
expires:
  trigger: screener_drops
  symbol: AAPL
  screener_name: vcp-screener
```

**Evaluation Logic:**
```
candidates = current_state["screener_outputs"][screener_name]
fired = symbol not in candidates
```

**Data requirements:**
- `current_state["screener_outputs"]` dict: `{screener_name: [symbols]}`
- If missing: trigger returns `data_unavailable` and directive stays ACTIVE

**Example:** "Watch AAPL from VCP screener; stop watching if it drops."
- Directive created: AAPL appears in vcp-screener output
- Fires: When AAPL no longer appears in next run's vcp-screener list
- Once fired: Watch ends; if re-enters screener later, new directive would be created

**Use case:** Stopping watch on candidates that fail momentum or strength criteria.

---

### 7. manual_override

**Description:** Never fires automatically. Requires explicit user override.

**Configuration:**
```yaml
expires:
  trigger: manual_override
```

**Evaluation Logic:**
```
return (False, "manual_override: never auto-fires")
```

**How to trigger manually:**
```bash
python3 cli.py override dir_20260507_aapl_defer_a3f1 --reason "Changed mind"
```

Or via Python:
```python
transition(state_dir, directive_id, "OVERRIDDEN", "User override")
```

**Example:** "I'm going to manually decide when to roll this."
- User wants to retain control without setting a specific exit condition
- Directive stays ACTIVE until user explicitly overrides
- Useful for manually-managed positions

**Use case:** Explicit control; management decisions deferred to user judgment.

---

### 8. open_ended

**Description:** Never fires. Directive remains ACTIVE indefinitely until explicitly overridden. After 30 days, the briefing prompts for renewal.

**Configuration:**
```yaml
expires:
  trigger: open_ended
```

**Evaluation Logic:**
```
days_old = (current_date - created_at).days
if days_old >= 30:
    return (False, "open_ended: prompt for renewal after 30 days")
return (False, "open_ended: active, no auto-expiry")
```

**Renewal prompt:** After 30 days, briefing shows:
```
MSFT short calls — MANUAL (since 2026-05-07; confirm renewal in 23 days)
```

**How to renew:** User confirms, and the 30-day clock resets. No explicit CLI action required; confirmation happens during briefing review.

**Example:** "I'm managing MSFT calls manually, ongoing."
- Created: 2026-05-07
- Status: ACTIVE (MANUAL) with no auto-expiry
- Day 30 (2026-06-06): Briefing prompts "Confirm you want to keep managing MSFT calls manually"
- User confirms: 30-day clock resets to 2026-06-06
- If user doesn't confirm: Directive transitions to EXPIRED and manual override ends

**Use case:** Ongoing manual management, long-term suppressions, permanent portfolio exclusions.

---

## Trigger Evaluation Failures

If a trigger cannot evaluate (missing data), it returns:
```python
(False, "data_unavailable: <what's missing>")
```

Directive stays ACTIVE and is re-evaluated on the next briefing run. This is safer than failing closed (suppressing a recommendation that should be shown) or failing open (surfacing one that shouldn't be).

**Examples:**
- `earnings_passed` on a symbol not in earnings_calendar → stays ACTIVE
- `last_close` missing for a symbol → trigger doesn't fire, try again next time
- `positions` list empty when evaluating `position_closed` → fires (position no longer exists)

---

## Typical Trigger Combinations by Directive Type

### DEFER
- `time_elapsed` — defer until a date
- `earnings_passed` — defer until after earnings
- `price_above` — defer until price validates setup
- `position_closed` — defer until position exits

### MANUAL
- `open_ended` — manage manually indefinitely (most common)

### WATCH_ONLY
- `price_above` — watch until breakout confirmed
- `price_below` — watch until oversold
- `screener_drops` — watch while screener surfaces it
- `earnings_passed` — watch until earnings are out

### SUPPRESS
- `open_ended` — suppress permanently (most common)
- `time_elapsed` — suppress until date (temporary blacklist)

### OVERRIDE
- `position_closed` — apply override until position closes
- `earnings_passed` — apply override until after earnings
- `time_elapsed` — apply override until a date
