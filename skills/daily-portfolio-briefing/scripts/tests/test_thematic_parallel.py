"""Tests for the thematic-scout parallelism config (task #9).

thematic_scout.parallel / .max_workers in briefing.yaml control the research
fan-out. parallel: false must fall back to a sequential loop with IDENTICAL
per-ticker logic — same tickers, same results, only slower. A failing ticker
is skipped (warned), never fatal, on both paths.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps import thematic_research as tr  # noqa: E402


def _stub_scout(fail_tickers: set[str] | None = None):
    """A stand-in for the scout module: deterministic, zero network."""
    fail = fail_tickers or set()

    def _research_ticker(ticker, theme, cfg, recs_map, held_weights,
                         existing_short_puts=None):
        if ticker in fail:
            raise RuntimeError(f"boom {ticker}")
        return {"ticker": ticker, "theme": theme, "verdict": "WATCH",
                "rationale": [], "spot": 100.0}

    mod = types.SimpleNamespace(_research_ticker=_research_ticker)
    return mod


def _flat(payload):
    return sorted(
        (theme, r["ticker"])
        for theme, rs in payload["results_by_theme"].items()
        for r in rs
    )


@pytest.fixture()
def snapshot_dir(tmp_path):
    d = tmp_path / "snapshots" / "2026-07-03"
    d.mkdir(parents=True)
    return d


def test_parallel_and_sequential_produce_identical_results(
        monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "_load_scout_module", lambda: _stub_scout())

    par_dir = tmp_path / "par" / "2026-07-03"
    seq_dir = tmp_path / "seq" / "2026-07-03"
    par_dir.mkdir(parents=True)
    seq_dir.mkdir(parents=True)

    par = tr.run_thematic_research(par_dir, refresh=True,
                                   parallel=True, max_workers=8)
    seq = tr.run_thematic_research(seq_dir, refresh=True,
                                   parallel=False)

    assert par is not None and seq is not None
    assert _flat(par) == _flat(seq), \
        "parallel fan-out must not change WHICH tickers get researched"
    assert par["summary"] == seq["summary"]


def test_sequential_fallback_via_max_workers_one(monkeypatch, snapshot_dir):
    """max_workers <= 1 must take the sequential path (no thread pool)."""
    monkeypatch.setattr(tr, "_load_scout_module", lambda: _stub_scout())

    def _no_pool(*a, **k):
        raise AssertionError("ThreadPoolExecutor must not be used")

    import concurrent.futures as cf
    monkeypatch.setattr(cf, "ThreadPoolExecutor", _no_pool)

    payload = tr.run_thematic_research(snapshot_dir, refresh=True,
                                       parallel=True, max_workers=1)
    assert payload is not None
    assert payload["summary"]["total"] > 0


def test_failing_ticker_skipped_not_fatal_on_both_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(tr, "_load_scout_module",
                        lambda: _stub_scout(fail_tickers={"NVDA"}))
    for name, parallel in (("p", True), ("s", False)):
        d = tmp_path / name / "2026-07-03"
        d.mkdir(parents=True)
        payload = tr.run_thematic_research(d, refresh=True, parallel=parallel)
        assert payload is not None
        assert all(r["ticker"] != "NVDA"
                   for rs in payload["results_by_theme"].values() for r in rs)
        assert payload["summary"]["total"] > 0


def test_cache_reused_after_run(monkeypatch, snapshot_dir):
    """Second call within the TTL serves the cache — the scout module is not
    even loaded (a loader that raises proves it)."""
    monkeypatch.setattr(tr, "_load_scout_module", lambda: _stub_scout())
    first = tr.run_thematic_research(snapshot_dir, refresh=True, parallel=True)
    assert first is not None

    def _boom():
        raise AssertionError("scout must not be re-loaded on a fresh cache")

    monkeypatch.setattr(tr, "_load_scout_module", _boom)
    second = tr.run_thematic_research(snapshot_dir, refresh=False, parallel=True)
    assert second is not None
    assert _flat(second) == _flat(first)
