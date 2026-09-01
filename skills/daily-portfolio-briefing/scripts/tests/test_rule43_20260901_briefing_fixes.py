"""Rule #43 regression tests — three defects observed on the REAL 2026-09-01
briefing (briefing_2026-09-01.md). Each test docstring quotes the observed
output verbatim.

BUG A — HOLD FOR BASIS items carried aging tags:
    "5. **HOLD FOR BASIS** AMAT_PUT_470_20260925 — assignment acceptable
     (exit-cost verdict); no roll ticket · RSI 35 🟢 pullback
     ⏳ IGNORED 3 DAYS"
(also GOOG_PUT_345_20270115 at #6 and IREN_PUT_47_20261218 at #9). A HOLD
verdict has nothing to execute — tagging it IGNORED nags the operator to
act on a card whose whole point is "do nothing". The 2026-08-31 fix
(is_hold_class_directive) exempted 🏇 RIDING / 🔔 reminder items; the
exemption now covers ALL passive-verdict headline kinds (HOLD FOR BASIS,
HOLD THROUGH EARNINGS, HOLD — GTC AT 50%, HOLD_FOR_DECAY variants), and
stale aging state for hold-class idents is purged.

BUG B — negative coverage-after rendered bare: the Money Plan showed
    "**Net option cash today (mid-fills):** −$3,340 buybacks +$780 roll
     credit = −$2,560 · **Coverage after:** 0.00× → ~-0.02×"
The honest formula ((cash − buybacks)/(obligation − freed)) correctly goes
negative when buybacks exceed available cash — a temporary margin draw —
but a bare "-0.02×" reads broken. The DISPLAYED post ratio floors at 0.00×
with a measured margin-draw note; the raw value stays in the JSON.

BUG C — prime-conjunction earnings sign bug: the Best Setups closest-miss
line read
    "- _none today — closest miss: RBRK (… earnings -5d away
     (< 14d clear))_"
A NEGATIVE days-away means the print already happened (5 days AGO) — that
SATISFIES the earnings-clear check (RBRK's next print shows 243d in the
chip), it doesn't fail it. Past prints are clear; a genuine near-future
miss renders with honest wording ("next earnings 9d away (< 14d clear)");
an unknown next date stays non-prime.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import setup_grade as sg  # noqa: E402
from analysis.rec_aging import (  # noqa: E402
    apply_aging_to_action_items,
    is_hold_class_directive,
    render_stalled_panel,
)
from render.money_plan import build_money_plan  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────
# BUG A — HOLD-family passive verdicts never age
# ─────────────────────────────────────────────────────────────────────────

_HOLD_BASIS_LINE = (
    "5. **HOLD FOR BASIS** AMAT_PUT_470_20260925 — assignment acceptable "
    "(exit-cost verdict); no roll ticket  · RSI 35 🟢 pullback")


def test_bug_a_every_hold_class_kind_is_exempt():
    """Observed: "5. **HOLD FOR BASIS** AMAT_PUT_470_20260925 — …
    ⏳ IGNORED 3 DAYS" (and GOOG_PUT_345 #6, IREN #9). Every HOLD-family
    kind is hold-class — no aging clock."""
    for kind in ("HOLD FOR BASIS", "HOLD_FOR_BASIS",
                 "HOLD THROUGH EARNINGS — willing owner",
                 "HOLD — GTC AT 50%", "HOLD_FOR_DECAY", "HOLD"):
        assert is_hold_class_directive(kind=kind), kind
    for line in (
        _HOLD_BASIS_LINE,
        "3. **HOLD THROUGH EARNINGS — willing owner** IREN_PUT_47_20261218 "
        "— basis $28.36 is 39% below spot",
        "4. **HOLD — GTC AT 50%** NOK_PUT_11_20261218 — +30% captured",
    ):
        assert is_hold_class_directive(line=line), line


def test_bug_a_actionable_kinds_still_age():
    """Actionable items (CLOSE/ROLL/URGENT/EXIT/STALL-FIRED) are NOT
    hold-class — their aging clocks keep ticking."""
    for kind in ("CLOSE", "EXECUTE ROLL", "DEFENSIVE ROLL (core override)",
                 "URGENT — EXECUTE ROLL", "EXIT", "TRIM",
                 "EXIT — STALL FIRED (directive)", "CLOSE_BEFORE_EARNINGS"):
        assert not is_hold_class_directive(kind=kind), kind
    # A mid-line "**HOLD**" mention on an actionable card must not exempt.
    assert not is_hold_class_directive(
        line="1. **CLOSE** MU_PUT_700_20261218 — advisor said **HOLD** "
             "yesterday; close today")


def test_bug_a_hold_for_basis_never_gets_ignored_tag():
    """The observed AMAT card had prior state (days_flagged 2) and an
    IGNORED reconciliation — exactly the setup that rendered "⏳ IGNORED 3
    DAYS". A HOLD FOR BASIS block must render with NO ⏳ tag."""
    key = "HOLD_FOR_BASIS:AMAT_PUT_470_20260925"
    aging_info = {
        "today": "2026-09-01",
        "state": {key: {"first_flagged": "2026-08-30", "days_flagged": 2,
                        "last_aged": "2026-08-31"}},
        "reconciliation": {key: "IGNORED"},
    }
    out = apply_aging_to_action_items([_HOLD_BASIS_LINE], aging_info)
    text = "\n".join(out)
    assert "HOLD FOR BASIS" in text
    assert "⏳ IGNORED" not in text
    assert key not in (aging_info.get("aged") or {})


