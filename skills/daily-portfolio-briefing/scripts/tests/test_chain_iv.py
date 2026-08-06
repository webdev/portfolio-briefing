"""Task #43 — true chain-implied vol (analysis/chain_iv.py).

Motivation, pinned throughout: the legacy "IV rank" is a 252-day
REALIZED-vol percentile — backward-looking, so it spikes AFTER big moves.
The AMZN LONG-DATED CSP card (2026-07-31) claimed "IV rank 100 = fat
premium" while the DELIVERED premium was 4% annualized: the +13.7%
earnings gap itself pinned the realized-vol rank at 100 while the actual
implied premium had crushed post-print. These tests pin the replacement:
TRUE ATM implied vol measured from the chains fetched this cycle, a true
IV rank vs that measure's own rolling history, 25Δ skew + term-structure
signals, and honest labeling everywhere (RVrank for the proxy — never
"IV rank").
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import chain_iv  # noqa: E402


# ── Fixture builders ──────────────────────────────────────────────────────

STRIKES = [80.0, 85.0, 90.0, 92.5, 95.0, 97.5, 100.0,
           102.5, 105.0, 107.5, 110.0, 115.0, 120.0]


def _chain(expiration="2026-09-04", iv=0.30, put_ivs=None, call_ivs=None,
           strikes=None, underlying="AMZN"):
    """Synthetic yfinance-shaped chain (no delta fields — like the real
    snapshot chains)."""
    strikes = strikes or STRIKES
    put_ivs = put_ivs or {}
    call_ivs = call_ivs or {}
    calls = [{"strike": k, "impliedVolatility": call_ivs.get(k, iv)}
             for k in strikes]
    puts = [{"strike": k, "impliedVolatility": put_ivs.get(k, iv)}
            for k in strikes]
    return {"underlying": underlying, "expiration": expiration,
            "calls": calls, "puts": puts}


AS_OF = date(2026, 8, 6)


# ── atm_iv ────────────────────────────────────────────────────────────────


def test_atm_iv_from_fixture_chain():
    """ATM IV = mid-IV of the strikes bracketing spot. A flat-0.30 chain at
    spot 100 must read exactly 0.30 — the number the AMZN card should have
    shown instead of the realized-vol proxy's 100th percentile."""
    assert chain_iv.atm_iv(_chain(), 100.0) == pytest.approx(0.30)


def test_atm_iv_interpolates_between_bracketing_strikes():
    """Spot between listed strikes → linear interpolation, not a grab of
    whichever side happened to sort first."""
    ch = _chain(call_ivs={100.0: 0.20, 102.5: 0.40},
                put_ivs={100.0: 0.20, 102.5: 0.40})
    assert chain_iv.atm_iv(ch, 101.25) == pytest.approx(0.30)


def test_atm_iv_rejects_insane_values():
    """Sanity bounds 0.05 < iv < 5.0: a chain whose IV fields are data
    artifacts (0.001 placeholder, 900% vol) yields None — never a
    fabricated number (rule #19)."""
    bad = _chain(iv=0.001)
    assert chain_iv.atm_iv(bad, 100.0) is None
    bad2 = _chain(iv=9.0)
    assert chain_iv.atm_iv(bad2, 100.0) is None
    # Mixed: insane strikes are discarded, sane neighbors still bracket.
    mixed = _chain(call_ivs={100.0: 9.0}, put_ivs={100.0: 9.0})
    got = chain_iv.atm_iv(mixed, 100.0)
    assert got is not None and 0.05 < got < 5.0


def test_atm_iv_refuses_far_one_sided_chain():
    """When every listed strike sits >10% from spot on one side, there is no
    ATM read — return None rather than pretend a far wing is ATM."""
    ch = _chain(strikes=[50.0, 55.0, 60.0])
    assert chain_iv.atm_iv(ch, 100.0) is None


def test_atm_iv_missing_inputs_fail_closed():
    assert chain_iv.atm_iv(None, 100.0) is None
    assert chain_iv.atm_iv(_chain(), None) is None
    assert chain_iv.atm_iv({"calls": [], "puts": []}, 100.0) is None


