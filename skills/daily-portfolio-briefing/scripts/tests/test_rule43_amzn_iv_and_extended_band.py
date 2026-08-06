"""Rule #43 regression tests — 2026-07-31 AMZN LONG DATED CSP card (line ~639).

Observed output (briefing_2026-07-31.md):

    ### 💎 6. LONG DATED CSP · `AMZN`
    **Trade:** SELL 1× AMZN $245P exp Fri Oct 16 '26 (77 DTE)
    - **Triggers:** ... RSI 66 🟡 extended; third-party BUY; IV rank 100 (elevated); ...
    - **Rationale:** Patient capital trade: elevated IV 100 + 77-DTE horizon
      (expires Fri Oct 16 '26) = fat premium. ...
    - **Yield/Cost:** premium $630 ... ~12% annualized · $24,500 cash collateral ...

Two defects:
  1. "elevated IV 100 = fat premium" while the DELIVERED yield was ~12%
     annualized (thin) — IV rank 100 was inflated BY the +13.7% earnings gap
     (backward-looking realized-vol proxy); implied premium crushed post-print.
     The NFLX card on the same list fired its thin-premium reconsider note
     (premium $186 < $500); AMZN's $630 slipped past the absolute floor.
  2. "RSI 66 🟡 extended" annotated correctly (no ✅ badge) but the rec still
     rendered as numbered rec #6 — an extended-band new put-sale must demote
     to a "⏸ CSPs — wait for a pullback" subsection (full ticket, rule #24).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import iv_honesty, rsi_discipline  # noqa: E402
from steps.long_term_opportunities import render_long_term_opportunities  # noqa: E402
from render.panels import render_opportunities  # noqa: E402


# The AMZN card's measured values (2026-07-31 snapshot).
AMZN_IV_RANK = 100.0
AMZN_ANNUALIZED = 12.0        # $630 / $24,500 / 77d
AMZN_PREMIUM_TOTAL = 630.0
AMZN_RSI = 66.0
AMZN_RATIONALE = (
    "Patient capital trade: elevated IV 100 + 77-DTE horizon "
    "(expires Fri Oct 16 '26) = fat premium. If assigned, you own at $245 "
    "(effective basis $238). Strike anchored to $245 support (sma_50 + fib, 1 touch)."
)
# 07-30 close 235.50 → 07-31 close 267.84 = +13.7% one-day earnings gap
AMZN_TECH = {"recent_closes": [232.11, 231.39, 230.86, 226.65, 235.50, 267.84]}


def test_fat_premium_claim_requires_delivered_yield():
    """AMZN 2026-07-31: 'elevated IV 100 ... = fat premium' with delivered
    '$630 ... ~12% annualized' must NOT survive — claimed-fat (rank ≥ 60) +
    delivered-thin (< 20% ann) rewrites the claim. The old check only fired
    below $500 total premium, so AMZN's $630 kept the fat claim."""
    out = iv_honesty.rewrite_fat_premium(
        AMZN_RATIONALE,
        iv_rank=AMZN_IV_RANK,
        annualized_pct=AMZN_ANNUALIZED,
        premium_total=AMZN_PREMIUM_TOTAL,
        tech_entry=AMZN_TECH,
    )
    assert out is not None
    assert "fat premium" not in out
    assert "thin" in out
    assert "reconsider" in out
    # Genuinely fat delivery keeps the claim (no rewrite).
    assert iv_honesty.rewrite_fat_premium(
        AMZN_RATIONALE, iv_rank=100.0, annualized_pct=28.0,
        premium_total=1500.0, tech_entry=AMZN_TECH,
    ) is None
    # Fail-open: unknown delivered yield → no rewrite invented.
    assert iv_honesty.rewrite_fat_premium(
        AMZN_RATIONALE, iv_rank=100.0, annualized_pct=None,
        premium_total=None, tech_entry=AMZN_TECH,
    ) is None


