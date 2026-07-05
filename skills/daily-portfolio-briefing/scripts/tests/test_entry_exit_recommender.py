"""Tests for analysis/entry_exit_recommender.py — every recommendation type,
every discipline gate, every override (TDD hard rule #33).

Fixtures are explicit field dicts (the ``deep`` to_dict() shape) so each test
pins exactly the signals that drive the call.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import entry_exit_recommender as eer  # noqa: E402


def _tech(**kw) -> dict:
    """A neutral, healthy deep snapshot; override fields per test."""
    base = dict(
        ticker="TEST",
        spot=100.0,
        bars=490,
        rsi_14=50.0,
        bb_upper=105.0, bb_mid=100.0, bb_lower=95.0,
        bb_position_pct=50.0, bb_width_pct=10.0,
        macd=0.5, macd_signal=0.4, macd_hist=0.1, macd_hist_5d_ago=0.05,
        atr_14=2.0, atr_pct=2.0,
        sma_20=99.0, sma_50=97.0, sma_200=92.0,
        sma_50_slope_pct=0.8, sma_200_slope_pct=1.2,
        vs_sma20_pct=1.0, vs_sma50_pct=3.1, vs_sma200_pct=8.7,
        cross="golden",
        ath=110.0, ath_dd_pct=-9.1, yr_hi=110.0, yr_lo=80.0, yr_position_pct=67,
        ret_1w_pct=1.0, ret_1m_pct=3.0, ret_3m_pct=8.0, ret_1y_pct=20.0,
        vol_ratio_30d=1.0,
        short_term_verdict="neutral",
        long_term_verdict="uptrend",
    )
    base.update(kw)
    return base


def _sr(spot=100.0, support_pct_below=2.0, resistance_pct_above=5.0,
        s_touches=4, r_touches=3) -> dict:
    return {
        "spot": spot,
        "supports": [{"price": round(spot * (1 - support_pct_below / 100), 2),
                      "touches": s_touches, "strength": 3.0}],
        "resistances": [{"price": round(spot * (1 + resistance_pct_above / 100), 2),
                         "touches": r_touches, "strength": 2.0}],
        "note": None,
    }


def _entry_tech(**kw) -> dict:
    """RSI 34, MACD hist turning up, LT uptrend — ENTRY_STRONG-qualifying
    when paired with a support within 3%."""
    base = _tech(rsi_14=34.0, macd_hist=-0.2, macd_hist_5d_ago=-0.6,
                 long_term_verdict="uptrend")
    base.update(kw)
    return base


def _broken_tech(**kw) -> dict:
    """Below 200-SMA, death cross, falling negative MACD hist."""
    base = _tech(rsi_14=38.0, cross="death", vs_sma200_pct=-12.3,
                 macd_hist=-1.2, macd_hist_5d_ago=-0.8,
                 sma_200_slope_pct=-1.0, long_term_verdict="downtrend",
                 short_term_verdict="bear-momentum", ath_dd_pct=-30.0)
    base.update(kw)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Recommendation types
# ─────────────────────────────────────────────────────────────────────────────

def test_entry_strong_happy_path():
    rec = eer.recommend("AAOI", _entry_tech(), "none", {}, sr=_sr(), gate_open=True)
    assert rec.call == eer.ENTRY_STRONG
    # every reason cites a real number (hard rule #19)
    joined = " ".join(rec.reasons)
    assert "RSI 34" in joined
    assert "at support $98" in joined
    assert "-0.20" in joined and "-0.60" in joined  # macd hist now vs 5d ago
    assert "200-SMA" in joined


def test_entry_strong_requires_support():
    """Same signals but no support nearby → not ENTRY_STRONG."""
    rec = eer.recommend("AAOI", _entry_tech(), "none", {},
                        sr=_sr(support_pct_below=8.0), gate_open=True)
    assert rec.call != eer.ENTRY_STRONG


def test_entry_strong_requires_lt_uptrend():
    rec = eer.recommend("AAOI", _entry_tech(long_term_verdict="sideways"),
                        "none", {}, sr=_sr(), gate_open=True)
    assert rec.call != eer.ENTRY_STRONG


def test_entry_watch_setup_forming():
    """RSI in band + LT intact + hist rising, but not at strong support."""
    rec = eer.recommend("VRT", _tech(rsi_14=42.0, macd_hist=0.3, macd_hist_5d_ago=0.1),
                        "none", {}, sr=None, gate_open=True)
    assert rec.call == eer.ENTRY_WATCH
    assert any("RSI 42" in r for r in rec.reasons)


def test_trim_stretched_long_position():
    rec = eer.recommend(
        "PLTR",
        _tech(rsi_14=74.0, bb_position_pct=92.0, short_term_verdict="stretched-pullback-risk"),
        "long_shares", {}, sr=_sr(resistance_pct_above=1.5), gate_open=True,
    )
    assert rec.call == eer.TRIM
    joined = " ".join(rec.reasons)
    assert "RSI 74" in joined
    assert "92%" in joined
    assert "at resistance" in joined


def test_trim_needs_long_shares():
    """Stretched but not held → no TRIM (falls through to HOLD)."""
    rec = eer.recommend("PLTR", _tech(rsi_14=74.0, bb_position_pct=92.0),
                        "none", {}, gate_open=True)
    assert rec.call == eer.HOLD


def test_exit_urgent_broken_long():
    rec = eer.recommend("XYZ", _broken_tech(), "long_shares", {}, gate_open=True)
    assert rec.call == eer.EXIT_URGENT
    joined = " ".join(rec.reasons)
    assert "-12.3% vs 200-SMA" in joined
    assert "Death cross" in joined
    assert "-1.20" in joined and "-0.80" in joined


def test_avoid_broken_candidate_not_held():
    rec = eer.recommend("XYZ", _broken_tech(), "none", {}, gate_open=True)
    assert rec.call == eer.AVOID


def test_hold_neutral_position():
    rec = eer.recommend("MSFT", _tech(rsi_14=58.0, macd_hist=0.1, macd_hist_5d_ago=0.2),
                        "long_shares", {}, gate_open=True)
    assert rec.call == eer.HOLD
    assert any("RSI 58" in r for r in rec.reasons)


def test_missing_tech_returns_none():
    assert eer.recommend("GONE", None, "long_shares", {}) is None


# ─────────────────────────────────────────────────────────────────────────────
# Discipline gates
# ─────────────────────────────────────────────────────────────────────────────

def test_capacity_gate_downgrades_entry_strong_to_watch():
    rec = eer.recommend("AAOI", _entry_tech(), "none", {}, sr=_sr(),
                        gate_open=False, gate_reason="stress coverage 0.16× < 0.50×")
    assert rec.call == eer.ENTRY_WATCH
    assert any("⏸ Capacity gated" in f for f in rec.flags)
    assert any("0.16×" in f for f in rec.flags)  # real gate reason surfaced


def test_falling_knife_downgrades_entry_to_watch():
    rec = eer.recommend("KNIF", _entry_tech(rsi_14=22.0), "none", {},
                        sr=_sr(), gate_open=True)
    assert rec.call == eer.ENTRY_WATCH
    assert any("falling knife" in f for f in rec.flags)


def test_rsi_overbought_never_yields_entry():
    """Hard rule #11: RSI 72 can never produce an ENTRY_* call even with a
    perfect support/MACD/trend setup."""
    rec = eer.recommend("HOT", _entry_tech(rsi_14=72.0), "none", {},
                        sr=_sr(), gate_open=True)
    assert rec.call not in (eer.ENTRY_STRONG, eer.ENTRY_WATCH)


def test_core_override_exit_to_trim():
    config = {"core_positions": ["NVDA"]}
    rec = eer.recommend("NVDA", _broken_tech(), "long_shares", config, gate_open=True)
    assert rec.call == eer.TRIM
    assert any("core hold — override to trim" in f for f in rec.flags)


def test_parkev_disagree_flag_on_exit():
    rec = eer.recommend("UBER", _broken_tech(), "long_shares", {},
                        parkev={"ticker": "UBER", "recommendation": "BUY",
                                "rating_tier": 4}, gate_open=True)
    assert rec.call == eer.EXIT_URGENT  # NOT suppressed
    assert any("Parkev disagrees" in f for f in rec.flags)


def test_parkev_low_tier_no_flag():
    rec = eer.recommend("UBER", _broken_tech(), "long_shares", {},
                        parkev={"ticker": "UBER", "recommendation": "BUY",
                                "rating_tier": 3}, gate_open=True)
    assert rec.call == eer.EXIT_URGENT
    assert not any("Parkev" in f for f in rec.flags)


def test_directive_hold_suppresses_trim():
    rec = eer.recommend(
        "AMD", _tech(rsi_14=75.0, bb_position_pct=93.0), "long_shares", {},
        directive_holds={"AMD"}, gate_open=True,
    )
    assert rec.call == eer.HOLD
    assert any("standing directive" in f for f in rec.flags)


def test_directive_hold_does_not_suppress_exit():
    """Directives suppress TRIM (per spec) — a genuinely broken chart still
    fires EXIT_URGENT."""
    rec = eer.recommend("AMD", _broken_tech(), "long_shares", {},
                        directive_holds={"AMD"}, gate_open=True)
    assert rec.call == eer.EXIT_URGENT


# ─────────────────────────────────────────────────────────────────────────────
# Directive parser
# ─────────────────────────────────────────────────────────────────────────────

_MEMORY_SAMPLE = """# Fable Advisor Memory

