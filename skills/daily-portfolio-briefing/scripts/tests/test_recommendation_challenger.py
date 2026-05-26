"""Tests for the recommendation challenger (deterministic counterpoints)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import recommendation_challenger as rc  # noqa: E402


def _ctx(**over):
    base = {
        "known_tickers": ["SMH", "GOOG", "SPY", "AMD", "NVDA"],
        "options_by_contract": {},
        "fv_by_ticker": {},
        "spot_by_ticker": {},
        "weights": {},
        "coverage_ratio": None,
        "rsi_by_ticker": {},
    }
    base.update(over)
    return base


def _run(action_lines, ctx):
    lines = ["## Today's Action List — Test", ""] + action_lines + ["", "## Next Section", "done"]
    return rc.challenge_action_list(lines, ctx)


def test_covered_call_roll_flags_cap_and_ceiling():
    block = [
        "1. **EXECUTE ROLL** SMH_CALL_595_20260618 — Calendar roll: +$14,275 net credit (1 spreads @ $142.75/share)",
        "   - **Order:** Combo — Buy-to-Close 1× SMH $595C; Sell-to-Open 1× $595C Fri Dec 15 '28",
        "   - **Why:** adds 911 more days of theta runway",
    ]
    out, panel = _run(block, _ctx(
        options_by_contract={"SMH_CALL_595_20260618": {"type": "CALL", "recommendation": "BUY"}},
    ))
    joined = "\n".join(out + panel)
    assert "caps SMH at $595" in joined
    assert "effective ceiling is ~$738" in joined        # 595 + 142.75
    assert "locks the position for ~911 more days" in joined
    assert "⚖️ **Counterpoint:**" in "\n".join(out)       # inline present


def test_roll_contradicting_hold_is_flagged():
    block = [
        "1. **EXECUTE ROLL** SMH_CALL_595_20260618 — Calendar roll: +$14,275 net credit (1 spreads @ $142.75/share)",
        "   - **Order:** Sell-to-Open 1× $595C Fri Dec 15 '28",
    ]
    out, panel = _run(block, _ctx(
        options_by_contract={"SMH_CALL_595_20260618": {"type": "CALL", "recommendation": "HOLD",
                                                        "recommended_candidate_id": "A"}},
    ))
    joined = "\n".join(out + panel)
    assert "own roll advisor recommends HOLD" in joined
    # consistency objection leads the inline counterpoint
    inline = [l for l in out if "Counterpoint" in l][0]
    assert "HOLD" in inline


def test_close_flags_forgone_theta():
    block = [
        "1. **CLOSE** GOOG_PUT_355_20260612 — +34% ($+119); buy-to-close limit $2.43",
        "   - **Why:** 34% captured with 21d still on the contract. Remaining ~$231 of theta isn't worth it.",
    ]
    out, panel = _run(block, _ctx())
    joined = "\n".join(out + panel)
    assert "34% captured with 21d left" in joined
    assert "theta" in joined


def test_new_csp_flags_commitment_and_valuation():
    block = ["1. **NEW CSP** AMD $135P — premium $900"]
    out, panel = _run(block, _ctx(
        fv_by_ticker={"AMD": {"dcf": 120.0, "analyst_target": 160.0}},
        spot_by_ticker={"AMD": 150.0},
        weights={"AMD": 9.0},
    ))
    joined = "\n".join(out + panel)
    assert "commits you to buying AMD at $135" in joined
    assert "DCF $120" in joined and "analyst target $160" in joined
    assert "already ~9% of NLV" in joined


def test_trim_flags_tax():
    block = ["1. **TRIM** GOOG — raises $65,000 (LTCG ~$15,470)"]
    out, panel = _run(block, _ctx())
    joined = "\n".join(out + panel)
    assert "realizes a taxable gain" in joined
    assert "$15,470" in joined


def test_hedge_flags_cost_and_cheaper_alternative():
    block = ["1. **HEDGE** Buy 13× SPY put $709P (~$9,700; coverage 17% → target 10%)"]
    out, panel = _run(block, _ctx(coverage_ratio=0.17))
    # Inspect the counterpoint TEXT only (not the original action line, which also
    # contains $9,700) — it must cite the COST $9,700, never the strike $709.
    cp_text = "\n".join([l for l in out if "Counterpoint" in l]
                        + [l for l in panel if l.startswith("- ")])
    assert "$9,700" in cp_text
    assert "$709" not in cp_text
    assert "closing winners to free collateral" in "\n".join(panel)


def test_panel_built_and_other_sections_untouched():
    block = ["1. **TRIM** NVDA — raises $50,000 (LTCG ~$11,900)"]
    out, panel = _run(block, _ctx())
    assert any("⚖️ Counterpoints / Second Opinion" in l for l in panel)
    # the "## Next Section" content is unchanged and not annotated
    assert out[-1] == "done"
    assert not any("Counterpoint" in l for l in out if "Next Section" in l)


def test_no_actions_no_panel():
    out, panel = rc.challenge_action_list(["## Health", "- NLV $1M"], _ctx())
    assert panel == []
