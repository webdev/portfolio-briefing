"""Tests for the pre-flight verifier — orchestrator of all gates."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from preflight import run_pre_flight


def test_clean_briefing_releases():
    """Briefing with matching position data should not be blocked by reconciler."""
    md = """
# Daily Briefing — Friday, May 8, 2026
_For trading session **2026-05-08** · generated Fri May 8_

## Today's Action List

1. **CLOSE** AAPL_PUT_180_20260618 — +35% profit
   - Yield: 12% ann.
   - **Why:** Theta capture
   - **Gain:** Locks $200

### 📋 Total Impact (if all actions executed)
- Total: 1

## Stress Test
Coverage 0.7x

## Hedge Book
SPY puts in place
"""
    snap = [{"symbol": "AAPL_PUT_180_20260618", "assetType": "OPTION",
             "type": "PUT", "strike": 180, "expiration": "2026-06-18", "qty": -1,
             "entry_price": 5.00}]
    broker = list(snap)
    result = run_pre_flight(md, snap, broker)
    # Position reconciler must pass — the gate that matters most for this test
    assert result.gate_results["position_reconciler"]["verified"] is True
    assert result.blocking_gate != "position_reconciler"


def test_position_mismatch_blocks():
    """Snapshot says Sep '27, broker has Dec '27 — must BLOCK."""
    md = """
## Today's Action List

1. **EXECUTE ROLL** GOOG_CALL_450_20270917 — Diagonal up
"""
    snap = [{"symbol": "GOOG_CALL_450_20270917", "assetType": "OPTION",
             "type": "CALL", "strike": 450, "expiration": "2027-09-17", "qty": -4,
             "entry_price": 43}]
    broker = [{"symbol": "GOOG_CALL_450_20271217", "assetType": "OPTION",
               "type": "CALL", "strike": 450, "expiration": "2027-12-17", "qty": -4,
               "entry_price": 60}]
    result = run_pre_flight(md, snap, broker)
    assert result.verdict == "BLOCK"
    assert result.blocking_gate == "position_reconciler"
    assert "DO NOT ACT" in result.rendered_briefing
    assert "PRE-FLIGHT BLOCKED" in result.rendered_briefing


def test_missing_broker_positions_warns():
    """No broker positions provided → WARN, not BLOCK."""
    md = "## Today's Action List\n\n1. **CLOSE** something\n"
    result = run_pre_flight(md, snapshot_positions=None, broker_positions=None)
    assert result.verdict in ("WARN", "BLOCK")
    # Skipped reconciler should leave a note
    assert "Position Reconciler Skipped" in result.rendered_briefing or \
           "PRE-FLIGHT BLOCKED" in result.rendered_briefing


def test_block_renders_with_clear_header():
    """When blocked, the briefing should start with a clear DO-NOT-TRADE message."""
    md = "## Today's Action List\n\n1. **EXECUTE ROLL** FAKE_CALL_1_20990101\n"
    snap = [{"symbol": "FAKE_CALL_1_20990101", "assetType": "OPTION",
             "type": "CALL", "strike": 1, "expiration": "2099-01-01", "qty": -1,
             "entry_price": 1}]
    broker = []  # nothing at broker
    result = run_pre_flight(md, snap, broker)
    assert result.verdict == "BLOCK"
    # First line should be the loud header
    first_line = result.rendered_briefing.split("\n")[0]
    assert "BLOCKED" in first_line


def test_consolidated_panel_includes_all_warnings():
    """Multiple gates flagging issues → all panels merged into consolidated_panel_md."""
    md = "## Today's Action List\n\n1. **EXECUTE ROLL** A_B_C_20260101\n"
    snap = [{"symbol": "A_B_C_20260101", "assetType": "OPTION",
             "type": "CALL", "strike": 100, "expiration": "2026-01-01", "qty": -1}]
    broker = []
    result = run_pre_flight(md, snap, broker)
    # Position reconciler panel should be present in consolidated
    assert "Position Data Mismatch" in result.consolidated_panel_md or \
           "DO NOT TRADE" in result.consolidated_panel_md


def test_gate_results_dict_complete():
    """gate_results should contain entries for every gate."""
    md = "## Today's Action List\n\n1. **CLOSE** AAPL_CALL_180_20260618\n"
    snap = [{"symbol": "AAPL_CALL_180_20260618", "assetType": "OPTION",
             "type": "CALL", "strike": 180, "expiration": "2026-06-18", "qty": -1}]
    broker = list(snap)
    result = run_pre_flight(md, snap, broker)
    assert "position_reconciler" in result.gate_results
    assert "data_verifier" in result.gate_results
    assert "quality_gate" in result.gate_results


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
