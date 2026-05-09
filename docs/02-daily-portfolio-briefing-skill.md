# daily-portfolio-briefing — SKILL.md draft

**Status:** Draft v0.1
**Eventual location:** `claude-trading-skills/skills/daily-portfolio-briefing/SKILL.md`
**Date:** 2026-05-07

This is a draft of the SKILL.md content, formatted exactly as it will be committed to the skills repo. It is the orchestrator that ties E*TRADE positions, market context, screeners, options analysis, and trader-memory-core into a single daily report.

---

```markdown
---
name: daily-portfolio-briefing
description: Produce a single daily markdown briefing covering market regime, every existing portfolio position (equity and options), wheel/options roll decisions, and screener-sourced new ideas with position sizing. Pulls live holdings from E*TRADE via etrade-mcp, anchors recommendations to persisted theses in trader-memory-core, and snapshots inputs for day-over-day consistency. Use when the user requests a daily portfolio review, morning briefing, "what should I do today", portfolio health check, or end-of-day report. Anchors against yesterday's recommendations to prevent inconsistencies between runs.
---

# Daily Portfolio Briefing

## Overview

Produce one markdown report per trading day. Section ordering follows wheelhouz's "health-first" pattern — portfolio state first, recommendations follow from the dashboard rather than the reverse:

1. **Header** — date, regime label, portfolio NLV, day change, YTD vs SPY
2. **Health** — concentration, sector exposure, options book net Greeks, stress coverage
3. **Performance** — P&L, win rate, profit factor, strategy mix
4. **Hedge book** — current hedges, recommended adjustments (passive in v1; active in v2)
5. **Risk alerts** — defensive rolls, earnings within window, concentration drift, tail-risk warnings
6. **Watch / Portfolio review** — every existing equity and option position with explicit recommendation tag
7. **Income opportunities / New ideas** — screener output filtered to user's enabled strategies, sized
8. **Today's action list** — deduplicated, prioritized list of "do these things today"
9. **Recommendation changes since last briefing** — day-over-day diff with trigger explanations
10. **Inconsistencies flagged** — empty in clean runs; populated when day-over-day check fails
11. **Appendix: snapshot manifest** — files written for reproducibility

The skill is an **orchestrator**. It calls other skills (and MCP servers) as subprocess steps with explicit JSON input/output, never relying on Claude to "remember" results between steps. Each section is rendered by a separate panel module returning a `list[str]` of markdown lines; final assembly is a deterministic concatenation. **Claude's judgment lives inside each step, not in the assembly.**

This panel architecture is borrowed from wheelhouz `src/delivery/briefing/` — see `04-from-wheelhouz-keep-drop.md` for the full mapping of what we keep and drop.

## When to Use

Trigger on any of:
- "Daily briefing" / "morning briefing" / "EOD briefing"
- "What should I do with my portfolio today?"
- "Portfolio review" / "Portfolio health check"
- "Run the briefing"
- Scheduled/cron-driven invocation

## Prerequisites

**Required:**
- `etrade-mcp` MCP server connected and authenticated (see `references/etrade-mcp-setup.md`)
- `trader-memory-core` skill installed and `state/theses/` directory populated for current holdings
- Python 3.10+, `pyyaml`

**Recommended (briefing degrades gracefully if absent):**
- `FMP_API_KEY` — used by economic-calendar-fetcher, earnings-calendar, screeners
- `FINVIZ_API_KEY` — speeds up screeners by 70-80%

**Sub-skills invoked:**
- `economic-calendar-fetcher` — today's events
- `earnings-calendar` — today's reporting companies, plus earnings within 7 days for held tickers
- `market-news-analyst` — overnight news scan
- `breadth-chart-analyst` — market breadth state (loads cached chart if available)
- `technical-analyst` — chart status for each held position (and watchlist)
- `wheel-roll-advisor` — per-option roll/hold/close decisions (NEW skill, see 03-wheel-roll-advisor-skill.md)
- `recommendation-list-fetcher` — third-party stock recommendations from a Google Sheet, used as triggers in equity review and as candidate sources in new ideas (NEW skill, see 11-recommendation-list-skill.md)
- `value-dividend-screener`, `dividend-growth-pullback-screener`, `earnings-trade-analyzer`, `pead-screener` — idea generation, filtered by user's enabled strategies
- `position-sizer` — for any new candidates the briefing wants to size
- `data-quality-checker` — runs against the final markdown to catch numeric drift before publishing
- `trader-memory-core` — read all theses, mark review-due, transition state where briefings recommend it

## Workflow

### Step 1: Pre-flight

1. Read `state/briefing_config.yaml` (user-customizable; see `assets/briefing_config_template.yaml`):
   - Enabled strategies (e.g., `wheel`, `dividend_growth`, `swing`)
   - Risk parameters (max position %, max sector %, max portfolio risk %)
   - Preferred screeners and frequency (e.g., `pead-screener: every_run`, `pair-trade-screener: weekly`)
   - Per-account scope (which E*TRADE `accountIdKey`s to include)
2. Check `etrade-mcp.auth_status()`. If not authenticated, abort cleanly with the verifier URL and instructions.
3. Load yesterday's briefing from `reports/daily/` (most recent file). This becomes the "anchor" for consistency comparison in Step 7.

### Step 1.5: Load and evaluate directives

User-issued directives from prior briefing reviews are persistent context that today's run must honor. See `05-briefing-directives.md` for the full design.

1. Read `state/directives/index.yaml`. For each ACTIVE directive:
   - Evaluate its expiry trigger against current state (positions, prices, calendar, screener output)
   - If trigger fired, transition to EXPIRED, move file to `state/directives/expired/`, write timestamp
2. Carry still-ACTIVE directives forward as input to Steps 4-6 (recommendation generation)
3. Capture today's expirations (directives that fired this run) for surfacing in the report

Outputs:
- `state/briefing_snapshots/YYYY-MM-DD/directives_active.json` — active directives at briefing time
- `state/briefing_snapshots/YYYY-MM-DD/directives_expired_today.json` — directives that expired during this run

If a directive evaluation fails (e.g., earnings calendar unavailable so `earnings_passed` trigger can't be checked), the directive stays ACTIVE and the briefing logs a warning — better to over-suppress than to surface a recommendation the user has already deferred.

### Step 1.6: Fetch third-party recommendations

Run `recommendation-list-fetcher` (see `11-recommendation-list-skill.md`). Output is `reports/daily/recommendations_YYYY-MM-DD.json` containing parsed BUY/SELL/HOLD records from the user's Google Sheet, normalized and freshness-filtered.

The output feeds two downstream steps:

- **Step 4 (portfolio review):** for each held position, look up matching recommendations. SELL on held → flag in watch panel and upgrade trim/exit if thesis is WEAKENING. BUY on held → supporting evidence for an existing ADD recommendation.
- **Step 6 (new ideas):** unheld tickers with current BUY recommendations enter the candidate pool with `source: "recommendation_list"`, ranked by conviction × freshness.

Recommendations are *triggers*, not new state-variable dimensions in the equity matrix. Same level as `NEWS_NEGATIVE` or `MA50_LOST`.

If `recommendation-list-fetcher` fails (sheet inaccessible, quota exceeded, etc.) the briefing continues without third-party signals — they're enrichment, not load-bearing.

### Step 2: Snapshot inputs

Persist a snapshot of every input to the briefing under `state/briefing_snapshots/YYYY-MM-DD/`. This is the single biggest fix for the inconsistency problem: every run is reproducible from its snapshot, and day-over-day diffs become trivial.

Files written:
- `accounts.json` — `etrade-mcp.list_accounts()`
- `positions.json` — `etrade-mcp.get_positions()` for each in-scope account, merged
- `balance.json` — `etrade-mcp.get_account_balance()` per account
- `quotes.json` — `etrade-mcp.get_batch_quotes()` for all held symbols + watchlist
- `chains/<symbol>_<expiry>.json` — `etrade-mcp.get_option_chain()` for every open option position's underlying, plus rolling targets (next 2 monthly expirations beyond current)
- `open_orders.json` — `etrade-mcp.list_orders(status="OPEN")`
- `theses.json` — `trader-memory-core` thesis store dump filtered to held tickers

After this step, the briefing operates against the snapshot, not against live API calls. If something is off, you can re-run the briefing tomorrow against today's snapshot and reproduce the report exactly.

### Step 3: Regime snapshot

Read `references/regime_framework.md` for the regime classification rules.

Run in parallel:
- `economic-calendar-fetcher --from today --to today+1`
- `earnings-calendar --from today --to today+1`
- `market-news-analyst` (last 18 hours)
- `breadth-chart-analyst` (most recent breadth chart, if available)

Combine into a regime label using the deterministic rules in `references/regime_framework.md`:
- `RISK_ON` / `RISK_OFF` / `MIXED` / `CAUTION` / `DEFENSIVE`

Write `state/briefing_snapshots/YYYY-MM-DD/regime.json` with both the inputs and the derived label. The label changes downstream behavior (e.g., in `RISK_OFF` regime, new long ideas are suppressed unless they pass a stricter screen).

### Step 4: Portfolio review (equity positions)

For each equity position in `positions.json` where `assetType == "EQUITY"`:

1. **Check directives first.** Look up active directives whose target matches this position. If a `DEFER`, `MANUAL`, or `WATCH_ONLY` directive applies, the position is flagged for the watch panel only — no actionable recommendation generated. If a `SUPPRESS` directive applies (rare for equities since they're already held), surface the directive and skip.
2. Look up the matching thesis in `theses.json`. If no thesis exists, the position is "untracked" — flag it with a `REVIEW` recommendation and "Register thesis with trader-memory-core" as the action.
3. Run `technical-analyst` on the position's chart. Pass through the user's holding period (from thesis) so the analyst uses the right timeframe.
4. Check thesis invalidation triggers:
   - Price below `invalidation_price`?
   - Past `next_review_date` from thesis?
   - Earnings within 7 days? (cross-reference earnings-calendar)
   - News flagged in step 3?
5. Apply the deterministic decision matrix from `references/equity_decision_matrix.md` to map (thesis status, technical status, triggers, position size vs. target) → recommendation tag.
6. **Apply OVERRIDE directives if any.** If a directive overrides a parameter (e.g., trim threshold, target weight), use the override instead of the matrix default. Tag the matrix cell ID with `_DIRECTIVE_<id>` for auditability.
7. Output one entry per position with: ticker, current price, P&L, position weight, thesis tag, technical tag, **recommendation tag**, **rationale (1-2 sentences)**, and any directive that affected the recommendation.

Recommendation tags are an enum: `HOLD`, `ADD`, `TRIM`, `EXIT`, `REVIEW`, `DEFERRED`, `MANUAL`. Never freeform.

### Step 5: Options book

For each option position in `positions.json` where `assetType == "OPTION"`:

1. **Check directives first.** Look up active directives matching this contract or its underlying. If `DEFER`/`MANUAL`/`WATCH_ONLY`, suppress the actionable recommendation and surface in the watch panel with the directive reason. If `OVERRIDE`, prepare the parameter override to pass into `wheel-roll-advisor`.
2. Load the relevant chain from `chains/`.
3. Invoke `wheel-roll-advisor` with structured input: position, current chain, underlying outlook (from Step 4 if held; from technical-analyst if cash-secured put), IV percentile, days to expiration, earnings-proximity flag, **plus any OVERRIDE directives** as `overrides` field.
4. Receive a structured recommendation: tag (`LET_EXPIRE` / `ROLL_OUT` / `ROLL_OUT_AND_DOWN` / `ROLL_OUT_AND_UP` / `CLOSE` / `TAKE_ASSIGNMENT` / `WAIT` / `DEFERRED`), suggested target contract (if rolling), expected net credit/debit, rationale, matrix cell ID.
5. Output one entry per contract.

Cross-check with `open_orders.json`: if a roll is already submitted, surface "Order #X already in market at $Y" instead of re-recommending.

### Step 6: New ideas

For each enabled screener in `briefing_config.yaml`:

1. Run the screener. Save full output to `state/briefing_snapshots/YYYY-MM-DD/screeners/<name>.json`.
2. Filter to top N (configurable, default 5) by the screener's native score.
3. Drop any candidate already held (cross-reference positions.json).
4. **Apply directives.** Drop any candidate matching a `SUPPRESS` directive. Move any candidate matching a `WATCH_ONLY` directive into the watching subsection (with the trigger condition surfaced) instead of the actionable list.
5. For each surviving candidate, run `position-sizer` with the user's risk params and current portfolio constraints from `briefing_config.yaml` and `balance.json`.
6. If the regime is `RISK_OFF` or `CAUTION`, suppress new long ideas; only show short ideas (parabolic-short etc.) and existing-position trims.

Output a ranked list. Each entry: ticker, screener source, score, recommended sizing (shares + dollar amount + % of portfolio), 1-line rationale.

### Step 7: Day-over-day consistency check

Read yesterday's briefing's machine-readable JSON output (`state/briefing_snapshots/YYYY-MM-DD-1/recommendations.json`).

For each position with a recommendation today:
- If recommendation flipped (e.g., HOLD → TRIM), require a corresponding **trigger event** (price level, news, earnings, technical break). Surface flips with their trigger reason in the report under "Recommendation changes since last briefing."
- If a recommendation flipped without a trigger, the briefing flags this as a self-inconsistency and **does not publish the new recommendation**. Instead it surfaces "Inconsistency detected: yesterday HOLD, today TRIM, no trigger event found. Investigate." This is the consistency safeguard.

The same check runs for options-book entries with even tighter rules (an option roll recommendation flipping is a serious enough thing that we want the trigger to be very explicit).

### Step 8: Aggregate and render

Combine all step outputs into one markdown file: `reports/daily/briefing_YYYY-MM-DD.md`.

Structure (rendered from `assets/briefing_template.md`). Each panel is its own module in `scripts/render/`:

```markdown
# Daily Briefing — YYYY-MM-DD                          [render/header.py]
- Regime: <emoji+label>
- Portfolio value: $X (day change: ±Y%, vs SPY: ±Z bps, YTD: ±W%)
- Cash: $X (Y% of NLV)
- Action items: N (M urgent)
- Recommendation changes since last run: K

