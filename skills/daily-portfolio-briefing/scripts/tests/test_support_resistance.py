"""Tests for the support / resistance discipline module."""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis import support_resistance as sr  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fixture builders — deterministic synthetic OHLC so we can pin level math.
# ─────────────────────────────────────────────────────────────────────────────

def _ohlc(rows: list[tuple[str, float, float, float, float]]) -> pd.DataFrame:
    """Build an OHLC DataFrame from (date, open, high, low, close) tuples."""
    idx = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows], name="Date")
    return pd.DataFrame(
        {
            "Open": [r[1] for r in rows],
            "High": [r[2] for r in rows],
            "Low": [r[3] for r in rows],
            "Close": [r[4] for r in rows],
            "Volume": [1_000_000] * len(rows),
        },
        index=idx,
    )


def _synthetic_swing_history(spot: float = 100.0) -> pd.DataFrame:
    """Build 200 daily bars with two clear swing-low clusters at $90 / $85
    and two swing-high clusters at $110 / $115. Includes some noise so the
    swing detector has work to do."""
    rows: list[tuple[str, float, float, float, float]] = []
    base = pd.Timestamp("2025-12-01")

    # Pattern: trending up through 110, pulling back to 90, rallying to 115,
    # pulling back to 85, recovering to spot. Repeated touches build clusters.
    pattern = [
        100, 102, 105, 108, 110, 109, 107, 104, 100, 97,        # touch 110
        94, 92, 90, 91, 93,                                     # touch 90 #1
        96, 100, 104, 108, 111, 110, 108, 105, 101,
        98, 95, 92, 90, 89, 91,                                 # touch 90 #2
        95, 100, 105, 110, 114, 115, 113, 110, 107, 103,        # touch 115
        99, 95, 91, 88, 86, 85, 87, 90,                         # touch 85 #1
        93, 97, 101, 106, 110, 113, 115, 114, 111, 107,         # touch 115 #2
        103, 99, 95, 90, 87, 85, 86, 89, 93, 97,                # touch 85 #2
        100, 103, 106, 109, 112, 110, 107, 104, 101, 100,
    ]
    # Stretch the pattern with mild oscillation to reach 200 bars.
    while len(pattern) < 200:
        last = pattern[-1]
        pattern.append(last + 1 if len(pattern) % 2 == 0 else last - 1)
    pattern = pattern[:200]

    for i, close in enumerate(pattern):
        dt = base + pd.Timedelta(days=i)
        high = close + 0.5
        low = close - 0.5
        open_ = close - 0.2 if i % 2 == 0 else close + 0.2
        rows.append((dt.strftime("%Y-%m-%d"), open_, high, low, close))

    # Set the last close to the requested spot so spot-relative filtering is predictable.
    last_dt, _, _, _, _ = rows[-1]
    rows[-1] = (last_dt, spot - 0.2, spot + 0.5, spot - 0.5, spot)
    return _ohlc(rows)


# ─────────────────────────────────────────────────────────────────────────────
# Pivot point math
# ─────────────────────────────────────────────────────────────────────────────

def test_pivot_daily_classic_formula():
    """P = (H+L+C)/3; R1 = 2P-L; S1 = 2P-H; pin against a known example."""
    rows = [
        ("2026-06-02", 100.0, 110.0, 90.0, 105.0),  # prior day
        ("2026-06-03", 102.0, 108.0, 98.0, 103.0),  # current day
    ]
    p = sr.compute_pivots(_ohlc(rows), period="daily")
    assert p is not None
    # Prior day H=110, L=90, C=105 → P = 101.67
    assert p["P"] == pytest.approx(101.67, abs=0.01)
    assert p["R1"] == pytest.approx(2 * 101.67 - 90.0, abs=0.05)
    assert p["S1"] == pytest.approx(2 * 101.67 - 110.0, abs=0.05)
    assert p["R2"] == pytest.approx(101.67 + 20.0, abs=0.05)
    assert p["S2"] == pytest.approx(101.67 - 20.0, abs=0.05)


