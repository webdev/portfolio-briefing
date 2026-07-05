"""Tests for the universal capacity-gate DEFERRED tag (CLAUDE.md hard rule #41).

Origin: audit 2026-07-03 #7 — new_ideas correctly blocked new CSPs at 0.16×
coverage, yet 4 actionable new short puts (MU + ZS LT_CSPs, AMZN + META
PULLBACK_CSPs) shipped via the LTO / actions surfaces with NO deferred tag.

Contract: below the floor every new-open rec carries the tag (never hidden —
rule #24); at/above the floor NO tag is applied (regression against
over-application).
"""

import sys
import types
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.capacity_gate import (  # noqa: E402
    DEFERRED_TAG,
    capacity_deferred_tag,
    coverage_ratio_from,
)
from steps import long_term_opportunities as lto  # noqa: E402
from steps.strategy_upgrades import compute_strategy_upgrades  # noqa: E402
from render import panels  # noqa: E402
from render.panels import render_action_list  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Unit — the shared function
# ─────────────────────────────────────────────────────────────────────────────

def test_tag_fires_below_floor_with_measured_ratio():
    tag = capacity_deferred_tag({"stress_coverage": {"coverage_ratio": 0.16}}, {})
    assert tag is not None
    assert DEFERRED_TAG in tag
    assert "0.16" in tag  # measured ratio, not boilerplate (rule #19)


def test_no_tag_at_or_above_floor():
    assert capacity_deferred_tag({"stress_coverage": {"coverage_ratio": 0.50}}, {}) is None
    assert capacity_deferred_tag({"stress_coverage": {"coverage_ratio": 1.20}}, {}) is None


def test_fails_open_on_missing_data():
    assert capacity_deferred_tag(None, {}) is None
    assert capacity_deferred_tag({}, {}) is None
    assert capacity_deferred_tag({"stress_coverage": None}, {}) is None


def test_accepts_gatestate_and_ratio_key_shapes():
    # GateState / StressCoverage-like object
    assert capacity_deferred_tag(SimpleNamespace(coverage_ratio=0.16), {}) is not None
    assert capacity_deferred_tag(SimpleNamespace(coverage_ratio=0.80), {}) is None
    # "ratio" key shape (spec) and nested object
    assert coverage_ratio_from({"stress_coverage": {"ratio": 0.3}}) == 0.3
    assert coverage_ratio_from(
        {"stress_coverage": SimpleNamespace(coverage_ratio=0.3)}) == 0.3


def test_config_floor_override():
    cfg = {"capacity_gates": {"min_coverage_ratio": 0.25}}
    assert capacity_deferred_tag({"stress_coverage": {"coverage_ratio": 0.30}}, cfg) is None
    assert capacity_deferred_tag({"stress_coverage": {"coverage_ratio": 0.20}}, cfg) is not None
    # Falls back to the existing capacity_gates key when the new one is absent
    cfg2 = {"capacity_gates": {"min_stress_coverage_for_new_puts": 0.40}}
    assert capacity_deferred_tag({"stress_coverage": {"coverage_ratio": 0.45}}, cfg2) is None


# ─────────────────────────────────────────────────────────────────────────────
# LTO surface — LONG_DATED_CSP / ADD tagged below floor, untouched above
# ─────────────────────────────────────────────────────────────────────────────

def _stub(monkeypatch, ops):
    class _StubOp:
        def __init__(self, d):
            self._d = d

        def to_dict(self):
            return dict(self._d)

    monkeypatch.setattr(lto, "_load_lt_module", lambda: types.SimpleNamespace(
        generate_long_term_opportunities=lambda **kw: [_StubOp(o) for o in ops]
    ))


def _snap():
    return {
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [], "technicals": {"NVDA": {"rsi_14": 45.0}},
        "iv_ranks": {}, "quotes": {}, "chains": {},
    }


def _add_op() -> dict:
    """Fresh op per test — trigger_reasons must never be shared across tests."""
    return {
        "kind": "ADD", "ticker": "NVDA",
        "concrete_trade": "BUY ~$5,000 of NVDA (~28 shares @ ~$180.00)",
        "trigger_reasons": [], "rationale": "", "yield_or_cost": "",
    }


def test_lto_add_tagged_when_gates_closed(monkeypatch):
    _stub(monkeypatch, [_add_op()])
    ops = lto.generate_long_term_opportunities_step(
        _snap(), [], {}, gate_state=SimpleNamespace(coverage_ratio=0.16))
    add = [o for o in ops if o.get("ticker") == "NVDA"][0]
    assert add["kind"] == "ADD"                    # NOT hidden, NOT demoted
    assert add.get("capacity_deferred") is True
    assert any(DEFERRED_TAG in str(t) for t in add["trigger_reasons"])


