# Equity Decision Matrix

**Status:** Draft v0.1
**Date:** 2026-05-07
**Used by:** `daily-portfolio-briefing` Step 4 (Portfolio review — equity positions)
**Companion files:**
- `06-wheel-parameters.md` — numerical thresholds (concentration, sizing, regime bands)
- `07-tail-risk-names.md` — curated list of conservative-treatment equities
- `05-briefing-directives.md` — how directives override matrix decisions

This document is the deterministic lookup table for equity (long stock) position recommendations. It maps position state (thesis, technical, triggers, concentration) to one of seven decision tags: `HOLD`, `ADD`, `TRIM`, `EXIT`, `REVIEW`, `DEFERRED`, `MANUAL`. Every cell is data-driven: thresholds reference parameters from `06-wheel-parameters.md`, and tuning a single parameter shifts the matrix's behavior automatically. Every recommendation includes a `matrix_cell_id` for auditability and postmortem replay.

**Key design principle:** This matrix encodes trading judgment as a flat lookup table, not as if/else branches. The goal is determinism, auditability, and simplicity — the same position state always produces the same recommendation, every time.

---

## Reading the Matrix

The skill executes this procedure for each equity position:

1. **Parse the position.** Extract: ticker, shares, cost_basis_avg, current_price, market_value, unrealized_pl_pct, weight_in_portfolio, holding_days, lot_lts status.

2. **Compute derived state:**
   - `concentration_status`: Compare current position weight to ${max_per_single_name_pct} and ${thesis_target_weight_pct}. Classify as `WITHIN_TARGET` (0.95 × target ≤ weight ≤ 1.05 × target), `UNDERWEIGHT` (weight < 0.95 × target), `OVERWEIGHT` (weight > 1.05 × target), or `BREACH` (weight > ${max_per_single_name_pct}, currently 10% NLV).
   - `unrealized_pl_bucket`: Classify as LOSS (<0%), BREAKEVEN (0-5%), GAIN_MODERATE (5-25%), GAIN_STRONG (>25%).
   - `tax_lot_status`: From trader-memory-core. Classify as `LTCG_ELIGIBLE` (>365 days from purchase, gain >$5K), `LTCG_APPROACHING` (90-365 days, gain >$5K), or `STCG` (<90 days or gain <$5K).
   - `thesis_health`: From thesis assessment in trader-memory-core. Classify as `INTACT`, `WEAKENING`, or `BROKEN`.
   - `technical_tag`: From technical-analyst. Classify as `STRONG_UPTREND`, `UPTREND`, `SIDEWAYS`, `DOWNTREND`, or `STRONG_DOWNTREND`.
   - `triggers`: Set of active conditions — combination of {`PRICE_BELOW_INVALIDATION`, `MA50_LOST`, `MA200_LOST`, `EARNINGS_LE_7D`, `NEWS_NEGATIVE`, `THESIS_REVIEW_DUE`, `LTCG_APPROACHING`, `EX_DIVIDEND_LE_5D`}.
   - `regime`: From regime framework (RISK_ON / NORMAL / CAUTION / RISK_OFF).

3. **Apply pre-matrix guardrails** (section below). If any guardrail fires, return that decision and **skip the matrix lookup**.

4. **Look up the matrix** (sections "NORMAL Regime", "CAUTION Regime", "RISK_OFF Regime"). Find the row whose conditions all match. Return the decision tag + matrix_cell_id.

5. **Apply post-matrix guardrails** (section below). These can modify or downgrade the decision from step 4.

6. **Return structured output.** Include: ticker, current_price, P&L, weight, thesis_health, technical_tag, **decision_tag**, **rationale (1–2 sentences)**, matrix_cell_id, any directive that affected the recommendation.

---

## State Variables

Reference guide to inputs used in the matrix. All boundaries and thresholds reference `06-wheel-parameters.md`.

