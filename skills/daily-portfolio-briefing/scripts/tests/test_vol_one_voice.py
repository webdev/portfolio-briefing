"""Vol display one-voice — every card shows the vol the grade scored.

George (2026-08-12): "Yes, we absolutely need to fix the right
recommendations for both CSPs and CCs so that recommendations are A or B,
not D, because I'm very much relying on it." The rec_grade_audit found 61%
of CSP recs below the true-IV vol floor while cards displayed flattering
RVrank proxies (MU shown 'RVrank 84' when true chain IVr was 15).

These tests pin: wherever a recommendation/candidate card displays a
volatility rank it displays the SAME effective vol the setup grade used
(true chain IVr preferred, labeled 'IVr'; RVr proxy only when chain IV
history is unavailable, labeled 'RVr (realized-vol proxy)'); a ≥30-point
divergence between the two is stated explicitly; and no card renders two
vol numbers unexplained.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import chain_iv, rsi_discipline  # noqa: E402

_RSI_TH = rsi_discipline.load_thresholds(None)
CFG_ON = {"setup_grade": {"enabled": True}}

# The audit's MU case: true chain IVr 15, realized-vol proxy 84.
_MU_CHAIN_IV = {"MU": {"atm_iv_30d": 0.30, "iv_rank": 15.0,
                       "history_days": 40}}

_DIVERGENCE = ("IVr 15 (true chain) · RVr 84 proxy diverges — premium "
               "thinner than the proxy suggests")


# ── The one formatter ────────────────────────────────────────────────────


def test_format_vol_display_true_chain_preferred():
    assert chain_iv.format_vol_display(62.0, "chain") == "IVr 62"
    assert chain_iv.format_vol_display(62.0, "chain", rv_rank=70.0) == "IVr 62"


def test_format_vol_display_divergence_stated_explicitly():
    """The MU dressing case (audit 2026-08-12): shown 'RVrank 84' when
    true chain IVr was 15 — the divergence must be explicit."""
    assert chain_iv.format_vol_display(15.0, "chain", rv_rank=84.0) == \
        _DIVERGENCE


def test_format_vol_display_richer_direction():
    out = chain_iv.format_vol_display(84.0, "chain", rv_rank=15.0)
    assert out == ("IVr 84 (true chain) · RVr 15 proxy diverges — premium "
                   "richer than the proxy suggests")


def test_format_vol_display_rv_proxy_labeled():
    assert chain_iv.format_vol_display(84.0, "rv") == \
        "RVr 84 (realized-vol proxy)"
    assert chain_iv.format_vol_display(None, "none") == "IV n/a"


def test_vol_display_for_resolves_effective_rank():
    """vol_display_for shows the SAME effective vol effective_iv_rank
    resolves (the value the setup grade scores) — one voice."""
    assert chain_iv.vol_display_for("MU", _MU_CHAIN_IV, 84.0) == _DIVERGENCE
    assert chain_iv.vol_display_for("MU", {}, 84.0) == \
        "RVr 84 (realized-vol proxy)"
    assert chain_iv.vol_display_for("MU", None, None) == "IV n/a"


def test_divergence_threshold_is_30_points():
    assert chain_iv.VOL_DIVERGENCE_PTS == 30.0
    # 29 points apart → no divergence note (single number).
    assert chain_iv.format_vol_display(55.0, "chain", rv_rank=84.0) == "IVr 55"
    # exactly 30 → note fires.
    assert "proxy diverges" in chain_iv.format_vol_display(
        54.0, "chain", rv_rank=84.0)


# ── Candidate cards (candidate_research) ─────────────────────────────────


def _scout_result(**kw):
    base = {
        "ticker": "MU", "spot": 100.0, "verdict": "CSP ENTRY",
        "rsi_14": 48.0, "iv_rank": 84.0, "drawdown_pct": 8.0,
        "sma_200": 90.0, "fivedayret_pct": -1.0,
        "third_party_rec": "BUY", "rating_tier": 3, "rationale": [],
        "days_to_earnings": 42, "earnings_date": "2026-09-23",
        "support_resistance": {"supports": [], "resistances": []},
        "csp_entry": {"strike": 95, "expiration": "2026-10-16",
                      "dte": 65, "mid": 3.1, "bid": 3.0, "ask": 3.2},
    }
    base.update(kw)
    return base


def test_candidate_card_shows_effective_vol_not_bare_proxy():
    """The MU card must never again read 'RVrank 84' beside a grade scored
    on true chain IVr 15 — the divergence renders on the metrics line."""
    from steps.candidate_research import _format_card
    card = "\n".join(_format_card(_scout_result(), {}, set(), _RSI_TH,
                                  config=CFG_ON, chain_iv_map=_MU_CHAIN_IV))
    assert _DIVERGENCE in card
    assert "IV rank 84" not in card
    # The grade line scores the same source (IVr 15 → thin ✗).
    assert "IVr 15 thin ✗" in card


def test_candidate_card_no_chain_map_keeps_labeled_proxy():
    from steps.candidate_research import _format_card
    card = "\n".join(_format_card(_scout_result(), {}, set(), _RSI_TH,
                                  config=CFG_ON, chain_iv_map=None))
    assert "RVr 84 (realized-vol proxy)" in card
    assert "IV rank 84" not in card


def test_candidate_card_never_two_unexplained_vol_numbers():
    """One-voice: any card carrying both a true and proxy number must
    carry the divergence explanation on the same line."""
    from steps.candidate_research import _format_card
    card = "\n".join(_format_card(_scout_result(), {}, set(), _RSI_TH,
                                  config=CFG_ON, chain_iv_map=_MU_CHAIN_IV))
    if "IVr 15" in card and "RVr 84" in card:
        assert "proxy diverges" in card


# ── When-To-Enter cards ──────────────────────────────────────────────────


def _wte_payload(results):
    return {
        "generated_at_iso": "2026-08-12T08:00:00",
        "themes": {"semis": {"name": "Semis", "group": "AI Buildout",
                             "anchors": [], "etfs": []}},
        "results_by_theme": {"semis": results},
    }


def test_when_to_enter_card_effective_vol():
    """Card metrics line AND the classify() Read prose both consume the
    effective vol (true chain IVr 15), so no bare 'IV rank 84' proxy
    survives anywhere on the card."""
    from steps.when_to_enter import render_when_to_enter_report
    md = render_when_to_enter_report(
        _wte_payload([_scout_result()]), config=CFG_ON, generated_at="t",
        chain_iv_map=_MU_CHAIN_IV)
    assert _DIVERGENCE in md
    assert "IV rank 84" not in md      # prose Read line uses the display too


def test_when_to_enter_without_map_keeps_labeled_proxy():
    from steps.when_to_enter import render_when_to_enter_report
    md = render_when_to_enter_report(
        _wte_payload([_scout_result()]), config=CFG_ON, generated_at="t")
    assert "RVr 84 (realized-vol proxy)" in md


# ── Scout pulse lines (thematic_research) ────────────────────────────────


def test_pulse_rich_premium_claims_read_effective_vol():
    """'rich premium' may only be claimed on the effective vol — MU's
    proxy 84 with true chain IVr 15 is NOT rich; the claim must drop."""
    from steps.thematic_research import _market_setup_read
    r = _scout_result()
    with_map = _market_setup_read(r, _MU_CHAIN_IV)
    assert "rich premium" not in with_map
    without_map = _market_setup_read(r, None)
    assert "RVr 84 (realized-vol proxy) (rich premium)" in without_map
    assert "IV rank 84" not in without_map


def test_pulse_action_read_rich_iv_uses_effective_vol():
    from steps.thematic_research import _action_read
    r = _scout_result(rsi_14=65.0)
    assert "rich IV" not in _action_read(r, _MU_CHAIN_IV)   # true IVr 15
    assert "rich IV" in _action_read(r, None)               # proxy 84


# ── Playbook rows ────────────────────────────────────────────────────────


def test_playbook_rich_flag_routes_through_formatter():
    """Covered in test_chain_iv (IVr / RVr labeled tokens) — pin here that
    the divergence form also lands in setup_flags when the proxy is ≥30
    points above the true rank ≥85."""
    from datetime import date
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
              "expiration": "2026-09-02", "dte": 30, "premium": 2.00,
              "iv_rank": 100.0, "rsi_14": 45.0}]
    sd = {"balance": {"accountValue": 1_000_000.0, "cash": 300_000.0},
          "chain_iv": {"CRM": {"atm_iv_30d": 0.55, "iv_rank": 60.0,
                               "history_days": 40}}}
    an = {"nlv": 1_000_000.0, "snapshot_data": sd, "technicals": {},
          "earnings_calendar": {"CRM": "2027-06-30"}}
    recs = {"CRM": {"rating_tier": 3, "conviction": "High", "age_days": 3,
                    "recommendation": "BUY"}}
    pb = compute_playbook(held, cands, recs, set(), an, {},
                          today=date(2026, 8, 3))
    (o,) = pb.opens
    # true 60 < 85 → no rich flag at all; no bare proxy token either.
    assert not any("IV rank 100" in f for f in o.setup_flags)
    assert not any(f.startswith("RVr 100") for f in o.setup_flags)
