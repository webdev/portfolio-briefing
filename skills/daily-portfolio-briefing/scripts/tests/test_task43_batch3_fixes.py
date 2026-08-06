"""Task #43 batch 3 (2026-08-03) — defects from the 11:37 AM briefing.

Each test's docstring quotes the observed output (house TDD rule #33 — the
test must fail against the broken code and pass against the fix).

Defect 1: "CLOSE PLTR_PUT_130_20270115 — exit-cost verdict: close after the
IV crush ... **Why:** earnings printed — 79% of the buyback is still time
value. Let the IV crush work for the first hour" — rendered at 11:37 AM
Monday while PLTR reports AFTER THE CLOSE tonight. days_to_earnings == 0 was
classified "printed_today"; the mistimed CLOSE_AFTER_CRUSH would have the
user pay PEAK pre-print IV.

Defect 2: Friday's "CLOSE PLTR_CALL_200_20261218 +37%" (4th consecutive
session flagged) decayed to +28.6% capture by Monday, slipped under the 30%
CLOSE-WINNERS floor, and vanished from the action list on the day of the
print — the diff said "✗ NOT FILLED (position unchanged — dropped without
execution)" and the call appeared NOWHERE in the 13-item list.

(Defect 3 — policer allow-list rejecting 'yfinance+fmp_fallback' — is tested
in skills/live-data-policer/scripts/tests/test_police.py.)
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.exit_cost import (  # noqa: E402
    analyze_exit_cost,
    classify_earnings_state,
)
from render.panels import render_action_list  # noqa: E402

TODAY = "2026-08-03"


# ── Shared fixtures (real 2026-08-03 snapshot numbers) ─────────────────────

def _pltr_put_130(mid=20.475, entry=21.3):
    """PLTR $130P Jan 15 '27 — spot $125.65 (ITM), +4% captured, BTC mid
    $20.48 = $435 intrinsic + $1,613 extrinsic (79%)."""
    return {
        "contract": "PLTR_PUT_130_20270115",
        "underlying": "PLTR", "type": "PUT", "qty": -1.0,
        "strike": 130.0, "expiration": "2027-01-15",
        "entry_price": entry, "current_mid": mid, "days_to_expiry": 165,
    }


def _pltr_call_200(mid=3.25):
    """PLTR $200C Dec 18 '26 ×7 — entry $4.5499; mid $3.25 → 28.6% capture."""
    return {
        "contract": "PLTR_CALL_200_20261218",
        "underlying": "PLTR", "type": "CALL", "qty": -7.0,
        "strike": 200.0, "expiration": "2026-12-18",
        "entry_price": 4.5499, "current_mid": mid, "days_to_expiry": 137,
        "recommendation": "HOLD",
        "matrix_cell_id": "CALL_NORMAL_OTM_HOLD",
        "roll_candidates": [],
    }


def _snapshot(earnings=None):
    return {
        "quotes": {"PLTR": {"last": 125.65}},
        "chains": {},
        "iv_ranks": {"PLTR": 57.79},
        "earnings_calendar": earnings if earnings is not None
        else {"PLTR": "2026-08-03"},
        "_config": {"core_positions": [], "accounts": []},
        "balance": {"accountValue": 1_097_283, "cash": 70_680},
        "positions": [],
    }


# ── Defect 1: 0d earnings is IMMINENT, never "printed" without evidence ────

def test_pltr_put_0d_earnings_is_imminent_not_close_after_crush():
    """The 11:37 AM Monday briefing said 'earnings printed — let the IV
    crush work for the first hour' on a put whose print was AMC that
    evening. Same-day earnings with no session info must classify as
    pre_print_0d (fail-safe) and must NOT produce CLOSE_AFTER_CRUSH."""
    a = analyze_exit_cost(
        _pltr_put_130(), {"bid": 20.35, "ask": 20.60}, spot=125.65,
        iv_rank=57.79, earnings_date="2026-08-03", today=TODAY,
        concentration_ok=True,
    )
    assert a is not None
    assert a.earnings_state == "pre_print_0d"
    assert a.verdict != "CLOSE_AFTER_CRUSH"
    # The verdict must reflect the imminent print, not a phantom crush
    assert "imminent" in a.verdict_reason
    assert "NOT yet printed" in a.verdict_reason
    # Profitable ITM put with basis cushion + concentration headroom →
    # holding for basis (Friday's own verdict for this position)
    assert a.verdict == "HOLD_FOR_BASIS"


