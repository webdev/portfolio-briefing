"""Regression tests for the broad-universe screener (CLAUDE.md hard rule #33).

Every discipline gate, every fail-closed path, the dedup contract, the
composite score ordering, and the report format are pinned here. No network:
FMP universe rows and deep-dive results are injected.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import screen_universe as su  # noqa: E402
from screen_universe import (  # noqa: E402
    ScreenerError,
    best_support,
    composite_score,
    fmp_screen,
    load_config,
    load_exclusions,
    load_parkev_tickers,
    load_scout_tickers,
    parse_fmp_rows,
    passes_gates,
    render_report,
    run_screener,
)


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def config():
    return load_config(Path("/nonexistent"))  # pure defaults


@pytest.fixture()
def repo(tmp_path):
    """Fake repo root with theme_universes.yaml + Parkev cache."""
    themes = tmp_path / "skills/thematic-scout/references"
    themes.mkdir(parents=True)
    (themes / "theme_universes.yaml").write_text(
        "themes:\n"
        "  semis:\n"
        "    name: Semis\n"
        "    anchors: [NVDA, MU, 'ON']\n"
        "    etfs: [SMH, SOXX]\n"
        "  power:\n"
        "    name: Power\n"
        "    anchors: [VRT]\n"
        "    etfs: []\n"
    )
    cache = tmp_path / "state/cache"
    cache.mkdir(parents=True)
    (cache / "recommendation_list.json").write_text(json.dumps([
        {"ticker": "V", "recommendation": "BUY"},
        {"ticker": "AMAT", "recommendation": "HOLD"},
    ]))
    return tmp_path


def good_tech(**over):
    """A deep-dive dict that passes every gate; override fields per test."""
    base = {
        "spot": 100.0,
        "hist_days": 200,
        "rsi_14": 42.0,
        "iv_rank": 75.0,
        "sma_50": 101.0,
        "sma_200": 95.0,
        "support_resistance": {
            "supports": [
                {"price": 97.0, "touches": 4, "age_days": 20, "strength": 3.2,
                 "source": "swing", "confluence": []},
            ],
            "resistances": [],
        },
        "earnings_date": "2026-08-15",
        "days_to_earnings": 43,
        "has_options": True,
        "expiries": ["2026-08-21", "2026-09-18", "2026-10-16"],
    }
    base.update(over)
    return base


def good_row(ticker="XYZ", **over):
    row = {"ticker": ticker, "company": "Xyz Corp", "sector": "Industrials",
           "market_cap_usd": 15e9, **good_tech()}
    row.update(over)
    return row


# ─── exclusions (dedup is load-bearing) ─────────────────────────────────────

class TestExclusions:
    def test_scout_tickers_include_anchors_and_etfs(self, repo):
        t = load_scout_tickers(repo / "skills/thematic-scout/references/theme_universes.yaml")
        assert t == {"NVDA", "MU", "ON", "SMH", "SOXX", "VRT"}

    def test_yaml_boolean_trap_on_ticker(self, tmp_path):
        """Bare ON parses as YAML True — str() must recover 'TRUE' not crash.
        (The real file quotes 'ON'; this pins the defensive str() cast.)"""
        p = tmp_path / "themes.yaml"
        p.write_text("themes:\n  x:\n    anchors: [ON]\n    etfs: []\n")
        t = load_scout_tickers(p)
        assert t  # doesn't crash; produces a string entry
        assert all(isinstance(x, str) for x in t)

    def test_missing_theme_file_fails_closed(self, tmp_path):
        with pytest.raises(ScreenerError, match="missing"):
            load_scout_tickers(tmp_path / "nope.yaml")

    def test_missing_parkev_cache_fails_closed(self, tmp_path):
        with pytest.raises(ScreenerError, match="missing"):
            load_parkev_tickers(tmp_path / "nope.json")

    def test_empty_parkev_cache_fails_closed(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("[]")
        with pytest.raises(ScreenerError, match="no tickers"):
            load_parkev_tickers(p)

    def test_corrupt_parkev_cache_fails_closed(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json")
        with pytest.raises(ScreenerError, match="unparseable"):
            load_parkev_tickers(p)

    def test_union_and_counts(self, repo, config):
        exc, n_scout, n_parkev = load_exclusions(config, repo)
        assert n_scout == 6 and n_parkev == 2
        assert {"NVDA", "MU", "SMH", "V", "AMAT"} <= exc

    def test_covered_names_never_surface(self, repo, config):
        """The MU/NVDA bug: an already-tracked name must NOT appear as new."""
        rows = [
            {"ticker": "MU", "company": "Micron", "sector": "Tech", "market_cap_usd": 100e9},
            {"ticker": "NVDA", "company": "Nvidia", "sector": "Tech", "market_cap_usd": 3e12},
            {"ticker": "ZZZZ", "company": "Zed", "sector": "Utilities", "market_cap_usd": 10e9},
        ]
        res = run_screener(config, repo_root=repo, universe_rows=rows,
                           deep_dive_fn=lambda t: good_tech())
        assert res.after_exclusion == 1
        assert [h["ticker"] for h in res.hits] == ["ZZZZ"]


# ─── FMP company-screener (top of funnel) ────────────────────────────────────

class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class TestFmpScreen:
    def test_parse_fmp_rows(self):
        data = [
            {"symbol": "abcd", "companyName": "Abcd Inc", "price": 44.10,
             "marketCap": 5_200_000_000, "sector": "Energy", "industry": "Oil",
             "volume": 900000},
            {"symbol": "", "companyName": "Blank", "marketCap": 1e9},
            {"symbol": "BADC", "companyName": "Bad Cap", "marketCap": "garbage",
             "sector": "x", "industry": "x"},
        ]
        rows = parse_fmp_rows(data)
        assert len(rows) == 2  # blank symbol dropped
        assert rows[0]["ticker"] == "ABCD"
        assert rows[0]["market_cap_usd"] == pytest.approx(5.2e9)
        assert rows[0]["sector"] == "Energy"
        assert rows[1]["market_cap_usd"] is None  # unparseable cap → fail closed

    def test_parse_non_list_payload_returns_empty(self):
        assert parse_fmp_rows({"Error Message": "Invalid API key"}) == []
        assert parse_fmp_rows(None) == []

    def test_missing_api_key_fails_closed(self, config):
        with pytest.raises(ScreenerError, match="FMP_API_KEY"):
            fmp_screen(config, None)

    def test_query_params_carry_config_filters(self, config, monkeypatch):
        """The FMP call must send the server-side filters from config."""
        import requests
        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            return _FakeResp(200, [{"symbol": "XYZ", "companyName": "Xyz",
                                    "marketCap": 15e9, "sector": "Industrials",
                                    "industry": "Machinery"}])

        monkeypatch.setattr(requests, "get", fake_get)
        rows, note = fmp_screen(config, "test-key")
        assert note is None
        assert [r["ticker"] for r in rows] == ["XYZ"]
        assert "stable/company-screener" in captured["url"]
        p = captured["params"]
        assert p["marketCapMoreThan"] == 2_000_000_000
        assert p["volumeMoreThan"] == 500_000
        assert p["exchange"] == "NYSE,NASDAQ"
        assert p["isEtf"] == "false"          # lowercase string, not Python bool
        assert p["isActivelyTrading"] == "true"
        assert p["country"] == "US"
        assert p["limit"] == 1500
        assert p["apikey"] == "test-key"

    def test_fmp_screener_returns_empty_on_rate_limit(self, repo, config, monkeypatch):
        """FMP 429 → graceful empty result with a note, never a crash.

        User requirement (task #8): 'FMP returns 429 (rate limit) → return
        empty result set with a note in the report.'
        """
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(429))
        rows, note = fmp_screen(config, "test-key")
        assert rows == []
        assert "429" in note

        res = run_screener(config, repo_root=repo, api_key="test-key",
                           deep_dive_fn=lambda t: good_tech())
        assert res.scanned == 0
        assert res.hits == []
        assert "429" in res.note
        md = render_report(res, date_str="2026-07-03")
        assert "429" in md
        assert "No setups passed" in md

    def test_fmp_non_200_returns_empty_with_note(self, config, monkeypatch):
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(500))
        rows, note = fmp_screen(config, "test-key")
        assert rows == [] and "500" in note

    def test_fmp_zero_rows_fails_closed(self, config, monkeypatch):
        """200-with-empty-list means the query is broken — never 'no market'."""
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp(200, []))
        with pytest.raises(ScreenerError, match="zero rows"):
            fmp_screen(config, "test-key")


# ─── discipline gates (each rule, each failure mode) ─────────────────────────

class TestGates:
    def test_good_row_passes(self, config):
        ok, why = passes_gates(good_row(), config)
        assert ok, why

    @pytest.mark.parametrize("rsi", [30.0, 34.9, 50.1, 65.0])
    def test_rsi_out_of_band_fails(self, config, rsi):
        ok, why = passes_gates(good_row(rsi_14=rsi), config)
        assert not ok and "RSI" in why

    @pytest.mark.parametrize("rsi", [35.0, 40.0, 50.0])
    def test_rsi_band_edges_pass(self, config, rsi):
        assert passes_gates(good_row(rsi_14=rsi), config)[0]

    def test_rsi_missing_fails_closed(self, config):
        ok, why = passes_gates(good_row(rsi_14=None), config)
        assert not ok and "fail closed" in why

    def test_iv_below_60_fails(self, config):
        ok, why = passes_gates(good_row(iv_rank=59.9), config)
        assert not ok and "IV rank" in why

    def test_iv_missing_fails_closed(self, config):
        ok, why = passes_gates(good_row(iv_rank=None), config)
        assert not ok and "fail closed" in why

    def test_no_support_cluster_fails(self, config):
        ok, why = passes_gates(
            good_row(support_resistance={"supports": [], "resistances": []}), config)
        assert not ok and "support" in why

    def test_support_missing_sr_fails_closed(self, config):
        ok, why = passes_gates(good_row(support_resistance=None), config)
        assert not ok and "support" in why

    def test_support_too_far_fails(self, config):
        sr = {"supports": [{"price": 90.0, "touches": 5, "strength": 4.0}]}
        ok, why = passes_gates(good_row(support_resistance=sr), config)  # 10% away
        assert not ok and "support" in why

    def test_support_too_few_touches_fails(self, config):
        sr = {"supports": [{"price": 97.0, "touches": 2, "strength": 2.0}]}
        ok, why = passes_gates(good_row(support_resistance=sr), config)
        assert not ok and "touches" in why

    def test_earnings_within_21d_fails(self, config):
        ok, why = passes_gates(
            good_row(earnings_date="2026-07-10", days_to_earnings=7), config)
        assert not ok and "earnings" in why.lower()

    def test_earnings_unknown_fails_closed(self, config):
        ok, why = passes_gates(
            good_row(earnings_date=None, days_to_earnings=None), config)
        assert not ok and "fail closed" in why

    def test_no_options_chain_fails(self, config):
        ok, why = passes_gates(good_row(has_options=False, expiries=[]), config)
        assert not ok and "options" in why.lower()

    def test_small_cap_fails(self, config):
        ok, why = passes_gates(good_row(market_cap_usd=1.5e9), config)
        assert not ok and "market cap" in why

    def test_cap_missing_fails_closed(self, config):
        ok, why = passes_gates(good_row(market_cap_usd=None), config)
        assert not ok and "fail closed" in why

    def test_short_history_fails(self, config):
        ok, why = passes_gates(good_row(hist_days=60), config)
        assert not ok and "history" in why


# ─── best_support ────────────────────────────────────────────────────────────

class TestBestSupport:
    def test_picks_strongest_qualifying(self):
        sr = {"supports": [
            {"price": 98.0, "touches": 3, "strength": 2.0},
            {"price": 96.5, "touches": 4, "strength": 3.5},
        ]}
        lv = best_support(sr, 100.0, max_distance_pct=5.0, min_touches=3)
        assert lv["price"] == 96.5

    def test_ignores_levels_above_spot(self):
        sr = {"supports": [{"price": 105.0, "touches": 5, "strength": 4.0}]}
        assert best_support(sr, 100.0, max_distance_pct=5.0, min_touches=3) is None

    def test_none_payload(self):
        assert best_support(None, 100.0, max_distance_pct=5.0, min_touches=3) is None


# ─── composite score ─────────────────────────────────────────────────────────

class TestScore:
    def test_rsi_40_beats_rsi_50(self, config):
        r40 = good_row(rsi_14=40.0)
        r50 = good_row(rsi_14=50.0)
        assert composite_score(r40, config) > composite_score(r50, config)

    def test_higher_iv_scores_higher(self, config):
        assert (composite_score(good_row(iv_rank=95.0), config)
                > composite_score(good_row(iv_rank=62.0), config))

    def test_stronger_support_scores_higher(self, config):
        weak = good_row()
        weak["support"] = {"price": 97.0, "touches": 3, "strength": 1.5}
        strong = good_row()
        strong["support"] = {"price": 97.0, "touches": 6, "strength": 4.5}
        assert composite_score(strong, config) > composite_score(weak, config)

    def test_score_bounded_0_10(self, config):
        row = good_row(rsi_14=40.0, iv_rank=100.0)
        row["support"] = {"price": 97.0, "touches": 9, "strength": 99.0}
        assert 0.0 <= composite_score(row, config) <= 10.0


# ─── orchestrator ────────────────────────────────────────────────────────────

class TestRunScreener:
    def test_output_capped_at_30(self, repo, config):
        rows = [{"ticker": f"T{i:03d}", "company": "x", "sector": "x",
                 "market_cap_usd": 10e9} for i in range(60)]
        res = run_screener(config, repo_root=repo, universe_rows=rows,
                           deep_dive_fn=lambda t: good_tech())
        assert len(res.hits) == 30
        assert res.passed_gates == 60

    def test_deep_dive_cap_respected(self, repo, config):
        config["deep_dive_max"] = 10
        rows = [{"ticker": f"T{i:03d}", "company": "x", "sector": "x",
                 "market_cap_usd": 10e9} for i in range(50)]
        res = run_screener(config, repo_root=repo, universe_rows=rows,
                           deep_dive_fn=lambda t: good_tech())
        assert res.deep_dived == 10

    def test_hits_sorted_by_score_desc(self, repo, config):
        def dd(t):
            return good_tech(iv_rank={"AAA": 62.0, "BBB": 95.0, "CCC": 75.0}[t])
        rows = [{"ticker": t, "company": "x", "sector": "x", "market_cap_usd": 10e9}
                for t in ("AAA", "BBB", "CCC")]
        res = run_screener(config, repo_root=repo, universe_rows=rows, deep_dive_fn=dd)
        assert [h["ticker"] for h in res.hits] == ["BBB", "CCC", "AAA"]

    def test_failed_deep_dive_recorded_not_defaulted(self, repo, config):
        rows = [{"ticker": "DEAD", "company": "x", "sector": "x", "market_cap_usd": 10e9}]
        res = run_screener(config, repo_root=repo, universe_rows=rows,
                           deep_dive_fn=lambda t: None)
        assert res.hits == []
        assert "fail closed" in res.fail_reasons["DEAD"]

    def test_gate_failures_carry_reasons(self, repo, config):
        rows = [{"ticker": "HOTT", "company": "x", "sector": "x", "market_cap_usd": 10e9}]
        res = run_screener(config, repo_root=repo, universe_rows=rows,
                           deep_dive_fn=lambda t: good_tech(rsi_14=72.0))
        assert "RSI" in res.fail_reasons["HOTT"]


# ─── report ──────────────────────────────────────────────────────────────────

def _result_with_hits(config, n=2):
    hits = []
    for i in range(n):
        row = good_row(ticker=f"HIT{i}")
        ok, _ = passes_gates(row, config)
        assert ok
        row["score"] = composite_score(row, config)
        hits.append(row)
    return su.ScreenResult(hits=hits, scanned=900, after_exclusion=100,
                           deep_dived=100, passed_gates=n,
                           n_scout_excluded=139, n_parkev_excluded=191)


class TestReport:
    def test_header_counts(self, config):
        md = render_report(_result_with_hits(config), date_str="2026-07-03")
        assert "# Broad Universe Screener — 2026-07-03" in md
        assert "Scanned 900 candidates" in md
        assert "191 Parkev + 139 Scout" in md

    def test_row_contents(self, config):
        md = render_report(_result_with_hits(config, 1), date_str="2026-07-03")
        assert "### 1. HIT0 — $100.00" in md
        assert "RSI 42 · IV rank 75 · at support $97.00 (4 touches" in md
        assert "Score:" in md
        assert "Earnings: 43d away" in md
        assert "Options chain: yes (2026-08-21" in md

    def test_capacity_deferred_tag(self, config):
        md = render_report(_result_with_hits(config), date_str="2026-07-03",
                           capacity_banner="🔒 CAPACITY: entry gates CLOSED",
                           capacity_open=False)
        assert md.startswith("🔒 CAPACITY")
        assert md.count("⏸ Deferred (capacity gated)") == 2

    def test_open_gates_no_deferred_tag(self, config):
        md = render_report(_result_with_hits(config), date_str="2026-07-03",
                           capacity_banner="✅ gates open", capacity_open=True)
        assert "⏸ Deferred" not in md

    def test_empty_result(self, config):
        res = su.ScreenResult(scanned=900, after_exclusion=50, deep_dived=50,
                              passed_gates=0, n_scout_excluded=139,
                              n_parkev_excluded=191)
        md = render_report(res, date_str="2026-07-03")
        assert "No setups passed" in md

    def test_no_fabricated_fv_line(self, config):
        """A hit without an FMP fair value renders NO FV line (hard rule #19)."""
        md = render_report(_result_with_hits(config, 1), date_str="2026-07-03")
        assert "FV:" not in md
