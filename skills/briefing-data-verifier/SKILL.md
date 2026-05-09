---
name: briefing-data-verifier
description: Scans the rendered briefing for stub markers, formula-derived prices, or missing live-data attribution. Suppresses or flags any action that wasn't backed by real E*TRADE chain data. Last gate before release.
version: 1.0
---

# Briefing Data Verifier

Hard gate that ensures every actionable order ticket in the briefing is backed by **live E*TRADE chain data** — not heuristics, not formulas, not estimates.

## When invoked

Called inside `gate_and_render()` AFTER the briefing has been rendered. Inspects each action item and:
- ✅ **Confirms live-data attribution** when source markers (`Live E*TRADE chain`, `Source: live_chain`, `bid $X.XX / mid $X.XX / ask $X.XX`) are present
- 🔴 **Flags stub-derived actions** that lack live-data markers
- 🚫 **Optionally suppresses** stub actions when `strict_mode=True`

## What counts as live-data

A line counts as live-data-backed if it shows ANY of:
- A bid/ask quote tuple (e.g., `current bid $59.30 / mid $60.72 / ask $62.15`)
- An explicit source line: `**Source:** Live E*TRADE chain`
- A "Buy-to-Close" / "Sell-to-Open" pair both with limit prices (combo rolls always show both legs from chain)

## What gets flagged

- A PULLBACK CSP without bid/ask context AND without `Source: live_chain`
- A DEFENSIVE COLLAR with `price_source: estimated` markers
- Any action with `~$X.XX` formula-style premium (the `~` prefix is a stub indicator)

## Output

```python
{
  "verified": True | False,
  "live_actions": int,
  "stubbed_actions": int,
  "flagged_lines": [str],
  "panel_md": str  # markdown panel to prepend if any stubs found
}
```

When `verified=False`, the briefing renderer prepends a `## 🔴 Live-Data Verification` panel listing exactly which actions were stub-derived. In `strict_mode`, those actions are suppressed.

## Why this is a separate skill

The deterministic quality-gate validates structure. The trade-validator validates economics. **This skill validates provenance** — i.e., "did these prices come from the broker, or did we make them up?" It's the last sanity check before a user trades.
