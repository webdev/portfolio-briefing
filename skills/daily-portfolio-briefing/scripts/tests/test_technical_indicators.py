"""Tests for analysis/technical_indicators.py — indicator math (known-answer
fixtures) + verdict band boundaries + fail-closed behavior + card rendering.

TDD hard rule #33: every band boundary in the verdict functions is pinned
here so a refactor that shifts a threshold fails loudly.
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import technical_indicators as ti  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — synthetic OHLC with known properties
# ─────────────────────────────────────────────────────────────────────────────

def _ohlc(closes, spread_pct: float = 0.01, volume: float = 1_000_000) -> pd.DataFrame:
    """OHLC frame from a close series; High/Low straddle Close by spread_pct."""
    closes = pd.Series(closes, dtype=float)
    idx = pd.bdate_range(end="2026-07-02", periods=len(closes))
    return pd.DataFrame(
        {
            "Open": closes.values,
            "High": closes.values * (1 + spread_pct),
            "Low": closes.values * (1 - spread_pct),
            "Close": closes.values,
            "Volume": volume,
        },
        index=idx,
    )


def _uptrend_df(n: int = 500) -> pd.DataFrame:
    """Steady riser: +0.3%/day with a small wobble — golden cross, above 200-SMA."""
    base = 100 * (1.003 ** np.arange(n))
    wobble = 1 + 0.004 * np.sin(np.arange(n) / 5.0)
    return _ohlc(base * wobble)


def _downtrend_df(n: int = 500) -> pd.DataFrame:
    """Steady faller: -0.3%/day — death cross, below 200-SMA, deep drawdown."""
    base = 400 * (0.997 ** np.arange(n))
    wobble = 1 + 0.004 * np.sin(np.arange(n) / 5.0)
    return _ohlc(base * wobble)


# ─────────────────────────────────────────────────────────────────────────────
# Indicator math — known answers
# ─────────────────────────────────────────────────────────────────────────────

def test_bollinger_known_answer():
    """19×100 then 110: ma=100.5, sd=sqrt(5); bands and position hand-checked."""
    s = pd.Series([100.0] * 19 + [110.0])
    bb = ti.bollinger(s)
    sd = math.sqrt(5.0)  # sample std of 19×100 + 110
    assert bb["mid"] == pytest.approx(100.5)
    assert bb["upper"] == pytest.approx(100.5 + 2 * sd)
    assert bb["lower"] == pytest.approx(100.5 - 2 * sd)
    # spot 110 is ABOVE the upper band → position > 1
    expected_pos = (110 - bb["lower"]) / (bb["upper"] - bb["lower"])
    assert bb["position"] == pytest.approx(expected_pos)
    assert bb["position"] > 1.0
    assert bb["width_pct"] == pytest.approx((4 * sd) / 100.5 * 100)


def test_bollinger_flat_series_centers_position():
    s = pd.Series([50.0] * 30)
    bb = ti.bollinger(s)
    assert bb["position"] == pytest.approx(0.5)  # upper == lower → 0.5 by contract


def test_atr_known_answer_constant_range():
    """H=110, L=90, C=100 every day → TR=20 → ATR(14)=20."""
    n = 30
    df = pd.DataFrame({"High": [110.0] * n, "Low": [90.0] * n, "Close": [100.0] * n})
    assert ti.atr(df) == pytest.approx(20.0)


def test_macd_flat_series_is_zero():
    s = pd.Series([100.0] * 60)
    m = ti.macd(s)
    assert m["macd"] == pytest.approx(0.0)
    assert m["signal"] == pytest.approx(0.0)
    assert m["hist"] == pytest.approx(0.0)


def test_macd_uptrend_positive_hist():
    s = pd.Series(100 * (1.01 ** np.arange(80)))
    m = ti.macd(s)
    assert m["macd"] > 0
    assert m["hist"] != 0  # histogram computed, not defaulted


def test_sma_slope_linear_series():
    """Close rises +1/day → 20-SMA rises 20 points over 20 days."""
    s = pd.Series(np.arange(100, 200, dtype=float))
    slope = ti.sma_slope(s, 20)
    # ma_now = mean(180..199)=189.5, ma_then(21 bars ago) = 169.5
    assert slope == pytest.approx((189.5 - 169.5) / 169.5 * 100)


def test_ath_metrics_known_answer():
    """Max 200, last 150 → dd = -25%; 52w range position from tail(252)."""
    closes = [100.0] * 100 + [200.0] + [150.0] * 300
    s = pd.Series(closes)
    m = ti.ath_metrics(s)
    assert m["ath"] == pytest.approx(200.0)
    assert m["ath_dd_pct"] == pytest.approx(-25.0)
    # tail(252) is all 150s → hi == lo → position defaults to 50
    assert m["yr_position_pct"] == pytest.approx(50)


def test_wilder_rsi_extremes():
    up = pd.Series(100 * (1.01 ** np.arange(60)))
    down = pd.Series(100 * (0.99 ** np.arange(60)))
    assert ti.wilder_rsi(up) > 70
    assert ti.wilder_rsi(down) < 30


def test_swing_levels_cluster_requires_two_touches():
    """A double-bottom at ~100 produces a support cluster with 2 touches."""
    closes = ([110.0] * 10 + [100.0] + [110.0] * 10 + [100.5] + [110.0] * 10) * 2
    df = _ohlc(closes, spread_pct=0.001)
    sr = ti.swing_levels(df, lookback=len(df))
    assert any(s["touches"] >= 2 and abs(s["level"] - 100.3) < 2 for s in sr["supports"])


# ─────────────────────────────────────────────────────────────────────────────
# Verdicts — every label reachable, boundaries pinned
# ─────────────────────────────────────────────────────────────────────────────

def _st(**kw):
    base = dict(rsi=50, bb_position_pct=50, macd_hist=0.0, macd_hist_5d_ago=0.0,
                vs_sma20_pct=0.0, ret_1w_pct=0.0, ret_1m_pct=0.0)
    base.update(kw)
    return ti.short_term_verdict(**base)


def test_st_stretched_on_rsi_70():
    assert _st(rsi=70) == ti.ST_STRETCHED
    assert _st(rsi=69.9, bb_position_pct=50) != ti.ST_STRETCHED


def test_st_stretched_on_rsi_60_plus_upper_band():
    assert _st(rsi=62, bb_position_pct=92) == ti.ST_STRETCHED
    assert _st(rsi=62, bb_position_pct=89) != ti.ST_STRETCHED


def test_st_oversold_bounce_needs_hist_turning_up():
    assert _st(rsi=30, macd_hist=-1.0, macd_hist_5d_ago=-1.5) == ti.ST_OVERSOLD_BOUNCE
    # still falling → bear-momentum, not a bounce call
    assert _st(rsi=30, macd_hist=-1.5, macd_hist_5d_ago=-1.0) == ti.ST_BEAR


def test_st_bear_momentum():
    assert _st(rsi=45, macd_hist=-0.5, macd_hist_5d_ago=-0.2) == ti.ST_BEAR


def test_st_bull_momentum():
    assert _st(rsi=55, macd_hist=0.5, macd_hist_5d_ago=0.2, vs_sma20_pct=2.0) == ti.ST_BULL


def test_st_stabilizing():
    assert _st(rsi=45, macd_hist=0.1, macd_hist_5d_ago=-0.1, vs_sma20_pct=-1.0,
               ret_1w_pct=0.5, ret_1m_pct=-4.0) == ti.ST_STABILIZING


def test_st_neutral_default():
    assert _st(rsi=50, macd_hist=0.0, macd_hist_5d_ago=0.0) == ti.ST_NEUTRAL


def _lt(**kw):
    base = dict(cross="golden", vs_sma200_pct=5.0, sma_200_slope_pct=0.5,
                sma_50_slope_pct=0.5, ath_dd_pct=-5.0, yr_position_pct=70)
    base.update(kw)
    return ti.long_term_verdict(**base)


def test_lt_broken():
    assert _lt(cross="death", vs_sma200_pct=-25, sma_200_slope_pct=-2,
               ath_dd_pct=-45, yr_position_pct=5) == ti.LT_BROKEN


def test_lt_downtrend():
    assert _lt(cross="death", vs_sma200_pct=-10, sma_200_slope_pct=-1,
               ath_dd_pct=-20, yr_position_pct=20) == ti.LT_DOWNTREND


def test_lt_recovery_reclaimed_200_on_death_cross():
    assert _lt(cross="death", vs_sma200_pct=3.0, sma_200_slope_pct=-0.5,
               ath_dd_pct=-30, yr_position_pct=40) == ti.LT_RECOVERY


def test_lt_recovery_deep_dd_above_200_rising_50():
    assert _lt(cross="golden", vs_sma200_pct=2.0, sma_50_slope_pct=1.5,
               ath_dd_pct=-30, yr_position_pct=45) == ti.LT_RECOVERY


def test_lt_secular_uptrend():
    assert _lt(cross="golden", vs_sma200_pct=8.0, sma_200_slope_pct=1.5,
               ath_dd_pct=-4.0, yr_position_pct=87) == ti.LT_SECULAR


def test_lt_uptrend_when_slope_or_range_short_of_secular():
    assert _lt(cross="golden", vs_sma200_pct=4.0, sma_200_slope_pct=0.5,
               yr_position_pct=50) == ti.LT_UPTREND


def test_lt_weakening():
    assert _lt(cross="golden", vs_sma200_pct=-3.0, sma_200_slope_pct=0.2,
               yr_position_pct=30) == ti.LT_WEAKENING


def test_lt_sideways_default():
    assert _lt(cross="death", vs_sma200_pct=-1.0, sma_200_slope_pct=0.5,
               ath_dd_pct=-10, yr_position_pct=45) == ti.LT_SIDEWAYS


def test_verdict_labels_stay_in_vocabulary():
    assert _st() in ti.SHORT_TERM_LABELS
    assert _lt() in ti.LONG_TERM_LABELS


# ─────────────────────────────────────────────────────────────────────────────
# compute_technicals — end-to-end on synthetic series
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_technicals_uptrend_end_to_end():
    snap = ti.compute_technicals("UPUP", _uptrend_df())
    assert snap is not None
    assert snap.cross == "golden"
    assert snap.vs_sma200_pct > 0
    assert snap.rsi_14 is not None and snap.rsi_14 > 50
    assert snap.long_term_verdict in (ti.LT_SECULAR, ti.LT_UPTREND)
    assert snap.ret_1y_pct is not None and snap.ret_1y_pct > 0
    assert snap.atr_pct > 0
    assert snap.yr_position_pct > 80
    d = snap.to_dict()
    assert d["ticker"] == "UPUP"
    assert d["short_term_verdict"] in ti.SHORT_TERM_LABELS


def test_compute_technicals_downtrend_end_to_end():
    snap = ti.compute_technicals("DNDN", _downtrend_df())
    assert snap is not None
    assert snap.cross == "death"
    assert snap.vs_sma200_pct < 0
    assert snap.ath_dd_pct < -40
    assert snap.long_term_verdict in (ti.LT_BROKEN, ti.LT_DOWNTREND)


def test_compute_technicals_respects_caller_rsi():
    """The briefing carries ONE RSI per ticker — caller's value wins."""
    snap = ti.compute_technicals("UPUP", _uptrend_df(), rsi_14=61.8)
    assert snap.rsi_14 == 61.8


