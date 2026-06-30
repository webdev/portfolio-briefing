# Portfolio Briefing — Web App Architecture

**Status:** Proposal · 2026-06-30
**Author:** Architecture agent (Plan mode), Claude
**Audience:** Single developer or Claude implementing the web app in 1-2 weeks

---

## 0. TL;DR

- **Stack:** FastAPI (Python 3.12) + DuckDB (reads JSON in place) + Jinja2 + HTMX + Plotly + Pico.css, all in one `uvicorn` process on `localhost:8765`.
- **Why:** matches the user's Python-first stack 1:1, zero JS build chain, real interactivity without Streamlit's UI ceiling, and DuckDB reads the existing JSON snapshots **without an ETL job** — the pipeline keeps writing exactly what it does today.
- **Schema evolution:** DuckDB's `read_json_auto(..., union_by_name=true)` unifies the differently-shaped historical snapshots into one superset; Pydantic V1→V2 models coerce forward at the FastAPI boundary.
- **Run-vs-read:** strict read-only against existing snapshots by default. A "Refresh briefing" button shells out to `uv run python scripts/run_briefing.py --etrade-live --force --refresh-scout` (single-flight, SSE log tail) as an explicit user action — never on a timer.
- **Deploy:** `uv run uvicorn app.main:app` on the laptop. Tailscale-fronted later if remote access is wanted. No cloud needed, ever.

---

## 1. System diagram

```
┌──────────────────────── EXISTING (sacred, untouched) ────────────────────────────┐
│  E*TRADE OAuth ──► run_briefing.py ──► state/briefing_snapshots/<DATE>/          │
│  yfinance / FMP   (10 steps,           ├── balance.json, positions.json,         │
│  Parkev sheet      ~daily, manual)     ├── quotes.json, technicals.json,         │
│                                         ├── earnings.json, iv_ranks.json,        │
│                                         ├── open_orders.json, theses.json,       │
│                                         ├── recommendations_list.json,           │
│                                         ├── regime.json, ytd_pnl.json,           │
│                                         └── chains/<TICKER>_<EXP>.json (~100)    │
│                                                                                  │
│                 ──► ~/Documents/briefings/briefing_<DATE>.{md,json}              │
│                     reports/daily/{candidates,when_to_enter}_<DATE>.md           │
└──────────────────────────────────┬───────────────────────────────────────────────┘
                                   │ filesystem (read-only)
                                   ▼
┌──────────────── NEW: webapp/ (this proposal) ────────────────────────────────────┐
│  ingest (Python)                                                                 │
│    • scan briefing_snapshots/ + ~/Documents/briefings/                           │
│    • DuckDB views over JSON globs (zero-copy, in place)                          │
│    • materialize 3 hot tables: portfolio_timeseries, positions_timeseries,       │
│      parkev_history                                                              │
│                          │                                                       │
│                          ▼                                                       │
│  FastAPI (uvicorn :8765)                                                         │
│    GET  /                  → dashboard shell                                     │
│    GET  /briefing/<date>   → full briefing render                                │
│    GET  /positions/<tk>    → ticker drill-down                                   │
│    GET  /charts/*.json     → Plotly figure JSON                                  │
│    GET  /history/parkev/<tk>                                                     │
│    GET  /diff/<a>/<b>      → side-by-side briefing diff                          │
│    POST /refresh           → spawns run_briefing.py, single-flight               │
│    GET  /events            → SSE: refresh log tail + ingest_complete event       │
│                          │                                                       │
│                          ▼                                                       │
│  browser (no build step) — Jinja2 + HTMX swaps + Plotly.js from CDN + Pico.css   │
└──────────────────────────────────────────────────────────────────────────────────┘

Optional, later:
  Tailscale  ── localhost:8765 to the user's phone/iPad via MagicDNS, no code change.
  Caddy      ── if ever hosted: TLS + Basic Auth in ~8 lines of Caddyfile.
```

The pipeline writes; the web app reads. They share **only the filesystem.**

