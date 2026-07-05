# Portfolio Briefing — Web App (Milestone 2)

Dashboard + interactive operations layer over the daily portfolio briefing snapshots.

## What this is

A FastAPI + DuckDB + Jinja2 + HTMX + Plotly web app that renders the
existing daily briefing JSON snapshots in a clickable, time-aware surface.

**Milestone 1** (shipped): dashboard skeleton + 3 Plotly time-series
charts (NLV, stress coverage, expiration ladder) + per-ticker drill-down
+ Parkev rating history.

**Milestone 2** (this milestone) adds:
- **Refresh button** — kicks off `run_briefing.py` as a background subprocess
  with live SSE log tail; dashboard auto-reloads on completion.
- **Diff view** — side-by-side comparison of any two briefings:
  positions added/removed/changed, actions new/completed/pending,
  Parkev rating flips, NLV/cash/coverage/regime deltas.
- **Ticker hover popover** — hover any ticker chip → 30-day price
  sparkline + Parkev chip + latest 3 mentions.
- **Counterpoint disclosures** — every action item has an expandable
  "Show counterpoint" disclosure that fetches the devil's-advocate text
  inline.
- **Cross-snapshot search** — DuckDB-backed search across every
  briefing's mentions (ticker, action kind, date prefix, source filter).
- **Chip date correctness** — chips on historic briefings now show the
  rating that was in effect *on that date*, not today's. Fixes a
  visual lie M1 silently shipped.

## How to run

```bash
cd /Users/gblazer/workspace/portfolio-briefing/webapp
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 17776 --reload
```

Open <http://127.0.0.1:17776>. The footer shows snapshots loaded +
last ingest timestamp.

## Routes

### M1 (read-only)

| Route | Purpose |
|---|---|
| `GET /` | Dashboard — latest briefing rolled up |
| `GET /briefing/latest` | 302 to latest dated briefing |
| `GET /briefing/{date}` | Full briefing render |
| `GET /positions` | All current positions |
| `GET /positions/{ticker}` | Per-ticker drill-down |
| `GET /parkev/{ticker}` | Parkev rating history |
| `GET /history` | NLV / coverage / expiration ladder charts |
| `GET /charts/nlv.json` | Plotly JSON |
| `GET /charts/coverage.json` | Plotly JSON |
| `GET /charts/expiration-ladder.json` | Plotly JSON |
| `GET /charts/parkev/{ticker}.json` | Plotly JSON |
| `GET /fragment/refresh-status` | HTMX target |
| `GET /health` | Liveness check |

### M2 (interactive)

| Route | Purpose |
|---|---|
| `POST /refresh` | Kick off a refresh — returns 202+job_id or 409 if running |
| `GET /jobs/{job_id}` | Job status JSON |
| `GET /events?job_id=...` | SSE stream of log lines + ingest_complete |
| `GET /diff/yesterday/latest` | Redirect to the two-most-recent comparison |
| `GET /diff/{a}/{b}` | Side-by-side diff page |
| `GET /fragment/diff/{a}/{b}` | Diff body fragment for HTMX swap |
| `GET /search?q=...&type=...` | Cross-snapshot search |
| `GET /fragment/ticker_card/{tk}` | Ticker hover popover (HTMX fragment) |
| `GET /fragment/counterpoint/{date}/{key}` | Counterpoint disclosure body |
| `GET /charts/sparkline/{tk}.json` | 30-day price sparkline (Plotly JSON) |

### Companion markdown reports (post-M2)

| Route | Purpose |
|---|---|
| `GET /candidates/{date}` | Render `~/Documents/briefings/candidates_<DATE>.md` as HTML |
| `GET /candidates/latest` | 302 to latest candidates report |
| `GET /when-to-enter/{date}` | Render `~/Documents/briefings/when_to_enter_<DATE>.md` |
| `GET /when-to-enter/latest` | 302 to latest when-to-enter report |

The Ideas tab on `/briefing/{date}` is now a merged view: `new_ideas` + the
actionable kinds from `long_term_opportunities` (LONG_DATED_CSP, BUY, ADD,
LT_ADD, PULLBACK_CSP), plus deferred/skipped kinds (DEFERRED_ADD_HAS_CSP,
SKIPPED_*) so opportunities are never hidden behind a capacity placeholder.
See CLAUDE.md hard rules #31 (humanize machine identifiers) and #32 (never
hide opportunities) for the contract.

