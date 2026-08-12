"""Redeployability-aware take-profit (2026-08-10).

George: "I'm happy to exit options and close it if we have a path to
redeployment. If we don't have a path to redeployment, then it doesn't
make sense to close it."

The smart take-profit stack fires yield-motivated closes at 31-50% capture
on the premise "lock the win, redeploy the collateral" — but when the entry
gates are CLOSED, the freed collateral has nowhere to go and a healthy
winner should be held toward the 70-80% capture zone instead. These tests
pin the whole contract: gates open → legacy untouched; gates closed with no
path → visible ⏳ hold-for-more demotion; the META case (the close itself
reopens the gates) and A/B setups both count as paths; every risk-driven
close fires regardless; missing inputs fail OPEN to legacy; config off is
byte-identical legacy; the Money Plan stops counting held winners as banks.

Source: scripts/analysis/redeploy_path.py + render/panels.py (CLOSE WINNERS
block #2, matrix CLOSE_FOR_PROFIT block #4) + render/money_plan.py +
steps/per_option_commentary.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import redeploy_path as rdp  # noqa: E402
from render.money_plan import build_money_plan  # noqa: E402
from render.panels import render_action_list, render_risk_alerts  # noqa: E402
from steps.per_option_commentary import render_watch_with_commentary  # noqa: E402

TODAY = "2026-08-12"

_RD_ON = {"redeploy_aware_tp": {"enabled": True, "hold_target_pct": 0.75}}


def _snapshot(config=None, quotes=None, earnings=None, best_setups=None):
    snap = {
        "quotes": quotes or {"VRT": {"last": 230.0}},
        "chains": {},
        "iv_ranks": {},
        "earnings_calendar": earnings or {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 60_000},
        "positions": [],
    }
    if best_setups is not None:
        snap["_redeploy_best_setups"] = best_setups
    return snap


def _analytics(coverage=0.17, cash=60_000.0, obligations=350_000.0):
    """Analytics carrying MEASURED stress-coverage numbers (dict shape —
    coverage_ratio_from / post_close_coverage accept it)."""
    return {"stress_coverage": {
        "coverage_ratio": coverage,
        "cash": cash,
        "total_put_obligations": obligations,
    }}


def _winner_put(strike=200.0, entry=4.00, mid=1.92, dte=60,
                contract="VRT_PUT_200_20261016"):
    """A healthy short-put winner: entry $4.00 / mid $1.92 → 52% captured,
    $208 banked (over the $100 dollar floor), 60 DTE, safely OTM."""
    return {
        "contract": contract,
        "underlying": "VRT", "type": "PUT", "qty": -1,
        "strike": strike, "expiration": "2026-10-16",
        "entry_price": entry, "current_mid": mid, "days_to_expiry": dte,
        "recommendation": "HOLD", "matrix_cell_id": "X",
        "roll_candidates": [],
    }


def _headlines(items):
    return [ln for ln in items if ln.lstrip()[:1].isdigit()]


# ── (a) gates open → legacy TP untouched ──────────────────────────────────

def test_gates_open_legacy_tp_untouched():
    """George: "I'm happy to exit options and close it if we have a path to
    redeployment." Gates OPEN (coverage 0.60× ≥ 0.50× floor) IS the path —
    the legacy CLOSE renders untouched, no demotion, no annotation noise."""
    rev = _winner_put()
    snap = _snapshot(config=_RD_ON)
    items = render_action_list([], [rev], [], _analytics(coverage=0.60),
                               snap, date_str=TODAY)
    assert any("**CLOSE** VRT_PUT_200" in h for h in _headlines(items))
    assert not rev.get("_redeploy_hold_demotion")
    assert not any("Redeploy path" in ln for ln in items)


# ── (b) gates closed + healthy 50%-capture winner → visible hold ──────────

def test_gates_closed_no_path_demotes_to_visible_hold():
    """George: "If we don't have a path to redeployment, then it doesn't
    make sense to close it." Gates closed 0.17×, the close frees only
    $20,000 (post-close ~0.18× still under the floor), no A/B setups →
    the 52%-capture winner demotes to the visible ⏳ hold-for-more note
    with MEASURED values and the config hold target."""
    rev = _winner_put()
    snap = _snapshot(config=_RD_ON)
    items = render_action_list([], [rev], [], _analytics(coverage=0.17),
                               snap, date_str=TODAY)
    assert not any("**CLOSE** VRT_PUT_200" in h for h in _headlines(items))
    note = rev.get("_redeploy_hold_demotion") or ""
    assert "⏳ Holding for 75%+" in note
    assert "no redeployment path" in note
    assert "gates closed 0.17×" in note
    assert "no A/B setups above floor" in note
    assert "Would close at 52% if a path opens" in note
    # Rule #24 — the position keeps rendering in Watch with the note.
    watch = "\n".join(render_watch_with_commentary([], [rev], snap))
    assert "⏳ Holding for 75%+" in watch


def test_hold_target_from_config():
    """The raised floor comes from redeploy_aware_tp.hold_target_pct —
    hold_target_pct: 0.80 renders "Holding for 80%+"."""
    rev = _winner_put()
    snap = _snapshot(config={"redeploy_aware_tp": {
        "enabled": True, "hold_target_pct": 0.80}})
    render_action_list([], [rev], [], _analytics(coverage=0.17),
                       snap, date_str=TODAY)
    assert "⏳ Holding for 80%+" in (rev.get("_redeploy_hold_demotion") or "")


# ── (c) the META case: the close itself reopens the gates ─────────────────

def test_close_that_reopens_gates_keeps_tp_with_path_reason():
    """The META case: closing THIS position pushes post-close coverage
    across the floor — (cash $60,000 − buyback $1,920) / (obligation
    $350,000 − freed $240,000) = 0.53× ≥ 0.50×. The TP rec stays, and the
    rec line carries the measured path reason."""
    rev = _winner_put(strike=2400.0, entry=40.0, mid=19.2,
                      contract="VRT_PUT_2400_20261016")
    snap = _snapshot(config=_RD_ON, quotes={"VRT": {"last": 2760.0}})
    items = render_action_list([], [rev], [], _analytics(coverage=0.17),
                               snap, date_str=TODAY)
    assert any("**CLOSE** VRT_PUT_2400" in h for h in _headlines(items))
    assert not rev.get("_redeploy_hold_demotion")
    path_lines = [ln for ln in items if "Redeploy path" in ln]
    assert path_lines, "the rec line must carry the path reason"
    assert "closing frees $240,000" in path_lines[0]
    assert "(gates reopen)" in path_lines[0]


# ── (d) A/B setups above the floor = a path; their absence = no path ──────

def test_ab_setups_are_a_redeploy_path():
    """Gates closed and the close doesn't reopen them, but 2 A/B-graded
    setups sit above the yield floor today — rotation IS the path; the TP
    stays with "2 A/B setups waiting (CGNX B, SNDK B)"."""
    rev = _winner_put()
    best = {"csp": [
        {"ticker": "CGNX", "letter": "B", "score": 70.0},
        {"ticker": "SNDK", "letter": "B", "score": 68.0},
    ], "cc": []}
    snap = _snapshot(config=_RD_ON, best_setups=best)
    items = render_action_list([], [rev], [], _analytics(coverage=0.17),
                               snap, date_str=TODAY)
    assert any("**CLOSE** VRT_PUT_200" in h for h in _headlines(items))
    path_lines = [ln for ln in items if "Redeploy path" in ln]
    assert path_lines
    assert "2 A/B setups waiting (CGNX B, SNDK B)" in path_lines[0]


def test_cd_grades_are_not_a_path():
    """C/D-graded (or hard-blocked '—') setups are NOT a rotation target —
    only A/A-/B letters count."""
    best = {"csp": [
        {"ticker": "ZZZ", "letter": "C", "score": 55.0},
        {"ticker": "YYY", "letter": "—", "score": 0.0},
    ]}
    ok, reason = rdp.redeployment_path(
        _analytics(coverage=0.17), best,
        {"side": "put", "freed_collateral": 20_000.0, "btc_cost": 192.0},
        config=_RD_ON)
    assert ok is False
    assert "no A/B setups above floor" in reason


# ── (e) risk-driven closes fire regardless of redeployment ────────────────

def test_gamma_escape_fires_regardless():
    """Gamma escape (DTE ≤ 10, ≥ 30% captured) is a RISK exit — it closes
    even with gates shut and no path."""
    rev = _winner_put(entry=4.00, mid=2.40, dte=5)  # 40% captured, DTE 5
    snap = _snapshot(config=_RD_ON)
    items = render_action_list([], [rev], [], _analytics(coverage=0.17),
                               snap, date_str=TODAY)
    assert any("**CLOSE** VRT_PUT_200" in h for h in _headlines(items))
    assert not rev.get("_redeploy_hold_demotion")


def test_hard_ceiling_fires_regardless():
    """≥ 85% captured always closes — the remaining premium isn't worth
    gamma risk, path or no path."""
    rev = _winner_put(entry=4.00, mid=0.40)  # 90% captured
    snap = _snapshot(config=_RD_ON)
    items = render_action_list([], [rev], [], _analytics(coverage=0.17),
                               snap, date_str=TODAY)
    assert any("**CLOSE** VRT_PUT_200" in h for h in _headlines(items))
    assert not rev.get("_redeploy_hold_demotion")


def test_hold_target_reached_closes_without_path():
    """At/above the raised floor (75%+) the winner closes even with no
    path — the hold-for-more target has been reached."""
    rev = _winner_put(entry=4.00, mid=0.96)  # 76% captured
    snap = _snapshot(config=_RD_ON)
    items = render_action_list([], [rev], [], _analytics(coverage=0.17),
                               snap, date_str=TODAY)
    assert any("**CLOSE** VRT_PUT_200" in h for h in _headlines(items))
    assert not rev.get("_redeploy_hold_demotion")


def test_earnings_window_exit_fires_regardless():
    """A pre-print close (earnings ≤ 2d) is a RISK exit — the binary gap
    outranks the redeployment question."""
    rev = _winner_put()
    snap = _snapshot(config=_RD_ON, earnings={"VRT": "2026-08-13"})
    items = render_action_list([], [rev], [], _analytics(coverage=0.17),
                               snap, date_str=TODAY)
    assert any("**CLOSE** VRT_PUT_200" in h for h in _headlines(items))
    assert not rev.get("_redeploy_hold_demotion")


def test_loss_stop_close_fires_regardless():
    """A loss-stop CLOSE (underwater, GUARDRAIL_LOSS_STOP) is a risk action
    — never gated on redeployment."""
    rev = {
        "contract": "VRT_PUT_200_20261016",
        "underlying": "VRT", "type": "PUT", "qty": -1,
        "strike": 200.0, "expiration": "2026-10-16",
        "entry_price": 2.00, "current_mid": 5.00, "days_to_expiry": 60,
        "recommendation": "CLOSE", "matrix_cell_id": "GUARDRAIL_LOSS_STOP",
        "rationale": "Loss stop triggered: loss ratio 2.50x >= 2.0x",
        "roll_candidates": [],
    }
    snap = _snapshot(config=_RD_ON, quotes={"VRT": {"last": 180.0}})
    items = render_action_list([], [rev], [], _analytics(coverage=0.17),
                               snap, date_str=TODAY)
    assert any("**CLOSE** VRT_PUT_200" in h for h in _headlines(items))
    assert not rev.get("_redeploy_hold_demotion")


def test_block4_close_for_profit_risk_cell_exempt():
    """A CLOSE_FOR_PROFIT whose cell is a RISK guardrail (gamma escape /
    hard ceiling / earnings imminent) renders through block #4 untouched."""
    rev = _winner_put(entry=4.00, mid=2.90, dte=40)  # 27.5% — below block #2
    rev["recommendation"] = "CLOSE_FOR_PROFIT"
    rev["matrix_cell_id"] = "GUARDRAIL_EARNINGS_IMMINENT"
    rev["rationale"] = "Earnings in 5d, DTE=40, profit=28%. Close."
    snap = _snapshot(config=_RD_ON)
    items = render_action_list([], [rev], [], _analytics(coverage=0.17),
                               snap, date_str=TODAY)
    assert any("**CLOSE_FOR_PROFIT** VRT_PUT_200" in h
               for h in _headlines(items))
    assert not rev.get("_redeploy_hold_demotion")


