"""Momentum-hold overlay (2026-08-17).

George: "I feel like we're trying to get out of our options a little too
early. It feels like IREN is ripping. SNDK was ripping... If something is
ripping and it didn't get out to RSI, say, to 70 or something. Do I really
need to close? Validate it, and let's make sure we squeeze as much as
possible out of these options."

Validated evidence: Friday's briefing said close SNDK at +33%; held through
the rip, Monday +62% (+$1,280 in 3 sessions).

These tests pin the whole contract: (a) the ride fires ONLY with all five
conditions (trend up / RSI below stall / OTM cushion / below the capture
ceiling / not risk-driven) — each single-miss case closes, the SNDK-Friday
fixture rides, the over-cap Monday fixture closes (risk always overrides
momentum); (b) the exit-trigger return path appends "(momentum stalled —
take it)" on red-day / RSI-stall fixtures; (c) the earnings basis-cushion
nuance flips CLOSE BEFORE EARNINGS to HOLD THROUGH EARNINGS — willing
owner at ≥25% cushion with measured basis math, below threshold closes as
today, one-voice verifier clean on both; (d) the Money Plan counts riders
under Blocked money; (e) config off is byte-identical legacy; (f)
unmeasurable momentum fails OPEN to no ride (never fabricated — rule #19).

Source: scripts/analysis/momentum_hold.py + render/panels.py (CLOSE WINNERS
block #2, matrix CLOSE_FOR_PROFIT block #4, the one-voice earnings branch)
+ render/money_plan.py + analysis/net_option_cash.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import momentum_hold as mh  # noqa: E402
from analysis.net_option_cash import demoted_close_idents  # noqa: E402
from render.money_plan import build_money_plan  # noqa: E402
from render.panels import (  # noqa: E402
    one_voice_violations,
    render_action_list,
    render_risk_alerts,
)

TODAY = "2026-08-17"

_MH_ON = {"momentum_hold": {"enabled": True}}
_BOTH_ON = {"momentum_hold": {"enabled": True},
            "redeploy_aware_tp": {"enabled": True, "hold_target_pct": 0.75}}

# The real SNDK close series from the 2026-08-17 technicals snapshot —
# 1271 → 1344 → 1528 → 1625 over the rip.
_SNDK_CLOSES = [1150.0, 1180.0, 1210.0, 1271.05, 1344.29, 1528.11, 1625.38]


def _snapshot(config=None, quotes=None, technicals=None, earnings=None,
              positions=None, balance=None):
    return {
        "quotes": quotes if quotes is not None else {
            "SNDK": {"last": 1650.0}},
        "chains": {}, "iv_ranks": {},
        "earnings_calendar": earnings or {},
        "technicals": technicals if technicals is not None else {
            "SNDK": {"spot": 1625.38, "rsi_14": 56.5,
                     "rsi_closes": list(_SNDK_CLOSES)}},
        "_config": {"core_positions": [], "accounts": [],
                    **(_MH_ON if config is None else config)},
        "balance": balance or {"accountValue": 5_000_000, "cash": 500_000},
        "positions": positions or [],
    }


def _sndk_friday(entry=43.50, mid=29.15, strike=1230.0, dte=32):
    """The SNDK-Friday shape: +33% captured on a ripping underlying —
    Friday's briefing said close; held through the rip, Monday +62%."""
    return {
        "contract": "SNDK_PUT_1230_20260918",
        "underlying": "SNDK", "type": "PUT", "qty": -1,
        "strike": strike, "expiration": "2026-09-18",
        "entry_price": entry, "current_mid": mid, "days_to_expiry": dte,
        "recommendation": "HOLD", "matrix_cell_id": "X",
        "roll_candidates": [],
    }


def _headlines(items):
    return [ln for ln in items if ln.lstrip()[:1].isdigit()]


def _render(rev, snap, analytics=None):
    return render_action_list([], [rev], [], analytics, snap,
                              date_str=TODAY)


# ═══════════════════════════════════════════════════════════════════════════
# (a) the ride fires only with ALL five conditions
# ═══════════════════════════════════════════════════════════════════════════

