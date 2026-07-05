"""Tests for the position tier framework (CLAUDE.md hard rule #29).

Pins:
  - tier_for() returns A/B/C for configured tickers, C default for unknown
  - cc_settings_for_tier() returns the right dict per tier (incl. fallback)
  - is_cc_enabled_for_tier() is False for Tier A, True for B/C
  - concentration_cap_for_tier() respects per-tier caps
  - format_tier_badge() output
  - annotate_tier_badges() appends after the Parkev chip; never double-annotates
  - Backward compat: empty / missing config → every ticker → Tier C (no-op)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import position_tiers as pt  # noqa: E402
from analysis.parkev_chip import annotate_parkev_chips  # noqa: E402


# ─── Sample config matching briefing.yaml defaults ─────────────────────────

_CONFIG = {
    "position_tiers": {
        "tier_a_core": ["NVDA", "GOOG", "MSFT", "META", "PLTR", "AMZN", "SPY", "VOO"],
        "tier_b_income": ["MU", "SMH"],
    },
    "covered_call_tiers": {
        "tier_a": {
            "enabled": False, "rsi_floor": 999, "min_otm_pct": 999,
            "max_delta": 0.0, "coverage_cap_pct": 0, "max_dte": 0,
            "roll_up_trigger": 0.92, "tax_aware_assignment_block": True,
        },
        "tier_b": {
            "enabled": True, "rsi_floor": 70, "min_otm_pct": 10.0,
            "max_delta": 0.15, "coverage_cap_pct": 50, "max_dte": 30,
            "roll_up_trigger": 0.93, "tax_aware_assignment_block": True,
        },
        "tier_c": {
            "enabled": True, "rsi_floor": 60, "min_otm_pct": 4.0,
            "max_delta": 0.30, "coverage_cap_pct": 100, "max_dte": 45,
            "roll_up_trigger": 0.97, "tax_aware_assignment_block": False,
        },
    },
    "concentration_caps": {
        "tier_a_max_pct": 22.0,
        "tier_b_max_pct": 12.0,
        "tier_c_max_pct": 8.0,
        "default_max_pct": 10.0,
    },
}


# ─── tier_for() ───────────────────────────────────────────────────────────


def test_tier_for_returns_a_for_listed_core():
    assert pt.tier_for("NVDA", _CONFIG) == pt.TIER_A
    assert pt.tier_for("GOOG", _CONFIG) == pt.TIER_A
    assert pt.tier_for("MSFT", _CONFIG) == pt.TIER_A
    assert pt.tier_for("AMZN", _CONFIG) == pt.TIER_A
    assert pt.tier_for("META", _CONFIG) == pt.TIER_A
    assert pt.tier_for("PLTR", _CONFIG) == pt.TIER_A
    assert pt.tier_for("SPY", _CONFIG) == pt.TIER_A
    assert pt.tier_for("VOO", _CONFIG) == pt.TIER_A


def test_tier_for_returns_b_for_listed_income():
    assert pt.tier_for("MU", _CONFIG) == pt.TIER_B
    assert pt.tier_for("SMH", _CONFIG) == pt.TIER_B


def test_tier_for_returns_c_for_unconfigured_ticker():
    """A ticker not in any explicit list defaults to Tier C (active wheel) —
    the backward-compatibility guarantee."""
    assert pt.tier_for("VRT", _CONFIG) == pt.TIER_C
    assert pt.tier_for("CRM", _CONFIG) == pt.TIER_C
    assert pt.tier_for("ADBE", _CONFIG) == pt.TIER_C
    assert pt.tier_for("XOM", _CONFIG) == pt.TIER_C


def test_tier_for_is_case_insensitive():
    assert pt.tier_for("nvda", _CONFIG) == pt.TIER_A
    assert pt.tier_for("Mu", _CONFIG) == pt.TIER_B
    assert pt.tier_for("  GOOG  ", _CONFIG) == pt.TIER_A


def test_tier_for_handles_empty_or_none_ticker():
    assert pt.tier_for("", _CONFIG) == pt.TIER_C
    assert pt.tier_for(None, _CONFIG) == pt.TIER_C  # type: ignore[arg-type]


def test_tier_for_with_no_config_defaults_to_c():
    """No `position_tiers` block → every ticker → Tier C (legacy default)."""
    assert pt.tier_for("NVDA", None) == pt.TIER_C
    assert pt.tier_for("MU", {}) == pt.TIER_C
    assert pt.tier_for("ANY", {"position_tiers": {}}) == pt.TIER_C


def test_tier_for_handles_string_in_yaml_list_block():
    """A user accidentally writes `tier_a_core: NVDA` (scalar, not list);
    we tolerate it rather than dropping the ticker."""
    cfg = {"position_tiers": {"tier_a_core": "NVDA"}}
    assert pt.tier_for("NVDA", cfg) == pt.TIER_A


# ─── cc_settings_for_tier() ────────────────────────────────────────────────


def test_cc_settings_tier_a_disabled():
    s = pt.cc_settings_for_tier(pt.TIER_A, _CONFIG)
    assert s["enabled"] is False
    assert s["rsi_floor"] == 999
    assert s["max_delta"] == 0.0
    assert s["coverage_cap_pct"] == 0


def test_cc_settings_tier_b_conservative_envelope():
    s = pt.cc_settings_for_tier(pt.TIER_B, _CONFIG)
    assert s["enabled"] is True
    assert s["rsi_floor"] == 70
    assert s["min_otm_pct"] == 10.0
    assert s["max_delta"] == 0.15
    assert s["coverage_cap_pct"] == 50
    assert s["max_dte"] == 30


def test_cc_settings_tier_c_current_discipline():
    s = pt.cc_settings_for_tier(pt.TIER_C, _CONFIG)
    assert s["enabled"] is True
    assert s["rsi_floor"] == 60
    assert s["min_otm_pct"] == 4.0
    assert s["max_delta"] == 0.30
    assert s["coverage_cap_pct"] == 100
    assert s["max_dte"] == 45


def test_cc_settings_falls_back_to_module_defaults_with_no_config():
    """Missing `covered_call_tiers` → return the canonical defaults."""
    s = pt.cc_settings_for_tier(pt.TIER_A, None)
    assert s["enabled"] is False
    s = pt.cc_settings_for_tier(pt.TIER_B, {})
    assert s["enabled"] is True and s["max_delta"] == 0.15
    s = pt.cc_settings_for_tier(pt.TIER_C, None)
    assert s["enabled"] is True and s["max_delta"] == 0.30


def test_cc_settings_merges_user_overrides_on_top_of_defaults():
    """A user can override one knob (rsi_floor) and leave the rest at default."""
    cfg = {"covered_call_tiers": {"tier_b": {"rsi_floor": 75}}}
    s = pt.cc_settings_for_tier(pt.TIER_B, cfg)
    assert s["rsi_floor"] == 75            # user override wins
    assert s["max_delta"] == 0.15           # default preserved
    assert s["coverage_cap_pct"] == 50      # default preserved


def test_cc_settings_unknown_tier_defaults_to_c_settings():
    s = pt.cc_settings_for_tier("X", _CONFIG)
    assert s["max_delta"] == 0.30   # Tier C default


# ─── is_cc_enabled_for_tier() ──────────────────────────────────────────────


def test_is_cc_enabled_tier_a_false():
    assert pt.is_cc_enabled_for_tier(pt.TIER_A, _CONFIG) is False


def test_is_cc_enabled_tier_b_true():
    assert pt.is_cc_enabled_for_tier(pt.TIER_B, _CONFIG) is True


def test_is_cc_enabled_tier_c_true():
    assert pt.is_cc_enabled_for_tier(pt.TIER_C, _CONFIG) is True


def test_is_cc_enabled_default_is_true_when_no_config():
    """No CC config → default behavior allows writes."""
    assert pt.is_cc_enabled_for_tier(pt.TIER_C, None) is True
    assert pt.is_cc_enabled_for_tier(pt.TIER_B, {}) is True
    # Tier A's fallback IS disabled even without user config (the framework
    # default is to not write CCs on Tier A regardless).
    assert pt.is_cc_enabled_for_tier(pt.TIER_A, None) is False


# ─── concentration_cap_for_tier() ──────────────────────────────────────────


def test_concentration_cap_tier_a_raised_to_22():
    assert pt.concentration_cap_for_tier(pt.TIER_A, _CONFIG) == 22.0


def test_concentration_cap_tier_b_at_12():
    assert pt.concentration_cap_for_tier(pt.TIER_B, _CONFIG) == 12.0


def test_concentration_cap_tier_c_at_8():
    assert pt.concentration_cap_for_tier(pt.TIER_C, _CONFIG) == 8.0


def test_concentration_cap_falls_back_to_defaults_with_no_config():
    assert pt.concentration_cap_for_tier(pt.TIER_A, None) == 22.0
    assert pt.concentration_cap_for_tier(pt.TIER_B, None) == 12.0
    assert pt.concentration_cap_for_tier(pt.TIER_C, None) == 8.0


def test_concentration_cap_honors_default_max_pct_when_per_tier_missing():
    cfg = {"concentration_caps": {"default_max_pct": 15.0}}
    # No tier_a_max_pct in cfg → falls through to default_max_pct → 15
    assert pt.concentration_cap_for_tier(pt.TIER_A, cfg) == 15.0


# ─── format_tier_badge() ───────────────────────────────────────────────────


def test_format_tier_badge_a_is_green():
    assert pt.format_tier_badge(pt.TIER_A) == "🟢 Tier A"


def test_format_tier_badge_b_is_yellow():
    assert pt.format_tier_badge(pt.TIER_B) == "🟡 Tier B"


def test_format_tier_badge_c_is_blue():
    assert pt.format_tier_badge(pt.TIER_C) == "🔵 Tier C"


def test_format_tier_badge_unknown_defaults_to_c():
    assert pt.format_tier_badge("X") == "🔵 Tier C"
    assert pt.format_tier_badge("") == "🔵 Tier C"
    assert pt.format_tier_badge(None) == "🔵 Tier C"  # type: ignore[arg-type]


# ─── annotate_tier_badges() ────────────────────────────────────────────────


def _line_with_chip(line: str) -> str:
    """Add a Parkev chip to a line the way aggregate.py does — so the tier
    annotator has something to attach to."""
    recs = {
        "NVDA": {"rating_tier": 4, "raw_recommendation": "Top 12 Stock",
                 "conviction": "High", "age_days": 3, "aging": False},
        "MU":   {"rating_tier": 3, "raw_recommendation": "Buy",
                 "conviction": "Medium", "age_days": 5, "aging": False},
        "CRM":  {"rating_tier": 3, "raw_recommendation": "Buy",
                 "conviction": "Medium", "age_days": 8, "aging": False},
    }
    return annotate_parkev_chips(line, recs)


def test_annotate_tier_badge_appends_a_for_nvda_after_parkev_chip():
    chipped = _line_with_chip("- **NVDA** @ $190 — 7.5% → **HOLD**")
    assert "🅿️" in chipped
    annotated = pt.annotate_tier_badges(chipped, _CONFIG)
    assert "🟢 Tier A" in annotated


def test_annotate_tier_badge_appends_b_for_mu():
    chipped = _line_with_chip("- **MU** @ $120 — 3.0% → **HOLD**")
    annotated = pt.annotate_tier_badges(chipped, _CONFIG)
    assert "🟡 Tier B" in annotated


def test_annotate_tier_badge_appends_c_for_unconfigured_ticker():
    chipped = _line_with_chip("- **CRM** @ $250 — 2.0% → **HOLD**")
    annotated = pt.annotate_tier_badges(chipped, _CONFIG)
    assert "🔵 Tier C" in annotated


def test_annotate_tier_badge_skips_lines_without_parkev_chip():
    """Prose / sub-bullets without a Parkev chip stay untouched."""
    md = "Some explanatory paragraph mentioning NVDA without a chip header."
    annotated = pt.annotate_tier_badges(md, _CONFIG)
    assert "🟢 Tier A" not in annotated
    assert annotated == md  # no-op


def test_annotate_tier_badge_never_double_annotates():
    """Running the annotator twice doesn't add a second badge."""
    chipped = _line_with_chip("- **NVDA** @ $190 — 7.5% → **HOLD**")
    once = pt.annotate_tier_badges(chipped, _CONFIG)
    twice = pt.annotate_tier_badges(once, _CONFIG)
    assert once == twice
    assert once.count("🟢 Tier A") == 1


