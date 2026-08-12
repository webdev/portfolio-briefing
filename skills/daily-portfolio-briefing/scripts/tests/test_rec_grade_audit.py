"""Recommendation Grade Audit tests (rule #33 — TDD, docstrings quote
the user).

George (2026-08-12): "Can you look at briefings, say, for the last five
different briefings, and see what the recommendations make sense? If I
were to listen to those recommendations, I would get into the A or B
category. ... some recommendations recommend selling CSPs where our
[RSI] is 50/50. That doesn't sound like a good idea."

Fixture-based: synthetic briefing JSON + full-markdown artifacts and a
snapshot archive. Pins the JSON extractor (every surface, placeholder
exclusion), the markdown Candidate-card fallback (incl. the '(37 DTE,
monthly)' variant and section scoping), retro-grade wiring into
analysis/setup_grade (spy), aggregate math (distribution / by-surface /
the RSI-50-50 and thin-vol pattern counts / A-B verdict), the ledger
cross-reference (taken vs off-list), and the ungradeable fail-closed
paths (long legs, missing snapshot, no strike).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import rec_audit  # noqa: E402


DATE = "2026-08-11"

BRIEFING_JSON = {
    "new_ideas": [
        # capacity placeholder — NOT a recommendation
        {"ticker": "CAPACITY", "source": "capacity_gates_blocked",
         "capacity_blocked": True},
        # a real PULLBACK CSP idea
        {"ticker": "AMZN", "strike": 240.0, "expiration": "2026-09-18",
         "rsi_14": 44.0, "capacity_blocked": True},
    ],
    "long_term_opportunities": [
        {"kind": "LONG_DATED_CSP", "ticker": "PLTR",
         "concrete_trade": "SELL 1× PLTR $155P exp Fri Oct 16 '26 (70 DTE)",
         "trigger_reasons": ["⏸ Deferred (capacity gated) — stress "
                             "coverage 0.22× < 0.50× floor",
                             "RSI 66 🟡 extended"],
         "rsi_wait": True},
        {"kind": "LEAP_CALL", "ticker": "CCL",
         "concrete_trade": "BUY 1× CCL $25C exp Fri Aug 20 '27 (379 DTE) "
                           "(ITM, delta ~0.70)"},
        {"kind": "ADD", "ticker": "AFRM"},          # equity — excluded
        {"kind": "SKIPPED_LT_VERDICT", "ticker": "ZS"},  # no ticket
    ],
    "strategy_upgrades": [
        {"type": "tier_a_no_cc", "underlying": "META"},  # excluded
        {"type": "write_covered_call", "underlying": "NFLX",
         "target_strike": 81.0, "target_dte": 37, "contracts_writable": 2,
         "rsi_14": 49.6, "rsi_wait": True, "setup_grade": "D"},
        {"type": "index_covered_call", "underlying": "SPY",
         "writable": True, "target_strike": 787.0, "target_dte": 37,
         "contracts_writable": 1, "rsi_14": 64.4, "setup_grade": "A-"},
        {"type": "index_covered_call", "underlying": "VOO",
         "writable": False},  # un-writable — excluded
        {"type": "covered_strangle", "underlying": "SMH",
         "rsi_14": 54.3, "rsi_blocked": False,
         "proposed": {"strike": 500.0, "expiration": "2026-11-20",
                      "qty": 1}},
        {"type": "collar", "underlying": "MSFT", "rsi_14": 71.5,
         "proposed_put": {"strike": 440.0, "expiration": "2026-09-11",
                          "qty": 2}},
    ],
    "rotation_playbook": {"opens": []},
}

# Candidate section with BOTH DTE variants; a thin-premium "SELL $245P"
# line AFTER the section must never be extracted.
BRIEFING_MD = """
# Daily briefing

## 💰 Income Opportunities
- nothing here

## 🎯 Candidate Trades — Across Themes

**🎯 CANDIDATE · `MU` · $859.00** ✅ RSI favourable
  - RSI 48 · well above 200-SMA (+60%) · RVrank 84
  - ⏸ **Deferred (capacity gated)** · SELL 1× MU $795P exp **Fri Sep 11
