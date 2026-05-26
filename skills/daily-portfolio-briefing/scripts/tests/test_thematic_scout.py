"""Tests for the Thematic Scout market-read deepening (Market Pulse section)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps import thematic_research as tr  # noqa: E402


def _payload():
    return {
        "generated_at_iso": "2026-05-21T08:00:00",
        "summary": {"buys": 1, "csps": 1, "avoids": 1, "total": 6},
        "themes": {
            "semis": {"name": "Semis", "group": "The AI Buildout"},
            "power": {"name": "Power", "group": "The AI Buildout"},
            "quantum": {"name": "Quantum Computing", "group": "Adjacent"},
        },
        "results_by_theme": {
            "semis": [
                {"ticker": "NVDA", "spot": 172.0, "rsi_14": 72.0, "iv_rank": 65.0,
                 "sma_200": 128.0, "drawdown_pct": 2.0, "fivedayret_pct": 6.3,
                 "verdict": "WATCH"},
                {"ticker": "AMD", "spot": 150.0, "rsi_14": 40.0, "iv_rank": 55.0,
                 "sma_200": 145.0, "drawdown_pct": 12.0, "fivedayret_pct": -1.2,
                 "verdict": "BUY (pullback)", "rationale": ["pullback"]},
            ],
            "power": [
                {"ticker": "OKLO", "spot": 60.0, "rsi_14": 78.0, "iv_rank": 80.0,
                 "sma_200": 40.0, "drawdown_pct": 1.0, "fivedayret_pct": 12.0,
                 "verdict": "AVOID — overheated"},
            ],
            "quantum": [
                {"ticker": "IONQ", "spot": 40.0, "rsi_14": 29.0, "iv_rank": 70.0,
                 "sma_200": 45.0, "drawdown_pct": 35.0, "fivedayret_pct": -8.0,
                 "verdict": "CSP ENTRY (fat premium)", "rationale": ["fat premium"]},
                {"ticker": "RGTI", "spot": None, "rsi_14": None, "verdict": "NO DATA"},
            ],
        },
    }


def test_market_pulse_present_and_titled():
    out = "\n".join(tr.render_scout_section(_payload(), config={}))
    assert "Market Read Across Themes" in out
    assert "### 📊 Market Pulse" in out
    assert "### 🎯 Actionable shortlist" in out


def test_include_shortlist_false_omits_actionable():
    # The daily briefing carries a separate Candidate Trades section, so it asks
    # the scout for the Market Pulse only.
    out = "\n".join(tr.render_scout_section(_payload(), config={}, include_shortlist=False))
    assert "### 📊 Market Pulse" in out
    assert "Actionable shortlist" not in out


def test_market_pulse_breadth_and_themes():
    md = "\n".join(tr._render_market_pulse(
        _payload()["results_by_theme"], _payload()["themes"]))
    # 4 live names (RGTI is NO DATA → excluded), 2 of 4 green (NVDA, OKLO)
    assert "2/4" in md
    # Hottest theme is Power (+12 avg), coldest Quantum (-8 avg)
    assert "Hottest theme: **Power**" in md
    assert "Coldest: **Quantum Computing**" in md


def test_hottest_and_cooling_lists():
    md = "\n".join(tr._render_market_pulse(
        _payload()["results_by_theme"], _payload()["themes"]))
    assert "🔥 Hottest names" in md
    assert "🧊 Cooling / pulling back" in md
    # OKLO is the top mover and should be tagged Extended (overbought + near highs)
    assert "`OKLO`" in md
    assert "Extended" in md
    # IONQ is the deepest decliner — Oversold
    assert "`IONQ`" in md
    assert "Oversold" in md


def test_no_data_ticker_excluded_from_pulse():
    md = "\n".join(tr._render_market_pulse(
        _payload()["results_by_theme"], _payload()["themes"]))
    assert "RGTI" not in md  # NO DATA names never appear in the market read


def test_setup_label_buckets():
    assert tr._setup_label({"rsi_14": 78, "drawdown_pct": 1, "fivedayret_pct": 12,
                            "sma_200": 40, "spot": 60}) == "Extended"
    assert tr._setup_label({"rsi_14": 29, "drawdown_pct": 35, "fivedayret_pct": -8,
                            "sma_200": 45, "spot": 40}) == "Oversold"
    assert tr._setup_label({"rsi_14": 58, "drawdown_pct": 6, "fivedayret_pct": 2.5,
                            "sma_200": 250, "spot": 280}) == "Trending up"


def test_action_read_maps_state_to_verdict():
    # Overbought → no new entry, write calls / trim if held
    ob = tr._action_read({"rsi_14": 76, "iv_rank": 90, "drawdown_pct": 2})
    assert "no new buy/CSP" in ob and ("WRITE COVERED CALLS" in ob or "TRIM" in ob)
    # Pullback zone → CSP/BUY entry favoured
    pb = tr._action_read({"rsi_14": 40, "iv_rank": 50, "drawdown_pct": 12})
    assert "entry" in pb.lower() and "CSP/BUY" in pb
    # Falling knife (low drawdown so the thesis-check branch doesn't pre-empt it)
    fk = tr._action_read({"rsi_14": 20, "drawdown_pct": 10})
    assert "falling knife" in fk.lower()
    # Deep drawdown without strength → thesis check / exit
    th = tr._action_read({"rsi_14": 38, "drawdown_pct": 45})
    assert "thesis check" in th


def test_action_tag_compact():
    assert tr._action_tag(76) == "calls/trim"
    assert tr._action_tag(40) == "entry zone"
    assert tr._action_tag(None) == "verify"


def test_market_pulse_names_carry_action_line():
    md = "\n".join(tr._render_market_pulse(
        _payload()["results_by_theme"], _payload()["themes"]))
    assert "🎬 **Action:**" in md
    # every hottest/cooling 🔥/🧊 name is followed by an action line
    name_lines = [l for l in md.splitlines() if l.startswith("- 🔥") or l.startswith("- 🧊")]
    action_lines = [l for l in md.splitlines() if "🎬 **Action:**" in l]
    assert len(action_lines) >= len(name_lines) > 0


def test_empty_payload_returns_empty():
    assert tr.render_scout_section(None) == []
    assert tr._render_market_pulse({}, {}) == []


def test_multi_theme_ticker_deduped_in_aggregate():
    # ARM anchors two themes; it must appear ONCE in the cross-theme aggregate
    # (hottest list + breadth count), but still lead each of its themes.
    arm = {"ticker": "ARM", "spot": 301.0, "rsi_14": 76, "iv_rank": 100,
           "sma_200": 147.0, "drawdown_pct": 1.0, "fivedayret_pct": 44.1,
           "verdict": "AVOID — overheated"}
    payload_results = {
        "semis": [dict(arm)],
        "applications": [dict(arm)],
    }
    meta = {"semis": {"name": "Semis", "group": "AI"},
            "applications": {"name": "Applications", "group": "AI"}}
    # _live_results dedupes by ticker
    assert len(tr._live_results(payload_results)) == 1
    md = "\n".join(tr._render_market_pulse(payload_results, meta))
    # ARM appears once in the hottest list, not twice
    assert md.count("🔥 `ARM`") == 1
    # breadth counts it once
    assert "1/1" in md
    # but the per-theme pulse lists it under BOTH themes as leader
    assert md.count("leader `ARM`") == 2
