"""Sector look-through exposure + diversification-aware conviction tests.

George (2026-08-07): "It seems like I'm pretty heavily invested in tech.
Is it the right thing?" — the book is ~90% tech-correlated and nothing in
the briefing measured it. These tests pin:

  (a) the look-through math on a fixture book (ETF decomposition +
      assignment-adjusted view),
  (b) Unclassified fail-closed (no curated mapping + no FMP key → bucketed,
      never guessed, never counted as tech),
  (c) the cap flag fires over 35% with measured numbers / stays silent under,
  (d) the +1 / −1 / 0 conviction adjustment cases + unknown-sector 0,
  (e) the digest keeps the one-line summary and stays ≤ 250 lines,
  (f) the Red Flags & Priorities entry renders.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.sector_exposure import (  # noqa: E402
    UNCLASSIFIED,
    compute_sector_exposure,
    load_sector_map,
    sector_conviction_adjustment,
    sector_exposure_enabled,
    sector_of,
)
from render.sector_panel import render_sector_panel, summary_line  # noqa: E402
from render.digest import build_digest  # noqa: E402
from steps.red_flags import compute_red_flags, render_red_flags_md  # noqa: E402


CFG_ON = {"sector_exposure": {"enabled": True, "cap_pct": 35,
                              "underweight_pct": 5}}
CFG_OFF = {"sector_exposure": {"enabled": False}}

NLV = 1_000_000.0


def _fixture_positions(nvda_mv=200_000.0):
    """Fixture book: single stocks + broad ETF (SPY look-through) + semis
    ETF (SMH = 100% Info Tech) + tech-correlated flag (TSLA) + short puts
    (MU tech / GOOG comm) + a short CALL that must NOT add obligation."""
    return [
        {"symbol": "NVDA", "assetType": "EQUITY", "marketValue": nvda_mv},
        {"symbol": "SPY", "assetType": "EQUITY", "marketValue": 100_000.0},
        {"symbol": "SMH", "assetType": "EQUITY", "marketValue": 50_000.0},
        {"symbol": "TSLA", "assetType": "EQUITY", "marketValue": 50_000.0},
        {"symbol": "JPM", "assetType": "EQUITY", "marketValue": 20_000.0},
        {"assetType": "OPTION", "underlying": "MU", "type": "PUT",
         "qty": -1.0, "strike": 950.0, "expiration": "2027-01-15"},
        {"assetType": "OPTION", "underlying": "GOOG", "type": "PUT",
         "qty": -2.0, "strike": 300.0, "expiration": "2026-10-16"},
        {"assetType": "OPTION", "underlying": "NVDA", "type": "CALL",
         "qty": -7.0, "strike": 260.0, "expiration": "2026-11-20"},
    ]


@pytest.fixture(scope="module")
def smap():
    return load_sector_map()


# ── (a) look-through math ────────────────────────────────────────────────


def test_lookthrough_equity_math_with_etf_decomposition(smap):
    """'It seems like I'm pretty heavily invested in tech. Is it the right
    thing?' — the equity view must decompose ETFs: SPY contributes ~34% of
    its MV to Info Tech, SMH contributes 100%."""
    exp = compute_sector_exposure(_fixture_positions(), NLV, CFG_ON,
                                  smap=smap, api_key=None, cache_path=None)
    eq = exp["equity_by_sector"]
    # IT = NVDA 200K + SPY 34% × 100K + SMH 100% × 50K = 284K
    assert eq["Information Technology"] == pytest.approx(284_000.0)
    # Cons Disc = TSLA 50K + SPY 10% × 100K = 60K
    assert eq["Consumer Discretionary"] == pytest.approx(60_000.0)
    # Financials = JPM 20K + SPY 13% × 100K = 33K
    assert eq["Financials"] == pytest.approx(33_000.0)
    assert exp["equity_mv_total"] == pytest.approx(420_000.0)
    assert exp["approx_lookthrough"] is True  # SPY weights are estimates


def test_assignment_adjusted_view_adds_put_obligations_not_calls(smap):
    """'If all puts assign': MU $950P adds $95K to Info Tech, 2× GOOG $300P
    adds $60K to Comm Services. The short NVDA CALL adds NOTHING."""
    exp = compute_sector_exposure(_fixture_positions(), NLV, CFG_ON,
                                  smap=smap, api_key=None, cache_path=None)
    asg = exp["assignment_by_sector"]
    assert asg["Information Technology"] == pytest.approx(284_000.0 + 95_000.0)
    assert asg["Communication Services"] == pytest.approx(
        100_000.0 * 0.10 + 60_000.0)
    assert exp["put_obligation_total"] == pytest.approx(155_000.0)


def test_tech_correlated_includes_flagged_names_and_etf_slices(smap):
    """Net tech-correlated % counts Info Tech + Comm Services, the tech
    slices of SPY's look-through, AND the explicit tech_correlated flag on
    TSLA — but NOT JPM or SPY's non-tech slices."""
    exp = compute_sector_exposure(_fixture_positions(), NLV, CFG_ON,
                                  smap=smap, api_key=None, cache_path=None)
    # equity tech = NVDA 200K + SMH 50K + SPY (34K IT + 10K comm) + TSLA 50K
    assert exp["tech_correlated_equity_mv"] == pytest.approx(344_000.0)
    assert exp["tech_pct_nlv"] == pytest.approx(34.4)
    # assignment adds MU 95K + GOOG 60K (both tech)
    assert exp["tech_assignment_pct_nlv"] == pytest.approx(49.9)


