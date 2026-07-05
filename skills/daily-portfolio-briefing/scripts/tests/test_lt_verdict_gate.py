"""Regression tests for the LT-verdict discipline gate (CLAUDE.md hard rule
#39) + its wiring, pinned to the ACTUAL cases from the analyst audit
2026-07-03 (ZS ×2, SOFI, META, MU sub-lot, AMZN PULLBACK_CSP overlap).

The systemic gap: the pipeline computed ``long_term_verdict`` for every
ticker but never consumed it when generating new-open recs — RSI band +
Parkev BUY was enough to fire.
"""

import sys
import types
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import lt_verdict_gate  # noqa: E402
from analysis import put_overlap_check  # noqa: E402
from steps import long_term_opportunities as lto  # noqa: E402
from steps.strategy_upgrades import compute_strategy_upgrades  # noqa: E402
from render import panels  # noqa: E402
from render.panels import render_action_list  # noqa: E402
from render.strategy_upgrades_panel import render_strategy_upgrades  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Fixture builders — numbers straight from analyst_audit_2026-07-03.md
# ─────────────────────────────────────────────────────────────────────────────

def _tech(verdict: str, vs200: float, rsi: float, **deep_extra) -> dict:
    deep = {"long_term_verdict": verdict, "vs_sma200_pct": vs200}
    deep.update(deep_extra)
    return {"rsi_14": rsi, "deep": deep}


# ZS: LT `broken`, -28.1% below 200-SMA, drawdown -56.2%, RSI 59.4.
ZS_TECH = _tech("broken", -28.1, 59.4, ath_dd_pct=-56.2, sma_50_slope_pct=-1.0)
ZS_REC = {"ticker": "ZS", "recommendation": "BUY", "rating_tier": 3,
          "age_days": 37, "aging": True, "conviction": "Medium"}

# SOFI: LT `broken`, -18.3% below 200-SMA, DD -43.4%; Parkev BUY tier 3, 0d.
SOFI_TECH = _tech("broken", -18.3, 46.0, ath_dd_pct=-43.4)
SOFI_REC = {"ticker": "SOFI", "recommendation": "BUY", "rating_tier": 3,
            "age_days": 0, "conviction": "Medium"}

# META: LT `downtrend`, -9.7% below 200-SMA; Parkev tier 5, High, 2d.
META_TECH = _tech("downtrend", -9.7, 45.0, sma_50_slope_pct=-2.2)
META_REC = {"ticker": "META", "recommendation": "BUY",
            "raw_recommendation": "Top Stock to Buy", "rating_tier": 5,
            "age_days": 2, "conviction": "High"}


def _stub_lt_module(monkeypatch, ops: list[dict]):
    """Stub the long-term-opportunity-advisor so the step's GATES (the code
    under test) run against deterministic op dicts — no advisor heuristics."""
    class _StubOp:
        def __init__(self, d):
            self._d = d

        def to_dict(self):
            return dict(self._d)

    mod = types.SimpleNamespace(
        generate_long_term_opportunities=lambda **kw: [_StubOp(o) for o in ops]
    )
    monkeypatch.setattr(lto, "_load_lt_module", lambda: mod)