def test_pivot_returns_none_on_empty():
    assert sr.compute_pivots(pd.DataFrame(), period="daily") is None
    assert sr.compute_pivots(None, period="daily") is None


def test_pivot_returns_none_on_too_short():
    rows = [("2026-06-03", 100.0, 110.0, 90.0, 105.0)]
    assert sr.compute_pivots(_ohlc(rows), period="daily") is None


def test_pivot_weekly_aggregates_prior_week():
    """Weekly pivot uses prior week's H/L/C aggregated over its trading sessions."""
    # Build 14 bars over 2 weeks; check weekly pivot is from week 1.
    rows = []
    base = pd.Timestamp("2026-05-04")  # Monday
    week1_highs = [105, 107, 108, 106, 104]  # high of week = 108
    week1_lows = [100, 101, 100, 99, 98]     # low of week = 98
    week1_closes = [103, 104, 105, 102, 101]  # close of week = 101 (Fri)
    week2_highs = [110, 112, 111, 113, 112]
    week2_lows = [105, 106, 105, 107, 108]
    week2_closes = [108, 110, 108, 111, 110]

    for i in range(5):
        rows.append(((base + pd.Timedelta(days=i)).strftime("%Y-%m-%d"),
                     week1_closes[i] - 0.5, week1_highs[i], week1_lows[i], week1_closes[i]))
    for i in range(5):
        rows.append(((base + pd.Timedelta(days=7 + i)).strftime("%Y-%m-%d"),
                     week2_closes[i] - 0.5, week2_highs[i], week2_lows[i], week2_closes[i]))

    p = sr.compute_pivots(_ohlc(rows), period="weekly")
    assert p is not None
    # Prior week (week 1) H=108, L=98, C=101 → P=(108+98+101)/3=102.33
    assert p["P"] == pytest.approx(102.33, abs=0.05)


# ─────────────────────────────────────────────────────────────────────────────
# Swing-point detection
# ─────────────────────────────────────────────────────────────────────────────

def test_swing_highs_lows_basic():
    """A peak surrounded by strictly lower bars on both sides is a swing high.
    A trough surrounded by strictly higher bars is a swing low."""
    rows = [
        ("2026-05-01", 100, 102, 99, 101),
        ("2026-05-02", 101, 103, 100, 102),
        ("2026-05-03", 102, 104, 101, 103),
        ("2026-05-04", 103, 105, 102, 104),
        ("2026-05-05", 104, 106, 103, 105),
        ("2026-05-06", 105, 110, 104, 109),  # SWING HIGH at high=110
        ("2026-05-07", 109, 108, 105, 106),
        ("2026-05-08", 106, 107, 104, 105),
        ("2026-05-09", 105, 106, 103, 104),
        ("2026-05-10", 104, 105, 102, 103),
        ("2026-05-11", 103, 104, 101, 102),
        ("2026-05-12", 102, 103, 95, 96),    # SWING LOW at low=95
        ("2026-05-13", 96, 98, 96, 97),
        ("2026-05-14", 97, 99, 97, 98),
        ("2026-05-15", 98, 100, 98, 99),
        ("2026-05-16", 99, 101, 99, 100),
        ("2026-05-17", 100, 102, 100, 101),
    ]
    highs, lows = sr.find_swing_points(_ohlc(rows), window=5, lookback_days=30)
    high_prices = [p for _, p in highs]
    low_prices = [p for _, p in lows]
    assert 110.0 in high_prices
    assert 95.0 in low_prices


def test_swing_points_empty_on_short_history():
    rows = [("2026-05-01", 100, 102, 99, 101)]
    highs, lows = sr.find_swing_points(_ohlc(rows), window=5, lookback_days=30)
    assert highs == []
    assert lows == []


# ─────────────────────────────────────────────────────────────────────────────
# Clustering
# ─────────────────────────────────────────────────────────────────────────────

