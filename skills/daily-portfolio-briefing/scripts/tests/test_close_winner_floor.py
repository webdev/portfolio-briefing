"""Rule #43 micro-fix (2026-08-04 rerun, action #3) — close-winner dollar
action floor.

Observed output, verbatim:

    CLOSE AMZN_CALL_330_20260904 — +30% ($+27); buy-to-close limit $0.66
    ... Remaining ~$63 of theta

A recommendation to bank twenty-seven dollars. Churn noise: commissions/
spread eat a meaningful fraction, and it occupies an action slot. Fix: a
take-profit close whose banked profit is below close_winner_min_dollars
(config, default $100) demotes to a Watch-panel note. Exceptions that
ALWAYS surface: pre-print closes (earnings ≤ 2d), loss-stops,
CLOSE_URGENT/recovery verdicts, gamma-week (DTE ≤ 10) closes on short
puts. Config 0 disables the floor.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from render.panels import render_action_list  # noqa: E402
from steps.per_option_commentary import render_watch_with_commentary  # noqa: E402

TODAY = "2026-08-04"


def _snapshot(earnings=None, config=None, quotes=None):
    return {
        "quotes": quotes or {"AMZN": {"last": 215.0}},
        "chains": {},
        "iv_ranks": {},
        "earnings_calendar": earnings or {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }


def _amzn_call_review():
    """AMZN-shaped: the observed $27 card. entry $0.90 / mid $0.63 → +30%
    captured, $27 banked, buy-to-close limit $0.66, ~$63 remaining theta."""
    return {
        "contract": "AMZN_CALL_330_20260904",
        "underlying": "AMZN", "type": "CALL", "qty": -1,
        "strike": 330.0, "expiration": "2026-09-04",
        "entry_price": 0.90, "current_mid": 0.63, "days_to_expiry": 31,
        "recommendation": "HOLD", "matrix_cell_id": "X",
        "roll_candidates": [],
    }


def _headlines(items):
    return [ln for ln in items if ln.lstrip()[:1].isdigit()]


def test_close_below_dollar_floor_demoted():
    """Observed 'CLOSE AMZN_CALL_330_20260904 — +30% ($+27); buy-to-close
    limit $0.66' — a $27 banked profit must NOT occupy an action slot; it
    demotes to a Watch-panel note."""
    rev = _amzn_call_review()
    snap = _snapshot()
    items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
    assert not any("**CLOSE** AMZN_CALL_330" in h for h in _headlines(items))
    note = rev.get("_close_floor_demotion") or ""
    assert "+30% captured but only $27" in note
    assert "below the $100 action floor" in note
    assert "let it decay or close at your convenience" in note
    # The demotion surfaces on the Watch panel (rule #24 — never hidden).
    watch = "\n".join(render_watch_with_commentary([], [rev], snap))
    assert "below the $100 action floor" in watch


def test_pre_print_close_ignores_floor():
    """A profitable close with earnings ≤ 2 days away always surfaces —
    the pre-print discipline (close before the binary) outranks the $27
    churn floor."""
    rev = _amzn_call_review()
    snap = _snapshot(earnings={"AMZN": "2026-08-06"})  # 2d away
    items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
    assert any("**CLOSE** AMZN_CALL_330" in h for h in _headlines(items))
    assert not rev.get("_close_floor_demotion")


def test_gamma_week_ignores_floor():
    """A gamma-week (DTE ≤ 10) close on a SHORT PUT always surfaces even
    below the dollar floor — last-week gamma risk beats the churn math."""
    rev = {
        "contract": "IREN_PUT_20_20260807",
        "underlying": "IREN", "type": "PUT", "qty": -1,
        "strike": 20.0, "expiration": "2026-08-07",
        "entry_price": 0.50, "current_mid": 0.20, "days_to_expiry": 3,
        "recommendation": "HOLD", "matrix_cell_id": "X",
        "roll_candidates": [],
    }
    snap = _snapshot(quotes={"IREN": {"last": 25.0}})  # comfortably OTM
    items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
    assert any("**CLOSE** IREN_PUT_20" in h for h in _headlines(items))
    assert not rev.get("_close_floor_demotion")


def test_floor_config_zero_disables():
    """close_winner_min_dollars: 0 disables the floor — the observed $27
    AMZN card renders as a plain CLOSE again."""
    rev = _amzn_call_review()
    snap = _snapshot(config={"close_winner_min_dollars": 0})
    items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
    assert any("**CLOSE** AMZN_CALL_330" in h for h in _headlines(items))
    assert not rev.get("_close_floor_demotion")
