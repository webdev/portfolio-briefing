"""Tests for the persistent OHLC cache (task #9).

User's constraint: "STABILITY AND NO BUGS." Every fail-open path is pinned
here: cold miss, warm hit (zero network), incremental upsert, >30d staleness,
corruption, adjusted-close drift, disabled bypass, and the row/size cap.
No network anywhere — the yfinance fetch is injected.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import ohlc_cache  # noqa: E402

NY = ZoneInfo("America/New_York")

# Friday 2026-07-03, 6pm ET — after the close, so last close = same day.
NOW = datetime(2026, 7, 3, 18, 0, tzinfo=NY)


def make_frame(end: str = "2026-07-03", rows: int = 300, base: float = 100.0):
    """Synthetic daily OHLC frame shaped like yf.Ticker().history() output."""
    idx = pd.bdate_range(end=end, periods=rows, tz="America/New_York")
    idx.name = "Date"
    closes = [base + i * 0.1 for i in range(rows)]
    return pd.DataFrame(
        {
            "Open": [c - 0.5 for c in closes],
            "High": [c + 1.0 for c in closes],
            "Low": [c - 1.0 for c in closes],
            "Close": closes,
            "Volume": [1_000_000 + i for i in range(rows)],
            "Dividends": [0.0] * rows,
            "Stock Splits": [0.0] * rows,
        },
        index=idx,
    )


class Recorder:
    """Injectable fetch_fn that records calls and serves canned frames."""

    def __init__(self, frame=None, frames_by_call=None, raise_always=False):
        self.calls: list[tuple[str, int]] = []
        self.frame = frame
        self.frames_by_call = list(frames_by_call or [])
        self.raise_always = raise_always

    def __call__(self, ticker: str, days: int):
        self.calls.append((ticker, days))
        if self.raise_always:
            raise AssertionError("network fetch was called but must not be")
        if self.frames_by_call:
            return self.frames_by_call.pop(0)
        return self.frame


@pytest.fixture(autouse=True)
def cache_in_tmp(tmp_path):
    """Point the cache at a temp dir for every test; restore defaults after."""
    ohlc_cache.configure({"enabled": True, "dir": str(tmp_path / "ohlc"),
                          "max_stale_days": 30})
    yield tmp_path / "ohlc"
    ohlc_cache.configure(None)


# ─── freshness helpers ───────────────────────────────────────────────────────

def test_last_market_close_weekend_rolls_back():
    sat = datetime(2026, 7, 4, 12, 0, tzinfo=NY)  # Saturday
    assert ohlc_cache.last_market_close_date(sat).isoformat() == "2026-07-03"


def test_last_market_close_before_4pm_uses_prior_day():
    thu_morning = datetime(2026, 7, 2, 9, 30, tzinfo=NY)
    assert ohlc_cache.last_market_close_date(thu_morning).isoformat() == "2026-07-01"


# ─── cold miss / warm hit ────────────────────────────────────────────────────

def test_cold_miss_fetches_full_window_and_stores(cache_in_tmp):
    frame = make_frame()
    rec = Recorder(frame=frame)
    out = ohlc_cache.cached_history("TEST", days=730, fetch_fn=rec, now=NOW)
    assert rec.calls == [("TEST", 730)]
    assert len(out) == len(frame)
    files = list(cache_in_tmp.glob("TEST.*"))
    assert files, "cache file must be written on cold miss"


def test_warm_hit_serves_from_disk_zero_network(cache_in_tmp):
    frame = make_frame()
    ohlc_cache.cached_history("TEST", days=730, fetch_fn=Recorder(frame=frame), now=NOW)
    # Second call: any network fetch is a test failure.
    out = ohlc_cache.cached_history(
        "TEST", days=730, fetch_fn=Recorder(raise_always=True), now=NOW)
    assert len(out) == len(frame)
    pd.testing.assert_frame_equal(out, frame, check_dtype=False, check_freq=False)


def test_warm_hit_output_matches_uncached_output(cache_in_tmp):
    """Zero behavior change: cache round-trip returns the same values the raw
    yfinance call would have returned."""
    frame = make_frame()
    uncached = frame.copy()
    ohlc_cache.cached_history("TEST", days=730, fetch_fn=Recorder(frame=frame), now=NOW)
    cached = ohlc_cache.cached_history(
        "TEST", days=730, fetch_fn=Recorder(raise_always=True), now=NOW)
    pd.testing.assert_frame_equal(cached, uncached, check_dtype=False,
                                  check_freq=False)


# ─── stale → incremental upsert ──────────────────────────────────────────────

def test_stale_cache_fetches_only_the_gap_and_upserts(cache_in_tmp):
    # One continuous "truth" series: the cache holds all but the last 4 bars
    # (ends Mon 2026-06-29), the incremental fetch returns the last 7 bars —
    # overlapping closes agree exactly, as real adjusted data would.
    truth = make_frame(end="2026-07-03", rows=304)
    old = truth.iloc[:-4]  # ends 2026-06-29 — 4 days stale vs NOW
    recent = truth.tail(7)
    ohlc_cache.cached_history("TEST", days=730, fetch_fn=Recorder(frame=old), now=NOW)

    rec = Recorder(frame=recent)
    out = ohlc_cache.cached_history("TEST", days=730, fetch_fn=rec, now=NOW)

    assert len(rec.calls) == 1
    _, span = rec.calls[0]
    assert span <= 15, f"incremental fetch should be small, got {span}d"
    assert out.index[-1].date().isoformat() == "2026-07-03"
    assert not out.index.duplicated().any()
    # All the old history is still there (merged, not replaced).
    assert out.index[0] == old.index[0]


def test_over_max_stale_days_does_full_refetch(cache_in_tmp):
    old = make_frame(end="2026-05-15", rows=300)  # ~49 days stale vs NOW
    ohlc_cache.cached_history("TEST", days=730, fetch_fn=Recorder(frame=old), now=NOW)

    fresh = make_frame(end="2026-07-03", rows=505)
    rec = Recorder(frame=fresh)
    out = ohlc_cache.cached_history("TEST", days=730, fetch_fn=rec, now=NOW)
    assert rec.calls == [("TEST", 730)], "must refetch the FULL window"
    assert len(out) == len(fresh)


def test_incremental_fetch_returning_nothing_serves_cache(cache_in_tmp):
    """Holiday / transient-failure path: empty incremental → keep the cache."""
    old = make_frame(end="2026-07-01", rows=300)
    ohlc_cache.cached_history("TEST", days=730, fetch_fn=Recorder(frame=old), now=NOW)
    out = ohlc_cache.cached_history(
        "TEST", days=730, fetch_fn=Recorder(frame=pd.DataFrame()), now=NOW)
    assert len(out) == len(old)


# ─── adjusted-close drift (dividend/split re-adjustment) ─────────────────────

def test_adjustment_drift_triggers_full_refetch(cache_in_tmp):
    old = make_frame(end="2026-07-01", rows=300)
    ohlc_cache.cached_history("TEST", days=730, fetch_fn=Recorder(frame=old), now=NOW)

    # Overlapping bars now carry different (re-adjusted) closes → the whole
    # cached series is on a stale adjustment basis → full refetch required.
    drifted = make_frame(end="2026-07-03", rows=7, base=50.0)
    full = make_frame(end="2026-07-03", rows=505, base=98.0)
    rec = Recorder(frames_by_call=[drifted, full])
    out = ohlc_cache.cached_history("TEST", days=730, fetch_fn=rec, now=NOW)
    assert len(rec.calls) == 2
    assert rec.calls[-1] == ("TEST", 730), "drift must force a FULL refetch"
    assert float(out["Close"].iloc[0]) == pytest.approx(98.0)


# ─── fail-open paths ─────────────────────────────────────────────────────────

def test_corrupt_cache_file_falls_back_to_full_fetch(cache_in_tmp):
    cache_in_tmp.mkdir(parents=True, exist_ok=True)
    (cache_in_tmp / "TEST.csv").write_text("not,a,frame\n\x00garbage")
    frame = make_frame()
    rec = Recorder(frame=frame)
    out = ohlc_cache.cached_history("TEST", days=730, fetch_fn=rec, now=NOW)
    assert rec.calls == [("TEST", 730)]
    assert len(out) == len(frame)


def test_disabled_bypasses_cache_entirely(cache_in_tmp):
    ohlc_cache.configure({"enabled": False, "dir": str(cache_in_tmp)})
    frame = make_frame()
    rec = Recorder(frame=frame)
    out = ohlc_cache.cached_history("TEST", days=730, fetch_fn=rec, now=NOW)
    assert rec.calls == [("TEST", 730)]
    assert out is frame
    assert not list(cache_in_tmp.glob("TEST.*")), "disabled cache must not write"


def test_unwritable_cache_dir_still_returns_data(tmp_path):
    ohlc_cache.configure({"enabled": True,
                          "dir": str(tmp_path / "file-not-dir" / "ohlc")})
    (tmp_path / "file-not-dir").write_text("blocks mkdir")  # dir creation fails
    frame = make_frame()
    out = ohlc_cache.cached_history("TEST", days=730,
                                    fetch_fn=Recorder(frame=frame), now=NOW)
    assert len(out) == len(frame), "write failure must not block the data"


# ─── window coverage (300d-seeded cache can't serve a 730d request) ──────────

def test_shallow_cache_refetches_for_deeper_window(cache_in_tmp):
    shallow = make_frame(end="2026-07-03", rows=100)
    ohlc_cache.cached_history("TEST", days=140, fetch_fn=Recorder(frame=shallow), now=NOW)

    deep = make_frame(end="2026-07-03", rows=505)
    rec = Recorder(frame=deep)
    out = ohlc_cache.cached_history("TEST", days=730, fetch_fn=rec, now=NOW)
    assert rec.calls == [("TEST", 730)]
    assert len(out) == len(deep)

    # And the meta now records 730d — a short-history ticker (IPO) whose full
    # fetch simply has no more bars won't refetch forever.
    out2 = ohlc_cache.cached_history(
        "TEST", days=730, fetch_fn=Recorder(raise_always=True), now=NOW)
    assert len(out2) == len(deep)


def test_deep_cache_serves_trimmed_shallow_request(cache_in_tmp):
    deep = make_frame(end="2026-07-03", rows=505)
    ohlc_cache.cached_history("TEST", days=730, fetch_fn=Recorder(frame=deep), now=NOW)
    out = ohlc_cache.cached_history(
        "TEST", days=300, fetch_fn=Recorder(raise_always=True), now=NOW)
    assert len(out) < len(deep)
    cutoff = NOW - timedelta(days=300)
    assert out.index[0] >= pd.Timestamp(cutoff)


# ─── size sanity (regression against runaway growth) ─────────────────────────

def test_cache_file_row_capped_and_size_bounded(cache_in_tmp):
    huge = make_frame(end="2026-07-03", rows=2000)
    ohlc_cache.cached_history("TEST", days=730, fetch_fn=Recorder(frame=huge), now=NOW)
    files = list(cache_in_tmp.glob("TEST.parquet")) + list(cache_in_tmp.glob("TEST.csv"))
    assert len(files) == 1
    assert files[0].stat().st_size < 200_000, "per-ticker cache must stay small"
    reloaded = ohlc_cache._load("TEST")
    assert len(reloaded) <= 800, "row cap prevents runaway growth"


# ─── consumer wiring: snapshot technicals read through the cache ─────────────

def test_full_technicals_uses_cache_no_yfinance(monkeypatch, cache_in_tmp):
    """_full_technicals must get its OHLC through the cache module and never
    touch yfinance when the cache serves the frame."""
    from steps import snapshot_inputs as si

    frame = make_frame(rows=505)
    ohlc_cache.cached_history("FAKE", days=730, fetch_fn=Recorder(frame=frame), now=NOW)

    monkeypatch.setattr(si, "_ohlc_cache", ohlc_cache)
    monkeypatch.setattr(
        si, "_fetch_price_history",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("yfinance called")))

    # NOW is in the past relative to the real clock, so freshness would fail
    # and trigger a network fetch — pin cached_history's now for the test.
    real = ohlc_cache.cached_history
    monkeypatch.setattr(
        ohlc_cache, "cached_history",
        lambda ticker, days=730, **kw: real(
            ticker, days, fetch_fn=Recorder(raise_always=True), now=NOW))

    tech = si._full_technicals("FAKE")
    assert tech is not None
    assert tech["spot"] == pytest.approx(float(frame["Close"].iloc[-1]), rel=1e-6)
    assert tech["rsi_14"] is not None
    assert tech["sma_200"] is not None
