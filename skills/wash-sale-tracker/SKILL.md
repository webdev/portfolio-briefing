---
name: wash-sale-tracker
description: Track closed-at-loss option and stock trades, blocking new buys and cash-secured puts on the same security within 30 calendar days. Enforces IRS wash-sale rules to prevent accidental tax-loss disallowance and to flag compliance risk to the briefing.
version: 1.0
---

# Wash Sale Tracker

The IRS disallows a realized loss if you acquire a "substantially identical" security within 30 calendar days before or after the loss close. This skill tracks every trade closed at a loss and exposes a checker so the briefing can block wash-sale violations before they happen.

## When to use

Triggered automatically by the briefing before recommending any new position:
1. Before suggesting a **buy** of any stock
2. Before suggesting a **cash-secured put** on any ticker
3. Before suggesting a **call-short** or **put-short** that might be assigned on a loss-close ticker

Also triggered by a portfolio reconciliation tool to audit existing positions for wash-sale exposure.

## Wash-sale rule essentials

Per IRS Pub 550:
- **30-day window:** 30 calendar days BEFORE the loss close + 30 AFTER (61-day total)
- **Substantially identical:** For v1, interpreted as the same ticker symbol (AAPL ≠ AAPL calls; both blocked)
- **Loss disallowance:** The loss is disallowed; instead, it adjusts the cost basis of the replacement purchase upward
- **Only losses trigger:** Profitable closes do NOT create a wash-sale window
- **Applies to all traders:** Even accidental purchases trigger the rule

This skill prevents wash-sale **violations** (opening a new position in a wash-sale window on a loss-closed ticker).

## Public interface

### `record_trade_close(ticker: str, close_date: str, realized_pl: float) -> None`

Record a closed trade in the ledger. If `realized_pl < 0`, the loss is logged. Profitable closes are ignored.

**Parameters:**
- `ticker`: Stock symbol (uppercase, e.g., "MU", "AAPL")
- `close_date`: Close date as ISO string "YYYY-MM-DD"
- `realized_pl`: Realized P&L in dollars; only `< 0` triggers the wash-sale window

**Example:**
```python
record_trade_close("MU", "2026-04-15", -340.00)  # Loss recorded
record_trade_close("NVDA", "2026-04-16", 1200.00)  # Ignored (profit)
```

### `is_wash_sale_blocked(ticker: str, as_of_date: str) -> tuple[bool, str]`

Check if a ticker is blocked from re-entry due to a recent loss close.

**Parameters:**
- `ticker`: Stock symbol (uppercase)
- `as_of_date`: Check date as ISO string "YYYY-MM-DD"

**Returns:**
- `(blocked: bool, reason: str)` — If blocked, reason includes the loss amount, close date, days remaining, and unblock date
- If not blocked, reason is empty string

**Example:**
```python
blocked, reason = is_wash_sale_blocked("MU", "2026-04-15")
# Returns (False, "")  ← MU loss was on 04-15, need to wait to 05-15; as_of is still 04-15

blocked, reason = is_wash_sale_blocked("MU", "2026-05-14")
# Returns (True, "MU closed at -$340 on 2026-04-15, 29 days ago — re-entry blocked until 2026-05-15")

blocked, reason = is_wash_sale_blocked("MU", "2026-05-16")
# Returns (False, "")  ← 31 days have passed, rule expired
```

## Ledger schema

Stored in `state/wash_sale_ledger.json`:

```json
{
  "version": 1,
  "records": [
    {
      "ticker": "MU",
      "close_date": "2026-04-15",
      "loss_dollars": -340.0
    },
    {
      "ticker": "AAPL",
      "close_date": "2026-04-10",
      "loss_dollars": -1250.0
    }
  ]
}
```

## Design decisions

- **Same ticker = substantially identical (v1).** Calls, puts, spreads on the same ticker are blocked. Future versions may refine to distinguish AAPL calls from AAPL stock, but conservative v1 blocks both.
- **Most recent loss per ticker.** If a ticker has multiple losses, only the most recent loss window matters. Once the most recent loss expires, that ticker is unblocked even if older losses exist.
- **JSON ledger, file-backed.** No database; simple human-readable storage. Path overridable for testing.
- **Ledger is append-only for records.** Once logged, a loss close stays in history; the checker queries the ledger at check time.

## Integration with briefing

The briefing calls `is_wash_sale_blocked(ticker, as_of_date)` before rendering any actionable recommendation:

```python
# In recommendation builder:
blocked, reason = wash_sale_tracker.is_wash_sale_blocked("AAPL", today)
if blocked:
    # Don't render the trade; surface a skip reason instead
    print(f"⚠ Wash-sale block: {reason}")
    continue
```

This prevents the briefing from surfacing a trade that would violate the wash-sale rule.

## References

See `references/wash_sale_rules.md` for IRS guidance summary and practical examples.