'26** (32 DTE) · mid $33.75 · _Live E*TRADE chain_

**🎯 CANDIDATE · `APP` · $325.51**
  - RSI 31 — OVERRIDE (drawdown 54% + BUY rec)
  - SELL 1× APP $280P exp **Fri Sep 18 '26** (37 DTE, monthly) · mid $8.15

## Other Section

- **`MCD`** — SELL $245P mid $0.35 → ⏸ premium too thin
""".replace("$795P exp **Fri Sep 11\n'26**", "$795P exp **Fri Sep 11 '26**")


def _write_snapshot(root: Path, date: str, technicals: dict,
                    quotes: dict | None = None,
                    earnings: dict | None = None) -> None:
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    (d / "positions.json").write_text("[]")
    (d / "technicals.json").write_text(json.dumps(technicals))
    (d / "quotes.json").write_text(json.dumps(quotes or {}))
    (d / "earnings.json").write_text(json.dumps(earnings or {}))


@pytest.fixture()
def archive(tmp_path):
    root = tmp_path / "briefing_snapshots"
    tech = {
        "MU": {"rsi_14": 48.0, "spot": 881.24, "sma_200": 538.26,
               "iv_rank": 84.0, "drawdown_pct": 27.4,
               "support_resistance": {"supports": [], "resistances": []}},
        "PLTR": {"rsi_14": 66.0, "spot": 165.0, "sma_200": 140.0,
                 "iv_rank": 100.0, "drawdown_pct": 12.0},
        "SPY": {"rsi_14": 64.4, "spot": 772.0, "sma_200": 700.0,
                "iv_rank": 30.0, "drawdown_pct": 1.0},
        "NFLX": {"rsi_14": 49.6, "spot": 84.0, "sma_200": 100.0,
                 "iv_rank": 50.0, "drawdown_pct": 35.0},
        "SMH": {"rsi_14": 54.3, "spot": 560.0, "sma_200": 480.0,
                "iv_rank": 20.0, "drawdown_pct": 8.0},
        "AMZN": {"rsi_14": 44.0, "spot": 250.0, "sma_200": 230.0,
                 "iv_rank": 35.0, "drawdown_pct": 9.0},
        "APP": {"rsi_14": 31.0, "spot": 325.5, "sma_200": 520.0,
                "iv_rank": 84.0, "drawdown_pct": 54.0},
    }
    quotes = {t: {"dayChangePct": -0.01} for t in tech}
    _write_snapshot(root, DATE, tech, quotes)
    return root


# ── Extraction: JSON path ────────────────────────────────────────────────


def test_json_extractor_covers_every_surface():
    recs = rec_audit.extract_json_recs(BRIEFING_JSON, DATE)
    by_surface = {r["surface"]: r for r in recs}
    assert set(by_surface) == {"income_opportunity", "lt_csp", "leap_call",
                               "cc_write", "index_cc", "strangle_put",
                               "collar_put"}
    # the capacity placeholder row is NOT a rec
    assert all(r["ticker"] != "CAPACITY" for r in recs)
    # tier_a_no_cc and un-writable index CC excluded
    assert all(r["ticker"] != "META" for r in recs)
    assert all(r["ticker"] != "VOO" for r in recs)
    # LT CSP parsed from concrete_trade with ISO expiration
    lt = by_surface["lt_csp"]
    assert (lt["ticker"], lt["strike"], lt["expiration"]) == \
        ("PLTR", 155.0, "2026-10-16")
    assert lt["status"] == "rsi_wait"  # rsi_wait outranks capacity
    # CC write carries its shown grade + wait-for-strength status
    cc = by_surface["cc_write"]
    assert cc["status"] == "rsi_wait" and cc["shown_grade"] == "D"
    # collar put is a LONG leg
    assert by_surface["collar_put"]["side"] == "long_put"
    # strangle put add is a short put
    assert by_surface["strangle_put"]["side"] == "csp"
    assert by_surface["income_opportunity"]["status"] == "deferred_capacity"


def test_lt_status_precedence_hard_skip_over_capacity():
    op = {"kind": "LONG_DATED_CSP", "ticker": "PLTR",
          "concrete_trade": "SELL 1× PLTR $140P exp Fri Oct 16 '26 "
                            "(71 DTE)",
          "trigger_reasons": ["⛔ equity-stacking hard-skip zone",
                              "⏸ Deferred (capacity gated)"]}
    recs = rec_audit.extract_json_recs(
        {"long_term_opportunities": [op]}, DATE)
    assert recs[0]["status"] == "hard_skip"


# ── Extraction: markdown fallback ────────────────────────────────────────


def test_markdown_candidates_extracted_with_both_dte_variants():
    recs = rec_audit.extract_candidate_recs(BRIEFING_MD, DATE)
    assert len(recs) == 2
    mu = next(r for r in recs if r["ticker"] == "MU")
    assert (mu["strike"], mu["expiration"]) == (795.0, "2026-09-11")
    assert mu["status"] == "deferred_capacity"
    assert mu["shown_rsi"] == 48.0
    app = next(r for r in recs if r["ticker"] == "APP")
    # "(37 DTE, monthly)" variant parses; no deferred tag → actionable
    assert (app["strike"], app["expiration"]) == (280.0, "2026-09-18")
    assert app["status"] == "actionable"
    # the thin-premium MCD line OUTSIDE the section is never extracted
    assert all(r["ticker"] != "MCD" for r in recs)


def test_extract_recs_dedupes_identical_contract_per_surface():
    md = BRIEFING_MD
    recs = rec_audit.extract_recs(DATE, BRIEFING_JSON, md)
    keys = [(r["surface"], r["ticker"], r["strike"]) for r in recs]
    assert len(keys) == len(set(keys))
    # both sources contribute
    assert any(r["surface"] == "candidate" for r in recs)
    assert any(r["surface"] == "lt_csp" for r in recs)


def test_extract_recs_json_only_and_md_only_paths():
    assert rec_audit.extract_recs(DATE, None, BRIEFING_MD)
    assert rec_audit.extract_recs(DATE, BRIEFING_JSON, None)
    assert rec_audit.extract_recs(DATE, None, None) == []


# ── Retro-grade wiring (spy) ─────────────────────────────────────────────


def test_retro_grade_passes_that_days_snapshot_conditions(
        archive, monkeypatch):
    """The grader must receive the REC DAY's own snapshot inputs — the
    same resolution as the entry audit's retro grading."""
    from analysis import setup_grade as sg
    seen = {}
    real = sg.csp_setup

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(rec_audit._sg, "csp_setup", spy)
    rec = rec_audit._rec(DATE, "candidate", "csp", "MU", "PUT", 795.0,
                         "2026-09-11", ticket="SELL 1× MU $795P",
                         status="deferred_capacity")
    rec_audit.grade_rec(rec, archive, None, None, None)
    assert seen["rsi"] == 48.0
    assert seen["strike"] == 795.0
    assert seen["spot"] == 881.24
    assert seen["iv_rank"] == 84.0          # RV fallback (no chain history)
    assert seen["iv_rank_source"] == "rv"
    assert rec["gradeable"] is True
    assert rec["grade"]["letter"] in ("A", "A-", "B", "C", "D", "—")


