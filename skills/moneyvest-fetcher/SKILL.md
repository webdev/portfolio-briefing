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

Login state is detected FIRST, before any data selector waits (the
2026-08-06 probe failure was a login wall masquerading as a selector
timeout): after loading `/shopping-list` the fetcher reads the rendered
header — "Sign Out" (+ user name) means logged in; "Sign In"/"Sign Up" or
a redirect to a landing/login route means logged out. Then:

1. **Logged out + creds in env** → scripted login with `MONEYVEST_EMAIL` /
   `MONEYVEST_PASSWORD` (from `.env`; the code only calls `os.getenv` —
   never reads `.env` directly and NEVER logs credentials), verified by
   re-loading `/shopping-list`.
2. **Logged out + no creds + `--headed`** → the visible browser waits up
   to 4 min for a MANUAL login; on success the session is saved so creds
   stay optional for all future headless runs.
3. **Logged out + no creds + headless** → explicit failure: "not logged in
   and MONEYVEST_EMAIL/MONEYVEST_PASSWORD not set in .env — add
   credentials or run --headed to log in manually once (session
   persists)".

Playwright storage-state persists at
`~/.config/portfolio-briefing/moneyvest_state.json` (chmod 0600) and is
refreshed on every successful fetch.

## Scrape notes (SPA discipline)

- The site is a React SPA with heavy ad scripts. NEVER wait on
  `networkidle` (never settles) or on `<table>` markup (the layout can
  render as a div-grid) — the fetcher polls the rendered DOM for TEXT
  landmarks: "Fair Value" / "Light Buy" / "Heavy Buy" / "No Brainer".
- Data rides Firebase/WebSockets — scrape the RENDERED DOM
  (`page.content()` / `inner_text`), not network responses.
- Parsing is layout-generic: the column-header row is located by its text
  landmarks, then row-like siblings are walked extracting ticker + numeric
  cells in column order — works for BOTH real `<table>`s and div-grids.
  Sections come from heading tags or interleaved single-cell section rows
  (e.g. "MAG7"). Column mapping is name-fuzzy (`_map_columns`) so cosmetic
  header edits don't break the fetch; rows without a recognizable ticker
  are skipped, never fabricated.
- The probe prints each stage: `loaded /shopping-list → login state:
  LOGGED IN as <name> → found headers: [...] → parsed N rows (sections:
  [...]) → index: 3.82 OPTIMISTIC → M-Scores: N fetched`.

## Debug artifacts (on ANY fetch failure)

Every failed fetch dumps `page.html` (rendered DOM), `screenshot.png`,
and `console.log` (probe-stage log + browser console) under
`state/moneyvest_debug/<timestamp>/` — last 3 dumps kept — and prints
"debug dump: <path> — send this to Claude to fix selectors". Send that
directory when a probe fails and the selectors can be fixed from the
captured DOM without another live session.

## Runbook (first run)

```bash
uv add playwright && uv run playwright install chromium

# Option A (no stored creds): log in manually ONCE in a visible browser —
# the session persists at ~/.config/portfolio-briefing/moneyvest_state.json
uv run skills/moneyvest-fetcher/scripts/fetch_moneyvest.py --probe --headed

# Option B: add to .env:  MONEYVEST_EMAIL=...  MONEYVEST_PASSWORD=...
uv run skills/moneyvest-fetcher/scripts/fetch_moneyvest.py --probe

# expect: "[moneyvest] loaded /shopping-list → login state: LOGGED IN ..."
#         "moneyvest: N shopping-list rows · index S&P 3.82 OPTIMISTIC ..."
# on failure: "debug dump: state/moneyvest_debug/<ts> — send this to
#              Claude to fix selectors"
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
DOM fixtures matching the observed column structure in BOTH `<table>` and
div-grid renderings (never live), plus login-state detection, debug-dump
artifacts (write + prune-to-3), cache/staleness, and the
degraded-no-playwright path.
