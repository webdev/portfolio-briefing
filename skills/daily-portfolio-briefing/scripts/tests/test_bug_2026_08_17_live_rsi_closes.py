"""BUG D (2026-08-17 briefing) — live-RSI recompute unavailable exactly
when needed: the snapshot never threaded enough closes to seed Wilder's.

Observed (real 2026-08-17 briefing, Best Setups exclusions — three cards,
all Monday-gap names):

  "⏸ WDC A- (80) — excluded: stale RSI on a +9.4% up-move — live RSI not
   computable; new puts excluded (rule #44 fail-safe)"
  "⏸ SNDK C (56) — excluded: stale RSI on a +8.7% up-move — live RSI not
   computable; new puts excluded (rule #44 fail-safe)"
  "⏸ MU D (40) — excluded: stale RSI on a +5.4% up-move — live RSI not
   computable; new puts excluded (rule #44 fail-safe)"

Root cause: the snapshot's ``recent_closes`` carries only the last ~6
closes (built for the iv_honesty gap detector) — a 14-period Wilder RSI
needs ≥ 15 prices, so ``vintage_guard.wilder_rsi_live`` could NEVER run on
this path even though the technicals computed the snapshot RSI from a full
OHLC pull. Fix: snapshot_inputs threads ``rsi_closes`` (last ~60 closes,
same pull, no extra fetch) and the vintage guard prefers it.

Pinned here:
  (a) with rsi_closes threaded, a +9.4% mover gets a REAL live RSI
      (status "live") — in-band → graded on the live value;
  (b) live RSI past the >70 block → visible hard-block exclusion (not the
      fail-safe wording);
  (c) production shape (6-close recent_closes, NO rsi_closes) → the
      legacy rule-#44 fail-safe exclusion, unchanged;
  (d) legacy snapshots whose recent_closes IS long enough keep working
      (back-compat with the rule-#46 fixtures).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import setup_grade as sg  # noqa: E402
from analysis.vintage_guard import (  # noqa: E402
    resolve_new_open_rsi,
    wilder_rsi_live,
)

# WDC-shaped: technicals close 101, live +9.4%
TECH_CLOSE = 101.0
LIVE_SPOT = round(TECH_CLOSE * 1.094, 2)      # +9.4% — the Monday gap

# A declining 60-close series ending at the technicals close — appending
# the +9.4% live bar lands the Wilder RSI INSIDE the 35-55 put band.
DECLINING_60 = [160.0 - 1.0 * i for i in range(60)]
# A rising 60-close series — appending the +9.4% live bar → RSI 100 (>70).
RISING_60 = [50.0 + 1.0 * i for i in range(60)]
RISING_CLOSE = RISING_60[-1]                  # 109.0
RISING_LIVE = round(RISING_CLOSE * 1.094, 2)


def _technicals(spot, rsi_closes, recent_closes=None):
    """Production-shaped technicals: recent_closes is the REAL ~6-close
    slice (the shape that broke); rsi_closes is the new threaded series."""
    tech = {
        "spot": spot, "rsi_14": 48.0, "sma_200": spot * 0.85,
        "iv_rank": 70.0, "drawdown_pct": 5.0,
        "recent_closes": (recent_closes if recent_closes is not None
                          else (rsi_closes or [spot] * 6)[-6:]),
        "support_resistance": {
            "supports": [{"price": 90.0, "touches": 3, "strength": 4.0}],
            "resistances": [],
        },
        "deep": {"long_term_verdict": "uptrend"},
    }
    if rsi_closes is not None:
        tech["rsi_closes"] = rsi_closes
    return {"WDC": tech}


def _snapshot(live, rsi_closes, spot=TECH_CLOSE, recent_closes=None):
    return {
        "technicals": _technicals(spot, rsi_closes,
                                  recent_closes=recent_closes),
        "iv_ranks": {"WDC": 70.0},
        "quotes": {"WDC": {"last": live}},
        "positions": [],
        "earnings_calendar": {},
    }


def _scout_result(spot=TECH_CLOSE):
    return {
        "ticker": "WDC", "rsi_14": 48.0, "iv_rank": 70.0,
        "spot": spot, "sma_200": spot * 0.85, "drawdown_pct": 5.0,
        "days_to_earnings": 40,
        "support_resistance": {
            "supports": [{"price": 90.0, "touches": 3, "strength": 4.0}],
            "resistances": [],
        },
        "csp_entry": {"strike": 90.0, "mid": 2.15, "dte": 36,
                      "expiration": "2026-09-18"},
    }


# ── (a) threaded closes → real live RSI, in-band → graded ─────────────────

def test_resolver_recomputes_live_rsi_from_rsi_closes():
    """The +9.4% up-move that rendered "live RSI not computable" now
    recomputes: status "live", verified, with the measured value."""
    expected = wilder_rsi_live(DECLINING_60, LIVE_SPOT)
    assert expected is not None and 35.0 <= expected <= 55.0  # in-band
    res = resolve_new_open_rsi(
        "WDC", _snapshot(LIVE_SPOT, DECLINING_60)["technicals"],
        {"WDC": {"last": LIVE_SPOT}}, [], {})
    assert res["status"] == "live"
    assert res["verified"] is True
    assert res["rsi"] == pytest.approx(round(expected, 1))
    assert res["move_pct"] == pytest.approx(9.4, abs=0.1)


def test_spotlight_grades_on_the_real_live_rsi_when_in_band():
    """WDC at +9.4% with an in-band live RSI is GRADED on the measured
    value — no exclusion, and the drivers carry the live RSI, not the
    pre-move 48."""
    live = wilder_rsi_live(DECLINING_60, LIVE_SPOT)
    best = sg.collect_best_setups(
        scout_results=[_scout_result()],
        snapshot_data=_snapshot(LIVE_SPOT, DECLINING_60),
        config={})
    assert [e["ticker"] for e in best["csp"]] == ["WDC"]
    drivers = " · ".join(best["csp"][0]["drivers"])
    assert f"RSI {live:.0f}" in drivers
    assert "not computable" not in str(best.get("excluded_csp"))


# ── (b) live RSI past the block → measured hard-block exclusion ───────────

def test_live_rsi_past_block_excludes_with_measured_value():
    """On a rising series the +9.4% gap recomputes past the >70 block —
    the exclusion names the LIVE value, never the "not computable"
    fail-safe wording."""
    best = sg.collect_best_setups(
        scout_results=[_scout_result(spot=RISING_CLOSE)],
        snapshot_data=_snapshot(RISING_LIVE, RISING_60, spot=RISING_CLOSE),
        config={})
    assert [e["ticker"] for e in best["csp"]] == []
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert "WDC" in ex
    assert "live RSI" in ex["WDC"]["reason"]
    assert "hard block" in ex["WDC"]["reason"]
    assert "not computable" not in ex["WDC"]["reason"]


# ── (c) absent closes → legacy fail-safe unchanged ────────────────────────

def test_production_six_close_shape_keeps_the_fail_safe():
    """The observed pre-fix shape (recent_closes = 6 closes, no
    rsi_closes): "stale RSI on a +9.4% up-move — live RSI not computable;
    new puts excluded (rule #44 fail-safe)" — the fail-safe must remain
    when the closes are genuinely absent."""
    res = resolve_new_open_rsi(
        "WDC", _technicals(TECH_CLOSE, None,
                           recent_closes=DECLINING_60[-6:]),
        {"WDC": {"last": LIVE_SPOT}}, [], {})
    assert res["status"] == "stale"
    assert res["rsi"] is None
    best = sg.collect_best_setups(
        scout_results=[_scout_result()],
        snapshot_data=_snapshot(LIVE_SPOT, None,
                                recent_closes=DECLINING_60[-6:]),
        config={})
    assert [e["ticker"] for e in best["csp"]] == []
    ex = {e["ticker"]: e for e in best["excluded_csp"]}
    assert "WDC" in ex
    assert "up-move" in ex["WDC"]["reason"]
    assert "rule #44" in ex["WDC"]["reason"]
    assert "not computable" in ex["WDC"]["reason"]


# ── (d) legacy long recent_closes still works (back-compat) ───────────────

def test_legacy_long_recent_closes_still_recomputes():
    """Older snapshots / fixtures that carry a LONG recent_closes series
    (the rule-#46 test shape) keep recomputing — rsi_closes is preferred,
    recent_closes is the fallback."""
    res = resolve_new_open_rsi(
        "WDC", _technicals(TECH_CLOSE, None, recent_closes=DECLINING_60),
        {"WDC": {"last": LIVE_SPOT}}, [], {})
    assert res["status"] == "live"
    assert res["rsi"] is not None


def test_rsi_closes_preferred_over_recent_closes():
    """When BOTH are present, the threaded rsi_closes drives the recompute
    (recent_closes is the 6-close gap-detector slice, not an RSI seed)."""
    expected = wilder_rsi_live(DECLINING_60, LIVE_SPOT)
    res = resolve_new_open_rsi(
        "WDC", _technicals(TECH_CLOSE, DECLINING_60,
                           recent_closes=DECLINING_60[-6:]),
        {"WDC": {"last": LIVE_SPOT}}, [], {})
    assert res["status"] == "live"
    assert res["rsi"] == pytest.approx(round(expected, 1))
