# UX Critique — Portfolio Briefing Web App
**Date:** 2026-06-30 · **Reviewer:** Claude (UX designer + financial-analyst lens)
**App version:** Today's build (DuckDB resilience + theme contrast + candidate cards landed)
**Sample data:** 2026-06-30 briefing · INDIVIDUAL account · NLV $1,016,915 · coverage 0.07× (CRITICAL)

The app has come a long way — Bloomberg-grade chrome, structured cards, Shoelace polish,
no markdown literal leaks, working charts after the bootstrap fix. This critique covers
the **next layer** of work: information hierarchy, scannability, what's hidden, and what
a financial analyst would expect to see that isn't there yet.

---

## Top-3 things to fix first

1. **Dashboard hides the most important fact.** Coverage 0.07× is CRITICAL (the system's own
   threshold is 0.50×). It's shown as a 12px topbar pill — same size as Date / NLV / Cash.
   The dashboard body has 2 sections (Top 5 holdings, Action queue 12) — no banner, no
   alert, no Red Flags panel. A new visitor wouldn't know they're in defensive mode.

2. **History page has no labels.** Three Plotly charts stack with no H2 headers. The user
   sees three canvases and has to guess which is NLV vs coverage vs expiration ladder.
   `h2: []` on `/history`.

3. **No single "what to do today, ranked" view.** Recommendations live in 4 places: Action
   Queue (briefing), Capital Plan (briefing), Ideas (briefing), When-to-Enter (companion).
   Each is independently sorted. There's no consolidated, severity-ranked daily list.
   A PM would want: "Top 5 most urgent items across all surfaces, sorted by R/R impact."

---

## Page-by-page findings

### `/` — Dashboard ("Today")

**What works:**
- Parkev chip + Tier badge already render correctly in the Top 5 holdings table
  (`🅿️ BUY · 🔥 High · ⏰ 28d 🟢 Tier A`) — clean, scannable, hard rules #27 + #29 honored
- Action queue is present
- All four nav links work (Today / Briefing / Candidates / When-to-Enter / Positions /
  Diff / History)

**Gaps:**

