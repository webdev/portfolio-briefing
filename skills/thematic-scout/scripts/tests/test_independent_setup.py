"""Tests for the INDEPENDENT SETUP verdict path (CLAUDE.md hard rule #25).

When a name has favorable technicals (RSI 35-55, IV rank ≥ 50) but no
third-party BUY rec, the scout should still produce a tradeable
"CSP ENTRY (independent setup)" verdict — Parkev's sheet is one
catalyst source, not the only one. The user validates against other
sources.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scout  # noqa: E402


_CFG = {
    "rsi_oversold": 35,
    "rsi_overheated": 75,
    "drawdown_oversold_pct": 10,
    "drawdown_thesis_broken_pct": 30,
    "sma_within_pct": 5,
    "iv_rank_elevated": 50,
}


def _v(tech, rec=None):
    """Shorthand for calling _verdict with the test cfg."""
    return scout._verdict(tech, None, rec, 0, _CFG)


# ─── Positive cases: technicals qualify, no third-party rec ──────────────

def test_aaoi_independent_setup_rsi50_iv76():
    """AAOI: RSI 50 (mid-band), IV 76 (rich premium), small drawdown."""
    verdict, reasons, want_csp = _v({
        "rsi_14": 50, "iv_rank": 76, "drawdown_pct": 22,
        "spot": 174.0, "sma_200": 75.0,
    })
    assert verdict == "CSP ENTRY (independent setup)"
    assert want_csp is True
    assert any("no third-party rec" in r for r in reasons)


# ─── Task #59: Parkev HOLD gets a precise badge, not a misleading one ─────


def test_hold_rec_gets_precise_badge_not_no_rec():
    """AMAT case (2026-07-03): Parkev has HOLD, RSI/IV qualify for independent
    setup. Badge must say "Parkev HOLD (not a BUY catalyst)" not "no third-party
    rec" — the latter falsely implies Parkev doesn't cover the name."""
    verdict, reasons, want_csp = _v(
        {"rsi_14": 54, "iv_rank": 79, "drawdown_pct": 17,
         "spot": 603.0, "sma_200": 550.0},
        rec="HOLD",
    )
    assert verdict == "CSP ENTRY (independent setup)"
    assert want_csp is True
    # Must NOT say "no third-party rec" — that's the AMAT bug
    assert not any("no third-party rec" in r for r in reasons), (
        f"HOLD-rec case must not say 'no third-party rec' — got: {reasons}"
    )
    # Must say something honest about Parkev's HOLD
    assert any("HOLD" in r and "BUY catalyst" in r for r in reasons), (
        f"HOLD-rec case must mention 'HOLD' and 'BUY catalyst' — got: {reasons}"
    )


def test_no_rec_still_says_no_rec_precisely():
    """The other side of the fix: when there's truly no rec, the badge
    should still say "no third-party rec" — don't break the working case."""
    verdict, reasons, want_csp = _v(
        {"rsi_14": 50, "iv_rank": 76, "drawdown_pct": 22,
         "spot": 174.0, "sma_200": 75.0},
        rec=None,
    )
    assert verdict == "CSP ENTRY (independent setup)"
    assert any("no third-party rec" in r for r in reasons)


def test_hold_rec_watch_fallthrough_mentions_hold():
    """When RSI/IV don't qualify AND Parkev has HOLD, the WATCH fallthrough
    reason must mention the HOLD (not say 'no third-party catalyst'). Same
    honesty rule as the CSP branch."""
    verdict, reasons, _ = _v(
        {"rsi_14": 30, "iv_rank": 40, "drawdown_pct": 5,
         "spot": 100.0, "sma_200": 90.0},
        rec="HOLD",
    )
    # Under normal thresholds this should NOT be independent setup
    assert verdict == "WATCH"
    assert any("HOLD" in r for r in reasons)
    # And must NOT say "no third-party catalyst" — that's misleading when HOLD exists
    assert not any(r.strip() == "no third-party catalyst" for r in reasons)


def test_aa_oversold_high_iv_qualifies():
    """AA: RSI 40 (lower band), IV 99, dd 25%."""
    verdict, _, want_csp = _v({
        "rsi_14": 40, "iv_rank": 99, "drawdown_pct": 25,
        "spot": 63.0, "sma_200": 53.0,
    })
    assert verdict == "CSP ENTRY (independent setup)"
    assert want_csp is True


