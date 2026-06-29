"""Tests for portfolio capacity gates (06-wheel-parameters.md §7A).

Covers:
1. Portfolio gates CLOSED at coverage 0.04x / cash 3.2% (the June 2026 state)
2. Portfolio gates OPEN at coverage 0.6x with healthy cash + obligation
3. Per-name second-put exception (allowed under 6% NLV + 60d gap, blocked otherwise)
4. Expiry-cluster cap (single expiration's put obligation ≤ 25% NLV)
"""

from datetime import date, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.capacity_gates import GateState, check_new_entry, evaluate_gates


TODAY = date.today()


def _put(symbol: str, strike: float, qty: int, exp: date) -> dict:
    """Raw snapshot-shaped short-put position (no position_type — the gates
    module must derive it the same way compute_analytics does)."""
    return {
        "symbol": symbol,
        "assetType": "OPTION",
        "type": "PUT",
        "underlying": symbol,
        "strike": float(strike),
        "expiration": exp.isoformat(),
        "qty": qty,
        "underlying_price": float(strike) * 1.1,
    }


def _open_gate_state(positions=None, nlv=1_000_000.0) -> GateState:
    return GateState(
        open=True, coverage_ratio=1.0, cash_pct=0.20, obligation_pct=0.20,
        reasons=[], banner="CAPACITY: ... | ENTRY GATES: OPEN",
        positions=positions or [], nlv=nlv,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio-level gates
# ─────────────────────────────────────────────────────────────────────────────

def test_gates_closed_at_low_coverage_and_low_cash():
    """The June 2026 state: coverage 0.04x, cash 3.2% NLV → gates CLOSED with
    both failures named, coverage first (gate evaluation order §7A)."""
    exp = TODAY + timedelta(days=45)
    positions = [
        _put("PATH", 80, -50, exp),    # $400k obligation
        _put("MU", 100, -40, exp),     # $400k obligation
    ]
    gs = evaluate_gates(positions, cash=32_000, nlv=1_000_000, config={})

    assert gs.open is False
    assert abs(gs.coverage_ratio - 0.04) < 1e-9
    assert abs(gs.cash_pct - 0.032) < 1e-9
    assert abs(gs.obligation_pct - 0.80) < 1e-9
    assert gs.reasons[0] == "coverage 0.04x < 0.50x"
    assert any("cash 3.2%" in r for r in gs.reasons)
    assert gs.banner == (
        "CAPACITY: coverage 0.04x | cash 3.2% | obligation 80% NLV | "
        "ENTRY GATES: CLOSED (coverage 0.04x < 0.50x)"
    )


def test_gates_open_when_coverage_cash_and_obligation_healthy():
    """Coverage 0.6x, cash well above 5%, obligation 50% NLV → gates OPEN,
    no reasons, OPEN banner."""
    exp = TODAY + timedelta(days=45)
    positions = [_put("AAPL", 250, -20, exp)]   # $500k obligation
    gs = evaluate_gates(positions, cash=300_000, nlv=1_000_000, config={})

    assert gs.open is True
    assert gs.reasons == []
    assert abs(gs.coverage_ratio - 0.6) < 1e-9
    assert abs(gs.obligation_pct - 0.50) < 1e-9
    assert "ENTRY GATES: OPEN" in gs.banner


def test_total_obligation_ceiling_blocks_alone():
    """Obligation > 80% NLV closes the gates even with plenty of cash."""
    exp = TODAY + timedelta(days=45)
    positions = [_put("NVDA", 850, -10, exp)]   # $850k obligation
    gs = evaluate_gates(positions, cash=600_000, nlv=1_000_000, config={})

    assert gs.open is False
    assert len(gs.reasons) == 1
    assert "obligation 85% NLV > 80% NLV" in gs.reasons[0]
    assert "ENTRY GATES: CLOSED" in gs.banner


def test_no_short_puts_means_gates_open():
    """Empty book → infinite coverage, zero obligation; only cash can close."""
    gs = evaluate_gates([], cash=100_000, nlv=1_000_000, config={})
    assert gs.open is True
    assert gs.obligation_pct == 0.0


def test_config_overrides_are_read():
    """Thresholds come from config.capacity_gates when present."""
    exp = TODAY + timedelta(days=45)
    positions = [_put("AAPL", 250, -20, exp)]   # coverage 0.6 with $300k cash
    cfg = {"capacity_gates": {"min_stress_coverage_for_new_puts": 0.70}}
    gs = evaluate_gates(positions, cash=300_000, nlv=1_000_000, config=cfg)
    assert gs.open is False
    assert "coverage 0.60x < 0.70x" in gs.reasons[0]


# ─────────────────────────────────────────────────────────────────────────────
# Per-name second-put exception
# ─────────────────────────────────────────────────────────────────────────────

def test_second_put_allowed_under_collateral_and_gap_exception():
    """2nd put on a name is OK when combined collateral ≤ 6% NLV AND the
    expirations are ≥ 60 days apart."""
    existing = [_put("MU", 100, -1, TODAY + timedelta(days=30))]  # $10k
    ok, reason = check_new_entry(
        "MU", collateral=20_000, expiry=TODAY + timedelta(days=120),  # 90d gap
        positions=existing, nlv=1_000_000, gate_state=None, config={},
    )
    assert ok is True
    assert reason == ""


def test_second_put_blocked_when_expiry_gap_too_small():
    existing = [_put("MU", 100, -1, TODAY + timedelta(days=30))]
    ok, reason = check_new_entry(
        "MU", collateral=20_000, expiry=TODAY + timedelta(days=60),  # 30d gap
        positions=existing, nlv=1_000_000, gate_state=None, config={},
    )
    assert ok is False
    assert "30d apart" in reason and "60d" in reason


def test_second_put_blocked_when_combined_collateral_over_6pct():
    existing = [_put("MU", 100, -1, TODAY + timedelta(days=30))]  # $10k
    ok, reason = check_new_entry(
        "MU", collateral=55_000, expiry=TODAY + timedelta(days=120),
        positions=existing, nlv=1_000_000, gate_state=None, config={},  # 6% = $60k
    )
    assert ok is False
    assert "combined collateral" in reason


def test_third_put_blocked_outright():
    """≥2 existing short puts on a name → hard block, no exception."""
    existing = [
        _put("PATH", 12, -1, TODAY + timedelta(days=30)),
        _put("PATH", 11, -1, TODAY + timedelta(days=100)),
    ]
    ok, reason = check_new_entry(
        "PATH", collateral=1_100, expiry=TODAY + timedelta(days=200),
        positions=existing, nlv=1_000_000, gate_state=None, config={},
    )
    assert ok is False
    assert "already 2 short puts" in reason


def test_per_name_collateral_cap_blocks_first_put_too():
    """A FIRST put that alone breaches the 6% NLV per-name cap is blocked."""
    ok, reason = check_new_entry(
        "NVDA", collateral=70_000, expiry=TODAY + timedelta(days=45),
        positions=[], nlv=1_000_000, gate_state=None, config={},
    )
    assert ok is False
    assert "per-name cap" in reason


def test_closed_portfolio_gates_fail_every_candidate():
    gs = GateState(open=False, coverage_ratio=0.04, cash_pct=0.032,
                   obligation_pct=0.81, reasons=["coverage 0.04x < 0.50x"],
                   banner="CAPACITY: ... CLOSED")
    ok, reason = check_new_entry(
        "AAPL", collateral=10_000, expiry=TODAY + timedelta(days=45),
        positions=[], nlv=1_000_000, gate_state=gs, config={},
    )
    assert ok is False
    assert "entry gates closed" in reason


# ─────────────────────────────────────────────────────────────────────────────
# Expiry-cluster cap
# ─────────────────────────────────────────────────────────────────────────────

def test_expiry_cluster_cap_blocks_entry_pushing_over_25pct():
    """Existing puts hold 24% NLV on one Friday; a new entry on the SAME
    expiration pushing it past 25% is rejected."""
    cluster_exp = TODAY + timedelta(days=70)
    existing = [_put("AAPL", 800, -3, cluster_exp)]  # $240k = 24% NLV
    ok, reason = check_new_entry(
        "MSFT", collateral=20_000, expiry=cluster_exp,  # → 26%
        positions=existing, nlv=1_000_000, gate_state=None, config={},
    )
    assert ok is False
    assert "expiry cluster" in reason
    assert cluster_exp.isoformat() in reason


def test_expiry_cluster_cap_allows_different_expiration():
    """Same entry on a different (uncrowded) expiration passes."""
    cluster_exp = TODAY + timedelta(days=70)
    existing = [_put("AAPL", 800, -3, cluster_exp)]
    ok, reason = check_new_entry(
        "MSFT", collateral=20_000, expiry=cluster_exp + timedelta(days=28),
        positions=existing, nlv=1_000_000, gate_state=None, config={},
    )
    assert ok is True
    assert reason == ""


# ─────────────────────────────────────────────────────────────────────────────
# Renderer integration — banner + ENTRY NOW downgrade + ticket suppression
# ─────────────────────────────────────────────────────────────────────────────

def _closed_gate_state() -> GateState:
    return GateState(
        open=False, coverage_ratio=0.04, cash_pct=0.032, obligation_pct=0.81,
        reasons=["coverage 0.04x < 0.50x", "cash 3.2% < 5% NLV",
                 "obligation 81% NLV > 80% NLV"],
        banner=("CAPACITY: coverage 0.04x | cash 3.2% | obligation 81% NLV | "
                "ENTRY GATES: CLOSED (coverage 0.04x < 0.50x)"),
    )


def _scout_payload():
    return {
        "themes": {"semis": {"name": "Semis", "group": "The AI Buildout",
                             "anchors": ["AMD"], "etfs": []}},
        "results_by_theme": {
            "semis": [
                {"ticker": "AMD", "spot": 150.0, "rsi_14": 40, "iv_rank": 60,
                 "sma_200": 140, "drawdown_pct": 12, "fivedayret_pct": -1.2,
                 "verdict": "CSP ENTRY (fat premium)", "rationale": ["fat premium"],
                 "csp_entry": {"strike": 135, "mid": 2.1, "bid": 2.0, "ask": 2.2,
                               "expiration": (TODAY + timedelta(days=35)).isoformat(),
                               "dte": 35}},
            ],
        },
        "generated_at_iso": "2026-06-11T08:00:00",
    }


def test_when_to_enter_banner_and_deferred_tag_when_closed():
    """Hard rule #24 (and bug #8 fix): when gates are CLOSED the When-To-Enter
    report keeps ENTRY NOW classifications with their live tickets, tagged
    `· ⏸ DEFERRED`, instead of demoting to "WAIT — entry gates closed".
    User explicit rule: "I always want to know of great opportunities."
    """
    from steps.when_to_enter import render_when_to_enter_report

    gs = _closed_gate_state()
    md = render_when_to_enter_report(_scout_payload(), generated_at="X", gate_state=gs)
    assert md.splitlines()[0] == gs.banner          # banner still the first line
    # ENTRY classification KEPT (not demoted to 0).
    assert "🟢 0 ENTRY NOW" not in md
    # DEFERRED tag attached to the entry label and surfaced in summary.
    assert "⏸ DEFERRED" in md
    assert "of which" in md and "DEFERRED" in md
    # Live ticket preserved (the whole point of rule #24).
    assert "SELL 1×" in md
    # Capacity-gated note appears in the trigger.
    assert "Capacity-gated" in md
    # The legacy demotion label MUST NOT appear (it was the bug).
    assert "WAIT — entry gates closed" not in md


def test_when_to_enter_unchanged_without_gate_state():
    from steps.when_to_enter import render_when_to_enter_report

    md = render_when_to_enter_report(_scout_payload(), generated_at="X")
    assert "ENTRY NOW — CSP" in md
    assert "SELL 1×" in md
    assert "entry gates closed" not in md


def test_candidate_report_banner_and_deferred_tag_when_closed():
    """Hard rule #24: candidates report keeps the full live ticket but tags
    it as `⏸ Deferred (capacity gated)` instead of suppressing it with a
    "— blocked: ..." message. Banner explains the gate once at the top."""
    from steps.candidate_research import render_candidate_report

    gs = _closed_gate_state()
    md = render_candidate_report(_scout_payload(), fv_by_ticker={}, config={},
                                 generated_at="T", gate_state=gs)
    assert md.splitlines()[0] == gs.banner               # banner first line
    # Live ticket PRESERVED (rule #24); just tagged DEFERRED.
    assert "Deferred (capacity gated)" in md
    assert "SELL 1×" in md
    # Legacy "— blocked: ..." text must not appear (deprecated by rule #24).
    assert "— blocked: " not in md


def test_candidate_report_caps_headline_candidates_at_3():
    from steps.candidate_research import render_candidate_report

    results = []
    for i, tk in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
        results.append({
            "ticker": tk, "spot": 100.0, "rsi_14": 42, "iv_rank": 50 + i,
            "sma_200": 95, "drawdown_pct": 10, "fivedayret_pct": -1.0,
            "verdict": "CSP ENTRY (fat premium)", "rationale": ["fat premium"],
            "rating_tier": 5 - i,
            "csp_entry": {"strike": 90, "mid": 1.5, "bid": 1.4, "ask": 1.6,
                          "expiration": (TODAY + timedelta(days=35)).isoformat(),
                          "dte": 35},
        })
    payload = {
        "themes": {"semis": {"name": "Semis", "group": "G", "anchors": [], "etfs": []}},
        "results_by_theme": {"semis": results},
    }
    gs = _open_gate_state(nlv=10_000_000)  # roomy book — nothing blocked per-name
    md = render_candidate_report(payload, fv_by_ticker={}, config={},
                                 generated_at="T", gate_state=gs)
    assert md.count("Entry (CSP):") == 3            # only 3 headline cards
    assert "More qualifying names" in md
    overflow_block = md.split("More qualifying names")[1]
    assert "`DDD`" in overflow_block and "`EEE`" in overflow_block


def test_candidate_report_blocks_name_failing_per_name_cap():
    from steps.candidate_research import render_candidate_report

    # AMD already carries 2 short puts — may not render as CANDIDATE at all.
    existing = [
        _put("AMD", 120, -1, TODAY + timedelta(days=30)),
        _put("AMD", 110, -1, TODAY + timedelta(days=100)),
    ]
    gs = _open_gate_state(positions=existing, nlv=10_000_000)
    md = render_candidate_report(_scout_payload(), fv_by_ticker={}, config={},
                                 generated_at="T", gate_state=gs)
    assert "Blocked — capacity gates" in md
    blocked_block = md.split("Blocked — capacity gates")[1]
    assert "`AMD`" in blocked_block and "already 2 short puts" in blocked_block
    assert "Entry (CSP):" not in md                 # no ticket for a blocked name


def test_candidate_report_unchanged_without_gate_state():
    from steps.candidate_research import render_candidate_report

    md = render_candidate_report(_scout_payload(), fv_by_ticker={}, config={},
                                 generated_at="T")
    assert md.startswith("# Candidate Research — T")
    assert "Entry (CSP):" in md
    assert "CAPACITY:" not in md


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])


