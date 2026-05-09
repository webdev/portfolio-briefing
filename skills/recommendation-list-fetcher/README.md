# Recommendation List Fetcher Skill

A standalone Claude skill that fetches stock buy/sell/hold recommendations from a Google Sheet, normalizes them into a structured JSON format, and makes them available for downstream consumption by the daily-portfolio-briefing skill.

## Quick Start

### 1. Set up the Google Sheet (one-time)

Ensure your Google Sheet is shared with "anyone with the link can view" access:

1. Open your Google Sheet
2. Click Share → Change to "Viewer" → "Anyone with the link"
3. Copy the sheet ID from the URL (e.g., `12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M`)

See `references/google_sheets_setup.md` for detailed instructions.

### 2. Create the config file

Copy `assets/config_template.yaml` to `config/recommendation_list_config.yaml` and update:

```yaml
source:
  url: "https://docs.google.com/spreadsheets/d/{YOUR_SHEET_ID}/gviz/tq?tqx=out:csv"
  sheet_id: "{YOUR_SHEET_ID}"
```

All other fields have sensible defaults. See `references/normalization_rules.md` for customization options.

### 3. Run the skill

```bash
# Fetch and output to reports/daily/
python3 scripts/fetch_recommendations.py

# Test sheet access first
python3 scripts/fetch_recommendations.py --validate-access

# Force refresh (ignore cache)
python3 scripts/fetch_recommendations.py --force-refresh

# See all options
python3 scripts/fetch_recommendations.py --help
```

## What It Does

1. **Fetches** the Google Sheet via the public gviz CSV endpoint (no OAuth needed)
2. **Parses** recommendation rows and resolves company names to tickers
3. **Normalizes** rating strings to numeric tiers and canonical enum values (BUY, SELL, HOLD, etc.)
4. **Caches** results for 1 hour to avoid rate limiting
5. **Outputs** a structured JSON file with:
   - Summary statistics (count by rating tier)
   - Individual recommendations with:
     - Ticker and company name
     - Canonical recommendation enum
     - Raw recommendation string (for auditing)
     - Rating tier (0-5)
     - Date updated and age in days
     - Price targets if available
     - Row number from source sheet

## Integration with Daily Briefing

The daily-portfolio-briefing consumes this output to:

- **Step 4:** Enrich held positions with external recommendations
- **Step 6:** Surface BUY recommendations on unheld tickers

See `references/integration_with_briefing.md` for details.

## File Structure

```
recommendation-list-fetcher/
├── SKILL.md                          # Skill definition
├── README.md                         # This file
├── scripts/
│   ├── fetch_recommendations.py      # CLI entry point
│   ├── shopping_list.py              # Core parsing logic
│   ├── test_live_sheet.py            # Manual live sheet test
│   ├── validate_imports.py           # Import validation
│   └── tests/
│       ├── conftest.py               # Test fixtures
│       └── test_fetch_recommendations.py  # Unit tests
├── references/
│   ├── google_sheets_setup.md        # Sheet sharing setup guide
│   ├── normalization_rules.md        # Rating mapping and edge cases
│   └── integration_with_briefing.md  # Briefing integration details
└── assets/
    └── config_template.yaml          # Config template (copy to config/)
```

## Output Format

The skill writes a JSON file to `reports/daily/recommendations_YYYY-MM-DD.json`:

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
    },
    ...
  ]
}
```

## Canonical Recommendation Enums

| Tier | Enum | Source Rating |
|---|---|---|
| 5 | STRONG_BUY | "Top Stock to Buy" |
| 4 | BUY | "Top 15 Stock" |
| 3 | BUY | "Buy" |
| 2 | WEAK_BUY | "Borderline Buy" |
| 1 | HOLD | "Hold/ Market Perform" |
| 0 | SELL | "Sell" |

## Configuration

All customization is in `config/recommendation_list_config.yaml`:

- **source.url**: Google Sheet gviz CSV endpoint
- **column_mapping**: Which columns contain name, rating, dates, price targets
- **ticker_resolution.manual_overrides**: Company name → ticker mappings (e.g., "Alphabet" → GOOG)
- **normalization.rating_tiers**: Map rating strings to numeric tiers
- **normalization.tier_to_recommendation**: Map tiers to canonical enums
- **freshness.max_age_days**: Archive (skip) recommendations older than this
- **freshness.warn_age_days**: Tag as "aging" if older than this
- **caching.cache_for_minutes**: Cache TTL (default 60 minutes)

See `assets/config_template.yaml` for all options with comments.

## Testing

Run the test suite:

```bash
# Install dev dependencies
pip install pytest pytest-cov

