"""Tests for analysis/csp_rotation.py (task #20) — the CSP rotation
recommender: close a lower-yield held CSP to fund a higher-yield new CSP,
coverage-neutral or better.

User's ask (2026-07-07): "I'd rather close a current CSP with less yields
and get into another one with better opportunities."
"""

from __future__ import annotations

from datetime import date

import pytest

from analysis.csp_rotation import (
    candidate_annualized_yield,
    compute_csp_rotations,
    remaining_annualized_yield,
)
from render.csp_rotation_panel import render_csp_rotations


TODAY = date(2026, 7, 7)


# ── Fixture builders ──────────────────────────────────────────────────────


def held(ticker="GOOG", strike=325.0, exp="2026-08-21", qty=-1,
         entry=7.95, mid=4.45, dte=45, **kw):
    """Options-review-shaped held short put (the real briefing JSON shape)."""
    row = {
        "underlying": ticker, "type": "PUT", "strike": strike,
        "expiration": exp, "qty": qty, "entry_price": entry,
        "current_mid": mid, "days_to_expiry": dte,
    }
    row.update(kw)
    return row


def cand(ticker="MU", strike=830.0, exp="2026-09-18", dte=73,
         premium=104.45, kind="LONG_DATED_CSP", **kw):
    """LT-opportunity-shaped open-side candidate (live E*TRADE premium)."""
    row = {
        "kind": kind, "ticker": ticker, "target_expiration": exp,
        "target_dte": dte, "live_mid": premium, "live_strike": strike,
        "concrete_trade": f"SELL 1× {ticker} ${strike:g}P exp {exp} ({dte} DTE)",
        "capacity_deferred": True,
    }
    row.update(kw)
    return row


def analytics(**kw):
    a = {"nlv": 1_000_000.0}
    a.update(kw)
    return a


def rotations(helds, cands, an=None, config=None):
    return compute_csp_rotations(helds, cands, an or analytics(), config or {},
                                 today=TODAY)


# ── Yield math ────────────────────────────────────────────────────────────


def test_remaining_yield_otm_known_input():
    """OTM put: mid $2.00, 73 days, $100 strike → 200*365/(73*10000) = 10% ann."""
    y, theta = remaining_annualized_yield(100.0, 1, 2.0, 73, spot=110.0)
    assert theta == pytest.approx(200.0)
    assert y == pytest.approx(10.0, abs=0.01)


def test_remaining_yield_itm_subtracts_intrinsic():
    """ITM put (spot 95, strike 100): mid $7 = $5 intrinsic + $2 extrinsic.
    Only the extrinsic is theta you earn → same 10% as the OTM case."""
    y, theta = remaining_annualized_yield(100.0, 1, 7.0, 73, spot=95.0)
    assert theta == pytest.approx(200.0)
    assert y == pytest.approx(10.0, abs=0.01)


def test_remaining_yield_near_expiry_floors_days_at_one():
    """0-DTE never divides by zero; yield stays finite."""
    y, _ = remaining_annualized_yield(100.0, 1, 0.5, 0, spot=110.0)
    assert y == pytest.approx(50.0 * 365 / 10_000 * 100, rel=0.01)


def test_candidate_yield_known_input():
    """MU $830P, mid $104.45, 73 DTE → 10445*365/(73*83000) ≈ 62.9% ann."""
    y = candidate_annualized_yield(104.45, 73, 830.0)
    assert y == pytest.approx(62.92, abs=0.1)


# ── Coverage constraint ───────────────────────────────────────────────────


def test_rotation_with_freed_over_required_is_surfaced():
    """One close freeing MORE than the candidate needs → surfaced."""
    # GOOG $325P at 44% capture: remaining ≈ 11% ann — a classic low-yield close.
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    # required = $30,000 < freed $32,500; yield 9*365/(30*300) ≈ 36.5% ann ≥ 2×11%.
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30)]
    rots = rotations(helds, cands)
    assert len(rots) == 1
    r = rots[0]
    assert r.freed_collateral == pytest.approx(32_500)
    assert r.required_collateral == pytest.approx(30_000)
    assert r.net_collateral_delta == pytest.approx(2_500)
    assert r.open_yield_ann > r.close_yield_ann