def test_compute_technicals_fails_closed_on_short_history():
    snap = ti.compute_technicals("SHRT", _uptrend_df(120))
    assert snap is None  # < MIN_BARS → no fabricated indicators


def test_compute_technicals_fails_closed_on_missing_columns():
    df = pd.DataFrame({"Close": [100.0] * 300})
    assert ti.compute_technicals("NOCOL", df) is None
    assert ti.compute_technicals("EMPTY", pd.DataFrame()) is None
    assert ti.compute_technicals("NONE", None) is None


def test_compute_technicals_no_volume_column_yields_none_ratio():
    df = _uptrend_df().drop(columns=["Volume"])
    snap = ti.compute_technicals("NOVOL", df)
    assert snap is not None
    assert snap.vol_ratio_30d is None  # not fabricated


# ─────────────────────────────────────────────────────────────────────────────
# Technical Read card rendering (steps/technical_read.py)
# ─────────────────────────────────────────────────────────────────────────────

def test_technical_read_card_renders_real_numbers():
    from steps.technical_read import render_technical_read_sections

    snap = ti.compute_technicals("NVDA", _uptrend_df(), rsi_14=72.0)
    snapshot_data = {
        "technicals": {
            "NVDA": {
                "rsi_14": 72.0,
                "deep": snap.to_dict(),
                "support_resistance": {
                    "spot": snap.spot,
                    "supports": [{"price": round(snap.spot * 0.98, 2),
                                  "touches": 5, "strength": 3.0}],
                    "resistances": [{"price": round(snap.spot * 1.03, 2),
                                     "touches": 3, "strength": 2.0}],
                    "note": None,
                },
            },
        },
        "positions": [{"assetType": "EQUITY", "symbol": "NVDA", "qty": 100}],
    }
    config = {"core_positions": ["NVDA"], "technical_analysis": {"enabled": True}}
    md = "\n".join(render_technical_read_sections(snapshot_data, config))
    assert "## 🎯 Technical Read" in md
    assert "**Core equity holdings**" in md
    assert f"### NVDA — ${snap.spot:,.2f}" in md
    assert "RSI 72" in md
    assert "Golden cross" in md
    assert "↑ R $" in md and "↓ S $" in md
    assert "52w range:" in md
    # no fabricated boilerplate markers
    assert "~0.30 delta" not in md


def test_technical_read_fails_closed_per_ticker():
    """A held ticker whose deep read is missing gets the unavailable line —
    never fabricated indicators (hard rules #10/#19)."""
    from steps.technical_read import render_technical_read_sections

    snapshot_data = {
        "technicals": {"AAPL": {"rsi_14": 55.0, "deep": None}},
        "positions": [{"assetType": "EQUITY", "symbol": "AAPL", "qty": 100}],
    }
    md = "\n".join(render_technical_read_sections(snapshot_data, {}))
    assert "### AAPL — chart data unavailable, verify manually" in md
    assert "BB " not in md.split("AAPL")[1].split("###")[0]