def test_clusters_merge_within_pct():
    """Two swing lows at $90 and $90.50 should cluster into one level."""
    now = pd.Timestamp("2026-06-01")
    pts = [
        (now - pd.Timedelta(days=15), 90.0),
        (now - pd.Timedelta(days=45), 90.5),
    ]
    levels = sr.cluster_levels(
        pts, side=sr.SUPPORT, cluster_pct=0.02,
        recency_weights={"30d": 1.0, "90d": 0.6, "180d": 0.3}, now=now,
    )
    assert len(levels) == 1
    assert levels[0].touches == 2
    # Recency: 15d → 1.0 weight, 45d → 0.6 weight → strength = 1.6
    assert levels[0].strength == pytest.approx(1.6, abs=0.01)


def test_clusters_split_when_outside_pct():
    """Swing lows at $90 and $95 (5.5% apart) should be two separate clusters."""
    now = pd.Timestamp("2026-06-01")
    pts = [
        (now - pd.Timedelta(days=15), 90.0),
        (now - pd.Timedelta(days=30), 95.0),
    ]
    levels = sr.cluster_levels(
        pts, side=sr.SUPPORT, cluster_pct=0.02,
        recency_weights={"30d": 1.0, "90d": 0.6, "180d": 0.3}, now=now,
    )
    assert len(levels) == 2
    assert sorted(lv.price for lv in levels) == [90.0, 95.0]


# ─────────────────────────────────────────────────────────────────────────────
# Confluence
# ─────────────────────────────────────────────────────────────────────────────

def test_confluence_bumps_strength_when_near_sma():
    """A swing-low at $200.5 with the 200-SMA at $201 (well within 2%) gets a bonus."""
    level = sr.Level(price=200.5, side=sr.SUPPORT, source=sr.SRC_SWING, touches=2, strength=1.6)
    out = sr.add_confluence(
        [level],
        spot=210.0,
        sma_50=None,
        sma_200=201.0,
        high_52w=None,
        low_52w=None,
        confluence_pct=0.02,
        confluence_bonus=1.0,
    )
    assert out[0].strength == pytest.approx(2.6, abs=0.01)
    assert sr.SRC_SMA200 in out[0].confluence


def test_confluence_skipped_when_outside_pct():
    """A swing-low at $200 with 200-SMA at $215 (7.5% away) gets no bonus."""
    level = sr.Level(price=200.0, side=sr.SUPPORT, source=sr.SRC_SWING, touches=2, strength=1.6)
    out = sr.add_confluence(
        [level],
        spot=210.0,
        sma_50=None,
        sma_200=215.0,
        high_52w=None,
        low_52w=None,
        confluence_pct=0.02,
        confluence_bonus=1.0,
    )
    assert out[0].strength == 1.6
    assert sr.SRC_SMA200 not in out[0].confluence


# ─────────────────────────────────────────────────────────────────────────────
# compute_sr orchestrator (end-to-end)
# ─────────────────────────────────────────────────────────────────────────────

def test_compute_sr_fail_closed_on_empty_history():
    out = sr.compute_sr(pd.DataFrame(), spot=100.0)
    assert out.supports == []
    assert out.resistances == []
    assert out.note is not None
    assert "history" in out.note


def test_compute_sr_fail_closed_on_missing_columns():
    rows = [("2026-05-01", 100, 102, 99, 101)] * 50
    df = pd.DataFrame(
        {"Open": [r[1] for r in rows], "Close": [r[4] for r in rows]},
        index=pd.DatetimeIndex([pd.Timestamp(r[0]) + pd.Timedelta(days=i) for i, r in enumerate(rows)]),
    )
    out = sr.compute_sr(df, spot=100.0)
    assert out.supports == []
    assert out.resistances == []
    assert out.note is not None
    assert "missing" in out.note.lower()


