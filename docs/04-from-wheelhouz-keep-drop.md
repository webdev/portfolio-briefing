# Lessons from `wheelhouz`: What to Keep, What to Drop

**Status:** Draft v0.1
**Date:** 2026-05-07
**Source:** `/Users/gblazer/workspace/wheelhouz` — algorithmic wheel-strategy bot

The wheelhouz codebase is sophisticated: 90+ modules, ~733 tests, modular briefing renderer, multi-engine portfolio model, full tax integration. The pain point George cites — *inconsistent recommendations* — is not a code-quality issue; it's a **design-pattern mismatch**. The new tooling preserves the domain knowledge wheelhouz codified (thresholds, heuristics, override lists) while replacing the architecture (monolithic decision functions) with a skill-based one.

This document is the explicit cheat-sheet for what makes the cut.

---

## KEEP — domain knowledge worth migrating verbatim

### 1. Modular briefing panel architecture

**What wheelhouz did:** `src/delivery/briefing/` is split into independent renderers — `header.py`, `performance_panel.py`, `hedge_panel.py`, `risk_alerts_panel.py`, `watch_panel.py`. Each returns `list[str]` of markdown lines. Briefing assembly is a deterministic concatenation, not a free-form generation.

**What we keep:** Same architecture in `daily-portfolio-briefing/scripts/render/`. One module per panel. Concatenation is mechanical. **This is what makes the briefing format stable across runs.**

### 2. Section ordering: health-first

**What wheelhouz did:** The brief leads with portfolio state (health → performance → hedges → risk alerts → opportunities → watch → analyst), not with trade ideas. Recommendations follow from the dashboard, not the reverse.

**What we keep:** Same ordering. Updated section list in `02-daily-portfolio-briefing-skill.md`.

### 3. Greeks-aware roll targeting

**What wheelhouz did:** `_pick_put_roll_target()` adapts the target delta by IV regime:

```python
if iv_rank > 60:    # high IV
    target_delta = 0.16
    max_delta = 0.22
else:               # normal IV
    target_delta = 0.22
    max_delta = 0.30
```

**What we keep:** Verbatim in `wheel-roll-advisor/references/wheel_parameters.md`. These are the right numbers; they came from real trading. Don't re-derive.

### 4. Stress test before recommending a roll

**What wheelhouz did:** Calculates loss at 10% and 20% underlying drops before recommending a roll; gates the roll if risk/reward exceeds 3.0x.

**What we keep:** Bake into `wheel-roll-advisor` workflow as a guardrail step. A roll that looks good at current price but craters at -10% is not a roll we recommend.

### 5. Multi-threshold take-profit (moneyness-adaptive)

**What wheelhouz did:** Take-profit thresholds vary by moneyness:
- Deep OTM (delta < 0.10) + DTE > 120: 50% capture
- Deep OTM + medium DTE: 65%
- Deep OTM + short DTE: 80%
- Near-ATM (delta > 0.25): 40%
- Moderate OTM: 50%

Plus the "squeeze play": in 50–75% capture band with DTE > 14 and earnings 30+ days out, recommend a GTC limit at 75% capture rather than immediate close.

**What we keep:** Encoded in the `wheel-roll-advisor` decision matrix. The squeeze-play row is its own matrix cell with explicit conditions.

### 6. Loss-stop multipliers

**What wheelhouz did:** Hard loss stops:
- Monthlies: option price ≥ 2.0× entry → CLOSE NOW
- Weeklies (DTE ≤ 10): option price ≥ 1.5× entry → CLOSE NOW

Covered calls exempted from this stop (an underwater call means the stock rallied — net position is profitable).

**What we keep:** Same numbers, encoded as a guardrail that fires before the matrix lookup.

### 7. Defensive roll thresholds

**What wheelhouz did:** Defensive roll fires when underwater 1.30–1.40x entry AND DTE ≥ 21. The threshold drops from 1.40x to 1.30x when `macro_caution == "high"`.

**What we keep:** Same numbers. **Big change:** macro state is an explicit input to `wheel-roll-advisor`, not an implicit threshold modifier. The skill's matrix has separate rows for `regime=NORMAL` and `regime=CAUTION`. No hidden side effects.

### 8. Tail-risk override list

