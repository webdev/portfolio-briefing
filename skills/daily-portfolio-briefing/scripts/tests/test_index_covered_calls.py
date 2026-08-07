"""Index covered calls — rule #34 INDEX envelope (SPY/VOO/QQQ).

George (2026-08-07): "How can I improve the briefing and my portfolio? ...
Give me some great ideas." — one approved idea: surface INDEX covered calls
on the uncapped SPY/VOO holdings (income with no single-name risk). Indexes
have no earnings gaps or single-name headline risk, so the punitive
single-name Tier A envelope (RSI ≥ 75 / ≥20% OTM) is inappropriate; a
distinct `covered_call_tiers.tier_a.index_cc` envelope applies:

  - RSI ≥ 55 favored (write into strength — rule #44), 40-55 wait, <40 block
  - delta-first strike (0.18 ± 0.07) via the canonical E*TRADE chain fetcher
  - MEASURED delta rendered (δ n/a if the chain has no Greeks), REAL DTE
  - per-account writability: 100 shares in ONE account (SPY 101 → 1 contract;
    VOO 50 → visible "⏸ not writable" row, never silently skipped)
  - chain unavailable → NO actionable ticket (fail closed, rule #10)
  - config disabled → surface absent, legacy behavior byte-identical
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from steps.strategy_upgrades import compute_strategy_upgrades
import steps.strategy_upgrades as _su
from render.strategy_upgrades_panel import render_strategy_upgrades
from analysis import position_tiers


# ─── Fixtures ──────────────────────────────────────────────────────────────

_INDEX_CC_BLOCK = {
    "enabled": True,
    "tickers": ["SPY", "VOO", "QQQ"],
    "target_delta": 0.18,
    "delta_tolerance": 0.07,
    "max_dte": 45,
    "min_dte": 21,
    "rsi_favored": 55,
    "rsi_block_below": 40,
    "coverage_cap_pct": 100,
}


def _config(index_cc=True):
    cfg = {
        "max_position_pct": 0.10,
        "position_tiers": {
            "tier_a_core": ["NVDA", "GOOG", "MSFT", "META", "PLTR", "AMZN",
                            "VOO", "SPY"],
            "tier_b_income": ["MU", "SMH"],
        },
        "covered_call_tiers": {
            "tier_a": {
                "enabled": True,
                "rsi_floor": 75,
                "min_otm_pct": 20.0,
                "max_delta": 0.10,
                "coverage_cap_pct": 20,
                "max_dte": 30,
                "willing_to_write_cc_on": ["NVDA", "MSFT"],
            },
        },
    }
    if index_cc:
        cfg["covered_call_tiers"]["tier_a"]["index_cc"] = dict(_INDEX_CC_BLOCK)
    return cfg


def _snapshot(rsi=62.0, spy_breakdown=None):
    """George's real 2026-08-07 index book: 101 SPY ($77,910-ish) + 50 VOO."""
    return {
        "positions": [
            {"symbol": "SPY", "assetType": "EQUITY", "qty": 101,
             "price": 772.28, "costBasis": 500.0,
             "accountsBreakdown": spy_breakdown or ["INDIVIDUAL: 101 sh"]},
            {"symbol": "VOO", "assetType": "EQUITY", "qty": 50,
             "price": 709.88, "costBasis": 500.0,
             "accountsBreakdown": ["INDIVIDUAL: 50 sh"]},
        ],
        "balance": {"accountValue": 1_000_000, "cash": 50_000},
        "chains": {},
        "earnings_calendar": {},
        "quotes": {},
        "technicals": {"SPY": {"rsi_14": rsi}, "VOO": {"rsi_14": rsi}},
    }


