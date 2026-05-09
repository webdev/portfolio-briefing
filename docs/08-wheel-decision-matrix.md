# Wheel Roll Advisor — Decision Matrix

**Status:** Draft v0.1
**Date:** 2026-05-07
**Companion files:**
- `06-wheel-parameters.md` — numerical values for all thresholds
- `07-tail-risk-names.md` — curated list of conservative-treatment names
- `03-wheel-roll-advisor-skill.md` — skill definition and workflow

This document is the lookup table that the wheel-roll-advisor skill uses to make deterministic roll/hold/close decisions. The skill's job is to convert an open option position + market context into one of seven decision tags: `CLOSE`, `CLOSE_FOR_PROFIT`, `TAKE_ASSIGNMENT`, `WAIT`, `LET_EXPIRE`, `ROLL_OUT`, `ROLL_OUT_AND_DOWN`, or `ROLL_OUT_AND_UP`. Every cell references parameter names from `06-wheel-parameters.md` — when a threshold changes, only the parameters file is edited; the matrix's behavior shifts automatically.

The matrix is exhaustive: it covers every combination of moneyness, DTE, profit capture, outlook, and regime that produces a distinct action. Pre- and post-matrix guardrails can modify the decision, but the matrix is the single point of truth for normal operation. Every recommendation includes a `matrix_cell_id` for auditability.

---

## Reading the Matrix

The skill executes this procedure for every position:

1. **Parse the position.** Extract: option type (PUT or CALL), side (SHORT for wheel), strike, expiration, entry price, current price, quantity.

2. **Compute derived state:**
   - `moneyness`: Compare current underlying price to strike. Classify as DEEP_OTM (delta < ${deep_otm_delta_threshold}), MODERATE_OTM (delta 0.10–0.25), NEAR_ATM (delta > 0.25), or ITM (underlying past strike).
   - `dte_band`: Days to expiration. Classify as EXPIRY_WEEK (≤ ${expiry_week_dte_max}), SHORT_DTE (${expiry_week_dte_max} < DTE ≤ ${short_dte_max}), MID_DTE (${short_dte_max} < DTE ≤ ${mid_dte_max}), or LONG_DTE (> ${mid_dte_max}).
   - `profit_captured_pct`: (current_mid - entry_price) / entry_price, capped at 0 for underwater.
   - `iv_regime`: Compare current IV rank to ${low_iv_threshold} and ${high_iv_threshold}. Classify as LOW, NORMAL, or HIGH.
   - `outlook`: Provided by caller (STRONG_BULLISH, BULLISH, NEUTRAL, BEARISH, STRONG_BEARISH).
   - `regime`: Provided by caller (NORMAL, CAUTION, RISK_OFF).

3. **Apply pre-matrix guardrails** (section below). If any guardrail fires, return that decision and **skip the matrix**.

4. **Look up the matrix** (sections "Short Put" and "Short Call"). Find the row whose conditions all match. Return the decision tag + matrix_cell_id.

5. **Apply post-matrix guardrails** (section below). These can modify or downgrade the decision from step 4.

6. **If decision is ROLL_\*, run roll-target selection** (section below). Evaluate the candidate contracts from the chain and pick the best fit by delta, liquidity, credit, and stress tests.

7. **Return structured output.** Include: position identifier, decision tag, matrix_cell_id, rationale (one sentence), roll target (if applicable), warnings (if any).

---

## State Variables

Reference guide to inputs used in the matrix. All boundaries and thresholds reference `06-wheel-parameters.md`.

| Variable | Type | Values | Parameter Tie | Meaning |
|----------|------|--------|----------------|---------|
| `position_type` | enum | SHORT_PUT, SHORT_CALL_COVERED | N/A | Which leg of the wheel |
| `moneyness` | enum | DEEP_OTM, MODERATE_OTM, NEAR_ATM, ITM | `deep_otm_delta_threshold`, `near_atm_delta_threshold` | How far from strike to underlying price |
| `dte_band` | enum | EXPIRY_WEEK, SHORT_DTE, MID_DTE, LONG_DTE | `expiry_week_dte_max`, `short_dte_max`, `mid_dte_max` | Time remaining, bucketed |
| `profit_captured_pct` | float | 0.0–1.0+ | N/A | Profit realized as % of entry premium |
| `iv_regime` | enum | LOW, NORMAL, HIGH | `low_iv_threshold`, `high_iv_threshold` | Volatility environment |
| `outlook` | enum | STRONG_BULLISH, BULLISH, NEUTRAL, BEARISH, STRONG_BEARISH | N/A | Underlying direction (from caller) |
| `regime` | enum | NORMAL, CAUTION, RISK_OFF | N/A | Market regime (from caller or sentiment) |
| `earnings_proximity` | enum | NONE, BEFORE_EXPIRY, IMMINENT_LE_7D | N/A | Next earnings relative to expiration |
| `dividend_proximity` | enum | NONE, EX_DIV_LE_3D | N/A | Next ex-dividend relative to expiration |
| `tail_risk_name` | bool | true, false | 07-tail-risk-names.md | On the conservative-treatment list |
| `delta` | float | 0.0–1.0 | N/A | Absolute delta of the option |
| `current_mid` | float | USD | N/A | Current bid/ask midpoint |
| `entry_price` | float | USD | N/A | Premium collected or paid |
| `days_to_earnings` | int | 0–365 | N/A | Days until next earnings |
| `existing_open_order` | bool | true, false | N/A | Order already in market for this contract |

---

## Pre-Matrix Guardrails

Run these checks **in order**. The first guardrail that fires returns a decision and **skips the matrix**.

### Guardrail 1: Loss Stop (Monthly)

**Cell ID:** `PRE_LOSS_STOP_MONTHLY`

**Condition:**
- `position_type in [SHORT_PUT, SHORT_CALL_UNCOVERED]`
- `current_mid / entry_price >= ${loss_stop_monthly}` (default 2.0)
- `dte > ${weekly_put_dte_threshold}` (default 10)

**Decision:** `CLOSE` with rationale "Loss stop triggered: option price ≥ 2× entry. Buy back immediately."