| Variable | Type | Values | Parameter Tie | Meaning |
|----------|------|--------|----------------|---------|
| `ticker` | string | e.g., AAPL, MSFT | N/A | Stock symbol |
| `thesis_status` | enum | IDEA / ENTRY_READY / ACTIVE / CLOSED / INVALIDATED | N/A | From trader-memory-core lifecycle |
| `thesis_health` | enum | INTACT / WEAKENING / BROKEN | N/A | From thesis assessment in trader-memory-core |
| `thesis_target_weight_pct` | float | e.g., 7% | N/A | Declared target weight from thesis |
| `technical_tag` | enum | STRONG_UPTREND, UPTREND, SIDEWAYS, DOWNTREND, STRONG_DOWNTREND | N/A | From technical-analyst |
| `concentration_status` | enum | WITHIN_TARGET, UNDERWEIGHT, OVERWEIGHT, BREACH | `max_per_single_name_pct`, `thesis_target_weight_pct` | Position weight vs target |
| `unrealized_pl_pct` | float | −0.50 to +5.0+ | N/A | Current P&L % (negative = loss) |
| `unrealized_pl_bucket` | enum | LOSS, BREAKEVEN, GAIN_MODERATE, GAIN_STRONG | N/A | P&L category |
| `holding_days` | int | 0–3650+ | N/A | Days held since entry |
| `tax_lot_status` | enum | LTCG_ELIGIBLE, LTCG_APPROACHING, STCG | N/A | Tax treatment status |
| `triggers` | set | {PRICE_BELOW_INVALIDATION, MA50_LOST, MA200_LOST, EARNINGS_LE_7D, NEWS_NEGATIVE, THESIS_REVIEW_DUE, LTCG_APPROACHING, EX_DIVIDEND_LE_5D} | N/A | Active disqualifying conditions |
| `regime` | enum | RISK_ON, NORMAL, CAUTION, RISK_OFF | 06-wheel-parameters.md § regime thresholds | Market regime |
| `weight_in_portfolio` | float | 0.0–1.0 | N/A | Current market value / NLV |
| `directive_active` | bool | true, false | N/A | Any directive (DEFER, MANUAL, OVERRIDE, etc.) applies |

---

## Pre-Matrix Guardrails

Run these checks **in order**. The first guardrail that fires returns a decision and **skips the matrix lookup**.

### Guardrail 1: Untracked Position

**Cell ID:** `PRE_UNTRACKED`

**Condition:**
- No thesis exists in trader-memory-core for this ticker

**Decision:** `REVIEW` with rationale "Untracked position. Register thesis with trader-memory-core to enable recommendations."

**Rationale:** Positions without a thesis lack entry criteria and invalidation triggers. Recommend registration first.

---

### Guardrail 2: Active Directive (DEFER / MANUAL)

**Cell ID:** `PRE_DIRECTIVE_DEFERRED` / `PRE_DIRECTIVE_MANUAL`

**Condition:**
- `directive_active == true AND directive_type in [DEFER, MANUAL]`

**Decision:** `DEFERRED` or `MANUAL` (whichever the directive specifies) with rationale from the directive reason.

**Rationale:** User has explicitly deferred or is manually managing; respect the decision and surface in watch panel only.

---

### Guardrail 3: Concentration Breach

**Cell ID:** `PRE_CONCENTRATION_BREACH`

**Condition:**
- `weight_in_portfolio > ${max_per_single_name_pct} + 0.02` (i.e., 12%+ when limit is 10%)

**Decision:** `TRIM` (urgent) with rationale "Concentration breach: ${weight_in_portfolio}% of portfolio exceeds ${max_per_single_name_pct}% hard limit. Reduce immediately."

**Rationale:** Concentration limits are non-negotiable for portfolio stability. Trim to fit.

---

### Guardrail 4: Thesis Status INVALIDATED

**Cell ID:** `PRE_THESIS_INVALIDATED`

**Condition:**
- `thesis_status == INVALIDATED`

**Decision:** `EXIT` with rationale "Thesis status is INVALIDATED. Close position."

**Rationale:** An invalidated thesis means the thesis no longer holds; closing is the default action.

---

### Guardrail 5: Price Below Invalidation Level

**Cell ID:** `PRE_PRICE_BELOW_INVALIDATION`

**Condition:**
- `PRICE_BELOW_INVALIDATION` trigger is active

**Decision:** `EXIT` with rationale "Price below thesis invalidation level. Close position immediately."

**Rationale:** Explicit invalidation trigger has fired; respect it.

---

### Guardrail 6: LTCG Deferral (Near Window)

**Cell ID:** `PRE_LTCG_DEFER`

**Condition:**
- `tax_lot_status == LTCG_APPROACHING AND would_otherwise_recommend [EXIT or TRIM] AND unrealized_pl_pct > 0.20`

**Decision:** `REVIEW` with rationale "EXIT/TRIM signal but LTCG-eligible in ${days_to_ltcg} days with ${unrealized_pl_pct}% gain. Consider deferral to capture 17% tax savings ($X)."

**Rationale:** A 17% tax difference justifies deferring a trim if the thesis is still intact. Flag for manual review rather than auto-exit.

---

### Guardrail 7: Tail-Risk Name

**Cell ID:** `PRE_TAIL_RISK_OVERRIDE`

**Condition:**
- `ticker in tail_risk_names.md AND [thesis_status == ACTIVE]`

**Decision:** Flag as WATCH with warning "⚠️ TAIL RISK: ${ticker} is on conservative-treatment list (headline-gap exposure). No ADD recommendations. On EXIT conditions, close rather than trim. Aggressive profit-taking >30% capture."

