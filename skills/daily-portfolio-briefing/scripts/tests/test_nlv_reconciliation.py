"""Regression tests — NLV must not exclude short-option liabilities.

Observed 2026-08-04 (10:25 AM PT briefing): the header rendered
"Portfolio NLV: $1,149,562" while the broker's own Portfolios page —
37 minutes earlier in the same session — showed Net Account Value
$1,082,940.74. The $66,622 difference ≈ the sum of absolute short-option
position values on the broker screen (MU $950P -$21,122, PLTR $200C ×7
-$7,857, VRT/QCOM/NVDA/MSFT/GOOG/AMZN/SOFI short marks...).

Root cause: steps/snapshot_inputs.py recomputed `accountValue` as
long_market_value + cash — EQUITY positions only — overwriting the broker's
own `totalAccountValue` that the E*TRADE adapter returns, and never
subtracting the (negative) marketValue of short options. The snapshot
history shows the drift on EVERY snapshot back to 2026-05-09 (+7.2%).

Fix: `_compose_balance` prefers the broker `totalAccountValue` when
present; the computed fallback includes signed option marks; a >1%
computed-vs-broker divergence attaches a `nlv_reconciliation` warning that
renders 🔴 in the header and in the Live-Data policer panel.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps.snapshot_inputs import _compose_balance  # noqa: E402
from render.panels import render_header  # noqa: E402


def test_nlv_subtracts_short_option_liabilities():
    """When no broker NLV is available, the computed fallback must include
    SIGNED option marks — short options (negative marketValue, like the
    broker screen's MU $950P -$21,122 row) reduce NLV. The old code
    (long equity + cash only) produced $1,149,562 instead of ~$1,082,940.
    """
    base = {"cash": 73_001.03}  # no totalAccountValue → computed path
    long_mv = 1_076_561.24
    option_mv = -64_669.08  # Σ signed short-option marks
    bal = _compose_balance(base, long_mv, option_mv)
    assert bal["accountValue"] == round(long_mv + option_mv + 73_001.03, 2)
    # The buggy value (equity + cash, shorts dropped) must NOT be produced
    assert bal["accountValue"] != round(long_mv + 73_001.03, 2)
    assert bal["optionMarketValue"] == round(option_mv, 2)


def test_broker_accountvalue_preferred_when_present():
    """Broker truth beats reconstruction: with E*TRADE's totalAccountValue
    present ($1,084,893.19 in the 2026-08-04 balance.json), accountValue
    must be the broker figure — not the $1,149,562 reconstruction.
    """
    base = {"cash": 73_001.03, "totalAccountValue": 1_084_893.19}
    bal = _compose_balance(base, 1_076_561.24, -64_669.08)
    assert bal["accountValue"] == 1_084_893.19
    # The reconstruction is preserved for cross-checking, never as the NLV
    assert bal["computedAccountValue"] == round(
        1_076_561.24 - 64_669.08 + 73_001.03, 2)
    rec = bal["nlv_reconciliation"]
    assert rec["using"] == "broker"
    assert rec["broker_nlv"] == 1_084_893.19


def test_reconciliation_warning_over_1pct():
    """The $1,149,562-vs-$1,082,940 case (~6% apart) must fire a 🔴
    data-integrity warning showing both numbers; a <1% divergence must not.
    Never silently ship a reconstructed NLV that disagrees with broker truth.
    """
    # >1% divergence: computed reconstruction (shorts dropped upstream,
    # simulated via option_mv=0) vs broker figure
    base = {"cash": 73_001.03, "totalAccountValue": 1_084_893.19}
    bal = _compose_balance(base, 1_076_561.24, 0.0)
    rec = bal["nlv_reconciliation"]
    assert rec["warning"] is True
    assert rec["delta"] > 0

    # Header renders the 🔴 line with both numbers
    lines = render_header(
        "2026-08-04", "NORMAL", bal["accountValue"], bal["cash"], 3,
        balance=bal,
    )
    header_md = "\n".join(lines)
    assert "🔴" in header_md
    assert f"${rec['computed_nlv']:,.0f}" in header_md
    assert f"${rec['broker_nlv']:,.0f}" in header_md
    assert "broker" in header_md

    # <1% divergence: no warning, no 🔴 line
    bal_ok = _compose_balance(base, 1_076_561.24, -64_669.08)
    assert bal_ok["nlv_reconciliation"]["warning"] is False
    lines_ok = render_header(
        "2026-08-04", "NORMAL", bal_ok["accountValue"], bal_ok["cash"], 3,
        balance=bal_ok,
    )
    assert "🔴" not in "\n".join(lines_ok)


def test_reconciliation_warning_renders_in_live_data_panel():
    """The Live-Data policer surfaces the same 🔴 reconciliation line
    (advisory — never a BLOCK: the pipeline already uses the broker figure).
    """
    policer_dir = (Path(__file__).resolve().parents[3]
                   / "live-data-policer" / "scripts")
    sys.path.insert(0, str(policer_dir))
    from police import police_data_freshness  # noqa: E402

    from datetime import datetime
    now = datetime(2026, 8, 4, 10, 25)
    fresh = now.isoformat()
    provenance = {
        s: {"source": src, "fetched_at": fresh, "fresh": True}
        for s, src in [
            ("positions", "etrade_live"), ("broker_positions", "etrade_live"),
            ("quotes", "yfinance"), ("chains", "etrade_live"),
            ("iv_ranks", "yfinance_252d"), ("earnings_calendar", "yfinance"),
        ]
    }
    snapshot_data = {
        "data_provenance": provenance,
        "balance": {
            "accountValue": 1_084_893.19,
            "nlv_reconciliation": {
                "broker_nlv": 1_084_893.19,
                "computed_nlv": 1_149_562.27,
                "delta": 64_669.08,
                "pct": 5.96,
                "warning": True,
                "using": "broker",
            },
        },
    }
    result = police_data_freshness(snapshot_data, now=now)
    assert result.verdict == "WARN"  # advisory, not BLOCK
    assert result.live is True
    assert not result.blocking_sources
    assert "🔴" in result.panel_md
    assert "$1,149,562" in result.panel_md
    assert "$1,084,893" in result.panel_md
    assert "using the broker figure" in result.panel_md


def test_downstream_percentages_use_corrected_nlv():
    """Cash %, concentration %, coverage % all divide by balance.accountValue.
    With the fix, they divide by the broker NLV ($1,084,893) not the inflated
    reconstruction ($1,149,562) — the old figure understated every ratio by
    ~6% relatively (e.g. a $110K position: 9.6% "under cap" vs a true 10.1%).
    """
    base = {"cash": 73_001.03, "totalAccountValue": 1_084_893.19}
    bal = _compose_balance(base, 1_076_561.24, -64_669.08)
    nlv = float(bal.get("accountValue", 0) or 0)  # downstream consumer pattern
    position_value = 110_000.0
    concentration_pct = position_value / nlv * 100
    assert nlv == 1_084_893.19
    # True concentration crosses the 10% cap; the buggy NLV said 9.57%
    assert concentration_pct > 10.0
    assert position_value / 1_149_562.27 * 100 < 10.0  # the bug's blind spot
    # Cash % likewise corrected
    assert abs(bal["cash"] / nlv * 100 - 6.73) < 0.01
