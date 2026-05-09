# Portfolio Management Tooling — Build Plan

**Status:** v0.1
**Date:** 2026-05-07
**Owner:** George (with Claude as design partner)

This is the entry-point document. Read this first; the numbered docs are the detailed specs.

---

## What we're building

A daily portfolio briefing system that:

1. Pulls live holdings from **E*TRADE** (positions, balances, options, open orders)
2. Reads **market context** (economic events, earnings, news, breadth, regime)
3. Reviews every existing position (equity HOLD/ADD/TRIM/EXIT, options ROLL/CLOSE/HOLD)
4. Generates **new ideas** from screeners filtered to your strategies, sized against current portfolio
5. Aggregates into **one markdown report** with a prioritized action list
6. **Anchors recommendations to yesterday's report** so we don't get whiplash

It runs daily — manually or scheduled — and outputs a single canonical report you can review in 5 minutes and decide what to act on.

---

## Why we are not building it as an algorithmic bot

George's earlier wheel bot (`wheelhouz`) worked but was brittle. The lesson:

- **Algorithmic code encodes one specific way to make a decision.** wheelhouz's `review_position()` is 830 lines of cascading if/elif logic. Hard to tune, hard to debug "why did it recommend that," and unforgiving when the situation has nuance the if-statement didn't anticipate.
- **Skills + Claude in the loop encode the framework, not the answer.** The framework is deterministic (decision matrices, scoring rubrics, snapshot diffs). Claude's judgment fills in the inputs the framework needs (outlook tags, regime calls). The combination is consistent *and* situationally aware.
- **MCP servers replace browser automation.** Direct API calls to brokerage data are an order of magnitude faster, more reliable, and don't break when E*TRADE redesigns its UI.

**But we don't throw wheelhouz away.** Its production-tested numbers (loss-stop multipliers, IV-adaptive delta targets, take-profit bucket thresholds, tail-risk override list) migrate verbatim into the new skills' reference documents. What changes is the architecture, not the trading discipline. See `04-from-wheelhouz-keep-drop.md` for the explicit mapping.

---

## The five pieces of work

### 1. E*TRADE MCP server (~3-4 days)

