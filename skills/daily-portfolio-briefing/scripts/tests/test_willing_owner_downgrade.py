"""Willing-owner roll downgrade (rule #51 extension) — the NOK case.

George (2026-08-18), on the NOK 10-lot card "🚨 URGENT — EXECUTE ROLL
NOK_PUT_11_20261218 — Calendar roll... +$115 net credit" with 122 DTE and
assignment basis $8.44 vs spot $10.29: "Why should I roll Nokia if it's in
December? It's kind of hard."

Agreed design: the strike-tested URGENT roll tier protects UNWILLING
owners; for a WILLING owner (deep basis cushion, no near event, long DTE)
a same-strike credit roll is an optional income optimization, not urgent.
The headline downgrades to "🔧 OPTIONAL — credit extension (no action
required)" with the honest measured framing; the FULL combo ticket stays
on the card (rule #24) for the days he feels like taking the $115.
Risk-driven rolls (loss stops, crash cells, tail risk, earnings window)
are NEVER downgraded. Config off → byte-identical legacy.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.willing_owner import (  # noqa: E402
    OPTIONAL_PREFIX,
    assess_willing_owner_roll,
    format_willing_owner_line,
    wo_settings,
)
from render.panels import (  # noqa: E402
    render_action_list,
    sort_deferred_actions,
    sync_action_item_count,
)

TODAY = "2026-08-18"
NLV = 1_088_019.0

_WO_ENABLED = {"willing_owner_downgrade": {
    "enabled": True, "min_cushion_pct": 15, "min_dte": 60,
    "earnings_clear_days": 21}}


# ── Fixtures — the observed NOK 10-lot shape ──────────────────────────────

def _nok_review(**over):
    """NOK_PUT_11_20261218: strike 11, entry premium 2.56 → assignment
    basis $8.44; 122 DTE; strike-tested urgent (δ 0.48)."""
    rev = {
        "contract": "NOK_PUT_11_20261218",
        "underlying": "NOK", "type": "PUT", "qty": -10,
        "strike": 11.0, "expiration": "2026-12-18",
        "entry_price": 2.56, "current_mid": 2.35, "days_to_expiry": 122,
        "delta": -0.48,
        "recommendation": "ROLL_OUT",
        "matrix_cell_id": "GUARDRAIL_STRIKE_TESTED",
        "rationale": ("🚨 🎯 Strike tested (δ 0.48 ≥ 0.45) with 8% captured "
                      "and 122 DTE — credit-roll window open; roll while "
                      "extrinsic is at its peak."),
        "roll_candidates": [_cand()],
    }
    rev.update(over)
    return rev


def _cand(**over):
    c = {"id": "B", "description": "10× $11P Jan 15 '27",
         "instruction": {"sell_strike": 11.0,
                         "sell_expiration": "2027-01-15",
                         "sell_mid": 2.475, "sell_bid": 2.45,
                         "sell_ask": 2.50},
         "netDollars": 115.0, "dteExtension": 28}
    c.update(over)
    return c


def _snapshot(config_extra=None, earnings=None, spot=10.29):
    cfg = {"core_positions": [], "accounts": [],
           "roll": dict(config_extra or {})}
    return {
        "quotes": {"NOK": {"last": spot}},
        "chains": {},
        "iv_ranks": {"NOK": 55},
        "earnings_calendar": dict(earnings or {}),
        "_config": cfg,
        "balance": {"accountValue": NLV, "cash": 69_442},
        "positions": [],
        "_credit_windows": {"NOK_PUT_11_20261218": {
            "state": "open", "best_credit": 0.12,
            "best_candidate_desc": "10× $11P Jan 15 '27"}},
    }


def _render(reviews, snapshot):
    return "\n".join(render_action_list(
        [], reviews, [], analytics={"nlv": NLV},
        snapshot_data=snapshot, date_str=TODAY))


# ── (a) The NOK shape downgrades with measured framing + ticket kept ──────

def test_nok_card_downgrades_to_optional_with_measured_framing():
    """"Why should I roll Nokia if it's in December? It's kind of hard."
    — the NOK card (cushion (10.29 − 8.44)/10.29 = 18.0%, 122 DTE, no
    earnings, same-strike +$115 credit) downgrades from '🚨 URGENT —
    EXECUTE ROLL' to '🔧 OPTIONAL — credit extension (no action
    required)' with the honest willing-owner framing — every number
    measured (rule #19)."""
    md = _render([_nok_review()], _snapshot(config_extra=_WO_ENABLED))
    assert (f"{OPTIONAL_PREFIX} NOK_PUT_11_20261218") in md
    assert "URGENT — EXECUTE ROLL" not in md
    opt_line = next(ln for ln in md.splitlines() if "OPTIONAL" in ln)
    assert "🚨" not in opt_line
    # Measured willing-owner framing
    assert "willing owner: basis $8.44 is 18% below spot $10.29" in md
    assert "assignment in Dec is a win" in md
    assert "Roll adds +$115/+28d if convenient" in md
    assert "park the GTC at 50% ($1.28) or let it run" in md


def test_nok_downgraded_card_keeps_the_full_combo_ticket():
    """The full combo ticket stays on the card (rule #24) for the days he
    feels like taking the $115 — Buy-to-Close and Sell-to-Open legs with
    real chain values, plus the credit-window context line."""
    md = _render([_nok_review()], _snapshot(config_extra=_WO_ENABLED))
    assert "**Order:** Combo (calendar/diagonal)" in md
    assert "Buy-to-Close 10×" in md and "NOK $11P" in md
    assert "Sell-to-Open 10× $11P" in md
    assert "+$115 net credit" in md
    # Interaction: the credit-window state line stays — the honest
    # "this offer expires" context.
    assert "**Credit window: 🟢 OPEN**" in md


# ── (b) Thin cushion / short DTE / near earnings stay URGENT ──────────────

def test_thin_cushion_stays_urgent():
    """Cushion below the floor is an UNWILLING-owner shape: spot $9.80 →
    (9.80 − 8.44)/9.80 = 13.9% < 15 — the card keeps '🚨 URGENT —
    EXECUTE ROLL', never OPTIONAL."""
    md = _render([_nok_review()],
                 _snapshot(config_extra=_WO_ENABLED, spot=9.80))
    assert "OPTIONAL" not in md
    assert "🚨 **URGENT — EXECUTE ROLL** NOK_PUT_11_20261218" in md


def test_short_dte_stays_urgent():
    """"if it's in December" was the whole point — with only 45 DTE the
    roll decision is genuinely near, so the urgency stays (min_dte 60)."""
    rev = _nok_review(days_to_expiry=45, expiration="2026-10-02",
                      roll_candidates=[_cand(
                          instruction={"sell_strike": 11.0,
                                       "sell_expiration": "2026-10-30",
                                       "sell_mid": 2.475, "sell_bid": 2.45,
                                       "sell_ask": 2.50})])
    md = _render([rev], _snapshot(config_extra=_WO_ENABLED))
    assert "OPTIONAL" not in md
    assert "🚨 **URGENT — EXECUTE ROLL** NOK_PUT_11_20261218" in md


def test_near_earnings_never_downgrades():
    """Earnings inside earnings_clear_days (21) is a near event — the
    willing-owner downgrade must not fire (the card runs the existing
    earnings machinery instead; whatever renders, it is never OPTIONAL)."""
    md = _render([_nok_review()],
                 _snapshot(config_extra=_WO_ENABLED,
                           earnings={"NOK": "2026-09-05"}))  # 18d away
    assert "OPTIONAL" not in md
    assert "URGENT" in md


def test_near_earnings_unit_gate():
    """Unit pin: a measured earnings date inside the clear window returns
    None even with the full NOK cushion/DTE."""
    assert assess_willing_owner_roll(
        option_type="PUT", strike=11.0, entry_premium=2.56, spot=10.29,
        dte=122, days_to_earnings=18,
        matrix_cell_id="GUARDRAIL_STRIKE_TESTED",
        credit_dollars=115.0, new_strike=11.0,
        config={"roll": _WO_ENABLED}) is None


# ── (c) Defensive / loss-stop rolls NEVER downgrade ───────────────────────

def test_risk_driven_cells_never_downgrade():
    """Risk-driven rolls (loss stops, crash cells, tail-risk,
    earnings-window, delta-expansion DEFENSIVE ROLL on unwilling
    positions) are NEVER downgraded — even with the full NOK cushion."""
    for cell in ("GUARDRAIL_LOSS_STOP", "GUARDRAIL_CRASH_STOP",
                 "GUARDRAIL_TAIL_RISK", "GUARDRAIL_EARNINGS_IMMINENT",
                 "PUT_DEFENSIVE_ROLL"):
        assert assess_willing_owner_roll(
            option_type="PUT", strike=11.0, entry_premium=2.56, spot=10.29,
            dte=122, days_to_earnings=None, matrix_cell_id=cell,
            credit_dollars=115.0, new_strike=11.0,
            config={"roll": _WO_ENABLED}) is None, cell


def test_roll_up_and_debit_rolls_never_read_as_optional_income():
    """A roll-UP (deeper ITM) or a debit roll is not optional income —
    the debit-only window keeps the existing GTC/hold voice (exit-cost
    verdict / debit-cap machinery), never a '🔧 OPTIONAL' badge."""
    base = dict(option_type="PUT", strike=11.0, entry_premium=2.56,
                spot=10.29, dte=122, days_to_earnings=None,
                matrix_cell_id="GUARDRAIL_STRIKE_TESTED",
                config={"roll": _WO_ENABLED})
    assert assess_willing_owner_roll(
        credit_dollars=115.0, new_strike=12.0, **base) is None   # roll-up
    assert assess_willing_owner_roll(
        credit_dollars=-80.0, new_strike=11.0, **base) is None   # debit
    # roll-DOWN credit still qualifies (same-strike/down credit)
    assert assess_willing_owner_roll(
        credit_dollars=40.0, new_strike=10.0, **base) is not None


def test_calls_and_unmeasurable_inputs_never_downgrade():
    """Fail direction (rule #19): CALL side, missing spot / entry / DTE →
    None — a downgrade is never fabricated on missing data."""
    base = dict(strike=11.0, entry_premium=2.56, spot=10.29, dte=122,
                days_to_earnings=None,
                matrix_cell_id="GUARDRAIL_STRIKE_TESTED",
                credit_dollars=115.0, new_strike=11.0,
                config={"roll": _WO_ENABLED})
    assert assess_willing_owner_roll(option_type="CALL", **base) is None
    for k, v in (("spot", 0), ("entry_premium", None), ("dte", None)):
        kw = dict(base, option_type="PUT")
        kw[k] = v
        assert assess_willing_owner_roll(**kw) is None, k


def test_min_cushion_default_is_15_so_the_nok_card_actually_fires():
    """The NOK card measures (10.29 − 8.44)/10.29 = 18.0% — a 20 floor
    would have left the very card that prompted the rule urgent. Default
    min_cushion_pct is 15; a config floor of 20 keeps NOK urgent."""
    assert wo_settings({})["min_cushion_pct"] == 15.0
    p = assess_willing_owner_roll(
        option_type="PUT", strike=11.0, entry_premium=2.56, spot=10.29,
        dte=122, days_to_earnings=None,
        matrix_cell_id="GUARDRAIL_STRIKE_TESTED",
        credit_dollars=115.0, new_strike=11.0,
        config={"roll": {"willing_owner_downgrade": {"enabled": True}}})
    assert p is not None
    assert abs(p["cushion_pct"] - 17.98) < 0.05
    assert abs(p["basis"] - 8.44) < 1e-9
    assert abs(p["gtc_half_price"] - 1.28) < 1e-9
    assert assess_willing_owner_roll(
        option_type="PUT", strike=11.0, entry_premium=2.56, spot=10.29,
        dte=122, days_to_earnings=None,
        matrix_cell_id="GUARDRAIL_STRIKE_TESTED",
        credit_dollars=115.0, new_strike=11.0,
        config={"roll": {"willing_owner_downgrade": {
            "enabled": True, "min_cushion_pct": 20}}}) is None


def test_framing_line_measured_numbers_only():
    """rule #19 — the framing renders exactly the measured numbers, and
    omits the extension segment when no extension was measured."""
    p = {"basis": 8.44, "cushion_pct": 17.98, "spot": 10.29,
         "gtc_half_price": 1.28}
    line = format_willing_owner_line(p, 115.0, 28, exp_month="Dec")
    assert line == ("willing owner: basis $8.44 is 18% below spot $10.29; "
                    "assignment in Dec is a win. Roll adds +$115/+28d if "
                    "convenient; otherwise park the GTC at 50% ($1.28) or "
                    "let it run.")
    assert "/+" not in format_willing_owner_line(p, 115.0, None)
    assert "at expiry" in format_willing_owner_line(p, 115.0, 28)


# ── (d) Ordering + count — OPTIONAL sorts with the hold-class group ───────

_ACTION_MD_OPTIONAL = "\n".join([
    "# Daily Briefing — Tuesday, August 18, 2026",
    "",
    "**Action Items:** 3",
    "",
    "## Today's Action List — Tuesday, August 18, 2026",
    "",
    "1. 🔧 **OPTIONAL — credit extension (no action required)**"
    " NOK_PUT_11_20261218 — Calendar roll (same strike, longer date):"
    " +$115 net credit  · RSI 49 🟢 pullback",
    "   - **Why (optional):** willing owner: basis $8.44 is 18% below"
    " spot $10.29; assignment in Dec is a win.",
    "2. **CSP — PAID-TO-WAIT** VRT — sell $240P exp Fri Sep 18 '26"
    " (monthly) for $5.72 premium  · RSI 46 🟢 pullback",
    "   - **⏸ Deferred (capacity gated) — stress coverage 0.11× < 0.50×"
    " floor; shown for planning, not a green light (rule #41)**",
    "3. **CLOSE** RDDT_PUT_140_20260911 — +31% ($+151); buy-to-close"
    " limit $3.57 · RSI 44",
    "   - **Order:** $3.57 GTC · RSI 44",
    "",
    "### 📋 Total Impact (if all actions executed)",
    "",
    "- **Total actions:** 3",
])


def test_optional_sorts_after_executable_before_deferred():
    """OPTIONAL items sort with the hold-class group (after executable,
    before deferred): CLOSE first, 🔧 OPTIONAL second, deferred card
    third — renumbered."""
    out = sort_deferred_actions(_ACTION_MD_OPTIONAL)
    close = out.index("**CLOSE** RDDT_PUT_140_20260911")
    opt = out.index("**OPTIONAL — credit extension")
    vrt = out.index("**CSP — PAID-TO-WAIT** VRT")
    assert close < opt < vrt
    assert "1. **CLOSE** RDDT_PUT_140_20260911" in out
    assert ("2. 🔧 **OPTIONAL — credit extension (no action required)**"
            " NOK_PUT_11_20261218") in out
    assert "3. **CSP — PAID-TO-WAIT** VRT" in out


def test_optional_counts_under_the_hold_bucket():
    """The count line reads '1 (+1 hold, +1 deferred)' — the OPTIONAL
    no-action-required item counts under the existing hold bucket, never
    as an executable action."""
    out = sync_action_item_count(sort_deferred_actions(_ACTION_MD_OPTIONAL))
    assert "**Action Items:** 1 (+1 hold, +1 deferred)" in out


# ── (e) Config off = legacy byte-identical ────────────────────────────────

def test_config_off_is_legacy_byte_identical():
    """Code default OFF: with no willing_owner_downgrade block (or
    enabled: false) the NOK card renders the legacy '🚨 URGENT — EXECUTE
    ROLL' ticket, byte-identical between the two off-shapes."""
    md_absent = _render([_nok_review()], _snapshot())
    md_disabled = _render([_nok_review()], _snapshot(config_extra={
        "willing_owner_downgrade": {"enabled": False}}))
    assert md_absent == md_disabled
    assert "🚨 **URGENT — EXECUTE ROLL** NOK_PUT_11_20261218" in md_absent
    assert "OPTIONAL" not in md_absent
    assert "**Why (urgent):**" in md_absent