# ── skew / term / 30d interpolation ──────────────────────────────────────


def test_skew_25d_moneyness_proxy_math():
    """25Δ skew via the documented moneyness proxy (no deltas on yfinance
    chains): K ≈ S·exp(∓0.675·σ·√T). At spot 100, σ_atm 0.40, DTE 30 the
    proxy targets ≈92.6 put / ≈108.0 call — nearest listed strikes are 92.5
    and 107.5. Puts bid at 0.50 vs calls at 0.35 → skew +0.15."""
    ch = _chain(iv=0.40, put_ivs={92.5: 0.50}, call_ivs={107.5: 0.35})
    skew = chain_iv.skew_25d(ch, 100.0, atm=0.40, dte=30)
    assert skew == pytest.approx(0.15)


def test_skew_25d_prefers_chain_deltas_when_present():
    """A chain that carries real deltas uses them (no proxy): the |Δ|≈0.25
    put at IV 0.60 minus the |Δ|≈0.25 call at IV 0.40 = +0.20."""
    ch = {
        "puts": [{"strike": 90.0, "impliedVolatility": 0.60, "delta": -0.26},
                 {"strike": 80.0, "impliedVolatility": 0.90, "delta": -0.10}],
        "calls": [{"strike": 110.0, "impliedVolatility": 0.40, "delta": 0.24},
                  {"strike": 120.0, "impliedVolatility": 0.70, "delta": 0.08}],
    }
    assert chain_iv.skew_25d(ch, 100.0, atm=0.50, dte=30) == pytest.approx(0.20)


def test_iv_metrics_interpolates_atm_to_30_dte():
    """Front expiry (10 DTE) at 0.20, back (50 DTE) at 0.40 → the 30-DTE
    interpolated ATM IV is 0.30."""
    chains = {
        "2026-08-16": _chain("2026-08-16", iv=0.20),   # 10 DTE
        "2026-09-25": _chain("2026-09-25", iv=0.40),   # 50 DTE
    }
    m = chain_iv.iv_metrics(chains, 100.0, as_of=AS_OF)
    assert m["atm_iv_30d"] == pytest.approx(0.30)


def test_iv_metrics_term_slope_positive_when_front_stressed():
    """Front ATM 0.50 > back ATM 0.30 → term_slope +0.20 — the inverted /
    front-stressed shape (wheelhouz signal #8)."""
    chains = {
        "2026-08-16": _chain("2026-08-16", iv=0.50),
        "2026-11-20": _chain("2026-11-20", iv=0.30),
    }
    m = chain_iv.iv_metrics(chains, 100.0, as_of=AS_OF)
    assert m["term_slope"] == pytest.approx(0.20)
    # Normal upward term structure → negative slope, no inversion.
    chains2 = {
        "2026-08-16": _chain("2026-08-16", iv=0.25),
        "2026-11-20": _chain("2026-11-20", iv=0.35),
    }
    m2 = chain_iv.iv_metrics(chains2, 100.0, as_of=AS_OF)
    assert m2["term_slope"] == pytest.approx(-0.10)


def test_iv_metrics_single_expiry_no_term_slope():
    m = chain_iv.iv_metrics({"2026-09-04": _chain()}, 100.0, as_of=AS_OF)
    assert m["atm_iv_30d"] == pytest.approx(0.30)
    assert m["term_slope"] is None


def test_compute_metrics_for_snapshot_fails_closed_without_spot():
    """No spot for an underlying → no metrics for it (never fabricate)."""
    chains = {"AMZN_2026-09-04": _chain()}
    assert chain_iv.compute_metrics_for_snapshot(chains, {}, AS_OF) == {}
    out = chain_iv.compute_metrics_for_snapshot(
        chains, {"AMZN": 100.0}, AS_OF)
    assert out["AMZN"]["atm_iv_30d"] == pytest.approx(0.30)


# ── History / true rank ───────────────────────────────────────────────────


