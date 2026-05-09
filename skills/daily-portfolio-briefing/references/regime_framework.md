# Regime Framework

**Status:** Reference v0.1 (copied from docs/09-regime-framework.md)
**Used by:** daily-portfolio-briefing Step 3
**Date:** 2026-05-07

This is the deterministic 11-rule classifier that maps market inputs (VIX, SPY, breadth, calendar) to regime labels: RISK_ON, NORMAL, CAUTION, RISK_OFF.

## Regime Labels

- **RISK_ON** — Broad bull, low VIX, expanding breadth, no catalysts
- **NORMAL** — Typical conditions, neutral breadth, no extremes
- **CAUTION** — Heightened watch, multiple flags, new longs sized down
- **RISK_OFF** — Defensive posture, high VIX/drawdown, new longs suppressed

## Classification Rules (Priority Order)

1. **VIX Extreme High:** `vix_last > 35` → RISK_OFF
2. **SPY Extreme Drop:** `spy_day_change ≤ -8%` → RISK_OFF
3. **SPY Crisis + Weak Breadth:** `spy_day_change ≤ -5% AND breadth == WEAK` → RISK_OFF
4. **VIX Rapid Spike:** `vix_change ≥ 0.25 AND vix_last > 25` → CAUTION
5. **SPY Severe Drop + Weak Breadth:** `spy_day_change ≤ -3% AND breadth == WEAK` → CAUTION
6. **Breadth Deterioration:** `breadth == WEAK AND dist_days ≥ 5` → CAUTION
7. **Distribution Extreme:** `dist_days ≥ 7` → RISK_OFF
8. **Economic Event Cluster:** `economic_events ≥ 2 AND vix > 25` → CAUTION
9. **Earnings Cluster + Weak Breadth:** `earnings_intensity ≥ 5 AND breadth == WEAK` → CAUTION
10. **News Intensity Spike:** `news_intensity ≥ 5 AND vix ≥ 25` → CAUTION
11. **Default:** No rule fires → NORMAL

## Stickiness (Anti-Flipping)

- **Down-stepping (toward less defensive):** Gated by prior-day regime
- **Up-stepping (toward defensive):** Immediate, no gate

## Confidence Scoring

- **HIGH:** All inputs available, clear rule match
- **MEDIUM:** One optional input missing or boundary case
- **LOW:** Multiple inputs missing, conflicting signals
- **DATA_ERROR:** VIX unavailable (abort)

## Downstream Behavior by Regime

| Regime | wheel-roll-advisor | New equities | New short puts | Concentration |
|--------|---|---|---|---|
| RISK_ON | normal 1.40× loss trigger | Enabled | Enabled | 10% per name |
| NORMAL | normal 1.40× | Enabled | Enabled | 10% per name |
| CAUTION | tighter 1.30× | HIGH suppressed | Enabled | 8% per name |
| RISK_OFF | tightest 1.20× or close-only | Suppressed entirely | Enabled | 6% per name |

See docs/09-regime-framework.md for full details.