def test_rotation_with_freed_below_95pct_of_required_is_rejected():
    """A single $32.5K close cannot fund an $83K candidate (< 95%)."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [cand(ticker="MU", strike=830, premium=104.45, dte=73)]
    assert rotations(helds, cands) == []


def test_small_obligation_increase_within_allowance_is_allowed_with_warning():
    """freed = 96% of required (≥ 0.95 floor) → surfaced, but warns."""
    helds = [held(ticker="GOOG", strike=288, entry=10.0, mid=5.0, dte=45)]  # $28.8K
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30)]          # $30K
    rots = rotations(helds, cands)
    assert len(rots) == 1
    assert rots[0].net_collateral_delta < 0
    assert any("obligation increases" in w for w in rots[0].warnings)


# ── Open-side discipline ──────────────────────────────────────────────────


def test_rotation_into_broken_chart_rejected_lt_verdict_gate():
    """Rule #39: LT verdict `broken` + below 200-SMA → candidate invalid."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [cand(ticker="ZS", strike=300, premium=9.0, dte=30)]
    an = analytics(snapshot_data={
        "technicals": {"ZS": {"deep": {"long_term_verdict": "broken",
                                       "vs_sma200_pct": -28.0}}},
    })
    assert rotations(helds, cands, an) == []


def test_rotation_into_5pct_overlap_strike_rejected():
    """Rule #40: candidate AMZN $220P vs held AMZN $225P (2.2%) → invalid."""
    helds = [
        # The overlapping held put (low capture — NOT an eligible close).
        held(ticker="AMZN", strike=225, entry=6.25, mid=5.475, dte=45),
        # A clean funding close.
        held(ticker="GOOG", strike=350, entry=16.67, mid=10.975, dte=45),
    ]
    cands = [cand(ticker="AMZN", strike=220, premium=7.0, dte=45)]
    assert rotations(helds, cands) == []


def test_rotation_into_earnings_within_21d_rejected():
    """Earnings 13 days out → candidate invalid."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30)]
    an = analytics(earnings_calendar={"INTC": "2026-07-20"})   # 13d from TODAY
    assert rotations(helds, cands, an) == []
    # Same setup with earnings far out → surfaced.
    an2 = analytics(earnings_calendar={"INTC": "2026-10-20"})
    assert len(rotations(helds, cands, an2)) == 1


# ── Close-side discipline ─────────────────────────────────────────────────


def test_multi_close_two_small_closes_fund_one_big_open():
    """Two $32.5K + $35K closes combine to fund a $65K candidate."""
    helds = [
        held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45),   # 44% cap
        held(ticker="MSFT", strike=350, entry=23.43, mid=9.53, dte=73),  # 59% cap
    ]
    cands = [cand(ticker="MRVL", strike=650, premium=30.0, dte=45)]      # $65K
    rots = rotations(helds, cands)
    assert len(rots) == 1
    r = rots[0]
    assert len(r.close_positions) == 2
    assert r.freed_collateral == pytest.approx(67_500)
    assert r.net_collateral_delta >= 0


def test_directive_hold_excludes_position_from_close_side():
    """A standing fable-advisor-memory hold directive protects the close."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30)]
    an = analytics(directive_holds={"GOOG"})
    assert rotations(helds, cands, an) == []
    # Without the directive the same rotation surfaces.
    assert len(rotations(helds, cands)) == 1


def test_capture_below_30pct_not_used_for_closing():
    """A 12%-capture put is never proposed as a close (would churn / realize
    thin profit); with no other close available, no rotation."""
    helds = [held(ticker="AMZN", strike=305, entry=6.25, mid=5.475, dte=45)]  # 12%
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30)]
    assert rotations(helds, cands) == []


def test_losing_position_never_proposed_as_close():
    """Negative capture (underwater short put) is never a close candidate."""
    helds = [held(ticker="MU", strike=1000, entry=125.55, mid=199.25, dte=73)]
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30)]
    assert rotations(helds, cands) == []


# ── Scoring ───────────────────────────────────────────────────────────────


def test_bucket_decompression_bonus_fires_on_overweight_bucket():
    """Closes in a ≥20%-NLV single-expiration bucket earn the bonus."""
    helds = [
        held(ticker="GOOG", strike=325, exp="2026-08-21", entry=7.95,
             mid=4.45, dte=45),
        # Same bucket ballast (not an eligible close: 5% capture) pushes the
        # Aug 21 bucket to $67.5K = 22.5% of the $300K NLV.
        held(ticker="VRT", strike=350, exp="2026-08-21", entry=24.87,
             mid=23.6, dte=45),
    ]
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30)]
    small_nlv = analytics(nlv=300_000.0)
    rots = rotations(helds, cands, small_nlv)
    assert len(rots) == 1
    assert rots[0].freed_bucket_pct > 0
    # Same book on a huge NLV → bucket under 20%, no bonus, lower score.
    big_nlv = analytics(nlv=10_000_000.0)
    rots_big = rotations(helds, cands, big_nlv)
    assert rots_big[0].freed_bucket_pct == 0
    assert rots[0].score > rots_big[0].score


