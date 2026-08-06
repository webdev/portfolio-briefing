"""Task #43 batch 2 (2026-07-31) — four defects from the 10:31 AM briefing.

Each test's docstring quotes the observed output (house TDD rule #33 — the
test must fail against the broken code and pass against the fix).

Defect 1: Fable advisor memory rot — "Roll-avoidance pattern — thirty-third
week. You have never executed a roll." in the SAME review that says "NVDA
roll detected", after a week with 7 detected executed rolls; counters
incremented per-session while labeled weeks.
Defect 2: NOK CLOSE swallowed — yesterday's CLOSE_CLEAN close ticket
vanished when the verdict slipped to NEUTRAL and the debit cap demoted the
ranked roll, leaving NOTHING actionable on a δ0.72 ITM put.
Defect 4: Risk Alerts truncated mid-word ("… credit-roll windo") from a
fixed-width [:80] slice.

(Defect 3 — Live-Data Verification flagging HOLD FOR BASIS — is tested in
skills/briefing-data-verifier/scripts/tests/test_verify.py.)
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import fable_advisor as fa  # noqa: E402
from render.panels import render_action_list, render_risk_alerts  # noqa: E402
from steps.per_option_commentary import render_watch_with_commentary  # noqa: E402

TODAY = "2026-07-31"


# ── Shared fixtures ────────────────────────────────────────────────────────

def _snapshot(quotes=None, chains=None, iv_ranks=None, earnings=None,
              config=None):
    return {
        "quotes": quotes or {},
        "chains": chains or {},
        "iv_ranks": iv_ranks or {},
        "earnings_calendar": earnings or {},
        "technicals": {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }


def _render(reviews, snap):
    return "\n".join(render_action_list([], reviews, [], snapshot_data=snap,
                                        date_str=TODAY))


def _nok_review():
    """The real NOK shape (2026-07-31 snapshot): $11P Sep 18 ×10, spot
    $9.16, position mid $2.155, measured δ -0.7195, ranked roll-down C =
    $2,065 debit on $6,000 new collateral (34%)."""
    return {
        "contract": "NOK_PUT_11_20260918",
        "underlying": "NOK", "type": "PUT", "qty": -10,
        "strike": 11.0, "expiration": "2026-09-18",
        "entry_price": 1.4499, "current_mid": 2.155, "days_to_expiry": 49,
        "delta": -0.7195,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT",
        "recommended_candidate_id": "C",
        "rationale": "ITM — roll down-and-out to cut assignment risk.",
        "roll_candidates": [
            {"id": "A", "description": "HOLD (don't roll)", "instruction": None,
             "netDollars": 0, "dteExtension": 0,
             "notes": "Wait for theta/IV mean-reversion to work"},
            {"id": "C", "description": "10× $6P Oct 16 '26 @ $0.10 +28d",
             "instruction": {"sell_strike": 6.0,
                             "sell_expiration": "2026-10-16",
                             "sell_mid": 0.10, "sell_bid": 0.08,
                             "sell_ask": 0.12},
             "netDollars": -2065.0, "dteExtension": 28,
             "notes": "Lower strike (−$5), extend +28d (~4w)"},
        ],
    }


def _nok_snapshot(chain_bid=2.15, chain_ask=2.21, **config):
    """Real chain leg: bid 2.15 / ask 2.21 → mid $2.18; intrinsic $1.84 →
    extrinsic 15.6% of the buyback (a hair OVER the 15% clean bar →
    verdict NEUTRAL, the shape that vanished)."""
    return _snapshot(
        quotes={"NOK": {"last": 9.16}},
        chains={"NOK_2026-09-18": {"puts": [
            {"strike": 11.0, "bid": chain_bid, "ask": chain_ask}]}},
        iv_ranks={"NOK": 55},
        config=config,
    )


# ── Defect 2: a demoted roll must never swallow the close ─────────────────

def test_debit_capped_itm_put_still_surfaces_close():
    """Observed (2026-07-31): NOK_PUT_11_20260918 was ABSENT from the
    action list — only a Watch note '⏸ Roll demoted (debit cap): $2,065
    debit is 34% of the $6,000 new collateral (cap 8%) — disproportionate;
    close or take assignment instead (see the exit-cost verdict).' while
    yesterday rendered 'CLOSE — exit-cost verdict: clean exit; buy-to-close
    10× limit $2.28'. The debit cap kills the ROLL candidate → control
    falls to the exit-cost verdict → the close-or-assignment choice
    composes as a CLOSE ticket. Roll demoted note stays in Watch."""
    rev = _nok_review()
    snap = _nok_snapshot()
    md = _render([rev], snap)
    assert "EXECUTE ROLL" not in md
    assert "**CLOSE** NOK_PUT_11_20260918" in md
    assert "defensive roll debit-capped" in md
    assert "buy-to-close 10× limit $2.18" in md          # live chain mid
    assert "or take assignment (basis $9.55" in md        # measured basis
    note = rev.get("_debit_cap_demotion") or ""
    assert "34% of the $6,000 new collateral" in note
    watch = "\n".join(render_watch_with_commentary([], [rev], snap))
    assert "Roll demoted (debit cap)" in watch


def test_close_clean_verdict_close_survives_debit_roll():
    """The instructed shape: verdict CLOSE_CLEAN + a 34%-of-collateral
    debit roll → the CLOSE action is present (verdict-driven path; the
    disproportionate roll never composes). Chain mid nudged to $2.13 →
    extrinsic 13.6% < the 15% clean bar → CLOSE_CLEAN (yesterday's NOK)."""
    rev = _nok_review()
    snap = _nok_snapshot(chain_bid=2.10, chain_ask=2.16)
    md = _render([rev], snap)
    assert "**CLOSE** NOK_PUT_11_20260918" in md
    assert "exit-cost verdict: clean exit" in md
    assert "EXECUTE ROLL" not in md


def test_debit_cap_close_fallback_kill_switch():
    """exit_cost.verdict_drives_action: false → the debit cap demotes to a
    Watch note only (legacy behavior) — no verdict-driven CLOSE composed."""
    rev = _nok_review()
    snap = _nok_snapshot(exit_cost={"verdict_drives_action": False})
    md = _render([rev], snap)
    assert "**CLOSE** NOK_PUT_11_20260918" not in md
    assert "EXECUTE ROLL" not in md
    assert "34% of the $6,000 new collateral" in (rev.get("_debit_cap_demotion") or "")


def test_debit_cap_roll_dont_close_stays_watch_only():
    """Guard against over-application: a debit-capped roll whose verdict is
    ROLL_DONT_CLOSE (pumped extrinsic — closing pays panic premium) keeps
    the Watch-note-only demotion; no CLOSE is composed (the IREN shape:
    extrinsic ~40% of the buyback at IV rank 96)."""
    rev = {
        "contract": "IREN_PUT_47_20261218",
        "underlying": "IREN", "type": "PUT", "qty": -1,
        "strike": 47.0, "expiration": "2026-12-18",
        "entry_price": 18.64, "current_mid": 16.88, "days_to_expiry": 141,
        "delta": -0.45,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "1× $37P Jan 15 '27",
             "instruction": {"sell_strike": 37.0,
                             "sell_expiration": "2027-01-15",
                             "sell_mid": 10.52, "sell_bid": 10.35,
                             "sell_ask": 10.70},
             "netDollars": -652.0, "dteExtension": 28},
        ],
    }
    snap = _snapshot(
        quotes={"IREN": {"last": 36.88}},
        chains={"IREN_2026-12-18": {"puts": [
            {"strike": 47.0, "bid": 16.35, "ask": 17.25}]}},
        iv_ranks={"IREN": 96},
    )
    md = _render([rev], snap)
    assert "EXECUTE ROLL" not in md
    assert "**CLOSE** IREN_PUT_47_20261218" not in md
    assert rev.get("_debit_cap_demotion")


