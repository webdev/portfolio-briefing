"""Tests for the When-To-Enter report.

The classifier is the contract: technical state first (overbought / extended /
falling-knife), then thesis check, then verdict-driven AVOID, then the favored
pullback band. Every test pins a specific (RSI, drawdown, verdict, trend)
combination to a single expected status so the priority order can't regress.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps.when_to_enter import (  # noqa: E402
    classify, render_when_to_enter_report,
)


def _r(**kw):
    """Build a scout result with defaults the classifier expects."""
    base = {
        "ticker": "XYZ", "spot": 100.0, "rsi_14": 50,
        "iv_rank": 50, "sma_200": 100.0, "drawdown_pct": 0,
        "fivedayret_pct": 0, "verdict": "WATCH", "rationale": [],
        "csp_entry": None, "days_to_earnings": None,
    }
    base.update(kw)
    return base


def test_overbought_overrides_avoid_verdict():
    """An AVOID verdict on an RSI-75 name reduces to 'WAIT — overbought', not
    AVOID. (The AMD bug: scout flagged it AVOID for chasing, but it's just
    overbought and entry-able after cool-off.)"""
    r = _r(ticker="AMD", spot=498, rsi_14=75, drawdown_pct=1, fivedayret_pct=20,
           verdict="AVOID — extended", iv_rank=84, sma_200=234)
    status, label, read, trigger = classify(r)
    assert status == "wait"
    assert "overbought" in label
    assert "WAIT for RSI < 55" in trigger
    # Not the "Deep drawdown (1%)" nonsense
    assert "Deep drawdown (1%)" not in read


def test_extended_band_60_to_70():
    r = _r(rsi_14=65, iv_rank=40)
    status, label, _, trigger = classify(r)
    assert status == "wait"
    assert "extended" in label
    assert "45-55" in trigger


def test_thesis_check_when_dd_weak_below_sma():
    """The genuine thesis-broken case: deep drawdown + weak RSI + below SMA."""
    r = _r(rsi_14=38, drawdown_pct=55, sma_200=180.0, spot=80.0)   # 56% below 200-SMA
    status, label, _, _ = classify(r)
    assert status == "avoid"
    assert "thesis check" in label.lower()


def test_falling_knife_under_25():
    r = _r(rsi_14=22, drawdown_pct=30, fivedayret_pct=-12)
    status, label, _, trigger = classify(r)
    assert status == "wait"
    assert "falling knife" in label
    assert "RSI > 35" in trigger


def test_verdict_avoid_when_not_overbought_or_thesis():
    """Scout flagged a concern (not captured by the technical or thesis branches)
    — surface the scout's actual rationale rather than inventing one."""
    r = _r(rsi_14=50, verdict="AVOID — single-binary biotech",
           rationale=["FDA decision Jun 30"])
    status, label, read, _ = classify(r)
    assert status == "avoid"
    assert "scout flag" in label.lower()
    assert "FDA" in read


def test_entry_now_csp_in_pullback_band():
    r = _r(rsi_14=42, drawdown_pct=20, verdict="CSP ENTRY (fat premium)",
           csp_entry={"strike": 90, "mid": 1.50, "bid": 1.40, "ask": 1.60,
                      "expiration": "2026-07-17", "dte": 31})
    status, label, _, trigger = classify(r)
    assert status == "enter"
    assert "ENTRY NOW — CSP" in label
    assert "SELL 1× $90P" in trigger
    assert "Jul 17 '26" in trigger
    assert "Collateral ~$9,000" in trigger


def test_entry_now_csp_flags_earnings_inside_window():
    r = _r(rsi_14=42, verdict="CSP ENTRY (fat premium)",
           csp_entry={"strike": 100, "mid": 2.0, "bid": 1.9, "ask": 2.1,
                      "expiration": "2026-07-02", "dte": 35},
           days_to_earnings=10)
    _, _, _, trigger = classify(r)
    assert "earnings in 10d INSIDE" in trigger


def test_entry_now_buy_in_pullback_band():
    r = _r(rsi_14=42, drawdown_pct=25, verdict="BUY (pullback)")
    status, label, _, _ = classify(r)
    assert status == "enter"
    assert "ENTRY NOW — BUY" in label


def test_pullback_band_with_no_actionable_verdict_is_watch():
    """RSI in the favored 35-55 band but the scout has no actionable side
    (WATCH verdict). Don't manufacture an entry — render WATCH."""
    r = _r(rsi_14=45, verdict="WATCH — no catalyst")
    status, label, _, trigger = classify(r)
    assert status == "watch"
    assert "WATCH" in label
    assert "CSP entry becomes favored" in trigger


def test_oversold_band_25_to_35():
    r = _r(rsi_14=30, drawdown_pct=22, fivedayret_pct=-6)
    status, label, _, _ = classify(r)
    assert status == "wait"
    assert "oversold" in label