def test_compute_sr_returns_supports_below_resistances_above():
    """End-to-end: synthetic history with known swing clusters → kept supports
    sit below spot, kept resistances sit above spot, both within reach (≤20%)."""
    df = _synthetic_swing_history(spot=100.0)
    out = sr.compute_sr(
        df, spot=100.0, sma_50=98.0, sma_200=99.0,
        # Allow single-touch swings and a longer lookback so the test isn't
        # tripped by recency-weighted strength on a 200-bar synthetic fixture.
        config={"min_touches": 1, "min_strength": 0.5, "lookback_days": 300},
    )
    # Every kept support is below spot; every kept resistance is above.
    for level in out.supports:
        assert level.price < 100.0
    for level in out.resistances:
        assert level.price > 100.0
    # Both sides should have at least one cluster within 20% of spot.
    assert out.supports, "expected at least one support cluster below spot"
    assert out.resistances, "expected at least one resistance cluster above spot"
    assert min(lv.price for lv in out.supports) >= 70.0
    assert max(lv.price for lv in out.resistances) <= 130.0


def test_compute_sr_anchors_to_pattern_clusters():
    """Pattern designed with multiple touches at ~$85 and ~$115 → those clusters
    should appear among the kept levels when min_touches=1 + long lookback."""
    df = _synthetic_swing_history(spot=100.0)
    out = sr.compute_sr(
        df, spot=100.0, sma_50=98.0, sma_200=99.0,
        config={"min_touches": 1, "min_strength": 0.5, "lookback_days": 300},
    )
    # Pattern visits ~85 multiple times → expect a cluster within 5% of 85.
    near_85 = [lv for lv in out.supports if abs(lv.price - 85.0) / 85.0 <= 0.05]
    assert near_85, f"expected a support near 85; got {[lv.price for lv in out.supports]}"
    # Pattern visits ~115 multiple times → expect a cluster within 5% of 115.
    near_115 = [lv for lv in out.resistances if abs(lv.price - 115.0) / 115.0 <= 0.05]
    assert near_115, f"expected a resistance near 115; got {[lv.price for lv in out.resistances]}"


def test_compute_sr_confidence_grows_with_real_levels():
    df = _synthetic_swing_history(spot=100.0)
    out = sr.compute_sr(
        df, spot=100.0, sma_50=98.0, sma_200=99.0,
        config={"min_touches": 1, "min_strength": 0.5, "lookback_days": 300},
    )
    assert out.confidence in {"medium", "high"}


# ─────────────────────────────────────────────────────────────────────────────
# Rendering + strike-anchoring helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_format_sr_note_empty_when_no_levels():
    out = sr.SupportResistance(spot=100.0)
    assert sr.format_sr_note(out) == ""


def test_format_sr_note_renders_compact():
    out = sr.SupportResistance(
        spot=100.0,
        supports=[
            sr.Level(price=90.0, side=sr.SUPPORT, source=sr.SRC_SWING, touches=3, strength=2.5),
        ],
        resistances=[
            sr.Level(price=115.0, side=sr.RESISTANCE, source=sr.SRC_SWING, touches=2, strength=1.8),
        ],
    )
    note = sr.format_sr_note(out)
    assert "S: $90" in note
    assert "R: $115" in note
    assert "3 touches" in note
    assert "2 touches" in note


def test_nearest_support_in_range_picks_strongest():
    out = sr.SupportResistance(
        spot=100.0,
        supports=[
            sr.Level(price=88.0, side=sr.SUPPORT, source=sr.SRC_SWING, touches=2, strength=1.6),
            sr.Level(price=92.0, side=sr.SUPPORT, source=sr.SRC_SWING, touches=4, strength=3.5),
        ],
    )
    # Range that includes both — pick the stronger one.
    pick = sr.nearest_support_in_range(out, min_price=85.0, max_price=95.0)
    assert pick is not None
    assert pick.price == 92.0


def test_nearest_support_returns_none_when_no_match():
    out = sr.SupportResistance(
        spot=100.0,
        supports=[sr.Level(price=88.0, side=sr.SUPPORT, source=sr.SRC_SWING, touches=2, strength=1.6)],
    )
    assert sr.nearest_support_in_range(out, min_price=70.0, max_price=80.0) is None


def test_nearest_support_respects_min_strength():
    out = sr.SupportResistance(
        spot=100.0,
        supports=[sr.Level(price=88.0, side=sr.SUPPORT, source=sr.SRC_SWING, touches=1, strength=0.5)],
    )
    assert sr.nearest_support_in_range(out, min_price=85.0, max_price=95.0, min_strength=1.5) is None


