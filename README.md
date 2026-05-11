# portfolio-briefing

Daily, deterministic portfolio briefing generator for a multi-account E*TRADE
options portfolio. Pulls live holdings, refreshes market data in parallel,
runs the book through a stack of skill-based decision engines, and writes a
single canonical markdown report per trading day. Recommendations come with
trade tickets, rationale, expected-value math, and tax annotations — all
backed by real chain data, not synthesized strikes.

```
┌─────────────────────────────────────────────────────────────────────┐
│ run_briefing.py --etrade-live                                       │
│                                                                     │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │ E*TRADE    │   │ yfinance     │   │ recommendation-list      │  │
│  │ accounts/  │──▶│ technicals + │   │ Google Sheet (BUY/SELL)  │  │
│  │ positions/ │   │ chains in    │   └──────────────────────────┘  │
│  │ Greeks     │   │ parallel     │                  │               │
│  └────────────┘   └──────────────┘                  │               │
│         │                │                          │               │
│         ▼                ▼                          ▼               │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ Snapshot (state/briefing_snapshots/YYYY-MM-DD/)            │    │
│  └────────────────────────────────────────────────────────────┘    │
│                            │                                        │
│            ┌───────────────┼────────────────┐                       │
│            ▼               ▼                ▼                       │
│   ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐          │
│   │ wheel-roll- │  │ defensive-  │  │ long-term-       │          │
│   │ advisor     │  │ collar      │  │ opportunity      │          │
│   │             │  │ advisor     │  │ advisor          │          │
│   └─────────────┘  └─────────────┘  └──────────────────┘          │
│                            │                                        │
│                            ▼                                        │
│   ┌────────────────────────────────────────────────────────┐       │
│   │ trade-validator (EV / break-even / verdict)            │       │
│   │ wash-sale-tracker · earnings-guard · concentration     │       │
│   └────────────────────────────────────────────────────────┘       │
│                            │                                        │
│                            ▼                                        │
│   ┌────────────────────────────────────────────────────────┐       │
│   │ pre-flight-verifier (4 gates)                          │       │
│   │   live-data-policer → broker-position-reconciler →     │       │
│   │   briefing-data-verifier → briefing-quality-gate       │       │
│   └────────────────────────────────────────────────────────┘       │
│                            │                                        │
│                            ▼                                        │
│   ┌────────────────────────────────────────────────────────┐       │
│   │ ~/Documents/briefings/latest.md  +  briefing_DATE.md   │       │
│   └────────────────────────────────────────────────────────┘       │
└─────────────────────────────────────────────────────────────────────┘
```

## Quick start

```bash
# 1. Clone
git clone git@github.com:webdev/portfolio-briefing.git
cd portfolio-briefing

# 2. Install Python deps (3.10+)
pip install pyetrade yfinance python-dotenv structlog pyyaml

# 3. Set up E*TRADE consumer credentials
cp .env.example .env
# Edit .env, paste in your ETRADE_CONSUMER_KEY and ETRADE_CONSUMER_SECRET
# from https://us.etrade.com/etx/ris/apikey

# 4. One-time interactive OAuth (opens browser, asks for verifier code)
cd skills/daily-portfolio-briefing/scripts
python3 etrade_auth.py
# Tokens saved to ~/.config/portfolio-briefing/etrade_tokens.json

# 5. Run a briefing
cd ..
python3 scripts/run_briefing.py --config config/briefing.yaml --etrade-live
# Output lands at:
#   reports/daily/briefing_YYYY-MM-DD.md (in-repo)
#   ~/Documents/briefings/latest.md      (delivery)
#   ~/Documents/briefings/briefing_YYYY-MM-DD.md (dated copy)
```

## What's in the briefing

Each daily briefing has 17 sections rendered in a fixed order so day-over-day
diffing works. The major panels:

| Panel | What it shows |
|---|---|
| Header + Market Context | Date, regime (RISK_ON/CAUTION/RISK_OFF), VIX, SPY, action count |
| Health | NLV, cash, Net Greeks, deployed % |
| Stress Test | Coverage ratio with traffic light (<0.5 red, 0.5–0.7 yellow, >0.7 green) |
| Hedge Book | Current hedges + delta neutralization vs target by macro caution |
| Risk Alerts | Defensive rolls, collar expirations, expiration clusters, concentration drift |
| Today's Action List | Closes, trims, rolls, defensive collars — every line is a placeable order |
| Watch / Portfolio Review | Equities + options with per-position commentary |
| Income Opportunities | New CSP/CC ideas, validator-filtered for positive EV |
| 🔭 Long-Term Opportunities | ADD/TRIM/EXIT/HOLD + LEAP/LONG_DATED_CSP on a 3–12 month horizon |
| Strategy Upgrades | Recommended structural changes (e.g., switch from CSP to put spread) |
| Analyst Brief | Claude-powered synthesis (last, not first — narrative on top of dashboard) |
| Recommendation Changes | Diff against yesterday's third-party recs |
| Inconsistencies Flagged | Self-inconsistencies detected vs yesterday's briefing |
| Since Yesterday | Day-over-day diff |
| Appendix: Snapshot Manifest | Provenance + paths for the run's snapshot files |

## Architecture

### Skills (per-component decision engines)

Each skill owns one decision domain. Inputs come from the snapshot; outputs
are dataclass dicts the renderer formats. Skills don't import each other —
they're composed by the briefing orchestrator. Adding a new skill means
adding a directory, not modifying existing logic.

| Skill | Owns |
|---|---|
| `daily-portfolio-briefing` | Orchestrator + renderer + delivery |
| `wheel-roll-advisor` | Roll target selection (calendar / diagonal / out-and-up) with NORMAL/CAUTION/RISK_OFF cells |
| `defensive-collar-advisor` | 3-leg collar proposals when an existing CC + protective put is needed |
| `long-term-opportunity-advisor` | 3–12 month equity ADD/TRIM/EXIT plus LEAP / long-dated CSP ideas |
| `recommendation-list-fetcher` | Pulls third-party BUY/HOLD/SELL list from a Google Sheet, normalizes tiers |
| `briefing-directives` | Persistent user directives ("don't roll AAPL until earnings") with trigger evaluators |
| `trade-validator` | Probability-weighted EV / break-even / verdict per trade. **POOR EV on new trades is filtered; on management trades it's badged.** See `skills/trade-validator/SKILL.md` for the canonical filtering rules. |
| `yield-calculator` | CSP / CC / roll yield math (one place, not scattered) |
| `wash-sale-tracker` | 30-calendar-day wash-sale ledger, blocks new trades on names with recent loss closures |
| `live-data-policer` | Hard gate: every recommendation must trace to a live data source. Stub-derived prices block the briefing. |
| `broker-position-reconciler` | Compares snapshot positions vs broker truth; surfaces drift |
| `briefing-data-verifier` | Scans the rendered markdown for chain-attribution markers on every actionable line |
| `briefing-quality-gate` | 4-persona structural validator (financial-advisor, options-trader, risk-manager, copy-editor) |
| `pre-flight-verifier` | Master gate orchestrator. Runs the four above and emits RELEASE / WARN / BLOCKED |

### Briefing pipeline (11 steps)

`scripts/run_briefing.py` orchestrates the run:

1. **Pre-flight** — load config, find yesterday's briefing for diffing.
2. **Load directives** — read `state/directives/index.yaml`, evaluate which fire today.
3. **Fetch recommendations** — run `recommendation-list-fetcher`, persist to snapshot.
4. **Snapshot inputs** — pull E*TRADE accounts/positions/balance via the in-repo adapter, then parallel-fetch yfinance technicals (RSI, IV-rank, 200-SMA, drawdown), earnings dates, and option chains. ~22 underlyings × 3 fetch types in ~6 seconds with 16 workers.
5. **Classify regime** — VIX + SPY thresholds → RISK_ON / CAUTION / RISK_OFF.
6. **Review equities** — per-position decision against the equity matrix.
7. **Review options** — per-contract review through `wheel-roll-advisor`'s decision walker (CLOSE NOW → DEFENSIVE ROLL → TAKE PROFIT → WATCH → HOLD).
8. **New ideas** — high-IV CSP/CC opportunities filtered by validator + wash-sale + earnings + concentration.
9. **Long-term opportunities** — `long-term-opportunity-advisor` runs against the held universe + recommended-but-not-held.
10. **Consistency check** — day-over-day diff. Recommendations don't flip without a trigger event.
11. **Aggregate + render + quality gate + deliver** — assemble markdown, run pre-flight gates, write to `reports/daily/` and copy to `~/Documents/briefings/`.

### Hard rules (load-bearing constraints)

These rules govern recommendation rendering. Every actionable line must
satisfy all of them or be filtered:

- **Live data on every actionable trade.** Underlying price, IV rank, technical consensus, and chain validity must come from this run's fetches. Missing data → "verify before placing" advisory + skip the order ticket.
- **Chain validation.** Synthesized dates (`today + 30`) get snapped to a real chain expiration before rendering, or dropped. `Sat`/`Sun` on an option contract is a bug.
- **Macro caution gate.** When `_market_caution == "high"`, new put recommendations print a DEFER reason and skip the actionable ticket. Existing covered calls (defensive) stay actionable.
- **Concentration check (post-sizing).** Verify `existing_pct + new_collateral / NLV ≤ 10%` before recommending; pre-sizing alone is insufficient.
- **Earnings guard.** No new puts when `next_earnings ≤ expiration`. No LEAP buys when next earnings within 5 days.
- **Tail-risk gate.** Chinese ADRs, single-binary biotechs, crypto-proxies, and high-borrow memes get blocked from new puts and surfaced with `⚠ TAIL RISK` on existing positions.
- **Trade-validator filtering — Category A vs B.** NEW trades (PULLBACK CSP, new CSP, new BTO LEAP, opening strangle/spread) with verdict `POOR` or `BLOCK` are filtered out of the action list — only a transparency footer mentions them. POSITION MANAGEMENT trades (DEFENSIVE ROLL, DEFENSIVE COLLAR, EXECUTE ROLL, CLOSE NOW, TAKE PROFIT) are rendered regardless of verdict (but `BLOCK` still drops). The full table is in `skills/trade-validator/SKILL.md`.

### Data layout

```
portfolio-briefing/
├── .env                                     # gitignored — your consumer credentials
├── .env.example                             # template
├── README.md                                # this file
├── docs/                                    # design specs (00–11)
├── etrade-mcp/                              # MCP server (optional alternative path)
└── skills/
    ├── daily-portfolio-briefing/
    │   ├── config/briefing.yaml             # tunable thresholds (account routes, LTCG rate, etc.)
    │   ├── scripts/
    │   │   ├── run_briefing.py              # orchestrator entry point
    │   │   ├── etrade_auth.py               # in-repo OAuth (load/save/renew/interactive)
    │   │   ├── renew_etrade_token.py        # heartbeat renewer (called by hourly task)
    │   │   ├── run_briefing_scheduled.sh    # launchd/Cowork wrapper
    │   │   ├── adapters/                    # E*TRADE account/portfolio/market clients
    │   │   ├── steps/                       # one file per orchestrator step
    │   │   ├── render/                      # one file per markdown panel
    │   │   ├── analysis/                    # stress, expirations, hedge book, drift
    │   │   └── tests/                       # 80 unit + integration tests
    │   ├── reports/daily/                   # gitignored — per-run output
    │   ├── state/                           # gitignored — directives, snapshots, ledger
    │   └── launchd/                         # macOS launchd plist (alternative to Cowork)
    └── (one directory per skill)

~/.config/portfolio-briefing/
└── etrade_tokens.json                       # OAuth tokens, mode 0600

~/Documents/briefings/
├── latest.md                                # rolling pointer
├── briefing_YYYY-MM-DD.md                   # dated copy
├── briefing_YYYY-MM-DD.json                 # machine-readable companion
└── logs/
    ├── briefing_YYYY-MM-DD.log              # scheduled run output
    ├── renewer_YYYY-MM-DD.log               # heartbeat history
    └── etrade_token_dead.txt                # appears when re-auth needed
```

## Operating it

### Daily routine (steady state)

1. Wake up. If yesterday's briefing failed overnight or the heartbeat detected a dead token, you'll see a notification from the `etrade-token-heartbeat` task (it stays silent on success).
2. If you got a "re-auth needed" ping or saw `~/Documents/briefings/logs/etrade_token_dead.txt`:
   ```bash
   cd ~/workspace/portfolio-briefing/skills/daily-portfolio-briefing/scripts
   python3 etrade_auth.py
   ```
3. Otherwise: do nothing. The 8 AM ET briefing has already landed at `~/Documents/briefings/latest.md`. Open it.
4. The hourly heartbeat keeps tokens warm 6 AM–8 PM ET so any ad-hoc API work mid-day doesn't 401.

### Scheduled tasks

Two recurring tasks should be registered (either as Cowork scheduled tasks or via the launchd plist):

| Task | Schedule | Purpose |
|---|---|---|
| `daily-portfolio-briefing` | Mon–Fri 08:00 ET | Run `run_briefing.py --etrade-live`, deliver to `~/Documents/briefings/` |
| `etrade-token-heartbeat` | Hourly Mon–Fri 06:00–20:00 ET | Call `renew_etrade_token.py` to reset the 2h idle timer |

