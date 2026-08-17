"""BUG B (2026-08-17 briefing) — roll INTO the flagged expiration cluster,
holiday-shifted monthly mislabeled "(weekly)", and the doubled ⚠️ emoji.

Observed (real 2026-08-17 briefing, action #2 vs red flag #5):

  "🚨 **URGENT — EXECUTE ROLL** QCOM_PUT_180_20270219 — Calendar roll
   (same strike, longer date): +$470 net credit …
   - **Order:** Combo (calendar/diagonal) — Buy-to-Close 1× QCOM $180P
     Fri Feb 19 '27 (current mid ~$31.60); Sell-to-Open 1× $180P
     Thu Jun 17 '27 (weekly) (current bid $36.30 / mid $37.90 / ask $39.50)."

  "### 📊 5. MEDIUM — Expiration cluster on Thu Jun 17 27 (304d out) —
   3 short puts, $257,000 obligation, 22.9% NLV …
   **Don't:** Roll multiple positions INTO this date — it amplifies the
   cluster."

  "- **Earnings check:** ⚠️ ⚠️ Earnings 73d away — prints 231d BEFORE
   expiry — contract spans earnings"

Three defects pinned:
  1. Thu Jun 17 '27 is the June 2027 MONTHLY shifted for the Juneteenth
     closure (Fri Jun 18 '27) — labeling it "(weekly)" is rule-#19 wrong.
  2. The roll composition never consulted the put-bucket concentration, so
     the action list rolled INTO the exact date the red flag forbade —
     with no warning line.
  3. format_earnings_badge double-prefixed the ⚠️ the Case-4 message
     already carries.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.earnings_guard import (  # noqa: E402
    check_earnings_conflict,
    format_earnings_badge,
)
from analysis.expiration_ladder import (  # noqa: E402
    PutBucketCluster,
    bucket_pct_by_exp,
    roll_into_cluster_warning,
)
from analysis.expiration_policy import (  # noqa: E402
    expiration_kind,
    is_holiday_shifted_monthly,
    is_monthly,
    kind_suffix,
    label_exp,
)
from render.panels import render_action_list  # noqa: E402

TODAY = "2026-08-17"
NLV = 1_122_271.0            # 257,000 / 0.229 — the observed cluster math


# ── Fix 1: holiday-shifted monthly labeling ────────────────────────────────

def test_thu_before_holiday_third_friday_is_monthly_holiday_shifted():
    """Observed: "Sell-to-Open 1× $180P Thu Jun 17 '27 (weekly)". Fri Jun
    18 '27 is the 3rd Friday AND the Juneteenth market closure — the
    Thursday before is the June 2027 monthly, never a weekly."""
    assert is_holiday_shifted_monthly("2027-06-17") is True
    assert is_monthly("2027-06-17") is True
    assert expiration_kind("2027-06-17") == "monthly, holiday-shifted"
    assert label_exp("Thu Jun 17 '27", "2027-06-17") \
        == "Thu Jun 17 '27 (monthly, holiday-shifted)"
    assert "(weekly)" not in label_exp("Thu Jun 17 '27", "2027-06-17")


def test_good_friday_shift_recognized_by_the_general_rule():
    """Good Friday years where it coincides with the 3rd Friday (Fri Apr 19
    '30) shift the April monthly to Thu Apr 18 '30 — the general
    Thursday-before-3rd-Friday rule labels it monthly, holiday-shifted."""
    assert expiration_kind("2030-04-18") == "monthly, holiday-shifted"


def test_normal_fridays_and_genuine_weeklies_unchanged():
    """Normal 3rd Fridays stay "monthly"; ordinary Fridays and Thursdays
    that are NOT adjacent to a 3rd Friday stay "weekly"."""
    assert expiration_kind("2027-05-21") == "monthly"     # 3rd Fri May '27
    assert expiration_kind("2026-09-18") == "monthly"     # 3rd Fri Sep '26
    assert expiration_kind("2026-09-11") == "weekly"      # 2nd Friday
    assert expiration_kind("2027-06-10") == "weekly"      # plain Thursday
    assert is_holiday_shifted_monthly("2027-06-10") is False
    assert kind_suffix("2026-09-18") == ", monthly"
    assert kind_suffix("2027-06-17") == ", monthly, holiday-shifted"


def test_thursday_before_a_non_holiday_third_friday_stays_weekly():
    """The shift needs an actual market-holiday Friday: Fri Aug 21 '26 is
    an ordinary 3rd Friday (no closure), so a synthetic Thu Aug 20 '26
    date is NOT the August monthly — never misclassify it."""
    assert is_holiday_shifted_monthly("2026-08-20") is False
    assert expiration_kind("2026-08-20") == "weekly"
    assert is_monthly("2026-08-21") is True               # 3rd Fri itself


# ── Fix 3: single ⚠️ on the earnings badge ────────────────────────────────

def test_earnings_badge_renders_one_warning_emoji():
    """Observed: "**Earnings check:** ⚠️ ⚠️ Earnings 73d away — prints 231d
    BEFORE expiry — contract spans earnings" — the Case-4 message embeds
    its own ⚠️ and the badge prefixed another. Exactly one must render."""
    chk = check_earnings_conflict("QCOM", "2027-06-17",
                                  {"QCOM": "2026-10-29"}, TODAY)
    assert chk["level"] == "warn"
    assert chk["days_to_earnings"] == 73
    badge = format_earnings_badge(chk)
    assert badge.count("⚠️") == 1
    assert "Earnings 73d away" in badge
    # block-level badges keep their single 🔴 too
    blk = check_earnings_conflict("QCOM", "2026-09-18",
                                  {"QCOM": "2026-08-25"}, TODAY)
    assert blk["level"] == "block"
    assert format_earnings_badge(blk).count("🔴") == 1


# ── Fix 2: the measured cluster warning helper ────────────────────────────

def _jun17_bucket():
    return PutBucketCluster(
        expiration=date(2027, 6, 17), days_to_expiry=304, contract_count=3,
        total_obligation=257_000.0, pct_of_nlv=0.229, severity="warning",
        names={"MELI": 146_000.0, "MU": 92_000.0, "QCOM": 19_000.0})


def test_cluster_warning_measured_math():
    """The warning carries the MEASURED bucket math (rule #19): $257K
    existing + $18K new obligation → $275K, 24.5% of the $1.12M NLV."""
    line = roll_into_cluster_warning(
        "2027-06-17", 180.0, 1, [_jun17_bucket()], NLV)
    assert line is not None
    assert "rolls INTO the Jun 17 '27 cluster" in line
    assert "$257,000 → $275,000" in line
    assert "24.5% NLV" in line
    assert "prefer an adjacent monthly" in line


def test_cluster_warning_none_off_bucket_or_below_threshold():
    """No warning for an expiration off the bucket, a bucket below the
    warning threshold, or unmeasurable inputs (fail-open, rule #19)."""
    b = _jun17_bucket()
    assert roll_into_cluster_warning("2027-05-21", 180.0, 1, [b], NLV) is None
    cool = PutBucketCluster(
        expiration=date(2027, 6, 17), days_to_expiry=304, contract_count=1,
        total_obligation=130_000.0, pct_of_nlv=0.116, severity="info")
    assert roll_into_cluster_warning(
        "2027-06-17", 180.0, 1, [cool], NLV) is None
    assert roll_into_cluster_warning("2027-06-17", 180.0, 1, [b], 0) is None
    assert roll_into_cluster_warning(None, 180.0, 1, [b], NLV) is None
    assert bucket_pct_by_exp([b]) == {"2027-06-17": 0.229}


# ── Fix 2 end-to-end: the QCOM-shaped action-list ticket ──────────────────

def _qcom_review(candidates):
    return {
        "contract": "QCOM_PUT_180_20270219",
        "underlying": "QCOM", "type": "PUT", "qty": -1,
        "strike": 180.0, "expiration": "2027-02-19",
        "entry_price": 34.30, "current_mid": 31.60, "days_to_expiry": 186,
        "delta": -0.53,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "GUARDRAIL_STRIKE_TESTED",
        "rationale": ("🎯 Strike tested (δ 0.53 ≥ 0.45) with 8% captured "
                      "and 186 DTE — credit-roll window open; roll "
                      "down-and-out while extrinsic is at its peak."),
        "roll_candidates": candidates,
    }


def _cand(cid, exp, net, ext, mid, bid, ask):
    return {"id": cid, "description": f"1× $180P {exp}",
            "instruction": {"sell_strike": 180.0, "sell_expiration": exp,
                            "sell_mid": mid, "sell_bid": bid,
                            "sell_ask": ask},
            "netDollars": net, "dteExtension": ext}


def _snapshot():
    return {
        "quotes": {"QCOM": {"last": 163.56}},
        "chains": {},
        "iv_ranks": {"QCOM": 68},
        "earnings_calendar": {},
        "_config": {"core_positions": [], "accounts": [],
                    "expiration_policy": {"prefer_monthly": True}},
        "balance": {"accountValue": NLV, "cash": 100_000},
        "positions": [],
        "_credit_windows": {"QCOM_PUT_180_20270219": {
            "state": "open", "best_credit": 4.70,
            "best_candidate_desc": "1× $180P Jun 17 '27"}},
    }


def _analytics():
    return {"put_buckets": [_jun17_bucket()], "nlv": NLV}


def _render(reviews, analytics):
    return "\n".join(render_action_list(
        [], reviews, [], analytics=analytics, snapshot_data=_snapshot(),
        date_str=TODAY))


def test_only_viable_clustered_roll_renders_measured_warning_and_label():
    """When the Jun 17 '27 candidate is the ONLY viable roll, the ticket
    still composes (rule #24) but MUST carry the measured cluster warning
    — and the STO leg labels "(monthly, holiday-shifted)", never the
    observed "(weekly)"."""
    rev = _qcom_review([_cand("B", "2027-06-17", 470.0, 118,
                              37.90, 36.30, 39.50)])
    md = _render([rev], _analytics())
    assert "EXECUTE ROLL" in md and "QCOM_PUT_180_20270219" in md
    assert "rolls INTO the Jun 17 '27 cluster" in md
    assert "$257,000 → $275,000" in md
    assert "24.5% NLV" in md
    assert "(monthly, holiday-shifted)" in md
    assert "(weekly)" not in md


def test_alternative_within_tolerance_avoids_the_cluster():
    """With an adjacent monthly (May 21 '27) inside the credit tolerance,
    the composed STO leg avoids the flagged Jun 17 '27 date entirely and
    no cluster warning renders."""
    rev = _qcom_review([
        _cand("B", "2027-06-17", 470.0, 118, 37.90, 36.30, 39.50),
        _cand("C", "2027-05-21", 400.0, 91, 35.60, 34.90, 36.30),
    ])
    md = _render([rev], _analytics())
    assert "EXECUTE ROLL" in md
    assert "Sell-to-Open 1× $180P Fri May 21 '27" in md
    assert "Jun 17 '27" not in md.split("Sell-to-Open", 1)[1].split("\n")[0]
    assert "rolls INTO the" not in md


def test_no_analytics_keeps_legacy_composition():
    """Fail-open: without analytics (no put buckets, no NLV) the roll
    composes exactly as before — no fabricated warning (rule #19)."""
    rev = _qcom_review([_cand("B", "2027-06-17", 470.0, 118,
                              37.90, 36.30, 39.50)])
    md = _render([rev], None)
    assert "EXECUTE ROLL" in md
    assert "rolls INTO the" not in md