---

## 2. Tech stack — per-layer rationale

| Layer            | Choice                       | Why                                                                                       |
| ---------------- | ---------------------------- | ----------------------------------------------------------------------------------------- |
| Language         | **Python 3.12**              | Matches the existing pipeline. Skill reuse on day one.                                    |
| Web framework    | **FastAPI**                  | Async-friendly, Pydantic = free schema validation = the schema-evolution lever.           |
| Server           | **uvicorn**                  | `uv run uvicorn app:app --reload` is one line.                                            |
| Templating       | **Jinja2**                   | Ships with FastAPI. No JSX, no build step.                                                |
| Interactivity    | **HTMX**                     | Server returns HTML fragments, swaps into the DOM by `id`. No SPA framework needed.       |
| Charting         | **Plotly (Python + JS)**     | Python emits JSON spec → Plotly.js renders. Interactive out of the box.                   |
| Data store       | **DuckDB**                   | Reads JSON in place with `read_json_auto`. Analytical queries over 37+ snapshots in ms.   |
| Models           | **Pydantic v2**              | Coerce N versions of briefing JSON into one typed model at the boundary.                  |
| Styling          | **Pico.css (classless)**     | Looks professional without designing. 10 KB. Override 5 vars for dark mode.               |
| Testing          | **pytest** + `TestClient`    | Same harness the pipeline already uses.                                                   |
| Process manager  | none locally; **launchd**    | A plist or `tmux` is enough. systemd-equivalent if it ever leaves the laptop.             |

---

## 3. Data model

### 3.1 The two JSON populations (real numbers from the repo)

| Population                | Path                                                          | Per-file size | Growth     | Role                                                  |
| ------------------------- | ------------------------------------------------------------- | ------------- | ---------- | ----------------------------------------------------- |
| Snapshot inputs (per date)| `state/briefing_snapshots/<DATE>/*.json` + `chains/*.json`    | 5–7 MB/day    | ~6 MB/day  | Raw facts: positions, balance, quotes, chains, etc.   |
| Briefing summary (per date)| `~/Documents/briefings/briefing_<DATE>.json`                 | 45–90 KB/day  | ~70 KB/day | Rendered facts: equity_reviews, options_reviews, actions, etc. |

**Today's count:** ~37 snapshot directories on disk + ~25 in delivery folder. 5-year footprint ≈ 11 GB. Trivial for a laptop.

### 3.2 Storage choice: keep JSON as truth, use DuckDB as the engine

Do **not** migrate JSON into a relational schema. The JSON IS the source of truth — `run_briefing.py` writes it, and a rewrite breaks pipeline assumptions. Instead declare DuckDB views ON TOP of the JSON files:

```sql
CREATE OR REPLACE VIEW briefings_raw AS
SELECT
    regexp_extract(filename, 'briefing_(\d{4}-\d{2}-\d{2})\.json$', 1) AS date,
    *
FROM read_json_auto(
    '/Users/.../briefings/briefing_*.json',
    filename = true,
    union_by_name = true,           -- critical: handles schema evolution silently
    maximum_object_size = 10485760
);

CREATE OR REPLACE VIEW positions_raw AS
SELECT
    regexp_extract(filename, 'briefing_snapshots/(\d{4}-\d{2}-\d{2})/', 1) AS date,
    unnest(positions, recursive := true) AS position
FROM read_json_auto(
    '/Users/.../state/briefing_snapshots/*/positions.json',
    filename = true, union_by_name = true
);
```

`union_by_name = true` is the entire schema-evolution story: missing fields become NULL, new fields appear in later rows, you query the **superset.** No migration scripts, ever.

### 3.3 Materialized projections (for hot dashboards, refreshed on ingest)