def test_annotate_tier_badge_no_config_is_a_noop():
    """Backward-compatibility guarantee — when the briefing config has no
    `position_tiers` block, the tier annotator is a NO-OP. The briefing
    renders byte-identical to the pre-framework baseline. Tagging every
    line with a default `🔵 Tier C` would be noisy without conveying any
    real information, and would break the "byte-identical baseline" promise
    for users who haven't opted in to the framework."""
    chipped = _line_with_chip("- **NVDA** @ $190 — 7.5% → **HOLD**")
    annotated = pt.annotate_tier_badges(chipped, None)
    # No tier badge should be appended — annotator is a no-op
    assert "🔵 Tier C" not in annotated
    assert "🟢 Tier A" not in annotated
    assert "🟡 Tier B" not in annotated
    assert annotated == chipped  # truly byte-identical

    # Same guarantee when config is present but `position_tiers` block is empty
    annotated_empty = pt.annotate_tier_badges(chipped, {"position_tiers": {}})
    assert annotated_empty == chipped

    # And when config dict exists but doesn't contain `position_tiers` at all
    annotated_other = pt.annotate_tier_badges(chipped, {"other_key": "value"})
    assert annotated_other == chipped


def test_annotate_tier_badge_empty_input_returns_empty():
    assert pt.annotate_tier_badges("", _CONFIG) == ""
    assert pt.annotate_tier_badges(None, _CONFIG) is None  # type: ignore[arg-type]