## Health                                               [render/health_panel.py]
- Top 5 holdings by weight, with concentration flags (>10% breach)
- Sector exposure with flags (>35% breach)
- Options book net Greeks (delta/theta/vega/gamma)
- Stress coverage ratio with traffic light (<0.5 red / 0.5-0.7 yellow / >0.7 green)

## Performance                                          [render/performance_panel.py]
- YTD vs SPY vs vanilla wheel
- WR / Avg Win / Avg Loss / Profit Factor / Max DD / Current DD
- Strategy mix (alert when one strategy >70% of recent trades)

## Hedge Book                                           [render/hedge_panel.py]
- Current hedges + delta neutralization %
- Recommended adds/removes given regime
- Scenario table: P&L at SPY -5% / -10% / -15% / -20%
- (v1: read-only summary; v2: actionable hedge tickets)

## Risk Alerts                                          [render/risk_alerts_panel.py]
- Drawdown alerts (highest severity, top of list)
- DEFENSIVE ROLL items (from wheel-roll-advisor)
- Collar expirations approaching (<30 DTE)
- Expiration cluster warnings (>15% NLV on a single date)
- Concentration drift (6-8% drift / 8-10% warning / >10% breach)
- Tail-risk warnings (per references/tail_risk_names.md)

## Directives                                           [render/directives_panel.py]
- Active directives summary (DEFER / MANUAL / OVERRIDE / WATCH_ONLY / SUPPRESS)
- Expired-today directives — recommendations re-surfacing below
- "Resume?" prompts for MANUAL directives older than 30 days