def _seed_history(n_days, iv_fn=lambda i: 0.20 + i * 0.005,
                  skew_fn=None, start=date(2026, 5, 1)):
    from datetime import timedelta
    h = chain_iv.load_history(Path("/nonexistent"))
    for i in range(n_days):
        d = (start + timedelta(days=i)).isoformat()
        entry = {"AMZN": {"atm_iv_30d": iv_fn(i)}}
        if skew_fn is not None:
            entry["AMZN"]["skew_25d"] = skew_fn(i)
        chain_iv.update_history(h, d, entry)
    return h


def test_rank_building_history_below_min_days():
    """While history < min_history_days (20) the TRUE rank is n/a — the
    label must say 'building history, Nd', never render an unearned rank
    (the whole point is not repeating the proxy's overconfidence)."""
    h = _seed_history(5)
    m = {"AMZN": {"atm_iv_30d": 0.30, "skew_25d": 0.01, "term_slope": -0.02}}
    chain_iv.attach_ranks(m, h)
    assert m["AMZN"]["iv_rank"] is None
    assert m["AMZN"]["history_days"] == 5
    label = chain_iv.iv_label(m["AMZN"], 62.0)
    assert "building history, 5d" in label
    assert "RVrank 62" in label
    assert "IVrank n/a" in label


def test_rank_with_sufficient_history():
    """30 ascending observations, today's value at the top → rank 100; at
    the bottom → low rank. The AMZN case inverted: post-gap the TRUE rank
    would be LOW (implied crushed) while the realized proxy said 100."""
    h = _seed_history(30)
    top = {"AMZN": {"atm_iv_30d": 0.99}}
    chain_iv.update_history(h, "2026-08-06", {"AMZN": {"atm_iv_30d": 0.99}})
    chain_iv.attach_ranks(top, h)
    assert top["AMZN"]["iv_rank"] == pytest.approx(100.0)
    low = {"AMZN": {"atm_iv_30d": 0.01}}
    chain_iv.attach_ranks(low, h)
    assert low["AMZN"]["iv_rank"] is not None
    assert low["AMZN"]["iv_rank"] < 10.0


def test_rolling_limit_252_entries():
    h = _seed_history(300)
    assert len(h["tickers"]["AMZN"]) == chain_iv.ROLLING_LIMIT


def test_skew_blowout_flag_vs_own_trailing_mean():
    """Skew blowout = today's 25Δ skew > 2σ above ITS OWN trailing mean
    (wheelhouz signal #7, flag-only). Trailing skews alternate 0.01/0.02;
    today's 0.05 is far past 2σ → flag. A today-at-mean skew → no flag."""
    h = _seed_history(30, skew_fn=lambda i: 0.01 if i % 2 else 0.02)
    hot = {"AMZN": {"atm_iv_30d": 0.30, "skew_25d": 0.05,
                    "term_slope": 0.01}}
    chain_iv.attach_ranks(hot, h)
    assert hot["AMZN"]["skew_blowout"] is True
    assert hot["AMZN"]["skew_z"] > 2.0
    assert hot["AMZN"]["term_inversion"] is True   # slope > 0
    calm = {"AMZN": {"atm_iv_30d": 0.30, "skew_25d": 0.015,
                     "term_slope": -0.02}}
    chain_iv.attach_ranks(calm, h)
    assert calm["AMZN"]["skew_blowout"] is False
    assert calm["AMZN"]["term_inversion"] is False


# ── Backfill ──────────────────────────────────────────────────────────────


def _write_snapshot_day(root: Path, day: str, iv=0.30):
    d = root / day
    (d / "chains").mkdir(parents=True)
    (d / "technicals.json").write_text(json.dumps({"AMZN": {"spot": 100.0}}))
    (d / "quotes.json").write_text(json.dumps({"AMZN": {"last": 100.0}}))
    ch = _chain("2026-12-18", iv=iv)
    (d / "chains" / "AMZN_2026-12-18.json").write_text(json.dumps(ch))