```sql
-- 1. Time-series: ~37 rows today, one per date. <50 KB.
CREATE OR REPLACE TABLE portfolio_timeseries AS
SELECT date::DATE AS date, nlv, cash, cash/NULLIF(nlv,0) AS cash_pct, regime
FROM briefings_raw ORDER BY date;

-- 2. positions × date — for ticker drill-downs.
CREATE OR REPLACE TABLE positions_timeseries AS
SELECT date::DATE AS date, position.symbol, position.assetType, position.qty,
       position.price, position.qty*position.price AS market_value,
       position.type AS opt_type, position.strike, position.expiration
FROM positions_raw;

-- 3. Parkev rating history.
CREATE OR REPLACE TABLE parkev_history AS
SELECT date::DATE AS date,
       unnest(recommendations_list).ticker,
       unnest(recommendations_list).recommendation,
       unnest(recommendations_list).rating_tier,
       unnest(recommendations_list).conviction,
       unnest(recommendations_list).age_days
FROM read_json_auto('/Users/.../briefing_snapshots/*/recommendations_list.json',
                    filename = true, union_by_name = true);
```

All three tables total **<1 MB**. Full re-materialize on every refresh; takes seconds.

### 3.4 Versioned Pydantic models (DuckDB ↔ FastAPI boundary)

```python
class BriefingV1(BaseModel):
    date: date; nlv: float; cash: float; regime: str
    equity_reviews: list[EquityReview]
    options_reviews: list[OptionsReview]
    new_ideas: list[NewIdea]
    long_term_opportunities: list[Opportunity]
    strategy_upgrades: list[StrategyUpgrade]
    consistency_report: ConsistencyReport

class BriefingV2(BriefingV1):
    actions: list[ActionItem] = Field(default_factory=list)   # added 2026-06-30+

def load_briefing(raw: dict) -> BriefingV2:
    raw.setdefault("actions", [])
    return BriefingV2.model_validate(raw)
```

Principle: **history is upgraded forward, never the reverse.** New optional fields default to None / [].

---

## 4. Backfill strategy

### 4.1 The non-event

This is the whole reason for picking DuckDB-over-JSON: there is no migration step. On first boot:

1. `CREATE VIEW` over the JSON glob → views queryable immediately.
2. Run the three `CREATE TABLE … AS SELECT` projections → materialized into `webapp/data/briefings.duckdb`.
3. Stamp `~/.config/portfolio-briefing/webapp_ingest_state.json` with last-seen snapshot date.

On every subsequent boot / `POST /refresh`: list JSONs newer than the stamp, re-run the three projections (full re-materialize beats incremental for <1 MB tables), update the stamp. ~50 lines of Python.

### 4.2 Real oddities in the dataset