# ── (b) Unclassified fail-closed ─────────────────────────────────────────


def test_unclassified_fail_closed_never_guessed_never_tech(smap):
    """A ticker with no curated mapping and no FMP key buckets Unclassified
    — surfaced by name, never guessed a sector, never counted as tech."""
    positions = [
        {"symbol": "ZZZZ", "assetType": "EQUITY", "marketValue": 10_000.0},
        {"symbol": "NVDA", "assetType": "EQUITY", "marketValue": 10_000.0},
    ]
    exp = compute_sector_exposure(positions, NLV, CFG_ON,
                                  smap=smap, api_key=None, cache_path=None)
    assert exp["equity_by_sector"][UNCLASSIFIED] == pytest.approx(10_000.0)
    assert exp["unclassified_tickers"] == ["ZZZZ"]
    assert exp["tech_correlated_equity_mv"] == pytest.approx(10_000.0)  # NVDA only


def test_unclassified_never_fires_the_cap_flag(smap):
    """A huge Unclassified bucket is a DATA gap, not a sector breach — it
    must never appear in over_cap (rule #19: no fabricated sector read)."""
    positions = [
        {"symbol": "ZZZZ", "assetType": "EQUITY", "marketValue": 500_000.0},
    ]
    exp = compute_sector_exposure(positions, NLV, CFG_ON,
                                  smap=smap, api_key=None, cache_path=None)
    assert exp["over_cap"] == []


# ── (c) cap flag ─────────────────────────────────────────────────────────


def test_cap_flag_fires_over_35_with_measured_numbers(smap):
    """NVDA bumped to $300K puts Info Tech equity at 38.4% NLV — over the
    35% cap; the flag carries BOTH the measured % and the assignment-
    adjusted % (47.9% with the MU put)."""
    exp = compute_sector_exposure(_fixture_positions(nvda_mv=300_000.0), NLV,
                                  CFG_ON, smap=smap, api_key=None,
                                  cache_path=None)
    assert len(exp["over_cap"]) == 1
    oc = exp["over_cap"][0]
    assert oc["sector"] == "Information Technology"
    assert oc["pct_nlv"] == pytest.approx(38.4)
    assert oc["assignment_pct_nlv"] == pytest.approx(47.9)


