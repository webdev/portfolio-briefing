"""Tests for _live_quote — holiday/pre-open NaN handling.

Root cause from 2026-06-19 (Juneteenth, US market closed): yfinance's
`history(period="10d")` returned today as a partial bar with NaN Close,
and `.iloc[-1]` picked up that NaN. The result: every quote in the
snapshot came back `last=nan`, poisoning the entire briefing.

The fix in _live_quote: drop NaN closes before extracting `last`, so we
always get the most recent REAL close (yesterday on a holiday, or last
Friday on a long weekend).
"""

from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps import snapshot_inputs as si  # noqa: E402


class _FakeTicker:
    """yfinance.Ticker stand-in with a controllable history DataFrame."""
    def __init__(self, hist_df):
        self._hist = hist_df

    def history(self, period=None):
        return self._hist


def _patch_yf(monkeypatch, hist_df):
    monkeypatch.setattr(si, "_fetch_price_history",
                        lambda ticker, days=10: _FakeTicker(hist_df))


def test_live_quote_drops_holiday_nan_bar(monkeypatch):
    """The exact 2026-06-19 Juneteenth scenario: history has 10 real rows
    plus today's NaN bar tacked on. The fix must skip the NaN and return
    the most recent real close."""
    dates = pd.date_range("2026-06-09", periods=10, freq="B")
    closes = [730.0, 732.5, 735.1, 740.0, 736.2, 738.8, 743.0, 745.5, 748.4, float("nan")]
    hist = pd.DataFrame({"Close": closes}, index=dates)
    _patch_yf(monkeypatch, hist)

    q = si._live_quote("SPY")
    assert q != {}                                # not bailed out
    assert q["last"] == 748.4                     # most recent REAL close, not NaN
    assert q["previousClose"] == 745.5            # one step back
    # day change: (748.4 - 745.5) / 745.5 ≈ 0.00389
    assert abs(q["dayChangePct"] - 0.00389) < 1e-4


def test_live_quote_handles_multiple_trailing_nans(monkeypatch):
    """Long weekend or extended holiday — multiple trailing NaN bars
    should all be skipped."""
    dates = pd.date_range("2026-06-09", periods=8, freq="B")
    closes = [730.0, 732.5, 735.1, 740.0, 736.2, float("nan"), float("nan"), float("nan")]
    hist = pd.DataFrame({"Close": closes}, index=dates)
    _patch_yf(monkeypatch, hist)

    q = si._live_quote("SPY")
    assert q["last"] == 736.2
    assert q["previousClose"] == 740.0


def test_live_quote_returns_empty_when_all_nan(monkeypatch):
    """Pathological case — every bar is NaN. Return empty dict so callers
    treat the symbol as missing rather than receiving a NaN price."""
    dates = pd.date_range("2026-06-09", periods=5, freq="B")
    hist = pd.DataFrame({"Close": [float("nan")] * 5}, index=dates)
    _patch_yf(monkeypatch, hist)

    q = si._live_quote("BROKEN")
    assert q == {}


def test_live_quote_normal_trading_day_unchanged(monkeypatch):
    """Regression check: a regular trading day with no NaNs returns the
    same values as before the fix."""
    dates = pd.date_range("2026-06-09", periods=10, freq="B")
    closes = [730.0, 732.5, 735.1, 740.0, 736.2, 738.8, 743.0, 745.5, 748.4, 750.0]
    hist = pd.DataFrame({"Close": closes}, index=dates)
    _patch_yf(monkeypatch, hist)

    q = si._live_quote("SPY")
    assert q["last"] == 750.0
    assert q["previousClose"] == 748.4
    # 5d change vs iloc[-6] = closes[4] = 736.2 → (750 - 736.2) / 736.2 ≈ 0.0187
    assert abs(q["fiveDayChangePct"] - 0.0187) < 1e-3


def test_live_quote_returns_empty_when_history_empty(monkeypatch):
    """Empty DataFrame (symbol not found) returns empty dict."""
    hist = pd.DataFrame({"Close": []})
    _patch_yf(monkeypatch, hist)

    q = si._live_quote("DELISTED")
    assert q == {}
