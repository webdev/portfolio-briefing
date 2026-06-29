"""Tests for the unified Parkev chip (CLAUDE.md hard rule #27)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.parkev_chip import (  # noqa: E402
    format_parkev_chip,
    parkev_chip_for_ticker,
    PARKEV_MARK,
)


def _rec(**kw):
    base = {
        "rating_tier": 3,
        "raw_recommendation": "Buy",
        "conviction": None,
        "age_days": 5,
        "aging": False,
    }
    base.update(kw)
    return base


# ─── Rating tier labels ──────────────────────────────────────────────────

def test_tier5_renders_as_top_stock():
    chip = format_parkev_chip(_rec(rating_tier=5, raw_recommendation="Top Stock to Buy"))
    assert "TOP STOCK" in chip
    assert PARKEV_MARK in chip


def test_tier4_top12_renders_as_top12():
    chip = format_parkev_chip(_rec(rating_tier=4, raw_recommendation="Top 12 Stock"))
    assert "TOP 12" in chip


def test_tier4_top15_renders_as_top15():
    chip = format_parkev_chip(_rec(rating_tier=4, raw_recommendation="Top 15 Stock"))
    assert "TOP 15" in chip


def test_tier4_top25_renders_as_top25():
    chip = format_parkev_chip(_rec(rating_tier=4, raw_recommendation="Top 25 Stock"))
    assert "TOP 25" in chip


def test_tier4_unknown_flavor_falls_back_to_top_tier():
    """If a future tier-4 flavor appears (Top 50?), don't drop it — emit
    a safe TOP TIER label instead of silently rendering 'NO REC'."""
    chip = format_parkev_chip(_rec(rating_tier=4, raw_recommendation="Top 50 Stock"))
    assert "TOP TIER" in chip


def test_tier3_buy():
    chip = format_parkev_chip(_rec(rating_tier=3, raw_recommendation="Buy"))
    assert "BUY" in chip and "BDL" not in chip


def test_tier2_borderline_buy():
    chip = format_parkev_chip(_rec(rating_tier=2, raw_recommendation="Borderline Buy"))
    assert "BDL BUY" in chip


def test_tier1_hold():
    chip = format_parkev_chip(_rec(rating_tier=1, raw_recommendation="Hold/ Market Perform"))
    assert "HOLD" in chip


def test_tier0_sell():
    chip = format_parkev_chip(_rec(rating_tier=0, raw_recommendation="Sell"))
    assert "SELL" in chip


# ─── Conviction chip ─────────────────────────────────────────────────────

def test_high_conviction_shows_fire():
    chip = format_parkev_chip(_rec(rating_tier=4, raw_recommendation="Top 12 Stock", conviction="High"))
    assert "🔥 High" in chip


def test_medium_conviction_shows_half_circle():
    chip = format_parkev_chip(_rec(conviction="Medium"))
    assert "◐ Med" in chip


def test_low_conviction_shows_triangle():
    chip = format_parkev_chip(_rec(conviction="Low"))
    assert "▽ Low" in chip


def test_none_conviction_omitted_entirely():
    """No conviction → no chip section, no 'None' string."""
    chip = format_parkev_chip(_rec(conviction=None))
    assert "None" not in chip
    assert "🔥" not in chip and "◐" not in chip and "▽" not in chip


# ─── Age + aging clock ──────────────────────────────────────────────────

def test_fresh_age_no_clock_icon():
    chip = format_parkev_chip(_rec(age_days=5, aging=False))
    assert "5d" in chip
    assert "⏰" not in chip


def test_aging_flag_adds_clock():
    chip = format_parkev_chip(_rec(age_days=18, aging=True))
    assert "⏰ 18d" in chip


def test_age_over_14_infers_aging_even_if_flag_missing():
    """Defensive: if `aging` is missing from the rec dict, derive from age_days."""
    chip = format_parkev_chip(_rec(age_days=22, aging=False))  # flag forced False
    assert "⏰" in chip                                          # should still mark stale


# ─── Composite samples (the documented examples in the helper docstring) ─

def test_canonical_top_signal():
    """The strongest possible chip — Top 12 + High + fresh."""
    chip = format_parkev_chip(_rec(
        rating_tier=4, raw_recommendation="Top 12 Stock",
        conviction="High", age_days=8, aging=False,
    ))
    assert chip == f"{PARKEV_MARK} TOP 12 · 🔥 High · 8d"


def test_canonical_buy_medium():
    chip = format_parkev_chip(_rec(
        rating_tier=3, raw_recommendation="Buy",
        conviction="Medium", age_days=12, aging=False,
    ))
    assert chip == f"{PARKEV_MARK} BUY · ◐ Med · 12d"


def test_canonical_stale_hold_low():
    chip = format_parkev_chip(_rec(
        rating_tier=1, raw_recommendation="Hold/ Market Perform",
        conviction="Low", age_days=22, aging=True,
    ))
    assert chip == f"{PARKEV_MARK} HOLD · ▽ Low · ⏰ 22d"


def test_canonical_bearish_high_conviction():
    chip = format_parkev_chip(_rec(
        rating_tier=0, raw_recommendation="Sell",
        conviction="High", age_days=3, aging=False,
    ))
    assert chip == f"{PARKEV_MARK} SELL · 🔥 High · 3d"


# ─── Fail-closed: missing data → 'no rec' (never fabricate) ─────────────

def test_none_input_returns_no_rec():
    assert format_parkev_chip(None) == f"{PARKEV_MARK} no rec"


def test_empty_dict_returns_no_rec():
    assert format_parkev_chip({}) == f"{PARKEV_MARK} no rec"


def test_missing_rating_tier_returns_no_rec():
    chip = format_parkev_chip({"conviction": "High", "age_days": 5})
    assert chip == f"{PARKEV_MARK} no rec"


def test_non_dict_returns_no_rec():
    assert format_parkev_chip("garbage") == f"{PARKEV_MARK} no rec"
    assert format_parkev_chip(42) == f"{PARKEV_MARK} no rec"


# ─── Lookup-by-ticker helper ────────────────────────────────────────────

def test_lookup_by_ticker_hits_when_present():
    recs = {"META": _rec(rating_tier=4, raw_recommendation="Top 12 Stock", conviction="High")}
    chip = parkev_chip_for_ticker("META", recs)
    assert "TOP 12" in chip and "🔥 High" in chip


def test_lookup_by_ticker_case_insensitive():
    recs = {"META": _rec(rating_tier=4, raw_recommendation="Top 12 Stock")}
    assert parkev_chip_for_ticker("meta", recs) == parkev_chip_for_ticker("META", recs)


def test_lookup_by_ticker_misses_returns_no_rec():
    recs = {"META": _rec(rating_tier=4)}
    assert parkev_chip_for_ticker("UNKNOWN", recs) == f"{PARKEV_MARK} no rec"


def test_lookup_by_empty_inputs():
    assert parkev_chip_for_ticker("", {"META": _rec()}) == f"{PARKEV_MARK} no rec"
    assert parkev_chip_for_ticker("META", None) == f"{PARKEV_MARK} no rec"
    assert parkev_chip_for_ticker("META", {}) == f"{PARKEV_MARK} no rec"


# ─── annotate_parkev_chips — markdown post-processor ──────────────────────

from analysis.parkev_chip import annotate_parkev_chips


def _recs():
    return {
        "META": _rec(rating_tier=4, raw_recommendation="Top 12 Stock",
                     conviction="High", age_days=8, aging=False),
        "GOOG": _rec(rating_tier=3, raw_recommendation="Buy",
                     conviction="Medium", age_days=12),
        "MU": _rec(rating_tier=1, raw_recommendation="Hold/ Market Perform",
                   conviction="Low", age_days=22, aging=True),
    }


def test_annotate_appends_chip_to_action_list_header():
    md = "1. **CLOSE** META_PUT_525_20260821 — +35% capture; mid $5.40"
    out = annotate_parkev_chips(md, _recs())
    assert "🅿️ TOP 12 · 🔥 High · 8d" in out


def test_annotate_appends_chip_to_watch_panel_row():
    md = "- **GOOG** @ $338.35 — 14.7% (+38.1%) → **HOLD**"
    out = annotate_parkev_chips(md, _recs())
    assert "🅿️ BUY · ◐ Med · 12d" in out


def test_annotate_appends_chip_to_capital_plan_bullet():
    md = "- CLOSE GOOG — locks $+10,290, frees $0 — net cash −$3,683"
    out = annotate_parkev_chips(md, _recs())
    assert "🅿️ BUY" in out


def test_annotate_appends_chip_to_candidate_card():
    md = "**🎯 CANDIDATE · `META` · $548.80** ✅ RSI favourable 🏆 TOP CONVICTION"
    out = annotate_parkev_chips(md, _recs())
    assert "🅿️ TOP 12 · 🔥 High · 8d" in out


def test_annotate_appends_chip_to_long_term_card():
    md = "### 💎 4. LONG DATED CSP · `MU`"
    out = annotate_parkev_chips(md, _recs())
    assert "🅿️ HOLD · ▽ Low · ⏰ 22d" in out


def test_annotate_never_double_annotates_existing_chip():
    """If a line already carries the 🅿️ marker (e.g., from review_equities),
    don't add a second chip — leave the line untouched."""
    md = "- **META** @ $547.83 → **HOLD** Thesis intact, technical sound. 🅿️ TOP 12 · 🔥 High · 8d"
    out = annotate_parkev_chips(md, _recs())
    assert out.count("🅿️") == 1