def test_backfill_seeds_history_and_is_idempotent(tmp_path):
    """Backfill iterates stored snapshot chains/ dirs to seed the history
    (the ~60 days of chains already on disk mean the rank isn't starting
    from zero). Re-running must be a no-op: 0 new dates, identical file."""
    root = tmp_path / "briefing_snapshots"
    _write_snapshot_day(root, "2026-08-01", iv=0.30)
    _write_snapshot_day(root, "2026-08-02", iv=0.35)
    hist_path = tmp_path / "chain_iv_history.json"

    seeded = chain_iv.backfill_history(root, hist_path)
    assert seeded == 2
    h = chain_iv.load_history(hist_path)
    assert h["tickers"]["AMZN"]["2026-08-01"]["atm_iv_30d"] == pytest.approx(0.30)
    assert h["tickers"]["AMZN"]["2026-08-02"]["atm_iv_30d"] == pytest.approx(0.35)
    first_bytes = hist_path.read_text()

    # Idempotence: second run seeds nothing and changes nothing.
    assert chain_iv.backfill_history(root, hist_path) == 0
    assert hist_path.read_text() == first_bytes


def test_backfill_skips_non_date_dirs(tmp_path):
    root = tmp_path / "briefing_snapshots"
    _write_snapshot_day(root, "2026-08-01")
    (root / "2026-06-30.test" / "chains").mkdir(parents=True)
    (root / "cache").mkdir()
    assert chain_iv.backfill_history(root, tmp_path / "h.json") == 1


# ── Labels / annotation / preference order ───────────────────────────────


def test_iv_label_rvrank_fallback_when_chains_absent():
    """No chain IV for a name → the proxy renders as a LABELED companion:
    'RVrank 55 (realized-vol proxy)' — never as 'IV rank 55'."""
    assert chain_iv.iv_label(None, 55.0) == "RVrank 55 (realized-vol proxy)"
    assert chain_iv.iv_label(None, None) == "IV n/a"


def test_iv_label_true_rank():
    m = {"atm_iv_30d": 0.34, "iv_rank": 62.0, "history_days": 40}
    assert chain_iv.iv_label(m, 100.0) == "IV 34% · IVrank 62 · RVrank 100"
    assert chain_iv.iv_label(m, None) == "IV 34% · IVrank 62"


def test_annotate_rewrites_legacy_token_with_true_iv():
    """The AMZN card's 'IV rank 100' (realized-vol proxy pinned by the gap)
    must render honestly: 'IV 31% · IVrank 34 · RVrank 100' when the true
    chain measure exists — the user sees implied crushed even though the
    proxy screams 100."""
    md = "**AMZN** $240P — RSI 45 · IV rank 100 · drawdown 10%"
    civ = {"AMZN": {"atm_iv_30d": 0.31, "iv_rank": 34.0, "history_days": 40}}
    out, stats = chain_iv.annotate_briefing(md, civ, {"AMZN": 100.0})
    assert "IV 31% · IVrank 34 · RVrank 100" in out
    assert "IV rank 100" not in out
    assert stats["true"] == 1


def test_annotate_rvrank_fallback_when_no_chain_for_ticker():
    """A name without chains this cycle keeps the proxy number but the
    label says what it is."""
    md = "**ZS** $135P — RSI 44 · IV rank 62 · drawdown 56%"
    out, stats = chain_iv.annotate_briefing(md, {}, {"ZS": 62.0})
    assert "RVrank 62 (realized-vol proxy)" in out
    assert "IV rank 62" not in out
    assert stats["rv_only"] == 1


def test_annotate_building_history_render():
    md = "- **VRT** — pullback · IV rank 80"
    civ = {"VRT": {"atm_iv_30d": 0.52, "iv_rank": None, "history_days": 12}}
    out, stats = chain_iv.annotate_briefing(md, civ, {"VRT": 80.0})
    assert "IV 52% (IVrank n/a — building history, 12d) · RVrank 80" in out
    assert stats["building"] == 1


def test_annotate_appends_signal_flags():
    """Skew-blowout / term-inversion surface as informational tags on the
    card (wheelhouz #7/#8 — flags, not forecasts, no autonomous action)."""
    md = "**NVDA** — CSP entry · IV rank 70"
    civ = {"NVDA": {"atm_iv_30d": 0.45, "iv_rank": 88.0, "history_days": 40,
                    "skew_25d": 0.06, "skew_z": 2.7, "skew_blowout": True,
                    "term_slope": 0.03, "term_inversion": True}}
    out, _ = chain_iv.annotate_briefing(md, civ, {"NVDA": 70.0})
    assert "🌊 skew blowout" in out
    assert "term inversion (front-stressed)" in out


