"""Rule #46 spotlight vintage fail-safe + RSI one-voice — the 2026-08-14
SNDK bug.

George (2026-08-14): "RSI on SNDK is 76 now... What kind of recommendation
is this? B (66) SNDK — SELL 1× $1230P … RSI 48 late-band … What kind of
weakness are we talking about here?"

The same 2026-08-14 briefing rendered SNDK with THREE RSI values: Best
Setups "RSI 48 late-band" (stale scout-cache close), the action-list close
card "RSI 56" (snapshot technicals), and live intraday reality ~76 after
SNDK moved $1,367 → $1,625 (+19%) in ~2 sessions. Rule #46 (vintage
fail-safe) exists exactly for this, but the Best Setups spotlight pool
(``collect_best_setups``) graded candidates without vintage resolution, so
a +19% mover kept its pre-move 'RSI 48' and earned a B.

Pinned here:
  (a) the spotlight grade uses the vintage-resolved RSI — drift → live
      Wilder recompute; live hard block at ~76 → excluded with a visible
      reason, never a green-lit B;
  (b) unverifiable vintage → the grade caps (never A/B) and the line says
      "RSI unverified this cycle";
  (c) the no-drift case is unchanged;
  (d) the same-render RSI consistency check flags a two-value fixture and
      stays silent when consistent;
  (e) the SNDK smoke case end-to-end (fixture inputs shaped from the
      2026-08-14 snapshot + a live price implying RSI > 70).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import price_consistency as pc  # noqa: E402
from analysis import setup_grade as sg  # noqa: E402
from analysis.vintage_guard import (  # noqa: E402
    resolve_new_open_rsi,
    wilder_rsi_live,
)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures — shaped from the real 2026-08-14 SNDK numbers
# ─────────────────────────────────────────────────────────────────────────

PRE_MOVE_CLOSE = 1367.0     # the technicals' reference close
LIVE_SPOT = 1625.0          # +18.9% — the two-session melt-up


def _recent_closes():
    """A mild pre-move daily close series ending at $1,367 — appending the
    $1,625 live bar must produce a Wilder RSI past the >70 hard block."""
    closes = [1300.0 + 3.0 * i + (7.0 if i % 2 else -7.0) for i in range(24)]
    closes.append(PRE_MOVE_CLOSE)
    return closes


def _technicals(recent_closes=None):
    return {
        "SNDK": {
            "spot": PRE_MOVE_CLOSE,
            "rsi_14": 56.0,          # the action-list card's fresher read
            "sma_200": 1100.0,
            "iv_rank": 70.0,
            "drawdown_pct": 5.0,
            "recent_closes": (_recent_closes() if recent_closes is None
                              else recent_closes),
            "support_resistance": {
                "supports": [{"price": 1230.0, "touches": 3,
                              "strength": 4.0}],
                "resistances": [],
            },
            "deep": {"long_term_verdict": "uptrend"},
        },
    }


def _snapshot(quotes=None, recent_closes=None):
    return {
        "technicals": _technicals(recent_closes=recent_closes),
        "iv_ranks": {"SNDK": 70.0},
        "quotes": quotes if quotes is not None else {},
        "positions": [],
        "earnings_calendar": {},
    }


def _scout_result():
    """The scout-cache shape that produced "B (66) SNDK — SELL 1× $1230P …
    RSI 48 late-band" — RSI 48 is the STALE 24h-cache close read."""
    return {
        "ticker": "SNDK", "rsi_14": 48.0, "iv_rank": 70.0,
        "spot": PRE_MOVE_CLOSE, "sma_200": 1100.0, "drawdown_pct": 5.0,
        "days_to_earnings": 40,
        "support_resistance": {
            "supports": [{"price": 1230.0, "touches": 3, "strength": 4.0}],
            "resistances": [],
        },
        "csp_entry": {"strike": 1230.0, "mid": 29.35, "dte": 36,
                      "expiration": "2026-09-18"},
    }


def _graded_idea():
    """The income-opportunity shape of the observed line: B (66) $1230P."""
    return {
        "ticker": "SNDK", "setup_grade": "B", "setup_grade_score": 66.0,
        "setup_grade_message": "🏁 Entry: B — good setup",
        "setup_grade_drivers": ["RSI 48 late-band", "RVr 70 ✓"],
        "strike": 1230.0, "expiration_pretty": "2026-09-18",
        "annualized_pct": 35.0, "rsi_14": 48.0,
    }