def test_sndk_friday_fixture_rides():
    """George: "SNDK was ripping... Do I really need to close?" The
    Friday shape — +33% captured, +1.5% today, +8% 3-session, RSI 56 < 65,
    25% OTM, no risk driver — defers to the visible 🏇 RIDE item with the
    measured exit trigger (rule #24: a HOLD-class action item, never
    hidden)."""
    rev = _sndk_friday()
    items = _render(rev, _snapshot())
    heads = _headlines(items)
    assert any("🏇 **RIDE — momentum hold** SNDK_PUT_1230" in h
               for h in heads), heads
    assert not any("**CLOSE** SNDK_PUT_1230" in h for h in heads)
    note = rev.get("_momentum_ride") or ""
    assert "SNDK +1.5% today" in note
    assert "RSI 56 rising" in note
    assert "25% OTM" in note
    assert "close on FIRST RED DAY or RSI ≥ 65 or 85% capture" in note
    # The would-have-closed capture is shown (one voice, measured).
    body = "\n".join(items)
    assert "Would have closed at +33%" in body
    assert one_voice_violations(items) == []


def test_red_day_single_miss_does_not_ride():
    """George's exit trigger is the FIRST RED DAY: same fixture but the
    live quote prints red (-1.6% today) — no ride; the close fires."""
    rev = _sndk_friday()
    snap = _snapshot(quotes={"SNDK": {"last": 1600.0}})
    items = _render(rev, snap)
    assert not rev.get("_momentum_ride")
    assert any("**CLOSE** SNDK_PUT_1230" in h for h in _headlines(items))


def test_rsi_66_single_miss_does_not_ride():
    """"If something is ripping and it didn't get out to RSI, say, to 70
    or something" — at RSI 66 ≥ the 65 stall the ride never fires."""
    rev = _sndk_friday()
    snap = _snapshot(technicals={"SNDK": {
        "spot": 1625.38, "rsi_14": 66.0, "rsi_closes": list(_SNDK_CLOSES)}})
    items = _render(rev, snap)
    assert not rev.get("_momentum_ride")
    assert any("**CLOSE** SNDK_PUT_1230" in h for h in _headlines(items))


def test_thin_otm_cushion_single_miss_does_not_ride():
    """8% OTM is under the 10% gamma-safety floor — momentum never rides a
    thin cushion; the close fires with no stall annotation."""
    rev = _sndk_friday(strike=1518.0)  # (1650 − 1518) / 1650 = 8.0% OTM
    rev["contract"] = "SNDK_PUT_1518_20260918"
    items = _render(rev, _snapshot())
    assert not rev.get("_momentum_ride")
    heads = _headlines(items)
    assert any("**CLOSE** SNDK_PUT_1518" in h for h in heads)
    assert "momentum stalled" not in "\n".join(items)


def test_86_capture_single_miss_closes():
    """At 86% captured — past the 85% hard ceiling — the winner ALWAYS
    closes; the remaining premium is not worth the gamma."""
    rev = _sndk_friday(mid=6.09)  # (43.50 − 6.09) / 43.50 = 86.0%
    items = _render(rev, _snapshot())
    assert not rev.get("_momentum_ride")
    assert any("**CLOSE** SNDK_PUT_1230" in h for h in _headlines(items))


def test_over_cap_monday_fixture_still_closes():
    """The over-cap Monday fixture: SNDK $1230P at +62% on a $1.12M NLV —
    $123,000 obligation = 11.0% of NLV over the 8% Tier C cap. The
    over-cap risk exemption (rule #43, 2026-08-17) ALWAYS overrides
    momentum: no ride, the CLOSE stays with the visible risk tag."""
    rev = _sndk_friday(mid=16.53)  # +62% captured, still under the ceiling
    snap = _snapshot(
        config=_BOTH_ON,
        balance={"accountValue": 1_120_428.0, "cash": 79_398.0},
        positions=[{"assetType": "OPTION",
                    "symbol": "SNDK_PUT_1230_20260918",
                    "underlying": "SNDK", "type": "PUT", "qty": -1,
                    "strike": 1230.0}])
    analytics = {"stress_coverage": {"coverage_ratio": 0.11,
                                     "cash": 79_398.0,
                                     "total_put_obligations": 752_700.0}}
    items = _render(rev, snap, analytics)
    assert not rev.get("_momentum_ride")
    heads = _headlines(items)
    assert any("**CLOSE** SNDK_PUT_1230" in h for h in heads)
    tag = "\n".join(ln for ln in items if "Risk-driven close" in ln)
    assert "risk: SNDK at 11.0% of NLV over the 8% Tier C cap" in tag


