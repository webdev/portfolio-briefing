"""BUG C (2026-08-17 briefing) — theme-stacking over-breadth: a held
mega-cap put blocked a pure-play candidate.

Observed (real 2026-08-17 briefing, Best Setups exclusions):

  "⏸ CGNX B (70) — excluded: ⛔ same-theme put already held — AMZN $245P
   (Robotics & Autonomy); stacking correlated assignment risk"

AMZN is a diversified mega-cap anchor appearing in many scout themes — an
AMZN put is not a robotics bet and must not block a robotics pure-play.
Pinned here:
  (a) a held put on an exempt (mega-cap/index) name never triggers the
      same-theme WAIT — default exemption = Tier A core union +
      SPY/VOO/QQQ (config: entry_algorithm.theme_stacking
      .exempt_held_tickers);
  (b) pure-play held names (SNDK, MU, WDC, …) still trigger;
  (c) the exemption applies ONLY to the held side — a mega-cap CANDIDATE
      is still checked against held pure-play puts;
  (d) explicit config list overrides the default entirely;
  (e) end-to-end: evaluate_entry no longer WAITs CGNX on a held AMZN put
      (real theme_universes.yaml — both are Robotics & Autonomy anchors).
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import entry_algorithm as ea  # noqa: E402
from analysis import theme_stacking as ts  # noqa: E402

AS_OF = date(2026, 8, 17)

THEME_CFG = {"entry_algorithm": {"theme_stacking": {"enabled": True,
                                                    "mode": "wait"}}}

# The observed shapes
AMZN_PUT = {"assetType": "OPTION", "type": "PUT", "underlying": "AMZN",
            "qty": -1.0, "strike": 245.0}
SNDK_PUT = {"assetType": "OPTION", "type": "PUT", "underlying": "SNDK",
            "qty": -1.0, "strike": 1230.0}

# Pure test map mirroring the real theme_universes.yaml memberships
TM = {
    "CGNX": {"Robotics & Autonomy"},
    "AMZN": {"Robotics & Autonomy", "AI Data Centers",
             "Memory & Storage"},
    "WDC": {"Memory & Storage"},
    "SNDK": {"Memory & Storage"},
}


# ── (a) held mega-cap exemption ───────────────────────────────────────────

def test_held_amzn_put_no_longer_blocks_cgnx():
    """The observed exclusion: "⏸ CGNX B (70) — excluded: ⛔ same-theme put
    already held — AMZN $245P (Robotics & Autonomy)". A held AMZN put is
    exempt by default (diversified mega-cap) — no theme hit."""
    assert ts.check_theme_stacking("CGNX", {"AMZN": [245.0]},
                                   theme_map=TM, config={}) is None


def test_default_exempt_set_contains_megacaps_and_indexes():
    ex = ts.exempt_held_tickers({})
    for t in ("GOOG", "GOOGL", "NVDA", "MSFT", "VRT", "ADBE", "AMZN",
              "META", "SPY", "VOO", "QQQ"):
        assert t in ex


def test_tier_a_core_from_config_drives_the_default():
    """With a configured Tier A core list, the exemption is core_union +
    the index names — NOT the hardcoded fallback (META held put on a
    shared theme still blocks when META is not Tier A in that config)."""
    cfg = {"position_tiers": {"tier_a_core": ["AMZN"]}}
    ex = ts.exempt_held_tickers(cfg)
    assert "AMZN" in ex and "SPY" in ex
    assert "META" not in ex
    tm = dict(TM, META={"Robotics & Autonomy"})
    hit = ts.check_theme_stacking("CGNX", {"META": [600.0]},
                                  theme_map=tm, config=cfg)
    assert hit is not None and "META $600P" in hit["detail"]


# ── (b) pure-play held names still block ──────────────────────────────────

def test_held_sndk_put_still_blocks_wdc():
    """The original rule-#49 case is untouched: SNDK is a pure-play — a
    held SNDK $1230P still WAITs a new WDC CSP."""
    hit = ts.check_theme_stacking("WDC", {"SNDK": [1230.0]},
                                  theme_map=TM, config={})
    assert hit is not None
    assert "SNDK $1230P" in hit["detail"]
    assert "Memory & Storage" in hit["detail"]


# ── (c) candidate side unaffected ─────────────────────────────────────────

def test_megacap_candidate_still_checked_against_pure_play_held():
    """The exemption is HELD-side only: a NEW AMZN CSP while a pure-play
    SNDK put is held (shared Memory & Storage theme) still triggers."""
    hit = ts.check_theme_stacking("AMZN", {"SNDK": [1230.0]},
                                  theme_map=TM, config={})
    assert hit is not None
    assert "SNDK $1230P" in hit["detail"]


# ── (d) explicit config override ──────────────────────────────────────────

def test_explicit_config_list_overrides_default():
    """entry_algorithm.theme_stacking.exempt_held_tickers replaces the
    default entirely: SNDK exempted by config stops blocking; AMZN (off
    the explicit list) starts blocking again."""
    cfg = {"entry_algorithm": {"theme_stacking": {
        "enabled": True, "mode": "wait",
        "exempt_held_tickers": ["SNDK"]}}}
    assert ts.check_theme_stacking("WDC", {"SNDK": [1230.0]},
                                   theme_map=TM, config=cfg) is None
    hit = ts.check_theme_stacking("CGNX", {"AMZN": [245.0]},
                                  theme_map=TM, config=cfg)
    assert hit is not None and "AMZN $245P" in hit["detail"]


def test_exempt_held_param_overrides_everything():
    """The explicit ``exempt_held`` kwarg (tests / callers) wins over any
    config resolution."""
    assert ts.check_theme_stacking("WDC", {"SNDK": [1230.0]}, theme_map=TM,
                                   exempt_held={"SNDK"}) is None
    hit = ts.check_theme_stacking("CGNX", {"AMZN": [245.0]}, theme_map=TM,
                                  exempt_held=set())
    assert hit is not None


# ── (e) end-to-end through the entry algorithm (real YAML) ────────────────

def _snap(ticker, positions):
    return {
        "technicals": {ticker: {
            "spot": 40.0, "rsi_14": 48.0, "sma_200": 35.0,
            "recent_closes": [38.0 + 0.1 * i for i in range(20)],
            "support_resistance": {
                "supports": [{"price": 36.0, "touches": 3,
                              "strength": 4.0}],
                "resistances": []},
            "deep": {"long_term_verdict": "uptrend"},
        }},
        "iv_ranks": {ticker: 70.0},
        "quotes": {ticker: {"last": 40.0}},
        "positions": list(positions),
        "balance": {"accountValue": 2_000_000},
        "earnings_calendar": {ticker: "2026-12-01"},
    }


def _finding(dec, check):
    for f in dec.ordered_reasons:
        if f["check"] == check:
            return f
    return None


def test_evaluate_entry_cgnx_passes_theme_gate_on_held_amzn():
    """End-to-end with the REAL theme_universes.yaml (CGNX and AMZN are
    both Robotics & Autonomy anchors): the CGNX entry must NOT wait on the
    held AMZN $245P — the observed exclusion can never render again."""
    dec = ea.evaluate_entry("csp", "CGNX", 36.0, "2026-09-18", 1.2,
                            _snap("CGNX", [AMZN_PUT]), None, THEME_CFG,
                            as_of=AS_OF)
    f = _finding(dec, "theme_stacking")
    assert f is not None and f["status"] == "pass"
    assert not any(x["check"] == "theme_stacking"
                   and x["status"] in ("wait", "block")
                   for x in dec.ordered_reasons)


def test_evaluate_entry_wdc_still_waits_on_held_sndk():
    """The rule-#49 origin case survives the exemption: WDC vs a held
    SNDK put (real YAML, both Memory & Storage) still WAITs."""
    dec = ea.evaluate_entry("csp", "WDC", 90.0, "2026-09-18", 2.5,
                            _snap("WDC", [SNDK_PUT]), None, THEME_CFG,
                            as_of=AS_OF)
    f = _finding(dec, "theme_stacking")
    assert f is not None and f["status"] == "wait"
    assert "SNDK $1230P" in f["detail"]