def test_annotate_skips_footers_transitions_and_is_idempotent():
    """Italic transparency footers stay verbatim; 'IV rank 61→69'
    transition notes are not tokens; a second pass changes nothing."""
    md = "\n".join([
        "_Verdict changed vs yesterday: … IV rank 61→69._",
        "**QCOM** — roll · IV rank 69",
    ])
    out, _ = chain_iv.annotate_briefing(md, {}, {"QCOM": 69.0})
    assert "IV rank 61→69._" in out                    # footer untouched
    assert "RVrank 69 (realized-vol proxy)" in out
    again, stats2 = chain_iv.annotate_briefing(out, {}, {"QCOM": 69.0})
    assert again == out
    assert not any(stats2.values())


def test_effective_iv_rank_preference_order():
    """Gate battery / playbook preference: TRUE chain rank → labeled RV
    fallback → none. This is the order that stops 'iv_rank ≥ 60' gates
    from firing on the AMZN post-gap proxy artifact."""
    civ = {"AMZN": {"atm_iv_30d": 0.31, "iv_rank": 34.0}}
    assert chain_iv.effective_iv_rank("AMZN", civ, 100.0) == (34.0, "chain")
    # Building history (rank None) → fall back to RV, labeled by caller.
    civ2 = {"AMZN": {"atm_iv_30d": 0.31, "iv_rank": None}}
    assert chain_iv.effective_iv_rank("AMZN", civ2, 100.0) == (100.0, "rv")
    assert chain_iv.effective_iv_rank("ZS", civ, 62.0) == (62.0, "rv")
    assert chain_iv.effective_iv_rank("ZS", {}, None) == (None, "none")


# ── Gate-battery integration (rotation playbook) ─────────────────────────


def _pb_with_chain_iv(chain_iv_entry=None, rv_rank=100.0):
    """AMZN-shaped playbook run: thin-ish premium CRM candidate carrying the
    realized-vol proxy rank 100, optionally with a TRUE chain-IV entry."""
    from analysis.rotation_playbook import compute_playbook
    held = [
        {"underlying": "GOOG", "type": "PUT", "strike": 325.0,
         "expiration": "2026-08-21", "qty": -1, "entry_price": 7.9447,
         "current_mid": 4.45, "days_to_expiry": 18},
        {"underlying": "MSFT", "type": "PUT", "strike": 350,
         "expiration": "2026-09-18", "qty": -1, "entry_price": 23.4244,
         "current_mid": 9.525, "days_to_expiry": 46},
    ]
    cands = [{"kind": "SCOUT_CSP", "ticker": "CRM", "strike": 100.0,
              "expiration": "2026-09-02", "dte": 30, "premium": 1.20,
              "iv_rank": rv_rank, "rsi_14": 45.0}]   # 14.6% ann — thin-ish
    sd = {"balance": {"accountValue": 1_000_000.0, "cash": 300_000.0}}
    if chain_iv_entry is not None:
        sd["chain_iv"] = {"CRM": chain_iv_entry}
    an = {"nlv": 1_000_000.0, "snapshot_data": sd, "technicals": {},
          "earnings_calendar": {"CRM": "2027-06-30"}}
    recs = {"CRM": {"rating_tier": 3, "conviction": "High", "age_days": 3,
                    "recommendation": "BUY"}}
    return compute_playbook(held, cands, recs, set(), an, {},
                            today=date(2026, 8, 3))


def test_gate_battery_prefers_true_rank_no_gap_inflation_theater():
    """With TRUE chain IV present (rank 30 — implied actually crushed), the
    playbook must use IT for every iv_rank condition: no +1 IV bonus, no
    'IV rank 100' token, and no '⚠ IV rank gap-inflated' apology either —
    the honest number needs no apology. Score = 22.5 base + 2 pullback."""
    pb = _pb_with_chain_iv({"atm_iv_30d": 0.31, "iv_rank": 30.0,
                            "history_days": 40})
    (o,) = pb.opens
    assert o.conviction_score == pytest.approx(24.5)
    assert "IV rank 100" not in o.setup_flags
    assert "IVrank(true) 30" not in o.setup_flags     # < 85 → no flag at all
    assert "⚠ IV rank gap-inflated" not in o.setup_flags


