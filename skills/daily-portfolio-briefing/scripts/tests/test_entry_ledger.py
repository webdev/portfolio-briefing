"""🎓 Entry-grade ledger + scorecard tests (rule #33 — TDD).

George (2026-08-12): "can we incorporate these learnings in the daily
briefing? i want to make sure we have a running score of our entries."

Pins the spec's contract:
  (a) seed idempotence — a second run adds nothing; the ledger file is
      byte-identical;
  (b) new-open grading + LOCK — a detected open is graded with entry-day
      conditions; later runs with different conditions never regrade it;
  (c) close fills the outcome once; a roll closes the old record and
      opens a new one flagged roll: true;
  (d) rolling averages / trend / distribution math;
  (e) outcome table gated at ≥3 closed records total (buckets show n=);
  (f) discipline callouts derive thresholds from config (rule #19);
  (g) digest line + Since-Yesterday grade line;
  (h) config off = byte-identical legacy (no writes, no panels).
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import entry_ledger  # noqa: E402
from analysis.briefing_diff import (  # noqa: E402
    render_diff_panel,
    render_executed_open_lines,
)

CFG = {"entry_scorecard": {"enabled": True},
       "setup_grade": {"enabled": True}}

DATES = ["2026-08-03", "2026-08-04", "2026-08-05"]
TODAY = "2026-08-05"


def _pos(symbol="NVDA_PUT_200_20261218", underlying="NVDA", typ="PUT",
         strike=200.0, exp="2026-12-18", qty=-1.0, cost_per_share=5.5,
         premium_received=5.5, current_mid=5.0, total_gain=None,
         **extra) -> dict:
    p = {"symbol": symbol, "assetType": "OPTION", "underlying": underlying,
         "type": typ, "strike": strike, "expiration": exp, "qty": qty,
         "costPerShare": cost_per_share, "premiumReceived": premium_received,
         "currentMid": current_mid, "totalGain": total_gain,
         "costBasis": -0.0}
    p.update(extra)
    return p


_TECH = {
    "NVDA": {"rsi_14": 42.0, "iv_rank": 71.0, "sma_200": 180.0,
             "drawdown_pct": 12.0, "spot": 210.0,
             "support_resistance": {
                 "spot": 210.0,
                 "supports": [{"price": 198.0, "touches": 3,
                               "strength": 4.0}],
                 "resistances": []},
             "deep": {"long_term_verdict": "secular-uptrend"}},
    "AAPL": {"rsi_14": 44.0, "iv_rank": 65.0, "sma_200": 140.0,
             "drawdown_pct": 10.0, "spot": 155.0},
}
_QUOTES = {"NVDA": {"dayChangePct": -0.021},
           "AAPL": {"dayChangePct": -0.01}}


def _write_snap(root: Path, d: str, positions: list) -> None:
    snap = root / d
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "positions.json").write_text(json.dumps(positions))
    (snap / "technicals.json").write_text(json.dumps(_TECH))
    (snap / "quotes.json").write_text(json.dumps(_QUOTES))
    (snap / "earnings.json").write_text(json.dumps({}))


@pytest.fixture()
def env(tmp_path):
    """Archive: AAPL $150P from the earliest day (before-archive), NVDA
    $200P first appears 2026-08-04 (in-archive graded seed)."""
    root = tmp_path / "state" / "briefing_snapshots"
    aapl = _pos(symbol="AAPL_PUT_150_20261218", underlying="AAPL",
                strike=150.0, cost_per_share=2.5, premium_received=2.5,
                current_mid=2.0, total_gain=50.0)
    for d in DATES:
        positions = [dict(aapl)]
        if d >= "2026-08-04":
            positions.append(_pos(total_gain=50.0))
        _write_snap(root, d, positions)
    snapshot_dir = root / TODAY
    snapshot_data = {
        "positions": json.loads((snapshot_dir / "positions.json").read_text()),
        "technicals": _TECH, "quotes": _QUOTES,
    }
    return {"root": root, "snapshot_dir": snapshot_dir,
            "snapshot_data": snapshot_data,
            "ledger_path": tmp_path / "state" / "entry_grade_ledger.json"}


def _maintain(env, **kw):
    args = dict(snapshot_dir=env["snapshot_dir"],
                snapshot_data=env["snapshot_data"],
                prev_positions=None, today_iso=TODAY, config=CFG,
                ledger_path=env["ledger_path"],
                snapshots_root=env["root"])
    args.update(kw)
    return entry_ledger.maintain(**args)


# ── (h) config off = legacy ──────────────────────────────────────────────


def test_disabled_no_writes_no_update(env):
    """entry_scorecard.enabled off (the in-code default) → maintain
    returns None and NOTHING is written — byte-identical legacy."""
    out = entry_ledger.maintain(
        snapshot_dir=env["snapshot_dir"], snapshot_data=env["snapshot_data"],
        prev_positions=None, today_iso=TODAY, config={},
        ledger_path=env["ledger_path"], snapshots_root=env["root"])
    assert out is None
    assert not env["ledger_path"].exists()
    assert entry_ledger.entry_scorecard_enabled(None) is False
    assert entry_ledger.entry_scorecard_enabled(CFG) is True


def test_legacy_open_lines_unchanged_without_grades():
    """No entry_grades → render_executed_open_lines output is
    byte-identical to the legacy signature (config off = legacy)."""
    opens = [{"symbol": "MELI_PUT_1460_20270618", "underlying": "MELI",
              "type": "PUT", "strike": 1460.0, "expiration": "2027-06-18",
              "qty": 1.0, "obligation": 146000.0, "pct_of_nlv": 13.3,
              "coverage_with": None, "coverage_without": None,
              "total_put_obligations": 300000.0}]
    assert render_executed_open_lines(opens) == \
        render_executed_open_lines(opens, entry_grades=None)


# ── (a) seed + idempotence ───────────────────────────────────────────────


def test_seed_grades_open_positions_from_archive(env):
    out = _maintain(env)
    assert out["seeded"] == 2 and out["new_opens"] == 0 and out["closed"] == 0
    ledger = entry_ledger.load_ledger(env["ledger_path"])
    by_und = {e["contract"]["underlying"]: e for e in ledger["entries"]}
    nvda = by_und["NVDA"]
    assert nvda["entry_date"] == "2026-08-04"  # first archive appearance
    assert nvda["grade"]["letter"] in ("A", "A-", "B", "C", "D")
    assert nvda["grade"]["score"] is not None
    assert nvda["seeded"] is True and nvda["status"] == "open"
    assert nvda["premium"] == pytest.approx(5.5)
    # AAPL predates the archive — never guessed, not graded (rule #19)
    aapl = by_und["AAPL"]
    assert aapl["before_archive"] is True
    assert aapl["grade"]["letter"] == "n/a"


def test_seed_idempotent_second_run_byte_identical(env):
    """Spec (a): a second run adds nothing and the grades are unchanged —
    the ledger file is byte-identical."""
    _maintain(env)
    first = env["ledger_path"].read_bytes()
    out2 = _maintain(env)
    assert out2["seeded"] == 0 and out2["new_opens"] == 0 \
        and out2["closed"] == 0
    assert env["ledger_path"].read_bytes() == first


# ── (b) new-open grading + lock ──────────────────────────────────────────


def _env_with_open(env):
    """Yesterday's book had only AAPL; today NVDA appeared → detected open."""
    prev = [p for p in env["snapshot_data"]["positions"]
            if p["underlying"] == "AAPL"]
    return prev


