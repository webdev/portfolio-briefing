# Briefing Directives — Persistent User Decisions

**Status:** Draft v0.1
**Date:** 2026-05-07

## The problem

Today's briefing tells you: "Roll AAPL 170P, take profit on MSFT 180C, watch NVDA earnings."

You read it. You decide:
- "Going to wait on the AAPL roll until earnings clear next week."
- "I'll close MSFT manually, don't keep recommending it."
- "Watching NVDA but no action until it closes above $185."

Tomorrow's briefing has none of that context. It re-surfaces the AAPL roll, re-recommends closing MSFT, and re-flags NVDA — exactly the things you've already decided about. You re-read all three, re-decide all three, and either grumble through it or override the recommendations again, training yourself to discount the briefing.

This is the gap: **the briefing has memory of positions but no memory of the user's decisions about them.**

`trader-memory-core` tracks position state ("AAPL is in ACTIVE status, thesis intact"). It does not track *user directives* ("wait on AAPL until earnings"). Those are different: a thesis is a belief about the position; a directive is an instruction about how to handle it.

This document specifies the directive system that closes the gap.

---

## Concept

A **directive** is a structured record of a user decision about a specific recommendation, with explicit duration and resolution conditions. Directives are read at the start of each briefing run, applied during recommendation generation, and either renewed or expired based on their resolution conditions.

Directives are user-authored but Claude-mediated: during a conversation about the briefing, Claude offers to capture decisions as directives, the user confirms, and they're persisted. Claude does not silently invent directives from inference.

---

## Lifecycle

Each directive moves through a small state machine:

```
        creates                expires
[user] ─────────► ACTIVE ─────────────► EXPIRED
                    │
                    ├── overridden by user ──► OVERRIDDEN
                    │
                    └── target resolved ─────► RESOLVED
                       (position closed, idea
                        no longer surfaces)
```

- **ACTIVE** — directive is currently applied during briefing runs
- **EXPIRED** — auto-expiry condition met (time, event, threshold); the suppressed recommendation re-surfaces with a "previously deferred" note
- **OVERRIDDEN** — user explicitly changed their mind; directive is closed but flagged in the next briefing as "you previously deferred this; you've now reversed that"
- **RESOLVED** — the underlying position or idea no longer exists (position closed, screener stopped surfacing the idea, etc.); directive is closed silently

---

## Directive types

There are five directive types, covering most decisions a user makes during a briefing review:

### 1. `DEFER` — wait, don't recommend action

Most common. "Don't tell me to roll AAPL until earnings clear."

```yaml
directive_id: dir_20260507_aapl_defer_a3f1
type: DEFER
target:
  kind: option_position
  identifier: "AAPL  260619P00170000"
created_at: 2026-05-07T09:30:00-04:00
created_via: briefing review session
reason: "Want to wait until earnings on 2026-05-21 are out before deciding."
expires:
  trigger: earnings_passed
  symbol: AAPL
  earliest_resurface: 2026-05-22
status: ACTIVE
```

When the briefing generates a recommendation for `AAPL  260619P00170000`, it looks up active directives, finds this one, and:
- Suppresses the actionable recommendation in the action list
- Keeps the position in the watch panel with a `🔵 DEFERRED` flag and the reason
- Surfaces in a "Deferred recommendations" section: "AAPL roll deferred until 2026-05-22 (after earnings)"

### 2. `MANUAL` — I'll handle this myself

"Don't recommend on MSFT covered calls; I'm managing those by hand."

```yaml
directive_id: dir_20260507_msft_manual_b8e2
type: MANUAL
target:
  kind: position_scope
  symbol: MSFT
  position_type: short_call  # optional narrowing
created_at: 2026-05-07T09:30:00-04:00
reason: "Managing MSFT calls manually for now."
expires:
  trigger: open_ended  # no auto-expiry; user must override
status: ACTIVE
```

The briefing surfaces these positions in the watch panel for context but does not generate actionable recommendations. After 30 days, the briefing prompts: "MSFT positions on manual since 2026-05-07; want to resume automatic recommendations?"

### 3. `OVERRIDE` — change a threshold for this position only

"Take profit on AMD at 80% capture, not 50%."

```yaml
directive_id: dir_20260507_amd_override_c4d9
type: OVERRIDE
target:
  kind: option_position
  identifier: "AMD  260516P00180000"
parameter: take_profit_threshold
new_value: 0.80
old_value: 0.50  # what the matrix would have said
created_at: 2026-05-07T09:30:00-04:00
reason: "Want to ride this one further; high conviction."
expires:
  trigger: position_closed
status: ACTIVE
```

The wheel-roll-advisor receives the override as part of its input context and uses 0.80 instead of 0.50 for that specific position. The matrix cell ID becomes `OTM_LONGDTE_HICAPTURE_OVERRIDE_0.80` for auditability.