def test_nearest_resistance_in_range_mirrors_support():
    out = sr.SupportResistance(
        spot=100.0,
        resistances=[
            sr.Level(price=108.0, side=sr.RESISTANCE, source=sr.SRC_SWING, touches=2, strength=1.8),
            sr.Level(price=115.0, side=sr.RESISTANCE, source=sr.SRC_SWING, touches=4, strength=3.5),
        ],
    )
    pick = sr.nearest_resistance_in_range(out, min_price=105.0, max_price=120.0)
    assert pick is not None
    assert pick.price == 115.0


# ─────────────────────────────────────────────────────────────────────────────
# Audit pass — annotation + missing-S/R coverage
# ─────────────────────────────────────────────────────────────────────────────

def test_annotate_appends_sr_note_to_buy_lines():
    out = sr.SupportResistance(
        spot=16.64,
        supports=[sr.Level(price=15.0, side=sr.SUPPORT, source=sr.SRC_SWING, touches=3, strength=2.5)],
        resistances=[sr.Level(price=20.0, side=sr.RESISTANCE, source=sr.SRC_SWING, touches=2, strength=1.8)],
    )
    md = """### 📈 3. ADD · `SOFI`

**Trade:** BUY ~$5,000 of SOFI (~300 shares @ ~$16.64)
- Trigger: pullback"""
    annotated = sr.annotate_actionable_equity_lines(md, {"SOFI": out})
    assert "S: $15" in annotated
    assert "R: $20" in annotated


def test_annotate_skips_lines_with_existing_sr_note():
    """Idempotent — don't double-append if a note is already present."""
    out = sr.SupportResistance(
        spot=16.64,
        supports=[sr.Level(price=15.0, side=sr.SUPPORT, source=sr.SRC_SWING, touches=3, strength=2.5)],
    )
    md = "**Trade:** BUY ~$5,000 of SOFI · S: $15 (existing)"
    annotated = sr.annotate_actionable_equity_lines(md, {"SOFI": out})
    # Should not have appended a second S: bit
    assert annotated.count("S: $15") == 1


def test_audit_missing_sr_finds_buy_lines():
    md = """**Trade:** BUY ~$5,000 of LULU (~39 shares @ ~$127.50)
**Trade:** TRIM PLTR ~47% of position"""
    misses = sr.audit_missing_sr(md)
    # Both lines should be flagged since neither has an S: or R: hint.
    assert len(misses) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Whole-briefing orchestrator (annotate_briefing)
# ─────────────────────────────────────────────────────────────────────────────

def _sample_sr(symbol_spot: float = 100.0) -> sr.SupportResistance:
    return sr.SupportResistance(
        spot=symbol_spot,
        supports=[sr.Level(price=symbol_spot * 0.90, side=sr.SUPPORT, source=sr.SRC_SWING, touches=3, strength=2.5)],
        resistances=[sr.Level(price=symbol_spot * 1.10, side=sr.RESISTANCE, source=sr.SRC_SWING, touches=2, strength=1.8)],
    )


def test_annotate_briefing_watch_equity_line():
    md = "- **AMZN** @ $253.13 — 2.2% (+20.1%) → **HOLD**  · RSI 43"
    annotated = sr.annotate_briefing(md, {"AMZN": _sample_sr(253.0)})
    assert "S: $" in annotated
    assert "R: $" in annotated


def test_annotate_briefing_lt_csp_line():
    md = "**Trade:** SELL 1× AMD $460P exp Fri Aug 21 '26 (78 DTE)"
    annotated = sr.annotate_briefing(md, {"AMD": _sample_sr(465.0)})
    assert "S: $" in annotated
    assert "R: $" in annotated


def test_annotate_briefing_candidate_header():
    md = "**🎯 CANDIDATE · `CRM` · $192.17** ✅ RSI favourable  · _Applications_"
    annotated = sr.annotate_briefing(md, {"CRM": _sample_sr(192.0)})
    assert "S: $" in annotated