def test_detected_open_graded_with_entry_day_conditions(env):
    prev = _env_with_open(env)
    out = _maintain(env, prev_positions=prev)
    assert out["new_opens"] == 1
    grades = out["new_open_grades"]
    assert "NVDA_PUT_200_20261218" in grades
    g = grades["NVDA_PUT_200_20261218"]
    assert g["letter"] in ("A", "A-", "B", "C", "D")
    assert g["top_driver"]  # e.g. 'RSI 42 prime'
    ledger = entry_ledger.load_ledger(env["ledger_path"])
    nvda = next(e for e in ledger["entries"]
                if e["contract"]["underlying"] == "NVDA")
    assert nvda["seeded"] is False and nvda["roll"] is False
    assert nvda["entry_date"] == TODAY


def test_grade_locks_at_first_write_never_regraded(env):
    """Spec (b): 'Grades LOCK at first write — never regraded on later
    runs (the score reflects the day you traded).' A later run with very
    different conditions leaves the stored grade byte-identical."""
    prev = _env_with_open(env)
    _maintain(env, prev_positions=prev)
    before = copy.deepcopy(entry_ledger.load_ledger(env["ledger_path"]))
    # Conditions swing wildly the next run — RSI 75 would hard-block a
    # NEW put grade, but the locked record must not move.
    changed = copy.deepcopy(env["snapshot_data"])
    changed["technicals"]["NVDA"]["rsi_14"] = 75.0
    out = _maintain(env, snapshot_data=changed,
                    prev_positions=env["snapshot_data"]["positions"])
    assert out["new_opens"] == 0
    after = entry_ledger.load_ledger(env["ledger_path"])
    nv_before = next(e for e in before["entries"]
                     if e["contract"]["underlying"] == "NVDA")
    nv_after = next(e for e in after["entries"]
                    if e["contract"]["underlying"] == "NVDA")
    assert nv_after["grade"] == nv_before["grade"]


