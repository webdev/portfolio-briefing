"""Tests for the universal 5% strike-overlap check (CLAUDE.md hard rule #40).

Origin: audit 2026-07-03 #4 — AMZN $215P PULLBACK_CSP shipped while the user
held AMZN $225P (4.4% apart), because the overlap check lived ONLY in the
LT_CSP path. Every generator surface must now catch overlap uniformly, and
the pre-trade validator backstops it as Rule 15 PUT_STRIKE_OVERLAP (BLOCK).
"""

import sys
import types
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import pre_trade_validator as ptv  # noqa: E402
from analysis.put_overlap_check import OVERLAP_PCT, check_strike_overlap  # noqa: E402
from steps import long_term_opportunities as lto  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Unit — the shared function
# ─────────────────────────────────────────────────────────────────────────────

def test_amzn_215_overlaps_held_225():
    """The audit case: $215P vs held $225P = 4.4% — inside the 5% band."""
    r = check_strike_overlap("AMZN", 215.0, [225.0])
    assert r["overlap"] is True
    assert r["existing_strike"] == 225.0
    assert abs(r["distance_pct"] - (10.0 / 225.0)) < 1e-9  # 4.44%, measured


def test_far_strike_does_not_overlap():
    r = check_strike_overlap("AMZN", 190.0, [225.0])
    assert r["overlap"] is False
    assert r["existing_strike"] == 225.0  # nearest still reported
    assert r["distance_pct"] > OVERLAP_PCT


def test_no_holdings_no_overlap():
    for empty in ([], None, {}, {"OTHER": {"strikes": [100.0]}}):
        r = check_strike_overlap("AMZN", 215.0, empty)
        assert r == {"overlap": False, "existing_strike": None, "distance_pct": None}


def test_accepts_all_caller_shapes():
    """Every generator carries a different shape — all must behave identically."""
    shapes = [
        [225.0],                                        # bare strike list
        [{"strike": 225.0, "expiration": "2026-08-21"}],  # validator entries
        {"AMZN": {"strikes": [225.0], "count": 1}},     # LTO / panels map
        {"AMZN": [225.0]},                              # map of lists
    ]
    for shape in shapes:
        assert check_strike_overlap("AMZN", 215.0, shape)["overlap"] is True
        assert check_strike_overlap("AMZN", 190.0, shape)["overlap"] is False


def test_nearest_of_multiple_strikes_reported():
    r = check_strike_overlap("VRT", 310.0, [300.0, 315.0])
    assert r["overlap"] is True
    assert r["existing_strike"] == 315.0  # nearest, not first


def test_garbage_inputs_fail_open():
    assert check_strike_overlap("X", None, [225.0])["overlap"] is False
    assert check_strike_overlap("X", 0, [225.0])["overlap"] is False
    assert check_strike_overlap("X", 215.0, ["bogus", None])["overlap"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Validator Rule 15 — late-stage backstop
# ─────────────────────────────────────────────────────────────────────────────

def _csp_ctx(**kw) -> ptv.PreTradeContext:
    defaults = dict(
        ticker="AMZN", strike=215.0,
        expiration=date.today() + timedelta(days=35),
        option_type="PUT", action="SELL_OPEN", quantity=1,
        spot=226.0, rsi=45.0, nlv=1_000_000.0, cash=300_000.0,
        stress_coverage=0.60,
        existing_short_puts=[], existing_long_puts=[],
        existing_short_calls=[], held_shares=0,
        obligation_by_expiration={},
    )
    defaults.update(kw)
    return ptv.PreTradeContext(**defaults)


def test_validator_rule15_blocks_overlapping_put():
    """Rule 15 PUT_STRIKE_OVERLAP fires BLOCK on the AMZN $215P/$225P case."""
    findings = ptv.validate_proposed_trade(_csp_ctx(
        existing_short_puts=[{"strike": 225.0, "expiration": "2026-08-21", "qty": 1}],
    ))
    overlap = [f for f in findings if f.rule_id == "PUT_STRIKE_OVERLAP"]
    assert len(overlap) == 1
    assert overlap[0].severity == ptv.SEV_BLOCK
    assert "225" in overlap[0].reason
    assert ptv.has_blockers(findings)


def test_validator_rule15_silent_without_overlap():
    findings = ptv.validate_proposed_trade(_csp_ctx(
        strike=190.0,
        existing_short_puts=[{"strike": 225.0, "expiration": "2026-08-21", "qty": 1}],
    ))
    assert not [f for f in findings if f.rule_id == "PUT_STRIKE_OVERLAP"]


def test_validator_rule15_ignores_calls_and_closes():
    """Only NEW short puts are gated — CC writes and closes never fire it."""
    cc = _csp_ctx(option_type="CALL", strike=240.0, held_shares=100,
                  existing_short_puts=[{"strike": 225.0}])
    assert not [f for f in ptv.validate_proposed_trade(cc)
                if f.rule_id == "PUT_STRIKE_OVERLAP"]
    close = _csp_ctx(action="BUY_CLOSE",
                     existing_short_puts=[{"strike": 225.0}])
    assert not [f for f in ptv.validate_proposed_trade(close)
                if f.rule_id == "PUT_STRIKE_OVERLAP"]


# ─────────────────────────────────────────────────────────────────────────────
# LTO surface — uses the SAME shared function (refactor must not drift)
# ─────────────────────────────────────────────────────────────────────────────

def test_lto_lt_csp_overlap_demotes_via_shared_function(monkeypatch):
    class _StubOp:
        def __init__(self, d):
            self._d = d

        def to_dict(self):
            return dict(self._d)

    ops = [{
        "kind": "LONG_DATED_CSP", "ticker": "AMZN",
        "concrete_trade": "SELL 1× AMZN $215P ~75 DTE",
        "trigger_reasons": [], "rationale": "", "yield_or_cost": "",
    }]
    monkeypatch.setattr(lto, "_load_lt_module", lambda: types.SimpleNamespace(
        generate_long_term_opportunities=lambda **kw: [_StubOp(o) for o in ops]
    ))
    snap = {
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [
            {"symbol": "AMZN", "assetType": "EQUITY", "qty": 100, "price": 226.0},
            {"symbol": "AMZN_PUT_20260821_225", "assetType": "OPTION",
             "underlying": "AMZN", "type": "PUT", "qty": -1, "strike": 225.0,
             "expiration": "2026-08-21"},
        ],
        "technicals": {"AMZN": {"rsi_14": 45.0,
                                "deep": {"long_term_verdict": "uptrend",
                                         "vs_sma200_pct": 4.2}}},
        "iv_ranks": {}, "quotes": {}, "chains": {},
    }
    result = lto.generate_long_term_opportunities_step(snap, [], {})
    amzn = [o for o in result if o.get("ticker") == "AMZN"][0]
    assert amzn["kind"] == "SKIPPED_LT_CSP"
    assert "concentrates rather than diversifies" in amzn["skip_reason"]
    assert "$225" in amzn["skip_reason"]
