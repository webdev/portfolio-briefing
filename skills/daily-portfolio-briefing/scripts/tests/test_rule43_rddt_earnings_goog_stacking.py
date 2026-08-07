"""Rule #43 regression tests — two defects from the 2026-07-31 briefing's
Rotation Playbook / Long-Term Opportunities sections.

Defect 1 (RDDT): Playbook Phase 2 selected "RDDT $130P Sep 04 '26" (42% ann
yield, IV rank 90, drawdown 44%) with NO earnings annotation anywhere on the
card. RDDT is an ad-tech name that historically prints early August — inside
the Sep 04 expiry. The earnings-calendar lookup returned nothing and every
downstream check (EARNINGS_WINDOW validator rule, playbook filter, LT_CSP
gate) silently passed. Fix: earnings checks fail CLOSED for new opens — a
WARN `EARNINGS_DATE_UNKNOWN` + a −2 playbook conviction penalty + a loud
"⚠ earnings unverified" flag. Demotion, never exclusion (rule #24).

Defect 2 (GOOG): the LTO section rendered "💎 6. LONG DATED CSP · GOOG —
SELL 1× GOOG $330P" with "✅ RSI favourable" and NO mention of equity
concentration, while the playbook two sections later hard-skipped the
identical trade: "⛔ GOOG $330P skipped — holds 15.9% NLV in GOOG equity
(≥ 10% NLV hard-skip)". Same document, same trade — recommend + refuse.
Fix: the equity-stacking check is a shared module
(analysis/equity_stacking.py) and the LTO cards render the matching
annotation.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import pre_trade_validator as ptv  # noqa: E402
from analysis.equity_stacking import (  # noqa: E402
    equity_pct_by_ticker,
    stacking_card_annotation,
)
from analysis.rotation_playbook import compute_playbook  # noqa: E402
from render.rotation_playbook_panel import render_rotation_playbook  # noqa: E402
from steps.long_term_opportunities import (  # noqa: E402
    _annotate_equity_stacking,
    render_long_term_opportunities,
)

TODAY = date(2026, 7, 31)


# ── Fixture builders ──────────────────────────────────────────────────────


def _ctx(**kw) -> ptv.PreTradeContext:
    """Healthy new-CSP context; tests override what they exercise."""
    defaults = dict(
        ticker="RDDT",
        strike=130.0,
        expiration=date(2026, 9, 4),
        option_type="PUT",
        action="SELL_OPEN",
        quantity=1,
        spot=170.0,
        rsi=48.0,
        iv_rank=90.0,
        earnings_date=None,
        nlv=1_000_000.0,
        cash=200_000.0,
        stress_coverage=0.60,
    )
    defaults.update(kw)
    return ptv.PreTradeContext(**defaults)


def _held(ticker="GOOG", strike=325.0, exp="2026-08-21", qty=-1,
          entry=7.9447, mid=4.45, dte=21):
    return {"underlying": ticker, "type": "PUT", "strike": strike,
            "expiration": exp, "qty": qty, "entry_price": entry,
            "current_mid": mid, "days_to_expiry": dte}


def _sweeper():
    """Frees $67.5K — enough to fund both test candidates."""
    return [
        _held(),
        _held(ticker="MSFT", strike=350, exp="2026-09-18",
              entry=23.4244, mid=9.525, dte=49),
    ]


def _cand(ticker, strike, exp, dte, premium):
    return {"kind": "SCOUT_CSP", "ticker": ticker, "strike": strike,
            "expiration": exp, "dte": dte, "premium": premium}


def _rec(tier=3, conviction="High", age=5):
    return {"rating_tier": tier, "conviction": conviction,
            "age_days": age, "recommendation": "BUY"}


def _goog_lto_op(**kw):
    """The observed GOOG card shape: ✅ RSI favourable, no concentration
    context."""
    op = {
        "kind": "LONG_DATED_CSP",
        "ticker": "GOOG",
        "concrete_trade": "SELL 1× GOOG $330P exp Fri Oct 16 '26 (77 DTE)",
        "trigger_reasons": ["✅ RSI favourable · RSI 44 (pullback band)"],
        "rationale": "77-DTE horizon below a tested support shelf",
        "yield_or_cost": "premium ~$610 on $33,000 collateral",
    }
    op.update(kw)
    return op


def _snapshot_with_equity(ticker, pct_of_nlv, nlv=1_000_000.0):
    """Snapshot holding ``pct_of_nlv`` (fraction) of NLV in ``ticker``
    equity."""
    return {
        "balance": {"accountValue": nlv, "cash": 300_000.0},
        "positions": [{"assetType": "EQUITY", "symbol": ticker,
                       "price": 100.0, "qty": pct_of_nlv * nlv / 100.0}],
    }


# ── Defect 1 — validator fails CLOSED on unknown earnings ─────────────────


def test_earnings_unknown_warns_not_passes():
    """Observed: 'RDDT $130P Sep 04 '26 … IV rank 90 · drawdown 44%' — no
    earnings line at all, because the calendar lookup returned nothing and
    the EARNINGS_WINDOW rule silently passed. An unknown earnings date on a
    single-stock new open must now emit the EARNINGS_DATE_UNKNOWN WARN with
    the verify-at-the-broker instruction."""
    findings = ptv.validate_proposed_trade(_ctx(earnings_date=None))
    unknown = [f for f in findings if f.rule_id == "EARNINGS_DATE_UNKNOWN"]
    assert unknown, "missing earnings date must WARN, not silently pass"
    f = unknown[0]
    assert f.severity == ptv.SEV_WARN          # demotion, not exclusion
    assert "earnings date unavailable" in f.reason
    assert "RDDT" in f.reason and "2026-09-04" in f.reason
    assert "before placing" in f.reason
    # It is a WARN — never a blocker on its own.
    assert not ptv.has_blockers(findings)


def test_earnings_known_clean_no_warn():
    """A measured earnings date AFTER expiry is genuinely clean — no
    EARNINGS_DATE_UNKNOWN, no EARNINGS_WINDOW."""
    findings = ptv.validate_proposed_trade(
        _ctx(earnings_date=date(2026, 10, 29)))
    rules = [f.rule_id for f in findings]
    assert "EARNINGS_DATE_UNKNOWN" not in rules
    assert "EARNINGS_WINDOW" not in rules


def test_earnings_known_inside_window_still_blocks():
    """The fail-closed WARN must not weaken the existing hard rule: a
    MEASURED print inside the window still BLOCKs (and the unknown WARN
    stays silent — the date is known).

    Calendar-safe (fixed 2026-08-07): the original used the file-level
    frozen ``TODAY`` (2026-07-31) + 6d, but the validator measures the
    window against the REAL ``date.today()`` — from Aug 7 onward the frozen
    print date sat in the past and the rule correctly stopped firing,
    failing this test by date-rot. Anchor both dates to today instead."""
    findings = ptv.validate_proposed_trade(
        _ctx(earnings_date=date.today() + timedelta(days=6),
             expiration=date.today() + timedelta(days=30)))
    by_rule = {f.rule_id: f for f in findings}
    assert "EARNINGS_WINDOW" in by_rule
    assert by_rule["EARNINGS_WINDOW"].severity == ptv.SEV_BLOCK
    assert "EARNINGS_DATE_UNKNOWN" not in by_rule


def test_etf_exempt_from_earnings_unknown():
    """ETFs have no earnings print — SPY with no calendar entry must NOT
    carry the unknown-earnings WARN (data absence is structural, not a
    gap)."""
    findings = ptv.validate_proposed_trade(
        _ctx(ticker="SPY", strike=560.0, spot=620.0, earnings_date=None))
    assert "EARNINGS_DATE_UNKNOWN" not in [f.rule_id for f in findings]


# ── Defect 1 — playbook penalty + warning propagation ─────────────────────


def test_playbook_penalty_and_warning_propagate():
    """The RDDT-shaped candidate (no earnings date anywhere) takes the −2
    conviction penalty (22.5 → 20.5), drops behind the otherwise-identical
    earnings-clean CRM (22.5), carries the '⚠ earnings unverified' flag in
    its conviction column, and the playbook warnings block names it. It is
    NOT excluded — demotion + loud annotation, per rule #24."""
    cands = [
        _cand("RDDT", 130.0, "2026-09-04", 35, 3.40),   # no earnings entry
        _cand("CRM", 150.0, "2026-09-11", 42, 2.40),    # clean print later
    ]
    an = {"nlv": 1_000_000.0,
          "earnings_calendar": {"CRM": "2027-01-15"}}   # RDDT absent
    pb = compute_playbook(_sweeper(), cands,
                          {"RDDT": _rec(), "CRM": _rec()}, set(), an, {},
                          today=TODAY)
    assert pb is not None
    by_tk = {o.ticker: o for o in pb.opens}
    assert "RDDT" in by_tk, "unknown earnings must demote, never exclude"
    rddt, crm = by_tk["RDDT"], by_tk["CRM"]
    # −2 penalty (config earnings_unknown_penalty, default 2).
    assert crm.conviction_score == pytest.approx(22.5)
    assert rddt.conviction_score == pytest.approx(20.5)
    assert rddt.earnings_unknown is True
    assert crm.earnings_unknown is False
    # Drops in ranking behind the earnings-clean twin.
    assert [o.ticker for o in pb.opens] == ["CRM", "RDDT"]
    # Flag renders in the conviction column (setup_flags → panel cell).
    assert "⚠ earnings unverified" in rddt.setup_flags
    assert "⚠ earnings unverified" not in crm.setup_flags
    # Candidate-level warning names the expiry to verify against…
    assert any("earnings unverified" in w and "Sep 04 '26" in w
               for w in rddt.warnings)
    # …and propagates to the playbook warnings block.
    assert any("RDDT" in w and "earnings unverified" in w
               for w in pb.warnings)
    # Rendered panel: the RDDT row carries the flag; the warnings section
    # names it too.
    md = "\n".join(render_rotation_playbook(pb))
    rddt_row = next(ln for ln in md.splitlines()
                    if "RDDT $130P" in ln and "Sell-to-Open" in ln)
    assert "⚠ earnings unverified" in rddt_row
    assert "verify no RDDT print before Sep 04 '26" in md


