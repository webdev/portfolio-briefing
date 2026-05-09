"""Tests for long-term-opportunity-advisor."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from advise import (
    evaluate_equity_action,
    evaluate_options_idea,
    generate_long_term_opportunities,
    format_opportunity_md,
)


def test_exit_on_third_party_sell():
    op = evaluate_equity_action(
        ticker="XYZ", weight_pct=5, target_weight_pct=5,
        rsi=50, drawdown_pct=5, spot=100, sma_200=100,
        third_party_rec="SELL",
    )
    assert op is not None
    assert op.kind == "EXIT"
    assert "SELL" in op.trigger_reasons[0]


def test_trim_on_overweight_extended():
    op = evaluate_equity_action(
        ticker="NVDA", weight_pct=15, target_weight_pct=5,
        rsi=80, drawdown_pct=0, spot=200, sma_200=180,
        third_party_rec="BUY",
    )
    assert op is not None
    assert op.kind == "TRIM"


def test_add_on_oversold_with_buy_rec():
    op = evaluate_equity_action(
        ticker="AMZN", weight_pct=2, target_weight_pct=5,
        rsi=28, drawdown_pct=15, spot=200, sma_200=205,
        third_party_rec="BUY",
    )
    assert op is not None
    assert op.kind == "ADD"
    assert any("oversold" in r.lower() for r in op.trigger_reasons)


def test_no_action_on_neutral_position():
    op = evaluate_equity_action(
        ticker="MSFT", weight_pct=5, target_weight_pct=5,
        rsi=55, drawdown_pct=3, spot=400, sma_200=395,
        third_party_rec="HOLD",
    )
    assert op is None  # HOLD = no action


def test_leap_call_on_buy_rec_with_low_iv():
    op = evaluate_options_idea(
        ticker="GOOG", weight_pct=2, spot=400,
        rsi=50, iv_rank=20, sma_200=395,
        third_party_rec="BUY", has_cash=True,
    )
    assert op is not None
    assert op.kind == "LEAP_CALL"
    assert "365 DTE" in op.concrete_trade or "DTE" in op.concrete_trade


def test_long_dated_csp_on_high_iv():
    op = evaluate_options_idea(
        ticker="AMD", weight_pct=0, spot=150,
        rsi=55, iv_rank=70, sma_200=145,
        third_party_rec="BUY", has_cash=True,
    )
    # Could be either LEAP_CALL or LONG_DATED_CSP depending on IV — high IV = CSP
    assert op is not None
    assert op.kind == "LONG_DATED_CSP"


def test_full_pipeline():
    positions = {
        "NVDA": {"weight_pct": 15, "spot": 200},
        "AMZN": {"weight_pct": 3, "spot": 250},
        "TSLA": {"weight_pct": 9, "spot": 400},
    }
    rsi = {"NVDA": 75, "AMZN": 30, "TSLA": 65}
    iv = {"NVDA": 50, "AMZN": 25, "TSLA": 55}
    recs = {"NVDA": "BUY", "AMZN": "BUY", "TSLA": "HOLD",
            "PLTR": "BUY"}  # not held
    drawdowns = {"NVDA": 0, "AMZN": 12, "TSLA": 5}
    sma200 = {"NVDA": 180, "AMZN": 245, "TSLA": 380, "PLTR": 100}
    targets = {"NVDA": 5, "AMZN": 5, "TSLA": 5}
    # Add PLTR as not-held but recommended (also has spot for fairness)
    positions["PLTR"] = {"weight_pct": 0, "spot": 110}
    del positions["PLTR"]  # actually delete to test "not held" branch

    ops = generate_long_term_opportunities(
        positions, rsi, iv, recs, drawdowns, sma200, targets, has_cash=True,
    )
    kinds = [op.kind for op in ops]
    # NVDA overweight + RSI extended → TRIM
    assert any(op.kind == "TRIM" and op.ticker == "NVDA" for op in ops)
    # AMZN BUY + RSI 30 + drawdown → ADD
    assert any(op.kind == "ADD" and op.ticker == "AMZN" for op in ops)


def test_format_opportunity_md():
    from advise import LongTermOpportunity
    op = LongTermOpportunity(
        kind="ADD", ticker="AAPL",
        trigger_reasons=["RSI 28 oversold", "third-party BUY"],
        concrete_trade="BUY 50 shares @ ~$150",
        rationale="Tech pullback in a quality name.",
        yield_or_cost="$7,500 deployed",
        source="yfinance + Kanchi sheet",
    )
    md = format_opportunity_md(op, 1)
    txt = "\n".join(md)
    assert "ADD" in txt
    assert "AAPL" in txt
    assert "BUY 50 shares" in txt
    assert "RSI 28 oversold" in txt


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
