"""Four-bug batch from the real 2026-08-24 briefing (rule #43 — bug-fix-on-
sight; rule #33 — every docstring quotes the observed output).

BUG A — IREN precedence violated rule #51: "8. **CLOSE INTO RECOVERY**
IREN_PUT_47_20261218 — recovered to ~breakeven (+29.9% of premium) before
the Aug 27 print" while assignment basis $28.36 vs spot $40.06 = 29.2%
cushion ≥ the 25% willing-owner threshold (three days earlier the SAME
position correctly rendered "HOLD THROUGH EARNINGS — willing owner" at 31%
cushion). The fortress outranks the recovery close.

BUG B — roll cards rendered the WRONG delta with a wrong label: "Delta
-0.08 (~8% ITM probability — OTM)" on a SOXX $520P $20 ITM (spot $499.69,
true position delta ~-0.63) — the NEW STO leg's delta ($420P) wearing the
POSITION's label.

BUG C — meaningless debit annualization: "net-cash -291.1% ann. on
position over the +7d extension" (SOXX) / "-200.7% ann." (AMAT) — a
one-time debit annualized over a 7-day window.

BUG D — NVDA $260C close was not earnings-aware: appendix item #6 said only
"45% of max profit already captured with 88d still on the contract" with NO
mention that NVDA prints in 2 days, and ranked in the APPENDIX below
non-urgent closes (TSLA #5, SMH #2).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from render.panels import render_action_list  # noqa: E402
from analysis.net_option_cash import compute_net_option_cash  # noqa: E402
from analysis.rec_aging import (  # noqa: E402
    _is_earnings_imminent_close,
    apply_aging_to_action_items,
)

import yield_formulas  # noqa: E402  (skills root put on sys.path by panels)

TODAY = "2026-08-24"


# ── Shared fixtures (the real 2026-08-24 numbers) ─────────────────────────

def _snapshot(quotes, chains=None, iv_ranks=None, earnings=None, config=None):
    return {
        "quotes": quotes,
        "chains": chains or {},
        "iv_ranks": iv_ranks or {},
        "earnings_calendar": earnings or {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_071_292, "cash": 75_007},
        "positions": [],
    }


def _render(reviews, snap):
    return "\n".join(render_action_list([], reviews, [], snapshot_data=snap,
                                        date_str=TODAY))


def _render_items(reviews, snap):
    return render_action_list([], reviews, [], snapshot_data=snap,
                              date_str=TODAY)


def _iren_review():
    """The real IREN $47P Dec 18 '26 on 2026-08-24: entry $18.64 → mid
    $13.07 (+29.9% of premium captured), assignment basis $28.36, earnings
    Aug 27 (3d), only roll path spans the print (earnings-blocked)."""
    return {
        "contract": "IREN_PUT_47_20261218",
        "underlying": "IREN", "type": "PUT", "qty": -1,
        "strike": 47.0, "expiration": "2026-12-18",
        "entry_price": 18.64, "current_mid": 13.07, "days_to_expiry": 116,
        "delta": -0.53,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "1× $40P Jan 15 '27",
             "instruction": {"sell_strike": 40.0,
                             "sell_expiration": "2027-01-15",
                             "sell_mid": 9.10, "sell_bid": 8.80,
                             "sell_ask": 9.40},
             "netDollars": -397.0, "dteExtension": 28},
        ],
    }


def _iren_snapshot(config=None):
    cfg = {"momentum_hold": {"enabled": True}}
    cfg.update(config or {})
    return _snapshot(
        quotes={"IREN": {"last": 40.06}},
        chains={"IREN_2026-12-18": {"puts": [
            {"strike": 47.0, "bid": 12.70, "ask": 13.45}]}},
        iv_ranks={"IREN": 94},
        earnings={"IREN": "2026-08-27"},  # 3d away — inside the ≤14d window
        config=cfg,
    )


def _soxx_review(sto_delta=-0.08, pos_delta=-0.6349):
    """The real SOXX $520P Sep 11 '26: spot $499.69 → $20 ITM, position
    delta -0.6349 (positions payload), diagonal to $420P Sep 18 at a
    −$2,790 net debit; the STO leg's chain delta is -0.08."""
    instr = {"sell_strike": 420.0, "sell_expiration": "2026-09-18",
             "sell_mid": 2.50, "sell_bid": 1.80, "sell_ask": 3.20}
    if sto_delta is not None:
        instr["sell_delta"] = sto_delta
    rev = {
        "contract": "SOXX_PUT_520_20260911",
        "underlying": "SOXX", "type": "PUT", "qty": -1,
        "strike": 520.0, "expiration": "2026-09-11",
        "entry_price": 15.12, "current_mid": 29.70, "days_to_expiry": 18,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "1× $420P Sep 18 '26",
             "instruction": instr,
             "netDollars": -2790.0, "dteExtension": 7},
        ],
    }
    if pos_delta is not None:
        rev["delta"] = pos_delta
    return rev


