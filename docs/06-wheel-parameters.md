# Wheel Roll Advisor — Parameters Reference

**Status:** Draft v0.1, sourced from wheelhouz codebase
**Date:** 2026-05-07

This document is the single source of truth for every numerical threshold, list, and tunable in the wheel-roll-advisor skill. Numbers are calibrated to wheelhouz production usage and migrated verbatim. Provenance citations let you audit any value against the original implementation.

The wheel-roll-advisor skill loads this document (or its YAML companion at the bottom of this file) at startup. Decision logic is data-driven — when a parameter changes, no code change is required; the matrix re-evaluates with the new value.

---

## 1. Loss-Stop Multipliers

| Parameter | Value | Applies To | Source | Semantic |
|-----------|-------|-----------|--------|----------|
| `loss_stop_monthly` | 2.0× | Short options, DTE > 10 | monitor/continuous.py:156 | Buy back when option price rises to 2× entry (monthly puts/calls) |
| `loss_stop_weekly` | 1.5× | Short options, DTE ≤ 10 | monitor/continuous.py:157 | Buy back when option price rises to 1.5× entry (weekly puts/calls) |
| `underlying_crash_stop_pct` | 15% | Underlying price drop | CLAUDE.md | Circuit breaker: close all weekly puts if stock drops 15%+ intraday |

---

## 2. Defensive-Roll Thresholds

| Regime | Loss Multiple Trigger | DTE Requirement | Special Rules | Source |
|--------|----------------------|-----------------|----------------|--------|
| **NORMAL** | 1.40× (40% loss) | ≥21 DTE | Trigger only if loss_multiple ≥ 1.40 OR (delta expanded past 0.35 AND loss_multiple ≥ 1.20) | position_review.py:573 |
| **HIGH CAUTION** | 1.30× (30% loss) | ≥21 DTE | Lower threshold during "pullback risk high" / macro_caution=high | position_review.py:574–575 |
| **Block Roll** | — | <21 DTE | Defensive rolls are blocked under 21 DTE; close instead | position_review.py:567 |
| **Tail-Risk Override** | — | Any DTE | Chinese ADRs / binary biotechs / crypto / memes: override roll to CLOSE NOW; rolling doesn't help against headline gaps | position_review.py:585–595 |

---

## 3. Take-Profit Thresholds (by Delta × DTE Bucket)

Close short options when P&L reaches these capture percentages. Scale depends on moneyness and time remaining.

| Delta Band | DTE > 120 | 60 < DTE ≤ 120 | DTE ≤ 60 | Context | Source |
|------------|-----------|-----------------|---------|---------|--------|
| **Deep OTM** (delta < 0.10) | 50% | 65% | 80% | Theta accelerates short-dated; long-dated positions decay slowly. Deep OTM + 120+ DTE → close at 50% and redeploy | position_review.py:613–619 |
| **Moderate OTM** (0.10–0.25 delta) | 50% | 50% | 50% | Standard wheel sweet spot | position_review.py:623 |
| **Near ATM** (delta > 0.25) | 40% | 40% | 40% | Assignment risk rising; take profits sooner | position_review.py:621 |

**Additional take-profit triggers:**
- **High IV + short-dated:** If IV rank > 60 AND DTE ≤ 45 AND pnl_pct ≥ 50% → close to lock gains (position_review.py:676–688)
- **Time decay only:** If DTE ≤ 21 AND pnl_pct ≥ 30% → close (gamma risk rising) (position_review.py:661–669)
- **High-volatility earnings movers:** If symbol in HIGH_VOL_EARNINGS_MOVERS AND earnings_before_expiry AND pnl_pct ≥ 50% → close before report (position_review.py:693–703)

**Squeeze logic (GTC limit orders):**
- Eligible when: DTE > 14 AND days_to_next_earnings > 30 AND 50% ≤ pnl_pct < 75%
- GTC limit target: position.entry_price × 0.25 (≈75% capture target)
- Hard close by 14 DTE if not filled
- (position_review.py:639–654)

