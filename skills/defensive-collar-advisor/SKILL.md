---
name: defensive-collar-advisor
description: Propose defensive collars (3-leg structures — buy-to-close existing call, sell-to-open higher call, buy long put) for core long positions with significant unrealized gains. Triggered when a position is concentration-breaching, has an existing covered call, and would generate a large tax bill if assigned.
version: 1.0
---

# Defensive Collar Advisor

A collar (long stock + short call + long put) caps both upside and downside on a position. For core long-term holdings with embedded gains, this is the right structure when:
- Forced assignment would trigger a large taxable event
- Concentration is over the per-name cap
- The trader is willing to give up some upside for downside protection

This skill identifies which positions qualify and proposes the specific 3-leg trade.

## When to use

Triggered automatically by the daily briefing for any position meeting ALL the following:
1. Position is classified as `core` (configured per-ticker, OR detected via `unrealized_gain_pct > 30% AND in_taxable_account`)
2. Position has an existing short call (covered call already in place)
3. Concentration > 10% of NLV (breach)
4. Embedded tax cost on assignment > 2% of NLV (material)

When triggered, this skill produces a `CollarProposal` with all three legs priced from live chain data, plus the yield/cost summary computed via the `yield-calculator` skill.

## Decision matrix

See `references/collar_decision_matrix.yaml` for full rules. Key decisions:

### Whether to add a put leg
- `put_iv_rank >= 70` → puts expensive → SKIP put leg, just propose roll-up (no collar)
- `put_iv_rank in [30, 70]` → puts reasonably priced → propose collar
- `put_iv_rank < 30` → puts very cheap → MUST propose collar (great insurance value)

### New call strike
- Default: `max(spot × 1.15, current_call_strike + 1 strike interval)` — at least 15% OTM
- For aggressive bullish: `spot × 1.20-1.25`
- Never below current call strike (would reduce headroom — pointless)

### Put strike
- Default: `spot × 0.90` (10% OTM put)
- For high IV environments: `spot × 0.85` (15% OTM, cheaper insurance)
- For earnings windows: `spot × 0.92` (5-8% OTM, more sensitive)

### Expiration matching
- Both legs SAME expiration if possible (matching legs simplifies management)
- Target DTE: 60-180 days
- Avoid earnings within DTE window if possible

## Output format

```python
{
    "qualified": True,
    "ticker": "NVDA",
    "trigger_reasons": ["core", "concentration_breach", "high_tax_exposure"],
    "current_call": {"strike": 245, "expiration": "2026-12-18", "mid": 20.05},
    "proposed_legs": [
        {"action": "BTC", "type": "CALL", "strike": 245, "exp": "2026-12-18", "limit": 20.45},
        {"action": "STO", "type": "CALL", "strike": 295, "exp": "2027-01-15", "limit": 10.62},
        {"action": "BTO", "type": "PUT", "strike": 200, "exp": "2027-01-15", "limit": 8.50},
    ],
    "net_cash": -10500,  # debit
    "tax_avoided_if_no_assignment": 25160,
    "max_loss": -10500,  # the put premium + debit
    "max_gain_at_cap": 50000 + 10000 - 10500,
    "yield_summary": {...},  # from yield-calculator
}
```

## Why this is a separate skill, not an if/else

- The decision matrix has many threshold parameters (IV rank, OTM %, DTE bands) that benefit from being declarative YAML, not buried in code
- The 3-leg structure is reusable: future "earnings collar," "merger arb collar," etc. can extend
- Other skills can call this advisor when they need to propose a collar (not just the daily briefing)
