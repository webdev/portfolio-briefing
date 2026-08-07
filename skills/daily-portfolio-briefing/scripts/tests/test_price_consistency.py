"""Regression tests — 2026-08-07 briefing BUG A: one ticker, three prices.

Observed (briefing_full_2026-08-07.md): TEAM rendered THREE spot prices —

  "**🎯 CANDIDATE · `TEAM` · $109.73**"                       (scout cache, Aug 6 pre-earnings)
  "### TEAM — $110.17 · 🔄 Recovery · 🚀 Bull momentum"        (technicals OHLC close)
  "BUY ~$5,000 of TEAM (~34 shares @ ~$144.41)"               (live E*TRADE quote, +31% gap)

Ground truth: the live quote was CORRECT (the same-cycle E*TRADE chain showed
TEAM's ~0.50-delta strike at $145) — TEAM gapped +31% post-earnings on Aug 7.
The stale surface was the 24h scout cache, whose pre-gap "$99P · _Live E*TRADE
chain_" ticket rendered as actionable. Fixes under test:

  - every candidate surface re-prices from the ONE canonical resolved quote
    (analysis.price_consistency.resolve_spot);
  - a >5% drift fail-closes the cached chain ticket (rule #10);
  - a render-time price-consistency verifier flags any ticker whose rendered
    spots disagree >2% (conservative patterns — strikes/targets/FV exempt).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import price_consistency as pc  # noqa: E402
from steps import candidate_research as cr  # noqa: E402
from steps.technical_read import _fmt_card  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# resolve_spot — the ONE canonical quote
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_spot_prefers_live_quote():
    quotes = {"TEAM": {"last": 144.41, "previousClose": 110.17}}
    assert pc.resolve_spot("TEAM", quotes) == 144.41


def test_resolve_spot_falls_back_to_broker_positions():
    positions = [{"assetType": "EQUITY", "symbol": "TEAM", "price": 143.9}]
    assert pc.resolve_spot("TEAM", {}, positions) == 143.9


def test_resolve_spot_fails_closed_with_no_source():
    assert pc.resolve_spot("TEAM", {}, []) is None
    assert pc.resolve_spot("TEAM", {"TEAM": {"last": 0}}, []) is None


# ─────────────────────────────────────────────────────────────────────────────
# (a) candidate surface prices from the canonical resolved spot
# ─────────────────────────────────────────────────────────────────────────────

def _team_payload():
    return {
        "themes": {"applications": {"name": "Applications", "group": "AI",
                                    "anchors": [], "etfs": []}},
        "results_by_theme": {"applications": [{
            "ticker": "TEAM", "spot": 109.73, "rsi_14": 42, "iv_rank": 83,
            "sma_200": 108.0, "drawdown_pct": 37.7, "fivedayret_pct": 5.2,
            "verdict": "CSP ENTRY (independent setup)", "rationale": [],
            "csp_entry": {"strike": 99, "mid": 6.25, "bid": 4.60, "ask": 7.90,
                          "expiration": "2026-09-11", "dte": 36},
        }]},
    }


def test_candidate_header_reprices_at_live_quote_and_ticket_fails_closed():
    """Observed: '**🎯 CANDIDATE · `TEAM` · $109.73**' with '⏸ **Deferred
    (capacity gated)** · SELL 1× TEAM $99P ... · _Live E*TRADE chain_' while
    the live quote was $144.41 (+31.6% vs the scout cache). The header must
    show the canonical live spot (the same price the LT ADD sizing row
    'BUY ~$5,000 of TEAM (~34 shares @ ~$144.41)' used) and the pre-move
    chain ticket must fail closed — never render as a live actionable quote."""
    snapshot = {"quotes": {"TEAM": {"last": 144.41, "previousClose": 110.17,
                                    "source": "etrade"}},
                "positions": []}
    md = cr.render_candidate_briefing(
        _team_payload(), fv_by_ticker={}, config={}, generated_at="T",
        snapshot_data=snapshot)
    assert "$144.41" in md                       # canonical resolved spot
    assert "· $109.73**" not in md               # stale scout-cache header price
    assert "Chain quote stale — no actionable ticket (fail closed)" in md
    assert "SELL 1× TEAM $99P" not in md
    assert "_Live E*TRADE chain_" not in md


def test_candidate_small_drift_keeps_ticket_with_verify_note():
    """Drift in the 2-5% window: header re-prices, the ticket stays but
    carries a measured verify note (not fail-closed)."""
    snapshot = {"quotes": {"TEAM": {"last": 113.0}}, "positions": []}  # +3.0%
    md = cr.render_candidate_briefing(
        _team_payload(), fv_by_ticker={}, config={}, generated_at="T",
        snapshot_data=snapshot)
    assert "· $113.00**" in md
    assert "SELL 1× TEAM $99P" in md
    assert "since chain fetch — verify quote" in md


def test_candidate_in_tolerance_unchanged():
    """No drift → byte-identical legacy card (no notes, scout spot kept)."""
    snapshot = {"quotes": {"TEAM": {"last": 109.9}}, "positions": []}  # +0.2%
    md = cr.render_candidate_briefing(
        _team_payload(), fv_by_ticker={}, config={}, generated_at="T",
        snapshot_data=snapshot)
    assert "· $109.73**" in md
    assert "SELL 1× TEAM $99P" in md
    assert "Chain quote stale" not in md
    assert "verify quote" not in md


def test_candidate_no_quote_fails_open_to_scout_spot():
    md = cr.render_candidate_briefing(
        _team_payload(), fv_by_ticker={}, config={}, generated_at="T",
        snapshot_data={"quotes": {}, "positions": []})
    assert "· $109.73**" in md
    assert "SELL 1× TEAM $99P" in md


# ─────────────────────────────────────────────────────────────────────────────
# Technical Read header — live spot headline
# ─────────────────────────────────────────────────────────────────────────────

def test_technical_read_header_headlines_live_spot_on_gap():
    """Observed: '### TEAM — $110.17 · 🔄 Recovery · 🚀 Bull momentum' while
    the live quote was $144.41. The header must headline the live price and
    keep the close visible only as the labelled indicator reference."""
    deep = {"spot": 110.17, "short_term_verdict": "bull-momentum",
            "long_term_verdict": "recovery", "rsi_14": 63.0,
            "bb_position_pct": 90.0, "macd_hist": 1.0, "macd_hist_5d_ago": 0.5,
            "atr_pct": 3.0, "sma_50_slope_pct": 0.5, "sma_200_slope_pct": 0.1,
            "cross": "golden", "vs_sma200_pct": 1.0, "yr_position_pct": 60.0,
            "ath_dd_pct": -37.7, "ret_1w_pct": 5.0, "ret_1m_pct": 8.0,
            "ret_3m_pct": 12.0}
    lines = _fmt_card("TEAM", deep, None, live_spot=144.41)
    assert lines[0].startswith("### TEAM — $144.41 (live · indicators from $110.17 close)")


def test_technical_read_header_unchanged_in_tolerance():
    deep = {"spot": 110.17, "short_term_verdict": "neutral",
            "long_term_verdict": "sideways", "rsi_14": 50.0,
            "bb_position_pct": 50.0, "macd_hist": 0.0, "macd_hist_5d_ago": 0.0,
            "atr_pct": 2.0, "cross": "golden", "vs_sma200_pct": 0.0,
            "yr_position_pct": 50.0, "ath_dd_pct": -5.0,
            "ret_1w_pct": 0.0, "ret_1m_pct": 0.0, "ret_3m_pct": 0.0}
    lines = _fmt_card("TEAM", deep, None, live_spot=110.5)   # +0.3%
    assert lines[0].startswith("### TEAM — $110.17 ·")
    assert "(live" not in lines[0]


# ─────────────────────────────────────────────────────────────────────────────
# (b) render-time price-consistency verifier
# ─────────────────────────────────────────────────────────────────────────────

_DIRTY_MD = """\
**🎯 CANDIDATE · `TEAM` · $109.73** 🟡 low conviction — trial size

