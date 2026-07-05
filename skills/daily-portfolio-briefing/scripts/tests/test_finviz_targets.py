"""Tests for the FINVIZ target-price annotator (analysis/finviz_targets.py).

Pure unit tests — no network, no I/O. Feed known inputs, assert exact
chip format + annotation stats. Regression coverage for hard rules #12,
#19, #11.

Run with:
    python3 -m pytest skills/daily-portfolio-briefing/scripts/tests/test_finviz_targets.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add the skill's scripts dir to path so the imports work
_SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS))

from analysis.finviz_targets import (  # noqa: E402
    _find_ticker_in_line,
    _is_rec_header,
    _recom_label,
    annotate_finviz,
    cross_check,
    format_finviz_chip,
)


# ─── _recom_label ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("rec, expected", [
    (1.0, "Strong Buy"),
    (1.5, "Strong Buy"),
    (1.8, "Buy"),
    (2.5, "Buy"),
    (3.0, "Hold"),
    (3.5, "Hold"),
    (4.0, "Sell"),
    (4.5, "Sell"),
    (5.0, "Strong Sell"),
    (None, None),
])
def test_recom_label_matches_finviz_buckets(rec, expected):
    assert _recom_label(rec) == expected


# ─── format_finviz_chip ───────────────────────────────────────────────────


def test_chip_with_both_target_and_recom():
    fv = {
        "target_price": 318.50,
        "spot_price": 200.09,
        "target_upside_pct": 59.2,
        "analyst_recommendation": 1.80,
    }
    chip = format_finviz_chip("NVDA", fv)
    assert chip is not None
    assert "📈 FINVIZ" in chip
    assert "$319" in chip or "$318" in chip
    assert "+59%" in chip
    assert "🅰 1.8/5" in chip
    assert "Buy" in chip


def test_chip_none_when_no_target_no_recom():
    """Fail-closed: neither target nor recom → nothing to render."""
    assert format_finviz_chip("XYZ", None) is None
    assert format_finviz_chip("XYZ", {}) is None
    assert format_finviz_chip("XYZ", {"target_price": None, "analyst_recommendation": None}) is None


def test_chip_with_only_recommendation():
    fv = {"target_price": None, "analyst_recommendation": 2.3}
    chip = format_finviz_chip("XYZ", fv)
    assert chip is not None
    assert "🅰 2.3/5" in chip
    assert "Buy" in chip


def test_chip_shows_divergence_when_fmp_and_finviz_disagree():
    """UX hard-rule contract: divergence >= threshold gets a ⚠ flag."""
    fv = {"target_price": 385, "spot_price": 200, "target_upside_pct": 92.5}
    # FMP says $318 → divergence = (385-318)/318 * 100 = 21%
    chip = format_finviz_chip("NVDA", fv, fmp_target=318, divergence_threshold_pct=20.0)
    assert "⚠" in chip
    assert "diverges from FMP" in chip
    assert "21%" in chip


def test_chip_no_divergence_flag_when_within_threshold():
    fv = {"target_price": 325, "spot_price": 200, "target_upside_pct": 62.5}
    # FMP says $318 → divergence = 2.2% (well below 20%)
    chip = format_finviz_chip("NVDA", fv, fmp_target=318, divergence_threshold_pct=20.0)
    assert "⚠" not in chip
    assert "diverges" not in chip


def test_chip_no_divergence_when_fmp_target_missing():
    """No FMP data → no divergence flag (can't compute)."""
    fv = {"target_price": 100, "analyst_recommendation": 2.0}
    chip = format_finviz_chip("XYZ", fv, fmp_target=None)
    assert "⚠" not in chip


# ─── cross_check ──────────────────────────────────────────────────────────


def test_cross_check_diverged():
    result = cross_check(100, 130, threshold_pct=20)
    assert result["diverged"] is True
    assert result["divergence_pct"] == 30.0
    assert "diverge" in result["label"]


def test_cross_check_aligned():
    result = cross_check(100, 105, threshold_pct=20)
    assert result["diverged"] is False
    assert result["divergence_pct"] == 5.0
    assert "aligned" in result["label"]


def test_cross_check_missing_one_side():
    assert cross_check(None, 100)["diverged"] is False
    assert cross_check(100, None)["diverged"] is False
    assert cross_check(0, 100)["diverged"] is False  # zero-division safety


# ─── _find_ticker_in_line ─────────────────────────────────────────────────


def test_find_ticker_prefers_longer_matches():
    """AAPL should match AAPL, not AA (word-boundary + length-desc order)."""
    tickers = ["AA", "AAPL"]  # order-invariant in the sorted call
    line = "1. **BUY** AAPL 100 shares at $200"
    # Because annotate_finviz sorts longest-first, real usage passes sorted list
    sorted_tickers = sorted(tickers, key=len, reverse=True)
    assert _find_ticker_in_line(line, sorted_tickers) == "AAPL"


def test_find_ticker_returns_none_for_unmatched():
    assert _find_ticker_in_line("just some prose", ["NVDA", "AMD"]) is None


# ─── _is_rec_header ───────────────────────────────────────────────────────


@pytest.mark.parametrize("line, expected", [
    ("1. **CLOSE** NVDA_PUT_180_20260918 — +51%", True),
    ("2. **EXECUTE ROLL** GOOG_CALL_450 — reason", True),
    ("**BUY** NVDA at $200 (thesis intact)", True),
    ("**CSP ENTRY** NVDA $185P", True),
    ("just prose", False),
    ("- some bullet", False),
    ("## Section header", False),
])
def test_is_rec_header(line, expected):
    assert _is_rec_header(line) == expected


# ─── annotate_finviz (the main entry point) ───────────────────────────────


def test_annotate_appends_chip_to_recognized_headers():
    lines = [
        "1. **CLOSE** NVDA_PUT_180_20260918 — +51%",
        "  - some sub-bullet",
        "2. **EXECUTE ROLL** GOOG_CALL_450 — reason",
    ]
    finviz = {
        "NVDA": {"target_price": 318.5, "spot_price": 200, "target_upside_pct": 59, "analyst_recommendation": 1.8},
        "GOOG": {"target_price": 400, "spot_price": 350, "target_upside_pct": 14, "analyst_recommendation": 2.5},
    }
    out, stats = annotate_finviz(
        lines,
        finviz_by_ticker=finviz,
        known_tickers=["NVDA", "GOOG"],
        etf_set=set(),
    )
    # Header lines got chips
    assert "📈 FINVIZ" in out[0]
    assert "📈 FINVIZ" in out[2]
    # Sub-bullet was NOT annotated
    assert "📈 FINVIZ" not in out[1]
    assert stats["annotated"] == 2
    assert stats["no_data"] == 0


def test_annotate_skips_etfs():
    """Hard rule #12 — ETFs are baskets, no per-fund analyst target."""
    lines = ["1. **BUY** SPY 100 shares"]
    finviz = {"SPY": {"target_price": 500, "analyst_recommendation": 2.0}}
    out, stats = annotate_finviz(
        lines,
        finviz_by_ticker=finviz,
        known_tickers=["SPY"],
        etf_set={"SPY"},
    )
    assert out == lines
    assert stats["etf_skipped"] == 1
    assert stats["annotated"] == 0


def test_annotate_skips_when_no_finviz_data():
    """Hard rule #19 — no data → skip, never invent."""
    lines = ["1. **BUY** XYZ 100 shares"]
    out, stats = annotate_finviz(
        lines,
        finviz_by_ticker={"XYZ": None},
        known_tickers=["XYZ"],
        etf_set=set(),
    )
    assert out == lines
    assert stats["no_data"] == 1


def test_annotate_never_double_annotates():
    """Idempotent: running annotator twice doesn't stack chips."""
    lines = ["1. **BUY** NVDA at $200"]
    finviz = {"NVDA": {"target_price": 318, "target_upside_pct": 59}}
    out1, _ = annotate_finviz(
        lines,
        finviz_by_ticker=finviz,
        known_tickers=["NVDA"],
        etf_set=set(),
    )
    out2, stats = annotate_finviz(
        out1,
        finviz_by_ticker=finviz,
        known_tickers=["NVDA"],
        etf_set=set(),
    )
    assert out1 == out2
    # Second pass counts as no_data or skipped since the chip is already present
    assert out2[0].count("📈 FINVIZ") == 1


def test_annotate_flags_divergence_when_fmp_disagrees():
    """When FMP and FINVIZ targets diverge >= threshold, chip carries ⚠."""
    lines = ["1. **BUY** NVDA thesis intact"]
    finviz = {"NVDA": {"target_price": 400, "spot_price": 200, "target_upside_pct": 100}}
    fmp = {"NVDA": 300}  # 33% divergence from FINVIZ 400
    out, stats = annotate_finviz(
        lines,
        finviz_by_ticker=finviz,
        fmp_targets_by_ticker=fmp,
        known_tickers=["NVDA"],
        etf_set=set(),
    )
    assert "⚠" in out[0]
    assert "diverges" in out[0]
    assert stats["diverged"] == 1


def test_annotate_skips_capital_plan_section():
    """The Capital Plan rollup should not be annotated."""
    lines = [
        "## Capital Plan",
        "1. **BUY** NVDA (from plan)",
        "## Other Section",
        "1. **BUY** NVDA (actionable)",
    ]
    finviz = {"NVDA": {"target_price": 318, "analyst_recommendation": 2.0}}
    out, _ = annotate_finviz(
        lines,
        finviz_by_ticker=finviz,
        known_tickers=["NVDA"],
        etf_set=set(),
    )
    # Line inside Capital Plan should NOT be annotated
    assert "📈 FINVIZ" not in out[1]
    # Line outside Capital Plan SHOULD be annotated
    assert "📈 FINVIZ" in out[3]