def test_etn_low_drawdown_high_iv_qualifies():
    """ETN: RSI 52, IV 99, dd 6% — clean pullback with rich premium."""
    verdict, _, want_csp = _v({
        "rsi_14": 52, "iv_rank": 99, "drawdown_pct": 6,
        "spot": 405.0, "sma_200": 365.0,
    })
    assert verdict == "CSP ENTRY (independent setup)"
    assert want_csp is True


# ─── Negative cases: gates correctly hold back unsuitable setups ─────────

def test_low_iv_falls_through_to_watch():
    """BE: RSI 56 BUT IV 32 (below 50) — thin premium, not actionable."""
    verdict, _, want_csp = _v({
        "rsi_14": 56, "iv_rank": 32, "drawdown_pct": 6,
        "spot": 290.0, "sma_200": 151.0,
    })
    assert verdict == "WATCH"
    assert want_csp is False


def test_deep_drawdown_hits_avoid_first():
    """CEG: RSI 45, IV 83, dd 35% — broken thesis takes precedence."""
    verdict, _, want_csp = _v({
        "rsi_14": 45, "iv_rank": 83, "drawdown_pct": 35,
        "spot": 262.0, "sma_200": 320.0,
    })
    assert verdict == "AVOID"
    assert want_csp is False


def test_rsi_out_of_band_falls_through():
    """ARM: RSI 66 (above 55) — extended, wait for retrace."""
    verdict, _, want_csp = _v({
        "rsi_14": 66, "iv_rank": 99, "drawdown_pct": 3,
        "spot": 399.0, "sma_200": 164.0,
    })
    assert verdict == "WATCH"
    assert want_csp is False


def test_oversold_below_band_does_not_qualify_as_independent():
    """RSI 28 is BELOW the 35-55 band — falls through to standard WATCH."""
    verdict, _, want_csp = _v({
        "rsi_14": 28, "iv_rank": 80, "drawdown_pct": 25,
        "spot": 100.0, "sma_200": 90.0,
    })
    assert verdict == "WATCH"
    assert want_csp is False


def test_overheated_rsi_hits_avoid_first():
    """RSI 78 with low drawdown — AVOID overheated, not independent."""
    verdict, _, want_csp = _v({
        "rsi_14": 78, "iv_rank": 80, "drawdown_pct": 1,
        "spot": 100.0, "sma_200": 70.0,
    })
    assert verdict.startswith("AVOID")
    assert want_csp is False


def test_missing_iv_fails_closed():
    """No IV data → no independent setup (fail-closed per live-data rule)."""
    verdict, _, want_csp = _v({
        "rsi_14": 45, "iv_rank": None, "drawdown_pct": 20,
        "spot": 100.0, "sma_200": 95.0,
    })
    assert verdict == "WATCH"
    assert want_csp is False


# ─── Regression checks: existing paths still win when they should ────────

def test_buy_rec_pullback_unchanged():
    """A name WITH BUY rec stays on the BUY (pullback) path, not independent."""
    verdict, _, want_csp = _v({
        "rsi_14": 37, "iv_rank": 97, "drawdown_pct": 40,
        "spot": 162.0, "sma_200": 215.0,
    }, rec="BUY")
    assert verdict == "BUY (pullback)"
    assert want_csp is True


def test_sell_rec_still_avoid():
    """SELL rec wins over technicals — no independent override."""
    verdict, _, want_csp = _v({
        "rsi_14": 45, "iv_rank": 80, "drawdown_pct": 10,
        "spot": 100.0, "sma_200": 95.0,
    }, rec="SELL")
    assert verdict == "AVOID"
    assert want_csp is False


def test_concentration_override_blocks_independent():
    """If user already holds ≥10% NLV, independent setup is suppressed."""
    verdict, _, want_csp = scout._verdict(
        {"rsi_14": 45, "iv_rank": 80, "drawdown_pct": 10,
         "spot": 100.0, "sma_200": 95.0},
        None, None, held_weight_pct=12.0, cfg=_CFG)
    assert "already concentrated" in verdict
    assert want_csp is False
