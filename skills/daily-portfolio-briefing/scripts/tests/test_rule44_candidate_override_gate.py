"""Regression tests — 2026-08-07 briefing BUG B: RSI override violations on
candidate cards (rule #44).

Observed lines (briefing_full_2026-08-07.md):

  "**🎯 CANDIDATE · `NOW` · $117.21** ... · RSI 61 — OVERRIDE (BUY rec)"
  "**🎯 CANDIDATE · `TEAM` · $109.73** ... RSI 64 — OVERRIDE (BUY rec)"
  "**🎯 CANDIDATE · `QQQ` · $716.31** ... RSI 55 — OVERRIDE (outside 35-50 band)"

Two defects:
  1. Rule #44: for NEW put-sales the favored band is 35-55 and 60-70 is the
     extended-wait demotion band — a third-party BUY rec must NOT override the
     extended band (recs differentiate WITHIN the actionable set; they never
     loosen RSI gates). NOW (RSI 61) and TEAM (RSI 64) rendered actionable
     CSP tickets via the BUY-verdict path, which had no extended demotion.
  2. "outside 35-50 band" mis-stated the band — the configured entry band is
     35-55 (rsi_discipline.put_entry_band; scout rule #25), so RSI 55 is IN
     band and needs no override at all. verdict_state hardcoded a second band
     definition (35-50) that drifted from the scout's qualifying band.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import rsi_discipline, verdict_state  # noqa: E402
from steps import candidate_research as cr  # noqa: E402


def _res(**kw):
    base = {
        "ticker": "NOW", "spot": 117.21, "rsi_14": 61, "iv_rank": 67,
        "sma_200": 123.0, "drawdown_pct": 38.1, "fivedayret_pct": 1.3,
        "verdict": "BUY (pullback)", "rationale": ["drawdown 38% + BUY rec"],
        "third_party_rec": "BUY",
        "csp_entry": {"strike": 105, "mid": 3.27, "bid": 2.75, "ask": 3.80,
                      "expiration": "2026-09-11", "dte": 36},
    }
    base.update(kw)
    return base


def _payload(results):
    return {
        "themes": {"applications": {"name": "Applications",
                                    "group": "AI", "anchors": [], "etfs": []}},
        "results_by_theme": {"applications": results},
    }


# ─────────────────────────────────────────────────────────────────────────────
# (c) RSI ≥ 60 + BUY rec → demoted, never an OVERRIDE actionable card
# ─────────────────────────────────────────────────────────────────────────────

def test_rsi_61_buy_rec_demotes_to_on_deck_not_override_candidate():
    """Observed: '**🎯 CANDIDATE · `NOW` · $117.21** ... RSI 61 — OVERRIDE
    (BUY rec)' followed by an actionable '⏸ **Deferred (capacity gated)** ·
    SELL 1× NOW $105P exp **Fri Sep 11 '26** (36 DTE) ... RSI 61 — OVERRIDE
    (BUY rec)'. A BUY rec must not override the 60-70 extended-wait band —
    the name demotes to the wait/On-Deck presentation (visible, rule #24)."""
    md = cr.render_candidate_briefing(
        _payload([_res()]), fv_by_ticker={}, config={}, generated_at="T")
    assert "🎯 CANDIDATE · `NOW`" not in md
    assert "OVERRIDE (BUY rec)" not in md
    assert "SELL 1× NOW" not in md          # no actionable ticket in the band
    # Kept visible on deck (rule #24 — never hidden).
    assert "`NOW`" in md
    assert "On Deck" in md


def test_rsi_64_team_buy_rec_demotes_too():
    """Observed: '**🎯 CANDIDATE · `TEAM` · $109.73** ... RSI 64 — OVERRIDE
    (BUY rec)' with a 'SELL 1× TEAM $99P' ticket. Same demotion applies."""
    r = _res(ticker="TEAM", spot=109.73, rsi_14=64.2, drawdown_pct=37.7,
             fivedayret_pct=5.2,
             csp_entry={"strike": 99, "mid": 6.25, "bid": 4.60, "ask": 7.90,
                        "expiration": "2026-09-11", "dte": 36})
    status, rv = cr._status(r, rsi_discipline.load_thresholds({}))
    assert status == "held_rsi"
    assert "never loosens the RSI gate" in rv.reason


def test_status_demotion_fires_at_60_but_not_below():
    th = rsi_discipline.load_thresholds({})
    assert cr._status(_res(rsi_14=60), th)[0] == "held_rsi"
    assert cr._status(_res(rsi_14=59), th)[0] == "candidate"


def test_rsi_70_plus_still_hard_blocked():
    th = rsi_discipline.load_thresholds({})
    status, rv = cr._status(_res(rsi_14=72), th)
    assert status == "held_rsi"
    assert rv.removed


# ─────────────────────────────────────────────────────────────────────────────
# (d) RSI 55 with band 35-55 → in-band, no override text
# ─────────────────────────────────────────────────────────────────────────────

def test_rsi_55_in_band_no_override_text():
    """Observed: '**🎯 CANDIDATE · `QQQ` · $716.31** ... RSI 55 — OVERRIDE
    (outside 35-50 band)'. The configured entry band is 35-55, so RSI 55 is
    IN band — the card must carry the normal RSI read, no OVERRIDE at all."""
    # (mid raised from the observed $2.92 so the ticket clears the 2026-08-10
    # delivered-yield floor — this test pins the OVERRIDE labelling, not the
    # yield gate, which has its own tests in test_2026_08_10_briefing_fixes.)
    r = _res(ticker="QQQ", spot=716.31, rsi_14=55.0, drawdown_pct=3.9,
             third_party_rec=None,
             verdict="CSP ENTRY (independent setup)",
             csp_entry={"strike": 645, "mid": 8.20, "bid": 8.10, "ask": 8.30,
                        "expiration": "2026-09-11", "dte": 36})
    md = cr.render_candidate_briefing(
        _payload([r]), fv_by_ticker={}, config={}, generated_at="T")
    assert "🎯 CANDIDATE · `QQQ`" in md
    assert "OVERRIDE" not in md
    assert "outside 35-50 band" not in md
    assert "SELL 1× QQQ $645P" in md


def test_rsi_in_band_uses_unified_35_55():
    assert verdict_state.rsi_in_band(55) is True
    assert verdict_state.rsi_in_band(50) is True
    assert verdict_state.rsi_in_band(56) is False
    assert verdict_state.rsi_in_band(34) is False


# ─────────────────────────────────────────────────────────────────────────────
# (e) band label text derives from config, never a hardcoded string
# ─────────────────────────────────────────────────────────────────────────────

def test_override_label_band_text_from_default_config():
    label = verdict_state.override_label(57)
    assert label == "RSI 57 — OVERRIDE (outside 35-55 band)"
    assert "35-50" not in label


def test_override_label_band_text_from_custom_config():
    th = rsi_discipline.load_thresholds(
        {"rsi_discipline": {"put_entry_band": [40, 52]}})
    label = verdict_state.override_label(57, thresholds=th)
    assert "outside 40-52 band" in label
    # And rsi_in_band gates on the same configured values.
    assert verdict_state.rsi_in_band(53, th) is False
    assert verdict_state.rsi_in_band(51, th) is True


def test_put_entry_band_single_source_of_truth():
    """The band comes from rsi_discipline (config-loaded), not a second
    hardcoded definition in verdict_state."""
    assert rsi_discipline.put_entry_band(None) == (35.0, 55.0)
    th = rsi_discipline.load_thresholds(
        {"rsi_discipline": {"put_entry_band": [30, 50]}})
    assert rsi_discipline.put_entry_band(th) == (30.0, 50.0)
