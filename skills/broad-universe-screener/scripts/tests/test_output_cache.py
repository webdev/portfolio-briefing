"""Tests for the broad-screener output cache (task #9).

The cache must be invisible when warm (same ScreenResult back, banner + FV
re-computed by the caller either way) and must NEVER block a run when broken:
miss, stale, config change, date change, corruption, and degraded-result
paths all fall through to the full screener.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import screen_universe as su  # noqa: E402
from screen_universe import (  # noqa: E402
    ScreenResult,
    load_cached_result,
    load_config,
    run_screener_cached,
    save_cached_result,
    screener_config_hash,
)

DATE = "2026-07-03"


@pytest.fixture()
def config():
    return load_config(Path("/nonexistent"))  # pure defaults


@pytest.fixture()
def repo(tmp_path):
    """Fake repo root with theme_universes.yaml + Parkev cache (mirrors
    test_screener.py's fixture)."""
    themes = tmp_path / "skills/thematic-scout/references"
    themes.mkdir(parents=True)
    (themes / "theme_universes.yaml").write_text(
        "themes:\n  semis:\n    anchors: [NVDA]\n    etfs: [SMH]\n")
    cache = tmp_path / "state/cache"
    cache.mkdir(parents=True)
    (cache / "recommendation_list.json").write_text(
        json.dumps([{"ticker": "V", "recommendation": "BUY"}]))
    return tmp_path


def sample_result(**over) -> ScreenResult:
    r = ScreenResult(
        hits=[{"ticker": "XYZ", "spot": 100.0, "rsi_14": 42.0, "iv_rank": 75.0,
               "score": 7.5, "hist_days": 200, "days_to_earnings": 43,
               "earnings_date": "2026-08-15", "expiries": ["2026-08-21"],
               "support": {"price": 97.0, "touches": 4, "strength": 3.2}}],
        scanned=1200, after_exclusion=900, deep_dived=800, passed_gates=1,
        n_scout_excluded=2, n_parkev_excluded=1,
        fail_reasons={"ABC": "RSI 70 outside 35-50"},
    )
    for k, v in over.items():
        setattr(r, k, v)
    return r


def universe_rows():
    return [{"ticker": "XYZ", "company": "Xyz", "sector": "Ind",
             "market_cap_usd": 15e9, "volume": 1e6}]


def good_deep_dive(_ticker):
    return {
        "spot": 100.0, "hist_days": 200, "rsi_14": 42.0, "iv_rank": 75.0,
        "sma_50": 101.0, "sma_200": 95.0,
        "support_resistance": {"supports": [
            {"price": 97.0, "touches": 4, "age_days": 20, "strength": 3.2}],
            "resistances": []},
        "earnings_date": "2026-08-15", "days_to_earnings": 43,
        "has_options": True, "expiries": ["2026-08-21"],
    }


def boom_deep_dive(_ticker):
    raise AssertionError("deep dive ran — cache hit must skip ALL fetches")


# ─── round trip ──────────────────────────────────────────────────────────────

class TestRoundTrip:
    def test_save_then_load_returns_equal_result(self, tmp_path, config):
        r = sample_result()
        save_cached_result(tmp_path, DATE, config, r)
        loaded = load_cached_result(tmp_path, DATE, config)
        assert loaded is not None
        assert asdict(loaded) == asdict(r)

    def test_cache_hit_skips_fmp_and_yfinance(self, tmp_path, config):
        """On a hit, run_screener never executes — exclusions, FMP, and the
        deep-dive (which would raise here) are all skipped."""
        save_cached_result(tmp_path, DATE, config, sample_result())
        result, from_cache = run_screener_cached(
            config, date_str=DATE, cache_dir=tmp_path,
            repo_root=Path("/nonexistent-repo"),   # would fail exclusions
            deep_dive_fn=boom_deep_dive,           # would raise
        )
        assert from_cache is True
        assert result.hits[0]["ticker"] == "XYZ"

    def test_cache_miss_runs_full_flow_and_populates_cache(self, tmp_path, config, repo):
        result, from_cache = run_screener_cached(
            config, date_str=DATE, cache_dir=tmp_path, repo_root=repo,
            universe_rows=universe_rows(), deep_dive_fn=good_deep_dive,
        )
        assert from_cache is False
        assert result.passed_gates == 1
        # Second run: same result, from cache, zero fetches.
        again, hit = run_screener_cached(
            config, date_str=DATE, cache_dir=tmp_path,
            repo_root=Path("/nonexistent-repo"), deep_dive_fn=boom_deep_dive,
        )
        assert hit is True
        assert asdict(again) == asdict(result)


# ─── invalidation ────────────────────────────────────────────────────────────

class TestInvalidation:
    def test_stale_cache_reruns_full_flow(self, tmp_path, config, repo):
        save_cached_result(tmp_path, DATE, config, sample_result())
        # Age the cache past the TTL.
        path = tmp_path / f"broad_screener_output_{DATE}.json"
        data = json.loads(path.read_text())
        data["generated_at"] = (datetime.now() - timedelta(hours=7)).isoformat()
        path.write_text(json.dumps(data))

        result, from_cache = run_screener_cached(
            config, date_str=DATE, cache_dir=tmp_path, ttl_hours=6,
            repo_root=repo, universe_rows=universe_rows(),
            deep_dive_fn=good_deep_dive,
        )
        assert from_cache is False

    def test_config_change_invalidates(self, tmp_path, config, repo):
        save_cached_result(tmp_path, DATE, config, sample_result())
        changed = json.loads(json.dumps(config))
        changed["gates"]["rsi_min"] = 30.0
        assert screener_config_hash(changed) != screener_config_hash(config)
        assert load_cached_result(tmp_path, DATE, changed) is None

    def test_date_change_invalidates(self, tmp_path, config):
        save_cached_result(tmp_path, DATE, config, sample_result())
        assert load_cached_result(tmp_path, "2026-07-04", config) is None

    def test_corrupted_cache_falls_back_to_full_flow(self, tmp_path, config, repo):
        (tmp_path / f"broad_screener_output_{DATE}.json").write_text("{corrupt")
        result, from_cache = run_screener_cached(
            config, date_str=DATE, cache_dir=tmp_path, repo_root=repo,
            universe_rows=universe_rows(), deep_dive_fn=good_deep_dive,
        )
        assert from_cache is False
        assert result.passed_gates == 1

    def test_cache_disabled_never_reads_or_writes(self, tmp_path, config, repo):
        save_cached_result(tmp_path, DATE, config, sample_result(scanned=999))
        result, from_cache = run_screener_cached(
            config, date_str=DATE, cache_dir=tmp_path, cache_enabled=False,
            repo_root=repo, universe_rows=universe_rows(),
            deep_dive_fn=good_deep_dive,
        )
        assert from_cache is False
        assert result.scanned == 1  # fresh run, not the seeded 999


# ─── degraded results are never cached ───────────────────────────────────────

class TestDegraded:
    def test_rate_limited_result_not_cached(self, tmp_path, config):
        degraded = sample_result(
            note="FMP rate limit hit (HTTP 429) — broad scan skipped this cycle")
        save_cached_result(tmp_path, DATE, config, degraded)
        assert not (tmp_path / f"broad_screener_output_{DATE}.json").exists(), \
            "caching a skipped scan would suppress the retry for 6 hours"

    def test_zero_scan_result_not_cached(self, tmp_path, config):
        save_cached_result(tmp_path, DATE, config, sample_result(scanned=0))
        assert not (tmp_path / f"broad_screener_output_{DATE}.json").exists()

    def test_unwritable_cache_dir_does_not_block(self, tmp_path, config, repo):
        blocked = tmp_path / "not-a-dir"
        blocked.write_text("file blocks mkdir")
        result, from_cache = run_screener_cached(
            config, date_str=DATE, cache_dir=blocked / "cache",
            repo_root=repo, universe_rows=universe_rows(),
            deep_dive_fn=good_deep_dive,
        )
        assert from_cache is False
        assert result.passed_gates == 1