**What wheelhouz did:** A curated list of names that get treated more conservatively — Chinese ADRs (BABA, JD, etc.), binary biotechs, crypto-proxies (MSTR, COIN), high-borrow meme stocks. Rules:
- Block new put-sale recommendations entirely
- On existing positions: surface `⚠ TAIL RISK` warning
- Suppress ROLL suggestion (rolling lower-strike doesn't help against headline gaps)
- DEFENSIVE ROLL trigger overridden to `CLOSE_NOW` with rationale "don't roll — buy back and walk"

**What we keep:** The list and the override semantics. Lives in `wheel-roll-advisor/references/tail_risk_names.md` and is loaded as a guardrail. **Note for George:** keep this list tight — adding a name commits the system to more conservative treatment.

### 9. Earnings-aware expiration snapping

**What wheelhouz did:** Hard rule: every expiration date is rendered as `Fri May 29 '26` (weekday + year). Never `May 29` or `5/29`. Every actionable date is validated against the actual chain — Saturday SPY puts, Thursday VIX calls, dates not on the chain are bugs that have shipped before.

**What we keep:** Non-negotiable. Becomes a utility skill `expiration-validator` (or stays inside `wheel-roll-advisor`) that every other skill calls before emitting a recommendation. **No more relying on code review or CLAUDE.md discipline; it's a function call.**

### 10. Live-data backing requirement

**What wheelhouz CLAUDE.md says:**
> Every actionable trade ticket must be backed by real market data fetched in this analysis cycle: underlying price, IV rank/percentile, technical consensus, chain validity. Missing data → no recommendation.

**What we keep:** Same rule, enforced by the briefing's quality gate (`data-quality-checker`). If a recommendation is rendered without live underlying price + chain data, the briefing is downgraded to DRAFT.

### 11. Concentration constraint enforcement

**What wheelhouz did:** Pre-sizing AND post-sizing concentration checks. The pre-sizing check alone is insufficient — the trade itself can push a name over 10% NLV.

**What we keep:** Same two-stage check in `position-sizer` integration. Pre-sizing filters candidates; post-sizing rejects or scales the trade.

### 12. The 13 alpha signals

**What wheelhouz did:** A defined set of entry signals (intraday dip, multi-day pullback, IV rank spike, support bounce, oversold RSI, macro fear, skew blowout, term inversion, earnings overreaction, sector rotation, volume climax, gap fill, dark pool).

**What we keep:** The taxonomy. Most of these map to existing screener skills. Where one doesn't (e.g., skew blowout, term inversion), we don't build it for v1 — we leave it as a TODO and accept narrower coverage.

---

## DROP — patterns that don't fit a skill-based design

### 1. The 830-line `review_position()` function

**What wheelhouz did:** All seven decision branches (CLOSE NOW, DEFENSIVE ROLL, TAKE PROFIT, WATCH, HOLD, plus roll sub-decisions) live in one function with cascading if/elif logic. Adding a new gate without breaking an old one is hard.

**What we drop:** Replace with a deterministic decision matrix in `wheel-roll-advisor/references/decision_matrix.md`. Each branch is a row in a table, not a code path. Adding a new behavior is a row insertion. **This is the single biggest fix for the "inconsistent recommendations" pain point.**

### 2. Implicit `macro_caution` coupling

**What wheelhouz did:** The `macro_caution` parameter threads through `review_position()` to silently adjust thresholds (e.g., 1.40x → 1.30x for defensive roll). Subtle, hard to debug.

**What we drop:** Make regime an explicit matrix dimension. Different rows for `regime=NORMAL` vs `regime=CAUTION`. Same input visibility for the user — "this recommendation came from row CAUTION_PUT_ITM_DTE21" is auditable.

### 3. Position-review-centric architecture

**What wheelhouz did:** `review_position()` is the orchestrator for *every* position-level decision. It calls into roll-target selection, take-profit logic, earnings checks, tax-aware exits, etc. — all from one function. Single point of failure.

**What we drop:** Decisions are distributed across skills (`wheel-roll-advisor` for options, `equity-position-reviewer` for stock — actually we don't even build the latter; it's just the matrix in `daily-portfolio-briefing/references/equity_decision_matrix.md`). Each skill is independently testable and versionable.

### 4. OAuth subprocess auto-refresh

**What wheelhouz did:** `src/data/portfolio.py` calls `subprocess.Popen(['python', '-m', 'src.data.auth', '--live'])` to re-auth when tokens expire. Works, but couples portfolio code to shell semantics.

**What we drop:** The MCP server owns token management. The skill never sees an expired token — it sees a clean `AUTH_REQUIRED` response and surfaces it to the user. Cleaner separation.

### 5. Expiration validation as CLAUDE.md discipline

**What wheelhouz did:** The CLAUDE.md has multi-paragraph rules about valid expiration dates. Enforcement is "be careful when writing code." Bugs have shipped (Saturday SPY puts).

**What we drop:** Validation lives in code, not in prose. A `validate_expiration(symbol, strike, exp, chain)` function. If you skip the call, the linter or test suite catches it.

### 6. Continuous monitor + 60-second VIX/SPY heartbeat

**What wheelhouz did:** `src/monitor/continuous.py` runs a 60-second monitor checking VIX/SPY for crisis triggers. Independent of the 5x daily analysis.

**What we drop:** Out of scope for v1. The briefing runs once or twice daily. Crisis monitoring is a separate concern; if the user wants it, build it as a separate scheduled task that produces alerts. Don't bundle it into the briefing.

### 7. The 5x-daily analysis cadence

**What wheelhouz did:** Five push points per day (8am morning brief, 10:30 post-open, 1pm midday, 3:30pm EOD, 4:30pm post-market) plus pre-market sentinel.

**What we drop:** v1 is one briefing per day, on demand. Multiple cadences are valuable but premature; we should validate one before building five.

### 8. Bloodbath protocol / regime detection ladder

**What wheelhouz did:** Elaborate crisis-management logic (`src/monitor/bloodbath.py`, `regime.py`, `sentinel.py`) with VIX thresholds, SPY drop levels, employer-crisis overrides for ADBE, sector repricing detection, etc.

**What we drop (for v1):** The regime layer in v1 is a single classifier (RISK_ON / CAUTION / RISK_OFF). The complex bloodbath response logic is deferred. If we ever need it, it's a separate skill that reads the regime and emits a list of "emergency action" recommendations the briefing surfaces above the normal ones.

### 9. Tax-loss harvesting as a built-in pipeline stage

**What wheelhouz did:** `src/risk/tax_harvest.py` plus `src/risk/ltcg_manager.py` are first-class pipeline stages. Significant complexity.

**What we drop (for v1):** Surface tax flags in the briefing (LTCG approaching, harvest candidate) but don't build the harvest planner. Tax-loss harvesting decisions warrant human attention, not algorithmic generation.

### 10. Multi-engine portfolio model (E1/E2/E3 split)

**What wheelhouz did:** Engine 1 (45% core), Engine 2 (45% wheel), Engine 3 (10% dry powder) with explicit drift tracking and rebalancing recommendations between engines.

**What we drop (for v1):** Track allocation by sector and concentration, but don't impose the engine model. It's a meaningful structure but it's also opinionated, and the briefing should be useful before the user has bought into a specific allocation framework. If George wants the E1/E2/E3 view, it lives in `briefing_config.yaml` as an optional template, not as core logic.

### 11. Telegram delivery

**What wheelhouz did:** `src/delivery/telegram_bot.py` pushes the briefing to Telegram, accepts EXECUTE commands.

**What we drop:** v1 surfaces the briefing as a markdown file + chat summary. No Telegram, no remote execution. Simple is the point.

### 12. The 7-agent parallel-build architecture

**What wheelhouz did:** The `wheelhouz/CLAUDE.md` describes seven Claude Code agents owning seven module sections. Sophisticated, but optimized for a project being built rapidly by multiple agents in parallel.

**What we drop:** Single-agent build. The project is small enough that the parallelism overhead isn't worth it. One agent, one branch, sequential development.

---

## THE META-LESSON

The wheelhouz codebase shows what happens when you build trading software the way you'd build any other software: with abstractions, layers, configuration, and patterns of indirection. The result is genuinely impressive but also brittle in exactly the way George experienced — *the inconsistencies aren't bugs, they're emergent behavior of layered if/elif logic interacting in ways that grow harder to reason about as the codebase scales*.

The skill-based design solves this by making decisions **flat, deterministic, and individually testable**. There is no orchestrator that "decides everything." Each decision is owned by a skill, expressed as a table lookup over explicit inputs, with a known output enum. The briefing's job is to call the right skills in the right order and concatenate their outputs.

This won't scale to wheelhouz's full ambition (5x daily, social intel, learning loop, bloodbath protocol). It's not supposed to. v1 is one briefing per day, deterministic, debuggable. Once that's stable, *individual* additional capabilities — bloodbath alerts, learning loop, multi-cadence — can be added as separate skills without re-entering the inconsistency trap, because each new skill has its own narrow surface.

The simplest version is the version that ships. Everything wheelhouz does that v1 doesn't isn't being deleted from the world — it's deferred to v2+ where it can be added as discrete skills rather than as branches in an ever-growing decision function.
