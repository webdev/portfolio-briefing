---
name: recommendation-list-fetcher
description: Pull stock buy/sell/hold recommendations from a Google Sheets list, normalize into structured records, and emit JSON for downstream consumption by the daily-portfolio-briefing. Supports config-driven column mapping so any sheet structure is parseable. Use when the user references "the recommendation list", "the stock list", "my picks sheet", or when the daily briefing's pre-flight pulls third-party recommendations.
---

# Recommendation List Fetcher

## Overview

This skill is an input adapter. It connects to a specific Google Sheet, parses recommendation rows into structured records, and produces a JSON output that the daily-portfolio-briefing consumes in Steps 4 (portfolio review enrichment) and 6 (new ideas generation). The skill does **not** make recommendations of its own — it surfaces what the source sheet says.

## When to Use

Trigger this skill:
- "Fetch my recommendation list"
- "Pull my picks from the spreadsheet"
- "Load the stock list"
- During daily briefing pre-flight, as a pre-Step 2 fetch

## Prerequisites

- **Python 3.10+** with `httpx` (for fetching) and `yfinance` (for ticker resolution)
- **Network access to `docs.google.com`** — the gviz CSV endpoint is public and does not require auth
- **Sheet-link permission set to "anyone with the link can view"** — already true for the user's sheet at time of writing
- **`config/recommendation_list_config.yaml` populated** with the URL, ticker overrides, and rating tiers (see Configuration section)

## Workflow

### Step 1: Load config and verify prerequisites
1. Read `config/recommendation_list_config.yaml`. If not found, abort with clear instructions on where to create it.
2. Log the sheet ID and expected data range for clarity.

### Step 2: Check cache
1. If cache exists at `cache_path` and its age is less than `cache_for_minutes`, return cached payload immediately.
2. Include `cached: true` and `cache_age_minutes: X` in the output for transparency.

### Step 3: Fetch sheet contents via public gviz endpoint
1. Fetch the CSV from the configured URL via httpx (no authentication required).
2. Handle network errors gracefully:
   - If cache exists and is stale: return cached payload with `stale: true` flag and a warning message
   - If no cache: abort with a clear message
3. Parse the response into rows. Validate that row count is > 1 (header + data) and that data starts at the configured row.

### Step 4: Parse rows into normalized records

For each row at or after `data_starts_at_row`:

1. Extract values from columns per `column_mapping`.
2. **Normalize recommendation rating:**
   - Check against `rating_tiers` mapping (case-insensitive, strip whitespace)
   - If no match, tag as unknown and default to tier 1 (Hold)
3. **Resolve ticker:**
   - Check `manual_overrides` first
   - Check persistent cache in `state/cache/ticker_map.json`
   - Fall back to yfinance search
   - Store resolved mapping in cache for next run
4. **Parse date:**
   - Support multiple date formats (%Y-%m-%d, %m/%d/%Y, %m/%d/%y)
   - Compute age in days: `today - date`
   - Tag as `aging: true` if age >= `warn_age_days`
5. **Parse price targets:**
   - Handle ranges like "320-350" or single values "300"
   - Drop if unparseable
   - Return as tuple or single Decimal
6. **Apply age filter:**
   - If age > `max_age_days`: skip row and record reason (archived)
7. **Output:** structured record conforming to Output Format below

### Step 5: Emit structured output

Write to `reports/daily/recommendations_YYYY-MM-DD.json` with exact structure specified below.

Also write the recommendations to cache: `state/cache/recommendation_list.json` with `cached_at` timestamp for next-run cache-age calculation.

## Output Format

Emit JSON conforming to this exact structure:

```json
{
  "fetched_at": "2026-05-07T09:32:14-04:00",
  "source": {
    "sheet_id": "12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M",
    "row_count_total": 190,
    "row_count_parsed": 187,
    "row_count_skipped": 3,
    "skipped_reasons": ["#REF! error: 1", "unparseable_date: 2"],
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
    },
    {
      "ticker": "META",
      "name": "Meta Platforms",
      "recommendation": "STRONG_BUY",
      "raw_recommendation": "Top Stock to Buy",
      "rating_tier": 5,
      "date_updated": "2026-04-20",
      "age_days": 17,
      "aging": true,
      "price_target_2026": [280.0, 310.0],
      "price_target_2027": [350.0, 400.0],
      "row_number": 5
    }
  ]
}
```

