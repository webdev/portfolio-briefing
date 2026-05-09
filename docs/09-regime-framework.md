# Regime Framework

**Status:** Draft v0.1
**Date:** 2026-05-07
**Used by:** `daily-portfolio-briefing` Step 3
**Companion:** `06-wheel-parameters.md` section 12 (VIX/SPY thresholds)

This document is the deterministic classifier that maps market inputs (calendar events, breadth state, price action, volatility) to one of four regime labels. The same inputs always produce the same label. Each downstream skill (wheel-roll-advisor, equity-decision-matrix, screeners) consults this regime label to apply conditional logic: in RISK_OFF, new long ideas are suppressed; in CAUTION, defensive-roll thresholds tighten. The briefing runs this classifier every day and persists both the label and the inputs that derived it, enabling reproducible day-over-day comparisons.

---

## Regime Labels

Four distinct labels, each with semantic and typical triggering context:

- **RISK_ON** — Broad bull market, low VIX, expanding breadth, no near-term catalysts. Aggressive sizing enabled, full strategy menu. Most of 2024, pre-election years typically.
- **NORMAL** — Typical market conditions, neutral breadth, no extremes in calendar or events. Standard sizing, baseline rules apply. Default state most trading days.
- **CAUTION** — Heightened watch, multiple warning flags but no crisis. VIX elevated, breadth weakening, or event cluster approaching. New long sizing reduced, defensive rolls tighten, covered calls stay actionable. Pre-FOMC weeks, post-earnings volatility, sector rotation stress.
- **RISK_OFF** — Defensive posture, high VIX or major drawdown, or multiple red flags compounding. New long ideas suppressed. Existing positions evaluated for trim/close. Hedges added. Circuit breaker actuation conditions checked. October 2018, March 2020, August 2011.

---

## Inputs

Every input consumed by the classifier, with source, type, and meaning:

| Input | Source | Type | Range / Values | Meaning |
|-------|--------|------|--------|---------|
| **VIX last close** | Market data (yfinance) | float | 10–80+ | Implied volatility; drives base regime. Lower = calmer. |
| **VIX 1-day change** | Market data | float | -5 to +15 | Intraday VIX shock; rapid increases signal regime escalation. |
| **SPY day change** | Market data | float | -8% to +4% | Single-day S&P 500 move; large drops override VIX-only classification. |
| **SPY 5-day cumulative** | Market data | float | -15% to +8% | Week-long drawdown; identifies sustained stress vs noise. |
| **Breadth state** | breadth-chart-analyst (CSV) | enum: STRONG / NEUTRAL / WEAK | Inferred from 8MA/200MA position and dead-cross status | Market health indicator; WEAK with elevated VIX = dual confirmation. |
| **Distribution-day count (25 sessions)** | IBD distribution-day monitor | int | 0–15 | Number of closed-on-lower-volume days in rolling 25-session window; 5+ = weakness, 7+ = crisis. |
| **Economic events high-impact** | economic-calendar-fetcher | int | 0–10 | Count of FOMC/CPI/Jobs today; clustering = event risk. |
| **Earnings cluster intensity** | earnings-calendar | int | 0–50 | Count of large-cap earnings reports today; 5+ during earnings season = event risk. |
| **Market news intensity** (last 18h) | market-news-analyst | int | 0–20 | High-impact stories in past 18 hours; 5+ = narrative shift risk. |
| **Yesterday regime** | Prior day snapshot | enum | RISK_ON / NORMAL / CAUTION / RISK_OFF | Anchor for stickiness logic; prevents regime whipsaw. |

All inputs must be fetched fresh at briefing time. Missing data:
- **Breadth unavailable** → mark confidence as LOW; proceed with VIX/SPY rules only.
- **Economic calendar API down** → assume no high-impact events; mark confidence MEDIUM.
- **VIX missing** → abort classification and return DATA_ERROR; cannot classify without volatility data.

---

## Classification Rules

