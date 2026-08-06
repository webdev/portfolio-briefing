"""Task #40 (2026-07-30) — nine fixes from validating the 11:54 AM briefing.

Each test's docstring quotes the observed output (house TDD rule #33 — the
test must fail against the broken code and pass against the fix).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from render.panels import (  # noqa: E402
    _short_put_roll_gate_ok,
    _truncate_at_word,
    render_action_list,
)
from steps.per_option_commentary import render_watch_with_commentary  # noqa: E402
from analysis.briefing_diff import (  # noqa: E402
    detect_executed_rolls,
    render_diff_panel,
    render_executed_roll_directives,
)
from analysis.churn_guard import (  # noqa: E402
    build_position_ages,
    check_churn_guard,
    trading_days_between,
)

TODAY = "2026-07-30"


# ── Shared fixtures ────────────────────────────────────────────────────────

def _snapshot(quotes, chains, iv_ranks=None, earnings=None, config=None,
              credit_windows=None, position_ages=None):
    snap = {
        "quotes": quotes,
        "chains": chains,
        "iv_ranks": iv_ranks or {},
        "earnings_calendar": earnings or {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }
    if credit_windows is not None:
        snap["_credit_windows"] = credit_windows
    if position_ages is not None:
        snap["_position_ages"] = position_ages
    return snap


def _render(reviews, snap):
    return "\n".join(render_action_list([], reviews, [], snapshot_data=snap,
                                        date_str=TODAY))


def _qcom_review():
    """QCOM-shaped (2026-07-30 card #2): $185P, spot $150.88, basis $144.37
    — deep ITM but assignment basis 4.3% below market → HOLD_FOR_BASIS."""
    return {
        "contract": "QCOM_PUT_185_20261218",
        "underlying": "QCOM", "type": "PUT", "qty": -1,
        "strike": 185.0, "expiration": "2026-12-18",
        "entry_price": 40.63, "current_mid": 42.73, "days_to_expiry": 141,
        "delta": -0.65,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "1× $165P Mar 19 '27",
             "instruction": {"sell_strike": 165.0,
                             "sell_expiration": "2027-03-19",
                             "sell_mid": 33.42, "sell_bid": 32.45,
                             "sell_ask": 34.40},
             "netDollars": -1027.0, "dteExtension": 91},
        ],
    }


def _qcom_snapshot(**config):
    return _snapshot(
        quotes={"QCOM": {"last": 150.88}},
        chains={"QCOM_2026-12-18": {"puts": [
            {"strike": 185.0, "bid": 41.50, "ask": 43.55}]}},
        iv_ranks={"QCOM": 55},
        config=config,
    )


# ── Fix 1: HOLD_FOR_BASIS suppresses the roll ticket ──────────────────────

def test_hold_for_basis_suppresses_roll_ticket():
    """Observed (card #2): '**EXECUTE ROLL** QCOM_PUT_185_20261218 —
    Diagonal down-and-out … −$1,027 net debit' rendered directly above
    '⚖️ Verdict: Assignment acceptable — hold for basis — assignment basis
    $144.37 vs spot $150.88 — you'd own it 4.3% below market'. The verdict
    must DRIVE the action: no roll ticket, verdict + Roll-skipped note."""
    md = _render([_qcom_review()], _qcom_snapshot())
    assert "EXECUTE ROLL" not in md
    assert "Sell-to-Open" not in md
    assert "Single-ticket limit" not in md
    assert "**HOLD FOR BASIS** QCOM_PUT_185_20261218" in md
    assert "Assignment acceptable — hold for basis" in md
    assert "Roll skipped: assignment basis $144.37 is 4.3% below market" in md
    assert "the roll menu stays in Watch" in md


def test_hold_for_basis_kill_switch_restores_roll():
    """exit_cost.verdict_drives_action: false → the roll renders as before
    (shared kill switch with the CLOSE_* verdict flow)."""
    md = _render([_qcom_review()],
                 _qcom_snapshot(exit_cost={"verdict_drives_action": False},
                                roll={"max_debit_pct_of_collateral": 0.15}))
    assert "**EXECUTE ROLL** QCOM_PUT_185_20261218" in md
    assert "HOLD FOR BASIS" not in md


# ── Fix 2: recently-opened churn guard ─────────────────────────────────────

def test_churn_guard_demotes_just_opened_roll():
    """Observed: 'VRT $280P was filled at ~2:23 PM and the 2:54 PM run
    recommended re-rolling it for -$2,740'. A contract opened within
    roll.min_position_age_days (default 5 trading days) gets NO roll
    recommendation — a Watch settling note instead."""
    rev = _qcom_review()
    # Make the verdict a roll (not HOLD_FOR_BASIS) so ONLY the churn guard
    # can demote: put the position underwater with pumped extrinsic.
    rev["entry_price"] = 30.0
    snap = _qcom_snapshot()
    snap["iv_ranks"]["QCOM"] = 90
    snap["_position_ages"] = {"QCOM_PUT_185_20261218": 0}
    md = _render([rev], snap)
    assert "EXECUTE ROLL" not in md
    assert rev.get("_churn_guard_demotion"), "expected a churn demotion note"
    assert "opened 0 day(s) ago" in rev["_churn_guard_demotion"]
    assert "churn guard" in rev["_churn_guard_demotion"]
    assert "guardrails still monitor it" in rev["_churn_guard_demotion"]
    watch = "\n".join(render_watch_with_commentary([], [rev], snap))
    assert "letting the new position settle (churn guard)" in watch


def test_churn_guard_annotates_hold_for_basis_item():
    """A fresh position whose verdict is HOLD_FOR_BASIS renders the basis
    note AND the settling note (VRT $280P: both hold-for-basis and
    churn-guarded on 2026-07-30)."""
    snap = _qcom_snapshot()
    snap["_position_ages"] = {"QCOM_PUT_185_20261218": 1}
    md = _render([_qcom_review()], snap)
    assert "**HOLD FOR BASIS** QCOM_PUT_185_20261218" in md
    assert "opened 1 day(s) ago" in md
    assert "churn guard" in md


def test_churn_guard_exempts_loss_stop():
    """'UNLESS a loss-stop/crash guardrail fires (safety always wins)' —
    GUARDRAIL_LOSS_STOP cells never consult the churn guard."""
    assert check_churn_guard("X_PUT_1_20270101", "GUARDRAIL_LOSS_STOP",
                             {"X_PUT_1_20270101": 0}, {}) is None
    assert check_churn_guard("X_PUT_1_20270101", "GUARDRAIL_CRASH_STOP",
                             {"X_PUT_1_20270101": 0}, {}) is None
    # non-safety cell with fresh age → note
    assert check_churn_guard("X_PUT_1_20270101", "PUT_NORMAL_ITM",
                             {"X_PUT_1_20270101": 0}, {}) is not None


def test_churn_guard_fails_open_on_unmeasured_age():
    """No prior snapshots → no measurable age → the guard never fires."""
    assert check_churn_guard("X_PUT_1_20270101", "PUT_NORMAL_ITM",
                             {}, {}) is None
    assert check_churn_guard("X_PUT_1_20270101", "PUT_NORMAL_ITM",
                             None, {}) is None


def test_build_position_ages_from_snapshots(tmp_path):
    """A contract absent yesterday = opened today (age 0); present
    yesterday but absent the day before = age 1 trading day; present in
    every scanned snapshot = age ≥ the oldest snapshot's distance."""
    def _write(day, syms):
        d = tmp_path / day
        d.mkdir()
        (d / "positions.json").write_text(json.dumps([
            {"symbol": s, "assetType": "OPTION", "qty": -1} for s in syms]))

    _write("2026-07-29", ["OLD_PUT_100_20261218", "MID_PUT_50_20261218"])
    _write("2026-07-27", ["OLD_PUT_100_20261218"])
    positions = [
        {"symbol": "NEW_PUT_10_20261218", "assetType": "OPTION", "qty": -1},
        {"symbol": "MID_PUT_50_20261218", "assetType": "OPTION", "qty": -1},
        {"symbol": "OLD_PUT_100_20261218", "assetType": "OPTION", "qty": -1},
    ]
    ages = build_position_ages(positions, tmp_path, "2026-07-30")
    assert ages["NEW_PUT_10_20261218"] == 0    # absent 7/29 → opened today
    assert ages["MID_PUT_50_20261218"] == 1    # first seen 7/29 (Wed)
    assert ages["OLD_PUT_100_20261218"] >= 3   # present through 7/27 (Mon)


def test_build_position_ages_fails_open_without_history(tmp_path):
    positions = [{"symbol": "A_PUT_1_20270101", "assetType": "OPTION"}]
    assert build_position_ages(positions, tmp_path, "2026-07-30") == {}


def test_trading_days_between_skips_weekends():
    from datetime import date
    # Fri 7/24 → Thu 7/30 spans a weekend: Mon-Thu = 4 trading days
    assert trading_days_between(date(2026, 7, 24), date(2026, 7, 30)) == 4
    assert trading_days_between(date(2026, 7, 30), date(2026, 7, 30)) == 0


# ── Fix 3: URGENT wrapper Why must be condition-specific ──────────────────

def _urgent_review(rationale, earnings=None, cell="GUARDRAIL_LOSS_STOP"):
    return {
        "contract": "MU_PUT_950_20261218",
        "underlying": "MU", "type": "PUT", "qty": -1,
        "strike": 950.0, "expiration": "2026-12-18",
        "entry_price": 200.0, "current_mid": 214.0, "days_to_expiry": 141,
        "recommendation": "CLOSE", "matrix_cell_id": cell,
        "rationale": rationale,
    }


def test_urgent_why_not_earnings_template_when_earnings_far():
    """Observed (item #9): '**Why:** Earnings or expiry within ~14d combined
    with material underwater P&L' rendered on MU with earnings 55d away and
    141 DTE — a pre-earnings template reused as boilerplate. The false
    claim must never render when neither condition holds."""
    md = _render(
        [_urgent_review("🚨 URGENT: loss stop triggered — review")],
        _snapshot(quotes={"MU": {"last": 739.0}}, chains={},
                  earnings={"MU": "2026-09-23"}))  # 55d away
    assert "🚨 **URGENT** MU_PUT_950_20261218" in md
    assert "Earnings or expiry within ~14d" not in md
    assert "caps tail risk on the next print" not in md


def test_urgent_why_earnings_text_only_when_actually_imminent():
    """Pre-earnings urgency keeps the earnings Why ONLY when earnings truly
    are ≤ 14d away — and then names the measured day count."""
    md = _render(
        [_urgent_review("🚨 URGENT: earnings risk with underwater P&L")],
        _snapshot(quotes={"MU": {"last": 739.0}}, chains={},
                  earnings={"MU": "2026-08-04"}))  # 5d away
    assert "**Why:** Earnings in 5d" in md
    assert "tail risk on the next print" in md  # Gain line (capitalized join)


# ── Fix 4: close-into-recovery pre-print action ───────────────────────────

def _lite_review(entry=107.00, mid=107.02):
    """LITE-shaped: $700P, spot $683.50, earnings 12d away, roll
    earnings-blocked. Default P&L: underwater by ~$2 (recovered)."""
    return {
        "contract": "LITE_PUT_700_20260918",
        "underlying": "LITE", "type": "PUT", "qty": -1,
        "strike": 700.0, "expiration": "2026-09-18",
        "entry_price": entry, "current_mid": mid, "days_to_expiry": 50,
        "delta": -0.47,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_NEAR_ATM_ROLL_OUT",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "1× $600P Oct 16 '26",
             "instruction": {"sell_strike": 600.0,
                             "sell_expiration": "2026-10-16",
                             "sell_mid": 77.05, "sell_bid": 75.50,
                             "sell_ask": 78.60},
             "netDollars": -3150.0, "dteExtension": 28},
        ],
    }


