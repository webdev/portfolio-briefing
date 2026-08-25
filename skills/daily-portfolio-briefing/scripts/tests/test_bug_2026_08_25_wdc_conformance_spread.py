"""Regression tests for the three defects observed in the REAL 2026-08-25
briefing (rule #43 — bug-fix-on-sight; docstrings quote the observed lines).

BUG A — the Rotation Playbook green-lit
    "| 3 | **WDC $380P Sep 18 '26** | Sell-to-Open | 1 | $8.15 GTD | +$815 |"
while the SAME briefing's Best Setups excluded WDC:
    "⏸ WDC B (67) — excluded: ⛔ same-theme put already held — MU $920P
     (Memory & Storage); stacking correlated assignment risk"
and the digest Money Plan advertised
    "- **Deploy today:** 1 entry → +$815 premium over ~24d (WDC — playbook)".
The playbook's Phase-2 gate battery predates rules #48/#49 and never ran
theme-stacking or day-color. Fix: the canonical evaluator
(analysis/entry_algorithm.evaluate_entry) is the FINAL green light on every
composed STO open.

BUG B — the '🧮 Entry Algorithm Conformance' panel appeared absent: the
audit ran with ZERO offenders (the success line rendered at the bottom)
because the WDC ticket exists ONLY as a table row, and table rows were
verifier-exempt by old convention. Fix: order-table `| Sell-to-Open |`
rows are audited; a failed audit renders a VISIBLE warning line instead of
nothing.

BUG C — the top candidate "HACK $98P … mid $0.97 (bid $0.10 / ask $1.85)"
carried a 180%-of-mid spread — untradeable. Fix: spread-quality demotion
(config max_candidate_spread_pct, default 40% of mid) mirroring the
yield-floor demotion pattern.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import entry_algorithm as ea  # noqa: E402
from analysis import setup_grade as sg  # noqa: E402
from analysis.rotation_playbook import compute_playbook  # noqa: E402
from render.money_plan import build_money_plan  # noqa: E402
from render.rotation_playbook_panel import render_rotation_playbook  # noqa: E402
from steps import candidate_research as cr  # noqa: E402
from steps.aggregate import aggregate_briefing  # noqa: E402

TODAY = date(2026, 8, 25)

# Theme-stacking gate ON — the briefing.yaml state the 2026-08-25 run had.
THEME_CFG = {"entry_algorithm": {"theme_stacking": {"enabled": True,
                                                    "mode": "wait"}}}


# ── Fixtures ──────────────────────────────────────────────────────────────


def _held_winner(ticker, strike, exp, entry, mid, dte):
    """Options-review-shaped held short put — a ≥30%-capture winner."""
    return {"underlying": ticker, "type": "PUT", "strike": strike,
            "expiration": exp, "qty": -1, "entry_price": entry,
            "current_mid": mid, "days_to_expiry": dte}


def _sweeper():
    """A close book freeing $67.5K — funds the test candidates."""
    return [
        _held_winner("GOOG", 325.0, "2026-09-18", 7.9447, 4.45, 24),
        _held_winner("MSFT", 350.0, "2026-10-16", 23.4244, 9.525, 52),
    ]


def _snapshot_with_held_mu_put():
    """Snapshot positions: the two Phase-1 winners PLUS the held MU $920P —
    the Memory & Storage put the observed Best Setups exclusion named."""
    return {
        "balance": {"accountValue": 1_000_000.0, "cash": 500_000.0},
        "positions": [
            {"assetType": "OPTION", "type": "PUT", "underlying": "GOOG",
             "strike": 325.0, "qty": -1},
            {"assetType": "OPTION", "type": "PUT", "underlying": "MSFT",
             "strike": 350.0, "qty": -1},
            {"assetType": "OPTION", "type": "PUT", "underlying": "MU",
             "strike": 920.0, "qty": -1},
        ],
        "technicals": {},
        "quotes": {},
        "earnings_calendar": {"WDC": "2027-06-30", "KO": "2027-06-30"},
    }


def _wdc_cand():
    """The observed ticket shape: WDC $380P Sep 18 '26, mid $8.15, 24 DTE."""
    return {"kind": "SCOUT_CSP", "ticker": "WDC", "strike": 380.0,
            "expiration": "2026-09-18", "dte": 24, "premium": 8.15,
            "rsi_14": 39.0}


def _ko_cand():
    """A clean candidate in NO mapped scout theme (unmapped → fail-open)."""
    return {"kind": "SCOUT_CSP", "ticker": "KO", "strike": 60.0,
            "expiration": "2026-09-18", "dte": 24, "premium": 1.0,
            "rsi_14": 45.0}


