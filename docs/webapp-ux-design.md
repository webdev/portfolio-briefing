# Portfolio Briefing — Web App UX Design

**Status:** v1 design spec  ·  **Author:** Senior Product Design  ·  **Date:** 2026-06-30
**Audience:** Frontend engineer building the app; the user (sole consumer) for sign-off

> This document defines a single-user web application that consumes the existing
> daily briefing pipeline (markdown + JSON output) and the ~140 historical
> `briefing_snapshots/` directories. It does **not** replace the briefing — it
> renders the same disciplined vocabulary in a clickable, time-aware surface,
> and unlocks views a static markdown file fundamentally cannot.
>
> **Non-goals:** order placement, multi-user/collab, an AI chat sidebar,
> tablet-first design.

---

## 1. User Research Summary

### Who

A single power-user managing a ~$1M E*TRADE INDIVIDUAL account on a wheel +
long-term hybrid strategy. Decades of investing experience; runs a disciplined
process he encoded himself across ~30 skill modules (`skills/*/SKILL.md`),
~25 hard rules (`CLAUDE.md`), and a daily Python pipeline that emits
`briefings/briefing_YYYY-MM-DD.md` every morning around 8:00 AM ET.

Sophistication level: he knows what RSI(14) Wilder's smoothing is, distinguishes
LTCG vs STCG instinctively, has been burned by a margin call and rebuilt
discipline around it (the 5% cash floor, the 0.50× stress coverage gate), and
treats Parkev Tatevosian's Google Sheet as a third-party catalyst layer rather
than gospel.

### Job-to-be-done (morning)

> "In under 10 minutes, tell me what I need to place at E*TRADE today, what's
> safe to ignore, and what's quietly drifting toward danger."

Specifically (in observed sequence):

1. Open `~/Documents/briefings/briefing_YYYY-MM-DD.md` in an editor.
2. Skim the **Stalled Items** header (3 items today, oldest 11 days) — anything
   re-flagging means he punted yesterday.
3. Scroll past metadata (NLV $999K, cash 4.4%, coverage 0.06×) — but those
   numbers ARE the gate. He cross-references them mentally against the cap
   table (≥0.50× to open new puts, ≥5% cash floor).
4. Read the **Action List** (5 headline + appendix). Each line is a discipline
   verdict: CLOSE, HEDGE, ROLL, with rationale and a buy-to-close limit.
5. Skim **Risk Alerts** for new red flags vs yesterday (concentration creep,
   expiration bucket clusters, ITM puts).
6. Glance at **Today's Candidates (42)** — mostly DEFERRED right now (capacity
   closed), but he wants to know what's queued for when coverage rebuilds.
7. Eyeball the **Watch / Portfolio Review** to make sure no holding flipped
   from HOLD to SELL.
8. Open E*TRADE in another tab, place 3-5 orders, close laptop.

### What's broken about the current text workflow

| Pain | Evidence |
|---|---|
| **Numbers without trajectory.** NLV $999K — is that up or down from a week ago? Coverage 0.06× — when did it dip below 0.50×? | The briefing prints today's snapshot only. To answer "when did coverage break?" he has to manually diff 30+ markdown files. |
| **Conviction changes are invisible across days.** Parkev's chip shows `🅿️ HOLD · ◐ Med · ⏰ 18d` — but yesterday it was `BUY · 🔥 High`. | The chip format encodes "rec + conviction + age" in one cell, but not "what changed." Drift events are silent. |
| **Aging is per-item, not portfolio-wide.** Each CLOSE shows `⏳ IGNORED 11 DAYS`, but he has no scoreboard of "of my last 30 CLOSE recs, how many got executed?" | The `actions` JSON has `recon_status: IGNORED`, but there's no aggregate. |
| **Expiration cluster heatmap.** The 6 puts on Fri Sep 18 ($296K obligation, 29.6% NLV) is flagged once. But which other Fridays have ever hit that threshold historically? | Only the current snapshot's `put_buckets` is computed; no cross-time view. |
| **No drill-down.** "Tell me everything we've ever said about NVDA" requires `grep "NVDA" briefings/*.md`. | The data is structured per-day; no per-ticker timeline view. |
| **No wheel ROI vs counterfactual.** "What did my wheel actually earn this quarter, and would I have done better just holding the shares?" | Pipeline tracks closed trades but no rollup. |
| **Stalled items get re-flagged but consequence-free.** "⛔ DECISION REQUIRED" is the strongest signal the text format has. | A web app can surface stale items at the top, in red, with a dismiss/defer/override flow. |
| **The cap table is implicit.** `coverage 0.06x | cash 4.4% | obligation 78% NLV` is one line; it should be a **gate visualization** with the actual thresholds on a number line. | Mental arithmetic burden. |

### Anti-pattern guardrails (what this app must NOT become)

- Not a Robinhood-style "you could trade this!" UI. The user values **friction
  around bad trades**, not slickness.
- Not a Yahoo Finance / Koyfin clone. He has those for raw data. This app's
  edge is **his discipline rules encoded visually**.
- Not a chat interface. He runs Claude separately.
- Not collaborative. Single user.

---

## 2. Information Architecture

### Site map

```
/                          → Today (default; "above the fold" briefing)
/today                     → same as /
/actions                   → Action queue (today + stalled + history)
  /actions/:key              → Single action drill-down (e.g. CLOSE:AMD_PUT_420_20261218)
/positions                 → Portfolio (equities + options grid)
  /positions/:ticker         → Ticker drill-down (timeline, every rec, P&L, conviction)
  /positions/:contract       → Contract drill-down (e.g. AMD_PUT_420_20261218)
/candidates                → Candidate Trades (42 deferred + watch-only)
  /candidates/:ticker        → Same as positions/:ticker but with "no position yet" framing
/risk                      → Risk dashboard (coverage, concentration, expiration ladder, hedges)
  /risk/coverage             → Stress coverage timeline + scenario sandbox
  /risk/ladder               → Expiration bucket heatmap (calendar view)
  /risk/concentration        → Per-name weight over time + sector heat
  /risk/hedges               → Hedge book + delta neutralization history
/recommendations           → Parkev sheet view (52 names, ratings + conviction + age)
  /recommendations/changes   → Conviction-change feed (upgrades / downgrades / new / dropped)
/performance               → Wheel ROI vs buy-and-hold counterfactual, MTD/YTD
  /performance/trades        → Closed trades ledger (filterable)
  /performance/calendar      → Calendar heatmap of premium captured per day
/scout                     → Thematic Scout (14 themes, 127 names)
  /scout/:theme              → Theme drill-down with constituent grid
/journal                   → Day-by-day briefing archive (search + diff between any two days)
  /journal/:date             → Rendered briefing for that date + structured diff vs prior day
/settings                  → Thresholds (read from briefing.yaml, read-only initially)
```

### Primary navigation (left rail, desktop)

```
┌─ Logo ────────────────┐
│                       │
│ ● Today               │  ← default; badge with action count
│ ◔ Actions      (9)    │  ← red badge if any IGNORED ≥7d
│ ⚡ Risk         ⚠     │  ← warning icon if gates closed
│ ⚖ Positions   (49)    │
│ 🎯 Candidates  (42)   │
│ 🅿️  Recs        (52)   │  ← dot if any rating change since last visit
│ 🔭 Scout       (127)  │
│ 📈 Performance        │
│ 📓 Journal            │
│                       │
│ ─────                 │
│ ⚙ Settings            │
│                       │
└───────────────────────┘
```

