---
name: thematic-scout
description: Research engine that runs the full briefing-pipeline analysis stack (yfinance technicals + E*TRADE chains + third-party recommendations + earnings calendar) across a curated set of thematic universes (semis/AI, memory, nuclear, powergrid/infra, optical AI, quantum, cybersecurity, plus user-defined). For each ticker, produces a BUY / WATCH / AVOID verdict with entry strategy (shares vs CSP) and concrete order ticket. Output is a thematic research report — separate from the daily briefing's "what to do today on existing positions" focus, this skill answers "what should I be aware of in these spaces."
version: 1.0
---

# Thematic Scout

The daily briefing manages what you already own. This skill goes outbound — researching candidate names across themes you've flagged as interesting (semis/AI, nuclear energy, memory, powergrid/infra, optical AI, quantum, cybersecurity). For each candidate it runs the same analysis stack we use for held positions: yfinance for RSI/IV-rank/200-SMA/drawdown, the etrade-chain-fetcher for real CSP entry prices, third-party recommendations for rec tier, and the earnings calendar for binary-event guards.

## Inputs

```yaml
# references/theme_universes.yaml — extend by editing this file
themes:
  semis_ai:
    name: "Semiconductors & AI"
    anchors: [NVDA, AMD, AVGO, TSM, ARM, ASML]
    etfs: [SMH, SOXX]
    notes: "Anchor: Claude, GPT, hyperscaler capex cycle"
  memory:
    name: "Memory"
    anchors: [MU, WDC, SNDK, STX]
  nuclear:
    name: "Nuclear Energy"
    anchors: [CCJ, BWXT, NRG, VST, CEG, OKLO, SMR, NNE]
  powergrid_infra:
    name: "Powergrid & Infrastructure"
    anchors: [VRT, GEV, ETN, EMR, ABB, HUBB, PWR]
  optical_ai:
    name: "Optical AI / Networking"
    anchors: [COHR, LITE, FN, AAOI, NVDA]
  quantum:
    name: "Quantum Computing"
    anchors: [IONQ, RGTI, QBTS, IBM]
  cybersecurity:
    name: "Cybersecurity"
    anchors: [CRWD, PANW, ZS, S, NET, OKTA, FTNT]
```

## Per-ticker analysis (same stack as daily briefing)

For each anchor ticker the scout pulls:

| Source | Signal |
|---|---|
| yfinance | RSI(14), IV rank (252-day vol percentile), 200-SMA, drawdown from 52w high, spot, 5-day return |
| earnings calendar | Next earnings date |
| recommendation-list-fetcher | Third-party tier (BUY/HOLD/SELL) if available |
| **etrade-chain-fetcher** | Real bid/mid/ask for entry CSP at ~10% OTM, 30-45 DTE |

## Verdict per ticker

Same decision tree as long-term-opportunity-advisor's ADD logic, extended:

| Condition | Verdict |
|---|---|
| Third-party SELL, OR drawdown > 30% without rec support | **AVOID** |
| RSI > 80 + 200-SMA breach pending | **AVOID — overheated** |
| Third-party BUY + RSI < 35 + drawdown > 10% | **BUY (oversold pullback)** |
| Third-party BUY + price within 5% of 200-SMA | **BUY (support test)** |
| Third-party BUY + IV rank > 50 + chain has GOOD-EV CSP at 10% OTM | **CSP ENTRY** |
| Third-party BUY/HOLD + neutral technicals | **WATCH** |
| Otherwise | **HOLD / NEUTRAL** |

For BUY and CSP ENTRY verdicts, output includes a concrete order ticket using the **etrade-chain-fetcher** for real chain prices — same rule as the daily briefing: never yfinance for tradeable chain data.

## Output

A markdown report grouped by theme:

```markdown
# Thematic Scout Report — 2026-05-11

## 🔭 Semiconductors & AI

### ✅ BUY · NVDA · $215.20
- RSI 52 (neutral), IV rank 41, drawdown 4% from 52w high
- 3rd-party: BUY (tier 4, 1d fresh)
- Earnings: 2026-08-26 (108d away)
- **Strategy:** Already at decent levels; consider CSP at $195 strike (10% OTM)
  via 35-DTE Fri Jun 12. Live mid $4.20 (~6% annualized).
- **Why:** ...

### 👀 WATCH · AMD · $463.65
...

## 🔭 Memory
### ✅ BUY · MU · $797.88
...
```

## When to run

This skill is **research**, not daily trading. Run it:
- Weekly (e.g., Saturday morning) to refresh the watchlist
- Whenever you want to widen the scope beyond held positions
- After a major catalyst in a theme (earnings season, FOMC, geopolitical event)

Scheduled via Cowork as `thematic-scout-weekly`. Output lands at
`~/Documents/briefings/scout_YYYY-MM-DD.md`.

## What it does NOT do

- Place orders. Like the daily briefing, it's read-only.
- Replace your own thesis. The verdicts are mechanical — they say "the math is OK / not OK", not "this is a good business."
- Predict direction. RSI extremes and rec changes are conditions, not forecasts.

## Why a separate skill

- Different cadence than the daily briefing (weekly research vs every-morning portfolio management)
- Different universe (outbound research vs held positions)
- Different output (thematic report vs portfolio action list)
- Same analysis primitives though — reuses `etrade-chain-fetcher`, the technicals fetcher pattern from `snapshot_inputs.py`, and the verdict logic from `long-term-opportunity-advisor`

## Hard rules

1. **Chain data for any concrete CSP entry MUST come from `etrade-chain-fetcher`.** No yfinance for tradeable prices.
2. **Fail closed.** If E*TRADE is unreachable, the report renders without entry tickets and flags "broker unreachable — verify before placing."
3. **Earnings guard.** No CSP entry tickets when next earnings is inside the contract's life.
4. **Concentration awareness** (optional, when run against the live portfolio): if the user already holds 10%+ NLV in a name, the verdict downgrades to WATCH (don't add).
