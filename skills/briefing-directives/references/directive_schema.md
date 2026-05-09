# Directive Schema

Complete YAML schema for briefing directives. All fields are documented below.

## Top-level fields

```yaml
directive_id: dir_20260507_aapl_defer_a3f1  # Auto-generated; format: dir_YYYYMMDD_<slug>_<hash>
type: DEFER                                   # Required: DEFER | MANUAL | OVERRIDE | WATCH_ONLY | SUPPRESS
status: ACTIVE                                # Auto-set: ACTIVE | EXPIRED | OVERRIDDEN | RESOLVED
created_at: 2026-05-07T09:30:00-04:00        # ISO 8601 timestamp (auto-generated)
created_via: briefing review session          # Source: CLI, programmatic, briefing review session
reason: "Waiting for earnings to clear."      # Required; free-text explanation
target: {...}                                 # Required; see Target schema below
expires: {...}                                # Required; see Expires schema below
status_history: [...]                         # Auto-maintained; list of status transitions
parameter: ...                                # OVERRIDE only: parameter name
new_value: ...                                # OVERRIDE only: replacement value
old_value: ...                                # OVERRIDE only: original value for audit trail
```

## Target Schema

Directive targets can be at multiple scopes:

### option_position — specific options contract

```yaml
target:
  kind: option_position
  identifier: "AAPL  260619P00170000"  # Required: ticker + expiry + strike + type
```

**Matching behavior:** Matches only this exact contract.

### position_scope — all positions in symbol or symbol+type

```yaml
target:
  kind: position_scope
  symbol: MSFT                           # Required
  position_type: short_call              # Optional: short_call | short_put | long_call | long_put
```

**Matching behavior:**
- If `position_type` omitted, matches all positions in the symbol
- If `position_type` specified, matches only that type for the symbol

### new_idea — screener candidate

```yaml
target:
  kind: new_idea
  symbol: NVDA                           # Required
  source_screener: vcp-screener          # Required: screener name
```

**Matching behavior:** Matches this symbol when surfaced by this screener only.

### symbol — broad symbol-level rule

```yaml
target:
  kind: symbol
  symbol: BABA                           # Required
  scope: long_only                       # Optional: all | long_only (default: all)
```

**Matching behavior:**
- Matches any recommendation involving this symbol
- If `scope: long_only`, only matches long-related ideas/positions
- Highest-level scope; overrides all specific directives

## Expires Schema

Each directive declares its expiry trigger and necessary parameters:

### time_elapsed — absolute date

```yaml
expires:
  trigger: time_elapsed
  until_date: "2026-05-22"  # ISO date string (YYYY-MM-DD)
```

Fires when `current_date >= until_date`.

### earnings_passed — after earnings release

```yaml
expires:
  trigger: earnings_passed
  symbol: AAPL              # Required: symbol to track
```

Fires when next earnings for the symbol has passed (determined from earnings_calendar in current_state).

### position_closed — when position no longer exists

```yaml
expires:
  trigger: position_closed
  position_identifier: "AAPL  260619P00170000"  # Required
```

Fires when the position is no longer in the portfolio.

### price_above — stock price crosses level

```yaml
expires:
  trigger: price_above
  symbol: NVDA         # Required
  level: 185.00        # Required: numeric price
```

Fires when `last_close >= level`.

### price_below — stock price drops below level

```yaml
expires:
  trigger: price_below
  symbol: NVDA         # Required
  level: 170.00        # Required: numeric price
```

Fires when `last_close <= level`.

### screener_drops — idea no longer surfaces from screener

```yaml
expires:
  trigger: screener_drops
  symbol: AAPL                       # Required
  screener_name: vcp-screener        # Required
```

Fires when the symbol no longer appears in the screener's output list.

### manual_override — never auto-fires

```yaml
expires:
  trigger: manual_override
```

Never fires automatically. Directive must be explicitly overridden by the user via CLI or API.

### open_ended — perpetual with renewal prompt

```yaml
expires:
  trigger: open_ended
```

Never fires. Directive remains ACTIVE until explicitly overridden. After 30 days, the briefing prompts the user to confirm they want to continue.

## OVERRIDE-Specific Fields

OVERRIDE directives have additional fields:

```yaml
type: OVERRIDE
target:
  kind: option_position
  identifier: "AMD  260516P00180000"
parameter: take_profit_threshold     # Required: parameter name
new_value: 0.80                      # Required: new value (any type)
old_value: 0.50                      # Recommended: previous value for audit trail
reason: "High conviction; ride further."
expires:
  trigger: position_closed
  position_identifier: "AMD  260516P00180000"
```

The downstream skill (wheel-roll-advisor, etc.) receives `override_params` in its input and uses `new_value` for this position only.

## status_history — Auto-maintained transition log

```yaml
status_history:
  - at: 2026-05-07T09:30:00-04:00
    status: ACTIVE
    reason: created
  - at: 2026-05-14T08:15:00-04:00
    status: EXPIRED
    reason: earnings passed on 2026-05-13
```

Each transition appends an entry with timestamp, new status, and explanation. Never edit this field manually.

## Validation Rules

1. **type must be one of:** DEFER, MANUAL, OVERRIDE, WATCH_ONLY, SUPPRESS
2. **target.kind must be one of:** option_position, position_scope, new_idea, symbol
3. **trigger must be one of:** time_elapsed, earnings_passed, position_closed, price_above, price_below, screener_drops, manual_override, open_ended
4. **OVERRIDE directives must have:**
   - `parameter` (string)
   - `new_value` (any type)
   - Optionally: `old_value` for audit trail
5. **status must be one of:** ACTIVE, EXPIRED, OVERRIDDEN, RESOLVED
6. **created_at must be ISO 8601 with timezone**
7. **Target scope rules:**
   - option_position requires `identifier`
   - position_scope requires `symbol`
   - new_idea requires `symbol` and `source_screener`
   - symbol requires `symbol`
8. **Expires trigger rules:**
   - time_elapsed requires `until_date`
   - earnings_passed requires `symbol`
   - position_closed requires `position_identifier`
   - price_above requires `symbol` and `level`
   - price_below requires `symbol` and `level`
   - screener_drops requires `symbol` and `screener_name`
   - manual_override and open_ended require no additional fields
