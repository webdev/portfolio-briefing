"""Rule #43 regression — the 2026-08-17 SNDK/SOXX three-contradictory-states
bug (BUG A) and the hold-demoted-never-banked invariant.

Observed on the real 2026-08-17 briefing, all in ONE render:

    "- **Bank today:** 4 close(s) → $+4,637 realized (SMH $710C,
     SNDK $1230P, SOXX $520P, IREN $47P)"
    "- **Net option cash today (mid-fills):** −$5,113 buybacks = −$5,113"
    "- **Blocked money** (why not more): … 2 winners held for 75%+
     (no redeploy path: SNDK $1230P, SOXX $520P) …"

— while the Action List contained NO SNDK/SOXX closes (the redeploy-aware
hold demoted both) and the Total Impact card said "−$2,463". Meanwhile red
flags #9/#10 said:

    "### 📊 9. MEDIUM — Obligation-inclusive concentration: SNDK at 11.0%
     of NLV (equity 0.0% + $123,000 short-put obligation) — over the 8%
     Tier C cap" … "Close or roll down the SNDK put(s)…"
    "### 📊 10. MEDIUM — Obligation-inclusive concentration: SOXX at 11.1%
     of NLV (equity 6.5% + $52,000 short-put obligation) — over the 8%
     Tier C cap"

Two fixes pinned here:
  1. RISK-EXEMPTION GAP — a winner close that reduces an OVER-CAP
     obligation-inclusive concentration is RISK-driven and exempt from the
     no-redeploy-path hold (analysis/redeploy_path.over_cap_risk_exemption,
     reusing position_tiers.projected_name_concentration with zero new
     contracts). SNDK (+62%, $123K freed) stays a CLOSE with the risk tag.
  2. ONE-VOICE — the Money Plan bank/buyback lines derive from the FINAL
     rendered action set: a hold-demoted winner is NEVER banked via the
     playbook fold-in (the 08-14 NOK lesson generalized to ANY demotion
     path — net_option_cash.held_back_idents).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import redeploy_path as rdp  # noqa: E402
from analysis.net_option_cash import (  # noqa: E402
    compute_net_option_cash, demoted_close_idents, held_back_idents)
from render.money_plan import build_money_plan  # noqa: E402
from render.panels import render_action_list  # noqa: E402

TODAY = "2026-08-17"
NLV = 1_120_428.0

_RD_ON = {"redeploy_aware_tp": {"enabled": True, "hold_target_pct": 0.75}}


def _sndk_positions():
    """The 2026-08-17 book shape: SNDK $123,000 short-put obligation on $0
    equity (11.0% of NLV, over the 8% Tier C cap)."""
    return [{
        "assetType": "OPTION", "symbol": "SNDK_PUT_1230_20261218",
        "underlying": "SNDK", "type": "PUT", "qty": -1, "strike": 1230.0,
    }]


def _soxx_positions():
    """SOXX: equity 6.5% NLV ($72,836) + $52,000 short-put obligation →
    11.1% of NLV, over the 8% Tier C cap."""
    return [
        {"assetType": "EQUITY", "symbol": "SOXX", "qty": 100,
         "price": 728.36, "marketValue": 72_836.0},
        {"assetType": "OPTION", "symbol": "SOXX_PUT_520_20261218",
         "underlying": "SOXX", "type": "PUT", "qty": -1, "strike": 520.0},
    ]


def _snapshot(positions, config=None):
    return {
        "quotes": {}, "chains": {}, "iv_ranks": {}, "earnings_calendar": {},
        "_config": {"core_positions": [], "accounts": [],
                    **(_RD_ON if config is None else config)},
        "balance": {"accountValue": NLV, "cash": 79_398.0},
        "positions": positions,
    }


def _analytics():
    """The measured 2026-08-17 stress numbers: coverage 0.11×, cash
    $79,398, $752,700 of put obligation — gates CLOSED, and no single
    close reopens them."""
    return {"stress_coverage": {
        "coverage_ratio": 0.11, "cash": 79_398.0,
        "total_put_obligations": 752_700.0,
    }}


def _sndk_winner():
    """SNDK $1230P at +62% captured ($2,695) — the briefing's own analyst
    brief called it 'the single most impactful structural close available —
    it fixes the SNDK concentration flag (11% vs 8% Tier C cap)'."""
    return {
        "contract": "SNDK_PUT_1230_20261218",
        "underlying": "SNDK", "type": "PUT", "qty": -1,
        "strike": 1230.0, "expiration": "2026-12-18",
        "entry_price": 43.50, "current_mid": 16.53, "days_to_expiry": 32,
        "recommendation": "HOLD", "matrix_cell_id": "X",
        "roll_candidates": [],
    }