def _lto_snapshot(technicals: dict, positions: list | None = None) -> dict:
    return {
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": positions or [],
        "technicals": technicals,
        "iv_ranks": {},
        "quotes": {},
        "chains": {},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1+2. ZS — both the $135P LONG_DATED_CSP and the $5K ADD must demote
# ─────────────────────────────────────────────────────────────────────────────

def test_zs_lt_csp_blocked_by_lt_verdict_gate(monkeypatch):
    """Regression: audit 2026-07-03 flagged ZS $135P LT_CSP HIGH severity.
    LT verdict = 'broken', spot 28% below 200-SMA, drawdown -56%. Parkev
    BUY tier 3 (not tier 4) and 37d old (not fresh) — no override."""
    g = lt_verdict_gate.check_lt_verdict_gate("ZS", {"technicals": {"ZS": ZS_TECH}}, ZS_REC)
    assert g["pass"] is False
    assert g["override"] is False
    assert "broken" in g["reason"]
    assert "-28.1" in g["reason"]

    # Wiring: the LTO step demotes the op to SKIPPED_LT_VERDICT (never hides).
    _stub_lt_module(monkeypatch, [{
        "kind": "LONG_DATED_CSP", "ticker": "ZS",
        "concrete_trade": "SELL 1× ZS $135P ~75 DTE",
        "trigger_reasons": ["IV rank 51"], "rationale": "patient capital",
        "yield_or_cost": "",
    }])
    ops = lto.generate_long_term_opportunities_step(
        _lto_snapshot({"ZS": ZS_TECH}), [ZS_REC], {})
    zs = [o for o in ops if o.get("ticker") == "ZS"][0]
    assert zs["kind"] == "SKIPPED_LT_VERDICT"
    assert zs["kind_when_skipped"] == "LONG_DATED_CSP"
    assert "broken" in zs["skip_reason"]


def test_zs_add_blocked_by_lt_verdict_gate(monkeypatch):
    """Regression: audit flagged ZS ADD (BUY ~$5,000) HIGH — buying a name
    -56% off highs, -28% below a falling 200-SMA, tagged `broken`.
    RSI-favorable ≠ trend-favorable."""
    _stub_lt_module(monkeypatch, [{
        "kind": "ADD", "ticker": "ZS",
        "concrete_trade": "BUY ~$5,000 of ZS (~37 shares @ ~$135.00)",
        "trigger_reasons": [], "rationale": "starter position",
        "yield_or_cost": "",
    }])
    ops = lto.generate_long_term_opportunities_step(
        _lto_snapshot({"ZS": ZS_TECH}), [ZS_REC], {})
    zs = [o for o in ops if o.get("ticker") == "ZS"][0]
    assert zs["kind"] == "SKIPPED_LT_VERDICT"
    assert zs["kind_when_skipped"] == "ADD"
    # Hard rule #24: still rendered in the Skipped section, never hidden.
    md = "\n".join(lto.render_long_term_opportunities(ops))
    assert "ZS" in md
    assert "broken" in md


# ─────────────────────────────────────────────────────────────────────────────
# 3. MU — sub-lot completion deferred by the has-CSP extension
# ─────────────────────────────────────────────────────────────────────────────

def test_mu_sub_lot_blocked_by_has_csp_extension():
    """Regression: audit flagged MU 55-share sub-lot MEDIUM. User holds
    ITM MU $1000P — the CSP IS the entry mechanism, don't double-tap."""
    positions = [
        {"symbol": "MU", "assetType": "EQUITY", "qty": 45, "price": 975.56},
        {"symbol": "MU_PUT_20260918_1000", "assetType": "OPTION",
         "underlying": "MU", "type": "PUT", "qty": -1, "strike": 1000.0,
         "expiration": "2026-09-18"},
    ]
    snap = {"positions": positions, "balance": {"accountValue": 1_000_000},
            "chains": {}, "earnings_calendar": {}, "quotes": {},
            "technicals": {}}
    upgrades = compute_strategy_upgrades(snap, [], [], {})
    sublots = [u for u in upgrades if u.get("type") == "sublot_completion"
               and u.get("underlying") == "MU"]
    assert len(sublots) == 1
    sub = sublots[0]
    assert sub["discipline_deferred"] is True
    assert "$1000" in sub["discipline_reason"]
    assert "entry mechanism" in sub["discipline_reason"]

    # Rendered as deferred (shown with reason), NOT under the actionable header.
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "Sub-lot completions — deferred" in md
    assert "### Sub-lot Completions (" not in md  # no actionable sub-lot section


def test_sub_lot_without_csp_stays_actionable():
    """Regression guard against over-application: a plain sub-lot with NO
    held put and no broken chart still renders as actionable."""
    positions = [
        {"symbol": "VOO", "assetType": "EQUITY", "qty": 45, "price": 550.0},
    ]
    snap = {"positions": positions, "balance": {"accountValue": 1_000_000},
            "chains": {}, "earnings_calendar": {}, "quotes": {},
            "technicals": {}}
    upgrades = compute_strategy_upgrades(snap, [], [], {})
    sub = [u for u in upgrades if u.get("type") == "sublot_completion"][0]
    assert sub["discipline_deferred"] is False
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "### Sub-lot Completions (1)" in md


# ─────────────────────────────────────────────────────────────────────────────
# 4. AMZN — PULLBACK_CSP rejected by the universal 5% strike-overlap check
# ─────────────────────────────────────────────────────────────────────────────

def _amzn_snapshot() -> dict:
    return {
        "balance": {"accountValue": 1_000_000, "cash": 300_000},
        "quotes": {"AMZN": {"last": 226.0}},
        "technicals": {"AMZN": _tech("uptrend", 4.2, 45.0)},
        "positions": [
            {"symbol": "AMZN", "assetType": "EQUITY", "qty": 100, "price": 226.0},
            {"symbol": "AMZN_PUT_20260821_225", "assetType": "OPTION",
             "underlying": "AMZN", "type": "PUT", "qty": -1, "strike": 225.0,
             "expiration": "2026-08-21"},
        ],
        "earnings_calendar": {},
        "recommendations_list": [],
        "_config": {"core_positions": ["AMZN"], "accounts": []},
    }


def test_amzn_pullback_csp_blocked_by_overlap(monkeypatch):
    """Regression: audit flagged AMZN $215P — 4.4% away from held $225P,
    inside 5% overlap band."""
    monkeypatch.setattr(panels, "find_put_strike_near",
                        lambda *a, **kw: {"strike": 215.0, "mid": 3.50,
                                          "expiration": "2026-08-07"})
    equity_reviews = [{"ticker": "AMZN", "price": 226.0, "weight": 0.02,
                       "pl_pct": 0.1, "recommendation": "HOLD", "qty": 100}]
    md = "\n".join(render_action_list(
        equity_reviews, [], [], analytics=None,
        snapshot_data=_amzn_snapshot(), date_str=date.today().isoformat()))
    assert "**PULLBACK CSP** AMZN" not in md          # not actionable
    assert "PULLBACK CSP AMZN skipped" in md          # transparency footer
    assert "concentrates rather than diversifies" in md
    assert "4.4%" in md                                # measured, not boilerplate


# ─────────────────────────────────────────────────────────────────────────────
# 5. SOFI — fresh but tier 3 does NOT override; tier 4 fresh WOULD
# ─────────────────────────────────────────────────────────────────────────────

def test_sofi_add_blocked_by_lt_verdict_but_offset_by_fresh_parkev():
    """SOFI: LT broken, but Parkev BUY tier 3 Medium 0d (fresh but not
    tier 4). Gate should REJECT. If Parkev were tier 4, gate should PASS."""
    snap = {"technicals": {"SOFI": SOFI_TECH}}
    g = lt_verdict_gate.check_lt_verdict_gate("SOFI", snap, SOFI_REC)
    assert g["pass"] is False
    assert "tier 3" in g["reason"]

    tier4 = dict(SOFI_REC, rating_tier=4)
    g4 = lt_verdict_gate.check_lt_verdict_gate("SOFI", snap, tier4)
    assert g4["pass"] is True
    assert g4["override"] is True
    assert g4["warning"] and "broken" in g4["warning"]

    # ...but tier 4 + STALE (>14d) does NOT override.
    stale4 = dict(SOFI_REC, rating_tier=4, age_days=37)
    assert lt_verdict_gate.check_lt_verdict_gate("SOFI", snap, stale4)["pass"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. META — PULLBACK_CSP kept via tier-5 fresh override, with LT annotation
# ─────────────────────────────────────────────────────────────────────────────

def test_meta_pullback_csp_passes_with_tier5_fresh_override(monkeypatch):
    """META: LT downtrend, but Parkev tier 5 STRONG_BUY 2d old. Gate
    should PASS via the fresh-tier-≥4 override, but add an LT-warning
    annotation."""
    g = lt_verdict_gate.check_lt_verdict_gate(
        "META", {"technicals": {"META": META_TECH}}, META_REC)
    assert g["pass"] is True
    assert g["override"] is True
    assert "downtrend" in g["warning"]

    # Wiring: the PULLBACK CSP renders actionable WITH the LT-trend note.
    snap = {
        "balance": {"accountValue": 1_000_000, "cash": 300_000},
        "quotes": {"META": {"last": 585.0}},
        "technicals": {"META": META_TECH},
        "positions": [{"symbol": "META", "assetType": "EQUITY",
                       "qty": 100, "price": 585.0}],
        "earnings_calendar": {},
        "recommendations_list": [META_REC],
        "_config": {"core_positions": ["META"], "accounts": []},
    }
    monkeypatch.setattr(panels, "find_put_strike_near",
                        lambda *a, **kw: {"strike": 515.0, "mid": 8.00,
                                          "expiration": "2026-08-07"})
    monkeypatch.setattr(panels, "validate_csp", lambda **kw: None)
    equity_reviews = [{"ticker": "META", "price": 585.0, "weight": 0.06,
                       "pl_pct": 0.1, "recommendation": "HOLD", "qty": 100}]
    md = "\n".join(render_action_list(
        equity_reviews, [], [], analytics=None, snapshot_data=snap,
        date_str=date.today().isoformat()))
    assert "**PULLBACK CSP** META" in md      # kept — override honored
    assert "LT-trend note" in md              # ...but the contradiction shows
    assert "downtrend" in md


def test_pullback_csp_blocked_on_broken_chart_without_override(monkeypatch):
    """A ZS-style broken chart with a stale tier-3 rec never reaches the
    chain fetch — it lands in the transparency footer with the LT reason."""
    snap = {
        "balance": {"accountValue": 1_000_000, "cash": 300_000},
        "quotes": {"ZS": {"last": 135.0}},
        "technicals": {"ZS": ZS_TECH},
        "positions": [{"symbol": "ZS", "assetType": "EQUITY",
                       "qty": 100, "price": 135.0}],
        "earnings_calendar": {},
        "recommendations_list": [ZS_REC],
        "_config": {"core_positions": ["ZS"], "accounts": []},
    }

    def _boom(*a, **kw):  # chain must never be fetched for a blocked name
        raise AssertionError("chain fetched for LT-blocked ticker")

    monkeypatch.setattr(panels, "find_put_strike_near", _boom)
    equity_reviews = [{"ticker": "ZS", "price": 135.0, "weight": 0.01,
                       "pl_pct": 0.0, "recommendation": "HOLD", "qty": 100}]
    md = "\n".join(render_action_list(
        equity_reviews, [], [], analytics=None, snapshot_data=snap,
        date_str=date.today().isoformat()))
    assert "**PULLBACK CSP** ZS" not in md
    assert "PULLBACK CSP ZS blocked" in md
    assert "broken" in md


# ─────────────────────────────────────────────────────────────────────────────
# Fail-open + non-blocked verdicts (never block on missing data)
# ─────────────────────────────────────────────────────────────────────────────

def test_gate_fails_open_on_missing_deep_data():
    assert lt_verdict_gate.check_lt_verdict_gate(
        "XYZ", {"technicals": {}}, None)["pass"] is True
    assert lt_verdict_gate.check_lt_verdict_gate(
        "XYZ", {"technicals": {"XYZ": {"rsi_14": 40}}}, None)["pass"] is True
    # Blocked verdict but NO 200-SMA relationship measurable → fail-open.
    assert lt_verdict_gate.check_lt_verdict_gate(
        "XYZ", {"technicals": {"XYZ": {"deep": {"long_term_verdict": "broken"}}}},
        None)["pass"] is True


def test_gate_passes_healthy_verdicts_and_above_200sma():
    # MU per the audit: LT secular-uptrend — untouched by the gate.
    snap = {"technicals": {"MU": _tech("secular-uptrend", 119.6, 48.5)}}
    assert lt_verdict_gate.check_lt_verdict_gate("MU", snap, None)["pass"] is True
    # weakening-but-above-200 (reclaimed) → pass.
    snap2 = {"technicals": {"ABC": _tech("weakening", 1.5, 50.0)}}
    assert lt_verdict_gate.check_lt_verdict_gate("ABC", snap2, None)["pass"] is True


def test_cc_secular_uptrend_wait():
    """Belt-and-suspenders: a NEW covered call on a measured LT
    secular-uptrend chart is demoted to the wait list, not READY TO WRITE."""
    snap_tech = {"MU": dict(_tech("secular-uptrend", 119.6, 62.0))}
    reason = lt_verdict_gate.cc_secular_uptrend_wait("MU", {"technicals": snap_tech})
    assert reason and "secular-uptrend" in reason
    assert lt_verdict_gate.cc_secular_uptrend_wait(
        "ZS", {"technicals": {"ZS": ZS_TECH}}) is None

    snap = {"positions": [{"symbol": "MU", "assetType": "EQUITY",
                           "qty": 100, "price": 975.0}],
            "balance": {"accountValue": 5_000_000},
            "chains": {}, "earnings_calendar": {}, "quotes": {},
            "technicals": snap_tech}
    upgrades = compute_strategy_upgrades(snap, [], [], {})
    ccs = [u for u in upgrades if u.get("type") == "write_covered_call"]
    assert len(ccs) == 1
    assert ccs[0]["lt_secular_wait"] is True
    md = "\n".join(render_strategy_upgrades(upgrades))
    assert "READY TO WRITE" not in md
    assert "⏸ WAIT FOR STRENGTH" in md
    assert "secular-uptrend" in md