**Rationale:** Monthlies that lose 2× their value are no longer a wheel position worth holding — risk now exceeds remaining theta benefit. Covered calls exempt; an underwater call means the stock rallied.

---

### Guardrail 2: Loss Stop (Weekly)

**Cell ID:** `PRE_LOSS_STOP_WEEKLY`

**Condition:**
- `position_type in [SHORT_PUT, SHORT_CALL_UNCOVERED]`
- `current_mid / entry_price >= ${loss_stop_weekly}` (default 1.5)
- `dte <= ${weekly_put_dte_threshold}` (default 10)

**Decision:** `CLOSE` with rationale "Loss stop triggered (weekly): option price ≥ 1.5× entry. Gamma risk too high."

**Rationale:** Weeklies have compressed theta window and rising gamma. Accepting a 1.5× loss on a weekly is worse than accepting a 2× loss on a monthly because the payoff math doesn't work.

---

### Guardrail 3: Underlying Crash Stop

**Cell ID:** `PRE_CRASH_STOP`

**Condition:**
- `position_type == SHORT_PUT`
- `(current_underlying_price - prior_day_close) / prior_day_close <= -${underlying_crash_stop_pct}` (default −0.15)

**Decision:** `CLOSE_FOR_PROFIT` if `profit_captured_pct >= 0.30`, else `CLOSE` with rationale "Circuit breaker: stock crashed 15%+ intraday. Close all weeklies and reassess."

**Rationale:** A 15%+ intraday drop is a regime shift. Assignment risk spikes and rolling becomes uneconomical. Even if the short put was OTM this morning, it's likely ITM now — taking the assignment or buying back is cleaner than defending with a roll.

---

### Guardrail 4: Tail-Risk Override

**Cell ID:** `PRE_TAIL_RISK_OVERRIDE`

**Condition:**
- `tail_risk_name == true` (per 07-tail-risk-names.md)
- `decision_from_matrix in [DEFENSIVE_ROLL_OUT_AND_DOWN, ROLL_OUT_AND_DOWN, ROLL_OUT]`

**Decision:** Override to `CLOSE` with rationale "Tail-risk name: rolling lower strikes doesn't help against headline gaps. Buy back and walk."

**Rationale:** Chinese ADRs, binary biotechs, crypto-proxies, and meme stocks can gap 15–25% on regulatory or discrete events. Rolling down-and-out puts you in a worse position (lower strike, still blown through on the same gap). Capital preservation trumps premium.

---

### Guardrail 5: Open Order Exists

**Cell ID:** `PRE_OPEN_ORDER`

**Condition:**
- `existing_open_order == true`

**Decision:** `WAIT` with rationale "Order #XXXX already submitted for ${offer_price}. Waiting for fill or cancellation."

**Rationale:** Don't generate a new recommendation while one is in flight. Let it fill or expire, then re-evaluate.

---

### Guardrail 6: Earnings Within 7 Days (Short Position)

**Cell ID:** `PRE_EARNINGS_LE7D`

**Condition:**
- `position_type in [SHORT_PUT, SHORT_CALL_UNCOVERED]`
- `earnings_proximity == IMMINENT_LE_7D` (0 ≤ days_to_earnings ≤ 7)
- `dte <= ${earnings_guard_dte_max}` (default 30)

**Decision:**
- If `profit_captured_pct >= ${earnings_close_profit_min}` (default 0.50): `CLOSE_FOR_PROFIT` with rationale "Earnings within 7 days and expiration ≤ 30 DTE. Lock in ${profit_captured_pct}% profit before binary event."
- Else: `WAIT` with rationale "Earnings within 7 days; don't roll across earnings. Hold and monitor, or close if IV spikes."

**Rationale:** Earnings are binary. Rolling across them doesn't reduce the binary risk. If you have a 50%+ profit, take it and redeploy after earnings. If you're underwater, holding is usually the right answer (theta works for you, IV crush often recovers losses).

---

### Guardrail 7: Earnings Before Expiry + Short Position + Threatened

**Cell ID:** `PRE_EARNINGS_THREATENED`

**Condition:**
- `position_type in [SHORT_PUT, SHORT_CALL_UNCOVERED]`
- `earnings_proximity == BEFORE_EXPIRY`
- `profit_captured_pct < ${imminent_threatened_pnl_min}` (default −0.02, i.e., < 2% loss)
- `delta > ${imminent_threatened_delta_min}` (default 0.15)

**Decision:** `CLOSE` with rationale "Earnings before expiry, position is underwater + meaningful delta. Close before binary event."

**Rationale:** If you're close to breakeven or slightly underwater AND the option has meaningful delta (so it *will* be affected by earnings), closing is safer than rolling and hoping. This guardrail is conservative but prevents the "held through earnings and got gapped" disaster.

---

### Guardrail 8: Invalid Input or Missing Data

**Cell ID:** `PRE_INVALID_INPUT`

**Condition:**
- `outlookTag not in [STRONG_BULLISH, BULLISH, NEUTRAL, BEARISH, STRONG_BEARISH]`
- OR `chain.expirations is empty`
- OR `current_mid is None`

**Decision:** `WAIT` with rationale "Incomplete data: ${missing_field}. Skipping recommendation until data is available."

**Rationale:** Don't recommend a roll or close on stale data. Be explicit about what's missing.

---

## Short Put Matrix — NORMAL Regime

For positions where `regime == NORMAL`. Rows are ordered by decision priority: closes first (CLOSE_FOR_PROFIT), then holds/waits, then rolls.

### Deep OTM — Take Profit by DTE

