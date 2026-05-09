# Integration with Daily Portfolio Briefing

## Overview

The daily-portfolio-briefing consumes the recommendation list output in two places:

1. **Step 4 (Portfolio Review)** — Enrich held positions with external recommendations
2. **Step 6 (New Ideas)** — Surface BUY recommendations on unheld tickers

## Output Consumption Points

### Step 4: Portfolio Review Enrichment

When reviewing each held position, the briefing looks up matching recommendations from `reports/daily/recommendations_YYYY-MM-DD.json`:

#### SELL Recommendation on Held Position

If a recommendation exists with `recommendation: "SELL"` for a held ticker:

- **If position thesis is intact:** Flag in watch panel as informational
  ```
  ⚠ Sell rec from Analyst A (5/1/26): "Valuation stretched, growth slowing"
  ```
  The position's recommendation tag stays HOLD; the sell rec provides external context.

- **If position thesis is weakening:** Bump default recommendation from HOLD to TRIM
  ```
  TRIM: Sell rec from Analyst B aligns with weakening technicals
  ```

#### BUY Recommendation on Held Position (UNDERWEIGHT)

If the position is UNDERWEIGHT (held below target allocation) and a BUY recommendation exists:

- Flag as evidence for ADD/ACCUMULATE
  ```
  ADD: Sell rec from Newsletter C (5/3/26) supports adding on dips
  ```

#### Implementation Notes

- **No automatic action:** Recommendations are informational, not directives
- **Integration via lookup:** Loop through `result["recommendations"]` matching by ticker
- **Source attribution:** Always surface the `source` field (if present) or `raw_recommendation`
- **Date awareness:** Surface the `date_updated` so users know how fresh the recommendation is

### Step 6: New Ideas Generation

Surface BUY recommendations on tickers NOT currently in the portfolio:

#### Filtering

1. Read `reports/daily/recommendations_YYYY-MM-DD.json` (latest by date)
2. Filter to recommendations with `recommendation in ["BUY", "STRONG_BUY"]`
3. Cross-reference against `positions.json` — drop tickers already held
4. Rank by:
   - Strength: STRONG_BUY > BUY
   - Freshness: newer > older
5. Limit to top N (default 5; configurable in `briefing_config.yaml`)

#### Enrichment

For each candidate:

1. Get current price from yfinance
2. Compute price target upside (if `price_target_2026` is populated)
3. Look up IV rank from options chain
4. Check for conflicting signals (earnings within X days, etc.)
5. Size with `position-sizer` skill using user's risk parameters
6. Tag with `source: "recommendation_list"` to distinguish from screener candidates

#### Output Structure

```json
{
  "ticker": "META",
  "recommendation": "BUY",
  "source": "recommendation_list",
  "raw_recommendation": "Top Stock to Buy",
  "rating_tier": 5,
  "date_updated": "2026-04-20",
  "age_days": 17,
  "price_target_2026": [280.0, 310.0],
  "upside_pct": 0.05,
  "entry_price": 295.00,
  "suggested_size": 100,
  "sizing_note": "2% portfolio risk, 1.5% position size"
}
```

## JSON Schema

### Input (Recommendation List Output)

```json
{
  "fetched_at": "2026-05-07T09:32:14-04:00",
  "source": {
    "sheet_id": "12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M",
    "row_count_total": 190,
    "row_count_parsed": 187,
    "row_count_skipped": 3,
    "skipped_reasons": ["#REF! error: 1", "no_ticker: 2"],
    "cached": false,
    "stale": false
  },
  "summary": {
    "buy_count": 120,
    "sell_count": 2,
    "hold_count": 65,
    "aging_count": 8
  },
  "recommendations": [
    {
      "ticker": "AAPL",
      "name": "Apple",
      "recommendation": "BUY",
      "raw_recommendation": "Buy",
      "rating_tier": 3,
      "date_updated": "2026-05-01",
      "age_days": 6,
      "aging": false,
      "price_target_2026": [320.0, 350.0],
      "price_target_2027": null,
      "row_number": 14
    }
  ]
}
```

### Key Fields for Briefing

| Field | Type | Briefing Use |
|---|---|---|
| `ticker` | string | Lookup key, position matching |
| `recommendation` | enum (BUY, SELL, HOLD, STRONG_BUY, WEAK_BUY) | Filter, actionability |
| `raw_recommendation` | string | Display original rating string |
| `rating_tier` | int (0-5) | Ranking, conviction level |
| `date_updated` | ISO date | Freshness assessment |
| `age_days` | int | Show how stale recommendation is |
| `aging` | bool | Flag if > warn_age_days |
| `price_target_2026` | tuple or float | Upside calculation |
| `price_target_2027` | tuple or float | Forward-year target (optional) |

