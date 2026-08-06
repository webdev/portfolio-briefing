---
name: claude-portfolio-fetcher
description: Fetch the public Autopilot "Claude Portfolio" holdings (tickers + weights) as a SECOND recommendation source for the daily-portfolio-briefing. Live-fetches the joinautopilot.com portfolio landing page, falls back to a manual seed file, caches 24h, and labels provenance. Use when the user references "the Claude portfolio", "Autopilot holdings", or when the daily briefing's pre-flight pulls second-source recommendations.
---

# Claude Portfolio Fetcher

## Overview

An input adapter (same family as `recommendation-list-fetcher`): it surfaces
what the public Autopilot "Claude Portfolio" currently holds and emits JSON
for the daily-portfolio-briefing. It does **not** make recommendations of
its own, and it **never fabricates holdings** — when no source is reachable
the output is an empty, provenance-labeled payload.

**Signal weight (deliberately low).** The Claude portfolio is an anonymous,
methodology-opaque source. Downstream consumers treat CP membership as
**corroboration only**:

- Ticker headers get a `🤖 CP-held` chip appended AFTER the Parkev chip.
- The rotation playbook adds `claude_portfolio.agreement_bonus` (default +1)
  ONLY when BOTH Parkev (a BUY-variant) AND the Claude portfolio agree on a
  name — the **agreement bonus**. CP membership alone NEVER qualifies a
  candidate, never overrides an RSI/earnings/tier gate, and never creates a
  standalone entry signal.

## Data source

1. **Live fetch** (`provenance: live_fetch`) — the public portfolio landing
   page `https://www.joinautopilot.com/landing/1/950048` embeds the holdings
   (symbol, name, `percentOfPortfolio`) in its Next.js flight data. The
   fetcher extracts them with a strict pattern + sanity checks (1–60
   holdings, weights summing near 100%). There is no stable public JSON API;
   the page IS the machine-readable source. The URL is config-driven
   (`source_url`) so a mirror/API can be swapped in without code changes.
2. **Manual seed** (`provenance: manual_seed`) — when the live fetch fails
   or parses empty, fall back to `config/claude_portfolio_manual.yaml`
   (a ticker list George pastes from the app, with an `as_of` date).
3. **Cache** (`provenance: cache`) — successful live fetches are cached for
   `cache_hours` (default 24) at `state/claude_portfolio_cache.json`.
4. **Unavailable** (`provenance: unavailable`) — no live data, no cache, no
   seed → empty holdings. Fail-open; never blocks the briefing.

## Usage

```bash
python3 scripts/fetch_claude_portfolio.py \
  --config config/claude_portfolio_config.yaml \
  --output /path/to/claude_portfolio.json

# Ignore the 24h cache
python3 scripts/fetch_claude_portfolio.py --force-refresh
```

Output shape:

```json
{
  "fetched_at": "2026-08-06T14:00:00Z",
  "as_of": "2026-08-06",
  "provenance": "live_fetch",
  "source_url": "https://www.joinautopilot.com/landing/1/950048",
  "holdings": [{"ticker": "NOW", "name": "SERVICENOW, INC.", "weight_pct": 9.09}],
  "tickers": ["NOW", "..."]
}
```

## Briefing integration

`run_briefing.py` Step 1.8 invokes this skill fail-open (alongside the
Parkev fetch) and stashes the payload as `snapshot_data["claude_portfolio"]`.
Consumers: `analysis/parkev_chip.py` (the `🤖 CP-held` chip),
`analysis/rotation_playbook.py` (the agreement bonus), config block
`briefing.yaml → claude_portfolio` (`enabled`, `agreement_bonus`).

## Maintenance

- If the page layout changes and the parse yields nothing, the fetcher
  degrades to the manual seed — refresh
  `config/claude_portfolio_manual.yaml` from the Autopilot app and update
  its `as_of`.
- Keep the manual seed dated; entries older than `manual_seed_max_age_days`
  (default 45) emit a staleness warning in the payload (still used — a
  dated list beats no list, but the user must see the age).

## Tests

```bash
python3 -m pytest scripts/tests/ -v
```