class _IdxFakeFetcher:
    """Fixture chain — a real-future expiration so DTE math is honest."""

    def __init__(self, delta_quote="default", otm_quote=None, exp=None,
                 no_expiration=False):
        self.exp = exp or (date.today() + timedelta(days=35))
        self.no_expiration = no_expiration
        if delta_quote == "default":
            delta_quote = {
                "strike": 805.0, "bid": 3.10, "mid": 3.20, "ask": 3.30,
                "delta": 0.18, "iv": 0.14, "open_interest": 1200,
                "expiration": self.exp.isoformat(), "source": "etrade_live",
            }
        self._delta_quote = delta_quote
        self._otm_quote = otm_quote
        self.delta_kwargs = None

    def choose_expiration(self, **kw):
        return None if self.no_expiration else self.exp

    def find_strike_near_delta(self, **kw):
        self.delta_kwargs = kw
        return self._delta_quote

    def find_strike_at_otm_pct(self, **kw):
        return self._otm_quote


def _index_recs(upgrades, symbol=None):
    out = [u for u in upgrades if u.get("type") == "index_covered_call"]
    if symbol:
        out = [u for u in out if u.get("underlying") == symbol]
    return out


def _run(snapshot, config):
    return compute_strategy_upgrades(
        snapshot, equity_reviews=[], options_reviews=[], params=config,
    )


# ─── (a) SPY 101 shares → 1-contract ticket, measured delta, real DTE ─────


def test_spy_101_shares_one_contract_measured_delta_real_dte(monkeypatch):
    """George holds 101 SPY — exactly one writable contract. The ticket must
    carry the MEASURED delta from the fixture chain and the REAL DTE derived
    from the actual expiration (never an assumed ~30d)."""
    fake = _IdxFakeFetcher()
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    upgrades = _run(_snapshot(rsi=62.0), _config())

    recs = _index_recs(upgrades, "SPY")
    assert len(recs) == 1
    rec = recs[0]
    assert rec["writable"] is True
    assert rec["contracts_writable"] == 1
    assert rec["actionable"] is True
    assert rec["index_rsi_state"] == "favored"
    assert rec["target_strike"] == 805.0
    assert rec["target_delta"] == 0.18          # measured, from the chain
    assert rec["target_dte"] == 35              # real DTE from actual expiration
    assert rec["expiration"] == fake.exp.isoformat()
    assert rec["chain_source"] == "etrade_live"
    # Composer must feed the INDEX envelope to the delta-first selector.
    assert fake.delta_kwargs["target_delta"] == 0.18
    assert fake.delta_kwargs["tolerance"] == 0.07

    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "🗂 Index Covered Calls" in md
    assert "SELL 1× SPY $805C" in md
    assert "35 DTE" in md
    assert "δ 0.18" in md
    assert "✅ READY TO WRITE (index)" in md