**Rationale:** Tail-risk names (Chinese ADRs, binary biotechs, crypto-proxies) can gap 15–25% on discrete events. Wheel mechanics (hold and collect premium) don't apply; cut losses and take profits early.

---

### Guardrail 8: Invalid Input or Missing Data

**Cell ID:** `PRE_INVALID_INPUT`

**Condition:**
- `thesis_target_weight_pct is None OR technical_tag is None OR current_price is None`

**Decision:** `REVIEW` with rationale "Incomplete data: ${missing_field}. Skipping recommendation until data is available."

**Rationale:** Don't recommend without complete picture.

---

## Equity Matrix — NORMAL Regime

For positions where `regime == NORMAL`. Rows are ordered by decision priority: EXIT first, then TRIM, ADD, HOLD, REVIEW.

### Thesis INTACT × Uptrend

| Cell ID | Thesis | Technical | Concentration | Triggers | PL Bucket | Decision | Rationale |
|---------|--------|-----------|----------------|----------|-----------|----------|-----------|
| `EQ_NORMAL_INTACT_UPTREND_UW_CLEAN` | INTACT | STRONG_UPTREND / UPTREND | UNDERWEIGHT | none | any | ADD | Thesis playing out, position below target weight. Size per position-sizer. |
| `EQ_NORMAL_INTACT_UPTREND_TARGET_CLEAN` | INTACT | STRONG_UPTREND / UPTREND | WITHIN_TARGET | none | any | HOLD | Thesis intact, sized appropriately. Let thesis run. |
| `EQ_NORMAL_INTACT_UPTREND_OW_CLEAN` | INTACT | STRONG_UPTREND / UPTREND | OVERWEIGHT | none | GAIN_STRONG | TRIM | Thesis working but position >110% of target. Lock some gains. Trim 10–20% of position. |
| `EQ_NORMAL_INTACT_UPTREND_OW_BREAKEVEN` | INTACT | UPTREND | OVERWEIGHT | none | BREAKEVEN | HOLD | Overweight but no profit cushion. Hold and rebalance on next rally. |
| `EQ_NORMAL_INTACT_UPTREND_EARNINGS_LE7` | INTACT | UPTREND | any | EARNINGS_LE_7D | any | HOLD | Earnings within 7 days; thesis intact. Hold through report. Monitor for invalidation. |

### Thesis INTACT × Sideways

| Cell ID | Thesis | Technical | Concentration | Triggers | Decision | Rationale |
|---------|--------|-----------|----------------|----------|----------|-----------|
| `EQ_NORMAL_INTACT_SIDEWAYS_ANY` | INTACT | SIDEWAYS | any | none | HOLD | Thesis intact, price neutral. No action; thesis remains valid. |
| `EQ_NORMAL_INTACT_SIDEWAYS_REVIEW_DUE` | INTACT | SIDEWAYS | any | THESIS_REVIEW_DUE | REVIEW | Thesis review is due. Assess thesis health before next briefing. |

### Thesis INTACT × Downtrend

| Cell ID | Thesis | Technical | Concentration | Triggers | PL Bucket | Decision | Rationale |
|---------|--------|-----------|----------------|----------|-----------|----------|-----------|
| `EQ_NORMAL_INTACT_DOWNTREND_TARGET_HOLD` | INTACT | DOWNTREND | WITHIN_TARGET | none | GAIN_MODERATE | HOLD | Thesis intact; position consolidating. Stay disciplined. |
| `EQ_NORMAL_INTACT_DOWNTREND_TARGET_REVIEW` | INTACT | STRONG_DOWNTREND | WITHIN_TARGET | none | LOSS | REVIEW | Strong downtrend but thesis intact. Assess: is this noise or invalidation? |
| `EQ_NORMAL_INTACT_DOWNTREND_OW_TRIM` | INTACT | DOWNTREND | OVERWEIGHT | none | any | TRIM | Overweight during downtrend. Reduce to free capital for better ideas. |

### Thesis WEAKENING × Uptrend

| Cell ID | Thesis | Technical | Concentration | Triggers | PL Bucket | Decision | Rationale |
|---------|--------|-----------|----------------|----------|-----------|----------|-----------|
| `EQ_NORMAL_WEAKENING_UPTREND_HOLD` | WEAKENING | UPTREND | WITHIN_TARGET | none | GAIN_STRONG | HOLD | Thesis weakening but momentum intact and profitable. Hold but watch closely. |
| `EQ_NORMAL_WEAKENING_UPTREND_REVIEW` | WEAKENING | UPTREND | any | NEWS_NEGATIVE | LOSS | REVIEW | Thesis weakening + negative news. Manual assessment of thesis validity. |

