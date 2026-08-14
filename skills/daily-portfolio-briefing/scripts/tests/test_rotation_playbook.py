"""Tests for analysis/rotation_playbook.py (task #22) — the Actionable
Rotation Playbook: close ALL freeable winner CSPs, redeploy the freed
collateral into conviction-ranked deferred candidates.

Origin: user 2026-07-07 — after a manual "Scenario A" walk-through (4 winner
closes freeing ~$86K → 4 conviction-ranked opens) the user asked for the
pattern in the daily briefing every day: "here's the whole composed trade if
you want to sweep the winners."
"""

from __future__ import annotations

from datetime import date

import pytest

from analysis.rotation_playbook import (
    compute_playbook,
    conviction_score,
    star_badge,
)
from render.rotation_playbook_panel import render_rotation_playbook


TODAY = date(2026, 7, 7)


# ── Fixture builders ──────────────────────────────────────────────────────


def held(ticker="GOOG", strike=325.0, exp="2026-08-21", qty=-1,
         entry=7.9447, mid=4.45, dte=45, **kw):
    """Options-review-shaped held short put (the real briefing JSON shape)."""
    row = {
        "underlying": ticker, "type": "PUT", "strike": strike,
        "expiration": exp, "qty": qty, "entry_price": entry,
        "current_mid": mid, "days_to_expiry": dte,
    }
    row.update(kw)
    return row


def cand(ticker="CRM", strike=150.0, exp="2026-08-14", dte=38,
         premium=2.40, kind="SCOUT_CSP", **kw):
    """Scout-CSP-shaped open-side candidate (live E*TRADE mid)."""
    row = {
        "kind": kind, "ticker": ticker, "expiration": exp,
        "dte": dte, "premium": premium, "strike": strike,
    }
    row.update(kw)
    return row


def rec(tier=3, conviction="High", age=5, recommendation="BUY"):
    return {"rating_tier": tier, "conviction": conviction,
            "age_days": age, "recommendation": recommendation}


def playbook(helds, cands, recs=None, holds=None, an=None, config=None):
    # Rule #43 (RDDT): candidates with NO measured earnings date now take the
    # earnings-unverified penalty (−2) + flag. These legacy fixtures predate
    # that rule — give every candidate a known print safely AFTER expiry so
    # the exact scores they pin stay meaningful. Tests exercising earnings
    # behavior pass their own earnings_calendar entries (setdefault keeps
    # them); the unknown-earnings path is pinned in
    # test_rule43_rddt_earnings_goog_stacking.py.
    an = dict(an) if an else {"nlv": 1_000_000.0}
    cal = dict(an.get("earnings_calendar") or {})
    for c in cands or []:
        if isinstance(c, dict) and c.get("ticker"):
            cal.setdefault(c["ticker"], "2027-06-30")
    an["earnings_calendar"] = cal
    return compute_playbook(helds, cands, recs or {}, holds or set(),
                            an, config or {}, today=TODAY)


# ── Conviction score formula ──────────────────────────────────────────────


def test_conviction_score_tier3_high_fresh_is_22_5():
    """tier 3 × High(3) × very-fresh(2.5, task #32) = 22.5 — the manual
    CRM case (was 18 under the old ≤14d → 2.0 step)."""
    assert conviction_score(3, "High", 5) == pytest.approx(22.5)


def test_conviction_score_tier4_medium_30d_is_8():
    """tier 4 × Medium(2) × 22-30d(1) = 8."""
    assert conviction_score(4, "Medium", 30) == pytest.approx(8.0)


def test_conviction_score_stale_and_unknown_conviction():
    """31-60d halves; no conviction label weighs 0.5; no rec = 0 base."""
    assert conviction_score(3, "High", 45) == pytest.approx(4.5)   # 3×3×0.5
    assert conviction_score(3, None, 5) == pytest.approx(3.75)     # 3×0.5×2.5
    assert conviction_score(0, None, None) == pytest.approx(0.0)
    assert conviction_score(None, None, None) == pytest.approx(0.0)


def test_conviction_score_bonuses():
    """+2 pullback zone, +2 RSI<35, +1 IV rank ≥85 — additive on the base."""
    base = conviction_score(3, "Medium", 5)                        # 15
    assert conviction_score(3, "Medium", 5, pullback_zone=True) == base + 2
    assert conviction_score(3, "Medium", 5, rsi=29.0) == base + 2
    assert conviction_score(3, "Medium", 5, iv_rank=91.0) == base + 1
    assert conviction_score(3, "Medium", 5, pullback_zone=True,
                            rsi=29.0, iv_rank=91.0) == base + 5


def test_conviction_score_penalties():
    """−3 LT broken, −3 stacking, −5 earnings-in-window."""
    base = conviction_score(4, "High", 5)                          # 30
    assert conviction_score(4, "High", 5, lt_broken=True) == base - 3
    assert conviction_score(4, "High", 5, stacks_with_held=True) == base - 3
    assert conviction_score(4, "High", 5, earnings_in_window=True) == base - 5


def test_star_badge_bands():
    assert star_badge(18) == "⭐⭐⭐"
    assert star_badge(12) == "⭐⭐"
    assert star_badge(6) == "⭐"
    assert star_badge(5.9) == ""


# ── Close side ────────────────────────────────────────────────────────────


def test_directive_held_closes_excluded():
    """A fable-advisor hold directive excludes the position from the sweep;
    with no other freeable winner the playbook doesn't compose at all."""
    helds = [held(ticker="MSFT", strike=350, entry=23.4244, mid=9.525, dte=73)]
    cands = [cand(ticker="CRM")]
    assert playbook(helds, cands, {"CRM": rec()}, holds={"MSFT"}) is None
    # Without the directive the same book composes.
    pb = playbook(helds, cands, {"CRM": rec()})
    assert pb is not None
    assert [c.ticker for c in pb.closes] == ["MSFT"]


def test_directive_exclusion_surfaces_in_warnings():
    """When other winners keep the playbook alive, the excluded directive
    hold is named in the portfolio warnings (transparency, rule #24)."""
    helds = [
        held(ticker="GOOG", strike=325, entry=7.9447, mid=4.45, dte=45),
        held(ticker="MSFT", strike=350, entry=23.4244, mid=9.525, dte=73),
    ]
    pb = playbook(helds, [cand(ticker="CRM")], {"CRM": rec()},
                  holds={"MSFT"})
    assert pb is not None
    assert all(c.ticker != "MSFT" for c in pb.closes)
    assert any("directive-held" in w and "MSFT" in w for w in pb.warnings)


def test_capture_below_30pct_never_swept():
    """A 22%-capture put is not a banked winner — never in the closes."""
    helds = [
        held(ticker="AVGO", strike=330, entry=6.4946, mid=5.05, dte=24),   # 22%
        held(ticker="GOOG", strike=325, entry=7.9447, mid=4.45, dte=45),   # 44%
    ]
    pb = playbook(helds, [cand(ticker="CRM")], {"CRM": rec()})
    assert pb is not None
    assert [c.ticker for c in pb.closes] == ["GOOG"]


def test_playbook_none_when_freed_below_floor():
    """total freed < min_freed_collateral_usd → None (not worth composing)."""
    helds = [held(ticker="PATH", strike=9, qty=-10, entry=1.1647,
                  mid=0.685, exp="2026-11-20", dte=136)]     # frees $9,000
    assert playbook(helds, [cand(ticker="CRM")], {"CRM": rec()}) is None
    # Lower the floor → composes.
    pb = playbook(helds, [cand(ticker="CRM")], {"CRM": rec()},
                  config={"rotation_playbook": {"min_freed_collateral_usd": 5000}})
    assert pb is not None
    assert pb.total_freed == pytest.approx(9_000)


def test_closes_ranked_by_freed_times_capture():
    """Biggest wins first: freed_collateral × capture_pct DESC."""
    helds = [
        held(ticker="NOW", strike=95, exp="2026-11-20", entry=14.2046,
             mid=8.45, dte=136),                              # 9.5K × 41%
        held(ticker="MSFT", strike=350, exp="2026-09-18", entry=23.4244,
             mid=9.525, dte=73),                              # 35K × 59%
        held(ticker="GOOG", strike=325, entry=7.9447, mid=4.45, dte=45),  # 32.5K × 44%
    ]
    pb = playbook(helds, [cand(ticker="CRM")], {"CRM": rec()})
    assert [c.ticker for c in pb.closes] == ["MSFT", "GOOG", "NOW"]


# ── Open-side discipline ──────────────────────────────────────────────────


def _sweeper():
    """A close book freeing $67.5K — enough to fund the test candidates."""
    return [
        held(ticker="GOOG", strike=325, entry=7.9447, mid=4.45, dte=45),
        held(ticker="MSFT", strike=350, exp="2026-09-18", entry=23.4244,
             mid=9.525, dte=73),
    ]


def test_broken_chart_candidate_excluded_rule_39():
    """LT verdict `broken` + below 200-SMA → hard-excluded from the opens
    (Medium conviction doesn't earn the override); the playbook still
    composes closes-only with the coverage note."""
    an = {"nlv": 1_000_000.0, "snapshot_data": {
        "technicals": {"ZS": {"deep": {"long_term_verdict": "broken",
                                       "vs_sma200_pct": -28.0}}}}}
    pb = playbook(_sweeper(), [cand(ticker="ZS", strike=135, premium=4.0)],
                  {"ZS": rec(3, "Medium", 5)}, an=an)
    assert pb is not None
    assert pb.opens == []
    assert any("no qualified re-deployment" in w for w in pb.warnings)