### Top bar (persistent)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  Tue Jun 30, 2026  │  NLV $999,629  ▲  │  Cash 4.4% ●  │  Cov 0.06× ⛔  │  ⌘K  │
└────────────────────────────────────────────────────────────────────────────────┘
```

- Each pill is **clickable**, links to its drill-down.
- Color rule: ⛔ red when gate failed (coverage <0.50×, cash <5%), 🟡 yellow when
  approaching (cash 5-7%, coverage 0.50-0.70×), 🟢 green when clear.
- `⌘K` opens command palette (jump to ticker / contract / date).

### Mobile nav (≥iPhone-width)

Bottom tab bar with 5 destinations: **Today · Actions · Risk · Positions · More**.
Everything else moves under "More". See §6 Mobile.

---

## 3. Page-Level Wireframes

### 3.1 Today (`/`) — the morning landing page

Above the fold is the **action triage**, not the metrics. The morning question
is "what do I place today?" not "what's my NLV?".

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  TOP BAR (always visible)                                                        │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ⛔ STALLED — 3 items punted for ≥6 sessions. Decide today.        [View all →]  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  ⏳ 11d  CLOSE AMD_PUT_420 +45% ($+3,413)   🅿️ HOLD·◐·⏰18d   [Execute] [Defer]│  │
│  │  ⏳ 7d   HEDGE SPY $706P 16× $11,905        🅿️ no rec        [Execute] [Defer]│  │
│  │  ⏳ 6d   CLOSE GOOG_CALL_450 +35% ($+8,600) 🅿️ BUY·🔥·⏰28d    [Execute] [Defer]│  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  CAPACITY  (gate visualization)                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │                                                                            │  │
│  │   Stress Coverage     0.06×   ──────────────────●───[0.50×]──[0.70×]──     │  │
│  │                                ⛔ Below floor                              │  │
│  │                                                                            │  │
│  │   Cash floor          4.4%    ────────────────●─[5%]─[7%]────────────      │  │
│  │                                ⛔ Below floor                              │  │
│  │                                                                            │  │
│  │   Put obligation      78%     ─────────────────────────────────────●─[80%] │  │
│  │                                ⚠ Approaching saturation                    │  │
│  │                                                                            │  │
│  │   🔒 NEW CSP ENTRIES BLOCKED. Close winners to free coverage.              │  │
│  │      ▸ Top 3 closes free $93K and lock $4,191 profit  [See plan →]         │  │
│  │                                                                            │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  TODAY'S ACTIONS  (9)                                          [Sort ▾] [Filter] │
│                                                                                  │
│  1  CLOSE  AMD_PUT_420 PUT $420 Dec 18 '26  +45% ($+3,413)  ⏳11d               │
│      🅿️ HOLD · ◐ Med · ⏰18d   RSI 62 🟡   FV: DCF $50 / PT $477               │
│      ▸ Buy-to-close limit $43.60 — frees $42,000 cash                           │
│      [Mark executed] [Defer 1 day] [Override — never re-flag] [Full detail →]   │
│                                                                                  │
│  2  CLOSE  GOOG_CALL_450 CALL $450 Dec 17 '27  +35% ($+8,600)  ⏳6d            │
│      🅿️ BUY · 🔥 High · ⏰28d   RSI 46 🟡   FV: n/a                            │
│      ▸ Buy-to-close limit $41.27 — unlocks 400 shares for fresh CC premium      │
│      [Mark executed] [Defer 1 day] [Override — never re-flag] [Full detail →]   │
│                                                                                  │
│  …  (7 more, collapsed by default)                              [Show all 9 →]  │
│                                                                                  │
├────────────────────────────────────────────────┬─────────────────────────────────┤
│                                                │                                 │
│  HEALTH                                        │  WHAT CHANGED  (vs yesterday)   │
│                                                │                                 │
│  NLV         $999,629   ▲ $+1,847 (0.18%)      │  • AMD: BUY → HOLD              │
│  Cash        $44,435    ━ unchanged             │    (Parkev downgrade, 28d→18d) │
│  Coverage    0.06×      ↓ from 0.08× (7d ago)  │  • SPY: hedge re-flagged (+1d) │
│                                                │  • New: AVGO CSP candidate     │
│  [30d trend sparklines, click any to expand →] │  • Resolved: GOOG_PUT_325       │
│                                                │    earnings-window cleared      │
│                                                │  [Full diff →]                  │
│                                                │                                 │
├────────────────────────────────────────────────┴─────────────────────────────────┤
│                                                                                  │
│  RISK ALERTS  (7)                                                  [See risk →]  │
│  ⚠ NVDA 13.9% — over 10% cap  · 13d above cap                                   │
│  ⚠ GOOG 15.4% — over 10% cap  · 21d above cap                                   │
│  📊 Sep 18 '26 bucket: $296K (29.6% NLV) — approaching 30% cap                  │
│  …                                                                               │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Design notes:**

- The "Stalled" callout at the top is the **only** place red text fires by
  default. Discipline depends on knowing what you punted.
- The capacity gates use a **number line** with the actual thresholds drawn —
  far more informative than "0.06×" on its own. The dot's position relative
  to the floor encodes urgency without color alone.
- "What changed" is the diff-against-yesterday view the markdown can't do.
  Sourced from comparing today's JSON against `briefings/briefing_<prev>.json`.
- All numbers carry **trajectory chips** (▲ ▼ ━) and click-through to the
  30-day chart.

### 3.2 Actions (`/actions`) — the queue

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ACTIONS                                                          [+ Add manual] │
│  ─────────────────────────────────────────────────────────────────────────────── │
│  [Today (9)] [Stalled ≥6d (3)] [This week (24)] [Last 90d (612)] [All]          │
│  Filter:  Kind: [All ▾]  Ticker: [____]  Conviction: [All ▾]  Status: [All ▾]   │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┃ Age ┃ Kind   ┃ Contract / Ticker         ┃ Detail                  ┃ Parkev  ┃ Status      │
│  ┃─────┃────────┃───────────────────────────┃─────────────────────────┃─────────┃─────────────│
│  ┃⏳11d┃ CLOSE  ┃ AMD_PUT_420_20261218      ┃ +45% ($+3,413) ltd $43.60┃HOLD·◐·⏰┃ ⛔ IGNORED  │
│  ┃⏳7d ┃ HEDGE  ┃ SPY                       ┃ 16× $706P Aug 7 $11,905 ┃no rec   ┃ ⛔ IGNORED  │
│  ┃⏳6d ┃ CLOSE  ┃ GOOG_CALL_450_20271217    ┃ +35% ($+8,600) ltd$41.27┃BUY·🔥·⏰┃ ⛔ IGNORED  │
│  ┃ 3d  ┃ CLOSE  ┃ SOFI_PUT_14_20261016      ┃ +45% ($+67) ltd $0.86   ┃BUY·◐·4d ┃ — IGNORED  │
│  ┃ 2d  ┃ CLOSE  ┃ TWLO_PUT_180_20260821     ┃ +32% ($+545) ltd $12.29 ┃BUY·▽·⏰ ┃ — IGNORED  │
│  ┃ 2d  ┃ CLOSE  ┃ ZS_PUT_120_20261120       ┃ +41% ($+788) ltd $11.68 ┃BUY·◐·⏰ ┃ — IGNORED  │
│  ┃ 1d  ┃ CLOSE  ┃ AMZN_CALL_305_20270617    ┃ +32% ($+862) ltd $19.35 ┃TOP12·🔥 ┃ ◔ NEW       │
│  ┃ 1d  ┃ CLOSE  ┃ LITE_PUT_660_20260918     ┃ +31% ($+2,905) ltd$66.36┃BUY·▽·⏰ ┃ ◔ NEW       │
│  ┃ 1d  ┃ CLOSE  ┃ MSFT_PUT_350_20260918     ┃ +30% ($+713) ltd $17.12 ┃TOP12·🔥 ┃ ◔ NEW       │
│                                                                                  │
│  ─── BULK ACTIONS (3 selected) ─────────────────────────────────────────────────│
│  [Mark all executed]  [Defer all to tomorrow]  [Export as E*TRADE order list]   │
│                                                                                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│  KILLED ITEMS  ▾  (collapse by default)                                          │
│  Items the user overrode permanently or auto-resolved (e.g. contract closed).    │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Notes:**
- The age column uses ⏳ glyph at ≥6d (Stalled), matching the briefing's
  convention.
- Status column uses ⛔ IGNORED (≥6 sessions, blocking), — IGNORED (1-5
  sessions, soft), ◔ NEW (first surfaced today), ✓ EXECUTED, ⏸ DEFERRED, ✕
  OVERRIDDEN.
- Bulk-select with checkboxes; export to an order list is a key time-saver
  (paste into E*TRADE's basket order screen).
- The **3 selected** banner only appears when items are checked.

### 3.3 Action drill-down (`/actions/:key`)

Example: `/actions/CLOSE:AMD_PUT_420_20261218`

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ← Back to actions                                                               │
│                                                                                  │
│  CLOSE  ·  AMD_PUT_420_20261218     ⏳ IGNORED 11 DAYS (since 2026-06-15)        │
│  PUT $420  ·  Fri Dec 18 '26  ·  171d left  ·  -1 contracts                      │
│                                                                                  │
│  ┌────────────────────────────────────────┐  ┌──────────────────────────────┐   │
│  │  P&L over life of contract             │  │  PARKEV TIMELINE             │   │
│  │                                        │  │                              │   │
│  │  +50% ─────────────────────●─          │  │  May 1   BUY     ◐ Med   30d │   │
│  │  +45% ─────────────────●●●─●●          │  │  May 18  BUY     ◐ Med   12d │   │
│  │  +25% ──────────●●●●●●●●               │  │  Jun 1   TOP 12  🔥 High  4d │   │
│  │   +5% ────●●●●●●                       │  │  Jun 12  BUY     🔥 High 14d │   │
│  │    0%  ●●                              │  │  Jun 18  HOLD    ◐ Med   2d  │ ◀━ downgrade
│  │    Apr  May   Jun                      │  │  Today   HOLD    ◐ Med  18d  │ ◀━ aging
│  └────────────────────────────────────────┘  └──────────────────────────────┘   │
│                                                                                  │
│  WHY THIS FIRED                                                                  │
│  +45% of max profit captured with 171d still on contract. Remaining ~$4,152 of   │
│  theta isn't worth the gamma/gap risk for 171d more — close-at-50% rule.         │
│                                                                                  │
│  EXECUTION PLAN                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  ORDER:  BUY-TO-CLOSE 1× AMD_PUT_420_20261218 @ LIMIT $43.60               │  │
│  │  COST:   $4,360         GAIN:  $+3,413 locked         FREES: $42,000 cash  │  │
│  │  [Copy ticket to clipboard]  [Open in E*TRADE]  [Mark executed]            │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  COUNTERPOINTS                                                                   │
│  ⚖ Holding to expiration captures the remaining $4,152 — close locks 45% of     │
│    that, leaves $748 of theta on the table. The trade is "lock the win now vs   │
│    bet on 171d of theta + gap risk."                                             │
│  ⚖ Parkev downgraded BUY → HOLD 12 days ago. Catalyst is weakening.             │
│                                                                                  │
│  HISTORY OF THIS ITEM                                                            │
│  Jun 15  First flagged  ◔ NEW          (Action set to CLOSE)                    │
│  Jun 16  Re-flagged    — IGNORED                                                 │
│  Jun 17  Re-flagged    — IGNORED                                                 │
│  Jun 18  Re-flagged    — IGNORED                                                 │
│  Jun 19  Re-flagged    — IGNORED                                                 │
│  Jun 22  Re-flagged    ⛔ STALLED (6 sessions, blocking)                         │
│  Jun 24  Re-flagged    ⛔ STALLED                                                │
│  Jun 25  Re-flagged    ⛔ STALLED                                                │
│  Jun 26  Re-flagged    ⛔ STALLED                                                │
│  Jun 29  Re-flagged    ⛔ STALLED                                                │
│  Jun 30  Re-flagged    ⛔ STALLED — 11 days                                      │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 3.4 Risk (`/risk`) — the dashboard the brief has always wanted to be

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  RISK                                                                            │
│  ─────────────────────────────────────────────────────────────────────────────── │
│  [Coverage] [Concentration] [Expiration Ladder] [Hedges] [Stress Sandbox]        │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  STRESS COVERAGE — 90-day timeline                                               │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │                                                                            │  │
│  │  1.0× ────────────────────────────────────────────────────                 │  │
│  │  0.7× ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ (yellow line)        │  │
│  │  0.5× ──────────────────────────────────────────────── (red floor)         │  │
│  │       ●●●                                                                  │  │
│  │  0.3× ───●●●●●●●●●                                                         │  │
│  │  0.1× ──────────●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●  ●   │  │
│  │  0.0×                                                              today   │  │
│  │       Apr 1                                Jun 15              Jun 30      │  │
│  │                                                                            │  │
│  │  ⚠ Last above 0.50× floor: 78 days ago (Apr 13)                            │  │
│  │  ⚠ 0 of 90 sessions in green; 12 of 90 in yellow; 78 of 90 in red          │  │
│  │                                                                            │  │
│  │  [1W] [1M] [3M] [6M] [1Y] [ALL]                                            │  │
│  │                                                                            │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  WHAT WOULD CLEAR THE GATE?  (calculated from today's open positions)            │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  Close top 3 winners  →  coverage 0.06× → 0.15× (still ⛔)                  │  │
│  │  Close top 5 winners  →  coverage 0.06× → 0.21× (still ⛔)                  │  │
│  │  Close top 8 winners  →  coverage 0.06× → 0.28× (still ⛔, raises cash to  │  │
│  │                                                  21% though)              │  │
│  │  Roll 4 ITM puts to higher strikes  →  removes $137K obligation;          │  │
│  │                                        coverage projected → 0.31×          │  │
│  │  ▸ Recommended path: close top 8 + roll AAOI / IREN / NVDA puts up        │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 3.5 Expiration ladder (`/risk/ladder`)

Calendar heatmap, cell intensity = put obligation as % NLV at that expiry.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  EXPIRATION LADDER — short put obligation by Friday                              │
│  ─────────────────────────────────────────────────────────────────────────────── │
│                                                                                  │
│            Jul        Aug        Sep        Oct        Nov        Dec    Jan'27 │
│  Wk 1     ░░░░░     ░░░░░     ░░░░░     ░░░░░     ░░░░░     ░░░░░     ░░░░░   │
│  Wk 2     ░░░░░     ░░░░░     ░░░░░     ░░░░░     ░░░░░     ░░░░░     ░░░░░   │
│  Wk 3   ░░░Jul24░░░ ░░░░░     ░░░░░     ░░░░░     ░░░░░     ░░░░░     Jan15    │
│         11% NLV     ░░░░░     ░░░░░     ░░░░░     ░░░░░     ░░░░░    ░░░13%░░  │
│  Wk 4     ░░░░░    Jul31     Aug21     ░░Oct16░░ Nov20      Dec18     ░░░░░   │
│           ░░░░░    ░░3%░░   ░██21%██░  ░░14%░░  ░██24%██░   ░░4%░░   ░░░░░   │
│  Wk 5                Aug07     Aug28      Oct23      Nov27      Dec24            │
│                    ░░4%░░     ░░░░░     ░░░░░     ░░░░░     ░░░░░               │
│  Wk 6                          Sep18                                              │
│                              ███30%███                                            │
│                              ⚠ APPROACHING 30% CAP                                │
│                                                                                  │
│  Legend: [░ <5%]  [░░ 5-10%]  [░░░ 10-20%]  [██ 20-30%]  [███ ≥30%]              │
│                                                                                  │
│  ─── HISTORY OF >30% BUCKETS ───────────────────────────────────────────────────│
│  Last 90 days, any Friday bucket ever crossed 30% NLV:                          │
│  • 2026-05-15 bucket peaked at 31.2% on May 8  (cleared May 12, roll)           │
│  • 2026-06-19 bucket peaked at 28.7% on Jun 12  (stayed yellow)                 │
│  • 2026-09-18 bucket trending: 18% (3w ago) → 23% (2w ago) → 29.6% (today)      │
│    ↑ click trajectory for full daily history                                    │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

Click any cell → drill-down to all puts expiring that Friday, with per-position
ITM status, days left, P&L, and an inline "roll out" calculator.

### 3.6 Ticker drill-down (`/positions/:ticker`) — example: `/positions/NVDA`

This is the page the user spends the most time on after the morning triage.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  NVDA  ·  NVIDIA Corp.                                                           │
│  $197.76  ▲ $+0.43 (0.22%)  ·  RSI 43  ·  IV rank 64  ·  5d -3.2%               │
│  Position: 701 sh · MV $138,631 · 13.9% NLV  ⚠ OVER 10% CAP                      │
│  🅿️ TOP 12 · 🔥 High · ⏰ 15d   FV: DCF — / PT $245 (+24%, n=42)                 │
│  ─────────────────────────────────────────────────────────────────────────────── │
│  [Overview] [Price] [Options] [Briefing mentions] [Parkev history]              │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  PRICE  (left)                              POSITION HISTORY  (right)            │
│  ┌──────────────────────────────────┐  ┌──────────────────────────────────────┐ │
│  │  $250 ─                          │  │  Mar 12  Buy 200 sh @ $94.30 (Tier 4)│ │
│  │       │                          │  │  Apr 03  Buy 150 sh @ $112.80         │ │
│  │  $200 ──────────────────●●●●●●  │  │  May 17  CSP open $185P Sep 18 / +1.4K│ │
│  │   200-SMA ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄  │  │  May 28  CC open $220C Aug 21 / +0.9K │ │
│  │  $150 ──────●●●●●●●●●●          │  │  Jun 04  CSP open $200P Sep 18 / +1.2K│ │
│  │   50-SMA  ┄┄┄┄●●●┄┄┄┄┄┄┄┄┄┄┄┄  │  │  Jun 18  CC closed +$1.1K profit      │ │
│  │  $100 ●●●                        │  │  Jun 30  Parkev: BUY→TOP12 (Jun 12)  │ │
│  │       Mar Apr May Jun            │  │  ──────────                          │ │
│  │                                  │  │  Wheel premium YTD: +$8,420          │ │
│  │  S: $190 (200-SMA, daily pivot)  │  │  Equity P&L YTD:    +$72,318 (+109%) │ │
│  │  R: $200 (daily pivot)           │  │  Total:             +$80,738         │ │
│  │  R: $210 (50-SMA)                │  │  Buy-and-hold counterfactual: $79,420│ │
│  │                                  │  │  Wheel alpha: +$1,318 (+1.7%)        │ │
│  └──────────────────────────────────┘  └──────────────────────────────────────┘ │
│                                                                                  │
│  OPEN OPTIONS (2 short puts)                                                     │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  NVDA_PUT_200_20260918   PUT $200  Sep 18 '26  ⚠ ITM   P&L +$4  80d        │  │
│  │  NVDA_PUT_185_20261120   PUT $185  Nov 20 '26  OTM     P&L +$112 143d      │  │
│  │  → [See roll analysis]                                                     │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  BRIEFING MENTIONS  (last 90 days, 67 mentions)                                  │
│  Today    HOLD  · concentration warn · roll-out suggested                       │
│  Jun 29   HOLD  · concentration warn                                            │
│  Jun 26   HOLD  · CC strategy upgrade (wait for strength)                       │
│  Jun 22   HOLD                                                                   │
│  Jun 19   HOLD                                                                   │
│  …       [Search briefing mentions]                                              │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

**Design notes:**
- The **Price chart** is annotated with every open / close / roll on this
  ticker — like Composer.trade's position-aware chart. The user instantly
  sees that he entered NVDA puts at $185 on Jun 4 when spot was $208.
- The **Wheel alpha** number is the killer feature: "Did the wheel beat just
  holding shares?" Computed from the closed trades ledger + the equity P&L.
- **Briefing mentions** is a per-ticker grep across all dated briefings,
  showing recommendation changes day-by-day.

### 3.7 Recommendations (`/recommendations`) — Parkev sheet view

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  PARKEV'S SHEET                          Last fetched: Tue 8:01 AM (12m ago) ✓   │
│  ─────────────────────────────────────────────────────────────────────────────── │
│  247 names · 21 BUY / 26 CSP / 36 AVOID / 164 HOLD/NEUTRAL                       │
│  [All] [In portfolio (13)] [Watchlist (42)] [Recent changes (8)] [Aging ≥14d]   │
│  Filter: Rating [All ▾]  Conviction [All ▾]  Age [All ▾]  Sector [All ▾]        │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Ticker  Rating       Conv     Age    In Portfolio?    7d trend   FV vs spot     │
│  ──────────────────────────────────────────────────────────────────────────────  │
│  AMZN    TOP 12 ★★★★  🔥 High  ⏰29d   ✓ 100 sh + CSP   ━ unchanged  PT +32%     │
│  META    TOP STOCK ★★★★★ 🔥High ⏰20d   ✓ 110 sh        ▼ from TOP 12 PT +60%    │
│  MSFT    TOP 12 ★★★★  🔥 High  13d     ✓ 241 sh + CSP   ━ unchanged  PT +45%     │
│  NVDA    TOP 12 ★★★★  🔥 High  ⏰15d   ✓ 701 sh + 2CSP  ━ unchanged  PT +24%     │
│  GOOG    BUY ★★★      🔥 High  ⏰28d   ✓ 436 sh + 2CSP  ━ unchanged  —           │
│  PLTR    BUY ★★★      🔥 High  4d      ✓ 752 sh        ━ unchanged  PT +52%      │
│  AMD     HOLD ★★      ◐ Med    ⏰18d   ✓ AMD_PUT_420    ▼ from BUY (Jun18) -91% │
│  TSLA    SELL ✕      🔥 High  4d      ✓ 100 sh        ▼ from HOLD (Jun26)  —    │
│  VRT     HOLD ★★      ▽ Low    ⏰46d   ✓ 138 sh + 2CSP  ▼ from BUY (May15)  —    │
│  ZS      BUY ★★★      ◐ Med    ⏰34d   ✓ 100 sh + CSP   ━ unchanged  —           │
│  …                                                                               │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

Click any row → drilldown timeline of every Parkev change for that name.

### 3.8 Performance (`/performance`) — wheel ROI vs counterfactual

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  PERFORMANCE                                                                     │
│  ─────────────────────────────────────────────────────────────────────────────── │
│  Period: [MTD] [QTD] [YTD ●] [1Y] [Custom]    Comparison: vs Buy-and-Hold ●     │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  NLV  YTD  +$172,400  (+20.8%)                                                   │
│  Buy-and-hold equivalent  +$158,420  (+19.2%)                                    │
│  ▸ WHEEL ALPHA: +$13,980  (+1.6 pp)                                              │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │                                                                            │  │
│  │  $1.05M ──────────────────────────────────────────●  NLV (actual)          │  │
│  │                                              ────●●●●  B&H counterfactual  │  │
│  │  $1.00M ●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●●                              │  │
│  │                                                                            │  │
│  │  $0.95M                                                                    │  │
│  │         Jan  Feb  Mar  Apr  May  Jun                                       │  │
│  │                                                                            │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌──────────────────────────┐  ┌──────────────────────────────────────────────┐  │
│  │  WHEEL ECONOMICS         │  │  PREMIUM CALENDAR (heatmap)                  │  │
│  │                          │  │                                              │  │
│  │  Premium collected  $58K │  │  M T W T F                                   │  │
│  │  BTC paid          -$32K │  │  ░ ░ ░ ░ █  Jan  ($4,200 captured wk1)       │  │
│  │  Assignments        -$8K │  │  ░ ░ ▓ ░ █  Jan  ($3,800)                    │  │
│  │  Net wheel income   $18K │  │  …                                           │  │
│  │                          │  │  ░ ░ █ ░ █  Jun wk5                          │  │
│  │  Hedge cost         -$4K │  │                                              │  │
│  │                          │  │  Heat = $ captured per day                   │  │
│  └──────────────────────────┘  └──────────────────────────────────────────────┘  │
│                                                                                  │
│  ┌────────────────────────────────────────────────────────────────────────────┐  │
│  │  CLOSED TRADES LEDGER                                  [Filter] [Export]   │  │
│  │  ─────────────────────────────────────────────────────────────────────────│  │
│  │  Date     Contract                        Open    Close   P&L     Held   │  │
│  │  Jun 18  NVDA_PUT_185_20260619           +$890   -$0     +$890   45d    │  │
│  │  Jun 12  AMZN_PUT_220_20260612           +$540   -$120   +$420   32d    │  │
│  │  Jun 05  GOOG_CALL_375_20270319          +$1240  -$310   +$930   78d    │  │
│  │  …                                                                       │  │
│  └────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 3.9 Journal (`/journal`)

Two-pane: left = date list (last 90+ briefings), right = rendered briefing.
"Compare" button lets you select any two dates to see structured diff (which
recs were added/removed/changed). Search box scopes across all briefings.

```
┌────────────────────┬─────────────────────────────────────────────────────────────┐
│  JUNE 2026         │  briefing_2026-06-30.md            [Compare to: 2026-06-15] │
│                    │                                                              │
│  ● 30 Tue (today)  │  Header                                                     │
│    29 Mon          │    NLV: $999,629    cash 4.4%    coverage 0.06× ⛔          │
│    26 Fri          │    [+15] new items vs Jun 15      [-3] resolved              │
│    25 Thu          │                                                              │
│    24 Wed          │  CHANGES vs Jun 15:                                          │
│    22 Mon          │  ─────────────────────────────────────────                   │
│    19 Fri ←━━ pivot│  NEW   3× CLOSE recs added (LITE, MSFT-PUT, AMZN-CALL)       │
│    18 Thu          │  NEW   1× HEDGE rec for SPY ($706P)                          │
│    17 Wed          │  GONE  CSP entry blocks lifted on 2 names (then re-blocked)  │
│    16 Tue          │  CHGD  AMD: BUY → HOLD                                       │
│    15 Mon          │  CHGD  Coverage: 0.31× → 0.06× (significant drop)            │
│  ▶ MAY 2026        │  CHGD  Sep 18 bucket: 23.4% → 29.6%                          │
│  ▶ APRIL 2026      │  CHGD  Cash: 12.1% → 4.4% (margin call event Jun 19)         │
│                    │                                                              │
│  Search: [______]  │  [Full diff →]                                               │
└────────────────────┴─────────────────────────────────────────────────────────────┘
```

---

## 4. Component Library

These components are reused across pages. Implementation-friendly names below.

| Component | Description | Props (key ones) |
|---|---|---|
| **TopBar** | Persistent header pills (date, NLV, cash, coverage, ⌘K) | `nlv, cashPct, coveragex, regime` |
| **NavRail** | Left-rail nav with badges | `currentPath, badgeCounts` |
| **MetricPill** | Inline metric with trajectory chip ▲▼━ + click → 30d sparkline | `label, value, prev, format` |
| **ParkevChip** | The chip from rule #27. Renders `🅿️ RATING · CONV · AGE`. Hover = mini-timeline. Click = ticker drill-down. | `rating, conviction, ageDays, ticker` |
| **TierBadge** | Shows ★★★★★ tier (5=Top Stock, 4=Top 15, 3=Buy, 2=Borderline, 1=Hold, 0=Sell) | `tier` |
| **ConvictionDot** | 🔥 / ◐ / ▽ for High/Med/Low | `conviction` |
| **AgingPill** | `⏰ 18d` (with clock when >14d) or `5d` (no icon) | `ageDays` |
| **ActionRow** | One queued action in the action list | `action, onExecute, onDefer, onOverride` |
| **AgePill** | `⏳ 11d` for IGNORED, ◔ for NEW. Color: red ≥6d, gray <6d. | `daysSinceFirst, status` |
| **RsiBadge** | `RSI 62 🟡 extended` — color from band (green pullback / yellow extended / red overbought) | `rsi14, band` |
| **FvNote** | `💵 FV: DCF $50 (-91%) · PT $477 (-15%, n=28)` or `FV: n/a — basket (ETF)` | `dcf, pt, ptCount, spot, kind` |
| **SrLine** | `↳ S: $190 (200-SMA, 3 touches) · R: $210` | `supports, resistances` |
| **CapacityGate** | Number-line visualization of one gate (coverage / cash / obligation). The dot's x-position is computed; the gate threshold is a vertical line. | `metric, value, thresholds, status` |
| **GateBanner** | "🔒 CAPACITY: New CSP entries blocked …" full-width yellow card | `gateState, reasons[]` |
| **StalledCard** | The top-of-page red callout for ⛔ stalled items | `stalledItems[]` |
| **DiffChip** | `▼ from BUY (Jun18)` or `━ unchanged` or `▲ to TOP 12` | `before, after, dateChanged` |
| **PositionRow** | One row in the positions table | `position, recommendation` |
| **ContractRow** | One row in the options table; expands to show roll analysis | `contract, rollCandidates[]` |
| **RollAnalysisTable** | The 5-candidate roll table (id / Action / Net / Notes / ✅ recommended) — same shape as the briefing's markdown table | `candidates[], recommendedId` |
| **CandidateCard** | One card from the 42 Candidate Trades | `candidate, capacityState` |
| **DeferredTag** | `⏸ Deferred (capacity gated)` | `reason` |
| **ChangedSinceCard** | "What changed vs yesterday" with bullets like `AMD: BUY → HOLD` | `additions[], removals[], changes[]` |
| **CounterpointCallout** | `⚖ Counterpoint:` block under an action — from rule #14 | `counterpoints[]` |
| **ConvictionTimeline** | Horizontal timeline of Parkev rating + conviction changes for one ticker. Each marker labeled with date + rating. Hover for full row. | `events[]` |
| **PnlChart** | Position P&L sparkline / full-size, annotated with open/roll/close markers | `series, annotations[]` |
| **MetricTimeline** | Generic line chart for NLV / coverage / cash / obligation / drawdown over time | `series, thresholds[], rangePicker` |
| **ExpirationHeatmap** | Calendar grid of Fridays, cell = $ obligation, intensity 5 bands | `bucketsByDate, capPct` |
| **ConcentrationStacked** | Stacked area chart of % NLV by ticker over 90 days. Each band a ticker. | `series[]` |
| **CapacityScenarios** | "If you closed X, coverage goes Y → Z" cards | `closeOptions[]` |
| **BriefingDiffPane** | Renders structured diff between two briefing JSONs | `before, after` |
| **TickerBreadcrumb** | Small header bar present on any ticker-scoped page: ticker, price, key tags | `ticker` |
| **CommandPalette** | ⌘K modal — fuzzy search across tickers / contracts / dates / actions | — |
| **EmptyState** | "Nothing here yet" with iconography matched to context | `kind, message` |

---

## 5. Visual / Design Language

### 5.1 Extending the existing chip vocabulary

The web app **must reuse** the briefing's vocabulary. Hard rule:

| Concept | Glyph | Source (briefing rule) |
|---|---|---|
| Parkev attribution | `🅿️` | rule #27 |
| Conviction High | `🔥` | rule #26 |
| Conviction Medium | `◐` | rule #26 |
| Conviction Low | `▽` | rule #26 |
| Top Stock (tier 5) | `★★★★★` or `TOP STOCK` | rule #27 ladder |
| Top 12/15/25 (tier 4) | `★★★★` or `TOP 12` | rule #27 ladder |
| Buy (tier 3) | `★★★` or `BUY` | rule #27 ladder |
| Borderline Buy (tier 2) | `★★` or `BDL BUY` | rule #27 ladder |
| Hold (tier 1) | `★` or `HOLD` | rule #27 ladder |
| Sell (tier 0) | `✕` or `SELL` | rule #27 ladder |
| Stale (>14d) | `⏰` | rule #27 aging |
| Stalled (≥6 sessions ignored) | `⏳` + days count | briefing convention |
| Candidate (RSI-favorable actionable) | `🎯` | scout / candidates |
| Deferred (capacity gated) | `⏸` | rule #24 |
| Tradeable | `🟢` (entry now) | when-to-enter taxonomy |
| Wait / soft | `🟡` | when-to-enter |
| Avoid / blocked | `🔴` | when-to-enter |
| RSI overbought | `🟡 overbought` | rule #11 |
| RSI extended | `🟡 extended` | rule #11 |
| RSI pullback (favored) | `🟢 pullback` | rule #11 |
| RSI mid-range | `🟡 mid-range` | rule #11 |
| RSI oversold | `🟡 oversold` | rule #11 |
| Hedge | `🛡️` | briefing |
| Stress / risk | `🔻` `📊` `⚠` | briefing |
| Fair value note | `💵 FV:` | rule #12 |
| Support / Resistance | `↳ S: … · R: …` | rule #20 |
| Counterpoint | `⚖` | rule #14 |
| Action verdict prefix | `🎬 Action:` | rule #15 |

**New additions (only when absolutely needed):**

| New glyph | Meaning | Justification |
|---|---|---|
| `▲ ▼ ━` | Day-over-day trajectory chip on metrics | The text briefing renders one snapshot; the web app inherently compares. Standard arrows; no conflict with existing vocabulary. |
| `◔` | Newly-surfaced item (just appeared today) | Useful in action list to distinguish "new today" from "re-flagged". |
| `✓ ✕` | Status pills in queue (executed / overridden) | Standard, no conflict. |

### 5.2 Color palette

Inspired by Bloomberg + the briefing's discipline tone. NOT Robinhood green/red.

```
Background      Slate 950   #0B1220   (dark mode primary; the user is at a
                                       desk every morning, dark mode reduces eye fatigue)
