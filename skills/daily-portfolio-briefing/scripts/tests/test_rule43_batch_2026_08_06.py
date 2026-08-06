"""Rule #43 batch — two defects observed in the 2026-08-06 briefing.

Defect 1 — gamma-escape closes on short CALLS never reached the action list
(two days running). Risk Alerts rendered:

    🎯 SPY_CALL_784_20260811 → **CLOSE_FOR_PROFIT**: Gamma-escape close —
    DTE 5 ≤ 10, captured 77%. Lock the win before the …

while the action list contained NO SPY close either day (08-05: DTE 6,
captured 31%). The Watch panel showed why: "⏸ +77% captured but only $87 —
below the $100 action floor" — the close-winner dollar floor's
always-surface exception covered gamma-week closes on short PUTS only.

Defect 2 — exit-verdict changes day-over-day were silent (QCOM whiplash):
QCOM_PUT_185_20261218 rendered HOLD FOR BASIS (Aug 4) → churn-guarded
(Aug 5) → "EXECUTE ROLL −$783 … ⚖️ Verdict: ROLL, don't close" (Aug 6) —
same position, no explanation of what changed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.exit_cost import (  # noqa: E402
    ExitCostAnatomy,
    anatomy_persist_entry,
    build_today_verdicts,
    verdict_change_line,
    verdict_of,
)
from render.panels import (  # noqa: E402
    _exit_cost_lines,
    render_action_list,
    render_risk_alerts,
)

TODAY = "2026-08-06"


def _snapshot(quotes=None, chains=None, iv_ranks=None, config=None,
              prior_verdicts=None):
    snap = {
        "quotes": quotes or {},
        "chains": chains or {},
        "iv_ranks": iv_ranks or {},
        "earnings_calendar": {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }
    if prior_verdicts is not None:
        snap["_prior_exit_verdicts"] = {"date": "2026-08-05",
                                        "verdicts": prior_verdicts}
    return snap


def _headlines(items):
    return [ln for ln in items if ln.lstrip()[:1].isdigit()]


# ---------------------------------------------------------------------------
# Defect 1 — gamma-escape close on a short CALL
# ---------------------------------------------------------------------------


def _spy_call_review():
    """The observed SPY shape: short $784 CALL, DTE 5, entry $1.13 / mid
    $0.26 → 77% captured, only $87 banked (below the $100 floor)."""
    return {
        "contract": "SPY_CALL_784_20260811",
        "underlying": "SPY", "type": "CALL", "qty": -1,
        "strike": 784.0, "expiration": "2026-08-11",
        "entry_price": 1.13, "current_mid": 0.26, "days_to_expiry": 5,
        "recommendation": "CLOSE_FOR_PROFIT",
        "matrix_cell_id": "GUARDRAIL_GAMMA_ESCAPE",
        "rationale": ("Gamma-escape close — DTE 5 ≤ 10, captured 77%. "
                      "Lock the win before the gamma-risk zone."),
        "roll_candidates": [],
    }


def test_gamma_escape_call_surfaces_as_action():
    """Observed 2026-08-06: Risk Alerts said '🎯 SPY_CALL_784_20260811 →
    **CLOSE_FOR_PROFIT**: Gamma-escape close — DTE 5 ≤ 10, captured 77%'
    while the action list contained no SPY close (same on 08-05 at DTE 6 /
    31%) — the $87 profit fell under the $100 floor and the gamma-week
    exception was put-only. A gamma-escape close (DTE ≤ 10, capture ≥ 30%)
    must surface as a numbered action regardless of dollar size, put OR
    call, with the gamma rationale."""
    rev = _spy_call_review()
    snap = _snapshot(quotes={"SPY": {"last": 770.0}})
    items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
    heads = _headlines(items)
    assert any("**CLOSE** SPY_CALL_784_20260811" in h for h in heads), (
        "gamma-escape CALL close must be a numbered action")
    assert not rev.get("_close_floor_demotion"), (
        "gamma-escape close must not be floored to a Watch note")
    body = "\n".join(items)
    assert "Gamma-escape" in body and "DTE 5 ≤ 10" in body, (
        "the gamma rationale must travel with the action")


def test_gamma_escape_put_still_surfaces():
    """The put side of the same exception keeps working: a DTE ≤ 10 short
    PUT at ≥30% capture below the dollar floor still surfaces as a
    numbered action."""
    rev = {
        "contract": "IREN_PUT_20_20260807",
        "underlying": "IREN", "type": "PUT", "qty": -1,
        "strike": 20.0, "expiration": "2026-08-07",
        "entry_price": 0.50, "current_mid": 0.20, "days_to_expiry": 3,
        "recommendation": "HOLD", "matrix_cell_id": "X",
        "roll_candidates": [],
    }
    snap = _snapshot(quotes={"IREN": {"last": 25.0}})
    items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
    assert any("**CLOSE** IREN_PUT_20" in h for h in _headlines(items))
    assert not rev.get("_close_floor_demotion")
    assert "Gamma-escape" in "\n".join(items)


def test_floor_still_demotes_non_gamma_small_closes():
    """The dollar floor itself is unchanged: a small close OUTSIDE the
    gamma window (DTE 31, $27 banked — the original AMZN case) still
    demotes to a Watch note."""
    rev = {
        "contract": "AMZN_CALL_330_20260904",
        "underlying": "AMZN", "type": "CALL", "qty": -1,
        "strike": 330.0, "expiration": "2026-09-04",
        "entry_price": 0.90, "current_mid": 0.63, "days_to_expiry": 31,
        "recommendation": "HOLD", "matrix_cell_id": "X",
        "roll_candidates": [],
    }
    snap = _snapshot(quotes={"AMZN": {"last": 215.0}})
    items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
    assert not any("**CLOSE** AMZN_CALL_330" in h for h in _headlines(items))
    assert "below the $100 action floor" in (
        rev.get("_close_floor_demotion") or "")


# ---------------------------------------------------------------------------
# Defect 2 — verdict-change transparency (QCOM whiplash)
# ---------------------------------------------------------------------------


def _qcom_review():
    """QCOM_PUT_185_20261218 shape on 2026-08-06: ITM short put whose
    anatomy resolves ROLL_DONT_CLOSE (extrinsic 35% pumped, IV rank 69,
    DTE 134)."""
    return {
        "contract": "QCOM_PUT_185_20261218",
        "underlying": "QCOM", "type": "PUT", "qty": -1,
        "strike": 185.0, "expiration": "2026-12-18",
        "entry_price": 8.00, "current_mid": 19.50, "days_to_expiry": 134,
        "recommendation": "ROLL_OUT_AND_DOWN", "matrix_cell_id": "X",
        "roll_candidates": [],
    }


def _qcom_snapshot(prior_verdicts=None):
    # spot 172.33 → intrinsic 12.67; chain mid 19.50 → extrinsic 6.83 = 35%
    # of the buyback (pumped); IV rank 69 (elevated) → ROLL_DONT_CLOSE.
    return _snapshot(
        quotes={"QCOM": {"last": 172.33}},
        chains={"QCOM_2026-12-18": {
            "puts": [{"strike": 185.0, "bid": 19.0, "ask": 20.0}]}},
        iv_ranks={"QCOM": 69.0},
        prior_verdicts=prior_verdicts,
    )


def test_verdict_change_line_with_drivers():
    """Observed QCOM whiplash: 'Assignment acceptable — hold for basis'
    (Aug 4) became '⚖️ Verdict: ROLL, don't close' on the Aug 6 EXECUTE
    ROLL −$783 card with NO explanation of what changed. The anatomy block
    must attribute the transition from the persisted fields: extrinsic
    28%→35% crossed the 30% pumped threshold, IV rank 61→69."""
    prior = {"QCOM_PUT_185_20261218": {
        "verdict": "HOLD_FOR_BASIS", "extrinsic_pct": 0.28, "iv_rank": 61.0,
        "intrinsic_per_share": 10.0, "dte": 136}}
    rev = _qcom_review()
    lines = _exit_cost_lines(rev, _qcom_snapshot(prior_verdicts=prior))
    body = "\n".join(lines)
    assert "⚖️ Verdict: ROLL, don't close" in body
    assert ("_Verdict changed vs yesterday: HOLD_FOR_BASIS → "
            "ROLL_DONT_CLOSE — extrinsic 28%→35% (crossed the 30% pumped "
            "threshold), IV rank 61→69._") in body


def test_no_change_no_line():
    """Same verdict as yesterday → no 'Verdict changed' line (and no prior
    entry at all → no line either)."""
    prior = {"QCOM_PUT_185_20261218": {
        "verdict": "ROLL_DONT_CLOSE", "extrinsic_pct": 0.34,
        "iv_rank": 67.0, "intrinsic_per_share": 12.0, "dte": 135}}
    rev = _qcom_review()
    body = "\n".join(_exit_cost_lines(rev, _qcom_snapshot(prior_verdicts=prior)))
    assert "Verdict changed" not in body
    # No prior persisted at all — also silent.
    rev2 = _qcom_review()
    body2 = "\n".join(_exit_cost_lines(rev2, _qcom_snapshot()))
    assert "Verdict changed" not in body2


def test_unattributable_fallback():
    """When yesterday's entry is the legacy bare string (no anatomy
    fields), the drivers can't be identified — render the honest fallback:
    '_Verdict changed vs yesterday (BASIS → ROLL) — drivers not fully
    attributable; treat both as defensible and decide by intent._'"""
    prior = {"QCOM_PUT_185_20261218": "HOLD_FOR_BASIS"}
    rev = _qcom_review()
    body = "\n".join(_exit_cost_lines(rev, _qcom_snapshot(prior_verdicts=prior)))
    assert ("_Verdict changed vs yesterday (BASIS → ROLL) — drivers not "
            "fully attributable; treat both as defensible and decide by "
            "intent._") in body


