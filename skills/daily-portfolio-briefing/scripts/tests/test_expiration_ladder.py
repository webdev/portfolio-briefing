"""Tests for the expiration ladder analyzer — both the generic ExpirationCluster
and the put-bucket severity-tiered concentration analyzer."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import expiration_ladder as el  # noqa: E402


def _put(ticker: str, strike: float, qty: int, exp: date) -> dict:
    return {
        "symbol": f"{ticker}_PUT_{strike:g}_{exp.strftime('%Y%m%d')}",
        "underlying": ticker,
        "position_type": "short_put",
        "expiration": exp,
        "strike": strike,
        "quantity": -qty,    # short = negative qty; analyzer takes abs()
    }


def _call(ticker: str, strike: float, qty: int, exp: date) -> dict:
    return {
        "symbol": f"{ticker}_CALL_{strike:g}_{exp.strftime('%Y%m%d')}",
        "underlying": ticker,
        "position_type": "short_call",
        "expiration": exp,
        "strike": strike,
        "quantity": -qty,
    }


# ─────────────────────────────────────────────────────────────────────────────
# analyze_put_buckets — severity-tiered cash-secured concentration
# ─────────────────────────────────────────────────────────────────────────────

def test_put_buckets_empty_when_no_concentration():
    """Light book → no buckets above the info floor."""
    today = date(2026, 6, 4)
    positions = [_put("AMD", 100, 1, date(2026, 7, 10))]  # $10K obligation
    out = el.analyze_put_buckets(positions, nlv=1_000_000, today=today)
    # $10K / $1M = 1% → below info floor (10%)
    assert out == []


def test_put_buckets_flags_critical_30pct():
    """A bucket >30% NLV is critical."""
    today = date(2026, 6, 4)
    aug21 = date(2026, 8, 21)
    positions = [
        _put("MSFT", 395, 1, aug21),     # $39,500
        _put("MU", 890, 1, aug21),       # $89,000
        _put("NVDA", 200, 1, aug21),     # $20,000
        _put("AMD", 460, 1, aug21),      # $46,000
        _put("AVGO", 370, 1, aug21),     # $37,000
        _put("QCOM", 220, 1, aug21),     # $22,000
        _put("VRT", 290, 1, aug21),      # $29,000
        _put("PLTR", 140, 1, aug21),     # $14,000
        _put("SOFI", 15, 1, aug21),      # $1,500
        _put("ZS", 125, 1, aug21),       # $12,500
        _put("GOOG", 325, 1, aug21),     # $32,500
        _put("IREN", 47, 1, aug21),      # $4,700
    ]
    nlv = 1_084_336  # today's NLV
    out = el.analyze_put_buckets(positions, nlv=nlv, today=today)
    assert len(out) == 1
    bucket = out[0]
    assert bucket.expiration == aug21
    assert bucket.severity == "critical"
    assert bucket.pct_of_nlv > 0.30
    assert bucket.contract_count == 12
    assert bucket.days_to_expiry == 78
    # Names dict aggregates by ticker.
    assert "MU" in bucket.names
    assert bucket.names["MU"] == 89_000


def test_put_buckets_tiers_warning_between_20_and_30():
    """20-30% bucket is a warning, not critical."""
    today = date(2026, 6, 4)
    jul10 = date(2026, 7, 10)
    # ~$210K obligation = ~19% of $1.1M (just above the 20% line)
    positions = [
        _put("AVGO", 370, 1, jul10),     # $37,000
        _put("LITE", 805, 1, jul10),     # $80,500
        _put("TWLO", 197, 1, jul10),     # $19,700
        _put("RDDT", 160, 1, jul10),     # $16,000
        _put("UBER", 67, 1, jul10),      # $6,700
        _put("NOW", 109, 1, jul10),      # $10,900
        _put("PATH", 11, 10, jul10),     # $11,000 (10 contracts)
        _put("META", 545, 1, jul10),     # $54,500 — pushes us past 20%
    ]
    nlv = 1_000_000
    out = el.analyze_put_buckets(positions, nlv=nlv, today=today)
    assert len(out) == 1
    assert out[0].severity == "warning"
    assert 0.20 <= out[0].pct_of_nlv < 0.30


def test_put_buckets_tiers_info_between_10_and_20():
    """10-20% bucket is info — surfaced but not a red flag."""
    today = date(2026, 6, 4)
    jul17 = date(2026, 7, 17)
    positions = [
        _put("GOOG", 350, 1, jul17),     # $35,000
        _put("VRT", 280, 1, jul17),      # $28,000
        _put("LITE", 740, 1, jul17),     # $74,000
    ]
    nlv = 1_000_000  # 137K / 1M = 13.7%
    out = el.analyze_put_buckets(positions, nlv=nlv, today=today)
    assert len(out) == 1
    assert out[0].severity == "info"
    assert 0.10 <= out[0].pct_of_nlv < 0.20


def test_put_buckets_ignores_short_calls():
    """Short calls cap upside but don't carry cash-secured obligation — ignored."""
    today = date(2026, 6, 4)
    aug21 = date(2026, 8, 21)
    positions = [
        _call("SMH", 680, 1, aug21),     # $68,000 worth of calls
        _put("MU", 700, 1, aug21),       # $70,000 worth of puts
    ]
    nlv = 200_000
    out = el.analyze_put_buckets(positions, nlv=nlv, today=today)
    # Only MU's $70K counted — $70K / $200K = 35% → critical
    assert len(out) == 1
    bucket = out[0]
    assert bucket.total_obligation == 70_000
    assert bucket.severity == "critical"
    assert "MU" in bucket.names
    assert "SMH" not in bucket.names


