"""
Contradiction-detection integration tests.

Verify that the action list, stress closes, and analyst brief reflect ALL
actionable signals when the input data demands them. These tests catch the
"header says 1 URGENT, list shows 0" class of bug.
"""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from steps.aggregate import aggregate_briefing


def _make_snapshot():
    """Build a synthetic snapshot that exercises every action-list branch."""
    today = date.today()
    near_exp = today + timedelta(days=20)
    far_exp = today + timedelta(days=180)

    positions = [
        # Heavy concentration name (>10% NLV) — should TRIM
        {
            "symbol": "GOOG",
            "assetType": "EQUITY",
            "qty": 200,
            "price": 280.0,
            "cost_basis": 220.0,
            "account": "TAXABLE",
        },
        # Another concentration
        {
            "symbol": "NVDA",
            "assetType": "EQUITY",
            "qty": 100,
            "price": 200.0,
            "cost_basis": 100.0,
            "account": "TAXABLE",
        },
        # Modest equity (no trim)
        {
            "symbol": "MSFT",
            "assetType": "EQUITY",
            "qty": 30,
            "price": 420.0,
            "cost_basis": 380.0,
            "account": "TAXABLE",
        },
        # Profitable short put — should generate CLOSE WINNER + show in stress closes
        {
            "symbol": "MU_PUT_20260618_60",
            "assetType": "OPTION",
            "qty": -2,
            "type": "PUT",
            "strike": 60.0,
            "expiration": "2026-06-18",
            "entryPrice": 1.50,
            "currentMid": 0.20,
            "premiumReceived": 1.50,
            "underlying": "MU",
            "delta": -0.05,
            "account": "TAXABLE",
        },
        # Underwater short call with high-credit roll candidate — EXECUTE ROLL
        {
            "symbol": "GOOG_CALL_20270917_450",
            "assetType": "OPTION",
            "qty": -1,
            "type": "CALL",
            "strike": 450.0,
            "expiration": far_exp.isoformat(),
            "entryPrice": 5.00,
            "currentMid": 8.50,
            "premiumReceived": 5.00,
            "underlying": "GOOG",
            "delta": 0.30,
            "account": "TAXABLE",
        },
    ]

    return {
        "balance": {"accountValue": 100000, "cash": 5000},
        "positions": positions,
        "quotes": {
            "SPY": {"last": 600.0},
            "VIX": {"last": 28.0},
            "GOOG": {"last": 280.0},
            "NVDA": {"last": 200.0},
            "MSFT": {"last": 420.0},
            "MU": {"last": 95.0},
        },
        "ytd_pnl": {},
        "earnings_calendar": {},
    }


def _make_equity_reviews():
    return [
        {"ticker": "GOOG", "qty": 200, "price": 280.0, "weight": 0.56,
         "pl_pct": 0.27, "recommendation": "HOLD"},
        {"ticker": "NVDA", "qty": 100, "price": 200.0, "weight": 0.20,
         "pl_pct": 1.0, "recommendation": "HOLD"},
        {"ticker": "MSFT", "qty": 30, "price": 420.0, "weight": 0.126,
         "pl_pct": 0.10, "recommendation": "HOLD"},
    ]


def _make_options_reviews():
    today = date.today()
    return [
        # 86% capture short put — close winner
        {
            "contract": "MU_PUT_20260618_60",
            "underlying": "MU",
            "type": "PUT",
            "strike": 60.0,
            "qty": -2,
            "entry_price": 1.50,
            "current_mid": 0.20,
            "expiration": "2026-06-18",
            "days_to_expiry": 41,
            "recommendation": "HOLD",
            "rationale": "+87% capture",
        },
        # GOOG call with profitable roll
        {
            "contract": "GOOG_CALL_20270917_450",
            "underlying": "GOOG",
            "type": "CALL",
            "strike": 450.0,
            "qty": -1,
            "entry_price": 5.00,
            "current_mid": 8.50,
            "expiration": (today + timedelta(days=180)).isoformat(),
            "days_to_expiry": 180,
            "recommendation": "HOLD",
            "rationale": "underwater long-dated call",
            "roll_candidates": [
                {"id": "A", "description": "HOLD", "netDollars": 0.0},
                {"id": "B", "description": "Roll out 90d to $480 strike",
                 "netDollars": 2300.0},
                {"id": "C", "description": "Roll out 60d to $470 strike",
                 "netDollars": 1480.0},
            ],
        },
    ]


def test_action_list_fires_all_branches():
    """Action list must surface CLOSE WINNERS, EXECUTE ROLL, TRIM."""
    snapshot = _make_snapshot()
    equity_reviews = _make_equity_reviews()
    options_reviews = _make_options_reviews()
    new_ideas = []

    md, _ = aggregate_briefing(
        date.today().isoformat(),
        config={"enabled_strategies": ["wheel"], "accounts": ["E1"]},
        snapshot_data=snapshot,
        regime_data={"regime": "CAUTION", "confidence": "HIGH",
                     "triggered_rules": [{"rationale": "VIX elevated"}]},
        equity_reviews=equity_reviews,
        options_reviews=options_reviews,
        new_ideas=new_ideas,
        consistency_report={"note": "first run"},
        flagged_inconsistencies=[],
        directives_active=[],
        directives_expired=[],
        snapshot_dir=Path("/tmp/test_snap"),
    )

    # Must contain a CLOSE recommendation for the +87% MU put
    assert "**CLOSE** MU_PUT_20260618_60" in md, "CLOSE winner missing"

    # Must contain EXECUTE ROLL for the $2300 credit roll
    assert "**EXECUTE ROLL** GOOG_CALL_20270917_450" in md, \
        "EXECUTE ROLL missing despite $2300 credit roll candidate"
    assert "+$2,300" in md, "Roll credit dollars missing"

    # Must contain TRIM for GOOG (56% NLV) and NVDA (20% NLV)
    assert "**TRIM** GOOG" in md, "TRIM GOOG missing"
    assert "**TRIM** NVDA" in md, "TRIM NVDA missing"


