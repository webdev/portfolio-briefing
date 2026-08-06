"""Task #43 batch (2026-07-31) — three defects from the 07-31 briefing.

Each test's docstring quotes the observed output (house TDD rule #33 — the
test must fail against the broken code and pass against the fix).

Defect 1: Risk Alerts rendered the matrix roll directive unfiltered while
the action gate demoted the SAME roll in Watch (AVGO contradiction).
Defect 2: a +854d (~2.3yr) roll candidate rendered with no tenor-cap
warning in the ROLL ANALYSIS menu.
Defect 3: a standing hold directive suppressed a CLOSE 4 days before an
earnings print because its release conditions predated the print (AMD).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.advisor_directives import (  # noqa: E402
    HoldDirective,
    earnings_pause,
    parse_directives,
)
from render.panels import render_action_list, render_risk_alerts  # noqa: E402
from steps.per_option_commentary import render_watch_with_commentary  # noqa: E402

TODAY = "2026-07-31"


# ── Shared fixtures ────────────────────────────────────────────────────────

def _snapshot(quotes=None, earnings=None, config=None, directives=None):
    snap = {
        "quotes": quotes or {},
        "chains": {},
        "iv_ranks": {},
        "earnings_calendar": earnings or {},
        "technicals": {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }
    if directives is not None:
        snap["_advisor_directives"] = directives
    return snap


def _avgo_review():
    """The real AVGO shape (2026-07-31): $375P Aug 14, spot $386, measured
    position δ -0.38, +14% captured — fully OTM and profitable."""
    return {
        "contract": "AVGO_PUT_375_20260814",
        "underlying": "AVGO", "type": "PUT", "qty": -1,
        "strike": 375.0, "expiration": "2026-08-14",
        "entry_price": 12.85, "current_mid": 11.05, "days_to_expiry": 14,
        "delta": -0.38,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "matrix_cell_id": "PUT_NORMAL_NEAR_ATM_SHORT_DTE_TESTED",
        "rationale": ("Tested at/near the strike inside 21 DTE with under "
                      "25% captured — gamma week approaching: roll "
                      "down-and-out, close, or file an accept-assignment "
                      "directive."),
        "roll_candidates": [
            {"id": "A", "description": "HOLD (don't roll)", "instruction": None,
             "netDollars": 0, "dteExtension": 0,
             "notes": "Wait for theta/IV mean-reversion to work"},
            {"id": "B", "description": "1× $325P Aug 21 '26",
             "instruction": {"sell_strike": 325.0,
                             "sell_expiration": "2026-08-21",
                             "sell_mid": 2.31, "sell_bid": 2.24,
                             "sell_ask": 2.37},
             "netDollars": -871.0, "dteExtension": 7,
             "notes": "Lower strike (−$50), extend +7d"},
        ],
    }


# ── Defect 1: Risk Alerts must reflect the action-gate demotion ────────────

def test_risk_alert_reflects_demotion():
    """Observed (2026-07-31): Risk Alerts rendered '🎯 AVGO_PUT_375_20260814
    → **ROLL_OUT_AND_DOWN**: Tested at/near the strike inside 21 DTE...'
    while the Watch panel for the SAME position rendered '⏸ Roll demoted
    (nothing to defend): position is OTM and profitable (14% captured) with
    measured |δ| 0.38 < 0.40'. The alert must reflect the demotion —
    rewritten (never hidden, rule #24), not a bare roll directive."""
    rev = _avgo_review()
    snap = _snapshot(quotes={"AVGO": {"last": 386.00}})
    # render_action_list runs first in aggregate and stashes the demotion
    # note on the review dict — the single source of truth.
    md_actions = "\n".join(render_action_list(
        [], [rev], [], analytics={}, snapshot_data=snap, date_str=TODAY))
    assert "EXECUTE ROLL" not in md_actions
    assert "nothing to defend" in (rev.get("_roll_gate_demotion") or "")
    md_alerts = "\n".join(render_risk_alerts([], [rev], {}))
    assert "⏸ ROLL DEMOTED" in md_alerts
    assert "nothing to defend" in md_alerts
    assert "AVGO_PUT_375_20260814" in md_alerts
    assert "→ **ROLL_OUT_AND_DOWN**" not in md_alerts


def test_risk_alert_untouched_when_not_demoted():
    """A genuinely tested roll (no demotion key — e.g. the MU tested shape,
    ITM with measured |δ| ≥ 0.40) keeps the normal 🎯 alert unchanged."""
    rev = {
        "contract": "MU_PUT_950_20261218",
        "underlying": "MU", "type": "PUT", "qty": -1,
        "strike": 950.0, "expiration": "2026-12-18",
        "entry_price": 21.4, "current_mid": 35.0, "days_to_expiry": 140,
        "delta": -0.55,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "rationale": "Tested at the strike — roll down-and-out.",
    }
    md_alerts = "\n".join(render_risk_alerts([], [rev], {}))
    assert "🎯 MU_PUT_950_20261218 → **ROLL_OUT_AND_DOWN**" in md_alerts
    assert "ROLL DEMOTED" not in md_alerts


# ── Defect 2: multi-year roll candidate carries a tenor-cap warning ────────

def test_menu_tenor_warning_past_cap():
    """Observed (AVGO ROLL ANALYSIS, 2026-07-31): '| E | 1× $350P Dec 15 '28
    @ $80.00 +854d | +$6,690 credit | Lower strike (−$25), extend +854d
    (~2.3yr)...' — honest tenor phrasing but NO warning that +854d is 7×
    past the 120d action tenor cap. Candidates past the cap must say so;
    candidates inside the cap must not."""
    rev = _avgo_review()
    rev["roll_candidates"].append(
        {"id": "E", "description": "1× $350P Dec 15 '28 @ $80.00 +854d",
         "instruction": {"sell_strike": 350.0,
                         "sell_expiration": "2028-12-15",
                         "sell_mid": 80.0},
         "netDollars": 6690.0, "dteExtension": 854,
         "notes": "Lower strike (−$25), extend +854d (~2.3yr), "
                  "reduces assignment risk and obligation by $2,500"})
    snap = _snapshot(quotes={"AVGO": {"last": 386.00}},
                     config={"roll": {"max_action_tenor_days": 120}})
    md = "\n".join(render_watch_with_commentary([], [rev], snap))
    warn = "far past the 120d tenor cap — shown for completeness, not recommended"
    assert warn in md
    # exactly one candidate warns — the +7d and HOLD rows stay clean
    assert md.count("far past the") == 1
    e_row = next(ln for ln in md.splitlines() if "| E |" in ln)
    b_row = next(ln for ln in md.splitlines() if "| B |" in ln)
    assert warn in e_row
    assert "far past the" not in b_row


def test_menu_tenor_warning_core_gets_3x_cap():
    """Core names roll year after year — the menu warning uses the same 3×
    allowance the action list uses (360d default), so a +200d candidate on
    a core name does NOT warn while +854d still does."""
    rev = _avgo_review()
    rev["roll_candidates"] = [
        {"id": "B", "description": "1× $360P Feb 19 '27",
         "instruction": {"sell_strike": 360.0,
                         "sell_expiration": "2027-02-19", "sell_mid": 20.0},
         "netDollars": 900.0, "dteExtension": 200, "notes": "extend +200d"},
        {"id": "E", "description": "1× $350P Dec 15 '28",
         "instruction": {"sell_strike": 350.0,
                         "sell_expiration": "2028-12-15", "sell_mid": 80.0},
         "netDollars": 6690.0, "dteExtension": 854,
         "notes": "extend +854d (~2.3yr)"},
    ]
    snap = _snapshot(quotes={"AVGO": {"last": 386.00}},
                     config={"roll": {"max_action_tenor_days": 120},
                             "core_positions": ["AVGO"]})
    md = "\n".join(render_watch_with_commentary([], [rev], snap))
    assert "far past the 360d tenor cap" in md
    assert md.count("far past the") == 1


# ── Defect 3: directive auto-pause on imminent earnings ────────────────────

_AMD_MEMORY = """# Fable Advisor Memory

## Notes to Fable (user-editable)

- **AMD_PUT_420_20261218 — hold for higher capture.** My exit threshold on
  this position is _more than 33% capture_ — don't keep flagging it as
  stalled at current levels. Stop escalating until either
  (a) capture rises past ~65% AND DTE < 60d, or (b) capture drops below +25%.

## Recent reviews (auto-maintained, most recent first)
"""

_AMD_MEMORY_THROUGH_EARNINGS = _AMD_MEMORY.replace(
    "hold for higher capture.",
    "hold through earnings and for higher capture.")


def _amd_review():
    # entry 8.00 → mid 4.80 = +40% capture, 140 DTE (the observed shape)
    return {
        "contract": "AMD_PUT_420_20261218", "underlying": "AMD",
        "type": "PUT", "strike": 420.0, "expiration": "2026-12-18",
        "qty": -1, "entry_price": 8.00, "current_mid": 4.80,
        "days_to_expiry": 140, "recommendation": "HOLD", "rationale": "",
    }


def test_directive_paused_near_earnings_profitable():
    """Observed (2026-07-31): '_📋 CLOSE AMD_PUT_420_20261218 suppressed by
    standing directive (capture 40%, DTE 140d — release conditions not
    met)_' while the SAME briefing's Watch panel said 'Earnings in 4d with
    40% captured. Consider closing before report.' The directive predates
    the print — its suppression pauses; the CLOSE surfaces with the
    paused note."""
    snap = _snapshot(quotes={"AMD": {"last": 470.0}},
                     earnings={"AMD": "2026-08-04"},  # 4d from TODAY
                     directives=parse_directives(_AMD_MEMORY))
    md = "\n".join(render_action_list(
        [], [_amd_review()], [], analytics={}, snapshot_data=snap,
        date_str=TODAY))
    assert "**CLOSE** AMD_PUT_420_20261218" in md
    assert "Directive on AMD_PUT_420_20261218 paused" in md
    assert "earnings in 4d" in md
    assert "+40% captured" in md
    assert '"through earnings"' in md  # the reaffirm hint
    assert "suppressed by standing directive" not in md


def test_directive_holds_through_earnings_phrase():
    """A directive containing the phrase 'through earnings' is exempt from
    the pause — explicit user intent wins. Same AMD shape, earnings 4d,
    +40% capture → CLOSE stays suppressed with the standard footer."""
    directives = parse_directives(_AMD_MEMORY_THROUGH_EARNINGS)
    assert directives, "fixture must parse as a hold directive"
    # function-level: the pause never fires on a through-earnings directive
    assert earnings_pause(directives[0], 0.40, 4, {}) is False
    snap = _snapshot(quotes={"AMD": {"last": 470.0}},
                     earnings={"AMD": "2026-08-04"},
                     directives=directives)
    md = "\n".join(render_action_list(
        [], [_amd_review()], [], analytics={}, snapshot_data=snap,
        date_str=TODAY))
    assert "**CLOSE** AMD_PUT_420_20261218" not in md
    assert "CLOSE AMD_PUT_420_20261218 suppressed by standing directive" in md
    assert "Directive on AMD_PUT_420_20261218 paused" not in md


def _plain_directive(raw_text=""):
    return HoldDirective(
        contract="AMD_PUT_420_20261218", ticker="AMD", type="hold",
        release_capture_and_dte=(0.65, 60), release_capture_below=0.25,
        release_spot_below=None, raw_text=raw_text)


def test_directive_pause_skips_underwater():
    """Underwater positions near prints are owned by the loss-stop / urgent
    paths — capture -20% with earnings in 4d must NOT trigger the pause
    (the directive keeps holding; other paths surface the risk)."""
    d = _plain_directive()
    assert earnings_pause(d, -0.20, 4, {}) is False
    assert earnings_pause(d, 0.10, 4, {}) is False   # below the 25% floor
    assert earnings_pause(d, 0.25, 4, {}) is True    # at the floor — pauses
    assert earnings_pause(d, 0.40, None, {}) is False  # no earnings date
    assert earnings_pause(d, 40.0, 4, {}) is True    # lenient percent form


def test_directive_pause_config_days():
    """directives.earnings_release_days is config-driven (default 7)."""
    d = _plain_directive()
    cfg3 = {"directives": {"earnings_release_days": 3}}
    assert earnings_pause(d, 0.40, 4, cfg3) is False   # outside 3d window
    assert earnings_pause(d, 0.40, 2, cfg3) is True
    assert earnings_pause(d, 0.40, 4, {}) is True      # default 7d
    assert earnings_pause(d, 0.40, 8, {}) is False
    assert earnings_pause(d, 0.40, 7, None) is True    # boundary + no config