def _soxx_winner():
    return {
        "contract": "SOXX_PUT_520_20261218",
        "underlying": "SOXX", "type": "PUT", "qty": -1,
        "strike": 520.0, "expiration": "2026-12-18",
        "entry_price": 10.00, "current_mid": 4.80, "days_to_expiry": 32,
        "recommendation": "HOLD", "matrix_cell_id": "X",
        "roll_candidates": [],
    }


def _undercap_winner():
    """A healthy 52%-capture winner on a name UNDER its tier cap —
    obligation $20,000 = 1.8% of NLV; the no-redeploy-path hold applies."""
    return {
        "contract": "VRT_PUT_200_20261016",
        "underlying": "VRT", "type": "PUT", "qty": -1,
        "strike": 200.0, "expiration": "2026-10-16",
        "entry_price": 4.00, "current_mid": 1.92, "days_to_expiry": 60,
        "recommendation": "HOLD", "matrix_cell_id": "X",
        "roll_candidates": [],
    }


def _headlines(items):
    return [ln for ln in items if ln.lstrip()[:1].isdigit()]


# ── Fix 1 — over-cap winners are RISK closes, exempt from the hold ────────

def test_sndk_over_cap_winner_close_survives_no_redeploy_path():
    """Observed: 'Blocked money … 2 winners held for 75%+ (no redeploy
    path: SNDK $1230P, SOXX $520P)' while red flag #9 said 'Close or roll
    down the SNDK put(s)'. Over the 8% Tier C cap → the close is
    risk-driven: it stays on the action list with the risk tag."""
    rev = _sndk_winner()
    snap = _snapshot(_sndk_positions())
    items = render_action_list([], [rev], [], _analytics(), snap,
                               date_str=TODAY)
    assert any("**CLOSE** SNDK_PUT_1230" in h for h in _headlines(items)), \
        "over-cap SNDK winner must stay a CLOSE"
    assert not rev.get("_redeploy_hold_demotion")
    tag_lines = [ln for ln in items if "Risk-driven close" in ln]
    assert tag_lines, "the rec must carry the visible risk tag"
    assert "risk: SNDK at 11.0% of NLV over the 8% Tier C cap" in tag_lines[0]
    assert "close restores compliance" in tag_lines[0]


def test_soxx_equity_plus_put_over_cap_also_exempt():
    """SOXX (equity 6.5% + $52,000 obligation = 11.1% NLV, red flag #10)
    is the equity+obligation flavor of the same exemption."""
    rev = _soxx_winner()
    snap = _snapshot(_soxx_positions())
    items = render_action_list([], [rev], [], _analytics(), snap,
                               date_str=TODAY)
    assert any("**CLOSE** SOXX_PUT_520" in h for h in _headlines(items))
    assert not rev.get("_redeploy_hold_demotion")
    tag = "\n".join(ln for ln in items if "Risk-driven close" in ln)
    assert "risk: SOXX at 11.1% of NLV over the 8% Tier C cap" in tag


def test_under_cap_winner_still_holds_for_75():
    """The exemption is measured, not blanket: an under-cap winner (VRT at
    1.8% NLV) still demotes to the visible ⏳ hold-for-more note."""
    rev = _undercap_winner()
    positions = [{"assetType": "OPTION", "symbol": "VRT_PUT_200_20261016",
                  "underlying": "VRT", "type": "PUT", "qty": -1,
                  "strike": 200.0}]
    snap = _snapshot(positions)
    items = render_action_list([], [rev], [], _analytics(), snap,
                               date_str=TODAY)
    assert not any("**CLOSE** VRT_PUT_200" in h for h in _headlines(items))
    assert "⏳ Holding for 75%+" in (rev.get("_redeploy_hold_demotion") or "")