## Notes to Fable (user-editable)

### Winner-close discipline overrides (per-position)

- **AMD_PUT_420_20261218 — hold for higher capture.** My exit threshold ...
- **MSFT_PUT_350_20260918 — same treatment as AMD.** Hold for more than 33%.

### Holds despite third-party downgrade

- **TSLA (equity) — not ready to sell yet.** Parkev's SELL rating is noted.

## Recent reviews (auto-maintained, most recent first)

### 2026-07-03 — Fable review

- **LITE — mentioned in a past review, should NOT become a directive.** hold hold.
"""


def test_directive_parser_extracts_hold_tickers():
    holds = eer.directive_hold_tickers(_MEMORY_SAMPLE)
    assert holds == {"AMD", "MSFT", "TSLA"}


def test_directive_parser_ignores_auto_reviews_and_empty():
    assert "LITE" not in eer.directive_hold_tickers(_MEMORY_SAMPLE)
    assert eer.directive_hold_tickers(None) == set()
    assert eer.directive_hold_tickers("") == set()


# ─────────────────────────────────────────────────────────────────────────────
# Sorting + section rendering
# ─────────────────────────────────────────────────────────────────────────────

def test_sort_recommendations_urgency_order_and_hold_silent():
    recs = [
        eer.Recommendation("A", eer.ENTRY_WATCH),
        eer.Recommendation("B", eer.HOLD),
        eer.Recommendation("C", eer.EXIT_URGENT),
        eer.Recommendation("D", eer.ENTRY_STRONG),
        eer.Recommendation("E", eer.TRIM),
        eer.Recommendation("F", eer.AVOID),
    ]
    ordered = eer.sort_recommendations(recs)
    assert [r.call for r in ordered] == [
        eer.EXIT_URGENT, eer.TRIM, eer.ENTRY_STRONG, eer.ENTRY_WATCH, eer.AVOID,
    ]
    assert all(r.call != eer.HOLD for r in ordered)


def _actions_snapshot():
    """Snapshot with one EXIT candidate, one TRIM-able core name, one HOLD."""
    from analysis import technical_indicators as ti  # noqa: F401 (vocab check)

    broken = _broken_tech(ticker="XYZ", spot=50.0)
    stretched = _tech(ticker="NVDA", rsi_14=74.0, bb_position_pct=92.0)
    quiet = _tech(ticker="MSFT", rsi_14=58.0, macd_hist=0.1, macd_hist_5d_ago=0.2)
    return {
        "technicals": {
            "XYZ": {"deep": broken, "support_resistance": None},
            "NVDA": {"deep": stretched, "support_resistance": _sr()},
            "MSFT": {"deep": quiet, "support_resistance": None},
        },
        "positions": [
            {"assetType": "EQUITY", "symbol": "XYZ", "qty": 200},
            {"assetType": "EQUITY", "symbol": "NVDA", "qty": 100},
            {"assetType": "EQUITY", "symbol": "MSFT", "qty": 50},
        ],
    }


def test_per_ticker_actions_section_renders_sorted_and_hold_silent(tmp_path):
    from steps.technical_read import render_technical_read_sections

    config = {"core_positions": ["NVDA", "MSFT"]}
    md = "\n".join(render_technical_read_sections(
        _actions_snapshot(), config, memory_path=tmp_path / "no_memory.md",
    ))
    assert "## 📋 Per-Ticker Actions" in md
    actions = md.split("## 📋 Per-Ticker Actions", 1)[1]
    assert "**EXIT_URGENT** `XYZ`" in actions
    assert "**TRIM** `NVDA`" in actions
    # urgency order: EXIT before TRIM
    assert actions.index("EXIT_URGENT") < actions.index("**TRIM**")
    # HOLD is silent in the actions section
    assert "`MSFT`" not in actions
    assert "HOLD (not shown)" in actions


def test_per_ticker_actions_respects_directive_memory_file(tmp_path):
    from steps.technical_read import render_technical_read_sections

    mem = tmp_path / "memory.md"
    mem.write_text(
        "## Notes\n\n- **NVDA — hold, do not trim.** Standing order.\n\n"
        "## Recent reviews\n", encoding="utf-8",
    )
    config = {"core_positions": []}
    md = "\n".join(render_technical_read_sections(
        _actions_snapshot(), config, memory_path=mem,
    ))
    actions = md.split("## 📋 Per-Ticker Actions", 1)[1]
    assert "**TRIM** `NVDA`" not in actions  # directive suppressed the trim


def test_per_ticker_actions_capacity_gate_note(tmp_path):
    from steps.technical_read import render_technical_read_sections

    class FakeGate:
        open = False
        reasons = ["stress coverage 0.16× below 0.50× floor"]

    snapshot = {
        "technicals": {"AAOI": {"deep": _entry_tech(ticker="AAOI"),
                                "support_resistance": _sr()}},
        "positions": [],
    }
    # Scout payload marks AAOI a candidate — reuse the theme plumbing by
    # making it a held-style lookup instead: simplest is a short-put position.
    snapshot["positions"] = [{"assetType": "OPTION", "underlying": "AAOI",
                              "optionType": "PUT", "qty": -1, "symbol": "AAOI_P"}]
    md = "\n".join(render_technical_read_sections(
        snapshot, {}, gate_state=FakeGate(), memory_path=tmp_path / "m.md",
    ))
    actions = md.split("## 📋 Per-Ticker Actions", 1)[1]
    assert "**ENTRY_WATCH** `AAOI`" in actions
    assert "⏸ Capacity gated" in actions
    assert "0.16×" in actions
    assert "**ENTRY_STRONG**" not in actions