### Thesis WEAKENING × Sideways

| Cell ID | Thesis | Technical | Concentration | Triggers | Decision | Rationale |
|---------|--------|-----------|----------------|----------|----------|-----------|
| `EQ_NORMAL_WEAKENING_SIDEWAYS_UW_HOLD` | WEAKENING | SIDEWAYS | UNDERWEIGHT | none | HOLD | Thesis weakening but position is small. Hold; thesis may recover. |
| `EQ_NORMAL_WEAKENING_SIDEWAYS_OW_TRIM` | WEAKENING | SIDEWAYS | OVERWEIGHT | none | TRIM | Thesis weakening + oversized. Trim to free capital. |
| `EQ_NORMAL_WEAKENING_SIDEWAYS_BREAKEVEN_EXIT` | WEAKENING | SIDEWAYS | any | none | BREAKEVEN | EXIT | Thesis weakening, no profit cushion. Exit before thesis breaks. |

### Thesis WEAKENING × Downtrend

| Cell ID | Thesis | Technical | Concentration | Triggers | PL Bucket | Decision | Rationale |
|---------|--------|-----------|----------------|----------|-----------|----------|-----------|
| `EQ_NORMAL_WEAKENING_DOWNTREND_GAIN_TRIM` | WEAKENING | DOWNTREND | any | none | GAIN_MODERATE | TRIM | Thesis + technicals both weakening but still profitable. Trim to reduce losses. |
| `EQ_NORMAL_WEAKENING_DOWNTREND_LOSS_EXIT` | WEAKENING | DOWNTREND | any | none | LOSS | EXIT | Thesis + technicals both failing, underwater. Close. |
| `EQ_NORMAL_WEAKENING_DOWNTREND_EARNINGS_DUE` | WEAKENING | DOWNTREND | any | EARNINGS_LE_7D | LOSS | EXIT | Weakening thesis + downtrend + earnings approaching. Close before binary event. |

### Thesis BROKEN

| Cell ID | Thesis | Technical | Decision | Rationale |
|---------|--------|-----------|----------|-----------|
| `EQ_NORMAL_BROKEN_ANY_EXIT` | BROKEN | any | EXIT | Thesis broken. No other consideration. Exit immediately or TRIM if substantial tax impact. |

---

## Equity Matrix — CAUTION Regime

For positions where `regime == CAUTION`. Defaults shift more conservative: hold with higher scrutiny, add recommendations are rarer, trim thresholds lower.

### Thesis INTACT × Uptrend (CAUTION)

| Cell ID | Thesis | Technical | Concentration | Decision | Rationale |
|---------|--------|-----------|----------------|----------|-----------|
| `EQ_CAUTION_INTACT_UPTREND_UW_ADD` | INTACT | UPTREND | UNDERWEIGHT | ADD | Conservative: underweight + intact thesis + uptrend still warrants ADD, but size lower. |
| `EQ_CAUTION_INTACT_UPTREND_TARGET_HOLD` | INTACT | UPTREND | WITHIN_TARGET | HOLD | Sized appropriately in CAUTION regime. Let thesis run but monitor closely. |
| `EQ_CAUTION_INTACT_UPTREND_OW_TRIM` | INTACT | UPTREND | OVERWEIGHT | TRIM | Overweight in defensive regime. Trim to reduce portfolio risk. |

### Thesis INTACT × Sideways/Downtrend (CAUTION)

| Cell ID | Thesis | Technical | Concentration | Decision | Rationale |
|---------|--------|-----------|----------------|----------|-----------|
| `EQ_CAUTION_INTACT_SIDEWAYS_ANY_HOLD` | INTACT | SIDEWAYS | any | HOLD | Thesis intact but macro caution. Hold and don't rebalance until regime clears. |
| `EQ_CAUTION_INTACT_DOWNTREND_TARGET_HOLD` | INTACT | DOWNTREND | WITHIN_TARGET | HOLD | Thesis intact, sized normally. Monitor for invalidation more closely. |
| `EQ_CAUTION_INTACT_DOWNTREND_OW_TRIM` | INTACT | DOWNTREND | OVERWEIGHT | TRIM | Downtrend in CAUTION regime. Reduce overweight immediately. |

### Thesis WEAKENING (CAUTION)

| Cell ID | Thesis | Technical | Concentration | Decision | Rationale |
|---------|--------|-----------|----------------|----------|-----------|
| `EQ_CAUTION_WEAKENING_ANY_TRIM` | WEAKENING | any | OVERWEIGHT | TRIM | Thesis weakening + overweight in CAUTION = urgent trim. |
| `EQ_CAUTION_WEAKENING_ANY_REVIEW` | WEAKENING | DOWNTREND | WITHIN_TARGET | REVIEW | Thesis weakening + downtrend but within weight. Manual assessment. |
| `EQ_CAUTION_WEAKENING_ANY_EXIT` | WEAKENING | DOWNTREND | any | EXIT | Thesis weakening + strong downtrend in CAUTION regime. Close. |

