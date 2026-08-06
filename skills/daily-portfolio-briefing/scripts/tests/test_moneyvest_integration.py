"""Task #46 — Moneyvest integration tests: 💰 chip render, buy-ladder strike
anchoring (+ gate non-override), FV-divergence flag, index context / regime
advisory, and the degraded/disabled paths. All offline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import moneyvest_chip as mv  # noqa: E402


_PAYLOAD = {
    "as_of": "2026-08-06T12:00:00Z",
    "provenance": "live",
    "list_last_updated": "August 5, 2026",
    "shopping_list": [
        {"ticker": "NVDA", "section": "MAG7", "price": 182.1,
         "fair_value": 152.0, "light_buy": 196.0, "heavy_buy": 160.0,
         "no_brainer": 130.0, "pe_2027": 24.0},
        {"ticker": "SMH", "section": "ETFs", "price": 300.0,
         "fair_value": 280.0, "light_buy": 290.0, "heavy_buy": None,
         "no_brainer": None, "pe_2027": None},
    ],
    "index": {"sp500": {"value": 3.82, "label": "OPTIMISTIC"},
              "ndx": {"value": 3.10, "label": "NEUTRAL"}},
    "m_scores": {"NVDA": 4.05},
}


# ── Chip ──────────────────────────────────────────────────────────────────

def test_chip_renders_compact_format():
    """2026-08-06 attribution decoration: ONE chip grammar —
    `💰 MV: FV $X · LB $Y · HB $Z · M N.N` — so Moneyvest data is never
    confused with FMP FV / Parkev data."""
    row = _PAYLOAD["shopping_list"][0]
    chip = mv.format_mv_chip(row, 4.05)
    assert chip == "💰 MV: FV $152 · LB $196 · HB $160 · M 4.05"


def test_chip_na_safe_partial_data():
    assert mv.format_mv_chip({"ticker": "X", "section": "MAG7",
                              "fair_value": 100.0}) == "💰 MV: FV $100"
    assert mv.format_mv_chip(None) == ""
    assert mv.format_mv_chip({"ticker": "X", "section": "s"}) == ""
    # m_score only (no shopping-list row) still renders
    assert mv.format_mv_chip(None, 3.2) == "💰 MV: M 3.20"
    # No-Brainer only renders when neither LB nor HB is present
    assert mv.format_mv_chip({"ticker": "X", "section": "MAG7",
                              "no_brainer": 90.0}) == "💰 MV: NB $90"


def test_chip_etf_rows_get_index_only_treatment():
    """ETF section rows never get a per-stock chip — an ETF has no company
    fair value (mirrors the intrinsic-value basket rule)."""
    etf = _PAYLOAD["shopping_list"][1]
    assert mv.format_mv_chip(etf, 4.0) == ""


def test_annotate_appends_chip_to_parkev_lines_only():
    md = ("**CANDIDATE · `NVDA` · $182.10**  · 🅿️ BUY · ◐ Med · 0d\n"
          "- prose line about NVDA with no chip\n"
          "**`SMH` covered call**  · 🅿️ no rec\n")
    out = mv.annotate_mv_chips(md, _PAYLOAD)
    lines = out.splitlines()
    assert "💰 MV: FV $152 · LB $196 · HB $160 · M 4.05" in lines[0]
    assert "💰" not in lines[1]      # no 🅿️ marker → untouched
    assert "💰" not in lines[2]      # ETF → index-only, no chip


def test_annotate_never_double_annotates():
    md = "**`NVDA`** · 🅿️ BUY · 💰 FV $152\n"
    assert mv.annotate_mv_chips(md, _PAYLOAD) == md


def test_annotate_fail_open_on_empty_payload():
    md = "**`NVDA`** · 🅿️ BUY\n"
    assert mv.annotate_mv_chips(md, {}) == md
    assert mv.annotate_mv_chips(md, None) == md


# ── FV divergence ─────────────────────────────────────────────────────────

def test_divergence_flag_fires_over_40pct():
    note = mv.divergence_note(152.0, 242.0)
    assert note == "⚠ models diverge (MV $152 vs DCF $242) — trust neither blindly"


def test_divergence_flag_silent_when_models_agree():
    assert mv.divergence_note(152.0, 160.0) is None
    assert mv.divergence_note(None, 242.0) is None
    assert mv.divergence_note(152.0, None) is None


def test_annotate_appends_divergence_on_dcf_lines():
    md = "**`NVDA` LT_CSP** · 🅿️ BUY · 💵 FV: DCF $242 (+33% vs spot)\n"
    out = mv.annotate_mv_chips(md, _PAYLOAD)
    assert "⚠ models diverge (MV $152 vs DCF $242)" in out
    # And no divergence note when DCF agrees
    md2 = "**`NVDA` LT_CSP** · 🅿️ BUY · 💵 FV: DCF $160 (+5% vs spot)\n"
    out2 = mv.annotate_mv_chips(md2, _PAYLOAD)
    assert "models diverge" not in out2


# ── Index context + regime advisory ───────────────────────────────────────

def test_index_context_lines_and_contrarian_advisory():
    lines = mv.index_context_lines(_PAYLOAD)
    assert lines[0] == ("- 💰 Moneyvest — sentiment: **3.82 OPTIMISTIC (S&P) · "
                        "3.10 NEUTRAL (NDX)**")
    assert any("contrarian" in ln for ln in lines[1:])


def test_index_context_no_advisory_on_fear():
    p = {"index": {"sp500": {"value": 1.9, "label": "FEAR"}}}
    lines = mv.index_context_lines(p)
    assert len(lines) == 1
    assert "FEAR" in lines[0]


def test_index_context_stale_cache_label():
    p = dict(_PAYLOAD)
    p["provenance"] = "stale_cache"
    p["stale_hours"] = 26.0
    lines = mv.index_context_lines(p)
    assert "stale cache 26h" in lines[0]


def test_index_context_empty_payload_renders_nothing():
    assert mv.index_context_lines({}) == []
    assert mv.index_context_lines(None) == []


def test_regime_advisory_is_advisory_only():
    note = mv.regime_advisory(_PAYLOAD)
    assert "advisory only" in note
    assert "contrarian caution" in note
    assert mv.regime_advisory({}) is None


# ── Buy-ladder strike anchoring — LT_CSP (advise.py) ─────────────────────

def _load_advisor():
    import importlib.util as ilu
    name = "lt_advise_mv_test"
    if name in sys.modules:
        return sys.modules[name]
    target = (Path(__file__).resolve().parents[3]
              / "long-term-opportunity-advisor" / "scripts" / "advise.py")
    spec = ilu.spec_from_file_location(name, target)
    mod = ilu.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses needs the module registered
    spec.loader.exec_module(mod)
    return mod


def test_lt_csp_anchors_to_light_buy_in_band():
    """Light Buy $182 on a $200 stock (9% OTM — inside the 7-13% band) →
    strike floors to $180 and the rationale says so."""
    adv = _load_advisor()
    op = adv.evaluate_options_idea(
        ticker="NVDA", weight_pct=2.0, spot=200.0, rsi=45, iv_rank=60,
        sma_200=180.0, third_party_rec="BUY", has_cash=True,
        mv_ladder={"light_buy": 182.0, "heavy_buy": 150.0,
                   "no_brainer": 120.0})
    assert op is not None and op.kind == "LONG_DATED_CSP"
    assert "$180P" in op.concrete_trade
    assert "Strike anchored to 💰 MV Light Buy $182" in op.rationale


def test_lt_csp_ladder_outside_band_keeps_legacy_strike():
    """Light Buy far above/below the 7-13% OTM band → anchor ignored,
    legacy spot×0.90 heuristic preserved (never widens the band)."""
    adv = _load_advisor()
    op = adv.evaluate_options_idea(
        ticker="NVDA", weight_pct=2.0, spot=200.0, rsi=45, iv_rank=60,
        sma_200=180.0, third_party_rec="BUY", has_cash=True,
        mv_ladder={"light_buy": 196.0})  # only 2% OTM — chase territory
    assert op is not None
    assert "$180P" in op.concrete_trade  # legacy 200×0.90
    assert "💰" not in op.rationale


def test_lt_csp_mv_anchor_takes_precedence_over_sr():
    adv = _load_advisor()
    sr = [{"side": "support", "price": 185.0, "strength": 3.0,
           "touches": 3, "source": "swing"}]
    op = adv.evaluate_options_idea(
        ticker="NVDA", weight_pct=2.0, spot=200.0, rsi=45, iv_rank=60,
        sma_200=180.0, third_party_rec="BUY", has_cash=True,
        sr_levels=sr, mv_ladder={"light_buy": 182.0})
    assert "💰 MV Light Buy" in op.rationale
    assert "support (" not in op.rationale


def test_lt_csp_anchor_never_overrides_gates():
    """The anchor is selection-only: a rec that fails the IV/rec gate still
    produces NO idea even with a perfect ladder (never resurrects a gated
    trade)."""
    adv = _load_advisor()
    op = adv.evaluate_options_idea(
        ticker="NVDA", weight_pct=2.0, spot=200.0, rsi=45, iv_rank=20,
        sma_200=150.0, third_party_rec="SELL", has_cash=True,
        mv_ladder={"light_buy": 182.0})
    assert op is None


# ── Buy-ladder strike anchoring — new-idea composer ──────────────────────

def test_new_idea_pick_prefers_light_buy_strike_in_envelope():
    from steps.new_ideas import _pick_csp_strike

    class Row:
        def __init__(self, strike, delta):
            self.strike = strike
            self.bid = 2.0
            self.ask = 2.2
            self.last = 2.1
            self.open_interest = 500
            self.delta = delta
            self.iv = 0.4

    rows = [Row(190.0, -0.34), Row(180.0, -0.26), Row(170.0, -0.18)]
    pick = _pick_csp_strike(rows, spot=200.0,
                            mv_ladder={"light_buy": 182.0})
    assert pick["strike"] == 180.0
    assert pick["mv_anchor"] == "💰 MV Light Buy $182"


def test_new_idea_pick_ignores_ladder_outside_envelope():
    """A Light Buy so deep that every ≤LB strike is outside the 0.15-0.35
    delta envelope → anchor skipped, default delta targeting preserved."""
    from steps.new_ideas import _pick_csp_strike

    class Row:
        def __init__(self, strike, delta):
            self.strike = strike
            self.bid = 2.0
            self.ask = 2.2
            self.last = 2.1
            self.open_interest = 500
            self.delta = delta
            self.iv = 0.4

    rows = [Row(190.0, -0.31), Row(180.0, -0.26), Row(140.0, -0.05)]
    pick = _pick_csp_strike(rows, spot=200.0,
                            mv_ladder={"light_buy": 145.0})
    assert "mv_anchor" not in pick
    assert pick["strike"] == 190.0  # closest to 0.30 target delta


def test_new_idea_pick_without_ladder_unchanged():
    from steps.new_ideas import _pick_csp_strike

    class Row:
        def __init__(self, strike, delta):
            self.strike = strike
            self.bid = 2.0
            self.ask = 2.2
            self.last = 2.1
            self.open_interest = 500
            self.delta = delta
            self.iv = 0.4

    rows = [Row(190.0, -0.31), Row(180.0, -0.26)]
    assert _pick_csp_strike(rows, spot=200.0)["strike"] == 190.0


# ── Step wrapper (disabled / degraded paths) ─────────────────────────────

def test_step_disabled_returns_empty():
    from steps.fetch_moneyvest import fetch_moneyvest_step
    assert fetch_moneyvest_step({}) == {}
    assert fetch_moneyvest_step({"moneyvest": {"enabled": False}}) == {}
    assert fetch_moneyvest_step(None) == {}


def test_step_enabled_calls_fetcher(monkeypatch, tmp_path):
    from steps import fetch_moneyvest as step

    class FakeMod:
        @staticmethod
        def run(**kwargs):
            assert kwargs["tickers"] == ["MU", "NVDA"]
            return dict(_PAYLOAD)

    monkeypatch.setattr(step, "_load_fetcher_module", lambda: FakeMod)
    got = step.fetch_moneyvest_step({"moneyvest": {"enabled": True}},
                                    m_score_tickers=["nvda", "MU"])
    assert got["index"]["sp500"]["label"] == "OPTIMISTIC"


def test_step_fetcher_error_is_nonfatal(monkeypatch):
    from steps import fetch_moneyvest as step

    class BoomMod:
        @staticmethod
        def run(**kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(step, "_load_fetcher_module", lambda: BoomMod)
    assert step.fetch_moneyvest_step({"moneyvest": {"enabled": True}}) == {}
