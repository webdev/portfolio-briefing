"""Rule #43 regression — the 2026-08-17 Capital Plan desync (BUG E).

Observed on the real 2026-08-17 briefing: the Capital Plan header said

    "_Premium received:_ $0 · _Collateral freed:_ $0 · _New collateral
     locked:_ $0 · _Debits + BTC costs:_ $151,386"

and Tier 1 listed only EXIT reviews + SMH — while the SAME briefing's
Action List contained

    "3. **CLOSE BEFORE EARNINGS** IREN_PUT_47_20261218 — +39% captured
     ($+722); buy-to-close at mid $11.40 before IREN prints in 10d"
    "2. 🚨 **URGENT — EXECUTE ROLL** QCOM_PUT_180_20270219 — Calendar roll
     (same strike, longer date): +$470 net credit (1 spreads @ $4.70/share)"

Root causes: (a) _ACTION_LINE_RE required ** immediately after the item
number, so the 🚨-prefixed URGENT roll never matched; (b) the extractor
routed only the exact kind "CLOSE" — "CLOSE_BEFORE_EARNINGS" (and the
URGENT-prefixed "EXECUTE ROLL") matched nothing and fell out of the plan.
Every actionable action-list item must appear in the plan with its
MEASURED cash effect: the IREN close frees $4,700 (strike × 100 from the
contract ident) minus the $1,140 BTC; the roll banks its +$470 net credit
with collateral unchanged; 'Collateral freed' sums correctly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from plan import build_capital_plan, extract_actions_from_action_list  # noqa: E402


def _balance(cash=79_398, nlv=1_120_428):
    return {"cash": cash, "accountValue": nlv}


IREN_BLOCK = [
    "3. **CLOSE BEFORE EARNINGS** IREN_PUT_47_20261218 — +39% captured "
    "($+722); buy-to-close at mid $11.40 before IREN prints in 10d  · RSI 55",
    "   - **Why:** the GTC-at-50% squeeze requires earnings > 30d away — "
    "IREN prints in 10d, inside the contract.",
    "   - **Exit cost anatomy:** BTC mid $11.40 = $267 intrinsic + $873 "
    "extrinsic (77% — IV-pumped) · spread $0.50 (4.4% of mid)",
]

QCOM_BLOCK = [
    "2. 🚨 **URGENT — EXECUTE ROLL** QCOM_PUT_180_20270219 — Calendar roll "
    "(same strike, longer date): +$470 net credit (1 spreads @ $4.70/share)"
    "  · RSI 46 🟢 pullback",
    "   - **Why (urgent):** 🎯 Strike tested (δ 0.53 ≥ 0.45) with 8% "
    "captured and 186 DTE — credit-roll window open.",
    "   - **Earnings check:** ⚠️ ⚠️ Earnings 73d away — prints 231d BEFORE "
    "expiry — contract spans earnings",
]

SMH_BLOCK = [
    "1. **CLOSE** SMH_CALL_710_20261120 — +35% ($+705); buy-to-close "
    "limit $13.86  · RSI 53 🟡 mid-range",
    "   - **Gain:** Locks $+705 profit and unlocks 100×1 shares (notional "
    "$71,000) for fresh covered-call premium",
]


def test_close_before_earnings_flows_into_plan_with_measured_cash():
    """'3. **CLOSE BEFORE EARNINGS** IREN_PUT_47_20261218 — +39% captured
    ($+722); buy-to-close at mid $11.40' must appear as a CLOSE with net
    cash = freed collateral ($4,700, strike×100 from the ident) − BTC cost
    ($1,140) — not fall out of the plan ('Collateral freed: $0')."""
    actions = extract_actions_from_action_list(IREN_BLOCK)
    assert len(actions) == 1
    a = actions[0]
    assert a.kind == "CLOSE"
    assert a.ticker == "IREN"
    assert a.cash_in == 4_700.0          # 47 × 100 × 1, from the ident
    assert a.cash_out == 1_140.0         # $11.40 × 100
    assert a.net_cash == 3_560.0         # freed collateral − BTC cost
    assert a.tier == 1                    # event-risk close — do first
    assert "earnings" in a.tier_reason.lower()


def test_urgent_execute_roll_flows_into_plan_tier1_with_net_credit():
    """'2. 🚨 **URGENT — EXECUTE ROLL** QCOM_PUT_180_20270219 — … +$470
    net credit' fell out of the plan entirely (Tier 1 showed no roll).
    It must parse: ROLL QCOM, +$470 in, $0 out (collateral unchanged),
    Tier 1 (urgent)."""
    actions = extract_actions_from_action_list(QCOM_BLOCK)
    assert len(actions) == 1
    a = actions[0]
    assert a.kind == "ROLL"
    assert a.ticker == "QCOM"
    assert a.cash_in == 470.0
    assert a.cash_out == 0.0
    assert a.net_cash == 470.0
    assert a.tier == 1
    assert "urgent" in a.tier_reason.lower()
    # 73d-away earnings is NOT the imminent-earnings defer (no 🔴 BLOCK).
    assert a.skip_reason is None


def test_collateral_freed_sums_across_the_real_action_list():
    """With the 2026-08-17 action list (SMH CC close + urgent QCOM roll +
    IREN close-before-earnings), 'Collateral freed' must sum to $4,700
    (IREN put; the SMH covered call frees shares, not cash) — not $0."""
    lines = SMH_BLOCK + QCOM_BLOCK + IREN_BLOCK
    plan = build_capital_plan(
        balance=_balance(), positions=[], action_list_lines=lines)
    assert plan.total_collateral_freed == 4_700.0
    tier1 = [a for a in plan.actions if a.tier == 1]
    assert {("CLOSE", "IREN"), ("ROLL", "QCOM")} <= {
        (a.kind, a.ticker) for a in tier1}
    # The SMH covered-call close contributes $0 freed (share-secured).
    smh = next(a for a in plan.actions if a.ticker == "SMH")
    assert smh.cash_in == 0.0


def test_legacy_close_block_unchanged():
    """Config-off / legacy shape: a plain '**CLOSE** … frees $35,500 cash
    collateral' block parses exactly as before (no regression from the
    close-family routing)."""
    lines = [
        "1. **CLOSE** VRT_PUT_355_20260612 — +51% ($+1,020); buy-to-close "
        "limit $9.80",
        "   - **Gain:** Locks $+1,020 profit and frees $35,500 cash "
        "collateral.",
    ]
    actions = extract_actions_from_action_list(lines)
    assert len(actions) == 1
    a = actions[0]
    assert a.kind == "CLOSE"
    assert a.cash_in == 35_500.0          # explicit "frees $X" wins
    assert abs(a.cash_out - 980.0) < 0.01
    assert a.tier == 1                    # ≥30% capture promotion intact