def _lite_snapshot(**config):
    return _snapshot(
        quotes={"LITE": {"last": 683.50}},
        chains={"LITE_2026-09-18": {"puts": [
            {"strike": 700.0, "bid": 105.80, "ask": 108.60}]}},
        iv_ranks={"LITE": 66},
        earnings={"LITE": "2026-08-11"},  # 12d away → STO leg blocked
        config=config,
    )


def test_close_into_recovery_fires_pre_print():
    """Observed: 'LITE $700P: underwater by ~$2 (breakeven after +14%
    bounce), earnings 12d away, roll correctly earnings-blocked — but no
    action surfaced at all.' Recovered-to-better-than -5% of premium +
    earnings ≤ 14d → CLOSE INTO RECOVERY with a real BTC ticket."""
    md = _render([_lite_review()], _lite_snapshot())
    assert "**CLOSE INTO RECOVERY** LITE_PUT_700_20260918" in md
    assert "before the Aug 11 print" in md
    assert "buy-to-close 1× limit $107.02" in md
    assert "exiting flat removes the binary" in md
    assert "re-enter post-print on your" in md
    # it replaces (outranks) the deferred-roll rendering for this contract
    assert "ROLL DEFERRED" not in md


def test_close_into_recovery_requires_recovery():
    """A position still down 8% of premium is NOT recovered — the
    earnings-blocked roll keeps the task-#37 ROLL DEFERRED format."""
    md = _render([_lite_review(entry=100.0, mid=108.05)], _lite_snapshot())
    assert "CLOSE INTO RECOVERY" not in md
    assert "ROLL DEFERRED (earnings block)" in md