---

## 4. Roll-Target Selection

### Put Roll Targets

| Parameter | Value | Context | Source |
|-----------|-------|---------|--------|
| **Delta target (normal IV)** | 0.22 | Sweet spot for wheel puts; aim for this delta | position_review.py:26 |
| **Delta max (normal IV)** | 0.30 | Never roll to delta above this; too close to ATM | position_review.py:27 |
| **Delta target (high IV)** | 0.16 | When IV rank > 60, go further OTM to reduce assignment risk | position_review.py:28 |
| **Delta max (high IV)** | 0.22 | Upper bound in high IV environment | position_review.py:29 |
| **High IV threshold** | 60 | IV rank ≥ 60 triggers the "high IV" delta targets | position_review.py:30 |
| **Max risk/reward ratio** | 3.0 | Block rolls where 10% drop loss > 3× premium collected | position_review.py:31 |
| **Min net credit** | $0.25/contract | Economics gate: if net debit > new premium, close instead of roll | position_review.py:242–247 |
| **Never roll up** | — | Puts: max_strike = current position's strike (roll down-and-out only) | position_review.py:221–225 |

### Call Roll Targets

| Parameter | Value | Context | Source |
|-----------|-------|---------|--------|
| **Delta target (normal IV)** | 0.25 | Covered call sweet spot | position_review.py:437 |
| **Delta target (high IV)** | 0.18 | When IV rank > 60, go further OTM | position_review.py:437 |
| **Delta max** | 0.35 | Never roll to delta above this | position_review.py:438 |
| **Never roll down** | — | Calls: min_strike = current position's strike (roll up-and-out only) | position_review.py:289–292 |

### Expiration Logic

| Rule | Details | Source |
|------|---------|--------|
| **Default roll target** | 30 days from today OR 1 day after current position expires (whichever is later) | position_review.py:191–193 |
| **Earnings in window** | If earnings fall within the target roll window, push target to 30 days AFTER earnings | position_review.py:198–204 |
| **Snap to real expiration** | Proposed date is snapped to nearest real options expiration (chain.expirations or Friday) | position_review.py:206–207 |
| **Minimum DTE for roll** | Must be AFTER current position's expiration (rolled position can never be the same date or earlier) | position_review.py:211–216 |

---

## 5. Earnings-Guard Windows

| Guard Type | Rule | Applies To | Source |
|------------|------|-----------|--------|
| **CLOSE NOW (short-dated)** | If earnings ≤ expiration AND DTE ≤ 30 → CLOSE | Short puts/calls (covered calls treated as WATCH CLOSELY) | position_review.py:545–561 |
| **CLOSE NOW (imminent + threatened)** | If 0 ≤ days_to_earnings ≤ 21 AND pnl_pct < 30% AND position is meaningful (not deep OTM) → CLOSE | Short puts/calls (covered calls → WATCH CLOSELY) | position_review.py:539–544 |
| **Roll target override** | If earnings in target roll window, snap target to 30 days AFTER earnings | All positions being rolled | position_review.py:202–204 |
| **High-vol movers early close** | If symbol in HIGH_VOL_EARNINGS_MOVERS AND earnings_before_expiry AND pnl_pct ≥ 50% → TAKE PROFIT before report | Short puts (covers TSLA, NVDA, META, NFLX, etc.) | position_review.py:693–703 |

**List of high-volatility earnings movers** (expanded closure recommended):
TSLA, NVDA, META, NFLX, SNAP, ROKU, SHOP, COIN, PLTR, AFRM, UPST, MARA, RIOT, SMCI, ARM, MU, SOFI, HOOD, RBLX, PINS, TTD, CRWD, DDOG, NET

---

## 6. IV Regime Thresholds