def test_broken_chart_fresh_high_conviction_override():
    """Playbook-specific rule #39 exception: a FRESH (≤14d) Parkev BUY·High
    overrides the LT gate — candidate admitted WITH the −3 penalty and a
    visible warning (the manual CRM/ORCL case). A stale High rec does NOT
    override; config can disable the exception entirely."""
    an = {"nlv": 1_000_000.0, "snapshot_data": {
        "technicals": {"CRM": {"deep": {"long_term_verdict": "broken",
                                        "vs_sma200_pct": -22.0}}}}}
    crm = cand(ticker="CRM", strike=150, premium=2.40, rsi_14=49.0)
    # Fresh High BUY → admitted with penalty: 3×3×2.5=22.5 +2 pullback −3 = 21.5.
    pb = playbook(_sweeper(), [crm], {"CRM": rec(3, "High", 5)}, an=an)
    assert [o.ticker for o in pb.opens] == ["CRM"]
    o = pb.opens[0]
    assert o.conviction_score == pytest.approx(21.5)
    assert any("rule #39 playbook exception" in w for w in o.warnings)
    # Stale High rec (45d) → no override.
    pb2 = playbook(_sweeper(), [crm], {"CRM": rec(3, "High", 45)}, an=an)
    assert pb2.opens == []
    # Config kill-switch → no override even when fresh.
    pb3 = playbook(_sweeper(), [crm], {"CRM": rec(3, "High", 5)}, an=an,
                   config={"rotation_playbook":
                           {"lt_override_fresh_high_conviction": False}})
    assert pb3.opens == []


def test_earnings_in_window_candidate_excluded():
    """Earnings inside the expiry window → hard-excluded (no override)."""
    an = {"nlv": 1_000_000.0,
          "earnings_calendar": {"CRM": "2026-07-20"}}         # 13d < 38 DTE
    pb = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec()}, an=an)
    assert pb.opens == []
    # Earnings after expiry → qualifies.
    an2 = {"nlv": 1_000_000.0,
           "earnings_calendar": {"CRM": "2026-09-02"}}        # 57d > 38 DTE
    pb2 = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec()}, an=an2)
    assert [o.ticker for o in pb2.opens] == ["CRM"]


def test_stacking_candidate_excluded_unless_override():
    """A candidate on a name with an existing held short put (different
    strike) is excluded by default; the config override re-admits it with
    the stacking penalty + warning."""
    helds = _sweeper() + [
        # Held MU put, LOW capture (never a close) — but it makes any MU
        # candidate a stack.
        held(ticker="MU", strike=1000, exp="2026-09-18", entry=125.5423,
             mid=199.25, dte=73),
    ]
    mu = cand(ticker="MU", strike=600, premium=30.0, exp="2026-09-18", dte=73)
    pb = playbook(helds, [mu], {"MU": rec(tier=4)})
    assert pb.opens == []
    pb2 = playbook(helds, [mu], {"MU": rec(tier=4)},
                   config={"rotation_playbook": {"allow_stacking_with_held": True}})
    assert [o.ticker for o in pb2.opens] == ["MU"]
    o = pb2.opens[0]
    assert o.stacks_with_held
    assert any("stacks with existing MU" in w for w in o.warnings)
    # Penalty applied: 4×3×2.5 = 30, −3 stacking = 27.
    assert o.conviction_score == pytest.approx(27.0)


def test_overlap_within_5pct_always_excluded_rule_40():
    """Strike within 5% of a held put is excluded EVEN with the stacking
    override — rule #40 is not overridable."""
    helds = _sweeper() + [
        held(ticker="AMZN", strike=225, exp="2026-08-21", entry=6.2446,
             mid=5.475, dte=45),
    ]
    amzn = cand(ticker="AMZN", strike=220, premium=5.0)       # 2.2% apart
    pb = playbook(helds, [amzn], {"AMZN": rec(tier=5)},
                  config={"rotation_playbook": {"allow_stacking_with_held": True}})
    assert pb.opens == []


def test_missing_mid_candidate_skipped_rule_19():
    """No measured mid → the candidate never enters the playbook (live-chain
    regression: rule #19, never estimate a premium)."""
    no_mid = {"kind": "SCOUT_CSP", "ticker": "CIFR", "strike": 20.0,
              "expiration": "2026-08-14", "dte": 38}          # no premium key
    pb = playbook(_sweeper(), [no_mid, cand(ticker="CRM")], {"CRM": rec()})
    assert [o.ticker for o in pb.opens] == ["CRM"]


def test_min_conviction_score_floor_skips_weak_candidates():
    """Score below min_conviction_score (default 6) → skipped: a no-rec
    name with only an IV bonus doesn't qualify at the default floor.

    Chase-guard/gate-4 alignment: a no-rec name now needs a VERIFIED
    in-band RSI (live quote passing the vintage check) plus a quiet tape
    (chase guard, rule #44) to reach the floor check at all — the fixture
    supplies both so the test still pins the FLOOR, not the other gates."""
    weak = cand(ticker="WULF", strike=20, premium=1.2, iv_rank=90.0, rsi_14=55.0)
    an = {"nlv": 1_000_000.0, "snapshot_data": {
        "technicals": {"WULF": {"spot": 20.0, "rsi_14": 55.0,
                                "recent_closes": [20.0] * 15}},
        "quotes": {"WULF": {"last": 20.05}}}}
    pb = playbook(_sweeper(), [weak], {}, an=an)
    assert pb.opens == []
    # Lowering the floor admits it.
    pb2 = playbook(_sweeper(), [weak], {}, an=an,
                   config={"rotation_playbook": {"min_conviction_score": 1}})
    assert [o.ticker for o in pb2.opens] == ["WULF"]


# ── Ranking + selection ───────────────────────────────────────────────────


def test_ranking_higher_conviction_score_first():
    """Opens are ordered by conviction score DESC, not yield."""
    cands = [
        # ORCL: tier 3 Med very-fresh = 15, but juicy yield.
        cand(ticker="ORCL", strike=130, premium=6.47),
        # CRM: tier 3 High very-fresh = 22.5, thinner yield.
        cand(ticker="CRM", strike=150, premium=2.40),
    ]
    recs = {"CRM": rec(3, "High", 5), "ORCL": rec(3, "Medium", 5)}
    pb = playbook(_sweeper(), cands, recs)
    assert [o.ticker for o in pb.opens] == ["CRM", "ORCL"]
    assert pb.opens[0].conviction_score == pytest.approx(22.5)
    assert pb.opens[1].conviction_score == pytest.approx(15.0)


def test_diversification_no_double_stack_without_25pct_benefit():
    """Second entry on the same underlying loses to a different-ticker
    alternative unless it beats it by 25%+."""
    cands = [
        cand(ticker="CRM", strike=150, premium=2.40),          # 18
        cand(ticker="CRM", strike=140, premium=1.80),          # 18 (same rec)
        cand(ticker="ORCL", strike=130, premium=6.47),         # 12
    ]
    recs = {"CRM": rec(3, "High", 5), "ORCL": rec(3, "Medium", 5)}
    pb = playbook(_sweeper(), cands, recs,
                  config={"rotation_playbook": {"max_opens": 2}})
    # 2nd CRM (18) does NOT beat ORCL (12) by 25%+? 18 ≥ 15 — it does.
    # Make the bar explicit with a stronger alternative instead:
    recs2 = {"CRM": rec(3, "Medium", 5), "ORCL": rec(3, "High", 5)}
    pb2 = playbook(_sweeper(), cands, recs2,
                   config={"rotation_playbook": {"max_opens": 3}})
    # ORCL 18 first; CRM 12; 2nd CRM (12) vs no remaining alt → diversify.
    assert [o.ticker for o in pb2.opens] == ["ORCL", "CRM"]
    assert pb is not None                                      # smoke on pb


def test_diversification_25pct_benefit_allows_second_entry():
    """A repeat that beats the best alternative by 25%+ IS allowed."""
    cands = [
        cand(ticker="CRM", strike=150, premium=2.40),          # 18
        cand(ticker="CRM", strike=140, premium=1.80),          # 18
        cand(ticker="WULF", strike=20, premium=1.2),           # weak alt
    ]
    recs = {"CRM": rec(3, "High", 5), "WULF": rec(3, "Low", 40)}  # WULF 1.5→skipped
    pb = playbook(_sweeper(), cands, recs,
                  config={"rotation_playbook": {"min_conviction_score": 1}})
    # WULF alt score 1.5; 2nd CRM 18 ≥ 1.25×1.5 → the stack is justified.
    tickers = [o.ticker for o in pb.opens]
    assert tickers.count("CRM") == 2


def test_greedy_never_deploys_more_than_freed():
    """deployed ≤ total_freed even when candidates would overflow."""
    helds = [held(ticker="GOOG", strike=325, entry=7.9447, mid=4.45, dte=45)]
    cands = [
        cand(ticker="CRM", strike=150, premium=2.40),          # $15K
        cand(ticker="MDB", strike=320, premium=13.43),         # $32K — too big after CRM
        cand(ticker="DOCU", strike=41, premium=0.71),          # $4.1K
    ]
    recs = {"CRM": rec(3, "High", 5), "MDB": rec(3, "High", 5),
            "DOCU": rec(3, "Medium", 11)}
    pb = playbook(helds, cands, recs)
    assert pb.total_collateral_deployed <= pb.total_freed
    assert "MDB" not in [o.ticker for o in pb.opens] or \
        pb.total_collateral_deployed <= pb.total_freed