def test_annotate_tier_badge_multi_line_handles_mixed_tiers():
    md = "\n".join([
        _line_with_chip("- **NVDA** @ $190 — 7.5% → **HOLD**"),
        _line_with_chip("- **MU** @ $120 — 3.0% → **HOLD**"),
        _line_with_chip("- **CRM** @ $250 — 2.0% → **HOLD**"),
        "Plain prose line",
    ])
    annotated = pt.annotate_tier_badges(md, _CONFIG)
    assert "🟢 Tier A" in annotated  # NVDA
    assert "🟡 Tier B" in annotated  # MU
    assert "🔵 Tier C" in annotated  # CRM
    # Plain prose stays untouched
    assert "Plain prose line\n" in annotated + "\n"


# ─── Tier A conservative-CC override (task #28) ────────────────────────────


def test_tier_a_cc_disabled_by_default():
    """Default: Tier A rejects CCs regardless of ticker."""
    from analysis.position_tiers import is_cc_enabled_for_tier
    cfg = {
        "covered_call_tiers": {
            "tier_a": {"enabled": False, "willing_to_write_cc_on": []},
        },
    }
    assert is_cc_enabled_for_tier("A", cfg, ticker="NVDA") is False
    assert is_cc_enabled_for_tier("A", cfg, ticker="MSFT") is False


