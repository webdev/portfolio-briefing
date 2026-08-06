"""TSM 2026-07-30 — untested OTM put must never surface an EXECUTE ROLL.

User symptom, verbatim (today's rerun, action #1): "EXECUTE ROLL
TSM_PUT_380_20260828 — diagonal down-and-out, −$838 debit. Position state:
spot $402.92, strike $380 → spot is 6% ABOVE the strike (moneyness 1.0603),
delta -0.10, DTE 29, underwater only $103, exit anatomy shows $0 intrinsic +
$1,132 extrinsic (100%), no earnings in window. This is an OTM put where
theta works entirely for the holder — correct action is HOLD, not a debit
roll."

Three fixes pinned here:
1. analysis/exit_cost.py grows a HOLD_FOR_DECAY verdict (priority URGENT →
   CLEAN → CRUSH → HOLD_FOR_DECAY → ROLL → BASIS → NEUTRAL; loss-stop still
   overrides).
2. render/panels.py enforces the CLAUDE.md rule-#3 gate (moneyness < 1.03 OR
   measured |δ| ≥ 0.40) on EVERY path that surfaces an actionable short-put
   roll: block #3 (priced candidates), block #4 (generic roll directives),
   and the 4c forced-decision item. Failing items demote to a Watch note.
3. The matrix cell PUT_NORMAL_NEAR_ATM_UNDERWATER_ROLL requires a genuine
   test (pinned in wheel-roll-advisor's test_put_matrix_coverage.py).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.exit_cost import analyze_exit_cost  # noqa: E402
from render.panels import (  # noqa: E402
    _short_put_roll_gate_ok,
    render_action_list,
)
from steps.per_option_commentary import render_watch_with_commentary  # noqa: E402


TODAY = "2026-07-30"


# ── Fixtures (real TSM 2026-07-30 shape) ───────────────────────────────────

def _tsm_review(**over):
    r = {
        "contract": "TSM_PUT_380_20260828",
        "underlying": "TSM", "type": "PUT", "qty": -1,
        "strike": 380.0, "expiration": "2026-08-28",
        "entry_price": 10.29, "current_mid": 11.32, "days_to_expiry": 29,
        "delta": -0.10,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_NEAR_ATM_UNDERWATER_ROLL",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "1× $355P Sep 25 '26",
             "instruction": {"sell_strike": 355.0,
                             "sell_expiration": "2026-09-25",
                             "sell_mid": 8.94, "sell_bid": 8.60,
                             "sell_ask": 9.30},
             "netDollars": -838.0, "dteExtension": 28,
             "deltaChange": -0.25},
        ],
    }
    r.update(over)
    return r


def _tsm_snapshot(earnings=None, config=None):
    return {
        "quotes": {"TSM": {"last": 402.92}},
        "chains": {"TSM_2026-08-28": {"puts": [
            {"strike": 380.0, "bid": 11.12, "ask": 11.52}]}},
        "iv_ranks": {"TSM": 65},
        "earnings_calendar": earnings or {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }


def _pos(**over):
    p = {
        "contract": "TSM_PUT_380_20260828", "underlying": "TSM",
        "type": "PUT", "qty": -1, "strike": 380.0,
        "entry_price": 10.29, "current_mid": 11.32, "days_to_expiry": 29,
        "delta": -0.10, "expiration": "2026-08-28",
    }
    p.update(over)
    return p


_QUOTE = {"bid": 11.12, "ask": 11.52}


def _render(reviews, snap):
    return "\n".join(render_action_list([], reviews, [], snapshot_data=snap,
                                        date_str=TODAY))


# ── Fix 3: the HOLD_FOR_DECAY verdict (analysis/exit_cost.py) ──────────────

def test_hold_for_decay_verdict():
    """TSM shape (fully OTM, no earnings in window, |δ| < 0.40) →
    HOLD_FOR_DECAY. Earnings 10d inside the window → not. δ 0.45 → not."""
    a = analyze_exit_cost(_pos(), _QUOTE, spot=402.92, iv_rank=65,
                          today=TODAY)
    assert a is not None
    assert a.intrinsic_per_share == 0.0
    assert a.verdict == "HOLD_FOR_DECAY"
    assert "decaying in your favor" in a.verdict_reason
    assert "no exit needed" in a.verdict_reason

    # Earnings 10d away — inside the 29d contract window → not HOLD_FOR_DECAY
    a2 = analyze_exit_cost(_pos(), _QUOTE, spot=402.92, iv_rank=65,
                           earnings_date="2026-08-09", today=TODAY)
    assert a2 is not None and a2.verdict != "HOLD_FOR_DECAY"

    # Genuine test (measured δ 0.45) → not HOLD_FOR_DECAY
    a3 = analyze_exit_cost(_pos(delta=-0.45), _QUOTE, spot=402.92,
                           iv_rank=65, today=TODAY)
    assert a3 is not None and a3.verdict != "HOLD_FOR_DECAY"

    # Earnings AFTER expiration (outside window) → still HOLD_FOR_DECAY
    a4 = analyze_exit_cost(_pos(), _QUOTE, spot=402.92, iv_rank=65,
                           earnings_date="2026-10-15", today=TODAY)
    assert a4 is not None and a4.verdict == "HOLD_FOR_DECAY"


def test_hold_for_decay_price_fallback_without_delta():
    """No measured delta → price fallback: spot > 1.03 × strike qualifies;
    spot inside the band does not (never fabricate a delta)."""
    a = analyze_exit_cost(_pos(delta=None), _QUOTE, spot=402.92, iv_rank=65,
                          today=TODAY)
    assert a is not None and a.verdict == "HOLD_FOR_DECAY"
    # Spot only 1% above strike (underwater, fully OTM) → tested band →
    # ROLL_DONT_CLOSE territory, not a bare hold
    a2 = analyze_exit_cost(_pos(delta=None), _QUOTE, spot=383.8, iv_rank=65,
                           today=TODAY)
    assert a2 is not None and a2.verdict != "HOLD_FOR_DECAY"


def test_hold_for_decay_never_on_loss_stop():
    """A loss-stopped position can't get HOLD_FOR_DECAY — the verdict may
    retime the exit, never undo it (falls through to ROLL_DONT_CLOSE)."""
    a = analyze_exit_cost(_pos(), _QUOTE, spot=402.92, iv_rank=65,
                          today=TODAY, loss_stop_fired=True)
    assert a is not None
    assert a.verdict != "HOLD_FOR_DECAY"


# ── Fix 2: rule-#3 gate on every surfacing path ────────────────────────────

def test_roll_gate_helper():
    assert _short_put_roll_gate_ok(0.97, None)          # ITM, no delta
    # Task #40 fix 6 (AVGO 2026-07-30): a MEASURED |δ| < 0.25 fails the gate
    # REGARDLESS of moneyness — the old assertion here (1.02 + δ 0.10 → ok)
    # was the exact shape that produced an $871 defensive roll on a
    # 10-delta AVGO $375P at moneyness 1.0297. The moneyness band is the
    # fallback for MISSING delta only.
    assert not _short_put_roll_gate_ok(1.02, -0.10)      # measured-delta veto
    assert _short_put_roll_gate_ok(1.02, -0.30)          # near strike, δ mid-band
    assert _short_put_roll_gate_ok(1.02, None)           # near strike, no delta
    assert _short_put_roll_gate_ok(1.06, -0.45)          # tested by delta
    assert not _short_put_roll_gate_ok(1.06, -0.10)      # TSM shape
    assert not _short_put_roll_gate_ok(1.0603, None)     # no delta, 6% above


def test_tsm_shape_no_roll_action():
    """The exact TSM shape → NO EXECUTE ROLL on any path; the position
    surfaces in Watch with the HOLD_FOR_DECAY verdict note only."""
    rev = _tsm_review()
    snap = _tsm_snapshot()
    md = _render([rev], snap)
    assert "EXECUTE ROLL" not in md
    assert "Sell-to-Open" not in md
    assert "TSM_PUT_380_20260828" not in md  # fully demoted from the list

    watch = "\n".join(render_watch_with_commentary([], [rev], snap))
    assert "TSM_PUT_380_20260828" in watch
    assert "HOLD_FOR_DECAY" in watch
    assert "decaying in your favor" in watch


def test_action_gate_enforced_all_paths():
    """A 1.06×-moneyness δ-0.10 put driven through every surfacing path —
    block #3 (priced candidates), block #4 (roll directive, no candidates),
    the verdict-driven flow (kill switch off), and the 4c forced-decision
    item — never renders an actionable ticket."""
    # Block #3: priced roll candidates exist
    md3 = _render([_tsm_review()], _tsm_snapshot())
    assert "EXECUTE ROLL" not in md3

    # Block #4: matrix roll directive with NO priced candidates
    md4 = _render([_tsm_review(roll_candidates=[])], _tsm_snapshot())
    assert "ROLL_OUT_AND_DOWN" not in md4
    assert "Buy-to-Close" not in md4

    # Verdict-driven flow disabled (kill switch) — the gate alone must
    # still block the ticket (it does not depend on the anatomy verdict)
    mdk = _render([_tsm_review()],
                  _tsm_snapshot(config={"exit_cost":
                                        {"verdict_drives_action": False}}))
    assert "EXECUTE ROLL" not in mdk

    # 4c forced decision: DTE ≤ 21 at 1.06× with δ 0.10 → no ⛔ TESTED nag
    mdf = _render([_tsm_review(days_to_expiry=15, roll_candidates=[],
                               recommendation="HOLD",
                               matrix_cell_id="DEFAULT_HOLD")],
                  _tsm_snapshot())
    assert "TESTED" not in mdf
    assert "DECISION REQUIRED" not in mdf


def test_genuinely_tested_put_keeps_roll_action():
    """Regression guard: the same shape with a MEASURED δ 0.45 (genuine
    test) passes the gate and the EXECUTE ROLL still renders."""
    md = _render([_tsm_review(delta=-0.45)], _tsm_snapshot())
    assert "EXECUTE ROLL" in md
    assert "TSM_PUT_380_20260828" in md


def test_forced_decision_still_fires_when_tested():
    """4c regression: at/past the strike (or tested by delta) inside 21 DTE
    the forced-decision item still fires."""
    md = _render([_tsm_review(days_to_expiry=15, roll_candidates=[],
                              recommendation="HOLD",
                              matrix_cell_id="DEFAULT_HOLD",
                              delta=-0.45)],
                 _tsm_snapshot())
    assert "TESTED" in md and "DECISION REQUIRED" in md


def test_watch_note_on_gate_demotion():
    """A gate-demoted roll leaves a transparency note on the Watch panel."""
    rev = _tsm_review()
    snap = _tsm_snapshot()
    _render([rev], snap)  # mutates rev with the demotion note
    watch = "\n".join(render_watch_with_commentary([], [rev], snap))
    assert "rule #3 gate" in watch
    assert "theta is working" in watch


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