| Cell ID | Moneyness | DTE Band | Profit Captured | Outlook | Decision | Parameters | Rationale |
|---------|-----------|----------|-----------------|---------|----------|------------|-----------|
| `PUT_NORMAL_DEEP_OTM_LONG_DTE_50` | DEEP_OTM | > ${mid_dte_max} | ≥ 50% | any | CLOSE_FOR_PROFIT | ${take_profit_deep_otm_dte_120_plus} | Theta accelerates late; redeploy premium. |
| `PUT_NORMAL_DEEP_OTM_MID_DTE_65` | DEEP_OTM | ${short_dte_max} < DTE ≤ ${mid_dte_max} | ≥ 65% | any | CLOSE_FOR_PROFIT | ${take_profit_deep_otm_dte_60_120} | Mid-DTE premium locked in before acceleration phase. |
| `PUT_NORMAL_DEEP_OTM_SHORT_DTE_80` | DEEP_OTM | ≤ ${short_dte_max} | ≥ 80% | any | CLOSE_FOR_PROFIT | ${take_profit_deep_otm_dte_under_60} | Let theta decay; close only at 80% capture to squeeze final pennies. |
| `PUT_NORMAL_DEEP_OTM_HOLD` | DEEP_OTM | any | < capture threshold | any | WAIT | (derived) | Theta still working. Let it ride. |

### Moderate OTM — Standard Wheel Range

| Cell ID | Moneyness | DTE Band | Profit Captured | Outlook | Regime | Decision | Parameters | Rationale |
|---------|-----------|----------|-----------------|---------|--------|----------|------------|-----------|
| `PUT_NORMAL_MOD_OTM_TAKEPROFIT` | MODERATE_OTM | any | ≥ 50% | any | NORMAL | CLOSE_FOR_PROFIT | ${take_profit_moderate_otm} | Standard wheel sweet spot; take the profit. |
| `PUT_NORMAL_MOD_OTM_SQUEEZE` | MODERATE_OTM | 14 < DTE ≤ ${mid_dte_max} | 0.50–0.75 | NEUTRAL / BULLISH | NORMAL | GTC_LIMIT_75 | ${squeeze_dte_min}, ${squeeze_capture_band_min}, ${squeeze_capture_band_max}, ${squeeze_days_to_earnings_min} | DTE sufficient, earnings distant. Place GTC limit at 75% capture. |
| `PUT_NORMAL_MOD_OTM_WAIT` | MODERATE_OTM | any | < 50% | BULLISH / STRONG_BULLISH | NORMAL | WAIT | (derived) | Bullish outlook; let theta work. |
| `PUT_NORMAL_MOD_OTM_ROLL_OUT` | MODERATE_OTM | any | < 50% | NEUTRAL / BEARISH | NORMAL | ROLL_OUT | ${delta_target_normal_iv}, ${delta_max_normal_iv} | Neutral outlook: roll out for more decay. Bearish: roll out to avoid assignment. |
| `PUT_NORMAL_MOD_OTM_NEAR_EXPIRE` | MODERATE_OTM | ≤ 7 | any | any | NORMAL | LET_EXPIRE | N/A | Less than a week; let it expire worthless. |

### Near-ATM — Assignment Risk Rising

| Cell ID | Moneyness | DTE Band | Profit Captured | Outlook | Decision | Parameters | Rationale |
|---------|-----------|----------|-----------------|---------|----------|------------|-----------|
| `PUT_NORMAL_NEAR_ATM_TAKEPROFIT` | NEAR_ATM | any | ≥ 40% | any | CLOSE_FOR_PROFIT | ${take_profit_near_atm} | Close sooner; assignment risk high. |
| `PUT_NORMAL_NEAR_ATM_WAIT` | NEAR_ATM | any | < 40% | BULLISH / STRONG_BULLISH | WAIT | (derived) | Bullish outlook; assignment unlikely. |
| `PUT_NORMAL_NEAR_ATM_ROLL` | NEAR_ATM | any | < 40% | NEUTRAL / BEARISH | ROLL_OUT | ${delta_target_normal_iv} | Roll to reduce delta. |

### ITM (In The Money) — Assignment or Roll

| Cell ID | Moneyness | DTE Band | Outlook | Decision | Parameters | Rationale |
|---------|-----------|----------|---------|----------|------------|-----------|
| `PUT_NORMAL_ITM_LONG_DTE_BULLISH` | ITM | ≥ ${mid_dte_max} | BULLISH / STRONG_BULLISH | WAIT | (derived) | Bullish: underlying may snap back. Hold and hope; time is your friend. |
| `PUT_NORMAL_ITM_LONG_DTE_NEUTRAL` | ITM | ≥ ${mid_dte_max} | NEUTRAL | ROLL_OUT | ${delta_target_normal_iv} | Neutral: roll out, same strike or down-and-out to reduce delta. |
| `PUT_NORMAL_ITM_LONG_DTE_BEARISH` | ITM | ≥ ${mid_dte_max} | BEARISH / STRONG_BEARISH | TAKE_ASSIGNMENT | N/A | Bearish: assignment likely. Accept at strike — you wanted to own the stock anyway. |
| `PUT_NORMAL_ITM_SHORT_DTE_BULLISH` | ITM | ≤ ${short_dte_max} | BULLISH / STRONG_BULLISH | ROLL_OUT_AND_DOWN | ${delta_target_normal_iv} | Short DTE + bullish: roll down-and-out to reduce loss and extend runway. |
| `PUT_NORMAL_ITM_SHORT_DTE_NEUTRAL` | ITM | ≤ ${short_dte_max} | NEUTRAL | TAKE_ASSIGNMENT | N/A | Short DTE + no bullish conviction: assignment is inevitable and clean. |
| `PUT_NORMAL_ITM_SHORT_DTE_BEARISH` | ITM | ≤ ${short_dte_max} | BEARISH | TAKE_ASSIGNMENT | N/A | Short DTE + bearish: assignment is inevitable. |

### High IV + Short DTE — Early Close

| Cell ID | Moneyness | DTE Band | Profit Captured | IV Regime | Decision | Parameters | Rationale |
|---------|-----------|----------|-----------------|-----------|----------|------------|-----------|
| `PUT_NORMAL_HIGH_IV_EARLY_CLOSE` | MODERATE_OTM, NEAR_ATM | ≤ ${high_iv_early_close_dte_max} | ≥ 50% | HIGH | CLOSE_FOR_PROFIT | ${high_iv_early_close_iv_rank_min}, ${high_iv_early_close_dte_max} | IV rank ≥ 60, DTE ≤ 45, profit ≥ 50%: lock gains before IV crush. |

### Time Decay Only — Late Stage

