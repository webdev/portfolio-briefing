"""
Tests for live E*TRADE chain wiring in briefing panels.

Verify that:
- DEFENSIVE COLLAR receives chain_provider and uses live prices
- Collar legs with price_source="estimated" are correctly differentiated
"""

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

# Add collar advisor to path
_COLLAR = Path(__file__).resolve().parents[3] / "defensive-collar-advisor" / "scripts"
sys.path.insert(0, str(_COLLAR))


def test_defensive_collar_passes_chain_provider_to_propose():
    """DEFENSIVE COLLAR should pass chain_provider to propose_collar and use live prices."""
    from propose_collar import propose_collar

    # Mock chain_provider that returns live data
    def mock_chain_provider(ticker, expiration, strike, option_type):
        if option_type == "CALL":
            return {"bid": 3.00, "mid": 3.25, "ask": 3.50}
        elif option_type == "PUT":
            return {"bid": 2.00, "mid": 2.25, "ask": 2.50}
        return None

    # Proposal that should qualify: high concentration + big tax exposure
    # spot=300, cost=150 (100% gain), 200 shares = $30k gain
    # 20% concentration, tax exposure = 30k * 0.238 = 7,140 / 100k NLV = 7.1% (> 1.5% trigger)
    proposal = propose_collar(
        ticker="MSFT",
        spot=300.0,
        shares=200,
        cost_basis=150.0,  # 100% gain
        nlv=100000,
        concentration_pct=20.0,  # > 10% trigger
        is_core=True,
        has_short_call=True,
        current_call_strike=310.0,
        current_call_expiration="2026-06-19",
        current_call_mid=2.50,
        current_call_contracts=2,
        iv_rank=65.0,
        ltcg_rate=0.238,
        chain_provider=mock_chain_provider,
    )

    assert proposal.qualified
    legs = proposal.proposed_legs

    # Check that STO (sell-to-open call) leg got live pricing
    sto = next((l for l in legs if l["action"] == "STO"), None)
    assert sto is not None
    assert sto["price_source"] == "live_chain"
    assert sto["limit"] == 3.25  # mid from mock_chain_provider

    # Check that BTO (buy-to-open put) leg got live pricing (if included)
    bto = next((l for l in legs if l["action"] == "BTO"), None)
    if bto is not None:
        assert bto["price_source"] == "live_chain"
        assert bto["limit"] == 2.25


def test_defensive_collar_with_missing_chain_uses_estimated():
    """When chain_provider returns None, collar should use estimated prices."""
    from propose_collar import propose_collar

    # Mock chain_provider that returns None (chain unavailable)
    def mock_chain_provider(ticker, expiration, strike, option_type):
        return None

    # High tax exposure: spot=100, cost=50 (100% gain), 300 shares = $15k gain
    # tax = 15k * 0.238 = 3,570 / 100k NLV = 3.6% (> 1.5% trigger)
    proposal = propose_collar(
        ticker="GE",
        spot=100.0,
        shares=300,
        cost_basis=50.0,
        nlv=100000,
        concentration_pct=18.0,
        is_core=True,
        has_short_call=True,
        current_call_strike=105.0,
        current_call_expiration="2026-06-19",
        current_call_mid=2.00,
        current_call_contracts=3,
        iv_rank=50.0,
        ltcg_rate=0.238,
        chain_provider=mock_chain_provider,
    )

    assert proposal.qualified
    legs = proposal.proposed_legs

    # STO leg should have estimated pricing (no live chain)
    sto = next((l for l in legs if l["action"] == "STO"), None)
    assert sto is not None
    assert sto["price_source"] == "estimated"

    # BTO leg should have estimated pricing
    bto = next((l for l in legs if l["action"] == "BTO"), None)
    if bto is not None:
        assert bto["price_source"] == "estimated"


def test_defensive_collar_without_chain_provider_uses_estimated():
    """When no chain_provider is passed, collar should use estimated prices."""
    from propose_collar import propose_collar

    # High tax exposure for qualification
    proposal = propose_collar(
        ticker="JPM",
        spot=180.0,
        shares=250,
        cost_basis=100.0,
        nlv=100000,
        concentration_pct=17.0,
        is_core=True,
        has_short_call=True,
        current_call_strike=185.0,
        current_call_expiration="2026-06-19",
        current_call_mid=3.00,
        current_call_contracts=2,
        iv_rank=55.0,
        ltcg_rate=0.238,
        chain_provider=None,  # Explicitly no chain provider
    )

    assert proposal.qualified
    legs = proposal.proposed_legs

    # All legs should have estimated pricing
    sto = next((l for l in legs if l["action"] == "STO"), None)
    assert sto is not None
    assert sto["price_source"] == "estimated"

    bto = next((l for l in legs if l["action"] == "BTO"), None)
    if bto is not None:
        assert bto["price_source"] == "estimated"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