def test_call_side_unchanged():
    """SMH is a CALL — the overlay is put-only; covered-call closes render
    exactly as today, no ride, no stall text."""
    rev = {
        "contract": "SMH_CALL_710_20261120",
        "underlying": "SMH", "type": "CALL", "qty": -1,
        "strike": 710.0, "expiration": "2026-11-20",
        "entry_price": 20.25, "current_mid": 13.85, "days_to_expiry": 95,
        "recommendation": "HOLD", "matrix_cell_id": "X",
        "roll_candidates": [],
    }
    snap = _snapshot(
        quotes={"SMH": {"last": 596.72}},
        technicals={"SMH": {"spot": 584.25, "rsi_14": 53.0,
                            "rsi_closes": [572.93, 584.83, 589.12, 584.25]}})
    items = _render(rev, snap)
    assert not rev.get("_momentum_ride")
    assert any("**CLOSE** SMH_CALL_710" in h for h in _headlines(items))
    assert "momentum" not in "\n".join(items).lower()


# ═══════════════════════════════════════════════════════════════════════════
# (b) the exit-trigger return path — "(momentum stalled — take it)"
# ═══════════════════════════════════════════════════════════════════════════

def test_stalled_red_day_close_returns_with_take_it():
    """The stall trigger IS the exit: "next briefing where the underlying
    prints red ... the close recommendation returns with '(momentum
    stalled — take it)' appended." Red day after a 3-session run → the
    CLOSE headline carries the appended trigger."""
    rev = _sndk_friday()
    snap = _snapshot(quotes={"SNDK": {"last": 1600.0}})  # −1.6% today
    items = _render(rev, snap)
    head = next(h for h in _headlines(items)
                if "**CLOSE** SNDK_PUT_1230" in h)
    # The RSI-discipline annotate pass may append a chip after the trigger.
    assert "(momentum stalled — take it)" in head
    assert one_voice_violations(items) == []


def test_stalled_rsi_65_close_returns_with_take_it():
    """RSI reaching the stall (≥ 65) on a still-green 3-session run is the
    other measured exit — the returning close carries the trigger."""
    rev = _sndk_friday()
    snap = _snapshot(technicals={"SNDK": {
        "spot": 1625.38, "rsi_14": 67.0, "rsi_closes": list(_SNDK_CLOSES)}})
    items = _render(rev, snap)
    head = next(h for h in _headlines(items)
                if "**CLOSE** SNDK_PUT_1230" in h)
    assert "(momentum stalled — take it)" in head


def test_module_stall_reasons_are_measured():
    """momentum_ride names the measured stall: red day carries the day
    move, RSI stall carries the value vs threshold."""
    rev = _sndk_friday()
    snap = _snapshot(quotes={"SNDK": {"last": 1600.0}})
    ride, why = mh.momentum_ride(rev, snap, _MH_ON)
    assert ride is False and why.startswith("stalled — red day")
    assert "-1.6% today" in why
    snap2 = _snapshot(technicals={"SNDK": {
        "spot": 1625.38, "rsi_14": 67.0, "rsi_closes": list(_SNDK_CLOSES)}})
    ride2, why2 = mh.momentum_ride(rev, snap2, _MH_ON)
    assert ride2 is False and why2 == "stalled — RSI 67 ≥ 65 stall"


# ═══════════════════════════════════════════════════════════════════════════
# (c) earnings basis-cushion — the IREN willing-owner case
# ═══════════════════════════════════════════════════════════════════════════

def _iren_review():
    """The real IREN $47P Dec 18 '26: entry $18.64 → assignment basis
    $28.36; verdict machinery resolves ROLL_DONT_CLOSE (RVrank 99, all
    extrinsic), earnings inside the 30d GTC window."""
    return {
        "contract": "IREN_PUT_47_20261218",
        "underlying": "IREN", "type": "PUT", "qty": -1,
        "strike": 47.0, "expiration": "2026-12-18",
        "entry_price": 18.64, "current_mid": 10.43, "days_to_expiry": 123,
        "delta": -0.35,
        "recommendation": "HOLD",
        "matrix_cell_id": "PUT_NORMAL_MOD_OTM_TAKEPROFIT",
        "roll_candidates": [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
        ],
    }


def _iren_snapshot(config=None):
    snap = _snapshot(
        config=config,
        quotes={"IREN": {"last": 47.59}},
        technicals={},
        earnings={"IREN": "2026-08-27"},  # 10d from TODAY
    )
    # The exit-cost context that resolves ROLL_DONT_CLOSE (RVrank 99, all
    # extrinsic) — same shape as the 2026-08-13 IREN regression fixture.
    snap["iv_ranks"] = {"IREN": 99}
    snap["chains"] = {"IREN_2026-12-18": {"puts": [
        {"strike": 47.0, "bid": 10.00, "ask": 10.85}]}}
    return snap


