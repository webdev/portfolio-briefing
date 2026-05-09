---
name: recommendation-list-fetcher
description: Pull stock buy/sell/hold recommendations from a Google Sheets list, normalize into structured records, and emit JSON for downstream consumption by the daily-portfolio-briefing. Supports config-driven column mapping so any sheet structure is parseable. Use when the user references "the recommendation list", "the stock list", "my picks sheet", or when the daily briefing's pre-flight pulls third-party recommendations.
---

# Recommendation List Fetcher

## Overview

This skill is an input adapter. It connects to a specific Google Sheet, parses recommendation rows into structured records, and produces a JSON output that the daily-portfolio-briefing consumes in Steps 4 (portfolio review enrichment) and 6 (new ideas generation). The skill does **not** make recommendations of its own — it surfaces what the source sheet says.

## Source — confirmed structure

**Sheet ID:** `12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M`

**Access mechanism (verified 2026-05-07 against the live sheet):** the sheet is configured "anyone with the link can view," so it is reachable via the public CSV export endpoint without OAuth:

```
https://docs.google.com/spreadsheets/d/<id>/gviz/tq?tqx=out:csv
```

This is the same access path wheelhouz uses (`src/data/shopping_list.py:104-106`). Use it. Do **not** route through the Google Drive MCP — the Drive connector returns `Item not found` on this specific sheet (likely due to a Shared Drive parent), and even when it works it consumes API quota the gviz endpoint doesn't.

**Actual column layout** (verified):

| Column | Field | Example |
|---|---|---|
| A | Name (company, NOT ticker) | "Visa", "Meta Platforms", "Booking Holdings" |
| B | Rating | "Top Stock to Buy", "Top 15 Stock " (trailing space!), "Buy", "Borderline Buy", "Hold/ Market Perform", "Sell" |
| C | Date Updated | "5/6/26" or "5/6/2026" |
| D | 2026 Price Target | "320-350" or "" |
| E | As-of date for D | "3/16/26" |
| F | 2027 Price Target | "885-985" |
| G | As-of date for F | "1/20/26" |
| H | 2028 Price Target | usually empty |
| I | As-of date for H | usually empty |
| J | 2029 Price Target | usually empty |
| K | As-of date for J | usually empty |
| L | 2030 Price Target | sometimes "#REF!" |
| M | As-of date for L | "4/4/24" etc. |
| N-O | empty | — |
| P | footnote text | "*For informational purposes only…" |

Row count at time of writing: ~190 entries. Header is row 1; data starts row 2.

**Confirmed rating tiers** (verbatim, with whitespace quirks preserved):

| Rating string | Numeric tier | Count in current snapshot |
|---|---|---|
| `Top Stock to Buy` | 5 | 1 (META) |
| `Top 15 Stock ` (trailing space) | 4 | 14 |
| `Buy` | 3 | majority |
| `Borderline Buy` | 2 | small handful |
| `Hold/ Market Perform` (space after slash) | 1 | many |
| `Sell` | 0 | 2 (NKLA, TSLA) |

**Edge cases observed in the live data:**
- Trailing spaces in rating strings — must `.strip()` before mapping
- `#REF!` values in some price-target cells — drop these
- Inconsistent year format: some dates use `5/6/26` (2-digit) and some `4/2/2024` (4-digit); handle both
- Names that don't trivially map to tickers: "Alphabet" → GOOG, "Meta Platforms" → META, "Taiwan Semi" → TSM, "Procter & Gamble" → PG, "Eli Lilly" → LLY, "Mercadolibre" → MELI, "Booking Holdings" → BKNG, "Pinduoduo" → PDD, etc.

The skill is robust to quota errors (irrelevant — gviz has no rate limit), supports caching, and is config-driven so the column mapping can be extended without editing code.

## When to Use

Trigger this skill:
- "Fetch my recommendation list"
- "Pull my picks from the spreadsheet"
- "Load the stock list"
- During daily briefing pre-flight, as a pre-Step 2 fetch

## Prerequisites

- **Python 3.10+** with `httpx` (for fetching) and `yfinance` (for ticker resolution)
- **Network access to `docs.google.com`** — the gviz CSV endpoint is public and does not require auth
- **Sheet-link permission set to "anyone with the link can view"** — already true for the user's sheet at time of writing. If a future sheet is private, the gviz endpoint returns HTML instead of CSV; the skill detects this and surfaces a clear setup error.
- **`config/recommendation_list_config.yaml` populated** with the URL, ticker overrides, and rating tiers (see Configuration section)

