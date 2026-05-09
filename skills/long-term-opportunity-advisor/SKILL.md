---
name: long-term-opportunity-advisor
description: Surfaces long-term equity buy/trim/exit recommendations and multi-month option trade ideas (LEAPs, 60-180 DTE CSPs, calendar spreads, dividend captures). Combines third-party recommendation ratings, RSI, IV rank, drawdown from 52-week highs, and 200-day SMA position. Fills the gap left by the income-focused short-dated wheel briefing.
version: 1.0
---

# Long-Term Opportunity Advisor

The daily briefing is income-focused: short DTE wheel premium. This skill is the **patient capital** counterpart — it answers "what should I buy, trim, or exit on a 3–12 month horizon?" plus "what longer-dated options trades are attractive right now?"

## Equity actions emitted

- 📈 **ADD** — third-party rec is BUY + RSI < 35 (oversold) + price within 5% of 200-SMA OR drawdown > 10% from 52-week high
- ✂️ **TRIM** — overweight > target × 1.5 + RSI > 70 (overbought) OR third-party rec downgraded
- 🚪 **EXIT** — third-party rec hit SELL OR thesis broken (configurable: large drawdown without rec support)
- 🤝 **HOLD** — neutral, may include a "watch for X" note

## Long-term options ideas

- **LEAP CALL** — high-conviction holding (third-party BUY) + low IV (rank < 30) + price near 200-SMA → buy 6–18 month calls as cheap upside replacement for cash-heavy users
- **LONG-DATED CSP** — willing-to-acquire names with elevated IV (rank > 50) + 60–90 DTE → fat premium with assignment-friendly horizon
- **DIAGONAL CALENDAR** — names with normal IV + sideways outlook → sell short-dated, buy longer-dated
- **DIVIDEND CAPTURE** — long-stock + sell-call IF holding through ex-div date for qualified treatment + premium

## Inputs

- **Live positions** (E*TRADE) — current weights, P&L, basis
- **Third-party rec** (recommendation-list-fetcher → Kanchi sheet) — BUY/HOLD/SELL + tier
- **Technicals** (yfinance) — RSI(14), 50/200 SMA, 52-week high, drawdown%
- **IV rank** (yfinance 252-day historical vol)
- **Target allocation** (config-driven per ticker — your "ideal" % NLV)

## Decision matrix

```yaml
# references/long_term_decision_matrix.yaml

equity_signals:
  add:
    require_third_party_rec_in: [BUY, STRONG_BUY, OUTPERFORM]
    require_rsi_below: 35           # OR
    require_drawdown_pct_above: 10  # from 52w high
    max_current_weight_pct: 6       # don't add to already-heavy positions
  trim:
    third_party_downgrade: true     # OR
    weight_over_target_multiplier: 1.5
    rsi_above_for_extended: 70      # combined with weight rule
  exit:
    require_third_party_rec_in: [SELL, UNDERPERFORM]
    OR_drawdown_above: 30           # 30% drawdown without rec support
    OR_thesis_broken: true          # config-driven flag

options_signals:
  leap_call:
    require_third_party_rec_in: [BUY, STRONG_BUY]
    require_iv_rank_below: 30       # cheap LEAPs
    require_within_pct_of_200sma: 5
    suggested_dte: 365
    suggested_delta: 0.70           # deep ITM = stock replacement
  long_dated_csp:
    require_third_party_rec_in: [BUY, HOLD]
    require_iv_rank_above: 50
    suggested_dte: 75
    suggested_delta: -0.20
  dividend_capture:
    require_dividend_yield_above_pct: 2.0
    require_ex_div_within_days: 30
```

## Outputs

Each opportunity is a structured dict the briefing renderer formats inline:

```python
{
  "kind": "ADD" | "TRIM" | "EXIT" | "HOLD" | "LEAP_CALL" | "LONG_DATED_CSP" | "DIAGONAL" | "DIVIDEND",
  "ticker": str,
  "trigger_reasons": [str],
  "concrete_trade": str,           # e.g., "BUY 50 shares ~$397"; "BTO 1× $300C Jan 2027"
  "rationale": str,
  "yield_or_cost": str,            # "if assigned, 14% on cost basis"
  "source": str,                   # which inputs drove this
}
```

## Why a separate skill?

The wheel-roll-advisor handles short-dated income trades. The defensive-collar-advisor handles risk on existing core positions. This one fills the missing third pillar: **what to buy, trim, or exit at the equity level over a 3–12 month horizon**, plus the long-dated option strategies that match that timeframe. Different inputs, different decision rules, different recommended action types.