Eleven rules evaluated in priority order. The first rule that matches wins. All numeric thresholds reference `06-wheel-parameters.md` section 12 by name, not inline numbers.

### Rule 1: VIX Extreme High (Immediate Crisis)

**Condition:** `vix_last > regime_defend_vix_max`

**Target:** RISK_OFF

**Rationale:** VIX above 35 signals elevated tail risk, drawdown probability, or active crisis. Defensive rules apply immediately.

**Example:** VIX 37.5 after a Fed surprise announcement → RISK_OFF.

---

### Rule 2: SPY Extreme Drop (Circuit Breaker)

**Condition:** `spy_day_change <= regime_extreme_spy_drop`

**Target:** RISK_OFF (overrides all other rules)

**Rationale:** A -8% or worse single-day crash is a circuit-breaker event. Regime shifts to crisis regardless of VIX or other context.

**Example:** SPY -8.2% on a geopolitical shock → RISK_OFF (even if VIX only 32).

---

### Rule 3: SPY Crisis Drop + Broad Weakness

**Condition:** `spy_day_change <= regime_crisis_spy_drop AND breadth_state == WEAK`

**Target:** RISK_OFF

**Rationale:** -5% to -8% drop on weak breadth (dual confirmation) = sustained stress, not single-stock shock.

**Example:** SPY -5.2%, 8MA below 200MA with death cross visible → RISK_OFF.

---

### Rule 4: VIX Rapid Spike Intraday

**Condition:** `vix_change >= 0.25 AND vix_last > regime_hold_vix_max`

**Target:** CAUTION (or RISK_OFF if VIX ends above defend_vix_max)

**Rationale:** Sudden 25%+ intraday VIX jump (e.g., VIX 18 → 22.5 in 2 hours) signals surprise event or volatility cluster forming.

**Example:** Fed rate decision day, market rallies, but VIX jumps +5 points → CAUTION.

---

### Rule 5: SPY Severe Drop on Weak Breadth

**Condition:** `spy_day_change <= regime_severe_spy_drop AND breadth_state == WEAK`

**Target:** CAUTION

**Rationale:** -3% drop + deteriorating breadth suggests correction risk, but not yet crisis. New longs sized down.

**Example:** SPY -3.1%, breadth rolls over to WEAK, but VIX only 28 → CAUTION.

---

### Rule 6: Breadth Deterioration (Dead Cross)

**Condition:** `breadth_state == WEAK AND distribution_day_count >= 5`

**Target:** CAUTION

**Rationale:** 5+ distribution days in 25 sessions + WEAK breadth state = institutional distribution, reversal risk.

**Example:** 8MA crosses below 200MA on breadth chart, 6 distribution days accumulated → CAUTION (even if SPY flat).

---

### Rule 7: Distribution-Day Extreme

**Condition:** `distribution_day_count >= 7`

**Target:** RISK_OFF

**Rationale:** 7+ distribution days is IBD's "follow-through day failure" condition — heavy institutional selling, rally is breaking down.

**Example:** Week-long slide with 7 down days on volume → RISK_OFF.

---

### Rule 8: Economic Event Cluster

**Condition:** `economic_events_high_impact >= 2 AND vix_last > regime_hold_vix_max`

**Target:** CAUTION

**Rationale:** Two high-impact events on same day (e.g., FOMC + earnings announcements) + elevated VIX = event-driven volatility risk.

**Example:** FOMC decision + CPI print same day, VIX 26 → CAUTION.

---

### Rule 9: Earnings Cluster + Weak Breadth

**Condition:** `earnings_cluster_intensity >= 5 AND breadth_state == WEAK`

**Target:** CAUTION

**Rationale:** Heavy earnings season + deteriorating breadth = divergent performance, repricing risk.

**Example:** 7 mega-cap earnings reports scheduled for this week, breadth already turning down → CAUTION.

---

### Rule 10: News Intensity Spike

**Condition:** `market_news_intensity >= 5 AND vix_last >= regime_hold_vix_max`

**Target:** CAUTION