# Run all tests
pytest scripts/tests/ -v

# Run specific test
pytest scripts/tests/test_fetch_recommendations.py::TestRatingParsing -v

# Test live sheet access
python3 scripts/test_live_sheet.py
```

Test coverage includes:
- Rating tier parsing and mapping
- Price target range parsing
- Date parsing across multiple formats
- Ticker resolution (manual overrides, cache, yfinance)
- CSV row parsing and normalization
- Config validation
- Full integration pipeline

## Troubleshooting

### "Config file not found"

Create `config/recommendation_list_config.yaml` by copying `assets/config_template.yaml`:

```bash
mkdir -p config
cp assets/config_template.yaml config/recommendation_list_config.yaml
```

Then update the `source.url` with your sheet ID.

### "Sheet returned HTML (likely not publicly shared)"

The Google Sheet is not shared publicly. See `references/google_sheets_setup.md` for sharing setup.

### "Sheet accessible but no recommendations parsed"

1. Verify the column mapping in your config matches the sheet's layout
2. Check that company names resolve to tickers (use manual_overrides if needed)
3. Run `python3 scripts/test_live_sheet.py` to see the raw sheet data

### "Ticker resolution failed for [name]"

Add the company name to `config/recommendation_list_config.yaml` under `ticker_resolution.manual_overrides`:

```yaml
manual_overrides:
  "Your Company": YOUR_TICKER
```

### "Rating string not recognized"

The sheet has a rating string not in the config. Add it to `normalization.rating_tiers`:

```yaml
rating_tiers:
  "My Custom Rating": 3  # Tier 0-5
```

## Performance

- **Fetch time:** ~2-3 seconds (network dependent)
- **Parse time:** <1 second for 150-190 rows
- **Cache TTL:** 60 minutes (configurable)
- **Stale fallback:** Returns cached data if sheet is unreachable

On the briefing's 5x daily analysis cycle (8am, 10:30am, 1pm, 3:30pm, 4:30pm), caching ensures the sheet is fetched at most once per hour.

## Limitations

- **Single sheet only** — Multi-sheet aggregation is v2
- **Read-only** — Does not write back to the sheet
- **No deduplication** — Same ticker can appear twice (from different sources)
- **No conflict resolution** — Both BUY and SELL recs on same ticker are surfaced

## Error Handling

| Scenario | Behavior |
|---|---|
| Config missing | Abort with setup instructions |
| Sheet not publicly shared | Abort with link-sharing instructions |
| Network error | Return cached data if available (with stale flag) |
| Empty sheet | Abort with suggestion to check data_starts_at_row |
| Missing ticker | Skip row; increment skip count |
| Unparseable date | Treat as today; flag in summary |
| Unparseable rating | Default to tier 1 (HOLD); log warning |

## References

- `SKILL.md` — Full skill specification
- `references/google_sheets_setup.md` — Sheet sharing setup
- `references/normalization_rules.md` — Rating mapping and parsing rules
- `references/integration_with_briefing.md` — Integration details with briefing

## Dependencies

- **Required:** `pyyaml`, `structlog`
- **Optional (for live sheet access):** `httpx` (will be available in skill environment)
- **For ticker resolution:** `yfinance` (if using yfinance fallback)

## Development Notes

The skill is ported from `wheelhouz/src/data/shopping_list.py` with these adjustments:

- Standalone config file (YAML) instead of embedded code
- JSON output instead of dataclass
- Canonical enum mapping for downstream consumption
- No Telegram integration (logs only)
- Simplified to single-sheet operation

The core parsing logic is unchanged to ensure compatibility with the existing data format.