def test_gap_inflated_iv_rank_honest_text():
    """The honest text must name the gap: 'IV rank 100 is inflated by the
    recent +14% move (realized-vol proxy is backward-looking); delivered
    premium is thin (~12% annualized) — implied vol has crushed post-event.'
    (AMZN gapped 235.50 → 267.84 on 07-31, which is what pinned the rank.)"""
    gap = iv_honesty.detect_recent_gap(AMZN_TECH)
    assert gap is not None and gap > 13  # +13.7% one-day move detected

    out = iv_honesty.rewrite_fat_premium(
        AMZN_RATIONALE,
        iv_rank=AMZN_IV_RANK,
        annualized_pct=AMZN_ANNUALIZED,
        premium_total=AMZN_PREMIUM_TOTAL,
        tech_entry=AMZN_TECH,
    )
    assert "inflated by the recent +14% move" in out
    assert "backward-looking" in out
    assert "implied vol has crushed post-event" in out
    assert "~12% annualized" in out

    # No gap data → still honest, but no fabricated gap number (rule #19).
    out_no_gap = iv_honesty.rewrite_fat_premium(
        AMZN_RATIONALE,
        iv_rank=AMZN_IV_RANK,
        annualized_pct=AMZN_ANNUALIZED,
        premium_total=AMZN_PREMIUM_TOTAL,
        tech_entry=None,
    )
    assert "inflated by the recent" not in out_no_gap  # no fabricated gap number
    assert "overstates the premium actually on offer" in out_no_gap

    # Small drift (no gap) on recent closes → gap is None, not fabricated.
    assert iv_honesty.detect_recent_gap(
        {"recent_closes": [100, 101, 100.5, 101.2, 100.8, 101.5]}
    ) is None


def test_thin_premium_reconsider_fires_universally():
    """'The NFLX card ... correctly fires its thin-premium check ("thinner
    than the rule-of-thumb estimate — reconsider"); AMZN's didn't.' The
    reconsider note must fire on ALL claimed-fat + delivered-thin cards, not
    just the < $500 absolute path."""
    # AMZN path: $630 premium (over the old floor) but 12% ann → reconsider.
    out = iv_honesty.rewrite_fat_premium(
        AMZN_RATIONALE, iv_rank=100.0, annualized_pct=12.0,
        premium_total=630.0, tech_entry=AMZN_TECH,
    )
    assert "reconsider unless you specifically want this strike" in out

    # Legacy NFLX-style absolute path still fires when the yield check can't
    # claim fat (rank below the floor): $186 premium < $500.
    legacy = iv_honesty.rewrite_fat_premium(
        "elevated IV 55 + 75-DTE horizon = fat premium. If assigned, you own at $65.",
        iv_rank=55.0, annualized_pct=12.0, premium_total=186.0,
    )
    assert "$186 premium" in legacy
    assert "thinner than the rule-of-thumb estimate" in legacy


