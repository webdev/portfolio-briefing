"""Task #36 — snapshot chain/quote routing through E*TRADE (hard rule #2).

Covers: routing priority (held-first), budget cap + labeled yfinance
fallback, throttle math (≤4 req/s), per-item provenance truthfulness (the
line-813 "etrade_live" mislabel), token-absent clean fallback, and quote
batching. All network is mocked (house rule).
"""

import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import chain_routing as cr  # noqa: E402
from steps import snapshot_inputs as si  # noqa: E402


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _positions():
    """Two held option underlyings (NVDA near, MU far) + one equity."""
    return [
        {"symbol": "NVDA", "assetType": "EQUITY", "qty": 100, "costBasis": 100},
        {"symbol": "NVDA_P", "assetType": "OPTION", "underlying": "NVDA",
         "expiration": "2026-08-21", "qty": -1},
        {"symbol": "MU_P", "assetType": "OPTION", "underlying": "MU",
         "expiration": "2026-12-18", "qty": -1},
    ]


def _pairs():
    return [
        ("AAOI", "2026-09-18"),      # candidate
        ("MU", "2026-12-18"),        # held pair (far)
        ("NVDA", "2026-10-16"),      # held-underlying future exp
        ("NVDA", "2026-08-21"),      # held pair (near)
        ("UBER", "2026-08-28"),      # candidate (nearer than AAOI)
    ]


def _fast_limiter():
    return cr.RateLimiter(rate_per_sec=1e9, clock=lambda: 0.0,
                          sleeper=lambda s: None)


class FakeRow:
    def __init__(self, strike=100.0):
        self.strike = strike
        self.bid = 1.0
        self.ask = 1.2
        self.last = 1.1
        self.open_interest = 42
        self.iv = 0.31
        self.delta = -0.25
        self.gamma = 0.01
        self.theta = -0.05
        self.vega = 0.11


class FakeFetcher:
    """etrade-chain-fetcher stand-in — no network."""

    def __init__(self, fail_symbols=(), expirations=None):
        self.fail_symbols = set(fail_symbols)
        self.expirations = expirations or [
            date(2026, 8, 21), date(2026, 8, 28), date(2026, 9, 18),
            date(2026, 10, 16), date(2026, 12, 18),
        ]
        self.chain_calls = []

    def list_expirations(self, symbol, cache=None):
        return list(self.expirations)

    def get_chain(self, symbol, expiration, strike_near, n_strikes,
                  chain_type, cache=None):
        self.chain_calls.append((symbol, expiration.isoformat()))
        if symbol in self.fail_symbols:
            return None
        return {"symbol": symbol, "expiration": expiration,
                "calls": [FakeRow(105.0)], "puts": [FakeRow(95.0)],
                "source": "etrade_live"}


def _yf_fetch_many(pairs):
    return {
        f"{u}_{e}": {"underlying": u, "expiration": e,
                     "requested_expiration": e, "snapped": False,
                     "calls": [], "puts": [], "source": "yfinance"}
        for u, e in pairs
    }


# --------------------------------------------------------------------------
# 1. Routing priority — held first, near-expiry first
# --------------------------------------------------------------------------

def test_plan_held_pairs_come_first_near_expiry_first():
    plan = cr.build_chain_plan(_pairs(), _positions(),
                               today=date(2026, 8, 6))
    order = [(i.underlying, i.expiration, i.tier) for i in plan.items]
    # Tier 0 held pairs, near-expiry first: NVDA Aug before MU Dec.
    assert order[0] == ("NVDA", "2026-08-21", 0)
    assert order[1] == ("MU", "2026-12-18", 0)
    # Tier 1: NVDA's future expiration.
    assert order[2] == ("NVDA", "2026-10-16", 1)
    # Tier 2 candidates last, near-expiry first (UBER Aug < AAOI Sep).
    assert order[3] == ("UBER", "2026-08-28", 2)
    assert order[4] == ("AAOI", "2026-09-18", 2)


# --------------------------------------------------------------------------
# 2. Budget cap + labeled fallback
# --------------------------------------------------------------------------

