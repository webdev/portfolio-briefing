"""Symbol-universe gate (2026-08-26 5ZM.HM foreign-listing leak).

Observed on the real 2026-08-26 briefing — Best Setups #1 rendered:

    **B** (74) `5ZM.HM` — SELL 1× 5ZM.HM $75P exp Fri Nov 20 '26
    (86 DTE, monthly) · 13% ann · RSI 38 prime · RVr 68 ✓ — 🏁 Entry: B

and the 💎 closest-miss line cited the same symbol:

    _none today — closest miss: 5ZM.HM (RVr 68 is a realized-vol proxy …)_

".HM" is a Hamburg exchange suffix — no US option chain exists; the ticket
is unfillable fiction (rule #19). Source of the leak: the
recommendation-list-fetcher's yfinance name→ticker resolution took the
FIRST search hit for "Zoom Video" (the Hamburg listing 5ZM.HM), cached it
in state/cache/ticker_map.json, and the rec flowed into the graded pools.

These tests pin: (a) 5ZM.HM-shape symbols are excluded from
collect_best_setups with a VISIBLE one-line reason and never occupy a slot
or the prime/closest-miss pool; (b) legit dotted US classes (BRK.B) are
unaffected; (c) the source screener (shopping_list.resolve_ticker) ignores
a cached foreign listing and picks the first US-optionable yfinance quote
— or returns None when only foreign quotes exist (never caches a foreign
symbol).
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.setup_grade import (  # noqa: E402
    collect_best_setups,
    render_best_setups,
)
from analysis.symbol_universe import (  # noqa: E402
    is_us_optionable_symbol,
    symbol_exclusion_reason,
)

_REASON = "non-US listing, no US option chain"


def _idea(ticker, score=74.0, letter="B"):
    return {
        "ticker": ticker, "setup_grade": letter,
        "setup_grade_score": score, "setup_grade_drivers": ["RSI 38 prime"],
        "setup_grade_message": "good setup", "annualized_pct": 13.0,
        "strike": 75.0, "expiration": "2026-11-20",
        "expiration_pretty": "Fri Nov 20 '26", "dte": 86, "mid": 2.30,
        "rsi_14": 38.0,
    }


# ── unit: the universe pattern ─────────────────────────────────────────────

def test_is_us_optionable_symbol_shapes():
    """^[A-Z]{1,5}$ plus known dotted US classes; exchange-suffixed foreign
    listings (5ZM.HM Hamburg, SAP.DE, VOD.L, SHOP.TO) and digit roots are
    out."""
    assert is_us_optionable_symbol("AAPL")
    assert is_us_optionable_symbol("MELI")
    assert is_us_optionable_symbol("BRK.B")
    assert is_us_optionable_symbol("BF.B")
    assert not is_us_optionable_symbol("5ZM.HM")
    assert not is_us_optionable_symbol("SAP.DE")
    assert not is_us_optionable_symbol("VOD.L")
    assert not is_us_optionable_symbol("SHOP.TO")
    assert not is_us_optionable_symbol("5ZM")
    assert not is_us_optionable_symbol("")
    assert not is_us_optionable_symbol(None)


def test_symbol_exclusion_reason_wording():
    """The visible reason: '⏸ 5ZM.HM — excluded: non-US listing, no US
    option chain'."""
    assert symbol_exclusion_reason("5ZM.HM") == _REASON
    assert symbol_exclusion_reason("BRK.B") is None
    assert symbol_exclusion_reason("AAPL") is None


# ── pool boundary: collect_best_setups ─────────────────────────────────────

def test_5zm_hm_excluded_from_best_setups_with_visible_reason():
    """Observed: '**B** (74) `5ZM.HM` — SELL 1× 5ZM.HM $75P exp Fri Nov 20
    '26 (86 DTE, monthly) · 13% ann · RSI 38 prime' occupied Best Setups
    slot #1. The foreign symbol must land in the VISIBLE exclusions, never
    a green-lit slot, and never carry a ticket."""
    best = collect_best_setups(new_ideas=[_idea("5ZM.HM")],
                               snapshot_data={})
    assert all(e["ticker"] != "5ZM.HM" for e in best["csp"])
    ex = [e for e in best["excluded_csp"] if e["ticker"] == "5ZM.HM"]
    assert len(ex) == 1
    assert ex[0]["reason"] == _REASON

    lines = "\n".join(render_best_setups(best))
    assert "⏸ 5ZM.HM" in lines
    assert f"excluded: {_REASON}" in lines
    # Never a fabricated chain ticket for the non-optionable symbol.
    assert "SELL" not in lines.split("⏸ 5ZM.HM", 1)[1].splitlines()[0]
    assert "$75P" not in lines