## Today's Action List                                  [render/action_list.py]
[Numbered, prioritized list with explicit ORDER tickets where applicable]
[Items affected by active directives are excluded; expired-today directives' resurfaced recommendations are flagged]

## Watch / Portfolio Review                             [render/watch_panel.py]
### Equities (sorted by weight)
[Step 4 output, per-position with recommendation tag]
### Options
[Step 5 output, per-contract with wheel-roll-advisor decision]

## Income Opportunities / New Ideas                     [render/opportunities_panel.py]
[Step 6 output, sized + suppressed during RISK_OFF/CAUTION regimes]

## Recommendation Changes Since Last Briefing           [render/diff_panel.py]
[Step 7 diffs with trigger explanations]

## Inconsistencies Flagged                              [render/inconsistency_panel.py]
[Step 7 inconsistency reports — empty in clean runs]

## Appendix: Snapshot Manifest                          [render/manifest.py]
[List of every file in state/briefing_snapshots/YYYY-MM-DD/]
```

Also write the machine-readable companion: `reports/daily/briefing_YYYY-MM-DD.json` with the structured recommendations. Tomorrow's briefing reads this file in Step 7.

### Step 9: Quality gate

Run `data-quality-checker` against the rendered markdown:
- Price scale sanity (no $1.5 quotes for AAPL etc.)
- Date consistency (no off-by-year dates)
- Allocation math (position % weights sum to ~100%)
- Internal cross-references (every "see Section X" actually exists)

If quality check fails, the briefing is marked DRAFT and not surfaced as the canonical output. The skill returns the issues for the user to review.

### Step 10: Surface to user

Provide the user with:
- A one-paragraph summary in chat (regime label + top 3 action items)
- A `computer://` link to `reports/daily/briefing_YYYY-MM-DD.md`
- The count of trader-memory-core theses transitioned during the run (e.g., "3 review-due theses marked OK, 1 flagged for revision")

## Output Files

```
reports/daily/
  briefing_YYYY-MM-DD.md           # canonical markdown report
  briefing_YYYY-MM-DD.json         # machine-readable companion

state/briefing_snapshots/YYYY-MM-DD/
  accounts.json
  positions.json
  balance.json
  quotes.json
  open_orders.json
  theses.json
  regime.json
  directives_active.json           # active directives at briefing time
  directives_expired_today.json    # expirations during this run
  chains/
    <symbol>_<expiry>.json
  screeners/
    <name>.json
  recommendations.json             # structured recommendations (input to next-day step 7)

state/directives/
  index.yaml                       # source of truth: id → file, status, target
  active/
    dir_<date>_<target>_<hash>.yaml
  expired/
  overridden/
  resolved/
```

## Reference Files

- `references/regime_framework.md` — **Already drafted** (see `09-regime-framework.md`). Deterministic classifier with 11 rules + stickiness logic + confidence scoring. Maps VIX, SPY drop, breadth, distribution days, economic events, earnings cluster, news intensity → RISK_ON / NORMAL / CAUTION / RISK_OFF.
- `references/equity_decision_matrix.md` — **Already drafted** (see `10-equity-decision-matrix.md`). 36 matrix cells + 14 guardrails. Outputs HOLD/ADD/TRIM/EXIT/REVIEW/DEFERRED/MANUAL with LTCG and tax-loss-harvest awareness.
- `references/wheel_options_decision_matrix.md` — **Already drafted** (see `08-wheel-decision-matrix.md`). Invoked indirectly via wheel-roll-advisor.
- `references/directive_schema.md` — full schema for `state/directives/*.yaml` entries (see `05-briefing-directives.md`)
- `references/etrade-mcp-setup.md` — first-run setup, OAuth dance, troubleshooting (TBD)
- `references/briefing_config_schema.md` — full YAML schema for briefing_config.yaml (TBD)
- `assets/briefing_template.md` — markdown template (TBD)
- `assets/briefing_config_template.yaml` — starter config (TBD)

## Failure Modes & Fallbacks

| Failure | Behavior |
|---|---|
| etrade-mcp not authenticated | Surface verifier URL, abort. Do not produce a report against stale snapshot. |
| FMP API key invalid | Skip economic + earnings calendars; regime label set to `UNKNOWN`. Continue with portfolio review only. |
| Screener fails | That screener's section says "screener failed: <reason>"; rest of briefing proceeds. |
| Yesterday's briefing missing | Skip Step 7 consistency check; flag in report header "first run — no consistency check." |
| Quality gate fails | Save as `briefing_YYYY-MM-DD.DRAFT.md`; surface issues to user; do not write canonical version. |
| trader-memory-core empty | Treat all positions as untracked; recommend "register thesis" for each. |

## Hard rules — every rendering path must enforce

These are non-negotiable. Borrowed and adapted from wheelhouz CLAUDE.md (the disciplines that prevented its briefing from being a complete liability):

1. **Live-data backing.** Every actionable recommendation must be backed by data fetched in this analysis cycle: underlying price, IV rank/percentile, technical consensus, chain validity. Missing data → no recommendation. The briefing surfaces "data unavailable, verify before placing" instead of papering over with defaults.
2. **Expiration validation.** Every options contract surfaced has its expiration validated against `chains/<symbol>_<expiry>.json`. Render dates as `Fri May 29 '26` (weekday + year) — Saturday/Sunday on equity options is a bug. Use `wheel-roll-advisor`'s `validate_expiration()` utility.
3. **Concentration check (post-sizing).** Before recommending a new put or equity buy, verify `existing_pct + new_collateral / NLV ≤ 10%`. Pre-sizing check is insufficient — the trade itself can push a name over.
4. **Earnings guard.** No new short puts when `next_earnings ≤ expiration` of the proposed contract. Existing positions evaluated by `wheel-roll-advisor` separately.
5. **Tail-risk gate.** Names in `references/tail_risk_names.md` are NOT eligible for new short-put recommendations. On existing positions, surface ⚠ TAIL RISK warning. DEFENSIVE ROLL trigger overridden to CLOSE_NOW.
6. **Macro-caution gate.** When regime is `CAUTION` or `RISK_OFF`, new long ideas are suppressed; only flag existing-position trims and short ideas. Covered calls (defensive) stay actionable.
7. **No directional forecasts.** The briefing FLAGS conditions; it does NOT predict direction. "Setup historically precedes 3-5% pullbacks" not "SPY will drop." This is the difference between a tool that helps and one that misleads.

## Output Conventions

- All recommendations are tagged enums; never freeform verbs
- Every recommendation cites which decision matrix entry produced it (matrix cell ID for auditability)
- Day-over-day flips are highlighted explicitly with trigger reason
- Numeric values rounded consistently: prices to 2dp, percentages to 1dp, currencies with thousands separators
- Expiration dates always rendered as `Fri May 29 '26`

## Limitations

- Cannot place orders (read-only by design)
- Cannot replace user judgment on regime calls (the framework is deterministic but the inputs — news scoring, breadth interpretation — still involve LLM judgment per sub-skill)
- Multi-day vacation handling: if briefing has not run for >3 days, Step 7 consistency baseline is rebuilt from scratch and flagged in the report

```

---

## Notes for the build

**Why the snapshot directory matters.** Today the briefing makes ~30 API calls. The snapshot lets you re-render the briefing tomorrow against today's data, which is invaluable for debugging "why did it recommend that?" — you can rerun the report deterministically and confirm whether the issue was the input data or the reasoning. Without snapshots, every bug investigation is heisenberg-ed.

**Why decision matrices instead of "just ask Claude."** This is the core fix for the consistency problem. A markdown table in `references/equity_decision_matrix.md` like:

| Thesis status | Technical | Position weight | Recommendation |
|---|---|---|---|
| INTACT | Uptrend | < target | ADD |
| INTACT | Uptrend | ≥ target | HOLD |
| INTACT | Sideways | any | HOLD |
| INTACT | Downtrend | > target | TRIM |
| WEAKENING | Uptrend | any | HOLD (review) |
| WEAKENING | Downtrend | any | TRIM |
| BROKEN | any | any | EXIT |

…ensures the same inputs always produce the same recommendation tag. Claude's job becomes "fill in `thesis status` and `technical` for this position" — those individual judgments are still subjective, but they're narrow and well-defined, and the *aggregation* is mechanical.

**Why the day-over-day check.** This is the real consistency layer. Without it, day-to-day variance in Claude's judgment creates whiplash recommendations. With it, a flip from HOLD to TRIM requires evidence of *what changed*. If nothing changed materially, the recommendation stays the same.

**Why JSON snapshots even though Claude prefers markdown.** Because next-day Step 7 needs to do exact diff. Markdown is for humans; JSON is for the consistency check.
