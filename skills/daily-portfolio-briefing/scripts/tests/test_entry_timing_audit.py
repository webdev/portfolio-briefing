"""Entry Timing Audit tests (rule #33 — TDD, docstrings quote the user).

George (2026-08-12): "figure out whether my current options were sold or
bought at the best time. Is there any way to tell that?"

Fixture-based: synthetic snapshot dirs with a contract appearing
mid-archive. Pins entry-date detection (incl. the before-archive case),
premium math, retro-grade wiring into analysis/setup_grade, local-peak
capture math + insufficient-history fail-closed, MFE/MAE from marks,
aggregate grade buckets, and the long-leg 'n/a — hedge' path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import entry_audit  # noqa: E402


DATES = ["2026-01-05", "2026-01-06", "2026-01-07",
         "2026-01-08", "2026-01-09", "2026-01-12"]

KEY = ("NVDA", "PUT", 200.0, "2026-03-20")
ENTRY = "2026-01-07"  # contract first appears mid-archive


def _pos(symbol="NVDA_PUT_200_20260320", underlying="NVDA", typ="PUT",
         strike=200.0, exp="2026-03-20", qty=-1.0, cost_per_share=5.4946,
         premium_received=5.5, current_mid=5.0, total_gain=None,
         **extra) -> dict:
    p = {"symbol": symbol, "assetType": "OPTION", "underlying": underlying,
         "type": typ, "strike": strike, "expiration": exp, "qty": qty,
         "costPerShare": cost_per_share, "premiumReceived": premium_received,
         "currentMid": current_mid, "totalGain": total_gain,
         "costBasis": -0.0}
    p.update(extra)
    return p


@pytest.fixture()
def archive(tmp_path):
    """Synthetic snapshot archive.

    - NVDA $200P appears 2026-01-07 (mid-archive) — the audited entry.
    - AAPL $150P is present from the EARLIEST dir (before-archive case).
    - META $500P long (qty +1) held from 2026-01-08 — hedge leg.
    Entry-day snapshot carries technicals/quotes; chains provide the
    pre-entry window mids for the local-peak read.
    """
    root = tmp_path / "briefing_snapshots"
    # NVDA mark by date (currentMid) + broker totalGain marks
    nvda_marks = {"2026-01-07": (5.0, 50.0), "2026-01-08": (6.5, -100.0),
                  "2026-01-09": (4.0, 150.0), "2026-01-12": (2.5, 300.0)}
    for d in DATES:
        snap = root / d
        snap.mkdir(parents=True)
        positions = [
            _pos(symbol="AAPL_PUT_150_20260618", underlying="AAPL",
                 strike=150.0, exp="2026-06-18", cost_per_share=2.5,
                 premium_received=2.5, current_mid=2.0, total_gain=50.0),
        ]
        if d >= ENTRY:
            mid, tg = nvda_marks[d]
            positions.append(_pos(current_mid=mid, total_gain=tg))
        if d >= "2026-01-08":
            positions.append(
                _pos(symbol="META_PUT_500_20261218", underlying="META",
                     typ="PUT", strike=500.0, exp="2026-12-18", qty=1.0,
                     cost_per_share=12.0, premium_received=None,
                     current_mid=11.0, total_gain=-100.0))
        (snap / "positions.json").write_text(json.dumps(positions))
        # chains for NVDA on the two PRE-entry window days
        chains = snap / "chains"
        chains.mkdir()
        chain_mid = {"2026-01-05": (7.0, 8.0), "2026-01-06": (6.0, 7.0)}
        if d in chain_mid:
            bid, ask = chain_mid[d]
            (chains / "NVDA_2026-03-20.json").write_text(json.dumps({
                "underlying": "NVDA", "expiration": "2026-03-20",
                "puts": [{"strike": 200.0, "bid": bid, "ask": ask}],
                "calls": [],
            }))
        (snap / "technicals.json").write_text(json.dumps({
            "NVDA": {"rsi_14": 42.0, "iv_rank": 71.0, "sma_200": 180.0,
                     "drawdown_pct": 12.0, "spot": 210.0,
                     "support_resistance": {
                         "spot": 210.0,
                         "supports": [{"price": 198.0, "touches": 3,
                                       "strength": 4.0}],
                         "resistances": []},
                     "deep": {"long_term_verdict": "secular-uptrend"}},
            "META": {"rsi_14": 55.0, "iv_rank": 40.0, "sma_200": 480.0,
                     "drawdown_pct": 5.0, "spot": 520.0},
        }))
        (snap / "quotes.json").write_text(json.dumps({
            "NVDA": {"dayChangePct": -0.021},
            "META": {"dayChangePct": 0.01},
        }))
        (snap / "earnings.json").write_text(json.dumps(
            {"NVDA": "2026-02-25"}))
    return root


def _run(root, **kw):
    return entry_audit.run_audit(root, **kw)


def _card(result, key):
    return next(c for c in result["cards"] if c["key"] == key)


# ── (a) entry-date detection + before-archive ────────────────────────────


def test_entry_date_first_appearance(archive):
    """The contract's entry date is the FIRST archive date it appears."""
    result = _run(archive)
    card = _card(result, KEY)
    assert card["entry_date"] == ENTRY
    assert card["before_archive"] is False