def test_spy_no_greeks_renders_delta_na_never_fabricated(monkeypatch):
    """Chain has no Greeks → %OTM fallback carries delta=None and the ticket
    renders 'δ n/a' — never a fabricated delta (rule #16)."""
    exp = date.today() + timedelta(days=35)
    fake = _IdxFakeFetcher(
        delta_quote=None,
        otm_quote={"strike": 800.0, "bid": 3.90, "mid": 4.00, "ask": 4.10,
                   "delta": None, "expiration": exp.isoformat()},
        exp=exp,
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    upgrades = _run(_snapshot(rsi=62.0), _config())
    rec = _index_recs(upgrades, "SPY")[0]
    assert rec["target_delta"] is None
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "δ n/a" in md


# ─── (b) VOO 50 shares → visible not-writable row ─────────────────────────


def test_voo_50_shares_visible_not_writable_row(monkeypatch):
    """VOO has only 50 shares — a CC needs 100 in one account. The position
    must render as a VISIBLE '⏸ not writable' row (rule #24), never be
    silently skipped."""
    fake = _IdxFakeFetcher()
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    upgrades = _run(_snapshot(rsi=62.0), _config())

    recs = _index_recs(upgrades, "VOO")
    assert len(recs) == 1
    rec = recs[0]
    assert rec["writable"] is False
    assert rec["actionable"] is False
    assert "50 shares" in rec["not_writable_reason"]
    assert "need 100" in rec["not_writable_reason"]

    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "⏸ not writable — 50 shares (need 100 in one account)" in md
    assert "SELL 1× VOO" not in md  # no ticket without a writable lot


def test_per_account_writability_aggregate_106_not_writable(monkeypatch):
    """The real cross-account bug: 15 sh + 91 sh = 106 aggregate but NO
    single account holds a 100-share lot → not writable."""
    fake = _IdxFakeFetcher()
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    snap = _snapshot(
        rsi=62.0,
        spy_breakdown=["Individual Brokerage: 15 sh", "INDIVIDUAL: 91 sh"],
    )
    snap["positions"][0]["qty"] = 106
    upgrades = _run(snap, _config())
    rec = _index_recs(upgrades, "SPY")[0]
    assert rec["writable"] is False
    assert "91 shares" in rec["not_writable_reason"]


# ─── (c) Index RSI band: 50 → wait, 62 → actionable, 38 → blocked ─────────


def test_rsi_50_demotes_to_wait_for_strength(monkeypatch):
    """RSI 50 sits in the index 40-55 band → full ticket shown but demoted
    to wait-for-strength, not green-lit (rule #24)."""
    fake = _IdxFakeFetcher()
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    upgrades = _run(_snapshot(rsi=50.0), _config())
    rec = _index_recs(upgrades, "SPY")[0]
    assert rec["index_rsi_state"] == "wait"
    assert rec["actionable"] is False

    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "⏸ WAIT FOR STRENGTH (index band ≥55)" in md
    assert "SELL 1× SPY $805C" in md  # ticket still shown (rule #24)
    assert "✅ READY TO WRITE (index)" not in md


def test_rsi_62_actionable_with_favoured_badge(monkeypatch):
    """RSI 62 ≥ index favored band 55 → actionable with the ✅ badge
    (write into strength — rule #44)."""
    fake = _IdxFakeFetcher()
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    upgrades = _run(_snapshot(rsi=62.0), _config())
    rec = _index_recs(upgrades, "SPY")[0]
    assert rec["index_rsi_state"] == "favored"
    assert rec["actionable"] is True
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "✅ READY TO WRITE (index)" in md
    assert "✅ RSI favourable" in md


def test_rsi_38_blocked_with_visible_reason(monkeypatch):
    """RSI 38 < index block floor 40 → hard block, rendered VISIBLY with the
    hook's reason — no ticket, no chain call wasted."""
    fake = _IdxFakeFetcher()
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    upgrades = _run(_snapshot(rsi=38.0), _config())
    rec = _index_recs(upgrades, "SPY")[0]
    assert rec["index_rsi_state"] == "blocked"
    assert rec["actionable"] is False
    assert "target_strike" not in rec  # blocked before the chain is consulted

    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "⛔ RSI-blocked (index band)" in md
    assert "RSI 38" in md
    assert "SELL 1× SPY" not in md


def test_index_band_does_not_touch_global_call_bands():
    """The index override band is consumed by the index surface ONLY — the
    global single-name call bands (favored ≥60 / block <35) are unchanged."""
    from analysis import rsi_discipline
    base = rsi_discipline.load_thresholds(_config())
    idx = position_tiers.index_cc_rsi_thresholds(base, _config())
    assert idx["call"]["favored_above"] == 55.0
    assert idx["call"]["block_below"] == 40.0
    # base untouched (deep copy)
    assert base["call"]["favored_above"] == 60.0
    assert base["call"]["block_below"] == 35.0


# ─── (d) Chain unavailable → no actionable ticket ─────────────────────────


def test_chain_unavailable_no_actionable_ticket(monkeypatch):
    """E*TRADE chain unreachable → fail closed: visible row, NO ticket, no
    rule-of-thumb estimate (rule #10 — a wrong ticket costs more than a
    missing one)."""
    fake = _IdxFakeFetcher(delta_quote=None, otm_quote=None)
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    upgrades = _run(_snapshot(rsi=62.0), _config())
    rec = _index_recs(upgrades, "SPY")[0]
    assert rec["actionable"] is False
    assert rec["chain_source"] == "unavailable"
    assert "target_strike" not in rec
    assert "est_premium_per_share" not in rec  # no fabricated numbers

    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "⚠ CHAIN UNAVAILABLE" in md
    assert "no actionable ticket" in md
    assert "SELL 1× SPY" not in md


def test_no_expiration_within_window_fails_closed(monkeypatch):
    """choose_expiration finds nothing near the target window → same
    fail-closed path as an unreachable chain."""
    fake = _IdxFakeFetcher(no_expiration=True)
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    upgrades = _run(_snapshot(rsi=62.0), _config())
    rec = _index_recs(upgrades, "SPY")[0]
    assert rec["actionable"] is False
    assert rec["chain_source"] == "unavailable"


def test_dte_outside_index_window_demoted_with_visible_violation(monkeypatch):
    """A chain expiration outside the 21-45d index window is an envelope
    violation — the ticket renders demoted with the measured DTE shown
    (rule #45 spirit: tenor enforced at selection AND render)."""
    exp = date.today() + timedelta(days=60)
    fake = _IdxFakeFetcher(exp=exp)
    fake._delta_quote["expiration"] = exp.isoformat()
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    upgrades = _run(_snapshot(rsi=62.0), _config())
    rec = _index_recs(upgrades, "SPY")[0]
    assert rec["actionable"] is False
    assert rec["envelope_violations"] == ["DTE 60d outside index window 21-45d"]
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "DTE 60d outside index window 21-45d" in md


# ─── (e) Tier A single-name behavior unchanged ────────────────────────────


def test_tier_a_single_name_behavior_unchanged_by_index_config(monkeypatch):
    """NVDA (on willing_to_write_cc_on) and GOOG (not) must behave exactly
    the same whether or not index_cc is enabled — the index envelope never
    leaks into the single-name Tier A gate."""
    fake = _IdxFakeFetcher()
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))

    def _single_name_snap():
        return {
            "positions": [
                {"symbol": "NVDA", "assetType": "EQUITY", "qty": 700,
                 "price": 1000.0, "costBasis": 400.0},
                {"symbol": "GOOG", "assetType": "EQUITY", "qty": 300,
                 "price": 200.0, "costBasis": 100.0},
            ],
            "balance": {"accountValue": 1_000_000, "cash": 50_000},
            "chains": {}, "earnings_calendar": {}, "quotes": {},
            "technicals": {"NVDA": {"rsi_14": 65}, "GOOG": {"rsi_14": 65}},
        }

    with_idx = _run(_single_name_snap(), _config(index_cc=True))
    without_idx = _run(_single_name_snap(), _config(index_cc=False))

    def _types(upgrades, sym):
        return sorted(u["type"] for u in upgrades if u.get("underlying") == sym)

    for sym in ("NVDA", "GOOG"):
        assert _types(with_idx, sym) == _types(without_idx, sym)
    # GOOG (not opted in) stays a tier_a_no_cc transparency record;
    # NVDA (opted in) stays on the strict single-name envelope.
    assert _types(with_idx, "GOOG") == ["tier_a_no_cc"]
    assert "write_covered_call" in _types(with_idx, "NVDA")
    assert _index_recs(with_idx) == []  # no index records for single names


