# CLAUDE.md — portfolio-briefing project rules

Canonical rules that the briefing pipeline must follow. Update this file as
constraints evolve. Cross-cutting standards live here so we don't relitigate
them.

## Account scope — INDIVIDUAL only (for now)

The user has multiple E*TRADE accounts (Joint JTWROS, Individual Brokerage,
INDIVIDUAL, Roth IRA, Traditional IRA, etc.). The briefing pipeline is
currently scoped to **just the INDIVIDUAL account**. This is enforced by:

- `config/briefing.yaml` → `account_desc_whitelist: [INDIVIDUAL]`
- `adapters/etrade_adapter.fetch_etrade_snapshot(account_desc_whitelist=...)` —
  filters by `accountDesc` (exact match, case-insensitive) before any
  position/balance aggregation
- `snapshot_inputs.py` passes the config value through

**Why scoped:**
- Cross-account aggregation gave us a bad CC recommendation (SPY: 15 shares
  in "Individual Brokerage" + 91 in "INDIVIDUAL" = 106 aggregate, but no
  single account had a 100-share lot to write against).
- Tax routing rules differ by account (Roth IRA vs Taxable) and we haven't
  built that out fully yet.
- Until per-account ticket routing is wired, scoping to one account avoids
  proposing trades the user can't actually place there.

**To widen later:**
1. Add the new account's accountDesc to `account_desc_whitelist` in briefing.yaml.
2. Add account-routing logic so PULLBACK CSPs / new CCs are correctly
   routed (Roth IRA preference for short-dated premium, etc. — see the
   wheelhouz routing rules in `docs/04-from-wheelhouz-keep-drop.md`).
3. Test with `--etrade-live` and confirm positions de-dup correctly.

## Chain data — E*TRADE only, never yfinance

Every actionable recommendation that includes a strike or limit price MUST
pull its chain data through the `etrade-chain-fetcher` skill. yfinance is
forbidden for tradeable chain prices. See
`skills/etrade-chain-fetcher/SKILL.md` for the canonical surface.

yfinance IS still allowed for:
- IV rank approximation from 252-day historical vol
- RSI(14), 50/200-SMA, drawdown from 52-week high
- Earnings calendar dates
- General quotes/prices for non-option context

## Brokerage backend — E*TRADE (default) or Schwab, selected by config

