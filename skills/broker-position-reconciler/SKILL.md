---
name: broker-position-reconciler
description: Verifies that every action in the briefing references a position that ACTUALLY EXISTS at the broker with matching contract id, expiration, strike, quantity, and basis. Detects stale snapshot data, missing positions, and contract-id mismatches. Final safety gate before order placement.
version: 1.0
---

# Broker Position Reconciler

This is the most important gate in the system. Without it, the briefing can confidently recommend rolling a contract you don't own, or open a "covered call" against shares already encumbered by a different position — both of which would either fail at the broker or, worse, succeed in dangerous ways (e.g., creating naked short calls).

## What it catches

1. **Wrong expiration**: snapshot says `GOOG_CALL_450_20270917` but broker has `GOOG_CALL_450_20271217`
2. **Wrong quantity**: snapshot says 4 contracts, broker has 3
3. **Wrong strike**: snapshot says $245, broker has $250
4. **Missing positions**: snapshot doesn't list a position the broker actually holds (e.g., the GOOG $355P Jun '26)
5. **Stale basis**: snapshot's entry price differs from broker's by >5%
6. **Wrong account**: snapshot says position is in TAXABLE, broker has it in ROTH_IRA

## How it works

Inputs:
- The rendered briefing markdown
- The snapshot's position list (what the briefing thinks you own)
- The broker's live position list (what you actually own)

Process:
1. For each action in the briefing that references an existing position (CLOSE, EXECUTE ROLL, DEFENSIVE COLLAR), parse out the target contract identifier
2. Look that identifier up in the broker positions
3. If not found → **MISMATCH** (stale snapshot)
4. If found, verify qty/strike/exp/basis match within tolerance
5. Compute set diff: snapshot positions vs broker positions → flag missing/extra

Outputs:
```python
{
  "verified": True | False,
  "mismatches": [
      {
          "action": "EXECUTE ROLL GOOG_CALL_450_20270917",
          "issue": "contract not found at broker",
          "snapshot": "GOOG_CALL_450_20270917 qty=-4",
          "broker": "(no matching contract)"
      },
      ...
  ],
  "missing_at_broker": [...],   # in snapshot, not at broker
  "missing_in_snapshot": [...], # at broker, not in snapshot (e.g. GOOG $355P)
  "panel_md": "...",            # markdown panel to prepend if any issues
  "block_actions": [...]        # action labels that should be SUPPRESSED
}
```

## Hook integration

When `verified=False`:
- Prepend a `## 🚫 Position Data Mismatch — DO NOT TRADE` panel listing every mismatch
- Tag affected actions with `🚫 STALE POSITION` (caller can choose to filter them out of the action list)
- The pre-flight-verifier escalates this to a hard BLOCK — no release until the snapshot is refreshed

## Why this is the highest-priority gate

The trade-validator answers "is this trade economically sound?" — but the math depends on knowing what you own. Without position reconciliation, every other gate is computing on top of potentially-wrong inputs. **Garbage in, garbage out.** This skill must run FIRST in the gate sequence.

## Configuration

```yaml
# references/reconciler_thresholds.yaml
basis_tolerance_pct: 5.0       # allow ±5% drift in entry price before flagging
quantity_tolerance: 0          # exact match required
strike_tolerance: 0.01         # exact match required
expiration_tolerance_days: 0   # exact match required (calendar/diagonal rolls depend on this)
```
