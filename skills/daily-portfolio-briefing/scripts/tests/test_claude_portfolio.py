"""Task #45 — Claude/Autopilot portfolio as a second rec source.

Pins: the `🤖 CP-held` chip rides the Parkev-chip pass (appended AFTER the
Parkev chip, never on non-CP names, never double-annotated), and the
rotation-playbook agreement bonus fires ONLY when BOTH Parkev (BUY-variant)
AND the Claude portfolio agree — CP membership alone never adds a point.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.parkev_chip import annotate_parkev_chips  # noqa: E402
from analysis.rotation_playbook import cp_agreement_bonus  # noqa: E402


RECS = {"MU": {"rating_tier": 3, "raw_recommendation": "Buy",
               "conviction": "High", "age_days": 3, "aging": False},
        "SOFI": {"rating_tier": 3, "raw_recommendation": "Buy",
                 "conviction": "Medium", "age_days": 5, "aging": False}}


def test_cp_chip_appended_after_parkev_chip():
    md = "1. **CLOSE** MU_PUT_700_20261218 — take profit"
    out = annotate_parkev_chips(md, RECS, cp_tickers={"MU"})
    assert "🅿️ BUY · 🔥 High · 3d · 🤖 CP-held" in out
    # CP suffix sits AFTER the Parkev chip on the same line
    assert out.index("🅿️") < out.index("🤖 CP-held")


def test_non_cp_ticker_gets_no_cp_chip():
    md = "1. **CLOSE** SOFI_PUT_17_20261218 — take profit"
    out = annotate_parkev_chips(md, RECS, cp_tickers={"MU"})
    assert "🅿️ BUY" in out
    assert "CP-held" not in out


def test_cp_chip_without_cp_set_is_noop():
    md = "1. **CLOSE** MU_PUT_700_20261218 — take profit"
    out = annotate_parkev_chips(md, RECS)
    assert "CP-held" not in out and "🅿️" in out


def test_cp_chip_never_double_annotates():
    md = "1. **CLOSE** MU_PUT_700_20261218 — x  · 🅿️ BUY · 🔥 High · 3d · 🤖 CP-held"
    out = annotate_parkev_chips(md, RECS, cp_tickers={"MU"})
    assert out.count("CP-held") == 1


def test_agreement_bonus_requires_both_sources():
    """+1 ONLY when Parkev BUY-variant AND CP-held agree."""
    cfg = {"claude_portfolio": {"enabled": True, "agreement_bonus": 1}}
    # Both agree → +1 with the flag.
    delta, flag = cp_agreement_bonus("MU", "BUY", {"MU"}, cfg)
    assert delta == 1.0 and "CP agrees" in flag
    # CP-held but Parkev HOLD → nothing (never standalone qualification).
    assert cp_agreement_bonus("MU", "HOLD", {"MU"}, cfg) == (0.0, None)
    # CP-held but NO Parkev rec → nothing.
    assert cp_agreement_bonus("MU", None, {"MU"}, cfg) == (0.0, None)
    # Parkev BUY but not CP-held → nothing.
    assert cp_agreement_bonus("SOFI", "BUY", {"MU"}, cfg) == (0.0, None)
    # Parkev SELL + CP-held → nothing.
    assert cp_agreement_bonus("MU", "SELL", {"MU"}, cfg) == (0.0, None)


def test_agreement_bonus_config_knobs():
    # Disabled block → no bonus even on agreement.
    off = {"claude_portfolio": {"enabled": False, "agreement_bonus": 1}}
    assert cp_agreement_bonus("MU", "BUY", {"MU"}, off) == (0.0, None)
    # Custom bonus size flows through.
    two = {"claude_portfolio": {"agreement_bonus": 2}}
    delta, flag = cp_agreement_bonus("MU", "BUY", {"MU"}, two)
    assert delta == 2.0 and "+2" in flag
    # Missing config → default +1 on agreement (enabled by default).
    delta, _ = cp_agreement_bonus("MU", "BUY", {"MU"}, None)
    assert delta == 1.0