def test_deploy_stops_at_target_pct():
    """Once deployed ≥ deploy_target_pct × freed, no further opens."""
    helds = [held(ticker="GOOG", strike=325, entry=7.9447, mid=4.45, dte=45)]
    cands = [
        cand(ticker="MDB", strike=320, premium=13.43),         # $32K ≥ 90% of 32.5K
        cand(ticker="DOCU", strike=41, premium=0.71),
    ]
    recs = {"MDB": rec(3, "High", 5), "DOCU": rec(3, "High", 5)}
    pb = playbook(helds, cands, recs)
    assert [o.ticker for o in pb.opens] == ["MDB"]
    assert pb.cash_cushion_kept == pytest.approx(500.0)


def test_max_opens_cap():
    helds = [held(ticker="MSFT", strike=350, exp="2026-09-18",
                  entry=23.4244, mid=9.525, dte=73)] * 1 + [
        held(ticker="GOOG", strike=325, entry=7.9447, mid=4.45, dte=45)]
    cands = [cand(ticker=t, strike=20, premium=0.8)
             for t in ("A", "B", "C", "D", "E")]
    recs = {t: rec(3, "High", 5) for t in ("A", "B", "C", "D", "E")}
    pb = playbook(helds, cands, recs,
                  config={"rotation_playbook": {"max_opens": 3}})
    assert len(pb.opens) == 3


# ── Portfolio-level outputs ───────────────────────────────────────────────


def test_bucket_concentration_warning_fires_over_40pct():
    """Two opens on one expiration > 40% of deployed → warning."""
    cands = [
        cand(ticker="CRM", strike=150, premium=2.40, exp="2026-08-14"),
        cand(ticker="ORCL", strike=130, premium=6.47, exp="2026-08-14"),
    ]
    recs = {"CRM": rec(3, "High", 5), "ORCL": rec(3, "High", 5)}
    pb = playbook(_sweeper(), cands, recs)
    assert len(pb.opens) == 2
    assert pb.bucket_concentrations.get("2026-08-14") == pytest.approx(28_000)
    assert any("bucket concentration" in w for w in pb.warnings)


def test_high_conviction_only_day_flagged():
    """Every selected open carrying a Parkev BUY → the note surfaces."""
    cands = [cand(ticker="CRM"), cand(ticker="ORCL", strike=130, premium=6.47)]
    recs = {"CRM": rec(3, "High", 5), "ORCL": rec(4, "Medium", 5)}
    pb = playbook(_sweeper(), cands, recs)
    assert any("high-conviction only day" in w for w in pb.warnings)


def test_rich_mid_verify_moneyness_warning():
    """mid ≥ 8% of strike → 'verify chain moneyness' warning (the CIEN
    $38.55-on-$400 case from the manual analysis)."""
    cands = [cand(ticker="CIEN", strike=400, premium=38.55)]
    pb = playbook(_sweeper(), cands, {"CIEN": rec(3, "High", 5)})
    assert any("verify chain moneyness" in w for w in pb.warnings)


def test_weighted_yield_and_premium_totals():
    cands = [cand(ticker="CRM", strike=150, premium=2.40, dte=38)]
    pb = playbook(_sweeper(), cands, {"CRM": rec(3, "High", 5)})
    o = pb.opens[0]
    assert o.premium == pytest.approx(240.0)
    assert pb.total_premium_collected == pytest.approx(240.0)
    assert pb.weighted_yield_pct == pytest.approx(o.annualized_yield_pct)


def test_coverage_estimate_uses_measured_obligations_only():
    """coverage_after is a first-order scale of the measured ratio by the
    obligation change; absent obligations data it falls back to before."""
    an = {"nlv": 1_000_000.0,
          "stress_coverage": {"coverage_ratio": 0.17,
                              "total_put_obligations": 1_000_000.0}}
    pb = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec()}, an=an)
    assert pb.coverage_before == pytest.approx(0.17)
    # freed 67.5K, deployed 15K → obligations shrink → coverage improves.
    assert pb.coverage_after > pb.coverage_before
    an2 = {"nlv": 1_000_000.0, "stress_coverage": {"coverage_ratio": 0.17}}
    pb2 = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec()}, an=an2)
    assert pb2.coverage_after == pb2.coverage_before == pytest.approx(0.17)


# ── Fail-open ─────────────────────────────────────────────────────────────


def test_fail_open_garbage_inputs_return_none_never_raise():
    garbage_helds = [None, 42, {"underlying": "X"}, {"qty": "not-a-number"}]
    garbage_cands = [None, "str", {"kind": "SCOUT_CSP"},
                     {"ticker": "MU", "premium": "??"}]
    out = compute_playbook(garbage_helds, garbage_cands, None, None,
                           {"nlv": object()},
                           {"rotation_playbook": {"max_opens": "x"}},
                           today=TODAY)
    assert out is None


def test_disabled_via_config_returns_none():
    pb = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec()},
                  config={"rotation_playbook": {"enabled": False}})
    assert pb is None


# ── End-to-end on the real 2026-07-07 book ────────────────────────────────


def test_end_to_end_real_2026_07_07_scenario_a():
    """The real held book + the real deferred candidates must reproduce the
    manual Scenario A: 4 closes (GOOG ×2, NOW, PATH — AMD/MSFT directive-
    held, everything else under 30% capture) freeing $86,000, banking
    ~$1,974; conviction-ranked opens led by CRM (Parkev BUY High 5d = 18)."""
    helds = [
        held(ticker="AMD", strike=420, exp="2026-12-18", entry=75.6533,
             mid=50.125, dte=164),
        held(ticker="AMZN", strike=225, exp="2026-08-21", entry=6.2446,
             mid=5.475, dte=45),
        held(ticker="AVGO", strike=330, exp="2026-07-31", entry=6.4946,
             mid=5.05, dte=24),
        held(ticker="GOOG", strike=325, exp="2026-08-21", entry=7.9447,
             mid=4.45, dte=45),
        held(ticker="GOOG", strike=350, exp="2026-08-21", entry=16.6645,
             mid=10.975, dte=45),
        held(ticker="MSFT", strike=350, exp="2026-09-18", entry=23.4244,
             mid=9.525, dte=73),
        held(ticker="MSFT", strike=380, exp="2027-03-19", entry=55.0736,
             mid=37.35, dte=255),
        held(ticker="MU", strike=1000, exp="2026-09-18", entry=125.5423,
             mid=199.25, dte=73),
        held(ticker="NOW", strike=95, exp="2026-11-20", entry=14.2046,
             mid=8.45, dte=136),
        held(ticker="PATH", strike=9, exp="2026-11-20", qty=-10,
             entry=1.1647, mid=0.685, dte=136),
        held(ticker="IREN", strike=50, exp="2026-09-18", entry=10.6946,
             mid=14.5, dte=73),
    ]
    # Real deferred Candidate Trades tickets (live E*TRADE mids, 07-07).
    cands = [
        cand(ticker="CRM", strike=150, premium=2.40, dte=38, exp="2026-08-14",
             rsi_14=49.0, verdict="BUY (pullback)"),
        cand(ticker="CRWV", strike=78, premium=8.15, dte=38, exp="2026-08-14",
             rsi_14=38.0, verdict="BUY (pullback)"),
        cand(ticker="IREN", strike=40, premium=6.05, dte=38, exp="2026-08-14",
             rsi_14=40.0),                     # stacks with held IREN $50P
        cand(ticker="MDB", strike=320, premium=13.43, dte=38, exp="2026-08-14",
             rsi_14=57.0, verdict="BUY (pullback)"),
        cand(ticker="DOCU", strike=41, premium=0.71, dte=38, exp="2026-08-14",
             rsi_14=50.0, verdict="BUY (pullback)"),
    ]
    recs = {
        "CRM": rec(3, "High", 5), "CRWV": rec(3, "Medium", 91),
        "IREN": rec(3, "Low", 236), "MDB": rec(3, "Medium", 105),
        "DOCU": rec(3, "Medium", 11),
    }
    pb = playbook(helds, cands, recs, holds={"AMD", "MSFT"},
                  an={"nlv": 1_086_303.18})
    assert pb is not None
    # Phase 1 — exactly the manual sweep.
    assert sorted(c.contract for c in pb.closes) == sorted([
        "GOOG_PUT_325_20260821", "GOOG_PUT_350_20260821",
        "NOW_PUT_95_20261120", "PATH_PUT_9_20261120"])
    assert pb.total_freed == pytest.approx(86_000)
    assert pb.total_realized_profit == pytest.approx(1_973.6, abs=1.0)
    # Phase 2 — CRM leads (18); IREN excluded (stacks with the held $50P).
    assert pb.opens, "expected qualified opens on the real candidate set"
    assert pb.opens[0].ticker == "CRM"
    assert pb.opens[0].conviction_score >= 18.0
    assert "IREN" not in [o.ticker for o in pb.opens]
    assert pb.total_collateral_deployed <= pb.total_freed


# ── Renderer ──────────────────────────────────────────────────────────────


def test_renderer_none_returns_no_section():
    assert render_rotation_playbook(None) == []


def test_renderer_full_playbook_structure():
    cands = [cand(ticker="CRM", strike=150, premium=2.40, exp="2026-08-14")]
    pb = playbook(_sweeper(), cands, {"CRM": rec(3, "High", 5)})
    md = "\n".join(render_rotation_playbook(pb))
    assert "🎯 Actionable Rotation Playbook" in md
    assert "PHASE 1 — Close 2 winner(s)" in md
    assert "PHASE 2 — Open 1 new CSP(s)" in md
    assert "Buy-to-Close" in md and "Sell-to-Open" in md
    # Real measured numbers (rule #19).
    assert "$67,500" in md                     # total freed
    assert "$15,000" in md                     # CRM collateral
    assert "$2.40 GTD" in md                   # CRM limit
    assert "⭐⭐⭐" in md                        # score 18 badge
    assert "Parkev BUY High · 5d" in md
    assert "Order sequence:" in md


