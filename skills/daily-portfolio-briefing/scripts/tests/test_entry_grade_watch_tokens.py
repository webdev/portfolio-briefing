"""🎓 Entry-grade tokens on Watch option rows (George 2026-08-13).

George: "where in the briefing i can see the entries grades for my
current options" — the per-position entry grades exist in
state/entry_grade_ledger.json (analysis/entry_ledger.py) but the Watch /
Portfolio Review panel didn't show them.

Contract pinned here:
  - every OPTION row/block in the Watch panel carries a compact ledger
    token: ``🎓 entry D (30) · Aug 5`` (letter, score, entry date); the
    FULL block adds the locked top driver as a short parenthetical;
  - missing ledger record → ``🎓 entry n/a`` in the FULL block only
    (one-liners stay clean); hedge/long legs respect the ledger's
    'n/a — hedge' convention; nothing is ever fabricated (rule #19);
  - the ledger is loaded ONCE per render and fail-open (unreadable →
    no tokens, no crash);
  - the '## 🎓 Entry Scorecard' section gains a one-line open-book
    summary (current open positions by entry grade);
  - entry_scorecard.enabled off → byte-identical legacy output;
  - the digest stays a pure subset within its line budget.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import entry_ledger  # noqa: E402
from steps.per_option_commentary import render_watch_with_commentary  # noqa: E402

_CFG = {"entry_scorecard": {"enabled": True}}


# ── fixtures ─────────────────────────────────────────────────────────────


def _ledger_record(und="AMZN", typ="PUT", strike=245.0, exp="2026-10-16",
                   letter="D", score=30.0, drivers=None,
                   entry_date="2026-08-05", status="open",
                   side="short_put", message=None):
    return {
        "id": f"{und}|{typ}|{strike:g}|{exp}|{entry_date}",
        "contract": {"underlying": und, "type": typ, "strike": strike,
                     "expiration": exp},
        "symbol": f"{und}_{typ}_{strike:g}_{exp.replace('-', '')}",
        "label": f"{und} ${strike:g}{'P' if typ == 'PUT' else 'C'}",
        "entry_date": entry_date,
        "side": side,
        "qty": 1.0,
        "premium": 4.95,
        "premium_source": "costPerShare",
        "grade": {"letter": letter, "score": score,
                  "drivers": drivers if drivers is not None
                  else ["RSI 60 off-band ✗", "IVr 24 thin ✗"],
                  "hard_blocked": False, "message": message},
        "conditions": {"rsi": 60.0, "iv_rank": 24.0, "iv_source": "rv"},
        "seeded": False, "roll": False,
        "status": status, "outcome": None,
    }


def _write_ledger(tmp_path, records):
    p = tmp_path / "entry_grade_ledger.json"
    p.write_text(json.dumps({"version": 1, "entries": records}),
                 encoding="utf-8")
    return p


def _hold_review():
    return {"contract": "AMZN_PUT_245_20261016", "recommendation": "HOLD",
            "type": "PUT", "strike": 245.0, "expiration": "2026-10-16",
            "days_to_expiry": 71, "entry_price": 5.0, "current_mid": 4.7,
            "qty": -1, "underlying": "AMZN",
            "rationale": "MODERATE OTM with low profit. Hold for more decay."}


def _snap(ledger_path=None, config=_CFG):
    snap = {"technicals": {"AMZN": {"rsi_14": 63.0}}, "quotes": {}}
    if config is not None:
        snap["_config"] = config
    if ledger_path is not None:
        snap["entry_ledger_update"] = {"ledger_path": str(ledger_path)}
    return snap


# ── (a) real ledger values on one-liner + full block ─────────────────────


def test_hold_one_liner_carries_compact_token(tmp_path):
    """George: 'where in the briefing i can see the entries grades for my
    current options' — the compact HOLD one-liner carries the ledger's
    locked grade: `🎓 entry D (30) · Aug 5`."""
    lp = _write_ledger(tmp_path, [_ledger_record()])
    md_lines = render_watch_with_commentary(
        [], [_hold_review()], _snap(lp), compact=True)
    row = [ln for ln in md_lines if "AMZN_PUT_245_20261016" in ln]
    assert len(row) == 1
    assert "🎓 entry D (30) · Aug 5" in row[0]
    # One-liner never carries the driver parenthetical (full-block only).
    assert "off-band" not in row[0]


def test_full_block_carries_token_with_top_driver(tmp_path):
    """The full Watch block shows the same token plus the locked top
    driver as a short parenthetical: `🎓 entry D (30, RSI 60 off-band) ·
    Aug 5` — driver text is the ledger's, marks stripped, never
    rephrased."""
    lp = _write_ledger(tmp_path, [_ledger_record()])
    md = "\n".join(render_watch_with_commentary(
        [], [_hold_review()], _snap(lp), compact=False))
    assert "🎓 entry D (30, RSI 60 off-band) · Aug 5" in md


def test_review_dict_gains_entry_grade_for_webapp_json(tmp_path):
    """The webapp Options tab renders from the briefing JSON's structured
    options_reviews — the render attaches the same locked record so the
    structured surface can't drop the grade (rule #31)."""
    lp = _write_ledger(tmp_path, [_ledger_record()])
    rev = _hold_review()
    render_watch_with_commentary([], [rev], _snap(lp), compact=False)
    eg = rev.get("entry_grade")
    assert eg is not None
    assert eg["letter"] == "D" and eg["score"] == 30.0
    assert eg["entry_date"] == "2026-08-05"
    assert eg["token"] == "🎓 entry D (30, RSI 60 off-band) · Aug 5"


