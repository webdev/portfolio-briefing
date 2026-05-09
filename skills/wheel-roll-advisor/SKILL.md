---
name: wheel-roll-advisor
description: Deterministic decision engine for wheel-strategy options positions. Accepts position state + chain + context, returns structured recommendation (CLOSE, ROLL_OUT, TAKE_ASSIGNMENT, etc.) with matrix-cell ID for auditability.
---

# Wheel-Roll-Advisor Skill

Transforms an open options position (short put, covered call, collar) into one of seven deterministic recommendations via a decision matrix lookup. Applies pre-matrix guardrails (loss stop, earnings, tail risk), derives state variables from raw data, walks the decision matrix, and if a ROLL decision is returned, selects a specific roll target via a 6-step filter pipeline.

## When to Use

Use this skill when:
- You have an open short put, covered call, or collar with unknown action
- You need a deterministic, auditable decision (matrix-cell ID for replay)
- You want safety guardrails (loss stops, earnings checks, tail-risk overrides) applied automatically
- Position is under active management (not buy-and-hold)
- You want to roll defensively with specific target selection criteria

## How It Works

### 1. Input Schema

Accepts JSON with position, underlying, context, and chain data.

### 2. State Derivation

The skill derives 10 state variables:
- Moneyness, DTE band, IV regime, profit %, outlook, regime, delta, DTE, current mid, entry price

### 3. Pre-Matrix Guardrails

Safety checks that fire BEFORE matrix lookup:
- Loss Stop (2.0x monthly, 1.5x weekly)
- Crash Stop (intraday drop >15%)
- Earnings Imminent (within 7 days + DTE ≤ 30)
- Open Order
- Tail Risk (override rolls to CLOSE)

### 4. Decision Matrix

~40 cells indexed by state variables. Returns one of seven decisions:
- CLOSE, CLOSE_FOR_PROFIT, WAIT, LET_EXPIRE, TAKE_ASSIGNMENT, ROLL_OUT, ROLL_OUT_AND_DOWN, ROLL_OUT_AND_UP, GTC_LIMIT_75

### 5. Roll Target Selection (6-Step Pipeline)

When ROLL decision returned:
1. Expiration filter (≥ 21 DTE from current)
2. Delta (IV-adaptive: 0.16 high IV vs 0.22 normal)
3. Liquidity (OI ≥ 100, spread ≤ 5%)
4. Net Credit (≥ 10% original or ≥ $0.25)
5. Stress Test (loss at -10% ≤ 3.0x premium)
6. Strike Selection (same/down/up)

### 6. Output

Structured JSON with decision, matrixCell ID (for auditability), rationale, roll target if applicable, warnings, next review date, and derived state.

## CLI Usage

```bash
python3 scripts/advise.py --input position.json --output decision.json
```

## References

- wheel_parameters.yaml: Tunable thresholds
- decision_matrix.yaml: ~40 decision cells
- tail_risk_names.yaml: Conservative treatment lists