def test_cc_rec_routes_to_cc_setup(archive, monkeypatch):
    called = {}
    monkeypatch.setattr(
        rec_audit._sg, "cc_setup",
        lambda **kw: called.update(kw) or
        {"letter": "C", "score": 55.0, "drivers": []})
    rec = rec_audit._rec(DATE, "index_cc", "cc", "SPY", "CALL", 787.0,
                         None, ticket="SELL 1× SPY $787C",
                         status="actionable")
    rec_audit.grade_rec(rec, archive, None, None, None)
    assert called["strike"] == 787.0 and called["rsi"] == 64.4


# ── Ungradeable fail-closed ──────────────────────────────────────────────


def test_long_legs_missing_snapshot_and_no_strike_fail_closed(archive):
    """Rule #19: ungradeable recs are listed 'n/a' WITH the reason."""
    long_leg = rec_audit._rec(DATE, "collar_put", "long_put", "MSFT",
                              "PUT", 440.0, "2026-09-11",
                              ticket="BUY 2× MSFT $440P",
                              status="actionable")
    rec_audit.grade_rec(long_leg, archive, None)
    assert long_leg["gradeable"] is False
    assert "long leg" in long_leg["ungradeable_reason"]

    no_snap = rec_audit._rec("2026-01-02", "candidate", "csp", "MU",
                             "PUT", 795.0, "2026-09-11",
                             ticket="SELL 1× MU $795P", status="actionable")
    rec_audit.grade_rec(no_snap, archive, None)
    assert no_snap["gradeable"] is False
    assert "no snapshot" in no_snap["ungradeable_reason"]

    no_strike = rec_audit._rec(DATE, "leap_call", "csp", "MU", "PUT",
                               None, None, ticket="?", status="actionable")
    rec_audit.grade_rec(no_strike, archive, None)
    assert no_strike["gradeable"] is False
    assert "strike" in no_strike["ungradeable_reason"]


