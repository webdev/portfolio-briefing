"""Tests for analysis/benchmark_tracker.py (task #16).

Hard rule #33: new module = tests required. These pin:
  - exact alpha math on known NLV + SPY series
  - the ±3-day nearest-snapshot lookup (weekends/holidays)
  - None (never fabricated) when a window predates history
  - the <5-snapshot insufficient-history placeholder
  - the NLV-discontinuity guard (deposit/scope change)
  - fail-open behavior on garbage inputs
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import benchmark_tracker as bt  # noqa: E402


def _mk_history(start: date, days: int, start_nlv: float,
                daily_growth: float = 0.0) -> dict:
    """Business-day NLV history (skips weekends, like real snapshots)."""
    out = {}
    d = start
    v = start_nlv
    made = 0
    while made < days:
        if d.weekday() < 5:
            out[d] = v
            v *= (1 + daily_growth)
            made += 1
        d += timedelta(days=1)
    return out


AS_OF = date(2026, 7, 3)  # a Friday


class TestComputeBenchmarkExactMath:
    def test_alpha_exact_on_known_series(self):
        """portfolio +10%, SPY +5% over 30d → alpha exactly +5%."""
        start = AS_OF - timedelta(days=30)
        hist = {start: 100_000.0, AS_OF: 100_000.0}
        # pad to clear the min-snapshot gate
        for i in range(1, 6):
            hist[start + timedelta(days=i)] = 100_000.0
        spy = {start: 500.0, AS_OF: 525.0}
        rep = bt.compute_benchmark(110_000.0, hist, spy, as_of=AS_OF,
                                   windows=[("30d", 30)])
        assert rep.status == "ok"
        w = rep.windows[0]
        assert w.portfolio_return_pct == pytest.approx(10.0)
        assert w.spy_return_pct == pytest.approx(5.0)
        assert w.alpha_pct == pytest.approx(5.0)

    def test_negative_alpha(self):
        start = AS_OF - timedelta(days=30)
        hist = _mk_history(start, 22, 100_000.0)
        spy = {start: 500.0, AS_OF: 550.0}   # SPY +10%
        rep = bt.compute_benchmark(102_000.0, hist, spy, as_of=AS_OF,
                                   windows=[("30d", 30)])
        w = rep.windows[0]
        assert w.portfolio_return_pct == pytest.approx(2.0)
        assert w.alpha_pct == pytest.approx(-8.0)

    def test_iso_string_keys_accepted(self):
        """History keyed by ISO strings (JSON round-trip) works identically."""
        start = AS_OF - timedelta(days=30)
        hist = {(start + timedelta(days=i)).isoformat(): 100_000.0
                for i in range(0, 20)}
        spy = {start.isoformat(): 500.0, AS_OF.isoformat(): 510.0}
        rep = bt.compute_benchmark(105_000.0, hist, spy, as_of=AS_OF.isoformat(),
                                   windows=[("30d", 30)])
        assert rep.windows[0].alpha_pct == pytest.approx(5.0 - 2.0)


class TestWindowEdges:
    def test_window_before_history_returns_none(self):
        """1y window with 60 days of history → None, never fabricated."""
        start = AS_OF - timedelta(days=60)
        hist = _mk_history(start, 40, 100_000.0)
        spy = {d: 500.0 for d in hist}
        rep = bt.compute_benchmark(100_000.0, hist, spy, as_of=AS_OF,
                                   windows=[("1y", 365)])
        w = rep.windows[0]
        assert w.portfolio_return_pct is None
        assert w.alpha_pct is None
        assert "history starts" in w.note

    def test_missing_snapshot_beyond_3_days_returns_none(self):
        """Target start has no snapshot within ±3 days → None gracefully."""
        # History: a cluster ending 10 days before the 30d target, plus recent.
        target = AS_OF - timedelta(days=30)
        hist = {target - timedelta(days=20): 90_000.0}   # earliest — far away
        for i in range(0, 8):
            hist[AS_OF - timedelta(days=i)] = 100_000.0
        spy = {d: 500.0 for d in hist}
        rep = bt.compute_benchmark(100_000.0, hist, spy, as_of=AS_OF,
                                   windows=[("30d", 30)])
        w = rep.windows[0]
        assert w.portfolio_return_pct is None
        assert "no snapshot within" in w.note

    def test_weekend_target_snaps_to_nearest_business_day(self):
        """30d back from a Sunday as_of lands on a weekend gap — the lookup
        finds the Friday snapshot within ±3 days."""
        as_of = date(2026, 7, 5)  # Sunday
        target = as_of - timedelta(days=30)  # 2026-06-05, a Friday
        # Only weekday snapshots exist; remove exact target, keep 06-04 (Thu).
        hist = _mk_history(target - timedelta(days=1), 25, 100_000.0)
        hist.pop(target, None)
        spy = {min(hist): 500.0, max(hist): 500.0}
        rep = bt.compute_benchmark(100_000.0, hist, spy, as_of=as_of,
                                   windows=[("30d", 30)])
        w = rep.windows[0]
        assert w.snapshot_start is not None
        assert abs((w.snapshot_start - target).days) <= 3
        assert w.portfolio_return_pct == pytest.approx(0.0)

    def test_ytd_none_when_history_starts_midyear(self):
        hist = _mk_history(date(2026, 5, 8), 30, 1_000_000.0)
        spy = {d: 500.0 for d in hist}
        rep = bt.compute_benchmark(1_000_000.0, hist, spy, as_of=AS_OF,
                                   windows=[("YTD", None)])
        w = rep.windows[0]
        assert w.name == "YTD"
        assert w.portfolio_return_pct is None
        assert "YTD baseline missing" in w.note

    def test_ytd_computes_when_history_covers_jan(self):
        hist = _mk_history(date(2026, 1, 2), 120, 100_000.0)
        spy = {date(2026, 1, 2): 500.0, AS_OF: 550.0}
        rep = bt.compute_benchmark(120_000.0, hist, spy, as_of=AS_OF,
                                   windows=[("YTD", None)])
        w = rep.windows[0]
        assert w.portfolio_return_pct == pytest.approx(20.0)
        assert w.spy_return_pct == pytest.approx(10.0)
        assert w.alpha_pct == pytest.approx(10.0)

    def test_inception_uses_earliest_snapshot(self):
        start = date(2026, 5, 11)
        hist = _mk_history(start, 30, 1_000_000.0)
        spy = {start: 500.0, AS_OF: 510.0}
        rep = bt.compute_benchmark(1_050_000.0, hist, spy, as_of=AS_OF,
                                   windows=[("inception", None)])
        w = rep.windows[0]
        assert w.snapshot_start == start
        assert w.portfolio_return_pct == pytest.approx(5.0)
        assert w.alpha_pct == pytest.approx(3.0)

    def test_missing_spy_data_leaves_alpha_none(self):
        start = AS_OF - timedelta(days=30)
        hist = _mk_history(start, 22, 100_000.0)
        rep = bt.compute_benchmark(110_000.0, hist, {}, as_of=AS_OF,
                                   windows=[("30d", 30)])
        w = rep.windows[0]
        assert w.portfolio_return_pct == pytest.approx(10.0)
        assert w.spy_return_pct is None
        assert w.alpha_pct is None
        assert "benchmark data unavailable" in w.note


class TestInsufficientAndFailOpen:
    def test_fewer_than_5_snapshots_is_insufficient(self):
        hist = {AS_OF - timedelta(days=i): 100_000.0 for i in range(4)}
        rep = bt.compute_benchmark(100_000.0, hist, {}, as_of=AS_OF)
        assert rep.status == "insufficient_history"
        assert rep.windows == []
        assert "need ≥ 5" in rep.note

    def test_empty_history_is_insufficient(self):
        rep = bt.compute_benchmark(100_000.0, {}, {}, as_of=AS_OF)
        assert rep.status == "insufficient_history"

    def test_zero_nlv_is_unavailable_not_crash(self):
        hist = _mk_history(AS_OF - timedelta(days=30), 22, 100_000.0)
        rep = bt.compute_benchmark(0, hist, {}, as_of=AS_OF)
        assert rep.status == "unavailable"

    def test_garbage_inputs_never_raise(self):
        rep = bt.compute_benchmark("not-a-number", {"bad": "data"},
                                   {"also": "bad"}, as_of="nonsense")
        assert rep.status in ("insufficient_history", "unavailable")

    def test_to_dict_round_trips(self):
        hist = _mk_history(AS_OF - timedelta(days=40), 25, 100_000.0)
        spy = {min(hist): 500.0, AS_OF: 510.0}
        rep = bt.compute_benchmark(105_000.0, hist, spy, as_of=AS_OF)
        d = rep.to_dict()
        json.dumps(d)  # must be JSON-serializable
        assert d["status"] == "ok"
        assert isinstance(d["windows"], list)


class TestDiscontinuityGuard:
    def test_deposit_jump_resets_baseline(self):
        """The real 2026-05-08 case: $131K → $1.1M overnight is a scope
        change, not a +750% return — the baseline must move past it."""
        hist = {date(2026, 5, 8): 131_544.0}
        # 2026-05-09 is a Saturday — business-day history starts Mon 05-11.
        hist.update(_mk_history(date(2026, 5, 9), 25, 1_100_000.0))
        cleaned, note = bt.clean_nlv_history(hist)
        assert date(2026, 5, 8) not in cleaned
        assert min(cleaned) == date(2026, 5, 11)
        assert "discontinuity" in note

    def test_normal_volatility_not_dropped(self):
        hist = {date(2026, 6, 1): 100_000.0, date(2026, 6, 2): 92_000.0,
                date(2026, 6, 3): 105_000.0}
        cleaned, note = bt.clean_nlv_history(hist)
        assert cleaned == hist
        assert note == ""

    def test_inception_window_respects_discontinuity(self):
        hist = {date(2026, 5, 8): 131_544.0}
        # Business-day history starts Mon 2026-05-11 (05-09 is a Saturday).
        hist.update(_mk_history(date(2026, 5, 9), 25, 1_000_000.0))
        spy = {date(2026, 5, 11): 500.0, AS_OF: 500.0}
        rep = bt.compute_benchmark(1_050_000.0, hist, spy, as_of=AS_OF,
                                   windows=[("inception", None)])
        w = rep.windows[0]
        assert w.snapshot_start == date(2026, 5, 11)
        assert w.portfolio_return_pct == pytest.approx(5.0)


class TestLoadNlvHistory:
    def test_reads_balances_and_skips_test_dirs(self, tmp_path):
        for name, nlv in [("2026-07-01", 100.0), ("2026-07-02", 110.0),
                          ("2026-07-02.test", 999.0)]:
            d = tmp_path / name
            d.mkdir()
            (d / "balance.json").write_text(json.dumps({"accountValue": nlv}))
        (tmp_path / "not-a-date").mkdir()
        (tmp_path / "2026-07-03").mkdir()  # no balance.json — skipped
        (tmp_path / "2026-07-04").mkdir()
        (tmp_path / "2026-07-04" / "balance.json").write_text("{corrupt")
        hist = bt.load_nlv_history(tmp_path)
        assert hist == {date(2026, 7, 1): 100.0, date(2026, 7, 2): 110.0}

    def test_missing_root_returns_empty(self, tmp_path):
        assert bt.load_nlv_history(tmp_path / "nope") == {}


class TestConfigWindows:
    def test_windows_from_config(self):
        cfg = {"windows": [
            {"name": "30d", "days": 30},
            {"name": "YTD", "days": None, "mode": "ytd"},
            {"name": "bogus"},                      # no days/mode — dropped
            {"name": "1y", "days": "252"},          # string days coerced
        ]}
        wins = bt._windows_from_config(cfg)
        assert ("30d", 30) in wins
        assert ("YTD", None) in wins
        assert ("1y", 252) in wins
        assert all(name != "bogus" for name, _ in wins)

    def test_empty_config_returns_none_for_defaults(self):
        assert bt._windows_from_config({}) is None
        assert bt._windows_from_config({"windows": []}) is None


class TestSeries:
    def test_series_normalized_from_window_start(self):
        start = AS_OF - timedelta(days=10)
        hist = {start: 100_000.0, start + timedelta(days=5): 105_000.0,
                AS_OF: 110_000.0}
        hist.update({start - timedelta(days=i): 100_000.0 for i in range(1, 5)})
        spy = {start: 500.0, start + timedelta(days=5): 505.0, AS_OF: 510.0}
        rep = bt.compute_benchmark(110_000.0, hist, spy, as_of=AS_OF,
                                   windows=[("30d", 30)])
        s = rep.series
        assert s["dates"][0] <= start.isoformat() or s["portfolio_pct"][0] == 0.0
        assert s["portfolio_pct"][-1] == pytest.approx(10.0, abs=0.5)