The pipeline is broker-pluggable. `briefing.yaml` → `brokerage:` (or env
`PORTFOLIO_BRIEFING_BROKER`) selects the backend; default `etrade` keeps the
existing path byte-identical. `schwab` routes the live snapshot through
`adapters/schwab_adapter.py`, chain data through the env-selected backend in the
canonical chain fetcher and `adapters/broker_market.py`, and the Telegram
daemon's auth through `schwab_auth.py` (OAuth2; access token 30 min, refresh
silent, full re-auth ~weekly). Read-only on both brokers (hard rule #7). Schwab
account scope keys off `schwab_account_labels` (account number → desc) so
`account_desc_whitelist` gates the same way. The two brokers never run in one
process — the Schwab user runs a separate instance with its own bot token,
credentials, and token file.

## The Entry Algorithm — the canonical six-step evaluator (hard rule #48)

George (2026-08-14): "the exact algorithm that ensures that the entry is as
good as possible. I definitely don't want a coin-flip algorithm." Every
NEW-OPEN recommendation (CSP or CC) passes ONE evaluator —
`analysis/entry_algorithm.py::evaluate_entry(side, ticker, strike,
expiration, premium_mid, snapshot_data, analytics, config, positions=...,
action_close_idents=...)` → `EntryDecision {verdict ENTER|WAIT|BLOCKED,
grade, ordered_reasons, one_line}`. It is a CONDUCTOR over the existing
single sources of truth, in this exact order:

1. **DATA FRESHNESS** — `vintage_guard.resolve_new_open_rsi` (rule #47);
   unverifiable up-move on a put side → BLOCKED (rule #44 fail-safe).
2. **HARD BLOCKS** (ordered) — RSI hard block for the side
   (`rsi_discipline.hook`, index-CC thresholds honored); earnings inside
   the contract (`earnings_guard`; unknown date → WARN per
   `earnings_unknown`); held-put 5% overlap (`put_overlap_check`, #40);
   projected obligation-inclusive name concentration vs the tier cap
   (`position_tiers`, #16 math); LT-verdict gate (`lt_verdict_gate`, #39);
   tail-risk list (config); new-open tenor cap
   (`roll.max_action_tenor_days`, core ×3 — #45); contract closed by
   today's action list (#43).
3. **PAYMENT FLOORS** — delivered yield ≥ 12% ann + 0.5% of collateral
   (#44); `iv_honesty` gap-inflated note; vol source labeled
   (`chain_iv.effective_iv`).
4. **SETUP GRADE** — `setup_grade` on the RESOLVED inputs; unverified
   vintage caps the grade (never A/B on unverified RSI — #46).
5. **B FLOOR** — `setup_grade.below_actionable_floor` → WAIT with the
   graded reason.
6. **BOOK GATES** — `capacity_gate` (stress-coverage floor, #41) → WAIT
   with the measured ratio; `sector_exposure` conviction context
   (annotation, never a block).

Deterministic and pure; every step's finding — pass or fail — rides in
`ordered_reasons` (first hard block = primary). Fail directions inherit
from the single sources (rule #19). Enforcement:
`entry_algorithm.audit_conformance` re-runs the evaluator over every
green-lit new-open ticket in the RENDERED briefing — the "🧮 Entry
Algorithm Conformance" panel (steps/aggregate.py). A NEW surface MUST call
`evaluate_entry`, never re-derive the steps. Full contract: hard rule #48
below. Tests: `tests/test_entry_algorithm.py`.

## Roll discipline — moneyness-based, not P&L%

Surface EXECUTE ROLL only when the position is at/past strike (genuine
assignment risk):
- Short PUT: spot ≤ 1.03 × strike
- Short CALL: spot ≥ 0.97 × strike

P&L% bleed from a rally is NOT a defensive trigger by itself. A -70% short
call that's still 12% OTM is "the rally happened, theta will recover," not
"I'm about to lose my shares." Same rule applies to puts.

**Action-list roll gate (enforced in `render/panels.py` blocks #3 + #4).** For a
covered CALL that is still OTM (no genuine assignment risk), the action list
surfaces a roll ONLY when there's a genuine credit-positive roll-**UP** (higher
strike). A same-strike calendar or "no good roll" case defers to HOLD — never
render a same-strike re-cap labeled as up-and-out (the SMH/SOXX bug), and never
surface EXECUTE ROLL when the advisor's `recommendedCandidateId` is `A`=HOLD (the
SPY bug). Genuine ITM (spot at/through strike) still surfaces the roll. The Watch
ROLL ANALYSIS table always shows the full candidate menu regardless.

**Advisor's `recommendedCandidateId` respects the tenor cap.** The
`wheel-roll-advisor` (`advise.py`) calls its own `candidate_ranker` when picking
the table's `✅ recommended` candidate. That call MUST pass `max_tenor_days` —
otherwise the ranker just maximizes net credit and "recommends" multi-year
same-strike calendars (the SOXX `C` candidate, +791d / +$10,100 credit on a
covered call, labeled "extend 10-15 months" by the old hardcoded note). Default:
120d for non-core, 360d for core (`is_core` from context). Callers may override
via `context["max_tenor_days"]`. Source: `advise.py` line ~211 — the call now
matches the discipline of `render/panels.py` block #3's ranker.

**Both roll legs must carry REAL chain values — never "pick from the ROLL
ANALYSIS table."** Block #3 (priced-candidate path) already does this — it
renders concrete BTC and STO quotes from `enumerate_roll_candidates`. Block #4
(generic directive path, fires when a roll is warranted but block #3 had no
priced best) used to render only the BTC leg with a real mid and then hand-wave
the STO leg ("Sell-to-Open 1× a higher-strike call further out — pick the target
from the ROLL ANALYSIS table"). That violates the no-hand-wavy-output rule.
Block #4 now fetches a REAL STO quote via the canonical `etrade-chain-fetcher`:
delta-first selection (`find_strike_near_delta`, target ~0.25 within ±0.12,
volatility-adaptive), %OTM fallback when the chain has no Greeks, target DTE
~90d within the 120d action-list tenor cap. The order line renders the actual
strike, expiration, bid/mid/ask, DTE, delta (or `δ n/a`), and net credit/debit.
When the chain is unreachable, the line says "live chain unavailable, verify the
STO leg at the broker before placing" — never a fabricated strike or price.
Source: `render/panels.py` block #4 `_ROLL_DECISIONS` branch.

**Matrix is side-gated (PUT vs CALL).** The decision matrix encodes side only in
the row `id` prefix (`PUT_*` / `CALL_*`), so `decision_walker.row_matches` must
gate on it — a short CALL may match ONLY `CALL_*` rows (or side-agnostic ones
like `DEFAULT_HOLD`), never a `PUT_*` row. Without the gate, a covered call
matched the first PUT row whose moneyness/DTE/outlook/IV aligned and got
inverted advice (the real SMH bug: a barely-ITM covered call tagged
`PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT` → "keep strike, roll out", which re-caps a
bullish name). Covered-call ITM/NEAR-ATM cells (`CALL_NORMAL_ITM_ROLL_UP` etc.)
recommend **ROLL_OUT_AND_UP** (roll the strike up, preserve upside), never
keep-strike. And a ROLL is always a TWO-leg ticket — the action list never
renders a roll as a lone buy-to-close (that reads as a close / gives up shares).

See `skills/wheel-roll-advisor/` decision matrix.

## Core holdings — no force-sell

Tickers listed in `core_positions` are exempt from the standard 10% NLV
concentration trim. Use `core_concentration_cap_pct` (default 18%) as the
soft cap; only roll covered calls up at this stage, never sell shares.
At `core_runaway_cap_pct` (default 22%) flag REVIEW CORE with full option
menu, but still don't auto-recommend outright sale.

Current core list (`config/briefing.yaml`): GOOG, GOOGL, NVDA, MSFT, VRT,
ADBE, AMZN, META. TSLA is NOT on the core list — third-party SELL recs
on TSLA can fire EXIT.

### Short calls on core — never CLOSE on loss-stop

The user's explicit policy: keep core shares long-term, **roll covered
calls year after year**, never close them at a loss.

When the wheel-roll-advisor's `GUARDRAIL_LOSS_STOP` cell fires CLOSE on a
SHORT CALL whose underlying is in `core_positions`, the renderer overrides
the recommendation to "DEFENSIVE ROLL (core override)" and points at the
Watch-panel ROLL ANALYSIS table. Closing on a core position would:

1. Realize a short-term loss the user explicitly wants to avoid.
2. Leave the shares uncapped (any upside becomes assignable on a NEW
   short call written later — same exposure, fresh losses possible).
3. Defeat the wheel-on-core strategy entirely.

This rule does NOT apply to:
- `CLOSE_FOR_PROFIT` on core short calls (winners are fine to close).
- Short PUTs on core tickers (different mechanics: assignment buys shares
  the user actually wants on dips).
- Non-core short calls (standard loss-stop behavior applies).

## Trade-validator filtering — categorical

POOR/BLOCK trade-validator verdicts have asymmetric treatment:
- **NEW trades** (PULLBACK CSP, new CSP, new BTO LEAP, opening strangle):
  POOR/BLOCK → filter from action list, surface only in a transparency footer.
- **POSITION MANAGEMENT** (DEFENSIVE ROLL, DEFENSIVE COLLAR, EXECUTE ROLL,
  CLOSE NOW, TAKE PROFIT): render the badge but never filter. User may
  need to act regardless of EV.

Rationale: new exposure is discretionary; existing positions are committed.

## CC writability — per-account, not aggregate

Covered call recommendations must check that at least 100 shares are held
**within a single account**, not just aggregate across accounts. The CC
contract sits at the broker level; you can't combine a 15-share lot in
account A with a 91-share lot in account B to back one CC.

Implementation: `strategy_upgrades.py` reads each position's
`accountsBreakdown` field, parses per-account share counts, and uses
`max_qty_in_one_account` as the writability threshold. (With the INDIVIDUAL
account scope above, this is mostly moot — but the check stays in for
when we widen scope.)

## Live-data backing on every actionable line

Every actionable trade ticket MUST be backed by real market data fetched
this cycle. If yfinance or E*TRADE returns nothing, surface "data
unavailable, verify before placing" and skip the order ticket. Don't
substitute defaults.

This is enforced by the `briefing-data-verifier` skill, which scans the
rendered briefing for chain-attribution markers.

## RSI discipline — shown on every recommendation, hard-gates new opens

RSI(14) is surfaced on EVERY recommendation line and acts as a disciplined
trade gate. It is read **asymmetrically** across the two sides of the wheel:

- **Selling puts** (CSPs): favoured in a pullback (RSI ~30-50). OVERBOUGHT
  (RSI > 70) is a **hard block** — thinnest premium right before a reversal can
  whip the stock down through the strike. Falling-knife (RSI < 25) → warn.
- **Selling covered calls**: favoured when extended/overbought (RSI ≥ 60).
  OVERSOLD (RSI < 35) is a **hard block** — caps the name right before a likely
  bounce. **Mid-range (35-60) is NOT actionable** — writing here collects only
  average premium while capping upside; on the Strategy Upgrades surface these
  are pulled out of "✅ READY TO WRITE" into a "⏸ Covered calls — wait for
  strength" section (`rsi_wait` flag in `strategy_upgrades.py`, rendered by
  `strategy_upgrades_panel.py`). Only RSI ≥ 60 stays actionable; RSI-unknown
  writes stay actionable with a verify note. (The central hook still classifies
  mid-range as `keep`+caution for non-CC surfaces; the wait-for-strength
  demotion is specific to new covered-call writes.)

- **Buying shares** (equity ADD, sub-lot completion, thematic BUY): favoured in
  a pullback (RSI < ~60). OVERBOUGHT (RSI > 70) is a **hard block** — don't chase
  an extended move.

**Every recommendation passes through one central hook** —
`rsi_discipline.hook(side, rsi)` — which returns **remove / keep / promote**:
- `remove` (RSI unfavorable for a NEW open) → the rec is dropped from the
  actionable list into a transparency / "Held back by RSI" footer.
- `promote` (RSI favorable) → rendered with a "✅ RSI favourable" badge.
- `keep` (neutral/caution, or any management line) → rendered as-is; caution
  carries a "⚠ RSI caution" badge.

The hard gate (remove) applies ONLY to new opens. Existing-position management
(rolls/closes/trims/collars/hedges) is NEVER removed — RSI is annotated for
context. Sides: `put` (CSPs), `call` (covered calls), `buy` (equity adds).

Wired through every surface: action list, Income Opportunities, Strategy Upgrades
(CC / strangle / sub-lot), long-term opportunities, thematic scout, Watch, and
the Analyst Brief. A pipeline verifier — `audit_missing_rsi(md)` in
`aggregate.py` — scans the rendered briefing and emits an "RSI Coverage Check"
panel; every actionable recommendation line MUST carry an RSI read or it is
flagged. (Italic transparency footers and the Capital Plan rollup are exempt.)

Single source of truth: `scripts/analysis/rsi_discipline.py` (`assess`, `tag`,
`hook`, `annotate_action_lines`, `audit_missing_rsi`). Bands are
config-overridable via `briefing.yaml` → `rsi_discipline` ("standard wheel
bands" by default, incl. a `buy` block). RSI comes from
`snapshot_data["technicals"][sym]["rsi_14"]` (yfinance, Wilder's smoothing).

## Covered-call strike selection — DELTA-FIRST, show the REAL delta

New covered-call writes pick their strike by a **delta band** (default ~0.25,
the wheel 0.20-0.30 zone), NOT a flat %OTM. The same delta sits **further OTM on
a high-IV name** (more headroom) and **closer on a low-IV name** — a flat %OTM
target does the opposite and caps a volatile name too tight. This was a real
miss: a flat 6%-OTM pick put a SOFI $17 call on a ~$16 stock (≈30-delta, ~1-in-3
assignment) and capped a name the same briefing wanted to *accumulate*.

Hard requirements (mirror the "live-data backing / no fabricated numbers" rule):

- **Never display a delta you didn't measure.** The old renderer printed a
  hardcoded "~6% OTM, ~0.30 delta" on every CC line regardless of the actual
  contract. Now the SELL line shows the **measured delta** (`δ 0.24`) and the
  **actual OTM%** computed from the selected strike vs spot. When the chain
  carries no usable deltas, render **`δ n/a`** — never invent one.
- **Selection order:** `find_strike_near_delta` (delta band) first; fall back to
  `find_strike_at_otm_pct` ONLY when the chain has no deltas (`selected_by`
  records which path ran). Both come from the canonical `etrade-chain-fetcher`.
- Config: `briefing.yaml` → `covered_call` (`target_delta`, `delta_tolerance`,
  `fallback_otm_pct`). Source: `strategy_upgrades.py::_etrade_call_quote` +
  `strategy_upgrades_panel.py`.

## Intrinsic value — fair-value read on every single-stock recommendation

Every single-stock recommendation carries a fair-value annotation so the user
can see price vs worth at the point of action. Two independent estimates from
Financial Modeling Prep (FMP):

- **DCF** — `/stable/discounted-cash-flow?symbol=` (company-level model).
- **Analyst price-target consensus** — `/stable/price-target-summary?symbol=`
  (avg target + analyst count).

FMP migrated to the `/stable/` API; the legacy `/api/v3` + `/api/v4` endpoints
return HTTP 403 for keys created after 2025-08-31. Use `/stable/` only.

Rules (mirror the project's existing constraints):

- **Single stocks only.** ETFs are baskets — no company DCF — so they render
  `FV: n/a — basket (ETF)`, never a fabricated number. The ETF set is the
  built-in list ∪ `briefing.yaml` → `intrinsic_value.etf_tickers`.
- **Fail closed.** No `FMP_API_KEY`, a network error, or an empty FMP response →
  no value is invented. With no key the briefing shows ONE transparency footer
  (not per-line `n/a` spam); with a key, a specific ticker that returns nothing
  is marked `FV: n/a (no FMP data)`.
- **Cache.** Values cache to `state/<…>/intrinsic_value_cache.json` for
  `ttl_hours` (default 24h); only the single stocks that actually appear on a
  recommendation header are fetched (2 FMP calls each), so the daily run stays
  inside the FMP free tier.

Implemented as a deterministic post-pass over the assembled briefing in
`aggregate.py`, mirroring the RSI annotate pattern. Single source of truth:
`scripts/analysis/intrinsic_value.py` (`get_fair_values`, `format_fv_note`,
`annotate_intrinsic`, `is_etf`, `default_etf_set`). The matcher annotates
numbered action headers and bold rec headers that carry a recommendation
keyword; it skips prose, the Scout's market-read bullets, italic footers, and
the Capital Plan rollup.

## Capital Plan — new-put exposure gate

The Capital Plan must never surface a NEW put (`NEW_CSP` / `LT_CSP`) that the
rest of the briefing forbids. Two hard gates in `capital-planner/plan.py`
(`_apply_new_put_gate`), demoting the action to Skipped with the reason shown:

1. **Stress coverage.** If `coverage_ratio` is below the floor (default 0.50×,
   the same red threshold that promotes hedges to CRITICAL), no new put
   obligation is added — the book can't cover what it already has.
2. **Concentration.** `existing_weight% + new_collateral/NLV` must stay under the
   per-name cap (default 10% NLV) or the new put is skipped.

Existing-position management (CLOSE/ROLL/TRIM/HEDGE) is never gated here.
Config: `prioritization_rules.yaml` → `new_put_gate` (falls back to the existing
`stress_coverage.red_threshold` / `concentration.cap_pct`).

## Thematic Scout — market read, then shortlist

The scout leads with a deterministic **Market Pulse** (in
`thematic_research.py::_render_market_pulse`): cross-theme breadth, hottest /
coldest theme by 5-day momentum, RSI posture, the hottest and cooling movers
(each with a one-line market-setup read synthesized from trend / RSI / distance
from highs / IV), and a per-theme pulse line. This is a "what's happening across
the market" read on each ticker's own setup — independent of holdings. The
RSI-hook-gated **Actionable shortlist** (BUY / CSP ENTRY) follows it unchanged.

### Theme-by-theme pulse — companies + covering ETFs

Each theme line in the pulse lists its constituent **companies** (the curated
`anchors` in `theme_universes.yaml`) and the **ETFs** that cover them (the
`etfs` field). Both come from the YAML — the renderer never fabricates names.

**ETF tickers MUST be real and verified — never invent one.** When adding an
ETF to a theme, confirm (a) the ticker exists and (b) it actually covers that
theme, via the ETF issuer / a market-data source. Each `etfs` entry carries a
`# … verified <date>` comment. Verified mapping (as of 2026-05-22):

- **Semis** → SMH, SOXX, QQQ, **CHPX** (Global X AI Semiconductor & Quantum)
- **Memory & Storage** → **DRAM** (Roundhill Memory — SK hynix, Samsung, MU, SNDK, Kioxia)
- **Photonics & Optics** → *no dedicated ETF*; optics names (LITE, COHR, CIEN, GLW) sit in broad semis ETFs (SMH/SOXX)
- **AI Data Centers** → **WGMI** (CoinShares Bitcoin Mining — IREN, CIFR, WULF, CORZ)
- **Power** → URA, NLR, XLU, **POWR** (iShares US Power Infrastructure), **GRID** (First Trust Smart Grid)
- **Networking** → *no dedicated ETF*; ANET/CSCO are top holdings of broad tech (XLK, QQQ)
- **Quantum** → QTUM (Defiance), **CHPX** (Global X AI Semiconductor & Quantum)
- Space → UFO, ARKX · Robotics → BOTZ, ROBO · Drones → PPA, ITA · Rare Earths → REMX · Cybersecurity → HACK, CIBR

Note: **CHPX is an AI-semiconductor & quantum fund, not a photonics ETF** — the
source memo (`uploads/NEXT DECADE OPP .pdf`) listed it under optics, but it is
placed accurately here under semis/quantum. New verified theme ETFs are also
added to `intrinsic_value.DEFAULT_ETFS` so they render `FV: n/a — basket (ETF)`
rather than a fabricated DCF.

### Candidate Trades — inline in the daily briefing

The daily briefing carries the focused candidate set **inline**: the Thematic
Scout section renders the **Market Pulse only** (`render_scout_section(...,
include_shortlist=False)`), immediately followed by a **`## 🎯 Candidate Trades —
Across Themes`** section (`candidate_research.render_candidate_briefing(...,
as_section=True)`, wired in `aggregate.py`). That section lists Today's
Candidates (RSI-favorable, with a live entry card) + On Deck (qualifying but
RSI-blocked), de-duped across themes, and replaces the old per-theme "actionable
shortlist." Fair values for just the candidate tickers are fetched inline
(`briefing_candidate_tickers`, cached). The standalone scout report / web-app
path keeps the shortlist via the `include_shortlist=True` default.

**Position-aware — never recommend a contract you already hold.** The candidate
list cross-checks the user's CURRENT short puts (built from the live snapshot via
`short_puts_by_ticker`, passed as `existing_short_puts` from `aggregate.py`),
independent of the 24h scout cache. A CSP candidate whose strike duplicates a
held put (within `_STRIKE_OVERLAP_PCT`, 5%) is pulled out of the actionable list
into an **"⏸ Already positioned — you hold this put"** note ("the same trade, not
a new one"); a same-name candidate at a different strike is kept but annotated as
stacking single-name risk. This catches what the scout's own put-stack guard
misses: that guard only fires at ≥2 held puts (or a near-strike overlap) AND runs
off the possibly-stale cache, so a single held put — e.g. the real LITE $820P /
NOW $92P duplicates — slipped through as fresh candidates. The render-time check
is the backstop. Source: `candidate_research.py::render_candidate_briefing`.

### Candidate Research report — per-company, all themes

A separate dated report (`~/Documents/briefings/candidates_DATE.md`, also written
to `reports/daily/`) covers EVERY company across the Scout themes. Source:
`scripts/steps/candidate_research.py` (`render_candidate_report`,
`single_stock_tickers`); generated in `run_briefing.py` step 8.5 and pointed to
from the briefing's Scout section.

Each company gets a research card — RSI · trend vs 200-SMA · IV rank · drawdown ·
5-day · valuation (FMP DCF + analyst target, single stocks only) · the Scout's
verdict — and a concrete entry is attached **only when the setup qualifies AND
passes the RSI gate** (`rsi_discipline.hook`): a CSP ticket from the live E*TRADE
chain (reused from the Scout's `csp_entry`), or a BUY note. Statuses: 🎯 CANDIDATE
(RSI-favorable actionable), ⏸ HELD (RSI) (actionable but RSI-blocked, no entry),
👀 WATCH, 🔴 AVOID. It reuses the Scout's existing per-ticker research (no new
technical fetches); fair values are fetched once and cached
(`intrinsic_value_cache.json`, 24h), fail-closed. Theme metadata is read fresh
from `theme_universes.yaml` (config, not cached data).

### When-To-Enter report — entry triggers, every theme company

A second dated companion report (`~/Documents/briefings/when_to_enter_DATE.md`,
also `reports/daily/`) sits next to `candidates_DATE.md` and reframes the same
universe as **explicit entry triggers**. Where the candidate report is a
research card per name, this one is an action card: every company in the
Thematic Scout gets classified into one of four statuses and shown with the
exact trigger condition.

Source: `scripts/steps/when_to_enter.py` (`classify` — pure function over a
scout result; `render_when_to_enter_report` — the markdown). Generated in
`run_briefing.py` step 8.6 (right after `candidates_DATE.md`), delivered to the
same folder, pointed to from the Thematic Scout section of the daily briefing.

Classifier priority (this order is the contract — tests pin every band):

1. **RSI ≥ 70 → 🔴 WAIT — overbought.** Even an AVOID verdict on an overbought
   name reduces to "wait for the cool-off." Trigger: `RSI < 55 AND 8-12%
   pullback`. (Bonus: if IV rank ≥ 60 and the user owns shares, the trigger
   surfaces COVERED-CALL writing as the favored action right now.)
2. **60 ≤ RSI < 70 → 🟡 WAIT — extended.** Trigger: `RSI in 45-55 AND 5-8%
   pullback`.
3. **drawdown ≥ 40% AND RSI < 45 AND below 200-SMA by >15% → 🔴 AVOID — thesis
   check.** Genuine fundamental-broken case.
4. **RSI < 25 → 🟡 WAIT — falling knife.** Trigger: `RSI > 35 AND one green
   day`.
5. **`verdict` starts with AVOID (not caught above) → 🔴 AVOID — scout flag.**
   Surfaces the scout's actual rationale verbatim (no invented reason).
6. **35 ≤ RSI ≤ 55 AND verdict CSP → 🟢 ENTRY NOW — CSP** with the live
   `csp_entry` ticket (strike, expiration, mid/bid/ask, DTE, collateral). Flags
   earnings-inside-window inline.
7. **35 ≤ RSI ≤ 55 AND verdict BUY → 🟢 ENTRY NOW — BUY** (small starter, scale
   on weakness).
8. **35 ≤ RSI ≤ 55, no actionable verdict → 🟡 WATCH — neutral.** No invented
   entry.
9. **25 ≤ RSI < 35 → 🟡 WAIT — oversold** (not falling-knife).
10. **otherwise (55-60, missing RSI) → 🟡 NEUTRAL.**

The technical-state-first ordering is what avoided the AMD bug (RSI 75 with
verdict "AVOID — extended" was wrongly tagged AVOID with read "Deep drawdown
(1%)"; the correct read is "WAIT — overbought" with trigger "WAIT for RSI < 55
+ 8-12% pullback"). The tests in `test_when_to_enter.py` pin this priority.

Every number in the rendered report is real (RSI / IV / drawdown / 5d /
trend% / CSP entry quote — all from the scout cache); no hardcoded thresholds
in the output. RSI bands match `briefing.yaml` → `rsi_discipline`. Like the
candidate report, it reuses the scout's existing research (no extra fetches).

## Challenge every recommendation — multi-perspective (never relay at face value)

Every actionable recommendation must be stress-tested from multiple
perspectives before it's surfaced — the briefing presents the counter-case, not
just the case. This is a hard standard, prompted by a real miss (2026-05-22):
the action list surfaced "EXECUTE ROLL" on an SMH covered call (a max-credit,
911-day, same-strike calendar roll) while that position's OWN wheel-roll advisor
recommended HOLD — which would cap a bullish semis ETF at $595 for 2.5 years
just to harvest premium.

Two parts:

1. **Action list must defer to the position's own advisor.** Never surface
   EXECUTE ROLL when the wheel-roll-advisor / ROLL ANALYSIS recommends HOLD
   ("don't roll"). The moneyness / NEAR_ATM trigger flags assignment *risk*; it
   does NOT override an explicit HOLD. When a roll IS warranted, prefer
   roll-UP (higher strike — preserves upside) and a shorter tenor over the
   max-credit, longest-dated, same-strike candidate. Cap roll tenor
   (`roll.max_action_tenor_days`, default 120; core names may go longer).

2. **Challenge / Counterpoint layer.** A deterministic pass critiques every
   actionable rec from all angles and surfaces objections both inline (a
   "⚖️ Counterpoint:" line under each action) and in a consolidated
   "⚖️ Counterpoints / Second Opinion" panel:
   - Consistency — does the action contradict the position's own advisor?
   - Opportunity cost — what upside does it forgo (effective ceiling =
     strike + net premium; break-even vs holding)?
   - Tenor — is the commitment excessively long (capping a name for years)?
   - Tax — does it realize a loss / short-term gain?
   - Concentration / theme risk.
   - Valuation — price vs intrinsic value (FMP DCF + analyst target).
   Source: `scripts/analysis/recommendation_challenger.py`.

## Everything actionable — entry/exit/manage on every datapoint

No information in the briefing may be left merely descriptive. Describing a
state — "overbought", "oversold", "cold", "extended", "in drawdown", "rich IV" —
is not enough; every such state MUST be translated into **what to do**: is it a
good time to ENTER, EXIT, TRIM, WRITE CALLS, HOLD, or WAIT? This applies to the
context/read sections too (Market Pulse, theme reads, watch lines), not just the
order-ticket sections. If a line states a condition, it must also state the
action that condition implies.

The verdict is read **asymmetrically** (consistent with the RSI discipline) and
covers both holding states in one line (entry if you don't own it, manage/exit
if you do):
- **Overbought (RSI ≥ 70):** no new buy/CSP (chasing); if held → WRITE COVERED
  CALLS (rich IV) or TRIM into strength.
- **Extended (60-70):** wait for a pullback to enter; covered calls attractive.
- **Neutral (50-60):** no entry edge; hold/monitor.
- **Pullback (35-50):** favourable CSP/BUY entry; confirm support.
- **Oversold (25-35):** entry favoured but momentum weak; size small.
- **Falling knife (<25):** wait for stabilization; if held, defensive.
- **Deep drawdown (≥30%) without strength:** thesis check — review/trim, not a
  fresh entry.

Implementation: `thematic_research._action_read()` (full sentence) and
`_action_tag()` (compact verb) map a name's (RSI, trend, IV, drawdown) state to
this verdict; the Market Pulse renders `🎬 **Action:**` under every hottest /
cooling name and a `→ <tag>` on every theme-pulse leader. Extend the same
`_action_read` pattern to any new informational surface — a datapoint without an
action read is a bug.

## Test-driven discipline — write a test, run the suite, then ship

The user's explicit rule (2026-06-30): **"add tests everywhere and make sure
to run tests when you make changes. add it to claude.md, we can't be breaking
stuff."**

The rule has three parts, all hard:

1. **Every bug fix gets a regression test before it lands.** No exceptions.
   The test must be the kind that would have failed against the broken code
   and now passes against the fix. "Manual smoke-tested in the browser" is
   not a test. The home for these is `webapp/tests/test_regression_bugs.py`
   for the web app and `tests/` (skill-level) for pipeline bugs. Each test
   has a docstring that quotes the user's symptom verbatim — that way the
   next time the same class of bug shows up, grepping the test suite for
   the symptom finds the prior fix.

2. **Every PR / change touching production code runs the full suite locally
   before the change is considered done.** The contract:
   - Webapp: `cd webapp && python3 -m pytest tests/` — must be 100% green.
   - Pipeline / skills: `python3 -m pytest skills/<name>/scripts/tests/`
     and `python3 -m pytest tests/` (root).
   - Pre-push hook (`pytest-pre-push`) is the backstop, not the primary
     check — the change shouldn't get to the push without passing locally.
   "Tests pass on my machine" does NOT mean "the one test for the new code
   passes" — it means the FULL suite passes. A new test that breaks five
   old ones means the new code broke things, even if the new test is green.

3. **A test added in the same commit as the fix is the only proof the fix
   stuck.** Without it, the next refactor silently regresses the bug and we
   re-debug it from scratch six weeks later (it has happened multiple times
   — see the DuckDB WAL lock + chart-bootstrap script bugs from 2026-06-30).

Categories of test that MUST be present:

- **Route smoke tests** for every webapp endpoint — at minimum `assert
  r.status_code == 200`. The test in `test_regression_bugs.py::
  test_briefing_latest_does_not_500` is the canonical example: the user
  reported a 500, the fix went in, and the test now ensures it can't
  silently come back.
- **Contract tests** for every template that depends on a static asset
  (`/static/app.js`, `/static/app.css`) — see `test_base_template_loads_
  chart_bootstrapper`. Charts went silently empty for the user because a
  `<script>` tag was missing from `base.html`; this test prevents it.
- **Visual / a11y guards** for theme variants — see `test_light_themes_
  pass_wcag_aa_contrast`. The user reported "contrast is great, hard to
  read" (sarcasm) on the light theme; the test now asserts text/bg
  contrast is ≥ 4.5:1 (WCAG AA) for every theme so a token tweak that
  drops below threshold fails CI instead of shipping.
- **Recovery / fail-closed tests** for state corruption — see
  `test_corrupt_duckdb_recovers_on_next_boot`. A bad DB file used to
  500-bomb every route; the test now corrupts the DB and confirms the
  next boot rebuilds transparently.
- **Pipeline parser round-trips** for any new structured-output parser —
  see `test_report_parser_extracts_cards_from_real_fixture`. If the
  pipeline's markdown format drifts, the test fails immediately instead
  of producing empty cards in the UI weeks later.

Don't game the test by writing one that always passes (e.g. asserting
`True` or asserting on the test fixture rather than the production code
path). The test must fail when the bug is reintroduced.

When you add tests for a fix, also clean up duplicates and stale assertions
from earlier rounds — `test_regression_bugs.py` is append-only at the top
level but each test should be the strictest version of itself.

## Hard rules summary (one-liners)

1. **Account scope:** INDIVIDUAL only until further notice.
2. **Chain data:** E*TRADE via `etrade-chain-fetcher`, never yfinance.
3. **Roll trigger:** moneyness (at/past strike), not P&L%.
4. **Core trim:** roll CCs up; never auto-sell.
5. **POOR EV:** filter from new trades; render-with-badge for management.
6. **CC writability:** check per-account, not aggregate.
7. **No order placement:** read-only against brokerages.
8. **Tax framing:** rolling defers tax conditionally — never says "saves" tax.
9. **No directional forecasts:** flag conditions, don't predict prices.
10. **Fail closed:** missing data → suppress action, never fill with defaults.
11. **RSI discipline:** every rec passes `rsi_discipline.hook()` → remove/keep/promote. Show RSI on every rec; block new put-sales & buys >70, new covered calls <35; **mid-range (35-60) covered-call writes are demoted to "⏸ wait for strength" (not READY TO WRITE)**; management never removed. Verifier (`audit_missing_rsi`) enforces RSI coverage.
12. **Intrinsic value:** every single-stock rec shows FMP DCF + analyst-target fair value; ETFs → "n/a — basket"; fail closed (no key / no data → no fabricated number). Source: `intrinsic_value.py`.
13. **Capital Plan new-put gate:** `NEW_CSP`/`LT_CSP` demoted to Skipped when stress coverage < 0.50× or projected concentration ≥ 10% NLV — the plan can't contradict the Red Flags.
14. **Challenge every rec:** action list defers to the position's advisor (never EXECUTE ROLL when the advisor says HOLD; prefer roll-up/shorter-tenor over max-credit); a deterministic counterpoint layer (`recommendation_challenger.py`) surfaces contradictions, opportunity cost, tenor, tax, concentration & valuation — inline + in a panel. Never relay a rec at face value.
15. **Everything actionable:** no datapoint stays descriptive — every state (overbought/oversold/cold/extended/drawdown) maps to an entry/exit/trim/write-calls/hold/wait verdict, read asymmetrically. Market Pulse names carry `🎬 Action:`; theme leaders carry `→ <tag>`. Source: `thematic_research._action_read()` / `_action_tag()`. A datapoint without an action read is a bug.
16. **Covered-call strike = delta-first, real delta only:** new CC writes pick the strike by a delta band (`covered_call.target_delta`, default ~0.25), not a flat %OTM, so strike distance adapts to each name's IV. The SELL line shows the **measured** delta + actual OTM% (`δ n/a` when the chain has no Greeks) — never a hardcoded/fabricated delta. Fall back to %OTM only when deltas are missing. Source: `strategy_upgrades.py::_etrade_call_quote` + `strategy_upgrades_panel.py`.
17. **Candidate Trades are position-aware:** never surface a CSP candidate that duplicates a put the user already holds. The Candidate Trades section cross-checks live short puts (`short_puts_by_ticker` → `existing_short_puts`, from `aggregate.py`, NOT the 24h scout cache); a strike within 5% of a held strike → demoted to "⏸ Already positioned"; a same-name different-strike candidate → kept but flagged as stacking. The scout's own guard (≥2 puts, cache-based) is insufficient — the render-time check is the backstop. Source: `candidate_research.py::render_candidate_briefing`.
18. **Protective long puts cancel new-short-put recs:** if the user holds a LONG put on a name (collar floor / protective put), no new-short-put surface may recommend a short at the same/near strike — that would un-hedge the position. Applies to `Candidate Trades` (via `long_puts_by_ticker` → `existing_long_puts` + `_long_put_cancellation`) and `LT_CSP` (`long_term_opportunities.py` builds `long_puts_by_ticker` and refuses recs with a "would cancel your collar floor" reason). The original "stacking" guard only counted short puts and missed this; selling the same strike as a held long put isn't stacking, it's cancellation (the META bug, where the user held a long $570P as a collar floor and the briefing recommended SELL $570P at the same strike).
19. **No hardcoded boilerplate in actionable output — everything from real verified data:** any number that looks like data MUST come from a real source measured this cycle. No "~6% OTM, ~0.30 delta" baked into a format string, no "extend 4-6 weeks" / "extend 10-15 months" label irrespective of actual DTE, no "4×" hardcoded into roll-candidate descriptions, no `C` hardcoded for put positions. The SOXX bug surfaced this hard rule: candidate C labeled "10-15 months" was actually +791 days (~2.2 years), candidate descriptions hardcoded `4×` regardless of position qty, and `C` regardless of option type. If the value isn't computed from snapshot/chain/position data, it's not data — render `n/a` or omit, never a plausible-looking guess. Tenor phrasing: use `_tenor_phrase(dte_ext)` (returns `+Xd`, `+Xd (~Nw)`, `+Xd (~Nmo)`, or `+Xd (~N.Nyr)`) — always derived from real DTE. Dates: format with year (`Jul 17 '26`, never `0717` MMDD which collides across years). Source: `wheel-roll-advisor/scripts/roll_target.py` (description + notes), `strategy_upgrades.py` + `strategy_upgrades_panel.py` (CC SELL line). Every renderer touched in the future must obey this — a hardcoded number in the output is a bug.
20. **S/R discipline — every single-stock rec carries a support/resistance read; CSP strikes anchor to support, CC strikes anchor to resistance; fail closed when OHLC unavailable.** S/R levels are computed from the same 300d yfinance OHLC already pulled for RSI/SMA (no extra fetch). Two sources: classic daily/weekly/monthly **pivots** + **swing-point clusters** (180d lookback, 2% cluster band, min 2 touches). Confluence (within 2% of 50-SMA / 200-SMA / 52w high/low / fib retracements) boosts strength. Reading is **asymmetric** — support matters for buys/CSPs, resistance matters for CCs/exits — mirroring the RSI discipline. **S/R refines, never overrides, the RSI gate**: a name at support with RSI 75 is still RSI-blocked from new put-sales (hard rule #11). The render path appends `S: $X (200-SMA + Apr swing, 3 touches) · R: $Y` to every Watch-panel equity, LT_ADD/TRIM/SELL, LT_CSP/PULLBACK CSP, and Candidate Trades header via `support_resistance.annotate_briefing` (orchestrator in aggregate.py + run_briefing.py). The When-To-Enter classifier substitutes explicit support prices into trigger text — "WAIT for price into the $238-$245 support zone" replaces "WAIT for 8-12% pullback" — and adds a ✅ confluence badge to ENTRY cards when spot sits at a strong support. Strike anchoring: LT_CSP picks the strongest support within the 7-13% OTM band (advise.py); CC picks the strongest resistance within ±3% of the delta-selected strike (strategy_upgrades.py::_etrade_call_quote), then re-fetches via `quote_contract` so the **measured** delta at the snapped strike is rendered — never a fabricated one. **Strength scoring required** (touches + recency + confluence); `min_strength=1.5` filters out single-touch noise. **Fabricated levels are a bug** — when SR is unavailable, surface nothing rather than a synthesized number. Source: `support_resistance.py` (module), `snapshot_inputs.py::_full_technicals` (wiring), `when_to_enter.py::classify` (trigger refinement), `advise.py::evaluate_options_idea` (CSP anchoring), `strategy_upgrades.py::_etrade_call_quote` (CC anchoring). Verifier: `support_resistance.coverage_stats` emits the "📐 Support / Resistance Coverage" panel.
38. **"No third-party rec" language MUST be accurate — never imply Parkev doesn't cover a name that Parkev actually rates HOLD or SELL.** User (2026-07-03) saw AMAT surface as "🎯 CANDIDATE ⚠ no third-party rec — verify independently" and reasonably assumed the ticker → company mapping was broken (thought AMAT wasn't linking to "Applied Materials"). Investigation showed the mapping is fine — Parkev's cache has `{ticker: "AMAT", name: "Applied Materials", recommendation: "HOLD", tier: 1, conviction: "Low", age_days: 270}`. What went wrong: the scout's INDEPENDENT SETUP branch (hard rule #25) fires whenever the third-party rec is anything OTHER than a BUY variant, and the badge phrasing hard-coded "no third-party rec" — which is technically false when Parkev has HOLD. The disciplined phrasing must match reality: (a) `rec == "HOLD"` → badge says **"Parkev HOLD (not a BUY catalyst) — verify independently"** so the user knows Parkev covers the name but the rating isn't a buy signal; (b) `rec is None` → badge says **"no third-party rec — verify catalyst independently"** so absence is explicit; (c) same discipline applies to the WATCH fallthrough (when RSI/IV don't qualify), which now says "third-party HOLD, technicals don't qualify" instead of "no third-party catalyst." **The company-name → ticker mapping is already handled by the fetcher** (`recommendation-list-fetcher/scripts/shopping_list.py` captures both `ticker` and `name` at parse time, and downstream lookups are all ticker-keyed via `recs_map.get(ticker.upper())`). Do NOT add a name→ticker override layer — the source is already ticker-canonical; adding a mapping layer just introduces drift risk. If a user asks "why isn't X mapped?" the answer is almost always "it IS mapped, but the rec is HOLD/SELL which the scout treats as not-a-BUY-catalyst, and the badge phrasing was the misleading part." Source: `skills/thematic-scout/scripts/scout.py::_verdict` (independent setup branch, HOLD-aware catalyst_note), `candidate_research.py::_format_card` (badge composer). Tests: `test_independent_setup.py::test_hold_rec_gets_precise_badge_not_no_rec`, `::test_no_rec_still_says_no_rec_precisely`, `::test_hold_rec_watch_fallthrough_mentions_hold`.

41. **Universal capacity-gate DEFERRED tag — when `stress_coverage.ratio < capacity_gates.min_coverage_ratio` (default 0.50×), EVERY new-open recommendation surface MUST apply the `⏸ Deferred (capacity gated)` tag.** Applies to new_ideas, long_term_opportunities (LT_CSP / ADD), the action list's PULLBACK CSPs, and sub-lot completion. NEVER hide the rec (hard rule #24 already requires this) — the full ticket renders with the tag and the MEASURED ratio so the user can plan rotations, but it can't read as a green-lit trade. Historical bug (analyst audit 2026-07-03, finding #7): new_ideas correctly blocked new CSPs at 0.16× coverage, yet 4 actionable new short puts (MU + ZS LT_CSPs, AMZN + META PULLBACK_CSPs) shipped via the LTO/actions surfaces with no tag. Fail-open: when no coverage ratio is resolvable, no tag is applied — a rec is never marked capacity-gated on missing data. Source: `analysis/capacity_gate.py::capacity_deferred_tag` (single source of truth; accepts analytics dicts, `GateState`, `StressCoverage`); wired in `long_term_opportunities.py` (via `gate_state=` from run_briefing Step 5.5), `render/panels.py` (PULLBACK CSP, via `analytics`), `strategy_upgrades.py` (sub-lot, via `analytics=`). Tests: `test_capacity_gate.py` (tag below floor on every surface; NO tag at/above floor — the over-application regression; legacy no-arg calls unchanged).

43. **Bug-fix-on-sight — any defect observed in a generated briefing (wrong number, contradictory verdict/action, stale template text, truncation, missing ticket, churn recommendation) is fixed the same session it is observed, with a regression test whose docstring quotes the observed output. Briefing-analysis sessions do not accumulate known-bug lists — the list must be empty at session end. George's rule (2026-07-30): "we need to be fixing all the bugs we see."** Origin story: validating the 2026-07-30 11:54 AM briefing surfaced nine defects in one read, and all nine were fixed that session — (1) HOLD_FOR_BASIS verdicts rendered UNDER actionable debit-roll tickets on QCOM $185P / VRT $280P — the verdict now suppresses the roll ticket like CLOSE_CLEAN does (`render/panels.py::_verdict_hold_basis_lines`, shared `exit_cost.verdict_drives_action` kill switch); (2) VRT $280P filled at ~2:23 PM got a -$2,740 re-roll rec at 2:54 PM — churn guard: no roll rec on a contract opened within `roll.min_position_age_days` (5 trading days) unless a loss-stop/crash guardrail fires (`analysis/churn_guard.py`, ages from prior snapshots' positions.json); (3) every 🚨 URGENT item rendered "Earnings or expiry within ~14d …" — false for MU with earnings 55d away — the Why is now condition-specific (earnings text only when ≤14d measured); (4) LITE $700P recovered to ~breakeven with earnings 12d away and an earnings-blocked roll surfaced NO action — CLOSE INTO RECOVERY now fires (`recovery_close.max_loss_pct`, default -5% of premium; outranks ROLL_DONT_CLOSE — event risk beats premium mechanics); (5) IREN roll paid a $652 debit on $3,700 collateral (18%) — debit-to-collateral cap (`roll.max_debit_pct_of_collateral`, 8%) demotes disproportionate rolls; (6) AVGO $375P at moneyness 1.0297 with measured δ0.10 got an $871 "defensive" roll — measured |δ| < 0.25 now vetoes the roll gate regardless of moneyness (`_short_put_roll_gate_ok`); (7) URGENT titles truncated mid-word ("…at its peak. Th") — `_truncate_at_word`; (8) strike-tested URGENT items carried urgency with NO order — the guardrail path now runs through block #3's roll composition (two-leg combo ticket, credit-window-aware candidate choice, earnings deferral, directive note); (9) user-executed rolls (MU Nov→Dec, META→Oct) detected in the position diff now emit ready-to-paste directive templates in Since Yesterday (`analysis/briefing_diff.py::detect_executed_rolls`) so deliberate decisions stop generating next-day nags. Sources: `render/panels.py`, `analysis/churn_guard.py`, `analysis/exit_cost.py`, `analysis/briefing_diff.py`, `steps/per_option_commentary.py`, `steps/aggregate.py`, `config/briefing.yaml`. Tests: `tests/test_task40_briefing_fixes.py` (26 tests, each docstring quoting the observed output).

44. **Day-color + live-RSI entry timing — CSPs are sold into WEAKNESS (red days, RSI 35-55), CCs are sold into STRENGTH (green days, RSI ≥ 60); every new-open surface enforces this at LIVE prices, and no composed-trade surface may consume candidates upstream of the full gate battery. George's rule (2026-08-03): "we should not be making these mistakes all the time. Selling CSPs on red days with the right RSI. selling CCs on green days with the right RSI."** The asymmetry, stated once and enforced everywhere:
    - **New CSP / put-sale:** favored band RSI 35-55 measured at LIVE spot; RSI > 70 hard block; 60-70 demotes to "⏸ wait for a pullback"; a same-day/2-session up-move > 5% on the underlying disqualifies a new put REGARDLESS of what the snapshot RSI says (the green-day chase guard — the strike would be set against an inflated spot; sell fear, not celebration). Prefer red days, support retests, post-flush first-green-days with IV still elevated.
    - **New covered call:** favored at RSI ≥ 60 with spot at/near tested resistance; RSI < 35 hard block; 35-60 demotes to "⏸ wait for strength"; tier envelopes (rules #29/#34) apply on top and are checked by the render surface itself, not just the validator. Rich DELIVERED premium is required — a breakout day with post-event IV crush (5% ann on MSFT/AMZN 2026-07-31) is NOT a CC day just because RSI is high; on a Tier A compounder's breakout day the correct trade is usually NOTHING.
    - **Live, not snapshot:** any surface whose qualifying RSI is vintage-guard-stale (underlying moved > 5% vs technicals close) must recompute at live spot or exclude the candidate from composed trades (fail-safe: a >5% UP-move on a ≤70 snapshot RSI excludes new puts — the live RSI is plausibly over the block). Historical bugs: MSFT $410P card with pre-gap "RSI 50 ✅" while live RSI was 74 (2026-07-30); AMZN $240P playbook row at live RSI ~80 passing on snapshot RSI 66 (2026-08-03).
    - **Delivered-yield floors:** a premium-selling rec below `playbook_min_annualized_yield` (12%) or `playbook_min_premium_pct_of_collateral` (0.5%) is not income and may not render as actionable or carry conviction stars (AMZN $240P at 4% ann with ⭐⭐⭐ was the bug). IV rank is a realized-vol proxy — post-gap rank ≥ 60 with delivered yield < 20% ann must render the iv-honesty note and forfeits any IV-based conviction bonus (`analysis/iv_honesty.py`).
    - **Composed-trade surfaces run the FULL battery:** the Rotation Playbook (and any future composer) applies `_phase2_gate_battery` — live-RSI hook, vintage check, yield floors, iv-honesty — to every candidate at selection time, because upstream pools (LTO/new-ideas/scout caches) predate the newest gates. Every exclusion renders with its reason (rule #24). Fully-gated planning cards (hard-skip + stale qualifying RSI) drop to the unnumbered "📎 Shown for reference — not actionable today" subsection — a numbered "Trade:" slot means executable today (`lto_reference_demotion`).
    Sources: `analysis/rsi_discipline.py` (bands + hook), `analysis/vintage_guard.py`, `analysis/iv_honesty.py`, `analysis/rotation_playbook.py::_phase2_gate_battery` (+ `_wilder_rsi_live`), `steps/long_term_opportunities.py` (extended-band + reference demotions), `config/briefing.yaml` (`rsi_discipline.put_extended_wait_band`, `vintage_guard`, `playbook_min_*`, `lto_reference_demotion`). Tests: `test_rule43_amzn_iv_and_extended_band.py`, `test_rule43_amzn_playbook_gate_battery.py`, `test_rule43_lto_reference_demotion.py`, `test_vintage_guard` suites.

45. **Universal tenor cap on every rendered STO leg — any surface that composes a Sell-to-Open ticket MUST enforce `max_action_tenor_days` in the SHARED candidate-selection layer AND pass the render-time tenor sweep; a per-surface cap is a bug waiting for the next surface.** George (2026-08-04): "add such details to claude.md. we don't want to have such bugs!" Origin: TAKE PROFIT VIA ROLL-DOWN composed `STO VRT $240P Dec 15 '28` — +700 days, ~6× the 120d Tier C action-list cap — because the composer hunted net-credit and the only credit-positive roll-down candidate on the chain was the longest-dated one. This is the rule-#14 max-credit trap resurfacing through a NEW surface: **any composer that optimizes for credit will, by construction, find the longest tenor** (more days = more premium), so bolting the cap onto individual surfaces (the action list had it; the roll-down composer didn't) guarantees recurrence every time a new ticket-composing surface ships. Hard requirements: (a) the tenor cap lives in the SHARED candidate-selection layer — `max_action_tenor_days` (default 120d non-core, core-union names ×3) filters candidates BEFORE any credit ranking runs, so a max-credit search can only pick among cap-compliant tenors; (b) a render-time sweep — `analysis/tenor_guard.py::ticket_tenor_violations` — scans every rendered STO leg's expiration against the cap and is wired into the verifier, catching any surface that bypassed (a); (c) every FUTURE ticket-composing surface must call BOTH — selection-layer cap at generation, tenor sweep at render. A violation renders the ticket demoted with the measured tenor shown (rule #24 — never silently hidden). Sources: `analysis/tenor_guard.py` (sweep), the roll candidate selection layer (`wheel-roll-advisor` `candidate_ranker.py` `max_tenor_days` + `render/panels.py` block #3/#4 caps), `config/briefing.yaml` → `roll.max_action_tenor_days`. Tests: tenor-guard suite (sweep catches an over-cap STO leg on any surface; core-union ×3 allowance; compliant legs untouched).

46. **Vintage fail-SAFE — a favourable RSI badge (`✅ RSI favourable`, `RSI NN 🟢 pullback`) requires VERIFIED freshness; a missing quote is never license to trust the snapshot.** Origin (2026-08-04 rerun): "PULLBACK CSP PLTR — sell $145P ... RSI 48 🟢 pullback ... Trade-validator: ✅ GOOD TRADE" shipped while PLTR was +29% on the day (spot ~$163 from the E*TRADE positions feed — the SAME number the card's "-11% below spot" used; RSI 48 was computed through YESTERDAY's $125.65 close; real live RSI ≈ 70-75 = hard block). Root cause: only 22/36 symbols got yfinance quotes that cycle; PLTR's quote was missing, so the vintage guard had no drift reference and FAILED OPEN — the stale 🟢 badge and GOOD-TRADE verdict rendered untouched. Hard requirements: (a) **broker-quote fallback** — when the yfinance quote is missing, the drift reference comes from the E*TRADE positions payload (`vintage_guard.broker_price_map`: equity `price`/`lastTrade`, option underlying-price fields); only when NEITHER source exists is the vintage truly unverifiable; (b) **unverifiable → no favourable badge** — a new-open rec whose RSI vintage cannot be verified renders "RSI 48 ⚠ unverified (no live quote this cycle) — do not trust the favourable read" and the hook result downgrades promote → keep+caution; management lines unaffected; (c) **live-RSI recompute where possible** — with a drift reference in hand and the move past the vintage threshold, recompute Wilder's RSI with the live price as the current bar (`vintage_guard.wilder_rsi_live`, same math as the playbook gate battery) and gate on THAT; up-move + uncomputable → hard exclusion for new put opens (rule #44 fail-safe); (d) **validators get the live value** — `pre_trade_validator.build_context_from_snapshot` resolves ctx.rsi through `vintage_guard.resolve_new_open_rsi`, so the RSI rules never bless a new open on the stale snapshot number; (e) **coverage is surfaced** — snapshot provenance records `quotes.requested`/`quotes.fetched`, and the Live-Data policy panel carries the count whenever coverage < 90% (`live-data-policer` `_QUOTE_COVERAGE_MIN`). Sources: `analysis/vintage_guard.py` (`broker_price_map`, `wilder_rsi_live`, `resolve_new_open_rsi`, unverified flags in `compute_flags`/`annotate_briefing`), `render/panels.py` (PULLBACK CSP resolver wiring), `analysis/pre_trade_validator.py::build_context_from_snapshot`, `steps/snapshot_inputs.py` (coverage provenance), `skills/live-data-policer/scripts/police.py` (coverage line). Tests: `test_rule43_pltr_vintage_failsafe.py` (docstrings quote the PLTR card).

47. **Universal RSI vintage resolution + one RSI per name per render — EVERY surface that grades, gates, or displays an RSI on an actionable line resolves it through `vintage_guard.resolve_new_open_rsi` (memoized per cycle), and a single render may never show two different RSI values for the same ticker.** Origin (George, 2026-08-14): "RSI on SNDK is 76 now... What kind of recommendation is this? B (66) SNDK — SELL 1× $1230P … RSI 48 late-band … What kind of weakness are we talking about here?" The same briefing rendered SNDK with THREE RSI values — Best Setups "RSI 48" (24h scout cache), the action-list close card "RSI 56" (morning technicals), live reality ~76 after a +19% two-session rip — because `collect_best_setups` graded its four candidate pools (scout, new_ideas, strategy_upgrades, LT_CSP) without ever consulting the vintage guard, so a hard-blocked name earned a B on a void pre-move number. Rule #46 built the machinery; this rule makes it UNIVERSAL: (a) any pool feeding a graded/actionable surface passes vintage-resolved RSI into the scorer (`grade_for_new_open(rsi=live)`); drift >5% with live RSI computable → grade on the live value (hard block >70 → visible ⏸ exclusion naming the move, e.g. "live RSI 76 hard block (spot +18.9%; pre-move RSI 56 is void)"); stale up-move or unverifiable → grade caps below the B floor with "RSI unverified this cycle" — never A/B on unverified RSI; fresh → unchanged (fail-open, rule #19); (b) same-render one-voice: the RSI consistency sweep (`price_consistency.rsi_mentions`/`rsi_disagreements`, tolerance 1.0 pt, "📉 RSI Consistency Check" panel) flags any ticker whose actionable surfaces disagree — band thresholds, footers, tables, and vintage-disclaimed reads exempt; (c) a NEW surface that grades entries MUST use the same per-cycle memoized resolution — a fresh pool with its own RSI source is this bug reborn. Sources: `analysis/setup_grade.py::collect_best_setups` (+`_cap_unverified_grade`), `analysis/price_consistency.py` (RSI sweep), `steps/aggregate.py` (wiring). Tests: `test_rule46_sndk_spotlight_vintage.py` (15 tests, docstrings quote George's message).

48. **THE ENTRY ALGORITHM — every new-open recommendation passes the canonical six-step evaluator (data freshness → hard blocks → payment floors → setup grade → B floor → book gates); no surface may green-light a ticket the evaluator rejects (conformance panel enforces); a new surface MUST call `evaluate_entry`, never re-derive the steps.** George (2026-08-14): "Let's make sure we definitely encode this in the recommendation: the exact algorithm that ensures that the entry is as good as possible. I definitely don't want a coin-flip algorithm. It really needs to work, so use all the right steps to validate entries." Origin: the SNDK sequence proved two things at once — a D-entry-that-happened-to-win is NOT validation (outcome luck is not process), and stale data is THE vector: the same render carried "B (66) SNDK — SELL 1× $1230P … RSI 48 late-band" while the live RSI was ~76 (a hard block), the user HELD that exact put, action #1 was CLOSE it, and the name sat at 11.2% of NLV against its 8% Tier C cap. Every one of those checks already existed as a single source of truth; what did NOT exist was one CONDUCTOR guaranteeing every surface runs all of them, in order, on resolved data. `analysis/entry_algorithm.py::evaluate_entry(side, ticker, strike, expiration, premium_mid, snapshot_data, analytics, config, positions=..., action_close_idents=...)` ORCHESTRATES (never reimplements) the canonical modules in this exact order: STEP 1 `vintage_guard.resolve_new_open_rsi` (rule #47; unverifiable up-move on a put side → BLOCKED, rule #44 fail-safe); STEP 2 ordered hard blocks — `rsi_discipline.hook` (index-CC thresholds via `position_tiers`), `earnings_guard.check_earnings_conflict` (unknown date → WARN per `earnings_unknown`), `put_overlap_check` (#40), `position_tiers.projected_name_concentration` (#16 obligation-inclusive math), `lt_verdict_gate` (#39; CC side → secular-uptrend wait), the config tail-risk list, the new-open tenor cap (`roll.max_action_tenor_days`, core ×3 — #45), and the closing-today check (#43); STEP 3 delivered-yield floors (12% ann / 0.5% collateral, #44) + `iv_honesty` + the labeled vol source (`chain_iv.effective_iv`); STEP 4 `setup_grade` on the RESOLVED inputs (unverified vintage → grade capped, never A/B — #46); STEP 5 `setup_grade.below_actionable_floor` → WAIT with the graded reason; STEP 6 `capacity_gate` (#41) → WAIT with the measured ratio + `sector_exposure` context (annotation, never a block). Output: `EntryDecision {verdict ENTER|WAIT|BLOCKED, grade, ordered_reasons (EVERY step's finding, pass or fail — auditable), one_line}`; a multi-failure ticket reports the FIRST hard block as primary. Deterministic and pure; fail directions inherited from the underlying single sources (rule #19 — missing NLV / missing RSI-with-no-drift / missing premium never block). ENFORCEMENT: `entry_algorithm.audit_conformance` re-runs the evaluator over every green-lit (non-⏸/⛔) new-open ticket in the RENDERED briefing and flags any ticket the algorithm would not mark ENTER — the "🧮 Entry Algorithm Conformance" panel in `steps/aggregate.py` (fail-open try/except; zero flags on a healthy render) — so no surface can green-light a ticket the algorithm rejects, even before it is rewired. Wired as the ACTUAL decision path on the two highest-traffic surfaces: `candidate_research.render_candidate_briefing` (a BLOCKED verdict demotes the card to the visible "⛔ Held back by the entry algorithm" bucket, rule #24) and `setup_grade.collect_best_setups` (a BLOCKED pooled entry moves to the visible exclusions before ranking); other surfaces keep their existing wiring under the conformance audit. Sources: `analysis/entry_algorithm.py` (evaluator + conformance), `steps/aggregate.py` (panel), `steps/candidate_research.py` (decision wiring), `analysis/setup_grade.py::collect_best_setups` (decision wiring). Tests: `tests/test_entry_algorithm.py` (34 tests — step ordering, every step's block/wait/enter path, conformance flag/silence, candidate + Best-Setups parity incl. the SNDK RSI-76 and 11%-cap cases, fail-open directions, determinism; docstrings quote George's directive).

42. **Side-aware roll candidates — a defensive short-PUT roll is same-strike OUT or DOWN-and-out, ranked by RISK REDUCTION; max-credit ranking and roll-UP candidates are CALL-side only.** Historical bug (2026-07-29, market down hard): the ROLL ANALYSIS tables "✅ recommended" max-credit roll-UPs on six underwater short puts — NVDA $200P (spot $190, $10 ITM) → "$250P +$4,182" ($60 ITM), VRT $290P (spot $223) → "$340P +$5,308" ($117 ITM), MU $950P (spot $739) → "$1000P" ($261 ITM) — each carrying covered-call language ("raises the cap, preserves more upside"). On a short put a HIGHER strike = deeper ITM = MORE assignment risk and MORE capital at risk; the max-credit candidate is by construction the deepest-ITM one (the SOXX rule-#14 bug, put-side edition). Hard requirements: (a) **generation** (`roll_target.py::enumerate_roll_candidates`) offers puts only same-strike-out and roll-DOWN-and-out candidates (chain-snapped strikes at ~1-2 increments below, honest net math — roll-downs often cost a small debit and the table says so truthfully); a put roll-up is generated ONLY behind `include_bullish_roll_up: true` (default false) and labeled "⚠ BULLISH repair — increases assignment risk & obligation"; (b) **ranking** (`candidate_ranker.py::rank_candidates(option_type=)`) for puts scores strike reduction first, credit second (debit allowed only when the reduction is ≥5% of spot), shorter tenor as the tiebreak — and NEVER picks a strike above the current one, even if enumerated; (c) **descriptions are side-aware** — "Lower strike (−$X) … reduces assignment risk and obligation by $Y" on put rows, never call language; (d) **every ranker call site passes the side** — `advise.py` (recommendedCandidateId), `render/panels.py` block #3 (incl. the ImportError fallback stub). `option_type` defaults to "CALL" so call-side income rolls (max credit + roll-up bonus, tenor-capped) are byte-identical. Tests: `skills/wheel-roll-advisor/scripts/tests/test_put_defensive_rolls.py` (7 tests: no roll-up by default, roll-downs generated with honest debits, side-aware wording, flag-gated bullish repair, reduction-over-credit ranking, call-side regression guard, advise() end-to-end recommended-id).

40. **Universal 5% strike-overlap check on new short puts — every generator emitting a new short-put recommendation (LT_CSP / LONG_DATED_CSP, PULLBACK_CSP) must reject strikes within 5% of an existing held short put on the same underlying.** A near-strike duplicate is the same trade, not diversification — same assignment zone, doubled single-name risk. Historical bug (analyst audit 2026-07-03, finding #4): the check existed only inline in the LT_CSP path, so PULLBACK_CSP bypassed it and the briefing recommended AMZN $215P while the user held AMZN $225P Aug 21 — 4.4% apart, inside the band that made the LT_CSP path skip AMZN the SAME day ("concentrates rather than diversifies"). The put-stack guard (≥2 held puts) is NOT sufficient — a single held put with an overlapping strike must also block. Source: `analysis/put_overlap_check.py::check_strike_overlap` (single source of truth, 5% band measured against the HELD strike, accepts strike lists / dicts / per-ticker maps); wired in `long_term_opportunities.py` (replaced the inline check), `render/panels.py` (PULLBACK CSP, fires before the wash-sale/earnings checks), and enforced late-stage by `pre_trade_validator.py` Rule 15 `PUT_STRIKE_OVERLAP` (BLOCK). Tests: `test_put_overlap_check.py` (the AMZN 215/225 case on every surface, all caller shapes, calls/closes exempt).

39. **Long-term-verdict discipline gate — no new-open recommendation (CSP, ADD, BUY, sub-lot) may fire on a name whose `long_term_verdict ∈ {broken, downtrend, weakening}` AND spot < 200-SMA.** Origin: analyst audit 2026-07-03 (task #14) — the pipeline computed `long_term_verdict` on every ticker but never consumed it when generating new-open recs, so RSI-favorable + Parkev BUY was enough to ship ZS (LT `broken`, -28% below a falling 200-SMA, -56% drawdown) as BOTH a $135P LT_CSP and a $5K ADD, plus a SOFI ADD (LT `broken`). RSI-favorable ≠ trend-favorable. **Override:** a Parkev rec with `tier ≥ 4` (Top 12-15 Stock / Top Stock) AND `age_days ≤ 14` (fresh, not aged) AND a BUY variant overrides the gate — the META case (LT `downtrend` but tier-5 STRONG_BUY 2d old) stays actionable, but ALWAYS with a visible `⚠ LT-trend note` annotation so the contradiction is never silent. Blocked recs are demoted (kind `SKIPPED_LT_VERDICT`; PULLBACK CSPs to the transparency footer; sub-lots to the deferred subsection), never hidden (rule #24). Fail-open on missing deep tech data — never block on missing data. Companion check: a NEW covered call on a measured LT `secular-uptrend` chart is demoted to the wait-for-strength list (`lt_secular_wait`) — belt-and-suspenders for non-Tier-A compounders. The MU sub-lot case (audit #5) is covered by extending the has-CSP deferral (rule #22) to the sub-lot path: an open short put IS the entry mechanism, no equity double-tap. Source: `analysis/lt_verdict_gate.py::check_lt_verdict_gate` (+ `cc_secular_uptrend_wait`); wired into `long_term_opportunities.py`, `render/panels.py` (PULLBACK CSP), `strategy_upgrades.py` (sub-lot + CC). Tests: `test_lt_verdict_gate.py` (ZS ×2, SOFI tier-3-fresh-no-override, META tier-5 override + annotation, MU has-CSP sub-lot, fail-open).

37. **LLM review is a REVIEW, not a RECOMMENDER — deterministic rules stay source of truth.** User (2026-07-01) wanted an LLM second-opinion pass over the finished briefing to catch cross-section patterns the rules structurally miss (thematic risks, contradictions between sections, stale-vs-current inconsistencies). Implementation: `skills/daily-portfolio-briefing/scripts/analysis/fable_review.py` runs AFTER all deterministic rules have shaped the briefing markdown. Fires at the end of `aggregate_briefing()`. Hard constraints in the system prompt: (a) observations ONLY — no trade recommendations, (b) quote numbers directly from the briefing — never restate or approximate, (c) fixed 4-section output format (Cross-section observations / Themes I notice / Contradictions or stale items / One thing that would improve the book), (d) 350 words max. Default model: `claude-opus-4-6` because the whole point is REASONING (Haiku is fine for classification, Opus is where synthesis lives; ~$0.43/run = ~$155/year on daily cadence). Toggle via `briefing.yaml → fable_review.enabled: true` — OFF by default. Fail-open: any API error / timeout / rate-limit → briefing still ships with `_🔍 Fable review unavailable this cycle_` placeholder. Every run cached to `<snapshot_dir>/fable_review.json` for audit trail. Position at BOTTOM of markdown (not top) so it never competes with rule-based sections for attention. Tests: `test_fable_review.py` (14 tests pinning disabled/no-key/empty/success/error/cache paths, all with mocked Anthropic client). Telegram bot pulls the review section via `_extract_fable_section()` and sends as follow-up message. **Never let a future refactor turn this into a recommender — the discipline is: rules for correctness, LLM for pattern-recognition on top.**

36. **Smart CSP take-profit — three-layer guardrail: hard ceiling, gamma escape, time-adjusted early close.** User feedback (2026-07-01): "closing at 32% is wasteful, optimize for more gains without crazy risk." The old matrix cells fired CLOSE_FOR_PROFIT at 25-50% depending on cell, which under-captured on slow trades AND held too long on fast ones. Fix: pre-matrix guardrail in `skills/wheel-roll-advisor/scripts/guardrails.py::check_smart_take_profit` layered in this order (first hit wins): (a) HARD CEILING — profit ≥ 85% → CLOSE regardless (remaining premium not worth gamma risk); (b) GAMMA ESCAPE — DTE ≤ 10 AND profit ≥ 30% → CLOSE (lock the win before last-week gamma zone eats it); (c) TIME-ADJUSTED — profit ≥ 2 × (days_elapsed / initial_dte) × 100 AND profit ≥ 30% AND days_elapsed ≥ 3 → CLOSE (rewards fast winners, waits for slow ones). All emit `matrix_cell` = `GUARDRAIL_HARD_CEILING` / `GUARDRAIL_GAMMA_ESCAPE` / `GUARDRAIL_TIME_ADJUSTED` for auditability. Matrix cells raised in parallel: `PUT_NORMAL_DEEP_OTM_MID_LONG_DTE` and `PUT_NORMAL_MOD_OTM_TAKEPROFIT` bumped from 50% → 65% floor (wheel-strategy 65-85% sweet spot); `PUT_NORMAL_MOD_OTM_SHORT_DTE` from 40% → 55%. Config lives in `wheel_parameters.yaml::smart_take_profit` (hard_ceiling_pct=0.85, gamma_escape_dte=10, time_adjusted_multiplier=2.0, etc.). Tests in `test_guardrails.py::test_smart_tp_*` (8 tests pinning each layer + the "32% slow trade stays HOLD" case). Origin: user (2026-07-01) — "optimize for gains but don't want crazy risk."

35. **Tier A ENGINEERED CC mode — smart middle ground when strict RSI ≥ 75 gate blocks.** User pushback (2026-07-01): "we should be willing to write CC if the setup is really good — even for MSFT / META." The strict Tier A envelope (RSI ≥ 75) is too binary: at RSI 43 with 30% drawdown + IV rank 89, there IS a smart rebound-proof write that collects real premium without capping the recovery. Rule: config `covered_call_tiers.tier_a.engineered.enabled: true` unlocks a second gate that fires when (a) RSI ≥ 40 (not falling knife) AND (b) drawdown ≥ 10% (rebound premium in IV) AND (c) IV rank ≥ 70 (premium worth trading) AND (d) strike ≥ max(spot × 1.13, 200-SMA) so a full snapback to the 200-day rebound target doesn't touch it AND (e) delta ≤ 0.15 AND (f) DTE ≤ 21 (short-gamma window) AND (g) coverage ≤ 20% (80% of position keeps FULL upside for the compounder thesis). Optional ultra-conservative extra: `min_strike_above_analyst_pt: true` pushes strike above the FMP 12-month analyst PT — collects pennies but genuinely cannot be capped by any reasonable rebound; off by default because the 200-SMA gate + 21-DTE window already give rebound-proof strikes with real premium. The rationale surfaces "rebound-proof above 200-SMA $X" (and "bonus: above analyst PT $Y" when the strike happens to clear it). Source: `analysis/position_tiers.py::engineered_cc_eligible(ticker, config, spot, rsi, iv_rank, drawdown_pct, sma_200, analyst_pt)`. Tests: `test_position_tiers.py::test_engineered_cc_msft_today_fires` pins the MSFT $445 case; `test_engineered_cc_ultra_conservative_mode_pushes_above_analyst_pt` pins the opt-in override.

34. **Tier A conservative-CC opt-in — per-name whitelist, strict envelope.** Default: Tier A core compounders NEVER get CC recommendations (protect the long-term compounding — capping NVDA at $250 for 30-day theta on a name compounding 40%/yr is anti-strategy). But the user can OPT IN for specific Tier A names under a STRICT envelope. Config: `covered_call_tiers.tier_a.willing_to_write_cc_on: [NVDA, MSFT]` — those names are eligible, other Tier A names stay CC-free. The envelope for these opt-in writes is deliberately punitive (RSI ≥ 75, ≥20% OTM, δ ≤ 0.10, ≤20% coverage of shares, ≤30 DTE, `tax_aware_assignment_block: true`) so the CC only fires when the name is genuinely stretched and the write is a low-probability cap. User's rule: "I want to be able to write a good CC if it makes sense." Source: `analysis/position_tiers.py::is_cc_enabled_for_tier(tier, config, ticker)` — the ticker parameter is what turns on the per-name check; existing callers passing only `(tier, config)` see the default disabled state (backward compatible). Tests: `test_position_tiers.py::test_tier_a_cc_opt_in_per_name`, `test_tier_a_cc_disabled_by_default`. Origin: user (2026-06-30) — "let's also rethink TIER A. I want to be able to write a good CC if it makes sense."

33. **Test-driven discipline (TDD + full-suite-green):** every bug fix ships with a regression test in `webapp/tests/test_regression_bugs.py` (or the appropriate skill-level `tests/`). The test docstring quotes the user's symptom verbatim. The full pytest suite (`cd webapp && python3 -m pytest tests/`) MUST be 100% green before any change is considered done — a new test that breaks five old tests means the new code is broken, even if the new test is green. "Manually clicked it in the browser" is NOT a test. Categories that MUST exist: route smoke (`status_code == 200`), template-asset contract (every `<script src>` referenced is loaded), theme contrast (WCAG AA ≥ 4.5:1 on tertiary text), state-corruption recovery (DuckDB / config / cache), pipeline parser round-trips. See the full section above. Origin: user (2026-06-30) — "add tests everywhere... we can't be breaking stuff." Backstop: pre-push `pytest-pre-push` hook; primary check is local.

21. **Expiration-bucket concentration is a separate red flag from stress coverage.** A single-Friday short-put obligation ≥ 30% NLV fires `CRITICAL` (⚠️ alert in Risk Alerts + CRITICAL flag in Red Flags & Priorities). ≥ 20% NLV fires `WARNING` (📊 alert + MEDIUM flag). ≥ 10% NLV is informational (no alert, ladder panel only). This metric is **orthogonal** to (a) the static stress-coverage ratio (which assumes ALL puts assign at once — a fantasy on a well-laddered book and overstates real risk) and (b) the per-name 10% concentration cap (which is single-ticker, this is single-date). The user pushed back on a 0.02× stress-coverage scare with "but my puts are well distributed across expirations" and they were right — distribution by date is the realistic time-weighted risk; this flag captures it explicitly. Source: `analysis/expiration_ladder.py::analyze_put_buckets` (separate from the legacy `analyze_expiration_ladder` which counts puts+calls together). Wired into `Risk Alerts` via `render_risk_alerts(put_buckets=...)` and into `Red Flags & Priorities` via `red_flags.py`. The flag includes the top 5 names in the bucket so the user can identify which positions to close/roll to de-concentrate. Config in `briefing.yaml` → `expiration_bucket`: `critical_pct` (default 0.30), `warning_pct` (default 0.20), `info_pct` (default 0.10).
32. **Ideas + opportunities MUST be visible in the web app, even when capacity-gated — extends hard rule #24 specifically to the dashboard.** The "Ideas & Candidates" tab on `/briefing/{date}` MUST never show ONLY a capacity-blocked placeholder. When `briefing.new_ideas` is empty or contains just the `capacity_gates_blocked` placeholder, the rendering layer MUST merge in actionable kinds from `briefing.long_term_opportunities` (`LONG_DATED_CSP`, `BUY`, `ADD`, `LT_ADD`, `PULLBACK_CSP`) AND the deferred / skipped kinds (`DEFERRED_ADD_HAS_CSP`, `SKIPPED_ADD`, `SKIPPED_LT_CSP`, `SKIPPED_RSI`). Each merged row carries an explicit `status` chip (`actionable` / `capacity_blocked` / `deferred` / `skipped`) and a human-readable label so the user can see WHY an opportunity is gated rather than have it hidden. This is the same lesson as hard rule #24 ("never hide opportunities — surface them as DEFERRED, not suppressed") applied to the web surface: hiding ideas behind a "blocked" placeholder removes the user's ability to decide whether to ROTATE existing positions to make room. The user (2026-06-30): "the New Ideas tab shows only one 'capacity blocked' row, but there are 28 actual ideas in long_term_opportunities — surface them." Source: `webapp/app/ideas.py::build_merged_ideas` (the merger), `webapp/app/templates/briefing.html` (the tab, labeled "💎 Ideas & Candidates"), `webapp/app/main.py::briefing_detail` (computes `merged_ideas` from the briefing model and passes it into the template). Tests: `webapp/tests/test_ideas_merge.py` pins (a) that LTO tickers appear in the rendered HTML when `new_ideas` has only a placeholder, (b) ordering (actionable → deferred → blocked/skipped), (c) `EXIT` / `TRIM` / `_FUNDING_HINT` are NOT surfaced as ideas (they're position management / capital plan notes). When the pipeline adds a new opportunity kind that should be surfaced as an idea, add it to `IDEA_OPPORTUNITY_KINDS` (or `DEFERRED_OPPORTUNITY_KINDS` if it's a gated variant).
31. **Machine identifiers MUST be humanized before display in the web app — never leak `tier_a_no_cc` / `DEFERRED_ADD_HAS_CSP` / `SKIPPED_RSI` as bare text.** The pipeline writes opportunity kinds and strategy-upgrade types in machine form (`snake_case` and `SCREAMING_SNAKE`) because they're keys in code. The web app's Jinja templates MUST pass every user-visible occurrence through the `humanize_action` filter (or `action_with_icon`, which combines icon + humanize) so `tier_a_no_cc` renders as **"🟢 Tier A — no CC"**, `DEFERRED_ADD_HAS_CSP` as **"Deferred (held put)"**, `SKIPPED_RSI` as **"Skipped — RSI gate"**. Bare `{{ x.kind }}` or `{{ x.type }}` on any pipeline-emitted identifier is a leak — fix the template, don't fix the pipeline. The user (2026-06-30): "the Ideas tab shows `tier_a_no_cc` as raw text — make it readable." Source: `webapp/app/icons.py::humanize_action` (the label table) + `_ACTION_LABELS` mapping. **When the pipeline adds a new action / opportunity / strategy-upgrade type**, add an entry to `_ACTION_LABELS` BEFORE merging the pipeline change — otherwise the web app falls back to the generic snake-case-to-Title-Case conversion which loses domain meaning (e.g. `lt_csp` → "Lt Csp" instead of "LT CSP"). Visual test: open any web-app page, search the rendered HTML body text (not attribute values) for underscore-separated lowercase tokens — finding any is a leak. Tests: `webapp/tests/test_ideas_merge.py::test_humanize_action_no_raw_machine_identifiers` pins this contract against the merged Ideas tab.
30. **No raw markdown literals in the web app UI — every summary / detail / description string MUST be styled, never rendered as-is.** The user reported (2026-06-30) seeing `**CLOSE** AMD_PUT_420_20261218 — +51% ...` in the dashboard's "Today's action queue" — the `**CLOSE**` rendered as literal asterisks because the template was using `{{ a.summary }}` instead of a styling filter. **This is a hard rule for every renderer in `webapp/`**: any text that comes from the pipeline's briefing JSON / markdown (action summaries, opportunity notes, watch-row details, search results, ticker drill-down mentions, diff cells, etc.) MUST pass through one of these Jinja filters:
    - **`{{ text | summary_html(kind, ident) }}`** — for action summaries. Strips the redundant `**VERB** IDENT — ` prefix (since the badge above already shows verb + ident) AND renders inline markdown (`**bold**`, `\`code\``, `*italic*`) as proper HTML.
    - **`{{ text | inline_md }}`** — for descriptions / details / search results where the verb+ident badge isn't shown above. Just converts the markdown to HTML without stripping anything.
    - **`{{ verb | action_with_icon }}`** — for the action verb itself when rendered inline (returns `"🔚 CLOSE"`); pairs with `{{ verb | action_icon }}` for just the glyph.
    All three filters output `markupsafe.Markup` (HTML-safe; pre-escapes raw input then re-inserts approved patterns as tags — XSS-safe). Source: `webapp/app/inline_md.py` (helpers), `webapp/app/icons.py` (action verb icons), `webapp/app/main.py` (filter registration). **When you add a new template surface that displays pipeline-generated text, you MUST use one of these filters** — never use bare `{{ text }}` on strings that may contain markdown. The visual test: open any page, search for any literal `**`, `` ` ``, or `*` character pair. If you find any, that's a leak — fix the template. **Why this matters:** the pipeline's markdown briefings ship to `~/Documents/briefings/*.md` where markdown rendering is the user's terminal/editor's job. The web app is a different surface — it MUST render those same strings as proper HTML or strip markdown that's redundant with the surrounding visual structure (icons, badges, chips). The two surfaces share data, NOT presentation.
29. **Position tier framework — every holding is classified A/B/C and that tier gates covered-call writes + concentration caps. Tier A core compounders get NO CC recommendations EVER; concentration cap raised to ~22% (capping a conviction compounder at 10% defeats the long-term thesis).**

(legacy rule renumbering: the position tier framework was rule #29 in this file's prior revision; rule #30 inserts ahead of it. Rule numbers will be re-sorted in the next consolidation pass.)

28. **Parkev's recommendation list MUST be fetched fresh every briefing run — no exceptions, no caching beyond the same-run scope.** The user's explicit rule (2026-06-29): "you should be fetching it daily." The fetcher (`skills/recommendation-list-fetcher`) is called unconditionally in `run_briefing.py` Step 1.6 (`fetch_recommendations(snapshot_dir)`) without any `--refresh` flag — there is no "skip if cached" branch. Parkev updates the sheet at varying cadences (some names weekly, some monthly, the 2026-06-24 update added the Conviction Level column with no warning), so any cache that's even a day old can silently desynchronize the entire briefing's catalyst layer. **`max_age_days` in `recommendation_list_config.yaml` is 365 (one year), NOT a freshness gate — it's a sanity bound to drop multi-year-old entries.** The actual freshness signal is the `⏰` clock icon on the Parkev chip (rule #27): anything >14d shows the clock so the user judges staleness themselves. The previous 30-day archive cap silently dropped 82% of Parkev's coverage (Jun 2026: 247 sheet entries, only 45 passed the cap; ARM at 45d was a known false negative caught by the user). **If you ever see fewer than ~150 entries from the fetcher, the freshness config has been re-tightened — fix it back to 365.** Source: `skills/recommendation-list-fetcher/config/recommendation_list_config.yaml::freshness.max_age_days`, `skills/recommendation-list-fetcher/scripts/shopping_list.py::_parse_csv_rows`, `skills/daily-portfolio-briefing/scripts/run_briefing.py` Step 1.6.
27. **EVERY ticker-specific line in the briefing carries a unified `🅿️ Parkev chip` showing rating + conviction + age — NO EXCEPTIONS. Tickers Parkev doesn't rate render `🅿️ no rec` so absence is explicit, never silent.** The user needs to see Parkev's stance at a glance on every line we discuss, without scrolling to a separate panel. Format (single source of truth in `analysis/parkev_chip.py::format_parkev_chip`):
    `🅿️ {RATING} · {CONV-CHIP} · {AGE}`
    - **🅿️** = unambiguous Parkev attribution marker
    - **RATING** in caps: `TOP STOCK` (tier 5) · `TOP 12` / `TOP 15` / `TOP 25` (tier 4) · `BUY` (tier 3) · `BDL BUY` (tier 2) · `HOLD` (tier 1) · `SELL` (tier 0) · `no rec` (not in sheet)
    - **CONV-CHIP**: `🔥 High` · `◐ Med` · `▽ Low` — omitted entirely when `conviction` is None (legacy rows pre-2026-06-24)
    - **AGE**: `Nd` normally, `⏰ Nd` when stale (`aging` flag True or age > 14d)
    Examples:
    - `🅿️ TOP 12 · 🔥 High · 8d` — strongest signal (tier 4 + High, fresh)
    - `🅿️ BUY · ◐ Med · 12d` — standard tier-3 buy
    - `🅿️ HOLD · ▽ Low · ⏰ 22d` — stale soft hold
    - `🅿️ SELL · 🔥 High · 3d` — rare confidently bearish call
    - `🅿️ no rec` — ticker not in Parkev's sheet (ETFs, off-list names)
    **Where it appears:** EVERY top-level header line that mentions a single ticker — action list items, watch panel rows, capital plan bullets, candidate cards, long-term opportunity cards, strategy upgrade headers. **Where it does NOT appear:** sub-bullets, S/R continuation lines (`↳`), `Source:`/`Why:`/`Earnings check:`/`Wash-sale check:` rows, prose paragraphs, table rows, section headers (`##`). **Implementation:** single post-process pass in `aggregate.py` via `annotate_parkev_chips(md, recs_map)` — mirrors the `rsi_discipline.annotate_action_lines` and `intrinsic_value.annotate_intrinsic` patterns. **Fail-closed:** missing/unknown rating renders `🅿️ no rec` rather than fabricating a value (hard rule #19). **Never double-annotate:** lines that already contain the `🅿️` marker (e.g., Watch rows populated by `review_equities`) are left untouched. **Why this matters:** the previous state had Parkev info scattered inconsistently — Watch had `Third-party: Top 12 Stock (tier 4, 8d old)`, Candidate cards had `🏆 TOP CONVICTION`, Capital Plan had nothing, Action List was hit-or-miss. The unified chip makes the briefing scannable: you can scan any actionable line and immediately know what Parkev says without context-switching. The 2026-06-24 column-shift bug (rule #26) made the OLD ad-hoc format especially dangerous — different sections were reading different sources and showing contradictory ratings on the same name (the MU "Top 12 Stock" in Watch vs "HOLD" in LT_CSP that caught task #12). Single chip = single source of truth.
26. **Conviction Level modulates every third-party-driven signal — it is a separate dimension from the rating tier.** Parkev's sheet (2026-06-24 update) added a `Conviction Level` column at C, with values `High` / `Medium` / `Low`. The fetcher normalizes those to `conviction` (label) + `conviction_score` (3/2/1). Conviction is propagated through `ScoutResult.conviction`, the When-To-Enter classifier, and the Candidate Trades renderer. **The mental model:** rating = WHAT the analyst thinks (Buy/Hold/Sell with sub-tiers); conviction = HOW STRONGLY they're willing to bet on it. A `Buy + Low` and a `Buy + High` are not the same signal — the system must distinguish them. **Promotion / demotion rules** (in `when_to_enter.classify`, mirrored in `candidate_research._format_card`):
    - **tier ≥4 + High** → `🏆 TOP CONVICTION — ENTRY NOW`, sizing **1/2 of target weight is reasonable**
    - **tier ≥4 + Medium** → `🌟 STRONG BUY — ENTRY NOW`, sizing 1/2 (standard tier-4)
    - **tier ≥4 + Low** → label drops the 🏆/🌟 promotion entirely; conviction overrides the tier (the analyst's not pounding the table — treat like a tier-3 Buy)
    - **tier 3 + High** → `🟢 ENTRY NOW — BUY · 🔥 high conviction`, sizing 1/3 (selective)
    - **tier 3 + Medium** or **None** → `🟢 ENTRY NOW — BUY`, sizing 1/3 (legacy default)
    - **tier 3 + Low** → `🟡 TRIAL — BUY (low conviction)`, sizing **1/6 — trial only**
    - **Sell + High** → confidently bearish call, escalate to immediate review (rare — 1 of 52 names in the current sheet)
    **Fail-closed:** when conviction is None (missing/unknown), every branch falls back to the legacy tier-only behavior so the old contract is unchanged. **Never fabricate a conviction** — empty cells and unrecognized values both normalize to `None`. The **column shift the 2026-06-24 update introduced** (date_updated D, price targets E/G) is encoded in `config/recommendation_list_config.yaml`; if you ever see `age_days=0` on most recs, the old config got re-applied and date parsing is silently failing on conviction strings. Source: `skills/recommendation-list-fetcher/scripts/shopping_list.py::_parse_conviction`, `skills/thematic-scout/scripts/scout.py::_research_ticker`, `skills/daily-portfolio-briefing/scripts/steps/when_to_enter.py::classify`, `candidate_research.py::_format_card`.
25. **NEVER require a third-party rec to surface a tradeable CSP setup — Parkev's sheet is ONE catalyst source, not the only one.** The user's explicit rule (2026-06-16): "I have other places to validate recommendations against." When a name's technicals qualify on their own (RSI 35-55 + IV rank ≥ 50, with the AVOID branch already vetoing broken-thesis drawdowns >30% and SELL/UNDERPERFORM recs), the scout MUST produce a tradeable verdict ("CSP ENTRY (independent setup)") with a live CSP entry quote, instead of demoting to WATCH with "no third-party catalyst." Real cases this caught: AAOI (RSI 50, IV 76, dd 22%), RKLB (RSI 46, IV 89, dd 30%), AA (RSI 40, IV 99, dd 25%), LMT (RSI 52, IV 61, dd 21%), ETN (RSI 52, IV 99, dd 6%), VRT (RSI 50, IV 67, dd 16%), GEV (RSI 49, IV 62, dd 16%), CCJ (RSI 48, IV 84, dd 21%) — all previously demoted to "WATCH — neutral" because Parkev doesn't rate them, all now tradeable CSP candidates. The IV ≥ 50 gate keeps thin-premium speculative trades out (BE at IV 32 stays NEUTRAL). Renderers MUST flag these distinctly: `candidate_research._format_card` adds a "⚠ no third-party rec — verify independently" badge; `when_to_enter.classify` uses label "🟢 ENTRY NOW — CSP ⚠ no third-party rec" so the user is reminded to validate the catalyst against their other sources before placing. Source: `skills/thematic-scout/scripts/scout.py::_verdict` (new "INDEPENDENT SETUP" branch between BUY conditions and the final WATCH fallback), `candidate_research.py::_format_card` (badge), `when_to_enter.py::classify` (label).
24. **NEVER hide good opportunities — surface them as DEFERRED, not suppressed.** The user's explicit rule (2026-06-15): "I always want to know of great opportunities." Whenever a candidate trade or watchlist name passes the per-name discipline checks (RSI band, support quality, third-party rec, drawdown) but is blocked by a PORTFOLIO-level capacity gate (stress coverage, cash floor, expiration-bucket concentration, single-name concentration), the system MUST still render the full ticket — strike, expiration, premium, S/R, validator findings — with a ⏸ DEFERRED tag and a one-line explanation of WHY it's gated. Hiding the opportunity behind a "— blocked" message removes the user's ability to plan for when capacity reopens, and worse, removes the data that helps them decide whether to ROTATE existing positions to make room. The same rule applies to the Long-Term Opportunities Skipped section: every DEFERRED_ADD_HAS_CSP entry should surface the existing CSP strike + capture %, the proposed entry, and a rotation hint when the existing winner is at ≥30% capture. The Skipped section is a watchlist, not a graveyard. Implementation enforcement: `candidate_research.py::_format_card` renders the entry ticket with a `⏸ Deferred (capacity gated)` tag instead of `— blocked: ...` when `gate_state.open == False`; the Today's Candidates section emits a `🔒 CAPACITY: ...` banner once at the top to explain the gate without per-card duplication. The pre-trade validator's `ENTRY_GATES_CLOSED` finding is silenced inside this section (already in the banner). Source: `candidate_research.py` (banner + DEFERRED tag), `long_term_opportunities.py` (Skipped section tier rendering).
23. **Pre-trade validator — every proposed CSP/CC passes through a single discipline checkpoint before it counts as actionable.** The validator (`analysis/pre_trade_validator.py::validate_proposed_trade`) consolidates the rules previously scattered across candidate_research, capital_planner, expiration_ladder, and support_resistance into one structured pass. Each finding carries severity (BLOCK / WARN / OK), one-line reason, multi-sentence detail, and a `rule_id` so renderers/tests can filter precisely. The 10 rules currently enforced (each pinned by a test in `test_pre_trade_validator.py`):
    - `EARNINGS_WINDOW` (BLOCK) — new put spanning a single-stock earnings print
    - `ENTRY_GATES_CLOSED` (BLOCK) — stress coverage < 0.50× threshold
    - `CASH_FLOOR` (BLOCK) — cash < 5% NLV (post-margin-call discipline)
    - `EXPIRATION_BUCKET_CRITICAL` (BLOCK) — proposed trade pushes single-Friday obligation ≥ 30% NLV
    - `EXPIRATION_BUCKET_WARNING` (WARN) — projected bucket ≥ 20% NLV
    - `ROLL_UP_RISK_INCREASE` (WARN) — new strike is meaningfully closer to spot than existing on same name (rolling FROM safer TO riskier)
    - `LONG_PUT_CANCELLATION` (BLOCK) — proposed short put at/near a held long put strike (collar cancellation)
    - `RSI_OVERBOUGHT_PUT` (BLOCK) — RSI ≥ 70 + new short put
    - `RSI_OVERSOLD_CALL` (BLOCK) — RSI < 35 + new covered call
    - `STRIKE_NOT_AT_SUPPORT` (WARN) — no support cluster within 5% of the proposed strike
    Caught by the **MU $960P Aug 21 case (2026-06-15)**: the user had a stale GTC SELL_OPEN order at $154 limit that would have violated 3 BLOCK rules (earnings window, entry gates closed, bucket critical) and triggered 2 WARN rules (riskier than existing $890P, strike not at support). Validator returns the 5 findings sorted BLOCK-first so any caller can render them inline. Source: `pre_trade_validator.py` (module), tests in `test_pre_trade_validator.py` (23 tests). Future work: wire into Candidate Trades section + E*TRADE pending-order audit so the briefing surfaces this validation against open orders automatically.
22. **LT_ADD discipline — $5K "starter" recs are filler unless context makes them real.** Default LT ADD recommendations are sized at ~1/12 of a 6% NLV target = ~$5K. That starter sizing is the right default for a watchlist tracker, but it becomes counter-productive in three specific contexts and the system MUST surface those (not silently emit a `BUY ~$5K` line that the user has to manually filter). Gates (in `long_term_opportunities.py`, applied after the put-stack filter, before the RSI discipline):
    - **Suppress** (kind → `SKIPPED_ADD`) when **cash floor < 5% NLV**. After a margin-call experience this is non-negotiable — adding equity into a low-cash posture compounds the exact problem the system is trying to fix. Reason rendered: "cash floor breached — only X% NLV in cash; deploy into a low-conviction starter while defensive room is thin is the lesson Friday's margin call taught us."
    - **Suppress** (kind → `SKIPPED_ADD`) when **stress coverage < 0.30×**. System is in defensive mode; new long exposure compounds the problem.
    - **Demote** (kind → `DEFERRED_ADD_HAS_CSP`) when the **user already has a short put on the name**. The CSP IS the entry mechanism — assignment puts you long at the strike, which is usually $5-25 below the current spot. Buying equity at spot DOUBLES the exposure at a WORSE cost basis than the put assignment price. Reason rendered: "you already have N short put(s) at strike(s) $X — the CSP IS the entry mechanism; let it work or sell another CSP at a lower strike rather than buying equity at the higher current price."
    - **Promote** (concrete_trade size $5K → $20K) when **third-party tier ≥ 4** (Top 15 Stock / Top Stock to Buy per Parkev's ladder) AND **no existing CSP**. Tier-4 names are high-conviction catalysts; $5K starter on a high-conviction read is indecision masquerading as discipline. Promote to $20K to express the conviction. Adds badge: "🌟 Parkev tier-N high-conviction — sized meaningfully."
    Config: `briefing.yaml` → `lt_add_discipline`: `suppress_below_cash_pct` (default 0.05), `suppress_below_coverage` (default 0.30), `promote_tier_min` (default 4), `promote_to_size_usd` (default 20000). Source: `steps/long_term_opportunities.py::generate_long_term_opportunities_step` post-processing block. Lesson learned from a real conversation where the user pushed back: "are these good investments? say more about these" — and the right answer turned out to be "they're filler unless context says otherwise." The system should encode that judgement.
29. **Position tier framework — every holding is classified A/B/C and that tier gates covered-call writes + concentration caps. Tier A core compounders get NO CC recommendations EVER; concentration cap raised to ~22% (capping a conviction compounder at 10% defeats the long-term thesis).** Three tiers, three disciplines:
    | Tier | Names (default) | CC enabled | RSI floor | Min OTM | Max delta | Coverage cap | Max DTE | Concentration cap |
    |------|-----------------|------------|-----------|---------|-----------|--------------|---------|-------------------|
    | A — LT Core | NVDA, GOOG, MSFT, META, PLTR, AMZN, SPY/VOO | **false** | n/a | n/a | n/a | 0% | n/a | **22% NLV** |
    | B — Income | MU, SMH | true | 70 | 10% | 0.15 | 50% of round lots | 30d | 12% NLV |
    | C — Active wheel (default) | everything else | true | 60 | 4% | 0.30 | 100% | 45d | 8% NLV (or 10% default) |
    Tier A's whole point is "concentration in conviction is the strategy" — capping NVDA at 10% NLV means the system fights every quarter as the position compounds; the higher cap acknowledges that the user WANTS to compound there. Tier B's tighter envelope (≥10% OTM, ≤0.15 delta, ≤50% coverage) protects the income engine from giving back capital gains via assignment on a name with meaningful upside still on the table (MU/SMH still have semis-cycle runway). Tier C reproduces the existing discipline. **Tax-aware assignment block**: Tier A/B settings carry `tax_aware_assignment_block: true` — roll-ups that would reset the holding period inside the 60-day window before the 365-day LTCG threshold are deferred even on a clean roll-credit basis, since the assignment-equivalent tax cost outweighs the credit. **Backward compatible**: missing/empty `position_tiers` config → every ticker → Tier C (legacy default), so the framework is a no-op until config opts in. Config: `briefing.yaml` → `position_tiers` (ticker → tier mapping), `covered_call_tiers` (per-tier CC discipline), `concentration_caps` (per-tier cap %). Sources: `analysis/position_tiers.py` (single source of truth — `tier_for`, `cc_settings_for_tier`, `is_cc_enabled_for_tier`, `concentration_cap_for_tier`, `format_tier_badge`, `annotate_tier_badges`), `steps/strategy_upgrades.py` (Type D `write_covered_call` consults the tier — Tier A emits `tier_a_no_cc` transparency record; Tier B applies the conservative envelope + coverage cap; Tier C unchanged), `analysis/concentration_drift.py` (tier-aware caps with `within_bounds` severity for Tier A names in the 50-100% band), `analysis/pre_trade_validator.py` Rule 13 `COVERED_CALL_TIER_VIOLATION` (BLOCK on Tier A CCs and Tier B violations: too-close strike, delta > 0.15, contracts > coverage cap), `steps/aggregate.py` (post-process `annotate_tier_badges` after the Parkev chip, so every chip line carries `· 🟢 Tier A` / `· 🟡 Tier B` / `· 🔵 Tier C`).

## Third-party recommendations — Parkev's Google Sheet (fetched every run)

The "third-party rec" column in every surface of the briefing (Watch panel
`Third-party: Buy (tier 3, 1d old)` notes, scout verdicts, When-To-Enter
`rec BUY` tags, the Long-Term Opportunities EXIT decisions, the Capital Plan's
SELL routing) all draw from **one source: Parkev Tatevosian's Google Sheet**.

- **Sheet:** `https://docs.google.com/spreadsheets/d/12Fs_d8Zr4sKnoCxb5EaEbe2FciXIGPVTFGM9iehZq3M/`
- **Fetcher skill:** `skills/recommendation-list-fetcher/` (CSV export via `gviz/tq?tqx=out:csv`)
- **Briefing call site:** `run_briefing.py` Step 1.6 — `fetch_recommendations(snapshot_dir)`.
  Runs **unconditionally on every briefing**, before snapshot_inputs. No `--refresh`
  flag — the sheet is the authoritative source of truth and a fresh pull is part
  of the canonical run.

**Rating tier propagates through the scout** — `recs_map` in `run_briefing.py`
and `scout._load_recs_and_weights()` carries the FULL rec dict (recommendation +
rating_tier + aging + date_updated), not just the normalized BUY/HOLD/SELL
string. `_research_ticker` accepts either shape for backward compatibility, but
the briefing pipeline uses the dict form so `ScoutResult.rating_tier` (and
`.aging`) flow into the scout cache and out to every downstream surface
(`results_by_theme[*].rating_tier` is set).

**Tier-aware rendering in When-To-Enter** — `classify()` reads
`r.get("rating_tier")` and promotes tier ≥ 4 ENTRY NOW cards from
`🟢 ENTRY NOW — BUY` to **`🌟 STRONG BUY — ENTRY NOW`** (or `🌟 STRONG BUY — ENTRY
NOW (CSP)` for CSP setups). The read line annotates with `Parkev tier-N
(raw_recommendation) — high-conviction catalyst`, and the trigger upgrades
sizing guidance from "1/3 of target weight" (standard) to "1/2 of target weight
is reasonable" (tier 4-5). Tier-5 is the rare "Top Stock to Buy" — Parkev's
single strongest signal; tier-4 is "Top 15 Stock" / "Top 25 Stock." Tier-3
("Buy") keeps the standard 🟢 label and 1/3 sizing.

The underlying RSI gate is *unchanged* — tier doesn't loosen the favored-band
(35-55) requirement, doesn't override the overbought block (RSI ≥ 70 still
WAITs even on tier-5), and doesn't bypass the position-aware long-put-cancel
guard. The tier only differentiates *within* the already-actionable set so the
user can size accordingly.

Rating ladder (`raw_recommendation` → normalized `recommendation` + `rating_tier`):

| Raw value | Normalized | Tier |
|-----------|------------|------|
| `Top Stock to Buy` | BUY | 5 |
| `Top 15 Stock` / `Top 25 Stock` | BUY | 4 |
| `Buy` | BUY | 3 |
| `Borderline Buy` | BUY | 2 |
| `Hold/ Market Perform` | HOLD | 1 |
| `Borderline Sell` | SELL | 0 |
| `Sell` / `Top Stock to Sell` | SELL | 0 |

Each row also carries `date_updated`, `age_days`, and an `aging: true` flag when
the rec is >14 days old — the verdict logic gives more weight to fresh recs.

**Universe alignment between Parkev's sheet and the scout's themes** is a known
asymmetry. The sheet is broader (~200 names spanning consumer, healthcare,
finance, AI/tech, etc.); the scout's `theme_universes.yaml` is intentionally
narrow (AI Buildout + Ancillary + Adjacent, ~79 anchors). A ticker can be:

- **In both** → full treatment (rec drives verdict, scout adds technicals/chain, full When-To-Enter card).
- **In Parkev only** → "orphan" — rec is fetched and appears on relevant equity reviews but the scout never grades it. *No When-To-Enter card, no Candidate Trades visibility.* Add to `theme_universes.yaml` if the name fits an existing theme; otherwise it stays an out-of-universe rec.
- **In scout only** → covered by technicals but verdict tends to land WATCH for lack of a third-party catalyst.

When you see a name in the briefing that you expect to be in the scout but
isn't, it's almost always this asymmetry. The fix is either adding the anchor
to `theme_universes.yaml` (one-line config change, picked up on the next run
that uses `--refresh-scout` or the 24h cache cycle) or creating a new theme
for an out-of-AI-thesis name worth tracking.

**Failure mode:** if `fetch_recommendations` errors (sheet unreachable / format
change), the briefing continues with an empty rec list — downstream surfaces
just don't get third-party annotations. The fetcher itself logs the failure
to stderr. Watch for "Fetched 0 recommendations" on a run that should have had
~200.

## Pipeline overview

The briefing orchestrator runs these steps (see `scripts/run_briefing.py`):

1. Pre-flight (config, yesterday's briefing for diffing)
2. Load directives
3. Fetch third-party recommendations  — Parkev's sheet, every run (see above)
4. Snapshot inputs (E*TRADE positions + parallel yfinance technicals + chains)
5. Classify regime (VIX/SPY)
6. Review equities (per-position decision)
7. Review options (wheel-roll-advisor matrix walk)
8. Generate new ideas (PULLBACK CSPs)
9. Long-term opportunities (3-12mo horizon)
10. Thematic scout (cached 24h; semis/nuclear/quantum/etc)
11. Day-over-day consistency check
12. Capital plan (rank by risk-reward tier)
13. Aggregate + render
14. Quality gate (4-persona structural check + pre-flight verifier)
15. Surface + deliver

## Key file locations

- `skills/daily-portfolio-briefing/config/briefing.yaml` — tunable thresholds
- `skills/daily-portfolio-briefing/scripts/run_briefing.py` — orchestrator
- `skills/daily-portfolio-briefing/scripts/etrade_auth.py` — in-repo OAuth
- `skills/etrade-chain-fetcher/scripts/fetch.py` — canonical chain access
- `skills/<name>/SKILL.md` — per-skill spec; read this for any skill behavior
- `~/.config/portfolio-briefing/etrade_tokens.json` — OAuth tokens (0600)
- `~/Documents/briefings/latest.md` — delivered briefing