### Search query syntax

- Plain text → matches ticker, action kind, recommendation, or detail
  field (case-insensitive substring across all four)
- `q=NVDA` → every mention of NVDA
- `q=CLOSE&type=action` → every CLOSE action
- `q=2026-06` → every June briefing's mentions (date prefix)
- Source filter values: `equity_review` / `options_review` / `action`
  / `long_term_opportunity` / `new_idea` / `strategy_upgrade`

### SSE event format

```
event: log
data: <one stdout/stderr line>

event: log
data: <next line>

event: caught_up
data: <count of buffered lines replayed>

event: ingest_complete
data: {"status": "success", "return_code": 0, "job_id": "job_abc123"}
```

The connection closes after `ingest_complete`. Clients should listen
for that event and trigger a page reload.

Buffered log lines are replayed first when a late-joining client
connects, so a refresh modal opened mid-run still sees the full
history.

## Data sources (read-only)

- `~/Documents/briefings/briefing_*.json` — daily briefing summaries
- `<repo>/skills/daily-portfolio-briefing/state/briefing_snapshots/<DATE>/` —
  raw per-day inputs (positions, balance, regime, recommendations_list)

DuckDB views over these JSON globs use `union_by_name=true` so schema
evolution is silent. See `app/ingest.py`.

The web app NEVER writes to either path. The only state-mutating
endpoint is `POST /refresh`, which subprocesses the pipeline (which
itself writes to the snapshot dirs).

## M1 known issues — fixed in M2

| # | Issue | Status |
|---|-------|--------|
| 1 | DuckDB re-materialized projections on every request | **Fixed** — materialization happens at startup + after every `POST /refresh` only. Per-request connects are cheap. |
| 2 | `_briefing_mentions_for_ticker` opened every briefing JSON per request | **Fixed** — new materialized `briefing_mentions` table; `/positions/{tk}` and `/search` both query it. |
| 3 | Chips on historic `/briefing/{date}` showed today's ratings | **Fixed** — `parkev_chip_renderer(date)` looks up the rec from THAT date's snapshot, not today's. |

## Tests

```bash
uv run pytest -v
```

92 tests, 90% coverage on `app/`. Tests are organized:

- `tests/test_ingest.py` / `test_ingest_m2.py` — DuckDB layer
- `tests/test_models.py` — Pydantic V1 ↔ V2 coercion
- `tests/test_routes.py` — M1 route smoke tests
- `tests/test_charts.py` — Plotly figure builders
- `tests/test_diff.py` — diff function + diff routes
- `tests/test_search.py` — search routes
- `tests/test_refresh.py` — POST /refresh + SSE contract (uses `printf`
  as a fake subprocess — never runs the real pipeline)
- `tests/test_fragments.py` — HTMX fragment endpoints
- `tests/test_counterpoints.py` — markdown parsing
- `tests/test_chips_date_aware.py` — date-aware chip lookup (M1 issue #3)
- `tests/test_smoke.py` — 7-step user-flow walkthrough
- `tests/test_schema_evolution.py` — ingest 5 mixed-schema briefings;
  every route returns 200

## Architecture notes

- **No build step.** HTMX + Plotly served from CDN; no Webpack, no npm,
  no compilation.
- **No auth.** Single-user localhost (per architecture doc).
- **No background timer.** Refresh fires only when the user clicks
  the button.
- **Single-flight refresh.** Concurrent POSTs to `/refresh` return 409
  with the existing `job_id`; the SSE connection can tail an in-flight
  job from any browser tab.
- **Thread-backed subprocess.** `jobs.py` runs `subprocess.Popen` on a
  daemon thread (not `asyncio.create_subprocess_exec`) so the SSE
  consumer's event loop closing — which TestClient does between requests
  — doesn't cancel the worker. The SSE handler does `asyncio.to_thread`
  on the `queue.Queue.get()` to stay non-blocking.

## Not in scope (saved for M3)

- Trade execution
- Auth / multi-user
- Custom theme system
- Backtest visualizer
- Notifications / email / push
- Mobile-native app (Tailscale + responsive Pico covers it)