def test_gate_battery_rv_fallback_keeps_legacy_honesty_path():
    """Without chain IV the legacy path is byte-identical: proxy rank 100 +
    delivered-thin → gap-inflated rewrite + bonus revoked (the original
    rule-#43 AMZN fix stays as belt-and-suspenders)."""
    pb = _pb_with_chain_iv(None)
    (o,) = pb.opens
    assert o.conviction_score == pytest.approx(24.5)  # +1 granted then revoked
    assert "⚠ IV rank gap-inflated" in o.setup_flags
    assert "IV rank 100" not in o.setup_flags


def test_gate_battery_true_rank_85_gets_labeled_token():
    """A genuinely rich TRUE rank ≥85 earns the setup flag WITH the source
    label ('IVrank(true) 90'), plus the +1 conviction bonus — and no
    gap-inflation rewrite (true IV is measured, not gap-inflated)."""
    pb = _pb_with_chain_iv({"atm_iv_30d": 0.55, "iv_rank": 90.0,
                            "history_days": 40})
    (o,) = pb.opens
    assert "IVrank(true) 90" in o.setup_flags
    assert "⚠ IV rank gap-inflated" not in o.setup_flags
    assert o.conviction_score == pytest.approx(25.5)  # bonus kept


# ── Vol Surface panel ────────────────────────────────────────────────────


def test_vol_surface_renders_top_readings():
    civ = {
        "AMZN": {"atm_iv_30d": 0.31, "iv_rank": 34.0, "history_days": 40,
                 "skew_25d": 0.02, "skew_z": 0.5, "skew_blowout": False,
                 "term_slope": -0.02, "term_inversion": False},
        "NVDA": {"atm_iv_30d": 0.45, "iv_rank": 88.0, "history_days": 40,
                 "skew_25d": 0.07, "skew_z": 2.6, "skew_blowout": True,
                 "term_slope": 0.03, "term_inversion": True},
    }
    out = "\n".join(chain_iv.render_vol_surface(civ, None,
                                                rv_ranks={"AMZN": 100.0}))
    assert "Vol Surface — chain-implied (true IV)" in out
    assert out.index("NVDA") < out.index("AMZN")   # flagged names first
    assert "🌊 skew blowout" in out
    assert "term inversion" in out
    assert "IV 31% · IVrank 34 · RVrank 100" in out
    assert "Flags, not forecasts" in out


def test_vol_surface_empty_map_renders_nothing():
    """No chain IV this cycle → no section, nothing fabricated."""
    assert chain_iv.render_vol_surface({}, None) == []
    assert chain_iv.render_vol_surface(None, None) == []


# ── Config / paths ───────────────────────────────────────────────────────


def test_config_defaults_and_overrides():
    cfg = chain_iv.load_chain_iv_config(None)
    assert cfg["enabled"] is True
    assert cfg["min_history_days"] == 20
    assert cfg["skew_sigma_threshold"] == 2.0
    cfg2 = chain_iv.load_chain_iv_config(
        {"chain_iv": {"enabled": False, "min_history_days": 5,
                      "skew_sigma_threshold": 1.5,
                      "history_path": "/tmp/x.json"}})
    assert cfg2["enabled"] is False
    assert cfg2["min_history_days"] == 5
    assert cfg2["skew_sigma_threshold"] == 1.5
    assert cfg2["history_path"] == "/tmp/x.json"


def test_resolve_history_path_default_lands_in_state_dir(tmp_path):
    snap = tmp_path / "skill" / "state" / "briefing_snapshots" / "2026-08-06"
    snap.mkdir(parents=True)
    p = chain_iv.resolve_history_path(None, snap)
    assert p == tmp_path / "skill" / "state" / "chain_iv_history.json"
    p2 = chain_iv.resolve_history_path(
        {"chain_iv": {"history_path": "state/chain_iv_history.json"}}, snap)
    assert p2 == tmp_path / "skill" / "state" / "chain_iv_history.json"