### 4. `WATCH_ONLY` — surface but don't recommend action

"Keep flagging NVDA breakout setup but don't generate an entry order until it closes above $185."

```yaml
directive_id: dir_20260507_nvda_watch_d6f3
type: WATCH_ONLY
target:
  kind: new_idea
  symbol: NVDA
  source_screener: vcp-screener
created_at: 2026-05-07T09:30:00-04:00
reason: "Want to see daily close above $185 before entering."
expires:
  trigger: price_above
  symbol: NVDA
  level: 185.00
status: ACTIVE
```

The briefing surfaces NVDA in a "Watching" subsection with the trigger condition. Each run checks if the trigger fired; if yes, the directive expires and the recommendation re-surfaces with "Trigger met: NVDA closed above $185 on YYYY-MM-DD."

### 5. `SUPPRESS` — I never want to hear about this

"Don't recommend any further long positions in BABA. Ever."

```yaml
directive_id: dir_20260507_baba_suppress_e7g4
type: SUPPRESS
target:
  kind: symbol
  symbol: BABA
  scope: long_only  # or all
created_at: 2026-05-07T09:30:00-04:00
reason: "Tail-risk profile, won't trade going forward."
expires:
  trigger: open_ended
status: ACTIVE
```

This is essentially a per-user extension to `tail_risk_names.md`. It applies a hard filter; the symbol simply doesn't appear in screener output passed through to recommendations.

---

## Expiry triggers

The trigger system needs to support both simple (time-based) and conditional (event-based) expiry. Each directive declares its trigger:

| Trigger | Inputs | Evaluation |
|---|---|---|
| `time_elapsed` | `until_date` | true when current_date ≥ until_date |
| `earnings_passed` | `symbol` (looked up via earnings-calendar) | true when next_earnings has passed |
| `position_closed` | `position_identifier` | true when E*TRADE no longer reports the position |
| `price_above` | `symbol`, `level` | true when last_close ≥ level |
| `price_below` | `symbol`, `level` | true when last_close ≤ level |
| `screener_drops` | `symbol`, `screener_name` | true when screener no longer surfaces |
| `manual_override` | (none) | only fires when user says so |
| `open_ended` | (none) | never fires; auto-prompts after 30 days |

Triggers are evaluated by the briefing in pre-flight (Step 1.5 — see below). If a trigger fires, the directive transitions to EXPIRED. The recommendation it was suppressing gets re-surfaced in the next briefing with a "Previously deferred — condition met on YYYY-MM-DD" annotation.

---

## How the briefing applies directives

Insert two new steps into `02-daily-portfolio-briefing-skill.md`:

### Step 1.5: Load and evaluate directives

After loading yesterday's briefing in Step 1, before snapshotting inputs:

1. Read `state/directives/index.yaml`. For each ACTIVE directive:
   - Evaluate its trigger against current state (positions, prices, calendar, screener output)
   - If trigger fired, transition to EXPIRED and write the timestamp
2. Carry the still-ACTIVE directives forward as input to recommendation generation.

Output: `state/briefing_snapshots/YYYY-MM-DD/directives_active.json` (active list at briefing time)
Output: `state/briefing_snapshots/YYYY-MM-DD/directives_expired_today.json` (expired during this run; surface in report)

### Step 4-6 (recommendation generation): consult directives

Each recommendation generation step (equity review, options book, new ideas) takes `directives_active` as input alongside its other inputs. For each candidate recommendation:

1. Find any directive whose `target` matches the candidate
2. Apply the directive:
   - `DEFER` / `MANUAL` / `WATCH_ONLY`: suppress the actionable recommendation; flag for the appropriate report section
   - `OVERRIDE`: pass the override parameter into the underlying skill (e.g., wheel-roll-advisor receives `take_profit_threshold=0.80`)
   - `SUPPRESS`: drop the candidate entirely
3. The matrix cell ID for any decision affected by a directive includes a `_DIRECTIVE_<id>` suffix for auditability

### Step 8 (rendering): new "Directives" panel

Add a panel to the briefing template:

```markdown
## Directives                                           [render/directives_panel.py]

### Active (N total)
- 🔵 AAPL 260619P00170000 — DEFER until 2026-05-22 (earnings)
- ⚙️ MSFT short calls — MANUAL (since 2026-05-07; resume prompt in 23 days)
- 🎯 AMD 260516P00180000 — OVERRIDE take_profit_threshold = 80%
- 👁️ NVDA — WATCH_ONLY, trigger: close above $185
- 🚫 BABA — SUPPRESS long ideas

### Expired today (M total)
- 🔄 META roll deferred 2026-04-23 — earnings cleared, recommendation re-surfaces below
- 🔄 GOOG WATCH_ONLY (close > $175) — trigger met 2026-05-06; idea re-surfaces
```

