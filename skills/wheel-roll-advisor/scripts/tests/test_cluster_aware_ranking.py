"""Cluster-aware roll candidate ranking — the 2026-08-17 QCOM bug.

Observed (real 2026-08-17 briefing, action #2): "🚨 URGENT — EXECUTE ROLL
QCOM_PUT_180_20270219 … Sell-to-Open 1× $180P Thu Jun 17 '27 (weekly)" —
the STO leg landed on the EXACT date red flag #5 flagged the same morning
as a $257K / 22.9%-NLV expiration cluster, with the explicit "Don't: Roll
multiple positions INTO this date — it amplifies the cluster."

Pinned here (ranker level):
  (a) a candidate whose STO expiration lands on a bucket ≥ the warning
      threshold (20% NLV) is deprioritized when a non-clustered
      alternative exists within the credit tolerance (default: accept up
      to 20% less credit);
  (b) outside the tolerance the clustered max-credit candidate is kept
      (the caller renders the measured warning line instead);
  (c) monthly (incl. holiday-shifted Thursday) expirations are preferred
      among the qualifying non-clustered alternatives;
  (d) no bucket map → byte-identical legacy ranking;
  (e) the put-defensive mode gets the same swap.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from candidate_ranker import _is_monthlyish, rank_candidates  # noqa: E402

HOT_EXP = "2027-06-17"       # Thu before Fri Jun 18 '27 (Juneteenth closure)
BUCKETS = {HOT_EXP: 0.229}   # the observed 22.9%-NLV Jun 17 '27 bucket


def _call_cand(cid, strike, exp, net, ext):
    return {"id": cid, "netDollars": net, "dteExtension": ext,
            "current_strike": 500.0,
            "instruction": {"sell_strike": strike, "sell_expiration": exp}}


def _put_cand(cid, strike, exp, net, ext):
    return {"id": cid, "netDollars": net, "dteExtension": ext,
            "current_strike": 180.0,
            "instruction": {"sell_strike": strike, "sell_expiration": exp}}


def test_swaps_to_non_clustered_alternative_within_tolerance():
    """Max-credit B lands on the 22.9% Jun 17 '27 bucket; C (adjacent May
    monthly, 12% less credit — inside the 20% tolerance) must win."""
    cands = [_call_cand("B", 520.0, HOT_EXP, 5000.0, 100),
             _call_cand("C", 520.0, "2027-05-21", 4400.0, 80)]
    best, _ = rank_candidates(cands, spot=480.0,
                              bucket_pct_by_exp=BUCKETS)
    assert best["id"] == "C"


def test_keeps_clustered_pick_outside_credit_tolerance():
    """When the only alternative gives up MORE than the tolerance (24% less
    credit), the clustered best is kept — the caller renders the measured
    cluster warning instead of silently taking a much worse fill."""
    cands = [_call_cand("B", 520.0, HOT_EXP, 5000.0, 100),
             _call_cand("C", 520.0, "2027-05-21", 3800.0, 80)]
    best, _ = rank_candidates(cands, spot=480.0,
                              bucket_pct_by_exp=BUCKETS)
    assert best["id"] == "B"


def test_no_bucket_map_is_legacy_ranking():
    """Without a bucket map the ranking is unchanged — max credit wins."""
    cands = [_call_cand("B", 520.0, HOT_EXP, 5000.0, 100),
             _call_cand("C", 520.0, "2027-05-21", 4400.0, 80)]
    best, _ = rank_candidates(cands, spot=480.0)
    assert best["id"] == "B"


def test_below_warning_bucket_not_penalized():
    """A bucket under the 20% warning threshold never triggers the swap."""
    cands = [_call_cand("B", 520.0, HOT_EXP, 5000.0, 100),
             _call_cand("C", 520.0, "2027-05-21", 4400.0, 80)]
    best, _ = rank_candidates(cands, spot=480.0,
                              bucket_pct_by_exp={HOT_EXP: 0.12})
    assert best["id"] == "B"


def test_monthly_preferred_among_non_clustered_alternatives():
    """'prefer an adjacent monthly if fills allow' — between two qualifying
    non-clustered alternatives, the 3rd-Friday monthly (May 21 '27) beats
    the higher-credit weekly (May 28 '27)."""
    cands = [_call_cand("B", 520.0, HOT_EXP, 5000.0, 100),
             _call_cand("C", 520.0, "2027-05-21", 4400.0, 80),   # monthly
             _call_cand("D", 520.0, "2027-05-28", 4600.0, 85)]   # weekly
    best, _ = rank_candidates(cands, spot=480.0,
                              bucket_pct_by_exp=BUCKETS)
    assert best["id"] == "C"


def test_put_defensive_mode_gets_the_same_swap():
    """The QCOM side of the bug is a PUT roll: the composite-best
    same-strike roll onto Jun 17 '27 must yield to the non-clustered
    alternative within tolerance (1700 ≥ 2000 − 20%)."""
    cands = [_put_cand("B", 180.0, HOT_EXP, 2000.0, 60),
             _put_cand("C", 180.0, "2027-05-21", 1700.0, 90)]
    best, _ = rank_candidates(cands, spot=163.56, option_type="PUT",
                              bucket_pct_by_exp=BUCKETS)
    assert best["id"] == "C"
    # legacy (no map): composite-best B wins
    best, _ = rank_candidates(cands, spot=163.56, option_type="PUT")
    assert best["id"] == "B"


def test_is_monthlyish_recognizes_holiday_shifted_thursday():
    """Thu Jun 17 '27 IS the June 2027 monthly (Fri Jun 18 '27 is the
    Juneteenth market closure) — the local monthly check must accept it,
    alongside genuine 3rd Fridays, and reject plain weeklies."""
    assert _is_monthlyish("2027-06-17") is True     # holiday-shifted Thu
    assert _is_monthlyish("2027-06-18") is True     # the 3rd Friday itself
    assert _is_monthlyish("2027-05-21") is True     # normal monthly
    assert _is_monthlyish("2027-05-28") is False    # weekly Friday
    assert _is_monthlyish("2027-06-10") is False    # weekly Thursday
    assert _is_monthlyish("garbage") is False