def test_exemption_unit_fail_directions():
    """No exemption is ever fabricated from missing data (rule #19): no
    NLV, no positions over cap, call side, or non-dict inputs → None."""
    cfg = _RD_ON
    # Over cap → tag.
    tag = rdp.over_cap_risk_exemption(
        _sndk_winner(), _snapshot(_sndk_positions()), cfg)
    assert tag and tag.startswith("risk: SNDK")
    # Missing NLV → None.
    snap_no_nlv = _snapshot(_sndk_positions())
    snap_no_nlv["balance"] = {}
    assert rdp.over_cap_risk_exemption(_sndk_winner(), snap_no_nlv, cfg) is None
    # Under cap → None.
    assert rdp.over_cap_risk_exemption(
        _undercap_winner(), _snapshot([{
            "assetType": "OPTION", "symbol": "VRT_PUT_200_20261016",
            "underlying": "VRT", "type": "PUT", "qty": -1, "strike": 200.0,
        }]), cfg) is None
    # Call side → None (the hold gate is put-only anyway).
    call_rev = dict(_sndk_winner(), type="CALL")
    assert rdp.over_cap_risk_exemption(
        call_rev, _snapshot(_sndk_positions()), cfg) is None
    # Non-dict inputs → None.
    assert rdp.over_cap_risk_exemption(None, None, cfg) is None


def test_config_off_is_legacy():
    """redeploy_aware_tp disabled → the whole gate (and the exemption
    path) never runs; the winner closes exactly as legacy with no risk
    tag and no demotion flag."""
    rev = _sndk_winner()
    snap = _snapshot(_sndk_positions(),
                     config={"redeploy_aware_tp": {"enabled": False}})
    items = render_action_list([], [rev], [], _analytics(), snap,
                               date_str=TODAY)
    assert any("**CLOSE** SNDK_PUT_1230" in h for h in _headlines(items))
    assert not rev.get("_redeploy_hold_demotion")
    assert not any("Risk-driven close" in ln for ln in items)


# ── Fix 2 — one voice: hold-demoted winners are NEVER banked ──────────────

def _demoted_rev(contract="SOXX_PUT_520_20261218", underlying="SOXX",
                 strike=520.0, entry=10.0, mid=4.80):
    rev = {
        "contract": contract, "underlying": underlying, "type": "PUT",
        "qty": -1, "strike": strike, "entry_price": entry,
        "current_mid": mid, "days_to_expiry": 32,
    }
    rev["_redeploy_hold_demotion"] = (
        "⏳ Holding for 75%+ — no redeployment path (gates closed 0.11×; "
        "no A/B setups above floor). Would close at 52% if a path opens.")
    return rev


def _playbook_with_demoted_close():
    """A playbook whose composed close list still contains the demoted
    winner (the playbook sweeps pre-demotion)."""
    return {"closes": [{
        "contract": "SOXX_PUT_520_20261218", "ticker": "SOXX",
        "strike": 520.0, "qty": -1, "buy_to_close_mid": 4.80,
        "realized_profit": 520.0,
    }], "opens": []}


_ACTION_LIST = [
    "## Today's Action List",
    "",
    "1. **CLOSE** SMH_CALL_710_20261120 — +35% ($+705); buy-to-close "
    "limit $13.86",
    "   - **Why:** winner-close discipline.",
]

_SMH_REV = {
    "contract": "SMH_CALL_710_20261120", "underlying": "SMH",
    "type": "CALL", "qty": -1, "strike": 710.0,
    "entry_price": 21.00, "current_mid": 13.86,
}


def test_hold_demoted_winner_never_banked_in_money_plan():
    """Observed: 'Bank today: 4 close(s) → $+4,637 realized (… SNDK
    $1230P, SOXX $520P …)' while the SAME block said '2 winners held for
    75%+ (no redeploy path: SNDK $1230P, SOXX $520P)'. The playbook
    fold-in must key on the FINAL action set: a demotion-flagged contract
    is never banked, whatever the demotion path (NOK/GTC generalized)."""
    reviews = [_SMH_REV, _demoted_rev()]
    lines, plan = build_money_plan(
        date_str=TODAY,
        action_list_lines=_ACTION_LIST,
        options_reviews=reviews,
        new_ideas=[],
        playbook=_playbook_with_demoted_close(),
        analytics=_analytics(),
        snapshot_data={"positions": []},
        config=_RD_ON,
    )
    bank_line = next(ln for ln in lines if "Bank today" in ln)
    assert "SOXX" not in bank_line, \
        "a hold-demoted winner must never appear in Bank today"
    assert "SMH $710C" in bank_line
    # …and it IS named in Blocked money (rule #24 — visible, not hidden).
    blocked = next(ln for ln in lines if "Blocked money" in ln)
    assert "SOXX $520P" in blocked
    assert not any(b.get("ident") == "SOXX_PUT_520_20261218"
                   for b in plan["banks"])