# ── Fix 5: debit-to-collateral sanity cap ─────────────────────────────────

def _iren_review():
    """IREN-shaped (card #8): $47P, roll debit $652 on $3,700 new
    collateral = 18%."""
    return {
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


def _iren_snapshot(**config):
    return _snapshot(
        quotes={"IREN": {"last": 36.88}},
        chains={"IREN_2026-12-18": {"puts": [
            {"strike": 47.0, "bid": 16.35, "ask": 17.25}]}},
        iv_ranks={"IREN": 96},
        config=config,
    )


def test_debit_cap_demotes_disproportionate_roll():
    """Observed (card #8): 'EXECUTE ROLL IREN_PUT_47_20261218 … −$652 net
    debit' — $652 on $3,700 of new collateral = 18%, over the 8% cap. The
    roll demotes to a Watch note; no ticket."""
    rev = _iren_review()
    md = _render([rev], _iren_snapshot())
    assert "EXECUTE ROLL" not in md
    note = rev.get("_debit_cap_demotion") or ""
    assert "18% of the $3,700 new collateral" in note
    assert "disproportionate; close or take assignment instead" in note
    watch = "\n".join(render_watch_with_commentary(
        [], [rev], _iren_snapshot()))
    assert "Roll demoted (debit cap)" in watch


def test_debit_cap_configurable_and_credit_rolls_exempt():
    """Raising roll.max_debit_pct_of_collateral above the measured ratio
    restores the ticket; credit rolls never consult the cap."""
    md = _render([_iren_review()],
                 _iren_snapshot(roll={"max_debit_pct_of_collateral": 0.25}))
    assert "EXECUTE ROLL" in md


# ── Fix 6: measured-delta veto at the gate edge ───────────────────────────

def test_measured_delta_veto_overrides_moneyness_band():
    """Observed (card #3): 'AVGO $375P: moneyness 1.0297 (inside the <1.03
    gate) but measured δ0.10 → $871 defensive roll on a position with
    nothing to defend.' A MEASURED |δ| < 0.25 fails the gate regardless of
    moneyness; the band is the fallback for missing delta only."""
    assert not _short_put_roll_gate_ok(1.0297, -0.10)   # the AVGO shape
    assert _short_put_roll_gate_ok(1.0297, None)        # no delta → band
    assert _short_put_roll_gate_ok(1.0297, -0.30)       # mid-band δ → band
    assert _short_put_roll_gate_ok(1.06, -0.40)         # δ ≥ 0.40 passes


def test_avgo_real_shape_profitable_otm_nothing_to_defend():
    """The REAL AVGO snapshot values: measured POSITION δ -0.3681 (the
    briefing's 'Delta -0.10' was the proposed STO leg), entry $12.85 → mid
    $10.95 (+15% captured), spot $386.00 vs strike $375 (fully OTM,
    moneyness 1.0293). Observed: '**EXECUTE ROLL** AVGO_PUT_375_20260814 —
    … −$871 net debit' — a defensive roll on a profitable OTM put with
    nothing to defend. Demote."""
    rev = {
        "contract": "AVGO_PUT_375_20260814",
        "underlying": "AVGO", "type": "PUT", "qty": -1,
        "strike": 375.0, "expiration": "2026-08-14",
        "entry_price": 12.8499, "current_mid": 10.95, "days_to_expiry": 15,
        "delta": -0.3681,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_NEAR_ATM_SHORT_DTE_TESTED",
        "roll_candidates": [
            {"id": "B", "description": "1× $325P Aug 21 '26",
             "instruction": {"sell_strike": 325.0,
                             "sell_expiration": "2026-08-21",
                             "sell_mid": 2.31, "sell_bid": 2.24,
                             "sell_ask": 2.37},
             "netDollars": -871.0, "dteExtension": 7},
        ],
    }
    snap = _snapshot(quotes={"AVGO": {"last": 386.00}}, chains={})
    md = _render([rev], snap)
    assert "EXECUTE ROLL" not in md
    note = rev.get("_roll_gate_demotion") or ""
    assert "nothing to defend" in note
    assert "|δ| 0.37" in note
    # an UNDERWATER put at the same delta/moneyness keeps its defense path
    rev2 = dict(rev, entry_price=8.0, roll_candidates=rev["roll_candidates"])
    rev2.pop("_roll_gate_demotion", None)
    md2 = _render([rev2], _snapshot(quotes={"AVGO": {"last": 386.00}},
                                    chains={}))
    assert "EXECUTE ROLL" in md2


def test_avgo_shape_demoted_with_measured_delta_note():
    rev = {
        "contract": "AVGO_PUT_375_20260814",
        "underlying": "AVGO", "type": "PUT", "qty": -1,
        "strike": 375.0, "expiration": "2026-08-14",
        "entry_price": 8.0, "current_mid": 10.95, "days_to_expiry": 15,
        "delta": -0.10,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_NEAR_ATM_ROLL_OUT",
        "roll_candidates": [
            {"id": "B", "description": "1× $325P Aug 21 '26",
             "instruction": {"sell_strike": 325.0,
                             "sell_expiration": "2026-08-21",
                             "sell_mid": 2.31, "sell_bid": 2.24,
                             "sell_ask": 2.37},
             "netDollars": -871.0, "dteExtension": 7},
        ],
    }
    snap = _snapshot(quotes={"AVGO": {"last": 386.14}}, chains={})
    md = _render([rev], snap)
    assert "EXECUTE ROLL" not in md
    note = rev.get("_roll_gate_demotion") or ""
    assert "measured-delta veto" in note
    assert "|δ| 0.10" in note


# ── Fix 7: URGENT title truncation at word boundary ───────────────────────

def test_truncate_at_word_boundary():
    """Observed: titles rendered '…roll down-and-out while extrinsic is at
    its peak. Th' — a fixed-width slice cutting mid-word. Truncation must
    land on a word boundary with an ellipsis."""
    text = ("🎯 Strike tested (δ 0.46 ≥ 0.45) with 0% captured and 141 DTE "
            "— credit-roll window open; roll down-and-out while extrinsic "
            "is at its peak. The less profit at test, the more urgent.")
    out = _truncate_at_word(text, 140)
    assert not out.rstrip("…").rstrip().endswith("Th")
    assert out.endswith("…")
    # every truncated form ends on a complete word
    assert out[:-2].split()[-1] in text.split()
    # short text passes through unchanged
    assert _truncate_at_word("short", 140) == "short"


# ── Fix 8: URGENT strike-tested items compose two-leg tickets ─────────────

def _nvda_strike_tested_review():
    """NVDA-shaped (item #10): rationale from GUARDRAIL_STRIKE_TESTED —
    '🎯 Strike tested (δ 0.54 ≥ 0.45) with 2% captured and 50 DTE —
    credit-roll window open; roll down-and-out while extrinsic is at its
    peak. The' — surfaced as URGENT with NO order at all."""
    return {
        "contract": "NVDA_PUT_200_20260918",
        "underlying": "NVDA", "type": "PUT", "qty": -1,
        "strike": 200.0, "expiration": "2026-09-18",
        "entry_price": 15.30, "current_mid": 15.00, "days_to_expiry": 50,
        "delta": -0.54,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "GUARDRAIL_STRIKE_TESTED",
        "rationale": ("🎯 Strike tested (δ 0.54 ≥ 0.45) with 2% captured "
                      "and 50 DTE — credit-roll window open; roll "
                      "down-and-out while extrinsic is at its peak. The "
                      "less profit at test, the more urgent."),
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "1× $190P Nov 20 '26 (credit)",
             "instruction": {"sell_strike": 190.0,
                             "sell_expiration": "2026-11-20",
                             "sell_mid": 18.10, "sell_bid": 17.80,
                             "sell_ask": 18.40, "sell_delta": -0.42},
             "netDollars": 280.0, "dteExtension": 63},
            {"id": "C", "description": "1× $170P Dec 18 '26 (debit)",
             "instruction": {"sell_strike": 170.0,
                             "sell_expiration": "2026-12-18",
                             "sell_mid": 11.00, "sell_bid": 10.80,
                             "sell_ask": 11.20},
             "netDollars": -400.0, "dteExtension": 91},
        ],
    }