# ─── (f) Config disabled → surface absent, legacy identical ───────────────


def test_config_disabled_surface_absent_legacy_identical(monkeypatch):
    """Without the index_cc block, SPY (Tier A, 101 sh) reverts to the legacy
    tier_a_no_cc transparency record and the 🗂 subsection is absent."""
    fake = _IdxFakeFetcher()
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    upgrades = _run(_snapshot(rsi=62.0), _config(index_cc=False))

    assert _index_recs(upgrades) == []
    spy_types = sorted(
        u["type"] for u in upgrades if u.get("underlying") == "SPY"
    )
    assert spy_types == ["tier_a_no_cc"]  # legacy path intact

    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "Index Covered Calls" not in md


def test_index_cc_settings_fail_closed_defaults():
    """Missing config → enabled False with the canonical envelope defaults."""
    s = position_tiers.index_cc_settings(None)
    assert s["enabled"] is False
    assert s["target_delta"] == 0.18
    assert s["delta_tolerance"] == 0.07
    assert s["max_dte"] == 45 and s["min_dte"] == 21
    assert position_tiers.is_index_cc_ticker("SPY", None) is False
    assert position_tiers.is_index_cc_ticker("SPY", _config()) is True
    assert position_tiers.is_index_cc_ticker("NVDA", _config()) is False