# ─────────────────────────────────────────────────────────────────────────
# Sanity — the fixture really produces the observed vintage shape
# ─────────────────────────────────────────────────────────────────────────

def test_fixture_sanity_live_rsi_recomputes_past_the_hard_block():
    """"RSI on SNDK is 76 now" — the +19% move must recompute a live
    Wilder RSI past the >70 put hard block from the fixture's series."""
    live = wilder_rsi_live(_recent_closes(), LIVE_SPOT)
    assert live is not None and live > 70.0
    res = resolve_new_open_rsi(
        "SNDK", _technicals(), {"SNDK": {"last": LIVE_SPOT}}, [], {})
    assert res["status"] == "live"
    assert res["verified"] is True
    assert res["rsi"] > 70.0
    assert res["move_pct"] == pytest.approx(18.9, abs=0.1)


# ─────────────────────────────────────────────────────────────────────────
# (a) drift → live re-grade; hard block → excluded with visible reason
# ─────────────────────────────────────────────────────────────────────────

def test_a_spotlight_scout_pool_uses_vintage_resolved_rsi():
    """Observed: "B (66) SNDK — SELL 1× $1230P … RSI 48 late-band" spotlit
    while live RSI was ~76. With the +19% drift measured, the scout-pool
    candidate re-grades on the LIVE RSI, hard-blocks, and lands in the
    visible exclusions — never a green-lit slot."""
    best = sg.collect_best_setups(
        scout_results=[_scout_result()],
        snapshot_data=_snapshot(quotes={"SNDK": {"last": LIVE_SPOT}}),
        config={})
    assert [e["ticker"] for e in best["csp"]] == []
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert "SNDK" in ex
    assert "live RSI" in ex["SNDK"]["reason"]
    assert "hard block" in ex["SNDK"]["reason"]
    assert "+18.9%" in ex["SNDK"]["reason"]
    assert ex["SNDK"]["letter"] == "—"
    md = "\n".join(sg.render_best_setups(best))
    assert "⏸ SNDK" in md
    assert "live RSI" in md
    # The observed green-lit line can never render again.
    assert "**B** (66) `SNDK`" not in md


def test_a_new_ideas_pool_also_vintage_resolved():
    """The generation-time grade ("B" / 66 on the idea dict) is NOT trusted
    over the vintage: the income-opportunity pool re-resolves and the +19%
    mover's B is voided by the live hard block."""
    best = sg.collect_best_setups(
        new_ideas=[_graded_idea()],
        snapshot_data=_snapshot(quotes={"SNDK": {"last": LIVE_SPOT}}),
        config={})
    assert [e["ticker"] for e in best["csp"]] == []
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert "SNDK" in ex and "hard block" in ex["SNDK"]["reason"]


def test_a_drift_without_hard_block_regrades_on_live_value():
    """A measurable drift whose live RSI stays inside the band re-grades on
    the LIVE value (rule #46 'live' status) instead of keeping the stale
    grade — the drivers carry the recomputed RSI, not the cache's 48."""
    # A grinding-up series ending at 1367, then a -6% live flush: the
    # recomputed RSI lands inside the put band — drift threshold tripped.
    closes = [1250.0]
    for i in range(23):
        closes.append(closes[-1] + (10.0 if i % 2 == 0 else -2.0))
    closes.append(PRE_MOVE_CLOSE)
    live_price = PRE_MOVE_CLOSE * 0.94          # -6% — past the 5% threshold
    live = wilder_rsi_live(closes, live_price)
    assert live is not None and 35.0 <= live <= 55.0   # in the put band
    best = sg.collect_best_setups(
        scout_results=[_scout_result()],
        snapshot_data=_snapshot(quotes={"SNDK": {"last": live_price}},
                                recent_closes=closes),
        config={})
    assert [e["ticker"] for e in best["csp"]] == ["SNDK"]
    drivers = " · ".join(best["csp"][0]["drivers"])
    assert f"RSI {live:.0f}" in drivers          # graded on the LIVE value
    assert "RSI 48" not in drivers               # never the stale cache read


# ─────────────────────────────────────────────────────────────────────────
# (b) unverifiable vintage → capped, never A/B, with the note
# ─────────────────────────────────────────────────────────────────────────