**Rationale:** Multiple high-impact news items + elevated VIX suggests narrative rotation or sector stress (not broad crisis yet, but flag it).

**Example:** Three geopolitical headlines + two earnings shocks in 18h, VIX 25 → CAUTION.

---

### Rule 11: Default

**Condition:** No rule fires

**Target:** NORMAL

**Rationale:** No warning signals detected. Market in baseline state.

---

## Regime Stickiness (Anti-Flipping)

Regimes are "sticky" to prevent whipsaw recommendations. Stepping DOWN (toward less defensive) is gated by prior-day state; stepping UP (toward more defensive) is immediate.

### Down-Stepping Gates

| From | To | Condition |
|------|----|----|
| RISK_OFF | CAUTION | Requires 2 consecutive NORMAL-like sessions (VIX < 28, SPY +0.5%, breadth NEUTRAL+) |
| CAUTION | NORMAL | Requires 1 normal session (VIX < 25, SPY flat ±2%, breadth STRONG or NEUTRAL) |
| NORMAL | RISK_ON | Rare; requires VIX < 18 AND breadth STRONG AND no event clusters for 3+ sessions |

### Up-Stepping

Immediately transitions without gates (SPY crash or breadth collapse triggers CAUTION/RISK_OFF instantly).

### Application

After raw classification, check `yesterday_regime`:
- If raw rule output is "less defensive" than yesterday, apply the down-stepping gate.
- If not met, hold yesterday's regime; log "stickiness held yesterday's regime."
- If raw output is "more defensive," apply immediately.

**Example:**
- Yesterday: NORMAL
- Today's SPY: -2.1%, VIX 26, breadth WEAK
- Rule 5 fires: CAUTION
- Stickiness: No gate (up-stepping), apply CAUTION immediately.

---

## Confidence Scoring

The classifier returns not just the regime label, but a confidence level reflecting data completeness.

| Confidence | Data State | Implications |
|---|---|---|
| **HIGH** | All inputs available, no missing APIs, clear rule match | Regime recommendation applies with full weight |
| **MEDIUM** | One optional input unavailable (breadth, news) OR marginal rule match | Regime recommendation applies; downstream skills may add buffer |
| **LOW** | Multiple inputs unavailable OR rule match is boundary case | Flag to user; downstream skills reduce new position sizing by 20-30% |
| **DATA_ERROR** | VIX unavailable OR multiple critical inputs missing | Abort briefing; request re-run when data available |

---

## Output

The classifier returns a structured object persisted to `state/briefing_snapshots/YYYY-MM-DD/regime.json`:

```json
{
  "regime": "CAUTION",
  "confidence": "HIGH",
  "inputs_at_evaluation": {
    "vix_last": 26.3,
    "vix_change": 0.12,
    "spy_day_change": -0.021,
    "spy_5d_cumulative": -0.038,
    "breadth_state": "WEAK",
    "distribution_day_count": 5,
    "economic_events_high_impact": 0,
    "earnings_cluster_intensity": 2,
    "market_news_intensity": 3,
    "yesterday_regime": "NORMAL"
  },
  "triggered_rules": [
    {
      "rule_id": "BREADTH_DETERIORATION",
      "priority": 6,
      "rationale": "breadth_state=WEAK AND distribution_day_count (5) >= 5 threshold"
    },
    {
      "rule_id": "SPY_SEVERE_DROP_WEAK_BREADTH",
      "priority": 5,
      "rationale": "spy_day_change (-2.1%) <= regime_severe_spy_drop (-3%): FALSE, rule did not match"
    }
  ],
  "stickiness_applied": false,
  "sticky_hold_reason": null,
  "evaluation_time": "2026-05-07T14:30:00Z",
  "valid": true
}
```

---

## Behavior Implications by Regime

Downstream skills apply regime-conditional logic. This table is the **contract** — every skill reads `regime.json` and enforces its column:

| Regime | wheel-roll-advisor | New equity ideas | New short puts | Hedge book | Concentration check | Sizing tier cap |
|---|---|---|---|---|---|---|
| **RISK_ON** | Normal roll params | Enabled, all sizes | Enabled | Passive (0–10% delta hedge) | 10% per name, 35% per sector | No cap |
| **NORMAL** | Normal roll params | Enabled, all sizes | Enabled | Passive (0–10%) | 10% per name, 35% per sector | No cap |
| **CAUTION** | Defensive roll @ 1.30× loss trigger, tighter DTE | HIGH sizing suppressed; MEDIUM/LOW OK | Enabled | Active hedge add (target 15–20% delta coverage) | 8% per name, 30% per sector | MEDIUM max (3% NLV) |
| **RISK_OFF** | Defensive roll @ 1.20× loss trigger OR close-only | **Suppressed entirely** | Enabled (but see tail-risk gate) | Mandatory hedge (target 25–35% delta coverage) | 6% per name, 25% per sector | LOW only (1.5% NLV) |

**Notes:**
- "Defensive roll @ X×" means the loss-multiple threshold from `06-wheel-parameters.md` is tightened to X instead of the base 1.40×.
- "Sizing tier" references conviction levels from `06-wheel-parameters.md` section 7.
- Concentrate check applies AFTER position-sizer runs; both names must fit.
- Tail-risk names (China ADRs, binary biotech, etc.) are never eligible for new shorts regardless of regime, but existing positions surface extra warnings in RISK_OFF.

---

## Missing or Stale Data Handling

### Breadth Chart Unavailable

**Action:** Skip Rule 6 and Rule 9. Proceed with VIX/SPY/calendar/news rules only.

**Confidence:** Reduce from HIGH to MEDIUM (one data source missing).

**Rationale:** Breadth provides confirmation but is not essential; VIX and SPY drops alone can classify regime.

### Economic Calendar API Down

**Action:** Assume `economic_events_high_impact = 0`. Skip Rule 8.

**Confidence:** Reduce from HIGH to MEDIUM if event risk was near-miss (Rule 8 boundary case).

**Rationale:** Risk of missing a cluster exists, but VIX elevation would catch it.

### News Intensity Unavailable

**Action:** Skip Rule 10. Proceed with other rules.

**Confidence:** Reduce from HIGH to MEDIUM.

**Rationale:** News spikes are supplementary; breadth and VIX drive classification.

### Distribution-Day Count Unavailable

**Action:** Skip Rules 6 and 7. Breadth WEAK state alone can still trigger Rule 5.

**Confidence:** Reduce from HIGH to MEDIUM.

**Rationale:** Distribution days are a confirmation signal; breadth state carries the core information.

### VIX Unavailable

**Action:** **ABORT.** Return `valid=false` and error message. Briefing cannot proceed.

**Rationale:** VIX is the backbone of volatility-driven classification. Substitute data is unreliable.

---

## YAML Machine-Readable Companion