def test_block4_yield_motivated_close_for_profit_demoted():
    """A CLOSE_FOR_PROFIT from the yield-motivated fast-winner layer
    (GUARDRAIL_TIME_ADJUSTED, below-30% capture path through block #4)
    demotes when no path exists — same discipline as block #2."""
    rev = _winner_put(entry=4.00, mid=2.90, dte=40)  # 27.5% captured
    rev["recommendation"] = "CLOSE_FOR_PROFIT"
    rev["matrix_cell_id"] = "GUARDRAIL_TIME_ADJUSTED"
    rev["rationale"] = "Time-adjusted close — fast winner."
    snap = _snapshot(config=_RD_ON)
    items = render_action_list([], [rev], [], _analytics(coverage=0.17),
                               snap, date_str=TODAY)
    assert not any("CLOSE_FOR_PROFIT** VRT_PUT_200" in h
                   for h in _headlines(items))
    note = rev.get("_redeploy_hold_demotion") or ""
    assert "⏳ Holding for 75%+" in note
    assert "Would close at 28% if a path opens" in note


# ── (f) missing inputs → fail-open legacy ─────────────────────────────────

def test_missing_coverage_fails_open_to_legacy():
    """George's rule must never trap winners on MISSING data — with no
    resolvable coverage ratio (analytics None), the legacy CLOSE renders
    untouched (fail-open, rule #19 fail direction)."""
    rev = _winner_put()
    snap = _snapshot(config=_RD_ON)
    items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
    assert any("**CLOSE** VRT_PUT_200" in h for h in _headlines(items))
    assert not rev.get("_redeploy_hold_demotion")
    assert not any("Redeploy path" in ln for ln in items)