def _soxx_snapshot():
    return _snapshot(
        quotes={"SOXX": {"last": 499.69}},
        chains={"SOXX_2026-09-11": {"puts": [
            {"strike": 520.0, "bid": 28.0, "ask": 31.4}]}},
        iv_ranks={"SOXX": 42},
    )


def _nvda_close_review():
    """The real NVDA $260C Nov 20 '26 (7 contracts): +45% captured
    ($+1,890 at these mids), 88 DTE, NVDA prints 2026-08-26 — 2 days out."""
    return {
        "contract": "NVDA_CALL_260_20261120",
        "underlying": "NVDA", "type": "CALL", "qty": -7,
        "strike": 260.0, "expiration": "2026-11-20",
        "entry_price": 6.00, "current_mid": 3.30, "days_to_expiry": 88,
        "recommendation": "CLOSE_FOR_PROFIT",
        "matrix_cell_id": "CALL_NORMAL_DEEP_OTM_TAKEPROFIT",
    }


def _nvda_snapshot():
    return _snapshot(
        quotes={"NVDA": {"last": 208.79}},
        earnings={"NVDA": "2026-08-26"},  # prints in 2d
    )


# ═══════════════════════════════════════════════════════════════════════════
# BUG A — willing-owner fortress outranks CLOSE INTO RECOVERY (rule #51)
# ═══════════════════════════════════════════════════════════════════════════

def test_iren_cushion_fortress_outranks_close_into_recovery():
    """Observed action #8: '**CLOSE INTO RECOVERY** IREN_PUT_47_20261218 —
    recovered to ~breakeven (+29.9% of premium) before the Aug 27 print'
    while assignment basis $28.36 vs spot $40.06 = 29.2% cushion ≥ the 25%
    willing-owner threshold. Three days earlier (08-21) the same position
    correctly rendered 'HOLD THROUGH EARNINGS — willing owner' at 31%
    cushion. Rule #51: a ≥25% basis cushion holds THROUGH the print —
    the fortress outranks the recovery close."""
    md = _render([_iren_review()], _iren_snapshot())
    assert "**HOLD THROUGH EARNINGS — willing owner** IREN_PUT_47" in md
    assert "CLOSE INTO RECOVERY" not in md
    assert "basis $28.36 is 29% below spot $40.06" in md
    assert "place NO GTC through the print" in md
    # the close-the-binary case renders only as subordinate context
    assert "Counter-case (subordinate):" in md
    assert "+29.9% of premium" in md


def test_iren_below_threshold_recovery_close_fires_as_today():
    """Cushion below the willing-owner threshold → CLOSE INTO RECOVERY
    exactly as today (same fixture, threshold raised above the measured
    29.2% cushion)."""
    md = _render([_iren_review()],
                 _iren_snapshot(config={"momentum_hold": {
                     "enabled": True,
                     "earnings_hold_min_cushion_pct": 35}}))
    assert "**CLOSE INTO RECOVERY** IREN_PUT_47" in md
    assert "HOLD THROUGH EARNINGS" not in md


def test_recovery_close_states_measured_capture_not_breakeven():
    """Observed: 'recovered to ~breakeven (+29.9% of premium)' — +29.9%
    captured is NOT breakeven. The template must state the measured
    capture, with the profit dollars in the standard parseable form so the
    Capital Plan and Money Plan derive the SAME number (rule #19/#14)."""
    md = _render([_iren_review()],
                 _iren_snapshot(config={"momentum_hold": {
                     "enabled": True,
                     "earnings_hold_min_cushion_pct": 35}}))
    assert "recovered to +29.9% of premium ($+557)" in md
    assert "~breakeven" not in md


def test_recovery_close_small_loss_keeps_breakeven_wording():
    """A genuine near-flat recovery (small residual LOSS within the -5%
    band — the original 2026-07-30 LITE case) keeps the honest '~breakeven'
    wording, with the measured numbers shown."""
    rev = dict(_iren_review(), entry_price=13.00, current_mid=13.20)
    md = _render([rev],
                 _iren_snapshot(config={"momentum_hold": {"enabled": False}}))
    assert "**CLOSE INTO RECOVERY** IREN_PUT_47" in md
    assert "recovered to ~breakeven (-1.5% of premium, $-20)" in md
    assert "exiting flat removes the binary" in md