### Thesis BROKEN (CAUTION)

| Cell ID | Thesis | Technical | Decision | Rationale |
|---------|--------|-----------|----------|-----------|
| `EQ_CAUTION_BROKEN_ANY_EXIT` | BROKEN | any | EXIT | Thesis broken + CAUTION regime. Exit immediately. Thesis + macro = no ambiguity. |

---

## Equity Matrix — RISK_OFF Regime

For positions where `regime == RISK_OFF`. Most defensive: suppress ADD, tighten TRIM thresholds, exit readily on any weakness.

### Thesis INTACT (RISK_OFF)

| Cell ID | Thesis | Technical | Concentration | Decision | Rationale |
|---------|--------|-----------|----------------|----------|-----------|
| `EQ_RISK_OFF_INTACT_UPTREND_NO_ADD` | INTACT | UPTREND | UNDERWEIGHT | HOLD | Thesis intact + uptrend but RISK_OFF regime. Do not ADD. Hold existing. |
| `EQ_RISK_OFF_INTACT_ANY_OW_TRIM` | INTACT | any | OVERWEIGHT | TRIM | Overweight in RISK_OFF. Trim immediately to reduce exposure. |
| `EQ_RISK_OFF_INTACT_ANY_TRIM_BIAS` | INTACT | SIDEWAYS / DOWNTREND | WITHIN_TARGET | TRIM | RISK_OFF regime bias: trim 15–25% of position as defensive rebalance. |

### Thesis WEAKENING (RISK_OFF)

| Cell ID | Thesis | Technical | Concentration | Decision | Rationale |
|---------|--------|-----------|----------------|----------|-----------|
| `EQ_RISK_OFF_WEAKENING_ANY_TRIM_EXIT` | WEAKENING | any | any | EXIT | Thesis weakening + RISK_OFF regime = exit now. No ambiguity. |

### Thesis BROKEN (RISK_OFF)

| Cell ID | Thesis | Technical | Decision | Rationale |
|---------|--------|-----------|----------|-----------|
| `EQ_RISK_OFF_BROKEN_ANY_EXIT` | BROKEN | any | EXIT | Thesis broken + RISK_OFF regime. Exit immediately. |

---

## Post-Matrix Guardrails

After the matrix returns a decision, run these checks. They can **modify or downgrade** the decision.

### Guardrail A: LTCG Window — Defer Large Gains

**Cell ID:** `POST_LTCG_DEFER`

**Condition:**
- Decision is `TRIM` or `EXIT`
- `tax_lot_status == LTCG_APPROACHING AND unrealized_pl_pct > 0.20`
- Days to LTCG ≤ 90

**Modification:** Downgrade `EXIT` → `TRIM` (sell 50–75% of position), or maintain `TRIM` but note "deferred exit: LTCG in ${days_to_ltcg} days. Tax savings ~${estimated_tax_savings}. Plan exit after LTCG threshold."

**Rationale:** 17% tax difference on large gains justifies deferring a full exit. Partial trim reduces portfolio risk without full tax hit.

---

### Guardrail B: Tax-Loss Harvest Opportunity

**Cell ID:** `POST_TAX_LOSS_HARVEST`

**Condition:**
- Decision is `EXIT`
- `unrealized_pl_pct < -0.10 AND unrealized_pl_pct > 0` (loss between 10–30%)
- No wash-sale conflict detected (no sale of same/similar ticker within 30 calendar days prior)

**Modification:** Append to rationale "Tax-loss harvest candidate: $X loss. Plan redemption with replacement equity or ETF for sector exposure."

**Rationale:** Turn a loss into tax alpha by harvesting the loss. Surface the opportunity without modifying the decision.

---

### Guardrail C: Concentration Post-ADD

**Cell ID:** `POST_CONCENTRATION_CHECK_ADD`

**Condition:**
- Decision is `ADD`
- Proposed shares would push `weight_in_portfolio + proposed_weight > ${max_per_single_name_pct} − 0.01` (i.e., >9.9% when limit is 10%)

**Modification:** Scale the ADD size down to fit. Append "ADD capped at ${fitted_shares} shares to stay ≤ 10% concentration."

**Rationale:** ADD must not violate concentration limits. Scale rather than reject.

---

### Guardrail D: Earnings Window Penalty

**Cell ID:** `POST_EARNINGS_URGENCY`