def test_neutral_band_55_to_60():
    r = _r(rsi_14=57, verdict="WATCH")
    status, label, _, _ = classify(r)
    assert status == "watch"
    assert "NEUTRAL" in label


def test_tier5_top_stock_to_buy_promotes_to_strong_buy():
    """Parkev tier 5 (Top Stock to Buy) in the pullback band → 🌟 STRONG BUY,
    not plain BUY. Sizing guidance shifts from '1/3 starter' to '1/2 starter'."""
    r = _r(ticker="META", rsi_14=48, drawdown_pct=15, verdict="BUY (pullback)",
           rating_tier=5, raw_recommendation="Top Stock to Buy")
    status, label, read, trigger = classify(r)
    assert status == "enter"
    assert "🌟 STRONG BUY" in label
    assert "Parkev tier-5" in read
    assert "Top Stock to Buy" in read
    assert "1/2 of target weight" in trigger


def test_tier4_top_15_also_strong_buy():
    """Tier 4 (Top 15/25 Stock) gets the same STRONG BUY treatment."""
    r = _r(rsi_14=45, verdict="BUY (pullback)",
           rating_tier=4, raw_recommendation="Top 15 Stock")
    status, label, read, _ = classify(r)
    assert status == "enter"
    assert "🌟 STRONG BUY" in label
    assert "tier-4" in read


def test_tier3_plain_buy_stays_standard_label():
    """Tier 3 (plain Buy) keeps the standard 🟢 ENTRY NOW — BUY label and
    1/3-starter sizing — the discipline only widens for genuinely top-tier recs."""
    r = _r(rsi_14=45, verdict="BUY (pullback)",
           rating_tier=3, raw_recommendation="Buy")
    status, label, read, trigger = classify(r)
    assert status == "enter"
    assert "🌟" not in label
    assert "🟢 ENTRY NOW — BUY" == label
    assert "tier-" not in read       # no tier annotation for non-top-tier
    assert "1/3 of target weight" in trigger


def test_tier5_csp_also_promoted():
    """A tier-5 name with a CSP setup gets the STRONG BUY (CSP) label."""
    r = _r(rsi_14=42, verdict="CSP ENTRY (fat premium)",
           rating_tier=5, raw_recommendation="Top Stock to Buy",
           csp_entry={"strike": 95, "mid": 2.0, "bid": 1.9, "ask": 2.1,
                      "expiration": "2026-07-17", "dte": 35})
    status, label, _, trigger = classify(r)
    assert status == "enter"
    assert "🌟 STRONG BUY" in label
    assert "(CSP)" in label
    assert "SELL 1× $95P" in trigger


def test_tier_missing_treated_as_low_tier():
    """No rating_tier in the scout result (e.g., legacy data or scout-only name) →
    classify as standard BUY without the star, no crash."""
    r = _r(rsi_14=45, verdict="BUY (pullback)")  # no rating_tier key
    status, label, _, _ = classify(r)
    assert status == "enter"
    assert "🌟" not in label


def test_render_handles_empty_payload():
    assert render_when_to_enter_report(None) == ""
    assert render_when_to_enter_report({}) == ""


def test_render_groups_themes_and_dedupes_tickers():
    """A ticker appearing in multiple themes shows up once under its first
    theme, with the other themes listed as 'also in'."""
    payload = {
        "themes": {
            "semis": {"name": "Semis", "group": "The AI Buildout"},
            "applications": {"name": "Applications", "group": "The AI Buildout"},
        },
        "results_by_theme": {
            "semis": [_r(ticker="ARM", spot=300, rsi_14=72,
                         fivedayret_pct=30, iv_rank=90, sma_200=140)],
            "applications": [_r(ticker="ARM", spot=300, rsi_14=72,
                                fivedayret_pct=30, iv_rank=90, sma_200=140)],
        },
        "generated_at_iso": "2026-05-28T10:00:00",
    }
    md = render_when_to_enter_report(payload, generated_at="Thursday, May 28, 2026")
    # ARM appears exactly once as a card
    assert md.count("#### `ARM`") == 1
    # The cross-theme membership is surfaced
    assert "also in: Applications" in md
    # And the status reflects the overbought RSI
    assert "WAIT — overbought" in md


def test_unlisted_theme_still_renders_via_auto_extend():
    """Regression: a theme present in the scout payload but missing from the
    hardcoded THEME_ORDER must still render — the loop auto-extends with any
    payload themes not in the canonical order. (Caught META/PINS/RDDT/SNAP/TTD
    being silently dropped from the Social & Ad-Tech AI theme on 2026-06-01.)"""
    payload = {
        "themes": {
            "brand_new_theme_added_yesterday": {
                "name": "Made Up Theme",
                "group": "Adjacent",
            },
        },
        "results_by_theme": {
            "brand_new_theme_added_yesterday": [
                _r(ticker="ZZZ", spot=100, rsi_14=50, verdict="BUY (pullback)",
                   rating_tier=3, raw_recommendation="Buy"),
            ],
        },
        "generated_at_iso": "2026-06-02T00:00:00",
    }
    md = render_when_to_enter_report(payload, generated_at="X")
    assert "Made Up Theme" in md
    assert "`ZZZ`" in md
    assert "🟢 Today's Candidates" in md or "ENTRY NOW" in md


