"""Tests for guardrails.py."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import guardrails


def test_loss_stop_monthly_triggers():
    """Test loss stop for monthly (DTE > 10) at 2.0x."""
    position = {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "strikePrice": 170.00,
        "entryPrice": 2.00,
        "currentMid": 4.50,  # 2.25x loss
        "daysToExpiry": 42,  # Monthly
    }
    
    params = {
        "loss_stop_monthly": 2.0,
        "weekly_put_dte_threshold": 10,
    }
    
    result = guardrails.check_loss_stop(position, params)
    
    assert result.fired is True
    assert result.decision.decision == "CLOSE"


def test_loss_stop_weekly_triggers():
    """Test loss stop for weekly (DTE <= 10) at 1.5x."""
    position = {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "strikePrice": 170.00,
        "entryPrice": 2.00,
        "currentMid": 3.10,  # 1.55x loss (> 1.5x threshold)
        "daysToExpiry": 5,  # Weekly
    }
    
    params = {
        "loss_stop_weekly": 1.5,
        "weekly_put_dte_threshold": 10,
    }
    
    result = guardrails.check_loss_stop(position, params)
    
    assert result.fired is True


def test_loss_stop_does_not_fire_on_profit():
    """Test that loss stop doesn't fire when profitable."""
    position = {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "strikePrice": 170.00,
        "entryPrice": 4.85,
        "currentMid": 1.50,  # Profitable (current < entry)
        "daysToExpiry": 42,
    }
    
    params = {
        "loss_stop_monthly": 2.0,
        "weekly_put_dte_threshold": 10,
    }
    
    result = guardrails.check_loss_stop(position, params)
    
    assert result.fired is False


def test_crash_stop_triggers():
    """Test crash stop on >15% intraday drop."""
    position = {
        "symbol": "AAPL",
        "positionType": "SHORT_PUT",
        "entryPrice": 2.00,
        "currentMid": 1.50,
        "dayChange": -18.0,  # 18% drop
    }
    
    result = guardrails.check_crash_stop(position)
    
    assert result.fired is True
    assert result.decision.decision in ["CLOSE", "CLOSE_FOR_PROFIT"]


def test_open_order_wait():
    """Test that open order returns WAIT."""
    context = {
        "existingOpenOrder": True,
    }
    
    result = guardrails.check_open_order(context)
    
    assert result.fired is True
    assert result.decision.decision == "WAIT"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ─── Smart take-profit (task #49) ───────────────────────────────────────


def _params():
    return {
        "hard_ceiling_pct": 0.85,
        "gamma_escape_dte": 10,
        "gamma_escape_min_profit": 0.30,
        "time_adjusted_multiplier": 2.0,
        "time_adjusted_min_days_elapsed": 3,
        "time_adjusted_min_profit": 0.30,
    }


def test_smart_tp_hard_ceiling_fires_at_85pct():
    """Near max profit → close regardless of DTE. Remaining premium
    isn't worth the gamma risk."""
    from guardrails import check_smart_take_profit
    pos = {"entryPrice": 5.0, "currentMid": 0.50,  # 90% captured
           "daysToExpiry": 45, "initialDte": 60}
    r = check_smart_take_profit(pos, _params())
    assert r.fired is True
    assert r.decision.decision == "CLOSE_FOR_PROFIT"
    assert r.decision.matrix_cell == "GUARDRAIL_HARD_CEILING"


def test_smart_tp_gamma_escape_fires_in_last_week():
    """DTE ≤ 10 + ≥30% profit → close before gamma-risk zone eats it."""
    from guardrails import check_smart_take_profit
    pos = {"entryPrice": 5.0, "currentMid": 3.0,   # 40% captured
           "daysToExpiry": 7, "initialDte": 45}
    r = check_smart_take_profit(pos, _params())
    assert r.fired is True
    assert r.decision.matrix_cell == "GUARDRAIL_GAMMA_ESCAPE"


def test_smart_tp_time_adjusted_fires_on_fast_winner():
    """Fast wins get closed early — 40% capture in 5 days of 45-DTE trade
    (11% elapsed) is 40/(11*2) = 1.8× linear expected → fire."""
    from guardrails import check_smart_take_profit
    pos = {"entryPrice": 5.0, "currentMid": 3.0,   # 40% captured
           "daysToExpiry": 40, "initialDte": 45}    # 5 days in, 11% elapsed
    r = check_smart_take_profit(pos, _params())
    assert r.fired is True
    assert r.decision.matrix_cell == "GUARDRAIL_TIME_ADJUSTED"


def test_smart_tp_time_adjusted_holds_slow_trades():
    """32% capture in 30 days of 45-DTE trade (67% elapsed) is only
    32/(67*2) = 0.24× linear — HOLD, let the standard 50% wheel-rule
    take over."""
    from guardrails import check_smart_take_profit
    pos = {"entryPrice": 5.0, "currentMid": 3.4,   # 32% captured
           "daysToExpiry": 15, "initialDte": 45}    # 30d elapsed, 67%
    r = check_smart_take_profit(pos, _params())
    assert r.fired is False


def test_smart_tp_time_adjusted_respects_min_days_floor():
    """Only 1 day in = below min_days_elapsed floor (3) → no fire even
    at high capture (prevents day-1 volatility whipsaws)."""
    from guardrails import check_smart_take_profit
    pos = {"entryPrice": 5.0, "currentMid": 2.5,   # 50% in 1 day
           "daysToExpiry": 44, "initialDte": 45}
    r = check_smart_take_profit(pos, _params())
    # Day 1 → time-adjusted skipped (min_days_elapsed=3), profit not high
    # enough for hard ceiling (85%), DTE not low enough for gamma escape
    assert r.fired is False


def test_smart_tp_returns_hold_at_32pct_slow_trade():
    """The user's example: 32% capture on a slow trade → do NOT fire.
    This is the 'stop wastefully closing at 32%' fix. The matrix cell
    (raised to 50%+ in decision_matrix.yaml) handles the standard rule."""
    from guardrails import check_smart_take_profit
    pos = {"entryPrice": 5.0, "currentMid": 3.4,   # 32% captured
           "daysToExpiry": 30, "initialDte": 45}
    r = check_smart_take_profit(pos, _params())
    assert r.fired is False


def test_smart_tp_no_fire_on_loss():
    """Position underwater → this guardrail doesn't apply.
    (Loss stops handle that separately.)"""
    from guardrails import check_smart_take_profit
    pos = {"entryPrice": 5.0, "currentMid": 7.5,   # -50%
           "daysToExpiry": 30, "initialDte": 45}
    r = check_smart_take_profit(pos, _params())
    assert r.fired is False


def test_smart_tp_uses_config_overrides():
    """User can override each parameter — verifies the config plumbing."""
    from guardrails import check_smart_take_profit
    aggressive = {**_params(), "hard_ceiling_pct": 0.70}  # tighter ceiling
    pos = {"entryPrice": 5.0, "currentMid": 1.4,   # 72% captured
           "daysToExpiry": 30, "initialDte": 45}
    r = check_smart_take_profit(pos, aggressive)
    assert r.fired is True
    assert r.decision.matrix_cell == "GUARDRAIL_HARD_CEILING"
