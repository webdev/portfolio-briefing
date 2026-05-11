"""Tests for capital-planner."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from plan import (
    CapitalAction,
    CapitalPlan,
    build_capital_plan,
    format_capital_plan_md,
    _parse_yield_or_cost,
    _weights_by_ticker,
)


def _balance(cash=150_000, nlv=1_000_000):
    return {"cash": cash, "accountValue": nlv}


def _positions(weights_pct: dict[str, float], nlv=1_000_000):
    """Build a positions list that yields the requested weight percentages."""
    out = []
    for sym, pct in weights_pct.items():
        mv = nlv * pct / 100.0
        # Use price=100 so qty = mv/100
        out.append({"assetType": "EQUITY", "symbol": sym, "qty": mv / 100, "price": 100})
    return out


# ---------------------------------------------------------------------------
# Cash-flow math
# ---------------------------------------------------------------------------

def test_close_at_30pct_capture_promotes_to_tier_1():
    plan = build_capital_plan(
        balance=_balance(),
        positions=[],
        options_reviews=[
            {"action": "CLOSE", "contract": "GOOG_PUT_355_20260612",
             "underlying": "GOOG", "profit_pct": 0.31, "btc_cost": 255,
             "collateral": 35_500, "profit_dollars": 107, "dte": 34},
        ],
    )
    assert len(plan.actions) == 1
    a = plan.actions[0]
    assert a.tier == 1
    assert a.kind == "CLOSE"
    assert a.cash_in == 35_500
    assert a.cash_out == 255
    assert a.net_cash == 35_245


def test_close_under_30pct_stays_tier_2():
    plan = build_capital_plan(
        balance=_balance(),
        positions=[],
        options_reviews=[
            {"action": "CLOSE", "contract": "FOO", "profit_pct": 0.20,
             "btc_cost": 100, "collateral": 5_000, "profit_dollars": 25, "dte": 60},
        ],
    )
    assert plan.actions[0].tier == 2


def test_credit_roll_promotes_to_tier_2():
    plan = build_capital_plan(
        balance=_balance(),
        positions=[],
        options_reviews=[
            {"action": "EXECUTE_ROLL", "contract": "VRT_CALL_360",
             "underlying": "VRT", "net_credit": 1_128},
        ],
    )
    a = plan.actions[0]
    assert a.kind == "ROLL"
    assert a.tier == 2
    assert a.cash_in == 1_128


def test_debit_roll_stays_tier_3():
    plan = build_capital_plan(
        balance=_balance(),
        positions=[],
        options_reviews=[
            {"action": "EXECUTE_ROLL", "contract": "MSFT_CALL_450",
             "underlying": "MSFT", "net_debit": 2_360},
        ],
    )
    a = plan.actions[0]
    assert a.tier == 3
    assert a.cash_out == 2_360


def test_roll_with_imminent_earnings_defers_to_tier_4():
    plan = build_capital_plan(
        balance=_balance(),
        positions=[],
        options_reviews=[
            {"action": "EXECUTE_ROLL", "contract": "NVDA_CALL_245",
             "underlying": "NVDA", "net_debit": 6_671, "days_to_earnings": 11},
        ],
    )
    # Skipped — should land in skipped_actions, not actions
    assert len(plan.actions) == 0
    assert len(plan.skipped_actions) == 1
    a = plan.skipped_actions[0]
    assert a.tier == 4
    assert "earnings in 11d" in a.skip_reason


def test_hedge_critical_when_coverage_red():
    plan = build_capital_plan(
        balance=_balance(),
        positions=[],
        hedge_recs=[{"ticker": "SPY", "cost": 13_277, "description": "HEDGE SPY $700P"}],
        coverage_ratio=0.48,
    )
    a = plan.actions[0]
    assert a.tier == 1
    assert "0.48" in a.tier_reason


def test_hedge_optional_when_coverage_green():
    plan = build_capital_plan(
        balance=_balance(),
        positions=[],
        hedge_recs=[{"ticker": "SPY", "cost": 5_000}],
        coverage_ratio=0.85,
    )
    a = plan.actions[0]
    assert a.tier == 3


# ---------------------------------------------------------------------------
# Concentration filtering for long-term CSPs
# ---------------------------------------------------------------------------

def test_lt_csp_skipped_when_over_concentration_cap():
    plan = build_capital_plan(
        balance=_balance(),
        positions=_positions({"GOOG": 14.8}),
        long_term_opportunities=[
            {"kind": "LONG_DATED_CSP", "ticker": "GOOG",
             "concrete_trade": "SELL 1× GOOG $355P ~75 DTE",
             "yield_or_cost": "~$993 premium · ~14% annualized · $35,500 cash collateral"},
        ],
        third_party_recs={"GOOG": "BUY"},
    )
    assert len(plan.actions) == 0
    skipped = plan.skipped_actions[0]
    assert skipped.kind == "LT_CSP"
    assert "over" in skipped.skip_reason and "cap" in skipped.skip_reason


def test_lt_csp_skipped_when_near_cap():
    plan = build_capital_plan(
        balance=_balance(),
        positions=_positions({"MSFT": 8.9}),
        long_term_opportunities=[
            {"kind": "LONG_DATED_CSP", "ticker": "MSFT",
             "yield_or_cost": "~$1038 premium · ~13% annualized · $37,500 cash collateral"},
        ],
        third_party_recs={"MSFT": "BUY"},
    )
    assert plan.skipped_actions[0].skip_reason.startswith("already 8.9% NLV")


def test_lt_csp_skipped_when_only_hold_rec():
    plan = build_capital_plan(
        balance=_balance(),
        positions=_positions({"AMD": 0.0}),
        long_term_opportunities=[
            {"kind": "LONG_DATED_CSP", "ticker": "AMD",
             "yield_or_cost": "~$1138 premium · $41,000 cash collateral"},
        ],
        third_party_recs={"AMD": "HOLD"},
    )
    assert plan.skipped_actions[0].skip_reason.startswith("third-party rec is HOLD")


def test_lt_csp_kept_when_room_and_buy_rec():
    plan = build_capital_plan(
        balance=_balance(),
        positions=_positions({"META": 2.5}),
        long_term_opportunities=[
            {"kind": "LONG_DATED_CSP", "ticker": "META",
             "yield_or_cost": "~$1524 premium · ~13% annualized · $55,000 cash collateral"},
        ],
        third_party_recs={"META": "STRONG_BUY"},
    )
    assert len(plan.actions) == 1
    a = plan.actions[0]
    assert a.tier == 3
    assert a.cash_in == 1_524    # premium
    assert a.cash_out == 55_000  # collateral
    assert a.new_collateral_locked == 55_000


# ---------------------------------------------------------------------------
# Trim handling
# ---------------------------------------------------------------------------

def test_trim_severe_concentration_promotes_to_tier_1():
    """13.5% NLV is over the 12% severe threshold → must be tier 1."""
    plan = build_capital_plan(
        balance=_balance(),
        positions=_positions({"NVDA": 13.5}),
        equity_reviews=[
            {"action": "TRIM", "ticker": "NVDA", "trim_dollar_amount": 50_044, "weight_pct": 13.5},
        ],
    )
    a = plan.actions[0]
    assert a.tier == 1
    assert "severe" in a.tier_reason.lower()


def test_trim_modest_concentration_stays_tier_2():
    plan = build_capital_plan(
        balance=_balance(),
        positions=_positions({"PLTR": 10.5}),
        equity_reviews=[
            {"action": "TRIM", "ticker": "PLTR", "trim_dollar_amount": 5_000, "weight_pct": 10.5},
        ],
    )
    assert plan.actions[0].tier == 2


def test_trim_includes_ltcg_estimate():
    plan = build_capital_plan(
        balance=_balance(),
        positions=_positions({"GOOG": 14.8}),
        equity_reviews=[
            {"action": "TRIM", "ticker": "GOOG", "trim_dollar_amount": 64_446, "weight_pct": 14.8},
        ],
    )
    # 64,446 * 0.238 = 15,338.148
    assert abs(plan.tax_estimate_ltcg - 64_446 * 0.238) < 0.5


# ---------------------------------------------------------------------------
# yield_or_cost parsing
# ---------------------------------------------------------------------------

def test_parse_yield_or_cost_typical():
    p, c = _parse_yield_or_cost(
        "~$1524 premium · ~13% annualized · $55,000 cash collateral"
    )
    assert p == 1524
    assert c == 55000


def test_parse_yield_or_cost_with_K_suffix():
    p, c = _parse_yield_or_cost(
        "~$987 per contract — leverages ~$5K of capital into $55,000 of exposure"
    )
    assert p == 987
    # No "collateral" keyword → c == 0; that's the caller's signal it's a LEAP.
    assert c == 0


def test_parse_yield_or_cost_empty():
    p, c = _parse_yield_or_cost("")
    assert (p, c) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# weights_by_ticker
# ---------------------------------------------------------------------------

def test_weights_by_ticker_aggregates_across_accounts():
    positions = [
        {"assetType": "EQUITY", "symbol": "GOOG", "qty": 100, "price": 400},
        {"assetType": "EQUITY", "symbol": "GOOG", "qty": 50,  "price": 400},
        {"assetType": "OPTION", "symbol": "GOOG_PUT_355", "qty": 1},
    ]
    weights = _weights_by_ticker(positions, nlv=1_000_000)
    assert weights["GOOG"] == 6.0  # (100+50)*400 / 1M = 6%
    assert "GOOG_PUT_355" not in weights  # options excluded


# ---------------------------------------------------------------------------
# End-to-end realistic scenario
# ---------------------------------------------------------------------------

def test_full_briefing_scenario():
    """Mimic the real 2026-05-09 briefing's recommendation set."""
    plan = build_capital_plan(
        balance={"cash": 152_979, "accountValue": 1_117_998},
        positions=_positions({
            "GOOG": 14.8, "NVDA": 13.5, "MSFT": 8.9, "META": 2.5,
            "MU": 3.0, "SOFI": 1.0, "LULU": 0.0, "QCOM": 0.0, "AMD": 0.0,
        }, nlv=1_117_998),
        equity_reviews=[
            {"action": "TRIM", "ticker": "GOOG", "trim_dollar_amount": 64_446, "weight_pct": 14.8},
            {"action": "TRIM", "ticker": "NVDA", "trim_dollar_amount": 50_044, "weight_pct": 13.5},
        ],
        options_reviews=[
            {"action": "CLOSE", "contract": "GOOG_PUT_355_20260612", "underlying": "GOOG",
             "profit_pct": 0.31, "btc_cost": 255, "collateral": 35_500, "profit_dollars": 107, "dte": 34},
            {"action": "CLOSE", "contract": "MU_PUT_605_20260626", "underlying": "MU",
             "profit_pct": 0.40, "btc_cost": 3_684, "collateral": 60_500, "profit_dollars": 2_292, "dte": 48},
            {"action": "CLOSE", "contract": "QCOM_PUT_182_20260605", "underlying": "QCOM",
             "profit_pct": 0.31, "btc_cost": 406, "collateral": 18_250, "profit_dollars": 177, "dte": 27},
            {"action": "EXECUTE_ROLL", "contract": "MSFT_CALL_450", "underlying": "MSFT",
             "net_debit": 2_360},
            {"action": "EXECUTE_ROLL", "contract": "NVDA_CALL_245", "underlying": "NVDA",
             "net_debit": 6_671, "days_to_earnings": 11},
            {"action": "EXECUTE_ROLL", "contract": "VRT_CALL_360", "underlying": "VRT",
             "net_credit": 1_128},
        ],
        new_ideas=[
            {"ticker": "VRT", "strike": 300, "premium": 975, "collateral": 30_000,
             "validator_verdict": "GOOD", "ev": 675},
        ],
        long_term_opportunities=[
            {"kind": "EXIT", "ticker": "TSLA", "concrete_trade": "SELL TSLA — exit position"},
            {"kind": "LONG_DATED_CSP", "ticker": "META",
             "yield_or_cost": "~$1524 premium · ~13% annualized · $55,000 cash collateral"},
            {"kind": "LONG_DATED_CSP", "ticker": "GOOG",
             "yield_or_cost": "~$993 premium · ~14% annualized · $35,500 cash collateral"},
            {"kind": "LONG_DATED_CSP", "ticker": "MSFT",
             "yield_or_cost": "~$1038 premium · ~13% annualized · $37,500 cash collateral"},
            {"kind": "LONG_DATED_CSP", "ticker": "AMD",
             "yield_or_cost": "~$1138 premium · ~14% annualized · $41,000 cash collateral"},
            {"kind": "LONG_DATED_CSP", "ticker": "MU",
             "yield_or_cost": "~$1867 premium · ~14% annualized · $67,000 cash collateral"},
        ],
        hedge_recs=[{"ticker": "SPY", "cost": 13_277, "description": "HEDGE SPY $700P"}],
        coverage_ratio=0.48,
        third_party_recs={"GOOG": "BUY", "META": "STRONG_BUY", "MSFT": "BUY",
                          "AMD": "HOLD", "MU": "BUY", "TSLA": "SELL"},
    )

    by_tier = plan.actions_by_tier()
    # Tier 1 should contain: HEDGE (red coverage), the 3 closes, GOOG TRIM, NVDA TRIM, TSLA EXIT
    t1_kinds = sorted(a.kind for a in by_tier[1])
    assert t1_kinds.count("HEDGE") == 1
    assert t1_kinds.count("CLOSE") == 3
    assert t1_kinds.count("TRIM") == 2
    assert "LT_EXIT" in t1_kinds

    # Tier 2: VRT credit roll, NEW_CSP VRT (GOOD verdict)
    t2_kinds = [a.kind for a in by_tier[2]]
    assert "ROLL" in t2_kinds
    assert "NEW_CSP" in t2_kinds

    # Tier 3: MSFT debit roll, META LT_CSP, MU LT_CSP
    t3_tickers = sorted(a.ticker for a in by_tier[3])
    assert "META" in t3_tickers
    assert "MU" in t3_tickers
    assert "MSFT" in t3_tickers  # debit roll

    # Skipped: GOOG LT_CSP (over cap), MSFT LT_CSP (near cap), AMD LT_CSP (HOLD), NVDA roll (earnings)
    skipped_summary = [(a.kind, a.ticker, a.skip_reason) for a in plan.skipped_actions]
    assert any(k == "LT_CSP" and t == "GOOG" for k, t, _ in skipped_summary)
    assert any(k == "LT_CSP" and t == "MSFT" for k, t, _ in skipped_summary)
    assert any(k == "LT_CSP" and t == "AMD"  for k, t, _ in skipped_summary)
    assert any(k == "ROLL" and t == "NVDA"   for k, t, _ in skipped_summary)

    # Cash flow sanity check — closes free $114,250 + trims $114,490 = ≥$228K
    assert plan.total_collateral_freed >= 228_000

    # Net cash including all LT_CSPs that pass filters:
    #   closes:      +$109,437  (114,250 freed - 4,813 BTC)
    #   trims:       +$114,490  (sells GOOG + NVDA shares)
    #   rolls:       −$1,232    (MSFT -$2,360 + VRT +$1,128, NVDA skipped)
    #   hedge:       −$13,277
    #   new CSP VRT: −$29,025   (collat $30k − premium $975)
    #   LT_CSP META: −$53,476   (collat $55k − premium $1,524)
    #   LT_CSP MU:   −$65,133   (collat $67k − premium $1,867)
    # = +$61,784 (positive — closes + trims pay for LT CSPs)
    assert plan.net_cash_change > 60_000
    assert plan.net_cash_change < 70_000