def test_cap_flag_silent_under_cap(smap):
    """At $200K NVDA, Info Tech equity is 28.4% NLV — under the 35% cap →
    no over_cap entry, and the panel summary says 'within the 35% cap'."""
    exp = compute_sector_exposure(_fixture_positions(), NLV, CFG_ON,
                                  smap=smap, api_key=None, cache_path=None)
    assert exp["over_cap"] == []
    assert "within the 35% cap" in summary_line(exp)


# ── (d) conviction adjustment ────────────────────────────────────────────


_PCTS = {"Information Technology": 62.0, "Financials": 2.0,
         "Industrials": 20.0}


def test_adjustment_minus_one_over_cap():
    """A new NVDA put when Info Tech already sits at 62% assignment-adjusted
    → −1 conviction with the measured note."""
    delta, note = sector_conviction_adjustment("NVDA", _PCTS, CFG_ON)
    assert delta == -1.0
    assert note == "🧭 sector Info Tech at 62% — over 35% cap"


def test_adjustment_plus_one_underweight():
    """A JPM candidate when Financials is at 2% → +1 diversification bonus."""
    delta, note = sector_conviction_adjustment("JPM", _PCTS, CFG_ON)
    assert delta == 1.0
    assert note == "🧭 diversifies — Financials at 2%"


def test_adjustment_plus_one_for_wholly_absent_sector():
    """A sector wholly absent from a MEASURED book (Health Care at 0%)
    diversifies maximally → +1."""
    delta, note = sector_conviction_adjustment("LLY", _PCTS, CFG_ON)
    assert delta == 1.0
    assert "Health Care at 0%" in note


def test_adjustment_zero_between_bands():
    """Industrials at 20% — between the 5% floor and 35% cap → 0, no note."""
    assert sector_conviction_adjustment("VRT", _PCTS, CFG_ON) == (0.0, None)


def test_adjustment_zero_unknown_sector_fail_closed():
    """Unknown ticker → no sector → (0, None): fail-closed, never guessed."""
    assert sector_conviction_adjustment("ZZZZ", _PCTS, CFG_ON) == (0.0, None)


def test_adjustment_zero_when_disabled_or_unmeasured():
    """Feature flag off → 0 (legacy byte-identical); empty pcts (book
    unmeasured) → 0 even when enabled."""
    assert sector_conviction_adjustment("NVDA", _PCTS, CFG_OFF) == (0.0, None)
    assert sector_conviction_adjustment("NVDA", {}, CFG_ON) == (0.0, None)
    assert sector_exposure_enabled(None) is False


def test_single_sector_etf_resolves_and_gets_penalized(smap):
    """A CSP candidate on SMH (100% Info Tech look-through) resolves to Info
    Tech and takes the −1 in an over-cap book; a multi-sector ETF (SPY)
    stays sector-less → 0 (fail-closed)."""
    assert sector_of("SMH", smap) == "Information Technology"
    assert sector_of("SPY", smap) is None
    delta, note = sector_conviction_adjustment("SMH", _PCTS, CFG_ON)
    assert delta == -1.0 and "Info Tech" in note
    assert sector_conviction_adjustment("SPY", _PCTS, CFG_ON) == (0.0, None)


# ── (e) digest summary line + length ─────────────────────────────────────


_REAL_FIXTURE = Path(__file__).parent / "fixtures" / "briefing_full_2026-08-06.md"


def _sector_section() -> str:
    exp = compute_sector_exposure(_fixture_positions(nvda_mv=300_000.0), NLV,
                                  CFG_ON, smap=load_sector_map(),
                                  api_key=None, cache_path=None)
    return "\n".join(render_sector_panel(exp))