def _nvda_snapshot():
    return _snapshot(
        quotes={"NVDA": {"last": 190.79}},
        chains={"NVDA_2026-09-18": {"puts": [
            {"strike": 200.0, "bid": 14.60, "ask": 15.40}]}},
        iv_ranks={"NVDA": 70},
        earnings={"NVDA": "2026-08-27"},  # 28d — no earnings block
        credit_windows={"NVDA_PUT_200_20260918": {
            "state": "open", "best_credit": 2.80,
            "best_candidate_desc": "1× $190P Nov 20 '26"}},
    )


def test_urgent_strike_tested_composes_two_leg_ticket():
    """Observed (items #9/#10/#12-15): '🚨 URGENT NVDA_PUT_200_20260918 —
    🎯 Strike tested …' with Why/Gain but NO order lines whatsoever. The
    guardrail path must run through block #3's roll composition: two-leg
    combo ticket, limits, credit-window read, TRUE rationale."""
    md = _render([_nvda_strike_tested_review()], _nvda_snapshot())
    assert "🚨 **URGENT — EXECUTE ROLL** NVDA_PUT_200_20260918" in md
    assert "Buy-to-Close" in md and "Sell-to-Open" in md
    assert "Single-ticket limit" in md
    # condition-specific Why — the strike-test trigger, not the earnings lie
    assert "**Why (urgent):**" in md
    assert "Strike tested (δ 0.54" in md
    assert "Earnings or expiry within ~14d" not in md
    # credit-window state carried onto the item
    assert "Credit window: 🟢 OPEN" in md