def test_renderer_closes_only_mode():
    """No qualified opens → the section still renders the closes with the
    'close for coverage' note (never hidden, rule #24)."""
    an = {"nlv": 1_000_000.0, "earnings_calendar": {"CRM": "2026-07-20"}}
    pb = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec()}, an=an)
    md = "\n".join(render_rotation_playbook(pb))
    assert "PHASE 1" in md
    assert "no qualified re-deployment today" in md
    assert "Sell-to-Open" not in md


def test_renderer_accepts_to_dict_form():
    """The renderer takes both the dataclass and its to_dict() dict (the
    webapp / JSON round-trip shape)."""
    pb = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec(3, "High", 5)})
    md_obj = "\n".join(render_rotation_playbook(pb))
    md_dict = "\n".join(render_rotation_playbook(pb.to_dict()))
    assert md_obj == md_dict


def test_to_dict_round_trip_json_safe():
    import json
    pb = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec(3, "High", 5)})
    blob = json.dumps(pb.to_dict())
    assert "CRM" in blob and "GOOG" in blob


# ── Bug #23 — Phase 2 gates against PROJECTED post-Phase-1 state ──────────


def _big_sweep_helds(collateral_k=200):
    """One winner close freeing ``collateral_k``×$1K (capture 50%)."""
    return [held(ticker="GOOG", strike=collateral_k * 10, qty=-1,
                 entry=20.0, mid=10.0, dte=45, exp="2026-08-21")]


def test_phase2_gates_use_projected_cash():
    """2026-07-22 symptom: cash 4% NLV pre-close → every Phase 2 candidate
    hit 🚫 BLOCK (CASH_FLOOR). The composed rotation must still deploy:
    it is ~cash-neutral (winner buyback out, new premium in) and
    obligation-reducing (deploy ≤ freed), so a pre-existing cash-floor
    breach is not worsened by it. NOTE (rule #43, 2026-08-14): the old
    rationale "freed $200K → projected cash ~24% NLV" was fiction — freed
    collateral never becomes cash on a margin-secured book; the sanctioned
    rebuild path stands on obligation reduction instead."""
    an = {
        "nlv": 1_000_000.0,
        "snapshot_data": {
            "balance": {"accountValue": 1_000_000.0, "cash": 40_000.0},
        },
    }
    pb = playbook(_big_sweep_helds(200), [cand(ticker="CRM")],
                  {"CRM": rec(3, "High", 5)}, an=an)
    assert pb is not None
    assert pb.total_freed == pytest.approx(200_000)
    assert [o.ticker for o in pb.opens] == ["CRM"]
    # Sequencing is explicit: the open only unlocks after Phase 1 fires.
    assert any("unlocks after Phase 1" in f for f in pb.opens[0].setup_flags)


def test_phase2_projected_coverage_reopens_gate():
    """Pre-close coverage 0.10× (< 0.50× floor) → ENTRY_GATES_CLOSED would
    block. The coverage-IMPROVING composed rotation is still admitted, and
    coverage_after uses the HONEST margin-secured formula (rule #43,
    2026-08-14 — the real briefing rendered "Coverage after: 0.11× →
    ~0.38×" by treating $123K of freed SNDK collateral as new cash; the
    honest number was ~0.13×): (cash − buybacks + new premium) /
    (obl − freed + deployed). Freed collateral shrinks the obligation; it
    never becomes cash."""
    an = {
        "nlv": 2_000_000.0,
        "stress_coverage": {"coverage_ratio": 0.10,
                            "total_put_obligations": 1_000_000.0,
                            "cash": 100_000.0},
        "snapshot_data": {
            "balance": {"accountValue": 2_000_000.0, "cash": 100_000.0},
        },
    }
    pb = playbook(_big_sweep_helds(300), [cand(ticker="CRM")],
                  {"CRM": rec(3, "High", 5)}, an=an)
    assert pb is not None
    assert pb.total_freed == pytest.approx(300_000)
    # Honest projection: (100K − 1K btc) / (1M − 300K) ≈ 0.141× ≥ 0.10×
    # measured → coverage-improving → candidate admitted.
    assert [o.ticker for o in pb.opens] == ["CRM"]
    assert any("unlocks after Phase 1" in f for f in pb.opens[0].setup_flags)
    # coverage_after — HONEST: (100,000 − 1,000 + 240) / (1,000,000 −
    # 300,000 + 15,000) = 99,240 / 715,000 ≈ 0.1388×. NEVER the fictional
    # (cash + freed) / (obl − freed + deployed) = 400K/715K ≈ 0.5594×.
    assert pb.coverage_after == pytest.approx(99_240 / 715_000, abs=1e-3)
    assert pb.coverage_after < 0.20  # the 0.38×-style fiction can't return


def test_projected_state_none_preserves_current_behavior():
    """Backward compat: analytics without cash/coverage/obligations behaves
    exactly as before — no gating, no unlock tag, legacy coverage math."""
    pb = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec(3, "High", 5)})
    assert pb is not None
    assert [c.ticker for c in pb.closes] == ["MSFT", "GOOG"]
    assert [o.ticker for o in pb.opens] == ["CRM"]
    assert not any("unlocks after Phase 1" in f
                   for o in pb.opens for f in o.setup_flags)
    assert pb.coverage_before is None and pb.coverage_after is None
    # Legacy first-order coverage estimate still used when cash is unknown.
    an = {"nlv": 1_000_000.0,
          "stress_coverage": {"coverage_ratio": 0.17,
                              "total_put_obligations": 1_000_000.0}}
    pb2 = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec()}, an=an)
    assert pb2.coverage_after == pytest.approx(
        0.17 * 1_000_000 / (1_000_000 - 67_500 + 15_000), abs=1e-4)


def test_phase2_earnings_window_still_blocks():
    """Position-shape gates are NOT sensitive to projected state — earnings
    inside the expiry window excludes the candidate no matter how much cash
    Phase 1 frees."""
    an = {
        "nlv": 1_000_000.0,
        "snapshot_data": {
            "balance": {"accountValue": 1_000_000.0, "cash": 40_000.0},
        },
        "earnings_calendar": {"CRM": "2026-07-20"},   # 13d < 38 DTE
    }
    pb = playbook(_big_sweep_helds(200), [cand(ticker="CRM")],
                  {"CRM": rec(3, "High", 5)}, an=an)
    assert pb is not None
    assert pb.opens == []


def test_upstream_cash_floor_skip_readmitted():
    """A candidate the LT-opportunities step demoted to SKIPPED_LT_CSP with
    'pre-trade validator BLOCK: CASH_FLOOR' re-enters the playbook pool and
    qualifies against the projected post-close state (the 2026-07-22
    CRM/ORCL/CRWD case). A candidate whose skip includes a POSITION-shape
    gate (earnings) stays skipped."""
    an = {
        "nlv": 1_000_000.0,
        "snapshot_data": {
            "balance": {"accountValue": 1_000_000.0, "cash": 40_000.0},
        },
    }
    blocked = cand(ticker="ORCL", strike=130, premium=6.47,
                   kind="SKIPPED_LT_CSP")
    blocked["kind_when_skipped"] = "LONG_DATED_CSP"
    blocked["skip_reason"] = "pre-trade validator BLOCK: CASH_FLOOR"
    blocked["validator_findings"] = [
        {"severity": "BLOCK", "rule_id": "CASH_FLOOR",
         "reason": "Cash floor breached — 4.0% NLV < 5% floor"}]
    mixed = cand(ticker="CRWD", strike=300, premium=9.0,
                 kind="SKIPPED_LT_CSP")
    mixed["kind_when_skipped"] = "LONG_DATED_CSP"
    mixed["skip_reason"] = \
        "pre-trade validator BLOCK: CASH_FLOOR; EARNINGS_WINDOW"
    pb = playbook(_big_sweep_helds(200), [blocked, mixed],
                  {"ORCL": rec(3, "High", 5), "CRWD": rec(3, "High", 5)},
                  an=an)
    assert pb is not None
    assert [o.ticker for o in pb.opens] == ["ORCL"]
    assert any("unlocks after Phase 1" in f for f in pb.opens[0].setup_flags)


# ── Task #27 — sweep-the-winners loosening (discipline intact) ────────────


def test_min_conviction_score_4_admits_tier2_borderline_buy_medium():
    """Floor 6 → 4: a tier-2 borderline BUY · Medium at 22-30d freshness
    scores 2×2×1 = 4 — below the old floor, exactly at the new one. No-rec
    names (score ≤ 3) stay below floor 4 (the floor isn't blurred)."""
    c = cand(ticker="ALAB", strike=100, premium=3.10)
    recs = {"ALAB": rec(2, "Medium", 25)}                      # 2×2×1 = 4
    # Old floor (default 6) rejects it.
    pb = playbook(_sweeper(), [c], recs)
    assert pb.opens == []
    # New configured floor 4 admits it.
    pb2 = playbook(_sweeper(), [c], recs,
                   config={"rotation_playbook": {"min_conviction_score": 4}})
    assert [o.ticker for o in pb2.opens] == ["ALAB"]
    assert pb2.opens[0].conviction_score == pytest.approx(4.0)
    # A no-rec name (0.5 base × anything ≤ 3) still fails floor 4.
    pb3 = playbook(_sweeper(), [cand(ticker="GEV", strike=90, premium=3.0)],
                   {}, config={"rotation_playbook": {"min_conviction_score": 4}})
    assert pb3.opens == []