def test_playbook_penalty_config_override_and_etf_exempt():
    """`earnings_unknown_penalty` is config-tunable, and an ETF candidate
    (no print to verify) is exempt from both penalty and flag."""
    cands = [_cand("RDDT", 130.0, "2026-09-04", 35, 3.40),
             _cand("SMH", 280.0, "2026-09-11", 42, 6.20)]
    an = {"nlv": 1_000_000.0}                           # calendar empty
    pb = compute_playbook(
        _sweeper(), cands, {"RDDT": _rec(), "SMH": _rec()}, set(), an,
        {"rotation_playbook": {"earnings_unknown_penalty": 5}}, today=TODAY)
    by_tk = {o.ticker: o for o in pb.opens}
    assert by_tk["RDDT"].conviction_score == pytest.approx(17.5)   # 22.5 − 5
    smh = by_tk["SMH"]
    assert smh.earnings_unknown is False
    assert smh.conviction_score == pytest.approx(22.5)
    assert "⚠ earnings unverified" not in smh.setup_flags


# ── Defect 2 — LTO card carries the equity-stacking annotation ────────────


def test_lto_card_carries_stacking_annotation_at_15pct():
    """Observed: '💎 6. LONG DATED CSP · GOOG — SELL 1× GOOG $330P' with
    ✅ RSI favourable and NO concentration context, while the playbook
    hard-skipped the same trade ('⛔ GOOG $330P skipped — holds 15.9% NLV in
    GOOG equity (≥ 10% NLV hard-skip)'). The card must now render the ⛔
    hard-skip-zone annotation — and stay visible (rule #24)."""
    op = _goog_lto_op()
    ops = [op]
    _annotate_equity_stacking(ops, _snapshot_with_equity("GOOG", 0.159), {})
    assert any("⛔ equity-stacking hard-skip zone" in str(t)
               for t in op["trigger_reasons"])
    note = next(t for t in op["trigger_reasons"] if "⛔" in str(t))
    assert "15.9% NLV in GOOG equity" in note
    assert "playbook will exclude this trade" in note
    md = "\n".join(render_long_term_opportunities(ops))
    assert "LONG DATED CSP · `GOOG`" in md              # card still visible
    assert "⛔ equity-stacking hard-skip zone" in md
    assert "15.9% NLV in GOOG equity" in md
    # The ✅-favorable framing never appears without the ⛔ context above it.
    assert md.index("⛔ equity-stacking hard-skip zone") \
        < md.index("✅ RSI favourable")


