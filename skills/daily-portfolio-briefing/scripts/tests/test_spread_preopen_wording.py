"""Pre-open spread-quality wording (2026-08-26).

Observed on the real 2026-08-26 briefing — generated at "_… generated Wed
Aug 26 '26, 8:55 AM local_" (pre-open), 10 exclusions rendered e.g.:

    - ⏸ HACK B (68) — excluded: ⏸ spread too wide — $0.65 (113% of mid);
      premium is unfillable

At 8:55 AM the market hasn't opened — spreads are NATURALLY wide, so
"premium is unfillable" implies a permanence the measurement can't
support. The ticket stays excluded (the measured spread IS unfillable
right now), but before 09:35 the wording must read honestly:

    ⏸ spread too wide pre-open — $0.65 (113% of mid); recheck after the
    open
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.setup_grade import (  # noqa: E402
    collect_best_setups,
    spread_quality_failure,
)

# bid 0.25 / ask 0.90 → spread $0.65, mid $0.575 → 113% of mid (the
# observed HACK numbers' shape).
_WIDE_Q = {"bid": 0.25, "ask": 0.90, "mid": 0.575}
_PRE = datetime(2026, 8, 26, 8, 55)
_POST = datetime(2026, 8, 26, 10, 15)


def test_pre_open_wording_recheck_after_the_open():
    """Before 09:35 the reason says 'pre-open … recheck after the open',
    never 'premium is unfillable' (which implies permanence)."""
    f = spread_quality_failure(_WIDE_Q, {}, now=_PRE)
    assert f is not None
    assert f["reason"] == ("⏸ spread too wide pre-open — $0.65 "
                           "(113% of mid); recheck after the open")
    assert f["pre_open"] is True
    assert "unfillable" not in f["reason"]


def test_post_open_wording_unchanged():
    """After the open the observed wording stands: '⏸ spread too wide —
    $0.65 (113% of mid); premium is unfillable'."""
    f = spread_quality_failure(_WIDE_Q, {}, now=_POST)
    assert f is not None
    assert f["reason"] == ("⏸ spread too wide — $0.65 "
                           "(113% of mid); premium is unfillable")
    assert f["pre_open"] is False


def test_boundary_is_0935():
    """09:34 is still pre-open wording; 09:35 flips to the settled
    wording."""
    at_0934 = spread_quality_failure(
        _WIDE_Q, {}, now=datetime(2026, 8, 26, 9, 34))
    at_0935 = spread_quality_failure(
        _WIDE_Q, {}, now=datetime(2026, 8, 26, 9, 35))
    assert "pre-open" in at_0934["reason"]
    assert "pre-open" not in at_0935["reason"]


def test_tight_spread_still_passes_regardless_of_clock():
    """The gate itself is unchanged — a fillable spread never fails,
    pre-open or not."""
    q = {"bid": 1.00, "ask": 1.10, "mid": 1.05}
    assert spread_quality_failure(q, {}, now=_PRE) is None
    assert spread_quality_failure(q, {}, now=_POST) is None


def test_collect_best_setups_threads_pre_open_clock():
    """The scout-pool exclusion line inherits the honest pre-open wording
    — still excluded from Top-N, but 'recheck after the open'."""
    scout = [{
        "ticker": "HACK", "rsi_14": 47.0, "iv_rank": 86.0,
        "spot": 100.0, "sma_200": 90.0, "drawdown_pct": 10.0,
        "csp_entry": {"strike": 98.0, "mid": 0.575, "bid": 0.25,
                      "ask": 0.90, "dte": 30,
                      "expiration": "2026-09-25"},
    }]
    pre = collect_best_setups(scout_results=scout, snapshot_data={},
                              now=_PRE)
    ex = [e for e in pre["excluded_csp"] if e["ticker"] == "HACK"]
    assert len(ex) == 1
    assert "pre-open" in ex[0]["reason"]
    assert "recheck after the open" in ex[0]["reason"]
    assert all(e["ticker"] != "HACK" for e in pre["csp"])

    post = collect_best_setups(scout_results=scout, snapshot_data={},
                               now=_POST)
    ex2 = [e for e in post["excluded_csp"] if e["ticker"] == "HACK"]
    assert len(ex2) == 1
    assert "premium is unfillable" in ex2[0]["reason"]