Surface         Slate 900   #111827
Surface-alt     Slate 800   #1F2937
Border          Slate 700   #334155
Text-primary    Slate 100   #F1F5F9
Text-secondary  Slate 400   #94A3B8
Text-muted      Slate 500   #64748B

Accent (chips / links / focus rings):
Brand           Cyan 400    #22D3EE   (used very sparingly; never on a chart line
                                       to avoid confusion with "data" colors)

Discipline traffic light (used CONSISTENTLY — never re-purpose):
Green/clear     Emerald 400 #34D399   ✓ gate clear, RSI favourable, execution-ready
Yellow/caution  Amber 400   #FBBF24   ⚠ approaching cap, mid-range RSI, stale
Red/blocked     Rose 500    #F43F5E   ⛔ gate failed, ITM puts, stalled ≥6d
Purple/info     Violet 400  #A78BFA   ⓘ informational (e.g., "what changed today")

Conviction (NEVER use traffic-light colors here, to keep them orthogonal):
High            Orange 500  #F97316   🔥
Medium          Slate 300   #CBD5E1   ◐
Low             Slate 600   #475569   ▽

Tier ladder (gradient from cold to hot — orthogonal again):
Tier 0 (Sell)   Rose 700    #BE123C
Tier 1 (Hold)   Slate 500   #64748B
Tier 2 (BdlBuy) Sky 600     #0284C7
Tier 3 (Buy)    Sky 400     #38BDF8
Tier 4 (Top12)  Indigo 400  #818CF8
Tier 5 (TopSt)  Fuchsia 400 #E879F9
```

**Chart colors** (data series, max ~8 distinct):

```
#22D3EE cyan   #FBBF24 amber   #34D399 emerald   #A78BFA violet
#F472B6 pink   #60A5FA blue    #FB923C orange    #94A3B8 slate
```

Coverage / cash / NLV trend lines use **cyan**; threshold lines use red/yellow
ghosted bars (not solid colors that compete with the series).

### 5.3 Typography

```
UI font           Inter (system fallback: -apple-system, BlinkMacSystemFont)
Mono / numerics   JetBrains Mono (system fallback: 'SF Mono', Menlo, monospace)
```

**Why mono for numerics?** Every dollar value, percentage, and ratio is in a
table or chip; tabular figures align. Inter has tabular nums (`font-variant-numeric: tabular-nums;`) — apply globally to numeric-bearing components.

**Type scale:**

| Use | Size | Weight | Example |
|---|---|---|---|
| Display | 32px / 40px LH | 600 | "NLV $999,629" on Today header |
| H1 | 24px / 32px | 600 | Page titles |
| H2 | 20px / 28px | 600 | Section headers |
| H3 | 16px / 24px | 600 | Sub-section |
| Body | 14px / 20px | 400 | Default text |
| Small | 13px / 18px | 400 | Metadata, captions |
| Mono-num | 14px / 20px | 500, tabular | Dollar/% figures |
| Code | 13px / 18px | 400 | Contract symbols (AMD_PUT_420_20261218) |

### 5.4 Spacing & layout

8pt grid. Cards use **16px** internal padding (12px on mobile). Sections separated
by **24px** vertical gap. Page max width **1440px** (the user's MBP is ~1512px).

### 5.5 Iconography

Use [Lucide](https://lucide.dev) for UI icons (settings, search, filters, chevrons).
DO NOT use emoji for UI affordances — emoji is reserved for the discipline
vocabulary (Parkev, conviction, RSI). Mixing the two would dilute the
discipline glyphs' meaning.

Examples:
- Search → `<Search />` (lucide)
- Filter → `<SlidersHorizontal />`
- Settings → `<Settings2 />`
- Chevron → `<ChevronRight />`
- External link → `<ArrowUpRight />`
- Copy → `<Copy />`

---

## 6. Interaction Patterns

### 6.1 Drill-down

Every metric is clickable. Click → drill-down page.

| Click on | Goes to |
|---|---|
| Ticker (anywhere) | `/positions/:ticker` (or `/candidates/:ticker` if no position) |
| Contract symbol | `/positions/:contract` |
| Parkev chip | `/recommendations#:ticker` |
| Coverage pill (top bar) | `/risk/coverage` |
| Cash pill | `/risk` (highlight cash section) |
| NLV pill | `/performance` |
| Action row "Full detail" | `/actions/:key` |
| Risk alert | Risk page, scrolled to that alert |
| Date label | `/journal/:date` |

