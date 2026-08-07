"""BUG C (2026-08-07) — unpriced actionable LEAP tickets + "net cash +$0".

Observed on the real 2026-08-07 briefing:

    "### 🎯 5. LEAP CALL · `MELI` ... **Trade:** BUY 1× MELI $1545C exp Fri
    Aug 20 '27 (378 DTE) (ITM, delta ~0.70) · 💵 FV: n/a (no FMP data)"

and in the Capital Plan (digest, three of them):

    "- LT LEAP MELI — BUY 1× MELI $1545C exp Fri Aug 20 '27 (378 DTE)
    (ITM, delta ~0.70) — net cash +$0"
    "- LT LEAP CCL — BUY 1× CCL $25C ... — net cash +$0"
    "- LT LEAP PFE — BUY 1× PFE $25C ... — net cash +$0"

A deep-ITM MELI $1545C with spot ~$1815 is a ~$30,000+ debit rendered as
costing $0, with NO quote (no bid/mid/ask) and a "delta ~0.70" that was the
composer's hardcoded TARGET, not a measurement (rules #10/#19).

Fixes pinned here:
  (a) LEAP with a live E*TRADE quote → real debit + measured delta rendered;
      capital-plan net cash == −debit.
  (b) LEAP without a quote → demoted ("chain unavailable — verify at
      broker"), no actionable BUY line, capital-plan net cash n/a — never $0.
  (c) Measured debit beyond the ~$5K LT sizing intent / 5%-NLV per-trade cap
      → demoted with the measured numbers visible.
"""

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps import long_term_opportunities as lto  # noqa: E402

_CHAIN_DOWN = "chain unavailable — verify at broker"


def _plan_module():
    target = (Path(__file__).resolve().parents[3]
              / "capital-planner" / "scripts" / "plan.py")
    spec = importlib.util.spec_from_file_location("capital_planner_plan",
                                                  target)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["capital_planner_plan"] = mod  # dataclass needs the module
    spec.loader.exec_module(mod)
    return mod


def _meli_op():
    return {
        "kind": "LEAP_CALL", "ticker": "MELI",
        "concrete_trade": ("BUY 1× MELI $1545C exp Fri Aug 20 '27 (378 DTE) "
                           "(ITM, delta ~0.70)"),
        "rationale": "Stock-replacement LEAP: deep-ITM call captures upside.",
        "yield_or_cost": "~$36298 per contract (rule-of-thumb est)",
        "trigger_reasons": ["third-party BUY"],
        "source": "recommendation-list-fetcher + yfinance IV + 200-SMA",
        "target_expiration": "2027-08-20", "target_dte": 378,
    }


def _ccl_op(**overrides):
    op = {
        "kind": "LEAP_CALL", "ticker": "CCL",
        "concrete_trade": ("BUY 1× CCL $25C exp Fri Aug 20 '27 (378 DTE) "
                           "(ITM, target δ~0.70)"),
        "rationale": "Stock-replacement LEAP.",
        "yield_or_cost": "~$569 per contract (rule-of-thumb est)",
        "trigger_reasons": ["third-party BUY"],
        "source": "recommendation-list-fetcher",
        "target_expiration": "2027-08-20", "target_dte": 378,
    }
    op.update(overrides)
    return op


# ── (a) live quote → real debit + MEASURED delta ─────────────────────────

def test_leap_with_live_quote_renders_real_debit_and_measured_delta():
    """The observed ticket carried NO quote and a hardcoded-looking
    'delta ~0.70'. With a live chain quote the ticket must show bid/mid/ask,
    the real debit, and the MEASURED delta — never the composer's target."""
    op = _ccl_op()
    data = {"strike": 25.0, "bid": 5.55, "ask": 5.85, "mid": 5.70,
            "delta": 0.68, "source": "etrade_live"}
    lto._apply_leap_quote(op, data, 25.0, config={}, nlv=1_089_204.0,
                          chain_down_reason=_CHAIN_DOWN)
    assert op["live_debit_total"] == 570.0
    assert op["live_delta"] == 0.68
    assert "δ 0.68 measured" in op["concrete_trade"]
    assert "δ~0.70" not in op["concrete_trade"]
    assert "delta ~0.70" not in op["concrete_trade"]
    assert "debit $570" in op["yield_or_cost"]
    assert "mid $5.70" in op["yield_or_cost"]
    assert "bid $5.55" in op["yield_or_cost"]
    assert "ask $5.85" in op["yield_or_cost"]
    assert "Live E*TRADE chain" in op["yield_or_cost"]
    assert not op.get("reference_demoted")
    assert not op.get("quote_unavailable")


