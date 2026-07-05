---
name: finviz-target-fetcher
description: Fetch analyst consensus target prices + analyst recommendation scores from FINVIZ (public quote.ashx). Caches 24h. Fail-closed on errors. Public mode only — no FINVIZ Elite key required.
---

# FINVIZ Target Fetcher

Second-source analyst target prices alongside FMP's `intrinsic_value` module.

## What this provides

For every ticker in the user's held positions + Scout candidates, returns:

```python
{
    "ticker": "NVDA",
    "spot_price": 200.09,
    "target_price": 318.50,
    "target_upside_pct": 59.2,
    "analyst_recommendation": 1.8,   # 1.0=Strong Buy, 5.0=Strong Sell
    "analyst_label": "Buy",          # derived from the 1-5 score
    "p_e": 64.2,
    "fwd_p_e": 38.1,
    "eps_growth_next_y_pct": 24.5,
    "fetched_at": "2026-06-30T14:42:00Z",
    "source": "finviz_public",
}
```

`None` for any ticker FINVIZ doesn't cover (ETFs, OTC tickers, recent IPOs).

## Why this exists (vs FMP analyst price-target)

FMP's `/stable/price-target-summary` is already wired in via `analysis/intrinsic_value.py`.
FINVIZ adds:

1. **Better breadth** — covers tickers FMP doesn't (`FV: n/a (no FMP data)` on
   ~60% of today's candidates is the reason this was built).
2. **Analyst recommendation as a 1-5 score** — FMP doesn't normalize this.
3. **Cross-check** — when both sources have a target, divergence > 20% is a
   data-quality signal worth surfacing.

## Hard rules this respects

- **#12 (intrinsic value)**: FINVIZ extends, never replaces. Both sources rendered.
- **#19 (no fabricated data)**: every parse failure → `None`, never invented.
- **#2 (chain data E*TRADE only)**: this skill does NOT touch option chains.
- **#11 (RSI discipline)**: target upside does NOT override RSI gate.

## Access mode — public scrape

This skill uses **public scraping** (`finviz.com/quote.ashx?t=TICKER`),
NOT the FINVIZ Elite API. Implications:

- **No API key required** — works free.
- **Rate-limited** — default 2 sec/request, daily soft cap 60 requests
  (covers held positions + top candidates comfortably).
- **Fragile** — FINVIZ may serve a captcha or block the IP after sustained
  scraping. When that happens, the skill returns `None` for the affected
  tickers and surfaces a warning. No retries, no spoofing.
- **ToS gray area** — FINVIZ's ToS discourages programmatic scraping. The
  user has opted into this mode explicitly (alternative was Elite at $39.50/mo).
  If FINVIZ blocks the IP, the right answer is to subscribe to Elite, not to
  build evasion logic.

## Files

```
skills/finviz-target-fetcher/
├── SKILL.md                          (this file)
├── config/finviz_config.yaml         (cache TTL, rate limits, daily cap)
└── scripts/
    ├── fetch_finviz_targets.py       (CLI entry point + cache layer)
    ├── public_fetcher.py             (quote.ashx HTML scraper)
    └── tests/
        ├── fixtures/
        │   └── sample_quote_nvda.html
        └── test_finviz_fetcher.py
```

## CLI usage

```bash
# Fetch a single ticker, dump to stdout
python3 scripts/fetch_finviz_targets.py --ticker NVDA

# Fetch a batch of held + candidate tickers from a JSON file
python3 scripts/fetch_finviz_targets.py \
    --tickers-file state/today_tickers.json \
    --output state/finviz_targets_2026-06-30.json \
    --cache-dir state/

# Force refresh (ignore cache)
python3 scripts/fetch_finviz_targets.py --ticker NVDA --refresh
```

Exit codes: 0 success, 1 partial (some tickers fetched, some failed), 2 hard fail (all blocked).

## Integration

Called from `skills/daily-portfolio-briefing/scripts/run_briefing.py` step 1.7
(after the Parkev fetch, before `snapshot_inputs`):

```python
from finviz_target_fetcher import fetch_targets

tickers = held_tickers | candidate_tickers
finviz_targets = fetch_targets(tickers, cache_dir=snapshot_dir)
# → propagated through `snapshot_data["finviz_targets"]` and consumed by
#   `analysis/finviz_targets.annotate_finviz()` in the post-pass.
```

The chip rendered in the briefing:

```
📈 FINVIZ $318 (+59%) · 🅰 1.8/5
```

Divergence flag when FMP and FINVIZ disagree by >20%:

```
💵 FV: DCF $249 · FMP PT $318 · 📈 FINVIZ $385  ⚠ diverge
```