### 6.2 Time-range picker

Standard chip group on every chart: `[1W] [1M] [3M ●] [6M] [1Y] [ALL]`. Default
`3M` (90 days = ~one quarter, matches the available snapshot depth).

Range syncs across charts on the same page (so opening Risk = all four risk
charts share the range).

### 6.3 Comparison views

On Journal: select date A and date B → structured diff.

On Performance: dropdown `Comparison: [vs Buy-and-Hold ●] [vs SPY] [vs Vanilla Wheel] [None]`. The
counterfactual line is dashed and 60% opacity to distinguish from actual NLV.

### 6.4 Hover states

- Hovering a **Parkev chip** opens a 200ms-delay popover with: 30d
  rating-and-conviction timeline, last 3 rating changes, link to drill-down.
- Hovering a **conviction dot** (`🔥`) shows the verbal "High conviction" tooltip.
- Hovering a **sparkline** shows crosshair + value at that point.
- Hovering an **action row** highlights it with a 1px brand-cyan left border.
- Hovering a **gate visualization** dot shows the precise value and "X days
  below floor".

### 6.5 Keyboard shortcuts

- `⌘K` → command palette
- `g t` → Today
- `g a` → Actions
- `g r` → Risk
- `g p` → Positions
- `g c` → Candidates
- `j / k` → next / previous action in queue
- `e` → mark current action executed
- `d` → defer current action 1 day
- `o` → override current action
- `/` → focus search

