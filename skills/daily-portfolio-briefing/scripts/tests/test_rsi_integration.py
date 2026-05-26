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
    # Blocked → appears in footer, NOT as an actionable PULLBACK CSP line.
    assert "PULLBACK CSP NVDA blocked" in md
    assert "overbought" in md
    assert "**PULLBACK CSP** NVDA" not in md  # not an actionable header


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