```yaml
# Regime Framework Configuration
# Used by: daily-portfolio-briefing Step 3, wheel-roll-advisor, all downstream skills

regime_labels:
  - name: RISK_ON
    description: "Broad bull market, low VIX, expanding breadth, no catalysts. Aggressive sizing."
    semantic: "Attack mode — full strategy menu."
  - name: NORMAL
    description: "Typical conditions, neutral breadth, no extremes. Standard sizing."
    semantic: "Baseline — default state."
  - name: CAUTION
    description: "Heightened watch, multiple flags but no crisis. New longs sized down, rolls tighten, hedges add."
    semantic: "Defend mode — watch closely."
  - name: RISK_OFF
    description: "Defensive posture, high VIX, major drawdown, or compounded warnings. New longs suppressed."
    semantic: "Crisis mode — protect capital."

inputs_required:
  - vix_last
  - vix_change
  - spy_day_change
  - spy_5d_cumulative
  - breadth_state
  - distribution_day_count
  - economic_events_high_impact
  - earnings_cluster_intensity
  - market_news_intensity
  - yesterday_regime

rules:
  - id: VIX_EXTREME_HIGH
    priority: 1
    conditions:
      - "vix_last > ${regime_defend_vix_max}"  # 35.0
    target_regime: RISK_OFF
    rationale_template: "VIX {vix_last} > {regime_defend_vix_max} — immediate crisis"

  - id: SPY_EXTREME_DROP
    priority: 2
    conditions:
      - "spy_day_change <= ${regime_extreme_spy_drop}"  # -8%
    target_regime: RISK_OFF
    rationale_template: "SPY {spy_day_change:+.1%} <= extreme drop threshold"

  - id: SPY_CRISIS_PLUS_WEAK_BREADTH
    priority: 3
    conditions:
      - "spy_day_change <= ${regime_crisis_spy_drop}"  # -5%
      - "breadth_state == WEAK"
    target_regime: RISK_OFF
    rationale_template: "SPY {spy_day_change:+.1%} + WEAK breadth = dual confirmation"

  - id: VIX_RAPID_SPIKE
    priority: 4
    conditions:
      - "vix_change >= 0.25"
      - "vix_last > ${regime_hold_vix_max}"  # 25.0
    target_regime: CAUTION
    rationale_template: "VIX spiked +{vix_change*100:.0f}% to {vix_last:.1f} — event-driven volatility"

  - id: SPY_SEVERE_DROP_WEAK_BREADTH
    priority: 5
    conditions:
      - "spy_day_change <= ${regime_severe_spy_drop}"  # -3%
      - "breadth_state == WEAK"
    target_regime: CAUTION
    rationale_template: "SPY {spy_day_change:+.1%} on WEAK breadth = correction signal"

  - id: BREADTH_DETERIORATION
    priority: 6
    conditions:
      - "breadth_state == WEAK"
      - "distribution_day_count >= 5"
    target_regime: CAUTION
    rationale_template: "WEAK breadth + {distribution_day_count} distribution days = institutional selling"

  - id: DISTRIBUTION_DAY_EXTREME
    priority: 7
    conditions:
      - "distribution_day_count >= 7"
    target_regime: RISK_OFF
    rationale_template: "{distribution_day_count} distribution days = follow-through day failure"

  - id: ECONOMIC_EVENT_CLUSTER
    priority: 8
    conditions:
      - "economic_events_high_impact >= 2"
      - "vix_last > ${regime_hold_vix_max}"  # 25.0
    target_regime: CAUTION
    rationale_template: "{economic_events_high_impact} high-impact economic events today + VIX elevated"

  - id: EARNINGS_CLUSTER_WEAK_BREADTH
    priority: 9
    conditions:
      - "earnings_cluster_intensity >= 5"
      - "breadth_state == WEAK"
    target_regime: CAUTION
    rationale_template: "{earnings_cluster_intensity} mega-cap earnings + WEAK breadth = repricing risk"

  - id: NEWS_INTENSITY_SPIKE
    priority: 10
    conditions:
      - "market_news_intensity >= 5"
      - "vix_last >= ${regime_hold_vix_max}"  # 25.0
    target_regime: CAUTION
    rationale_template: "{market_news_intensity} high-impact news + elevated VIX = narrative shift"

  - id: DEFAULT
    priority: 11
    conditions: []
    target_regime: NORMAL
    rationale_template: "No warning signals — baseline regime"

stickiness:
  RISK_OFF_to_CAUTION: "2 consecutive normal sessions (VIX < 28, SPY +0.5%, breadth NEUTRAL+)"
  CAUTION_to_NORMAL: "1 normal session (VIX < 25, SPY ±2%, breadth STRONG or NEUTRAL)"
  NORMAL_to_RISK_ON: "Rare; VIX < 18 AND breadth STRONG AND no event clusters for 3+ sessions"
  upward_transitions: "Immediate (no gate)"
  downward_transitions: "Gated by prior-day regime + confirmation"

confidence_rules:
  HIGH:
    - "All inputs available"
    - "Clear rule match (not boundary)"
    - "No API failures"
  MEDIUM:
    - "One optional input missing (breadth, news, distribution days)"
    - "OR rule match is boundary case (e.g., VIX 24.9 vs hold_vix_max 25.0)"
  LOW:
    - "Multiple optional inputs missing"
    - "OR strong conflicting signals (e.g., WEAK breadth but VIX only 18)"
    - "Mark for user review; downstream sizing reduced 20–30%"
  DATA_ERROR:
    - "VIX unavailable"
    - "OR >3 critical inputs missing"
    - "Action: abort briefing, request re-run"

behavior_downstream:
  wheel_roll_advisor:
    RISK_ON: "normal_loss_trigger_multiplier (1.40x)"
    NORMAL: "normal_loss_trigger_multiplier (1.40x)"
    CAUTION: "high_caution_loss_trigger_multiplier (1.30x), min_dte_for_roll 21"
    RISK_OFF: "high_caution_loss_trigger_multiplier (1.20x) OR close-only"

  new_equity_ideas:
    RISK_ON: "Enabled, all sizes"
    NORMAL: "Enabled, all sizes"
    CAUTION: "Suppressed HIGH; MEDIUM/LOW OK"
    RISK_OFF: "Suppressed entirely"

  hedge_book:
    RISK_ON: "Passive, target 0–10% long delta coverage"
    NORMAL: "Passive, target 0–10%"
    CAUTION: "Active, target 15–20%"
    RISK_OFF: "Mandatory, target 25–35%"

  concentration:
    RISK_ON: "10% per name, 35% per sector"
    NORMAL: "10% per name, 35% per sector"
    CAUTION: "8% per name, 30% per sector"
    RISK_OFF: "6% per name, 25% per sector"

  sizing_tier:
    RISK_ON: "No cap — all conviction sizes enabled"
    NORMAL: "No cap"
    CAUTION: "MEDIUM max (3% NLV); HIGH suppressed"
    RISK_OFF: "LOW only (1.5% NLV max)"

missing_data_handling:
  breadth_unavailable:
    action: "Skip breadth-dependent rules. Proceed with VIX/SPY/calendar/news only."
    confidence_impact: "HIGH → MEDIUM"
  economic_calendar_down:
    action: "Assume economic_events_high_impact = 0. Skip Rule 8."
    confidence_impact: "HIGH → MEDIUM if near boundary"
  news_unavailable:
    action: "Skip Rule 10."
    confidence_impact: "HIGH → MEDIUM"
  distribution_day_unavailable:
    action: "Skip Rules 6 and 7. Use breadth WEAK state from other sources."
    confidence_impact: "HIGH → MEDIUM"
  vix_unavailable:
    action: "ABORT. Return valid=false."
    confidence_impact: "DATA_ERROR"
```

---

## Notes for Implementation

1. **Determinism.** Same inputs → same output. Avoid subjective judgment in the rules. All thresholds are explicit and come from `06-wheel-parameters.md` or this document.

2. **Parameter References.** Use names like `regime_defend_vix_max`, not inline `35.0`. This allows the framework to stay in sync with `06-wheel-parameters.md` without code changes.

3. **Confidence Scoring.** The briefing outputs not just the regime, but the confidence level. Downstream skills use confidence to adjust behavior (e.g., reduce new-position sizing if confidence is LOW).

4. **Stickiness.** Anti-flipping logic is essential for consistent day-to-day recommendations. A -2% down day shouldn't flip from NORMAL to CAUTION if no breadth deterioration or other warning signs confirm it.

5. **Edge Cases.** The regime is defined by rules, not "gut feel." If the rules don't match your intuition, the rules are right — the intuition is probably missing context. Add rules as new patterns emerge in production.

6. **Bloodbath Protocol (Deferred).** This framework classifies the regime. The bloodbath protocol — which executes CLOSE ALL WEEKLIES or other emergency actions — is deferred to v2 and handled separately. This framework is pre-bloodbath; it provides the state signal that bloodbath would consume.
