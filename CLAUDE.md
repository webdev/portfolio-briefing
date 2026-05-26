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

## Roll discipline — moneyness-based, not P&L%

Surface EXECUTE ROLL only when the position is at/past strike (genuine
assignment risk):
- Short PUT: spot ≤ 1.03 × strike
- Short CALL: spot ≥ 0.97 × strike

P&L% bleed from a rally is NOT a defensive trigger by itself. A -70% short
call that's still 12% OTM is "the rally happened, theta will recover," not
"I'm about to lose my shares." Same rule applies to puts.

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
- **Selling covered calls**: favoured when extended/overbought (RSI > 60).
  OVERSOLD (RSI < 35) is a **hard block** — caps the name right before a likely
  bounce. Mid-range (35-60) → warn.

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
11. **RSI discipline:** every rec passes `rsi_discipline.hook()` → remove/keep/promote. Show RSI on every rec; block new put-sales & buys >70, new covered calls <35; management never removed. Verifier (`audit_missing_rsi`) enforces RSI coverage.
12. **Intrinsic value:** every single-stock rec shows FMP DCF + analyst-target fair value; ETFs → "n/a — basket"; fail closed (no key / no data → no fabricated number). Source: `intrinsic_value.py`.
13. **Capital Plan new-put gate:** `NEW_CSP`/`LT_CSP` demoted to Skipped when stress coverage < 0.50× or projected concentration ≥ 10% NLV — the plan can't contradict the Red Flags.
14. **Challenge every rec:** action list defers to the position's advisor (never EXECUTE ROLL when the advisor says HOLD; prefer roll-up/shorter-tenor over max-credit); a deterministic counterpoint layer (`recommendation_challenger.py`) surfaces contradictions, opportunity cost, tenor, tax, concentration & valuation — inline + in a panel. Never relay a rec at face value.
15. **Everything actionable:** no datapoint stays descriptive — every state (overbought/oversold/cold/extended/drawdown) maps to an entry/exit/trim/write-calls/hold/wait verdict, read asymmetrically. Market Pulse names carry `🎬 Action:`; theme leaders carry `→ <tag>`. Source: `thematic_research._action_read()` / `_action_tag()`. A datapoint without an action read is a bug.

## Pipeline overview

The briefing orchestrator runs these steps (see `scripts/run_briefing.py`):

1. Pre-flight (config, yesterday's briefing for diffing)
2. Load directives
3. Fetch third-party recommendations
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
