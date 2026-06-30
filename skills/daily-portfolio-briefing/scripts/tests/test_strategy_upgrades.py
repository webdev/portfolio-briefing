"""Unit tests for strategy upgrades computation."""

import pytest
from datetime import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from steps.strategy_upgrades import (
    compute_strategy_upgrades,
    _check_concentration,
    _earnings_conflict,
    _find_short_call,
    _is_tail_risk_name,
)
import steps.strategy_upgrades as _su
from datetime import date


class _FakeFetcher:
    """Stand-in for the etrade-chain-fetcher module, to exercise the
    delta-first / %OTM-fallback selection in _etrade_call_quote without a live
    broker."""

    def __init__(self, *, delta_quote=None, otm_quote=None):
        self._delta_quote = delta_quote
        self._otm_quote = otm_quote
        self.delta_calls = 0
        self.otm_calls = 0

    def choose_expiration(self, **kw):
        return date(2026, 7, 17)

    def find_strike_near_delta(self, **kw):
        self.delta_calls += 1
        return self._delta_quote

    def find_strike_at_otm_pct(self, **kw):
        self.otm_calls += 1
        return self._otm_quote


def test_call_quote_prefers_delta_band(monkeypatch):
    """When the chain has deltas, select by delta band and tag selected_by='delta'."""
    fake = _FakeFetcher(
        delta_quote={"strike": 18.0, "bid": 0.40, "mid": 0.42, "ask": 0.44, "delta": 0.24},
        otm_quote={"strike": 17.0, "bid": 0.66, "mid": 0.68, "ask": 0.70, "delta": None},
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    q = _su._etrade_call_quote(symbol="SOFI", spot=16.0, target_delta=0.25)
    assert q["selected_by"] == "delta"
    assert q["strike"] == 18.0
    assert q["delta"] == 0.24
    assert fake.delta_calls == 1 and fake.otm_calls == 0  # never fell back


def test_call_quote_falls_back_to_otm_when_no_deltas(monkeypatch):
    """When delta selection yields nothing (chain lacks Greeks), fall back to
    %OTM and carry delta=None (caller will render 'δ n/a')."""
    fake = _FakeFetcher(
        delta_quote=None,
        otm_quote={"strike": 17.0, "bid": 0.66, "mid": 0.68, "ask": 0.70, "delta": None},
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    q = _su._etrade_call_quote(symbol="SMH", spot=16.0, target_delta=0.25)
    assert q["selected_by"] == "otm_pct"
    assert q["delta"] is None
    assert fake.delta_calls == 1 and fake.otm_calls == 1  # tried delta, then fell back


class _FakeFetcherWithQuoteContract(_FakeFetcher):
    """Adds quote_contract() to support the S/R-anchor snap path."""

    def __init__(self, *, delta_quote=None, otm_quote=None, anchor_quote=None):
        super().__init__(delta_quote=delta_quote, otm_quote=otm_quote)
        self._anchor_quote = anchor_quote
        self.quote_contract_calls = 0
        self.last_anchor_strike = None

    def quote_contract(self, *, symbol, strike, expiration, opt_type, cache=None):
        self.quote_contract_calls += 1
        self.last_anchor_strike = strike
        return self._anchor_quote


def test_call_quote_snaps_to_sr_resistance_when_in_range(monkeypatch):
    """A strong resistance cluster inside ±3% of the delta-selected strike
    causes the quote to snap to that strike via quote_contract."""
    fake = _FakeFetcherWithQuoteContract(
        delta_quote={"strike": 18.0, "bid": 0.40, "mid": 0.42, "ask": 0.44, "delta": 0.24},
        anchor_quote={"strike": 18.5, "bid": 0.50, "mid": 0.52, "ask": 0.54, "delta": 0.27},
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    sr_resistances = [
        {"price": 18.5, "side": "resistance", "source": "swing",
         "touches": 3, "strength": 2.5, "confluence": ["sma_50"]},
    ]
    q = _su._etrade_call_quote(
        symbol="SOFI", spot=16.0, target_delta=0.25,
        sr_resistances=sr_resistances,
    )
    assert q["selected_by"] == "sr_anchor"
    assert q["strike"] == 18.5
    assert q["delta"] == 0.27  # MEASURED delta at the snapped strike, never invented
    assert q["sr_anchor"]["price"] == 18.5
    assert fake.quote_contract_calls == 1


def test_call_quote_no_snap_when_resistance_outside_band(monkeypatch):
    """A resistance far from the delta strike → no snap, keep the delta pick."""
    fake = _FakeFetcherWithQuoteContract(
        delta_quote={"strike": 18.0, "bid": 0.40, "mid": 0.42, "ask": 0.44, "delta": 0.24},
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    sr_resistances = [
        # 25 is ~39% above the delta strike of 18 — way outside ±3%
        {"price": 25.0, "side": "resistance", "source": "swing",
         "touches": 3, "strength": 2.5, "confluence": []},
    ]
    q = _su._etrade_call_quote(
        symbol="SOFI", spot=16.0, target_delta=0.25,
        sr_resistances=sr_resistances,
    )
    assert q["selected_by"] == "delta"
    assert q["strike"] == 18.0
    assert q["sr_anchor"] is None
    assert fake.quote_contract_calls == 0  # never tried to snap


def test_call_quote_no_snap_when_resistance_too_weak(monkeypatch):
    """A weak (strength < 1.5) resistance inside the band → no snap."""
    fake = _FakeFetcherWithQuoteContract(
        delta_quote={"strike": 18.0, "bid": 0.40, "mid": 0.42, "ask": 0.44, "delta": 0.24},
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    sr_resistances = [
        {"price": 18.3, "side": "resistance", "source": "swing",
         "touches": 1, "strength": 0.5, "confluence": []},
    ]
    q = _su._etrade_call_quote(
        symbol="SOFI", spot=16.0, target_delta=0.25,
        sr_resistances=sr_resistances,
    )
    assert q["selected_by"] == "delta"
    assert q["sr_anchor"] is None


def test_call_quote_falls_back_when_anchor_strike_not_listed(monkeypatch):
    """If quote_contract returns None (anchor strike not on the chain), keep
    the delta pick rather than failing the whole quote."""
    fake = _FakeFetcherWithQuoteContract(
        delta_quote={"strike": 18.0, "bid": 0.40, "mid": 0.42, "ask": 0.44, "delta": 0.24},
        anchor_quote=None,  # snap fails
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    sr_resistances = [
        {"price": 18.5, "side": "resistance", "source": "swing",
         "touches": 3, "strength": 2.5, "confluence": []},
    ]
    q = _su._etrade_call_quote(
        symbol="SOFI", spot=16.0, target_delta=0.25,
        sr_resistances=sr_resistances,
    )
    assert q["selected_by"] == "delta"
    assert q["strike"] == 18.0
    assert fake.quote_contract_calls == 1  # tried to snap, but didn't replace


def test_call_quote_sr_none_preserves_legacy_behavior(monkeypatch):
    """sr_resistances=None must reproduce the exact pre-S/R quote."""
    fake = _FakeFetcherWithQuoteContract(
        delta_quote={"strike": 18.0, "bid": 0.40, "mid": 0.42, "ask": 0.44, "delta": 0.24},
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))
    q = _su._etrade_call_quote(symbol="SOFI", spot=16.0, target_delta=0.25, sr_resistances=None)
    assert q["selected_by"] == "delta"
    assert q.get("sr_anchor") is None


@pytest.fixture
def mock_snapshot_data():
    """Create a mock snapshot with positions, chains, earnings, quotes."""
    return {
        "positions": [
            # Equity: PLTR (755 shares @ $185, 22% weight)
            {
                "symbol": "PLTR",
                "assetType": "EQUITY",
                "qty": 755,
                "price": 185.00,
                "costBasis": 120.00,
            },
            # Equity: GOOG (415 shares @ $200, 8.3% weight, big gain)
            {
                "symbol": "GOOG",
                "assetType": "EQUITY",
                "qty": 415,
                "price": 200.00,
                "costBasis": 120.00,
            },
            # Equity: META (46 shares, sub-100 lot)
            {
                "symbol": "META",
                "assetType": "EQUITY",
                "qty": 46,
                "price": 615.59,
                "costBasis": 500.00,
            },
            # Equity: AMZN (100 shares, exact lot)
            {
                "symbol": "AMZN",
                "assetType": "EQUITY",
                "qty": 100,
                "price": 325.00,
                "costBasis": 300.00,
            },
            # Option: PLTR short call (7 contracts, 145C June 5)
            {
                "symbol": "PLTR_SHORT_CALL",
                "assetType": "OPTION",
                "underlying": "PLTR",
                "type": "CALL",
                "strike": 145.00,
                "expiration": "2026-06-05",
                "qty": -7,
                "currentMid": 2.50,
                "premiumReceived": 3.00,
            },
            # Option: AMZN short call (1 contract, 325C June 5)
            {
                "symbol": "AMZN_SHORT_CALL",
                "assetType": "OPTION",
                "underlying": "AMZN",
                "type": "CALL",
                "strike": 325.00,
                "expiration": "2026-06-05",
                "qty": -1,
                "currentMid": 2.00,
                "premiumReceived": 2.50,
            },
        ],
        "balance": {
            "accountValue": 1000000,  # $1M NLV
            "cash": 50000,
        },
        "chains": {
            # PLTR 2026-06-05 chain with short put at 0.20 delta
            "PLTR_2026-06-05": {
                "underlying": "PLTR",
                "expiration": "2026-06-05",
                "puts": [
                    {
                        "strike": 125.00,
                        "bid": 3.00,
                        "ask": 3.25,
                        "lastPrice": 3.12,
                        "delta": -0.20,
                        "openInterest": 500,
                    },
                    {
                        "strike": 110.00,
                        "bid": 1.50,
                        "ask": 1.75,
                        "lastPrice": 1.62,
                        "delta": -0.10,
                        "openInterest": 800,
                    },
                ],
                "calls": [],
            },
            # AMZN 2026-06-05 chain
            "AMZN_2026-06-05": {
                "underlying": "AMZN",
                "expiration": "2026-06-05",
                "puts": [
                    {
                        "strike": 255.00,
                        "bid": 2.40,
                        "ask": 2.70,
                        "lastPrice": 2.56,
                        "delta": -0.19,
                        "openInterest": 600,
                    },
                ],
                "calls": [],
            },
            # GOOG 2026-06-05 chain (for collar)
            "GOOG_2026-06-05": {
                "underlying": "GOOG",
                "expiration": "2026-06-05",
                "puts": [
                    {
                        "strike": 350.00,
                        "bid": 1.40,
                        "ask": 1.80,
                        "lastPrice": 1.59,
                        "delta": -0.09,
                        "openInterest": 400,
                    },
                ],
                "calls": [],
            },
        },
        "earnings_calendar": {
            # No earnings conflict for these dates
            "PLTR": "2026-07-15",
            "GOOG": "2026-07-20",
            "AMZN": "2026-07-25",
        },
        "quotes": {
            "PLTR": {"last": 185.00},
            "GOOG": {"last": 200.00},
            "META": {"last": 615.59},
            "AMZN": {"last": 325.00},
        },
    }


@pytest.fixture
def mock_config():
    return {
        "max_position_pct": 0.10,
        "max_sector_pct": 0.35,
    }


def test_concentration_check_blocks_when_would_exceed_cap():
    """Test that concentration check correctly blocks when new position would breach 10% cap."""
    # Current weight 8%, adding 3% collateral would push to 11% → should block
    conc = _check_concentration(existing_weight_pct=0.08, new_collateral=30000, nlv=1000000)
    assert conc["blocked"] is True
    assert "10%" in conc["reason"]


def test_concentration_check_allows_within_cap():
    """Test that concentration check allows when total stays under cap."""
    # Current weight 8%, adding 1% collateral → 9% total → should allow
    conc = _check_concentration(existing_weight_pct=0.08, new_collateral=10000, nlv=1000000)
    assert conc["blocked"] is False


def test_earnings_conflict_blocks_when_earnings_before_expiry():
    """Test earnings guard blocks puts when earnings occur before expiration."""
    # Earnings on 2026-06-01, expiration 2026-06-05 → conflict
    conflict = _earnings_conflict(
        {"TSLA": "2026-06-01"},
        "TSLA",
        "2026-06-05"
    )
    assert conflict is True


def test_earnings_conflict_allows_when_earnings_after_expiry():
    """Test earnings guard allows when earnings after expiration."""
    # Earnings on 2026-07-15, expiration 2026-06-05 → no conflict
    conflict = _earnings_conflict(
        {"PLTR": "2026-07-15"},
        "PLTR",
        "2026-06-05"
    )
    assert conflict is False


def test_is_tail_risk_name():
    """Test tail risk name detection."""
    assert _is_tail_risk_name("BABA") is True
    assert _is_tail_risk_name("GME") is True
    assert _is_tail_risk_name("AAPL") is False
    assert _is_tail_risk_name("MSFT") is False


def test_find_short_call():
    """Test finding short call on underlying."""
    positions = [
        {
            "symbol": "TEST_CALL",
            "assetType": "OPTION",
            "underlying": "TEST",
            "type": "CALL",
            "qty": -1,
            "strike": 150.00,
        },
        {
            "symbol": "TEST_PUT",
            "assetType": "OPTION",
            "underlying": "TEST",
            "type": "PUT",
            "qty": -1,
        },
    ]
    call = _find_short_call(positions, "TEST")
    assert call is not None
    assert call["type"] == "CALL"
    assert call["qty"] == -1


def test_covered_strangle_proposed(mock_snapshot_data, mock_config):
    """Test that covered strangle is proposed for PLTR (existing call + available put chain)."""
    upgrades = compute_strategy_upgrades(
        mock_snapshot_data,
        equity_reviews=[],
        options_reviews=[],
        params=mock_config,
    )

    # Find the PLTR strangle recommendation
    pltr_strangles = [u for u in upgrades if u.get("type") == "covered_strangle" and u.get("underlying") == "PLTR"]
    assert len(pltr_strangles) >= 1

    strangle = pltr_strangles[0]
    assert strangle["proposed"]["strike"] == 125.00
    assert strangle["proposed"]["qty"] == 7
    assert strangle["concentration_check"]["blocked"] is True  # PLTR at 22% already


def test_covered_strangle_amzn_allowed(mock_snapshot_data, mock_config):
    """Test that AMZN strangle is allowed (not blocked by concentration)."""
    upgrades = compute_strategy_upgrades(
        mock_snapshot_data,
        equity_reviews=[],
        options_reviews=[],
        params=mock_config,
    )

    amzn_strangles = [u for u in upgrades if u.get("type") == "covered_strangle" and u.get("underlying") == "AMZN"]
    assert len(amzn_strangles) >= 1

    strangle = amzn_strangles[0]
    assert strangle["concentration_check"]["blocked"] is False  # AMZN at 3.25%, collateral only 3%


def test_collar_proposed_for_goog(mock_snapshot_data, mock_config):
    """Test that collar is proposed for GOOG (big gain + good weight)."""
    upgrades = compute_strategy_upgrades(
        mock_snapshot_data,
        equity_reviews=[],
        options_reviews=[],
        params=mock_config,
    )

    goog_collars = [u for u in upgrades if u.get("type") == "collar" and u.get("underlying") == "GOOG"]
    assert len(goog_collars) >= 1

    collar = goog_collars[0]
    assert collar["proposed_put"]["strike"] == 350.00
    assert collar["floor_strike"] == 350.00
    assert collar["scenario_minus_20pct"]["saves"] > 0


def test_sublot_completion_meta(mock_snapshot_data, mock_config):
    """Test that sub-lot completion is proposed for META (46 shares)."""
    upgrades = compute_strategy_upgrades(
        mock_snapshot_data,
        equity_reviews=[],
        options_reviews=[],
        params=mock_config,
    )

    meta_subs = [u for u in upgrades if u.get("type") == "sublot_completion" and u.get("underlying") == "META"]
    assert len(meta_subs) >= 1

    sub = meta_subs[0]
    assert sub["shares_held"] == 46
    assert sub["shares_to_buy"] == 54
    assert sub["cost"] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ─── NaN-safety: yfinance/E*TRADE quote failures can leave equity positions
# with price=None or NaN. The strategy_upgrades math (`round(price * 1.06 / 5)
# * 5`, etc.) crashes with `ValueError: cannot convert float NaN to integer`.
# _safe_price coerces to 0 so the existing `price <= 0` skip-guards catch it.
# (Symptom 2026-06-18: pipeline crash at strategy_upgrades.py:672.)

def test_safe_price_coerces_nan_and_none_and_nonfinite():
    from steps.strategy_upgrades import _safe_price
    assert _safe_price(None) == 0.0
    assert _safe_price(float("nan")) == 0.0
    assert _safe_price(float("inf")) == 0.0
    assert _safe_price(float("-inf")) == 0.0
    assert _safe_price(-5.0) == 0.0
    assert _safe_price(0) == 0.0
    assert _safe_price("garbage") == 0.0


def test_safe_price_passes_real_values_through():
    from steps.strategy_upgrades import _safe_price
    assert _safe_price(123.45) == 123.45
    assert _safe_price("456.78") == 456.78
    assert _safe_price(1) == 1.0


def test_safe_price_blocks_the_original_round_crash():
    """The exact failure mode: round(NaN * 1.06 / 5) * 5 raises ValueError.
    With _safe_price, the price collapses to 0 and the upstream guard skips."""
    from steps.strategy_upgrades import _safe_price
    price = _safe_price(float("nan"))
    assert price == 0.0
    # The skip-guard `if price <= 0: continue` evaluates True now — confirming
    # the position is bypassed rather than reaching the round() call.
    assert price <= 0


# ─────────────────────────────────────────────────────────────────────────────
# CLAUDE.md hard rule #29 — Position Tier Framework
# Tier A (NVDA, GOOG, MSFT, META, PLTR, AMZN, SPY/VOO): NO CC recs.
# Tier B (MU, SMH): conservative CC only.
# Tier C (default): current discipline (no behavior change).
# ─────────────────────────────────────────────────────────────────────────────


_TIER_CONFIG = {
    "max_position_pct": 0.10,
    "max_sector_pct": 0.35,
    "position_tiers": {
        "tier_a_core": ["NVDA", "GOOG", "MSFT", "META", "PLTR", "AMZN", "SPY", "VOO"],
        "tier_b_income": ["MU", "SMH"],
    },
    "covered_call_tiers": {
        "tier_a": {"enabled": False, "rsi_floor": 999, "min_otm_pct": 999,
                   "max_delta": 0.0, "coverage_cap_pct": 0, "max_dte": 0,
                   "roll_up_trigger": 0.92, "tax_aware_assignment_block": True},
        "tier_b": {"enabled": True, "rsi_floor": 70, "min_otm_pct": 10.0,
                   "max_delta": 0.15, "coverage_cap_pct": 50, "max_dte": 30,
                   "roll_up_trigger": 0.93, "tax_aware_assignment_block": True},
        "tier_c": {"enabled": True, "rsi_floor": 60, "min_otm_pct": 4.0,
                   "max_delta": 0.30, "coverage_cap_pct": 100, "max_dte": 45,
                   "roll_up_trigger": 0.97, "tax_aware_assignment_block": False},
    },
}


def _tier_snapshot():
    """Minimal snapshot with three CC-eligible holdings — one per tier."""
    return {
        "positions": [
            # NVDA (Tier A) — 700 shares: should yield NO CC rec.
            {"symbol": "NVDA", "assetType": "EQUITY", "qty": 700,
             "price": 1000.00, "costBasis": 400.00},
            # MU (Tier B) — 700 shares: should yield a CC rec capped at 50%
            # coverage (max 3 contracts) with the tier-B envelope.
            {"symbol": "MU", "assetType": "EQUITY", "qty": 700,
             "price": 120.00, "costBasis": 100.00},
            # VRT (Tier C / default) — 300 shares: standard CC rec.
            {"symbol": "VRT", "assetType": "EQUITY", "qty": 300,
             "price": 130.00, "costBasis": 80.00},
        ],
        "balance": {"accountValue": 1_000_000, "cash": 50_000},
        "chains": {},
        "earnings_calendar": {},
        "quotes": {
            "NVDA": {"last": 1000.0},
            "MU":   {"last": 120.0},
            "VRT":  {"last": 130.0},
        },
        "technicals": {
            # All three at RSI ~65 so the global rsi gate (≥60 to write CC)
            # doesn't filter them out. Tier B's stricter 70 floor will.
            "NVDA": {"rsi_14": 65},
            "MU":   {"rsi_14": 65},
            "VRT":  {"rsi_14": 65},
        },
    }


def _mock_call_quote(*, strike, delta, mid=2.50, bid=2.40, ask=2.60,
                     selected_by="delta", expiration="2026-08-15"):
    return {
        "strike": strike, "bid": bid, "mid": mid, "ask": ask,
        "delta": delta, "iv": 0.45, "open_interest": 100,
        "expiration": expiration, "source": "etrade_live",
        "selected_by": selected_by, "sr_anchor": None,
    }


def test_tier_a_nvda_gets_no_cc_recommendation(monkeypatch):
    """NVDA (Tier A) MUST NOT produce a write_covered_call upgrade.
    Instead, it should appear as a tier_a_no_cc transparency record."""
    # Even if the chain fetcher would return a healthy quote, NVDA should
    # be diverted to the tier_a_no_cc record before the chain is consulted.
    fake = _FakeFetcher(
        delta_quote=_mock_call_quote(strike=1100, delta=0.20),
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))

    snap = _tier_snapshot()
    upgrades = compute_strategy_upgrades(
        snap, equity_reviews=[], options_reviews=[], params=_TIER_CONFIG,
    )

    nvda_writes = [
        u for u in upgrades
        if u.get("type") == "write_covered_call" and u.get("underlying") == "NVDA"
    ]
    assert nvda_writes == [], (
        "Tier A NVDA should have NO write_covered_call upgrade; got: "
        + str(nvda_writes)
    )

    nvda_a_records = [
        u for u in upgrades
        if u.get("type") == "tier_a_no_cc" and u.get("underlying") == "NVDA"
    ]
    assert len(nvda_a_records) == 1, (
        "Expected a single tier_a_no_cc record for NVDA; got: " + str(nvda_a_records)
    )
    rec = nvda_a_records[0]
    assert rec["tier"] == "A"
    assert rec["shares_held"] == 700
    assert "Tier A" in rec["rationale"]


def test_tier_b_mu_gets_conservative_cc_with_50pct_coverage_cap(monkeypatch):
    """MU (Tier B) at RSI 70+ should get a CC rec, but:
       - max 3 contracts (50% of 7 round lots = 3)
       - strike ≥ 10% OTM (tier-B floor)
       - delta ≤ 0.15
       - ≤ 30 DTE
    """
    snap = _tier_snapshot()
    # Bump MU's RSI to 75 so the tier-B floor (70) is satisfied — otherwise
    # the rec gets flagged with a tier_violations entry and we want to test
    # the happy path coverage-cap math.
    snap["technicals"]["MU"]["rsi_14"] = 75

    # Mock the fetcher to return a strike ~13% OTM at 0.13 delta — a
    # tier-B-compliant pick. The strategy_upgrades CC logic passes
    # target_delta=min(0.25, 0.15) = 0.15 to the fetcher; we honor that
    # by returning a 0.13-delta strike at $136 (13.3% OTM from $120).
    fake = _FakeFetcher(
        delta_quote=_mock_call_quote(
            strike=136.0, delta=0.13, mid=1.10, bid=1.05, ask=1.15,
            expiration="2026-07-25",  # ~25 DTE from 2026-06-30
        ),
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))

    upgrades = compute_strategy_upgrades(
        snap, equity_reviews=[], options_reviews=[], params=_TIER_CONFIG,
    )
    mu_writes = [
        u for u in upgrades
        if u.get("type") == "write_covered_call" and u.get("underlying") == "MU"
    ]
    assert len(mu_writes) == 1, (
        "Expected exactly one MU CC rec; got: " + str(mu_writes)
    )
    rec = mu_writes[0]
    assert rec["tier"] == "B"
    # 700 shares = 7 round lots; tier-B 50% coverage cap = max 3 contracts
    assert rec["contracts_writable"] == 3, (
        f"Tier-B 50% coverage of 7 lots = 3 contracts; got {rec['contracts_writable']}"
    )
    # No tier violations on the happy path
    assert rec.get("tier_violations") == [], (
        f"Expected zero tier violations on healthy MU rec; got: {rec.get('tier_violations')}"
    )
    # Tier-B knobs threaded through to the rec
    assert rec["tier_max_delta"] == 0.15
    assert rec["tier_min_otm_pct"] == 10.0
    assert rec["tier_coverage_cap_pct"] == 50


def test_tier_b_mu_records_tier_violation_when_strike_too_close(monkeypatch):
    """If the chain returns a strike only 5% OTM (violating Tier B's 10%
    floor), the rec is still emitted but carries a tier_violations entry
    so the renderer can show the user WHY the trade is off-policy."""
    snap = _tier_snapshot()
    snap["technicals"]["MU"]["rsi_14"] = 75
    # 5% OTM from $120 = $126, way under the tier-B 10% floor
    fake = _FakeFetcher(
        delta_quote=_mock_call_quote(strike=126.0, delta=0.20, expiration="2026-07-25"),
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))

    upgrades = compute_strategy_upgrades(
        snap, equity_reviews=[], options_reviews=[], params=_TIER_CONFIG,
    )
    mu = next(
        u for u in upgrades
        if u.get("type") == "write_covered_call" and u.get("underlying") == "MU"
    )
    assert mu["tier"] == "B"
    violations = mu.get("tier_violations") or []
    # We expect BOTH the OTM% violation AND the delta violation to fire
    assert any("OTM" in v for v in violations), f"Missing OTM violation: {violations}"
    assert any("delta" in v for v in violations), f"Missing delta violation: {violations}"


def test_tier_c_vrt_keeps_current_discipline_no_regression(monkeypatch):
    """VRT (Tier C — default for unconfigured tickers) MUST behave exactly
    like the pre-framework code: standard 0.25 delta / 6% OTM / 100% coverage."""
    fake = _FakeFetcher(
        delta_quote=_mock_call_quote(
            strike=140.0, delta=0.24, mid=2.50, expiration="2026-08-05",
        ),
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))

    snap = _tier_snapshot()
    upgrades = compute_strategy_upgrades(
        snap, equity_reviews=[], options_reviews=[], params=_TIER_CONFIG,
    )
    vrt = next(
        (u for u in upgrades
         if u.get("type") == "write_covered_call" and u.get("underlying") == "VRT"),
        None,
    )
    assert vrt is not None, "Tier C VRT should produce a CC recommendation"
    assert vrt["tier"] == "C"
    # 300 shares = 3 round lots; tier-C 100% coverage = 3 contracts
    assert vrt["contracts_writable"] == 3
    assert vrt["tier_max_delta"] == 0.30
    assert vrt["tier_min_otm_pct"] == 4.0
    assert vrt["tier_coverage_cap_pct"] == 100
    # No tier violations — delta 0.24 < 0.30, ~7.7% OTM > 4% floor
    assert vrt.get("tier_violations") == []


def test_backward_compat_no_tier_config_preserves_legacy_behavior(monkeypatch):
    """If `position_tiers` is missing entirely, every ticker → Tier C → the
    pre-framework CC code path. NVDA, GOOG etc. all get CC recs as before."""
    legacy_cfg = {"max_position_pct": 0.10}  # NO position_tiers block
    fake = _FakeFetcher(
        delta_quote=_mock_call_quote(strike=1100, delta=0.20),
    )
    monkeypatch.setattr(_su, "_load_chain_fetcher", lambda: (fake, None))

    snap = _tier_snapshot()
    upgrades = compute_strategy_upgrades(
        snap, equity_reviews=[], options_reviews=[], params=legacy_cfg,
    )
    nvda_writes = [
        u for u in upgrades
        if u.get("type") == "write_covered_call" and u.get("underlying") == "NVDA"
    ]
    assert len(nvda_writes) == 1, (
        "With NO position_tiers config, NVDA must get a CC rec (legacy behavior); "
        f"got: {nvda_writes}"
    )
    # And NO tier_a_no_cc record
    a_records = [u for u in upgrades if u.get("type") == "tier_a_no_cc"]
    assert a_records == [], (
        f"With NO position_tiers config, no tier_a_no_cc records should appear; got: {a_records}"
    )


# ─── Renderer tests — make sure `tier_a_no_cc` upgrades actually reach the
# ─── Strategy Upgrades panel (the original integration bug — records were
# ─── generated upstream but the panel had no partition for them, so they
# ─── were silently dropped).


def test_render_tier_a_no_cc_section_appears_with_tier_a_records():
    """A `tier_a_no_cc` upgrade dict must render a dedicated section in
    the Strategy Upgrades panel — not be silently dropped (the bug found
    during the tier-framework integration loop, 2026-06-30)."""
    from render.strategy_upgrades_panel import render_strategy_upgrades
    upgrades = [
        {
            "type": "tier_a_no_cc",
            "underlying": "NVDA",
            "tier": "A",
            "shares_held": 701,
            "current_price": 198.87,
            "current_weight_pct": 13.8,
            "rationale": "Tier A core compounder — no CC recommendations.",
        },
        {
            "type": "tier_a_no_cc",
            "underlying": "META",
            "tier": "A",
            "shares_held": 110,
            "current_price": 560.65,
            "current_weight_pct": 6.1,
            "rationale": "Tier A core compounder — no CC recommendations.",
        },
    ]
    md = "\n".join(render_strategy_upgrades(upgrades))
    # The dedicated section header must appear
    assert "Tier A core holdings — no CC" in md, (
        f"Expected 'Tier A core holdings — no CC' section in rendered output; got:\n{md}"
    )
    # Both tickers must appear in the section
    assert "**NVDA**" in md
    assert "**META**" in md
    # The note explaining the policy must appear
    assert "uncapped" in md.lower() or "long-term" in md.lower()


def test_render_sublot_completion_for_tier_a_does_not_promise_cc_income():
    """When a sub-lot completion is for a Tier A holding (e.g. VOO),
    the renderer must NOT include the 'enable 1× covered call writing'
    line — that would contradict the no-CC-on-tier-A policy. Instead
    it should render a tier-aware note."""
    from render.strategy_upgrades_panel import render_strategy_upgrades
    upgrades = [
        {
            "type": "sublot_completion",
            "underlying": "VOO",
            "shares_held": 50,
            "shares_to_buy": 50,
            "current_price": 687.0,
            "cost": 34350.0,
            "post_buy_weight_pct": 6.8,
            "rsi_14": 56,
            "rsi_tag": "RSI 56 🟢 pullback",
            "rsi_note": "favourable spot to add",
            "rsi_decision": "promote",
            "rsi_badge": "✅ RSI favourable",
            "rsi_blocked": False,
            "position_tier": "A",
            "cc_enabled_after_completion": False,
            "rationale": "Complete 100-share lot (Tier A core — no CC)",
        },
    ]
    md = "\n".join(render_strategy_upgrades(upgrades))
    # No "enable 1× covered call writing" promise
    assert "enable 1× covered call writing" not in md, (
        f"Tier A sublot completion must NOT promise CC writing; got:\n{md}"
    )
    # No spurious annualized income estimate
    assert "annualized" not in md.lower(), (
        f"Tier A sublot completion must NOT include income projection; got:\n{md}"
    )
    # Must include a tier-aware note
    assert "Tier A core" in md or "no CC by policy" in md, (
        f"Expected tier-aware note for VOO Tier A sublot; got:\n{md}"
    )


def test_render_sublot_completion_for_tier_c_still_promises_cc_income():
    """Backward compat — Tier C sublots (legacy default) keep the
    'enable 1× covered call writing' projection."""
    from render.strategy_upgrades_panel import render_strategy_upgrades
    upgrades = [
        {
            "type": "sublot_completion",
            "underlying": "ZS",
            "shares_held": 50,
            "shares_to_buy": 50,
            "current_price": 150.0,
            "cost": 7500.0,
            "post_buy_weight_pct": 1.5,
            "rsi_14": 50,
            "rsi_tag": "RSI 50",
            "rsi_note": "neutral",
            "rsi_decision": "keep",
            "rsi_badge": "",
            "rsi_blocked": False,
            "position_tier": "C",
            "cc_enabled_after_completion": True,
            "rationale": "Complete 100-share lot",
        },
    ]
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "enable 1× covered call writing" in md
    assert "annualized" in md.lower()


def test_render_sublot_completion_missing_tier_flags_defaults_to_legacy():
    """When `cc_enabled_after_completion` is absent (older upgrades dict),
    the renderer falls back to the legacy behavior (always show CC
    projection) — no regression for callers that haven't been updated."""
    from render.strategy_upgrades_panel import render_strategy_upgrades
    upgrades = [
        {
            "type": "sublot_completion",
            "underlying": "ANY",
            "shares_held": 50,
            "shares_to_buy": 50,
            "current_price": 100.0,
            "cost": 5000.0,
            "post_buy_weight_pct": 0.5,
            "rationale": "legacy upgrade dict, no tier flags",
        },
    ]
    md = "\n".join(render_strategy_upgrades(upgrades))
    # Defaults to True → CC projection shown
    assert "enable 1× covered call writing" in md


# ─── Panel test — render_risk_alerts must use tier caps when config is set ──


def test_render_risk_alerts_uses_tier_cap_for_tier_a_concentration():
    """A Tier A holding at 14% NLV (cap 22%) must NOT trigger a 'over 10%
    NLV cap' warning — it must render as a tier-aware informational note.
    This was the GOOG/NVDA 'concentration cap' bug found in the integration
    loop (2026-06-30)."""
    from render.panels import render_risk_alerts
    equity_reviews = [
        {"ticker": "NVDA", "weight": 0.138},  # 13.8% — well below tier-A cap 22%
        {"ticker": "GOOG", "weight": 0.153},  # 15.3% — same
        {"ticker": "VRT", "weight": 0.046},   # 4.6% — within tier-C cap 8%
    ]
    config = {
        "position_tiers": {
            "tier_a_core": ["NVDA", "GOOG"],
        },
    }
    out = render_risk_alerts(
        equity_reviews=equity_reviews,
        options_reviews=[],
        regime_data={"regime": "RISK_ON"},
        config=config,
    )
    md = "\n".join(out)
    # Neither NVDA nor GOOG should fire a warning
    assert "NVDA concentration 13.8% — over 10% NLV cap" not in md
    assert "GOOG concentration 15.3% — over 10% NLV cap" not in md
    # Both should appear as tier-aware notes ("within Tier A bounds" or similar)
    assert "NVDA" in md and "Tier A" in md
    assert "GOOG" in md and "Tier A" in md


def test_render_risk_alerts_no_tier_config_falls_back_to_legacy_10pct_cap():
    """Backward compat — no `position_tiers` config → the legacy 10% NLV cap
    warning fires on any holding over 10%, just like before the framework."""
    from render.panels import render_risk_alerts
    equity_reviews = [
        {"ticker": "NVDA", "weight": 0.138},
        {"ticker": "GOOG", "weight": 0.153},
    ]
    out = render_risk_alerts(
        equity_reviews=equity_reviews,
        options_reviews=[],
        regime_data={"regime": "RISK_ON"},
        # No config / no tier block → legacy
    )
    md = "\n".join(out)
    assert "NVDA concentration 13.8% — over 10% NLV cap" in md
    assert "GOOG concentration 15.3% — over 10% NLV cap" in md


def test_render_risk_alerts_tier_b_breach_fires_warning():
    """A Tier B name at 14% (cap 12%) must fire a BREACH warning, not the
    'within bounds' note. The tier framework doesn't silence breaches —
    it raises the threshold for Tier A, lowers it for Tier B/C."""
    from render.panels import render_risk_alerts
    equity_reviews = [
        {"ticker": "MU", "weight": 0.14},  # 14% — over tier-B cap 12%
    ]
    config = {
        "position_tiers": {
            "tier_a_core": ["NVDA"],
            "tier_b_income": ["MU"],
        },
    }
    out = render_risk_alerts(
        equity_reviews=equity_reviews,
        options_reviews=[],
        regime_data={"regime": "RISK_ON"},
        config=config,
    )
    md = "\n".join(out)
    assert "MU" in md
    assert "BREACH" in md
    assert "Tier B" in md