### 6.6 Empty / loading / error states

- **Loading:** Skeleton rows (8 placeholder bars) — NOT spinners. The data is
  local file system, so this should be near-instant; spinners imply a wait.
- **Empty action queue:** "✓ Nothing to do. Capacity gates are clear." (rare
  given current state).
- **API/data missing** (e.g. FMP key not set): mirror the brief's fail-closed
  behavior. Show `FV: n/a (no FMP data)` not a fabricated number.
- **No briefing for date:** Journal shows "No briefing generated this date —
  weekend or holiday."

### 6.7 Destructive / irreversible actions

The user is sole consumer, but the action queue tracks ⛔ STALLED state and
that state's reset is meaningful. So:

- `Mark executed` — confirm modal: "Mark CLOSE AMD_PUT_420 as executed at
  E*TRADE? This will remove it from the queue."
- `Override — never re-flag` — confirm modal with a **required reason text field**:
  "Why are you overriding? This will silence future re-flags of this exact
  recommendation." Stored to a directives YAML.
- `Defer 1 day` — no confirm, undo toast appears for 5s.

### 6.8 Reading-then-doing flow

The morning happy path:

1. Land on `/` → see Stalled card at top, capacity gates, action list.
2. Click first stalled action → drill-down page → review history & counterpoints.
3. Click `Copy ticket to clipboard` → ticket text on clipboard.
4. Switch to E*TRADE in another tab, paste, place, confirm.
5. Switch back to app, click `Mark executed`. Item gone from queue.
6. Repeat 2-5 for remaining items.
7. Glance Risk / Concentration / Recs for outliers.
8. Close laptop.

