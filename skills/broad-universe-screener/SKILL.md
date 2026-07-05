# Broad Universe Screener

Broad-universe wheel-setup screener using FMP's company-screener + yfinance
deep-dive. Requires `FMP_API_KEY` (already required by `intrinsic_value.py`);
no additional subscription. Surfaces tradeable wheel setups on names the user
does NOT already track via Parkev's recommendation list or the Thematic
Scout's theme universes. Output: ~20-30 ranked hits per day, not a raw dump.

## What it does

Three-stage, best-tool-per-job funnel:

1. **FMP company-screener broad scan** (`FMP_API_KEY` required) — ONE call to
   `https://financialmodelingprep.com/stable/company-screener` (the `/stable/`
   API; legacy `/api/v3` returns 403 for post-2025-08-31 keys). Server-side
   filters: market cap > $2B, avg volume > 500K, NYSE/NASDAQ, US only, no
   ETFs, actively trading, limit 1500. Approximates S&P 500 + Nasdaq 100 +
   Russell 1000 (~1200-1500 names). Replaced the FINVIZ Elite CSV export
   (2026-07-03, task #8) — the $40/mo Elite subscription is no longer needed.
   Rate-limit friendly: one call per briefing run against a 250/day free tier.
2. **Exclusion pass** — drop every ticker already covered by:
   - `skills/thematic-scout/references/theme_universes.yaml` (anchors + etfs,
     all themes)
   - Parkev's list (`state/cache/recommendation_list.json`)
   Missing exclusion sources are a HARD error (fail closed) — running without
   dedup would surface MU / NVDA as "new" candidates.
3. **yfinance deep-dive** on the survivors ONLY, capped at `deep_dive_max`
   (default 800 — the scale-trap guard) and parallelized
   (`parallel_fetch: true`, thread pool — yfinance is I/O-bound): 300d OHLC →
   RSI(14) Wilder's, IV rank (252d realized-vol percentile — same math as the
   Scout), support clusters (reuses
   `analysis/support_resistance.py::compute_sr`), next earnings date,
   options-chain existence. **Reject-early:** RSI > 65 or < 25 skips the S/R
   + earnings + chain calls — those names can never pass the 35-50 gate.
   FMP can't pre-filter on RSI the way FINVIZ Elite did, so the deep-dive
   input is ~600-1000 names instead of ~100; the cap + parallelism +
   reject-early keep the run bounded.

## Discipline gates (every row must pass ALL)

- Not in Scout themes, not in Parkev's list
- RSI(14) in 35-50 (pullback band)
- IV rank ≥ 60
- Spot within 5% of a strong support (≥ 3 touches, ≥ 90d of history)
- No earnings within 21 days — **unknown earnings date fails closed**
- Options chain exists (yfinance `ticker.options` non-empty)
- Market cap > $2B

## Ranking

Composite 0-10 score: RSI proximity to 40 (deepest pullback) + IV rank
(fatter premium) + support strength (recency-weighted touches + confluence).
Output capped at 30 rows.

## FMP fair-value cross-check

The same `FMP_API_KEY` also gives the top rows a fair-value annotation via
`analysis/intrinsic_value.py` (DCF + analyst PT). Fail-soft — annotation
only, never a gate, never a fabricated number.

## Capacity gate integration

When the briefing pipeline passes its `GateState`, the report leads with the
capacity banner; when entry gates are CLOSED every row carries a
`⏸ Deferred (capacity gated)` tag (hard rule #24: surface as DEFERRED, never
hide).

## Usage

```bash
# Standalone (from repo root)
python3 skills/broad-universe-screener/scripts/screen_universe.py \
    --output reports/daily/daily_screener_2026-07-03.md

# Pipeline: Step 8.7 in run_briefing.py — opt-in via briefing.yaml:
#   screeners:
#     broad_universe:
#       enabled: true
```

Fail-open at the pipeline level: a screener error never blocks the briefing,
and an FMP rate limit (HTTP 429) returns an empty result with an explicit
note in the report instead of an error. Fail-closed at the data level:
missing `FMP_API_KEY`, missing exclusion sources, or missing per-name data →
that name (or the whole run) is dropped with an explicit reason, never
papered over with defaults.

## Tests

```bash
python3 -m pytest skills/broad-universe-screener/scripts/tests/ -v
```