def test_before_archive_contract_not_graded(archive):
    """A contract already present in the EARLIEST snapshot is marked
    'opened before the archive' and never graded — rule #19, no
    guessing at conditions the archive can't show."""
    result = _run(archive)
    card = _card(result, ("AAPL", "PUT", 150.0, "2026-06-18"))
    assert card["before_archive"] is True
    assert card["grade"]["letter"] == "n/a"
    assert card["conditions"] is None
    assert "opened on or before" in card["verdict"]
    md = entry_audit.render_markdown(result)
    assert "present in the earliest snapshot" in md


# ── (b) premium math ─────────────────────────────────────────────────────


def test_entry_premium_prefers_cost_per_share():
    prem, src = entry_audit.entry_premium_per_share(
        {"costPerShare": 5.4946, "premiumReceived": 5.5})
    assert prem == pytest.approx(5.4946)
    assert src == "costPerShare"


def test_entry_premium_falls_back_to_premium_received():
    prem, src = entry_audit.entry_premium_per_share(
        {"costPerShare": None, "premiumReceived": 2.5})
    assert (prem, src) == (2.5, "premiumReceived")


def test_entry_premium_cost_basis_fallback_and_fail_closed():
    """costBasis is only used when it's a REAL dollar figure — the
    current archive writes -0.0 for options, which must NOT become a
    fabricated premium (rule #19)."""
    prem, src = entry_audit.entry_premium_per_share(
        {"costPerShare": None, "premiumReceived": None,
         "costBasis": -550.0, "qty": -1.0})
    assert prem == pytest.approx(5.50)
    assert src == "costBasis"
    prem, src = entry_audit.entry_premium_per_share(
        {"costPerShare": None, "premiumReceived": None,
         "costBasis": -0.0, "qty": -1.0})
    assert prem is None
    assert src == "n/a"


# ── (c) retro grade wired to setup_grade with ENTRY-date inputs ──────────