def test_b_unverified_vintage_caps_grade_never_ab():
    """No live quote from ANY source (rule #46 'unverified') → the B grade
    caps at C, the score drops under the B floor, and the line says
    'RSI unverified this cycle' — never an A/B on unverified RSI."""
    best = sg.collect_best_setups(
        scout_results=[_scout_result()],
        snapshot_data=_snapshot(quotes={}),      # no quote, no position price
        config={})
    entries = {e["ticker"]: e for e in best["csp"]}
    assert "SNDK" in entries
    e = entries["SNDK"]
    assert e["letter"] not in ("A", "A-", "B")
    assert e["letter"] == "C"
    assert float(e["score"]) < 65.0
    assert any(sg.UNVERIFIED_RSI_NOTE in d for d in e["drivers"])
    assert sg.UNVERIFIED_RSI_NOTE in e["message"]


def test_b_stale_up_move_excludes_new_puts_rule44():
    """Drift measured (+19% UP) but the live RSI is NOT computable (close
    series too short) → rule #44 fail-safe: the live RSI is plausibly past
    the block, so the CSP is EXCLUDED with a visible reason — never capped
    into a green-lit slot."""
    best = sg.collect_best_setups(
        scout_results=[_scout_result()],
        snapshot_data=_snapshot(quotes={"SNDK": {"last": LIVE_SPOT}},
                                recent_closes=[PRE_MOVE_CLOSE] * 3),
        config={})
    assert [e["ticker"] for e in best["csp"]] == []
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert "SNDK" in ex
    assert "rule #44" in ex["SNDK"]["reason"]
    assert "up-move" in ex["SNDK"]["reason"]


def test_b_stale_down_move_caps_with_unverified_note():
    """Drift DOWN with the live RSI uncomputable → not the rule-#44 put
    exclusion (a down-move lowers RSI), but the grade still caps with the
    unverified note — the stale B never survives verbatim."""
    best = sg.collect_best_setups(
        scout_results=[_scout_result()],
        snapshot_data=_snapshot(quotes={"SNDK": {"last": 1100.0}},
                                recent_closes=[PRE_MOVE_CLOSE] * 3),
        config={})
    entries = {e["ticker"]: e for e in best["csp"]}
    assert "SNDK" in entries
    assert entries["SNDK"]["letter"] == "C"
    assert any(sg.UNVERIFIED_RSI_NOTE in d
               for d in entries["SNDK"]["drivers"])


# ─────────────────────────────────────────────────────────────────────────
# (c) no-drift case unchanged
# ─────────────────────────────────────────────────────────────────────────

def test_c_no_drift_keeps_the_generation_grade():
    """A fresh vintage (live quote within the 5% threshold) leaves the
    candidate's grade untouched — no cap, no exclusion, no note."""
    fresh = sg.collect_best_setups(
        scout_results=[_scout_result()],
        snapshot_data=_snapshot(quotes={"SNDK": {"last": 1372.0}}),  # +0.4%
        config={})
    control = sg.collect_best_setups(
        scout_results=[_scout_result()], snapshot_data={}, config={})
    assert [e["ticker"] for e in fresh["csp"]] == ["SNDK"]
    assert fresh["excluded_csp"] == []
    f, c = fresh["csp"][0], control["csp"][0]
    assert (f["letter"], f["score"]) == (c["letter"], c["score"])
    assert not any(sg.UNVERIFIED_RSI_NOTE in d for d in f["drivers"])


def test_c_missing_snapshot_data_fails_open():
    """No snapshot at all (legacy callers / tests) → the vintage resolver
    has nothing to measure and the pool behaves exactly as before."""
    best = sg.collect_best_setups(
        new_ideas=[_graded_idea()], snapshot_data=None, config={})
    assert [e["ticker"] for e in best["csp"]] == ["SNDK"]
    assert best["csp"][0]["letter"] == "B"


# ─────────────────────────────────────────────────────────────────────────
# (d) same-render RSI consistency check
# ─────────────────────────────────────────────────────────────────────────