- **Risk header missing.** No banner for coverage 0.07× CRITICAL, cash 5.2%, expiration
  bucket warnings. The pipeline emits Red Flags & Priorities in the briefing — Dashboard
  doesn't pull from it. Add a `🚨 Critical: stress coverage 0.07× (red threshold 0.50×) —
  defensive mode active` banner above Top 5.
- **No KPI cards.** Two-row strip wanted: top row = NLV / Cash / Cov / Regime as
  monospace-bold cards (bigger than the current pills), bottom row = today's deltas
  (NLV ΔΔ, Cov Δ, # new actions).
- **No mini-charts.** A 7-day NLV sparkline + 30-day coverage trendline next to the KPI
  cards would give "is this normal or unusual" at a glance.
- **Action queue is flat.** 12 items in one list. Should be grouped by severity:
  - 🔴 Urgent (DEFENSIVE_ROLL within DTE 21, CLOSE on loss stop, expiration this week)
  - 🟡 This week (TAKE_PROFIT, RSI-favourable PULLBACK)
  - 🟢 Monitor (HOLD, low-priority watches)
- **No companion-report jump-list.** Should have prominent links to 🎯 Candidate Research,
  🚦 When-to-Enter, 📊 Capital Plan PDF, etc. — these are first-class daily deliverables but
  buried in the topbar nav.

### `/briefing/{date}` — Full briefing

**What works:**
- 7 well-organized tabs (Actions 12 / Equities 15 / Options 33 / Long-term 28 / Strategy 7
  / Ideas 26 / Consistency)
- Action verbs use icons (`🔚 CLOSE`)
- Contracts pretty-print (10 `.contract` chips)
- Parkev info embedded in markdown text per hard rule #27

**Gaps:**

- **No counterpoints rendered as components** — `counterpoints: 0`. CLAUDE.md hard rule #14
  says every actionable rec must have a counterpoint. The data is there in the markdown
  but it's not surfaced as a distinct collapsible chip per action. User has to read prose.
- **Parkev chips render as plain text, not as styled chips.** `parkevChips: 0` (no
  `.parkev-chip` class). The unified format is correct (`🅿️ BUY · 🔥 High · 28d`) but it's
  embedded in `<p>` tags. Should be a styled inline chip with the same visual weight as
  the action verb icon.
- **No "jump to companion" strip.** Briefing should have a sticky "Companion reports:
  🎯 Candidates (85) · 🚦 When to Enter (116) · 📊 Capital Plan" with the latest counts.
- **Tab badge counts are right but tabs themselves don't show pill counts.** Each tab text
  shows " 12" with a non-breaking space — should be a proper `<sl-badge>` for visual rank.
- **No filtering inside Action Queue.** 21 cards visible (12 main + 9 strategy upgrades),
  no way to filter by verb (only CLOSE / only ROLL) or by tier. The Candidates page DOES
  have status filtering — same pattern would help here.
- **No "since yesterday" diff inline.** When an action item has been pending for 3+ days,
  it should show a `⏱ 3d` badge inline. The data is in `/diff/yesterday/latest::
  still_pending` but not joined back into the briefing view.

### `/candidates/{date}` — Candidate Research (new)

**What works (this is the strongest page):**
- 14 sections × 85 cards in responsive grid
- Status tone breakdown: 3 ok / 0 warn / 1 info / 48 muted / 33 bad
- 3 🏆 Top conviction badges
- 4 summary chips + 6 filter buttons + capacity banner all present
- Empty-section auto-hide on filter

**Gaps:**

- **3 cards in `ok` tone vs 48 `muted`** — the visual hierarchy is right, but a 48:3 ratio
  means most of the page is grey-bordered watch cards. Consider: collapse "WATCH" cards
  by default (show 5, "Show 43 more" expander) so the 3 actionable ones lead the eye.
- **0 trigger blocks rendered** (`hasTriggers: 0`). The card template has the
  `rep-trigger` row but no candidates carry a `trigger` field in their parsed dict.
  Either the markdown has triggers and the parser missed them, OR candidate cards
  legitimately don't carry trigger lines (only when-to-enter does — by design).
  Worth verifying the data shape vs parser regex.
- **`fair_value` chip uses the same neutral row styling** as Read / Verdict. FV is the
  most decision-relevant chip — give it its own tone (cyan or violet accent) and put
  it next to the price chip on the header row, not buried in the body rows.
- **No "Open in briefing" link from each card.** A user reading a CANDIDATE card might
  want to see what the briefing said about the same ticker — link back.

### `/when-to-enter/{date}` — When-To-Enter

**What works:**
- 20 sections × 116 cards, all 116 with trigger lines (`triggerLines: 116`)
- ETF pills on 10 cards (basket distinction visible)
- Status breakdown: 0 ok / 24 warn / 19 info / 35 muted / 38 bad

**Gaps:**

- **0 cards in `ok` tone — but the summary says "19 ENTRY NOW"** (deferred). All 19 are
  ⏸ DEFERRED (capacity-gated) — coverage 0.07× blocks new puts. The page should LEAD
  with a banner: "🔒 19 entries qualify on technicals but are DEFERRED because stress
  coverage 0.07× < 0.50× threshold. Close N% NLV from oversized positions to unlock."
  Currently the deferral reason is per-card; the systemic cause is invisible.
- **Trigger blocks have great accent border** (cyan) but are visually the same weight as
  Read/Verdict. The trigger is the order ticket — bump font weight / increase contrast.
- **No way to compare ENTRY NOW candidates against each other.** A side-by-side table
  view showing all 19 with (Ticker / Strike / Expiry / Premium / Collateral / Upside /
  RSI / Parkev) would be more useful than 19 cards.

### `/positions` — All positions

**What works:**
- Parkev + Tier columns now render correctly (`chip chip-parkev` + `chip tier-A`)
- 48 equity rows, 33 options, sortable headers
- Pretty contract chips render (33 `.contract` elements)

**Gaps:**

- **Options table has redundant columns.** Header is Underlying / Symbol / Type / Strike /
  Expiry / Qty / Mid / P/L% / Δ / IV% / Parkev. The pretty-printed Symbol chip ALREADY
  shows ticker + type + strike + expiry as styled spans. So Underlying / Type / Strike /
  Expiry duplicate Symbol. Remove them.
- **No "Account" column on options table** (Equities has it, Options doesn't). The user
  has multi-account scope eventually planned (CLAUDE.md hard rule #1); even now showing
  it would help.
- **No target price column.** Equities table lacks FMP DCF and (future) FINVIZ target
  upside. P/L% tells you where you've been; target upside tells you where it's going.
- **No tier filter / sort.** With 15 equities, sorting by tier or filtering "Tier A only"
  is useful — currently you sort by ticker / price / value.
- **No row-level action.** A position is just a row. Should have a small kebab (⋮) menu:
  "View briefing mentions · Open Parkev history · Sell-to-close (advisor)".

### `/positions/{ticker}` — Per-ticker drill-down

**This is the thinnest page given how central it is.**

**What works (after today's fixes):**
- 2 charts render (price sparkline + Parkev timeline)
- Position history table
- Briefing mentions (121 for GOOG)

**Gaps (financial-analyst lens):**

- **No current state panel.** A drill-down should lead with: current Parkev rec, RSI(14),
  IV rank, distance from 200-SMA, drawdown from 52w high, FMP DCF, S/R levels, Tier badge,
  related options on this ticker, position weight as % NLV. None of this is on the page
  today — the user has to cross-reference 3 other pages.
- **No fair-value section.** FMP analyst PT + FMP DCF are computed daily for every
  single-stock rec — should have a dedicated card with both, plus (after task #37)
  FINVIZ target side-by-side with divergence flag.
- **No EXIT signals.** The pipeline knows when a position has hit the loss stop / earnings
  trigger / DEFENSIVE_ROLL threshold — should surface as a colored callout on the page.
- **Charts have no toolbar.** Plotly's mode bar is removed but no `range slider` / `1m 3m
  6m 1y` quick-select. Stuck with the default 30d window.
- **Briefing mentions list isn't grouped by date.** 121 mentions for GOOG over months
  shown as a flat list (probably truncated). Should be reverse-chronological with date
  group headers + verdict pills.

### `/history` — Charts ⚠️

- **No H2 labels at all** — `h2: []`. Three Plotly charts stack with zero context.
- Should be: H2 "NLV trajectory (90d)" / "Stress coverage trend (90d)" / "Expiration ladder
  distribution" — plus a 1-paragraph "what to look for" caption under each.
- Date-range selector missing — assumes 90d but might want 30d / 1y.

### `/diff/{a}/{b}` — Diff page

**What works:**
- Clean H2/H3 structure (Action queue diff / Position diff / Parkev rating changes)
- Contracts pretty-printed
- Three buckets: new / completed / still_pending

**Gaps:**

- **No portfolio-level summary card at top.** Should be: "Yesterday → today: NLV
  $1,016,915 (Δ $+12K, +1.2%), 1 position added (NVDA), 2 removed (SOFI, BE), 6 new
  actions, 3 done, 6 still pending (oldest 4d). Cash 5.2% (Δ -0.3%). Coverage 0.07×
  (no change)." — answer the "should I bother reading the rest" question in 2 seconds.
- **No "stalled action" highlight.** `still_pending` with `days_flagged >= 3` should
  stand out (right now there's a CSS class `.stalled` for ≥6d only).
- **No `/diff/{a}/today` quick-pick** — only "yesterday vs latest". Should let user
  pick any A vs any B.

### `/search?q=...`

**What works:**
- 121 results for GOOG; pretty contract chips on 82 of them
- Result rows compact, ticker-clickable

**Gaps:**

- **No facet filters in UI.** The `type=action|equity_review|options_review|...` filter is
  documented in the README but not exposed as UI chips. Add a row of toggle chips above
  results.
- **No "sort by date desc"** option — results appear in document order.
- **No grouping by date** — 121 GOOG mentions stretch back months in one flat list.

### Topbar (global)

**What works:**
- Date / NLV / Cash / Cov / Regime pills always visible
- Search input ready
- Refresh button + theme switcher in right cluster
- 4 themes all defined with WCAG-AA contrast (today's fix)

**Gaps:**

- **Cov pill is the only red signal** (border color) but no inline explanation. Hover
  tooltip: "Stress coverage 0.07× — below 0.50× critical threshold. Click for details."
  Clicking should jump to the Red Flags & Priorities panel in the briefing.
- **Theme switcher shows only the icon.** First-time user won't know what 🌒 means.
  Show "Solarized Dark" label next to the icon (or in the trigger tooltip).
- **Refresh button has no staleness signal.** Last-run timestamp lives in the footer. Move
  to the button: "↻ Refresh · ran 14:42" — and make the button amber when last run > 4h ago.

### Themes (global, today's fix)

**What works:**
- All 4 themes have CSS now (light / dark / solarized-dark / solarized-light)
- WCAG AA contrast verified by tests for tertiary text on bg

**Gaps:**

- **Selected theme has no checked state in the dropdown.** Menu items are `type="checkbox"`
  but the `checked` attribute only sets on the dark menu item by default. Need to read
  localStorage and mark the current theme.
- **No theme-aware Plotly colorway.** Plotly colors are baked into the JSON spec from the
  Python side. When the user switches to a light theme, the chart background goes white
  (good) but the line colors stay dark-theme bright (clash). Should pass theme into the
  Plotly spec.

---

## Cross-cutting (would benefit every page)

### 1. Severity-ranked daily summary card

A persistent component shown on Dashboard + at the top of every page:

```
🚨 1 critical · 📊 3 warnings · ⏱ 2 stale (3+ days)
   → Critical: stress coverage 0.07× (jump)
   → Warning: expiration cluster Aug 21 = 31% NLV (jump)
   → Stale: ROLL on TWLO open 4 days