- The earliest snapshot (`2026-05-08`) has standalone `equity_reviews.json` and `options_reviews.json`; newer snapshots inline these into the briefing JSON. **Per-step files are informational; briefing summary JSON is canonical.**
- `intrinsic_value_cache.json` and `scout_cache.json` sit at the **`briefing_snapshots/` root**, NOT per-date. Skip them in the time-series view (they're caches, not facts).
- Two `.DRAFT.json` files exist (the `--force` quality-gate fallback writes these). **Exclude `*.DRAFT.json`** from the time-series.
- Only `2026-06-30` so far carries the `actions` array. The V1→V2 coercion handles this with a one-liner default.

### 4.3 Reconciliation invariant for the footer

After ingest: row count in `portfolio_timeseries` must equal the count of valid briefing JSONs found. Surface in the dashboard footer: **"37 / 37 snapshots loaded · last ingest 12s ago"**.

---

## 5. API surface

REST, JSON-or-HTML based on `Accept`. HTMX endpoints return fragments; chart endpoints return Plotly JSON.

**Page routes (HTML):**
- `GET /` — dashboard shell: latest briefing rolled up (regime, NLV, coverage, top 5).
- `GET /briefing/{date}` — full render for one date.
- `GET /briefing/latest` — redirect to the latest snapshot.
- `GET /positions` — all positions today, sortable, Parkev chip per row.
- `GET /positions/{ticker}` — per-ticker drill-down: price + qty + options history.
- `GET /options` — all open options grouped by underlying.
- `GET /options/expiration-ladder` — calendar/heatmap of short-put expirations.
- `GET /parkev/{ticker}` — Parkev rating + conviction history for one ticker.
- `GET /history` — time-series charts page (NLV, coverage, cash %, regime tape).
- `GET /diff/{date_a}/{date_b}` — side-by-side briefing diff.

**Fragment routes (HTMX swaps):**
- `GET /fragment/recommendation/{key}` — single recommendation card with annotations.
- `GET /fragment/red-flags` — live red-flags panel (5–10s cache).
- `GET /fragment/refresh-status` — polling target during a refresh.

**Chart routes (Plotly JSON):**
- `GET /charts/nlv.json?days=90` — NLV line + cash overlay.
- `GET /charts/coverage.json?days=90` — coverage ratio with 0.5x red zone.
- `GET /charts/expiration-ladder.json` — bar chart, put-obligation by expiry.
- `GET /charts/regime.json` — RISK_ON/OFF tape + VIX overlay.
- `GET /charts/parkev/{ticker}.json` — tier + conviction over time.

**Action routes:**
- `POST /refresh` — single-flight `subprocess.Popen` of `run_briefing.py --etrade-live --force --refresh-scout`. Returns `202` + job id; concurrent calls get `409` + the existing id.
- `GET /events` — SSE: refresh log lines + `ingest_complete` event so the dashboard auto-reloads when ready.

`/refresh` is the **only** state-changing endpoint and the **only** one that touches the pipeline.

---

## 6. Frontend stack

### Why HTMX + Jinja2 (not React, not Streamlit, not Reflex)

| Option              | UI ceiling      | JS surface for the dev   | Lock-in to one framework  | Verdict                                                            |
| ------------------- | --------------- | ------------------------ | ------------------------- | ------------------------------------------------------------------ |
| Streamlit           | Low (rigid)     | Zero                     | High                      | Hits a wall fast — you'll be cramming Markdown into `st.markdown`. |
| Dash                | Medium          | Light (callbacks)        | Medium                    | Better, still constrained.                                         |
| Reflex              | High            | Zero (compiles to React) | High (their compiler)     | Immature ecosystem — don't bet daily work on it.                   |
| Next.js / SvelteKit | Very high       | Massive                  | High                      | Wrong language for this user.                                      |
| **FastAPI + HTMX**  | High            | Trivial (`hx-get`)       | Low                       | **Pick this.**                                                     |

HTMX gives a Python back end with **real interactivity** (click a ticker, swap in the drill-down without a full reload) by sending HTML fragments — no JSON marshaling, no client state library, no Webpack.

### Charting: Plotly (Python emits, JS renders)

| Library           | Effort                        | Interactivity        | When to pick                                                    |
| ----------------- | ----------------------------- | -------------------- | --------------------------------------------------------------- |
| **Plotly Express**| `px.line(df, x="date", y="nlv")` | Hover/zoom/pan free | **Default — pick this.**                                        |
| Vega-Altair       | Elegant grammar, lower-level  | Yes                  | Worth growing into for v2. Not day-one.                         |
| Streamlit charts  | Streamlit-embedded only       | Limited              | Irrelevant — we're not on Streamlit.                            |
| ECharts / Recharts| Strong, JS-native             | Strong               | Forces a build step. Skip.                                      |
| Observable Plot   | Beautiful defaults            | OK                   | Forces a JS build per chart. Skip.                              |

Concretely: `app/charts/nlv.py` builds a `plotly.graph_objects.Figure`, calls `fig.to_json()`, FastAPI returns it from `/charts/nlv.json`, the page does `Plotly.newPlot('nlv', fig.data, fig.layout)` (3 lines of vanilla JS in the base template, never touched again).

### Styling: Pico.css — classless. Drop one `<link>` and tables/buttons/articles get a credible look without designing anything.

### State management
Effectively none on the client. Server holds it all in DuckDB + Pydantic. Browser holds URL + scroll position. The one stateful surface — the in-progress refresh — is an in-memory `dict[job_id, JobState]` on FastAPI, streamed to the browser via SSE.

---

## 7. Deployment

### 7.1 Local (the actual deployment)

```
cd /Users/gblazer/workspace/portfolio-briefing/webapp
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Open `http://localhost:8765`. Done.

Persistence: one `webapp/data/briefings.duckdb` file (10–50 MB) + `~/.config/portfolio-briefing/webapp_ingest_state.json`. Both regenerable from source JSON in one boot.

### 7.2 Remote access (Tailscale path) — zero code changes

Install Tailscale, enable MagicDNS. `http://<laptop-name>:8765` works from the user's phone/iPad on the tailnet. No auth, no TLS, no public exposure.

### 7.3 Hosted path (only if needed later)

1. `uv export --format requirements-txt > requirements.txt` → `FROM python:3.12-slim` Dockerfile.
2. Push to Fly.io / Railway (~$5/mo).
3. Caddy in front for TLS + Basic Auth (8 lines).
4. **Pipeline still runs locally** (E*TRADE OAuth lives on the user's machine). Sync `~/Documents/briefings/` to the hosted DuckDB via `rclone bisync` or a nightly `rsync` cron.

---

## 8. Implementation plan — two 5-day milestones

### Milestone 1 (Days 1–5): Read-only dashboard parity

| Day | Deliverable |
| --- | --- |
| 1 | `webapp/` scaffold. uv env. FastAPI `/` returns "hello". DuckDB connected with the three views from §3.2. pytest test: open DuckDB, count ≥30 rows in `briefings_raw`. |
| 2 | Pydantic V1/V2 models + loader. Jinja base + Pico + nav. `/briefing/{date}` renders regime + NLV + cash + top-5 holdings. |
| 3 | Render `equity_reviews` + `options_reviews` as tables. Render the action list. Reuse `parkev_chip.format_parkev_chip()` so chips are byte-identical to the markdown's. |
| 4 | Plotly endpoints (`nlv.json`, `coverage.json`, `expiration-ladder.json`). Wire onto `/history`. `portfolio_timeseries` table materialized on boot. |
| 5 | `/positions/{ticker}` drill-down. `/parkev/{ticker}` history. Empty-state copy, error pages, footer with snapshot count + last-ingest timestamp. End of week 1: usable daily. |

### Milestone 2 (Days 6–10): Interactive operations layer

| Day | Deliverable |
| --- | --- |
| 6 | `POST /refresh` + subprocess wrapper around `run_briefing.py`. Single-flight enforcement. `GET /events` SSE. Front-end button + log-tail panel. |
| 7 | `/diff/{date_a}/{date_b}` — positions added/removed, recommendations changed, NLV delta, Parkev flips. |
| 8 | HTMX fragments: click ticker → swap in 30-day price + Parkev mini-card. Render the counterpoint layer as an expandable disclosure under each action. |
| 9 | Cross-snapshot search: "every date META appeared in equity_reviews", "every red flag in June". One search bar; DuckDB does the heavy lifting. |
| 10 | Hardening: pytest coverage of every route, smoke test ingesting all snapshots from a fixture dir, README. |

### What we explicitly do **not** build in this window
- Trade execution (rule 7).
- Auth / multi-user.
- Custom theme system.
- Backtest visualizer (different product).
- Notifications / email / push (markdown delivery already exists).
- Mobile-native app (Tailscale + responsive Pico covers it).

---

## 9. What I'd specifically NOT use, and why

| Anti-rec | Why |
| --- | --- |
| **Postgres** | 216 MB of JSON on a single laptop. No concurrent writers, no remote replica need. DuckDB is the right scale; Postgres is two orders of magnitude over-engineered. |
| **SQLite as the time-series engine** | Fine for a key-value cache. The analytical queries here are exactly DuckDB's sweet spot and SQLite's weakness. |
| **Streamlit** | Wrong UI ceiling. The user will outgrow the layout primitives by day 3. |
| **Reflex** | Immature ecosystem, opinionated React compile that breaks if you stray off-path. |
| **Next.js / SvelteKit / any JS SPA** | Wrong language. Build chain, type-system shift, deployment story — all real costs for zero win on a one-developer app. |
| **Dash** | Acceptable second choice but heavier and more opinionated than FastAPI + HTMX. |
| **Flask** | Would work; FastAPI's automatic Pydantic validation is the right lever for the schema-evolution problem specifically. |
| **Django** | Overpowered. ORM + admin + auth + middleware all unused. |
| **TimescaleDB / InfluxDB** | Real-time streaming engines for a once-a-day briefing. Wrong product. |
| **Docker / docker-compose (locally)** | Indirection for zero benefit when the only consumer is the user's laptop. |
| **Kubernetes / nginx / gunicorn** | Not for this app. uvicorn alone serves one user fine. |
| **GraphQL** | One client, ~15 endpoints, all known. REST + Plotly-JSON is enough. |
| **Separate frontend repo** | One process, one repo, one `uvicorn` command. |
| **WebSockets** | The one streaming surface (`/events`) is SSE — simpler, browser-native, one-directional matches the use case exactly. |
| **Redis / Celery** | The only "background job" is the manual refresh, which is `subprocess.Popen`. |
| **Tailwind** | Designer-tool. Pico solves the same problem in 10 KB. |
| **Auth0 / Clerk / Supabase Auth** | One user. localhost. The auth story for v1 is "the keyboard." |

---

## 10. Critical files to reuse from the existing pipeline

- `/Users/gblazer/workspace/portfolio-briefing/skills/daily-portfolio-briefing/scripts/run_briefing.py` — the pipeline entry the refresh endpoint shells out to. **Do not modify.**
- `/Users/gblazer/workspace/portfolio-briefing/skills/daily-portfolio-briefing/scripts/steps/aggregate.py` — defines the briefing JSON shape. Mirror it in the V2 Pydantic model.
- `/Users/gblazer/workspace/portfolio-briefing/skills/daily-portfolio-briefing/scripts/analysis/parkev_chip.py` — reuse `format_parkev_chip()` so web app and markdown render byte-identical chips (hard rule #27).
- `/Users/gblazer/workspace/portfolio-briefing/skills/daily-portfolio-briefing/scripts/analysis/support_resistance.py` — needed to render S/R chips on position rows (hard rule #20).
- `/Users/gblazer/workspace/portfolio-briefing/CLAUDE.md` — the project conventions. Every renderer in the web app must comply.

**New files to be created** (sibling `webapp/` directory):
- `webapp/app/main.py` — FastAPI app + routes
- `webapp/app/ingest.py` — DuckDB views + materialized projections
- `webapp/app/models/briefing.py` — Pydantic V1/V2 + forward coercion
- `webapp/app/charts/*.py` — one module per Plotly figure
- `webapp/app/templates/*.html` — Jinja2 + HTMX
- `webapp/pyproject.toml` — `uv`-managed env: fastapi, uvicorn, jinja2, pydantic, duckdb, plotly, pico-css

---

## Open questions for the user

1. **Snapshot count discrepancy.** I count 37 directories in `state/briefing_snapshots/` and 25 distinct dates in `~/Documents/briefings/`. Earlier conversation referenced ~140 — confirm the right number.
2. **Multi-account future.** CLAUDE.md scopes everything to INDIVIDUAL today, but Roth IRA / Joint JTWROS are on the roadmap. Should the data model assume single-account today and refactor later, or model `account` as a dimension from day one?
3. **Refresh runtime.** Plan assumes `run_briefing.py --etrade-live --force --refresh-scout` completes in 1–2 min. If closer to 5 min, the SSE log-tail UX matters more and we'd want a "background, notify when done" affordance.
4. **Tailscale appetite.** Already installed? If yes, "view from phone" is free.
5. **Hosted ambition timeline.** Months or years? If months, factor Dockerfile into Milestone 2; if years, skip entirely.