def test_bug_a_actionable_close_still_gets_ignored_tag():
    """Control: the same 2-days-prior + IGNORED setup on a CLOSE block
    still renders "⏳ IGNORED 3 DAYS" — the exemption is hold-class only."""
    key = "CLOSE:MU_PUT_700_20261218"
    aging_info = {
        "today": "2026-09-01",
        "state": {key: {"first_flagged": "2026-08-30", "days_flagged": 2,
                        "last_aged": "2026-08-31"}},
        "reconciliation": {key: "IGNORED"},
    }
    out = apply_aging_to_action_items(
        ["1. **CLOSE** MU_PUT_700_20261218 — +10% captured; "
         "buy-to-close limit $5.00"], aging_info)
    text = "\n".join(out)
    assert "⏳ IGNORED 3 DAYS" in text


def test_bug_a_stale_hold_class_state_is_purged():
    """Yesterday's state file carries "HOLD_FOR_BASIS:AMAT_PUT_470_20260925"
    with a ticking clock (written before the exemption). After today's run
    the persisted state must contain NO hold-class keys — tomorrow's render
    starts clean."""
    stale = {
        "HOLD_FOR_BASIS:AMAT_PUT_470_20260925": {
            "first_flagged": "2026-08-30", "days_flagged": 3,
            "last_aged": "2026-08-31"},
        "HOLD_FOR_BASIS:GOOG_PUT_345_20270115": {
            "first_flagged": "2026-08-30", "days_flagged": 3,
            "last_aged": "2026-08-31"},
        "CLOSE:MU_PUT_700_20261218": {
            "first_flagged": "2026-08-30", "days_flagged": 2,
            "last_aged": "2026-08-31"},
    }
    aging_info = {
        "today": "2026-09-01", "state": stale,
        "reconciliation": {"CLOSE:MU_PUT_700_20261218": "IGNORED"},
    }
    out = apply_aging_to_action_items(
        [_HOLD_BASIS_LINE,
         "6. **HOLD FOR BASIS** GOOG_PUT_345_20270115 — assignment "
         "acceptable (exit-cost verdict); no roll ticket  · RSI 41 🟢 "
         "pullback",
         "1. **CLOSE** MU_PUT_700_20261218 — +10% captured; "
         "buy-to-close limit $5.00"], aging_info)
    updated = aging_info["updated_state"]
    assert not any(k.startswith("HOLD") for k in updated), updated
    assert "CLOSE:MU_PUT_700_20261218" in updated  # actionable clock kept
    assert "⏳ IGNORED" not in "\n".join(
        ln for ln in out if "HOLD FOR BASIS" in ln)


def test_bug_a_stalled_panel_never_promotes_hold_class():
    """A stale aged entry for a HOLD-family kind (6+ days on the old clock)
    must not surface in ⛔ Stalled Items — tracked, not ignored."""
    aged = {
        "HOLD_FOR_BASIS:IREN_PUT_47_20261218": {
            "kind": "HOLD_FOR_BASIS", "ident": "IREN_PUT_47_20261218",
            "days_flagged": 7, "first_flagged": "2026-08-25"},
        "TRIM:NVDA": {"kind": "TRIM", "ident": "NVDA",
                      "days_flagged": 7, "first_flagged": "2026-08-25"},
    }
    text = "\n".join(render_stalled_panel(aged))
    assert "IREN_PUT_47_20261218" not in text
    assert "TRIM NVDA" in text


# ─────────────────────────────────────────────────────────────────────────
# BUG B — negative coverage-after floors at 0.00× with the measured note
# ─────────────────────────────────────────────────────────────────────────

def _money_plan(coverage_after, coverage_now=0.0, total_freed=137_000.0):
    """One banked CLOSE (buyback $3,340 at mid) + a playbook projection."""
    return build_money_plan(
        date_str="2026-09-01",
        action_list_lines=[
            "1. **CLOSE** GOOG_PUT_345_20270115 — +17% ($+660); "
            "buy-to-close limit $33.40",
        ],
        options_reviews=[
            {"contract": "GOOG_PUT_345_20270115", "underlying": "GOOG",
             "type": "PUT", "strike": 345.0, "qty": -1,
             "entry_price": 40.0, "current_mid": 33.40},
        ],
        new_ideas=[],
        playbook={"closes": [], "opens": [],
                  "coverage_before": coverage_now,
                  "coverage_after": coverage_after,
                  "total_freed": total_freed},
        analytics={"stress_coverage": {"coverage_ratio": coverage_now}},
        snapshot_data={"positions": []},
        config={},
    )