def _anatomy(verdict, contract="QCOM_PUT_185_20261218", ext_pct=0.35,
             intrinsic=12.67, earnings_state=None):
    mid = 19.5
    return ExitCostAnatomy(
        contract=contract, spot=172.33, strike=185.0,
        intrinsic_per_share=intrinsic,
        extrinsic_per_share=ext_pct * mid,
        extrinsic_total=ext_pct * mid * 100,
        spread_per_share=1.0, spread_pct_of_mid=0.05,
        premium_received_per_share=8.0, assignment_basis=177.0,
        basis_vs_spot_pct=0.027, iv_context="elevated",
        earnings_state=earnings_state, verdict=verdict,
        verdict_reason="test", btc_mid=mid, bid=19.0, ask=20.0,
        intrinsic_total=intrinsic * 100, qty=1.0)


def test_churn_gap_not_a_change():
    """A churn-guarded / uncomputable day must persist the LAST computed
    verdict (carry-forward), so the gap day doesn't read as a change and
    the day after compares against the real prior read — the QCOM Aug 5
    churn-guard day must not swallow the Aug 4 HOLD_FOR_BASIS baseline."""
    day1_entry = anatomy_persist_entry(
        _anatomy("HOLD_FOR_BASIS", ext_pct=0.28, intrinsic=10.0),
        dte=136, iv_rank=61.0)
    rev_gap = _qcom_review()
    # Day 2 (churn gap): anatomy not computable → prior entry carried.
    day2 = build_today_verdicts(
        [rev_gap], {"QCOM_PUT_185_20261218": day1_entry}, {},
        lambda _r: None)
    assert day2["QCOM_PUT_185_20261218"] == day1_entry
    assert "_verdict_change" not in rev_gap, (
        "a gap day must not be flagged as a verdict change")
    # Day 3: same verdict as the carried entry → still no change.
    assert verdict_change_line(
        _anatomy("HOLD_FOR_BASIS"), day2["QCOM_PUT_185_20261218"]) is None
    # Day 3 alt: a REAL change vs the carried entry is still detected.
    assert verdict_change_line(
        _anatomy("ROLL_DONT_CLOSE"), day2["QCOM_PUT_185_20261218"],
        iv_rank=69.0, dte=134) is not None


