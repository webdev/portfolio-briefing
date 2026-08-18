"""2026-08-18 briefing fixes — two defects observed in the real briefing.

Each test's docstring quotes the observed output (house TDD rule #33 — the
test must fail against the broken code and pass against the fix).

Observed (briefing_full_2026-08-18.md + digest):

BUG 1 — the 📉 RSI Consistency Check panel flagged 12 tickers ~24-29 pts
apart: "NOK — RSI 37 vs RSI 66 (29 pts apart) across 13 line(s)",
"SOXX — RSI 42 vs RSI 71", "META — RSI 38 vs RSI 66", "PG — RSI 42 vs
RSI 70" … The RSI VALUES themselves were all correct (hand-computed Wilder
RSI from the snapshot's rsi_closes matches every rendered per-name read:
NOK 49, SOXX 47, META 38). The divergence was MISATTRIBUTION in the
sweep's context tracker (analysis/price_consistency.rsi_mentions):
  (a) equity/market-overview rows carry a BOLD ticker + price
      ("- **PLTR** $172.11 — RSI 66") which was NOT a context marker, so
      the read attributed to the last backticked/option-ident name
      (PLTR 66 → META; PFE 70 → PG; ZS 71 → SOXX; NFLX 63 → MU …);
  (b) new-open wait-card headers carry a BARE ticker after the bold verb
      ("- ⏸ **CSP — PAID-TO-WAIT (wait)** PLTR — sell $150P") — the PLTR
      card's three RSI 66 lines attributed to the previous numbered item's
      NOK ("NOK — RSI 37 vs RSI 66");
  (c) the historical entry-grade fragment "🎓 entry D (43, RSI 37 prime)
      · Aug 3" is a dated ENTRY-time read, not a second voice on today's
      RSI — it produced NOK's "37".

BUG 2 — the digest said "Action Items: 1 (+2 deferred)" and the numbered
list ordered: "1. HOLD THROUGH EARNINGS — willing owner IREN_PUT_47…",
"2. CSP — PAID-TO-WAIT VRT … ⏸ Deferred (capacity gated)", "3. 🚨 URGENT —
EXECUTE ROLL NOK_PUT_11…" — the ONLY executable item sorted LAST, below a
deferred planning card. Root cause: _split_action_section only terminated
the items region at "### ", so the trailing un-numbered "⏸ CSPs — wait for
a pullback" subsection (whose PLTR card carries '⏸ Deferred (capacity
gated)') was absorbed into the LAST numbered block — NOK classified as
deferred (hence "+2 deferred") and sort_deferred_actions left the order
IREN, VRT, NOK.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import price_consistency as pc  # noqa: E402
from analysis.vintage_guard import wilder_rsi_live  # noqa: E402
from render.panels import (  # noqa: E402
    _split_action_section,
    sort_deferred_actions,
    sync_action_item_count,
)

# ─────────────────────────────────────────────────────────────────────────
# BUG 1 fixtures — the REAL NOK close series from the 2026-08-18 snapshot
# (state/briefing_snapshots/2026-08-18/technicals.json, oldest-first, the
# last element is today's intraday bar; snapshot rsi_14 = 49.4).
# ─────────────────────────────────────────────────────────────────────────

_NOK_CLOSES = [
    14.18, 15.47, 16.46, 15.68, 15.28, 14.84, 16.25, 16.85, 16.73, 16.62,
    14.38, 14.59, 13.85, 13.4, 14.09, 14.8, 14.82, 13.98, 13.83, 13.49,
    14.43, 13.7, 13.81, 13.98, 13.01, 13.03, 13.28, 12.91, 12.07, 12.51,
    11.85, 11.95, 12.9, 12.44, 11.69, 11.7, 11.25, 10.38, 10.12, 10.08,
    10.63, 10.28, 9.73, 9.1, 9.28, 8.93, 8.41, 9.09, 9.14, 9.36,
    9.92, 9.58, 9.43, 9.36, 9.13, 9.44, 10.32, 10.56, 10.76, 10.285,
]
_NOK_LIVE = 10.29           # quotes.json: E*TRADE last, -4.5% on the day
_NOK_SNAPSHOT_RSI = 49.4    # technicals.json rsi_14


def _hand_wilder_rsi(closes, period: int = 14) -> float:
    """Independent hand computation of Wilder's RSI (test-local arithmetic,
    not the production function)."""
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, ls in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + ls) / period
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def test_nok_hand_computed_wilder_rsi_matches_the_snapshot_surface():
    """Panel said "NOK — RSI 37 vs RSI 66 (29 pts apart)". The hand-computed
    Wilder RSI from the snapshot's own rsi_closes is ~49 — matching the
    rendered per-name reads ("EXECUTE ROLL NOK_PUT_11_20261218 … RSI 49 🟢
    pullback") and the snapshot rsi_14 (49.4). Neither 37 nor 66 is NOK's
    RSI — the pipeline's values were correct; the panel misattributed."""
    hand = _hand_wilder_rsi(_NOK_CLOSES)
    assert abs(hand - _NOK_SNAPSHOT_RSI) < 1.0
    assert not (60.0 <= hand <= 72.0)   # 66 was never NOK's RSI


def test_nok_live_recompute_path_is_correct_not_inflated():
    """Hypothesis check (the rsi_closes threading fix from 2026-08-17):
    wilder_rsi_live with the live E*TRADE price appended to the threaded
    60-close series produces ~49 for NOK — the live-recompute path is NOT
    the source of the 66. A reversed (newest-first) series WOULD inflate a
    decliner's RSI — pin the ordering contract: the production series is
    oldest-first and the two orderings disagree on a trending tail."""
    live = wilder_rsi_live(_NOK_CLOSES, _NOK_LIVE)
    assert live is not None
    assert abs(live - _NOK_SNAPSHOT_RSI) < 1.5
    assert live < 60.0
    # Ordering contract: a reversed series is a DIFFERENT computation —
    # never feed newest-first closes into wilder_rsi_live.
    rev = wilder_rsi_live(list(reversed(_NOK_CLOSES)), _NOK_LIVE)
    assert rev is not None and abs(rev - live) > 0.5


# ─────────────────────────────────────────────────────────────────────────
# BUG 1 — attribution fixes in the RSI one-voice sweep
# ─────────────────────────────────────────────────────────────────────────

def test_bold_ticker_rows_attribute_to_their_own_name_not_the_context():
    """Observed: "META — RSI 38 vs RSI 66 (28 pts apart) across 7 line(s)"
    — the 66 was PLTR's ("- **PLTR** $172.11 — RSI 66 🟡 extended"), the 63
    MSFT's, the 57s NVDA/SPY's: bold-ticker market-overview rows were not
    context markers, so every row attributed to the lingering META context.
    Each bold-ticker+price row must set its own context."""
    md = "\n".join([
        "## Market overview",
        "",
        "- **META** $551.51 — RSI 38 · 📊 read",
        "- **MSFT** $482.16 — RSI 63 🟡 extended",
        "- **PLTR** $172.11 — RSI 66 🟡 extended",
        "- **SPY** $767.90 — RSI 57",
    ])
    mentions = pc.rsi_mentions(md)
    assert [v for v, _ in mentions.get("META", [])] == [38.0]
    assert [v for v, _ in mentions.get("PLTR", [])] == [66.0]
    assert [v for v, _ in mentions.get("MSFT", [])] == [63.0]
    assert pc.rsi_disagreements(md) == []


_WAIT_CARD_MD = "\n".join([
    "## Today's Action List — Tuesday, August 18, 2026",
    "",
    "3. 🚨 **URGENT — EXECUTE ROLL** NOK_PUT_11_20261218 — Calendar roll"
    " (same strike, longer date): +$115 net credit  · RSI 49 🟢 pullback",
    "   - **Why (urgent):** 🎯 Strike tested (δ 0.48 ≥ 0.45).",
    "",
    "**⏸ CSPs — wait for a pullback / below setup floor** (full ticket"
    " shown, never green-lit — each carries its measured reason)",
    "- ⏸ **CSP — PAID-TO-WAIT (wait)** PLTR — sell $150P exp Fri Sep 18"
    " '26 (monthly) for $1.89 premium",
    "   - **⏸ RSI 66 extended — selling puts into a green streak sets the"
    " strike against an inflated spot; wait for RSI 35-55 / a red day.**",
    "   - **Setup Grade: D** (25/100) · RSI 66 off-band ✗ · IVr 40 thin ✗",
])


def test_wait_card_bare_ticker_attributes_to_pltr_not_nok():
    """Observed: "NOK — RSI 37 vs RSI 66 (29 pts apart) across 13 line(s)"
    — the RSI 66 lines belong to the PLTR wait card ("⏸ **CSP —
    PAID-TO-WAIT (wait)** PLTR — sell $150P … RSI 66 extended") that
    renders BELOW the numbered NOK item; the bare-ticker-after-bold-verb
    header was not a context marker so PLTR's lines attributed to NOK."""
    mentions = pc.rsi_mentions(_WAIT_CARD_MD)
    assert [v for v, _ in mentions.get("NOK", [])] == [49.0]
    assert sorted(set(v for v, _ in mentions.get("PLTR", []))) == [66.0]
    assert pc.rsi_disagreements(_WAIT_CARD_MD) == []


def test_entry_grade_rsi_is_historical_not_a_second_voice():
    """Observed: NOK's "37" came from "🎓 entry D (43, RSI 37 prime) ·
    Aug 3" — the dated ENTRY-time grade, historical by design, never a
    second voice on today's RSI 49. The fragment is stripped before value
    extraction; a current RSI on the same line still sweeps."""
    md = "\n".join([
        "## Watch",
        "",
        "⚠️ **NOK_PUT_11_20261218** PUT $11 Fri Dec 18 '26, 122d left"
        "  · RSI 49",
        "  🎓 entry D (43, RSI 37 prime) · Aug 3",
        "📌 **AVGO_PUT_340_20270115** PUT $340 · RSI 42 · P&L -$625 →"
        " **HOLD** · 🎓 entry D (25) · Aug 12",
    ])
    mentions = pc.rsi_mentions(md)
    assert [v for v, _ in mentions.get("NOK", [])] == [49.0]   # no 37
    assert [v for v, _ in mentions.get("AVGO", [])] == [42.0]  # survives
    assert pc.rsi_disagreements(md) == []


def test_sweep_still_loud_on_a_genuine_two_voice_render():
    """The fix must not blind the sweep: a fabricated render where the SAME
    name genuinely carries two RSI values on attributable lines still
    flags (the 2026-08-14 SNDK 48/56 contract)."""
    md = "\n".join([
        "## Today's Action List",
        "",
        "1. **CLOSE** NOK_PUT_11_20261218 — RSI 49 🟢 pullback",
        "",
        "## Best Setups",
        "",
        "- **B** (66) `NOK` — SELL 1× $11P · RSI 77 late-band",
    ])
    offenders = pc.rsi_disagreements(md)
    assert [o["ticker"] for o in offenders] == ["NOK"]
    assert offenders[0]["min"] == 49.0 and offenders[0]["max"] == 77.0
    panel = "\n".join(pc.render_rsi_panel(offenders))
    assert "**NOK** — RSI 49 vs RSI 77" in panel


def test_bold_verbs_and_labels_never_become_context():
    """Guard the new bold-context patterns against false positives: bold
    verbs/labels followed by dollar amounts ("**CLOSE** … $3.57") must not
    hijack the context."""
    md = "\n".join([
        "## Today's Action List",
        "",
        "1. **CLOSE** RDDT_PUT_140_20260911 — +31% ($+151); buy-to-close"
        " limit $3.57 · RSI 44",
        "   - **Order:** $3.57 GTC · RSI 44",
    ])
    mentions = pc.rsi_mentions(md)
    assert set(mentions) == {"RDDT"}
    assert [v for v, _ in mentions["RDDT"]] == [44.0, 44.0]


# ─────────────────────────────────────────────────────────────────────────
# BUG 2 fixtures — the observed 2026-08-18 action list shape
# ─────────────────────────────────────────────────────────────────────────

_ACTION_MD_0818 = "\n".join([
    "# Daily Briefing — Tuesday, August 18, 2026",
    "",
    "**Portfolio NLV:** $1,088,019 | **Cash:** $69,442 (6.4%)",
    "**Action Items:** 3",
    "",
    "## Today's Action List — Tuesday, August 18, 2026",
    "",
    "1. **HOLD THROUGH EARNINGS — willing owner** IREN_PUT_47_20261218 —"
    " basis $28.36 is 34% below spot $42.69  · RSI 52",
    "   - **Why:** willing-owner fortress — place NO GTC through the print.",
    "2. **CSP — PAID-TO-WAIT** VRT — sell $240P exp Fri Sep 18 '26"
    " (monthly) for $5.72 premium  · RSI 46 🟢 pullback",
    "   - **⏸ Deferred (capacity gated) — stress coverage 0.11× < 0.50×"
    " floor; shown for planning, not a green light (rule #41)**",
    "3. 🚨 **URGENT — EXECUTE ROLL** NOK_PUT_11_20261218 — Calendar roll"
    " (same strike, longer date): +$115 net credit  · RSI 49 🟢 pullback",
    "   - **Why (urgent):** 🎯 Strike tested (δ 0.48 ≥ 0.45).",
    "   - **Trade-validator:** ✅ GOOD TRADE — EV $+34 — P(assignment) 47%",
    "",
    "_⏸ HEDGE (SPY put) vacated from the numbered list — undecided 38"
    " consecutive sessions (hedge_nag_days=14)._",
    "",
    "**⏸ CSPs — wait for a pullback / below setup floor** (full ticket"
    " shown, never green-lit — each carries its measured reason)",
    "- ⏸ **CSP — PAID-TO-WAIT (wait)** PLTR — sell $150P exp Fri Sep 18"
    " '26 (monthly) for $1.89 premium",
    "   - **⏸ Deferred (capacity gated) — stress coverage 0.11× < 0.50×"
    " floor; shown for planning, not a green light (rule #41)**",
    "",
    "### 📋 Total Impact (if all actions executed)",
    "",
    "- **Total actions:** 3",
    "",
    "## 🚦 Red Flags & Priorities",
    "",
    "_none_",
])


def test_urgent_executable_sorts_first_hold_second_deferred_third():
    """Observed: "1. HOLD THROUGH EARNINGS — willing owner IREN_PUT_47…",
    "2. CSP — PAID-TO-WAIT VRT … ⏸ Deferred (capacity gated)", "3. 🚨
    URGENT — EXECUTE ROLL NOK_PUT_11…" — the ONLY executable item sorted
    LAST, below a deferred planning card. Fixed order: executable (URGENT
    first) → hold-class → deferred, renumbered."""
    out = sync_action_item_count(sort_deferred_actions(_ACTION_MD_0818))
    nok = out.index("**URGENT — EXECUTE ROLL** NOK_PUT_11_20261218")
    iren = out.index("**HOLD THROUGH EARNINGS — willing owner** IREN_PUT_47")
    vrt = out.index("**CSP — PAID-TO-WAIT** VRT")
    assert nok < iren < vrt
    assert "1. 🚨 **URGENT — EXECUTE ROLL** NOK_PUT_11_20261218" in out
    assert "2. **HOLD THROUGH EARNINGS — willing owner** IREN_PUT_47" in out
    assert "3. **CSP — PAID-TO-WAIT** VRT" in out
    # deferred card stays fully visible (rule #24/#41)
    assert "⏸ Deferred (capacity gated)" in out


def test_count_line_reads_one_executable_one_hold_one_deferred():
    """Observed digest: "Action Items: 1 (+2 deferred)" — NOK (the urgent
    executable) was counted as DEFERRED because the trailing wait-list
    subsection's capacity tag leaked into its block. Correct count for the
    observed list: 1 executable (NOK), 1 hold (IREN), 1 deferred (VRT)."""
    out = sync_action_item_count(sort_deferred_actions(_ACTION_MD_0818))
    assert "**Action Items:** 1 (+1 hold, +1 deferred)" in out
    assert "(+2 deferred)" not in out


def test_wait_subsection_not_absorbed_into_last_numbered_block():
    """Root cause pin: _split_action_section terminated the items region
    only at "### ", so the col-0 "**⏸ CSPs — wait for a pullback**"
    subsection (with the PLTR card's '⏸ Deferred (capacity gated)' tag)
    was absorbed into NOK's block — 41 lines in the real briefing — and
    NOK classified as deferred. The subsection must land in the tail."""
    parts = _split_action_section(_ACTION_MD_0818)
    assert parts is not None
    _, _, blocks, tail, _ = parts
    assert len(blocks) == 3
    nok_block = "\n".join(blocks[2])
    assert "NOK_PUT_11_20261218" in nok_block
    assert "⏸ Deferred (capacity gated)" not in nok_block
    tail_text = "\n".join(tail)
    assert "⏸ CSPs — wait for a pullback" in tail_text
    assert "### 📋 Total Impact" in tail_text


def test_wait_subsection_stays_below_the_renumbered_items():
    """The reorder must not drag the trailing subsection or footers along
    with a moved block — they render after the numbered items, before the
    Total Impact tail, exactly as composed."""
    out = sort_deferred_actions(_ACTION_MD_0818)
    vrt = out.index("**CSP — PAID-TO-WAIT** VRT")
    hedge = out.index("_⏸ HEDGE (SPY put) vacated")
    wait = out.index("**⏸ CSPs — wait for a pullback")
    impact = out.index("### 📋 Total Impact")
    assert vrt < hedge < wait < impact


def test_ride_headline_is_hold_class():
    """🏇 RIDE — momentum hold items are HOLD-class (rule #51): they sort
    below executable actions, above deferred cards."""
    md = _ACTION_MD_0818.replace(
        "1. **HOLD THROUGH EARNINGS — willing owner** IREN_PUT_47_20261218 —"
        " basis $28.36 is 34% below spot $42.69  · RSI 52",
        "1. 🏇 **RIDE — momentum hold** IREN_PUT_47_20261218 — trend UP,"
        " RSI 52 below stall  · RSI 52")
    out = sync_action_item_count(sort_deferred_actions(md))
    assert out.index("**URGENT — EXECUTE ROLL** NOK_PUT_11_20261218") \
        < out.index("**RIDE — momentum hold** IREN_PUT_47_20261218") \
        < out.index("**CSP — PAID-TO-WAIT** VRT")
    assert "**Action Items:** 1 (+1 hold, +1 deferred)" in out


def test_ordering_pass_is_idempotent():
    once = sync_action_item_count(sort_deferred_actions(_ACTION_MD_0818))
    twice = sync_action_item_count(sort_deferred_actions(once))
    assert once == twice