def _an():
    return {"nlv": 1_000_000.0,
            "snapshot_data": _snapshot_with_held_mu_put(),
            "earnings_calendar": {"WDC": "2027-06-30", "KO": "2027-06-30"}}


def _recs():
    return {"WDC": {"rating_tier": 3, "conviction": "High", "age_days": 5,
                    "recommendation": "BUY"},
            "KO": {"rating_tier": 3, "conviction": "High", "age_days": 5,
                   "recommendation": "BUY"}}


def _playbook(cands, config=None):
    return compute_playbook(_sweeper(), cands, _recs(), set(), _an(),
                            config if config is not None else THEME_CFG,
                            today=TODAY)


# ── BUG A — the playbook runs the canonical evaluator ─────────────────────


def test_playbook_wdc_theme_stacking_demoted_out_of_order_slots():
    """Observed 2026-08-25: the playbook order table green-lit "| 3 |
    **WDC $380P Sep 18 '26** | Sell-to-Open | 1 | $8.15 GTD | +$815 |"
    while Best Setups excluded WDC — "⏸ WDC B (67) — excluded: ⛔ same-theme
    put already held — MU $920P (Memory & Storage); stacking correlated
    assignment risk." With the held MU $920P in the snapshot, the WDC
    candidate must demote to the warnings footer with the evaluator's
    measured reason and never occupy an order slot."""
    pb = _playbook([_wdc_cand()])
    assert pb is not None
    assert [o.ticker for o in pb.opens] == []
    wdc_warns = [w for w in pb.warnings if "WDC" in w]
    assert wdc_warns, f"WDC exclusion missing from warnings: {pb.warnings}"
    joined = " ".join(wdc_warns)
    assert "entry algorithm WAIT" in joined
    assert "theme_stacking" in joined
    assert "same-theme put already held" in joined
    assert "MU $920P" in joined and "Memory & Storage" in joined
    # The rendered panel has no Sell-to-Open order slot for WDC.
    panel = "\n".join(render_rotation_playbook(pb))
    assert "Sell-to-Open" not in panel
    assert "no qualified re-deployment today" in panel
    assert "same-theme put already held" in panel   # rule #24 — visible


def test_playbook_clean_candidate_unaffected():
    """A candidate in no mapped scout theme (KO) passes the evaluator
    fail-open and keeps its order slot — the gate demotes only measured
    same-theme stacks."""
    pb = _playbook([_ko_cand()])
    assert pb is not None
    assert [o.ticker for o in pb.opens] == ["KO"]
    assert not any("entry algorithm" in w for w in pb.warnings)
    panel = "\n".join(render_rotation_playbook(pb))
    assert "Sell-to-Open" in panel and "KO $60P" in panel


def test_playbook_money_plan_deploy_line_empty_when_demoted():
    """Observed digest line: "- **Deploy today:** 1 entry → +$815 premium
    over ~24d (WDC — playbook)". A theme-stacking-demoted candidate never
    reaches playbook opens, so the Money Plan Deploy line must render
    "none actionable this cycle" — never the WDC premium."""
    pb = _playbook([_wdc_cand()])
    assert pb is not None and pb.opens == []
    lines, _ = build_money_plan(
        date_str="2026-08-25", action_list_lines=[], options_reviews=[],
        new_ideas=[], playbook=pb.to_dict(), analytics=_an(),
        snapshot_data=_snapshot_with_held_mu_put(), config=THEME_CFG)
    md = "\n".join(lines)
    deploy = [ln for ln in lines if "Deploy today" in ln]
    assert deploy, md
    assert "none actionable this cycle" in deploy[0]
    assert "WDC" not in deploy[0]
    assert "+$815" not in md


def test_playbook_evaluate_entry_is_the_deciding_call(monkeypatch):
    """The canonical evaluator — analysis/entry_algorithm.evaluate_entry —
    is the deciding call on every composed STO open (rule #48: 'a new
    surface MUST call evaluate_entry, never re-derive the steps'). A spy
    replaces it; its WAIT verdict alone demotes the candidate."""
    calls = []

    def spy(side, ticker, strike, expiration, premium_mid, *a, **kw):
        calls.append((side, str(ticker).upper()))
        return ea.EntryDecision(
            verdict=ea.VERDICT_WAIT, side="csp",
            ticker=str(ticker).upper(), strike=strike,
            expiration=None, premium_mid=premium_mid, grade=None,
            ordered_reasons=[{
                "step": 2, "name": "hard_blocks", "check": "theme_stacking",
                "status": "wait",
                "detail": ("⛔ same-theme put already held — MU $920P "
                           "(Memory & Storage); stacking correlated "
                           "assignment risk")}])

    monkeypatch.setattr("analysis.entry_algorithm.evaluate_entry", spy)
    pb = _playbook([_ko_cand()])          # KO is clean — only the spy WAITs
    assert ("csp", "KO") in calls, "evaluate_entry was not consulted"
    assert pb is not None and pb.opens == []
    assert any("entry algorithm WAIT" in w and "KO" in w
               for w in pb.warnings)