def test_risk_alert_clock_prefix_on_verdict_change():
    """Risk Alerts convention (mirrors credit-window transitions): when the
    verdict CHANGED today, the 🎯 alert line carries a ⏰ prefix."""
    rev = _qcom_review()
    prior = {"QCOM_PUT_185_20261218": {
        "verdict": "HOLD_FOR_BASIS", "extrinsic_pct": 0.28,
        "iv_rank": 61.0, "intrinsic_per_share": 10.0, "dte": 136}}
    snap = _qcom_snapshot(prior_verdicts=prior)
    # Simulate aggregate's persistence pass (which stashes the flag).
    from render.panels import _exit_cost_anatomy
    today = build_today_verdicts(
        [rev], prior, snap["iv_ranks"],
        lambda r: _exit_cost_anatomy(r, snap, [], TODAY,
                                     include_near_money=True)[0])
    assert rev.get("_verdict_change") == "HOLD_FOR_BASIS → ROLL_DONT_CLOSE"
    assert verdict_of(today["QCOM_PUT_185_20261218"]) == "ROLL_DONT_CLOSE"
    alerts = "\n".join(render_risk_alerts([], [rev], {}))
    assert "⏰ 🎯 QCOM_PUT_185_20261218" in alerts
    # Without the flag, no clock.
    rev2 = _qcom_review()
    alerts2 = "\n".join(render_risk_alerts([], [rev2], {}))
    assert "⏰ 🎯 QCOM_PUT_185_20261218" not in alerts2
    assert "🎯 QCOM_PUT_185_20261218" in alerts2