def test_etfs_render_in_dedicated_section_not_themes():
    """ETFs must appear in the '🪙 ETF Benchmarks' section, NOT inside the
    per-theme sections. User explicitly asked for ETFs in one obvious place
    instead of scattered through theme groups."""
    payload = {
        "themes": {
            "semis": {"name": "Semis", "group": "The AI Buildout"},
        },
        "results_by_theme": {
            "semis": [
                _r(ticker="NVDA", spot=200, rsi_14=72, fivedayret_pct=10, iv_rank=80),  # stock
                _r(ticker="SMH", spot=600, rsi_14=72, fivedayret_pct=10, iv_rank=85),   # ETF
            ],
        },
        "generated_at_iso": "2026-06-02T00:00:00",
    }
    # SMH must be in DEFAULT_ETFS in intrinsic_value.py for the ETF detection
    # to fire — check that's the case before asserting on routing.
    from analysis.intrinsic_value import is_etf, default_etf_set
    assert is_etf("SMH", default_etf_set(None)), "SMH must be in DEFAULT_ETFS"

    md = render_when_to_enter_report(payload, generated_at="X")
    # Anchor section boundaries on newline+"## " so we don't mis-match against
    # "## " appearing inside "### " H3 headers (which contain the substring).
    etf_pos = md.find("## 🪙 ETF Benchmarks")
    assert etf_pos != -1, "ETF Benchmarks section must exist"
    theme_groups_pos = md.find("\n## The AI Buildout")
    assert theme_groups_pos != -1, "The AI Buildout group section must exist"
    assert etf_pos < theme_groups_pos, "ETF section must come before the theme groups"

    # SMH lives ONLY in the ETF section.
    etf_block = md[etf_pos:theme_groups_pos]
    assert "`SMH`" in etf_block, "SMH card must be in the ETF Benchmarks section"
    assert "`NVDA`" not in etf_block, "NVDA (stock) must NOT be in the ETF section"

    # The Semis theme block under "## The AI Buildout" has NVDA but NOT SMH.
    theme_block = md[theme_groups_pos:]
    assert "`NVDA`" in theme_block, "NVDA card must be under Semis (theme section)"
    assert "`SMH`" not in theme_block, "SMH must NOT be duplicated under Semis"


def test_render_summary_line_present():
    payload = {
        "themes": {"semis": {"name": "Semis", "group": "The AI Buildout"}},
        "results_by_theme": {
            "semis": [
                _r(ticker="A", rsi_14=42, verdict="BUY (pullback)"),
                _r(ticker="B", rsi_14=75, fivedayret_pct=20),
                _r(ticker="C", rsi_14=50, verdict="WATCH"),
            ],
        },
    }
    md = render_when_to_enter_report(payload, generated_at="X")
    assert "ENTRY NOW · 🟡 1 WAIT" in md     # 1 enter, 1 wait, 1 watch
    assert "WATCH/NEUTRAL" in md


# ─────────────────────────────────────────────────────────────────────────────
# S/R enrichment — triggers use explicit support levels when SR is provided.
# Backwards-compatible: SR=None reproduces the legacy phrasing.
# ─────────────────────────────────────────────────────────────────────────────

def _sr_payload(spot, support_price=None, resistance_price=None):
    """Build a SupportResistance-shaped dict matching what the snapshot emits."""
    supports = []
    if support_price is not None:
        supports.append({
            "price": support_price, "side": "support", "source": "swing",
            "touches": 3, "age_days": 30, "strength": 2.5, "confluence": ["sma_50"],
        })
    resistances = []
    if resistance_price is not None:
        resistances.append({
            "price": resistance_price, "side": "resistance", "source": "swing",
            "touches": 2, "age_days": 20, "strength": 1.8, "confluence": [],
        })
    return {
        "spot": spot, "supports": supports, "resistances": resistances,
        "pivots": {}, "confidence": "medium", "note": None,
    }


def test_classify_overbought_uses_sr_support_zone():
    """RSI 75 + S/R provided → trigger says 'into the $X support zone', not '8-12%'."""
    r = _r(rsi_14=75, spot=100.0, fivedayret_pct=25.0)
    sr = _sr_payload(spot=100.0, support_price=90.0)
    _, _, _, trigger = classify(r, sr=sr)
    assert "into" in trigger
    assert "$90" in trigger
    assert "8-12%" not in trigger  # legacy phrasing replaced