## Filtering & Gating Rules

### New Idea Gating

When surfacing a BUY recommendation in Step 6, the briefing applies the same gates as for screener-generated ideas:

1. **Macro caution gate:** Skip new puts/longs when macro_caution = "high" (unless under certain conditions)
2. **Concentration check:** Verify `existing_pct + new_size ≤ 10%` of portfolio
3. **Earnings guard:** No new longs when next_earnings < 5 days (peak IV crush risk)
4. **Tail-risk gate:** Skip recommendations for names on the tail-risk watchlist (Chinese ADRs, biotech, memes)

See `src/risk/tail_risk.py` for the curated tail-risk list.

### Position Watch Gating

When enriching an existing position's watch with a SELL recommendation:

- **No gate.** SELL recommendations are informational and don't trigger forced action
- The position's original thesis and close logic remain in effect
- SELL recs are displayed as context only

## Caching & Freshness

The recommendation list is cached for 1 hour (configurable). The briefing should:

1. Check if `cached` flag is true
2. If `cached: true` and `cache_age_minutes < 30`, use the cached payload as-is
3. If `cached: true` and `cache_age_minutes ≥ 30`, consider re-fetching
4. If `stale: true`, surface a caution in the briefing ("Sheet data unavailable; using cached copy from X hours ago")

## Error Handling

If the recommendation list fetch fails:

- **Briefing behavior:** Treat as a missing data source, not a fatal error
- Log the error but continue with other analysis
- Surface note: "Recommendation list unavailable (network error). Using prior session's data if available."
- Don't surface new BUY ideas from the list (fall back to screener ideas)
- Still enrich existing positions if cached data is available

If the recommendation list is parsed but contains warnings:

- **Aging recommendations:** Surface with age flag (e.g., "17 days old") in the briefing
- **Duplicate tickers:** List both recommendations; let users decide which to act on
- **Unknown ratings:** Log as warning; treat as HOLD
- **Missing price targets:** List recommendation but note "price target not available"

## Example: Enriching a HOLD Position

Scenario: Apple is held, thesis intact. A SELL recommendation arrives from an analyst.

**Briefing output:**

```
AAPL: HOLD
├─ Reason: Thesis intact, earnings 30 days out
├─ Recommendation sources:
│  ├─ Internal: HOLD (technicals stable)
│  └─ External: 🔴 SELL from Analyst A (5/1/26) — "Valuation stretched, growth slowing"
├─ Position metrics:
│  ├─ Gain: +12.5% from cost basis
│  ├─ Win rate thesis: Intact
└─ Next action: Monitor; close if earnings miss < -8%
```

The SELL recommendation is visible but doesn't override the HOLD tag. Users can choose to respect the external view or trust their thesis.

## Example: New STRONG_BUY Idea

Scenario: META is not held. A STRONG_BUY recommendation arrives.

**Briefing output:**

```
NEW IDEA: META (Meta Platforms)
├─ Source: recommendation_list (Top Stock to Buy)
├─ Rating: STRONG_BUY (tier 5) — only 1 name at this conviction
├─ Updated: 4/20/26 (17 days old)
├─ Price target: $280-$310 (5% upside from $295)
├─ Suggested entry: $295 (recent support)
├─ Sizing: 100 shares ($29.5K, 3% portfolio risk)
└─ Risks:
   ├─ Aging (17 days old — verify thesis still valid)
   ├─ IV rank elevated (83%tile) — consider smaller size
   └─ Earnings: 4/30/26 (23 days) — watch for pre-earnings momentum
```

The briefing provides all context needed to decide: act now, defer, or skip.

## Limitations & Future Work

### Current Limitations

- **Single sheet only.** Multi-sheet aggregation planned for v2.
- **Read-only.** No write-back to the sheet (e.g., "acted on this recommendation").
- **No source weighting.** All sources treated equally; downstream analysis could weight by historical accuracy.
- **No conflict resolution.** Conflicting recommendations (BUY vs SELL on same ticker) are both surfaced; user decides.

### Future Enhancements

- **Source reputation tracking.** Track win rates by analyst/source; weight recommendations accordingly.
- **Feedback loop.** Mark which recommendations led to executed trades; feed back into analyst scoring.
- **Multi-sheet aggregation.** Combine multiple recommendation sheets (different analysts, newsletters).
- **Alert escalation.** STRONG_BUY from high-accuracy source triggers immediate alert (not just briefing mention).
- **Directive integration.** Respect user directives (e.g., "suppress BABA recommendations").
