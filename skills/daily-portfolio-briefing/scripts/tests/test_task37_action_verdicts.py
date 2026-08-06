"""Task #37 (2026-07-30) — four fixes found in the real briefing.

User symptoms, verbatim from the 2026-07-30 briefing review:

1. "five actions carry '⚖️ Verdict: CLOSE — clean exit — mostly intrinsic'
   yet each renders as EXECUTE ROLL with a large debit ($5,002 on META
   $580P to roll into a $480P collecting $4.03!)" — the verdict layer
   annotated but never DROVE the action.
2. "LITE roll renders 'Earnings check: 🔴 BLOCK: Imminent earnings 12d
   away' UNDER a fully actionable limit-order ticket" — selling puts
   through earnings.
3. "NOK roll proposes BTC $11P / STO 10× $1P at bid $0.00, mid $0.00" —
   garbage STO leg (covered in wheel-roll-advisor tests; the render-side
   effects are pinned here).
4. Stub data / boilerplate: identical "EV $+0 — P(assignment) 30%" on all
   14 cards; "$850P with spot $835.83" labeled OTM; "the roll books net
   credit" on debit rolls; "cap headroom" call-language on puts; stalled
   HEDGE item conflating stress coverage with hedge coverage.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from render.panels import (  # noqa: E402
    _format_delta_line,
    _roll_why_text,
    render_action_list,
)
from analysis.rec_aging import age_actions  # noqa: E402


TODAY = "2026-07-30"


# ── Fixtures ───────────────────────────────────────────────────────────────

def _snapshot(quotes, chains, iv_ranks=None, earnings=None, config=None):
    return {
        "quotes": quotes,
        "chains": chains,
        "iv_ranks": iv_ranks or {},
        "earnings_calendar": earnings or {},
        "_config": {"core_positions": [], "accounts": [],
                    **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }


def _meta_review():
    """META-shaped: $580P, spot $528.71 — deep ITM, buyback is 98%
    intrinsic → exit-cost verdict CLOSE_CLEAN. A roll-down candidate exists
    (the $480P the real briefing wanted to pay $5,002 to roll into)."""
    return {
        "contract": "META_PUT_580_20260814",
        "underlying": "META", "type": "PUT", "qty": -1,
        "strike": 580.0, "expiration": "2026-08-14",
        "entry_price": 9.31, "current_mid": 52.12, "days_to_expiry": 15,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "1× $480P Aug 21 '26",
             "instruction": {"sell_strike": 480.0,
                             "sell_expiration": "2026-08-21",
                             "sell_mid": 4.03, "sell_bid": 3.85,
                             "sell_ask": 4.20},
             "netDollars": -4827.0, "dteExtension": 7,
             "deltaChange": -0.20},
        ],
    }


def _meta_snapshot(**config):
    return _snapshot(
        quotes={"META": {"last": 528.71}},
        chains={"META_2026-08-14": {"puts": [
            {"strike": 580.0, "bid": 50.05, "ask": 54.20}]}},
        iv_ranks={"META": 55},
        earnings={"META": "2026-10-28"},
        config=config,
    )


def _mu_review():
    """MU-shaped: $950P, spot $835.83 — ITM but the buyback is 52% panic
    extrinsic at IV rank 83 → verdict ROLL_DONT_CLOSE. The roll ticket must
    still render (it did today, correctly) — but with sign-aware Why/Gain,
    put-cushion language, honest validator, and a strike-vs-spot delta tag."""
    return {
        "contract": "MU_PUT_950_20261120",
        "underlying": "MU", "type": "PUT", "qty": -1,
        "strike": 950.0, "expiration": "2026-11-20",
        "entry_price": 220.0, "current_mid": 229.25, "days_to_expiry": 113,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "1× $850P Dec 18 '26",
             "instruction": {"sell_strike": 850.0,
                             "sell_expiration": "2026-12-18",
                             "sell_mid": 185.18, "sell_bid": 182.25,
                             "sell_ask": 188.10},
             "netDollars": -4700.0, "dteExtension": 28,
             "deltaChange": -0.40},
        ],
    }


def _mu_snapshot(**config):
    return _snapshot(
        quotes={"MU": {"last": 835.83}},
        chains={"MU_2026-11-20": {"puts": [
            {"strike": 950.0, "bid": 233.0, "ask": 238.0}]}},
        iv_ranks={"MU": 83},
        earnings={"MU": "2026-09-23"},  # 55d away — no block
        config=config,
    )


def _render(reviews, snap):
    return "\n".join(render_action_list([], reviews, [], snapshot_data=snap,
                                        date_str=TODAY))


# ── Fix 1: exit-cost verdict DRIVES the action ─────────────────────────────

def test_verdict_close_clean_forces_close_action():
    """META-shaped: CLOSE_CLEAN verdict + roll directive → the action is a
    plain CLOSE (BTC at mid, GTC); no debit roll ticket, no STO leg."""
    md = _render([_meta_review()], _meta_snapshot())
    assert "**CLOSE** META_PUT_580_20260814" in md
    assert "EXECUTE ROLL" not in md
    assert "Sell-to-Open" not in md
    assert "Single-ticket limit" not in md
    assert "Roll skipped: exit-cost verdict says closing is cheap" in md
    # BTC ticket at the measured chain mid ((50.05 + 54.20) / 2 = 52.125)
    assert "limit $52.12" in md
    assert "GTC" in md
    # anatomy block still renders for context
    assert "Exit cost anatomy" in md


def test_verdict_roll_dont_close_keeps_roll():
    """MU-shaped: ROLL_DONT_CLOSE verdict → the roll ticket renders exactly
    as before (this was correct in today's briefing)."""
    md = _render([_mu_review()], _mu_snapshot())
    assert "**EXECUTE ROLL** MU_PUT_950_20261120" in md
    assert "Sell-to-Open" in md
    assert "$850" in md
    assert "ROLL, don't close" in md
    assert "Roll skipped" not in md


def test_verdict_drives_action_kill_switch():
    """exit_cost.verdict_drives_action: false → old behavior (the roll
    renders even on a CLOSE_CLEAN verdict; the verdict stays annotation).

    (Task #40 fix 5 note: the META fixture's $4,827 debit is 10.1% of the
    $48,000 new-leg collateral, which the new debit-to-collateral cap would
    demote; the cap is raised here so this test keeps pinning ONLY the
    kill-switch behavior — the cap has its own tests in
    test_task40_briefing_fixes.py.)"""
    md = _render([_meta_review()],
                 _meta_snapshot(exit_cost={"verdict_drives_action": False},
                                roll={"max_debit_pct_of_collateral": 0.15}))
    assert "**EXECUTE ROLL** META_PUT_580_20260814" in md
    assert "Roll skipped" not in md
    # the verdict annotation itself still renders
    assert "CLOSE — clean exit" in md


# ── Fix 2: earnings 🔴 BLOCK suppresses the actionable ticket ──────────────

def _lite_review(extra_candidates=None):
    """LITE-shaped: $700P, spot $691.70 — 92% extrinsic → ROLL_DONT_CLOSE,
    but the only roll-down's STO leg (Oct 16) spans the Aug 11 print
    (12d away → earnings guard BLOCK)."""
    cands = [
        {"id": "A", "description": "HOLD", "instruction": None,
         "netDollars": 0, "dteExtension": 0},
        {"id": "B", "description": "1× $600P Oct 16 '26",
         "instruction": {"sell_strike": 600.0,
                         "sell_expiration": "2026-10-16",
                         "sell_mid": 84.60, "sell_bid": 82.00,
                         "sell_ask": 87.20},
         "netDollars": -2605.0, "dteExtension": 28, "deltaChange": -0.30},
    ]
    cands.extend(extra_candidates or [])
    return {
        "contract": "LITE_PUT_700_20260918",
        "underlying": "LITE", "type": "PUT", "qty": -1,
        "strike": 700.0, "expiration": "2026-09-18",
        "entry_price": 100.0, "current_mid": 108.05, "days_to_expiry": 50,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_NEAR_ATM_ROLL_OUT",
        "roll_candidates": cands,
    }


def _lite_snapshot():
    return _snapshot(
        quotes={"LITE": {"last": 691.70}},
        chains={"LITE_2026-09-18": {"puts": [
            {"strike": 700.0, "bid": 105.60, "ask": 110.50}]}},
        iv_ranks={"LITE": 66},
        earnings={"LITE": "2026-08-11"},  # 12d away → BLOCK on Oct 16 STO
    )


def test_earnings_block_demotes_ticket():
    """LITE-shaped: STO leg spans the print → visible ROLL DEFERRED entry
    with the analysis, but NO executable order/limit lines (rule #24:
    demote, never hide)."""
    md = _render([_lite_review()], _lite_snapshot())
    assert "ROLL DEFERRED (earnings block)" in md
    assert "EXECUTE ROLL" not in md
    assert "Single-ticket limit" not in md
    assert "**Order:**" not in md
    assert "🔴" in md  # the BLOCK badge stays visible
    assert "re-evaluate after earnings or pick a post-print expiration" in md
    # reference strikes stay visible for planning
    assert "$600" in md and "NOT an order" in md


def test_earnings_block_prefers_pre_print_expiration():
    """When an alternative STO expiration CLEARS the print (expires before
    earnings) with viable premium, use it — and say so."""
    pre_print = {
        "id": "C", "description": "1× $700P Aug 07 '26",
        "instruction": {"sell_strike": 700.0,
                        "sell_expiration": "2026-08-07",
                        "sell_mid": 111.0, "sell_bid": 110.0,
                        "sell_ask": 112.0},
        "netDollars": 195.0, "dteExtension": -42, "deltaChange": -0.55,
    }
    md = _render([_lite_review(extra_candidates=[pre_print])],
                 _lite_snapshot())
    assert "ROLL DEFERRED" not in md
    assert "**EXECUTE ROLL** LITE_PUT_700_20260918" in md
    assert "Aug 07" in md
    assert "chosen to clear the earnings print" in md


# ── Fix 4b: delta ITM/OTM label from strike vs spot ────────────────────────

def test_delta_itm_label_uses_strike_vs_spot():
    """'MU card says Delta -0.40 (~40% ITM probability — OTM) for an $850P
    with spot $835.83 — that's ITM.' The tag must come from moneyness, not
    a |delta| > 0.5 threshold."""
    line = _format_delta_line(-0.40, "PUT", strike=850.0, spot=835.83)
    assert "ITM" in line and "OTM" not in line.replace("ITM", "")
    assert line.endswith("— ITM)")
    assert "~40% ITM probability" in line
    # put above spot → OTM
    assert _format_delta_line(-0.20, "PUT", strike=480.0, spot=528.71).endswith("— OTM)")
    # call: spot above strike → ITM
    assert _format_delta_line(0.60, "CALL", strike=500.0, spot=528.71).endswith("— ITM)")
    # missing strike/spot → no tag rather than a guess
    no_ctx = _format_delta_line(-0.40)
    assert "ITM probability" in no_ctx
    assert "— ITM" not in no_ctx and "— OTM" not in no_ctx
    # end-to-end: the MU render tags the $850P ITM (spot $835.83)
    md = _render([_mu_review()], _mu_snapshot())
    assert "— ITM)" in md


# ── Fix 4c/4d: sign- and side-aware Why/Gain text ──────────────────────────

def test_roll_why_text_sign_aware():
    """A debit roll must say 'pays $X debit ...' — never 'books net credit'."""
    debit_why = _roll_why_text(
        True, -4700.0, True, 1550.0, 950.0, 850.0, 835.83, 28,
        "Fri Nov 20 '26", "Fri Dec 18 '26")
    assert "pays $4,700 debit" in debit_why
    assert "books net credit" not in debit_why
    assert "strike reduction" in debit_why
    credit_why = _roll_why_text(
        True, 1200.0, True, 500.0, 950.0, 950.0, 835.83, 28,
        "Fri Nov 20 '26", "Fri Dec 18 '26")
    assert "books net credit" in credit_why
    # end-to-end on the MU debit roll
    md = _render([_mu_review()], _mu_snapshot())
    assert "books net credit" not in md
    assert "debit" in md
    # put cushion language, not call-side cap headroom (fix 4d)
    assert "more downside cushion" in md
    assert "cap headroom" not in md
    assert "Cap buffer" not in md
    assert "Strike cushion" in md


# ── Fix 4a: no hardcoded EV / P(assignment) constants ──────────────────────

def test_no_hardcoded_ev_passignment():
    """The old path fed delta=0.30 and credit=0 into the validator on every
    put roll, rendering the same 'EV $+0 — P(assignment) 30%' on all 14
    cards. Debit rolls now render an honest n/a; credit rolls use the
    MEASURED chain delta."""
    # Debit roll (MU-shaped) → n/a, never a fake $+0 / 30%
    md_debit = _render([_mu_review()], _mu_snapshot())
    assert "EV n/a (not computed for debit rolls" in md_debit
    assert "EV $+0" not in md_debit
    assert "P(assignment) 30%" not in md_debit

    # Credit roll with a measured chain delta → real numbers from that delta
    credit_rev = {
        "contract": "XYZ_PUT_190_20261120",
        "underlying": "XYZ", "type": "PUT", "qty": -1,
        "strike": 190.0, "expiration": "2026-11-20",
        "entry_price": 5.0, "current_mid": 4.0, "days_to_expiry": 113,
        "recommendation": "ROLL_OUT",
        "matrix_cell_id": "PUT_NORMAL_NEAR_ATM_ROLL_OUT",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "1× $190P Feb 19 '27",
             "instruction": {"sell_strike": 190.0,
                             "sell_expiration": "2027-02-19",
                             "sell_mid": 9.20, "sell_bid": 9.00,
                             "sell_ask": 9.40, "sell_delta": -0.22},
             "netDollars": 500.0, "dteExtension": 91},
        ],
    }
    snap = _snapshot(quotes={"XYZ": {"last": 191.0}}, chains={},
                     iv_ranks={"XYZ": 50})
    md_credit = _render([credit_rev], snap)
    assert "**EXECUTE ROLL** XYZ_PUT_190" in md_credit
    assert "P(assignment) 22%" in md_credit  # measured 0.22, not 30%
    assert "P(assignment) 30%" not in md_credit