# ── Aggregate math ───────────────────────────────────────────────────────


def _graded(surface, side, letter, score, status="actionable",
            rsi=None, ivr=None, iv_src="rv", date=DATE, ticker="X"):
    return {"date": date, "surface": surface, "side": side,
            "ticker": ticker, "opt_type": "PUT" if side == "csp" else "CALL",
            "strike": 100.0, "expiration": "2026-12-18",
            "ticket": f"SELL 1× {ticker} $100P", "status": status,
            "shown_rsi": rsi, "shown_grade": None, "gradeable": True,
            "grade": {"letter": letter, "score": score, "drivers": []},
            "conditions": {"rsi": rsi, "iv_rank": ivr,
                           "iv_source": iv_src}}


def test_aggregate_distribution_surfaces_and_pattern_counts():
    recs = [
        _graded("candidate", "csp", "B", 66.0, rsi=41.0, ivr=80.0),
        _graded("candidate", "csp", "D", 30.0, rsi=52.0, ivr=15.0),
        _graded("lt_csp", "csp", "C", 55.0, status="rsi_wait",
                rsi=66.0, ivr=100.0),
        _graded("cc_write", "cc", "D", 12.8, rsi=49.6),
        {"date": DATE, "surface": "collar_put", "side": "long_put",
         "ticker": "MSFT", "opt_type": "PUT", "strike": 440.0,
         "expiration": None, "ticket": "BUY", "status": "actionable",
         "shown_rsi": None, "shown_grade": None, "gradeable": False,
         "grade": {"letter": "n/a", "score": None},
         "ungradeable_reason": "long leg"},
    ]
    agg = rec_audit.aggregate(recs)
    assert agg["total_recs"] == 5 and agg["graded"] == 4
    assert agg["distribution"] == {"B": 1, "C": 1, "D": 2}
    assert agg["by_surface"]["candidate"] == {"B": 1, "D": 1}
    # the 50/50 problem: RSI ≥ 48 CSP recs (52 and 66 qualify; 41 not)
    assert agg["csp_rsi_5050"] == 2
    # thin vol: IVr/RVr < 40 (only the 15)
    assert agg["csp_thin_vol"] == 1
    # A/B verdict math
    assert agg["ab_count"] == 1
    assert agg["ab_pct"] == 25.0
    # actionable-only restriction excludes the rsi_wait C
    assert agg["actionable_graded"] == 3
    assert agg["ab_actionable_count"] == 1


def test_aggregate_avg_score():
    recs = [_graded("candidate", "csp", "B", 70.0),
            _graded("candidate", "csp", "D", 30.0)]
    assert rec_audit.aggregate(recs)["avg_score"] == 50.0


# ── Ledger cross-reference ───────────────────────────────────────────────