### TEAM — $110.17 · 🔄 Recovery · 🚀 Bull momentum

- LT ADD TEAM — BUY ~$5,000 of TEAM (~34 shares @ ~$144.41) — net cash −$5,000
"""

_CLEAN_MD = """\
**🎯 CANDIDATE · `TEAM` · $144.41** 🟡 low conviction — trial size

### TEAM — $144.41 (live · indicators from $110.17 close) · 🔄 Recovery

- LT ADD TEAM — BUY ~$5,000 of TEAM (~34 shares @ ~$144.41) — net cash −$5,000
  - ⏸ **Deferred (capacity gated)** · SELL 1× TEAM $99P exp **Fri Sep 11 '26** (36 DTE) · mid $6.25
  - 💵 FV: DCF $80 · analyst PT $150
"""


def test_verifier_flags_the_observed_team_three_price_split():
    offenders = pc.price_disagreements(_DIRTY_MD)
    assert len(offenders) == 1
    o = offenders[0]
    assert o["ticker"] == "TEAM"
    assert o["min"] == 109.73 and o["max"] == 144.41
    assert o["spread_pct"] > 30
    panel = pc.render_panel(offenders)
    assert any("Price Consistency Check" in ln for ln in panel)
    assert any("TEAM" in ln for ln in panel)


def test_verifier_silent_on_clean_briefing_with_strikes_and_fv():
    """Strikes ($99P), FV notes and the parenthesized indicator reference must
    never count as spot mentions — a consistent briefing stays silent."""
    assert pc.price_disagreements(_CLEAN_MD) == []
    assert pc.render_panel([]) == []


def test_verifier_ignores_sub_2pct_spread():
    md = ("**🎯 CANDIDATE · `MELI` · $1,814.91**\n"
          "### MELI — $1,830.00 · 📊 Uptrend intact\n")   # -0.8% spread
    assert pc.price_disagreements(md) == []


def test_verifier_needs_two_mentions_to_flag():
    assert pc.price_disagreements("### TEAM — $110.17 · 🔄 Recovery\n") == []