**Condition:**
- Decision is `HOLD`
- `EARNINGS_LE_7D` trigger is active
- `thesis_health == WEAKENING`

**Modification:** Upgrade `HOLD` → `TRIM` with rationale "Thesis weakening + earnings within 7 days. Trim before binary event. Re-enter post-earnings if thesis recovers."

**Rationale:** Weaken + imminent binary = reduce exposure.

---

### Guardrail E: MA Break — Early Warning

**Cell ID:** `POST_MA_BREAK`

**Condition:**
- Decision is `HOLD`
- `MA50_LOST` OR `MA200_LOST` trigger fired
- `thesis_health == INTACT`

**Modification:** Flag as `HOLD` with warning "⚠️ Technical alert: ${ticker} broke below 50/200-day MA. Thesis intact but monitor closely for invalidation. If breaks below 2nd support level, escalate to TRIM."

**Rationale:** Alert user to technical deterioration while preserving the HOLD decision (thesis still intact).

---

### Guardrail F: Directive Override — Parameter Modification

**Cell ID:** `POST_DIRECTIVE_OVERRIDE`

**Condition:**
- An `OVERRIDE` directive applies to this position
- `directive_active == true AND directive_type == OVERRIDE`

**Modification:** Apply the override parameter (e.g., `trim_threshold_pct = 25%` instead of 15%), recompute the recommendation, and append `_DIRECTIVE_<id>` to the matrix_cell_id.

**Rationale:** User has explicitly adjusted the threshold for this position. Honor it and mark for auditability.

---

## Triggers — Definitions and Detection

**PRICE_BELOW_INVALIDATION:** `current_price <= thesis.invalidation_price`. Fired when a position falls through the thesis's hard stop. Hard trigger; always exit.

**MA50_LOST:** Closing price below 50-day moving average for 2+ consecutive days. Technical deterioration signal.

**MA200_LOST:** Closing price below 200-day moving average. Long-term uptrend broken.

**EARNINGS_LE_7D:** Days until next earnings ≤ 7. From earnings-calendar. Binary event warning.

**NEWS_NEGATIVE:** From market-news-analyst, impact ≥ MEDIUM, sentiment NEGATIVE. Reputational or operational threat.

**THESIS_REVIEW_DUE:** `thesis.next_review_date <= today`. From trader-memory-core. Scheduled thesis health check.

**LTCG_APPROACHING:** `holding_days in [275, 365] AND unrealized_pl_pct > 0.05`. Tax planning window open.

**EX_DIVIDEND_LE_5D:** Days until ex-dividend date ≤ 5. From dividend calendar (if available).

---

## Recommendation Tag Semantics

| Tag | What it means | When to use | Expected user action |
|---|---|---|---|
| `HOLD` | Position appropriate; thesis intact; no action needed | Thesis working as planned | Nothing. Monitor. |
| `ADD` | Underweight given conviction; buy more | Thesis intact + below target weight + bullish setup | Place buy order via position-sizer |
| `TRIM` | Overweight or weakening; reduce | Position >110% of target OR thesis weakening | Sell 10–25% of position |
| `EXIT` | Thesis broken; close position | Thesis BROKEN OR price below invalidation | Sell entire position |
| `REVIEW` | Matrix can't decide; manual review needed | Incomplete data OR edge case | Read position notes; decide manually |
| `DEFERRED` | Active DEFER directive applies | User previously said "wait on this" | Watch panel only; actionable recommendation suppressed |
| `MANUAL` | Active MANUAL directive applies | User said "I'm managing this manually" | Watch panel only; no recommendations |

---

## Auditability

Every recommendation includes a `matrix_cell_id` (e.g., `EQ_NORMAL_INTACT_UPTREND_OW_CLEAN`, `PRE_CONCENTRATION_BREACH`). This ID uniquely identifies:
- The section of the matrix (pre-guardrails, NORMAL regime, CAUTION regime, etc.)
- The state combination (thesis health, technical tag, concentration band, etc.)
- The rule that fired

**Postmortem procedure:** Given a decision + cell ID + timestamp, you can:
1. Look up the cell ID in this document.
2. Fetch the position state (ticker, price, P&L, weight, etc.) at that timestamp.
3. Fetch the market context (technical, regime, triggers) at that timestamp.
4. Re-run the matrix lookup. It should produce the same cell ID and decision.
5. If it doesn't, either parameters in `06-wheel-parameters.md` changed, or there's a bug in the implementation.

This repeatability is the foundation of trustworthy automation.

---

## Tests

Every cell in the matrix has a test fixture in `scripts/tests/test_equity_decision_matrix.py`. The fixture defines:
- Input state (position, thesis, technical, triggers, regime)
- Expected decision tag (e.g., `ADD`, `TRIM`, `EXIT`)
- Expected matrix_cell_id (e.g., `EQ_NORMAL_INTACT_UPTREND_UW_CLEAN`)