| Cell ID | Moneyness | DTE Band | Profit Captured | Decision | Parameters | Rationale |
|---------|-----------|----------|-----------------|----------|------------|-----------|
| `PUT_NORMAL_TIME_DECAY_ONLY` | MODERATE_OTM | ≤ ${time_decay_only_dte_max} | ≥ 30% | CLOSE_FOR_PROFIT | ${time_decay_only_dte_max}, ${time_decay_only_capture_min} | DTE ≤ 21, profit ≥ 30%: gamma rising, time decay accelerates. Close and redeploy. |

---

## Short Put Matrix — CAUTION Regime

For positions where `regime == CAUTION`. The regime shift affects loss-stop triggers and defensive-roll thresholds. Most of the matrix is identical to NORMAL; deviations are listed here.

### Defensive Roll Threshold (CAUTION-specific)

| Cell ID | Moneyness | DTE Band | Loss Multiple | Decision | Parameters | Rationale |
|---------|-----------|----------|----------------|----------|------------|-----------|
| `PUT_CAUTION_DEFENSIVE_ROLL` | ITM | ≥ ${min_dte_for_roll} | ≥ ${defensive_roll_caution_loss_trigger} | DEFENSIVE_ROLL_OUT_AND_DOWN | ${defensive_roll_caution_loss_trigger_multiplier}, ${min_dte_for_roll} | CAUTION regime: roll at 1.30× loss instead of 1.40×. More conservative. |

### CAUTION: Higher-Delta Alert (Delta Expansion)

| Cell ID | Moneyness | DTE Band | Delta | Loss Multiple | Decision | Parameters | Rationale |
|---------|-----------|----------|-------|----------------|----------|------------|-----------|
| `PUT_CAUTION_DELTA_EXPANSION` | any | ≥ ${min_dte_for_roll} | > ${delta_expansion_threshold} | ≥ ${delta_expansion_loss_trigger} | DEFENSIVE_ROLL_OUT_AND_DOWN | ${delta_expansion_threshold}, ${delta_expansion_loss_trigger} | Gamma risk (delta > 0.35) + 1.20× loss in CAUTION regime → roll down. |

### CAUTION: Same rows as NORMAL for OTM/Near-ATM

All other rows (DEEP_OTM take-profit, MODERATE_OTM, etc.) behave identically in CAUTION and NORMAL regimes. The shift is confined to ITM positions where the macro risk is highest.

---

## Short Put Matrix — RISK_OFF Regime

For positions where `regime == RISK_OFF`. Even tighter thresholds. Marked `${TBD_*}` if not in wheelhouz source; these parameters are to be calibrated.

### Defensive Roll Threshold (RISK_OFF-specific)

| Cell ID | Moneyness | DTE Band | Loss Multiple | Decision | Parameters | Rationale |
|---------|-----------|----------|----------------|----------|------------|-----------|
| `PUT_RISK_OFF_DEFENSIVE_ROLL` | ITM | ≥ ${min_dte_for_roll} | ≥ ${TBD_defensive_roll_risk_off_trigger} | DEFENSIVE_ROLL_OUT_AND_DOWN | ${TBD_defensive_roll_risk_off_trigger_multiplier} | RISK_OFF: most conservative. Value TBD (suggest 1.20× or no roll at all). |

### RISK_OFF: Close instead of Roll for Certain Names

| Cell ID | Moneyness | DTE Band | Decision | Rationale |
|---------|-----------|----------|----------|-----------|
| `PUT_RISK_OFF_NO_ROLL` | ITM | ≥ ${min_dte_for_roll} | CLOSE | RISK_OFF + ITM position: don't roll. Close and redeploy capital to hedges or cash. |

### RISK_OFF: Same rows as NORMAL for OTM/Near-ATM

All other rows (DEEP_OTM take-profit, MODERATE_OTM, etc.) are identical. The tightening applies only to ITM defense.

---

## Short Call Matrix (Covered) — NORMAL Regime

Symmetric framework; the risk side is "called away" rather than "assigned at $X."

### OTM (Safe) Covered Calls — Theta Decay

| Cell ID | Moneyness | DTE Band | Profit Captured | Outlook | Decision | Parameters | Rationale |
|---------|-----------|----------|-----------------|---------|----------|------------|-----------|
| `CALL_NORMAL_OTM_SAFE_TAKEPROFIT` | OTM (delta < 0.30) | any | ≥ 50% | any | CLOSE_FOR_PROFIT | ${take_profit_moderate_otm} | Stock hasn't rallied; call will expire worthless or can be bought back cheap. |
| `CALL_NORMAL_OTM_SAFE_HOLD` | OTM | any | < 50% | BEARISH / NEUTRAL | WAIT | (derived) | Bearish: underlying unlikely to rally past strike. Let theta decay. |
| `CALL_NORMAL_OTM_SAFE_ROLL` | OTM | any | < 50% | BULLISH | ROLL_OUT | ${delta_target_normal_iv} | Bullish: underlying may approach strike. Roll out for more premium and time. |

### Near-ATM Covered Calls — Assignment Risk Rising

| Cell ID | Moneyness | DTE Band | Profit Captured | Outlook | Decision | Parameters | Rationale |
|---------|-----------|----------|-----------------|---------|----------|------------|-----------|
| `CALL_NORMAL_NEAR_ATM_TAKEPROFIT` | 0.25 < delta ≤ 0.40 | any | ≥ 40% | any | CLOSE_FOR_PROFIT | ${take_profit_near_atm} | Assignment risk high; take the profit. |
| `CALL_NORMAL_NEAR_ATM_WAIT` | 0.25 < delta ≤ 0.40 | ≥ ${mid_dte_max} | < 40% | BEARISH | WAIT | (derived) | Bearish outlook: underlying unlikely to surge past strike. Hold. |
| `CALL_NORMAL_NEAR_ATM_ROLL_UP` | 0.25 < delta ≤ 0.40 | any | < 40% | BULLISH | ROLL_OUT_AND_UP | ${delta_target_normal_iv} | Bullish: call will be assigned; roll up to capture more premium. |

### ITM (Called Away) Covered Calls