def test_digest_keeps_sector_summary_line_and_stays_under_250():
    """The DIGEST's Health zone carries the one-line sector summary — 'It
    seems like I'm pretty heavily invested in tech' must be answerable from
    the short document — and the digest stays ≤ 250 lines on the real
    2026-08-06 fixture with the panel inserted."""
    full = _REAL_FIXTURE.read_text()
    lines = full.splitlines()
    # Insert the sector section right after the Health section (before the
    # next '## ' header that follows the Health header).
    h_idx = next(i for i, ln in enumerate(lines)
                 if ln.startswith("## ") and "Health" in ln)
    nxt = next(i for i in range(h_idx + 1, len(lines))
               if lines[i].startswith("## "))
    lines[nxt:nxt] = _sector_section().splitlines() + [""]
    full_with_sector = "\n".join(lines)

    digest = build_digest(full_with_sector,
                          config={"render": {"digest": True}},
                          extras={"date": "2026-08-06"})
    dlines = digest.splitlines()
    assert len(dlines) <= 250
    summary = [ln for ln in dlines if ln.startswith("**🧭")]
    assert len(summary) == 1
    assert "Net tech-correlated" in summary[0]
    assert "over the 35% sector cap" in summary[0]
    # The full table stays OUT of the digest (only the summary line is kept).
    assert "| Sector | Equity MV |" not in digest


def test_digest_without_sector_section_unchanged():
    """No sector panel in the full render → no 🧭 line in the digest (never
    fabricated)."""
    digest = build_digest(_REAL_FIXTURE.read_text(),
                          config={"render": {"digest": True}},
                          extras={"date": "2026-08-06"})
    assert not any(ln.startswith("**🧭") for ln in digest.splitlines())


# ── (f) red-flag entry ───────────────────────────────────────────────────


def _red_flags_for(exposure):
    return compute_red_flags(
        snapshot_data={"balance": {"accountValue": NLV, "cash": 100_000.0},
                       "positions": []},
        analytics={"sector_exposure": exposure},
        options_reviews=[],
        equity_reviews=[],
        capital_plan=None,
        recommendations_list=[],
    )


def test_red_flag_renders_measured_and_assignment_adjusted_pcts(smap):
    """Over-cap sector → a Red Flags & Priorities entry showing BOTH the
    measured % and the assignment-adjusted % (George: 'Is it the right
    thing?' needs the numbers, not vibes)."""
    exp = compute_sector_exposure(_fixture_positions(nvda_mv=300_000.0), NLV,
                                  CFG_ON, smap=smap, api_key=None,
                                  cache_path=None)
    flags = _red_flags_for(exp)
    sector_flags = [f for f in flags if "Sector concentration" in f.headline]
    assert len(sector_flags) == 1
    f = sector_flags[0]
    assert f.severity == "MEDIUM"          # 38.4% < 1.5 × 35%
    assert "Information Technology at 38%" in f.headline
    assert "assignment-adjusted 48%" in f.headline
    md = "\n".join(render_red_flags_md(flags))
    assert "38.4% of NLV" in md
    assert "47.9%" in md


def test_red_flag_high_severity_at_1_5x_cap():
    """A sector at ≥ 1.5× the cap escalates MEDIUM → HIGH."""
    exp = {"cap_pct": 35.0, "tech_pct_nlv": 80.0,
           "tech_assignment_pct_nlv": 95.0,
           "over_cap": [{"sector": "Information Technology",
                         "pct_nlv": 60.0, "assignment_pct_nlv": 75.0}]}
    flags = _red_flags_for(exp)
    f = next(f for f in flags if "Sector concentration" in f.headline)
    assert f.severity == "HIGH"


def test_red_flag_silent_under_cap(smap):
    """Under the cap → NO sector red flag (silent under)."""
    exp = compute_sector_exposure(_fixture_positions(), NLV, CFG_ON,
                                  smap=smap, api_key=None, cache_path=None)
    flags = _red_flags_for(exp)
    assert not any("Sector concentration" in f.headline for f in flags)


# ── panel rendering ──────────────────────────────────────────────────────


