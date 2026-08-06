"""Task #38 (2026-07-30) — put roll-trigger discipline, briefing surfaces.

User symptom, verbatim from the task: "MU $950P on Jul 22 (spot $959, 0.9%
above strike, +12%) → silent HOLD; one week later $211 ITM, roll = $4,700
debit. The at-the-money moment is when extrinsic peaks and credit rolls are
biggest — the system watched both cross in silence. Fire at the strike, not
through it; make the credit window visible."

Pins the four briefing-side behaviors:
  1. the Credit-window line renders on held short puts in the Watch panel;
  2. open→closing / open→debit_only transitions emit a Risk Alert (and a
     first run with no history emits none — fail-open);
  3. a tested short put at ≤21 DTE gets a mandatory ⛔ decision item in the
     action list;
  4. a standing directive downgrades that item to a transparency note (the
     note renders, the nag doesn't).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.advisor_directives import HoldDirective  # noqa: E402
from analysis.credit_windows import (  # noqa: E402
    build_credit_windows,
    load_previous_credit_windows,
    persist_credit_windows,
    transition_alerts,
)
from render.panels import render_action_list, render_risk_alerts  # noqa: E402
from steps.per_option_commentary import render_watch_with_commentary  # noqa: E402

TODAY = "2026-07-30"


def _short_put_review(**overrides):
    rev = {
        "contract": "MU_PUT_950_20261120",
        "underlying": "MU", "type": "PUT", "qty": -1,
        "strike": 950.0, "expiration": "2026-11-20",
        "entry_price": 20.0, "current_mid": 17.6, "days_to_expiry": 113,
        "recommendation": "HOLD", "matrix_cell_id": "DEFAULT_HOLD",
        "roll_candidates": [
            {"id": "A", "description": "HOLD (don't roll)", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "1× $950P Dec 18 '26",
             "instruction": {"sell_strike": 950.0,
                             "sell_expiration": "2026-12-18"},
             "netDollars": 185.0, "dteExtension": 28},
        ],
    }
    rev.update(overrides)
    return rev


def _snapshot(mu_last=959.0, **extra):
    snap = {
        "quotes": {"MU": {"last": mu_last}},
        "technicals": {},
        "earnings_calendar": {},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
        "_config": {"core_positions": [], "accounts": []},
    }
    snap.update(extra)
    return snap


# ── 1. Watch-panel credit-window line ──────────────────────────────────────

def test_credit_window_line_renders():
    """Every held short put shows its credit-roll window one line above the
    ROLL ANALYSIS table — best same-or-lower-strike credit, per share."""
    md = "\n".join(render_watch_with_commentary(
        [], [_short_put_review()], _snapshot()))
    assert "**Credit window: 🟢 OPEN**" in md
    # $185 net on 1 contract = $1.85/share — a measured number, not boilerplate
    assert "$1.85/share" in md
    # It renders above the ROLL ANALYSIS table
    assert md.index("Credit window") < md.index("ROLL ANALYSIS")


def test_credit_window_line_debit_only():
    rev = _short_put_review()
    rev["roll_candidates"][1]["netDollars"] = -470.0
    md = "\n".join(render_watch_with_commentary([], [rev], _snapshot()))
    assert "**Credit window: 🔴 DEBIT-ONLY**" in md
    assert "close / accept assignment / pay for cushion" in md


def test_credit_window_line_absent_on_short_calls():
    rev = _short_put_review(contract="SMH_CALL_600_20261120",
                            underlying="SMH", type="CALL")
    md = "\n".join(render_watch_with_commentary(
        [], [rev], _snapshot(**{"quotes": {"SMH": {"last": 580.0}}})))
    assert "Credit window" not in md


# ── 2. Transition alert (open → closing / debit_only) ──────────────────────

def test_credit_window_transition_alert(tmp_path):
    """Yesterday OPEN, today DEBIT-ONLY → a ⏰ Risk Alert names the contract;
    first run (no history) → no alert (fail-open)."""
    yesterday_dir = tmp_path / "2026-07-29"
    yday_reviews = [_short_put_review()]           # net +$185 → open
    persist_credit_windows(yesterday_dir,
                           build_credit_windows(yday_reviews, {}),
                           "2026-07-29")

    today_rev = _short_put_review()
    today_rev["roll_candidates"][1]["netDollars"] = -470.0   # → debit_only
    today_map = build_credit_windows([today_rev], {})
    prev_map, prev_date = load_previous_credit_windows(tmp_path, TODAY)
    assert prev_date == "2026-07-29"
    alerts = transition_alerts(today_map, prev_map, prev_date)
    assert len(alerts) == 1
    assert "MU_PUT_950_20261120" in alerts[0]
    assert "DEBIT-ONLY" in alerts[0]
    assert "was open on 2026-07-29" in alerts[0]

    # The alert flows into the Risk Alerts panel verbatim
    md = "\n".join(render_risk_alerts([], [], {"regime": "NORMAL"},
                                      credit_window_alerts=alerts))
    assert "credit-roll window now DEBIT-ONLY" in md

    # First run: no history anywhere → no alert
    empty_prev, no_date = load_previous_credit_windows(tmp_path / "elsewhere",
                                                       TODAY)
    assert transition_alerts(today_map, empty_prev, no_date) == []


def test_credit_window_open_to_closing_alert(tmp_path):
    persist_credit_windows(tmp_path / "2026-07-29",
                           build_credit_windows([_short_put_review()], {}),
                           "2026-07-29")
    today_rev = _short_put_review()
    today_rev["roll_candidates"][1]["netDollars"] = 30.0     # $0.30/sh → closing
    prev_map, prev_date = load_previous_credit_windows(tmp_path, TODAY)
    alerts = transition_alerts(build_credit_windows([today_rev], {}),
                               prev_map, prev_date)
    assert len(alerts) == 1
    assert "CLOSING" in alerts[0]
    assert "act while a credit remains" in alerts[0]


# ── 3. Forced decision at ≤21 DTE on a tested short put ────────────────────

def test_forced_decision_at_21_dte_tested():
    """A short put ITM/NEAR_ATM with DTE ≤ 21 and no other actionable item
    gets the mandatory ⛔ decision item — the gap the guardrail (min_dte 21)
    deliberately hands off to the briefing."""
    rev = _short_put_review(days_to_expiry=18, current_mid=22.0,  # underwater
                            roll_candidates=[])
    md = "\n".join(render_action_list([], [rev], [],
                                      snapshot_data=_snapshot(mu_last=941.0),
                                      date_str=TODAY))
    assert "⛔ **TESTED ≤21 DTE** MU_PUT_950_20261120" in md
    assert "accept-assignment directive" in md
    assert "**⛔ DECISION REQUIRED:**" in md
    # Measured numbers, not boilerplate
    assert "$941.00" in md and "$950.00" in md


def test_forced_decision_not_fired_when_comfortably_otm():
    """Spot 10% above strike at 18 DTE is theta's job — no nag."""
    rev = _short_put_review(days_to_expiry=18, roll_candidates=[])
    md = "\n".join(render_action_list([], [rev], [],
                                      snapshot_data=_snapshot(mu_last=1045.0),
                                      date_str=TODAY))
    assert "TESTED ≤21 DTE" not in md


