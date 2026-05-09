# wheel-roll-advisor — SKILL.md draft

**Status:** Draft v0.1
**Eventual location:** `claude-trading-skills/skills/wheel-roll-advisor/SKILL.md`
**Date:** 2026-05-07

This is the new skill that owns the wheel-strategy roll/hold/close decision. It's the single piece of new functionality that gives the briefing actual decision-making teeth on options. Everything else in the briefing is wiring existing skills.

---

```markdown
---
name: wheel-roll-advisor
description: Deterministic decision engine for wheel-strategy options positions. Given an open option (cash-secured put or covered call), the current option chain, the underlying outlook, and earnings/IV context, produce one of seven structured recommendations: LET_EXPIRE, ROLL_OUT, ROLL_OUT_AND_DOWN, ROLL_OUT_AND_UP, CLOSE_FOR_PROFIT, TAKE_ASSIGNMENT, or WAIT. Returns the target contract for any roll recommendation along with expected net credit. Use when analyzing wheel positions individually or as part of the daily-portfolio-briefing options book section.
---

# Wheel Roll Advisor

## Overview

The wheel strategy (selling cash-secured puts on stocks you'd want to own, then covered calls if assigned) lives or dies on the roll decision. Every existing wheel position gets evaluated by the same questions: is this still ITM, how close to expiry, what's the underlying doing, what's IV, are earnings near, is there a profitable roll target? Doing this consistently across 5-15 contracts on a daily basis is exactly what skills are good at — and exactly what algorithmic bots get wrong, because the right answer depends on context an if-statement can't capture.

This skill encodes the framework as a deterministic decision matrix. The same inputs always produce the same output. Claude's judgment is reserved for the underlying outlook (which feeds in as one input), not for the roll decision itself.

## When to Use

- Daily-portfolio-briefing's options-book section invokes this per contract
- Ad hoc: "Should I roll my AAPL 170P?" or "Look at my open options"
- Postmortem analysis: "Was my roll on MSFT correct?" (re-run with historical chain data)

## Prerequisites

**Required:**
- Python 3.10+, `pyyaml`
- E*TRADE option chain data (or any compatible chain JSON)

**Recommended:**
- `FMP_API_KEY` for IV-percentile lookup and historical IV
- `earnings-calendar` skill output for earnings-proximity flag
- `technical-analyst` skill output for underlying outlook (or pass it directly)

## Inputs

The skill takes structured JSON, not freeform prompts:

```json
{
  "position": {
    "optionSymbol": "AAPL  260619P00170000",
    "underlyingSymbol": "AAPL",
    "optionType": "PUT",
    "side": "SHORT",
    "strike": 170.00,
    "expiration": "2026-06-19",
    "quantity": -2,
    "averageCost": 4.85,
    "currentMid": 1.50,
    "openDate": "2026-04-22"
  },
  "underlying": {
    "lastPrice": 181.20,
    "dayChange": 0.37,
    "outlookTag": "BULLISH",
    "outlookSource": "technical-analyst",
    "supportLevel": 175.00,
    "resistanceLevel": 188.00
  },
  "context": {
    "ivPercentile": 42,
    "ivRank": 38,
    "earningsInDays": null,
    "exDividendInDays": null,
    "regime": "RISK_ON",
    "userIntent": "WHEEL_PUT_SIDE"
  },
  "chain": {
    "underlyingPrice": 181.20,
    "expirations": [
      {"expiration": "2026-06-19", "type": "MONTHLY"},
      {"expiration": "2026-07-17", "type": "MONTHLY"},
      {"expiration": "2026-08-21", "type": "MONTHLY"}
    ],
    "candidateContracts": [
      {"optionSymbol": "...", "strike": 165, "expiration": "2026-07-17", "bid": 2.10, "ask": 2.30, "delta": -0.18, "iv": 0.23},
      ...
    ]
  }
}
```

`outlookTag` is one of: `STRONG_BULLISH`, `BULLISH`, `NEUTRAL`, `BEARISH`, `STRONG_BEARISH`. Either Claude or `technical-analyst` produces this — the wheel-roll-advisor doesn't compute it.

## Decision Matrix

Read `references/decision_matrix.md` for the full table. Summary structure below.

**Numbers in the matrix are calibrated to wheelhouz's production thresholds** — these came from real trading and are worth migrating verbatim. See `04-from-wheelhouz-keep-drop.md` for provenance.

### Pre-matrix guardrails (run first; can short-circuit the matrix)

These fire before matrix lookup. If any guardrail returns a decision, that decision is final:

1. **Loss stop:** if `current_mid / averageCost ≥ 2.0` (monthlies) or `≥ 1.5` (weeklies, DTE ≤ 10) → `CLOSE` with rationale "loss stop". Covered calls exempt — an underwater call means the stock rallied.
2. **Earnings within 7 days of expiration:** for short puts where the stock has earnings before expiry → `CLOSE_FOR_PROFIT` if profit ≥ 50%, otherwise `WAIT` (no roll across earnings).
3. **Tail-risk name (per `references/tail_risk_names.md`):** any DEFENSIVE_ROLL or ROLL_* recommendation overridden to `CLOSE` with rationale "tail risk: don't roll, walk away."
4. **Open order on this contract already in market:** decision is `WAIT` with note "order #X already submitted at $Y".

### Matrix: SHORT PUTs (cash-secured)

The framework is governed by four state variables: **moneyness**, **time**, **profit captured**, and **outlook**.

| Moneyness | DTE | Captured | Delta | Outlook | Regime | Decision | Cell ID |
|---|---|---|---|---|---|---|---|
| OTM (delta < 0.10, deep) | > 120 | ≥ 50% | any | any | any | CLOSE_FOR_PROFIT | DEEP_OTM_LONGDTE |
| OTM (delta < 0.10, deep) | 60-120 | ≥ 65% | any | any | any | CLOSE_FOR_PROFIT | DEEP_OTM_MIDDTE |
| OTM (delta < 0.10, deep) | < 60 | ≥ 80% | any | any | any | CLOSE_FOR_PROFIT | DEEP_OTM_SHORTDTE |
| OTM (delta 0.10-0.25) | > 21 | < 50% | < 0.25 | any | NORMAL | WAIT | OTM_LONGDTE_LOWCAPTURE |
| OTM (delta 0.10-0.25) | > 21 | 50-75% | < 0.25 | any | NORMAL | GTC_LIMIT_75 (squeeze play) | OTM_LONGDTE_SQUEEZE |
| OTM (delta 0.10-0.25) | > 21 | ≥ 75% | any | any | any | CLOSE_FOR_PROFIT | OTM_LONGDTE_HICAPTURE |
| OTM (delta > 0.25, near-ATM) | any | ≥ 40% | any | any | any | CLOSE_FOR_PROFIT | NEAR_ATM_TAKEPROFIT |
| OTM | ≤ 21 | < 50% | < 0.30 | BULLISH+ | NORMAL | ROLL_OUT | OTM_SHORTDTE_BULLISH |
| OTM | ≤ 21 | < 50% | < 0.30 | NEUTRAL | NORMAL | ROLL_OUT | OTM_SHORTDTE_NEUTRAL |
| OTM | ≤ 21 | < 50% | < 0.30 | BEARISH | NORMAL | LET_EXPIRE | OTM_SHORTDTE_BEARISH |
| OTM | ≤ 7 | any | any | any | any | LET_EXPIRE | EXPIRY_WEEK |
| ITM | > 21 | n/a | any | BULLISH+ | NORMAL | WAIT | ITM_LONGDTE_BULLISH |
| ITM | > 21 | n/a | any | NEUTRAL | NORMAL | ROLL_OUT | ITM_LONGDTE_NEUTRAL |
| ITM | > 21 | n/a | any | BEARISH | NORMAL | TAKE_ASSIGNMENT | ITM_LONGDTE_BEARISH |
| ITM | ≤ 21 | n/a | any | BULLISH+ | NORMAL | ROLL_OUT_AND_DOWN | ITM_SHORTDTE_BULLISH |
| ITM | ≤ 21 | n/a | any | NEUTRAL | NORMAL | TAKE_ASSIGNMENT | ITM_SHORTDTE_NEUTRAL |
| ITM | ≤ 21 | n/a | any | BEARISH | NORMAL | TAKE_ASSIGNMENT | ITM_SHORTDTE_BEARISH |

**Defensive roll variant (regime = CAUTION or RISK_OFF):**

When regime is `CAUTION`/`RISK_OFF`, an additional pre-matrix check fires for ITM positions:
- If `current_mid / averageCost ≥ 1.30` AND `DTE ≥ 21` → `DEFENSIVE_ROLL_OUT_AND_DOWN` (matrix cell `CAUTION_DEF_ROLL`)
- (In NORMAL regime the threshold is 1.40 — wheelhouz `position_review.py` lines 567-580.)

### For SHORT CALLs (covered)

Symmetric framework with inverted bullish/bearish handling. The risk side is "called away" rather than "assigned at $X" — same math, different feel.

### Roll target selection

When a roll is recommended, the skill selects a target contract from the chain by:

1. **Filter by expiration:** next monthly ≥ current expiry + 21 days (avoid weekly chop)
2. **Filter by delta — IV-adaptive (from wheelhouz `_pick_put_roll_target` lines 364-417):**
   - If IV rank > 60 (high IV): target delta = 0.16, max delta = 0.22
   - If IV rank ≤ 60 (normal IV): target delta = 0.22, max delta = 0.30
   - These thresholds are user-tunable in `references/wheel_parameters.md`
3. **Filter by liquidity:** open interest ≥ 100, bid-ask spread ≤ 5% of mid
4. **Filter by net credit:** must produce a positive net credit ≥ 10% of original premium after closing the existing contract. Net credit < 10% → drop the recommendation.
5. **Stress test (also from wheelhouz):** compute target's projected loss at underlying -10% and -20%. If risk/reward at -10% exceeds 3.0x, drop the recommendation.
6. **Pick the strike:**
   - `ROLL_OUT`: same strike, later expiration
   - `ROLL_OUT_AND_DOWN`: one strike below, or down to support level if specified in input
   - `ROLL_OUT_AND_UP`: one strike above (covered calls, when underlying rallies past strike)

If no contract satisfies all constraints, the recommendation downgrades to `CLOSE` with the rationale "no acceptable roll target."

## Outputs

Returns structured JSON, never freeform:

```json
{
  "position": "AAPL  260619P00170000",
  "decision": "ROLL_OUT_AND_DOWN",
  "decisionRationale": "ITM (strike $170 vs spot $168.50), DTE 12, BULLISH outlook on underlying, IV percentile 42 (acceptable). Roll out to defer assignment while reducing strike.",
  "matrixCell": "PUT_ITM_DTE21_BULLISH",
  "rollTarget": {
    "optionSymbol": "AAPL  260717P00165000",
    "strike": 165.00,
    "expiration": "2026-07-17",
    "delta": -0.32,
    "bid": 2.85,
    "ask": 3.05,
    "expectedNetCredit": 1.35,
    "expectedNetCreditPct": 18.0
  },
  "warnings": [],
  "nextReviewDate": "2026-05-14"
}
```

The matrix cell ID is critical for debugging: it tells you exactly which row of `references/decision_matrix.md` produced the answer. Same inputs → same matrixCell → same decision, every time.

## Workflow

1. Validate inputs against schema
2. Compute derived state: moneyness, DTE, profit-captured-pct, IV regime
3. Look up earnings + dividend dates in context (or from sub-skills if not provided)
4. Apply decision matrix → get decision tag
5. If decision is a ROLL_*, run roll-target selection
6. Apply guardrails (see below) — these can downgrade or upgrade the decision
7. Return structured output

### Post-matrix guardrails

These run after the matrix and can modify the decision (in addition to the pre-matrix guardrails listed above the matrix):

- **Ex-dividend within 3 days for short call ITM:** flag early-assignment risk; downgrade to `CLOSE_FOR_PROFIT` if profit ≥ 50%.
- **Regime is RISK_OFF and decision is ROLL_OUT_AND_UP on a covered call:** require explicit user confirmation. We don't roll up calls on losers in defensive markets.
- **Stress test (loss at -10%/-20%) fails:** target contract's risk/reward exceeds 3.0x → drop and downgrade to `CLOSE`.

## Reference Files

The skill is **data-driven, not if/else-driven**. Decision logic loads parameters from these reference files at runtime; tuning a threshold means editing the reference, not the code.

- `references/wheel_parameters.md` — **Already drafted** (see `06-wheel-parameters.md`). Loss-stop multipliers, IV-adaptive delta targets, take-profit buckets by moneyness/DTE, defensive-roll thresholds (NORMAL/CAUTION variants), concentration & sizing rules, per-strategy parameters. Includes YAML companion for programmatic loading.
- `references/tail_risk_names.md` — **Already drafted** (see `07-tail-risk-names.md`). Curated lists of Chinese ADRs, binary biotechs, crypto-proxies, high-borrow memes with rationale per category.
- `references/decision_matrix.md` — **Already drafted** (see `08-wheel-decision-matrix.md`). Full cell-by-cell decision table: 8 pre-matrix guardrails + ~40 SHORT PUT cells across NORMAL/CAUTION/RISK_OFF + ~20 SHORT CALL cells + 6 post-matrix guardrails + roll-target selection. Every cell has a stable ID and references parameter names from `wheel_parameters.md` instead of hardcoded numbers. Includes YAML companion for programmatic loading.
- `references/wheel_strategy_basics.md` — primer: cash-secured puts, covered calls, the mechanic. (To be drafted; informational.)
- `references/iv_regimes.md` — how to interpret IV percentile and rank. (To be drafted.)
- `references/roll_target_selection.md` — algorithm details. (To be drafted.)
- `references/expiration_calendar.md` — SPY/QQQ weekly schedules, VIX Wednesdays, monthly 3rd-Friday rules. (To be drafted.)

## Failure Modes

| Failure | Behavior |
|---|---|
| Chain data missing the rolling expiration | Recommendation downgrades to `CLOSE` with rationale "no roll target available" |
| Invalid `outlookTag` | Treat as NEUTRAL and warn in output |
| Profit-capture percent computes negative (current value > average cost) | Position is moving against you; treat as ITM or skip CLOSE_FOR_PROFIT row |
| Earnings date is unknown | Treat as no-earnings; warn in output |

## Tests

The skill ships with a `scripts/tests/test_decision_matrix.py` containing one fixture per matrix cell. CI runs them on every commit. This is the most important test in the repo because consistency between runs depends on this matrix being deterministic.

## Limitations

- Single-leg positions only in v1 (no spreads, condors, butterflies). Wheel positions are single-leg by definition; that's fine.
- LEAPs handled with separate parameters (different DTE thresholds; see `references/wheel_parameters.md`)
- Doesn't optimize across positions — each contract is evaluated independently. Cross-position optimization (e.g., roll order under buying-power constraint) is left to the briefing's aggregator.

```

---

## Notes for the build

**Why this is the highest-leverage new skill.** Every other piece of the briefing is wiring existing skills together. This one is genuinely new functionality, and it's where the user has the most money on the line. Getting it right means the briefing's options recommendations are trustworthy enough to act on. Getting it wrong means the briefing makes good portfolio-level recommendations but its options advice is ignored — which is the worst outcome because the wheel is the strategy that needs the most daily attention.

**Why a decision matrix vs. an LLM in the loop.** Two reasons: consistency (same inputs → same answer, every run) and auditability (the matrix cell ID tells you exactly why a decision was made, which is critical when reviewing postmortems and tuning the framework). The LLM's role shifts to producing the inputs (outlook tag, IV regime, earnings flag) where its judgment is genuinely needed.

**Test fixture per matrix cell.** This is unusual but worth it. Every row of the table gets a fixture: input shape, expected decision tag, expected rationale. Whenever the matrix changes, the test suite catches what specifically changed. This is the kind of thing that makes a skill feel like infrastructure instead of vibes.
