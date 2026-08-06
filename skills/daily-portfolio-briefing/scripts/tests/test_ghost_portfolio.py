"""Tests for analysis/ghost_portfolio.py (task #41) — the 👻 options-stripped
counterfactual NAV ("is it the market or is it my moves?").

Construction rules pinned here (the module docstring is the contract):
equity trades mirrored at that day's marks; ALL option cash flows stripped;
option-caused share changes (assignment / called-away at expiry) NOT
mirrored; external deposits/withdrawals mirrored; gap = real − ghost starts
at exactly $0 at inception; backfill idempotent; fail-open everywhere.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import ghost_portfolio as gp  # noqa: E402
from render.ghost_panel import (  # noqa: E402
    ghost_money_plan_line,
    render_ghost_panel,
)


# ── Fixture builders ──────────────────────────────────────────────────────


def eq(sym, qty, price):
    return {"symbol": sym, "assetType": "EQUITY", "qty": float(qty),
            "price": float(price), "marketValue": float(qty) * float(price)}


def opt(und, otype, strike, exp, qty, mid):
    return {"symbol": f"{und}_{otype}_{strike:g}_{exp.replace('-', '')}",
            "assetType": "OPTION", "underlying": und, "type": otype,
            "strike": float(strike), "expiration": exp, "qty": float(qty),
            "currentMid": float(mid),
            "marketValue": float(mid) * float(qty) * 100.0}


def snap(d, positions, cash, quotes=None):
    """Broker-true balance: NAV = cash + equity MV + signed option marks."""
    eq_mv = sum(p["marketValue"] for p in positions
                if p["assetType"] == "EQUITY")
    op_mv = sum(p["marketValue"] for p in positions
                if p["assetType"] == "OPTION")
    return {"date": d, "positions": positions, "quotes": quotes or {},
            "balance": {"accountValue": cash + eq_mv + op_mv, "cash": cash,
                        "optionMarketValue": op_mv,
                        "longMarketValue": eq_mv}}


def base_day(d="2026-06-01", cash=10_000.0, aapl_px=100.0, put_mid=2.0):
    """100 AAPL + one short AAPL $95P Jun 19 — NAV = cash + 10,000 − mid·100."""
    return snap(d, [eq("AAPL", 100, aapl_px),
                    opt("AAPL", "PUT", 95, "2026-06-19", -1, put_mid)], cash)


# ── Inception + gap math ──────────────────────────────────────────────────


def test_gap_starts_at_zero_and_tracks_real_minus_ghost():
    """At inception the ghost closes the option book at marks, so
    Ghost NAV_0 == Real NAV_0 and gap_0 == $0 exactly. Day 2: the short
    put decays $0.50 (real NAV +$50 with no equity move) → gap +$50 and
    gap_delta_1d +$50 — pure options-program contribution."""
    s0 = base_day(put_mid=2.0)                       # real = 10k+10k−200
    s1 = base_day(d="2026-06-02", put_mid=1.5)       # real = 10k+10k−150
    rep = gp.compute_ghost_series([s0, s1])
    assert rep.status == "ok"
    assert rep.inception == date(2026, 6, 1)
    d0, d1 = rep.days
    assert d0.gap == 0.0
    assert d0.real_nav == 19_800.0
    assert d0.ghost_nav == 19_800.0                  # 9,800 cash + 10,000 eq
    assert d1.ghost_nav == 19_800.0                  # ghost never moves here
    assert d1.gap == 50.0
    assert d1.gap_delta_1d == 50.0


def test_option_premium_cash_flow_is_stripped():
    """Day 2 the real account sells a second put (premium +$300 into real
    cash; new −$300 liability). NAV-neutral in the real account AND invisible
    to the ghost: no premium banked, no liability carried — gap stays $0."""
    s0 = base_day()
    s1 = snap("2026-06-02",
              [eq("AAPL", 100, 100.0),
               opt("AAPL", "PUT", 95, "2026-06-19", -1, 2.0),
               opt("AAPL", "PUT", 90, "2026-07-17", -1, 3.0)],
              cash=10_300.0)                          # +$300 premium received
    rep = gp.compute_ghost_series([s0, s1])
    assert rep.status == "ok"
    d1 = rep.days[1]
    assert d1.ghost_nav == 19_800.0                   # unchanged
    assert d1.gap == 0.0                              # sale was NAV-neutral
    assert not any("mirrored" in e and "sh" in e for e in rep.events)


# ── Equity mirroring ──────────────────────────────────────────────────────


def test_equity_trade_is_mirrored_at_that_days_mark():
    """Real account buys 10 MSFT @ $50 on day 2 (cash −$500). The ghost
    mirrors the shares AND the cash flow at the same mark → ghost NAV
    unchanged, gap unchanged, event recorded."""
    s0 = base_day()
    s1 = snap("2026-06-02",
              [eq("AAPL", 100, 100.0), eq("MSFT", 10, 50.0),
               opt("AAPL", "PUT", 95, "2026-06-19", -1, 2.0)],
              cash=9_500.0)
    rep = gp.compute_ghost_series([s0, s1])
    assert rep.status == "ok"
    d1 = rep.days[1]
    assert d1.ghost_nav == 19_800.0                   # −500 cash +500 stock
    assert d1.gap == 0.0
    assert any("mirrored MSFT +10 sh @ $50.00" in e for e in rep.events)
    # Day 3: MSFT doubles — BOTH real and ghost hold it, gap still 0.
    s2 = snap("2026-06-03",
              [eq("AAPL", 100, 100.0), eq("MSFT", 10, 100.0),
               opt("AAPL", "PUT", 95, "2026-06-19", -1, 2.0)],
              cash=9_500.0)
    rep2 = gp.compute_ghost_series([s0, s1, s2])
    assert rep2.days[2].gap == 0.0
    assert rep2.days[2].ghost_nav == 20_300.0


def test_assignment_share_change_is_not_mirrored():
    """Synthetic assignment: the short AAPL $95P disappears at expiry with
    AAPL at $90 (ITM) and the real account +100 shares / −$9,500 cash.
    The ghost does NOT buy those shares — its NAV stays cash + 100·mark —
    and the event log says option-caused."""
    s0 = base_day(cash=12_000.0, put_mid=5.0)         # real 12k+10k−500
    s1 = snap("2026-06-22",                           # Monday after expiry
              [eq("AAPL", 200, 90.0)],                # +100 sh assigned
              cash=12_000.0 - 9_500.0)                # paid strike ×100
    rep = gp.compute_ghost_series([s0, s1])
    assert rep.status == "ok"
    d1 = rep.days[1]
    # Ghost: cash 12,000 − 500 (book closed at inception) + 100 sh @ 90.
    assert d1.ghost_nav == 11_500.0 + 9_000.0
    assert any("option-caused" in e and "AAPL" in e for e in rep.events)
    assert not any(e.startswith("2026-06-22: mirrored AAPL")
                   for e in rep.events)


def test_deposit_is_mirrored_into_ghost_cash():
    """A +$3,000 real cash deposit (no trades, no mark moves — a 15% NAV
    jump beyond the 8% flow threshold) is mirrored into ghost cash so the
    gap does NOT count it as an options gain."""
    s0 = base_day()
    s1 = base_day(d="2026-06-02", cash=13_000.0)      # +3,000 external
    rep = gp.compute_ghost_series([s0, s1])
    assert rep.status == "ok"
    d1 = rep.days[1]
    assert d1.gap == 0.0
    assert d1.ghost_nav == 22_800.0
    assert any("external flow +3,000" in e for e in rep.events)


# ── Fail-open + file I/O ──────────────────────────────────────────────────


def _write_snap(root: Path, s: dict, *, drop=()):
    d = root / s["date"]
    d.mkdir(parents=True)
    if "positions" not in drop:
        (d / "positions.json").write_text(json.dumps(s["positions"]))
    if "balance" not in drop:
        (d / "balance.json").write_text(json.dumps(s["balance"]))
    (d / "quotes.json").write_text(json.dumps(s.get("quotes") or {}))


def test_fail_open_on_missing_snapshot_days(tmp_path):
    """A day missing balance.json and a corrupt positions.json day are both
    skipped; the series continues across them (gap_delta spans the hole)."""
    root = tmp_path / "briefing_snapshots"
    _write_snap(root, base_day())
    _write_snap(root, base_day(d="2026-06-02", put_mid=1.5), drop=("balance",))
    bad = root / "2026-06-03"
    bad.mkdir()
    (bad / "positions.json").write_text("{corrupt json")
    (bad / "balance.json").write_text("{}")
    _write_snap(root, base_day(d="2026-06-04", put_mid=1.0))
    # Non-date + .test dirs never pollute the series.
    (root / "2026-06-02.test").mkdir()
    rep = gp.build_ghost_report(root, persist_state=False)
    assert rep.status == "ok"
    assert [d.date for d in rep.days] == [date(2026, 6, 1), date(2026, 6, 4)]
    assert rep.days[1].gap == 100.0                   # decay 2.0 → 1.0


def test_compute_never_raises_on_garbage():
    """Garbage input → 'insufficient_history'/'unavailable', never a raise
    (the briefing must ship)."""
    assert gp.compute_ghost_series(None).status == "insufficient_history"
    assert gp.compute_ghost_series([{"date": "nope"}]).status \
        == "insufficient_history"
    assert gp.compute_ghost_series(
        [{"date": "2026-06-01", "positions": "not-a-list",
          "balance": {}}]).status == "insufficient_history"


def test_backfill_is_idempotent(tmp_path):
    """Re-running the backfill over the same snapshots produces an identical
    series (full recompute, atomic overwrite)."""
    root = tmp_path / "briefing_snapshots"
    _write_snap(root, base_day())
    _write_snap(root, base_day(d="2026-06-02", put_mid=1.5))
    r1 = gp.build_ghost_report(root, persist_state=True)
    p1 = json.loads(gp.state_path(root).read_text())
    r2 = gp.build_ghost_report(root, persist_state=True)
    p2 = json.loads(gp.state_path(root).read_text())
    assert r1.to_dict() == r2.to_dict()
    p1.pop("generated_at"), p2.pop("generated_at")
    assert p1 == p2
    assert gp.state_path(root) == root.parent / "ghost_portfolio.json"


# ── Render ────────────────────────────────────────────────────────────────


def _three_day_report():
    s0 = base_day(put_mid=2.0)
    s1 = base_day(d="2026-06-02", put_mid=1.5)
    s2 = base_day(d="2026-06-03", put_mid=1.0)
    return gp.compute_ghost_series([s0, s1, s2])


def test_render_ghost_panel_lines():
    """Benchmark-section block: header, real-vs-ghost table rows for
    inception + today, the summary bullet, and the assumptions footnote."""
    md = "\n".join(render_ghost_panel(_three_day_report()))
    assert "### 👻 Ghost Portfolio (no-options counterfactual)" in md
    assert "| Inception (2026-06-01) | $19,800 | $19,800 | $0 |" in md
    assert "| Today (2026-06-03) | $19,900 | $19,800 | **+$100** |" in md
    assert "Options program net since 2026-06-01: +$100" in md
    assert "_Assumptions:" in md
    assert "option cash flows stripped" in md


def test_render_money_plan_line():
    """The 💰 Money Plan bullet carries the measured figures only."""
    line = ghost_money_plan_line(_three_day_report())
    assert line is not None
    assert line.startswith("- **Wheel vs Ghost:** ")
    assert "+$100 since 2026-06-01 (options program net)" in line
    assert "this month" in line


def test_render_fails_open_on_bad_report():
    assert render_ghost_panel(None) == []
    assert ghost_money_plan_line(None) is None
    bad = gp.GhostReport(status="unavailable")
    assert render_ghost_panel(bad) == []
    assert ghost_money_plan_line(bad) is None


def test_summary_figures_windows():
    """summary_figures: total gap from the latest day; month window starts
    at the first snapshot of the latest day's month; 7d window measured
    between available snapshots — never extrapolated."""
    rep = _three_day_report()
    figs = gp.summary_figures(rep)
    assert figs["total_gap"] == 100.0
    assert figs["inception"] == "2026-06-01"
    assert figs["month_gap"] == 100.0                 # Jun 1 → Jun 3
    assert figs["week_gap"] == 100.0
    assert gp.summary_figures(gp.GhostReport(status="unavailable")) \
        == {"total_gap": None, "month_gap": None, "week_gap": None,
            "inception": None, "as_of": None}