def test_fortress_hold_is_never_banked():
    """Observed one-voice split: the Money Plan banked IREN at ~$556 inside
    'Bank today: 6 close(s) → $+6,706 realized' while the Capital Plan row
    said 'CLOSE IREN — locks $+0'. After the precedence fix IREN is a HOLD
    THROUGH EARNINGS and must not be banked at all (the hold-never-banked
    invariant): the shared net-option-cash module sees a HOLD-class block,
    never a buyback."""
    rev = _iren_review()
    items = _render_items([rev], _iren_snapshot())
    text = "\n".join(items)
    assert "**HOLD THROUGH EARNINGS — willing owner** IREN_PUT_47" in text
    noc = compute_net_option_cash(items, options_reviews=[rev])
    assert noc["total_buybacks"] == 0.0
    assert all("IREN" not in c["label"] for c in noc["components"]
               if c["group"] == "buybacks")


def test_recovery_close_is_banked_at_measured_capture():
    """When the recovery close DOES fire (cushion below threshold), the
    rendered card carries the measured '$+557' and the net-option-cash
    module prices the buyback from the same review mids — one voice."""
    rev = _iren_review()
    items = _render_items(
        [rev], _iren_snapshot(config={"momentum_hold": {
            "enabled": True, "earnings_hold_min_cushion_pct": 35}}))
    text = "\n".join(items)
    assert "($+557)" in text
    noc = compute_net_option_cash(items, options_reviews=[rev])
    # BTC at mid $13.07 × 100 × 1
    assert noc["total_buybacks"] == -1307.0


# ═══════════════════════════════════════════════════════════════════════════
# BUG B — position delta labeled as position; STO-leg δ on the order line
# ═══════════════════════════════════════════════════════════════════════════

def test_soxx_roll_card_shows_position_delta_not_sto_delta():
    """Observed action #3 (SOXX $20 ITM, spot $499.69 vs $520 strike):
    'Delta -0.08 (~8% ITM probability — OTM)' — the NEW STO leg's delta
    ($420P) mislabeled as the position's ITM probability. The delta line
    must carry the CURRENT position's measured delta (-0.63, ITM) with
    the position label; the new leg's δ belongs on the order line."""
    md = _render([_soxx_review()], _soxx_snapshot())
    assert "**EXECUTE ROLL** SOXX_PUT_520_20260911" in md
    assert "Position delta -0.63 (~63% ITM probability — ITM)" in md
    assert "Delta -0.08 (~8% ITM probability — OTM)" not in md
    # the STO leg's measured δ renders on the order line, labeled as the leg
    assert "ask $3.20; δ -0.08)." in md


def test_roll_card_missing_deltas_fail_closed():
    """No measured position delta → the position-delta line is omitted
    (never fabricated); no chain δ on the STO leg → 'δ n/a' on the order
    line (rule #19 — never the moneyness heuristic on an order ticket)."""
    rev = _soxx_review(sto_delta=None, pos_delta=None)
    md = _render([rev], _soxx_snapshot())
    assert "**EXECUTE ROLL** SOXX_PUT_520_20260911" in md
    assert "ITM probability" not in md
    assert "δ n/a)." in md


# ═══════════════════════════════════════════════════════════════════════════
# BUG C — debit rolls render 'net cost $X = Y% of new collateral'
# ═══════════════════════════════════════════════════════════════════════════

def test_debit_roll_yield_line_renders_net_cost_not_annualized():
    """Observed: 'net-cash -291.1% ann. on position over the +7d extension'
    (SOXX, a one-time $2,790 debit annualized over a 7-day window — an
    absurd number that looks like data). Debits render the plain measured
    form: 'net cost: $2,790 = 6.6% of new collateral'."""
    y = yield_formulas.compute_roll_yield(
        new_premium=2.50, new_strike=420.0, new_dte=25, contracts=1,
        spot=499.69, net_credit_dollars=-2790.0, position_value=49_969.0,
        old_strike=520.0, option_type="PUT", extension_days=7)
    line = yield_formulas.format_yield_line(y)
    assert "net cost: $2,790 = 6.6% of new collateral" in line
    assert "ann. on position" not in line
    assert "-291" not in line


def test_credit_roll_yield_line_keeps_annualized_form():
    """Credits keep the existing annualized form (the NOK $11P calendar:
    'net-cash +12.5% ann. on position over the +28d extension')."""
    y = yield_formulas.compute_roll_yield(
        new_premium=2.17, new_strike=11.0, new_dte=144, contracts=10,
        spot=9.89, net_credit_dollars=95.0, position_value=9_890.0,
        old_strike=11.0, option_type="PUT", extension_days=28)
    line = yield_formulas.format_yield_line(y)
    assert "net-cash" in line and "ann. on position" in line
    assert "over the +28d extension" in line
    assert "net cost" not in line