def test_bucket_bonus_not_credited_when_open_recompresses_same_bucket():
    """An open landing in the SAME overweight bucket undoes the decompression
    — the bonus must be net-of-open, floored at 0 (never claim a
    decompression the rotation itself reverses)."""
    helds = [
        held(ticker="GOOG", strike=325, exp="2026-08-21", entry=7.95,
             mid=4.45, dte=45),
        held(ticker="VRT", strike=350, exp="2026-08-21", entry=24.87,
             mid=23.6, dte=45),
    ]
    # Candidate expires INTO the overweight Aug 21 bucket, bigger than the
    # close it's funded by ($34K vs $32.5K — inside the 5% allowance).
    cands = [cand(ticker="INTC", strike=340, premium=10.0, dte=45,
                  exp="2026-08-21")]
    rots = rotations(helds, cands, analytics(nlv=300_000.0))
    assert len(rots) == 1
    assert rots[0].freed_bucket_pct == 0


def test_score_ranking_highest_yield_delta_times_collateral_first():
    """Bigger (yield_delta × required_collateral) ranks first."""
    helds = [
        held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45),
        held(ticker="MSFT", strike=350, entry=23.43, mid=9.53, dte=73),
    ]
    cands = [
        # Big: $32K collateral at ~68% ann.
        cand(ticker="MRVL", strike=320, premium=19.0, dte=53),
        # Small: $30K collateral at ~36% ann.
        cand(ticker="INTC", strike=300, premium=9.0, dte=30),
    ]
    rots = rotations(helds, cands)
    assert len(rots) == 2
    assert rots[0].open_position.ticker == "MRVL"
    assert rots[0].score > rots[1].score


def test_yield_delta_threshold_rejects_marginal_upgrade():
    """Candidate must yield ≥ 2× the held's remaining (config default)."""
    # Held GOOG $350P: extrinsic 10.975 × 365 / (45 × 350) ≈ 25.4% ann.
    helds = [held(ticker="GOOG", strike=350, entry=16.67, mid=10.975, dte=45)]
    # Candidate at ~36% ann — better, but < 2× 25.4%.
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30)]
    assert rotations(helds, cands, config={"csp_rotation": {"min_yield_delta": 2.0}}) == []
    # Loosen the threshold → surfaces.
    assert len(rotations(helds, cands,
                         config={"csp_rotation": {"min_yield_delta": 1.2}})) == 1


# ── Fail-open ─────────────────────────────────────────────────────────────


def test_fail_open_empty_candidates_returns_empty():
    assert compute_csp_rotations([held()], [], analytics(), {}, today=TODAY) == []
    assert compute_csp_rotations([], [cand()], analytics(), {}, today=TODAY) == []
    assert compute_csp_rotations([], [], None, None, today=TODAY) == []


def test_fail_open_garbage_inputs_never_raise():
    garbage_helds = [None, 42, {"underlying": "X"}, {"qty": "not-a-number"}]
    garbage_cands = [None, "str", {"kind": "LONG_DATED_CSP"},
                     {"ticker": "MU", "live_mid": "??"}]
    out = compute_csp_rotations(garbage_helds, garbage_cands,
                                {"nlv": object()}, {"csp_rotation": {"min_yield_delta": "x"}},
                                today=TODAY)
    assert out == []


# ── End-to-end on real 2026-07-07 shapes ──────────────────────────────────