def test_pltr_put_next_trading_day_close_after_crush_allowed():
    """Same shape evaluated the NEXT trading day (the AMC print has
    definitely happened) → CLOSE_AFTER_CRUSH is allowed again."""
    a = analyze_exit_cost(
        _pltr_put_130(), {"bid": 20.35, "ask": 20.60}, spot=125.65,
        iv_rank=57.79, earnings_date="2026-08-03", today="2026-08-04",
        concentration_ok=True,
    )
    assert a is not None
    assert a.earnings_state == "printed_today"
    assert a.verdict == "CLOSE_AFTER_CRUSH"


def test_session_evidence_gates_same_day_printed():
    """Affirmative-evidence ladder: BMO + generation after 10:00 ET →
    printed; AMC / no session / BMO-before-10 → NOT printed (imminent)."""
    late = datetime(2026, 8, 3, 11, 37)
    early = datetime(2026, 8, 3, 8, 0)
    assert classify_earnings_state(
        "2026-08-03", TODAY, session="BMO", now=late) == "printed_today"
    assert classify_earnings_state(
        "2026-08-03", TODAY, session="BMO", now=early) == "pre_print_0d"
    assert classify_earnings_state(
        "2026-08-03", TODAY, session="AMC", now=late) == "pre_print_0d"
    assert classify_earnings_state(
        "2026-08-03", TODAY) == "pre_print_0d"


def test_underwater_put_0d_close_urgent_even_when_extrinsic_pumped():
    """An UNDERWATER short put on print day exits before the binary even
    with pumped extrinsic — the old code required extrinsic < 30% and would
    instead have fired CLOSE_AFTER_CRUSH ('let the crush work') pre-print."""
    pos = {
        "contract": "PLTR_PUT_125_20261120",
        "underlying": "PLTR", "type": "PUT", "qty": -1.0,
        "strike": 125.0, "expiration": "2026-11-20",
        "entry_price": 15.49, "current_mid": 16.5, "days_to_expiry": 109,
    }
    a = analyze_exit_cost(
        pos, {"bid": 16.40, "ask": 16.60}, spot=125.65,
        iv_rank=60.83, earnings_date="2026-08-03", today=TODAY,
    )
    assert a is not None
    assert a.earnings_state == "pre_print_0d"
    assert a.verdict == "CLOSE_URGENT"
    assert "imminent" in a.verdict_reason


# ── Defect 2: profitable short option + earnings ≤ 1d must surface ─────────

def _render(reviews, snap):
    return "\n".join(render_action_list([], reviews, [], snapshot_data=snap,
                                        date_str=TODAY))


def test_profitable_call_earnings_0d_surfaces_close():
    """'the call appears NOWHERE in today's 13-item action list — on the day
    its urgency peaks (print tonight)'. A profitable short call (+29%
    capture) with earnings 0d away must surface a CLOSE decision item with
    the pre-print rationale."""
    md = _render([_pltr_call_200()], _snapshot())
    assert "**CLOSE** PLTR_CALL_200_20261218" in md
    assert "+29%" in md
    assert "BEFORE the print" in md
    assert "re-sell after the IV crush" in md


def test_capture_29_without_imminent_earnings_stays_unsurfaced():
    """Over-application guard: the same +28.6% capture with earnings months
    out stays under the 30% CLOSE-WINNERS floor — no churn."""
    md = _render([_pltr_call_200()], _snapshot(earnings={"PLTR": "2026-11-02"}))
    assert "**CLOSE** PLTR_CALL_200_20261218" not in md


def test_capture_below_20_not_forced_even_on_print_day():
    """The pre-print relaxation floors at 20% capture — a barely-profitable
    short option isn't force-closed into peak IV just because earnings are
    imminent (the earnings-guard/watch surfaces still cover it)."""
    md = _render([_pltr_call_200(mid=4.0)], _snapshot())  # ~12% capture
    assert "**CLOSE** PLTR_CALL_200_20261218" not in md


def test_capture_over_30_keeps_standard_close_why():
    """Friday's shape (+37%, earnings 3d — outside the ≤1d window) renders
    the standard close-at-50% rationale, unchanged."""
    md = _render([_pltr_call_200(mid=2.855)],
                 _snapshot(earnings={"PLTR": "2026-08-06"}))
    assert "**CLOSE** PLTR_CALL_200_20261218" in md
    assert "close-at-50% rule" in md