Total clicks for 5 executions: ~20. Same workflow on the markdown takes ~50
clicks (open editor, scroll, copy strikes manually, etc.) plus mental tracking
of which items he already did.

---

## 7. Killer Features (the "why a web app, not just a markdown viewer" list)

These are the features that go **beyond** what the text briefing can do. Each is
specific enough to land in a sprint.

### 7.1  Conviction Timeline (`/recommendations/:ticker`)

Horizontal timeline of every Parkev change for a single name over the last
12 months. Each marker = (date, rating, conviction). Y-axis = tier ladder
(0-5). Color = conviction. Hover reveals the row from that date's
`recommendations_list.json`.

**Why:** The Parkev chip encodes "today's" stance in one cell. The user has
~140 snapshots — every chip change is a real signal. AMD's `BUY → HOLD` 12
days ago materially weakened the catalyst for the open `AMD_PUT_420`, but
that change is invisible in today's brief. Timeline makes it visible.

**Data source:** `briefing_snapshots/*/recommendations_list.json` →
group by ticker → sort by date.

### 7.2  Decision Retro (`/actions?view=retrospective`)

For every action ever surfaced, mark whether the user executed (✓), overrode
(✕), let auto-resolve (⌛), or punted indefinitely (⛔). Then for each
EXECUTED close, compute: would the user have made more money holding?

