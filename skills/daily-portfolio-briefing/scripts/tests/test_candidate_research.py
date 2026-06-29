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


def test_independent_setup_renders_with_warning_badge():
    """CLAUDE.md hard rule #25: a 'CSP ENTRY (independent setup)' verdict
    surfaces as a CANDIDATE with full live ticket, but carries a
    distinctive '⚠ no third-party rec — verify independently' badge so
    the user knows to validate the catalyst against their other sources."""
    payload = {
        "themes": {"power": {"name": "Power", "group": "AI Buildout",
                              "anchors": ["AAOI"], "etfs": []}},
        "results_by_theme": {
            "power": [
                {"ticker": "AAOI", "spot": 174.0, "rsi_14": 50, "iv_rank": 76,
                 "sma_200": 75.0, "drawdown_pct": 22, "fivedayret_pct": 7.0,
                 "verdict": "CSP ENTRY (independent setup)",
                 "rationale": ["RSI 50 in pullback band + IV rank 76 elevated"
                               " (no third-party rec — verify catalyst independently)"],
                 "csp_entry": {"strike": 160, "mid": 3.2, "bid": 3.0, "ask": 3.4,
                               "expiration": "2026-07-24", "dte": 38}},
            ]
        },
    }
    md = cr.render_candidate_report(payload, fv_by_ticker={}, config={},
                                    generated_at="T")
    aaoi = [b for b in md.split("\n\n") if "`AAOI`" in b][0]
    assert "🎯 CANDIDATE" in aaoi
    assert "⚠ no third-party rec" in aaoi  # the distinctive badge
    assert "$160P" in aaoi                    # full live ticket still shown
    assert "Live E*TRADE chain" in aaoi


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


def test_long_puts_by_ticker_tallies_long_puts_only():
    positions = [
        {"assetType": "OPTION", "type": "PUT", "underlying": "META", "strike": 570.0, "qty": 1.0},   # long
        {"assetType": "OPTION", "type": "PUT", "underlying": "META", "strike": 530.0, "qty": -1.0},  # short → ignore
        {"assetType": "OPTION", "type": "CALL", "underlying": "META", "strike": 725.0, "qty": -1.0}, # call → ignore
    ]
    m = cr.long_puts_by_ticker(positions)
    assert m["META"]["count"] == 1
    assert m["META"]["strikes"] == [570.0]


def test_candidate_briefing_blocks_short_put_that_cancels_protection():
    """The META bug: user holds long $570P as a collar floor; a CSP candidate
    at the same strike would cancel the hedge. Must be suppressed with a clear
    'this cancels your protection' note, not just listed as a 'stack.'"""
    payload = {
        "themes": {"applications": {"name": "Applications", "anchors": ["META"]}},
        "results_by_theme": {
            "applications": [{
                "ticker": "META", "spot": 610.0, "rsi_14": 55, "iv_rank": 56,
                "sma_200": 540, "drawdown_pct": 18, "fivedayret_pct": -1.0,
                "verdict": "CSP ENTRY (fat premium)", "rationale": ["fat premium"],
                "csp_entry": {"strike": 570, "mid": 18.38, "bid": 18.10, "ask": 18.65,
                              "expiration": "2026-08-21", "dte": 85},
            }],
        },
    }
    elp = {"META": {"count": 1, "strikes": [570.0]}}  # held LONG $570P (collar floor)
    md = cr.render_candidate_briefing(payload, fv_by_ticker={}, config={},
                                      generated_at="T", existing_long_puts=elp)
    # META does NOT appear as an actionable candidate
    assert "🎯 Today's Candidates (0)" in md or "🎯 Today's Candidates (1)" not in md.split("META")[0]
    # The cancellation is surfaced with the protective-put language
    assert "cancel" in md.lower()
    assert "protection" in md.lower() or "collar floor" in md.lower()
    # The protective-put icon distinguishes it from a plain duplicate
    assert "🛡️" in md
    assert "LONG $570P" in md


def test_short_puts_by_ticker_tallies_short_puts_only():
    positions = [
        {"assetType": "OPTION", "type": "PUT", "underlying": "AMD", "strike": 135.0, "qty": -1.0},
        {"assetType": "OPTION", "type": "PUT", "underlying": "AMD", "strike": 120.0, "qty": -2.0},
        {"assetType": "OPTION", "type": "CALL", "underlying": "AMD", "strike": 200.0, "qty": -1.0},  # call → ignore
        {"assetType": "OPTION", "type": "PUT", "underlying": "NVDA", "strike": 190.0, "qty": 1.0},   # long put → ignore
        {"assetType": "EQUITY", "symbol": "AMD", "qty": 100},                                         # equity → ignore
    ]
    m = cr.short_puts_by_ticker(positions)
    assert m["AMD"]["count"] == 3                      # 1 + 2 contracts
    assert sorted(m["AMD"]["strikes"]) == [120.0, 135.0]
    assert "NVDA" not in m                             # long put not counted