def test_foreign_symbol_never_cites_prime_or_closest_miss():
    """Observed: '_none today — closest miss: 5ZM.HM (RVr 68 is a
    realized-vol proxy …)_' — the excluded foreign symbol must never be
    the prime closest-miss either (it never enters the ranked pool)."""
    best = collect_best_setups(new_ideas=[_idea("5ZM.HM")],
                               snapshot_data={})
    assert all(e["ticker"] != "5ZM.HM" for e in best["prime"])
    cm = best.get("prime_closest_miss")
    assert cm is None or cm.get("ticker") != "5ZM.HM"


def test_brk_b_dotted_us_class_unaffected():
    """BRK.B is a real US listing with a real US chain — the dot check
    must never catch the known dotted US classes."""
    best = collect_best_setups(new_ideas=[_idea("BRK.B", score=70.0)],
                               snapshot_data={})
    assert all(e.get("reason") != _REASON
               for e in best["excluded_csp"])
    assert any(e["ticker"] == "BRK.B" for e in best["csp"])


# ── source screener: recommendation-list-fetcher ───────────────────────────

def _load_shopping_list():
    path = (Path(__file__).resolve().parents[3]
            / "recommendation-list-fetcher" / "scripts" / "shopping_list.py")
    spec = importlib.util.spec_from_file_location("_sl_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sl_config(tmp_path):
    return {
        "ticker_resolution": {
            "cache_path": str(tmp_path / "ticker_map.json"),
            "use_yfinance_fallback": True,
            "manual_overrides": {},
        },
    }


class _FakeSearch:
    quotes: list = []

    def __init__(self, _name):
        pass


def _fake_yfinance(quotes):
    mod = types.ModuleType("yfinance")

    class Search(_FakeSearch):
        pass

    Search.quotes = quotes
    mod.Search = Search
    return mod


def test_resolve_ticker_ignores_cached_foreign_and_prefers_us_quote(
        tmp_path, monkeypatch):
    """The real leak: state/cache/ticker_map.json pinned
    {"Zoom Video": "5ZM.HM"} from a first-hit yfinance resolution. The
    cached foreign listing must be ignored and the FIRST US-optionable
    search quote taken instead — filtered silently at the source."""
    sl = _load_shopping_list()
    cfg = _sl_config(tmp_path)
    (tmp_path / "ticker_map.json").write_text(
        json.dumps({"Zoom Video": "5ZM.HM"}))
    monkeypatch.setitem(
        sys.modules, "yfinance",
        _fake_yfinance([{"symbol": "5ZM.HM"}, {"symbol": "ZM"}]))
    assert sl.resolve_ticker("Zoom Video", cfg) == "ZM"
    # The cache is repaired with the US symbol.
    saved = json.loads((tmp_path / "ticker_map.json").read_text())
    assert saved["Zoom Video"] == "ZM"


def test_resolve_ticker_returns_none_when_only_foreign_quotes(
        tmp_path, monkeypatch):
    """Only foreign listings in the search result → None, and the foreign
    symbol is NEVER cached (rule #19 — no fabricated US symbol)."""
    sl = _load_shopping_list()
    cfg = _sl_config(tmp_path)
    monkeypatch.setitem(
        sys.modules, "yfinance",
        _fake_yfinance([{"symbol": "5ZM.HM"}, {"symbol": "ZM.F"}]))
    assert sl.resolve_ticker("Zoom Video", cfg) is None
    assert not (tmp_path / "ticker_map.json").exists()


def test_resolve_ticker_valid_cache_still_short_circuits(tmp_path):
    """A cached US symbol is returned without touching yfinance (legacy
    behavior unchanged)."""
    sl = _load_shopping_list()
    cfg = _sl_config(tmp_path)
    (tmp_path / "ticker_map.json").write_text(
        json.dumps({"Zoom Video": "ZM"}))
    assert sl.resolve_ticker("Zoom Video", cfg) == "ZM"