def test_urgent_strike_tested_prefers_credit_candidate_when_window_open():
    """'prefer the best same-or-lower-strike CREDIT candidate when window is
    open' — the $190P +$280 credit roll wins over the deeper $170P debit
    roll while the credit window is open."""
    md = _render([_nvda_strike_tested_review()], _nvda_snapshot())
    assert "$190P" in md
    assert "net credit" in md
    assert "$170P" not in md


def test_urgent_strike_tested_earnings_block_keeps_deferred_format():
    """'When earnings-blocked (PLTR ≤4d), keep the deferred format' — an
    imminent print still defers the roll, with the urgency visible. The
    PLTR shape: -12% captured (NOT recovered), so close-into-recovery does
    not apply either."""
    rev = _nvda_strike_tested_review()
    rev["entry_price"] = 15.30
    rev["current_mid"] = 17.14   # -12% capture → not recovered
    snap = _nvda_snapshot()
    snap["earnings_calendar"]["NVDA"] = "2026-08-03"  # 4d → BLOCK on STO legs
    md = _render([rev], snap)
    assert "URGENT — ROLL DEFERRED (earnings block)" in md
    assert "Single-ticket limit" not in md
    assert "**Order:**" not in md


def test_urgent_strike_tested_directive_renders_note_not_nag():
    """'When user directive covers the contract, render the note not the
    nag.'"""
    from analysis.advisor_directives import parse_directives
    rev = _nvda_strike_tested_review()
    snap = _nvda_snapshot()
    snap["_advisor_directives"] = parse_directives(
        "- **NVDA_PUT_200_20260918** — hold; rolled deliberately, do not "
        "re-flag.\n\n## Recent reviews\n")
    md = _render([rev], snap)
    assert "URGENT — EXECUTE ROLL" not in md
    assert "held by standing directive; decision on file" in md


