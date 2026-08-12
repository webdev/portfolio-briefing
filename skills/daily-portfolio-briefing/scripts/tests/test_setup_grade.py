"""Setup Grade scorer — component math, missing-input handling, messages.

George (2026-08-10): "We need a very clear message as to when I should get
in on every transaction." The Setup Grade answers WHEN (entry-timing
quality); Parkev/CP/MV answer WHAT. These tests pin the scorer's contract:
peak/taper band boundaries, vol scaling, missing-component renormalization
+ the never-an-A-on-partial-data cap, RSI hard-block '—' supremacy,
IVr-vs-RVr source labeling, and config-derived (never hardcoded) targets
in the wait messages.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import setup_grade as sg  # noqa: E402


CFG_ON = {"setup_grade": {"enabled": True}}

SR_EMPTY = {"supports": [], "resistances": []}
SR_GOOD_SUPPORT = {"supports": [
    {"price": 94.0, "touches": 3, "strength": 4.0, "side": "support"}],
    "resistances": []}
SR_GOOD_RESISTANCE = {"supports": [], "resistances": [
    {"price": 104.0, "touches": 3, "strength": 4.0, "side": "resistance"}]}


def _csp(**kw):
    base = dict(rsi=40, iv_rank=70, iv_rank_source="rv",
                support_resistance=SR_GOOD_SUPPORT, strike=95, spot=100,
                sma_200=95, days_to_earnings=42, day_change_pct=-0.01,
                drawdown_pct=10, config=CFG_ON)
    base.update(kw)
    return sg.csp_setup(**base)


def _cc(**kw):
    base = dict(rsi=70, iv_rank=70, iv_rank_source="rv",
                support_resistance=SR_GOOD_RESISTANCE, strike=104, spot=100,
                sma_200=92, days_to_earnings=42, day_change_pct=0.01,
                drawdown_pct=5, config=CFG_ON)
    base.update(kw)
    return sg.cc_setup(**base)


# ── Config loading ────────────────────────────────────────────────────


def test_defaults_are_off_in_code():
    """Kill-switch style: in-code default is DISABLED — briefing.yaml turns
    it on. Flag off → every wired surface stays byte-identical legacy."""
    assert sg.DEFAULTS["enabled"] is False
    assert sg.setup_grade_enabled(None) is False
    assert sg.setup_grade_enabled({}) is False
    assert sg.setup_grade_enabled(CFG_ON) is True


def test_default_letter_floors_pinned():
    """A≥85, A-≥78, B≥65, C≥50 — the webapp drift guard mirrors these."""
    assert sg.DEFAULTS["letters"] == {
        "a": 85.0, "a_minus": 78.0, "b": 65.0, "c": 50.0}
    assert sg.letter_for(85.0) == "A"
    assert sg.letter_for(84.9) == "A-"
    assert sg.letter_for(78.0) == "A-"
    assert sg.letter_for(65.0) == "B"
    assert sg.letter_for(50.0) == "C"
    assert sg.letter_for(49.9) == "D"


def test_config_overrides_letters():
    cfg = {"setup_grade": {"letters": {"a": 90}}}
    assert sg.letter_for(86.0, cfg) == "A-"
    assert sg.letter_for(90.0, cfg) == "A"


# ── CSP RSI component: peak 35-45, taper to 55, cliff below 35 ─────────


@pytest.mark.parametrize("rsi,expected", [
    (35.0, 1.0),   # band-low edge = peak
    (45.0, 1.0),   # peak upper edge (band midpoint)
    (50.0, 0.5),   # halfway down the taper
    (55.0, 0.0),   # band-high edge → 0
    (34.9, 0.0),   # below the band → 0 (oversold territory)
    (56.0, 0.0),   # above the band → 0
])
def test_csp_rsi_component_boundaries(rsi, expected):
    g = _csp(rsi=rsi)
    assert g["components"]["rsi"]["score"] == pytest.approx(expected, abs=0.02)


# ── CC RSI component: 0 at 60, 1.0 at 70+ ──────────────────────────────


@pytest.mark.parametrize("rsi,expected", [
    (60.0, 0.0),
    (65.0, 0.5),
    (70.0, 1.0),
    (78.0, 1.0),
    (59.0, 0.0),   # below favored → 0
    (45.0, 0.0),   # mid-range → 0 (wait for strength)
])
def test_cc_rsi_component_boundaries(rsi, expected):
    g = _cc(rsi=rsi)
    assert g["components"]["rsi"]["score"] == pytest.approx(expected, abs=0.02)


# ── Vol component: floor 40 → 0, linear to 1.0 at 100 ──────────────────


@pytest.mark.parametrize("rank,expected", [
    (40.0, 0.0), (39.0, 0.0), (70.0, 0.5), (100.0, 1.0), (79.0, 0.65),
])
def test_vol_component_scaling(rank, expected):
    g = _csp(iv_rank=rank)
    assert g["components"]["vol"]["score"] == pytest.approx(expected, abs=0.01)


def test_vol_floor_is_config_derived():
    cfg = {"setup_grade": {"enabled": True, "vol_floor_rank": 55}}
    g = _csp(iv_rank=55, config=cfg)
    assert g["components"]["vol"]["score"] == 0.0


# ── Support / resistance component ──────────────────────────────────────


def test_support_needs_two_touches():
    """A 1-touch cluster is noise (task #31) — never a support component."""
    sr = {"supports": [{"price": 94.0, "touches": 1, "strength": 4.0}],
          "resistances": []}
    g = _csp(support_resistance=sr)
    assert g["components"]["support"]["score"] == 0.0
    assert any("no support under strike" in d for d in g["drivers"])


def test_support_strength_scales_the_component():
    sr = {"supports": [{"price": 94.0, "touches": 2, "strength": 2.0}],
          "resistances": []}
    g = _csp(support_resistance=sr)
    # strength 2.0 / full 4.0 → 0.5
    assert g["components"]["support"]["score"] == pytest.approx(0.5)


def test_support_far_below_strike_zone_does_not_count():
    sr = {"supports": [{"price": 60.0, "touches": 3, "strength": 4.0}],
          "resistances": []}
    g = _csp(support_resistance=sr, strike=95)
    assert g["components"]["support"]["score"] == 0.0


def test_missing_sr_data_is_excluded_not_zero():
    """SR unmeasured (None) → component EXCLUDED + listed n/a — a measured
    empty cluster list is a real zero instead (rule #19 distinction)."""
    g_missing = _csp(support_resistance=None)
    assert "support" in g_missing["missing"]
    assert any(d == "S/R n/a" for d in g_missing["drivers"])
    g_zero = _csp(support_resistance=SR_EMPTY)
    assert "support" not in g_zero["missing"]
    assert g_zero["components"]["support"]["score"] == 0.0


# ── Missing-component renormalization + the B cap ───────────────────────


def test_missing_component_renormalizes_weights():
    """vol missing → remaining weights renormalize: score equals the
    weighted mean over PRESENT components only."""
    g = _csp(rsi=40, iv_rank=None, support_resistance=SR_GOOD_SUPPORT,
             strike=95, spot=100, sma_200=90, days_to_earnings=42,
             day_change_pct=-0.01, drawdown_pct=10)
    assert g["missing"] == ["vol"]
    # rsi 1.0×.30 + support 1.0×.20 + trend 1.0×.15 + context 1.0×.10
    # over total weight .75 → 100
    assert g["score"] == pytest.approx(100.0, abs=0.5)
    assert any(d == "IV rank n/a" for d in g["drivers"])


def test_two_missing_components_cap_the_letter_at_b():
    """Never an A on partial data (rule #19): ≥2 unmeasured components cap
    the letter at B even when the renormalized score clears the A floor."""
    g = _csp(rsi=40, iv_rank=None, support_resistance=SR_GOOD_SUPPORT,
             strike=95, spot=100, sma_200=90, days_to_earnings=None,
             day_change_pct=None, drawdown_pct=None)
    assert set(g["missing"]) == {"vol", "context"}
    assert g["score"] >= 85.0          # raw renormalized score is A-grade
    assert g["letter"] == "B"          # …but the letter is capped
    assert any("capped at B" in d for d in g["drivers"])
    assert "capped at B" in g["message"]


def test_one_missing_component_does_not_cap():
    g = _csp(rsi=40, iv_rank=None)
    assert len(g["missing"]) == 1
    # one missing input never caps by default (max_missing_for_full_grade=1)
    assert g["letter"] in ("A", "A-", "B")


def test_nothing_measurable_fails_closed():
    g = sg.csp_setup(config=CFG_ON)
    assert g["letter"] == "n/a"
    assert g["score"] is None
    assert "insufficient measured data" in g["message"]


# ── Hard-block supremacy ─────────────────────────────────────────────────


def test_csp_rsi_hard_block_zeroes_the_grade():
    """RSI ≥ put.block_above (70) → letter '—', score 0 — a perfect vol/
    support/trend picture can NEVER rescue a hard-blocked side."""
    g = _csp(rsi=72, iv_rank=95)
    assert g["letter"] == "—"
    assert g["score"] == 0.0
    assert g["hard_blocked"] is True
    assert "blocked" in g["message"]
    assert "72" in g["message"]        # measured value, from the assess reason


def test_cc_rsi_hard_block_zeroes_the_grade():
    g = _cc(rsi=30)
    assert g["letter"] == "—"
    assert g["hard_blocked"] is True


def test_custom_block_threshold_flows_from_config():
    cfg = {"setup_grade": {"enabled": True},
           "rsi_discipline": {"put": {"block_above": 65}}}
    g = sg.csp_setup(rsi=67, iv_rank=70, config=cfg)
    assert g["letter"] == "—"


# ── IVr vs RVr labeling ──────────────────────────────────────────────────


def test_chain_iv_source_labels_ivr():
    g = _csp(iv_rank=78, iv_rank_source="chain")
    assert any(d.startswith("IVr 78") for d in g["drivers"])
    assert not any(d.startswith("RVr") for d in g["drivers"])


def test_rv_proxy_source_labels_rvr():
    g = _csp(iv_rank=79, iv_rank_source="rv")
    assert any(d.startswith("RVr 79") for d in g["drivers"])
    assert not any(d.startswith("IVr") for d in g["drivers"])


# ── Message text derives from config values ──────────────────────────────


def test_wait_message_names_weakest_components_with_measured_values():
    """The MU worked example: RSI 52, RVrank 79, no support at strike,
    trend +69% vs 200-SMA, earnings 42d → C, and the wait message names
    the weakest components (support, RSI) with measured now-values and
    config-derived targets."""
    g = sg.csp_setup(rsi=52, iv_rank=79, iv_rank_source="rv",
                     support_resistance=SR_EMPTY, strike=95, spot=100,
                     sma_200=100 / 1.69, days_to_earnings=42, config=CFG_ON)
    assert g["letter"] == "C"
    assert g["score"] == pytest.approx(50.2, abs=0.5)
    assert "wait; prime needs" in g["message"]
    assert "a ≥2-touch support under the strike" in g["message"]
    assert "RSI 35-45 (now 52)" in g["message"]
    assert any("trend ✓ (+69% vs 200-SMA)" == d for d in g["drivers"])
    assert any(d == "earnings 42d ✓" for d in g["drivers"])


def test_wait_message_rsi_target_derives_from_config_band():
    cfg = {"setup_grade": {"enabled": True},
           "rsi_discipline": {"put_entry_band": [30, 50]}}
    g = sg.csp_setup(rsi=49.9, iv_rank=90, iv_rank_source="rv",
                     support_resistance=SR_GOOD_SUPPORT, strike=95,
                     spot=100, config=cfg)
    # RSI is the weakest present component; peak = (30+50)/2 = 40 — the
    # target band renders 30-40, never a hardcoded 35-45.
    assert "RSI 30-40 (now 50)" in g["message"]


def test_wait_message_vol_target_derives_from_floor():
    """Default floor 40 → target 60; floor 55 → target 70 (floor+15)."""
    g = sg.csp_setup(rsi=40, iv_rank=34, iv_rank_source="rv",
                     support_resistance=SR_GOOD_SUPPORT, strike=95,
                     spot=100, sma_200=110, config=CFG_ON)
    assert "RVr ≥ 60 (now 34)" in g["message"]
    cfg = {"setup_grade": {"enabled": True, "vol_floor_rank": 55}}
    g2 = sg.csp_setup(rsi=40, iv_rank=34, iv_rank_source="rv",
                      support_resistance=SR_GOOD_SUPPORT, strike=95,
                      spot=100, sma_200=110, config=cfg)
    assert "RVr ≥ 70 (now 34)" in g2["message"]


def test_wait_message_never_names_a_component_past_its_target():
    """CDNS 2026-08-10 replay bug: 'prime needs … or RVr ≥ 60 (now 63)' —
    a component already past its config target may never be named as the
    wait condition. Only genuinely weak components (score < 1/3) appear."""
    g = sg.csp_setup(rsi=37, iv_rank=63, iv_rank_source="rv",
                     support_resistance=SR_EMPTY, strike=290, spot=300,
                     sma_200=310, days_to_earnings=42, config=CFG_ON)
    assert g["letter"] in ("C", "D")
    assert "RVr ≥" not in g["message"]           # vol (0.38) is not weak
    assert "a ≥2-touch support under the strike" in g["message"]


def test_good_setup_message_says_enter_per_plan():
    g = _csp(rsi=40)
    assert g["letter"] in ("A", "A-", "B")
    assert "Enter per plan" in g["message"]
    assert g["message"].startswith("🏁 Entry:")


# ── Context / trend directionality (rule #44 asymmetry) ─────────────────


def test_red_day_favors_csp_green_day_favors_cc():
    red = _csp(day_change_pct=-0.02)
    green = _csp(day_change_pct=+0.02)
    assert red["components"]["context"]["score"] > \
        green["components"]["context"]["score"]
    cc_green = _cc(day_change_pct=+0.02)
    cc_red = _cc(day_change_pct=-0.02)
    assert cc_green["components"]["context"]["score"] > \
        cc_red["components"]["context"]["score"]


def test_lt_broken_zeroes_trend():
    g = _csp(lt_verdict="broken", sma_200=90)
    assert g["components"]["trend"]["score"] == 0.0
    assert any("LT broken" in d for d in g["drivers"])


def test_earnings_inside_near_window_scores_zero_sub():
    near = _csp(days_to_earnings=5, day_change_pct=None, drawdown_pct=None)
    clear = _csp(days_to_earnings=42, day_change_pct=None, drawdown_pct=None)
    assert near["components"]["context"]["score"] == 0.0
    assert clear["components"]["context"]["score"] == 1.0
    assert any("earnings 5d ✗" == d for d in near["drivers"])


# ── format_grade_note ────────────────────────────────────────────────────


def test_format_grade_note_composes_letter_score_drivers_message():
    g = _csp(rsi=40)
    note = sg.format_grade_note(g)
    assert note.startswith(f"**Setup Grade: {g['letter']}**")
    assert f"({g['score']:.0f}/100)" in note
    assert "🏁 Entry:" in note


def test_format_grade_note_blocked_has_no_score_segment():
    g = _csp(rsi=72)
    note = sg.format_grade_note(g)
    assert "**Setup Grade: —**" in note
    assert "/100" not in note
