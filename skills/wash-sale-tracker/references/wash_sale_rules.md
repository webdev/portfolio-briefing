# Wash Sale Rules — IRS Pub 550 Summary

## The Rule (26 U.S.C. § 1091)

You cannot deduct a loss from the sale or exchange of a security if you acquire a **substantially identical** security within 30 calendar days **before** or **after** the loss sale. The 61-day window (30 before + day of + 30 after) is often called the "wash-sale period."

**If violated:** The loss is disallowed in the year of sale. Instead, the loss adjusts the cost basis of the replacement purchase upward, deferring the loss recognition to a future sale of the replacement.

## Key Details

### The 30-day window is CALENDAR days, not trading days
- Loss closed on April 15
- Reacquisition allowed starting May 16 (30 calendar days after)
- May 15 reacquisition = wash sale violation
- Weekend/holiday doesn't extend the window

### Substantially identical means SAME SECURITY
For equities and options:
- **Blocked:** Buying stock AAPL, selling AAPL calls, selling AAPL puts (all same ticker)
- **Blocked:** Calling assignment on an AAPL short position within 30 days of loss-closing AAPL stock
- **NOT blocked (v1):** Buying MSFT if you closed AAPL at a loss (different ticker)
- **NOT blocked (v1):** AAPL shares vs. AAPL call options are treated as substantially identical by the IRS, so both are blocked

**Future refinement:** Some traders distinguish between stock and options; for now, same ticker = blocked.

### Only LOSSES trigger the rule
- Sell AAPL for a $500 gain, then buy AAPL stock 10 days later = NO wash-sale rule applies
- Sell AAPL for a $500 loss, then buy AAPL stock 10 days later = wash-sale disallowance

### Applies to ALL traders
- Individual traders: wash-sale rules apply (no exception for day traders)
- Tax professionals have debated "active trading" exception; IRS says no such exception exists
- **Accidental purchases count:** You don't have to intend the wash sale; mere reacquisition triggers it

### Substitute or rights don't help
- Buying the same stock under a different ticker symbol = substantially identical (wash sale)
- Rights to purchase or call options on the stock = substantially identical
- Preferred stock of the same company might be substantially identical (depends on economics; conservative approach: treat as identical)

## Examples

### Example 1: Direct re-entry (violation)
```
April 10: Sell 100 shares of MU @ $25, realized loss -$500
April 20: Buy 100 shares of MU @ $24
Result: Wash-sale disallowance.
        Loss of -$500 is disallowed.
        Cost basis of 100 shares purchased April 20 is increased by $500 (from $2,400 to $2,900).
        Loss is deferred to the later sale of the April 20 shares.
```

### Example 2: CSP re-entry (violation)
```
April 10: Sell 100 shares of MU @ $25, realized loss -$500
April 25: Sell cash-secured put MU $23 strike, expires May 10
Result: If the put is assigned (MU purchased), wash-sale disallowance.
        The assignment is treated as a reacquisition of MU on May 10.
        If May 10 is within 30 days of April 10 loss close:
        (April 10 + 30 = May 10, boundary case)
        Wash-sale rule applies. Disallowance.
```

### Example 3: Waiting out the window (compliant)
```
April 15: Sell 100 shares of AAPL @ $100, realized loss -$1,000
May 16: Buy 100 shares of AAPL @ $98
Result: 31 days have passed. Window closed. NO wash-sale disallowance.
        Loss of -$1,000 is recognized in April tax year.
        New 100 shares have cost basis of $9,800 (normal, no adjustment).
```

### Example 4: Multiple losses on same ticker
```
March 1: Sell 50 shares TSLA, loss -$1,200
April 1: Sell 50 shares TSLA, loss -$800
April 15: Buy 100 shares TSLA @ market
Result: The April 1 loss close is more recent.
        April 1 + 30 = May 1 (end of wash-sale window for the April 1 loss).
        April 15 reacquisition is within the window → wash-sale disallowance.
        The April 1 loss of -$800 is disallowed.
        The March 1 loss of -$1,200 is NOT affected (its window ended April 31, before reacquisition).
        Cost basis of the April 15 purchase is increased by $800 (the disallowed loss from April 1).
```

## Practical trader implications

### Before closing a position at a loss
- Ask: "Do I plan to re-enter this ticker in the next 30 days?"
- If yes: wait, or defer the loss close
- If no: proceed with the close; the loss is recognized

### Before opening a new position
- Check: "Did I close this ticker at a loss in the past 30 days?"
- If yes: **wait until after day 30** (calendar day count)
- If no: proceed with the new position; no wash-sale risk

### Tax-loss harvesting strategy
- Close a losing position to realize the loss
- **Mandatory 30-day wait** before re-entering the same ticker
- Option: buy a correlated but non-identical security (e.g., QQQ if you sold AAPL) to stay in the market
- After 30 days, re-enter the original ticker and harvest the new position later if it becomes profitable

### IRA accounts
- **Wash-sale rules DO apply in IRAs**
- Many traders think IRAs are exempt; they're not
- IRA statement may not track wash sales; your responsibility to monitor

## Computation: Is today within 30 days?

For a loss close on date D, the wash-sale window spans:
- **Start:** D - 30 days
- **End:** D + 30 days

If you check on date TODAY:
- **Days since loss close:** `(TODAY - D).days`
- **Blocked if:** `0 < (TODAY - D).days ≤ 30`
- **Allowed if:** `(TODAY - D).days > 30` OR `(TODAY - D).days ≤ 0` (haven't reached the loss close yet, unlikely)

**Boundary:** Exactly 30 days after close = OK to re-enter on day 31. On day 30, still blocked.

Example:
- Loss close: April 15, 2026
- Days until re-entry allowed: May 16, 2026 (31 calendar days after April 15)
- May 15 re-entry: BLOCKED (29 days have passed)
- May 16 re-entry: ALLOWED (30 days have passed; rule window expires end-of-day May 15)

## References

- **IRS Publication 550** — Investment Income and Expenses (wash sale rules, Section 1091)
- **IRC 26 U.S.C. § 1091** — Loss from wash sales of stocks or securities
- **Tax Court precedent:** Loss is disallowed if reacquisition occurs within the window, intent doesn't matter