# ── (c) close fills outcome; roll closes + opens with flag ───────────────


def test_close_fills_outcome_once(env):
    _maintain(env)  # seed both
    prev = env["snapshot_data"]["positions"]
    # Today NVDA is gone (closed at the broker) — AAPL remains.
    today_positions = [p for p in prev if p["underlying"] == "AAPL"]
    sd = dict(env["snapshot_data"], positions=today_positions)
    out = _maintain(env, snapshot_data=sd, prev_positions=prev,
                    today_iso="2026-08-06")
    assert out["closed"] == 1
    ledger = entry_ledger.load_ledger(env["ledger_path"])
    nvda = next(e for e in ledger["entries"]
                if e["contract"]["underlying"] == "NVDA")
    assert nvda["status"] == "closed"
    oc = nvda["outcome"]
    assert oc["close_date"] == "2026-08-06"
    assert oc["realized_pnl"] == pytest.approx(50.0)  # prev totalGain
    # capture = (5.5 − 5.0)/5.5 — measured from the last mark
    assert oc["capture_pct"] == pytest.approx(9.1, abs=0.1)
    assert oc["days_held"] == 2  # 2026-08-04 → 2026-08-06
    assert oc["estimated"] is True and oc["roll"] is False
    # A second run never refills the outcome.
    out2 = _maintain(env, snapshot_data=sd, prev_positions=prev,
                     today_iso="2026-08-07")
    assert out2["closed"] == 0


def test_close_outcome_fail_closed_without_prior_mark(env):
    """Rule #19: no prior-day record of the contract → outcome fields are
    None with the reason, never a guessed fill."""
    _maintain(env)
    prev = [p for p in env["snapshot_data"]["positions"]
            if p["underlying"] == "AAPL"]  # NVDA missing from prev too
    sd = dict(env["snapshot_data"], positions=list(prev))
    _maintain(env, snapshot_data=sd, prev_positions=prev,
              today_iso="2026-08-06")
    ledger = entry_ledger.load_ledger(env["ledger_path"])
    nvda = next(e for e in ledger["entries"]
                if e["contract"]["underlying"] == "NVDA")
    assert nvda["status"] == "closed"
    assert nvda["outcome"]["realized_pnl"] is None
    assert nvda["outcome"]["capture_pct"] is None
    assert "not measurable" in nvda["outcome"]["basis"]


def test_roll_closes_old_and_opens_new_flagged(env):
    """Spec: 'Roll = close old record (outcome) + new record for the STO
    leg, flagged roll:true.'"""
    _maintain(env)  # seed NVDA $200P + AAPL
    prev = env["snapshot_data"]["positions"]
    rolled = _pos(symbol="NVDA_PUT_190_20270115", strike=190.0,
                  exp="2027-01-15", premium_received=7.0,
                  cost_per_share=7.0, current_mid=7.0)
    today_positions = [p for p in prev if p["underlying"] == "AAPL"] + [rolled]
    sd = dict(env["snapshot_data"], positions=today_positions)
    out = _maintain(env, snapshot_data=sd, prev_positions=prev,
                    today_iso="2026-08-06")
    assert out["closed"] == 1 and out["new_opens"] == 1
    ledger = entry_ledger.load_ledger(env["ledger_path"])
    old = next(e for e in ledger["entries"]
               if e["contract"]["strike"] == 200.0)
    new = next(e for e in ledger["entries"]
               if e["contract"]["strike"] == 190.0)
    assert old["status"] == "closed" and old["outcome"]["roll"] is True
    assert new["status"] == "open" and new["roll"] is True
    assert new["grade"]["score"] is not None  # STO leg graded + locked