Output: a scoreboard.

```
Last 90 days  ·  214 actions surfaced
─────────────────────────────────────────────
Executed       127  (59%)    net +$18,420
Overridden      24  (11%)    "  +$3,180 (those positions improved/expired benignly)
Punted ≥6d      18  ( 8%)    "  -$2,840 (delay cost; mostly missed closes)
Auto-resolved   45  (21%)    "  +$9,100

Discipline score: 89/100  ▲ from 84 last quarter
```

**Why:** The user values discipline. Quantifying it (per rec, per quarter)
turns "I should close stalled items faster" into a measured behavior.

### 7.3  Aging Audit (`/actions/aging`)

A flat sorted list of all open recs by `days_flagged`, showing the conviction
of the underlying name, the dollar value at stake, and whether the rec has
gotten more or less urgent since it was flagged.

```
⏳11d  CLOSE  AMD_PUT_420       +$3,413  Parkev: BUY→HOLD (worse since flagged)
⏳ 7d  HEDGE  SPY               $11,905   Capacity: deteriorated (worse)
⏳ 6d  CLOSE  GOOG_CALL_450     +$8,600  Parkev: unchanged
⏳ 3d  CLOSE  SOFI_PUT_14       +$67     Parkev: unchanged
⏳ 2d  CLOSE  TWLO_PUT_180      +$545    Parkev: unchanged
```

The "worse since flagged" column is the alpha. If the underlying signal
deteriorated while the user dithered, the action should escalate visually.

### 7.4  Tier Drift Detector (alerts surface on `/` Today)

The user's rule (encoded in CLAUDE.md hard rule #27): a Tier 4 (TOP 12) name
that degrades to Tier 1 (HOLD) should be re-evaluated. The app auto-detects
these drift events overnight and surfaces them in a "What changed" card the
next morning.

```
▼ TIER DRIFT — META: TOP STOCK → TOP 12 (Jun 22)
  Holding: 110 sh + 0 CSP + 0 CC. Position weight 6.1%.
  Suggested: review whether to lighten or just acknowledge the rating change.
```

### 7.5  Coverage Timeline + Sandbox (`/risk/coverage`)

Already wireframed in §3.4. Two-panel:
- 90-day coverage chart with floor (0.50×) and target (0.70×) overlaid.
- A "what-if" sandbox: check checkboxes next to open positions to simulate
  closing them. Coverage recomputes in real-time. Result: "if I close these 8
  items, coverage goes 0.06× → 0.34×". This is the decision-support tool the
  briefing's "Top 3 Closes to Raise Coverage" hint gestures at but can't
  interactively explore.

**Why:** Coverage is the single hardest gate to clear right now. Letting the
user simulate the path forward (close X + roll Y) on the same page where
they see the gate is the highest-leverage interaction in the app.

### 7.6  Expiration Cluster Heatmap (`/risk/ladder`)

Already wireframed in §3.5. Calendar heatmap of put obligation by Friday,
with historical bands showing which Fridays ever crossed the warning (20%)
and critical (30%) thresholds.

**Why:** Hard rule #21 makes expiration-bucket concentration a separate red
flag from stress coverage — and the briefing fires it textually, but a
heatmap is the natural visual for "calendar concentration." Lets the user
see at a glance that Sep 18 is loaded and Oct 16 is also approaching.

### 7.7  Concentration Creep Visualizer (`/risk/concentration`)

Stacked-area chart of % NLV by ticker over 90 days. Each ticker is a band.
Bands above the 10% cap line are shaded with a hatched pattern.

```
% NLV
30% ─────────────────────────────────────────────
        ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓ GOOG (15%)
20% ─────────────────────────────────────────────
        ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒ NVDA (14%)
        ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ MSFT (9%)
        ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ PLTR (9%)
10% ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ ← 10% cap
         …
 0%
        Apr 1                          Jun 30
```

GOOG and NVDA bands have grown steadily over 90 days; the user can see drift
visually rather than discovering it once it crosses a threshold.

### 7.8  Replay Mode (`/journal/:date/replay`)

Pick any past date and see exactly what the app would have shown that
morning, with all the same chip colors, gates, alerts. Useful for retros:
"On Jun 19 (margin call day), what did the brief say the morning before?"

### 7.9  Wheel Alpha Calculator (`/performance`)

Already wireframed in §3.8. Computes:

```
Wheel alpha = (actual NLV YTD) − (what NLV would be if every share had been
              bought-and-held with zero options activity from Jan 1)
```

Decomposed by ticker. Lets the user answer: "Is the wheel actually earning
its complexity, or am I just trading?"

### 7.10  Ticker Timeline Search (`/positions/:ticker?tab=mentions`)

"Show me everything every briefing has ever said about NVDA." Grep across all
~140 markdown briefings, render as a chronological list with each mention's
verdict (HOLD / WATCH / etc), expandable to show the full block from that
day's briefing.

### 7.11  Capacity-Open Watchlist (`/candidates?view=ready-when-open`)

The 42 candidates today are all ⏸ DEFERRED. The web app reframes them as
"queued for when capacity reopens" with ETA estimates:

```
🎯 CRWV $95.38   SELL CRWV $86P Jul 31 $5.50
   Estimated capacity-reopen: after closing 5-8 winners
   This rec has been re-surfaced 4 days running
```

Bonus: a "ready to fire" trigger system — when stress coverage rises back
through 0.50× (an event the app can detect in real-time vs daily), highlight
the top N candidates as immediately actionable.

### 7.12  Daily Diff Notification (`/journal/diff/:date`)

When the user opens the app, show a small banner if the latest briefing has
material changes vs yesterday's. Material = any of:
- A new ⛔ stalled item
- A coverage state-change (clear → caution, caution → blocked, etc.)
- Any Parkev rating change ≥1 tier on a held name
- A new entry in put-bucket-critical
- Any concentration crossing 10% / 12% / 15%

Click → side-by-side diff (BriefingDiffPane component).

---

## 8. Specific Design Decisions (answers to the spec's open questions)

### Q1. What's above the fold on Today?

**Stalled items + Action list + Capacity gates.** Health metrics are below.
Rationale: the question the user wakes up asking is "what do I do?", not
"what's my number?" The user already trusts the system; he doesn't need to
re-read his NLV every morning to feel okay. But he does need to know he has
3 things punted from previous days. Stalled items are the system shouting
at him.

### Q2. When line vs candlestick vs sparkline vs heatmap?