# ─── NaN-safety guards (2026-06-18: snapshot NLV came back NaN from yfinance
# 404s on SPY/SMH/SOXX/VOO; capacity_gates.evaluate_gates crashed with
# decimal.InvalidOperation on `nlv_d > 0`) ──────────────────────────────────

def test_evaluate_gates_handles_nan_nlv_without_crashing():
    """A NaN NLV from upstream must NOT crash the pipeline. The gate should
    treat NaN as zero, evaluate cleanly (likely as CLOSED), and return a
    well-formed GateState so callers can render the briefing."""
    from analysis.capacity_gates import evaluate_gates
    gs = evaluate_gates([], cash=float("nan"), nlv=float("nan"), config=None)
    assert gs is not None
    assert isinstance(gs.banner, str) and gs.banner  # banner renders
    # cash/nlv collapsed to 0 → gates evaluate as zero coverage / zero cash
    assert gs.open is False


def test_evaluate_gates_handles_nan_cash_with_real_nlv():
    """Partial NaN: real NLV but NaN cash. Should not crash."""
    from analysis.capacity_gates import evaluate_gates
    gs = evaluate_gates([], cash=float("nan"), nlv=1_000_000.0, config=None)
    assert gs is not None
    assert "cash 0.0%" in gs.banner   # NaN cash → 0% cash


def test_evaluate_gates_normal_path_unchanged():
    """Regression check: clean inputs still produce the legacy output."""
    from analysis.capacity_gates import evaluate_gates
    gs = evaluate_gates([], cash=200_000.0, nlv=1_000_000.0, config=None)
    assert gs is not None
    assert "cash 20.0%" in gs.banner
