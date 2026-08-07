"""BUG D (2026-08-07) — one-voice violation on "today's net option cash".

Observed on the real 2026-08-07 briefing (digest + full):

    "- **Net option cash today:** $-948 (entry premium + roll credits −
    buybacks) · **Coverage after:** 0.22× → ~0.48×"          (💰 Money Plan)

    "- **Net cash today (mid-fills):** −$1,985"              (📋 Total Impact)

Same day, same three actions — META BTC limit $4.73, VRT BTC limit $5.22,
QCOM two-leg roll "Net: −$2,615 debit". Root causes (measured):

  - Money Plan: −948 = buyback cost of the two profit closes at the reviews'
    current_mid (META $4.50 + VRT $4.975 → −$947.5) — the QCOM roll was
    dropped entirely (ROLL kinds were neither "bank" nor "deploy").
  - Total Impact: −1,985 = "Locks $+362 profit" + "Locks $+268 profit"
    (realized P/L, NOT cash) − $2,615 roll debit.

Fix: ONE shared computation — analysis/net_option_cash.py — used by both
surfaces, with the composition spelled out from real per-action numbers.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.net_option_cash import (  # noqa: E402
    compute_net_option_cash,
    fmt_money,
)
from render.money_plan import build_money_plan  # noqa: E402
from render.panels import render_summary_card  # noqa: E402


# ── The 2026-08-07 action set, replicated from the observed briefing ──────

def _action_lines():
    return [
        "## Today's Action List — Friday, August 7, 2026",
        "",
        "1. **CLOSE** META_PUT_540_20260904 — +45% ($+362); "
        "buy-to-close limit $4.73",
        "   - **Gain:** Locks $+362 profit and frees $54,000 cash collateral",
        "2. **CLOSE** VRT_CALL_335_20260911 — +35% ($+268); "
        "buy-to-close limit $5.22",
        "   - **Gain:** Locks $+268 profit and unlocks 100×1 shares",
        "3. **ROLL_OUT_AND_DOWN** QCOM_PUT_185_20261218 — roll DOWN and out "
        "— lower the strike to cut assignment risk",
        "   - **Order (two legs):** Buy-to-Close 1× QCOM_PUT_185_20261218 "
        "(current mid $34.45) **and** Sell-to-Open 1× **$140P Fri Nov 20 "
        "'26** — bid $8.00 / mid $8.30 / ask $8.60 (105d, δ 0.24). "
        "Net: −$2,615 debit. Do NOT place the BTC alone.",
        "",
    ]


def _reviews():
    # entry/mid straight from the 2026-08-07 options_reviews.json snapshot.
    return [
        {"contract": "META_PUT_540_20260904", "underlying": "META",
         "type": "PUT", "qty": -1.0, "strike": 540.0,
         "entry_price": 8.1199, "current_mid": 4.50},
        {"contract": "VRT_CALL_335_20260911", "underlying": "VRT",
         "type": "CALL", "qty": -1.0, "strike": 335.0,
         "entry_price": 7.65, "current_mid": 4.975},
        {"contract": "QCOM_PUT_185_20261218", "underlying": "QCOM",
         "type": "PUT", "qty": -1.0, "strike": 185.0,
         "entry_price": 40.63, "current_mid": 34.45},
    ]


def test_shared_computation_counts_all_three_actions():
    """Neither observed number matched the action set. The shared function
    counts −$450 −$497.5 buybacks AND the −$2,615 roll: net −$3,562.50."""
    noc = compute_net_option_cash(_action_lines(),
                                  options_reviews=_reviews())
    by_group = {}
    for c in noc["components"]:
        by_group.setdefault(c["group"], []).append(c["cash"])
    assert sorted(by_group["buybacks"]) == [-497.5, -450.0]
    assert by_group["rolls"] == [-2615.0]
    assert noc["net_cash"] == -3562.5
    assert noc["total_buybacks"] == -947.5
    assert noc["total_rolls"] == -2615.0
    assert noc["unpriced"] == []


def test_composition_spells_out_real_per_action_numbers():
    """The line spells the equation from real per-action sums — buybacks
    term, roll-debit term, total — not a bare unexplained number like the
    observed '−$1,985'."""
    noc = compute_net_option_cash(_action_lines(),
                                  options_reviews=_reviews())
    comp = noc["composition"]
    assert "buybacks" in comp
    assert "roll debit" in comp
    assert "=" in comp
    assert fmt_money(-947.5) in comp       # −$948 buybacks
    assert fmt_money(-2615.0) in comp      # −$2,615 roll debit
    assert fmt_money(-3562.5) in comp      # = total
    # The two observed wrong totals never reappear.
    assert "$1,985" not in comp
    assert "-948 (" not in comp


def _extract(lines, marker):
    for ln in lines:
        if marker in ln:
            return ln
    raise AssertionError(f"{marker!r} not found in {lines}")


def test_money_plan_and_total_impact_agree_on_same_fixture():
    """Observed: Money Plan '$-948' vs Total Impact '−$1,985' for the SAME
    action set. Both surfaces now render the SAME shared composition."""
    mp_lines, mp_json = build_money_plan(
        date_str="2026-08-07",
        action_list_lines=_action_lines(),
        options_reviews=_reviews(),
        new_ideas=[],
        playbook=None,
        analytics={"stress_coverage": {"coverage_ratio": 0.22}},
        snapshot_data={"positions": []},
        config={},
        long_term_opportunities=[],
        aging_info=None,
    )
    mp_line = _extract(mp_lines, "Net option cash today")
    ti_lines = render_summary_card(
        _action_lines(), {"balance": {"accountValue": 1_089_204}},
        options_reviews=_reviews(), new_ideas=[])
    ti_line = _extract(ti_lines, "Net cash today")

    noc = compute_net_option_cash(_action_lines(),
                                  options_reviews=_reviews())
    # One composition string, rendered verbatim on both surfaces.
    assert noc["composition"] in mp_line
    assert noc["composition"] in ti_line
    assert mp_json["net_cash"] == noc["net_cash"] == -3562.5
    # Neither observed wrong number survives.
    joined = "\n".join(mp_lines + ti_lines)
    assert "$-948" not in joined
    assert "−$1,985" not in joined and "$1,985" not in joined


def test_locked_profit_strings_are_not_cash():
    """Total Impact's −$1,985 came from regex-harvesting 'Locks $+362
    profit' / 'Locks $+268 profit' (realized P/L) against the roll debit.
    P/L strings must never enter the cash number: the buyback components
    are the BTC costs (−$450 / −$497.5), not +$362/+$268."""
    noc = compute_net_option_cash(_action_lines(),
                                  options_reviews=_reviews())
    cashes = [c["cash"] for c in noc["components"]]
    assert 362.0 not in cashes and 268.0 not in cashes
    assert noc["net_cash"] != -1985.0


def test_playbook_terms_are_labelled_and_additive():
    """When the Money Plan folds in the Rotation Playbook's composed opens,
    the extra term is labelled '(playbook)' and the totals stay derived from
    the same per-action numbers as the playbook-less Total Impact card."""
    playbook = {"closes": [],
                "opens": [{"ticker": "NOW", "premium": 818.0, "dte": 38}],
                "coverage_before": 0.22, "coverage_after": 0.48}
    base = compute_net_option_cash(_action_lines(),
                                   options_reviews=_reviews())
    with_pb = compute_net_option_cash(_action_lines(),
                                      options_reviews=_reviews(),
                                      playbook=playbook)
    assert with_pb["net_cash"] == base["net_cash"] + 818.0
    pb_comps = [c for c in with_pb["components"]
                if "(playbook)" in c["label"]]
    assert len(pb_comps) == 1 and pb_comps[0]["cash"] == 818.0
    assert "+$818 new premium" in with_pb["composition"]


def test_unpriced_close_excluded_never_estimated():
    """A close with no review (no measured mid) is EXCLUDED and flagged —
    never guessed, never $0 folded silently into the total (rule #19)."""
    lines = [
        "## Today's Action List",
        "",
        "1. **CLOSE** XYZ_PUT_100_20261218 — +40% ($+400); "
        "buy-to-close limit $1.00",
        "",
    ]
    noc = compute_net_option_cash(lines, options_reviews=[])
    assert noc["components"] == []
    assert noc["net_cash"] == 0.0
    assert noc["unpriced"] == ["CLOSE XYZ_PUT_100_20261218"]
    assert "unpriced — excluded" in noc["composition"]


def test_skip_marked_blocks_do_not_count():
    """Blocked/deferred blocks contribute no cash (same actionable-only
    rule the Money Plan banks/deploys already follow)."""
    lines = _action_lines() + [
        "4. **PULLBACK CSP** MSFT — sell $410P  🚫 WASH-SALE BLOCKED",
        "   - **🚫 Skip — wash-sale rule:** closed at a loss 12d ago",
        "",
    ]
    ideas = [{"ticker": "MSFT", "mid": 4.0, "contracts": 1, "dte": 38,
              "instruction": {"x": 1}}]
    noc = compute_net_option_cash(lines, options_reviews=_reviews(),
                                  new_ideas=ideas)
    assert noc["total_deploys"] == 0.0
    assert noc["net_cash"] == -3562.5