def test_format_capital_plan_md_renders_all_sections():
    plan = CapitalPlan(starting_cash=152_979, nlv=1_117_998)
    plan.actions = [
        CapitalAction(kind="HEDGE", ticker="SPY", description="HEDGE SPY",
                       cash_out=13_277, tier=1, tier_reason="red coverage"),
        CapitalAction(kind="CLOSE", ticker="GOOG", description="CLOSE GOOG",
                       cash_in=35_500, cash_out=255, tier=1, tier_reason=">30% capture"),
        CapitalAction(kind="ROLL", ticker="VRT", description="ROLL VRT",
                       cash_in=1_128, tier=2, tier_reason="credit roll"),
        CapitalAction(kind="LT_CSP", ticker="META", description="LT CSP META",
                       cash_in=1_524, cash_out=55_000, new_collateral_locked=55_000,
                       tier=3, tier_reason="long-term income"),
    ]
    plan.skipped_actions = [
        CapitalAction(kind="LT_CSP", ticker="GOOG", description="LT CSP GOOG",
                       tier=4, skip_reason="already 14.8% NLV — over 10% cap"),
    ]
    plan.total_collateral_freed = 35_500
    plan.total_premium_received = 1_524
    plan.total_collateral_locked = 55_000

    md = format_capital_plan_md(plan)
    txt = "\n".join(md)
    assert "💰 Capital Plan" in txt
    assert "Tier 1 — CRITICAL" in txt
    assert "Tier 2 — IMPORTANT" in txt
    assert "Tier 3 — OPTIONAL" in txt
    assert "Skipped — see why" in txt
    assert "GOOG" in txt
    assert "META" in txt
    assert "$152,979" in txt


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