def test_iren_cushion_flips_to_hold_through_earnings():
    """"It feels like IREN is ripping... Do I really need to close?" —
    with the assignment-basis cushion ≥ 25% (basis $28.36 = $47 strike −
    $18.64 premium, 40% below spot $47.59) the single voice becomes HOLD
    THROUGH EARNINGS — willing owner; a -25% print still assigns above
    basis; the premium stays yours if held; NO GTC through the print. The
    close-the-binary case renders as SUBORDINATE context only."""
    items = _render(_iren_review(), _iren_snapshot())
    text = "\n".join(items)
    assert "**HOLD THROUGH EARNINGS — willing owner** IREN_PUT_47" in text
    assert "CLOSE BEFORE EARNINGS" not in text
    assert "basis $28.36 is 40% below spot $47.59" in text
    assert "a -25% print still assigns above basis" in text
    assert "place NO GTC through the print" in text
    assert "Counter-case (subordinate):" in text
    assert "closing at mid $10.43" in text
    assert one_voice_violations(items) == []


def test_below_threshold_cushion_closes_before_earnings_as_today():
    """Cushion below the willing-owner threshold → close-before-earnings
    EXACTLY as today (same fixture, threshold raised above the measured
    40% cushion). One voice on the close card too."""
    cfg = {"momentum_hold": {"enabled": True,
                             "earnings_hold_min_cushion_pct": 45}}
    items = _render(_iren_review(), _iren_snapshot(config=cfg))
    text = "\n".join(items)
    assert "**CLOSE BEFORE EARNINGS** IREN_PUT_47" in text
    assert "HOLD THROUGH EARNINGS" not in text
    assert one_voice_violations(items) == []


def test_config_off_earnings_path_is_legacy():
    """momentum_hold off → the 2026-08-13 IREN earnings-close behavior is
    byte-identical legacy (no willing-owner voice anywhere)."""
    items = _render(_iren_review(), _iren_snapshot(config={}))
    text = "\n".join(items)
    assert "**CLOSE BEFORE EARNINGS** IREN_PUT_47" in text
    assert "HOLD THROUGH EARNINGS" not in text


def test_earnings_hold_module_math():
    """earnings_hold measures basis = strike − premium and the cushion as
    a % of spot; below threshold / call side / missing spot → None (rule
    #19 — no fabricated cushion ever holds through a print)."""
    rev = _iren_review()
    snap = _iren_snapshot()
    res = mh.earnings_hold(rev, snap, _MH_ON)
    assert res is not None
    assert abs(res["basis"] - 28.36) < 1e-9
    assert abs(res["cushion_pct"] - (47.59 - 28.36) / 47.59 * 100) < 1e-6
    # Below threshold → None (basis $41 is only 13.8% below spot).
    thin = dict(rev, entry_price=6.00)
    assert mh.earnings_hold(thin, snap, _MH_ON) is None
    # Call side / missing spot / config off → None.
    assert mh.earnings_hold(dict(rev, type="CALL"), snap, _MH_ON) is None
    no_spot = dict(snap, quotes={}, technicals={}, positions=[])
    assert mh.earnings_hold(rev, no_spot, _MH_ON) is None
    assert mh.earnings_hold(rev, snap, {}) is None


# ═══════════════════════════════════════════════════════════════════════════
# (d) Money Plan — riders counted under Blocked money, never banked
# ═══════════════════════════════════════════════════════════════════════════

def test_money_plan_counts_riders_under_blocked_money():
    """"let's make sure we squeeze as much as possible out of these
    options" — a riding winner is NOT a banked close; Blocked money says
    'N winner(s) riding momentum'."""
    rev = _sndk_friday()
    rev["_momentum_ride"] = ("SNDK +1.5% today / RSI 56 rising "
                             "(3-session +8.0%) · 25% OTM · close on FIRST "
                             "RED DAY or RSI ≥ 65 or 85% capture")
    lines, plan = build_money_plan(
        date_str=TODAY, action_list_lines=[], options_reviews=[rev],
        new_ideas=[], playbook=None, analytics=None, snapshot_data=None,
        config=_MH_ON)
    md = "\n".join(lines)
    assert "**Bank today:** none actionable this cycle" in md
    assert "1 winner riding momentum (SNDK $1230P)" in md
    assert plan["riding_momentum"][0]["label"] == "SNDK $1230P"
    # The fold-in guard treats a rider like every other demotion — never
    # banked via the playbook path (the 08-14 NOK lesson generalized).
    assert demoted_close_idents([rev]) == {"SNDK_PUT_1230_20260918"}