### First-run validation

Before the briefing trusts this skill's output, run a manual validation:

```bash
python3 scripts/fetch_recommendations.py --validate-access --url "https://docs.google.com/spreadsheets/d/12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M/gviz/tq?tqx=out:csv"
```

Expected: prints first 3 rows, confirms column count and rating distribution, validates against config. If the response body is HTML rather than CSV, the sheet's link-sharing isn't set to public-anyone-with-link.

## Implementation note: port from wheelhouz

This skill's logic already exists in production form at `wheelhouz/src/data/shopping_list.py` (276 lines). It implements: gviz CSV fetch, 24-hour caching, stale-cache fallback on network failure, the rating-tier map (verbatim), the manual ticker overrides (verbatim), yfinance fallback for ticker resolution, persistent name→ticker cache, price-target range parsing ("320-350" → `(Decimal('320'), Decimal('350'))`), date parsing across multiple formats, and a "stale > 7 days" alert. **Port it directly** rather than rewriting. Adjustments needed:

1. Move the `_RATING_TIERS` and `_MANUAL_OVERRIDES` dicts out of code and into `config/recommendation_list_config.yaml` per the Configuration section.
2. Replace `from src.delivery.telegram_bot import send_alert` (Telegram-specific) with the briefing's standard logging.
3. Replace `from src.config.loader import load_trading_params` with a YAML loader for the new config file.
4. Drop the wheelhouz `ShoppingListEntry` dataclass; emit JSON conforming to the Output Format below instead.
5. Add the `tier_to_recommendation` mapping so downstream consumers see canonical enum values (BUY / SELL / HOLD / STRONG_BUY / WEAK_BUY) rather than the source's free-form rating strings.

The functional core (fetch, parse, normalize, ticker-resolve, cache) is identical.

## Configuration

The skill is config-driven. The values below are the **actual config** for the user's sheet, derived from the verified structure above:

```yaml
source:
  # Public CSV export — works without OAuth on link-shared sheets
  url: "https://docs.google.com/spreadsheets/d/12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M/gviz/tq?tqx=out:csv"
  sheet_id: "12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M"  # for logging only
  data_starts_at_row: 2

column_mapping:
  # Real columns. Names are company names — ticker resolved separately.
  name: A                  # company name, NOT ticker (e.g., "Visa", "Meta Platforms")
  recommendation: B        # rating string — see rating_tiers below
  date_updated: C          # last update for the rating
  price_target_2026: D     # range like "320-350" or empty
  price_target_2026_asof: E
  price_target_2027: F
  price_target_2027_asof: G
  # 2028+ columns mostly empty / #REF! errors; ignore for v1
  
ticker_resolution:
  # Strategy: manual_overrides first, then persistent cache, then yfinance lookup
  manual_overrides:
    "Alphabet": GOOG
    "Meta Platforms": META
    "Taiwan Semi": TSM
    "British American Tob.": BTI
    "Eli Lilly": LLY
    "Pinduoduo": PDD
    "Mercadolibre": MELI
    "Booking Holdings": BKNG
    "The Trade Desk": TTD
    "Procter & Gamble": PG
    "Keurig Dr Pepper": KDP
    "Unite Parsel Service": UPS
    "Lumen Tech": LUMN
    "Luminar Tech": LAZR
    "S&P Global": SPGI
    "Corning": GLW
    "Exxon": XOM
    "Cameco": CCJ
    "PACCAR": PCAR
    "Carnival Cruise Line": CCL
    "Royal Caribbean Cruise": RCL
    "Deer": DE
  cache_path: "state/cache/ticker_map.json"
  use_yfinance_fallback: true

normalization:
  # ACTUAL rating tiers — verbatim from the sheet, including whitespace quirks
  # Trim before mapping. Map UNKNOWN ratings to tier 1 (Hold) by default.
  rating_tiers:
    "Top Stock to Buy": 5
    "Top 15 Stock": 4              # NB: source has trailing space — strip first
    "Buy": 3
    "Borderline Buy": 2
    "Hold/ Market Perform": 1      # NB: literal " / " with space after slash
    "Sell": 0
  
  # Map tier → canonical recommendation enum for downstream skills
  tier_to_recommendation:
    5: STRONG_BUY      # "Top Stock to Buy"
    4: BUY             # "Top 15 Stock"
    3: BUY             # "Buy"
    2: WEAK_BUY        # "Borderline Buy"
    1: HOLD            # "Hold/ Market Perform"
    0: SELL            # "Sell"
  
  unknown_rating_handling: "tag_as_UNKNOWN_HOLD"  # warn but don't drop — tier 1 default
  
  # Strip whitespace, uppercase, drop "#REF!" sentinel values
  data_hygiene:
    strip_whitespace_all_fields: true
    drop_ref_errors: true          # skip rows where any field is "#REF!"
    accepted_date_formats:
      - "%m/%d/%Y"
      - "%m/%d/%y"
      - "%Y-%m-%d"
  
  # Date parsing
  date_format: "auto"  # "auto" (try %Y-%m-%d, %m/%d/%Y, etc.), or explicit "%Y-%m-%d"
  assume_year: null    # if date is partial (e.g., "May 1"), assume this year; null = current year
  
freshness:
  max_age_days: 30       # recommendations older than this are archived, not surfaced in briefing
  warn_age_days: 14      # recommendations aged 14+ days are tagged "aging" in the output
  
caching:
  cache_for_minutes: 60  # respect Google Sheets quota; refresh hourly during market hours
  cache_path: "state/cache/recommendation_list.json"
  cache_stale_on_quota_error: true  # return cached payload on quota exceeded (with stale: true flag)
```