| Cell ID | Moneyness | DTE Band | Outlook | Decision | Parameters | Rationale |
|---------|-----------|----------|---------|----------|------------|-----------|
| `CALL_NORMAL_ITM_LONG_DTE_BULLISH` | delta > 0.40 | ≥ ${mid_dte_max} | BULLISH / STRONG_BULLISH | WAIT | (derived) | Stock rallying; let it ride. Assignment at profit is acceptable. |
| `CALL_NORMAL_ITM_LONG_DTE_NEUTRAL` | delta > 0.40 | ≥ ${mid_dte_max} | NEUTRAL | ROLL_OUT_AND_UP | ${delta_target_normal_iv} | Neutral: roll up to defer call-away and capture more premium. |
| `CALL_NORMAL_ITM_LONG_DTE_BEARISH` | delta > 0.40 | ≥ ${mid_dte_max} | BEARISH | CLOSE | N/A | Bearish + ITM call: stock rallied despite bearish outlook. Close the call and reassess. |
| `CALL_NORMAL_ITM_SHORT_DTE_BULLISH` | delta > 0.40 | ≤ ${short_dte_max} | BULLISH | LET_EXPIRE or TAKE_ASSIGNMENT | N/A | ITM + short DTE + bullish: let it be called away. You captured the rally. |
| `CALL_NORMAL_ITM_SHORT_DTE_NEUTRAL` | delta > 0.40 | ≤ ${short_dte_max} | NEUTRAL | TAKE_ASSIGNMENT | N/A | Short DTE: assignment is imminent. Accept. |
| `CALL_NORMAL_ITM_SHORT_DTE_BEARISH` | delta > 0.40 | ≤ ${short_dte_max} | BEARISH | CLOSE | N/A | Short DTE + bearish + ITM: close the call before assignment to avoid forced sale. |

### Ex-Dividend Near ITM Call

| Cell ID | Moneyness | DTE Band | Dividend Proximity | Decision | Parameters | Rationale |
|---------|-----------|----------|-------------------|----------|------------|-----------|
| `CALL_NORMAL_EX_DIV_ITM` | NEAR_ATM or ITM | ≤ 3 | EX_DIV_LE_3D | CLOSE_FOR_PROFIT (if profit ≥ 40%) or CLOSE | N/A | Early assignment risk on ex-div date. Close the call if profitable. |

### Short Call Matrix — CAUTION & RISK_OFF

Same pattern as short puts: ITM positions get tighter roll targets. Row adjustment:
- **CAUTION:** Roll up at lower profit threshold (suggest 30% vs 50%).
- **RISK_OFF:** Don't roll up calls on losers. Override ROLL_OUT_AND_UP to CLOSE if position is underwater.

---

## Post-Matrix Guardrails

After the matrix returns a decision, run these checks. They can **modify or downgrade** the decision.

### Guardrail A: Roll Target Insufficient

**Condition:**
- Decision is ROLL_OUT, ROLL_OUT_AND_DOWN, or ROLL_OUT_AND_UP
- No contract in the chain meets all criteria (delta target, liquidity, net credit, stress test)

**Modification:** Downgrade decision to `CLOSE` with rationale "No acceptable roll target in available chain. Buy back and reassess."

**Rationale:** A roll that doesn't exist is worse than no roll. Close and redeploy.

---

### Guardrail B: Stress Test Failure

**Condition:**
- Decision is ROLL_\*
- Proposed roll target's loss at underlying −10% exceeds ${max_risk_reward_ratio} × premium collected (default 3.0)

**Modification:** Downgrade to `CLOSE` with rationale "Roll target fails stress test: loss at −10% exceeds 3× premium. Risk/reward unfavorable."

**Rationale:** A roll that blows up on a 10% drop isn't a wheel position.

---

### Guardrail C: Net Credit Insufficient

**Condition:**
- Decision is ROLL_\*
- Net credit (new premium − cost to close existing contract) < ${min_roll_net_credit_pct} × original premium (default 10%)

**Modification:** Downgrade to `CLOSE` with rationale "Roll net credit insufficient: economics don't work."

**Rationale:** Rolling for a $0.01 credit isn't worth the trading friction.

---

### Guardrail D: Ex-Dividend Near ITM Covered Call

**Condition:**
- Decision is WAIT or HOLD on a covered call
- Moneyness is NEAR_ATM or ITM
- Dividend_proximity == EX_DIV_LE_3D

**Modification:** Flag warning "Early assignment risk on ex-dividend date (${ex_div_date}). Consider closing the call." If profit ≥ 40%, suggest CLOSE_FOR_PROFIT instead.

**Rationale:** Early assignment happens on ex-div to collect the dividend. The owner of the call may exercise early. Be prepared.

---

### Guardrail E: RISK_OFF + ROLL_OUT_AND_UP on Covered Call

**Condition:**
- Regime == RISK_OFF
- Decision is ROLL_OUT_AND_UP (on a covered call)
- Position is underwater OR macro caution is high

**Modification:** Require explicit user confirmation. Flag as `CONFIRM_REQUIRED` with rationale "RISK_OFF + rolling up a call on a loser is contrarian. Confirm manually."

**Rationale:** Rolling up calls makes sense when bullish, not in defensive markets.

---

### Guardrail F: Earnings Before Roll Window

**Condition:**
- Decision is ROLL_\*
- Proposed roll target expiration is ≤ 30 days after earnings

**Modification:** Snap the target expiration to ≥ 30 days AFTER earnings. Re-evaluate strike targets. If no such contract exists, downgrade to CLOSE.

**Rationale:** Never roll across an earnings date. Defer to a safe window.

---

## Roll Target Selection

When the matrix recommends a ROLL_\* decision, the skill selects a target contract using this filter pipeline:

1. **Expiration filter:** Candidate must be ≥ current expiry + ${min_dte_for_roll} days (default 21). Avoid rolling into another earnings date if one is known.