def test_playbook_evaluator_enter_keeps_candidate(monkeypatch):
    """An ENTER verdict from the evaluator keeps the slot untouched."""
    def spy(side, ticker, strike, expiration, premium_mid, *a, **kw):
        return ea.EntryDecision(
            verdict=ea.VERDICT_ENTER, side="csp",
            ticker=str(ticker).upper(), strike=strike, expiration=None,
            premium_mid=premium_mid, grade=None, ordered_reasons=[])

    monkeypatch.setattr("analysis.entry_algorithm.evaluate_entry", spy)
    pb = _playbook([_ko_cand()])
    assert pb is not None
    assert [o.ticker for o in pb.opens] == ["KO"]


# ── BUG B — conformance audit scans order-table rows; visible fail-open ──


# The EXACT observed row (2026-08-25 briefing, Rotation Playbook order
# table) — green-lit as rendered.
_WDC_TABLE_ROW = (
    "| 3 | **WDC $380P Sep 18 '26** | Sell-to-Open | 1 | $8.15 GTD | +$815 "
    "| $38,000 | 33% | 🏁 B · RSI 39✓ · Parkev BUY Low · 12d · pullback "
    "zone · drawdown 43% |")


def _table_md(row):
    return "\n".join([
        "## 🎯 Actionable Rotation Playbook",
        "",
        "| # | Order | Type | Qty | Limit | Premium | Collateral "
        "| Ann yield | Conviction |",
        "|---|---|---|---|---|---|---|---|---|",
        row,
        "",
    ])


def test_conformance_audits_playbook_order_table_rows():
    """Observed 2026-08-25: the WDC ticket rendered ONLY as the table row
    "| 3 | **WDC $380P Sep 18 '26** | Sell-to-Open | 1 | $8.15 GTD |
    +$815 |" — and table rows were verifier-exempt by old convention,
    which is exactly how this surface escaped the audit. The auditor must
    now flag the row: the evaluator WAITs it (same-theme MU $920P held)."""
    sd = _snapshot_with_held_mu_put()
    off = ea.audit_conformance(_table_md(_WDC_TABLE_ROW), sd, None,
                               THEME_CFG, action_close_idents=set(),
                               as_of=TODAY)
    assert len(off) == 1, off
    assert off[0]["ticker"] == "WDC"
    assert off[0]["verdict"] == "WAIT"
    assert off[0]["check"] == "theme_stacking"
    assert "MU $920P" in off[0]["reason"]


def test_conformance_demoted_table_row_not_flagged():
    """A table row that already carries a demotion marker (⏸) is not
    green-lit — never an offender (rule #24 presentation, not a slot)."""
    demoted = _WDC_TABLE_ROW.replace("🏁 B", "⏸ Deferred (capacity gated)")
    sd = _snapshot_with_held_mu_put()
    off = ea.audit_conformance(_table_md(demoted), sd, None, THEME_CFG,
                               action_close_idents=set(), as_of=TODAY)
    assert off == []


def test_conformance_clean_table_row_not_flagged():
    """A table ticket the evaluator marks ENTER stays unflagged — the
    table sweep flags only genuine rejections."""
    row = ("| 3 | **KO $60P Sep 18 '26** | Sell-to-Open | 1 | $1.00 GTD "
           "| +$100 | $6,000 | 25% | 🏁 B |")
    sd = _snapshot_with_held_mu_put()
    off = ea.audit_conformance(_table_md(row), sd, None, THEME_CFG,
                               action_close_idents=set(), as_of=TODAY)
    assert off == []


