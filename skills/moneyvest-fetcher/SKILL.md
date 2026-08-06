---
name: moneyvest-fetcher
description: Fetch the Moneyvest shopping list (fair value + Light/Heavy/No-Brainer buy ladder), sentiment index, and per-stock M-Scores via authenticated Playwright scrape. Use when the briefing pipeline needs Moneyvest data (Step 1.9 morning scan) or when the user asks to refresh/probe Moneyvest.
---

# Moneyvest Fetcher

Scrape moneyvest.com (authenticated, React SPA) and cache the three datasets
the briefing pipeline consumes:

1. **Shopping list** (`/shopping-list`) — table sections (MAG7, "Long Term -
   High Quality", more below the fold; plus an ETFs tab). Columns captured
   per row: ticker · section · Price · **Fair Value** · Delta% · **Light
   Buy** · **Heavy Buy** · **No Brainer Buy** · **No Brainer 2027 PE**. The
   page's "Last updated on `<date>`" line is captured as
   `list_last_updated`.
2. **Sentiment index** (`/moneyvest-index`) — gauge value (e.g. 3.82) +
   label (PANIC / FEAR / UNCERTAINTY / NEUTRAL / OPTIMISTIC / GREED /
   EUPHORIA) for the S&P 500 and NASDAQ 100 tabs.
3. **Per-stock M-Score** (`/stock/<TICKER>/overview`, badge "M-Score ·
   4.05/5.00") — fetched ONLY for the bounded ticker set passed by the
   caller (held + candidate tickers from the latest snapshot); each ticker
   skips gracefully on failure.

## Output

`state/cache/moneyvest.json` (repo root), atomic write:

```json
{
  "as_of": "2026-08-06T12:00:00Z",
  "shopping_list": [{"ticker": "NVDA", "section": "MAG7", "price": 182.1,
                     "fair_value": 152.0, "light_buy": 196.0,
                     "heavy_buy": 160.0, "no_brainer": 130.0,
                     "pe_2027": 24.0}],
  "index": {"sp500": {"value": 3.82, "label": "OPTIMISTIC"},
            "ndx": {"value": 3.1, "label": "NEUTRAL"}},
  "m_scores": {"NVDA": 4.05},
  "list_last_updated": "August 5, 2026",
  "provenance": "live"
}
```

Cache TTL is 20h (daily refresh). Fail-open: ANY error (auth, network,
parse, missing Playwright) keeps the prior cache with
`provenance: "stale_cache"` + `stale_hours`; the briefing never blocks on
Moneyvest.

## Auth

Playwright storage-state persisted at
`~/.config/portfolio-briefing/moneyvest_state.json` (chmod 0600). When the
state is missing/expired the login flow runs with `MONEYVEST_EMAIL` /
`MONEYVEST_PASSWORD` read from the environment (put them in `.env`; the
pipeline loads it via python-dotenv). The code only calls `os.getenv` —
never reads `.env` directly and NEVER logs credentials.

## Scrape notes (SPA discipline)

- The site is a React SPA with heavy ad scripts. Use generous timeouts and
  `wait_for_selector("table tr td")` on the shopping list — NOT
  `networkidle`, which never settles.
- Data rides Firebase/WebSockets — scrape the RENDERED DOM
  (`page.content()` / `inner_text`), not network responses.
- Parsing is column-name-fuzzy (`_map_columns`) so cosmetic header edits
  don't break the fetch; rows without a recognizable ticker are skipped,
  never fabricated.

## Runbook (first run)

```bash
uv add playwright && uv run playwright install chromium
# add to .env:  MONEYVEST_EMAIL=...  MONEYVEST_PASSWORD=...
uv run skills/moneyvest-fetcher/scripts/fetch_moneyvest.py --probe
# expect: "moneyvest: N shopping-list rows · index S&P 3.82 OPTIMISTIC ..."
# first login may need:  --headed   (visible browser to clear any challenge)
```

Daily use is automatic: `run_briefing.py` Step 1.9 calls the fetcher on
every run (config-gated by `briefing.yaml → moneyvest.enabled`; a fresh
cache short-circuits the network fetch).

## Downstream integrations (daily-portfolio-briefing)

- **💰 MV chip** on ticker headers (`analysis/moneyvest_chip.py`, parkev_chip
  pattern): `💰 FV $152 · LB $196 · M 4.05` — n/a-safe, only when data is
  present; ETF rows get index-only treatment (no per-stock chip).
- **Buy-ladder strike anchoring** — LT_CSP (`long-term-opportunity-advisor`
  `advise.py`) and the new-idea CSP composer prefer a strike at/just-below
  Light Buy when it falls inside the discipline band; rendered as
  "strike anchored to 💰 Light Buy $196". Anchor selection only — never
  overrides RSI/chase/earnings gates.
- **MV Index → Market Context**: "Moneyvest sentiment: 3.82 OPTIMISTIC
  (S&P)" + advisory contrarian-caution note in the regime context
  (no regime changes).
- **FV divergence flag**: when MV fair value and FMP DCF diverge > 40%
  relative, the FV line renders "⚠ models diverge (MV $152 vs DCF $242) —
  trust neither blindly".

## Tests

`scripts/tests/test_moneyvest_fetcher.py` — parse tests run on constructed
DOM fixtures matching the observed column structure (never live), plus
cache/staleness and the degraded-no-playwright path.