def test_annotate_skips_sub_bullets_and_continuation_lines():
    """Sub-bullets (Earnings check:, Source:, etc.) and S/R continuation
    (↳) lines shouldn't get chips even if they happen to contain a ticker."""
    md = "\n".join([
        "1. **CLOSE** META_PUT — order ready",
        "  ↳ S: $525 (52w low, 1 touch) · R: $570 (daily pivot, 2 touches)",
        "  - **Source:** Live E*TRADE chain",
        "  - **Earnings check:** ✅ next earnings 79d away",
        "  - **Why:** META is the strongest signal",
    ])
    out = annotate_parkev_chips(md, _recs())
    # Only the header line (line 1) gets a chip.
    assert out.count("🅿️") == 1


def test_annotate_renders_no_rec_for_unknown_ticker():
    """CLAUDE.md hard rule #27: EVERY ticker-specific line MUST carry a chip.
    Tickers not in Parkev's sheet render `🅿️ no rec` — never silently
    skipped. Caught Jun 2026 when ARM (in sheet but archived by stale-cap)
    rendered without any chip, leaving the user wondering if it was a bug
    or intentional. The chip is the user's contract: presence guaranteed."""
    md = "- **SOXX** @ $599.65 — 6.4% → **HOLD**"
    # Recs map has no SOXX entry at all (not even None) — unknown ticker case.
    out = annotate_parkev_chips(md, _recs())
    assert "SOXX" in out
    assert f"{PARKEV_MARK} no rec" in out


def test_annotate_renders_no_rec_for_unknown_in_action_list():
    """The ARM case: candidate card with a ticker NOT in Parkev's sheet
    still gets `🅿️ no rec` rather than silent skip."""
    md = "**🎯 CANDIDATE · `ARM` · $336.92** ✅ RSI favourable"
    out = annotate_parkev_chips(md, _recs())  # ARM not in _recs()
    assert "ARM" in out
    assert f"{PARKEV_MARK} no rec" in out


def test_annotate_empty_recs_is_noop():
    md = "1. **CLOSE** META_PUT — order"
    assert annotate_parkev_chips(md, {}) == md
    assert annotate_parkev_chips(md, None) == md


def test_annotate_preserves_lines_without_tickers():
    md = "\n".join([
        "## Today's Action List",
        "",
        "Some prose without any ticker references.",
        "1. **CLOSE** META_PUT — header",
    ])
    out = annotate_parkev_chips(md, _recs())
    assert out.count("🅿️") == 1
    assert "## Today's Action List" in out
    assert "Some prose without any ticker" in out