## Workflow

### Step 1: Load config and verify prerequisites

1. Read `state/recommendation_list_config.yaml`. If not found, abort with clear instructions on where to create it.
2. Verify Google Drive MCP server is reachable. Try a lightweight call (e.g., list a dummy folder). If unavailable, abort with setup instructions.
3. Log the sheet ID and expected data range for clarity.

### Step 2: Check cache

1. If cache exists at `cache_path` and its age is less than `cache_for_minutes`, return cached payload immediately.
2. Include `cached: true` and `cache_age_minutes: X` in the output for transparency.

### Step 3: Fetch sheet contents via Google Drive MCP

1. Use the Google Drive MCP to read the spreadsheet. The exact tool name may be `mcp__80d780ff-2f1f-4960-b5c3-719c95b7f698__read_file_content` (placeholder; use the actual tool from the MCP server).
2. Handle quota-exceeded errors specifically:
   - If `cache_stale_on_quota_error: true` and cache exists: return cached payload with `stale: true` flag and a warning message
   - If no cache: abort with a clear message "Google Sheets API quota exceeded. Try again in a few minutes, or manually copy the sheet data."
3. Handle access errors: if the sheet ID is not found or the user lacks read access, surface the sheet URL and abort with instructions to verify sharing.
4. Parse the response into rows. Validate that the row count is > 1 (header + data) and that data starts at the configured row.

### Step 4: Parse rows into normalized records

For each row at or after `data_starts_at_row`:

1. Extract values from columns per `column_mapping`.
2. **Normalize ticker:**
   - Apply `ticker_normalization` rules (uppercase, strip whitespace)
   - Validate against `^[A-Z.]{1,5}$` if `reject_invalid: true`
   - If validation fails, skip row and record reason
3. **Normalize recommendation:**
   - Check against `recommendation_synonyms` (case-insensitive)
   - If no match and `unknown_recommendation_handling: "warn_and_skip"`, log warning and skip row
   - If `"tag_as_UNKNOWN"`, keep row but tag `recommendation: "UNKNOWN"` and warn
4. **Parse date:**
   - Use `date_format` to parse (or auto-detect if "auto")
   - Compute age in days: `today - date`
   - Tag as `aging: true` if age >= `warn_age_days`
5. **Handle optional fields:**
   - `conviction`, `price_target`, `notes` may be missing; represent as null
   - Normalize conviction to uppercase if present
6. **Apply age filter:**
   - If age > `max_age_days`: skip row and record reason (archived)
7. **Output:** structured record with fields:
   ```
   {
     "ticker": "AAPL",
     "recommendation": "BUY",
     "raw_recommendation": "Strong Buy",  # preserve original for auditing
     "source": "Analyst A",
     "date": "2026-05-01",
     "age_days": 6,
     "aging": false,
     "conviction": "HIGH",
     "price_target": 220.00,
     "notes": "AI services growth, services margin expansion",
     "row_number": 14
   }
   ```