The Cowork prompts for both tasks are stored in `~/Documents/Claude/Scheduled/`. The launchd alternative lives at `skills/daily-portfolio-briefing/launchd/` — see `INSTALL.md` there for the `launchctl bootstrap` commands.

### Manual run

```bash
cd ~/workspace/portfolio-briefing/skills/daily-portfolio-briefing
python3 scripts/run_briefing.py --config config/briefing.yaml --etrade-live
```

Useful flags:

| Flag | What it does |
|---|---|
| `--etrade-live` | Pull real positions from E*TRADE (default is to require this or `--etrade-fixture`) |
| `--etrade-fixture path/to.json` | Use a mock holdings JSON for testing without hitting E*TRADE |
| `--dry-run` | Build the briefing but print to stdout instead of writing files |
| `--no-delivery` | Skip the `~/Documents/briefings/` copy step |
| `--delivery-dir PATH` | Override delivery destination |
| `--force` | Re-run even if today's briefing already exists |

### Environment variables

| Var | Default | What it does |
|---|---|---|
| `PORTFOLIO_BRIEFING_REPO` | `~/workspace/portfolio-briefing` | Repo root (used to find `.env`) |
| `PORTFOLIO_BRIEFING_TOKEN_FILE` | `~/.config/portfolio-briefing/etrade_tokens.json` | Where OAuth tokens live |
| `PORTFOLIO_BRIEFING_DELIVERY_DIR` | `~/Documents/briefings` | Where the briefing is copied |
| `PORTFOLIO_BRIEFING_LOG_DIR` | `~/Documents/briefings/logs` | Renewer + scheduled-task logs |
| `PORTFOLIO_BRIEFING_FETCH_WORKERS` | `16` | Parallel yfinance worker count. Drop to 8 if you hit rate limits. |
| `PORTFOLIO_BRIEFING_PYTHON` | `/usr/bin/python3` | Python interpreter for scheduled wrapper |
| `PORTFOLIO_BRIEFING_ENV` | `$PORTFOLIO_BRIEFING_REPO/.env` | Override the .env path |
| `ETRADE_CONSUMER_KEY` | _(from .env)_ | E*TRADE app consumer key |
| `ETRADE_CONSUMER_SECRET` | _(from .env)_ | E*TRADE app consumer secret |

## E*TRADE token lifecycle

E*TRADE OAuth 1.0a access tokens have two expiration mechanisms:

- **Idle timeout** — 2 hours without an API call. Renewable via `/oauth/renew_access_token` (no user interaction). The hourly heartbeat task hits this.
- **Hard expiry** — midnight ET, every day. Requires the full browser OAuth flow to refresh. **There is no headless workaround for this** — it's by design from E*TRADE.

So the daily routine is: re-auth once each morning (if needed), then the heartbeat keeps tokens warm through 8 PM. The heartbeat detects dead tokens on the next run and surfaces a re-auth prompt; on success it's silent.

If you want fully headless authentication, you'd need to migrate the data layer to a broker with OAuth 2 refresh tokens (Alpaca, IBKR, Tradier, Schwab). That's a separate project — for now E*TRADE stays the source of truth.

## Design principles

- **Deterministic frameworks over if/else logic.** Decision matrices live in YAML with stable cell IDs. Same inputs → same output. Tuning means editing a parameter, not patching branches.
- **Persistent state across runs.** Theses (what we believe about positions) and directives (what the user has decided) carry day-to-day. The system remembers what you said yesterday.
- **Snapshot every input.** Each run persists every input it received under `state/briefing_snapshots/YYYY-MM-DD/`. Any past briefing is replayable for debugging.
- **Day-over-day consistency check.** Recommendations don't flip without an explicit trigger event. If today says TRIM where yesterday said HOLD, the briefing must point to a price level, news item, earnings, or technical break — otherwise it surfaces "self-inconsistency detected" rather than publishing.
- **Read-only against brokerages.** No order placement. The briefing tells you what to do; you place trades manually at the broker.
- **Live data backing on every actionable line.** Synthesized prices and stale snapshots are blocked at the pre-flight gate.
- **No directional forecasts.** The system flags conditions (RSI extremes, IV-rank percentiles, earnings clusters, FOMC proximity) that historically precede drawdowns. It does not predict 30-day SPY direction. Recommendation rationale phrases outcomes probabilistically.

## Development

### Testing