**Test count target:** ≥ 60 fixtures (covering all major cells + boundary conditions + regime transitions + guardrails).

Example test:

```python
def test_eq_normal_intact_uptrend_uw_clean():
    """Intact thesis + strong uptrend + underweight = ADD."""
    position = EquityPosition(
        ticker="AAPL",
        shares=50,
        cost_basis_avg=150.0,
        current_price=180.0,
        weight_in_portfolio=0.07,  # 7% < 9.5% target
        thesis_target_weight_pct=0.095,
        holding_days=200,
    )
    thesis = Thesis(status="ACTIVE", health="INTACT", invalidation_price=140.0)
    technical = "STRONG_UPTREND"
    triggers = set()
    regime = "NORMAL"
    
    result = equity_decision_matrix.evaluate(position, thesis, technical, triggers, regime)
    
    assert result.decision == "ADD"
    assert result.matrix_cell_id == "EQ_NORMAL_INTACT_UPTREND_UW_CLEAN"
    assert "underweight" in result.rationale.lower()
```

---

## YAML Companion

Programmatic representation of the matrix for skill loading and runtime lookups.

```yaml
equity_decision_matrix:
  version: "0.1"
  date: "2026-05-07"
  
  pre_guardrails:
    - id: "PRE_UNTRACKED"
      condition: "no_thesis_in_trader_memory_core"
      decision: REVIEW
      rationale: "Untracked position. Register thesis with trader-memory-core."
    
    - id: "PRE_DIRECTIVE_DEFERRED"
      condition: "directive_active AND directive_type == DEFER"
      decision: DEFERRED
      rationale: "User directive: DEFER"
    
    - id: "PRE_DIRECTIVE_MANUAL"
      condition: "directive_active AND directive_type == MANUAL"
      decision: MANUAL
      rationale: "User directive: MANUAL"
    
    - id: "PRE_CONCENTRATION_BREACH"
      condition: "weight_in_portfolio > max_per_single_name_pct + 0.02"
      decision: TRIM
      rationale: "Concentration breach: ${weight_in_portfolio}% > ${max_per_single_name_pct}%"
    
    - id: "PRE_THESIS_INVALIDATED"
      condition: "thesis_status == INVALIDATED"
      decision: EXIT
      rationale: "Thesis status is INVALIDATED."
    
    - id: "PRE_PRICE_BELOW_INVALIDATION"
      condition: "PRICE_BELOW_INVALIDATION in triggers"
      decision: EXIT
      rationale: "Price below thesis invalidation level."
    
    - id: "PRE_LTCG_DEFER"
      condition: "tax_lot_status == LTCG_APPROACHING AND unrealized_pl_pct > 0.20 AND [EXIT or TRIM would_be_recommended]"
      decision: REVIEW
      rationale: "EXIT/TRIM signal but LTCG-eligible in ${days_to_ltcg} days."
    
    - id: "PRE_TAIL_RISK_OVERRIDE"
      condition: "ticker in tail_risk_names.md AND thesis_status == ACTIVE"
      decision: HOLD_WITH_WARNING
      warning: "TAIL RISK: headline-gap exposure. No ADD. Aggressive profit-taking."
    
    - id: "PRE_INVALID_INPUT"
      condition: "missing [thesis_target_weight_pct OR technical_tag OR current_price]"
      decision: REVIEW
      rationale: "Incomplete data: ${missing_field}."
  
  normal_regime:
    intact_uptrend:
      - id: "EQ_NORMAL_INTACT_UPTREND_UW_CLEAN"
        thesis: INTACT
        technical: [STRONG_UPTREND, UPTREND]
        concentration: UNDERWEIGHT
        triggers: null
        decision: ADD
      
      - id: "EQ_NORMAL_INTACT_UPTREND_TARGET_CLEAN"
        thesis: INTACT
        technical: [STRONG_UPTREND, UPTREND]
        concentration: WITHIN_TARGET
        triggers: null
        decision: HOLD
      
      - id: "EQ_NORMAL_INTACT_UPTREND_OW_CLEAN"
        thesis: INTACT
        technical: [STRONG_UPTREND, UPTREND]
        concentration: OVERWEIGHT
        triggers: null
        pl_bucket: [GAIN_MODERATE, GAIN_STRONG]
        decision: TRIM
    
    intact_sideways:
      - id: "EQ_NORMAL_INTACT_SIDEWAYS_ANY"
        thesis: INTACT
        technical: SIDEWAYS
        concentration: any
        triggers: null
        decision: HOLD
    
    intact_downtrend:
      - id: "EQ_NORMAL_INTACT_DOWNTREND_TARGET_HOLD"
        thesis: INTACT
        technical: DOWNTREND
        concentration: WITHIN_TARGET
        triggers: null
        pl_bucket: [GAIN_MODERATE, GAIN_STRONG]
        decision: HOLD
    
    weakening_any:
      - id: "EQ_NORMAL_WEAKENING_ANY_TRIM_OR_EXIT"
        thesis: WEAKENING
        technical: [DOWNTREND, STRONG_DOWNTREND]
        concentration: any
        triggers: null
        pl_bucket: LOSS
        decision: EXIT
    
    broken_any:
      - id: "EQ_NORMAL_BROKEN_ANY_EXIT"
        thesis: BROKEN
        technical: any
        decision: EXIT
  
  caution_regime:
    - id: "EQ_CAUTION_INTACT_UPTREND_UW_ADD"
      thesis: INTACT
      technical: UPTREND
      concentration: UNDERWEIGHT
      decision: ADD
      note: "Conservative: size lower than NORMAL"
    
    - id: "EQ_CAUTION_INTACT_ANY_OW_TRIM"
      thesis: INTACT
      concentration: OVERWEIGHT
      decision: TRIM
      note: "Overweight in defensive regime"
    
    - id: "EQ_CAUTION_WEAKENING_ANY_EXIT"
      thesis: WEAKENING
      technical: DOWNTREND
      decision: EXIT
    
    - id: "EQ_CAUTION_BROKEN_ANY_EXIT"
      thesis: BROKEN
      decision: EXIT
  
  risk_off_regime:
    - id: "EQ_RISK_OFF_INTACT_UPTREND_NO_ADD"
      thesis: INTACT
      technical: UPTREND
      concentration: UNDERWEIGHT
      decision: HOLD
      note: "Do not ADD in RISK_OFF"
    
    - id: "EQ_RISK_OFF_INTACT_ANY_OW_TRIM"
      thesis: INTACT
      concentration: OVERWEIGHT
      decision: TRIM
    
    - id: "EQ_RISK_OFF_ANY_WEAKENING_OR_BROKEN_EXIT"
      thesis: [WEAKENING, BROKEN]
      decision: EXIT
  
  post_guardrails:
    - id: "POST_LTCG_DEFER"
      condition: "decision in [EXIT, TRIM] AND tax_lot_status == LTCG_APPROACHING AND unrealized_pl_pct > 0.20"
      modification: "[EXIT → TRIM, or append deferral note]"
      rationale: "Defer exit to capture LTCG tax savings."
    
    - id: "POST_TAX_LOSS_HARVEST"
      condition: "decision == EXIT AND unrealized_pl_pct in [-0.30, -0.10]"
      modification: "append_opportunity_note"
      note: "Tax-loss harvest opportunity."
    
    - id: "POST_CONCENTRATION_CHECK_ADD"
      condition: "decision == ADD AND projected_weight > max_per_single_name_pct - 0.01"
      modification: "scale_add_down"
      rationale: "Scale ADD to fit concentration limits."
    
    - id: "POST_EARNINGS_URGENCY"
      condition: "decision == HOLD AND EARNINGS_LE_7D in triggers AND thesis_health == WEAKENING"
      modification: "HOLD → TRIM"
      rationale: "Weakening + imminent earnings = reduce."
    
    - id: "POST_MA_BREAK"
      condition: "decision == HOLD AND [MA50_LOST or MA200_LOST] in triggers AND thesis_health == INTACT"
      modification: "append_warning"
      warning: "Technical alert: MA break. Monitor for invalidation."
    
    - id: "POST_DIRECTIVE_OVERRIDE"
      condition: "directive_type == OVERRIDE"
      modification: "apply_override_parameter_and_append_directive_id"
      rationale: "User has set custom threshold for this position."
```

---

## Summary

This matrix encodes equity position decisions as a deterministic lookup table. Key characteristics:

- **Data-driven:** All thresholds are parameters, not hardcoded. Tuning parameters in `06-wheel-parameters.md` shifts the matrix's behavior automatically.
- **Exhaustive:** Covers ~45 distinct state combinations (thesis health × technical × concentration × regime) producing 7 decision types.
- **Auditable:** Every cell has a stable ID for postmortem replay and consistency checks.
- **Testable:** One test fixture per cell; ≥60 total tests ensure consistency.
- **Deterministic:** Same inputs → same decision, every time. No LLM judgment inside the matrix itself.

The matrix is the operational heart of equity recommendations in the briefing. It trades breadth (all possible combinations) for simplicity (flat lookup, no cascading if/else). When a threshold needs tuning, edit one line in the parameters file; the matrix re-evaluates automatically.
