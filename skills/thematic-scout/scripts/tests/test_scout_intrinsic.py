"""Tests for intrinsic value wired into the standalone scout report."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scout  # noqa: E402

IV = scout._load_intrinsic_module()
ETFS = IV.default_etf_set(None) if IV else set()


def _results():
    R = scout.ScoutResult
    return {
        "semis": [
            R(ticker="NVDA", theme="semis", spot=170.0, rsi_14=40,
              verdict="BUY (pullback)", rationale=["pullback"]),
            R(ticker="IONQ", theme="semis", spot=40.0, rsi_14=29,
              verdict="CSP ENTRY (fat premium)", rationale=["fat premium"]),
        ],
        "applications": [
            R(ticker="IGV", theme="applications", spot=120.0, rsi_14=55,
              verdict="BUY (support test)", rationale=["proxy ETF"]),
            R(ticker="PLTR", theme="applications", spot=80.0, rsi_14=80,
              verdict="AVOID — overheated", rationale=["overheated"]),
        ],
    }


META = {"semis": {"name": "Semis"}, "applications": {"name": "Applications"}}


def test_intrinsic_module_loads():
    assert IV is not None


def test_no_key_single_stocks_clean_etf_basket_footer():
    md = scout._render_report(_results(), META, "Test", iv_mod=IV,
                              fv_by_ticker={}, etf_set=ETFS, fmp_available=False)
    # Single-stock recs are NOT spammed with n/a when there's no key
    nvda = [l for l in md.splitlines() if l.startswith("###") and "NVDA" in l][0]
    assert "FV:" not in nvda
    # ETF rec is still marked basket (no fetch needed)
    igv = [l for l in md.splitlines() if l.startswith("###") and "IGV" in l][0]
    assert "n/a — basket (ETF)" in igv
    # Footer explains fail-closed
    assert any("FMP_API_KEY not configured" in l for l in md.splitlines())


def test_with_fv_recommendations_annotated():
    fv = {
        "NVDA": {"dcf": 150.0, "analyst_target": 190.0, "num_analysts": 50},
        "IONQ": {"dcf": None, "analyst_target": 52.0, "num_analysts": 8},
    }
    md = scout._render_report(_results(), META, "Test", iv_mod=IV,
                              fv_by_ticker=fv, etf_set=ETFS, fmp_available=True)
    nvda = [l for l in md.splitlines() if l.startswith("###") and "NVDA" in l][0]
    assert "DCF $150 (-12% vs spot)" in nvda
    assert "analyst PT $190 (+12%, n=50)" in nvda
    ionq = [l for l in md.splitlines() if l.startswith("###") and "IONQ" in l][0]
    assert "analyst PT $52 (+30%, n=8)" in ionq


def test_non_recommendation_verdicts_not_annotated():
    md = scout._render_report(_results(), META, "Test", iv_mod=IV,
                              fv_by_ticker={"PLTR": {"dcf": 50.0}},
                              etf_set=ETFS, fmp_available=True)
    pltr = [l for l in md.splitlines() if l.startswith("###") and "PLTR" in l][0]
    assert "FV:" not in pltr  # AVOID is not an actionable recommendation