def test_action_list_count_matches_header():
    """Header 'Action Items: N' must match the number of numbered items rendered."""
    snapshot = _make_snapshot()
    md, _ = aggregate_briefing(
        date.today().isoformat(),
        config={"enabled_strategies": ["wheel"], "accounts": ["E1"]},
        snapshot_data=snapshot,
        regime_data={"regime": "NORMAL", "confidence": "HIGH",
                     "triggered_rules": []},
        equity_reviews=_make_equity_reviews(),
        options_reviews=_make_options_reviews(),
        new_ideas=[],
        consistency_report={"note": "first run"},
        flagged_inconsistencies=[],
        directives_active=[],
        directives_expired=[],
        snapshot_dir=Path("/tmp/test_snap"),
    )

    # Extract header count
    import re
    header_m = re.search(r"\*?\*?Action Items:\*?\*?\s*(\d+)", md)
    assert header_m, "Header should expose Action Items count"
    header_count = int(header_m.group(1))

    # Count numbered list items in the action list section
    action_section = md.split("## Today's Action List", 1)[1].split("##", 1)[0]
    numbered = re.findall(r"^\s*(\d+)\.\s+\*\*", action_section, re.MULTILINE)

    assert header_count == len(numbered), (
        f"Header says {header_count} action items but list has {len(numbered)}: "
        f"{action_section[:500]}"
    )


def test_stress_closes_populated_when_profitable_shorts_exist():
    """Section 4 STRESS COVERAGE close list must show the profitable MU put."""
    # Construct a snapshot where put obligations exceed cash so coverage<0.7x
    snapshot = _make_snapshot()
    # Drop cash and add multiple short puts to push coverage below 0.7x
    snapshot["balance"]["cash"] = 1000  # very low
    snapshot["positions"].append({
        "symbol": "AMD_PUT_20260618_140",
        "assetType": "OPTION",
        "qty": -3,
        "type": "PUT",
        "strike": 140.0,
        "expiration": "2026-06-18",
        "entryPrice": 2.00,
        "currentMid": 1.80,  # not profitable enough to close (10% capture)
        "premiumReceived": 2.00,
        "underlying": "AMD",
        "delta": -0.30,
        "account": "TAXABLE",
    })

    md, _ = aggregate_briefing(
        date.today().isoformat(),
        config={"enabled_strategies": ["wheel"], "accounts": ["E1"]},
        snapshot_data=snapshot,
        regime_data={"regime": "CAUTION", "confidence": "HIGH",
                     "triggered_rules": [{"rationale": "elevated VIX"}]},
        equity_reviews=_make_equity_reviews(),
        options_reviews=_make_options_reviews(),
        new_ideas=[],
        consistency_report={"note": "first run"},
        flagged_inconsistencies=[],
        directives_active=[],
        directives_expired=[],
        snapshot_dir=Path("/tmp/test_snap"),
    )

    # Section 4 should NOT be empty: must list the profitable MU put
    if "### 4. STRESS COVERAGE" in md:
        sec4 = md.split("### 4. STRESS COVERAGE", 1)[1].split("###", 1)[0]
        assert "MU" in sec4, (
            f"Stress coverage section 4 missing MU close-candidate. Got:\n{sec4[:500]}"
        )


def test_no_orphan_urgent_flag_in_header():
    """If header brags '🚨 1 URGENT', the action list must contain a URGENT line."""
    snapshot = _make_snapshot()
    options_reviews = _make_options_reviews()
    # Mark the GOOG call as URGENT in rationale
    options_reviews[1]["rationale"] = "🚨 URGENT — earnings in 14d, deep ITM"

    md, _ = aggregate_briefing(
        date.today().isoformat(),
        config={"enabled_strategies": ["wheel"], "accounts": ["E1"]},
        snapshot_data=snapshot,
        regime_data={"regime": "NORMAL", "confidence": "HIGH",
                     "triggered_rules": []},
        equity_reviews=_make_equity_reviews(),
        options_reviews=options_reviews,
        new_ideas=[],
        consistency_report={"note": "first run"},
        flagged_inconsistencies=[],
        directives_active=[],
        directives_expired=[],
        snapshot_dir=Path("/tmp/test_snap"),
    )

    # If header says URGENT, body must too
    import re
    header_urgent = bool(re.search(r"🚨\s*\d+\s+URGENT", md.split("## ")[0]))
    body_has_urgent = "🚨 **URGENT**" in md or "**URGENT**" in md
    if header_urgent:
        assert body_has_urgent, "Header advertises URGENT but body has none"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
