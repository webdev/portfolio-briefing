---
name: pre-flight-verifier
description: Master gate that runs ALL safety checks (broker position reconciliation, live-data verification, trade-economics validation, persona quality gate) before any briefing is released. Returns one consolidated verdict — RELEASE / WARN / BLOCK — with a unified summary panel. The single point of "is this briefing safe to act on?"
version: 1.0
---

# Pre-Flight Verifier

This skill exists because a briefing is only as safe as its weakest gate. Each downstream skill (data-verifier, position-reconciler, trade-validator, quality-gate) catches a different class of failure. Running them independently means a green light from one doesn't mean the briefing is safe — you need a unified verdict.

## Gate sequence

Every gate runs in priority order. The first gate to issue a hard BLOCK terminates the run.

| Order | Gate | What it catches |
|---|---|---|
| 1 | **broker-position-reconciler** | Stale snapshot, wrong contract id, missing positions |
| 2 | **briefing-data-verifier** | Stub-derived prices that aren't from live chain |
| 3 | **trade-validator** (per action) | Negative-EV trades, poor risk/reward |
| 4 | **briefing-quality-gate** | Structural issues, persona-rule violations |

## Verdicts

- **RELEASE**: all gates pass. Briefing is safe to act on.
- **WARN**: some gates flagged advisory issues but no hard block. Briefing is released with warning panel(s) prepended.
- **BLOCK**: at least one gate found a critical issue (mismatched position data, structural failure, or negative EV on every action). Briefing renders with a `🚫 PRE-FLIGHT BLOCKED` header and a clear remediation path.

## Output format

```python
{
  "verdict": "RELEASE" | "WARN" | "BLOCK",
  "gate_results": {
      "position_reconciler": {...},
      "data_verifier": {...},
      "quality_gate": {...},
  },
  "blocking_gate": str | None,    # name of the gate that triggered BLOCK
  "consolidated_panel_md": str,   # all warning panels merged in priority order
  "rendered_briefing": str,       # final markdown with all panels prepended
}
```

## Hook integration

This skill replaces `gate_and_render` in the daily-portfolio-briefing pipeline. The orchestrator calls:

```python
result = run_pre_flight(briefing_md, snapshot_positions, broker_positions)
if result["verdict"] == "BLOCK":
    # render with the BLOCKED header — user sees the issues, can't accidentally trade
    return result["rendered_briefing"]
```

## Why this is necessary

Without a unified gate, the briefing renderer would have to know about and call each gate separately, in the right order, with the right inputs. That's coupling. The pre-flight verifier is the single hook point — every future safety skill plugs into it, not into the renderer.

It also enforces ORDER. Position reconciliation MUST run before trade validation, because validating EV on a trade against a contract you don't own is meaningless. The pre-flight ensures gates fire in the right sequence.

## Configuration

```yaml
# references/gate_config.yaml
gates:
  - name: broker-position-reconciler
    enabled: true
    on_block: hard      # or 'warn'
  - name: briefing-data-verifier
    enabled: true
    on_block: warn
  - name: trade-validator
    enabled: true
    on_block: warn
  - name: briefing-quality-gate
    enabled: true
    on_block: warn

global:
  fail_open: false      # if a gate errors, default to BLOCK rather than WARN
  panel_order: priority # priority | alphabetical
```