# ── Defect 4: Risk Alerts word-boundary truncation ────────────────────────

def test_risk_alert_truncates_at_word_boundary():
    """Observed (2026-07-31): '- 🎯 PLTR_PUT_130_20270115 →
    **ROLL_OUT_AND_DOWN**: 🎯 Strike tested (δ 0.47 ≥ 0.45) with -4%
    captured and 168 DTE — credit-roll wind' — the [:80] slice cut
    mid-word. Risk Alert rationales truncate at a word boundary with an
    ellipsis (same helper as the task-#40 URGENT-title fix)."""
    rationale = ("🎯 Strike tested (δ 0.47 ≥ 0.45) with -4% captured and "
                 "168 DTE — credit-roll window open: same-strike Oct 16 "
                 "still pays $1.10/share while it lasts.")
    rev = {
        "contract": "PLTR_PUT_130_20270115", "underlying": "PLTR",
        "type": "PUT", "qty": -1, "recommendation": "ROLL_OUT_AND_DOWN",
        "rationale": rationale,
    }
    md = "\n".join(render_risk_alerts([], [rev], {}))
    line = next(ln for ln in md.splitlines() if "PLTR_PUT_130" in ln)
    assert line.endswith("…"), f"expected word-boundary ellipsis: {line!r}"
    # The last rendered token must be a COMPLETE word from the rationale —
    # never a mid-word fragment like 'wind'.
    last_token = line.rstrip(" …").split()[-1]
    assert last_token in rationale.split(), (
        f"line ends mid-word: {last_token!r} in {line!r}")