def test_soxx_rendered_card_carries_net_cost_form():
    """End-to-end: the rendered SOXX roll card's Yield line carries the
    net-cost form, never the annualized-debit form."""
    md = _render([_soxx_review()], _soxx_snapshot())
    assert "net cost: $2,790 = 6.6% of new collateral" in md
    assert "net-cash -" not in md


# ═══════════════════════════════════════════════════════════════════════════
# BUG D — earnings-imminent short-CALL close: rationale + promotion
# ═══════════════════════════════════════════════════════════════════════════

def test_nvda_call_close_carries_earnings_imminent_rationale():
    """Observed appendix #6: the NVDA $260C close said only '45% of max
    profit already captured with 88d still on the contract' — NO mention
    that NVDA prints in 2 days, the actual urgency (Fable correctly called
    it time-critical: 'NVDA prints Wednesday... This is time-sensitive —
    earnings in two days')."""
    md = _render([_nvda_close_review()], _nvda_snapshot())
    assert "**CLOSE** NVDA_CALL_260_20261120" in md
    assert ("**Earnings-imminent:** NVDA prints in 2d — closing before "
            "the print locks the gain and removes the call-away binary."
            ) in md


def test_call_close_no_imminent_print_keeps_plain_rationale():
    """Earnings far out (or unknown) → the close-at-50% rationale renders
    unchanged, with no fabricated earnings line."""
    snap = _snapshot(quotes={"NVDA": {"last": 208.79}},
                     earnings={"NVDA": "2026-11-25"})  # 93d out
    md = _render([_nvda_close_review()], snap)
    assert "**CLOSE** NVDA_CALL_260_20261120" in md
    assert "Earnings-imminent:" not in md


def _close_block(i, ident, strike_note, extra=None):
    blk = [
        f"{i}. **CLOSE** {ident} — +40% ($+900); buy-to-close limit $5.00",
        "   - **Why:** 40% of max profit already captured with 30d still "
        "on the contract.",
    ]
    if extra:
        blk.append(extra)
    return blk


def test_earnings_imminent_close_promotes_above_non_urgent_and_headline():
    """Observed ranking: the NVDA earnings-in-2d close sat in the APPENDIX
    (item #6) below non-urgent closes (TSLA #5, SMH #2) under the 5-item
    headline cap. Earnings proximity is a promotion key for closes: the
    imminent close ranks ABOVE non-urgent take-profits and never lands in
    the appendix."""
    items = []
    n = 1
    # Five bigger non-urgent closes (obligation × urgency would outrank NVDA)
    for ident in ("MELI_PUT_1460_20261218", "SMH_CALL_587.5_20261016",
                  "TSLA_CALL_400_20260918", "ZS_CALL_195_20261002",
                  "QQQ_PUT_600_20261120"):
        items.extend(_close_block(n, ident, ""))
        n += 1
    items.extend([
        f"{n}. **CLOSE** NVDA_CALL_260_20261120 — +45% ($+1,925); "
        f"buy-to-close limit $3.46",
        "   - **Why:** 45% of max profit already captured with 88d still "
        "on the contract.",
        "   - **Earnings-imminent:** NVDA prints in 2d — closing before "
        "the print locks the gain and removes the call-away binary.",
    ])
    aging_info = {"today": TODAY, "state": {}, "reconciliation": {}}
    out = apply_aging_to_action_items(items, aging_info)
    text = "\n".join(out)
    first = next(l for l in out if l.strip().startswith("1."))
    assert "NVDA_CALL_260_20261120" in first, (
        f"the earnings-imminent close must rank above non-urgent "
        f"take-profits; got:\n{text}")
    # never buried under the headline cap
    appendix = text.split("### Appendix", 1)[1] if "### Appendix" in text else ""
    assert "NVDA_CALL_260_20261120" not in appendix


def test_earnings_imminent_close_detector_shapes():
    """The detector keys on measured 'earnings/prints in Nd' text on
    CLOSE-family blocks only; affirmative clearance ('outside contract
    life') and far-out prints never promote."""
    assert _is_earnings_imminent_close(
        "**Earnings-imminent:** NVDA prints in 2d — closing before the "
        "print locks the gain", "CLOSE")
    # far-out print → no promotion
    assert not _is_earnings_imminent_close(
        "Earnings check: ✅ next earnings 80d away", "CLOSE")
    # clearance form: print lands after expiry → no binary, no promotion
    assert not _is_earnings_imminent_close(
        "Earnings check: ✅ next earnings 4d away (outside contract life).",
        "CLOSE")
    # rolls are not closes — the existing urgency tiers already cover them
    assert not _is_earnings_imminent_close(
        "Earnings in 2d — binary gap risk", "EXECUTE_ROLL")