def test_forced_decision_not_fired_above_dte_cap():
    """At 113 DTE the strike-tested guardrail owns the moment, not the
    forced-decision item."""
    rev = _short_put_review(roll_candidates=[])
    md = "\n".join(render_action_list([], [rev], [],
                                      snapshot_data=_snapshot(mu_last=941.0),
                                      date_str=TODAY))
    assert "TESTED ≤21 DTE" not in md


# ── 4. Directive-held contract: note, not nag ──────────────────────────────

def test_forced_decision_respects_directive():
    """A standing hold directive on the tested contract renders a
    transparency note instead of the ⛔ mandatory item."""
    directive = HoldDirective(
        contract="MU_PUT_950_20261120", ticker="MU", type="hold",
        release_capture_and_dte=None, release_capture_below=None,
        release_spot_below=None, raw_text="hold — accept assignment if tested",
    )
    rev = _short_put_review(days_to_expiry=18, current_mid=22.0,
                            roll_candidates=[])
    snap = _snapshot(mu_last=941.0, _advisor_directives=[directive])
    md = "\n".join(render_action_list([], [rev], [],
                                      snapshot_data=snap, date_str=TODAY))
    assert "⛔ **TESTED ≤21 DTE**" not in md          # no nag
    assert "held by standing directive" in md          # note renders (rule #24)
    assert "MU_PUT_950_20261120" in md