# ── Fix 4e: stalled items re-render from CURRENT data ──────────────────────

def test_stalled_item_rerenders_current_summary():
    """A stalled item's summary must reflect TODAY's rendered data, not the
    text frozen at first_flagged ('coverage 10% → target 10%' while the
    Hedge Book showed 0%)."""
    prior_state = {
        "HEDGE:SPY": {
            "first_flagged": "2026-07-04", "days_flagged": 26,
            "last_status": "IGNORED", "last_aged": "2026-07-29",
            "summary": "**HEDGE** Buy 19× SPY put (~$14,026; coverage 10% → target 10%)",
        }
    }
    today_action = {
        "key": "HEDGE:SPY", "kind": "HEDGE", "ident": "SPY",
        "summary": ("**HEDGE** Buy 19× SPY put (~$14,026; hedge coverage 0% "
                    "→ target 10%; stress coverage 0.10×)"),
    }
    updated, aged = age_actions([today_action], prior_state,
                                {"HEDGE:SPY": "IGNORED"}, today_iso=TODAY)
    assert aged["HEDGE:SPY"]["summary"] == today_action["summary"], (
        "stalled/aged items must carry TODAY's summary, not the frozen one")
    assert "coverage 10% → target 10%" not in aged["HEDGE:SPY"]["summary"]
    assert updated["HEDGE:SPY"]["summary"].startswith("**HEDGE** Buy 19× SPY put (~$14,026; hedge coverage 0%")
    assert aged["HEDGE:SPY"]["days_flagged"] == 27  # clock still ticks


def test_hedge_action_line_uses_hedge_book_coverage_not_stress_ratio():
    """The HEDGE action line must show the hedge book's own current/target
    coverage — not the stress-coverage ratio dressed up as hedge coverage
    (the 'coverage 10% → target 10%' vs 'Current coverage: 0%' mismatch)."""
    class _SC:
        coverage_ratio = 0.10

    class _Rec:
        instrument = "SPY_PUT"
        target_strike = 701
        target_expiration = None
        contracts = 19
        estimated_cost = 14026.0

    class _HB:
        current_coverage_pct = 0.0
        target_coverage_pct = 0.10
        recommendations = [_Rec()]

    analytics = {"stress_coverage": _SC(), "hedge_book": _HB(),
                 "nlv": 1_000_000, "spy_price": 738.0}
    md = "\n".join(render_action_list(
        [], [], [], analytics=analytics,
        snapshot_data=_snapshot({}, {}), date_str=TODAY))
    assert "**HEDGE**" in md
    assert "hedge coverage 0% → target 10%" in md
    assert "stress coverage 0.10×" in md
    assert "coverage 10% → target 10%" not in md