def test_retro_grade_calls_csp_setup_with_entry_day_inputs(
        archive, monkeypatch):
    """The retro grade re-runs the PRODUCTION csp_setup scorer with the
    entry-DATE snapshot's own numbers (not today's)."""
    captured = {}
    real = entry_audit._sg.csp_setup

    def spy(**kw):
        captured.update(kw)
        return real(**kw)

    monkeypatch.setattr(entry_audit._sg, "csp_setup", spy)
    result = _run(archive)
    card = _card(result, KEY)
    assert captured["rsi"] == 42.0                 # entry-day technicals
    assert captured["iv_rank"] == 71.0             # RV fallback (no IV hist)
    assert captured["iv_rank_source"] == "rv"
    assert captured["strike"] == 200.0
    assert captured["spot"] == 210.0
    assert captured["sma_200"] == 180.0
    assert captured["day_change_pct"] == -0.021    # entry-day quotes
    assert captured["drawdown_pct"] == 12.0
    assert captured["lt_verdict"] == "secular-uptrend"
    assert captured["days_to_earnings"] == 49      # 2026-02-25 − 2026-01-07
    assert card["grade"]["letter"] in ("A", "A-", "B", "C", "D")
    assert card["grade"]["side"] == "csp"


def test_short_call_routes_to_cc_setup(archive, monkeypatch):
    """Short CALL → cc_setup (side from contract type + qty sign)."""
    called = {}
    monkeypatch.setattr(
        entry_audit._sg, "cc_setup",
        lambda **kw: called.update(kw) or {"side": "cc", "letter": "B",
                                           "score": 70.0, "drivers": [],
                                           "missing": []})
    pos = _pos(symbol="NVDA_CALL_250_20260320", typ="CALL", strike=250.0)
    grade = entry_audit.retro_grade(pos, {"rsi": 65.0})
    assert grade["side"] == "cc"
    assert called["rsi"] == 65.0


def test_chain_iv_rank_at_needs_history_else_none():
    """True IV rank ('IVr') only when the chain-IV history covers the
    entry date with ≥20 trailing obs — else None (RVr fallback happens
    upstream, never a fabricated IVr)."""
    series = {f"2026-01-{d:02d}": {"atm_iv_30d": 0.30 + d * 0.01}
              for d in range(1, 25)}
    hist = {"tickers": {"NVDA": series}}
    rank = entry_audit.chain_iv_rank_at(hist, "NVDA", "2026-01-24")
    assert rank == 100.0  # highest value of its own trailing history
    assert entry_audit.chain_iv_rank_at(hist, "NVDA", "2026-01-05") is None
    assert entry_audit.chain_iv_rank_at({}, "NVDA", "2026-01-24") is None


# ── (d) local-peak capture ───────────────────────────────────────────────


def test_local_peak_capture_math(archive):
    """Window mids: chain 7.5 / 6.5 (pre-entry) + marks 5.0/6.5/4.0/2.5.
    Peak = 7.5; sold at 5.4946 → 73% of local peak."""
    result = _run(archive)
    card = _card(result, KEY)
    peak = card["peak"]
    assert peak["insufficient"] is False
    assert peak["points"] == 6
    assert peak["window_max_mid"] == pytest.approx(7.5)
    assert peak["capture_pct"] == pytest.approx(
        100.0 * 5.4946 / 7.5, abs=0.1)
    assert "% of local peak" in card["verdict"]


def test_local_peak_insufficient_history_fail_closed(tmp_path):
    """<3 usable window points → 'insufficient chain history', never a
    fabricated peak (rule #19)."""
    root = tmp_path / "snaps"
    for d in ["2026-01-05", "2026-01-07"]:
        snap = root / d
        snap.mkdir(parents=True)
        positions = [_pos()] if d == "2026-01-07" else []
        (snap / "positions.json").write_text(json.dumps(positions))
    result = _run(root)
    card = _card(result, KEY)
    assert card["peak"]["insufficient"] is True
    assert "capture_pct" not in card["peak"]
    md = entry_audit.render_markdown(result)
    assert "insufficient chain history" in md


# ── (e) MFE / MAE from daily marks ───────────────────────────────────────