def test_risk_alert_short_rationale_untouched():
    """Rationales at/under the limit pass through with no ellipsis."""
    rev = {
        "contract": "MU_PUT_950_20261218", "underlying": "MU",
        "type": "PUT", "qty": -1, "recommendation": "ROLL_OUT_AND_DOWN",
        "rationale": "Tested at the strike — roll down-and-out.",
    }
    md = "\n".join(render_risk_alerts([], [rev], {}))
    line = next(ln for ln in md.splitlines() if "MU_PUT_950" in ln)
    assert line.endswith("roll down-and-out.")
    assert "…" not in line


# ── Defect 1: Fable advisor — executed rolls falsify stale pattern notes ──

_EXECUTED_ROLLS = [
    {"old_symbol": "NVDA_PUT_200_20260918", "new_symbol": "NVDA_PUT_190_20261120",
     "underlying": "NVDA", "type": "PUT", "old_strike": 200.0,
     "new_strike": 190.0, "old_exp": "2026-09-18", "new_exp": "2026-11-20",
     "same_strike": False, "new_premium": 11.20},
    {"old_symbol": "META_PUT_575_20260821", "new_symbol": "META_PUT_570_20261016",
     "underlying": "META", "type": "PUT", "old_strike": 575.0,
     "new_strike": 570.0, "old_exp": "2026-08-21", "new_exp": "2026-10-16",
     "same_strike": False, "new_premium": 30.10},
]

_ROTTED_MEMORY = (
    f"{fa.MEMORY_HEADER}\n\n"
    f"{fa.USER_NOTES_HEADER}\n\n- Defer SPY hedge until VIX > 22\n\n"
    f"{fa.RECENT_REVIEWS_HEADER}\n\n"
    "### 2026-07-29 — Fable review\n\n"
    "**Roll-avoidance pattern — twenty-seventh week.** Twelve positions "
    "are now flagged for rolls. You have never executed a roll.\n"
)


def test_executed_rolls_block_in_prompt():
    """Observed (2026-07-31 review): 'Roll-avoidance pattern — thirty-third
    week. Five rolls dropped without execution again. You have never
    executed a roll.' rendered in the SAME review as 'NVDA roll detected.'
    The prompt must carry the detected rolls as explicit falsification
    evidence with a retire instruction."""
    msg = fa._build_user_message(
        "Brief." * 50, "", [], executed_rolls=_EXECUTED_ROLLS)
    assert "<executed-rolls-this-session>" in msg
    assert "The user HAS executed rolls" in msg
    assert "- NVDA: $200P 2026-09-18 → $190P 2026-11-20" in msg
    assert "- META: $575P 2026-08-21 → $570P 2026-10-16" in msg
    assert "FALSIFIED" in msg
    assert "retire it" in msg


def test_no_executed_rolls_no_block():
    """No detected rolls → no block, no fabricated evidence (fail-open)."""
    for empty in (None, []):
        msg = fa._build_user_message("Brief." * 50, "", [],
                                     executed_rolls=empty)
        assert "<executed-rolls-this-session>" not in msg


def test_system_prompt_memory_hygiene_rules():
    """The standing prompt rules: (a) pattern notes re-validated against
    today's facts, retired with a dated correction when contradicted;
    (b) counters expressed in SESSIONS with dates, never inferred weeks
    (the 'thirty-third week' on a 12-week-old book)."""
    assert "FALSIFIED" in fa.SYSTEM_PROMPT
    assert "Roll-avoidance note retired" in fa.SYSTEM_PROMPT
    assert "SESSIONS with dates" in fa.SYSTEM_PROMPT
    assert "12 sessions since Jul 3" in fa.SYSTEM_PROMPT
    assert "evidence date" in fa.SYSTEM_PROMPT