def test_annotate_briefing_lt_buy_line():
    md = "**Trade:** BUY ~$5,000 of SOFI (~300 shares @ ~$16.64)"
    annotated = sr.annotate_briefing(md, {"SOFI": _sample_sr(16.64)})
    assert "S: $" in annotated


def test_annotate_briefing_idempotent_skip_with_existing_hint():
    """Lines that already carry an S/R hint must not be double-annotated."""
    md = "- **AMZN** @ $253 · S: $238 (existing)"
    annotated = sr.annotate_briefing(md, {"AMZN": _sample_sr(253.0)})
    assert annotated.count("S: $") == 1


def test_annotate_briefing_accepts_dict_shape():
    """The snapshot serializes SR as a dict (to_dict); the orchestrator must coerce it back."""
    sr_dict = _sample_sr(100.0).to_dict()
    md = "- **TEST** @ $100 — 1.0% → **HOLD**"
    annotated = sr.annotate_briefing(md, {"TEST": sr_dict})
    assert "S: $" in annotated


def test_annotate_briefing_unknown_ticker_no_op():
    md = "- **ABCD** @ $50 — 0.0%"
    annotated = sr.annotate_briefing(md, {"AMZN": _sample_sr(253.0)})
    assert annotated == md  # No annotation added because we have no SR for ABCD.


def test_coverage_stats_counts_annotated_and_missing():
    md = """- **AMZN** @ $253.13 — 2.2% → **HOLD**  · S: $238 · R: $265
- **NVDA** @ $217.89 — 13.0% → **HOLD**
- **MSFT** @ $428 — 9.0% → **HOLD**"""
    levels = {
        "AMZN": _sample_sr(253.0),
        "NVDA": _sample_sr(217.0),  # Has data — annotation missing → flag
        # MSFT not in map → fail-closed, not a "miss"
    }
    stats = sr.coverage_stats(md, levels)
    assert stats["annotated"] == 1
    # NVDA has data but no annotation → 1 miss; MSFT is fail-closed so not counted.
    assert len(stats["missing"]) == 1
    assert "AMZN" in stats["covered_tickers"]


def test_annotate_briefing_emits_on_own_line_with_arrow():
    """The note must be on its OWN indented line below the header (↳ prefix),
    not appended to the end of the long header line where it gets lost."""
    md = "- **AMZN** @ $253.13 — 2.2% (+20.1%) → **HOLD**  · RSI 43"
    annotated = sr.annotate_briefing(md, {"AMZN": _sample_sr(253.0)})
    out_lines = annotated.split("\n")
    # The original header is untouched.
    assert out_lines[0] == md
    # The next line carries the S/R note with the ↳ prefix and 2-space indent.
    assert len(out_lines) >= 2
    assert out_lines[1].startswith("  ↳ ")
    assert "S: $" in out_lines[1]
    assert "R: $" in out_lines[1]


def test_annotate_briefing_idempotent_skips_when_next_line_has_hint():
    """If the next line already has an S/R hint, don't add another."""
    md = "- **AMZN** @ $253 — HOLD\n  ↳ S: $238 (existing)"
    annotated = sr.annotate_briefing(md, {"AMZN": _sample_sr(253.0)})
    # Should still have exactly 1 occurrence — the existing one.
    assert annotated.count("S: $") == 1


def test_coverage_stats_recognizes_next_line_hint():
    """coverage_stats must count a line as 'annotated' when the hint is on the
    next line (the new format), not just same-line."""
    md = """- **AMZN** @ $253 — HOLD
  ↳ S: $238 (swing, 3 touches) · R: $265 (swing, 2 touches)
- **NVDA** @ $217 — HOLD"""
    stats = sr.coverage_stats(md, {
        "AMZN": _sample_sr(253.0),
        "NVDA": _sample_sr(217.0),
    })
    # AMZN counted as annotated via the next-line hint; NVDA flagged as missing.
    assert stats["annotated"] == 1
    assert "AMZN" in stats["covered_tickers"]
    assert len(stats["missing"]) == 1
    assert "NVDA" in stats["missing"][0]