def test_end_to_end_real_2026_07_07_book():
    """Feed the real held book (options_reviews shape) + the real MU $830P
    LT candidate. The MU swap must qualify via multi-close (GOOG × 2 +
    MSFT $350P free $102.5K ≥ $83K) with a big yield upgrade."""
    helds = [
        held(ticker="AMD", strike=420, exp="2026-12-18", entry=75.66, mid=50.125, dte=164),
        held(ticker="AMZN", strike=225, exp="2026-08-21", entry=6.25, mid=5.475, dte=45),
        held(ticker="AVGO", strike=330, exp="2026-07-31", entry=6.5, mid=5.05, dte=24),
        held(ticker="GOOG", strike=325, exp="2026-08-21", entry=7.95, mid=4.45, dte=45),
        held(ticker="GOOG", strike=350, exp="2026-08-21", entry=16.67, mid=10.975, dte=45),
        held(ticker="MSFT", strike=350, exp="2026-09-18", entry=23.43, mid=9.525, dte=73),
        held(ticker="MU", strike=1000, exp="2026-09-18", entry=125.55, mid=199.25, dte=73),
        held(ticker="NOW", strike=95, exp="2026-11-20", entry=14.21, mid=8.45, dte=136),
        held(ticker="VRT", strike=290, exp="2026-08-21", entry=24.87, mid=26.025, dte=45),
    ]
    cands = [cand(ticker="MU", strike=830, exp="2026-09-18", dte=73, premium=104.45)]
    rots = rotations(helds, cands, analytics(nlv=1_086_303.18))
    assert isinstance(rots, list)
    assert len(rots) == 1
    r = rots[0]
    assert r.open_position.ticker == "MU"
    assert r.freed_collateral >= r.required_collateral * 0.95
    assert r.open_yield_ann == pytest.approx(62.9, abs=0.2)
    assert r.yield_delta_pct > 30
    # MU $830P vs held MU $1000P = 17% apart — NOT an overlap, but the
    # stacking warning must be visible (position-aware, rule #17).
    assert any("stacks single-name risk" in w for w in r.warnings)
    # The underwater MU $1000P must never be among the closes.
    assert all(c.capture_pct >= 0.30 for c in r.close_positions)


# ── Near-miss surface (task #21) ──────────────────────────────────────────
# User complaint (2026-07-07): "the panel says no rotations available...
# I still want to know what better rotations I could have had. It's useless."
# Near-misses = rotations blocked by exactly ONE gate, additive display only.


def test_near_miss_coverage_floor_only_blocker():
    """Freed 93% of required (between the 80% near-bound and the 95% floor)
    → no qualified rotation, ONE near-miss labeled coverage_floor."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]  # $32.5K
    cands = [cand(ticker="INTC", strike=350, premium=11.0, dte=30)]          # $35K
    rep = rotations(helds, cands)
    assert rep == []                          # qualified unchanged: none
    assert len(rep.near_miss) == 1
    nm = rep.near_miss[0]
    assert nm.block_reason == "coverage_floor"
    assert "93% < 95% floor" in nm.block_detail
    assert "min_freed_ratio" in nm.unblock_path
    assert nm.freed_collateral == pytest.approx(32_500)
    assert nm.required_collateral == pytest.approx(35_000)
    assert nm.yield_delta_pct > 0


def test_near_miss_directive_hold_only_blocker():
    """A rotation blocked ONLY by a fable-advisor hold directive surfaces as
    a near-miss quoting the directive, with a revisit-the-directive path."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30)]
    note = "**GOOG_PUT_325_20260821 — hold for higher capture.**"
    an = analytics(directive_holds={"GOOG"},
                   directive_hold_notes={"GOOG": note})
    rep = rotations(helds, cands, an)
    assert rep == []                          # qualified unchanged: none
    assert len(rep.near_miss) == 1
    nm = rep.near_miss[0]
    assert nm.block_reason == "directive_hold"
    assert "fable_advisor_memory.md" in nm.block_detail
    assert note in nm.block_detail            # directive quoted verbatim
    assert "GOOG hold directive" in nm.unblock_path


def test_near_miss_not_surfaced_when_two_gates_block():
    """Directive hold AND coverage floor both block → NOT a near-miss
    (two overrides is not a 'revisit this one thing' surface)."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [cand(ticker="INTC", strike=350, premium=11.0, dte=30)]  # 93% coverage
    rep = rotations(helds, cands, analytics(directive_holds={"GOOG"}))
    assert rep == []
    assert rep.near_miss == []


def test_near_miss_ranked_by_score_highest_first():
    """Two coverage-blocked candidates rank by yield_delta × collateral."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [
        cand(ticker="INTC", strike=350, premium=11.0, dte=30),   # ~38% ann
        cand(ticker="MRVL", strike=345, premium=15.0, dte=30),   # ~53% ann
    ]
    rep = rotations(helds, cands)
    assert rep == []
    assert len(rep.near_miss) == 2
    assert rep.near_miss[0].open_position.ticker == "MRVL"
    assert rep.near_miss[0].score > rep.near_miss[1].score


def test_near_miss_far_below_coverage_floor_not_surfaced():
    """Freed at 39% of required is NOT 'near' — loosening the floor that far
    is not a sane override, so nothing surfaces (below the 80% near-bound)."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [cand(ticker="MU", strike=830, premium=104.45, dte=73)]
    rep = rotations(helds, cands)
    assert rep == []
    assert rep.near_miss == []


def test_near_miss_skips_candidates_with_a_qualified_rotation():
    """A candidate that produced a QUALIFIED rotation never also appears as
    a near-miss — its best outcome already surfaced."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30)]
    rep = rotations(helds, cands)
    assert len(rep) == 1                      # qualified, as in task #20
    assert rep.near_miss == []