2. **Delta filter (IV-adaptive):**
   - If `iv_rank >= ${high_iv_threshold}` (default 60): target delta = ${delta_target_high_iv} (default 0.16 for puts, 0.18 for calls), max delta = ${delta_max_high_iv} (default 0.22 for puts, 0.35 for calls).
   - Else: target delta = ${delta_target_normal_iv} (default 0.22 for puts, 0.25 for calls), max delta = ${delta_max_normal_iv} (default 0.30 for puts, 0.35 for calls).

3. **Liquidity filter:** Open interest ≥ ${min_open_interest}, bid-ask spread ≤ ${max_spread_pct}% of mid. (Parameters TBD in `06-wheel-parameters.md`; wheelhouz sources: `position_review.py:355–360`.)

4. **Net credit filter:** New premium − cost to close ≥ ${min_roll_net_credit_pct} × original premium.

5. **Stress test:** Calculate loss at underlying −10% and −20%. If loss at −10% > ${max_risk_reward_ratio} × new premium, reject this contract and try the next one.

6. **Pick the strike:**
   - `ROLL_OUT`: Same strike, later expiration.
   - `ROLL_OUT_AND_DOWN`: One strike lower than current strike (put). Or down to ${support_level} if provided in input.
   - `ROLL_OUT_AND_UP`: One strike higher (put, when bullish), or higher (call, when capturing more premium).

If multiple contracts pass all filters, prefer the one with highest net credit.

---

## Auditability

Every recommendation includes a `matrix_cell_id` (e.g., `PUT_NORMAL_MOD_OTM_SQUEEZE`, `PRE_LOSS_STOP_MONTHLY`). This ID uniquely identifies:
- The section of the matrix (pre-guardrails, short put normal, etc.)
- The state combination (moneyness, DTE, outlook, etc.)
- The rule that fired

**Postmortem procedure:** Given a decision + cell ID + timestamp, you can:
1. Look up the cell ID in this document.
2. Fetch the position state (strike, expiration, entry price, etc.) at that timestamp.
3. Fetch the market data (underlying, IV rank, outlook) at that timestamp.
4. Re-run the matrix lookup. It should produce the same cell ID and decision.
5. If it doesn't, the parameters in `06-wheel-parameters.md` changed, or there's a bug in the implementation.

This is the auditability that makes the system trustworthy.

---

## Tests

Every cell in the matrix has a test fixture in `scripts/tests/test_decision_matrix.py`. The fixture defines:
- Input state (position data, market context, chain data)
- Expected decision tag (e.g., `ROLL_OUT`)
- Expected matrix_cell_id (e.g., `PUT_NORMAL_MOD_OTM_ROLL_OUT`)

Test count target: **≥ 80 fixtures** (covering all major cells + boundary conditions + regime transitions).

Example test:

```python
def test_put_normal_mod_otm_squeeze():
    """Moderate OTM put with 50-75% capture and DTE > 14 should recommend GTC limit at 75%."""
    position = ShortPut(strike=100, entry=2.50, current_mid=1.75, expiration=date(2026, 6, 19))
    underlying = Underlying(price=101, outlookTag="NEUTRAL")
    context = Context(ivRank=45, regime="NORMAL", daysToEarnings=45)
    
    result = decision_matrix.evaluate(position, underlying, context)
    
    assert result.decision == "GTC_LIMIT_75"
    assert result.matrix_cell_id == "PUT_NORMAL_MOD_OTM_SQUEEZE"
    assert result.gte_limit_target == 1.875  # 75% of 2.50
```

---

## YAML Companion

Programmatic representation of the matrix for skill loading and runtime lookups.

