"""Tests for the intrinsic-value annotation (pure functions, no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import intrinsic_value as iv  # noqa: E402


ETFS = iv.default_etf_set()


# ---------------------------------------------------------------------------
# ETF detection
# ---------------------------------------------------------------------------

def test_default_etf_set_includes_common_and_thematic():
    assert "SPY" in ETFS and "SMH" in ETFS and "URA" in ETFS


def test_config_etf_override_adds_tickers():
    s = iv.default_etf_set({"intrinsic_value": {"etf_tickers": ["FOOX"]}})
    assert "FOOX" in s and "SPY" in s


def test_is_etf():
    assert iv.is_etf("QQQ", ETFS)
    assert not iv.is_etf("NVDA", ETFS)


# ---------------------------------------------------------------------------
# format_fv_note
# ---------------------------------------------------------------------------

def test_format_etf_is_basket():
    assert iv.format_fv_note("SPY", 500.0, None, etf_set=ETFS) == "💵 FV: n/a — basket (ETF)"


def test_format_no_fv_is_na():
    assert iv.format_fv_note("NVDA", 170.0, None, etf_set=ETFS) == "💵 FV: n/a (no FMP data)"


def test_format_dcf_discount_vs_spot():
    note = iv.format_fv_note("NVDA", 200.0, {"dcf": 150.0}, etf_set=ETFS)
    assert "DCF $150" in note
    assert "-25% vs spot" in note  # 150 is 25% below 200


def test_format_target_with_analyst_count():
    note = iv.format_fv_note(
        "AMD", 100.0,
        {"dcf": 120.0, "analyst_target": 130.0, "num_analysts": 42},
        etf_set=ETFS,
    )
    assert "DCF $120 (+20% vs spot)" in note
    assert "analyst PT $130 (+30%, n=42)" in note


def test_format_no_spot_omits_delta():
    note = iv.format_fv_note("AMD", None, {"dcf": 120.0}, etf_set=ETFS)
    assert note == "💵 FV: DCF $120"


# ---------------------------------------------------------------------------
# annotate_intrinsic
# ---------------------------------------------------------------------------

KNOWN = ["NVDA", "AMD", "GOOG", "GOOGL", "SPY", "IONQ"]
FV = {
    "NVDA": {"dcf": 150.0, "analyst_target": 190.0, "num_analysts": 50},
    "AMD": {"dcf": 120.0},
    "GOOGL": {"dcf": 200.0},
}
SPOTS = {"NVDA": 170.0, "AMD": 100.0, "GOOGL": 180.0}


def _annot(lines, **kw):
    return iv.annotate_intrinsic(
        lines, fv_by_ticker=FV, spot_by_ticker=SPOTS,
        known_tickers=KNOWN, etf_set=ETFS, **kw,
    )


def test_numbered_action_header_annotated():
    out, stats = _annot(["1. **CLOSE** NVDA_PUT_150 — frees $15,000"])
    assert "💵 FV:" in out[0]
    assert "DCF $150" in out[0]
    assert stats["annotated"] == 1


def test_bold_scout_header_annotated():
    out, _ = _annot(["**💎 CSP ENTRY (fat premium) · `AMD` · $100.00**"])
    assert "💵 FV: DCF $120 (+20% vs spot)" in out[0]


def test_etf_recommendation_marked_basket():
    out, stats = _annot(["1. **NEW CSP** SPY $480P — premium $900"])
    assert "n/a — basket (ETF)" in out[0]
    assert stats["etf"] == 1


def test_single_stock_without_fv_gets_na():
    out, stats = _annot(["1. **NEW CSP** IONQ $34P — premium $150"])
    assert "n/a (no FMP data)" in out[0]
    assert stats["unavailable"] == 1


def test_prose_and_market_read_lines_untouched():
    lines = [
        "Some prose about NVDA the market and AMD trends.",
        "- 🔥 `NVDA` · $170.00 · +6.3% 5d — Extended: RSI 72 overbought",
    ]
    out, stats = _annot(lines)
    assert out == lines  # no rec keyword on a bold-header → not annotated
    assert stats == {"annotated": 0, "etf": 0, "unavailable": 0}


def test_capital_plan_section_skipped():
    lines = [
        "## 💰 Capital Plan",
        "- NEW CSP NVDA $150P — net cash −$15,000",
    ]
    out, _ = _annot(lines)
    assert "FV:" not in out[1]


def test_candidate_trades_section_skipped():
    # The Candidate Trades section renders its own FV — the post-pass must not
    # double-annotate its entry lines.
    lines = [
        "## 🎯 Candidate Trades — Across Themes",
        "  - **Entry (CSP):** SELL 1× NVDA $150P exp Fri Jun 19 '26",
    ]
    out, _ = _annot(lines)
    assert "FV:" not in out[1]


def test_italic_footer_skipped():
    out, _ = _annot(["_NEW CSP NVDA held back — see footer_"])
    assert "FV:" not in out[0]


def test_idempotent_when_fv_already_present():
    line = "1. **CLOSE** NVDA_PUT_150  · 💵 FV: DCF $150"
    out, _ = _annot([line])
    assert out[0] == line  # not double-annotated


def test_full_option_symbol_resolves_to_underlying_fv():
    # Regression: a CLOSE line carrying the full option contract symbol must
    # resolve to the UNDERLYING's fair value. (The bug was the option symbol
    # leaking into known_tickers and winning longest-match → n/a.)
    line = "2. **CLOSE** META_PUT_530_20260717 — +36% ($+265)  · RSI 46 🟢 pullback"
    out, _ = iv.annotate_intrinsic(
        [line], fv_by_ticker={"META": {"dcf": 276.0, "analyst_target": 800.0, "num_analysts": 40}},
        spot_by_ticker={"META": 610.0}, known_tickers=["META"], etf_set=ETFS,
    )
    assert "DCF $276" in out[0]
    assert "n/a" not in out[0]


def test_goog_not_matched_inside_googl():
    # GOOGL recommendation must use GOOGL's FV, never GOOG's.
    out, _ = _annot(["1. **TRIM** GOOGL — reduce to 12%"])
    assert "DCF $200" in out[0]  # GOOGL dcf, sorted longest-first


def test_fetch_fair_value_uses_stable_endpoints_and_parses(monkeypatch):
    # Exact response shapes returned by FMP's /stable/ API (from live diagnostic).
    calls = []

    def fake_get_json(url, timeout):
        calls.append(url)
        if "discounted-cash-flow" in url:
            return [{"symbol": "AAPL", "date": "2026-05-22",
                     "dcf": 155.47, "Stock Price": 304.99}]
        if "price-target-summary" in url:
            return [{"symbol": "AAPL", "lastMonthCount": 9,
                     "lastMonthAvgPriceTarget": 325.11, "lastQuarterCount": 13,
                     "lastQuarterAvgPriceTarget": 324.31}]
        return None

    monkeypatch.setattr(iv, "_get_json", fake_get_json)
    fv = iv.fetch_fair_value("AAPL", api_key="dummy")
    assert fv["dcf"] == 155.47
    assert fv["analyst_target"] == 324.31   # lastQuarter preferred over lastMonth
    assert fv["num_analysts"] == 13
    # Must hit /stable/ — never the dead legacy /api/v3 or /api/v4 endpoints.
    assert calls and all("/stable/" in u for u in calls)
    assert all("/api/v3" not in u and "/api/v4" not in u for u in calls)


def test_fmp_unavailable_marks_etf_but_not_single_stock():
    lines = [
        "1. **NEW CSP** NVDA $150P — premium $900",
        "1. **NEW CSP** SPY $480P — premium $900",
    ]
    out, stats = iv.annotate_intrinsic(
        lines, fv_by_ticker={}, spot_by_ticker={}, known_tickers=KNOWN,
        etf_set=ETFS, fmp_available=False,
    )
    assert "FV:" not in out[0]            # single stock left alone
    assert "n/a — basket (ETF)" in out[1]  # ETF still marked
    assert stats["etf"] == 1 and stats["unavailable"] == 0