def test_empty_positions_never_mass_closes(env):
    """An account that failed to load (empty positions) is a data gap,
    not a mass exit — no records are closed."""
    _maintain(env)
    sd = dict(env["snapshot_data"], positions=[])
    out = _maintain(env, snapshot_data=sd,
                    prev_positions=env["snapshot_data"]["positions"],
                    today_iso="2026-08-06")
    assert out["closed"] == 0
    ledger = entry_ledger.load_ledger(env["ledger_path"])
    assert all(e["status"] == "open" for e in ledger["entries"])


# ── (d) scorecard math ───────────────────────────────────────────────────


def _entry(und, strike, date, score, letter, side="short_put",
           status="open", capture=None, conditions=None, roll=False):
    e = {
        "id": f"{und}|PUT|{strike:g}|2026-12-18|{date}",
        "contract": {"underlying": und, "type": "PUT", "strike": strike,
                     "expiration": "2026-12-18"},
        "symbol": f"{und}_PUT_{strike:g}_20261218",
        "label": f"{und} ${strike:g}P",
        "entry_date": date, "before_archive": False, "side": side,
        "qty": 1.0, "premium": 5.0, "premium_source": "costPerShare",
        "grade": {"letter": letter, "score": score,
                  "drivers": ["RSI 42 prime"], "hard_blocked": False,
                  "message": ""},
        "conditions": conditions or {"rsi": 42.0, "iv_rank": 70.0,
                                     "iv_source": "rv"},
        "iv_source": "rv", "seeded": True, "roll": roll,
        "status": status,
        "outcome": ({"close_date": "2026-08-10", "realized_pnl": 100.0,
                     "capture_pct": capture, "days_held": 5, "roll": False,
                     "estimated": True, "basis": "test"}
                    if status == "closed" else None),
    }
    return e


def test_scorecard_averages_trend_distribution():
    """Spec (d): rolling averages (last 10 / last 30d / all-time), the
    week-over-week trend arrow, and the grade distribution are all
    measured from the ledger."""
    entries = (
        # old entries (outside 30d of 2026-08-05): scores 30, 30
        [_entry("OLD", 100 + i, "2026-06-01", 30.0, "D") for i in range(2)]
        # prior week (7-14d back): scores 40, 40
        + [_entry("PRI", 200 + i, "2026-07-26", 40.0, "D") for i in range(2)]
        # this week: scores 70, 80
        + [_entry("NEW", 300, "2026-08-04", 70.0, "B"),
           _entry("NEW", 310, "2026-08-05", 80.0, "A-")]
    )
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        CFG)
    assert sc["graded"] == 6
    assert sc["averages"]["all_time"]["avg"] == pytest.approx(48.3, abs=0.1)
    # last 30d = prior week + this week = (40+40+70+80)/4
    assert sc["averages"]["last_30d"]["avg"] == pytest.approx(57.5)
    assert sc["averages"]["last_30d"]["n"] == 4
    assert sc["averages"]["last_10"]["n"] == 6
    assert sc["trend"]["this_week"] == pytest.approx(75.0)
    assert sc["trend"]["prior_week"] == pytest.approx(40.0)
    assert sc["trend"]["arrow"] == "↗"
    assert sc["distribution"] == {"A-": 1, "B": 1, "D": 4}
    # last_5 is most-recent-first
    assert sc["last_5"][0]["label"] == "NEW $310P"
    assert sc["last_5"][0]["letter"] == "A-"


def test_trend_absent_without_both_windows():
    """No graded entry in the prior week → no arrow (never an arrow on
    missing data — rule #19)."""
    entries = [_entry("NEW", 300, "2026-08-04", 70.0, "B")]
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        CFG)
    assert sc["trend"] is None


# ── (e) outcome table gated at ≥3 closed ─────────────────────────────────


def test_outcome_table_too_small_below_three():
    """Spec (e): 'never render the table with fewer, show "outcome sample
    too small (n=2)" instead.'"""
    entries = [
        _entry("A1", 100, "2026-08-01", 90.0, "A", status="closed",
               capture=64.0),
        _entry("B1", 110, "2026-08-02", 55.0, "C", status="closed",
               capture=38.0),
    ]
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        CFG)
    assert sc["outcome"]["n"] == 2
    assert sc["outcome"]["by_bucket"] is None
    md = "\n".join(entry_ledger.render_scorecard_panel(sc))
    assert "outcome sample too small (n=2)" in md
    assert "avg capture" not in md