def test_budget_cap_routes_overflow_to_labeled_yfinance():
    plan = cr.build_chain_plan(_pairs(), _positions(), max_chains=3,
                               today=date(2026, 8, 6))
    assert [i.planned_source for i in plan.items] == \
        ["etrade", "etrade", "etrade", "yfinance", "yfinance"]
    # The overflow is the LOWEST-priority items (candidates), never held.
    assert all(i.tier == 2 for i in plan.yfinance_items)


def test_fetch_routed_labels_every_chain_with_actual_source():
    fetcher = FakeFetcher()
    plan = cr.build_chain_plan(_pairs(), _positions(), max_chains=3,
                               today=date(2026, 8, 6))
    spots = {u: 100.0 for u, _ in _pairs()}
    chains = cr.fetch_chains_routed(
        plan, spots, yf_fetch_many=_yf_fetch_many,
        limiter=_fast_limiter(), fetcher=fetcher, fetcher_cache=None)
    assert len(chains) == 5
    counts = cr.chain_source_counts(chains)
    assert counts["etrade"] == 3
    assert counts["yfinance"] == 2
    # Held chains came from E*TRADE with converted yfinance-compatible rows.
    nvda = chains["NVDA_2026-08-21"]
    assert nvda["source"] == "etrade"
    assert nvda["puts"][0]["strike"] == 95.0
    assert nvda["puts"][0]["impliedVolatility"] == 0.31
    assert nvda["puts"][0]["bid"] == 1.0 and nvda["puts"][0]["ask"] == 1.2
    assert nvda["puts"][0]["lastPrice"] == 1.1
    assert nvda["puts"][0]["openInterest"] == 42


def test_etrade_per_item_failure_falls_back_to_labeled_yfinance():
    fetcher = FakeFetcher(fail_symbols={"MU"})
    plan = cr.build_chain_plan(_pairs(), _positions(), max_chains=5,
                               today=date(2026, 8, 6))
    spots = {u: 100.0 for u, _ in _pairs()}
    chains = cr.fetch_chains_routed(
        plan, spots, yf_fetch_many=_yf_fetch_many,
        limiter=_fast_limiter(), fetcher=fetcher, fetcher_cache=None)
    assert chains["MU_2026-12-18"]["source"] == "yfinance"
    assert chains["NVDA_2026-08-21"]["source"] == "etrade"


def test_missing_spot_falls_back_to_yfinance_never_miscentered():
    """No spot → can't center the E*TRADE strike window → labeled yfinance
    fallback rather than a silently mis-centered chain."""
    fetcher = FakeFetcher()
    plan = cr.build_chain_plan([("NVDA", "2026-08-21")], _positions(),
                               today=date(2026, 8, 6))
    chains = cr.fetch_chains_routed(
        plan, {}, yf_fetch_many=_yf_fetch_many,
        limiter=_fast_limiter(), fetcher=fetcher, fetcher_cache=None)
    assert chains["NVDA_2026-08-21"]["source"] == "yfinance"
    assert fetcher.chain_calls == []


# --------------------------------------------------------------------------
# 3. Throttle math — never exceeds 4 req/s
# --------------------------------------------------------------------------

def test_rate_limiter_schedule_never_exceeds_4_per_second():
    t = {"now": 0.0}
    def clock():
        return t["now"]
    def sleeper(s):
        t["now"] += s
    rl = cr.RateLimiter(rate_per_sec=4.0, clock=clock, sleeper=sleeper)
    for _ in range(20):
        rl.acquire()
    slots = rl.slots
    # Consecutive spacing ≥ 0.25s
    for a, b in zip(slots, slots[1:]):
        assert b - a >= 0.25 - 1e-9
    # Any 1-second sliding window holds ≤ 4 request starts
    for s in slots:
        assert sum(1 for x in slots if s <= x < s + 1.0) <= 4


def test_plan_throttle_schedule_respects_rate():
    plan = cr.build_chain_plan(_pairs(), _positions(),
                               today=date(2026, 8, 6))
    sched = plan.throttle_schedule()
    assert len(sched) == plan.estimated_requests()
    for i in range(len(sched) - 4):
        assert sched[i + 4] - sched[i] >= 1.0 - 1e-9