def test_conformance_2026_08_25_root_cause_pinned():
    """Root cause of the 'absent panel': the 2026-08-25 audit did NOT
    crash — it ran, found ZERO offenders, and rendered the zero-offender
    success line ("_✅ Entry Algorithm conformance: every green-lit
    new-open ticket passes the canonical six-step evaluator (rule
    #48)._" — present at line 2105 of the full briefing). It found zero
    because `audit_conformance` skipped every line starting with '|'
    while the WDC ticket existed ONLY in table form. Pin both halves:
    the block form was always flaggable; the table-only form now is."""
    sd = _snapshot_with_held_mu_put()
    block_line = ("SELL 1× WDC $380P exp 2026-09-18 (24 DTE) @ $8.15 — "
                  "green-lit")
    off_block = ea.audit_conformance(block_line, sd, None, THEME_CFG,
                                     action_close_idents=set(), as_of=TODAY)
    assert [o["ticker"] for o in off_block] == ["WDC"]
    # The table-ONLY form — the exact 2026-08-25 escape — is now caught.
    off_table = ea.audit_conformance(_table_md(_WDC_TABLE_ROW), sd, None,
                                     THEME_CFG, action_close_idents=set(),
                                     as_of=TODAY)
    assert [o["ticker"] for o in off_table] == ["WDC"]


# ── BUG B(b) — verifier fail-open must be VISIBLE ────────────────────────


def _min_aggregate(config=None):
    return dict(
        config=config or {"enabled_strategies": ["wheel"],
                          "accounts": ["E1"]},
        snapshot_data={"balance": {"accountValue": 100000, "cash": 5000},
                       "positions": [], "quotes": {}, "ytd_pnl": {},
                       "earnings_calendar": {}},
        regime_data={"regime": "NORMAL", "confidence": "HIGH",
                     "triggered_rules": []},
        equity_reviews=[], options_reviews=[], new_ideas=[],
        consistency_report={"note": "first run"},
        flagged_inconsistencies=[], directives_active=[],
        directives_expired=[],
        snapshot_dir=Path("/tmp/test_bug_b_snap"),
    )


def test_conformance_failure_renders_visible_warning(monkeypatch):
    """When the conformance audit errors, the briefing must say so —
    "the fail-open try/except … silenc[ed] exactly the auditor that
    would have flagged the WDC ticket." A silent skip reads identically
    to a clean pass; the warning line is the difference."""
    def boom(*a, **kw):
        raise RuntimeError("forced conformance failure")

    monkeypatch.setattr("analysis.entry_algorithm.audit_conformance", boom)
    md, _ = aggregate_briefing("2026-08-25", **_min_aggregate())
    assert ("⚠ Entry Algorithm conformance audit failed to run this cycle "
            "(RuntimeError) — green-lit tickets unaudited") in md
    # And the success line must NOT render on a failed audit.
    assert "✅ Entry Algorithm conformance" not in md


def test_conformance_success_line_still_renders_when_healthy():
    """The healthy path keeps the zero-offender success line — the visible
    fail-open changes ONLY the error branch."""
    md, _ = aggregate_briefing("2026-08-25", **_min_aggregate())
    assert "✅ Entry Algorithm conformance" in md
    assert "failed to run this cycle" not in md


def test_rsi_consistency_failure_renders_visible_warning(monkeypatch):
    """The RSI-consistency sweep shares the silent fail-open pattern —
    its failure must render a visible warning line too."""
    def boom(*a, **kw):
        raise ValueError("forced rsi sweep failure")

    monkeypatch.setattr("analysis.price_consistency.rsi_disagreements", boom)
    md, _ = aggregate_briefing("2026-08-25", **_min_aggregate())
    assert ("⚠ RSI consistency sweep failed to run this cycle "
            "(ValueError) — RSI disagreements unaudited") in md


def test_price_consistency_failure_renders_visible_warning(monkeypatch):
    """The price-consistency sweep shares the silent fail-open pattern —
    its failure must render a visible warning line too."""
    def boom(*a, **kw):
        raise KeyError("forced price sweep failure")

    monkeypatch.setattr("analysis.price_consistency.price_disagreements",
                        boom)
    md, _ = aggregate_briefing("2026-08-25", **_min_aggregate())
    assert ("⚠ Price consistency sweep failed to run this cycle "
            "(KeyError) — spot disagreements unaudited") in md


def test_grade_coverage_failure_renders_visible_warning(monkeypatch):
    """The Setup Grade coverage check shares the silent fail-open pattern —
    its failure must render a visible warning line too (only reachable
    when the grader is enabled)."""
    def boom(*a, **kw):
        raise TypeError("forced grade coverage failure")

    monkeypatch.setattr("analysis.setup_grade.audit_missing_grade", boom)
    cfg = {"enabled_strategies": ["wheel"], "accounts": ["E1"],
           "setup_grade": {"enabled": True}}
    md, _ = aggregate_briefing("2026-08-25", **_min_aggregate(config=cfg))
    assert ("⚠ Setup Grade coverage check failed to run this cycle "
            "(TypeError) — new-open grade coverage unaudited") in md


# ── BUG C — spread-quality demotion ──────────────────────────────────────