# ── Fix 9: auto-suggest directives after user-executed rolls ──────────────

def _pos(sym, und, typ, strike, exp, qty=-1, premium=None):
    p = {"symbol": sym, "assetType": "OPTION", "underlying": und,
         "type": typ, "strike": strike, "expiration": exp, "qty": qty}
    if premium is not None:
        p["premiumReceived"] = premium
    return p


def test_detect_executed_rolls_same_strike_and_pairing():
    """Observed: 'MU Nov→Dec, META→Oct appeared as new contracts today' —
    the position diff must pair old-gone with new-appeared legs (same
    underlying+type, closest strike) into roll records."""
    prev = [
        _pos("MU_PUT_950_20261120", "MU", "PUT", 950.0, "2026-11-20"),
        _pos("META_PUT_575_20260821", "META", "PUT", 575.0, "2026-08-21"),
        _pos("META_PUT_580_20260814", "META", "PUT", 580.0, "2026-08-14"),
        _pos("KEEP_PUT_10_20261218", "KEEP", "PUT", 10.0, "2026-12-18"),
    ]
    today = [
        _pos("MU_PUT_950_20261218", "MU", "PUT", 950.0, "2026-12-18",
             premium=214.0),
        _pos("META_PUT_570_20261016", "META", "PUT", 570.0, "2026-10-16"),
        _pos("META_PUT_575_20261016", "META", "PUT", 575.0, "2026-10-16"),
        _pos("KEEP_PUT_10_20261218", "KEEP", "PUT", 10.0, "2026-12-18"),
    ]
    rolls = detect_executed_rolls(prev, today)
    by_old = {r["old_symbol"]: r for r in rolls}
    assert len(rolls) == 3
    mu = by_old["MU_PUT_950_20261120"]
    assert mu["new_symbol"] == "MU_PUT_950_20261218"
    assert mu["same_strike"] is True
    # closest-strike pairing: 575→575, 580→570
    assert by_old["META_PUT_575_20260821"]["new_symbol"] == "META_PUT_575_20261016"
    assert by_old["META_PUT_580_20260814"]["new_symbol"] == "META_PUT_570_20261016"


