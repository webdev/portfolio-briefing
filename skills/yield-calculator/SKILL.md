---
name: yield-calculator
description: Compute standardized annualized yields for every options trade type — short puts, covered calls, calendar/diagonal rolls, collars, hedges, and early closes. Single source of truth so every recommendation surfaces "% return on capital" alongside dollar amounts.
version: 1.0
---

# Yield Calculator

This skill defines how a "yield" is computed for every options trade the briefing surfaces. Without it, dollar amounts are ambiguous: $2,304 of credit on a 4-contract roll might be a fantastic 18% annualized return or a mediocre 4% — depending on collateral and duration.

## When to use

Whenever a briefing action item surfaces a trade (CSP, covered call, roll, collar leg, hedge, close), this skill is consulted to produce the `yield` block alongside the dollar P&L.

## Yield definitions

The skill exposes one function per trade archetype. Each returns a `YieldResult` with named fields the caller can format.

### Short put (cash-secured)
- `static_yield_pct` = premium / collateral × 365 / dte
- `if_assigned_basis` = strike − premium per share (effective cost basis if assigned)
- `if_assigned_yield_pct` = premium / (strike − premium) × 365 / dte (return on actual capital at risk)

### Covered call
- `static_yield_pct` = premium / (spot × 100) × 365 / dte (yield on stock value)
- `if_called_yield_pct` = (premium + strike − spot) / spot × 365 / dte (yield if assigned away)
- `assignment_probability_proxy` = delta (call delta ≈ probability ITM)

### Calendar roll (same strike, longer date)
- `new_leg_yield_pct` = new_premium / (new_strike × 100 × contracts) × 365 / new_dte (the going-forward yield)
- `net_cash_yield_pct` = net_credit / position_value × 365 / total_holding_days
- Caller should also display: assignment-probability change (delta-based), tax exposure on ASSUMED assignment

### Diagonal roll (different strike, may be different date)
- Same as calendar plus:
- `cap_buffer_change_pct` = (new_strike / spot − 1) × 100 (new headroom %)
- `cost_of_protection_per_share` = net_debit ÷ (new_strike − old_strike) (debit cost per dollar of new headroom)

### Collar (long put + existing covered call)
- `combined_static_yield_pct` = (call_premium − put_premium) / position_value × 365 / dte
- `protection_floor_pct` = (spot − put_strike) / spot × 100 (max downside loss % before put pays)
- `cap_ceiling_pct` = (call_strike − spot) / spot × 100 (max upside before call assignment)
- `insurance_cost_pct_of_value` = put_premium / position_value × 100 (annualized if needed)

### Hedge (standalone long puts on index)
- `protection_ratio` = protected_notional / put_cost (effective leverage on tail event)
- `cost_pct_nlv` = put_cost / nlv × 100
- `effective_strike_otm_pct` = (spot − strike) / spot × 100

### Early close (taking profit before expiration)
- `realized_pct_of_max` = realized_profit / original_premium × 100
- `annualized_capture_pct` = realized_profit / collateral × 365 / days_held

## Formula provenance

All yields are computed on capital actually at risk. For naked-short-style positions (CSP), capital is the strike × 100 (the cash that gets withdrawn on assignment). For covered calls, capital is the share market value (spot × 100). The annualization factor is 365 (calendar days), not 252 (trading days), so yields are comparable to bond/treasury yields.

## Formatting conventions

When the briefing renders these yields:
- Always show as percentages with one decimal: `7.1%` not `0.071` or `7%`
- Always indicate annualization: `7.1% ann.` or `(annualized)`
- For point-in-time yields (one-time cost): `2.4% one-time` or `2.4% (paid)`
- For multi-leg trades (rolls, collars): show both the new-leg yield AND the net-cash yield, labeled distinctly

## Outputs

Every `compute_*_yield` function returns a dict with:
- `kind`: trade archetype (cc, csp, calendar_roll, diagonal_roll, collar, hedge, close)
- `headline_yield_pct`: the single best-summary yield (the one to put in a one-line action heading)
- `all_yields`: dict of every yield variant computed
- `notes`: free-form caveats (e.g. "yield assumes OTM expiration; if assigned, see if_called_yield")

This skill never recommends a trade — it only computes yields for one the caller has already chosen.