def _orcl_shaped(conviction="Medium", age=5, tier=3, drawdown=60.0):
    """The real 2026-07-22 ORCL shape: LT `broken`, RSI 32 oversold, 60%
    drawdown, fresh Parkev BUY Medium tier 3."""
    an = {"nlv": 1_000_000.0, "snapshot_data": {
        "technicals": {"ORCL": {"deep": {"long_term_verdict": "broken",
                                         "vs_sma200_pct": -35.0}}}}}
    c = cand(ticker="ORCL", strike=115, premium=5.53, rsi_14=32.0,
             drawdown_pct=drawdown)
    recs = {"ORCL": rec(tier, conviction, age)}
    return an, c, recs


def test_lt_override_medium_conviction_admits_tier3_med_fresh_high_drawdown():
    """Task #27 widened rule #39 exception: fresh (≤14d) Parkev BUY·Medium
    tier ≥3 on a ≥30% drawdown overrides the LT gate — admitted WITH the −3
    penalty and the visible exception warning (the ORCL case)."""
    an, c, recs = _orcl_shaped()
    pb = playbook(_sweeper(), [c], recs, an=an)
    assert [o.ticker for o in pb.opens] == ["ORCL"]
    o = pb.opens[0]
    # 3×2×2.5 = 15, +2 RSI<35, −3 LT penalty = 14.
    assert o.conviction_score == pytest.approx(14.0)
    assert any("rule #39 playbook exception" in w for w in o.warnings)
    assert any("Medium" in w and "drawdown" in w for w in o.warnings)
    # Shallow drawdown (<30%) does NOT earn the Medium branch.
    an2, c2, recs2 = _orcl_shaped(drawdown=18.0)
    pb2 = playbook(_sweeper(), [c2], recs2, an=an2)
    assert pb2.opens == []


def test_lt_override_medium_conviction_kill_switch_reverts_to_strict():
    """`lt_override_medium_conviction: false` → the Medium branch is dead
    (old strict behavior); a fresh High BUY still overrides as before."""
    an, c, recs = _orcl_shaped()
    pb = playbook(_sweeper(), [c], recs, an=an,
                  config={"rotation_playbook":
                          {"lt_override_medium_conviction": False}})
    assert pb.opens == []
    # High conviction unaffected by the kill switch (pre-#27 behavior).
    an2, c2, recs2 = _orcl_shaped(conviction="High")
    pb2 = playbook(_sweeper(), [c2], recs2, an=an2,
                   config={"rotation_playbook":
                           {"lt_override_medium_conviction": False}})
    assert [o.ticker for o in pb2.opens] == ["ORCL"]


def test_lt_override_still_requires_fresh():
    """A 30d-old tier-3 Medium BUY on a 60% drawdown does NOT override —
    freshness (≤14d) is non-negotiable on both branches."""
    an, c, recs = _orcl_shaped(age=30)
    pb = playbook(_sweeper(), [c], recs, an=an)
    assert pb.opens == []
    # Stale High doesn't override either (pinned pre-#27, re-pinned here).
    an2, c2, recs2 = _orcl_shaped(conviction="High", age=30)
    pb2 = playbook(_sweeper(), [c2], recs2, an=an2)
    assert pb2.opens == []


def test_warn_findings_render_as_annotations_not_exclusions():
    """A pre-trade validator WARN (STRIKE_NOT_AT_SUPPORT) annotates the
    candidate's warnings but never excludes it; with the config off the
    candidate is still selected, just without the annotation."""
    an = {"nlv": 1_000_000.0, "snapshot_data": {
        "balance": {"accountValue": 1_000_000.0, "cash": 500_000.0},
        "technicals": {"CRM": {
            "spot": 200.0,
            # Nearest support 26% above the $150 strike → Rule 10 WARN.
            "support_resistance": {"supports": [{"price": 190.0}]},
        }},
    }}
    pb = playbook(_sweeper(), [cand(ticker="CRM")],
                  {"CRM": rec(3, "High", 5)}, an=an)
    assert [o.ticker for o in pb.opens] == ["CRM"]
    assert any("STRIKE_NOT_AT_SUPPORT" in w for w in pb.opens[0].warnings)
    # Config off → old behavior: selected, WARN silently dropped.
    pb2 = playbook(_sweeper(), [cand(ticker="CRM")],
                   {"CRM": rec(3, "High", 5)}, an=an,
                   config={"rotation_playbook":
                           {"treat_warn_as_annotation": False}})
    assert [o.ticker for o in pb2.opens] == ["CRM"]
    assert not any("STRIKE_NOT_AT_SUPPORT" in w
                   for w in pb2.opens[0].warnings)


def test_block_findings_still_exclude():
    """EARNINGS_WINDOW stays a hard exclude no matter how loose the task #27
    config is — WARN-as-annotation never weakens a BLOCK."""
    an = {"nlv": 1_000_000.0,
          "earnings_calendar": {"CRM": "2026-07-20"}}          # inside 38 DTE
    pb = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec(3, "High", 5)},
                  an=an,
                  config={"rotation_playbook": {
                      "treat_warn_as_annotation": True,
                      "min_conviction_score": 4,
                      "lt_override_medium_conviction": True}})
    assert pb.opens == []


def test_sell_rated_candidate_never_qualifies_at_floor_4():
    """Floor 4 must not let a Parkev SELL name float over on setup bonuses
    alone (0 base + 2 pullback + 2 oversold + 1 IV = 5) — the scout's AVOID
    branch vetoes SELL as a new-open catalyst (hard rule #25)."""
    c = cand(ticker="TSLA", strike=300, premium=9.0, rsi_14=32.0,
             iv_rank=90.0, verdict="pullback")
    pb = playbook(_sweeper(), [c],
                  {"TSLA": rec(0, "High", 3, recommendation="SELL")},
                  config={"rotation_playbook": {"min_conviction_score": 4}})
    assert pb.opens == []


def test_hold_qualifier_carries_setup_only_verify_badge():
    """A Parkev HOLD name clearing floor 4 on setup bonuses (the CRWD
    RSI-50/IV-86 shape) is admitted but annotated 'not a BUY catalyst'
    (hard rule #38 phrasing: HOLD ≠ no rec).

    Chase-guard/gate-4 alignment: a non-BUY name now qualifies ONLY via
    the full independent-setup bar — RSI VERIFIED live (vintage check) in
    35-55 + IV ≥ 50 + a quiet tape (chase guard, rule #44). The fixtures
    supply the verifying technicals + quotes, and the rule-#38 phrasing now
    lives on ``independent_setup_badge`` (rendered on the card), not in the
    warnings list."""
    c = cand(ticker="CRWD", strike=170, premium=8.05, rsi_14=49.8,
             iv_rank=86.5)
    an = {"nlv": 1_000_000.0, "snapshot_data": {
        "technicals": {"CRWD": {"spot": 185.0, "rsi_14": 49.8,
                                "recent_closes": [185.0] * 15}},
        "quotes": {"CRWD": {"last": 185.4}}}}
    pb = playbook(_sweeper(), [c],
                  {"CRWD": rec(1, "Medium", 48, recommendation="HOLD")},
                  an=an,
                  config={"rotation_playbook": {"min_conviction_score": 4}})
    # 1×2×0.5 = 1, +2 pullback, +1 IV ≥85 = 4.0 — exactly at the floor.
    assert [o.ticker for o in pb.opens] == ["CRWD"]
    assert pb.opens[0].conviction_score == pytest.approx(4.0)
    badge = pb.opens[0].independent_setup_badge or ""
    assert "Parkev HOLD" in badge and "not a BUY catalyst" in badge
    assert "verified" in badge          # RSI verified live, never assumed
    # No-rec phrasing stays distinct (never claims HOLD).
    c2 = cand(ticker="GEV", strike=90, premium=3.2, rsi_14=42.4,
              iv_rank=90.0)
    an2 = {"nlv": 1_000_000.0, "snapshot_data": {
        "technicals": {"GEV": {"spot": 100.0, "rsi_14": 42.4,
                               "recent_closes": [100.0] * 15}},
        "quotes": {"GEV": {"last": 100.2}}}}
    pb2 = playbook(_sweeper(), [c2], {}, an=an2,
                   config={"rotation_playbook": {"min_conviction_score": 3}})
    assert [o.ticker for o in pb2.opens] == ["GEV"]
    badge2 = pb2.opens[0].independent_setup_badge or ""
    assert "no third-party rec" in badge2 and "HOLD" not in badge2


def test_max_opens_8_caps_selection():
    """12 qualifying candidates, max_opens 8 → exactly 8 selected."""
    tickers = [f"T{i}" for i in range(12)]
    cands = [cand(ticker=t, strike=20, premium=0.8) for t in tickers]
    recs = {t: rec(3, "High", 5) for t in tickers}
    pb = playbook(_sweeper(), cands, recs,
                  config={"rotation_playbook": {"max_opens": 8}})
    assert len(pb.opens) == 8