_TWO_VOICE_MD = """## Today's Action List — Friday, August 14, 2026

1. **CLOSE** SNDK_PUT_1230_20260918 — +33% captured; buy-to-close $30.82
   - Technicals: RSI 56 (theta working) · IV rank 70

## 🏆 Best Setups Today

- **B** (66) `SNDK` — SELL 1× $1230P exp 2026-09-18 (36 DTE) · 35% ann · RSI 48 late-band — 🏁 Entry: B
"""

_ONE_VOICE_MD = _TWO_VOICE_MD.replace("RSI 48 late-band", "RSI 56 late-band")


def test_d_two_rsi_voices_flagged():
    """Observed: the close card said "RSI 56" while Best Setups said
    "RSI 48 late-band" in the SAME render. The sweep flags the 8-point
    disagreement and the panel names the ticker."""
    offenders = pc.rsi_disagreements(_TWO_VOICE_MD)
    assert len(offenders) == 1
    o = offenders[0]
    assert o["ticker"] == "SNDK"
    assert o["min"] == 48.0 and o["max"] == 56.0
    assert o["spread_pts"] == pytest.approx(8.0)
    panel = "\n".join(pc.render_rsi_panel(offenders))
    assert "RSI Consistency Check" in panel
    assert "**SNDK** — RSI 48 vs RSI 56" in panel


def test_d_consistent_render_is_silent():
    """One voice → no offenders, no panel."""
    assert pc.rsi_disagreements(_ONE_VOICE_MD) == []
    assert pc.render_rsi_panel([]) == []


def test_d_rounding_point_tolerance():
    """"differing by more than a rounding point" — a 1-point difference is
    rounding noise (55 vs 56 silent); 2 points flags."""
    md_1pt = _TWO_VOICE_MD.replace("RSI 48 late-band", "RSI 55 late-band")
    assert pc.rsi_disagreements(md_1pt) == []
    md_2pt = _TWO_VOICE_MD.replace("RSI 48 late-band", "RSI 54 late-band")
    assert [o["ticker"] for o in pc.rsi_disagreements(md_2pt)] == ["SNDK"]


def test_d_vintage_tagged_reads_are_exempt():
    """A read the vintage guard already disclaimed ("⚠ pre-gap" /
    "unverified") is not a second voice — the sweep skips it."""
    md = _TWO_VOICE_MD.replace(
        "RSI 48 late-band",
        "RSI 48 ⚠ pre-gap (spot has moved +18.9% since RSI computation)")
    assert pc.rsi_disagreements(md) == []


def test_d_band_thresholds_and_uncontexted_reads_never_match():
    """"RSI 35-45 (now 50)" is a config band, not a reading; an RSI with no
    high-confidence ticker context is never attributed."""
    md = ("1. **CLOSE** SNDK_PUT_1230_20260918 — RSI 56\n"
          "   - 🏁 Entry: C — wait; prime needs RSI 35-45 (now 50)\n")
    mentions = pc.rsi_mentions(md)
    assert [v for v, _l in mentions.get("SNDK", [])] == [56.0]
    assert pc.rsi_mentions("Some prose line with RSI 72 and no ticker") == {}


# ─────────────────────────────────────────────────────────────────────────
# (e) the SNDK smoke case end-to-end
# ─────────────────────────────────────────────────────────────────────────

def test_e_sndk_smoke_end_to_end():
    """"What kind of weakness are we talking about here?" — with the
    2026-08-14-shaped snapshot inputs and a live price implying RSI ~76,
    the CSP grade comes out '—' (RSI hard block >70), the spotlight renders
    the exclusion visibly, and the redeploy A/B pool never sees SNDK."""
    from analysis.redeploy_path import ab_setups

    snap = _snapshot(quotes={"SNDK": {"last": LIVE_SPOT}})
    best = sg.collect_best_setups(
        scout_results=[_scout_result()],
        new_ideas=[_graded_idea()],
        snapshot_data=snap, config={})
    # Grade is voided — SNDK is not in any green-lit or A/B pool.
    assert all(e["ticker"] != "SNDK" for e in best["csp"])
    assert all(e["ticker"] != "SNDK" for e in ab_setups(best))
    # …and the exclusion is visible with the measured live value (rule #24).
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert ex["SNDK"]["letter"] == "—"
    assert "live RSI" in ex["SNDK"]["reason"]
    md = "\n".join(sg.render_best_setups(best))
    assert "⏸ SNDK — (0) — excluded: live RSI" in md
    assert "`SNDK` — SELL" not in md