# ── (g) config off → byte-identical legacy ────────────────────────────────

def test_config_off_is_legacy_byte_identical():
    """redeploy_aware_tp absent/disabled → the gate never runs: the CLOSE
    renders exactly as legacy even with gates closed, no marker, no
    annotation anywhere."""
    for cfg in ({}, {"redeploy_aware_tp": {"enabled": False}}):
        rev = _winner_put()
        snap = _snapshot(config=cfg)
        items = render_action_list([], [rev], [], _analytics(coverage=0.17),
                                   snap, date_str=TODAY)
        assert any("**CLOSE** VRT_PUT_200" in h for h in _headlines(items))
        assert not rev.get("_redeploy_hold_demotion")
        assert not any("Redeploy path" in ln for ln in items)


# ── (h) Money Plan — held winners aren't banked closes ────────────────────

def test_money_plan_names_held_winners_in_blocked_money():
    """'Bank today' must reflect the hold: a demoted winner is NOT a banked
    close, and Blocked money carries the one-liner
    'N winner(s) held for 75%+ (no redeploy path)'."""
    rev = _winner_put()
    rev["_redeploy_hold_demotion"] = rdp.hold_note(
        52.0, "gates closed 0.17×; no A/B setups above floor", 0.75)
    lines, plan = build_money_plan(
        date_str=TODAY, action_list_lines=[], options_reviews=[rev],
        new_ideas=[], playbook=None, analytics=None, snapshot_data=None,
        config=_RD_ON)
    md = "\n".join(lines)
    assert "**Bank today:** none actionable this cycle" in md
    assert "1 winner held for 75%+ (no redeploy path: VRT $200P)" in md
    assert plan["held_for_more"][0]["label"] == "VRT $200P"


