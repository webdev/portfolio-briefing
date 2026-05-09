---
name: daily-portfolio-briefing
description: Produce a single daily markdown briefing covering market regime, every existing portfolio position (equity and options), wheel/options roll decisions, and screener-sourced new ideas with position sizing. Pulls live holdings from E*TRADE via etrade-mcp, anchors recommendations to persisted theses in trader-memory-core, and snapshots inputs for day-over-day consistency. Use when the user requests a daily portfolio review, morning briefing, "what should I do today", portfolio health check, or end-of-day report. Anchors against yesterday's recommendations to prevent inconsistencies between runs.
---

# Daily Portfolio Briefing

## Overview

Produce one markdown report per trading day. Section ordering follows a "health-first" pattern — portfolio state first, recommendations follow from the dashboard rather than the reverse.

The skill is an **orchestrator**. It calls other skills (and MCP servers) as subprocess steps with explicit JSON input/output, never relying on Claude to "remember" results between steps. Each section is rendered by a separate panel module returning a `list[str]` of markdown lines; final assembly is deterministic concatenation.

## When to Use

Trigger on any of:
- "Daily briefing" / "morning briefing" / "EOD briefing"
- "What should I do with my portfolio today?"
- "Portfolio review" / "Portfolio health check"
- "Run the briefing"
- Scheduled/cron-driven invocation

## Prerequisites

**Required:**
- Python 3.10+, `pyyaml`
- `etrade-mcp` MCP server connected (see `references/etrade-mcp-setup.md`)
- `trader-memory-core` skill installed with `state/theses/` populated

**Recommended (graceful degradation if absent):**
- `FMP_API_KEY` — economic calendar, earnings calendar, screeners
- `FINVIZ_API_KEY` — 70-80% faster screener execution

## Workflow

### Step 1: Pre-flight
1. Read `state/briefing_config.yaml` (user config: enabled strategies, risk params, accounts)
2. Check auth status (E*TRADE API ready)
3. Load yesterday's briefing as consistency anchor

### Step 1.5: Directives
Load and evaluate active directives from `state/directives/index.yaml`. Transition expired directives. Carry forward active directives as input to recommendation steps.

### Step 1.6: Third-party recommendations
Fetch BUY/SELL/HOLD recommendations from recommendation-list-fetcher. Filter by freshness.

### Step 2: Snapshot inputs
Persist all API inputs (positions, balances, quotes, chains, open orders, theses) to `state/briefing_snapshots/YYYY-MM-DD/`. Enables reproducible runs and deterministic day-over-day diffs.

### Step 3: Regime classification
Read `references/regime_framework.md` rules. Classify market regime (RISK_ON / NORMAL / CAUTION / RISK_OFF) based on VIX, SPY movement, breadth, calendar events. Persist regime.json with inputs and confidence.

### Step 4: Portfolio review (equities)
For each held equity:
1. Check directives
2. Load thesis from trader-memory-core
3. Run technical-analyst
4. Check invalidation triggers
5. Apply equity decision matrix (references/equity_decision_matrix.md)
6. Tag: HOLD / ADD / TRIM / EXIT / REVIEW / DEFERRED / MANUAL

### Step 5: Options book
For each open option (short put, covered call, collar):
1. Check directives
2. Invoke wheel-roll-advisor with position + chain + context
3. Receive: decision tag (CLOSE / ROLL_OUT / TAKE_ASSIGNMENT / etc.) + target contract + rationale + matrix cell ID

### Step 6: New ideas
For each enabled screener:
1. Run screener
2. Filter to top N by score
3. Drop already-held tickers
4. Apply directives (suppress/watch_only)
5. Run position-sizer for each candidate
6. Suppress new longs if regime is RISK_OFF / CAUTION

### Step 7: Day-over-day consistency check
Compare today's recommendations against yesterday's. Require trigger events for any flips (price move, news, earnings, technical break). Flag inconsistencies without trigger events; do not surface them.

### Step 8: Render and aggregate
Combine step outputs into one markdown file with 11 sections (health, performance, hedge, risk alerts, directives, action list, portfolio review, opportunities, diffs, inconsistencies, manifest).

### Step 9: Quality gate
Run data-quality-checker against markdown. Verify numeric sanity, date consistency, allocation math, cross-references. If failing, mark as DRAFT.

### Step 10: Surface to user
Provide one-paragraph summary in chat + link to markdown + thesis transition count.

## Output Files

```
reports/daily/
  briefing_YYYY-MM-DD.md           # canonical markdown
  briefing_YYYY-MM-DD.json         # machine-readable companion

state/briefing_snapshots/YYYY-MM-DD/
  accounts.json
  positions.json
  balance.json
  quotes.json
  open_orders.json
  theses.json
  regime.json
  directives_active.json
  directives_expired_today.json
  chains/
    <symbol>_<expiry>.json
  screeners/
    <name>.json
  recommendations.json             # input to next-day consistency check
```

## Hard Rules

1. **Live-data backing.** Every actionable recommendation backed by data fetched this cycle.
2. **Expiration validation.** Every options contract validated against chain. Render as `Fri May 29 '26`.
3. **Concentration check (post-sizing).** Verify `existing_pct + new / NLV ≤ 10%`.
4. **Earnings guard.** No new short puts when `earnings_date ≤ expiration`.
5. **Tail-risk gate.** Names in tail_risk_names.yaml never eligible for new shorts.
6. **Macro-caution gate.** When regime is CAUTION/RISK_OFF, suppress new longs.
7. **No directional forecasts.** Flag conditions; don't predict direction.

## CLI

```bash
python3 scripts/run_briefing.py \
  --config config/briefing.yaml \
  --output reports/daily/briefing_$(date +%F).md

# Mock fixture (default):
python3 scripts/run_briefing.py \
  --config config/briefing.yaml \
  --etrade-fixture assets/etrade_mock_fixture.json

# Dry-run:
python3 scripts/run_briefing.py --dry-run

# Force re-run (overwrite today's):
python3 scripts/run_briefing.py --force
```

## References

- `references/regime_framework.md` — deterministic 11-rule classifier (RISK_ON / NORMAL / CAUTION / RISK_OFF)
- `references/equity_decision_matrix.md` — 36-cell lookup for equity recommendations
- `references/tail_risk_names.md` — conservative-treatment list
- `references/etrade-mcp-setup.md` — E*TRADE first-run setup
- `assets/briefing_config_template.yaml` — starter config schema