def test_report_is_list_compatible():
    """CSPRotationReport IS the qualified list — every task-#20 caller
    (iteration, len, indexing, == [], isinstance list) keeps working."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30)]
    rep = rotations(helds, cands)
    assert isinstance(rep, list)
    assert rep.qualified == list(rep)
    assert rep[0].open_position.ticker == "INTC"


def test_parse_directive_hold_notes_extracts_first_line():
    from analysis.csp_rotation import parse_directive_hold_notes
    txt = (
        "# Fable Advisor Memory\n\n"
        "- **MSFT_PUT_350_20260918 — same treatment as AMD.** Hold for more "
        "than 33% capture; don't rank it as a top winner-close.\n"
        "## Recent reviews\n"
        "- **GOOG_PUT_325 — hold** (below the divider, must be ignored)\n"
    )
    notes = parse_directive_hold_notes(txt)
    assert "MSFT" in notes
    assert notes["MSFT"].startswith("**MSFT_PUT_350_20260918")
    assert "GOOG" not in notes                # auto-review section excluded
    assert parse_directive_hold_notes(None) == {}


# ── Renderer ──────────────────────────────────────────────────────────────


def test_renderer_empty_surfaces_explanatory_note():
    md = "\n".join(render_csp_rotations([]))
    assert "CSP Rotations" in md
    assert "No coverage-neutral CSP rotations available today" in md


def test_renderer_rotation_shows_real_numbers_and_dated_tickets():
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [cand(ticker="INTC", strike=300, premium=9.0, dte=30,
                  exp="2026-08-21")]
    rots = rotations(helds, cands)
    md = "\n".join(render_csp_rotations(rots))
    assert "Close GOOG $325P" in md
    assert "Open INTC $300P" in md
    # Rule #6: weekday + year on every expiration.
    assert "Fri Aug 21 '26" in md
    assert "$32,500" in md and "$30,000" in md
    assert "Yield delta:" in md


def test_renderer_near_miss_only_replaces_useless_empty_note():
    """When nothing qualifies but a near-miss exists, the panel shows the
    near-miss section — NOT the 'no rotations available' dead end."""
    helds = [held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45)]
    cands = [cand(ticker="INTC", strike=350, premium=11.0, dte=30,
                  exp="2026-08-21")]
    rep = rotations(helds, cands)
    md = "\n".join(render_csp_rotations(rep))
    assert "No coverage-neutral CSP rotations available today" not in md
    assert "Near-miss rotations (blocked by one gate)" in md
    assert "not automatic recommendations" in md
    assert "Blocked by:" in md and "Coverage floor" in md
    assert "To unlock:" in md and "min_freed_ratio" in md
    assert "Close GOOG $325P" in md and "Open INTC $350P" in md
    assert "Fri Aug 21 '26" in md             # rule #6 dates on near-misses too


def test_renderer_empty_both_still_shows_explanatory_note():
    """The rare true-empty state (no qualified AND no near-miss) keeps the
    explanatory note — never a blank section (rule #24)."""
    from analysis.csp_rotation import CSPRotationReport
    md = "\n".join(render_csp_rotations(CSPRotationReport()))
    assert "No coverage-neutral CSP rotations available today" in md
    assert "Near-miss" not in md


def test_renderer_qualified_section_unchanged_when_near_miss_present():
    """Qualified rotations render exactly as before; the near-miss section
    is appended AFTER them (additive display, stability constraint)."""
    helds = [
        held(ticker="GOOG", strike=325, entry=7.95, mid=4.45, dte=45),
        held(ticker="MSFT", strike=350, entry=23.43, mid=9.53, dte=73),
    ]
    cands = [
        cand(ticker="INTC", strike=300, premium=9.0, dte=30),    # qualifies
        # $72K required; best combo frees $67.5K = 93.75% — inside (80%, 95%).
        cand(ticker="MRVL", strike=720, premium=40.0, dte=45),
    ]
    rep = rotations(helds, cands)
    assert len(rep) == 1 and len(rep.near_miss) == 1
    md = "\n".join(render_csp_rotations(rep))
    assert "Open INTC $300P" in md            # qualified block intact
    assert md.index("Yield delta:") < md.index("Near-miss rotations")