def test_panel_renders_table_flag_and_footers(smap):
    """The panel shows the look-through table with the ⚠ over-cap marker,
    the '~' approximation footer, and surfaces Unclassified names."""
    positions = _fixture_positions(nvda_mv=300_000.0) + [
        {"symbol": "ZZZZ", "assetType": "EQUITY", "marketValue": 5_000.0}]
    exp = compute_sector_exposure(positions, NLV, CFG_ON, smap=smap,
                                  api_key=None, cache_path=None)
    md = "\n".join(render_sector_panel(exp))
    assert "## 🧭 Sector Exposure (look-through)" in md
    assert "| Information Technology ⚠ |" in md
    assert "approximate index sector weights" in md
    assert "Unclassified: ZZZZ" in md
    assert "never guessed" in md


def test_panel_empty_exposure_renders_nothing():
    """No measured book → no panel (fail-open, never a fabricated table)."""
    assert render_sector_panel(None) == []
    assert render_sector_panel({"equity_by_sector": {}}) == []


# ── candidate-card note (Task B surface wiring) ──────────────────────────


def test_candidate_card_carries_diversification_note():
    """A JPM candidate card in a tech-heavy book shows the 🧭 diversifies
    note beside the CP/MV bonus notes."""
    from analysis import rsi_discipline
    from steps.candidate_research import _format_card
    r = {"ticker": "JPM", "spot": 300.0, "verdict": "CSP ENTRY",
         "rsi_14": 45.0, "iv_rank": 60.0, "drawdown_pct": 10.0,
         "third_party_rec": "BUY", "rating_tier": 3,
         "csp_entry": {"strike": 280, "expiration": "2026-10-16",
                       "dte": 70, "mid": 4.0, "bid": 3.9, "ask": 4.1}}
    card = "\n".join(_format_card(
        r, {}, set(), rsi_discipline.load_thresholds(None),
        config=CFG_ON, sector_pcts=_PCTS))
    assert "🧭 diversifies — Financials at 2%" in card
    assert "Entry (CSP):" in card           # ticket untouched — conviction only


# ── rotation-playbook Phase-2 wiring ─────────────────────────────────────


def test_playbook_phase2_applies_sector_penalty_beside_other_bonuses():
    """George: 'It seems like I'm pretty heavily invested in tech.' — a new
    CRM (Info Tech) put in a book whose assignment-adjusted Info Tech slice
    is already over the 35% cap scores exactly −1 vs the flags-off run, with
    the 🧭 note in setup_flags. Flags-off stays byte-identical (score delta
    only when enabled)."""
    from datetime import date as _date
    from analysis.rotation_playbook import compute_playbook

    held = [{"underlying": "GOOG", "type": "PUT", "strike": 325.0,
             "expiration": "2026-08-21", "qty": -1, "entry_price": 7.9447,
             "current_mid": 4.45, "days_to_expiry": 45}]
    cands = [{"kind": "SCOUT_CSP", "ticker": "CRM", "strike": 150.0,
              "expiration": "2026-08-14", "dte": 38, "premium": 2.40}]
    recs = {"CRM": {"rating_tier": 3, "conviction": "High", "age_days": 5,
                    "recommendation": "BUY"}}
    an = {"nlv": 1_000_000.0,
          "earnings_calendar": {"CRM": "2027-06-30"},
          "snapshot_data": {"positions": [
              {"symbol": "NVDA", "assetType": "EQUITY",
               "marketValue": 400_000.0}]}}

    def _run(config):
        pb = compute_playbook(held, cands, recs, set(), an, config,
                              today=_date(2026, 7, 7))
        assert pb is not None
        return next(c for c in pb.opens if c.ticker == "CRM")

    base = _run({})
    adjusted = _run(dict(CFG_ON))
    assert adjusted.conviction_score == pytest.approx(
        base.conviction_score - 1.0)
    assert any("🧭 sector Info Tech at 40%" in f
               for f in adjusted.setup_flags)
    assert not any("🧭" in f for f in base.setup_flags)
