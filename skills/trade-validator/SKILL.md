---
name: trade-validator
description: Validates every roll/CSP/collar action with probability-weighted expected value math, break-even pricing, market-implied assignment probability, and alternative-comparison. Blocks negative-EV trades. Surfaces "is this actually a good trade?" answers backed by deltas, not heuristics.
version: 1.0
---

# Trade Validator

The yield-calculator answers "what % return on capital is this." This skill answers a different, harder question: **"is this a good trade?"**

Yields are misleading on debit rolls. A diagonal-up roll showing "47% ann. on collateral" is selling cap-buffer insurance, not generating income. The right questions are:
1. What does this trade ACTUALLY get me, in dollar EV terms?
2. What's the break-even price?
3. What does the market's pricing imply about assignment probability?
4. Is there a better alternative I'm not considering?

## When invoked

Called for every roll, CSP, and collar action BEFORE rendering. Returns a `TradeValidation` with verdict and reasoning. The briefing renders the verdict alongside the action, AND blocks any trade that fails validation.

## Outputs per action

```python
{
  "verdict": "GOOD" | "MARGINAL" | "POOR" | "BLOCK",
  "expected_value_dollars": float,   # probability-weighted profit/loss
  "break_even_price": float,         # underlying price at which trade pays back
  "implied_assignment_probability": float,  # 0-1 from delta
  "cost_per_dollar_of_protection": float,   # for diagonal rolls
  "alternatives_ranked": [
    {"name": "HOLD", "ev": ..., "tradeoff": "..."},
    {"name": "current proposal", "ev": ..., "tradeoff": "..."},
    {"name": "alternative", "ev": ..., "tradeoff": "..."},
  ],
  "reasoning": "..."  # 1-2 sentence explanation
}
```

## Verdict logic

| Verdict      | Math threshold                                                              | Treatment in briefing                                                                |
|--------------|-----------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| **GOOD**     | EV ≥ 1.2× cost; break-even within 1σ of spot; implied prob aligns w/ thesis | Render. Optionally surface as a "high-conviction" candidate.                         |
| **MARGINAL** | 0.8× cost ≤ EV < 1.2× cost                                                  | Render with badge. User decides.                                                     |
| **POOR**     | EV < 0.8× cost OR break-even far from spot; suggests holding instead        | **NEW trade → filter out.** **Position management → render with badge + alternative.** |
| **BLOCK**    | EV < 0 in expected case; trade math doesn't work                            | Always filter out, regardless of category.                                           |

POOR is treated asymmetrically by category — see "Hook integration" below for the full table and rationale.

## What it computes

### For diagonal-up debit rolls (cap-buffer purchases):
- `break_even_price = current_strike + debit / shares`
- `protection_dollars = (new_strike - current_strike) × shares`
- `cost_pct_of_protection = debit / protection_dollars`
- `prob_buffer_useful = P(spot < new_strike) - P(spot < current_strike)` — the probability range where the roll is net positive
- `EV = prob_buffer_useful × protection_dollars - debit`

### For calendar rolls (same strike):
- `forward_yield = new_premium × 365 / new_dte / collateral`
- Compares to alternative yields (CSPs on same name, treasury rate, etc.)

### For new CSPs:
- `EV_if_OTM_expiry = premium`
- `EV_if_assigned = premium - (strike - effective_basis)` where effective_basis is based on user's belief
- `prob_OTM_expiry = 1 - delta` (rough)
- `Total_EV = prob_OTM_expiry × premium + (1 - prob_OTM_expiry) × (premium - max_drawdown_to_basis)`

### For collars:
- Floor protection value (probability-weighted downside protected)
- Cap-yield comparison to existing covered call alone

## Alternatives generated

For each trade, the validator surfaces 2-3 alternatives:
1. **HOLD** (do nothing — let the existing position run)
2. **Current proposal** (the one being validated)
3. **Cheaper variant** (if applicable — e.g., wider strike, shorter DTE)

Ranked by EV. If the proposal isn't the highest-EV option, the validator says so.

## Hook integration — canonical filtering rules

The validator is wired into `gate_and_render()` alongside the quality gate. The renderer must distinguish two trade categories and treat them differently:

### Category A — NEW trades (income-seeking, opens fresh exposure)

Examples: `PULLBACK CSP`, new `CSP`, new `BTO LEAP`, new `STO`, opening strangle/spread.

These are **discretionary** — the user only wins if the math is positive. There's no hidden "doing nothing is worse" force. So the briefing filters by verdict:

| Verdict      | Render?                                                              |
|--------------|----------------------------------------------------------------------|
| **GOOD**     | Render with `✅ GOOD TRADE` badge.                                    |
| **MARGINAL** | Render with `⚠️ MARGINAL` badge — user decides.                       |
| **POOR**     | **Filter out.** Do not place in the action list.                     |
| **BLOCK**    | **Filter out.** Do not place in the action list.                     |

Rejected ideas are aggregated into a single one-line transparency footer at the end of the action list, e.g.:

> *📉 3 CSP idea(s) rejected by trade-validator (negative expected value): AMZN (EV −$114), META (EV −$270), MSFT (EV −$216). Premium is too thin or strike too close to spot — wait for a better setup.*

This way the user (a) doesn't waste eyeball-time on negative-EV trades, and (b) still sees that the system considered them — so they don't wonder "why isn't AMZN here today?"

### Category B — POSITION MANAGEMENT (defensive, manages existing exposure)

Examples: `DEFENSIVE ROLL`, `DEFENSIVE COLLAR`, `EXECUTE ROLL`, `CLOSE NOW`, `TAKE PROFIT`.

These are **forced choices** — the user already has the position. "Do nothing" is itself a trade with its own EV (often *negative*: assignment ITM at a worse strike than the roll target). Filtering MARGINAL/POOR rolls would leave the user with no guidance at all on positions that need attention.

| Verdict      | Render?                                                              |
|--------------|----------------------------------------------------------------------|
| **GOOD**     | Render with badge.                                                   |
| **MARGINAL** | Render with badge + show top-ranked HOLD alternative for comparison. |
| **POOR**     | Render with badge + show top-ranked HOLD alternative.                |
| **BLOCK**    | Filter out. The trade math is genuinely broken.                      |

The user needs to see the position management option even if the EV is poor — they'll judge whether to act, accept assignment, or pick the alternative.

### Why the asymmetry

> **NEW trades:** the validator answers "is this worth opening?" → POOR = no.
>
> **MANAGEMENT trades:** the validator answers "given you're already exposed, what's the best move?" → POOR = "this is the best you can do, but it's not great."

Renderer authors must keep this distinction explicit. When you add a new action type to the briefing, decide which category it falls into and follow the matching filter rule. If it's discretionary new exposure, filter POOR/BLOCK. If it's managing an existing position, render with the badge.

### Implementation pointer

The PULLBACK CSP loop in `daily-portfolio-briefing/scripts/render/panels.py::render_action_list` is the canonical reference implementation for Category A. It tracks rejected ideas in a `_filtered_csps` list and emits the transparency footer once after the loop. New Category-A integrations should follow the same pattern.

## Why this is a separate skill

- The math is non-trivial enough to test independently
- Different trade types need different EV models
- The validator can be reused outside the briefing (e.g., one-off "should I do this trade?" CLI tool)
- Probability assumptions (delta as P(ITM)) are tunable in YAML, not hardcoded