def test_lto_add_not_tagged_when_gates_open(monkeypatch):
    """Regression against over-application: coverage ≥ floor → no tag."""
    _stub(monkeypatch, [_add_op()])
    ops = lto.generate_long_term_opportunities_step(
        _snap(), [], {}, gate_state=SimpleNamespace(coverage_ratio=0.80))
    add = [o for o in ops if o.get("ticker") == "NVDA"][0]
    assert not add.get("capacity_deferred")
    assert not any(DEFERRED_TAG in str(t) for t in add.get("trigger_reasons", []))


def test_lto_legacy_call_without_gate_state_unchanged(monkeypatch):
    _stub(monkeypatch, [_add_op()])
    ops = lto.generate_long_term_opportunities_step(_snap(), [], {})
    add = [o for o in ops if o.get("ticker") == "NVDA"][0]
    assert not add.get("capacity_deferred")


# ─────────────────────────────────────────────────────────────────────────────
# PULLBACK CSP surface (panels) — tag rendered on the ticket below floor
# ─────────────────────────────────────────────────────────────────────────────

def _pullback_snap():
    return {
        "balance": {"accountValue": 1_000_000, "cash": 300_000},
        "quotes": {"VRT": {"last": 120.0}},
        "technicals": {"VRT": {"rsi_14": 45.0,
                               "deep": {"long_term_verdict": "uptrend",
                                        "vs_sma200_pct": 5.0}}},
        "positions": [{"symbol": "VRT", "assetType": "EQUITY",
                       "qty": 100, "price": 120.0}],
        "earnings_calendar": {},
        "recommendations_list": [],
        "_config": {"core_positions": ["VRT"], "accounts": []},
    }


def _render_pullback(monkeypatch, analytics):
    monkeypatch.setattr(panels, "find_put_strike_near",
                        lambda *a, **kw: {"strike": 105.0, "mid": 2.10,
                                          "expiration": "2026-08-07"})
    monkeypatch.setattr(panels, "validate_csp", lambda **kw: None)
    equity_reviews = [{"ticker": "VRT", "price": 120.0, "weight": 0.02,
                       "pl_pct": 0.1, "recommendation": "HOLD", "qty": 100}]
    return "\n".join(render_action_list(
        equity_reviews, [], [], analytics=analytics,
        snapshot_data=_pullback_snap(), date_str=date.today().isoformat()))


def test_pullback_csp_tagged_when_coverage_below_floor(monkeypatch):
    md = _render_pullback(monkeypatch,
                          {"stress_coverage": {"coverage_ratio": 0.16}})
    assert "**PULLBACK CSP** VRT" in md       # still fully rendered (rule #24)
    assert DEFERRED_TAG in md
    assert "0.16" in md


def test_pullback_csp_untagged_when_coverage_healthy(monkeypatch):
    md = _render_pullback(monkeypatch,
                          {"stress_coverage": {"coverage_ratio": 0.85}})
    assert "**PULLBACK CSP** VRT" in md
    assert DEFERRED_TAG not in md


# ─────────────────────────────────────────────────────────────────────────────
# Sub-lot surface (strategy upgrades)
# ─────────────────────────────────────────────────────────────────────────────

def _sublot_snap():
    return {"positions": [{"symbol": "VOO", "assetType": "EQUITY",
                           "qty": 45, "price": 550.0}],
            "balance": {"accountValue": 1_000_000},
            "chains": {}, "earnings_calendar": {}, "quotes": {},
            "technicals": {}}


def test_sublot_tagged_when_gates_closed():
    upgrades = compute_strategy_upgrades(
        _sublot_snap(), [], [], {},
        analytics={"stress_coverage": {"coverage_ratio": 0.16}})
    sub = [u for u in upgrades if u.get("type") == "sublot_completion"][0]
    assert sub["capacity_deferred_tag"] and DEFERRED_TAG in sub["capacity_deferred_tag"]


def test_sublot_untagged_when_gates_open_or_legacy():
    upgrades = compute_strategy_upgrades(
        _sublot_snap(), [], [], {},
        analytics={"stress_coverage": {"coverage_ratio": 0.90}})
    sub = [u for u in upgrades if u.get("type") == "sublot_completion"][0]
    assert sub["capacity_deferred_tag"] is None
    # Legacy call signature (no analytics) → unchanged behavior.
    upgrades2 = compute_strategy_upgrades(_sublot_snap(), [], [], {})
    sub2 = [u for u in upgrades2 if u.get("type") == "sublot_completion"][0]
    assert sub2["capacity_deferred_tag"] is None