def test_leap_chain_without_greeks_renders_delta_na_never_fabricated():
    """Rule #19: when the chain carries no Greeks, render δ n/a — never a
    plausible-looking number."""
    op = _ccl_op()
    data = {"strike": 25.0, "bid": 5.55, "ask": 5.85, "mid": 5.70,
            "delta": None, "source": "etrade_live"}
    lto._apply_leap_quote(op, data, 25.0, config={}, nlv=1_000_000.0,
                          chain_down_reason=_CHAIN_DOWN)
    assert "δ n/a" in op["concrete_trade"]
    assert "0.70" not in op["concrete_trade"]
    assert op["live_delta"] is None


def test_capital_plan_net_cash_equals_minus_debit_for_priced_leap():
    """Pinned against the observed '— net cash +$0': a priced LEAP's capital
    plan row must carry net cash == −(measured debit)."""
    mod = _plan_module()
    op = _ccl_op(live_debit_total=570.0, live_delta=0.68)
    plan = mod.build_capital_plan(
        balance={"accountValue": 1_089_204.0, "cash": 50_000.0},
        positions=[], long_term_opportunities=[op])
    leaps = [a for a in plan.actions if a.kind == "LT_LEAP"]
    assert len(leaps) == 1
    assert leaps[0].net_cash == -570.0
    assert leaps[0].cash_unknown is False
    md = "\n".join(mod.format_capital_plan_md(plan))
    assert "net cash −$570" in md
    assert "net cash +$0" not in md


# ── (b) no quote → demoted, no actionable line, net cash n/a ─────────────

def test_leap_without_quote_demotes_no_actionable_buy_line():
    """Hard rule #10: 'BUY 1× MELI $1545C … (ITM, delta ~0.70)' with no live
    price must NOT render as actionable. The ticket demotes to 'chain
    unavailable — verify at broker' with the BUY verb stripped and the
    fabricated-looking delta removed."""
    op = _meli_op()
    lto._apply_leap_quote(op, None, 1545.0, config={}, nlv=1_089_204.0,
                          chain_down_reason=_CHAIN_DOWN)
    assert op["quote_unavailable"] is True
    assert op["reference_demoted"] is True
    assert _CHAIN_DOWN in op["reference_reason"]
    assert not op["concrete_trade"].startswith("BUY")
    assert _CHAIN_DOWN in op["concrete_trade"]
    assert "delta ~0.70" not in op["concrete_trade"]
    assert "no live quote" in op["yield_or_cost"]


def test_leap_zero_quote_treated_as_unpriced():
    """A chain row with bid/mid/ask all 0 is not a price — demote."""
    op = _ccl_op()
    data = {"strike": 25.0, "bid": 0.0, "ask": 0.0, "mid": 0.0,
            "delta": 0.7, "source": "etrade_live"}
    lto._apply_leap_quote(op, data, 25.0, config={}, nlv=1_000_000.0,
                          chain_down_reason=_CHAIN_DOWN)
    assert op["quote_unavailable"] is True
    assert op["reference_demoted"] is True


def test_capital_plan_unpriced_leap_net_cash_na_never_zero():
    """Pinned against '- LT LEAP MELI — … — net cash +$0': an unpriced LEAP
    lands in Skipped with the chain-unavailable reason and cash_unknown; the
    rendered plan never shows a $0 LEAP row."""
    mod = _plan_module()
    op = _meli_op()  # no live_debit_total
    plan = mod.build_capital_plan(
        balance={"accountValue": 1_089_204.0, "cash": 50_000.0},
        positions=[], long_term_opportunities=[op])
    active = [a for a in plan.actions if a.kind == "LT_LEAP"]
    skipped = [a for a in plan.skipped_actions if a.kind == "LT_LEAP"]
    assert active == []
    assert len(skipped) == 1
    assert skipped[0].cash_unknown is True
    assert "net cash n/a" in skipped[0].skip_reason
    assert "verify at broker" in skipped[0].skip_reason
    md = "\n".join(mod.format_capital_plan_md(plan))
    assert "net cash +$0" not in md
    assert "verify at broker" in md