### Step 5: Apply directive filters

1. Read `state/directives/index.yaml` (the live directive index from daily-portfolio-briefing).
2. For each parsed recommendation:
   - Check for any ACTIVE directive with `target.kind: "symbol"` and `target.symbol: <ticker>` and `type: "SUPPRESS"`
   - If found and `scope: "all"` or `scope: "long_only"` and recommendation is BUY: drop the recommendation entirely
   - Record why: `filtered: true, filter_reason: "SUPPRESS directive <id>"`
3. Output filtered records to a separate "filtered_recommendations" array in the output JSON for debugging.

### Step 6: Emit structured output

Write to `reports/daily/recommendations_YYYY-MM-DD.json`:

```json
{
  "fetched_at": "2026-05-07T09:32:14-04:00",
  "source": {
    "sheet_id": "12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M",
    "sheet_name": "Sheet1",
    "row_count_total": 87,
    "row_count_parsed": 84,
    "row_count_skipped": 3,
    "skipped_reasons": ["missing_ticker: 1", "unknown_recommendation: 2"],
    "cached": false,
    "stale": false
  },
  "summary": {
    "buy_count": 32,
    "sell_count": 5,
    "hold_count": 47,
    "aging_count": 8,
    "unknown_count": 0,
    "by_source": {
      "Analyst A": 25,
      "Newsletter B": 32,
      "User watchlist": 27
    },
    "by_conviction": {
      "HIGH": 18,
      "MEDIUM": 42,
      "LOW": 24
    }
  },
  "recommendations": [
    {
      "ticker": "AAPL",
      "recommendation": "BUY",
      "raw_recommendation": "Strong Buy",
      "source": "Analyst A",
      "date": "2026-05-01",
      "age_days": 6,
      "aging": false,
      "conviction": "HIGH",
      "price_target": 220.00,
      "notes": "AI services growth, services margin expansion",
      "row_number": 14
    },
    ...
  ],
  "filtered_recommendations": [
    {
      "ticker": "BABA",
      "recommendation": "BUY",
      "raw_recommendation": "Buy",
      "source": "Newsletter C",
      "date": "2026-05-05",
      "age_days": 2,
      "aging": false,
      "conviction": "MEDIUM",
      "price_target": null,
      "notes": null,
      "row_number": 42,
      "filtered": true,
      "filter_reason": "SUPPRESS directive dir_20260507_baba_suppress_e7g4"
    }
  ]
}
```

Also write the recommendations to cache: `state/cache/recommendation_list.json` with `cached_at` timestamp for next-run cache-age calculation.

## Integration with daily-portfolio-briefing

The briefing consumes this data in two places:

### Step 1.5 (pre-flight) — Check for SUPPRESS directives
The briefing's directive-loading step reads `state/directives/index.yaml` and evaluates SUPPRESS directives. This skill applies those same directives in Step 5, so recommendations matching SUPPRESS directives never reach the briefing. Redundant filtering is acceptable; it keeps each system honest.

### Step 4 (portfolio review) — Enrich held-position watch
For each held position, the briefing looks up matching recommendations from `recommendations_YYYY-MM-DD.json`:
- **SELL rec on a held position with thesis INTACT** → flag in watch panel: `⚠ Sell rec from <source> on <date>: "<notes>"`. The position's recommendation tag stays HOLD; the sell rec is informational only.
- **SELL rec + thesis WEAKENING** → flag, and bump the matrix's default recommendation from HOLD to TRIM
- **BUY rec on a held position UNDERWEIGHT** → provide as supporting evidence for ADD recommendation

### Step 6 (new ideas) — Surface BUY recs on unheld tickers
The briefing surfaces top BUY recommendations on tickers NOT currently held:
- Filter to recommendations with `recommendation: "BUY"`
- Drop tickers already in `positions.json`
- Rank by `conviction` (HIGH > MEDIUM > LOW) and freshness (newer > older)
- Limit to top N (default 5; configurable in `briefing_config.yaml`)
- Size each candidate with `position-sizer` using user's risk parameters
- Tag each entry with `source: "recommendation_list"` to distinguish from screener candidates