def test_money_plan_without_held_winners_unchanged():
    """No demotions → no held-winner line (and the empty plan still returns
    ([], {}) exactly as legacy)."""
    lines, plan = build_money_plan(
        date_str=TODAY, action_list_lines=[], options_reviews=[_winner_put()],
        new_ideas=[], playbook=None, analytics=None, snapshot_data=None,
        config=_RD_ON)
    assert lines == [] and plan == {}


# ── Risk Alerts must not contradict the hold ──────────────────────────────

def test_risk_alert_rewrites_to_tp_held():
    """The alert surface may not scream CLOSE_FOR_PROFIT while the action
    list holds the winner (the AVGO two-surfaces lesson) — it rewrites to
    'TP HELD — no redeploy path' with the measured note."""
    rev = _winner_put()
    rev["recommendation"] = "CLOSE_FOR_PROFIT"
    rev["rationale"] = "Time-adjusted close"
    rev["_redeploy_hold_demotion"] = rdp.hold_note(
        52.0, "gates closed 0.17×; no A/B setups above floor", 0.75)
    alerts = "\n".join(render_risk_alerts([], [rev], {"regime": "NORMAL"}))
    assert "TP HELD — no redeploy path" in alerts
    assert "→ **CLOSE_FOR_PROFIT**:" not in alerts


# ── module unit coverage ──────────────────────────────────────────────────

def test_post_close_coverage_math():
    """(cash − buyback) / (obligation − freed): the META shape 0.53×; the
    last-obligation close → ∞."""
    an = _analytics(coverage=0.17)
    cov = rdp.post_close_coverage(
        an, {"side": "put", "freed_collateral": 240_000.0,
             "btc_cost": 1_920.0})
    assert abs(cov - (58_080.0 / 110_000.0)) < 1e-9
    cov_inf = rdp.post_close_coverage(
        an, {"side": "put", "freed_collateral": 350_000.0, "btc_cost": 0.0})
    assert cov_inf == float("inf")


def test_call_side_close_is_legacy():
    """A covered-call buyback frees shares, not gated cash — call-side
    closes keep legacy behavior (path True, no annotation)."""
    ok, reason = rdp.redeployment_path(
        _analytics(coverage=0.17), None,
        {"side": "call", "freed_collateral": 0.0, "btc_cost": 500.0},
        config=_RD_ON)
    assert ok is True and reason == ""


def test_module_fails_open_on_unresolvable_coverage():
    """No resolvable ratio → (True, "") — missing data must never trap
    winners at a raised floor."""
    assert rdp.redeployment_path(None, None, None, config=_RD_ON) == (True, "")
    assert rdp.redeployment_path({}, None, None, config=_RD_ON) == (True, "")


def test_close_impact_from_review_measures_real_numbers():
    """Impact is measured from the review: strike × 100 × |qty| collateral,
    mid × 100 × |qty| buyback."""
    imp = rdp.close_impact_from_review(_winner_put())
    assert imp == {"side": "put", "freed_collateral": 20_000.0,
                   "btc_cost": 192.0}
