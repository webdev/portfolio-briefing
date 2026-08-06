"""Exit-cost anatomy tests (task #35, 2026-07-29).

The user's symptom, verbatim: on 2026-07-29 (market down hard) he almost paid
a $15,570 ask to close a LITE $700P — $10,081 of which was intrinsic, ~$5,200
panic-IV extrinsic, $550 spread. The right move was a roll-down-and-out
(IV-neutral). These tests pin the module that turns that manual analysis into
pipeline logic: analysis/exit_cost.py + the render integration in
render/panels.py and steps/per_option_commentary.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.exit_cost import (  # noqa: E402
    analyze_exit_cost,
    build_fable_context,
    chain_quote_for_position,
    classify_earnings_state,
    format_anatomy_lines,
    format_basis_line,
    spread_guidance,
)
from render.panels import render_action_list  # noqa: E402


TODAY = "2026-07-29"


def _pos(contract="LITE_PUT_700_20260918", opt_type="PUT", qty=-1.0,
         strike=700.0, entry=106.98, dte=51, mid=152.95,
         expiration="2026-09-18", underlying=None):
    return {
        "contract": contract,
        "underlying": underlying or contract.split("_")[0],
        "type": opt_type,
        "qty": qty,
        "strike": strike,
        "entry_price": entry,
        "current_mid": mid,
        "days_to_expiry": dte,
        "expiration": expiration,
    }


# ── Core verdict shapes (real 2026-07-29 numbers) ─────────────────────────


def test_lite_shape_roll_dont_close():
    """LITE $700P: spot 599.19, bid 150.20 / ask 155.70, premium 106.98,
    IV rank 80, DTE 51 → ROLL_DONT_CLOSE. Closing pays ~$5,214 of panic
    extrinsic; the roll swaps inflated premium for inflated premium."""
    a = analyze_exit_cost(
        _pos(), {"bid": 150.20, "ask": 155.70}, spot=599.19,
        iv_rank=80, today=TODAY,
    )
    assert a is not None
    assert a.verdict == "ROLL_DONT_CLOSE"
    assert abs(a.intrinsic_per_share - 100.81) < 0.01
    assert abs(a.extrinsic_per_share - 52.14) < 0.01
    assert abs(a.extrinsic_total - 5214.0) < 1.0
    assert abs(a.intrinsic_total - 10081.0) < 1.0
    assert a.assignment_basis is not None
    assert abs(a.assignment_basis - 593.02) < 0.01
    assert a.basis_vs_spot_pct is not None and a.basis_vs_spot_pct < 0
    assert a.iv_context == "elevated"
    # Reason quantifies the panic premium being paid away
    assert "5,214" in a.verdict_reason


def test_meta_shape_close_after_crush():
    """Earnings printed (day after the report — the print has definitely
    happened) + extrinsic still > 20% of mid → CLOSE_AFTER_CRUSH (let the
    crush work, then work a limit at mid). Task #43: a SAME-DAY earnings
    date no longer counts as printed without session evidence, so the
    printed shape is pinned on delta == -1."""
    a = analyze_exit_cost(
        _pos(contract="META_PUT_580_20260814", strike=580.0, entry=11.09,
             dte=16, mid=25.875, expiration="2026-08-14"),
        {"bid": 24.25, "ask": 27.50}, spot=585.61,
        iv_rank=96.8, earnings_date="2026-07-28", today=TODAY,
    )
    assert a is not None
    assert a.earnings_state == "printed_today"
    assert a.verdict == "CLOSE_AFTER_CRUSH"
    assert a.intrinsic_per_share == 0.0  # spot above strike — OTM, all time value
    assert "limit at mid" in a.verdict_reason


def test_vrt_shape_close_clean():
    """VRT $290P: spot 223, mid ≈ 72 vs intrinsic 67 → extrinsic ≈ 7% of
    mid → CLOSE_CLEAN. Basis 248.51 is +11.4% ABOVE spot, so HOLD_FOR_BASIS
    must not fire even with concentration headroom."""
    a = analyze_exit_cost(
        _pos(contract="VRT_PUT_290_20260918", strike=290.0, entry=41.49,
             dte=51, mid=72.0),
        {"bid": 71.0, "ask": 73.0}, spot=223.0,
        iv_rank=86.9, today=TODAY, concentration_ok=True,
    )
    assert a is not None
    assert a.verdict == "CLOSE_CLEAN"
    assert abs(a.intrinsic_per_share - 67.0) < 0.01
    assert abs(a.extrinsic_per_share - 5.0) < 0.01
    assert a.assignment_basis is not None
    assert abs(a.assignment_basis - 248.51) < 0.01
    assert a.basis_vs_spot_pct is not None and a.basis_vs_spot_pct > 0.11
    assert "cheap exit" in a.verdict_reason


def test_mu_shape_hold_for_basis_or_neutral_per_concentration():
    """MU $950P: spot 739, mid 277, premium 213.75 → intrinsic 211,
    extrinsic 66 (24% — below the pumped threshold), basis 736.25 ≤ spot.
    concentration_ok=True → HOLD_FOR_BASIS; False/None → NEUTRAL."""
    kwargs = dict(spot=739.0, iv_rank=82.9, today=TODAY)
    pos = _pos(contract="MU_PUT_950_20261120", strike=950.0, entry=213.75,
               dte=114, mid=277.0, expiration="2026-11-20")
    quote = {"bid": 275.0, "ask": 279.0}

    a = analyze_exit_cost(pos, quote, concentration_ok=True, **kwargs)
    assert a is not None
    assert abs(a.intrinsic_per_share - 211.0) < 0.01
    assert abs(a.extrinsic_per_share - 66.0) < 0.01
    assert a.assignment_basis is not None
    assert abs(a.assignment_basis - 736.25) < 0.01
    assert a.verdict == "HOLD_FOR_BASIS"
    assert "below" in a.verdict_reason

    a2 = analyze_exit_cost(pos, quote, concentration_ok=False, **kwargs)
    assert a2 is not None and a2.verdict == "NEUTRAL"
    a3 = analyze_exit_cost(pos, quote, concentration_ok=None, **kwargs)
    assert a3 is not None and a3.verdict == "NEUTRAL"
    # Basis facts still rendered on the NEUTRAL anatomy (measured, not advice)
    md = "\n".join(format_anatomy_lines(a2))
    assert "736.25" in md


def test_loss_stop_never_bare_hold():
    """GUARDRAIL_LOSS_STOP fired + HOLD_FOR_BASIS conditions → the verdict
    must NOT be a bare hold. Timing/pricing guidance is fine; undoing the
    guardrail is not."""
    a = analyze_exit_cost(
        _pos(contract="MU_PUT_950_20261120", strike=950.0, entry=213.75,
             dte=114, mid=277.0, expiration="2026-11-20"),
        {"bid": 275.0, "ask": 279.0}, spot=739.0,
        iv_rank=82.9, today=TODAY,
        loss_stop_fired=True, concentration_ok=True,
    )
    assert a is not None
    assert a.verdict != "HOLD_FOR_BASIS"
    assert "loss stop" in a.verdict_reason


def test_close_urgent_before_binary():
    """Earnings in 2d + underwater + extrinsic < 30% → CLOSE_URGENT."""
    a = analyze_exit_cost(
        _pos(strike=700.0, entry=106.98, dte=10, mid=110.0),
        {"bid": 109.0, "ask": 111.0}, spot=610.0,
        iv_rank=50, earnings_date="2026-07-31", today=TODAY,
    )
    assert a is not None
    assert a.earnings_state == "pre_print_2d"
    assert a.verdict == "CLOSE_URGENT"
    assert "before the binary" in a.verdict_reason


def test_roll_needs_dte_runway():
    """Pumped extrinsic + elevated IV but DTE < min_dte_for_roll (21) →
    ROLL_DONT_CLOSE must not fire (no runway for the roll to work)."""
    a = analyze_exit_cost(
        _pos(dte=10, mid=152.95), {"bid": 150.20, "ask": 155.70},
        spot=599.19, iv_rank=80, today=TODAY,
    )
    assert a is not None
    assert a.verdict != "ROLL_DONT_CLOSE"


def test_roll_fires_on_missing_iv_rank_via_extrinsic_alone():
    """iv_rank missing → the extrinsic test alone drives ROLL_DONT_CLOSE
    (per spec), and the reason must not fabricate a rank."""
    a = analyze_exit_cost(
        _pos(), {"bid": 150.20, "ask": 155.70}, spot=599.19,
        iv_rank=None, today=TODAY,
    )
    assert a is not None
    assert a.verdict == "ROLL_DONT_CLOSE"
    assert "IV rank" not in a.verdict_reason  # no fabricated rank
    assert a.iv_context is None


# ── Spread guidance ────────────────────────────────────────────────────────


def test_wide_spread_guidance():
    """Spread > $2.00/share (LITE: $5.50) → 'work a GTC limit at mid,
    don't pay the ask' guidance, quoting the measured mid."""
    a = analyze_exit_cost(
        _pos(), {"bid": 150.20, "ask": 155.70}, spot=599.19,
        iv_rank=80, today=TODAY,
    )
    g = spread_guidance(a)
    assert g is not None
    assert "work a GTC limit at mid" in g
    assert "152.95" in g
    assert "don't pay the ask" in g
    md = "\n".join(format_anatomy_lines(a))
    assert "don't pay the ask" in md
    assert "spread $5.50 wide" in md


def test_tight_spread_no_guidance():
    a = analyze_exit_cost(
        _pos(strike=290.0, entry=41.49, mid=72.0),
        {"bid": 71.5, "ask": 72.5}, spot=223.0, today=TODAY,
    )
    assert spread_guidance(a) is None


# ── Fail-closed on missing chain (CLAUDE.md #10/#19) ──────────────────────


def test_missing_chain_fail_closed():
    """No quote → None; zero/one-sided quotes → None. Never approximate."""
    assert analyze_exit_cost(_pos(), None, spot=599.19) is None
    assert analyze_exit_cost(_pos(), {}, spot=599.19) is None
    assert analyze_exit_cost(_pos(), {"bid": 0, "ask": 155.7}, spot=599.19) is None
    assert analyze_exit_cost(_pos(), {"bid": 150.2, "ask": 0}, spot=599.19) is None
    # chain lookup: leg present but quote unusable → None
    chains = {"LITE_2026-09-18": {"puts": [{"strike": 700.0, "bid": 0, "ask": 0}]}}
    assert chain_quote_for_position(_pos(), chains) is None
    # chain lookup: leg present with real bid/ask → quote
    chains = {"LITE_2026-09-18": {"puts": [{"strike": 700.0, "bid": 150.2, "ask": 155.7}]}}
    q = chain_quote_for_position(_pos(), chains)
    assert q is not None and abs(q["mid"] - 152.95) < 0.01


def test_long_positions_and_zero_qty_skipped():
    assert analyze_exit_cost(_pos(qty=1.0), {"bid": 1, "ask": 2}, spot=599.19) is None
    assert analyze_exit_cost(_pos(qty=0.0), {"bid": 1, "ask": 2}, spot=599.19) is None


def test_call_positions_skipped_or_call_math():
    """Short calls use call intrinsic math: max(spot − strike, 0)."""
    a = analyze_exit_cost(
        _pos(contract="SMH_CALL_240_20260918", opt_type="CALL", strike=240.0,
             entry=10.0, mid=15.0),
        {"bid": 14.5, "ask": 15.5}, spot=250.0, today=TODAY,
    )
    assert a is not None
    assert abs(a.intrinsic_per_share - 10.0) < 0.01
    assert abs(a.extrinsic_per_share - 5.0) < 0.01
    # Calls never get a put-style assignment basis
    assert a.assignment_basis is None
    # OTM call → zero intrinsic
    a2 = analyze_exit_cost(
        _pos(contract="SMH_CALL_240_20260918", opt_type="CALL", strike=240.0,
             entry=10.0, mid=5.0),
        {"bid": 4.5, "ask": 5.5}, spot=230.0, today=TODAY,
    )
    assert a2 is not None and a2.intrinsic_per_share == 0.0


# ── Earnings-state classifier ─────────────────────────────────────────────


def test_earnings_state_bands():
    # Task #43: same-day = IMMINENT (pre_print_0d), not printed — without
    # affirmative session evidence. Day-after (delta -1) = printed_today.
    assert classify_earnings_state("2026-07-29", TODAY) == "pre_print_0d"
    assert classify_earnings_state("2026-07-28", TODAY) == "printed_today"
    assert classify_earnings_state("2026-07-27", TODAY) == "printed_recent"
    assert classify_earnings_state("2026-07-31", TODAY) == "pre_print_2d"
    assert classify_earnings_state("2026-08-20", TODAY) is None
    assert classify_earnings_state(None, TODAY) is None
    assert classify_earnings_state("garbage", TODAY) is None


# ── Basis line for the ROLL ANALYSIS header ───────────────────────────────


def test_format_basis_line():
    line = format_basis_line(700.0, 106.98, 599.19)
    assert line is not None
    assert "593.02" in line and "599.19" in line
    assert "below market" in line
    # Missing inputs → None, never a fabricated line
    assert format_basis_line(0, 106.98, 599.19) is None
    assert format_basis_line(700.0, None, 599.19) is None


# ── Render integration (render_action_list) ───────────────────────────────


def _snap(chains=None, iv_ranks=None, earnings=None):
    return {
        "quotes": {"LITE": {"last": 599.19}},
        "chains": chains or {},
        "iv_ranks": iv_ranks or {"LITE": 80},
        "earnings_calendar": earnings or {},
        "technicals": {},
        "_config": {"core_positions": [], "accounts": []},
        "balance": {"accountValue": 2_000_000, "cash": 200_000},
        "positions": [],
    }


def _lite_close_review():
    """LITE $700P with a loss-stop CLOSE — the shape the user faced tonight."""
    rev = _pos()
    rev.update({
        "recommendation": "CLOSE",
        "matrix_cell_id": "GUARDRAIL_LOSS_STOP",
        "rationale": "Loss stop triggered: loss ratio 1.43x >= 1.4x",
    })
    return rev


_LITE_CHAINS = {
    "LITE_2026-09-18": {
        "puts": [{"strike": 700.0, "bid": 150.20, "ask": 155.70}],
        "calls": [],
    }
}


def test_render_anatomy_on_itm_put_close_ticket():
    """The CLOSE order ticket on an ITM short put carries the anatomy block
    and the ROLL-don't-close verdict — the pipeline version of the manual
    analysis that stopped the $15,570 ask."""
    md = "\n".join(render_action_list(
        [], [_lite_close_review()], [],
        snapshot_data=_snap(chains=_LITE_CHAINS), date_str=TODAY,
    ))
    assert "Exit cost anatomy" in md
    assert "intrinsic" in md and "extrinsic" in md
    assert "ROLL, don't close" in md
    assert "don't pay the ask" in md          # wide-spread guidance
    assert "593.02" in md                      # assignment basis, measured


def test_render_anatomy_absent_on_otm_winner():
    """An OTM winner (capture ≥ 30%, spot > 3% above strike) gets a CLOSE
    ticket without the full anatomy decomposition — but since the 2026-08-05
    defect-2 fix it must SAY the buyback is pure time value rather than
    render silent (the VRT $280P/$270P cards shipped with no exit-cost read
    at all)."""
    rev = _pos(entry=20.0, mid=8.0)  # +60% captured, spot 599 < strike... make OTM
    rev.update({
        "strike": 500.0,   # spot 599.19 > 500 → genuinely OTM put (>3%)
        "contract": "LITE_PUT_500_20260918",
        "recommendation": "HOLD",
    })
    md = "\n".join(render_action_list(
        [], [rev], [], snapshot_data=_snap(chains=_LITE_CHAINS), date_str=TODAY,
    ))
    assert "CLOSE" in md and "LITE_PUT_500" in md
    # No intrinsic/extrinsic decomposition (nothing to decompose)…
    assert "BTC mid" not in md
    # …but never silent: the one-line OTM read is mandatory.
    assert "the buyback is pure time value" in md


def test_render_missing_chain_says_verify_at_broker():
    """ITM put CLOSE ticket with NO chain for the held contract → the
    anatomy fails closed with a 'verify ... at the broker' note, never
    fabricated numbers."""
    md = "\n".join(render_action_list(
        [], [_lite_close_review()], [],
        snapshot_data=_snap(chains={}), date_str=TODAY,
    ))
    assert "chain unavailable" in md
    assert "verify exit cost" in md
    assert "ROLL, don't close" not in md


def test_render_anatomy_disabled_by_config():
    snap = _snap(chains=_LITE_CHAINS)
    snap["_config"]["exit_cost"] = {"enabled": False}
    md = "\n".join(render_action_list(
        [], [_lite_close_review()], [], snapshot_data=snap, date_str=TODAY,
    ))
    assert "Exit cost anatomy" not in md


# ── Watch panel: assignment basis in the ROLL ANALYSIS header ─────────────


def test_watch_roll_analysis_gets_basis_line():
    from steps.per_option_commentary import render_watch_with_commentary
    rev = _pos()
    rev.update({
        "recommendation": "ROLL_OUT",
        "matrix_cell_id": "PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "netDollars": 0, "notes": ""},
            {"id": "B", "description": "same-strike roll out",
             "netDollars": 900, "notes": "+35d"},
        ],
    })
    lines = render_watch_with_commentary([], [rev], _snap(chains=_LITE_CHAINS))
    md = "\n".join(lines)
    assert "ROLL ANALYSIS" in md
    assert "Assignment basis" in md
    assert "593.02" in md


def test_watch_roll_analysis_no_basis_line_for_otm_put():
    from steps.per_option_commentary import render_watch_with_commentary
    rev = _pos(strike=500.0, contract="LITE_PUT_500_20260918", entry=20.0, mid=8.0)
    rev.update({
        "recommendation": "HOLD",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "netDollars": 0, "notes": ""},
        ],
    })
    md = "\n".join(render_watch_with_commentary([], [rev], _snap()))
    assert "ROLL ANALYSIS" in md
    assert "Assignment basis" not in md


# ── Fable position context ────────────────────────────────────────────────


def test_build_fable_context_summarizes_itm_puts():
    ctx = build_fable_context(
        [_lite_close_review()], _snap(chains=_LITE_CHAINS), config={},
    )
    assert "LITE_PUT_700_20260918" in ctx
    assert "ROLL_DONT_CLOSE" in ctx
    assert "intrinsic" in ctx


def test_build_fable_context_empty_without_chains():
    ctx = build_fable_context([_lite_close_review()], _snap(chains={}), config={})
    assert ctx == ""


def test_fable_user_message_carries_position_context():
    from analysis.fable_advisor import _build_user_message
    msg = _build_user_message("briefing body", "", [], "- LITE anatomy line")
    assert "<position-context>" in msg
    assert "LITE anatomy line" in msg
    # Empty context → section omitted entirely
    msg2 = _build_user_message("briefing body", "", [], None)
    assert "<position-context>" not in msg2