```bash
cd skills/daily-portfolio-briefing
python3 -m pytest scripts/tests/ -q       # 80 tests
python3 -m pytest -k test_long_term       # filter
```

Each skill has its own `scripts/tests/`. Most fixtures are JSON snapshots from real briefing runs, scrubbed of position-level NLV.

### Adding a new skill

1. Create `skills/<name>/` with `SKILL.md` (YAML frontmatter + body), `scripts/`, `references/`.
2. Wire it into the orchestrator at the right step in `scripts/run_briefing.py`. Convention: skills don't import each other — add a `steps/<name>.py` shim that imports the skill via `importlib.util.spec_from_file_location` if there's a name collision (we have two `advise.py` modules in this repo, so the long-term advisor's wiring uses this pattern — see `steps/long_term_opportunities.py` for the reference implementation).
3. Add tests to `skills/<name>/scripts/tests/`.
4. If your skill emits actionable trades, follow the canonical filter rules in `skills/trade-validator/SKILL.md` (filter POOR/BLOCK on new opens; render with badge on position management).

### Quality gates

Pre-flight runs four gates in order; first BLOCK aborts the briefing:

1. **live-data-policer** — every actionable line must have provenance metadata pointing at a fresh source (`etrade_live`, `yfinance`, etc.). Stub-derived prices block.
2. **broker-position-reconciler** — the snapshot's positions must match broker truth. In live mode this is trivially satisfied (snapshot = broker truth); in fixture mode it compares against a manual override at `state/broker_positions.json`.
3. **briefing-data-verifier** — scans the rendered markdown for chain-attribution markers (`current bid $X / mid $Y / ask $Z`, `**Source:** Live E*TRADE chain`) on every NEW-trade action. Equity actions, closes, and reviews are exempt.
4. **briefing-quality-gate** — 4-persona structural check (financial-advisor, options-trader, risk-manager, copy-editor). Catches Saturday expirations on actual options, missing yield lines, missing impact summary, etc.

The verdict (RELEASE / WARN / BLOCKED) appears at the top of every briefing. WARN is informational; BLOCKED prepends "🚫 PRE-FLIGHT BLOCKED — DO NOT ACT ON THIS BRIEFING" so you don't accidentally trade against a flagged briefing.

## Troubleshooting

| Symptom | Likely cause + fix |
|---|---|
| `401 Client Error: Unauthorized` from E*TRADE | Token hard-expired at midnight ET. Run `python3 skills/daily-portfolio-briefing/scripts/etrade_auth.py`. |
| `ETRADE_CONSUMER_KEY ... must be set` | `.env` missing or in wrong location. Check `$PORTFOLIO_BRIEFING_REPO/.env`. |
| Briefing shows `🚫 PRE-FLIGHT BLOCKED` | Read the blocking gate name in the banner. Most common: `live_data_policer` (snapshot wasn't actually fresh) or `quality_gate` (a structural rule fired). |
| `🔴 Live-Data Verification` advisory at the top | Some recommendations lack chain attribution. The briefing still releases (advisory, not blocking). Verify those tickets at the broker before placing. |
| yfinance returns `429 Too Many Requests` | Drop `PORTFOLIO_BRIEFING_FETCH_WORKERS` to 8. yfinance hammers a single backend; >32 concurrent calls trips rate limits. |
| `etrade_token_dead.txt` exists in logs dir | Heartbeat detected dead tokens. Re-auth via `etrade_auth.py`; the file auto-clears on next successful renewal. |
| "POOR EV" CSP not in action list, only in footer | Working as designed. New trades with poor EV are filtered; the footer tells you which were rejected. See `skills/trade-validator/SKILL.md`. |
| `'origin' does not appear to be a git repository` after `git push` | `git remote add` failed silently earlier in the chain. Check `git remote -v`; if empty, re-add and push. |

## Status

| Component | Status |
|---|---|
| E*TRADE adapter (account / portfolio / market data) | Working |
| Parallel yfinance fetcher (technicals + chains) | Working |
| In-repo OAuth (load / save / renew / interactive) | Working |
| Token heartbeat renewer + hourly schedule | Working |
| 5-pillar briefing (closes / trims / rolls / income / long-term) | Working |
| Pre-flight verifier (4 gates) | Working |
| Trade validator with category-aware filtering | Working |
| Daily 8 AM ET scheduled task | Working |
| Delivery to `~/Documents/briefings/` | Working |
| Wave 23 production wiring | Complete |
| Headless auth (alternative broker) | Not started |

## License

Private project. No license granted.
