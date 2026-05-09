---
name: briefing-directives
description: Persistent memory of user decisions during portfolio briefing review sessions. Captures DEFER/MANUAL/OVERRIDE/WATCH_ONLY/SUPPRESS directives with auto-expiry triggers (time, earnings, position-closed, price level, screener-drops, open-ended). Tomorrow's briefing reads active directives and applies them to recommendation generation. Use when user says "wait on this", "I'll handle this manually", "don't recommend this until X", or when the daily briefing's pre-flight loads directive state.
---

# Briefing Directives

## Overview

Persistent state layer that captures and remembers user decisions during briefing review sessions. When you decide to defer a recommendation, handle a position manually, override a threshold, or suppress a screener idea, the directive system records that decision with its expiry trigger and applies it to tomorrow's briefing — eliminating re-recommendations of choices you've already made.

Phase 1 supports five directive types: DEFER (wait, don't recommend), MANUAL (I'll handle this), OVERRIDE (change a threshold), WATCH_ONLY (surface but don't recommend), and SUPPRESS (never recommend).

## When to Use

- When you want to defer a recommendation until a specific event (earnings, price level, time-based)
- When managing certain positions manually and want the briefing to skip them
- When you want to override a decision threshold for a specific position
- When you want to watch a screener idea but not enter until a trigger fires
- When you never want to hear about a specific symbol or idea again

## Prerequisites

- Python 3.10+
- `pyyaml` (already in project dependencies)
- Earnings calendar data (for earnings_passed trigger evaluations)
- Market prices (for price_above/price_below trigger evaluations)

## Workflow

### 1. Capture — Create a directive from a decision

Interactive CLI capture (recommended):

```bash
python3 skills/briefing-directives/scripts/cli.py capture
```

This walks through a structured set of questions:
1. What kind of directive? (DEFER / MANUAL / OVERRIDE / WATCH_ONLY / SUPPRESS)
2. What's the target? (ticker, optionSymbol, position identifier, or screener name)
3. Why? (free-text reason)
4. When does it expire? (trigger type + parameters)
5. Confirm the structured directive

Or capture programmatically from Python:

```python
from briefing_directives.scripts.directive_store import create

directive_dict = {
    "type": "DEFER",
    "target": {
        "kind": "option_position",
        "identifier": "AAPL  260619P00170000"
    },
    "reason": "Wait until earnings clear on 2026-05-21.",
    "expires": {
        "trigger": "earnings_passed",
        "symbol": "AAPL"
    }
}

directive = create(state_dir="state/directives/", directive=directive_dict)
print(f"Created: {directive['directive_id']}")
```

### 2. List — See all active directives

```bash
python3 skills/briefing-directives/scripts/cli.py list
python3 skills/briefing-directives/scripts/cli.py list --status active
python3 skills/briefing-directives/scripts/cli.py list --status expired
```

### 3. Evaluate — Check triggers and expire directives

Run this in your briefing pre-flight. It evaluates all triggers against current state and transitions expired directives:

```python
from briefing_directives.scripts.trigger_evaluator import evaluate_all_active

current_state = {
    "earnings_calendar": {"AAPL": date(2026, 5, 21), "MSFT": date(2026, 5, 15)},
    "positions": [
        {"identifier": "AAPL  260619P00170000", "status": "open"},
        # ... more positions
    ],
    "last_close": {"NVDA": 185.50, "TSLA": 240.25},
    "screener_outputs": {
        "vcp-screener": ["AAPL", "MSFT"],
        "earnings-trade-analyzer": ["NVDA"]
    }
}

expired_today = evaluate_all_active(state_dir="state/directives/", current_state=current_state)
print(f"Expired today: {len(expired_today)} directives")
for directive in expired_today:
    print(f"  - {directive['directive_id']}: {directive['reason']}")
```

### 4. Apply — Filter recommendations using active directives

Before generating recommendations in your briefing:

```python
from briefing_directives.scripts.apply_to_recommendations import apply_directives

candidates = [
    {"ticker": "AAPL", "kind": "option_position", "identifier": "AAPL  260619P00170000", "action": "ROLL"},
    {"ticker": "NVDA", "kind": "new_idea", "source_screener": "vcp-screener", "action": "ENTRY"},
    {"ticker": "MSFT", "kind": "position_scope", "action": "CLOSE"}
]

active_directives = ... # from step 3 or list() call

modified = apply_directives(candidates, active_directives)
# modified[0] has action="DEFERRED" with explanation
# modified[1] has action="WATCH_ONLY" with trigger condition
# modified[2] is dropped entirely (SUPPRESS)
```

### 5. Show — Inspect a specific directive

```bash
python3 skills/briefing-directives/scripts/cli.py show dir_20260507_aapl_defer_a3f1
```

### 6. Override — Change your mind

If you've already deferred something but now want to change your mind:

```bash
python3 skills/briefing-directives/scripts/cli.py override dir_20260507_aapl_defer_a3f1 \
  --reason "Changed my mind; let's handle the roll anyway"
```

This transitions the directive to OVERRIDDEN, and the next briefing will note the reversal.

### 7. Renew — Extend an expiry

For open-ended directives (MANUAL, long-term SUPPRESS), extend the expiry:

```bash
python3 skills/briefing-directives/scripts/cli.py renew dir_20260507_msft_manual_b8e2
```

## Output Format

### Directive YAML (state/directives/active/)

Each directive is a YAML file with:
- Identity: directive_id, type, created_at
- Target: kind (option_position, position_scope, new_idea, symbol, etc.), identifier/symbol/screener
- Lifecycle: status, status_history with timestamps
- Trigger: type (earnings_passed, time_elapsed, price_above, etc.) and parameters
- User context: reason, created_via (briefing review, CLI, programmatic)

### Index (state/directives/index.yaml)

Lightweight index for fast lookups. Maps directive_id → file, status, target summary.

### Lifecycle directories

- `active/` — currently applied directives
- `expired/` — auto-expired, archived for audit trail
- `overridden/` — user changed mind
- `resolved/` — underlying position closed or screener dropped

## Directive Types

### DEFER

Suppress a recommendation until a specific event. Most common use case.

```yaml
type: DEFER
target:
  kind: option_position
  identifier: "AAPL  260619P00170000"
reason: "Wait until earnings on 2026-05-21 are out."
expires:
  trigger: earnings_passed
  symbol: AAPL
```

Effect: The recommendation is suppressed from action list; position appears in WATCH with DEFERRED flag; "Deferred until after earnings" appears in Directives panel.

### MANUAL

Handle a position or position_scope yourself; skip briefing recommendations.

```yaml
type: MANUAL
target:
  kind: position_scope
  symbol: MSFT
  position_type: short_call
reason: "Managing MSFT calls manually for now."
expires:
  trigger: open_ended
```

Effect: All MSFT short calls are suppressed from actionable recommendations; they appear in WATCH; after 30 days, briefing prompts for renewal.

### OVERRIDE

Change a decision threshold for a specific position.

```yaml
type: OVERRIDE
target:
  kind: option_position
  identifier: "AMD  260516P00180000"
parameter: take_profit_threshold
new_value: 0.80
old_value: 0.50
reason: "High conviction; ride it further."
expires:
  trigger: position_closed
```

Effect: The recommendation-generation skill receives the override and uses 0.80 instead of 0.50 for this position.

### WATCH_ONLY

Surface in WATCH panel but don't generate an actionable entry order until a trigger fires.

```yaml
type: WATCH_ONLY
target:
  kind: new_idea
  symbol: NVDA
  source_screener: vcp-screener
reason: "Want to see daily close above $185 before entering."
expires:
  trigger: price_above
  symbol: NVDA
  level: 185.00
```

Effect: Idea surfaces in Watching section; each briefing checks if close ≥ $185; when it does, directive expires and idea re-surfaces as actionable.

### SUPPRESS

Never recommend this symbol or screener idea.

```yaml
type: SUPPRESS
target:
  kind: symbol
  symbol: BABA
  scope: long_only
reason: "Tail-risk profile; won't trade."
expires:
  trigger: open_ended
```

Effect: Symbol is filtered out of all screener output and new-idea recommendations.

## Expiry Triggers

| Trigger | Parameters | Evaluation |
|---|---|---|
| `time_elapsed` | `until_date` | Fires when `today >= until_date` |
| `earnings_passed` | `symbol` | Fires when next_earnings for symbol has passed |
| `position_closed` | `position_identifier` | Fires when position no longer exists in E*TRADE |
| `price_above` | `symbol`, `level` | Fires when last_close ≥ level |
| `price_below` | `symbol`, `level` | Fires when last_close ≤ level |
| `screener_drops` | `symbol`, `screener_name` | Fires when symbol no longer appears in screener output |
| `manual_override` | (none) | Never fires automatically; user must override |
| `open_ended` | (none) | Never fires; auto-prompts after 30 days for renewal |

## Key Principles

- **Conversational capture**: Directives are offered and confirmed by user during briefing review; Claude does not silently invent them
- **Auto-expiry**: Directives transition to EXPIRED automatically when their trigger fires; expired directives re-surface their suppressed recommendation with a "previously deferred" note
- **Override-first**: When multiple directives match a target, most-specific wins (directive on exact position beats directive on symbol)
- **Audit trail**: All status transitions are timestamped and logged; file moves between state subdirs; index.yaml maintains source of truth
- **Atomic writes**: All file operations use tempfile + os.replace for data safety

## Resources

- `references/directive_schema.md` — Detailed YAML schema with all fields and validation rules
- `references/trigger_reference.md` — Exhaustive trigger type documentation with examples
- `references/conversational_capture.md` — Natural-language capture flow and Claude's reasoning
- `assets/directive_template.yaml` — Blank template for manual directive creation