The recommendation list feeds in as a **trigger**, not a new dimension of the decision matrix. It stays at the same level as NEWS_NEGATIVE or MA50_LOST.

## Output Files

```
reports/daily/
  recommendations_YYYY-MM-DD.json     # canonical output, read by daily-portfolio-briefing

state/cache/
  recommendation_list.json            # cache payload with timestamp
```

## CLI / Manual Invocation

```bash
# Fetch recommendations and emit to reports/daily/
python3 scripts/fetch_recommendations.py \
  --config state/recommendation_list_config.yaml \
  --output reports/daily/recommendations_$(date +%F).json

# Force refresh ignoring cache
python3 scripts/fetch_recommendations.py --force-refresh

# Dry run (parse but don't write)
python3 scripts/fetch_recommendations.py --dry-run

# Validate config only
python3 scripts/fetch_recommendations.py --validate-config
```

## Error Handling

| Error | Behavior |
|---|---|
| Config file not found | Abort with instructions on where to create `state/recommendation_list_config.yaml` and what to put in it |
| Google Drive MCP not reachable | Abort with setup instructions |
| Sheet ID not found / no read access | Abort with sheet URL; ask user to verify in browser that the file exists and is shared |
| Google Sheets API quota exceeded | Return cached payload if available (with `stale: true` flag); otherwise abort with "retry in a few minutes" message |
| Sheet is empty or has no header row | Abort; suggest adjusting `data_starts_at_row` in config |
| Row has missing ticker | Skip silently; increment skip count in summary |
| Row has unrecognizable recommendation | Per `unknown_recommendation_handling`: warn + skip, drop silently, or tag UNKNOWN |
| Date field unparseable | Treat date as today; flag in summary under parsing_issues |
| Multiple rows match same ticker + date + recommendation | No dedup; allow duplicates (user may have multiple sources for same idea; let briefing decide) |

## Reference Files

- `references/google_sheets_setup.md` — Google Drive MCP setup, how to share the sheet with the MCP service account, troubleshooting (TBD)
- `references/recommendation_normalization.md` — detailed synonym handling, edge cases, examples (TBD)
- `references/integration_contract.md` — exact JSON schema and integration points with daily-portfolio-briefing (TBD)
- `assets/config_template.yaml` — starter config with comments on each field (TBD)

## Failure Modes & Recovery

1. **Quota exceeded with no cache:** The skill gracefully aborts rather than returning partial/stale data. User retries in a few minutes, or manually provides a CSV export of the sheet to be loaded directly.
2. **Sheet structure changed (columns removed/rearranged):** The config-driven approach means the user updates the column_mapping in `state/recommendation_list_config.yaml` and re-runs without code changes. The skill validates the mapping and surfaces clear errors if columns don't match.
3. **Ticker in sheet but not yet tradeable (e.g., SPAC at IPO):** The skill normalizes and emits the ticker normally; downstream validation (when the briefing tries to size or look up market data) will flag unknown tickers, not this skill.
4. **SUPPRESS directive expires between briefing runs:** The directive system in daily-portfolio-briefing handles expiry and re-surfacing; this skill just applies the current active directives. Once the directive expires, the recommendation re-surfaces automatically.

## Limitations

- **Single sheet only.** Multi-sheet aggregation (combining picks from multiple tabs) is v2.
- **Read-only.** The skill does not write back to the sheet (e.g., to mark recommendations as "acted on"). Write-back is deferred.
- **Conviction interpretation is context-dependent.** HIGH conviction from one analyst is not comparable to HIGH from another. The skill normalizes the field but does not weight across sources — that's a job for downstream analysis (e.g., the briefing can count how many sources agree on a ticker).
- **No price-target validation.** If the source's price target is wildly off current price, this skill won't catch it. Sanity checks happen downstream in the briefing's quality gate.
- **No duplicate deduplication.** If the same ticker appears twice (from different sources), both records surface. The briefing's dedup logic in Step 6 decides whether to consolidate or surface separately.

## Disclaimers & Caveats

This skill surfaces **recommendations provided by third parties or captured by the user**, not recommendations generated by Claude or by any proprietary model in this portfolio system. The skill does **not** endorse, validate, or independently analyze the recommendations. It is an input adapter only.

Users remain solely responsible for evaluating recommendations before acting on them. All investment decisions carry risk and are the user's responsibility.