def test_bug_b_negative_raw_coverage_floors_display_with_margin_note():
    """Observed: "**Coverage after:** 0.00× → ~-0.02×" — bare negative.
    The displayed post ratio floors at 0.00× and the line explains the
    negative with measured numbers (buybacks draw on margin; obligation
    drop)."""
    lines, plan = _money_plan(coverage_after=-0.02)
    text = "\n".join(lines)
    assert "**Coverage after:** 0.00× → ~0.00×" in text
    assert "-0.02×" not in text
    assert ("buybacks −$3,340 draw on margin until freed collateral "
            "settles") in text
    assert "obligation drops $137,000" in text
    # Raw value preserved in the JSON — the display floors, the data does not.
    assert plan["coverage_after"] == -0.02


def test_bug_b_positive_raw_coverage_unchanged():
    """A positive projection renders exactly as before — no floor, no
    margin note; the small-gain physics note still fires."""
    lines, plan = _money_plan(coverage_after=0.03)
    text = "\n".join(lines)
    assert "**Coverage after:** 0.00× → ~0.03×" in text
    assert "draw on margin" not in text
    assert "obligation shrinks; cash does not" in text  # small-gain note
    assert plan["coverage_after"] == 0.03


def test_bug_b_large_positive_gain_no_notes():
    """A material gain (≥ 0.10×) renders with neither note — regression
    guard on the restructured branch."""
    lines, _ = _money_plan(coverage_after=0.45)
    text = "\n".join(lines)
    assert "**Coverage after:** 0.00× → ~0.45×" in text
    assert "draw on margin" not in text
    assert "obligation shrinks; cash does not" not in text


# ─────────────────────────────────────────────────────────────────────────
# BUG C — earnings sign in the prime conjunction
# ─────────────────────────────────────────────────────────────────────────

CFG_ON = {"setup_grade": {"enabled": True}}
SR_2TOUCH_SUPPORT = {"supports": [
    {"price": 145.0, "touches": 3, "strength": 5.0}], "resistances": []}


def _csp_components(**kw):
    """Every component in its prime band (mirrors test_prime_conjunction):
    RSI 40, TRUE chain IVr 75, 3-touch support under the $150 strike,
    uptrend above the 200-SMA, red day, earnings clear."""
    base = {
        "rsi": 40.0, "iv_rank": 75.0, "iv_rank_source": "chain",
        "support_resistance": SR_2TOUCH_SUPPORT, "strike": 150.0,
        "spot": 160.0, "sma_200": 150.0, "lt_verdict": "uptrend",
        "day_change_pct": -0.01, "days_to_earnings": 30,
    }
    base.update(kw)
    return base


def test_bug_c_past_earnings_minus_5d_satisfies_prime_clear():
    """Observed: "closest miss: RBRK (… earnings -5d away (< 14d clear))".
    -5d means the print happened 5 days AGO — that satisfies the
    earnings-clear check (RBRK's next print is 243d out), it doesn't fail
    it."""
    prime, missing = sg.prime_conjunction(
        _csp_components(days_to_earnings=-5), "csp", CFG_ON)
    assert prime is True
    assert missing == []
    assert not any("earnings" in m for m in missing)


def test_bug_c_genuine_near_future_fails_with_honest_wording():
    """A KNOWN next print inside the window still fails — with wording
    that names the NEXT print ("next earnings 9d away (< 14d clear)"),
    never a bare negative."""
    prime, missing = sg.prime_conjunction(
        _csp_components(days_to_earnings=9), "csp", CFG_ON)
    assert prime is False
    assert missing == ["next earnings 9d away (< 14d clear)"]


def test_bug_c_unknown_earnings_date_stays_non_prime():
    """An unknown next date remains non-prime (a conjunction can't be
    verified on missing data — rule #19), unchanged by the sign fix."""
    prime, missing = sg.prime_conjunction(
        _csp_components(days_to_earnings=None), "csp", CFG_ON)
    assert prime is False
    assert missing == [
        "earnings date unknown (prime needs ≥ 14d clear)"]


def test_bug_c_context_component_scores_past_print_as_clear():
    """The shared context component fed the same negative value: at
    d = -5 it scored the earnings sub 0.0 ("earnings -5d ✗"). A passed
    print is clear — full sub-score, honest note."""
    cfg = sg.load_setup_grade_config(None)
    score, notes = sg._context_component(None, -5, None, "csp", cfg)
    assert score == 1.0
    assert notes == ["earnings passed 5d ago ✓"]
    # Forward dates keep the legacy bands.
    score, notes = sg._context_component(None, 9, None, "csp", cfg)
    assert score == 0.5
    assert notes == ["earnings 9d ✗"]