def test_entry_gate_never_blocks_coverage_improving_playbook():
    """Real 2026-07-22 shape: coverage 0.06× pre-close, ~0.23× post-close —
    still under the 0.50× entry floor, but the playbook only IMPROVES
    coverage (deploy ≤ freed), so ENTRY_GATES_CLOSED must not empty Phase 2.
    The rotation modules are the sanctioned path to rebuild coverage while
    staying deployed (task #20 origin)."""
    an = {
        "nlv": 1_000_000.0,
        "stress_coverage": {"coverage_ratio": 0.06,
                            "total_put_obligations": 755_000.0,
                            "cash": 48_000.0},
        "snapshot_data": {
            "balance": {"accountValue": 1_000_000.0, "cash": 48_000.0},
        },
    }
    pb = playbook(_big_sweep_helds(100), [cand(ticker="CRM")],
                  {"CRM": rec(3, "High", 5)}, an=an)
    assert pb is not None
    # (48K + 100K) / (755K − 100K) ≈ 0.226× — below the floor, yet the
    # coverage-improving playbook still deploys.
    assert [o.ticker for o in pb.opens] == ["CRM"]
    assert any("unlocks after Phase 1" in f for f in pb.opens[0].setup_flags)


# ── Task #28 — coverage-adaptive deployment cap ───────────────────────────

BANDS = [
    {"below": 0.20, "cap_pct": 0.30,
     "label": "crisis coverage — prioritize cushion"},
    {"below": 0.50, "cap_pct": 0.50,
     "label": "defensive coverage — half cushion"},
    {"below": None, "cap_pct": 0.90,
     "label": "healthy coverage — normal deployment"},
]

ADAPTIVE_CFG = {"rotation_playbook": {"adaptive_deploy_bands": BANDS}}


def _coverage_an(cash, obligations, nlv=1_000_000.0):
    """Analytics with measured cash + obligations so the projected
    post-Phase-1 coverage ratio computes."""
    ratio = cash / obligations if obligations else None
    return {
        "nlv": nlv,
        "stress_coverage": {"coverage_ratio": ratio,
                            "total_put_obligations": obligations,
                            "cash": cash},
        "snapshot_data": {"balance": {"accountValue": nlv, "cash": cash}},
    }


def _two_cands():
    return [
        cand(ticker="CRM", strike=150, premium=2.40),          # $15K
        cand(ticker="ORCL", strike=130, premium=6.47),         # $13K
    ]


# Distinct scores so ranking is deterministic: CRM 18 leads, ORCL 12.
_TWO_RECS = {"CRM": rec(3, "High", 5), "ORCL": rec(3, "Medium", 5)}


def test_adaptive_cap_crisis_30pct():
    """User context 2026-07-22: at crisis coverage the playbook must be
    closes-heavy, opens-light. Projected post-close coverage
    (48K+67.5K)/(800K−67.5K) ≈ 0.16× < 0.20× → cap 30% of freed ($20,250);
    CRM ($15K) fits, ORCL ($13K) would breach the ceiling → skipped."""
    an = _coverage_an(cash=48_000.0, obligations=800_000.0)
    pb = playbook(_sweeper(), _two_cands(), _TWO_RECS, an=an,
                  config=ADAPTIVE_CFG)
    assert pb is not None
    assert pb.deploy_cap_pct == pytest.approx(0.30)
    assert "crisis" in pb.deploy_cap_label
    assert [o.ticker for o in pb.opens] == ["CRM"]
    assert pb.total_collateral_deployed <= 0.30 * pb.total_freed
    assert pb.deploy_cap_next_note.startswith("Once coverage clears 0.20×")


def test_adaptive_cap_defensive_50pct():
    """HONEST projected coverage (100K − 1.4K buybacks)/(500K − 67.5K
    freed) ≈ 0.23× → defensive band, cap 50% ($33,750) — both candidates
    ($28K total) fit. (Freed collateral shrinks the obligation; it does
    NOT become cash — rule #43, 2026-08-14.)"""
    an = _coverage_an(cash=100_000.0, obligations=500_000.0)
    pb = playbook(_sweeper(), _two_cands(), _TWO_RECS, an=an,
                  config=ADAPTIVE_CFG)
    assert pb.deploy_cap_pct == pytest.approx(0.50)
    assert "defensive" in pb.deploy_cap_label
    assert sorted(o.ticker for o in pb.opens) == ["CRM", "ORCL"]
    assert pb.total_collateral_deployed <= 0.50 * pb.total_freed


def test_adaptive_cap_healthy_90pct():
    """HONEST projected coverage (80K − 1.4K buybacks)/(200K − 67.5K freed)
    ≈ 0.59× → healthy band, cap 90% (the current default) — no
    reduced-band note."""
    an = _coverage_an(cash=80_000.0, obligations=200_000.0)
    pb = playbook(_sweeper(), _two_cands(), _TWO_RECS, an=an,
                  config=ADAPTIVE_CFG)
    assert pb.deploy_cap_pct == pytest.approx(0.90)
    assert "healthy" in pb.deploy_cap_label
    assert pb.deploy_cap_next_note == ""


def test_adaptive_cap_uses_projected_not_current():
    """Pre-close coverage 0.30× (defensive) but Phase 1 retires $300K of
    obligation → HONEST projected (150K − 1K buybacks)/(500K − 300K freed)
    = 0.745× → HEALTHY band cap 0.90, not the defensive 0.50. The cap reads
    the post-close state — Phase 1 already happened. (The projection is the
    honest margin-secured one: obligation shrinks; freed collateral never
    becomes cash — rule #43, 2026-08-14.)"""
    an = _coverage_an(cash=150_000.0, obligations=500_000.0)
    pb = playbook(_big_sweep_helds(300), [cand(ticker="CRM")],
                  {"CRM": rec(3, "High", 5)}, an=an, config=ADAPTIVE_CFG)
    assert pb is not None
    assert pb.deploy_cap_pct == pytest.approx(0.90)
    assert "healthy" in pb.deploy_cap_label
    assert pb.deploy_cap_coverage == pytest.approx(149_000 / 200_000, abs=1e-3)


def test_flat_deploy_target_fallback():
    """adaptive_deploy_bands: null → legacy flat deploy_target_pct behavior
    (soft stop — MDB at $32K on $32.5K freed still selected past 90%), and
    no adaptive-cap fields are set. Bands set but NO measurable coverage →
    same flat fallback (fail-open)."""
    helds = [held(ticker="GOOG", strike=325, entry=7.9447, mid=4.45, dte=45)]
    cands = [cand(ticker="MDB", strike=320, premium=13.43),
             cand(ticker="DOCU", strike=41, premium=0.71)]
    recs = {"MDB": rec(3, "High", 5), "DOCU": rec(3, "High", 5)}
    an = _coverage_an(cash=48_000.0, obligations=800_000.0)
    pb = playbook(helds, cands, recs, an=an,
                  config={"rotation_playbook": {"adaptive_deploy_bands": None}})
    assert [o.ticker for o in pb.opens] == ["MDB"]
    assert pb.deploy_cap_pct is None
    assert pb.deploy_cap_label == ""
    # Bands present but no coverage measurable → flat fallback too.
    pb2 = playbook(helds, cands, recs, config=ADAPTIVE_CFG)
    assert [o.ticker for o in pb2.opens] == ["MDB"]
    assert pb2.deploy_cap_pct is None


def test_cap_is_ceiling_not_floor():
    """Only one qualifying candidate at ~5% of freed, cap 30% → deploys 5%,
    not 30%. The cap never inflates deployment."""
    an = _coverage_an(cash=48_000.0, obligations=800_000.0)   # crisis band
    small = cand(ticker="DOCU", strike=34, premium=0.71)      # $3,400
    pb = playbook(_sweeper(), [small], {"DOCU": rec(3, "High", 5)}, an=an,
                  config=ADAPTIVE_CFG)
    assert pb.deploy_cap_pct == pytest.approx(0.30)
    assert pb.total_collateral_deployed == pytest.approx(3_400)
    assert pb.cash_cushion_kept == pytest.approx(64_100)


def test_playbook_renders_cap_rationale():
    """The rendered panel surfaces the cap banner (Phase 2) + the impact
    line: band label, cap %, amount saved, and the next-band note."""
    an = _coverage_an(cash=48_000.0, obligations=800_000.0)   # crisis band
    pb = playbook(_sweeper(), _two_cands(), _TWO_RECS, an=an,
                  config=ADAPTIVE_CFG)
    md = "\n".join(render_rotation_playbook(pb))
    assert "Deployment capped at 30% of freed" in md
    assert "crisis coverage — prioritize cushion" in md
    assert "$52,500" in md                     # cushion held (67.5K − 15K)
    assert "Once coverage clears 0.20× the cap rises to 50%." in md
    assert "cushion held" in md
    # Impact line carries the label + amount saved too.
    assert "Deployment cap: 30% of freed" in md


# ── Task #29 — bucket diversification (soft) ──────────────────────────────


def test_bucket_warning_fires_over_threshold():
    """100% of new deployment on one Friday → the hard diversification
    warning fires (same trade N times over, not diversification). With no
    alternative Friday in the pool, the warning says so explicitly."""
    pb = playbook(_sweeper(), _two_cands(), _TWO_RECS)  # both 2026-08-14
    assert len(pb.opens) == 2
    w = [x for x in pb.warnings if "of new deployment" in x]
    assert w and "carries 100%" in w[0] and "Aug 14 '26" in w[0]
    assert "no alternative Friday" in w[0]


def test_bucket_no_warning_diverse():
    """40/30/30 split across three Fridays — every bucket ≤ 60% → no
    diversification warning."""
    cands = [
        cand(ticker="CRM", strike=100, premium=2.4, exp="2026-08-14"),
        cand(ticker="ORCL", strike=75, premium=2.0, exp="2026-08-21", dte=45),
        cand(ticker="ZS", strike=75, premium=2.0, exp="2026-08-28", dte=52),
    ]
    recs = {t: rec(3, "High", 5) for t in ("CRM", "ORCL", "ZS")}
    pb = playbook(_sweeper(), cands, recs)
    assert len(pb.opens) == 3
    assert not any("of new deployment" in w for w in pb.warnings)


