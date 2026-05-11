---
name: etrade-chain-fetcher
description: Canonical, E*TRADE-only access to option chain data — bid, mid, ask, Greeks, IV, open interest, expirations, and strike lists. The user trades at E*TRADE; every actionable recommendation in the briefing must be backed by chain data the broker actually has, not a delayed third-party feed. This skill is the single source of truth — no other code path should fetch chains for trading purposes from yfinance or other sources.
version: 1.0
---

# E*TRADE Chain Fetcher

The user places trades at E*TRADE. Recommendations must match the chain the broker actually shows. yfinance chains are commonly 15 minutes delayed, sometimes have stale or missing strikes, and their bid/mid/ask doesn't always reconcile with what E*TRADE quotes. Using them for trade tickets means the user pastes a strike or limit that doesn't exist or won't fill — both of which have happened.

This skill is the **canonical chain access layer**. Every code path that needs a tradeable chain price MUST go through it.

## Surface

```python
from etrade_chain_fetcher import (
    list_expirations,        # all listed expirations for an underlying
    get_chain,                # full chain for one (underlying, expiration)
    find_strike_near_delta,   # closest strike to target delta + DTE window
    find_strike_at_otm_pct,   # closest strike at N% OTM
    quote_contract,           # single-contract bid/mid/ask/Greeks lookup
    ChainSource,              # enum of where the data came from
)
```

All functions return None on failure. The caller MUST treat None as "data unavailable — do not surface this recommendation." Fail-closed; never substitute yfinance.

## Rules of use

1. **Never use yfinance for tradeable chain data.** yfinance is only allowed for non-tradeable signals: 252-day historical-vol-based IV rank approximation, RSI(14), 200-SMA, drawdown from 52-week high, earnings dates. If you're computing a strike, limit price, or filling in a chain quote on an action ticket, you MUST use this skill.

2. **Fail closed on missing chain.** If `get_chain()` returns None, the calling skill must:
   - NOT fabricate a strike or limit price
   - Surface the recommendation as "chain unavailable — verify at broker before placing" OR suppress it entirely
   - Set `chain_source = "missing"` in the action's provenance

3. **No mid-from-bid-ask hacks.** E*TRADE returns bid/mid/ask directly. Don't compute `mid = (bid + ask) / 2` somewhere else — use the broker's mid.

4. **Cache within a single briefing run.** The orchestrator passes a chain cache through so multiple skills can share one fetch per (underlying, expiration) tuple. Don't refetch.

## When chains are needed

| Caller | Why |
|---|---|
| `wheel-roll-advisor` | Roll candidates need real STO bid/ask + Greeks |
| `defensive-collar-advisor` | Long put leg + short call leg both need broker prices |
| Action-list **PULLBACK CSP** | Real strike near 12% OTM + premium |
| Action-list **WRITE CC** | Real strike near 0.30Δ or 6% OTM + premium |
| Action-list **DEFENSIVE COLLAR** | 3-leg combo with all broker prices |
| `long-term-opportunity-advisor` LT_CSP | 75-DTE put at 10% OTM — broker price |
| `long-term-opportunity-advisor` LEAP_CALL | 365-DTE deep-ITM call — broker price |
| `briefing-data-verifier` | Confirms `Source: Live E*TRADE chain` marker present |
| `trade-validator` | Bid-ask spread and live mid for EV math |

## When chains are NOT needed (yfinance OK)

- IV rank approximation from 252-day historical vol of the underlying
- RSI(14), 50/200-SMA, drawdown from 52-week high
- Earnings calendar dates
- General quote/price for non-option context
- Backtesting / historical analysis

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ etrade-chain-fetcher (this skill)                       │
│                                                         │
│   list_expirations() → [date, date, ...]                │
│   get_chain(sym, exp, strike_center) → Chain             │
│   find_strike_near_delta(sym, exp, target_delta)         │
│   find_strike_at_otm_pct(sym, exp, otm_pct)              │
│   quote_contract(sym, strike, exp, type) → Quote         │
└────────────────────┬────────────────────────────────────┘
                     │ uses
                     ▼
┌─────────────────────────────────────────────────────────┐
│ scripts/adapters/etrade_market.py (pyetrade adapter)     │
│   - OAuth flow via etrade_auth.py                        │
│   - pyetrade.ETradeMarket.get_option_chains()            │
│   - pyetrade.ETradeMarket.get_option_expire_date()       │
└─────────────────────────────────────────────────────────┘
```

## Migration note

The briefing previously fetched chains via yfinance in `snapshot_inputs.py::_parallel_chain_fetch` and in `long_term_opportunities.py::_enrich_with_live_premiums`. Both must be refactored to use this skill. Any new chain-using code MUST start with this skill — no `yf.Ticker(t).option_chain(exp)` in the briefing pipeline.

If E*TRADE is unavailable (auth dead, network down), the briefing should suppress chain-dependent recommendations with a clear "broker unreachable" panel, not silently fall back to a worse source.