# ── (b) missing record → n/a in full block ONLY ──────────────────────────


def test_missing_record_renders_na_in_full_block_only(tmp_path):
    """A contract with no ledger record shows `🎓 entry n/a` in the full
    block (explicit absence — rule #19), but one-liners stay clean of
    n/a clutter."""
    lp = _write_ledger(tmp_path, [_ledger_record(und="MSFT", strike=500.0,
                                                 exp="2026-12-18")])
    full = "\n".join(render_watch_with_commentary(
        [], [_hold_review()], _snap(lp), compact=False))
    assert "🎓 entry n/a" in full
    compact = "\n".join(render_watch_with_commentary(
        [], [_hold_review()], _snap(lp), compact=True))
    assert "🎓" not in compact


# ── (c) hedge legs → the ledger's 'n/a — hedge' convention ───────────────


def test_hedge_long_leg_renders_na_hedge(tmp_path):
    """A LONG put (collar floor / hedge) is graded 'n/a — hedge/long leg,
    different objective' by the ledger; the Watch full block respects
    that convention verbatim rather than showing a fabricated grade."""
    rec = _ledger_record(letter="n/a", score=None, drivers=[],
                         side="long_put",
                         message="n/a — hedge/long leg, different objective")
    lp = _write_ledger(tmp_path, [rec])
    rev = _hold_review()
    rev["qty"] = 1  # long leg
    full = "\n".join(render_watch_with_commentary(
        [], [rev], _snap(lp), compact=False))
    assert "🎓 entry n/a — hedge/long leg" in full
    compact = "\n".join(render_watch_with_commentary(
        [], [rev], _snap(lp), compact=True))
    assert "🎓" not in compact


# ── (d) unreadable ledger → fail-open, no tokens, no crash ───────────────


def test_unreadable_ledger_fails_open(tmp_path):
    """An unreadable/corrupt ledger file must never crash the Watch
    render — the rows simply carry no 🎓 tokens."""
    lp = tmp_path / "entry_grade_ledger.json"
    lp.write_bytes(b"{corrupt json!!")
    for compact in (True, False):
        md = "\n".join(render_watch_with_commentary(
            [], [_hold_review()], _snap(lp), compact=compact))
        assert "AMZN_PUT_245_20261016" in md  # position never dropped
        assert "🎓" not in md


# ── (e) scorecard open-book summary math ─────────────────────────────────