def test_tie_break_prefers_new_bucket():
    """Two candidates tied on conviction score — one in the already-loaded
    Aug 14 bucket, one in the empty Sep 18 bucket → Sep 18 selected first.
    With tie_break_prefer_diverse off, rank order (yield) wins instead."""
    cands = [
        cand(ticker="CRM", strike=145, premium=2.955, exp="2026-08-14"),  # 18
        # Tied at 12; Y has the higher yield so it ranks ahead of Z.
        cand(ticker="YTIC", strike=100, premium=3.0, exp="2026-08-14"),
        # premium 2.5 (not 2.0) keeps ZTIC above the rule #43 yield floor
        # (2.5×100×365/(73×10000) = 12.5% ann > 12%) — this test pins the
        # tie-break, not the floor; YTIC still out-yields it (28.8%).
        cand(ticker="ZTIC", strike=100, premium=2.5, exp="2026-09-18", dte=73),
    ]
    recs = {"CRM": rec(3, "High", 5), "YTIC": rec(3, "Medium", 5),
            "ZTIC": rec(3, "Medium", 5)}
    pb = playbook(_sweeper(), cands, recs)
    assert [o.ticker for o in pb.opens] == ["CRM", "ZTIC", "YTIC"]
    pb2 = playbook(_sweeper(), cands, recs,
                   config={"rotation_playbook": {"bucket_diversification":
                           {"tie_break_prefer_diverse": False}}})
    assert [o.ticker for o in pb2.opens] == ["CRM", "YTIC", "ZTIC"]


def test_hard_cap_off_by_default():
    """hard_cap_pct null (default) → same-bucket concentration is allowed
    (warned, never blocked)."""
    cands = [
        cand(ticker="CRM", strike=150, premium=2.40),          # $15K
        cand(ticker="ORCL", strike=130, premium=6.47),         # $13K
        cand(ticker="ZS", strike=100, premium=3.0),            # $10K
    ]
    recs = {t: rec(3, "High", 5) for t in ("CRM", "ORCL", "ZS")}
    pb = playbook(_sweeper(), cands, recs)
    assert len(pb.opens) == 3                                  # all admitted
    assert any("of new deployment" in w for w in pb.warnings)  # but warned


def test_hard_cap_when_set_skips_over_concentrating_candidate():
    """hard_cap_pct 0.60 → bucket limit = 0.60 × deploy budget (0.9 × 67.5K
    = $60,750 → $36,450). CRM $15K + ORCL $13K fit; ZS $10K would push the
    Aug 14 bucket to $38K > $36,450 → skipped."""
    cands = [
        cand(ticker="CRM", strike=150, premium=2.40),
        cand(ticker="ORCL", strike=130, premium=6.47),
        cand(ticker="ZS", strike=100, premium=3.0),
    ]
    # Distinct scores → deterministic rank: CRM 18, ORCL 12, ZS 6.
    recs = {"CRM": rec(3, "High", 5), "ORCL": rec(3, "Medium", 5),
            "ZS": rec(3, "Medium", 20)}
    pb = playbook(_sweeper(), cands, recs,
                  config={"rotation_playbook": {"bucket_diversification":
                          {"hard_cap_pct": 0.60}}})
    assert sorted(o.ticker for o in pb.opens) == ["CRM", "ORCL"]
    assert "ZS" not in [o.ticker for o in pb.opens]


def test_book_level_bucket_warning_fires():
    """Surviving held puts + new opens on the same Friday reach 17% NLV →
    the rule #21-style book-level warning fires ('approaching' the 20%
    single-Friday threshold), reusing analyze_put_buckets math."""
    helds = _sweeper() + [
        # Low-capture (10%) TSM put survives Phase 1: $155K on Aug 14.
        held(ticker="TSM", strike=1550, exp="2026-08-14", entry=10.0,
             mid=9.0, dte=23),
    ]
    pb = playbook(helds, [cand(ticker="CRM")], {"CRM": rec(3, "High", 5)},
                  an={"nlv": 1_000_000.0})
    assert [o.ticker for o in pb.opens] == ["CRM"]
    w = [x for x in pb.warnings if "Post-playbook" in x]
    # TSM $155K + CRM $15K = $170K = 17.0% NLV on Aug 14 '26.
    assert w and "$170,000" in w[0] and "17.0% NLV" in w[0]
    assert "approaching the 20%" in w[0]
    # Below the 10% info floor → no book-level warning.
    pb2 = playbook(_sweeper(), [cand(ticker="CRM")],
                   {"CRM": rec(3, "High", 5)}, an={"nlv": 1_000_000.0})
    assert not any("Post-playbook" in x for x in pb2.warnings)


# ── Task #30 — equity-stacking penalty ────────────────────────────────────


def _eq_an(ticker, pct, nlv=1_000_000.0):
    """Analytics with a held EQUITY position worth ``pct`` (fraction) of NLV
    plus a comfortable cash balance (keeps the validator's cash floor open)."""
    return {"nlv": nlv, "snapshot_data": {
        "balance": {"accountValue": nlv, "cash": 500_000.0},
        "positions": [{"assetType": "EQUITY", "symbol": ticker,
                       "price": 100.0, "qty": pct * nlv / 100.0}],
    }}


def test_equity_stacking_none_below_2pct():
    """ZS real 2026-07-22 shape: equity held is only 1.4% NLV → NO penalty,
    no tag (the honest read — 'you already hold ZS equity' was overstated)."""
    pb = playbook(_sweeper(), [cand(ticker="ZS", strike=130, premium=3.0)],
                  {"ZS": rec(3, "High", 5)}, an=_eq_an("ZS", 0.014))
    assert [o.ticker for o in pb.opens] == ["ZS"]
    o = pb.opens[0]
    assert o.conviction_score == pytest.approx(22.5)          # untouched
    assert o.equity_held_pct == pytest.approx(0.014)
    assert not any("equity concentration" in w for w in o.warnings)


def test_equity_stacking_modest_penalty_2_5pct():
    """3% NLV held → −1 + 'modest equity concentration' tag."""
    pb = playbook(_sweeper(), [cand(ticker="MU", strike=90, premium=3.0)],
                  {"MU": rec(3, "High", 5)}, an=_eq_an("MU", 0.03))
    o = pb.opens[0]
    assert o.conviction_score == pytest.approx(21.5)          # 22.5 − 1
    assert any("modest equity concentration (3.0% NLV held)" in w
               for w in o.warnings)
    # The tag surfaces at the playbook level too (visible in the panel).
    assert any("modest equity concentration" in w for w in pb.warnings)


def test_equity_stacking_concerning_penalty_5_10pct():
    """7% NLV held → −3 + 'stacks equity concentration' tag."""
    pb = playbook(_sweeper(), [cand(ticker="PLTR", strike=110, premium=4.0)],
                  {"PLTR": rec(3, "High", 5)}, an=_eq_an("PLTR", 0.07))
    o = pb.opens[0]
    assert o.conviction_score == pytest.approx(19.5)          # 22.5 − 3
    assert any("stacks equity concentration (7.0% NLV held)" in w
               for w in o.warnings)


def test_equity_stacking_hard_skip_10pct_plus():
    """14.3% NLV held (the real NVDA case) → HARD SKIP; the reason renders
    in the playbook warnings footer (never silent). The per-ticker
    force_include kill switch downgrades to the −3 penalty."""
    an = _eq_an("NVDA", 0.143)
    pb = playbook(_sweeper(), [cand(ticker="NVDA", strike=160, premium=4.0)],
                  {"NVDA": rec(4, "High", 5)}, an=an)
    assert pb is not None
    assert pb.opens == []
    assert any("NVDA" in w and "hard-skip" in w for w in pb.warnings)
    # Kill switch: force_include re-admits with the concerning penalty.
    pb2 = playbook(_sweeper(), [cand(ticker="NVDA", strike=160, premium=4.0)],
                   {"NVDA": rec(4, "High", 5)}, an=an,
                   config={"rotation_playbook": {"equity_stacking":
                           {"force_include": ["NVDA"]}}})
    assert [o.ticker for o in pb2.opens] == ["NVDA"]
    assert pb2.opens[0].conviction_score == pytest.approx(27.0)  # 30 − 3
    assert any("force-included" in w for w in pb2.opens[0].warnings)


def test_equity_stacking_missing_data_fail_open():
    """No positions data for the ticker → no penalty, no error."""
    pb = playbook(_sweeper(), [cand(ticker="CRM")],
                  {"CRM": rec(3, "High", 5)}, an=_eq_an("NVDA", 0.143))
    o = pb.opens[0]
    assert o.conviction_score == pytest.approx(22.5)
    assert o.equity_held_pct is None
    # No analytics at all → same.
    pb2 = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec(3, "High", 5)})
    assert pb2.opens[0].conviction_score == pytest.approx(22.5)


# ── Task #31 — support-quality gate ───────────────────────────────────────


def _sr_an(ticker, supports, nlv=1_000_000.0):
    """Analytics whose technicals carry an S/R payload for ``ticker``."""
    return {"nlv": nlv, "snapshot_data": {
        "balance": {"accountValue": nlv, "cash": 500_000.0},
        "technicals": {ticker: {"support_resistance": {"supports": supports}}},
    }}