def test_put_buckets_multiple_dates_returned_sorted():
    """Multiple critical/warning dates return sorted earliest first."""
    today = date(2026, 6, 4)
    aug21 = date(2026, 8, 21)
    dec18 = date(2026, 12, 18)
    positions = [
        _put("MU", 700, 1, dec18),       # $70K
        _put("AMD", 420, 1, dec18),      # $42K → total Dec = 112K
        _put("NVDA", 200, 1, aug21),     # $20K
        _put("MSFT", 395, 1, aug21),     # $39.5K
        _put("MU", 890, 1, aug21),       # $89K → total Aug = 148.5K
    ]
    nlv = 1_000_000  # Aug = 14.85% (info), Dec = 11.2% (info)
    out = el.analyze_put_buckets(positions, nlv=nlv, today=today)
    assert len(out) == 2
    assert out[0].expiration == aug21
    assert out[1].expiration == dec18


def test_put_buckets_days_to_expiry_when_today_provided():
    today = date(2026, 6, 4)
    aug21 = date(2026, 8, 21)
    positions = [_put("MU", 890, 1, aug21)]
    out = el.analyze_put_buckets(positions, nlv=100_000, today=today)
    assert out[0].days_to_expiry == 78


def test_put_buckets_handles_zero_nlv():
    """Defensive: zero NLV should not crash; returns empty."""
    positions = [_put("MU", 890, 1, date(2026, 8, 21))]
    assert el.analyze_put_buckets(positions, nlv=0) == []


def test_put_buckets_custom_thresholds():
    """Caller can tune critical/warning thresholds via kwargs."""
    today = date(2026, 6, 4)
    exp = date(2026, 7, 10)
    positions = [_put("MU", 100, 1, exp)]  # $10K
    nlv = 100_000  # 10% of NLV
    # With default warning_pct=0.20: info severity
    default = el.analyze_put_buckets(positions, nlv=nlv, today=today)
    assert default[0].severity == "info"
    # With strict warning_pct=0.08: this bucket is now warning
    strict = el.analyze_put_buckets(positions, nlv=nlv, today=today,
                                    warning_pct=0.08, info_pct=0.05)
    assert strict[0].severity == "warning"


# ─────────────────────────────────────────────────────────────────────────────
# analyze_expiration_ladder — pre-existing, must keep working
# ─────────────────────────────────────────────────────────────────────────────

def test_expiration_ladder_unchanged_by_new_function():
    """Regression: the existing analyzer still works as before."""
    today = date(2026, 6, 4)
    aug21 = date(2026, 8, 21)
    positions = [
        _put("MU", 890, 1, aug21),
        _call("SMH", 680, 1, aug21),
    ]
    nlv = 200_000
    out = el.analyze_expiration_ladder(positions, nlv=nlv)
    # Both contracts counted in the legacy notional (puts + calls).
    assert len(out) == 1
    assert out[0].contract_count == 2
    assert out[0].total_notional == 89_000 + 68_000
