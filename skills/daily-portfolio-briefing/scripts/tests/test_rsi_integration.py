"""Integration tests: RSI discipline shows up in the actual rendered panels
and the hard gate fires. No network / E*TRADE tokens required — we drive the
render functions directly with synthetic snapshots that carry `technicals`.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from render.panels import render_action_list, render_opportunities  # noqa: E402
from render.strategy_upgrades_panel import render_strategy_upgrades  # noqa: E402
from render.analyst_brief import render_analyst_brief  # noqa: E402
from steps.per_option_commentary import render_watch_with_commentary  # noqa: E402


TECH = {
    "NVDA": {"rsi_14": 75.0},   # overbought → blocks new put-sale
    "MU": {"rsi_14": 41.0},     # pullback → favored for put-sale
    "META": {"rsi_14": 30.0},   # oversold → blocks new covered call
}


def test_opportunities_show_rsi_bullet():
    ideas = [{
        "ticker": "MU", "name": "Micron", "instruction": "SELL_TO_OPEN",
        "type": "PUT", "spot": 120.0, "strike": 110.0, "mid": 2.0, "bid": 1.9,
        "expiration_pretty": "Fri Jun 19 '26", "expiration": "2026-06-19",
        "dte": 30, "otm_pct": 8.0, "delta": 0.25, "yield_pct": 1.8,
        "annualized_pct": 22.0, "collateral": 11000, "premium": 200,
        "open_interest": 500, "spread_pct": 4.0, "iv": 45,
        "rsi_14": 41.0, "rsi_tag": "RSI 41 🟢 pullback",
        "rsi_note": "pullback zone — favourable.",
    }]
    md = "\n".join(render_opportunities(ideas))
    assert "RSI 41 🟢 pullback" in md
    assert "**RSI:**" in md


def test_action_list_pullback_csp_rsi_blocks_overbought():
    """A core name that is overbought must NOT surface a PULLBACK CSP — it
    lands in the transparency footer with an RSI reason instead."""
    config = {"core_positions": ["NVDA"], "accounts": []}
    snap = {
        "balance": {"accountValue": 1_000_000, "cash": 200_000},
        "quotes": {"NVDA": {"last": 200.0}},
        "technicals": TECH,
        "positions": [],
        "earnings_calendar": {},
        "_config": config,
    }
    equity_reviews = [{"ticker": "NVDA", "price": 200.0, "weight": 0.05,
                       "pl_pct": 0.1, "recommendation": "HOLD", "qty": 100}]
    md = "\n".join(render_action_list(equity_reviews, [], [], analytics=None,
                                      snapshot_data=snap,
                                      date_str=date.today().isoformat()))
    # Blocked → appears in footer, NOT as an actionable CSP line.
    # (Label renamed from "PULLBACK CSP" to "CSP — PAID-TO-WAIT", 2026-08-04.)
    assert "CSP — PAID-TO-WAIT NVDA blocked" in md
    assert "overbought" in md
    assert "**CSP — PAID-TO-WAIT** NVDA" not in md  # not an actionable header


def test_action_list_tags_new_csp_header_with_rsi():
    """Every numbered action line gets a side-aware RSI tag via the post-pass."""
    config = {"core_positions": [], "accounts": []}
    snap = {
        "balance": {"accountValue": 1_000_000, "cash": 1000},  # <5000 → skip pullback section
        "quotes": {"MU": {"last": 120.0}},
        "technicals": TECH,
        "positions": [],
        "earnings_calendar": {},
        "_config": config,
    }
    ideas = [{
        "ticker": "MU", "instruction": "SELL_TO_OPEN", "type": "PUT",
        "strike": 110, "mid": 2.0, "expiration_pretty": "Fri Jun 19 '26",
        "expiration": "2026-06-19", "dte": 30, "contracts": 1,
        "rationale": "pullback entry", "delta": 0.25,
    }]
    md = "\n".join(render_action_list([], [], ideas, analytics=None,
                                      snapshot_data=snap,
                                      date_str=date.today().isoformat()))
    assert "**NEW CSP** MU" in md
    assert "RSI 41 🟢 pullback" in md  # post-pass appended to the header


def test_strategy_upgrade_new_cc_removed_to_footer_when_oversold():
    """Per the central hook: an RSI-blocked new CC is REMOVED from the
    actionable list into the 'Held back by RSI' footer (not shown with a
    DEFER badge)."""
    upgrades = [{
        "type": "write_covered_call", "underlying": "META", "shares_held": 100,
        "contracts_writable": 1, "current_price": 600.0, "target_strike": 640.0,
        "target_dte": 35, "est_premium_per_share": 9.0, "est_premium_total": 900,
        "est_annualized_pct": 15.0, "current_weight_pct": 6.0,
        "earnings_blocked": False, "chain_source": "etrade_live",
        "bid": 8.8, "ask": 9.2,
        "rsi_14": 30.0, "rsi_tag": "RSI 30 🔴 oversold",
        "rsi_note": "oversold — capping right before a likely bounce.",
        "rsi_decision": "remove", "rsi_blocked": True,
    }]
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "Held back by RSI" in md
    # META appears in the footer, NOT as an actionable READY TO WRITE
    assert "META" in md.split("Held back by RSI")[1]
    assert "READY TO WRITE" not in md


def test_strategy_upgrade_promotes_favored_cc():
    upgrades = [{
        "type": "write_covered_call", "underlying": "SPY", "shares_held": 100,
        "contracts_writable": 1, "current_price": 600.0, "target_strike": 640.0,
        "target_dte": 35, "est_premium_per_share": 9.0, "est_premium_total": 900,
        "est_annualized_pct": 15.0, "current_weight_pct": 6.0,
        "earnings_blocked": False, "chain_source": "etrade_live",
        "bid": 8.8, "ask": 9.2,
        "rsi_14": 68.0, "rsi_tag": "RSI 68 🟢 extended",
        "rsi_note": "extended — favourable spot to write.",
        "rsi_decision": "promote", "rsi_badge": "✅ RSI favourable", "rsi_blocked": False,
    }]
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "READY TO WRITE" in md
    assert "✅ RSI favourable" in md


def test_strategy_upgrade_midrange_cc_waits_for_strength():
    """A mid-range-RSI new CC (caution zone, e.g. RSI 47) must NOT be headlined
    READY TO WRITE — it drops into the 'wait for strength' section, the same
    discipline that pulls overbought new puts/buys off the actionable list.
    (This is the SOFI RSI-47 case.)"""
    upgrades = [{
        "type": "write_covered_call", "underlying": "SOFI", "shares_held": 700,
        "contracts_writable": 7, "current_price": 16.0, "target_strike": 17.0,
        "target_dte": 31, "est_premium_per_share": 0.68, "est_premium_total": 476,
        "est_annualized_pct": 50.0, "current_weight_pct": 1.0,
        "earnings_blocked": False, "chain_source": "etrade_live",
        "bid": 0.66, "ask": 0.70,
        "rsi_14": 47.0, "rsi_tag": "RSI 47 🟡 mid-range",
        "rsi_note": "mid-range — premium average; prefer waiting for strength.",
        "rsi_decision": "keep", "rsi_badge": "⚠ RSI caution",
        "rsi_blocked": False, "rsi_wait": True,
    }]
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "wait for strength" in md.lower()
    assert "⏸ WAIT FOR STRENGTH" in md
    # SOFI is rendered, but NOT under a READY TO WRITE header.
    assert "SOFI" in md
    assert "READY TO WRITE" not in md
    # The premium / SELL detail is still shown for context.
    assert "SELL 7× SOFI" in md
    # ...and the RSI note explains why it's held.
    assert "RSI 47" in md


def test_strategy_upgrade_favored_cc_stays_ready_not_waiting():
    """RSI-favored (≥60) write stays actionable; no wait-for-strength section."""
    upgrades = [{
        "type": "write_covered_call", "underlying": "SPY", "shares_held": 100,
        "contracts_writable": 1, "current_price": 600.0, "target_strike": 640.0,
        "target_dte": 35, "est_premium_per_share": 9.0, "est_premium_total": 900,
        "est_annualized_pct": 15.0, "current_weight_pct": 6.0,
        "earnings_blocked": False, "chain_source": "etrade_live",
        "bid": 8.8, "ask": 9.2,
        "rsi_14": 68.0, "rsi_tag": "RSI 68 🟢 extended",
        "rsi_note": "extended — favourable spot to write.",
        "rsi_decision": "promote", "rsi_badge": "✅ RSI favourable",
        "rsi_blocked": False, "rsi_wait": False,
    }]
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "READY TO WRITE" in md
    assert "wait for strength" not in md.lower()


def test_cc_renders_real_delta_and_otm():
    """The SELL line shows the measured delta + actual OTM%, not a hardcoded
    '~6% OTM, ~0.30 delta'."""
    upgrades = [{
        "type": "write_covered_call", "underlying": "SPY", "shares_held": 100,
        "contracts_writable": 1, "current_price": 600.0, "target_strike": 645.0,
        "target_dte": 35, "est_premium_per_share": 9.0, "est_premium_total": 900,
        "est_annualized_pct": 15.0, "current_weight_pct": 6.0,
        "earnings_blocked": False, "chain_source": "etrade_live",
        "bid": 8.8, "ask": 9.2,
        "target_delta": 0.24, "otm_pct": 7.5, "strike_selected_by": "delta",
        "rsi_14": 68.0, "rsi_tag": "RSI 68 🟢 extended", "rsi_note": "extended.",
        "rsi_decision": "promote", "rsi_badge": "✅ RSI favourable",
        "rsi_blocked": False, "rsi_wait": False,
    }]
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "7.5% OTM" in md
    assert "δ 0.24" in md
    assert "~0.30 delta" not in md  # the old hardcoded text is gone


def test_cc_renders_delta_na_when_chain_has_no_greeks():
    """When the chain carried no deltas (fallback to %OTM), render 'δ n/a' —
    never a fabricated delta."""
    upgrades = [{
        "type": "write_covered_call", "underlying": "SMH", "shares_held": 100,
        "contracts_writable": 1, "current_price": 595.0, "target_strike": 630.0,
        "target_dte": 35, "est_premium_per_share": 12.0, "est_premium_total": 1200,
        "est_annualized_pct": 20.0, "current_weight_pct": 5.0,
        "earnings_blocked": False, "chain_source": "etrade_live",
        "bid": 11.8, "ask": 12.2,
        "target_delta": None, "otm_pct": 5.9, "strike_selected_by": "otm_pct",
        "rsi_14": 68.0, "rsi_tag": "RSI 68 🟢 extended", "rsi_note": "extended.",
        "rsi_decision": "promote", "rsi_badge": "✅ RSI favourable",
        "rsi_blocked": False, "rsi_wait": False,
    }]
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "δ n/a" in md
    assert "5.9% OTM" in md


def test_strategy_upgrade_sublot_blocked_when_overbought():
    upgrades = [{
        "type": "sublot_completion", "underlying": "XYZ", "shares_held": 50,
        "shares_to_buy": 50, "current_price": 100.0, "cost": 5000,
        "post_buy_weight_pct": 1.0, "rsi_14": 75.0,
        "rsi_tag": "RSI 75 🔴 overbought", "rsi_note": "overbought — don't chase.",
        "rsi_decision": "remove", "rsi_blocked": True,
    }]
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "Held back by RSI" in md
    assert "XYZ" in md.split("Held back by RSI")[1]


def test_watch_panel_shows_rsi_on_positions():
    equity_reviews = [{"ticker": "NVDA", "price": 200.0, "weight": 0.1,
                       "pl_pct": 0.5, "recommendation": "HOLD"}]
    options_reviews = [{
        "contract": "MU_PUT_20260619_110", "underlying": "MU", "type": "PUT",
        "strike": 110, "expiration": "2026-06-19", "days_to_expiry": 30,
        "qty": -1, "entry_price": 2.0, "current_mid": 1.0,
        "recommendation": "HOLD",
    }]
    snap = {"technicals": TECH, "quotes": {}, "earnings_calendar": {}}
    md = "\n".join(render_watch_with_commentary(equity_reviews, options_reviews, snap))
    assert "RSI 75 🔴 overbought" in md   # NVDA equity header
    assert "RSI 41" in md                  # MU option header


def test_analyst_brief_tags_close_winner_with_rsi():
    options_reviews = [{
        "contract": "MU_PUT_20260619_110", "underlying": "MU", "type": "PUT",
        "strike": 110, "expiration": "2026-06-19", "days_to_expiry": 40,
        "qty": -2, "entry_price": 2.0, "current_mid": 0.3,
        "recommendation": "HOLD",
    }]
    snap = {"balance": {"accountValue": 1_000_000, "cash": 50_000},
            "technicals": TECH, "earnings_calendar": {}, "new_ideas": []}
    md = "\n".join(render_analyst_brief([], options_reviews, snap,
                                        {"stress_coverage": {"coverage_ratio": 1.0}},
                                        {"regime": "NORMAL", "confidence": "HIGH"}))
    assert "CLOSE WINNERS" in md
    assert "RSI 41" in md  # MU close-winner header tagged


# ── Bug #26 (2026-07-22) — roll STO legs must carry the underlying's RSI ──
#
# User symptom: "## ⚠️ RSI Coverage Check — 2 recommendation line(s) are
# missing an RSI read: `SELL TO OPEN   1 × IREN ... $47 PUT ...`" — the roll
# ticket's STO leg rendered without RSI even though the parent ROLL header
# carried it (IREN 46 / NOK 36); the verifier's ±3-line window couldn't see
# past the ROLL ANALYSIS table.


def _roll_review_with_ticket(ticker="IREN", rsi=46.0):
    return {
        "contract": f"{ticker}_PUT_50_20260918", "underlying": ticker,
        "type": "PUT", "strike": 50.0, "expiration": "2026-09-18",
        "qty": -1, "entry_price": 10.69, "current_mid": 14.50,
        "days_to_expiry": 58, "recommendation": "HOLD",
        "recommended_candidate_id": "A", "if_rolling_anyway_candidate_id": "C",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "netDollars": 0, "notes": ""},
            {"id": "C", "description": "roll out to Jan '27 $47P",
             "netDollars": 90, "notes": ""},
        ],
        "if_rolling_anyway_ticket": {
            "buy_to_close": {"expiration": "2026-09-18", "strike": 50.0,
                             "limit_price": 14.50, "quantity": 1},
            "sell_to_open": {"expiration": "2027-01-15", "strike": 47.0,
                             "limit_price": 15.40, "quantity": 1},
            "net_dollars_total": 90,
        },
    }


def test_roll_sto_leg_carries_underlying_rsi():
    """The STO leg line itself shows '· RSI 46' (the underlying's measured
    value), and audit_missing_rsi no longer flags the block."""
    from steps.per_option_commentary import render_watch_with_commentary

    snap = {"technicals": {"IREN": {"rsi_14": 46.0}}, "quotes": {},
            "earnings_calendar": {}}
    md = "\n".join(render_watch_with_commentary(
        [], [_roll_review_with_ticket("IREN", 46.0)], snap))
    sto_lines = [ln for ln in md.splitlines() if "SELL TO OPEN" in ln]
    assert sto_lines, "expected the if-rolling-anyway STO leg to render"
    assert all("RSI 46" in ln for ln in sto_lines)
    # The verifier is clean on this block.
    from analysis import rsi_discipline
    assert rsi_discipline.audit_missing_rsi(md) == []


def test_roll_sto_leg_omits_rsi_when_unknown():
    """No measured RSI → no fabricated number on the STO leg (rule #19)."""
    from steps.per_option_commentary import render_watch_with_commentary

    snap = {"technicals": {}, "quotes": {}, "earnings_calendar": {}}
    md = "\n".join(render_watch_with_commentary(
        [], [_roll_review_with_ticket("NOK")], snap))
    sto_lines = [ln for ln in md.splitlines() if "SELL TO OPEN" in ln]
    assert sto_lines
    assert all("RSI" not in ln for ln in sto_lines)