def test_demote_all_unpriced_leaps_on_fetcher_unavailable():
    """When the chain fetcher is unreachable, EVERY surviving LEAP demotes
    (a BUY debit estimate is not a price) — CSPs keep their est-tag path."""
    ops = [_meli_op(), _ccl_op(),
           {"kind": "LONG_DATED_CSP", "ticker": "GOOG",
            "concrete_trade": "SELL 1× GOOG $300P", "yield_or_cost": "~$900"}]
    lto._demote_all_unpriced_leaps(ops, _CHAIN_DOWN)
    for op in ops[:2]:
        assert op["quote_unavailable"] is True
        assert op["reference_demoted"] is True
        assert _CHAIN_DOWN in op["concrete_trade"]
    assert "quote_unavailable" not in ops[2]


# ── (c) sizing sanity on the MEASURED debit ──────────────────────────────

def test_meli_scale_debit_demoted_against_5k_sizing_intent():
    """The MELI $1545C at ~$30K against a '$5K starter' intent is itself a
    flag — demote with the measured debit visible, never recommend it
    unpriced (or unsized)."""
    op = _meli_op()
    data = {"strike": 1545.0, "bid": 298.0, "ask": 304.4, "mid": 301.2,
            "delta": 0.71, "source": "etrade_live"}
    lto._apply_leap_quote(op, data, 1545.0, config={}, nlv=1_089_204.0,
                          chain_down_reason=_CHAIN_DOWN)
    # Quote still rendered in full (rule #24: demoted, never hidden)…
    assert op["live_debit_total"] == 30120.0
    assert "δ 0.71 measured" in op["concrete_trade"]
    # …but the card is size-demoted with the measured numbers in the reason.
    assert op["size_demoted"] is True
    assert op["reference_demoted"] is True
    assert "$30,120" in op["reference_reason"]
    assert "$5K LT sizing intent" in op["reference_reason"]


def test_debit_above_5pct_nlv_demoted():
    """5%-NLV per-trade cap applies on the MEASURED debit."""
    op = _meli_op()
    data = {"strike": 1545.0, "bid": 298.0, "ask": 304.4, "mid": 301.2,
            "delta": 0.71, "source": "etrade_live"}
    lto._apply_leap_quote(op, data, 1545.0,
                          config={"long_term": {"leap_max_debit_usd": 50_000}},
                          nlv=500_000.0, chain_down_reason=_CHAIN_DOWN)
    assert op["size_demoted"] is True
    assert "5%-NLV per-trade cap" in op["reference_reason"]
    assert "$25,000" in op["reference_reason"]


def test_small_leap_within_caps_stays_actionable():
    op = _ccl_op()
    data = {"strike": 25.0, "bid": 5.55, "ask": 5.85, "mid": 5.70,
            "delta": 0.68, "source": "etrade_live"}
    lto._apply_leap_quote(op, data, 25.0, config={}, nlv=1_089_204.0,
                          chain_down_reason=_CHAIN_DOWN)
    assert not op.get("size_demoted")
    assert not op.get("reference_demoted")


def test_capital_plan_size_demoted_leap_skipped_with_measured_reason():
    """A priced-but-oversized LEAP lands in Skipped with the measured-debit
    reason — the real number visible, never a green-lit $0 row."""
    mod = _plan_module()
    op = _meli_op()
    op["live_debit_total"] = 30120.0
    op["reference_demoted"] = True
    op["size_demoted"] = True
    op["reference_reason"] = ("real debit $30,120 is 6.0× the ~$5K LT sizing "
                              "intent (cap $10,000) — size down (lower "
                              "strike / call spread) or skip")
    plan = mod.build_capital_plan(
        balance={"accountValue": 1_089_204.0, "cash": 50_000.0},
        positions=[], long_term_opportunities=[op])
    skipped = [a for a in plan.skipped_actions if a.kind == "LT_LEAP"]
    assert len(skipped) == 1
    assert "$30,120" in skipped[0].skip_reason
    md = "\n".join(mod.format_capital_plan_md(plan))
    assert "net cash +$0" not in md
    assert "$30,120" in md