```yaml
wheel_decision_matrix:
  version: "0.1"
  date: "2026-05-07"
  
  pre_guardrails:
    - id: "PRE_LOSS_STOP_MONTHLY"
      position_type: [SHORT_PUT, SHORT_CALL_UNCOVERED]
      conditions:
        - "current_mid / entry_price >= ${loss_stop_monthly}"
        - "dte > ${weekly_put_dte_threshold}"
      decision: CLOSE
      rationale: "Loss stop triggered: option price ≥ 2× entry. Buy back immediately."
    
    - id: "PRE_LOSS_STOP_WEEKLY"
      position_type: [SHORT_PUT, SHORT_CALL_UNCOVERED]
      conditions:
        - "current_mid / entry_price >= ${loss_stop_weekly}"
        - "dte <= ${weekly_put_dte_threshold}"
      decision: CLOSE
      rationale: "Loss stop triggered (weekly): option price ≥ 1.5× entry. Gamma risk too high."
    
    - id: "PRE_CRASH_STOP"
      position_type: [SHORT_PUT]
      conditions:
        - "pct_change_today <= -${underlying_crash_stop_pct}"
      decision: CLOSE
      rationale: "Circuit breaker: stock crashed 15%+ intraday."
    
    - id: "PRE_TAIL_RISK_OVERRIDE"
      position_type: [SHORT_PUT, SHORT_CALL_UNCOVERED]
      conditions:
        - "tail_risk_name == true"
        - "pending_decision in [DEFENSIVE_ROLL_OUT_AND_DOWN, ROLL_OUT_AND_DOWN, ROLL_OUT]"
      decision: CLOSE
      rationale: "Tail-risk name: rolling doesn't help against gaps. Buy back and walk."
    
    - id: "PRE_OPEN_ORDER"
      conditions:
        - "existing_open_order == true"
      decision: WAIT
      rationale: "Order already submitted. Waiting for fill or cancellation."
    
    - id: "PRE_EARNINGS_LE7D"
      position_type: [SHORT_PUT, SHORT_CALL_UNCOVERED]
      conditions:
        - "0 <= days_to_earnings <= 7"
        - "dte <= ${earnings_guard_dte_max}"
      decision_branches:
        - condition: "profit_captured_pct >= ${earnings_close_profit_min}"
          decision: CLOSE_FOR_PROFIT
        - condition: "profit_captured_pct < ${earnings_close_profit_min}"
          decision: WAIT
      rationale: "Earnings within 7 days: lock profits or wait."
    
    - id: "PRE_EARNINGS_THREATENED"
      position_type: [SHORT_PUT, SHORT_CALL_UNCOVERED]
      conditions:
        - "earnings_proximity == BEFORE_EXPIRY"
        - "profit_captured_pct < ${imminent_threatened_pnl_min}"
        - "delta >= ${imminent_threatened_delta_min}"
      decision: CLOSE
      rationale: "Earnings before expiry, position threatened. Close before binary event."
  
  short_put:
    normal:
      - id: "PUT_NORMAL_DEEP_OTM_LONG_DTE_50"
        moneyness: DEEP_OTM
        dte_band: LONG_DTE
        profit_min: 0.50
        decision: CLOSE_FOR_PROFIT
        parameters: [take_profit_deep_otm_dte_120_plus]
      
      - id: "PUT_NORMAL_DEEP_OTM_MID_DTE_65"
        moneyness: DEEP_OTM
        dte_band: MID_DTE
        profit_min: 0.65
        decision: CLOSE_FOR_PROFIT
        parameters: [take_profit_deep_otm_dte_60_120]
      
      - id: "PUT_NORMAL_DEEP_OTM_SHORT_DTE_80"
        moneyness: DEEP_OTM
        dte_band: SHORT_DTE
        profit_min: 0.80
        decision: CLOSE_FOR_PROFIT
        parameters: [take_profit_deep_otm_dte_under_60]
      
      - id: "PUT_NORMAL_MOD_OTM_TAKEPROFIT"
        moneyness: MODERATE_OTM
        dte_band: any
        profit_min: 0.50
        decision: CLOSE_FOR_PROFIT
        parameters: [take_profit_moderate_otm]
      
      - id: "PUT_NORMAL_MOD_OTM_SQUEEZE"
        moneyness: MODERATE_OTM
        dte_band: [MID_DTE, LONG_DTE]
        profit_min: 0.50
        profit_max: 0.75
        outlook: [NEUTRAL, BULLISH]
        days_to_earnings_min: 30
        decision: GTC_LIMIT_75
        parameters: [squeeze_dte_min, squeeze_capture_band_min, squeeze_capture_band_max]
      
      - id: "PUT_NORMAL_MOD_OTM_WAIT"
        moneyness: MODERATE_OTM
        dte_band: any
        profit_max: 0.50
        outlook: [BULLISH, STRONG_BULLISH]
        decision: WAIT
      
      - id: "PUT_NORMAL_MOD_OTM_ROLL_OUT"
        moneyness: MODERATE_OTM
        dte_band: any
        profit_max: 0.50
        outlook: [NEUTRAL, BEARISH]
        decision: ROLL_OUT
        parameters: [delta_target_normal_iv, delta_max_normal_iv]
      
      - id: "PUT_NORMAL_NEAR_ATM_TAKEPROFIT"
        moneyness: NEAR_ATM
        dte_band: any
        profit_min: 0.40
        decision: CLOSE_FOR_PROFIT
        parameters: [take_profit_near_atm]
      
      - id: "PUT_NORMAL_ITM_LONG_DTE_BULLISH"
        moneyness: ITM
        dte_band: LONG_DTE
        outlook: [BULLISH, STRONG_BULLISH]
        decision: WAIT
      
      - id: "PUT_NORMAL_ITM_LONG_DTE_NEUTRAL"
        moneyness: ITM
        dte_band: LONG_DTE
        outlook: NEUTRAL
        decision: ROLL_OUT
        parameters: [delta_target_normal_iv]
      
      - id: "PUT_NORMAL_ITM_LONG_DTE_BEARISH"
        moneyness: ITM
        dte_band: LONG_DTE
        outlook: [BEARISH, STRONG_BEARISH]
        decision: TAKE_ASSIGNMENT
      
      - id: "PUT_NORMAL_ITM_SHORT_DTE_BULLISH"
        moneyness: ITM
        dte_band: SHORT_DTE
        outlook: [BULLISH, STRONG_BULLISH]
        decision: ROLL_OUT_AND_DOWN
        parameters: [delta_target_normal_iv]
      
      - id: "PUT_NORMAL_ITM_SHORT_DTE_NEUTRAL"
        moneyness: ITM
        dte_band: SHORT_DTE
        outlook: NEUTRAL
        decision: TAKE_ASSIGNMENT
      
      - id: "PUT_NORMAL_ITM_SHORT_DTE_BEARISH"
        moneyness: ITM
        dte_band: SHORT_DTE
        outlook: [BEARISH, STRONG_BEARISH]
        decision: TAKE_ASSIGNMENT
      
      - id: "PUT_NORMAL_HIGH_IV_EARLY_CLOSE"
        moneyness: [MODERATE_OTM, NEAR_ATM]
        dte_band: SHORT_DTE
        profit_min: 0.50
        iv_regime: HIGH
        decision: CLOSE_FOR_PROFIT
        parameters: [high_iv_early_close_iv_rank_min, high_iv_early_close_dte_max]
      
      - id: "PUT_NORMAL_TIME_DECAY_ONLY"
        moneyness: MODERATE_OTM
        dte_band: SHORT_DTE
        profit_min: 0.30
        decision: CLOSE_FOR_PROFIT
        parameters: [time_decay_only_dte_max, time_decay_only_capture_min]
    
    caution:
      - id: "PUT_CAUTION_DEFENSIVE_ROLL"
        moneyness: ITM
        dte_band: [MID_DTE, LONG_DTE]
        loss_min: 1.30
        decision: DEFENSIVE_ROLL_OUT_AND_DOWN
        parameters: [defensive_roll_caution_loss_trigger_multiplier, min_dte_for_roll]
      
      - id: "PUT_CAUTION_DELTA_EXPANSION"
        moneyness: any
        dte_band: [MID_DTE, LONG_DTE]
        delta_min: 0.35
        loss_min: 1.20
        decision: DEFENSIVE_ROLL_OUT_AND_DOWN
        parameters: [delta_expansion_threshold, delta_expansion_loss_trigger]
    
    risk_off:
      - id: "PUT_RISK_OFF_DEFENSIVE_ROLL"
        moneyness: ITM
        dte_band: [MID_DTE, LONG_DTE]
        loss_min: 1.20
        decision: CLOSE
        parameters: [TBD_defensive_roll_risk_off_trigger_multiplier]
        note: "RISK_OFF: close instead of rolling. Value TBD."
  
  short_call_covered:
    normal:
      - id: "CALL_NORMAL_OTM_SAFE_TAKEPROFIT"
        moneyness: OTM
        dte_band: any
        profit_min: 0.50
        decision: CLOSE_FOR_PROFIT
        parameters: [take_profit_moderate_otm]
      
      - id: "CALL_NORMAL_OTM_SAFE_HOLD"
        moneyness: OTM
        dte_band: any
        profit_max: 0.50
        outlook: [BEARISH, NEUTRAL]
        decision: WAIT
      
      - id: "CALL_NORMAL_OTM_SAFE_ROLL"
        moneyness: OTM
        dte_band: any
        profit_max: 0.50
        outlook: [BULLISH, STRONG_BULLISH]
        decision: ROLL_OUT
        parameters: [delta_target_normal_iv]
      
      - id: "CALL_NORMAL_NEAR_ATM_TAKEPROFIT"
        moneyness: NEAR_ATM
        dte_band: any
        profit_min: 0.40
        decision: CLOSE_FOR_PROFIT
        parameters: [take_profit_near_atm]
      
      - id: "CALL_NORMAL_NEAR_ATM_ROLL_UP"
        moneyness: NEAR_ATM
        dte_band: any
        profit_max: 0.40
        outlook: [BULLISH, STRONG_BULLISH]
        decision: ROLL_OUT_AND_UP
        parameters: [delta_target_normal_iv]
      
      - id: "CALL_NORMAL_ITM_LONG_DTE_BULLISH"
        moneyness: ITM
        dte_band: LONG_DTE
        outlook: [BULLISH, STRONG_BULLISH]
        decision: WAIT
      
      - id: "CALL_NORMAL_ITM_LONG_DTE_NEUTRAL"
        moneyness: ITM
        dte_band: LONG_DTE
        outlook: NEUTRAL
        decision: ROLL_OUT_AND_UP
        parameters: [delta_target_normal_iv]
      
      - id: "CALL_NORMAL_ITM_LONG_DTE_BEARISH"
        moneyness: ITM
        dte_band: LONG_DTE
        outlook: [BEARISH, STRONG_BEARISH]
        decision: CLOSE
      
      - id: "CALL_NORMAL_ITM_SHORT_DTE_BULLISH"
        moneyness: ITM
        dte_band: SHORT_DTE
        outlook: [BULLISH, STRONG_BULLISH]
        decision: LET_EXPIRE
      
      - id: "CALL_NORMAL_ITM_SHORT_DTE_NEUTRAL"
        moneyness: ITM
        dte_band: SHORT_DTE
        outlook: NEUTRAL
        decision: TAKE_ASSIGNMENT
      
      - id: "CALL_NORMAL_ITM_SHORT_DTE_BEARISH"
        moneyness: ITM
        dte_band: SHORT_DTE
        outlook: [BEARISH, STRONG_BEARISH]
        decision: CLOSE
      
      - id: "CALL_NORMAL_EX_DIV_ITM"
        moneyness: [NEAR_ATM, ITM]
        dividend_proximity: EX_DIV_LE_3D
        decision: CLOSE_FOR_PROFIT
        warning: "Early assignment risk on ex-dividend date."
  
  post_guardrails:
    - id: "POST_ROLL_TARGET_INSUFFICIENT"
      condition: "decision in [ROLL_OUT, ROLL_OUT_AND_DOWN, ROLL_OUT_AND_UP] AND no_valid_target"
      modification: "CLOSE"
      rationale: "No acceptable roll target in available chain."
    
    - id: "POST_STRESS_TEST_FAIL"
      condition: "decision in [ROLL_*] AND loss_at_minus_10pct > 3.0 * premium"
      modification: "CLOSE"
      rationale: "Roll target fails stress test."
    
    - id: "POST_NET_CREDIT_INSUFFICIENT"
      condition: "decision in [ROLL_*] AND net_credit < min_roll_net_credit_pct"
      modification: "CLOSE"
      rationale: "Net credit insufficient to justify roll."
    
    - id: "POST_EX_DIV_WARNING"
      condition: "moneyness in [NEAR_ATM, ITM] AND dividend_proximity == EX_DIV_LE_3D"
      modification: "flag_warning"
      warning: "Early assignment risk on ex-dividend. Consider closing."
    
    - id: "POST_EARNINGS_BEFORE_ROLL"
      condition: "decision in [ROLL_*] AND earnings_in_target_window"
      modification: "snap_expiration_post_earnings"
      rationale: "Roll target adjusted to 30 days after earnings."
  
  roll_target_selection:
    step_1_expiration: ">= current_expiry + ${min_dte_for_roll}"
    step_2_delta: "target_delta from ${delta_target_normal_iv} or ${delta_target_high_iv}, max from ${delta_max_normal_iv} or ${delta_max_high_iv}"
    step_3_liquidity: "open_interest >= ${min_open_interest}, bid_ask_pct <= ${max_spread_pct}"
    step_4_net_credit: ">= ${min_roll_net_credit_pct} * original_premium"
    step_5_stress_test: "loss_at_minus_10pct <= ${max_risk_reward_ratio} * new_premium"
    step_6_strike_selection:
      roll_out: "same_strike, later_expiration"
      roll_out_and_down: "one_strike_lower (put) or support_level"
      roll_out_and_up: "one_strike_higher (put or call)"
```

---

## Summary

This matrix is the operational heart of the wheel-roll-advisor skill. It encodes 12+ years of options trading heuristics as a flat lookup table. Key characteristics:

- **Data-driven:** Every threshold is a parameter reference, not hardcoded. Tuning is a parameter-file edit.
- **Exhaustive:** Covers ~35 distinct state combinations (PUT×regimes + CALL) producing ~8 decision types.
- **Auditable:** Every cell has a stable ID for postmortem replay.
- **Testable:** One test fixture per cell; ≥80 total tests ensure consistency across runs.
- **Deterministic:** Same inputs → same decision, every time. No LLM judgment in the matrix itself.

The matrix is not a replacement for judgment; it's a codification of judgment that makes it repeatable, debuggable, and tunable.
