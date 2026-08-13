"""Projected per-name concentration gate (2026-08-13 SNDK gap).

George's 2026-08-13 candidates file rendered:

    "⏸ **Deferred (capacity gated)** · SELL 1× SNDK $1230P exp
    **Fri Sep 18 '26** (36 DTE, monthly) · mid $43.00 (bid $41.80 /
    ask $44.20) · _Live E*TRADE chain_"
    "**Setup Grade: B** (66/100)"

with NO size/concentration warning — yet one contract is $123,000
collateral = 11.1% of NLV on a Tier C name (8% obligation-inclusive tier
cap). The obligation-inclusive concentration flag (red_flags 4c, built
2026-08-10 for the MELI case) covers HELD positions; nothing checked the
PROJECTED concentration of a fresh-name candidate ticket. MELI itself
($1460P, 13.2% NLV) slipped through the same way.

Single source of truth: analysis.position_tiers.projected_name_concentration
(+ _components), rendered by size_warning_line and enforced late-stage by
pre_trade_validator Rule 16 NAME_CONCENTRATION_EXCEEDED.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import position_tiers as pt              # noqa: E402
from analysis import pre_trade_validator as ptv        # noqa: E402
from analysis.capacity_gates import GateState          # noqa: E402
from steps import candidate_research as cr             # noqa: E402

# The observed SNDK numbers: $1230 strike × 100 = $123,000 collateral;
# NLV $1,108,000 → 11.1% of NLV; Tier C cap 8%.
_NLV = 1_108_000


def _sndk_payload():
    """The SNDK card from candidates_2026-08-13.md, as scout-result shape."""
    return {
        "themes": {"memory": {"name": "Memory & Storage",
                              "anchors": ["SNDK"], "etfs": ["DRAM"]}},
        "results_by_theme": {"memory": [
            {"ticker": "SNDK", "spot": 1367.35, "rsi_14": 48, "iv_rank": 97,
             "sma_200": 884.0, "drawdown_pct": 41, "fivedayret_pct": -4.2,
             "verdict": "BUY (pullback)",
             "rationale": ["drawdown 41% + BUY rec"],
             "csp_entry": {"strike": 1230, "mid": 43.00, "bid": 41.80,
                           "ask": 44.20, "expiration": "2026-09-18",
                           "dte": 36}},
        ]},
    }


def _snap(nlv=_NLV, positions=None):
    return {"balance": {"accountValue": nlv}, "positions": positions or []}


def _closed_gate(nlv=_NLV):
    return GateState(open=False, coverage_ratio=0.20, cash_pct=0.10,
                     obligation_pct=0.50,
                     reasons=["stress coverage 0.20× below the 0.50× floor"],
                     banner="🔒 CAPACITY: entry gates closed",
                     positions=[], nlv=nlv)


# ── (a) fresh name over cap → warning line + demotion, composing with the
#        capacity tag ──────────────────────────────────────────────────────

def test_fresh_name_over_cap_gets_warning_and_demotion_composing_with_capacity_tag():
    """The observed card ('⏸ **Deferred (capacity gated)** · SELL 1× SNDK
    $1230P ... mid $43.00' with NO size warning) must now carry the measured
    size warning AND demote out of the green-lit candidate list, composing
    with the capacity tag (rule #24 — shown, never green-lit)."""
    md = cr.render_candidate_briefing(
        _sndk_payload(), fv_by_ticker={}, config={}, generated_at="T",
        gate_state=_closed_gate(), snapshot_data=_snap())
    # The measured warning with the exact SNDK numbers.
    assert ("⚠ Size: 1 contract = $123,000 collateral — 11.1% of NLV "
            "on one name (Tier C cap 8%)") in md
    # Demoted to the planning section, never a green-lit entry.
    assert "Size exceeds tier concentration cap" in md
    assert "**Entry (CSP):** SELL 1× SNDK" not in md
    # Composes with the capacity tag — both reasons on the ticket line.
    ticket = [ln for ln in md.splitlines() if "SELL 1× SNDK" in ln][0]
    assert "⏸ **Deferred (capacity gated)**" in ticket
    assert "⏸ **Over tier size cap**" in ticket


def test_validator_rule16_blocks_fresh_name_over_cap():
    """One SNDK $1230P contract = $123,000 = 11.1% of NLV on a fresh Tier C
    name (8% cap): pre_trade_validator Rule 16 must fire — the root-cause of
    the miss was that NO per-name concentration rule existed (only the
    date-bucket Rule 4 and the CC tier Rule 13)."""
    ctx = ptv.build_context_from_snapshot(
        _snap(), ticker="SNDK", strike=1230.0,
        expiration=date(2026, 9, 18), option_type="PUT",
        action="SELL_OPEN", quantity=1)
    findings = ptv.validate_proposed_trade(ctx, {})
    hits = [f for f in findings if f.rule_id == "NAME_CONCENTRATION_EXCEEDED"]
    assert len(hits) == 1
    assert hits[0].severity == ptv.SEV_BLOCK
    assert "11.1% of NLV" in hits[0].reason
    assert "Tier C cap 8%" in hits[0].reason


# ── (b) existing-position name accumulates equity + held obligations + new ─

def test_existing_position_accumulates_equity_and_held_obligations():
    """Projected exposure = existing equity MV + held short-put obligations
    + NEW strike×100×contracts — the same math as red_flags 4c, plus the
    fresh ticket."""
    positions = [
        {"assetType": "EQUITY", "symbol": "SNDK", "qty": 40,
         "marketValue": 50_000.0, "price": 1250.0},
        {"assetType": "OPTION", "underlying": "SNDK", "type": "PUT",
         "qty": -1, "strike": 1000.0, "expiration": "2026-10-16"},
        # Noise on another name — must NOT count.
        {"assetType": "OPTION", "underlying": "MU", "type": "PUT",
         "qty": -1, "strike": 900.0, "expiration": "2026-10-16"},
    ]
    res = pt.projected_name_concentration(
        "SNDK", 1230.0, 1, 1_000_000, positions, {})
    assert res is not None
    assert res.existing_dollars == 150_000.0          # 50k equity + 100k put
    assert res.new_dollars == 123_000.0
    assert abs(res.pct - 27.3) < 0.05                 # 273k / 1M
    assert res.over is True
    line = pt.size_warning_line(res)
    assert "incl. $150,000 existing SNDK exposure" in line


# ── (c) within-cap ticket unchanged ────────────────────────────────────────

def test_within_cap_ticket_stays_green_lit_unchanged():
    """A $123,000 ticket on a $10M NLV book (1.2%, under the 8% Tier C cap)
    renders the normal green-lit entry with no size warning."""
    res = pt.projected_name_concentration(
        "SNDK", 1230.0, 1, 10_000_000, [], {})
    assert res is not None and res.over is False
    assert pt.size_warning_line(res) is None
    md = cr.render_candidate_briefing(
        _sndk_payload(), fv_by_ticker={}, config={}, generated_at="T",
        snapshot_data=_snap(nlv=10_000_000))
    assert "**Entry (CSP):** SELL 1× SNDK" in md
    assert "⚠ Size:" not in md
    assert "Size exceeds tier concentration cap" not in md


# ── (d) 'max within cap: N contracts' path ─────────────────────────────────

def test_max_within_cap_scaling_path():
    """5× $200P on a $1M book projects $100,000 (10%) over the 8% cap, but
    4 contracts ($80,000) fit — the warning scales instead of blocking."""
    res = pt.projected_name_concentration_components(
        "XYZ", 200.0, 5, 1_000_000, 0.0, 0.0, {})
    assert res is not None and res.over is True
    assert res.max_contracts_within_cap == 4
    line = pt.size_warning_line(res)
    assert "5 contracts = $100,000 collateral" in line
    assert "max within cap: 4 contracts" in line
    # Validator: scalable over-cap is a WARN (size down), not a BLOCK.
    ctx = ptv.build_context_from_snapshot(
        _snap(nlv=1_000_000), ticker="XYZ", strike=200.0,
        expiration=date(2026, 9, 18), option_type="PUT",
        action="SELL_OPEN", quantity=5)
    findings = ptv.validate_proposed_trade(ctx, {})
    hits = [f for f in findings if f.rule_id == "NAME_CONCENTRATION_EXCEEDED"]
    assert len(hits) == 1
    assert hits[0].severity == ptv.SEV_WARN
    assert "max within cap: 4 contracts" in hits[0].reason


# ── (e) fail-open on missing NLV ───────────────────────────────────────────

def test_fail_open_on_missing_nlv():
    """No NLV → no warning, never a fabricated pct (rule #19): the pure
    function returns None, the validator stays silent, and the candidate
    card renders green-lit exactly as before."""
    assert pt.projected_name_concentration("SNDK", 1230.0, 1, 0, [], {}) is None
    assert pt.projected_name_concentration("SNDK", 1230.0, 1, None, [], {}) is None
    ctx = ptv.build_context_from_snapshot(
        {"balance": {}, "positions": []}, ticker="SNDK", strike=1230.0,
        expiration=date(2026, 9, 18), option_type="PUT",
        action="SELL_OPEN", quantity=1)
    findings = ptv.validate_proposed_trade(ctx, {})
    assert not [f for f in findings
                if f.rule_id == "NAME_CONCENTRATION_EXCEEDED"]
    md = cr.render_candidate_briefing(
        _sndk_payload(), fv_by_ticker={}, config={}, generated_at="T",
        snapshot_data={"balance": {}, "positions": []})
    assert "**Entry (CSP):** SELL 1× SNDK" in md
    assert "⚠ Size:" not in md


# ── (f) CC writes exempt ───────────────────────────────────────────────────

def test_covered_call_writes_are_exempt():
    """A NEW covered call is share-backed — it adds no put obligation, so
    Rule 16 never fires on CALL SELL_OPEN, even at an enormous notional."""
    ctx = ptv.PreTradeContext(
        ticker="SNDK", strike=1500.0, expiration=date(2026, 9, 18),
        option_type="CALL", action="SELL_OPEN", quantity=5,
        spot=1367.35, nlv=float(_NLV), held_shares=500)
    findings = ptv.validate_proposed_trade(ctx, {})
    assert not [f for f in findings
                if f.rule_id == "NAME_CONCENTRATION_EXCEEDED"]


# ── (g) no duplicate warning when validator + generator both cover a card ──

def test_no_duplicate_warning_on_one_card():
    """The over-cap SNDK card renders the measured size warning exactly
    once — the generator-side demotion carries it; the validator's
    NAME_CONCENTRATION_EXCEEDED finding is silenced on this surface so the
    same breach is never reported twice on one card."""
    md = cr.render_candidate_briefing(
        _sndk_payload(), fv_by_ticker={}, config={}, generated_at="T",
        gate_state=_closed_gate(), snapshot_data=_snap())
    assert md.count("11.1% of NLV on one name") == 1
    assert "NAME_CONCENTRATION_EXCEEDED" not in md