def test_strike_at_multi_touch_no_penalty():
    """CRM-shape: $145P within 5% of a 3-touch $150 cluster → OK."""
    an = _sr_an("CRM", [{"price": 150.0, "touches": 3}])
    pb = playbook(_sweeper(), [cand(ticker="CRM", strike=145, premium=2.9)],
                  {"CRM": rec(3, "High", 5)}, an=an)
    o = pb.opens[0]
    assert o.support_quality == "at_multi_touch"
    assert o.conviction_score == pytest.approx(22.5)
    assert not any("strike" in w for w in o.warnings)


def test_strike_below_multi_touch_no_penalty():
    """$130P sitting 13% BELOW a 3-touch $150 cluster → OK — below-support
    is protective (assignment happens further in)."""
    an = _sr_an("CRM", [{"price": 150.0, "touches": 3}])
    pb = playbook(_sweeper(), [cand(ticker="CRM", strike=130, premium=1.8)],
                  {"CRM": rec(3, "High", 5)}, an=an)
    o = pb.opens[0]
    assert o.support_quality == "below_multi_touch"
    assert o.conviction_score == pytest.approx(22.5)


def test_strike_at_single_touch_penalty_2():
    """CRWV-shape: $75P anchored only to a single-touch $77 level (52w-low)
    → −2 + 'weak floor' tag."""
    an = _sr_an("CRWV", [{"price": 77.0, "touches": 1}])
    pb = playbook(_sweeper(), [cand(ticker="CRWV", strike=75, premium=4.0)],
                  {"CRWV": rec(3, "High", 5)}, an=an)
    o = pb.opens[0]
    assert o.support_quality == "single_touch_only"
    assert o.conviction_score == pytest.approx(20.5)          # 22.5 − 2
    assert any("1-touch support only (weak floor)" in w for w in o.warnings)
    assert any("1-touch support only" in w for w in pb.warnings)


def test_strike_floats_penalty_2():
    """ZS-shape: $130P with the next cluster at $118 (9% BELOW the strike —
    not protective, strike is above it) → −2 + 'floats in air' tag."""
    an = _sr_an("ZS", [{"price": 118.0, "touches": 2}])
    pb = playbook(_sweeper(), [cand(ticker="ZS", strike=130, premium=3.0)],
                  {"ZS": rec(3, "High", 5)}, an=an)
    o = pb.opens[0]
    assert o.support_quality == "floats"
    assert o.conviction_score == pytest.approx(20.5)          # 22.5 − 2
    assert any("floats in air (no support within 5%)" in w for w in o.warnings)


def test_strike_support_unknown_fail_open():
    """No S/R payload (or empty clusters) → no penalty, quality 'unknown'."""
    pb = playbook(_sweeper(), [cand(ticker="CRM")], {"CRM": rec(3, "High", 5)})
    o = pb.opens[0]
    assert o.support_quality == "unknown"
    assert o.conviction_score == pytest.approx(22.5)
    an = _sr_an("CRM", [])
    pb2 = playbook(_sweeper(), [cand(ticker="CRM")],
                   {"CRM": rec(3, "High", 5)}, an=an)
    assert pb2.opens[0].support_quality == "unknown"
    assert pb2.opens[0].conviction_score == pytest.approx(22.5)


# ── Task #32 — smooth freshness weighting ─────────────────────────────────


def test_freshness_1d_multiplier_2_5():
    """ZS-shape: rec 1d old → ×2.5 (3×3×2.5 = 22.5)."""
    assert conviction_score(3, "High", 1) == pytest.approx(22.5)


def test_freshness_5d_multiplier_2_5():
    assert conviction_score(3, "High", 5) == pytest.approx(22.5)


def test_freshness_9d_multiplier_2_0():
    """CRWV edge case: 9d was 2.0 in the old step, still 2.0 in the new
    bands — the smoothing didn't break the middle."""
    assert conviction_score(3, "High", 9) == pytest.approx(18.0)


def test_freshness_18d_multiplier_1_5():
    """New 15-21d band: ×1.5 (was 1.0 under the old ≤30d step)."""
    assert conviction_score(3, "High", 18) == pytest.approx(13.5)


def test_freshness_45d_multiplier_0_5():
    assert conviction_score(3, "High", 45) == pytest.approx(4.5)


def test_freshness_90d_multiplier_0_25():
    """>60d → near-zero credit (0.25 catch-all)."""
    assert conviction_score(3, "High", 90) == pytest.approx(2.25)


def test_freshness_null_no_multiplier():
    """age None (no rec) → the multiplier is NOT applied at all:
    score = tier × conviction_weight."""
    assert conviction_score(3, "High", None) == pytest.approx(9.0)
    assert conviction_score(2, "Medium", None) == pytest.approx(4.0)


def test_freshness_config_override():
    """Custom freshness_bands in config are respected end-to-end."""
    cfg = {"rotation_playbook": {"freshness_bands": [
        {"max_days": 10, "multiplier": 3.0},
        {"max_days": None, "multiplier": 0.1},
    ]}}
    pb = playbook(_sweeper(), [cand(ticker="CRM")],
                  {"CRM": rec(3, "High", 5)}, config=cfg)
    assert pb.opens[0].conviction_score == pytest.approx(27.0)  # 3×3×3.0
    # Past the custom bounded band → the custom catch-all (0.1 → 0.9 < floor).
    pb2 = playbook(_sweeper(), [cand(ticker="CRM")],
                   {"CRM": rec(3, "High", 20)}, config=cfg)
    assert pb2.opens == []
    # Malformed bands → built-in defaults (fail-open), not a crash.
    bad = {"rotation_playbook": {"freshness_bands": ["nope", 42]}}
    pb3 = playbook(_sweeper(), [cand(ticker="CRM")],
                   {"CRM": rec(3, "High", 5)}, config=bad)
    assert pb3.opens[0].conviction_score == pytest.approx(22.5)


# ── Tasks #30+#31+#32 — real 2026-07-22 shaped regression ─────────────────


def test_real_2026_07_22_gates_separate_the_field():
    """The analyst-mode pass the gates encode: ZS demoted by LT `broken` +
    floating strike (NOT by equity — only 1.4% NLV held); CRWV demoted by
    the 1-touch $64.55-anchor read; CRM/ORCL stay on top (CRM at a
    multi-touch cluster, ORCL below one); NVDA-style ≥10% equity names
    hard-skip with a visible reason."""
    nlv = 1_039_466.95
    an = {"nlv": nlv, "snapshot_data": {
        "balance": {"accountValue": nlv, "cash": 500_000.0},
        "positions": [
            {"assetType": "EQUITY", "symbol": "ZS", "price": 142.4,
             "qty": 100.0},                        # 1.37% NLV
            {"assetType": "EQUITY", "symbol": "NVDA", "price": 212.0,
             "qty": 701.0},                        # 14.3% NLV
        ],
        "technicals": {
            "CRM": {"support_resistance": {"supports": [
                {"price": 150.0, "touches": 3}]}},
            "ORCL": {"support_resistance": {"supports": [
                {"price": 122.0, "touches": 3}]}},
            "CRWV": {"support_resistance": {"supports": [
                {"price": 77.0, "touches": 1}]}},
            "ZS": {"deep": {"long_term_verdict": "broken",
                            "vs_sma200_pct": -27.3},
                   "support_resistance": {"supports": [
                       {"price": 118.0, "touches": 2}]}},
        },
    }}
    cands = [
        cand(ticker="CRM", strike=145, premium=2.9, rsi_14=49.0),
        cand(ticker="ORCL", strike=115, premium=5.53, rsi_14=44.0),
        cand(ticker="CRWV", strike=75, premium=6.1, rsi_14=41.0),
        cand(ticker="ZS", strike=130, premium=3.4, rsi_14=47.0,
             drawdown_pct=35.0),
        cand(ticker="NVDA", strike=170, premium=4.2, rsi_14=48.0),
    ]
    recs = {
        "CRM": rec(3, "High", 5), "ORCL": rec(3, "Medium", 5),
        "CRWV": rec(3, "Medium", 9), "ZS": rec(3, "High", 1),
        "NVDA": rec(4, "High", 5),
    }
    pb = playbook(_sweeper(), cands, recs, an=an,
                  config={"rotation_playbook": {"max_opens": 8,
                                                "min_conviction_score": 4}})
    assert pb is not None
    by_tk = {o.ticker: o for o in pb.opens}
    # NVDA: hard equity skip, visible.
    assert "NVDA" not in by_tk
    assert any("NVDA" in w and "hard-skip" in w for w in pb.warnings)
    # ZS: NO equity penalty at 1.4%; demoted by LT broken (−3, via the
    # fresh-High override) + floating strike (−2).
    zs = by_tk["ZS"]
    assert not any("equity concentration" in w for w in zs.warnings)
    assert zs.support_quality == "floats"
    # 3×3×2.5 = 22.5 +2 pullback −3 LT −2 floats = 19.5.
    assert zs.conviction_score == pytest.approx(19.5)
    # CRWV: 1-touch anchor read. 3×2×2.0 = 12 +2 pullback −2 = 12.
    crwv = by_tk["CRWV"]
    assert crwv.support_quality == "single_touch_only"
    assert crwv.conviction_score == pytest.approx(12.0)
    # CRM at a 3-touch cluster (24.5), ORCL below one (17) — both clean.
    assert by_tk["CRM"].support_quality == "at_multi_touch"
    assert by_tk["CRM"].conviction_score == pytest.approx(24.5)
    assert by_tk["ORCL"].support_quality == "below_multi_touch"
    assert by_tk["ORCL"].conviction_score == pytest.approx(17.0)
    # Ranking: CRM leads, ORCL ahead of CRWV — the analyst-pass order.
    order = [o.ticker for o in pb.opens]
    assert order.index("CRM") < order.index("ORCL") < order.index("CRWV")