def test_tier_a_cc_opt_in_per_name():
    """When ticker is on the willing list, CC is enabled for that name."""
    from analysis.position_tiers import is_cc_enabled_for_tier
    cfg = {
        "covered_call_tiers": {
            "tier_a": {
                "enabled": False,
                "willing_to_write_cc_on": ["NVDA", "MSFT"],
            },
        },
    }
    assert is_cc_enabled_for_tier("A", cfg, ticker="NVDA") is True
    assert is_cc_enabled_for_tier("A", cfg, ticker="MSFT") is True
    # Case-insensitive match
    assert is_cc_enabled_for_tier("A", cfg, ticker="nvda") is True
    # Not on the list → still disabled
    assert is_cc_enabled_for_tier("A", cfg, ticker="GOOG") is False


def test_tier_a_opt_in_ignored_when_no_ticker_passed():
    """Backward compat: existing callers passing only (tier, config) still
    see Tier A as disabled (opt-in requires ticker context)."""
    from analysis.position_tiers import is_cc_enabled_for_tier
    cfg = {
        "covered_call_tiers": {
            "tier_a": {
                "enabled": False,
                "willing_to_write_cc_on": ["NVDA"],
            },
        },
    }
    assert is_cc_enabled_for_tier("A", cfg) is False
    assert is_cc_enabled_for_tier("A", cfg, ticker=None) is False


