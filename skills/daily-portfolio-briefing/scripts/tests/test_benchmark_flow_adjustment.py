"""Regression tests — 2026-08-07 Benchmark & Attribution 30d-alpha defect.

Observed (briefing_full_2026-08-07.md, Benchmark & Attribution):

    | 30d | +20.8% | +3.2% | **+17.6%** |
    baseline "~30d ago (2026-07-08) | $901,634" vs today's NLV $1,089,204

while since-inception (2026-05-09) was only +4.4% — the two together implied
May 9 → Jul 8 was -13.6%, contradicting the steady wheel-income history.

Diagnosis: the $901,634 baseline was an ``accountValue_corrected`` artifact.
The migration rebuilt old-era NLV as ``cash + longMV + position marks``, but
old-era ``cash`` is E*TRADE's ``cashAvailableForInvestment`` — a
margin-availability figure that swings with collateral holds — so the
corrected series carried fake ±5-12% daily volatility. The broker's own
``totalAccountValue`` for 2026-07-08 was $1,002,569 (stored in every
snapshot all along, and equal to the corrected recompute to the penny on
days the marks were clean). True 30d return: +8.6%, alpha ≈ +5.4%.

These tests pin:
  - balance_nlv prefers broker totalAccountValue (the root-cause fix);
  - the flow detector on a synthetic series with an injected $100K deposit
    (window return must exclude it — TWR);
  - a clean volatile series (V-dip included) produces NO false positive;
  - the rendered flow annotation in the benchmark panel (rule #19: the
    alpha line must never present flow-driven return as alpha).
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import benchmark_tracker as bt  # noqa: E402
from render.benchmark_panel import render_benchmark_panel  # noqa: E402


def _bdays(start: date, n: int) -> list[date]:
    """n business days starting at (or after) start."""
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Root cause: balance_nlv must prefer broker totalAccountValue
# ---------------------------------------------------------------------------

# The REAL 2026-07-08 balance.json (abridged) behind the bogus baseline.
REAL_2026_07_08_BALANCE = {
    "totalAccountValue": 1002569.19,
    "cash": 68232.6,                      # cashAvailableForInvestment!
    "longMarketValue": 954662.05,
    "accountValue": 1022894.65,
    "accountValue_corrected": 901633.65,  # the artifact
}


class TestBrokerTavPreferred:
    def test_real_2026_07_08_baseline_uses_broker_truth(self):
        """The observed row '| 30d | +20.8% | +3.2% | **+17.6%** |' came from
        baseline $901,634 (accountValue_corrected). The broker's own
        totalAccountValue that day was $1,002,569 — balance_nlv must return
        it, killing the fabricated +20.8%."""
        assert bt.balance_nlv(REAL_2026_07_08_BALANCE) == pytest.approx(1002569.19)
        # And the balance counts as option-mark-inclusive (broker NLV is).
        assert bt.balance_option_inclusive(REAL_2026_07_08_BALANCE)

    def test_preference_order_fallbacks(self):
        # No broker figure → corrected wins over the inflated original.
        assert bt.balance_nlv({"accountValue": 110.0,
                               "accountValue_corrected": 100.0}) == 100.0
        # Zero/absent totalAccountValue never masks the fallbacks.
        assert bt.balance_nlv({"totalAccountValue": 0,
                               "accountValue": 110.0}) == 110.0
        # Last resort: longMV + cash.
        assert bt.balance_nlv({"longMarketValue": 90.0, "cash": 10.0}) == 100.0

    def test_true_30d_return_from_real_broker_series(self):
        """End-to-end on the real broker (totalAccountValue) values around
        the window endpoints: 2026-07-08 $1,002,569.19 → 2026-08-07
        $1,089,203.55 is +8.64%, NOT +20.8%; with SPY +3.2% the alpha is
        ≈ +5.4%, NOT +17.6%."""
        as_of = date(2026, 8, 7)
        hist = {
            date(2026, 7, 8): 1002569.19,
            date(2026, 7, 9): 1015955.88,
            date(2026, 7, 22): 1020111.32,
            date(2026, 7, 29): 906377.56,   # the real late-July trough
            date(2026, 7, 31): 990562.35,
            date(2026, 8, 3): 1032910.12,
            date(2026, 8, 6): 1077921.42,
        }
        spy = {date(2026, 7, 8): 500.0, as_of: 516.0}  # +3.2%
        rep = bt.compute_benchmark(1089203.55, hist, spy, as_of=as_of,
                                   windows=[("30d", 30)])
        w = rep.windows[0]
        assert w.snapshot_start == date(2026, 7, 8)
        assert w.portfolio_return_pct == pytest.approx(8.64, abs=0.05)
        assert w.alpha_pct == pytest.approx(5.44, abs=0.1)
        # The genuine late-July drawdown/recovery is volatility, not a flow —
        # nothing may be flow-flagged on this series.
        assert "flow" not in (w.note or "")
        assert "flow" not in (rep.note or "")


# ---------------------------------------------------------------------------
# Flow detector + TWR windowed returns
# ---------------------------------------------------------------------------

class TestFlowDetector:
    def test_injected_deposit_detected_and_excluded_from_window(self):
        """A $100K deposit into a flat $1M series is a persistent +10% level
        shift → detected as a flow; the 30d window return must EXCLUDE it
        (TWR ≈ 0%), never render as +10% performance/alpha (rule #19)."""
        days = _bdays(date(2026, 6, 1), 30)
        dep_day = days[15]
        hist = {d: (1_000_000.0 if i < 15 else 1_100_000.0)
                for i, d in enumerate(days)}
        as_of = days[-1]
        flows = bt.detect_flow_days(hist)
        assert flows == {dep_day: pytest.approx(100_000.0)}

        spy = {d: 500.0 for d in days}  # SPY flat
        rep = bt.compute_benchmark(1_100_000.0, hist, spy, as_of=as_of,
                                   windows=[("30d", 30), ("inception", None)])
        for w in rep.windows:
            assert w.portfolio_return_pct == pytest.approx(0.0, abs=0.01)
            assert w.alpha_pct == pytest.approx(0.0, abs=0.01)
            assert "flow-adjusted (TWR)" in w.note
            assert dep_day.isoformat() in w.note
        assert "external flow(s) detected" in rep.note
        assert "+$100,000" in rep.note

    def test_withdrawal_detected_symmetrically(self):
        days = _bdays(date(2026, 6, 1), 20)
        hist = {d: (1_000_000.0 if i < 10 else 900_000.0)
                for i, d in enumerate(days)}
        flows = bt.detect_flow_days(hist)
        assert flows == {days[10]: pytest.approx(-100_000.0)}

    def test_clean_volatile_series_no_false_positive(self):
        """Genuine volatility — including a >8% single-day V-dip that
        mean-reverts (the real corrected-series shape around 2026-07-08) —
        must NOT be flagged as a flow, and the window return stays the
        plain end/start ratio."""
        days = _bdays(date(2026, 6, 1), 12)
        vals = [1_000_000, 995_000, 1_010_000, 960_000, 1_005_000,
                1_000_000, 910_000,  # -9.0% dip...
                1_000_000,           # ...+9.9% recovery (V-dip, no level shift)
                1_010_000, 1_005_000, 1_015_000, 1_020_000]
        hist = {d: float(v) for d, v in zip(days, vals)}
        assert bt.detect_flow_days(hist) == {}

        as_of = days[-1]
        spy = {days[0]: 500.0, as_of: 500.0}
        rep = bt.compute_benchmark(1_020_000.0, hist, spy, as_of=as_of,
                                   windows=[("inception", None)])
        w = rep.windows[0]
        assert w.portfolio_return_pct == pytest.approx(2.0)  # 1.00M → 1.02M
        assert "flow" not in (w.note or "")
        assert "flow" not in (rep.note or "")

    def test_unconfirmed_last_day_jump_not_flagged(self):
        """A >8% jump on the LAST snapshot has no persistence evidence yet —
        fail-open: not flagged (it gets flagged the next day if it
        persists)."""
        days = _bdays(date(2026, 6, 1), 10)
        hist = {d: 1_000_000.0 for d in days[:-1]}
        hist[days[-1]] = 1_100_000.0
        assert bt.detect_flow_days(hist) == {}


# ---------------------------------------------------------------------------
# Rendered annotation (benchmark panel)
# ---------------------------------------------------------------------------

class TestRenderedAnnotation:
    def _flow_report(self):
        days = _bdays(date(2026, 6, 1), 30)
        hist = {d: (1_000_000.0 if i < 15 else 1_100_000.0)
                for i, d in enumerate(days)}
        as_of = days[-1]
        spy = {days[0]: 500.0, as_of: 510.0}
        return bt.compute_benchmark(1_100_000.0, hist, spy, as_of=as_of,
                                    windows=[("30d", 30)]), days[15]

    def test_flow_adjustment_renders_under_the_table(self):
        """The flow-adjusted window must carry a visible annotation in the
        rendered panel — never a silent adjustment (rules #19/#24)."""
        rep, dep_day = self._flow_report()
        md = "\n".join(render_benchmark_panel(rep, None))
        assert "flow-adjusted (TWR)" in md
        assert dep_day.isoformat() in md
        assert "external flow(s) detected" in md
        # The table row shows the TWR number (≈0%), not the raw +10%.
        assert "+10.0%" not in md

    def test_clean_report_renders_without_flow_lines(self):
        days = _bdays(date(2026, 6, 1), 10)
        hist = {d: 1_000_000.0 for d in days}
        as_of = days[-1]
        spy = {days[0]: 500.0, as_of: 505.0}
        rep = bt.compute_benchmark(1_000_000.0, hist, spy, as_of=as_of,
                                   windows=[("inception", None)])
        md = "\n".join(render_benchmark_panel(rep, None))
        assert "flow-adjusted" not in md
        assert "external flow" not in md
