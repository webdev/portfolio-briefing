"""Watch ROLL ANALYSIS menu trigger gate — regression tests.

George (2026-08-21): "Sometimes I see recommendations way early when I'm
out of the money."

The 2026-08-21 audit found the numbered action list fully conformant (only
tested-strike SOXX δ0.48 rolls actionable; all OTM underwater puts HOLD) —
the "way early" illusion came from the Watch panel rendering a full priced
ROLL ANALYSIS candidate table under EVERY option position, including
deep-OTM HOLDs (AMAT -7%, "strike is not genuinely tested (|δ| < 0.40) →
HOLD" — and a roll menu right beneath it).

Config: briefing.yaml → roll.watch_menu_on_trigger_only.enabled; code
default OFF = legacy byte-identical. Source: analysis/watch_menu_gate.py,
wired in steps/per_option_commentary.py.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import watch_menu_gate as wmg  # noqa: E402
from analysis.exit_cost import (  # noqa: E402
    analyze_exit_cost,
    format_anatomy_lines,
)
from steps.per_option_commentary import (  # noqa: E402
    render_watch_with_commentary,
)

_FLAG_ON = {"roll": {"watch_menu_on_trigger_only": {"enabled": True}}}
_FLAG_OFF = {"roll": {"watch_menu_on_trigger_only": {"enabled": False}}}


def _candidates():
    return [
        {"id": "A", "description": "HOLD (don't roll)", "netDollars": 0,
         "notes": "Wait for theta/IV mean-reversion to work"},
        {"id": "B", "description": "1× $340P Jan 15 '27 @ $28.05 +126d",
         "netDollars": 2000, "notes": "Same strike, extend +126d (~4mo)"},
    ]


def _untested_put_review():
    """The AMAT shape from the 2026-08-21 briefing: '📌 AMAT_PUT_470_20260925
    … P&L -$152 (-7% captured) → **HOLD** — Inside the NEAR_ATM band but the
    strike is not genuinely tested (|δ| < 0.40 …' — and a full ROLL ANALYSIS
    table right beneath it."""
    return {
        "contract": "AMAT_PUT_470_20260925", "recommendation": "HOLD",
        "type": "PUT", "strike": 470.0, "expiration": "2026-09-25",
        "days_to_expiry": 35, "entry_price": 21.55, "current_mid": 23.07,
        "qty": -1, "underlying": "AMAT", "delta": -0.29,
        "recommended_candidate_id": "A",
        "roll_candidates": _candidates(),
        "rationale": ("Inside the NEAR_ATM band but the strike is not "
                      "genuinely tested (|δ| < 0.40) → HOLD"),
    }


def _tested_put_review():
    """The SOXX shape: '🎯 Strike tested (δ 0.48 ≥ 0.45) … credit-roll
    window open' — the roll machinery is genuinely engaged."""
    return {
        "contract": "SOXX_PUT_520_20260911",
        "recommendation": "ROLL_OUT_AND_DOWN",
        "type": "PUT", "strike": 520.0, "expiration": "2026-09-11",
        "days_to_expiry": 21, "entry_price": 15.12, "current_mid": 20.30,
        "qty": -1, "underlying": "SOXX", "delta": -0.48,
        "recommended_candidate_id": "C",
        "roll_candidates": _candidates(),
    }


def _snap(config=None, quotes=None, extra=None):
    snap = {"technicals": {}, "quotes": quotes or {}}
    if config is not None:
        snap["_config"] = config
    if extra:
        snap.update(extra)
    return snap


def _render(reviews, snap, compact=False):
    return "\n".join(render_watch_with_commentary([], reviews, snap,
                                                  compact=compact))


# ── (a) untested OTM put → no table, the measured one-liner ─────────────────

def test_untested_otm_put_replaces_table_with_measured_one_liner():
    """George: 'Sometimes I see recommendations way early when I'm out of
    the money.' — the AMAT-shape deep-OTM HOLD (-7%, δ 0.29 < 0.40, spot
    6.2% above strike) must NOT render the priced ROLL ANALYSIS table; it
    renders ONE measured italic line saying when the menu returns."""
    snap = _snap(config=_FLAG_ON, quotes={"AMAT": {"last": 499.2}})
    md = _render([_untested_put_review()], snap)
    assert "ROLL ANALYSIS" not in md
    assert "| A" not in md  # no candidate rows either
    assert ("_roll menu: not triggered — spot 6.2% above strike, δ 0.29; "
            "the priced menu appears when the strike is tested "
            "(within 3% / δ ≥ 0.4)_") in md
    # Rule #24 — the position itself is still fully present.
    assert "AMAT_PUT_470_20260925" in md


def test_untested_one_liner_says_delta_na_when_unmeasured():
    """Rule #19: if δ is unmeasured, say δ n/a and gate on moneyness only —
    never a fabricated delta."""
    rev = _untested_put_review()
    rev["delta"] = None
    snap = _snap(config=_FLAG_ON, quotes={"AMAT": {"last": 499.2}})
    md = _render([rev], snap)
    assert "ROLL ANALYSIS" not in md
    assert "δ n/a" in md
    assert "spot 6.2% above strike" in md


def test_unmeasurable_strike_test_fails_open_and_keeps_menu():
    """No spot quote AND no measured delta → the strike test is
    unverifiable; the menu is KEPT (never hide information on missing
    data — fail-open direction, rule #19)."""
    rev = _untested_put_review()
    rev["delta"] = None
    snap = _snap(config=_FLAG_ON, quotes={})  # AMAT unquoted this cycle
    md = _render([rev], snap)
    assert "ROLL ANALYSIS" in md
    assert "roll menu: not triggered" not in md


def test_untested_short_call_is_side_aware():
    """A short CALL 8% BELOW strike with δ 0.20 is equally untriggered —
    the one-liner reads 'below strike' (side-aware moneyness)."""
    rev = {
        "contract": "SMH_CALL_587_20260911", "recommendation": "HOLD",
        "type": "CALL", "strike": 587.5, "expiration": "2026-09-11",
        "days_to_expiry": 21, "entry_price": 12.0, "current_mid": 12.0,
        "qty": -1, "underlying": "SMH", "delta": 0.20,
        "recommended_candidate_id": "A",
        "roll_candidates": _candidates(),
    }
    snap = _snap(config=_FLAG_ON, quotes={"SMH": {"last": 540.5}})
    md = _render([rev], snap)
    assert "ROLL ANALYSIS" not in md
    assert "spot 8.0% below strike" in md and "δ 0.20" in md


# ── (b) tested position → full table unchanged ─────────────────────────────

def test_tested_strike_keeps_full_table():
    """The SOXX δ0.48 shape (today's only genuinely-engaged rolls) keeps the
    full priced ROLL ANALYSIS table with the flag ON — trigger-gating must
    never touch a tested strike."""
    snap = _snap(config=_FLAG_ON, quotes={"SOXX": {"last": 519.56}})
    md = _render([_tested_put_review()], snap)
    assert "**ROLL ANALYSIS:**" in md
    assert "| B | 1× $340P Jan 15 '27 @ $28.05 +126d | +$2,000 credit" in md
    assert "roll menu: not triggered" not in md


def test_delta_alone_tests_the_strike_even_when_spot_missing():
    """Measured |δ| ≥ 0.40 is a strike test on its own (rule-#3 delta-first
    reading) — no spot quote needed to keep the menu."""
    rev = _tested_put_review()
    snap = _snap(config=_FLAG_ON, quotes={})  # no SOXX quote
    md = _render([rev], snap)
    assert "**ROLL ANALYSIS:**" in md


# ── (c) OPTIONAL willing-owner + closing-credit-window keep tables ─────────

def test_willing_owner_nok_shape_keeps_table():
    """The NOK shape (🔧 OPTIONAL — credit extension; strike tested δ 0.50,
    spot below strike) keeps its table — an OPTIONAL credit-extension
    decision is live, and the tested strike triggers the menu."""
    rev = {
        "contract": "NOK_PUT_11_20261218", "recommendation": "HOLD",
        "type": "PUT", "strike": 11.0, "expiration": "2026-12-18",
        "days_to_expiry": 119, "entry_price": 2.56, "current_mid": 1.90,
        "qty": -10, "underlying": "NOK", "delta": -0.50,
        "recommended_candidate_id": "B",
        "roll_candidates": _candidates(),
    }
    snap = _snap(config=_FLAG_ON, quotes={"NOK": {"last": 10.19}})
    md = _render([rev], snap)
    assert "**ROLL ANALYSIS:**" in md
    assert "roll menu: not triggered" not in md


def test_closing_credit_window_keeps_table_even_when_untested():
    """A 🟡 CLOSING credit window is a live 'act while a credit remains'
    decision — the menu stays even when the strike is not tested."""
    rev = _untested_put_review()
    snap = _snap(
        config=_FLAG_ON, quotes={"AMAT": {"last": 499.2}},
        extra={"_credit_windows": {
            "AMAT_PUT_470_20260925": {"state": "closing"}}})
    md = _render([rev], snap)
    assert "**ROLL ANALYSIS:**" in md
    assert "roll menu: not triggered" not in md


def test_urgent_commentary_keeps_table_even_when_untested():
    """The AVGO Jan '27 shape (🚨 URGENT: earnings in 12d with -56% capture)
    keeps its table even with spot 7.7% above the strike — an urgent item
    exists for it this cycle."""
    today = datetime.now().date()
    rev = _untested_put_review()
    rev["contract"] = "AVGO_PUT_340_20270115"
    rev["underlying"] = "AVGO"
    rev["strike"] = 340.0
    rev["entry_price"] = 18.0
    rev["current_mid"] = 28.0          # -56% capture → 🚨 URGENT pattern
    rev["delta"] = None
    snap = _snap(
        config=_FLAG_ON, quotes={"AVGO": {"last": 366.0}},
        extra={"earnings_calendar": {
            "AVGO": (today + timedelta(days=12)).isoformat()}})
    md = _render([rev], snap)
    assert "🚨 URGENT" in md
    assert "**ROLL ANALYSIS:**" in md


def test_advisor_recommended_roll_keeps_table_even_when_untested():
    """When the advisor's own table recommends a real roll (candidate ≠ A),
    the menu stays — the advisor engaged the machinery."""
    rev = _untested_put_review()
    rev["recommended_candidate_id"] = "B"
    snap = _snap(config=_FLAG_ON, quotes={"AMAT": {"last": 499.2}})
    md = _render([rev], snap)
    assert "**ROLL ANALYSIS:**" in md


def test_gate_demoted_roll_is_not_a_live_action():
    """The AVGO Sep '11 shape: a ROLL_* recommendation demoted by the rule-#3
    gate ('spot is 7.7% above the strike with no genuine strike test') is
    explicitly NOT engaged — the demotion note stays, the table goes."""
    rev = _untested_put_review()
    rev["recommendation"] = "ROLL_OUT"
    rev["_roll_gate_demotion"] = (
        "Roll demoted from the action list (rule #3 gate): spot is 6.2% "
        "above the strike with no genuine strike test (|δ| < 0.40) — theta "
        "is working; re-evaluate on a genuine test.")
    snap = _snap(config=_FLAG_ON, quotes={"AMAT": {"last": 499.2}})
    md = _render([rev], snap)
    assert "Roll demoted from the action list" in md      # note kept
    assert "ROLL ANALYSIS" not in md                       # table gone
    assert "roll menu: not triggered" in md


# ── (d) verdict demotion on untriggered HOLDs; live decisions unchanged ────

def _avgo_untriggered_roll_verdict_fixture():
    """Untriggered HOLD whose exit-cost verdict engine reads ROLL_DONT_CLOSE
    (OTM + underwater + earnings inside the contract + 100% extrinsic)."""
    today = datetime.now().date()
    exp = (today + timedelta(days=21)).isoformat()
    rev = {
        "contract": "AVGO_PUT_340_20260911", "recommendation": "HOLD",
        "type": "PUT", "strike": 340.0, "expiration": exp,
        "days_to_expiry": 21, "entry_price": 18.0, "current_mid": 20.0,
        "qty": -1, "underlying": "AVGO", "delta": -0.29,
        "recommended_candidate_id": "A",
        "roll_candidates": _candidates(),
    }
    snap = _snap(
        config=_FLAG_ON, quotes={"AVGO": {"last": 366.0}},
        extra={
            "chains": {f"AVGO_{exp}": {
                "puts": [{"strike": 340.0, "bid": 19.5, "ask": 20.5}]}},
            "earnings_calendar": {
                "AVGO": (today + timedelta(days=12)).isoformat()},
        })
    return rev, snap


def test_roll_verdict_demotes_to_exit_cost_note_on_untriggered_hold():
    """One voice (George 2026-08-21): a HOLD card with an untested strike
    must read HOLD everywhere — the ROLL_DONT_CLOSE verdict renders as
    '⚖️ exit-cost note: closing today would pay $X of extrinsic (Y%); no
    action triggered', never a sentence leading with ROLL."""
    rev, snap = _avgo_untriggered_roll_verdict_fixture()
    md = _render([rev], snap)
    assert ("⚖️ exit-cost note: closing today would pay $2,000 of "
            "extrinsic (100%); no action triggered") in md
    assert "Verdict: ROLL, don't close" not in md
    assert "ROLL ANALYSIS" not in md


def test_live_decision_verdict_sentence_unchanged():
    """The ROLL-vs-close phrasing still appears at full strength when a
    close/roll decision is actually live — the action-list renderer
    (format_anatomy_lines) is untouched by the Watch gate."""
    today = datetime.now().date()
    anatomy = analyze_exit_cost(
        {"contract": "SOXX_PUT_520_20260911", "type": "PUT", "qty": -1,
         "strike": 520.0, "entry_price": 15.12, "days_to_expiry": 30},
        {"bid": 19.5, "ask": 21.1, "mid": 20.30},
        spot=519.56,
        iv_rank=81.0,
        earnings_date=None,
        today=today.isoformat(),
    )
    assert anatomy is not None and anatomy.verdict == "ROLL_DONT_CLOSE"
    lines = "\n".join(format_anatomy_lines(anatomy))
    assert "**⚖️ Verdict: ROLL, don't close**" in lines


def test_hold_for_decay_verdict_line_untouched():
    """The existing HOLD_FOR_DECAY Watch verdict (already HOLD-consistent)
    keeps rendering with the flag ON."""
    today = datetime.now().date()
    exp = (today + timedelta(days=35)).isoformat()
    rev = _untested_put_review()
    rev["expiration"] = exp
    rev["current_mid"] = 23.07
    snap = _snap(
        config=_FLAG_ON, quotes={"AMAT": {"last": 499.2}},
        extra={"chains": {f"AMAT_{exp}": {
            "puts": [{"strike": 470.0, "bid": 22.5, "ask": 23.6}]}}})
    md = _render([rev], snap)
    assert "**⚖️ Verdict: HOLD_FOR_DECAY**" in md
    assert "ROLL ANALYSIS" not in md


# ── (e) config off = legacy byte-identical ─────────────────────────────────

def test_flag_off_is_byte_identical_legacy():
    """roll.watch_menu_on_trigger_only absent OR enabled:false → the exact
    legacy render (full table on every position with candidates)."""
    quotes = {"AMAT": {"last": 499.2}}
    legacy = render_watch_with_commentary(
        [], [_untested_put_review()], _snap(quotes=quotes))
    flag_off = render_watch_with_commentary(
        [], [_untested_put_review()], _snap(config=_FLAG_OFF, quotes=quotes))
    assert legacy == flag_off
    md = "\n".join(legacy)
    assert "**ROLL ANALYSIS:**" in md
    assert "roll menu: not triggered" not in md


def test_flag_default_is_off_in_code():
    """Code default OFF (the briefing.yaml opts in) — empty config never
    gates."""
    assert wmg.enabled({}) is False
    assert wmg.enabled(None) is False
    assert wmg.enabled({"roll": {}}) is False
    assert wmg.enabled(
        {"roll": {"watch_menu_on_trigger_only": {"enabled": True}}}) is True


# ── (f) compact mode + digest interactions ─────────────────────────────────

def test_compact_mode_triggered_position_keeps_table_with_flag_on():
    """Compact mode: a triggered (tested-strike) position's full block keeps
    the ROLL ANALYSIS table exactly as before, flag ON."""
    snap = _snap(config=_FLAG_ON, quotes={"SOXX": {"last": 519.56}})
    md = _render([_tested_put_review()], snap, compact=True)
    assert "**ROLL ANALYSIS:**" in md


def test_compact_mode_demoted_full_block_gets_one_liner_not_table():
    """Compact mode: a demotion-note position keeps its FULL block (the
    Watch panel is the note's only surface) but the untriggered menu is the
    one-liner, not the priced table."""
    rev = _untested_put_review()
    rev["_debit_cap_demotion"] = "Roll demoted (debit cap): example"
    snap = _snap(config=_FLAG_ON, quotes={"AMAT": {"last": 499.2}})
    md = _render([rev], snap, compact=True)
    assert "Roll demoted (debit cap)" in md   # full block, note visible
    assert "ROLL ANALYSIS" not in md
    assert "roll menu: not triggered" in md


def test_digest_never_carried_watch_tables_and_still_does_not():
    """The digest (briefing_<date>.md) never carried the Watch tables —
    the Watch section collapses to a pointer/count line. Verified against
    the real 2026-08-06 full-briefing fixture; the gate changes nothing
    here."""
    from render.digest import build_digest
    fixture = (Path(__file__).parent / "fixtures"
               / "briefing_full_2026-08-06.md").read_text()
    assert "**ROLL ANALYSIS:**" in fixture
    digest = build_digest(
        fixture, config={"render": {"digest": True}},
        extras={"date": "2026-08-06"})
    assert digest != fixture               # split actually happened
    assert "**ROLL ANALYSIS:**" not in digest