```

Pulls from `briefing.red_flags`, `briefing.risk_alerts`, `diff.still_pending`. Click each
to jump to the source page section.

### 2. Inline "since yesterday" badges on actions

Every action item in the briefing should carry a small badge:
- 🆕 New (first appeared today)
- ⏱ 3d (pending for N days)
- ↺ Recurring (third+ appearance)
- ✓ Done in yesterday's diff (greyed out)

The data exists in the diff endpoint; just needs joining.

### 3. Rotation-aware row actions (this is task #36)

Every holding row should have a "Compare to candidates" expander that opens an inline
table: "5 same-theme alternatives, ranked by composite score (RSI + IV + Parkev +
FV + S/R). Click to see suggested swap." This is the "rotation advisor" the user already
asked for — placement here is the natural surface.

### 4. P&L attribution view (missing entirely)

Closed trades log exists in the pipeline. A new page `/performance` would show:
- Realized P&L this week / month / quarter / YTD
- WR / Avg W / Avg L / Profit factor / Max DD
- Top contributors + top detractors
- Attribution by strategy (covered call / CSP / earnings crush / etc.)

Today the user has to compute this manually from the action history.

### 5. Risk budget view (missing entirely)

`/risk` page showing:
- Concentration by ticker, sector, theme
- Stress test: portfolio P&L at -5% / -10% / -20% market moves
- Hedge book status + delta neutralization %
- Margin headroom + days-of-cash runway

Currently scattered across briefing sections; should be a dedicated dashboard.

### 6. Mobile responsive check (not audited)

Cards are `minmax(380px, 1fr)` — at narrow widths they'll be one-column. Topbar pills will
overflow. Briefing tabs will need horizontal scroll. Haven't checked, but Tailscale-on-phone
is a stated use case in the README so this matters.

---

## Punch list — sorted by ROI

| # | Page | Fix | Effort | Why |
|---|------|-----|--------|-----|
| 1 | `/history` | Add H2 labels under each chart | 5 min | One-line fix, big clarity win |
| 2 | `/` | Add Red Flags banner from briefing.red_flags | 30 min | Most important signal currently hidden |
| 3 | `/positions` | Remove redundant Type / Strike / Expiry options columns | 10 min | De-duplication, easy |
| 4 | Topbar | Cov pill tooltip + click → red flags section | 15 min | Disambiguation |
| 5 | Topbar | Theme switcher label + checked state on current | 20 min | UX polish |
| 6 | `/diff/*` | Portfolio-level summary card at top | 30 min | "Should I read this" answer |
| 7 | `/positions/{tk}` | Current-state card (RSI/IV/Parkev/Tier/FV/SR) | 1 hr | Most-used page is thinnest |
| 8 | `/` | Severity-ranked daily summary component | 1-2 hr | Cross-page navigator |
| 9 | `/briefing/*` | Counterpoint disclosures rendered as `<sl-details>` | 1 hr | Hard rule #14 honored |
| 10 | `/when-to-enter/*` | Capacity-deferred banner at top + table view | 1 hr | Currently 19 deferred but cause invisible |
| 11 | `/search` | Filter chips + sort-by-date | 30 min | Search is hard to navigate at 121 results |
| 12 | `/performance` (new) | P&L attribution + WR + DD | 4 hr | Missing entirely |
| 13 | `/risk` (new) | Concentration + stress + hedge book + margin | 4 hr | Scattered today |
| 14 | All charts | Theme-aware Plotly colorway | 1 hr | Light themes look broken |
| 15 | `/positions/{tk}` | "Related options on this ticker" mini-table | 30 min | Cross-link to briefing |
| 16 | Mobile | Audit at ≤768px wide, fix overflow | 2 hr | Tailscale-on-phone case |

**Quick wins (do this hour):** 1, 3, 4, 5 = ~50 min total, all visual clarity.
**High-leverage half-day:** 2 + 7 + 8 + 9 + 10 = ~5 hr, fixes the "what's urgent / what
do I look at" question across every page.
**Net-new pages:** 12 + 13 = 8 hr, fills the analyst gaps (P&L attribution, risk budget).

---

## Financial-analyst gaps (one-line summary)

The app shows **state** (positions, ratings, technicals) and **actions** (recommendations)
extremely well. It under-serves three analyst staples:

1. **Performance.** WR / Avg W / Avg L / DD / attribution. Where the wins come from.
2. **Risk budget.** Concentration / stress / hedge / margin. Where the next blowup comes from.
3. **Rotation.** "Better than what I hold." Where to deploy fresh capital — already
   queued as task #36.

The first two should become net-new pages (`/performance`, `/risk`). The third is the
Rotation Advisor.

---

**End of critique.** Ready to start ticking through the punch list — or if you'd rather
I jump straight to the rotation advisor (task #36) now that the gaps are documented,
that's fine too.