def test_tier_b_c_ignore_ticker_override():
    """Ticker override is Tier A only. For B/C, the enabled flag drives it."""
    from analysis.position_tiers import is_cc_enabled_for_tier
    cfg = {
        "covered_call_tiers": {
            "tier_b": {"enabled": True, "willing_to_write_cc_on": []},
            "tier_c": {"enabled": True},
        },
    }
    assert is_cc_enabled_for_tier("B", cfg, ticker="ANY") is True
    assert is_cc_enabled_for_tier("C", cfg, ticker="ANY") is True


# ─── Tier A engineered CC (task #47) ───────────────────────────────────────


def _tier_a_config(enabled=True, whitelist=("MSFT", "META")):
    return {
        "covered_call_tiers": {
            "tier_a": {
                "enabled": False,
                "willing_to_write_cc_on": list(whitelist),
                "engineered": {
                    "enabled": enabled,
                    "rsi_floor": 40,
                    "min_drawdown_pct": 10.0,
                    "min_iv_rank": 70,
                    "min_otm_pct": 13.0,
                    "max_delta": 0.15,
                    "max_dte": 21,
                    "coverage_cap_pct": 20,
                    "require_rebound_proof": True,
                },
            },
        },
    }


def test_engineered_cc_msft_today_fires():
    """Real 2026-07-01 data: MSFT spot $376.88, RSI 43, IV 89, DD 30%,
    200-SMA $444, analyst PT $537 → engineered CC MUST fire.

    With default `min_strike_above_analyst_pt=false`, min strike is
    the 200-SMA floor (~$445 rounded up). analyst_PT is a 12-month
    target and would push strike to $540 (basically pennies premium)
    — used only as an ultra-conservative opt-in."""
    from analysis.position_tiers import engineered_cc_eligible
    cfg = _tier_a_config()
    ok, env, reasons = engineered_cc_eligible(
        "MSFT", cfg, spot=376.88, rsi=43, iv_rank=89,
        drawdown_pct=30.0, sma_200=444.36, analyst_pt=536.88,
    )
    assert ok is True, f"Expected fire, got reasons: {reasons}"
    assert env["applies"] is True
    # Rebound-proof: strike ≥ max(spot×1.13, sma_200) = max(425.87, 444.36) → $445
    assert 445 <= env["min_strike"] <= 465, (
        f"expected ~$445-465, got ${env['min_strike']}"
    )
    assert env["above_analyst_pt"] is False   # $445 < $537
    assert env["max_delta"] == 0.15
    assert env["max_dte"] == 21


def test_engineered_cc_ultra_conservative_mode_pushes_above_analyst_pt():
    """Setting min_strike_above_analyst_pt=true forces strike above the
    analyst PT — pushes to 35-45% OTM. Premium collapses; use only when
    you want maximum tail-risk protection."""
    from analysis.position_tiers import engineered_cc_eligible
    cfg = _tier_a_config()
    cfg["covered_call_tiers"]["tier_a"]["engineered"]["min_strike_above_analyst_pt"] = True
    ok, env, _ = engineered_cc_eligible(
        "MSFT", cfg, spot=376.88, rsi=43, iv_rank=89,
        drawdown_pct=30, sma_200=444, analyst_pt=537,
    )
    assert ok is True
    assert env["min_strike"] >= 540  # rounded up from analyst PT $537
    assert env["above_analyst_pt"] is True


