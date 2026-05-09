# portfolio-briefing

A skill-based daily portfolio briefing system. Pulls live holdings from E*TRADE, classifies the market regime, reviews every existing equity and option position against deterministic decision matrices, surfaces new ideas, and writes one canonical markdown report per trading day. Anchors recommendations to a persistent thesis store and a directive system so the briefing remembers what you said yesterday.

Replaces the algorithmic wheel bot at `~/workspace/wheelhouz` with a data-driven, auditable design.

## Status

**v0 — design locked, Wave 1 components built.** Wave 2 (orchestrator + integration) is next.

| Component | Path | Status |
|---|---|---|
| E*TRADE MCP server | `etrade-mcp/` | Built (account/position/order tools added to ohenak fork) |
| Wheel roll advisor | `skills/wheel-roll-advisor/` | Built — NORMAL regime cells; CAUTION/RISK_OFF + SHORT_CALL pending |
| Briefing directives | `skills/briefing-directives/` | Built — 5 directive types, 8 trigger evaluators |
| Recommendation list fetcher | `skills/recommendation-list-fetcher/` | Built — verified against live Google Sheet |
| Daily portfolio briefing orchestrator | `skills/daily-portfolio-briefing/` | Not built |

## Layout

```
portfolio-briefing/
├── docs/                     # design specs (read these first)
│   ├── 00-build-plan.md
│   ├── 01-etrade-mcp-spec.md
│   ├── 02-daily-portfolio-briefing-skill.md
│   ├── 03-wheel-roll-advisor-skill.md
│   ├── 04-from-wheelhouz-keep-drop.md
│   ├── 05-briefing-directives.md
│   ├── 06-wheel-parameters.md
│   ├── 07-tail-risk-names.md
│   ├── 08-wheel-decision-matrix.md
│   ├── 09-regime-framework.md
│   ├── 10-equity-decision-matrix.md
│   └── 11-recommendation-list-skill.md
├── etrade-mcp/               # MCP server (Python). Forked from ohenak/etrade-mcp.
└── skills/                   # one Claude skill per subdirectory
    ├── briefing-directives/
    ├── recommendation-list-fetcher/
    ├── wheel-roll-advisor/
    └── (daily-portfolio-briefing/  — orchestrator, to be built)
```

## Read order for new contributors

1. `docs/00-build-plan.md` — entry point. What we're building and why.
2. `docs/04-from-wheelhouz-keep-drop.md` — what we kept from the prior bot and what we deliberately dropped.
3. `docs/02-daily-portfolio-briefing-skill.md` — the orchestrator's workflow.
4. Whichever component spec is relevant to your work (`03-` for wheel, `05-` for directives, `09-` regime, `10-` equity, `11-` recommendations).

## Design principles (the short version)

- **Deterministic frameworks, not if/else logic.** Every recommendation comes from a YAML-loaded decision matrix with a stable cell ID. Same inputs → same output. Tuning means editing one parameter, not patching branches.
- **Persistent state across runs.** Theses (what we believe about positions) and directives (what the user has decided to do about them) carry day-to-day. Tomorrow's briefing knows what you said yesterday.
- **Snapshot every input.** Each run persists every input it received under `state/briefing_snapshots/YYYY-MM-DD/`, making any past briefing replayable for debugging.
- **Day-over-day consistency check.** Recommendations don't flip without an explicit trigger event. If today says TRIM where yesterday said HOLD, the briefing has to point to a price level, news item, earnings event, or technical break — otherwise it surfaces "self-inconsistency detected" rather than publishing.
- **Read-only against brokerages.** No order placement in v1. The briefing tells you what to do; you place trades manually.

## Setup

Each skill has its own setup instructions in its `README.md` or `SKILL.md`. The E*TRADE MCP requires an OAuth1 dance the first time (one-click-and-paste daily after that).

A consolidated setup runbook will live at `docs/SETUP.md` once Wave 2 is built.

## What's left to do

See `docs/00-build-plan.md` "Build order" section. In short:

1. Move `wheel-roll-advisor` matrix coverage from NORMAL-only to NORMAL/CAUTION/RISK_OFF for both PUT and CALL (~50 more matrix cells, ~70 more test fixtures).
2. Build `skills/daily-portfolio-briefing/` orchestrator that wires all four components together.
3. Smoke-test against E*TRADE sandbox.
4. Cut over to live account.
5. Tune for two weeks against real briefing output.

## License

Private project. No license granted. Domain knowledge migrated from `~/workspace/wheelhouz` (private) under the same terms.