def test_classify_overbought_no_sr_keeps_legacy_phrasing():
    """SR=None must reproduce the original '8-12% pullback' text exactly."""
    r = _r(rsi_14=75, spot=100.0, fivedayret_pct=25.0)
    _, _, _, trigger = classify(r, sr=None)
    assert "8-12%" in trigger


def test_classify_extended_uses_sr_support_zone():
    r = _r(rsi_14=63, spot=100.0)
    sr = _sr_payload(spot=100.0, support_price=92.0)
    _, _, _, trigger = classify(r, sr=sr)
    assert "$92" in trigger
    assert "5-8%" not in trigger


def test_classify_entry_csp_adds_confluence_when_at_support():
    """Spot within 3% of a strong support during ENTRY (CSP) → confluence badge."""
    r = _r(rsi_14=45, spot=100.0, verdict="CSP (fat premium)",
           csp_entry={"strike": 95, "mid": 2.0, "bid": 1.8, "ask": 2.2,
                      "expiration": "2026-07-17", "dte": 30})
    # Spot 100 is 2% above the support at 98 → "at support"
    sr = _sr_payload(spot=100.0, support_price=98.0)
    _, _, read, trigger = classify(r, sr=sr)
    # Confluence note goes into the READ (the explanatory text).
    assert "confluence" in read.lower() or "spot sits" in read.lower()


def test_classify_entry_csp_no_confluence_when_far_from_support():
    """Spot far from support → no confluence badge, ENTRY still fires."""
    r = _r(rsi_14=45, spot=100.0, verdict="CSP (fat premium)",
           csp_entry={"strike": 95, "mid": 2.0, "bid": 1.8, "ask": 2.2,
                      "expiration": "2026-07-17", "dte": 30})
    sr = _sr_payload(spot=100.0, support_price=85.0)  # 15% below spot
    status, label, read, trigger = classify(r, sr=sr)
    assert status == "enter"
    assert "confluence" not in read.lower()


def test_classify_entry_csp_strike_anchored_to_support():
    """When the proposed strike sits at a support cluster, surface that."""
    r = _r(rsi_14=45, spot=100.0, verdict="CSP (fat premium)",
           csp_entry={"strike": 90, "mid": 2.0, "bid": 1.8, "ask": 2.2,
                      "expiration": "2026-07-17", "dte": 30})
    sr = _sr_payload(spot=100.0, support_price=90.0)
    _, _, _, trigger = classify(r, sr=sr)
    assert "strike sits" in trigger.lower() or "real support" in trigger.lower()


def test_classify_watch_neutral_uses_sr_pullback_target():
    r = _r(rsi_14=45, spot=100.0, verdict="WATCH")
    sr = _sr_payload(spot=100.0, support_price=92.0)
    _, _, _, trigger = classify(r, sr=sr)
    assert "$92" in trigger


def test_classify_buy_entry_scale_target_uses_support():
    r = _r(rsi_14=42, spot=100.0, verdict="BUY (pullback)", drawdown_pct=15)
    sr = _sr_payload(spot=100.0, support_price=90.0)
    _, _, _, trigger = classify(r, sr=sr)
    # Scale-target phrasing uses the support price, not the legacy RSI band.
    assert "$90" in trigger


def test_classify_sr_none_preserves_legacy_behavior_across_all_paths():
    """Sanity check: every status produces identical output with sr=None vs
    no sr argument. Guarantees backward compatibility of the signature change."""
    test_cases = [
        _r(rsi_14=75),                          # overbought
        _r(rsi_14=63),                          # extended
        _r(rsi_14=45, dd=50, sma_200=130),      # thesis check
        _r(rsi_14=22),                          # falling knife
        _r(rsi_14=45, verdict="AVOID — flag"),  # scout AVOID
        _r(rsi_14=45, verdict="WATCH"),         # watch neutral
        _r(rsi_14=30),                          # oversold
        _r(rsi_14=57),                          # neutral mid
    ]
    for r in test_cases:
        assert classify(r) == classify(r, sr=None)


def test_render_passes_sr_by_sym_to_cards():
    """End-to-end: SR data appears in the rendered cards when sr_by_sym is supplied."""
    payload = {
        "themes": {"semis": {"name": "Semis", "group": "The AI Buildout"}},
        "results_by_theme": {
            "semis": [_r(ticker="AAPL", rsi_14=75, spot=200.0, fivedayret_pct=20.0)],
        },
    }
    sr_by = {"AAPL": _sr_payload(spot=200.0, support_price=180.0)}
    md = render_when_to_enter_report(payload, generated_at="X", sr_by_sym=sr_by)
    # Trigger should mention $180 (the support), not "8-12% pullback".
    assert "$180" in md
    assert "8-12%" not in md