def test_mfe_mae_from_marks(archive):
    """MFE/MAE track the broker totalGain marks since entry: best +300
    (01-12), worst -100 (01-08), current +300 with capture from the
    last mid (2.5 on 5.4946 premium → 54% captured)."""
    result = _run(archive)
    exc = _card(result, KEY)["excursion"]
    assert exc["mfe"] == pytest.approx(300.0)
    assert exc["mfe_date"] == "2026-01-12"
    assert exc["mae"] == pytest.approx(-100.0)
    assert exc["mae_date"] == "2026-01-08"
    assert exc["current_gain"] == pytest.approx(300.0)
    assert exc["current_capture_pct"] == pytest.approx(
        100.0 * (5.4946 - 2.5) / 5.4946, abs=0.1)


def test_daily_gain_computed_when_total_gain_missing():
    """No broker totalGain → computed from premium and the day's mid
    (short: (prem − mid) · |qty| · 100)."""
    g = entry_audit._daily_gain(
        {"totalGain": None, "currentMid": 3.0, "qty": -2.0}, 5.0)
    assert g == pytest.approx((5.0 - 3.0) * 2 * 100)


# ── (f) aggregate grade buckets ──────────────────────────────────────────


def test_aggregate_buckets_by_grade():
    """Aggregate = grade distribution + avg current capture per letter
    bucket over graded shorts; longs and pre-archive opens excluded."""
    cards = [
        {"short": True, "before_archive": False,
         "grade": {"letter": "A", "score": 90.0},
         "excursion": {"current_capture_pct": 60.0}},
        {"short": True, "before_archive": False,
         "grade": {"letter": "A", "score": 88.0},
         "excursion": {"current_capture_pct": 40.0}},
        {"short": True, "before_archive": False,
         "grade": {"letter": "D", "score": 20.0},
         "excursion": {"current_capture_pct": -10.0}},
        {"short": False, "before_archive": False,   # long leg — excluded
         "grade": {"letter": "n/a", "score": None}, "excursion": {}},
        {"short": True, "before_archive": True,     # pre-archive — excluded
         "grade": {"letter": "n/a", "score": None}, "excursion": {}},
    ]
    agg = entry_audit.aggregate(cards)
    assert agg["graded"] == 3
    assert agg["ungraded"] == 2
    assert agg["distribution"] == {"A": 2, "D": 1}
    assert agg["capture_by_grade"]["A"] == pytest.approx(50.0)
    assert agg["capture_by_grade"]["D"] == pytest.approx(-10.0)
    assert agg["avg_score"] == pytest.approx((90 + 88 + 20) / 3, abs=0.1)


def test_companion_pointer_only_when_report_exists(tmp_path):
    """The digest's 📎 Full Detail pointer for the audit report appears
    ONLY when today's report actually exists — never a dangling
    reference to a file the standalone tool hasn't written."""
    assert entry_audit.companion_pointer("2026-08-12", tmp_path) is None
    (tmp_path / "entry_timing_audit_2026-08-12.md").write_text("x")
    assert (entry_audit.companion_pointer("2026-08-12", tmp_path)
            == "entry_timing_audit_2026-08-12.md")
    # second dir searched too; missing dirs tolerated
    assert (entry_audit.companion_pointer(
        "2026-08-12", tmp_path / "nope", tmp_path)
        == "entry_timing_audit_2026-08-12.md")


# ── (g) long legs — 'n/a — hedge' path ───────────────────────────────────


def test_long_leg_gets_na_hedge_grade_but_shows_conditions(archive):
    """A LONG option (hedge/directional leg) is never graded by the
    wheel scorer — letter 'n/a — hedge/long leg, different objective' —
    but its entry conditions still render."""
    result = _run(archive)
    card = _card(result, ("META", "PUT", 500.0, "2026-12-18"))
    assert card["short"] is False
    assert card["grade"]["letter"] == "n/a"
    assert "hedge/long leg" in card["grade"]["message"]
    assert card["conditions"]["rsi"] == 55.0  # conditions still measured
    md = entry_audit.render_markdown(result)
    assert "n/a — hedge/long leg, different objective" in md
    assert "bought" in card["verdict"] or "insufficient" in card["verdict"]