def test_candidate_briefing_suppresses_held_put_duplicate():
    """The LITE-bug fix: a CSP candidate whose strike matches a put the user
    already holds is pulled out of the actionable list into 'Already positioned'."""
    esp = {"AMD": {"count": 1, "strikes": [135.0]}}   # exact match to AMD's $135 candidate
    md = cr.render_candidate_briefing(_payload(), fv_by_ticker=_FV, config={},
                                      generated_at="T", existing_short_puts=esp)
    # AMD drops out of candidates (only INTC's BUY remains)
    assert "🎯 Today's Candidates (1)" in md
    assert "Already positioned" in md
    already = md.split("Already positioned")[1]
    assert "`AMD`" in already and "already hold" in already
    # The AMD CSP entry ticket is NOT presented as a fresh actionable trade
    assert "Entry (CSP):" not in md.split("Already positioned")[0].split("Today's Candidates")[1]


def test_candidate_briefing_annotates_same_name_different_strike():
    """A candidate at a DIFFERENT strike on a name you already hold a put on stays
    actionable but is flagged as stacking."""
    esp = {"AMD": {"count": 1, "strikes": [110.0]}}   # 110 vs 135 candidate → >5% apart
    md = cr.render_candidate_briefing(_payload(), fv_by_ticker=_FV, config={},
                                      generated_at="T", existing_short_puts=esp)
    assert "🎯 Today's Candidates (2)" in md           # AMD still a candidate
    assert "You already hold 1× AMD PUT" in md          # bold markdown sits between PUT and strike
    assert "$110" in md
    assert "stack single-name assignment risk" in md


def test_candidate_briefing_no_positions_unchanged():
    """With no existing puts passed, behavior is exactly as before."""
    md = cr.render_candidate_briefing(_payload(), fv_by_ticker=_FV, config={}, generated_at="T")
    assert "🎯 Today's Candidates (2)" in md
    assert "Already positioned" not in md


def test_briefing_candidate_tickers_small_set():
    tks = cr.briefing_candidate_tickers(_payload(), config={})
    assert "AMD" in tks and "INTC" in tks and "NVDA" in tks   # candidates + held
    assert "TSM" not in tks and "SMCI" not in tks              # watch / avoid excluded
    assert "IGV" not in tks                                    # ETF excluded


def test_single_stock_tickers_excludes_etfs():
    tks = cr.single_stock_tickers(_payload(), config={})
    assert "AMD" in tks and "NVDA" in tks
    assert "IGV" not in tks   # ETF excluded from FV fetch set


# ─── Conviction Level badges (CLAUDE.md hard rule #26) ──────────────────────

def _conv_payload(rating_tier, conviction):
    return {
        "themes": {"core": {"name": "Core", "group": "AI Buildout",
                             "anchors": ["X"], "etfs": []}},
        "results_by_theme": {
            "core": [{
                "ticker": "X", "spot": 100.0, "rsi_14": 45, "iv_rank": 60,
                "sma_200": 95, "drawdown_pct": 15, "fivedayret_pct": -1.0,
                "verdict": "CSP ENTRY (fat premium)", "rationale": ["fat premium"],
                "rating_tier": rating_tier,
                "conviction": conviction,
                "csp_entry": {"strike": 90, "mid": 1.5, "bid": 1.4, "ask": 1.6,
                              "expiration": "2026-07-24", "dte": 38},
            }]
        },
    }


def test_candidate_badge_tier4_high_conviction_shows_trophy():
    md = cr.render_candidate_report(_conv_payload(4, "High"), fv_by_ticker={},
                                    config={}, generated_at="T")
    card = [b for b in md.split("\n\n") if "`X`" in b][0]
    assert "🏆 TOP CONVICTION" in card


def test_candidate_badge_tier4_low_conviction_shows_warning():
    md = cr.render_candidate_report(_conv_payload(4, "Low"), fv_by_ticker={},
                                    config={}, generated_at="T")
    card = [b for b in md.split("\n\n") if "`X`" in b][0]
    assert "low-conviction" in card
    assert "🏆" not in card  # no top promotion when conviction is low


def test_candidate_badge_tier3_high_conviction_shows_flame():
    md = cr.render_candidate_report(_conv_payload(3, "High"), fv_by_ticker={},
                                    config={}, generated_at="T")
    card = [b for b in md.split("\n\n") if "`X`" in b][0]
    assert "🔥 high conviction" in card


def test_candidate_badge_tier3_low_conviction_shows_trial():
    md = cr.render_candidate_report(_conv_payload(3, "Low"), fv_by_ticker={},
                                    config={}, generated_at="T")
    card = [b for b in md.split("\n\n") if "`X`" in b][0]
    assert "low conviction" in card.lower()
    assert "trial size" in card.lower()


def test_candidate_no_conviction_renders_unchanged():
    """Conviction=None → no chip added (legacy behavior preserved)."""
    md = cr.render_candidate_report(_conv_payload(3, None), fv_by_ticker={},
                                    config={}, generated_at="T")
    card = [b for b in md.split("\n\n") if "`X`" in b][0]
    assert "🏆" not in card and "🔥" not in card
    assert "trial" not in card.lower()