def test_engineered_cc_disabled_when_not_on_whitelist():
    """Even with engineered mode ON, only whitelisted names fire."""
    from analysis.position_tiers import engineered_cc_eligible
    cfg = _tier_a_config(whitelist=("MSFT",))   # META NOT on list
    ok, env, reasons = engineered_cc_eligible(
        "META", cfg, spot=600, rsi=55, iv_rank=87,
        drawdown_pct=24, sma_200=646, analyst_pt=767,
    )
    assert ok is False
    assert any("whitelist" in r for r in reasons)


def test_engineered_cc_blocks_when_rsi_below_floor():
    """RSI 30 = below the 40 floor (falling knife) → block."""
    from analysis.position_tiers import engineered_cc_eligible
    cfg = _tier_a_config()
    ok, _, reasons = engineered_cc_eligible(
        "MSFT", cfg, spot=376, rsi=30, iv_rank=89,
        drawdown_pct=30, sma_200=444, analyst_pt=537,
    )
    assert ok is False
    assert any("RSI" in r for r in reasons)


def test_engineered_cc_blocks_when_iv_rank_below_floor():
    """IV rank 50 = below 70 = premium isn't rich enough → block."""
    from analysis.position_tiers import engineered_cc_eligible
    cfg = _tier_a_config()
    ok, _, reasons = engineered_cc_eligible(
        "MSFT", cfg, spot=376, rsi=45, iv_rank=50,
        drawdown_pct=30, sma_200=444, analyst_pt=537,
    )
    assert ok is False
    assert any("IV rank" in r for r in reasons)


def test_engineered_cc_blocks_when_no_drawdown():
    """Stock at ATH (0% drawdown) → no rebound premium → block."""
    from analysis.position_tiers import engineered_cc_eligible
    cfg = _tier_a_config()
    ok, _, reasons = engineered_cc_eligible(
        "MSFT", cfg, spot=376, rsi=45, iv_rank=89,
        drawdown_pct=2, sma_200=350, analyst_pt=400,
    )
    assert ok is False
    assert any("drawdown" in r.lower() for r in reasons)


def test_engineered_cc_disabled_by_default():
    """Backward-compat: config without an `engineered` block → no fires."""
    from analysis.position_tiers import engineered_cc_eligible
    cfg = {"covered_call_tiers": {"tier_a": {"enabled": False,
                                              "willing_to_write_cc_on": ["MSFT"]}}}
    ok, _, reasons = engineered_cc_eligible(
        "MSFT", cfg, spot=376, rsi=45, iv_rank=89,
        drawdown_pct=30, sma_200=444, analyst_pt=537,
    )
    assert ok is False
    assert any("engineered" in r.lower() for r in reasons)


def test_engineered_cc_strike_rebound_proof_math():
    """Verifies min_strike math (default mode): max of (spot × (1+min_otm_pct),
    sma_200), rounded UP to nearest $5. Analyst PT is only surfaced as a
    bonus 'above_analyst_pt' flag, not enforced as a strike floor."""
    from analysis.position_tiers import engineered_cc_eligible
    cfg = _tier_a_config()
    # Case: 200-SMA dominates baseline
    ok, env, _ = engineered_cc_eligible(
        "MSFT", cfg, spot=376.88, rsi=45, iv_rank=89,
        drawdown_pct=30, sma_200=444, analyst_pt=537,
    )
    assert ok is True
    # 200-SMA $444 > baseline $425.87 → floor at $445
    assert 445 <= env["min_strike"] <= 465
    assert env["above_analyst_pt"] is False   # $445 < $537
    # Case: baseline dominates (spot ABOVE 200-SMA → SMA gate irrelevant)
    cfg2 = _tier_a_config(whitelist=("GOOG",))
    ok, env, _ = engineered_cc_eligible(
        "GOOG", cfg2, spot=355, rsi=45, iv_rank=80,
        drawdown_pct=15, sma_200=315, analyst_pt=None,
    )
    assert ok is True
    # sma_200 $315 < spot $355 (skipped); baseline = 355 × 1.13 = $401.15 → $405
    assert env["min_strike"] >= 400