Fork [`ohenak/etrade-mcp`](https://github.com/ohenak/etrade-mcp) — it already handles OAuth, encrypted tokens, quotes, and options chains. Add the missing pieces: account list, balances, positions, expirations, orders.

**See:** `01-etrade-mcp-spec.md`

**Deliverable:** A working MCP server registered in Claude Desktop that can return all four data types (positions, quotes, chains, orders) on command.

### 2. `daily-portfolio-briefing` orchestrator skill (~2-3 days after MCP)

The orchestrator. Calls all the existing skills + the new wheel-roll-advisor in the right order, snapshots inputs for reproducibility, applies decision matrices, runs a day-over-day consistency check, renders one markdown report.

**See:** `02-daily-portfolio-briefing-skill.md`

**Deliverable:** Running `daily-portfolio-briefing` (or just asking "run the briefing") produces `reports/daily/briefing_YYYY-MM-DD.md` with all five sections populated.

### 3. `wheel-roll-advisor` skill (~2 days)

The new skill. Decision matrix for SHORT_PUT and SHORT_CALL positions producing one of seven decision tags with a target contract for any roll. Test fixture per matrix cell.

**See:** `03-wheel-roll-advisor-skill.md`

**Deliverable:** A standalone skill that takes a position + chain + context JSON and emits a structured decision JSON.

### 4. Glue, references, decision matrices (~2 days)

The deterministic frameworks live in markdown files in each skill's `references/` directory. These need to be written carefully because they are the source of consistency between runs.

Files needed:
- `daily-portfolio-briefing/references/regime_framework.md` — calendar/news/breadth → regime label
- `daily-portfolio-briefing/references/equity_decision_matrix.md` — (thesis, technical, triggers, sizing) → recommendation tag
- `daily-portfolio-briefing/references/directive_schema.md` — full YAML schema for directives
- `wheel-roll-advisor/references/decision_matrix.md` — full options matrix
- `wheel-roll-advisor/references/wheel_parameters.md` — user-tunable deltas, IV thresholds, profit buckets
- `wheel-roll-advisor/references/tail_risk_names.md` — Chinese ADRs, binary biotechs, crypto-proxies, high-borrow memes

### 5. Briefing directives system (~1-2 days, after briefing skeleton)

User-issued decisions during a briefing review must persist to the next run. This is the "fresh briefing has no context of my previous directions" problem.

**See:** `05-briefing-directives.md`

**Deliverable:** Five directive types (DEFER, MANUAL, OVERRIDE, WATCH_ONLY, SUPPRESS) with auto-expiry triggers (time, earnings, position-closed, price level, screener-drops, open-ended). Storage in `state/directives/`. New "Directives" panel in the briefing. Conversational capture flow during briefing review sessions ("Want me to capture this as a directive?").

---

## Build order

```
Week 1
  Day 1-3: E*TRADE MCP — fork + add account/positions/orders tools
  Day 4-5: Decision matrices (regime, equity, wheel) + briefing references

Week 2
  Day 1-2: wheel-roll-advisor skill (logic + tests)
  Day 3-4: daily-portfolio-briefing orchestrator (without directives)
  Day 5: End-to-end smoke run against E*TRADE sandbox account

Week 3
  Day 1-2: Briefing directives system (storage, trigger evaluator, conversational capture, panel)
  Day 3: Cut over to production E*TRADE account
  Day 4-5: Daily briefing runs; collect feedback on missing pieces, format, recommendations that look off, directives that feel awkward
  → tune decision matrices and directive UX, not orchestrator code
```

---

## What this design solves vs. what George's current briefing does

| Problem with current briefing | How this solves it |
|---|---|
| Inconsistencies in recommendations between runs | Decision matrices + day-over-day consistency check (Step 7 in `02-`). Same inputs always produce same output. Flips require explicit triggers. |
| Fresh briefing has no context of my previous directions | Briefing directives system (`05-`). User decisions ("defer this", "manual on those", "watch only") persist as structured records and apply to subsequent runs until they expire. |
| Has to re-explain position context each session | trader-memory-core thesis store. Every position has a persistent thesis Claude reads at the start of each run. |
| Each run has slightly different format | Templates in `assets/`, deterministic aggregation step, `data-quality-checker` gate before publishing. |
| Manual data entry / browser scraping | E*TRADE MCP. Direct API. Auth once a day, queries are instant. |
| Wheel decisions are vibes-based | wheel-roll-advisor decision matrix. Auditable matrix-cell ID per recommendation. |
| Hard to debug "why did it say that" | Snapshot directory: `state/briefing_snapshots/YYYY-MM-DD/` has every input. Can re-run and diff. |

---

## The consistency design (the real fix for the bug George cares about)

There are four layers of consistency, in increasing order of strength:

**Layer 1: Persistent thesis state.** trader-memory-core stores a thesis per position. The next briefing reads that thesis and anchors recommendations to it.

**Layer 2: Persistent user directives.** The directives system (`05-`) stores user decisions about specific recommendations. Tomorrow's briefing knows what you said yesterday — defer, manual, override, watch-only, suppress. It applies those decisions automatically and re-surfaces them when their conditions expire.

**Layer 3: Deterministic frameworks.** Recommendations are produced by table lookups, not by "Claude, decide." Tables live in `references/*.md` and are the same every run.

**Layer 4: Day-over-day diff with trigger requirement.** If a recommendation flips, the briefing must point to a specific trigger (price level, news, earnings, technical break). If no trigger exists, the briefing surfaces "potential self-inconsistency: yesterday HOLD, today TRIM, no trigger event" and does not publish the new recommendation.

Together, these four layers make the briefing reproducible. If you run today's briefing on yesterday's snapshot, you should get yesterday's report — *and* tomorrow's briefing carries forward both your beliefs about positions and your decisions about how to handle them.

---

## What is intentionally *not* in v1

- No order placement. The briefing is read-only. You execute trades manually after reviewing.
- No portfolio optimization across positions (e.g., "given total buying power, which roll order maximizes credit"). Each position is evaluated independently. Cross-position optimization is hard, error-prone, and the value vs. complexity is poor for a portfolio that's read by a human in 5 minutes daily.
- No market-prediction layer. The regime label is a classifier of *current state*, not a forecast. We don't try to predict tomorrow's market.
- No backtesting integration. The backtest-expert skill exists; if a screener idea passes the briefing's filter, you can backtest separately.
- No multi-asset class beyond US equities and US options. Crypto, forex, bonds: deferred.
- No automatic re-auth. E*TRADE OAuth needs your daily click-and-paste. We don't try to script it (against TOS, fragile, and not worth the engineering for a 30-second daily ceremony).

---

## Success criteria

You'll know this is working when:

1. You run the briefing for 10 consecutive trading days and *every* day produces a clean report with no quality-gate failures.
2. The day-over-day consistency check fires zero times when the underlying portfolio is unchanged. (When it does fire, it correctly identifies a real trigger event.)
3. You make at least 3 portfolio decisions per week directly from the briefing's action list, without re-doing the analysis manually.
4. Your previous wheel bot is fully retired.
5. The briefing's options recommendations agree with what you would have decided manually ≥80% of the time over the first month, with disagreements traceable to specific decision-matrix rows you can tune.

---

## Open questions to resolve before starting

These are repeated from the individual specs, consolidated:

1. **Sandbox vs production for MCP build:** start in sandbox, cut over to prod when stable? *(Strong recommendation: yes.)*
2. **Multi-account scope:** which E*TRADE accounts should the briefing cover? Default = all; allow per-account analyses?
3. **Re-auth UX:** OK with manual daily click? Or want scripted OAuth (Selenium)?
4. **Strategy enabling:** what strategies should v1 support — wheel + dividend growth + swing equity, or narrower?
5. **Where do alerts go** if the consistency check flags a self-inconsistency? Just in the report? Or also a separate Slack/email/notification?

The first four are answerable now; the fifth can wait until we see how often it fires.

---

## Spec docs in this folder

**Skill specs:**
- `00-build-plan.md` — this document
- `01-etrade-mcp-spec.md` — E*TRADE MCP server design (read-only, fork of ohenak)
- `02-daily-portfolio-briefing-skill.md` — orchestrator skill draft (modular panels, day-over-day consistency, directive integration)
- `03-wheel-roll-advisor-skill.md` — wheel-roll decision skill draft (decision matrix + wheelhouz thresholds)
- `04-from-wheelhouz-keep-drop.md` — explicit mapping: what to migrate from wheelhouz, what to deliberately leave behind
- `05-briefing-directives.md` — persistent user-decision memory (DEFER/MANUAL/OVERRIDE/WATCH_ONLY/SUPPRESS) with auto-expiry triggers
- `11-recommendation-list-skill.md` — Google Sheets adapter that pulls third-party stock recommendations and feeds them into the briefing as triggers (not new matrix dimensions)

**Reference data (loaded by skills at runtime):**
- `06-wheel-parameters.md` — every threshold, delta target, DTE window, sizing rule from wheelhouz, with provenance citations and a YAML companion for programmatic loading. Includes regime VIX/SPY thresholds, stress-test scenarios, and v0 liquidity defaults.
- `07-tail-risk-names.md` — Chinese ADRs, binary biotechs, crypto-proxies, high-borrow memes; loaded by wheel-roll-advisor as a hard guardrail
- `08-wheel-decision-matrix.md` — full cell-by-cell decision table for SHORT PUT and SHORT CALL across NORMAL/CAUTION/RISK_OFF regimes; ~70 cells with stable IDs; references parameter names from `06-` instead of hardcoded numbers
- `09-regime-framework.md` — deterministic classifier mapping market inputs (VIX, SPY drop, breadth, distribution days, economic events, earnings, news) to regime label (RISK_ON / NORMAL / CAUTION / RISK_OFF); 11 rules with stickiness logic and confidence scoring
- `10-equity-decision-matrix.md` — cell-by-cell equity-position decision table; 36 matrix cells + 14 guardrails; outputs HOLD/ADD/TRIM/EXIT/REVIEW/DEFERRED/MANUAL with LTCG and tax-loss-harvest awareness