| Chart type | Use for |
|---|---|
| **Sparkline** | Inline trajectory chips (NLV, coverage, cash on the top bar; per-row in tables). Width 80-120px, height 24px, no axes. |
| **Line** | Single time series with thresholds (coverage history, cash history, drawdown). Always show the gate floors as colored horizontal bands. |
| **Stacked area** | Concentration (% NLV by ticker over time). |
| **Heatmap** | Expiration ladder (calendar grid, color = % NLV); premium calendar in performance. |
| **Annotated line** | Single-ticker price chart with open/close/roll markers (Robinhood-Gold-style position-aware chart). |
| **Candlestick** | NOT used. The user doesn't trade intraday; OHLC adds visual noise without decision support. |
| **Bar** | YTD performance comparison (wheel vs B&H), bucket histograms. |
| **Sankey** | NOT v1, but tempting for "where did the wheel income come from" (premiums in → BTC costs out → assignments out → net). Park for v2. |

### Q3. Parkev chip — inline or popover?

**Both.** Inline as the rendered chip (`🅿️ TOP 12 · 🔥 High · ⏰ 15d`) — same
format as the markdown. On hover (200ms delay), popover shows:

```
┌─ NVDA — Parkev's view ────────────────────┐
│  Today    TOP 12 · 🔥 High · ⏰ 15d        │
│                                            │
│  Recent changes (last 90d):                │
│    Jun 12  BUY → TOP 12                    │
│    May 28  Conviction Med → High           │
│    May 01  HOLD → BUY                      │
│                                            │
│  [Full Parkev timeline →]                  │
└────────────────────────────────────────────┘
```

Click → goes to `/recommendations#NVDA` for full timeline.

### Q4. Tier framework — how to make "Tier A, don't write CCs" obvious?

The tier ladder is encoded in the chip itself (`TOP 12 ★★★★`), but the
**"don't write CCs"** rule is a derived constraint. Render it as a
**badge on the position card**:

```
┌─ NVDA   701 sh · $138,631 · 13.9% NLV ──┐
│  🅿️ TOP 12 · 🔥 High · ⏰ 15d            │
│  🔒 Core (Tier 4+) — no CC writes        │ ← derived badge
│  ⚠ Over 10% NLV cap                      │
└──────────────────────────────────────────┘
```

The Strategy Upgrades page on a CC suggestion that would violate this rule
gets gated automatically (already enforced in the briefing for core
positions per CLAUDE.md hard rule "Short calls on core — never CLOSE on
loss-stop").

### Q5. Action urgency — today / this week / monitor?

Three states, three glyphs, three colors:

| State | Glyph | Color | Where |
|---|---|---|---|
| Do today | (no glyph; just position in the top action list) + ⛔ if stalled ≥6d | Red text on age ≥6d | Today's action list |
| This week | `📊` | Yellow border | Risk Alerts panel |
| Monitor | `📌` | Gray text | Position review / Watch list |

The user mentioned the briefing currently lacks a "today vs this week" split — the
app does it via where the item is rendered (top section vs lower-priority
panel) rather than via more glyphs.

### Q6. Mobile?

**Desktop-first, but a usable read-only mobile view for in-meeting checks.**

- Mobile: 5-tab bottom nav (Today, Actions, Risk, Positions, More). No
  drill-down to ticker pages from mobile (those have density that doesn't
  work on a 380px viewport). Read-only: no "execute" buttons on mobile
  (user shouldn't be hitting buttons in a meeting; if it's urgent he opens
  laptop).
- Today on mobile = Stalled cards + Action list (collapsed, expandable) +
  Capacity gates + What Changed. No charts beyond 24px-height inline
  sparklines.
- All metrics in landscape become legible; portrait shows stacked cards.

---

## 9. Tech notes for the engineer

### Stack suggestions

- **Framework:** Next.js 14 (App Router) or Astro + React islands. Static
  routes; data is local-file-system, not a backend. Server actions only for
  mutations (mark-executed, defer, override) which write to a local
  directives YAML.
- **Data layer:** Read `briefing_snapshots/*.json` at build/request time;
  cache aggressively (data only changes once/day at 8am).
- **Chart library:** [Visx](https://airbnb.io/visx/) (D3-based, primitives,
  full control) or [Recharts](https://recharts.org/) if you want batteries-included.
  Avoid Chart.js (canvas-only) — the user wants crosshairs and annotations
  that benefit from SVG.
- **State:** Zustand or simple React Context. No Redux (too heavy for single
  user).
- **Theming:** CSS variables for the palette in §5.2. Dark mode default,
  light mode optional (off by default).
- **Hosting:** Local — runs on the user's MacBook. `npm run dev` from
  `~/workspace/portfolio-briefing/web/`, bookmarked at
  `http://localhost:3000`. Optionally Tauri-wrap as a standalone app if the
  user wants a dock icon.
- **Auth:** None. Local, single user.

### Data flow

```
briefings/briefing_YYYY-MM-DD.json   ← daily source of truth (already exists)
briefings/briefing_YYYY-MM-DD.md      ← rendered text (already exists)
briefing_snapshots/YYYY-MM-DD/*.json  ← raw inputs (already exists)
                ↓
       [web app loader]
                ↓
       [aggregation: time series, diffs, drift detection]
                ↓
       [pages]
                ↓
       (user clicks "Mark executed")
                ↓
       state/directives.yaml          ← appended to (single new file)
       state/action_history.jsonl     ← appended to (audit log)
```

The web app is **read-only** against the briefing pipeline. The only files
it writes are `state/directives.yaml` (which the next briefing run reads
to honor permanent overrides) and `state/action_history.jsonl` (audit log
of executions/defers/overrides). This keeps the existing pipeline
authoritative and the web app a clean consumer.

### Performance budgets

- Initial page load: **<300ms** for Today. (140 JSON files × ~10KB = 1.4MB
  on cold start; aggregations are O(n) per ticker; trivially under budget.)
- Chart interactions: **<16ms** redraw on hover. Use canvas only if SVG
  hits a wall, which it won't at 90-365 data points per series.
- Cold data import: ETL into a single SQLite file (`state/webapp.db`) at
  app boot is acceptable if JSON parsing gets slow at >365 days history.

---

## 10. Open Questions for the User

Before implementation, confirm:

1. **Mark-executed semantics:** When the user clicks "Mark executed", does
   the app trust the click (no broker confirmation), or do we want the next
   briefing run to reconcile against E*TRADE positions and auto-mark
   executed items? My recommendation: trust the click for v1; add reconcile
   later if there's drift.

2. **Override directives:** Should "Override — never re-flag" target the
   exact contract (`AMD_PUT_420_20261218`) or the recommendation kind +
   reason (`CLOSE:AMD_*_at_45pct`)? My recommendation: exact contract.
   Broader overrides become discipline bypasses.

3. **Light mode required?** The mockups assume dark mode. Confirm.

4. **Tauri-wrap into a desktop app**, or keep as localhost browser? My
   recommendation: localhost in v1 (faster to iterate, no native build
   complexity), Tauri once the surface is stable.

5. **Mobile auth:** If you ever want to check this app from your phone
   when away from your laptop, do we need cloud-host the read view? My
   recommendation: skip until you ask for it.

6. **Auto-refresh during market hours?** The briefing runs at 8am only,
   but the snapshot data could be refreshed intraday if useful. My
   recommendation: NO. The discipline is "decide in the morning, execute
   morning, walk away." An intraday refresh invites churn.

7. **Performance comparison baseline:** "Buy-and-hold counterfactual" needs
   a definition. Options: (a) every share bought as of the position's
   actual entry, just held to today; (b) all current shares assumed bought
   at start of period at then-prices; (c) every option premium treated as
   if invested in SPY at that date. My recommendation: (a) — most
   faithful to "what would happen if I just never wrote options."

8. **Stalled threshold:** Hard rule treats ≥6 sessions as stalled. Should
   the app use the same number, or surface "approaching stalled" at 4
   sessions? My recommendation: same number — the user already calibrated
   to it.

---

## End of design doc.

This doc is intentionally tight enough to hand to a frontend engineer
without further interpretation. Open questions in §10 should be resolved
before implementation begins.
