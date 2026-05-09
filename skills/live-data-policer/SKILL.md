---
name: live-data-policer
description: Enforces a "no cached data" policy on every briefing. Reads each data input's provenance metadata (source, fetched_at, fresh-flag) and BLOCKs release if any input is older than the configured staleness threshold. Single source of truth for "is this briefing built from live data?"
version: 1.0
---

# Live Data Policer

The pre-flight verifier checks structure and content. This skill verifies a different question: **was every piece of data this briefing was built on FETCHED LIVE, not pulled from cache?**

Without this gate, the briefing can pass every other check while still presenting yesterday's positions, last week's quotes, or a stale option chain — confidently lying to you.

## What it polices

For each required data source, the policer checks:
- `source`: must NOT be `"fixture"`, `"cache"`, `"replay"`, or anything matching cached-data patterns
- `fetched_at`: must be within the configured staleness threshold
- `fresh`: explicit boolean flag set by the snapshot pipeline

## Required data sources

These MUST be live in every briefing:

| Source | Max staleness | Required for |
|---|---|---|
| `positions` | 30 min during market hours, 24h overnight | All position-level recommendations |
| `broker_positions` | same as above | Reconciliation gate |
| `quotes` | 15 min during market hours | Yields, EV math, hedge sizing |
| `chains` | 30 min during market hours | Roll order tickets, CSP proposals |
| `iv_ranks` | 24h | Collar trigger logic |
| `earnings_calendar` | 24h | Earnings guard |

## Output

```python
{
  "live": True | False,
  "stale_sources": [
      {
          "source": "positions",
          "age_minutes": 1440,
          "max_allowed_minutes": 30,
          "actual_source": "fixture",
          "issue": "loaded from fixture file, not live broker"
      },
      ...
  ],
  "panel_md": "...",  # warning panel if any stale
  "verdict": "PASS" | "WARN" | "BLOCK"
}
```

## Verdict logic

- **PASS**: every required source was fetched live within its staleness window
- **WARN**: at least one optional source is stale, but no critical inputs failed
- **BLOCK**: positions, broker_positions, quotes, OR chains is stale → no briefing release

## Hook integration

Slots into `pre-flight-verifier` as Gate 0 (runs FIRST, before everything else). If Gate 0 fails, no other gates run — there's nothing trustworthy to validate.

## Configuration

```yaml
# references/staleness_thresholds.yaml
market_hours_only:
  positions: 30
  broker_positions: 30
  quotes: 15
  chains: 30
overnight:
  positions: 1440
  broker_positions: 1440
  quotes: 480
always:
  iv_ranks: 1440
  earnings_calendar: 1440

allowed_sources:
  positions: [etrade_live]
  broker_positions: [etrade_live, manual_override]
  quotes: [yfinance, etrade_live]
  chains: [etrade_live, yfinance]

block_sources:
  - fixture
  - cache
  - replay
  - stale
```

## Why this exists

A briefing built on cached data is INDISTINGUISHABLE from a live briefing in its rendered output. The action items look the same, the yields look the same, the order tickets look the same. Without a policer reading provenance metadata and BLOCKING on staleness, the only way the user finds out is by trying to execute a recommendation that fails at the broker — exactly the failure mode that surfaced the GOOG bug.

This skill's job is to refuse to ship a briefing whose data didn't come from a live source within minutes of generation.