## Configuration

Create `config/recommendation_list_config.yaml` with these fields:

```yaml
source:
  url: "https://docs.google.com/spreadsheets/d/12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M/gviz/tq?tqx=out:csv"
  sheet_id: "12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M"
  data_starts_at_row: 2

column_mapping:
  name: A
  recommendation: B
  date_updated: C
  price_target_2026: D
  price_target_2026_asof: E
  price_target_2027: F
  price_target_2027_asof: G

ticker_resolution:
  manual_overrides:
    "Alphabet": GOOG
    "Meta Platforms": META
    "Taiwan Semi": TSM
  cache_path: "state/cache/ticker_map.json"
  use_yfinance_fallback: true

normalization:
  rating_tiers:
    "Top Stock to Buy": 5
    "Top 15 Stock": 4
    "Buy": 3
    "Borderline Buy": 2
    "Hold/ Market Perform": 1
    "Sell": 0
  
  tier_to_recommendation:
    5: STRONG_BUY
    4: BUY
    3: BUY
    2: WEAK_BUY
    1: HOLD
    0: SELL
  
  data_hygiene:
    strip_whitespace_all_fields: true
    drop_ref_errors: true
    accepted_date_formats:
      - "%m/%d/%Y"
      - "%m/%d/%y"
      - "%Y-%m-%d"

freshness:
  max_age_days: 30
  warn_age_days: 14

caching:
  cache_for_minutes: 60
  cache_path: "state/cache/recommendation_list.json"
```

## Error Handling

| Error | Behavior |
|---|---|
| Config file not found | Abort with instructions on where to create `config/recommendation_list_config.yaml` |
| Sheet URL not reachable | Return stale cache if available; otherwise abort with "retry in a few minutes" message |
| Sheet is empty | Abort and suggest adjusting `data_starts_at_row` in config |
| Row has missing ticker | Skip silently; increment skip count in summary |
| Date field unparseable | Treat date as today; flag in summary |
| Multiple rows match same ticker | No dedup; allow duplicates |

## Integration with daily-portfolio-briefing

The briefing consumes this data in two places:

### Step 4 (portfolio review) — Enrich held-position watch
For each held position, the briefing looks up matching recommendations:
- **SELL rec on a held position** → flag in watch panel
- **BUY rec on a held position UNDERWEIGHT** → provide as supporting evidence for ADD

### Step 6 (new ideas) — Surface BUY recs on unheld tickers
The briefing surfaces top BUY recommendations on tickers NOT currently held:
- Filter to `recommendation: "BUY"` or `"STRONG_BUY"`
- Drop tickers already in portfolio
- Rank by freshness (newer > older)
- Size each candidate with `position-sizer` using user's risk parameters

## Output Files

```
reports/daily/
  recommendations_YYYY-MM-DD.json     # canonical output

state/cache/
  recommendation_list.json            # cache payload with timestamp
  ticker_map.json                     # persistent name→ticker cache
```

## CLI / Manual Invocation

```bash
# Fetch recommendations and emit to reports/daily/
python3 scripts/fetch_recommendations.py \
  --config config/recommendation_list_config.yaml \
  --output reports/daily/recommendations_$(date +%F).json

# Force refresh ignoring cache
python3 scripts/fetch_recommendations.py --force-refresh

# Dry run (parse but don't write)
python3 scripts/fetch_recommendations.py --dry-run

# Validate config only
python3 scripts/fetch_recommendations.py --validate-config
```

## Limitations

- **Single sheet only.** Multi-sheet aggregation is v2.
- **Read-only.** Does not write back to the sheet.
- **No price-target validation.** Downstream validation happens in the briefing.
- **No duplicate deduplication.** Same ticker can appear twice if from different sources.

## Disclaimers

This skill surfaces **recommendations provided by third parties or captured by the user**, not recommendations generated by Claude. The skill does **not** endorse, validate, or independently analyze the recommendations. It is an input adapter only.

Users remain solely responsible for evaluating recommendations before acting on them. All investment decisions carry risk and are the user's responsibility.