The "Expired today" section is the bridge between yesterday's directive and today's recommendation: the user sees explicitly that "you previously deferred this; the condition you specified has now been met; here is the recommendation again."

---

## Directive creation UX

Two paths:

### A. Conversational capture (preferred)

During a briefing review session, user says something like:

> "Let's wait on the AAPL roll until earnings."

Claude responds:

> Want me to capture this as a directive? I'll defer the AAPL 260619P00170000 roll recommendation until 2026-05-22 (the day after earnings). Tomorrow's briefing will skip the recommendation; it re-surfaces automatically once earnings are out.
>
> [Confirm] [Modify] [Skip]

User confirms → Claude writes the directive to `state/directives/`.

This is the natural-language interface. Claude infers the type, target, and trigger from context, presents a structured directive for review, and only persists on explicit confirmation. **Never silently infer; always confirm.**

### B. Direct edit

For power users: edit YAML in `state/directives/` directly. The schema is documented in `references/directive_schema.md`. CI-style validation runs every briefing pre-flight; malformed directives are surfaced as warnings and skipped.

---

## Storage layout

```
state/directives/
  index.yaml                    # fast lookup: id → file, status, target
  active/
    dir_20260507_aapl_defer_a3f1.yaml
    dir_20260507_msft_manual_b8e2.yaml
    ...
  expired/
    dir_20260423_meta_defer_x9z2.yaml   # archived after expiry
  overridden/
    dir_20260415_tsla_defer_p4q5.yaml
  resolved/
    dir_20260301_v_defer_m1n8.yaml      # position closed
```

Each subdirectory mirrors the lifecycle state. `index.yaml` is the source of truth for "what's active right now." File rotation happens automatically when state transitions occur.

---

## Conflict handling

What happens if you give two directives that conflict?

> "Defer AAPL roll until earnings."
> [next day] "Actually, close the AAPL position now."

The second instruction creates a new directive (or the user simply triggers a CLOSE through different means). The previous DEFER directive is auto-transitioned to OVERRIDDEN with the reason "user closed position via X." If the position no longer exists, any directives targeting it auto-transition to RESOLVED.

The general rule: **the most recent user instruction wins.** The directive log preserves the history but does not block new actions.

---

## Why this is its own concept (not a feature of trader-memory-core)

trader-memory-core tracks **what we believe about a position** — the thesis, entry conditions, invalidation triggers, P&L. It's about *the position*.

Directives track **what the user has decided to do (or not do)** — defer, manual, override, watch, suppress. They're about *the user's choices*.

These are orthogonal:
- A position can have an active thesis AND an active directive (e.g., "thesis intact, but defer until earnings")
- A position can have a thesis but no directive (default: briefing applies normal recommendations)
- A new idea can have a directive without ever becoming a thesis (WATCH_ONLY on a candidate before entry)
- Both can transition independently (thesis BROKEN doesn't expire a DEFER; directive EXPIRED doesn't change thesis status)

Conflating them would muddy both. They're two memories with different purposes.

---

## What this changes elsewhere

- `02-daily-portfolio-briefing-skill.md` — adds Step 1.5 (load + evaluate directives) and a new "Directives" panel
- `03-wheel-roll-advisor-skill.md` — accepts an `overrides` field in its input that wires per-position parameter overrides
- `00-build-plan.md` — adds "directive system" as a fifth piece of work
- New reference: `daily-portfolio-briefing/references/directive_schema.md`
- New scripts: `daily-portfolio-briefing/scripts/directives/load.py`, `directives/evaluate_triggers.py`, `directives/apply.py`

Build effort: roughly 1-2 days after the briefing skeleton is up. The trigger evaluation logic is the only nontrivial piece (each trigger type needs its evaluator); everything else is YAML I/O and panel rendering.

---

## Open questions

1. **Directive creation: only via Claude conversation, or should the user be able to add them directly via a CLI?** Recommend: Claude is primary; CLI is an escape hatch for power users.
2. **Should directives be versioned in git?** State files generally aren't, but directives express user intent and may be worth preserving across machines / disasters. Recommend: yes, in a private git repo separate from the public skills repo.
3. **What happens when a position has multiple potential directives matching?** (e.g., a `MANUAL` for "all MSFT positions" plus an `OVERRIDE` for one specific contract.) Recommend: most-specific wins (override beats scope), and we log the resolution in the briefing for audit.
4. **Should the briefing prompt for new directives at the end of each run?** ("3 recommendations were re-surfaced today that were active yesterday — want to defer any?") This is a nice forcing function but might be noisy. Recommend: opt-in via `briefing_config.yaml`, off by default.
