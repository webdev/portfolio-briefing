---
name: capital-planner
description: Aggregates per-action cash flows across the full briefing (closes, trims, rolls, hedges, new CSPs, long-term opportunities), projects the ending cash position, ranks actions by risk-reward priority tier, filters long-term ideas by concentration + recommendation strength, and renders a Capital Plan panel that answers "do I have enough money for all of this, and which moves should I make first?"
version: 1.0
---

# Capital Planner

The action list tells you *what* to do; this skill tells you *whether you can afford it* and *what order to do it in*. Without this, the briefing leaves the user staring at 11 action items + 18 long-term opportunities trying to do the math themselves.

## Inputs

- `equity_reviews` — equity-level decisions (TRIM, HOLD, etc.)
- `options_reviews` — option-level decisions (CLOSE, ROLL, HOLD, etc.)
- `new_ideas` — short-dated income opportunities (PULLBACK CSP)
- `long_term_opportunities` — multi-month LongTermOpportunity dicts (ADD, TRIM, EXIT, LEAP_CALL, LONG_DATED_CSP, ...)
- `analytics["hedge_book"]` — recommended hedge adds (`spy put` or VIX call)
- `balance` — current cash, NLV
- `positions` — for current weight % per ticker (concentration check)
- `iv_ranks`, `recommendations_list` — for filtering long-term CSPs

## Outputs

A `CapitalPlan`:

```python
{
  "starting_cash": float,
  "ending_cash_projected": float,
  "total_premium_received": float,
  "total_btc_cost": float,
  "total_debit_paid": float,
  "total_collateral_freed": float,
  "total_collateral_locked": float,
  "net_cash_change": float,
  "tax_estimate_ltcg": float,
  "actions_by_tier": {1: [...], 2: [...], 3: [...], 4: [...]},
  "skipped_actions": [...],   # with reason
  "summary_md": str,          # rendered panel
}
```

Each `CapitalAction` carries:

```python
{
  "kind": "CLOSE" | "TRIM" | "ROLL" | "HEDGE" | "NEW_CSP" | "LT_CSP" | "LT_LEAP" | "LT_ADD" | "LT_TRIM" | "LT_EXIT",
  "ticker": str,
  "description": str,
  "cash_in": float,            # collateral freed + premium received
  "cash_out": float,           # BTC cost + debit + new collateral locked
  "net_cash": float,
  "new_collateral_locked": float,
  "ev": float | None,          # from trade-validator if available
  "validator_verdict": "GOOD" | "MARGINAL" | "POOR" | "BLOCK" | None,
  "tier": 1 | 2 | 3 | 4,
  "tier_reason": str,
  "skip_reason": str | None,   # set if filtered (e.g., concentration breach, weak rec)
}
```

## Priority tiers

The tier rules are deterministic (live in `references/prioritization_rules.yaml`):

| Tier | What                                                                          | When                                                                                                               |
|------|-------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------|
| **1 — CRITICAL** | Hedges when stress coverage < 0.5 ✦ Closes with ≥30% capture                  | Coverage red ✦ Profit already booked, residual risk near zero. Skipping costs you protection or theta.            |
| **2 — IMPORTANT** | Concentration trims (>10% NLV) ✦ Validator GOOD new trades ✦ Credit rolls    | Removes idiosyncratic risk ✦ Positive expected value backed by chain ✦ Free cash + cap headroom.                   |
| **3 — OPTIONAL** | Debit rolls with MARGINAL EV ✦ Validator MARGINAL trades ✦ Long-term CSPs    | Discretionary. Skip if cash-tight or if you'd rather wait for a better setup.                                      |
| **4 — DEFER**    | BLOCKED actions (earnings within window) ✦ Long-term ideas filtered out      | Don't act now. The skill emits these in a "Skipped — see why" subsection so the user knows the system saw them.   |

## Long-term CSP filtering

For LONG_DATED_CSP ideas from `long-term-opportunity-advisor`, this skill applies portfolio-fit rules **on top of** the existing trade-validator filtering:

| Filter                                                              | Outcome                |
|---------------------------------------------------------------------|------------------------|
| Underlying weight ≥ `concentration_cap_pct` (default 10)            | SKIP — over cap        |
| Underlying weight ≥ `near_cap_pct` (default 8)                       | DEFER — near cap       |
| Third-party rec is `HOLD` or `SELL`                                  | SKIP — weak rec        |
| Otherwise                                                            | TIER 3 — optional      |

The user is aiming to *add* exposure with these CSPs. Adding to a name that's already 14% NLV (and being trimmed!) compounds the concentration breach. Same logic for HOLD/SELL recs — the implicit "if assigned I'd be happy owning at strike" assumption fails.

## Capital ranking within tiers

Within tier 3, long-term CSPs are ranked by:

1. Validator EV per dollar of collateral (higher is better)
2. Recommendation tier (STRONG_BUY > BUY > others)
3. Distance from concentration cap (more headroom = higher rank)

The user gets a "do these top N first" list given their available cash.

## Outputs

A markdown panel rendered after `Today's Action List`:

```markdown
## 💰 Capital Plan

**Starting cash:** $152,979 | **Projected ending cash:** $326,701 (+$173,722)

### Tier 1 — CRITICAL (do first)
1. HEDGE SPY $700P · 18× · cost $13,277 · 19× tail leverage
2. CLOSE GOOG_PUT_355 · frees $35,500 · locks $107 profit
...

### Tier 2 — IMPORTANT
8. TRIM GOOG to 9% NLV · raises $64,446 · LTCG cost ~$6,146
...

### Tier 3 — OPTIONAL
5. ROLL MSFT diagonal-up · debit $2,360 · cap +12%
...

### Tier 4 — DEFER (do not act)
- ROLL NVDA — earnings 11d away; revisit post-print

### Skipped long-term CSPs
- AMD $410P — only HOLD rec
- GOOG $355P — already 14.8% (over concentration cap)
- MSFT $375P — already 8.9% (near cap)

**Top 5 long-term CSPs to do (within budget):**
| # | Trade | Collat | Premium | Yield |
| 1 | META $550P | $55,000 | $1,524 | 13.5% |
...
```

## Why this is a separate skill

- It needs to consume *every* recommendation type (closes, rolls, opens, long-term ideas) — putting this logic in any single advisor would couple advisors to each other.
- The tier rules and filtering logic should be testable in isolation.
- Future work (auto-rebalance suggestions, account-routing optimization) plugs in here naturally.

## Hook integration

Wired into `aggregate.py` as a panel renderer that runs after `render_action_list` and before `Watch / Portfolio Review`. The skill consumes the structured outputs of the prior steps — it does NOT parse the rendered markdown.
