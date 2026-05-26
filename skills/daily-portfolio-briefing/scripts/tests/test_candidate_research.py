"""Tests for the per-company Candidate Research report."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps import candidate_research as cr  # noqa: E402


def _payload():
    return {
        "themes": {"semis": {"name": "Semis", "group": "The AI Buildout",
                             "anchors": ["NVDA", "AMD"], "etfs": ["SMH"]}},
        "results_by_theme": {
            "semis": [
                # CSP candidate, RSI favorable (pullback), has live chain ticket
                {"ticker": "AMD", "spot": 150.0, "rsi_14": 40, "iv_rank": 60,
                 "sma_200": 140, "drawdown_pct": 12, "fivedayret_pct": -1.2,
                 "verdict": "CSP ENTRY (fat premium)", "rationale": ["fat premium"],
                 "csp_entry": {"strike": 135, "mid": 2.1, "bid": 2.0, "ask": 2.2,
                               "expiration": "2026-06-19", "dte": 29}},
                # BUY candidate, RSI favorable
                {"ticker": "INTC", "spot": 24.0, "rsi_14": 45, "iv_rank": 40,
                 "sma_200": 26, "drawdown_pct": 20, "fivedayret_pct": -2.0,
                 "verdict": "BUY (pullback)", "rationale": ["drawdown + BUY"]},
                # CSP but RSI overbought → held by RSI
                {"ticker": "NVDA", "spot": 217.0, "rsi_14": 78, "iv_rank": 80,
                 "sma_200": 180, "drawdown_pct": 2, "fivedayret_pct": 6.0,
                 "verdict": "CSP ENTRY (fat premium)", "rationale": ["fat premium"],
                 "csp_entry": {"strike": 195, "mid": 4.0, "bid": 3.9, "ask": 4.1,
                               "expiration": "2026-06-19", "dte": 29}},
                # WATCH
                {"ticker": "TSM", "spot": 200.0, "rsi_14": 55, "iv_rank": 45,
                 "sma_200": 180, "drawdown_pct": 8, "fivedayret_pct": 1.0,
                 "verdict": "WATCH", "rationale": ["no catalyst"]},
                # AVOID
                {"ticker": "SMCI", "spot": 30.0, "rsi_14": 30, "iv_rank": 90,
                 "sma_200": 45, "drawdown_pct": 45, "fivedayret_pct": -8.0,
                 "verdict": "AVOID", "rationale": ["drawdown 45%"]},
                # ETF anchor (should still render, valuation = basket)
                {"ticker": "IGV", "spot": 100.0, "rsi_14": 60, "iv_rank": 30,
                 "sma_200": 90, "drawdown_pct": 5, "fivedayret_pct": 2.0,
                 "verdict": "WATCH"},
            ]
        },
    }


_FV = {"AMD": {"dcf": 120.0, "analyst_target": 160.0, "num_analysts": 42},
       "NVDA": {"dcf": 241.0, "analyst_target": 308.0, "num_analysts": 35}}


def test_report_renders_with_summary():
    md = cr.render_candidate_report(_payload(), fv_by_ticker=_FV, config={},
                                    generated_at="Test")
    assert "# Candidate Research — Test" in md
    assert "companies analyzed:" in md
    assert "🎯 CANDIDATE" in md and "👀 WATCH" in md and "🔴 AVOID" in md


def test_csp_candidate_has_entry_and_valuation():
    md = cr.render_candidate_report(_payload(), fv_by_ticker=_FV, config={},
                                    generated_at="T")
    amd = [b for b in md.split("\n\n") if "`AMD`" in b][0]
    assert "🎯 CANDIDATE" in amd
    assert "Entry (CSP):" in amd and "$135P" in amd
    assert "DCF $120" in amd and "analyst PT $160" in amd
    assert "✅ RSI favourable" in amd


def test_buy_candidate_has_equity_entry():
    md = cr.render_candidate_report(_payload(), fv_by_ticker={}, config={}, generated_at="T")
    intc = [b for b in md.split("\n\n") if "`INTC`" in b][0]
    assert "🎯 CANDIDATE" in intc
    assert "Entry (equity):" in intc and "BUY `INTC`" in intc


def test_overbought_csp_held_by_rsi_no_entry():
    md = cr.render_candidate_report(_payload(), fv_by_ticker=_FV, config={}, generated_at="T")
    nvda = [b for b in md.split("\n\n") if "`NVDA`" in b][0]
    assert "⏸ HELD (RSI)" in nvda
    assert "Held back by RSI" in nvda
    assert "Entry (CSP)" not in nvda          # no actionable entry when blocked


def test_watch_and_avoid_have_cards_no_entry():
    md = cr.render_candidate_report(_payload(), fv_by_ticker={}, config={}, generated_at="T")
    tsm = [b for b in md.split("\n\n") if "`TSM`" in b][0]
    smci = [b for b in md.split("\n\n") if "`SMCI`" in b][0]
    assert "👀 WATCH" in tsm and "Entry" not in tsm
    assert "🔴 AVOID" in smci and "Entry" not in smci


def test_etf_anchor_marked_basket():
    md = cr.render_candidate_report(_payload(), fv_by_ticker={}, config={}, generated_at="T")
    igv = [b for b in md.split("\n\n") if "`IGV`" in b][0]
    assert "n/a — basket (ETF)" in igv


def test_candidate_briefing_lists_candidates_and_on_deck():
    md = cr.render_candidate_briefing(_payload(), fv_by_ticker=_FV, config={}, generated_at="T")
    assert "# Candidate Trade Briefing — T" in md
    assert "🎯 Today's Candidates (2)" in md     # AMD (CSP) + INTC (BUY) qualify
    assert "Entry (CSP):" in md and "$135P" in md
    assert "Market context:" in md
    # NVDA's CSP is RSI-blocked → on-deck, not a candidate
    assert "On Deck" in md
    on_deck = md.split("On Deck")[1]
    assert "`NVDA`" in on_deck


def test_candidate_briefing_dedupes_multi_theme_ticker():
    # A ticker in two themes (held by RSI in both) appears once in the flat briefing.
    R = cr  # alias
    blocked = {"ticker": "QCOM", "spot": 200.0, "rsi_14": 72, "iv_rank": 80,
               "sma_200": 150, "drawdown_pct": 5, "fivedayret_pct": 4.0,
               "verdict": "CSP ENTRY (fat premium)", "rationale": ["fat premium"],
               "csp_entry": {"strike": 180, "mid": 2.0, "expiration": "2026-06-19", "dte": 29}}
    payload = {
        "themes": {"semis": {"name": "Semis"}, "applications": {"name": "Applications"}},
        "results_by_theme": {"semis": [dict(blocked)], "applications": [dict(blocked)]},
    }
    md = R.render_candidate_briefing(payload, fv_by_ticker={}, config={}, generated_at="T")
    assert md.count("`QCOM`") == 1


def test_briefing_as_section_uses_demoted_headers():
    md = cr.render_candidate_briefing(_payload(), fv_by_ticker=_FV, config={},
                                      generated_at="T", as_section=True)
    assert md.startswith("## 🎯 Candidate Trades — Across Themes")
    assert "### 🎯 Today's Candidates" in md
    assert "# Candidate Trade Briefing" not in md  # no standalone H1 when embedded


def test_briefing_candidate_tickers_small_set():
    tks = cr.briefing_candidate_tickers(_payload(), config={})
    assert "AMD" in tks and "INTC" in tks and "NVDA" in tks   # candidates + held
    assert "TSM" not in tks and "SMCI" not in tks              # watch / avoid excluded
    assert "IGV" not in tks                                    # ETF excluded


def test_single_stock_tickers_excludes_etfs():
    tks = cr.single_stock_tickers(_payload(), config={})
    assert "AMD" in tks and "NVDA" in tks
    assert "IGV" not in tks   # ETF excluded from FV fetch set