def test_outcome_table_buckets_with_counts_at_three_plus():
    entries = [
        _entry("A1", 100, "2026-08-01", 90.0, "A", status="closed",
               capture=64.0),
        _entry("A2", 105, "2026-08-01", 70.0, "B", status="closed",
               capture=60.0),
        _entry("C1", 110, "2026-08-02", 55.0, "C", status="closed",
               capture=38.0),
        _entry("D1", 120, "2026-08-03", 30.0, "D", status="closed",
               capture=21.0),
    ]
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        CFG)
    bb = sc["outcome"]["by_bucket"]
    assert bb["A/B"] == {"avg_capture_pct": 62.0, "n": 2}
    assert bb["C"] == {"avg_capture_pct": 38.0, "n": 1}
    assert bb["D"] == {"avg_capture_pct": 21.0, "n": 1}
    md = "\n".join(entry_ledger.render_scorecard_panel(sc))
    assert "A/B: +62% avg capture (n=2)" in md
    assert "C: +38% avg capture (n=1)" in md
    assert "estimates, not fills" in md


def test_closed_without_measured_capture_not_counted():
    """A close whose capture wasn't measurable never pads the sample."""
    entries = [
        _entry("A1", 100, "2026-08-01", 90.0, "A", status="closed",
               capture=None),
        _entry("A2", 105, "2026-08-01", 70.0, "B", status="closed",
               capture=60.0),
    ]
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        CFG)
    assert sc["outcome"]["n"] == 1


# ── (f) discipline callouts — thresholds from config ─────────────────────


def test_callouts_derive_thresholds_from_config():
    """Spec (f): callout thresholds come from setup_grade /
    rsi_discipline config — NEVER hardcoded in the strings (rule #19)."""
    cfg = {"entry_scorecard": {"enabled": True},
           "setup_grade": {"enabled": True, "vol_floor_rank": 55},
           "rsi_discipline": {"put_entry_band": [40, 50]}}
    entries = [
        _entry("T1", 100, "2026-08-01", 40.0, "D",
               conditions={"rsi": 60.0, "iv_rank": 30.0, "iv_source": "rv"}),
        _entry("T2", 110, "2026-08-02", 40.0, "D",
               conditions={"rsi": 62.0, "iv_rank": 35.0, "iv_source": "rv"}),
        _entry("T3", 120, "2026-08-03", 40.0, "D",
               conditions={"rsi": 45.0, "iv_rank": 80.0, "iv_source": "rv"}),
    ]
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        cfg)
    joined = " | ".join(sc["callouts"])
    assert "2 of last 3 put entries had IV/RV rank < 55" in joined
    assert "RSI outside the 40-50 entry band" in joined


def test_callouts_silent_below_repeat_threshold():
    """One off-pattern entry is noise, not a callout."""
    entries = [
        _entry("T1", 100, "2026-08-01", 40.0, "D",
               conditions={"rsi": 42.0, "iv_rank": 30.0, "iv_source": "rv"}),
        _entry("T2", 110, "2026-08-02", 70.0, "B",
               conditions={"rsi": 44.0, "iv_rank": 80.0, "iv_source": "rv"}),
        _entry("T3", 120, "2026-08-03", 70.0, "B",
               conditions={"rsi": 45.0, "iv_rank": 82.0, "iv_source": "rv"}),
    ]
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        CFG)
    assert sc["callouts"] == []


def test_cc_mid_band_callout():
    entries = [
        _entry("T1", 100, "2026-08-01", 20.0, "D", side="short_call",
               conditions={"rsi": 48.0, "iv_rank": 70.0, "iv_source": "rv"}),
        _entry("T2", 110, "2026-08-02", 22.0, "D", side="short_call",
               conditions={"rsi": 50.0, "iv_rank": 70.0, "iv_source": "rv"}),
        _entry("T3", 120, "2026-08-03", 90.0, "A", side="short_call",
               conditions={"rsi": 72.0, "iv_rank": 70.0, "iv_source": "rv"}),
    ]
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        CFG)
    joined = " | ".join(sc["callouts"])
    assert "2 of last 3 covered-call entries were written below RSI 60" \
        in joined


# ── (g) digest line + Since-Yesterday grade line ─────────────────────────