| Threshold | Value | Usage | Source |
|-----------|-------|-------|--------|
| **High IV** | IV rank ≥ 60 | Triggers narrower put delta targets (0.16 target, 0.22 max) and early take-profit rules | position_review.py:30, 676–688 |
| **Low IV** | IV rank < 30 | Flag: "premium dried up, not worth opening new positions but existing can ride" | position_review.py:824 |
| **Min IV for entry** | IV rank ≥ 25 | Minimum acceptable IV rank to recommend new put sales | config/trading_params.yaml: min_iv_rank |
| **Preferred IV for entry** | IV rank ≥ 50 | Sweet spot for signal confirmation and sizing | config/trading_params.yaml: preferred_iv_rank |
| **IV override on dips** | True | Allow put sales at IV rank < 25 if a strong dip signal fires | config/trading_params.yaml: iv_rank_override_on_dip |

---

## 7. Concentration & Sizing

### Maximum Position Sizes

| Limit | Value | Applied By | Source |
|-------|-------|-----------|--------|
| **Max per single name** | 10% NLV | Pre-sizing + post-sizing check | CLAUDE.md, src/risk/routing.py |
| **Max per sector** | 35% NLV | Portfolio allocation rules | config/trading_params.yaml: max_concentration_per_sector |
| **ADBE special case** | Target 20% NLV (max) | Employer concentration; quarterly sell plan to < 15% | CLAUDE.md |

### Conviction-Based Sizing

| Conviction Level | % of NLV | Signal Count | IV Rank Requirement | Source |
|------------------|----------|--------------|---------------------|--------|
| **HIGH** | 3–5% | ≥3 signals | IV rank > 60 | config/trading_params.yaml |
| **MEDIUM** | 1.5–3% | ≥2 signals | IV rank > 45 | config/trading_params.yaml |
| **LOW** | 0.5–1.5% | ≥1 signal | IV rank > 30 | config/trading_params.yaml |

### Other Sizing Rules

| Rule | Value | Source |
|------|-------|--------|
| **Margin cutback** | If margin utilization > 40%, scale down new trades | config/trading_params.yaml |
| **Max new trades/day** | 5 | config/trading_params.yaml |
| **Idle capital target** | 10% NLV | config/trading_params.yaml |
| **Minimum cash reserve** | 3% NLV | config/trading_params.yaml |
| **Max margin** | 55% of NLV | config/trading_params.yaml |

---

## 8. Strategy-Specific Parameters

### 8.1 Monthly Put (Cash-Secured Put)

| Parameter | Value | Rationale | Source |
|-----------|-------|-----------|--------|
| **Target DTE** | 30 | Sweet spot; decay accelerates late, assignment risk manageable | config/trading_params.yaml: sweet_spot_dte |
| **DTE window** | 21–45 | Minimum 3 weeks for theta decay, max 45 to avoid stale premium | config/trading_params.yaml |
| **Target delta** | −0.30 | Wheel default | config/trading_params.yaml |
| **Delta range** | −0.40 to −0.15 | Acceptable bounds; pick within based on IV environment | config/trading_params.yaml |
| **Loss stop** | 2.0× entry | Close if option price rises to 2× what you sold it for | monitor/continuous.py:156 |
| **Take-profit threshold** | 50% (standard) | Close when profit ≥ 50% of premium collected | position_review.py:623 |
| **Min yield per trade** | 1.5% | Minimum annualized return on collateral | config/trading_params.yaml |
| **Min IV for entry** | Rank 25–50 | Can override down to 25 on dip signals | config/trading_params.yaml |

### 8.2 Weekly Put (Dip-Driven, High-Decay)

| Parameter | Value | Rationale | Source |
|-----------|-------|-----------|--------|
| **Target DTE** | 7–10 | Dip-triggered, fast decay; assignment risk if stock rallies into ATM | config/trading_params.yaml / CLAUDE.md |
| **Target delta** | −0.20 (tighter than monthlies) | Further OTM to reduce assignment risk on quick reversals | CLAUDE.md |
| **Loss stop** | 1.5× entry | Tighter stop than monthlies due to higher gamma | monitor/continuous.py:157 |
| **Dip requirement** | Intraday drop ≥ −2.5% OR 3+ red days, ≥ −5% total | Only sell weeklies on confirmed dips, not on normal drift | config/trading_params.yaml |
| **Take-profit threshold** | 80% (deep OTM, short DTE) | Let theta run; close only if 80% captured | position_review.py:619 |
| **Block on earnings week** | Mandatory | Never sell weeklies if earnings fall within the DTE window | position_review.py:545–561 |
| **Intraday dip threshold** | −2.5% | Trigger for intraday_dip signal | config/trading_params.yaml |