def test_roll_falsification_note_helper():
    """Deterministic correction fires only when BOTH rolls were executed
    AND memory carries a 'never rolls' / roll-avoidance note."""
    reviews = fa._parse_memory(_ROTTED_MEMORY)["recent_reviews"]
    note = fa._roll_falsification_note(reviews, _EXECUTED_ROLLS, TODAY)
    assert note is not None
    assert "system correction 2026-07-31" in note
    assert "falsified" in note
    assert "META" in note and "NVDA" in note
    # no rolls → no correction
    assert fa._roll_falsification_note(reviews, [], TODAY) is None
    assert fa._roll_falsification_note(reviews, None, TODAY) is None
    # rolls but no stale note in memory → nothing to correct
    clean = [{"date": "2026-07-29", "text": "All positions healthy."}]
    assert fa._roll_falsification_note(clean, _EXECUTED_ROLLS, TODAY) is None


def test_memory_write_carries_system_correction(tmp_path, monkeypatch):
    """End-to-end: memory carrying the rotted 'never executed a roll' note
    + detected executed rolls → today's auto-managed entry gets the dated
    system correction appended; the user's Notes section is untouched."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")
    mem_path = tmp_path / "state" / "fable_advisor_memory.md"
    mem_path.parent.mkdir(parents=True)
    mem_path.write_text(_ROTTED_MEMORY, encoding="utf-8")
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()

    def _ok_call(user_message, **kwargs):
        # The prompt must have carried the falsification evidence
        assert "<executed-rolls-this-session>" in user_message
        return (True, {"text": "Roll-avoidance note retired 2026-07-31 — "
                               "you executed 2 rolls this week.",
                       "error": None, "request_id": None, "usage": None})

    with patch.object(fa, "_call_anthropic", side_effect=_ok_call):
        result = fa.generate_advisor_review(
            "Coverage 0.10x. " * 50, snap_dir,
            config={"fable_advisor": {"enabled": True}},
            memory_path=mem_path, today=date(2026, 7, 31),
            executed_rolls=_EXECUTED_ROLLS,
        )
    assert result["status"] == "ok"
    written = mem_path.read_text(encoding="utf-8")
    today_entry = written.split("### 2026-07-31", 1)[1].split("### ", 1)[0]
    assert "system correction 2026-07-31" in today_entry
    assert "falsified" in today_entry
    # User notes preserved verbatim
    assert "Defer SPY hedge until VIX > 22" in written


def test_memory_write_no_correction_without_rolls(tmp_path, monkeypatch):
    """Same rotted memory, NO executed rolls → no system correction is
    fabricated (the note may still be true this session)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")
    mem_path = tmp_path / "state" / "fable_advisor_memory.md"
    mem_path.parent.mkdir(parents=True)
    mem_path.write_text(_ROTTED_MEMORY, encoding="utf-8")
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()

    def _ok_call(user_message, **kwargs):
        assert "<executed-rolls-this-session>" not in user_message
        return (True, {"text": "Today's review.", "error": None,
                       "request_id": None, "usage": None})

    with patch.object(fa, "_call_anthropic", side_effect=_ok_call):
        result = fa.generate_advisor_review(
            "Coverage 0.10x. " * 50, snap_dir,
            config={"fable_advisor": {"enabled": True}},
            memory_path=mem_path, today=date(2026, 7, 31),
        )
    assert result["status"] == "ok"
    assert "system correction" not in mem_path.read_text(encoding="utf-8")


def test_cascade_passes_executed_rolls(tmp_path):
    """aggregate's cascade hands the detected rolls through to v2."""
    from steps.aggregate import _run_fable_review_cascade
    from analysis import fable_advisor

    with patch.object(fable_advisor, "generate_advisor_review") as mock_v2:
        mock_v2.return_value = {"status": "ok", "text": "V2 review",
                                "model": "opus", "elapsed_ms": 100,
                                "review_count_before": 1}
        _run_fable_review_cascade(
            "Brief." * 50, tmp_path, {"fable_advisor": {"enabled": True}},
            executed_rolls=_EXECUTED_ROLLS)
    assert mock_v2.call_args.kwargs.get("executed_rolls") == _EXECUTED_ROLLS
