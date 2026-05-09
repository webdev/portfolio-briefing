"""Tests for broker-position reconciler — exact-match the GOOG bug case."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from reconcile import reconcile_positions, _parse_contract_id


def test_parses_contract_id():
    p = _parse_contract_id("GOOG_CALL_450_20270917")
    assert p["underlying"] == "GOOG"
    assert p["option_type"] == "CALL"
    assert p["strike"] == 450
    assert p["expiration"] == "2027-09-17"


def test_goog_bug_case_caught():
    """The exact bug from the user's screenshot — wrong expiration."""
    snapshot = [
        {"symbol": "GOOG", "assetType": "EQUITY", "qty": 416, "price": 397.05},
        {"symbol": "GOOG_CALL_450_20270917", "assetType": "OPTION",
         "type": "CALL", "strike": 450, "expiration": "2027-09-17", "qty": -4,
         "entry_price": 43.09},
    ]
    broker = [
        {"symbol": "GOOG", "assetType": "EQUITY", "qty": 416, "price": 397.05},
        # Reality: Dec 17 '27, NOT Sep 17 '27
        {"symbol": "GOOG_CALL_450_20271217", "assetType": "OPTION",
         "type": "CALL", "strike": 450, "expiration": "2027-12-17", "qty": -4,
         "entry_price": 60.87},
        # Reality also has a Jun '26 short put the snapshot is missing
        {"symbol": "GOOG_PUT_355_20260612", "assetType": "OPTION",
         "type": "PUT", "strike": 355, "expiration": "2026-06-12", "qty": -1,
         "entry_price": 3.50},
    ]
    md = """
## Today's Action List

6. **EXECUTE ROLL** GOOG_CALL_450_20270917 — Diagonal up
   - Order: BTC ...
"""
    result = reconcile_positions(md, snapshot, broker)
    assert result.verified is False
    # Should detect the missing (sep-17) snapshot position not at broker
    assert any("Sep" in m.get("issue", "") or "NOT FOUND" in m.get("issue", "")
               for m in result.mismatches)
    # Should detect the GOOG put as missing in snapshot
    assert any("PUT" in s for s in result.missing_in_snapshot)
    # Should block the EXECUTE ROLL action
    assert any("EXECUTE ROLL" in a for a in result.block_actions)
    # Panel should have the warning
    assert "DO NOT TRADE" in result.panel_md
    assert "GOOG" in result.panel_md


def test_perfect_match_passes():
    """When snapshot equals broker, verification passes."""
    snapshot = [
        {"symbol": "AAPL", "assetType": "EQUITY", "qty": 100},
        {"symbol": "AAPL_CALL_180_20260618", "assetType": "OPTION",
         "type": "CALL", "strike": 180, "expiration": "2026-06-18", "qty": -1,
         "entry_price": 5.00},
    ]
    broker = list(snapshot)  # exact copy
    md = "## Today's Action List\n\n1. **CLOSE** AAPL_CALL_180_20260618 — done\n"
    result = reconcile_positions(md, snapshot, broker)
    assert result.verified is True
    assert result.panel_md == ""


def test_quantity_mismatch_detected():
    """Snapshot says 4 contracts, broker has 3 — should mismatch."""
    snapshot = [
        {"symbol": "MSFT_CALL_450_20261218", "assetType": "OPTION",
         "type": "CALL", "strike": 450, "expiration": "2026-12-18", "qty": -4,
         "entry_price": 24.00},
    ]
    broker = [
        {"symbol": "MSFT_CALL_450_20261218", "assetType": "OPTION",
         "type": "CALL", "strike": 450, "expiration": "2026-12-18", "qty": -3,
         "entry_price": 24.00},
    ]
    result = reconcile_positions("## Today's Action List\n", snapshot, broker)
    assert result.verified is False
    assert any("quantity mismatch" in m["issue"] for m in result.mismatches)


def test_basis_drift_detected_above_tolerance():
    """5% basis tolerance — drift beyond should flag."""
    snapshot = [
        {"symbol": "NVDA_CALL_245_20261218", "assetType": "OPTION",
         "type": "CALL", "strike": 245, "expiration": "2026-12-18", "qty": -7,
         "entry_price": 11.50},
    ]
    broker = [
        {"symbol": "NVDA_CALL_245_20261218", "assetType": "OPTION",
         "type": "CALL", "strike": 245, "expiration": "2026-12-18", "qty": -7,
         "entry_price": 14.00},  # 21% drift
    ]
    result = reconcile_positions("", snapshot, broker)
    assert result.verified is False
    assert any("cost basis drift" in m["issue"] for m in result.mismatches)


def test_extra_position_at_broker_flagged():
    """Broker has a position the snapshot doesn't know about."""
    snapshot = [
        {"symbol": "AAPL", "assetType": "EQUITY", "qty": 100},
    ]
    broker = [
        {"symbol": "AAPL", "assetType": "EQUITY", "qty": 100},
        {"symbol": "TSLA", "assetType": "EQUITY", "qty": 50},  # missing in snapshot
    ]
    result = reconcile_positions("", snapshot, broker)
    assert result.verified is False
    assert any("TSLA" in s for s in result.missing_in_snapshot)


def test_action_referencing_missing_contract_blocked():
    """If an action targets a contract the broker doesn't have, BLOCK it."""
    snapshot = [
        {"symbol": "GOOG_CALL_450_20270917", "assetType": "OPTION",
         "type": "CALL", "strike": 450, "expiration": "2027-09-17", "qty": -4,
         "entry_price": 43.09},
    ]
    broker = []  # broker has nothing
    md = """
## Today's Action List

6. **EXECUTE ROLL** GOOG_CALL_450_20270917 — Diagonal up
"""
    result = reconcile_positions(md, snapshot, broker)
    assert result.verified is False
    assert "EXECUTE ROLL GOOG_CALL_450_20270917" in result.block_actions


def test_verified_when_no_action_list():
    """No actions to validate → verified = True (assuming snapshot matches broker)."""
    snapshot = [{"symbol": "AAPL", "assetType": "EQUITY", "qty": 100}]
    broker = [{"symbol": "AAPL", "assetType": "EQUITY", "qty": 100}]
    result = reconcile_positions("# Just a header", snapshot, broker)
    assert result.verified is True


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