### 8.3 Strangle (Short Put + Short Call)

| Parameter | Value | Rationale | Source |
|-----------|-------|-----------|--------|
| **Target DTE** | 30–45 | Standard wheel DTE to allow both legs to decay | config/trading_params.yaml |
| **Put delta** | −0.15 to −0.20 | Wide width; aim for 0.15 delta puts | CLAUDE.md |
| **Call delta** | 0.15 to 0.20 | Symmetric, defined risk both sides | CLAUDE.md |
| **Net credit minimum** | $0.25/contract | Minimum total premium to make it worthwhile | config/trading_params.yaml |
| **Sizing** | 3–5% of NLV (HIGH conviction only) | Wider exposure; only for high-confidence setups | config/trading_params.yaml |
| **Account requirement** | Options Level 4 minimum | Strangles are Level 3–4 strategies | CLAUDE.md |

### 8.4 Earnings Crush (Pre-Earnings Short Sell)

| Parameter | Value | Rationale | Source |
|-----------|-------|-----------|--------|
| **Entry window** | 1–3 days before earnings | Capture IV crush, exit before binary event | config/trading_params.yaml |
| **Min IV rank** | 65 | Only in high-IV environments; crush must be profitable | config/trading_params.yaml |
| **Target DTE** | 1–3 DTE | Sell and exit fast; gamma + IV crush = fast decay | config/trading_params.yaml |
| **Exit rule** | Close before earnings report closes; never hold through | Strict: avoid binary gap risk | position_review.py:545–561 |
| **Eligible symbols** | All, but prioritize HIGH_VOL_EARNINGS_MOVERS | High-vol names (TSLA, NVDA, META, etc.) deliver best risk/reward | position_review.py:57–61 |

### 8.5 Put Spread (Vertical Spread)

| Parameter | Value | Rationale | Source |
|-----------|-------|-----------|--------|
| **Spread width** | 0.25 delta / 0.15 delta | Defined risk; short 0.25, long 0.15 delta | CLAUDE.md |
| **Target DTE** | 30–45 | Standard wheel window | config/trading_params.yaml |
| **Max loss** | Difference between strikes × 100 | Capped risk; no assignment surprise | CLAUDE.md |
| **Account requirement** | Options Level 3+ | Spreads require Level 3 | CLAUDE.md |
| **Sizing** | 1.5–3% of NLV (MEDIUM conviction) | Lower than short puts due to defined risk | config/trading_params.yaml |

### 8.6 Dividend Capture (Pre-Ex-Dividend Put Sale)

| Parameter | Value | Rationale | Source |
|-----------|-------|-----------|--------|
| **Timing** | 2–5 days before ex-dividend date | Dividend yield is trapped; expires worthless after ex-date | CLAUDE.md |
| **Target delta** | −0.30 | Standard wheel delta; dividend de-risks slightly | config/trading_params.yaml |
| **Target DTE** | 7–14 DTE | Short window; fast decay post ex-div | CLAUDE.md |
| **Exit** | Hold through ex-dividend or close at 50% profit | Dividend provides cushion; theta accelerates | CLAUDE.md |
| **Block if:** | Next earnings ≤ ex-div date + 3 days | Avoid earnings surprise right after dividend | position_review.py:545–561 |

---

## 9. Roll Type Classification

The system classifies rolls based on strike and expiration movement:

| Roll Type | Strike Movement | Expiration Movement | Use Case |
|-----------|-----------------|-------------------|----------|
| **"out"** | Same strike | Later date | Neutral outlook; extend for more decay |
| **"down_and_out"** | Lower strike (put) | Later date | Defensive; reduce assignment risk after underwater move |
| **"up_and_out"** | Higher strike (put) or higher (call) | Later date | Bullish (puts) or capture more premium (calls) |