def test_spread_quality_failure_hack_shape():
    """Observed 2026-08-25 candidate: "SELL 1× HACK $98P exp Fri Sep 18
    '26 (24 DTE, monthly) · mid $0.97 (bid $0.10 / ask $1.85)" — a $1.75
    spread, 180% of mid. The helper returns the measured demotion."""
    ws = sg.spread_quality_failure(
        {"bid": 0.10, "ask": 1.85, "mid": 0.97}, {})
    assert ws is not None
    assert ws["reason"] == ("⏸ spread too wide — $1.75 (180% of mid); "
                            "premium is unfillable")
    assert ws["spread_usd"] == 1.75
    assert round(ws["spread_pct_of_mid"]) == 180


def test_spread_quality_clean_and_fail_open():
    """A tight spread passes; unmeasurable bid/ask fails OPEN (rule #19 —
    never a fabricated spread); the config ceiling is honored."""
    assert sg.spread_quality_failure(
        {"bid": 2.0, "ask": 2.2, "mid": 2.1}, {}) is None
    assert sg.spread_quality_failure({"ask": 1.85, "mid": 0.97}, {}) is None
    assert sg.spread_quality_failure({"bid": 0.10, "mid": 0.97}, {}) is None
    assert sg.spread_quality_failure(None, {}) is None
    # 10% spread fails a 5% ceiling, passes the 40% default.
    q = {"bid": 1.00, "ask": 1.11, "mid": 1.055}
    assert sg.spread_quality_failure(q, {}) is None
    assert sg.spread_quality_failure(
        q, {"max_candidate_spread_pct": 0.05}) is not None


def _spread_payload():
    return {
        "themes": {"cyber": {"name": "Cybersecurity", "group": "Adjacent",
                             "anchors": ["HACK", "AMD"], "etfs": []}},
        "results_by_theme": {"cyber": [
            # The observed HACK shape — qualifies, but the spread is 180%
            # of mid (yield floor passes: 15% ann).
            {"ticker": "HACK", "spot": 100.0, "rsi_14": 45, "iv_rank": 60,
             "sma_200": 95, "drawdown_pct": 8, "fivedayret_pct": -1.0,
             "verdict": "CSP ENTRY (fat premium)", "rationale": ["x"],
             "csp_entry": {"strike": 98, "mid": 0.97, "bid": 0.10,
                           "ask": 1.85, "expiration": "2026-09-18",
                           "dte": 24}},
            # Clean-spread control candidate.
            {"ticker": "AMD", "spot": 150.0, "rsi_14": 40, "iv_rank": 60,
             "sma_200": 140, "drawdown_pct": 12, "fivedayret_pct": -1.2,
             "verdict": "CSP ENTRY (fat premium)", "rationale": ["x"],
             "csp_entry": {"strike": 135, "mid": 2.1, "bid": 2.0,
                           "ask": 2.2, "expiration": "2026-09-18",
                           "dte": 24}},
        ]},
    }


def test_candidate_trades_demotes_hack_wide_spread():
    """The HACK-shape candidate demotes to the visible '⏸ Spread too wide'
    section with the measured numbers — badge forfeited, never a Today's
    Candidates slot. The clean-spread AMD candidate is unaffected."""
    md = cr.render_candidate_briefing(_spread_payload(), fv_by_ticker={},
                                      config={}, generated_at="T")
    assert "⏸ Spread too wide — premium unfillable at mid (1)" in md
    assert ("⏸ spread too wide — $1.75 (180% of mid); premium is "
            "unfillable") in md
    assert "(bid $0.10 / ask $1.85)" in md
    # HACK never occupies a candidate slot; AMD keeps its card.
    cand_section = md.split("Today's Candidates")[1].split("##")[0] \
        if "Today's Candidates" in md else ""
    assert "HACK" not in cand_section
    assert "`AMD`" in cand_section


def test_best_setups_excludes_wide_spread_ticket():
    """Best Setups: a wide-spread scout ticket forfeits its Top-N slot and
    lands in the visible exclusions with the measured reason (mirroring
    the yield-floor demotion pattern); the clean ticket keeps its slot."""
    results = _spread_payload()["results_by_theme"]["cyber"]
    best = sg.collect_best_setups(scout_results=results,
                                  snapshot_data={}, config={})
    csp_tickers = [e["ticker"] for e in best["csp"]]
    assert "HACK" not in csp_tickers
    assert "AMD" in csp_tickers
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert "HACK" in ex
    assert ("⏸ spread too wide — $1.75 (180% of mid); premium is "
            "unfillable") in ex["HACK"]["reason"]