def test_rate_limiter_is_thread_safe_under_concurrency():
    import threading
    t = {"now": 0.0}
    lock = threading.Lock()
    def clock():
        with lock:
            return t["now"]
    def sleeper(s):
        with lock:
            t["now"] += s
    rl = cr.RateLimiter(rate_per_sec=4.0, clock=clock, sleeper=sleeper)
    threads = [threading.Thread(target=rl.acquire) for _ in range(12)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    slots = sorted(rl.slots)
    for a, b in zip(slots, slots[1:]):
        assert b - a >= 0.25 - 1e-9


# --------------------------------------------------------------------------
# 4. Token-absent / total-failure clean fallback
# --------------------------------------------------------------------------

def test_no_fetcher_routes_everything_to_yfinance():
    """OAuth token absence (sandbox) → fetcher unavailable → every chain
    fetched via yfinance, labeled — the pipeline never blocks."""
    plan = cr.build_chain_plan(_pairs(), _positions(),
                               today=date(2026, 8, 6))
    chains = cr.fetch_chains_routed(
        plan, {u: 100.0 for u, _ in _pairs()},
        yf_fetch_many=_yf_fetch_many,
        limiter=_fast_limiter(), fetcher=None, fetcher_cache=None)
    assert len(chains) == 5
    assert all(c["source"] == "yfinance" for c in chains.values())


def test_quotes_token_absent_returns_empty_cleanly(monkeypatch):
    monkeypatch.setattr(cr, "_load_quote_fn", lambda: None)
    assert cr.fetch_quotes_etrade(["NVDA", "MU"],
                                  limiter=_fast_limiter()) == {}


def test_quote_circuit_breaker_stops_hammering_on_total_failure():
    calls = []
    def failing_quote_fn(batch):
        calls.append(batch)
        return None
    syms = [f"S{i}" for i in range(200)]  # 8 batches
    out = cr.fetch_quotes_etrade(syms, limiter=_fast_limiter(),
                                 quote_fn=failing_quote_fn)
    assert out == {}
    assert len(calls) == 3  # breaker trips after 3 consecutive failures


# --------------------------------------------------------------------------
# 5. Quote batching + labeling
# --------------------------------------------------------------------------

def test_quote_batches_max_25_and_indices_excluded():
    syms = [f"S{i}" for i in range(60)] + ["^VIX"]
    batches = cr.plan_quote_batches(syms)
    assert [len(b) for b in batches] == [25, 25, 10]
    assert all("^VIX" not in b for b in batches)


def test_fetch_quotes_etrade_labels_and_computes_day_change():
    def quote_fn(batch):
        return {s: {"last": 110.0, "previousClose": 100.0} for s in batch}
    out = cr.fetch_quotes_etrade(["NVDA", "^VIX"], limiter=_fast_limiter(),
                                 quote_fn=quote_fn)
    assert set(out) == {"NVDA"}  # ^VIX stays on yfinance
    q = out["NVDA"]
    assert q["source"] == "etrade"
    assert q["last"] == 110.0
    assert abs(q["dayChangePct"] - 0.10) < 1e-9


# --------------------------------------------------------------------------
# 6. Provenance truthfulness — per-item, never the mode flag
# --------------------------------------------------------------------------

def test_source_split_label_is_measured_not_mode_flag():
    assert cr.source_split_label(Counter(etrade=112, yfinance=38)) == \
        "etrade+yfinance-fallback"
    assert cr.source_split_label(Counter(etrade=150)) == "etrade"
    # Routing attempted but EVERY chain fell back → label says yfinance,
    # never "etrade_live" (the line-813 mislabel).
    assert cr.source_split_label(Counter(yfinance=44),
                                 routing_attempted=True) == "yfinance"


def test_split_line_format():
    line = cr.split_line("chains", Counter(etrade=112, yfinance=38))
    assert line == "chains: 112 etrade · 38 yfinance-fallback"


def _run_snapshot(tmp_path, monkeypatch, config, etrade_quotes, routed_chains,
                  extra_chain_underlyings=None, quote_capture=None):
    """Run snapshot_inputs end-to-end with all network mocked."""
    fixture_path = tmp_path / "portfolio.json"
    fixture_path.write_text(json.dumps({
        "accounts": [], "positions": _positions(),
        "balance": {"cash": 1000.0, "totalAccountValue": 50000.0},
        "open_orders": [],
    }))

    def fake_market_fetch(quote_syms, tech_syms, earn_syms):
        quotes = {s: {"last": 100.0, "previousClose": 99.0,
                      "dayChangePct": 0.0101} for s in quote_syms}
        return quotes, {}, {}, {}

    def fake_quotes_etrade(syms, **k):
        if quote_capture is not None:
            quote_capture.extend(syms)
        return dict(etrade_quotes)

    monkeypatch.setattr(si, "_parallel_market_data_fetch", fake_market_fetch)
    monkeypatch.setattr(si, "_build_chain_pairs",
                        lambda *a, **k: _pairs())
    monkeypatch.setattr(si, "_parallel_chain_fetch",
                        lambda pairs: _yf_fetch_many(pairs))
    monkeypatch.setattr(cr, "fetch_quotes_etrade", fake_quotes_etrade)
    monkeypatch.setattr(cr, "make_expiration_lister", lambda **k: None)
    monkeypatch.setattr(cr, "fetch_chains_routed",
                        lambda plan, spots, yf_fetch_many, **k: dict(routed_chains))

    snap_dir = tmp_path / "snap"
    return si.snapshot_inputs(config, snap_dir,
                              etrade_fixture=str(fixture_path),
                              extra_chain_underlyings=extra_chain_underlyings)


def test_provenance_reports_honest_per_item_split(tmp_path, monkeypatch):
    """The line-813 regression: chains provenance used to be stamped
    "etrade_live" purely because the run was live, while every chain was
    yfinance. With routing on and a MIXED outcome, provenance must carry the
    measured split + counts."""
    etrade_quotes = {"NVDA": {"last": 101.0, "previousClose": 100.0,
                              "dayChangePct": 0.01, "source": "etrade"}}
    routed_chains = {
        "NVDA_2026-08-21": {"underlying": "NVDA", "expiration": "2026-08-21",
                            "calls": [], "puts": [], "source": "etrade"},
        "MU_2026-12-18": {"underlying": "MU", "expiration": "2026-12-18",
                          "calls": [], "puts": [], "source": "yfinance"},
    }
    data = _run_snapshot(tmp_path, monkeypatch,
                         {"chains": {"source": "etrade"},
                          "chain_iv": {"enabled": False}},
                         etrade_quotes, routed_chains)
    prov = data["data_provenance"]
    assert prov["chains"]["source"] == "etrade+yfinance-fallback"
    assert prov["chains"]["etrade"] == 1
    assert prov["chains"]["yfinance"] == 1
    assert prov["chains"]["source"] != "etrade_live"
    assert prov["quotes"]["source"] == "etrade+yfinance-fallback"
    assert prov["quotes"]["etrade"] == 1
    assert prov["quotes"]["fetched"] == len(data["quotes"])


def test_provenance_all_fallback_says_yfinance_even_when_routing(tmp_path,
                                                                 monkeypatch):
    """Token-absent live-ish run: routing attempted, everything fell back →
    provenance must say yfinance (the truthful degradation), never etrade."""
    routed_chains = {
        "NVDA_2026-08-21": {"underlying": "NVDA", "expiration": "2026-08-21",
                            "calls": [], "puts": [], "source": "yfinance"},
    }
    data = _run_snapshot(tmp_path, monkeypatch,
                         {"chains": {"source": "etrade"},
                          "chain_iv": {"enabled": False}},
                         {}, routed_chains)
    prov = data["data_provenance"]
    assert prov["chains"]["source"] == "yfinance"
    assert prov["chains"]["etrade"] == 0
    assert prov["quotes"]["source"] == "yfinance"


def test_fixture_mode_without_routing_keeps_yfinance_labels(tmp_path,
                                                            monkeypatch):
    """No routing config → legacy yfinance path, per-item labeled."""
    data = _run_snapshot(tmp_path, monkeypatch,
                         {"chain_iv": {"enabled": False}}, {}, {})
    prov = data["data_provenance"]
    assert prov["chains"]["source"] == "yfinance"
    assert prov["quotes"]["source"] == "yfinance"
    assert all(q.get("source") == "yfinance"
               for q in data["quotes"].values())


def test_yfinance_chain_producer_labels_its_chains():
    """_fetch_option_chain output must self-identify as yfinance so per-item
    provenance stays truthful even on the legacy path."""
    fallback = _yf_fetch_many([("NVDA", "2026-08-21")])
    assert fallback["NVDA_2026-08-21"]["source"] == "yfinance"


# --------------------------------------------------------------------------
# 7. Dry-run script — offline, prints the plan
# --------------------------------------------------------------------------

def test_dry_run_script_runs_offline_and_prints_plan(capsys, monkeypatch):
    import importlib.util as ilu
    target = Path(__file__).resolve().parents[1] / "test_chain_routing.py"
    spec = ilu.spec_from_file_location("chain_routing_dryrun", target)
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Any network attempt inside the dry run is a bug — poison yfinance.
    import yfinance as yf
    def _boom(*a, **k):
        raise AssertionError("dry run must not touch the network")
    monkeypatch.setattr(yf, "Ticker", _boom)

    rc = mod.dry_run(max_chains=150, as_of=None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "held underlyings: 36" in out
    assert "candidates: 115" in out
    assert "budget: 150 etrade" in out
    assert "yfinance-fallback" in out
    assert "throttle" in out
    assert "no network calls" in out.lower()


# --------------------------------------------------------------------------
# 8. 2026-08-06 regression — routing engagement + silent wholesale fallback
# --------------------------------------------------------------------------

_CANDIDATES = ["ABNB", "ACHR", "TXN", "IBM", "TWLO"]


def test_routed_quote_universe_includes_candidate_names(tmp_path, monkeypatch):
    """2026-08-06 live run: vintage footer said '111 name(s) had no live
    quote this cycle' even though routing engaged — quote_symbols was still
    held ∪ WATCHLIST (~23 names), so the E*TRADE batch-quote router was
    never ASKED for the candidate/technicals universe. With routing on,
    the requested quote set MUST include extra_chain_underlyings."""
    captured: list = []
    _run_snapshot(tmp_path, monkeypatch,
                  {"chains": {"source": "etrade"},
                   "chain_iv": {"enabled": False}},
                  {}, {},
                  extra_chain_underlyings=_CANDIDATES,
                  quote_capture=captured)
    for sym in _CANDIDATES:
        assert sym in captured, f"{sym} missing from routed quote request"


def test_unrouted_quote_universe_stays_narrow(tmp_path, monkeypatch):
    """Without routing, yfinance can't absorb ~130 quote calls (the original
    throttle gap) — the legacy narrow set (held ∪ WATCHLIST) is preserved."""
    data = _run_snapshot(tmp_path, monkeypatch,
                         {"chain_iv": {"enabled": False}}, {}, {},
                         extra_chain_underlyings=_CANDIDATES)
    prov = data["data_provenance"]
    assert prov["quotes"]["routing_attempted"] is False
    for sym in _CANDIDATES:
        assert sym not in data["quotes"]


def test_engagement_condition_config_source_etrade(tmp_path, monkeypatch):
    """Engagement pin: config chains.source=etrade (no live flag) → routing
    attempted, recorded in provenance for both chains and quotes."""
    data = _run_snapshot(tmp_path, monkeypatch,
                         {"chains": {"source": "etrade"},
                          "chain_iv": {"enabled": False}}, {}, {})
    prov = data["data_provenance"]
    assert prov["chains"]["routing_attempted"] is True
    assert prov["quotes"]["routing_attempted"] is True


def test_wholesale_fallback_is_loud_never_silent(tmp_path, monkeypatch, capsys):
    """Silent wholesale fallback is the bug class: routing attempted but
    ZERO items came from E*TRADE → provenance must carry
    wholesale_fallback_reason AND a loud stderr line must print."""
    routed_chains = {
        "NVDA_2026-08-21": {"underlying": "NVDA", "expiration": "2026-08-21",
                            "calls": [], "puts": [], "source": "yfinance"},
    }
    data = _run_snapshot(tmp_path, monkeypatch,
                         {"chains": {"source": "etrade"},
                          "chain_iv": {"enabled": False}},
                         {}, routed_chains)
    prov = data["data_provenance"]
    assert prov["chains"].get("wholesale_fallback_reason")
    assert prov["quotes"].get("wholesale_fallback_reason")
    err = capsys.readouterr().err
    assert "E*TRADE routing fell back wholesale (chains)" in err
    assert "E*TRADE routing fell back wholesale (quotes)" in err


def test_mixed_cycle_has_no_wholesale_flag(tmp_path, monkeypatch):
    """A cycle with ANY measured etrade items is not a wholesale fallback."""
    etrade_quotes = {"NVDA": {"last": 101.0, "previousClose": 100.0,
                              "dayChangePct": 0.01, "source": "etrade"}}
    routed_chains = {
        "NVDA_2026-08-21": {"underlying": "NVDA", "expiration": "2026-08-21",
                            "calls": [], "puts": [], "source": "etrade"},
    }
    data = _run_snapshot(tmp_path, monkeypatch,
                         {"chains": {"source": "etrade"},
                          "chain_iv": {"enabled": False}},
                         etrade_quotes, routed_chains)
    prov = data["data_provenance"]
    assert "wholesale_fallback_reason" not in prov["chains"]
    assert "wholesale_fallback_reason" not in prov["quotes"]


def test_router_report_reason_on_missing_fetcher(monkeypatch):
    """fetch_chains_routed with no fetcher fills report['fallback_reason']."""
    monkeypatch.setattr(cr, "_load_chain_fetcher", lambda: (None, None))
    plan = cr.build_chain_plan(_pairs(), _positions(),
                               today=date(2026, 8, 6))
    report: dict = {}
    chains = cr.fetch_chains_routed(
        plan, {}, yf_fetch_many=_yf_fetch_many,
        limiter=_fast_limiter(), report=report)
    assert all(c.get("source") == "yfinance" for c in chains.values())
    assert "unavailable" in report.get("fallback_reason", "")


def test_quote_router_report_reason_on_missing_adapter(monkeypatch):
    monkeypatch.setattr(cr, "_load_quote_fn", lambda: None)
    report: dict = {}
    out = cr.fetch_quotes_etrade(["NVDA"], limiter=_fast_limiter(),
                                 report=report)
    assert out == {}
    assert "unavailable" in report.get("fallback_reason", "")


def test_manifest_renders_measured_split_line():
    """The split must be briefing-visible on every routed run — not just
    console stdout (the 2026-08-06 'provenance line appears NOWHERE' bug)."""
    from render.panels import render_manifest
    prov = {"chains": {"etrade": 150, "yfinance": 270,
                       "routing_attempted": True},
            "quotes": {"etrade": 22, "yfinance": 1,
                       "routing_attempted": True}}
    lines = render_manifest("/tmp/snap", prov)
    joined = "\n".join(lines)
    assert "chains 150 etrade · 270 yfinance-fallback" in joined
    assert "quotes 22 etrade · 1 yfinance-fallback" in joined


def test_manifest_renders_wholesale_reason():
    from render.panels import render_manifest
    prov = {"chains": {"etrade": 0, "yfinance": 420,
                       "routing_attempted": True,
                       "wholesale_fallback_reason": "circuit breaker tripped"},
            "quotes": {"etrade": 0, "yfinance": 23,
                       "routing_attempted": True}}
    joined = "\n".join(render_manifest("/tmp/snap", prov))
    assert "⚠ E*TRADE routing fell back wholesale: circuit breaker tripped" in joined


def test_manifest_no_split_line_without_routing():
    from render.panels import render_manifest
    joined = "\n".join(render_manifest("/tmp/snap", {
        "chains": {"etrade": 0, "yfinance": 5, "routing_attempted": False},
        "quotes": {"etrade": 0, "yfinance": 5, "routing_attempted": False}}))
    assert "Data routing" not in joined
    # Legacy no-provenance call still works
    assert "Snapshot Manifest" in "\n".join(render_manifest("/tmp/snap"))