def test_scorecard_open_book_summary_line_math():
    """The '## 🎓 Entry Scorecard' section answers George's question at a
    glance: open (and ONLY open) graded entries roll up into one line,
    e.g. `Open book: 2 C · 1 D · avg 48/100`."""
    entries = [
        _ledger_record(und="GOOG", strike=330.0, exp="2026-09-25",
                       letter="C", score=55.0, entry_date="2026-08-01"),
        _ledger_record(und="GOOG", strike=320.0, exp="2026-10-16",
                       letter="C", score=60.0, entry_date="2026-08-02"),
        _ledger_record(und="AVGO", strike=340.0, exp="2027-01-15",
                       letter="D", score=30.0, entry_date="2026-08-03"),
        _ledger_record(und="MU", strike=950.0, exp="2026-12-18",
                       letter="D", score=20.0, entry_date="2026-07-01",
                       status="closed"),
    ]
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        _CFG)
    ob = sc["open_book"]
    assert ob["n"] == 3
    assert ob["distribution"] == {"C": 2, "D": 1}
    assert ob["avg"] == round((55.0 + 60.0 + 30.0) / 3, 1)
    assert ob["line"] == "2 C · 1 D · avg 48/100"  # closed D excluded
    md = "\n".join(entry_ledger.render_scorecard_panel(sc))
    assert ("**Current open positions by entry grade:** "
            "Open book: 2 C · 1 D · avg 48/100 (n=3)") in md


def test_scorecard_open_book_absent_when_nothing_open():
    entries = [_ledger_record(status="closed")]
    sc = entry_ledger.compute_scorecard({"entries": entries}, "2026-08-05",
                                        _CFG)
    assert sc["open_book"] is None
    md = "\n".join(entry_ledger.render_scorecard_panel(sc))
    assert "Current open positions by entry grade" not in md


# ── (f) disabled → byte-identical legacy ─────────────────────────────────


def test_disabled_flag_is_byte_identical_legacy(tmp_path):
    """entry_scorecard.enabled off (or absent) → no tokens, output
    byte-identical to the pre-feature render even when a matching ledger
    record exists on disk."""
    lp = _write_ledger(tmp_path, [_ledger_record()])
    legacy_snap = {"technicals": {"AMZN": {"rsi_14": 63.0}}, "quotes": {}}
    for compact in (True, False):
        off = render_watch_with_commentary(
            [], [_hold_review()],
            _snap(lp, config={"entry_scorecard": {"enabled": False}}),
            compact=compact)
        legacy = render_watch_with_commentary(
            [], [_hold_review()], dict(legacy_snap), compact=compact)
        assert off == legacy
        assert "🎓" not in "\n".join(off)


def test_disabled_never_attaches_entry_grade(tmp_path):
    lp = _write_ledger(tmp_path, [_ledger_record()])
    rev = _hold_review()
    render_watch_with_commentary(
        [], [rev], _snap(lp, config={"entry_scorecard": {"enabled": False}}))
    assert "entry_grade" not in rev


# ── (g) digest — pure subset keeps tokens, budget holds ──────────────────


def test_digest_token_lines_count_and_budget():
    """The digest is a pure subset: a Watch one-liner carrying the 🎓
    token still counts as an option row in the Watch pointer, the kept
    `**🎓` scorecard summary line rides through verbatim, and the digest
    stays within its 250-line budget."""
    from render.digest import build_digest
    watch_line = ("📌 **AMZN_PUT_245_20261016** PUT $245 Fri Oct 16 '26, "
                  "71d left  · RSI 63 · P&L +$30 (+6% captured) → **HOLD**"
                  " — Hold for more decay · 🎓 entry D (30) · Aug 5")
    score_line = "**🎓 Entry quality: last 10 avg D (42) ↘ · last entry AMZN $245P — D (RSI 60 off-band)**"
    full_md = "\n".join([
        "# Daily Briefing — 2026-08-13", "",
        "## 📊 Health", "", "- NLV $1,000,000", "",
        "## 🎓 Entry Scorecard", "", score_line, "",
        "- **Current open positions by entry grade:** Open book: "
        "2 C · 1 D · avg 48/100 (n=3) — per-position 🎓 tokens on the "
        "Watch rows", "",
        "## Today's Action List", "", "1. **CLOSE** AMZN_PUT_245_20261016 "
        "— take profit", "",
        "## Watch / Portfolio Review", "", "### Options", "",
        watch_line, "",
    ]) + "\n"
    digest = build_digest(full_md, config={"render": {"digest": True}},
                          extras={"date": "2026-08-13"})
    dl = digest.splitlines()
    assert len(dl) <= 250
    # The kept scorecard summary line rides through verbatim (pure subset).
    assert score_line in dl
    # The token-carrying one-liner still counts in the Watch pointer.
    pointer = [ln for ln in dl if "Watch / Portfolio Review" in ln
               and "1 options" in ln]
    assert pointer, f"watch pointer with option count missing:\n{digest}"