def test_ledger_cross_reference_taken_vs_offlist():
    recs = [_graded("candidate", "csp", "B", 66.0, ticker="MU")]
    recs[0]["strike"] = 795.0
    recs[0]["expiration"] = "2026-09-11"
    ledger = {"entries": [
        {"contract": {"underlying": "MU", "type": "PUT", "strike": 795.0,
                      "expiration": "2026-09-11"},
         "label": "MU $795P", "entry_date": DATE,
         "grade": {"letter": "D", "score": 25.0}},
        {"contract": {"underlying": "AVGO", "type": "PUT", "strike": 340.0,
                      "expiration": "2027-01-15"},
         "label": "AVGO $340P", "entry_date": DATE,
         "grade": {"letter": "D", "score": 25.0}},
        {"contract": {"underlying": "OLD", "type": "PUT", "strike": 1.0,
                      "expiration": "2026-09-11"},
         "label": "OLD $1P", "entry_date": "2026-07-01",
         "grade": {"letter": "C", "score": 50.0}},  # outside window
    ]}
    xref = rec_audit.cross_reference(recs, ledger, [DATE])
    assert xref["ledger_entries_in_window"] == 2
    assert len(xref["taken"]) == 1
    assert xref["taken"][0]["entry"]["label"] == "MU $795P"
    assert [e["label"] for e in xref["offlist_entries"]] == ["AVGO $340P"]


def test_ledger_avg_score():
    avg, n = rec_audit.ledger_avg_score(
        {"entries": [{"grade": {"score": 30.0}},
                     {"grade": {"score": 50.0}}, {"grade": {}}]})
    assert (avg, n) == (40.0, 2)
    assert rec_audit.ledger_avg_score(None) == (None, 0)


# ── End-to-end + render ──────────────────────────────────────────────────


def test_run_audit_end_to_end_and_render(archive, tmp_path):
    briefings = tmp_path / "briefings"
    briefings.mkdir()
    (briefings / f"briefing_{DATE}.json").write_text(
        json.dumps(BRIEFING_JSON))
    (briefings / f"briefing_full_{DATE}.md").write_text(BRIEFING_MD)
    result = rec_audit.run_audit(briefings, archive, limit=5)
    assert result["dates"] == [DATE]
    assert result["aggregate"]["total_recs"] >= 8
    assert result["aggregate"]["graded"] >= 5
    md = rec_audit.render_markdown(result)
    assert "Recommendation Grade Audit" in md
    assert "50/50 problem" in md
    assert "Verdict" in md
    assert "n/a — long leg" in md  # rule #19 reason surfaces in the table


def test_render_caps_pattern_ticket_lists_and_has_recommendation():
    """George: "see how recommendations make sense and grade those
    recommendations" — the report must stay readable (example lists
    capped, counts complete) and end with the analysis-only
    RECOMMENDATION section + caveats."""
    recs = [_graded("candidate", "csp", "D", 30.0, rsi=55.0, ivr=20.0,
                    ticker=f"T{i}") for i in range(20)]
    result = {"dates": [DATE], "recs": recs,
              "aggregate": rec_audit.aggregate(recs),
              "ledger_xref": {"window": [DATE, DATE],
                              "ledger_entries_in_window": 0, "taken": [],
                              "offlist_entries": [],
                              "untaken_rec_count": 20},
              "ledger_avg_score": 37.0, "ledger_scored_entries": 20}
    md = rec_audit.render_markdown(result)
    # count line says 20; example bullets capped with an honest remainder
    assert "20 of 20" in md
    assert f"… and {20 - rec_audit.MAX_PATTERN_TICKETS} more" in md
    assert md.count("(RSI 55)") == rec_audit.MAX_PATTERN_TICKETS
    assert "## RECOMMENDATION (analysis only — not implemented)" in md
    assert "Counter-considerations" in md
    assert "## Caveats" in md


def test_run_audit_no_dates_errors(tmp_path):
    r = rec_audit.run_audit(tmp_path / "nope", tmp_path / "nada")
    assert "error" in r


def test_audit_dates_requires_both_artifact_and_snapshot(tmp_path):
    briefings = tmp_path / "b"
    briefings.mkdir()
    root = tmp_path / "s"
    _write_snapshot(root, "2026-08-10", {})
    _write_snapshot(root, "2026-08-11", {})
    (briefings / "briefing_2026-08-11.json").write_text("{}")
    # 08-10 has a snapshot but no artifact; 08-12 artifact but no snapshot
    (briefings / "briefing_2026-08-12.json").write_text("{}")
    assert rec_audit.audit_dates(briefings, root) == ["2026-08-11"]