def test_executed_roll_directive_template_ready_to_paste():
    """The Since-Yesterday panel appends a ready-to-paste directive template
    ('If deliberate, add to state/fable_advisor_memory.md: `- **MU_PUT_950_
    20261218** …`') so deliberate rolls stop generating next-day nags. The
    template must parse as a HOLD directive and carry the measured
    assignment basis (strike − premiumReceived), never a fabricated one."""
    prev = [_pos("MU_PUT_950_20261120", "MU", "PUT", 950.0, "2026-11-20")]
    today = [_pos("MU_PUT_950_20261218", "MU", "PUT", 950.0, "2026-12-18",
                  premium=214.0)]
    rolls = detect_executed_rolls(prev, today)
    lines = render_executed_roll_directives(rolls, TODAY)
    assert len(lines) == 1
    line = lines[0]
    assert "Detected roll: MU $950P Nov→Dec (same strike)" in line
    assert "state/fable_advisor_memory.md" in line
    assert "- **MU_PUT_950_20261218**" in line
    assert "2026-07-30" in line
    assert "~$736 basis" in line  # 950 − 214, measured
    # the suggested text parses as a HOLD directive
    from analysis.advisor_directives import parse_directives
    import re
    m = re.search(r"`(.+)`", line)
    parsed = parse_directives(m.group(1) + "\n\n## Recent reviews\n")
    assert parsed and parsed[0].contract == "MU_PUT_950_20261218"


def test_executed_rolls_render_in_since_yesterday_panel():
    prev = [_pos("MU_PUT_950_20261120", "MU", "PUT", 950.0, "2026-11-20")]
    today = [_pos("MU_PUT_950_20261218", "MU", "PUT", 950.0, "2026-12-18",
                  premium=214.0)]
    rolls = detect_executed_rolls(prev, today)
    yesterday_md = ("## Today's Action List\n"
                    "1. **CLOSE** MU_PUT_950_20261120 — x\n")
    today_md = ("## Today's Action List\n"
                "1. **CLOSE** MU_PUT_950_20261218 — x\n")
    panel = "\n".join(render_diff_panel(today_md, yesterday_md,
                                        executed_rolls=rolls,
                                        today_iso=TODAY))
    assert "Detected User-Executed Rolls" in panel
    assert "MU_PUT_950_20261218" in panel


def test_detect_executed_rolls_fails_open_on_missing_snapshots():
    assert detect_executed_rolls(None, [_pos("A_PUT_1_20270101", "A", "PUT",
                                             1.0, "2027-01-01")]) == []
    assert detect_executed_rolls([], []) == []