---

## 10. Stress Test & Risk Assessment

### Roll Risk Metrics

| Metric | Calculation | Block Condition | Source |
|--------|-----------|-----------------|--------|
| **Loss at 10% drop** | max(0, strike - (price × 0.90)) - premium | If > 3× premium (risk/reward > 3.0) → block roll | position_review.py:250–266 |
| **Loss at 20% drop** | max(0, strike - (price × 0.80)) - premium | Informational; surface as warning | position_review.py:250–266 |
| **Risk/reward ratio** | loss_at_10pct_drop / (premium × 100) | Block if > 3.0 (default threshold) | position_review.py:31, 265–274 |
| **Delta expansion alert** | If abs(delta) > 0.35 → gamma is hurting | Triggers DEFENSIVE ROLL at lower loss_multiple (1.20 vs 1.40) | position_review.py:576–577 |

---

## 9. Liquidity Gates (Roll-Target Selection)

**Heads up — these are NOT in wheelhouz source.** wheelhouz filters chains on `bid > 0` only; no explicit OI or spread guards. The values below are sensible defaults added at the start of v0 — production trading on illiquid contracts is a known footgun, and a 5% spread on a thin-OI strike will cost more in slippage than the matrix calculates. Tune after first month of live runs.

| Parameter | Default | Source | Semantic |
|-----------|---------|--------|----------|
| `min_open_interest` | 100 | v0 default (not in wheelhouz) | Reject roll candidates with OI < 100; thin OI = wide bid-ask + assignment risk |
| `max_spread_pct` | 0.05 (5% of mid) | v0 default (not in wheelhouz) | Reject roll candidates with spread > 5% of mid; closing later is too expensive |
| `min_volume_today` | 0 | v0 default | Volume gate disabled by default; some otherwise-good rolls land on quiet days |

---

## 10. Stress-Test Parameters (Post-Matrix Guardrail)

| Parameter | Value | Source | Semantic |
|-----------|-------|--------|----------|
| `stress_test_drop_scenarios` | [0.10, 0.20, 0.30] | stress_coverage.py:125; position_review.py:9, 151 | Three SPY drops simulated against any proposed roll; matrix uses -10%/-20% earlier doc said only two — wheelhouz uses three |
| `roll_max_risk_reward_ratio` | 3.0 | position_review.py:31 (`_MAX_RISK_REWARD`) | Reject roll candidates where loss at -10% drop exceeds 3.0× expected premium |
| `crisis_correlation_tech_semi` | 0.95 | stress_coverage.py:19-22; CLAUDE.md:299 | Tech/semi names assumed to move 95% with SPY in stress scenarios; used by stress test when VIX > 30 |

---

## 11. Net-Credit Gates (Roll Target Acceptance)

**Same caveat as section 9 — wheelhouz does NOT enforce a minimum net-credit-as-pct-of-original-premium gate.** It uses absolute net credit ≥ $0.25 per contract and per-strategy capture thresholds (50/75%) elsewhere. The "min 10% of original premium" gate referenced by the matrix is a v0 design addition; a roll that pays less than 10% of what you originally collected is rarely worth the transaction cost. Tune as needed.

| Parameter | Default | Source | Semantic |
|-----------|---------|--------|----------|
| `min_roll_net_credit_pct_of_original` | 0.10 | v0 default (matrix design) | Reject roll if net credit < 10% of original premium; small credit = poor risk/reward |
| `min_net_credit_per_contract` | 0.25 | trading_params.yaml:36 | Hard minimum: $0.25 per contract on any roll regardless of % calc |
| `roll_squeeze_debit_target_pct` | 0.25 | position_review.py:649 | For GTC squeeze: debit target = 25% of entry price (≈75% capture target) |

---

## 12. Regime Thresholds (VIX & SPY)