def test_money_plan_and_total_impact_byte_agree():
    """The Money Plan's net-option-cash composition must equal the Total
    Impact card's for the same action set — the demoted playbook close
    (−$480 SOXX buyback) may not widen the Money Plan side (−$5,113 vs
    −$2,463 on 2026-08-17)."""
    reviews = [_SMH_REV, _demoted_rev()]
    noc_money_plan = compute_net_option_cash(
        _ACTION_LIST, options_reviews=reviews, new_ideas=[],
        playbook=_playbook_with_demoted_close())
    noc_total_impact = compute_net_option_cash(
        _ACTION_LIST, options_reviews=reviews, new_ideas=[])
    assert noc_money_plan["composition"] == noc_total_impact["composition"]
    assert noc_money_plan["net_cash"] == noc_total_impact["net_cash"]


def test_held_back_idents_unions_gtc_and_demotions():
    """held_back_idents = HOLD/GTC verbs ∪ ANY render-time demotion flag
    (_redeploy_hold_demotion, _close_floor_demotion)."""
    lines = _ACTION_LIST + [
        "2. **HOLD — GTC AT 50%** NOK_PUT_11_20261218 — +38% captured; "
        "place a GTC buy-to-close at the 50%-capture price $1.28",
    ]
    floor_rev = {"contract": "AAA_PUT_10_20261218",
                 "_close_floor_demotion": "below the $100 action floor"}
    idents = held_back_idents(lines, [_demoted_rev(), floor_rev])
    assert "NOK_PUT_11_20261218" in idents
    assert "SOXX_PUT_520_20261218" in idents
    assert "AAA_PUT_10_20261218" in idents
    assert "SMH_CALL_710_20261120" not in idents
    assert demoted_close_idents(None) == set()


def test_close_before_earnings_banks_from_the_action_list():
    """'3. **CLOSE BEFORE EARNINGS** IREN_PUT_47_20261218 — +39% captured
    ($+722)' is a winner close — it must bank from the FINAL action list
    directly (it only appeared in 'Bank today' via the playbook fold-in
    on 2026-08-17), and never depend on a playbook sweep."""
    iren_rev = {
        "contract": "IREN_PUT_47_20261218", "underlying": "IREN",
        "type": "PUT", "qty": -1, "strike": 47.0,
        "entry_price": 18.62, "current_mid": 11.40, "days_to_expiry": 123,
    }
    lines = _ACTION_LIST + [
        "2. **CLOSE BEFORE EARNINGS** IREN_PUT_47_20261218 — +39% captured "
        "($+722); buy-to-close at mid $11.40 before IREN prints in 10d",
        "   - **Why:** event risk beats premium mechanics.",
    ]
    md, plan = build_money_plan(
        date_str=TODAY, action_list_lines=lines,
        options_reviews=[_SMH_REV, iren_rev], new_ideas=[],
        playbook=None, analytics=_analytics(),
        snapshot_data={"positions": []}, config=_RD_ON,
    )
    bank_line = next(ln for ln in md if "Bank today" in ln)
    assert "IREN $47P" in bank_line
    assert any(b.get("ident") == "IREN_PUT_47_20261218"
               for b in plan["banks"])


# ── The URGENT roll must be priced by the shared net-cash module ──────────

def test_urgent_roll_headline_counts_in_net_option_cash():
    """Observed: '2. 🚨 **URGENT — EXECUTE ROLL** QCOM_PUT_180_20270219 —
    Calendar roll (same strike, longer date): +$470 net credit' fell out
    of the Total Impact card entirely (the 🚨 prefix broke the block
    parser). The +$470 credit is real cash and must be a rolls component."""
    lines = _ACTION_LIST + [
        "2. 🚨 **URGENT — EXECUTE ROLL** QCOM_PUT_180_20270219 — Calendar "
        "roll (same strike, longer date): +$470 net credit (1 spreads @ "
        "$4.70/share)",
        "   - **Why (urgent):** 🎯 Strike tested (δ 0.53 ≥ 0.45).",
    ]
    noc = compute_net_option_cash(lines, options_reviews=[_SMH_REV])
    assert noc["total_rolls"] == 470.0
    rolls = [c for c in noc["components"] if c["group"] == "rolls"]
    assert rolls and "QCOM_PUT_180_20270219" in rolls[0]["label"]