def test_lto_card_modest_band_5_to_10():
    """5-10% NLV held → the modest ⚠ warning (not the ⛔ zone)."""
    op = _goog_lto_op()
    _annotate_equity_stacking([op], _snapshot_with_equity("GOOG", 0.07), {})
    notes = [str(t) for t in op["trigger_reasons"]]
    assert any("⚠ equity concentration" in t and "7.0% NLV" in t
               for t in notes)
    assert not any("⛔" in t for t in notes)


def test_lto_card_clean_below_5pct():
    """<5% NLV held → no annotation at all (and missing data → none
    either, fail-open)."""
    op = _goog_lto_op()
    _annotate_equity_stacking([op], _snapshot_with_equity("GOOG", 0.03), {})
    assert not any("equity" in str(t).lower() for t in op["trigger_reasons"])
    # Fail-open: no positions data → untouched.
    op2 = _goog_lto_op()
    _annotate_equity_stacking([op2], {"balance": {}, "positions": []}, {})
    assert op2["trigger_reasons"] == ["✅ RSI favourable · RSI 44 "
                                      "(pullback band)"]


def test_shared_helper_matches_playbook_bands():
    """The card annotation and the playbook gate read the SAME config block
    (rotation_playbook.equity_stacking) — a custom hard_skip_pct moves both,
    so the surfaces can't drift apart again."""
    pcts = equity_pct_by_ticker(
        _snapshot_with_equity("GOOG", 0.159)["positions"], 1_000_000.0)
    assert pcts["GOOG"] == pytest.approx(0.159)
    # Default band: 15.9% ≥ 10% → ⛔ zone.
    assert "⛔" in stacking_card_annotation("GOOG", pcts["GOOG"], {})
    # Custom hard_skip 0.20 → 15.9% falls to the modest ⚠ band.
    loose = stacking_card_annotation("GOOG", pcts["GOOG"],
                                     {"hard_skip_pct": 0.20})
    assert loose is not None and "⚠ equity concentration" in loose
    # Unknown held % → fail-open, no annotation.
    assert stacking_card_annotation("GOOG", None, {}) is None