def test_put_extended_band_demotes_to_wait():
    """'RSI 66 🟡 extended ... but still lists as numbered rec #6.' A new
    put-sale at RSI 60-70 must demote to the '⏸ CSPs — wait for a pullback'
    subsection with the full ticket shown (rule #24), tagged with the wait
    reason — across LT_CSP, income opportunities, and candidate CSP entries."""
    reason = rsi_discipline.put_extended_wait(AMZN_RSI)
    assert reason is not None
    assert "RSI 66 extended" in reason
    assert "wait for RSI 35-55" in reason
    assert "inflated spot" in reason

    # LT_CSP renderer: the AMZN-shaped op moves out of the numbered list.
    op = {
        "kind": "LONG_DATED_CSP", "ticker": "AMZN",
        "concrete_trade": "SELL 1× AMZN $245P exp Fri Oct 16 '26 (77 DTE)",
        "trigger_reasons": ["RSI 66 🟡 extended", "IV rank 100 (elevated)"],
        "rationale": "Patient capital trade.",
        "yield_or_cost": "premium $630 · ~12% annualized · $24,500 cash collateral",
        "rsi_wait": True, "rsi_wait_reason": reason,
    }
    md = "\n".join(render_long_term_opportunities([op]))
    assert "⏸ CSPs — wait for a pullback" in md
    assert "SELL 1× AMZN $245P exp Fri Oct 16 '26 (77 DTE)" in md  # full ticket
    assert "### 💎 1. LONG DATED CSP" not in md                      # not numbered
    assert "_0 signal(s)" in md                                      # count excludes waits

    # Income Opportunities renderer: rsi_wait idea leaves the actionable list.
    idea = {
        "ticker": "AMZN", "instruction": "SELL_OPEN", "spot": 267.81,
        "strike": 245.0, "mid": 6.30, "bid": 6.00, "expiration_pretty": "Oct 16 '26",
        "dte": 77, "annualized_pct": 12.0, "collateral": 24500.0, "premium": 630.0,
        "rsi_14": 66.0, "rsi_wait": True, "rsi_wait_reason": reason,
    }
    md2 = "\n".join(render_opportunities([idea]))
    assert "⏸ CSPs — wait for a pullback" in md2
    assert "Actionable: cash-secured puts" not in md2
    assert "$245 PUT" in md2  # full ticket still shown

    # Candidate CSP entries: RSI 66 routes to the held-by-RSI bucket.
    from steps.candidate_research import _status
    status, rv = _status(
        {"verdict": "CSP ENTRY (fat premium)", "rsi_14": 66.0},
        rsi_discipline.DEFAULT_THRESHOLDS,
    )
    assert status == "held_rsi"
    assert "wait for RSI 35-55" in rv.reason


def test_put_extended_band_config_nullable():
    """`rsi_discipline.put_extended_wait_band: null` disables the demotion —
    RSI 66 renders as before (keep + caution), nothing demoted."""
    th = rsi_discipline.load_thresholds(
        {"rsi_discipline": {"put_extended_wait_band": None}}
    )
    assert th["put_extended_wait_band"] is None
    assert rsi_discipline.put_extended_wait(66.0, th) is None
    # Custom band override works too.
    th2 = rsi_discipline.load_thresholds(
        {"rsi_discipline": {"put_extended_wait_band": [62, 68]}}
    )
    assert rsi_discipline.put_extended_wait(61.0, th2) is None
    assert rsi_discipline.put_extended_wait(65.0, th2) is not None


def test_over_70_hard_block_unchanged():
    """The existing >70 hard block is untouched: RSI 72 still REMOVES the new
    put-sale (transparency footer), and the wait band does not double-fire."""
    rv = rsi_discipline.hook("put", 72.0)
    assert rv.removed
    assert "overbought" in rv.reason
    assert rsi_discipline.put_extended_wait(72.0) is None  # band is [60, 70)
    # Boundary: exactly 70 → hard block territory, not wait.
    assert rsi_discipline.put_extended_wait(70.0) is None
    assert rsi_discipline.hook("put", 70.0).removed


def test_management_lines_untouched():
    """Existing-position management is NEVER demoted or removed by the
    extended band — rolls/closes/trims at RSI 66 render as-is."""
    for side in (None, "manage", "roll", "close", "trim"):
        rv = rsi_discipline.hook(side, 66.0)
        assert rv.decision == "keep"
        assert not rv.removed
    # put_extended_wait is only ever applied by NEW-open call sites; the hook
    # itself still returns keep+caution for a put at 66 (no behavior change
    # for callers that don't consult the wait band).
    rv_put = rsi_discipline.hook("put", 66.0)
    assert rv_put.decision == "keep"
    assert rv_put.badge == "⚠ RSI caution"


def test_favored_band_still_promotes():
    """RSI 45 (pullback band) is unchanged: promote with '✅ RSI favourable',
    no wait demotion — the GOOG card path (RSI 48 ✅) keeps working."""
    rv = rsi_discipline.hook("put", 45.0)
    assert rv.promoted
    assert rv.badge == "✅ RSI favourable"
    assert rsi_discipline.put_extended_wait(45.0) is None

    from steps.candidate_research import _status
    status, _ = _status(
        {"verdict": "CSP ENTRY (fat premium)", "rsi_14": 45.0},
        rsi_discipline.DEFAULT_THRESHOLDS,
    )
    assert status == "candidate"