def test_annotate_briefing_action_list_close_put():
    """Action List CLOSE on a short put extracts ticker from contract symbol."""
    md = "1. **CLOSE** AMD_PUT_420_20261218 — +30% ($+2,281); buy-to-close limit $55.49  · RSI 70"
    annotated = sr.annotate_briefing(md, {"AMD": _sample_sr(465.0)})
    out_lines = annotated.split("\n")
    assert len(out_lines) >= 2
    assert out_lines[1].startswith("  ↳ ")
    assert "S: $" in out_lines[1]


def test_annotate_briefing_action_list_roll_call():
    """ROLL_OUT_AND_UP on a covered call also gets annotated."""
    md = "9. **ROLL_OUT_AND_UP** SOXX_CALL_600_20261016 — roll UP and out  · RSI 73"
    annotated = sr.annotate_briefing(md, {"SOXX": _sample_sr(602.0)})
    out_lines = annotated.split("\n")
    assert len(out_lines) >= 2
    assert "↳ " in out_lines[1]
    assert "S: $" in out_lines[1]


def test_annotate_briefing_action_list_hedge():
    """HEDGE Buy Nx TICKER captures the ticker even though it isn't followed by '_'."""
    md = "10. **HEDGE** Buy 15× SPY put $718P Fri Jul 10 '26 (~$11,349; coverage 1% → target 10%)"
    annotated = sr.annotate_briefing(md, {"SPY": _sample_sr(756.0)})
    out_lines = annotated.split("\n")
    assert len(out_lines) >= 2
    assert "S: $" in out_lines[1]


def test_annotate_briefing_action_list_exit_equity():
    """EXIT TSLA — bare ticker after action verb."""
    md = "4. **EXIT** TSLA — SELL TSLA — exit position  · RSI 51"
    annotated = sr.annotate_briefing(md, {"TSLA": _sample_sr(419.0)})
    out_lines = annotated.split("\n")
    assert "S: $" in "\n".join(out_lines)


def test_action_list_pattern_doesnt_falsely_match_other_lines():
    """The pattern must NOT match prose lines that look like a numbered list
    but aren't actions, AND must not double-annotate something annotated
    by a different pattern."""
    md = """1. Not an action — just regular text
- **AMZN** @ $253 — HOLD
2. **CLOSE** AMZN_PUT_240_20260821 — winner"""
    annotated = sr.annotate_briefing(md, {"AMZN": _sample_sr(253.0)})
    # The CLOSE on line 3 should get annotated. The Watch line on line 2 too.
    # The prose line 1 should NOT.
    out_lines = annotated.split("\n")
    arrow_lines = [l for l in out_lines if l.startswith("  ↳")]
    assert len(arrow_lines) == 2  # one for Watch, one for CLOSE


def test_annotate_briefing_full_pass():
    """End-to-end: a tiny briefing with all four surfaces gets the right number of annotations."""
    md = """## Watch
- **AAPL** @ $311 — 5% → **HOLD**  · RSI 67
- **NVDA** @ $217 — 13% → **HOLD**  · RSI 53

## LT Opportunities
### 📈 ADD · `SOFI`

**Trade:** BUY ~$5,000 of SOFI (~300 shares @ ~$16.64)
- trigger

### 💎 LONG DATED CSP · `AMD`

**Trade:** SELL 1× AMD $460P exp Fri Aug 21 '26 (78 DTE)

## Candidate Trades
**🎯 CANDIDATE · `CRM` · $192** ✅ RSI favourable"""
    levels = {
        "AAPL": _sample_sr(311.0),
        "NVDA": _sample_sr(217.0),
        "SOFI": _sample_sr(16.64),
        "AMD": _sample_sr(465.0),
        "CRM": _sample_sr(192.0),
    }
    annotated = sr.annotate_briefing(md, levels)
    # All 5 actionable single-stock lines should now carry an S/R hint.
    assert annotated.count("S: $") == 5
    assert annotated.count("R: $") == 5