These feed into the regime classifier (see `09-regime-framework.md`) but are stored here because the wheel matrix consults regime as a state variable.

### VIX bands (regime.py:15-17)

| Parameter | Value | Regime When VIX < Value |
|-----------|-------|------------------------|
| `regime_attack_vix_max` | 18.0 | RISK_ON / ATTACK (most aggressive) |
| `regime_hold_vix_max` | 25.0 | NORMAL / HOLD |
| `regime_defend_vix_max` | 35.0 | CAUTION / DEFEND |
| (above 35.0) | — | RISK_OFF / CRISIS |

### SPY daily-drop severity bands (regime.py:21-24)

| Parameter | Value | Severity |
|-----------|-------|----------|
| `regime_elevated_spy_drop` | -0.02 (-2%) | Elevated — heightened watch |
| `regime_severe_spy_drop` | -0.03 (-3%) | Severe — defensive recommendations |
| `regime_crisis_spy_drop` | -0.05 (-5%) | Crisis — close all weeklies, halt new entries |
| `regime_extreme_spy_drop` | -0.08 (-8%) | Extreme — immediate CRISIS regime override |

---

## Machine-Readable Companion (YAML)

```yaml
# Wheel Roll Advisor — Parameters Export
# Generated from wheelhouz codebase, 2026-05-07

loss_stops:
  monthly_put_max_loss_multiplier: 2.0
  weekly_put_max_loss_multiplier: 1.5
  weekly_put_dte_threshold: 10
  underlying_crash_stop_pct: 0.15

defensive_roll:
  normal_loss_trigger_multiplier: 1.40
  high_caution_loss_trigger_multiplier: 1.30
  min_dte_for_roll: 21
  delta_expansion_threshold: 0.35
  delta_expansion_loss_trigger: 1.20
  tail_risk_override_to_close_now: true

take_profit:
  deep_otm_dte_120_plus: 0.50
  deep_otm_dte_60_120: 0.65
  deep_otm_dte_under_60: 0.80
  moderate_otm_standard: 0.50
  near_atm_threshold: 0.40
  high_iv_early_close_iv_rank_min: 60
  high_iv_early_close_dte_max: 45
  time_decay_only_dte_max: 21
  time_decay_only_capture_min: 0.30
  squeeze_dte_min: 14
  squeeze_days_to_earnings_min: 30
  squeeze_capture_band_min: 0.50
  squeeze_capture_band_max: 0.75

roll_targets_put:
  delta_target_normal_iv: 0.22
  delta_max_normal_iv: 0.30
  delta_target_high_iv: 0.16
  delta_max_high_iv: 0.22
  high_iv_threshold: 60
  max_risk_reward_ratio: 3.0
  min_net_credit_per_contract: 0.25
  default_roll_target_days: 30

roll_targets_call:
  delta_target_normal_iv: 0.25
  delta_target_high_iv: 0.18
  delta_max: 0.35

liquidity_gates:                # v0 defaults — not in wheelhouz; tune after first month
  min_open_interest: 100
  max_spread_pct: 0.05
  min_volume_today: 0

stress_test:
  drop_scenarios: [0.10, 0.20, 0.30]
  roll_max_risk_reward_ratio: 3.0
  crisis_correlation_tech_semi: 0.95
  crisis_correlation_vix_threshold: 30

net_credit_gates:               # v0 defaults — see section 11
  min_roll_net_credit_pct_of_original: 0.10
  min_net_credit_per_contract: 0.25
  roll_squeeze_debit_target_pct: 0.25

regime:
  attack_vix_max: 18.0
  hold_vix_max: 25.0
  defend_vix_max: 35.0
  elevated_spy_drop: -0.02
  severe_spy_drop: -0.03
  crisis_spy_drop: -0.05
  extreme_spy_drop: -0.08

earnings_guards:
  close_now_dte_max: 30
  imminent_threatened_days_to_earnings_max: 21
  imminent_threatened_pnl_pct_min: -0.02
  imminent_threatened_delta_min: 0.15
  roll_target_offset_days_after_earnings: 30
  high_vol_movers_take_profit_threshold: 0.50

iv_thresholds:
  high_iv_threshold: 60
  low_iv_threshold: 30
  min_iv_rank_for_entry: 25
  preferred_iv_rank: 50
  iv_rank_override_on_dip: true
  earnings_crush_min_iv_rank: 65

concentration:
  max_per_single_name_pct: 0.10
  max_per_sector_pct: 0.35
  adbe_target_max_pct: 0.20

sizing:
  high_conviction_pct: 0.04
  medium_conviction_pct: 0.02
  low_conviction_pct: 0.01
  high_conviction_min_signals: 3
  medium_conviction_min_signals: 2
  high_conviction_iv_threshold: 60
  medium_conviction_iv_threshold: 45
  low_conviction_iv_threshold: 30
  margin_cutback_threshold: 0.40
  max_new_trades_per_day: 5
  idle_capital_target: 0.10
  min_cash_reserve: 0.03
  max_margin_utilization: 0.55

strategies:
  monthly_put:
    target_dte: 30
    dte_min: 21
    dte_max: 45
    target_delta: -0.30
    delta_min: -0.40
    delta_max: -0.15
    loss_stop_multiplier: 2.0
    take_profit_threshold: 0.50
    min_yield_pct: 0.015
  weekly_put:
    target_dte: 7
    dte_max: 10
    intraday_dip_threshold: -0.025
    multi_day_pullback_days: 3
    multi_day_pullback_pct: 0.05
    loss_stop_multiplier: 1.5
    take_profit_threshold: 0.80
  strangle:
    target_dte: 30
    dte_max: 45
    put_delta_target: -0.18
    call_delta_target: 0.18
    min_net_credit: 0.25
    sizing_tier: high_conviction
    options_level_required: 4
  earnings_crush:
    days_before_earnings_min: 1
    days_before_earnings_max: 3
    min_iv_rank: 65
    target_dte_max: 3
  put_spread:
    target_dte: 30
    dte_max: 45
    short_delta: 0.25
    long_delta: 0.15
    sizing_tier: medium_conviction
    options_level_required: 3
  dividend_capture:
    days_before_exdiv_min: 2
    days_before_exdiv_max: 5
    target_delta: -0.30
    target_dte_max: 14

high_vol_earnings_movers:
  - TSLA
  - NVDA
  - META
  - NFLX
  - SNAP
  - ROKU
  - SHOP
  - COIN
  - PLTR
  - AFRM
  - UPST
  - MARA
  - RIOT
  - SMCI
  - ARM
  - MU
  - SOFI
  - HOOD
  - RBLX
  - PINS
  - TTD
  - CRWD
  - DDOG
  - NET
```

---

## Provenance

Every parameter cited above is extracted directly from wheelhouz source code:

- **Core thresholds:** `src/intelligence/position_review.py` (lines 26–32, 467–850)
- **Loss stops & monitoring:** `src/monitor/continuous.py` (lines 156–160)
- **Trading parameters:** `config/trading_params.yaml`
- **Tail-risk handling:** `src/risk/tail_risk.py`
- **Architecture & domain rules:** `CLAUDE.md` (wheelhouz project instructions)

When the skill extends this system, additions must be:
1. Documented in this file with full source citations
2. Tested against existing rules (no conflicts with priority order)
3. Committed to the YAML companion for data-driven re-evaluation

---

## Notes for Skill Developers

- Parameters are **not** hardcoded in skill logic. Load the YAML at startup.
- When a rule conflicts (e.g., two take-profit thresholds apply), the **priority order** in CLAUDE.md applies: CLOSE NOW > DEFENSIVE ROLL > TAKE PROFIT > WATCH CLOSELY > HOLD.
- Delta is always unsigned internally; negate for puts (−delta) in display.
- All dates must render with weekday + year: `Fri May 29 '26` not `May 29` or `5/29`.
- Expiration validation is mandatory: proposed strikes + expirations must snap to real chain data before display.
