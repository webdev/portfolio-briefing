# Daily Portfolio Briefing Skill

Orchestrator that produces a daily portfolio briefing covering market regime, position reviews, options roll decisions, and new ideas with deterministic decision matrices.

## Quick Start

```bash
# Using mock fixture (default, no auth needed)
python3 scripts/run_briefing.py \
  --config config/briefing.yaml \
  --etrade-fixture assets/etrade_mock_fixture.json \
  --output reports/daily/briefing_$(date +%F).md

# Dry-run (print to stdout, don't write files)
python3 scripts/run_briefing.py --dry-run

# Force re-run, overwriting today's
python3 scripts/run_briefing.py --force
```

## Architecture

The orchestrator runs 10 sequential steps:

1. **Pre-flight** — config, auth check, load yesterday's briefing
2. **Load directives** — evaluate active directives, transition expired ones
3. **Fetch recommendations** — third-party BUY/SELL/HOLD list (stubbed in v1)
4. **Snapshot inputs** — persist all API data to `state/briefing_snapshots/YYYY-MM-DD/`
5. **Classify regime** — deterministic 11-rule classifier (RISK_ON / NORMAL / CAUTION / RISK_OFF)
6. **Review equities** — apply equity decision matrix to each held stock
7. **Review options** — call wheel-roll-advisor for each open contract
8. **Generate new ideas** — run enabled screeners, size candidates
9. **Consistency check** — compare today to yesterday, flag unexplained flips
10. **Aggregate and render** — build final markdown with 11 sections
11. **Quality gate** — sanity checks (numeric range, required sections, etc.)

Each step writes JSON snapshots to enable reproducible re-runs.

## Output

- **`reports/daily/briefing_YYYY-MM-DD.md`** — human-readable briefing
- **`reports/daily/briefing_YYYY-MM-DD.json`** — machine-readable companion
- **`state/briefing_snapshots/YYYY-MM-DD/`** — all input snapshots for reproducibility

## Configuration

Copy `assets/briefing_config_template.yaml` to `state/briefing_config.yaml` and customize:
- Enabled strategies (wheel, dividend_growth, swing)
- Screener frequency (every_run, weekly, monthly)
- Risk parameters (concentration, sector limits, leverage)
- Account routing (taxable vs IRA, liquidity minimums)

## Testing

```bash
cd scripts
python3 -m pytest tests/test_e2e.py -v
```

Tests run the full pipeline end-to-end with the mock fixture, verifying:
- All 10 steps complete
- Briefing markdown has all 11 required sections
- JSON companion has expected keys
- Quality gate passes

## v1 Limitations

- **Regime classifier:** Stubbed to NORMAL (v1.1 will implement full 11-rule logic)
- **Equity decision matrix:** Stubbed to HOLD (v1.1 will implement full matrix)
- **Screeners:** Stubbed (v1.1 will integrate value-dividend-screener, earnings-trade-analyzer, etc.)
- **Wheel-roll-advisor integration:** Stubbed (v1.1 will call actual skill)
- **Directive evaluation:** Load only, no trigger evaluation yet
- **E*TRADE live mode:** Mock fixture only (v1.1 will wire real MCP calls)

## v1.1+ Roadmap

- Regime classifier: Implement all 11 rules with live VIX/SPY/breadth data
- Equity decision matrix: Full 36-cell matrix lookup
- Screeners: Wire value-dividend-screener, earnings-trade-analyzer, pead-screener
- Wheel-roll-advisor: Full integration with position matrix
- Day-over-day consistency: Full diff checking and trigger validation
- E*TRADE live mode: Call real MCP endpoints instead of fixture

## Hard Rules (Enforced)

1. **Live-data backing.** Every recommendation backed by data fetched this cycle.
2. **Expiration validation.** Every options contract validated against chain.
3. **Concentration check (post-sizing).** `existing_pct + new / NLV ≤ 10%`.
4. **Earnings guard.** No new short puts when `earnings_date ≤ expiration`.
5. **Tail-risk gate.** Names in tail_risk_names.yaml not eligible for new shorts.
6. **Macro-caution gate.** CAUTION/RISK_OFF regimes suppress new longs.
7. **No directional forecasts.** Flag conditions; don't predict direction.

See `references/regime_framework.md` for full rules.

## Integration Points

- **wheel-roll-advisor:** Step 7 calls this for each open option
- **recommendation-list-fetcher:** Step 3.6 calls for third-party BUY/SELL/HOLD
- **trader-memory-core:** All equity reviews loaded from thesis store
- **Data-quality-checker:** Step 9 validates final markdown (v1.1)
- **Position-sizer:** Step 6 sizes new ideas (v1.1)

## File Structure

```
daily-portfolio-briefing/
├── SKILL.md
├── README.md
├── scripts/
│   ├── run_briefing.py           # Main orchestrator
│   ├── steps/
│   │   ├── preflight.py
│   │   ├── load_directives.py
│   │   ├── fetch_recommendations.py
│   │   ├── snapshot_inputs.py
│   │   ├── classify_regime.py
│   │   ├── review_equities.py
│   │   ├── review_options.py
│   │   ├── new_ideas.py
│   │   ├── consistency_check.py
│   │   ├── aggregate.py
│   │   └── quality_gate.py
│   ├── render/
│   │   └── panels.py             # Panel rendering functions
│   └── tests/
│       └── test_e2e.py
├── references/
│   ├── regime_framework.md
│   └── etrade-mcp-setup.md
└── assets/
    ├── etrade_mock_fixture.json
    └── briefing_config_template.yaml
```