def test_risk_alert_rewrites_to_riding_momentum():
    """The alert surface must not scream CLOSE_FOR_PROFIT while the action
    list rides (the AVGO two-surfaces lesson) — it rewrites to RIDING
    MOMENTUM with the measured note."""
    rev = _sndk_friday()
    rev["recommendation"] = "CLOSE_FOR_PROFIT"
    rev["rationale"] = "Time-adjusted close"
    rev["_momentum_ride"] = "SNDK +1.5% today / RSI 56 rising"
    alerts = "\n".join(render_risk_alerts([], [rev], {"regime": "NORMAL"}))
    assert "RIDING MOMENTUM" in alerts
    assert "→ **CLOSE_FOR_PROFIT**:" not in alerts


def test_block4_close_for_profit_rides_too():
    """The matrix CLOSE_FOR_PROFIT path (block #4) gets the same overlay —
    a yield-motivated fast-winner close on a ripping put defers to RIDE."""
    rev = _sndk_friday(mid=31.0)  # 28.7% — below block #2's 30% floor
    rev["recommendation"] = "CLOSE_FOR_PROFIT"
    rev["matrix_cell_id"] = "GUARDRAIL_TIME_ADJUSTED"
    rev["rationale"] = "Time-adjusted close — fast winner."
    items = _render(rev, _snapshot())
    heads = _headlines(items)
    assert any("🏇 **RIDE — momentum hold** SNDK_PUT_1230" in h
               for h in heads)
    assert not any("CLOSE_FOR_PROFIT** SNDK_PUT_1230" in h for h in heads)
    assert rev.get("_momentum_ride")


# ═══════════════════════════════════════════════════════════════════════════
# (e) config off → byte-identical legacy
# ═══════════════════════════════════════════════════════════════════════════

def test_config_off_is_byte_identical_legacy():
    """momentum_hold absent or enabled: false → the overlay never runs:
    identical rendered items, plain CLOSE, no marker, no stall text."""
    outs = []
    for cfg in ({}, {"momentum_hold": {"enabled": False}}):
        rev = _sndk_friday()
        items = _render(rev, _snapshot(config=cfg))
        assert any("**CLOSE** SNDK_PUT_1230" in h for h in _headlines(items))
        assert not rev.get("_momentum_ride")
        assert "momentum" not in "\n".join(items).lower()
        outs.append(items)
    assert outs[0] == outs[1]


# ═══════════════════════════════════════════════════════════════════════════
# (f) fail-open on unmeasurable momentum — no fabricated ride (rule #19)
# ═══════════════════════════════════════════════════════════════════════════

def test_no_live_quote_means_no_ride():
    """No live quote AND no broker-position price → momentum is
    unmeasurable → the close fires exactly as today (fail-open FALSE —
    never a fabricated momentum hold)."""
    rev = _sndk_friday()
    snap = _snapshot(quotes={})
    items = _render(rev, snap)
    assert not rev.get("_momentum_ride")
    head = next(h for h in _headlines(items)
                if "**CLOSE** SNDK_PUT_1230" in h)
    assert "momentum stalled" not in head
    ride, why = mh.momentum_ride(rev, snap, _MH_ON)
    assert ride is False and "unmeasurable" in why


def test_no_close_series_means_no_ride():
    """A technicals entry without a 3-session close series can't measure
    the trend → no ride."""
    rev = _sndk_friday()
    snap = _snapshot(technicals={"SNDK": {"spot": 1625.38, "rsi_14": 56.5}})
    ride, why = mh.momentum_ride(rev, snap, _MH_ON)
    assert ride is False and "3-session" in why
    items = _render(rev, snap)
    assert not rev.get("_momentum_ride")
    assert any("**CLOSE** SNDK_PUT_1230" in h for h in _headlines(items))


def test_unresolvable_rsi_means_no_ride():
    """Vintage-stale RSI (big drift, no recomputable series) → no ride on
    an unverified read — George's squeeze never runs on stale data."""
    # Drift +20% with a too-short close series → resolve status "stale",
    # rsi None.
    snap = _snapshot(
        quotes={"SNDK": {"last": 1950.0}},
        technicals={"SNDK": {"spot": 1625.38, "rsi_14": 56.5,
                             "rsi_closes": [1528.11, 1560.0, 1625.38]}})
    ride, why = mh.momentum_ride(_sndk_friday(), snap, _MH_ON)
    assert ride is False
    assert "RSI unverifiable" in why