def test_digest_line_measured_only():
    """Spec (g): '🎓 Entry quality: last 10 avg … · last entry … —
    measured only.'"""
    entries = [
        _entry("MU", 950, "2026-08-04", 31.0, "D",
               conditions={"rsi": 42.0, "iv_rank": 34.0, "iv_source": "rv"}),
        _entry("PRI", 200, "2026-07-27", 45.0, "C"),
    ]
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        CFG)
    dl = sc["digest_line"]
    assert dl.startswith("🎓 Entry quality: last 10 avg ")
    assert "last entry MU $950P — D (RSI 42 prime)" in dl
    # trend has both windows here → arrow present
    assert sc["trend"] is not None and sc["trend"]["arrow"] in dl


def test_panel_carries_bold_digest_line_and_digest_keeps_it():
    """The FULL briefing's scorecard panel leads with the bold 🎓 line;
    render/digest.py keeps exactly that line in the Health zone
    (pure-subset rule)."""
    entries = [_entry("MU", 950, "2026-08-04", 31.0, "D")]
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        CFG)
    panel = entry_ledger.render_scorecard_panel(sc)
    bold = [ln for ln in panel if ln.startswith("**🎓")]
    assert len(bold) == 1

    from render.digest import build_digest
    full_md = "\n".join(
        ["# Daily Briefing — 2026-08-05", "",
         "## Today's Action List", "", "1. **CLOSE** MU_PUT_950_20261218",
         ""] + panel)  # panel carries its own '## 🎓 Entry Scorecard' header
    digest = build_digest(full_md, config={}, extras={"date": "2026-08-05"})
    assert bold[0] in digest
    # the full panel body stays in the full briefing only
    assert "Running averages" not in digest


def test_since_yesterday_open_line_gains_grade():
    """Spec (g): each detected open's Since-Yesterday line gains its
    just-assigned grade + top driver."""
    opens = [{"symbol": "NVDA_PUT_200_20261218", "underlying": "NVDA",
              "type": "PUT", "strike": 200.0, "expiration": "2026-12-18",
              "qty": 1.0, "obligation": 20000.0, "pct_of_nlv": 2.0,
              "coverage_with": None, "coverage_without": None,
              "total_put_obligations": 100000.0}]
    grades = {"NVDA_PUT_200_20261218": {
        "letter": "B", "score": 68.0, "top_driver": "RSI 42 prime",
        "label": "NVDA $200P"}}
    lines = render_executed_open_lines(opens, entry_grades=grades)
    joined = "\n".join(lines)
    assert "🎓 Entry grade: **B** (68/100) — RSI 42 prime" in joined
    assert "locked to entry-day conditions" in joined
    # and through the full panel path
    panel = render_diff_panel("## Today's Action List\n", "## Today's Action List\n1. **X** Y",
                              executed_opens=opens, entry_grades=grades,
                              today_iso="2026-08-05")
    assert any("🎓 Entry grade: **B**" in ln for ln in panel)


def test_no_grade_line_for_na_grade():
    """A grade that couldn't be measured (n/a) renders NO line — never
    fabricated (rule #19)."""
    opens = [{"symbol": "X_PUT_10_20261218", "underlying": "X",
              "type": "PUT", "strike": 10.0, "expiration": "2026-12-18",
              "qty": 1.0, "obligation": 1000.0, "pct_of_nlv": None,
              "coverage_with": None, "coverage_without": None,
              "total_put_obligations": 1000.0}]
    lines = render_executed_open_lines(
        opens, entry_grades={"X_PUT_10_20261218": {"letter": "n/a"}})
    assert not any("Entry grade" in ln for ln in lines)


# ── panel rendering edges ────────────────────────────────────────────────


def test_panel_empty_when_nothing_graded():
    sc = entry_ledger.compute_scorecard({"entries": []}, "2026-08-05", CFG)
    assert entry_ledger.render_scorecard_panel(sc) == []
    assert sc["digest_line"] is None


def test_ledger_write_is_atomic_and_loadable(env):
    _maintain(env)
    data = json.loads(env["ledger_path"].read_text())
    assert data["version"] == entry_ledger.LEDGER_VERSION
    assert isinstance(data["entries"], list) and data["entries"]
    # no stray tempfiles left behind
    stray = [p for p in env["ledger_path"].parent.iterdir()
             if p.name.startswith("entry_grade_ledger.json.")]
    assert stray == []
