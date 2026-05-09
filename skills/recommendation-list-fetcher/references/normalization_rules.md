# Recommendation Normalization Rules

## Rating Tier System

The skill normalizes rating strings from the sheet into numeric tiers (0-5), then maps tiers to canonical recommendation enums used downstream.

### Rating Tier Mapping

| Rating String | Numeric Tier | Canonical Enum | Usage |
|---|---|---|---|
| `Top Stock to Buy` | 5 | `STRONG_BUY` | Highest conviction buy |
| `Top 15 Stock` | 4 | `BUY` | High-conviction buy (list limited to 15 names) |
| `Buy` | 3 | `BUY` | Standard buy recommendation |
| `Borderline Buy` | 2 | `WEAK_BUY` | Marginal buy (close to hold) |
| `Hold/ Market Perform` | 1 | `HOLD` | Market-weight / no action |
| `Sell` | 0 | `SELL` | Reduce/exit position |

### Canonical Enums

The downstream briefing consumes these canonical enums in filter logic:

- **STRONG_BUY**: Tier 5. Most actionable. Top-of-watch list.
- **BUY**: Tiers 4 & 3. Standard actionable. Sized normally.
- **WEAK_BUY**: Tier 2. Borderline. Smaller position sizes.
- **HOLD**: Tier 1. No new action. Useful for enriching existing position context.
- **SELL**: Tier 0. Exit/reduce signal. Triggers close analysis in watch panel.
- **UNKNOWN**: Ratings not recognized. Default to tier 1 (HOLD). Flagged in logs.

## Data Hygiene

### Whitespace Handling

The skill **strips leading and trailing whitespace** from all fields:

- Input: `"Top 15 Stock "` (trailing space)
- Processing: Strip to `"Top 15 Stock"`
- Match: Succeeds against config `"Top 15 Stock": 4`
- Output: Tier 4 ✓

This handles common copy-paste and Excel quirks automatically.

### #REF! Error Handling

Excel's `#REF!` sentinel (broken formula reference) appears sometimes in the sheet. If configured to drop these:

- Any row containing `#REF!` in ANY column is skipped entirely
- Reason logged: `"#REF! error"`
- Count incremented in summary

### Missing/Empty Fields

Fields can be empty without breaking parsing:

- **Name or Rating missing**: Row skipped entirely (required for any recommendation)
- **Date missing**: Treated as today's date; no age computed
- **Price target missing**: Allowed; stored as `null` in output
- **Notes missing**: Allowed; stored as `null` in output

## Date Parsing

Multiple date formats are supported. First match wins:

| Format | Example | Parsed |
|---|---|---|
| `%m/%d/%Y` | `5/6/2026` | 2026-05-06 |
| `%m/%d/%y` | `5/6/26` | 2026-05-06 |
| `%Y-%m-%d` | `2026-05-06` | 2026-05-06 |

**Ambiguity:** 2-digit year `26` is interpreted as `2026` (current century). If you have dates before 2000, use 4-digit year format.

**Unparseable dates:** If a date can't be parsed, it's treated as today's date and a warning is logged. The recommendation is still included; aging flag reflects the "today" assumption.

## Price Target Parsing

Price targets are parsed into ranges or single values:

| Input | Output | Type |
|---|---|---|
| `320-350` | `(320.0, 350.0)` | Tuple |
| `320–350` | `(320.0, 350.0)` | Tuple (em-dash also works) |
| `320` | `320.0` | Float |
| `1,200-1,350` | `(1200.0, 1350.0)` | Tuple (comma removed) |
| `""` | `null` | Missing |
| `invalid` | `null` | Unparseable |

**Note:** Single values and ranges are both stored as numbers (floats). Downstream filtering can treat both uniformly.

## Ticker Resolution Strategy

When resolving a company name to a ticker symbol, the skill tries in order:

1. **Manual overrides** (fastest, most reliable)
   - Checked first
   - Maintained in `config/recommendation_list_config.yaml`
   - Add entries for names that don't map cleanly (e.g., "Alphabet" → GOOG)

2. **Persistent cache** (avoids repeated API calls)
   - File: `state/cache/ticker_map.json`
   - Built up over time as yfinance resolves names
   - Can be pre-populated with known mappings

3. **yfinance fallback** (network-based, slower)
   - Searches yfinance for the company name
   - Logs warnings if fallback is disabled
   - May fail for obscure or new tickers

### Adding Manual Overrides

If a company name doesn't resolve correctly:

1. Identify the correct ticker (e.g., `GOOG` for Alphabet)
2. Add to `config/recommendation_list_config.yaml` under `ticker_resolution.manual_overrides`:
   ```yaml
   manual_overrides:
     "Alphabet": GOOG
     "Your Company": YOUR_TICKER
   ```
3. Re-run the skill

The override takes effect immediately.

## Aging and Archival

Recommendations have an age (days since last update):

```
Age = today - date_updated
```

**Aging threshold:** If age >= `warn_age_days` (default 14 days), the recommendation is tagged `aging: true`.

**Archival threshold:** If age > `max_age_days` (default 30 days), the recommendation is skipped entirely with reason `"archived"`.

These thresholds are in `config/recommendation_list_config.yaml` under `freshness`:

```yaml
freshness:
  max_age_days: 30     # Recommendations older than this are skipped
  warn_age_days: 14    # Recommendations older than this are tagged "aging"
```

Adjust these values to match your needs:

- **Conservative (don't use stale picks):** `max_age_days: 14`, `warn_age_days: 7`
- **Liberal (use anything recent):** `max_age_days: 90`, `warn_age_days: 30`

## Edge Cases

### Same ticker, multiple recommendations

If the sheet lists the same ticker twice (from different sources or dates):

- Both entries are parsed and output separately
- No automatic deduplication
- The briefing can decide whether to surface one or both

Example:

```json
{
  "ticker": "AAPL",
  "recommendation": "BUY",
  "source": "Analyst A",
  "date_updated": "2026-05-01"
},
{
  "ticker": "AAPL",
  "recommendation": "HOLD",
  "source": "Analyst B",
  "date_updated": "2026-04-28"
}
```

This is intentional — divergent views on the same ticker are valuable context.

### Unknown rating strings

If the sheet contains a rating string not in the config's `rating_tiers`:

- The skill logs a warning
- The recommendation defaults to tier 1 (HOLD)
- The `raw_recommendation` field preserves the original string for auditing

**Fix:** Add the unknown rating to `config/recommendation_list_config.yaml`:

```yaml
rating_tiers:
  "My Custom Rating": 3
```

### Ticker resolution failure

If a company name can't be resolved to a ticker:

- The row is skipped
- Reason logged: `"no_ticker: {name}"`
- Summary incremented in `skipped_reasons`

**Fix:** Add a manual override in `config/recommendation_list_config.yaml`.
