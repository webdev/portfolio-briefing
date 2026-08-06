"""Bug #25 (2026-07-22) — MSFT $380P directive doesn't silence CLOSE +
stalled aging.

User symptom: action item "CLOSE MSFT_PUT_380_20270319 — +32% ⏳ IGNORED 5
DAYS · ⛔ DECISION REQUIRED" rendered while Fable's review on the SAME day
said "your directive holds". The CLOSE recommender and the rec-aging stalled
promotion must both respect the standing directive in
state/fable_advisor_memory.md until a release condition fires.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.advisor_directives import (  # noqa: E402
    HoldDirective,
    directive_holds_contract,
    parse_directives,
    should_suppress_close,
)
from analysis.rec_aging import STALLED_DAYS, render_stalled_panel  # noqa: E402
from render.panels import render_action_list  # noqa: E402


# The real user-editable head of state/fable_advisor_memory.md (2026-07-22).
_REAL_MEMORY = """# Fable Advisor Memory

## Notes to Fable (user-editable)

(add your directives below this line)

### Winner-close discipline overrides (per-position)

- **AMD_PUT_420_20261218 — hold for higher capture.** My exit threshold on this
  position is _more than 33% capture_ — don't keep flagging it as stalled at
  current levels (~36-42%). Stop escalating "day N ignored" until either
  (a) capture rises past ~65% AND DTE < 60d, or (b) capture drops below +25%,
  or (c) something material changes on AMD (earnings gap, thesis break).

- **MSFT_PUT_350_20260918 — same treatment as AMD.** Hold for more than 33%
  capture; don't rank it as a top winner-close at 50-60%.

- **MSFT_PUT_380_20270319 — same treatment as MSFT_PUT_350.** Long-dated
  position at +31% capture with ~240 DTE remaining. Don't ring the
  take-profit bell early on a Tier A compounder.
  Stop escalating the CLOSE recommendation until one of these fires:
  (a) capture rises past ~55% AND DTE < 90d, or
  (b) capture drops below +20% (position reverting), or
  (c) MSFT spot breaks $360 (approaching the $353 52w-low support), or
  (d) something material changes on MSFT (earnings gap-down, thesis break).

### Holds despite third-party downgrade

- **TSLA (equity) — not ready to sell yet.** Parkev's SELL rating is noted.

## Recent reviews (auto-maintained, most recent first)

### 2026-07-22 — Fable review

- **FAKE_PUT_999_20270101 — hold** this should never parse (below divider).
"""


def _msft_380(directives):
    return next(d for d in directives if d.contract == "MSFT_PUT_380_20270319")


# ── Parser ────────────────────────────────────────────────────────────────


def test_parses_msft_380_directive_from_real_memory():
    """Feed the actual memory.md content — both contract directives with
    release conditions parse, and nothing below '## Recent reviews' does."""
    ds = parse_directives(_REAL_MEMORY)
    contracts = {d.contract for d in ds}
    assert "MSFT_PUT_380_20270319" in contracts
    assert "AMD_PUT_420_20261218" in contracts
    assert "MSFT_PUT_350_20260918" in contracts
    assert "FAKE_PUT_999_20270101" not in contracts  # auto section excluded
    msft = _msft_380(ds)
    assert msft.ticker == "MSFT" and msft.type == "hold"
    assert msft.release_capture_and_dte == (0.55, 90)
    assert msft.release_capture_below == 0.20
    assert msft.release_spot_below == 360.0
    amd = next(d for d in ds if d.contract == "AMD_PUT_420_20261218")
    assert amd.release_capture_and_dte == (0.65, 60)
    assert amd.release_capture_below == 0.25
    assert amd.release_spot_below is None
    # MSFT_350 is a plain hold — no parseable release conditions.
    m350 = next(d for d in ds if d.contract == "MSFT_PUT_350_20260918")
    assert m350.release_capture_and_dte is None


def test_fail_open_on_malformed():
    """Garbage / empty / non-string input → empty list, never a raise."""
    assert parse_directives(None) == []
    assert parse_directives("") == []
    assert parse_directives("- ** broken ** no contract here") == []
    assert parse_directives(12345) == []  # type: ignore[arg-type]


# ── Release conditions ────────────────────────────────────────────────────


def _d(**kw):
    base = dict(contract="MSFT_PUT_380_20270319", ticker="MSFT", type="hold",
                release_capture_and_dte=(0.55, 90),
                release_capture_below=0.20,
                release_spot_below=360.0, raw_text="")
    base.update(kw)
    return HoldDirective(**base)


def test_release_conditions_capture_and_dte():
    """Suppressed at 32%/240d (the real position); released only when
    capture > 55% AND DTE < 90 — BOTH must hold."""
    assert should_suppress_close(_d(), 0.32, 240, 400.0) is True
    assert should_suppress_close(_d(), 0.60, 240, 400.0) is True   # DTE too far
    assert should_suppress_close(_d(), 0.40, 80, 400.0) is True    # capture too low
    assert should_suppress_close(_d(), 0.60, 80, 400.0) is False   # both → released
    # Lenient percent form (32 == 0.32).
    assert should_suppress_close(_d(), 32.0, 240, 400.0) is True


def test_release_conditions_capture_below():
    assert should_suppress_close(_d(), 0.19, 240, 400.0) is False
    assert should_suppress_close(_d(), 0.21, 240, 400.0) is True


def test_release_conditions_spot_break():
    assert should_suppress_close(_d(), 0.32, 240, 355.0) is False  # broke $360
    assert should_suppress_close(_d(), 0.32, 240, 365.0) is True
    # Missing spot never fires the release (fail toward the directive).
    assert should_suppress_close(_d(), 0.32, 240, None) is True


def test_directive_holds_contract_lookup():
    ds = parse_directives(_REAL_MEMORY)
    hit = directive_holds_contract("MSFT_PUT_380_20270319", ds, 0.32, 240, 400.0)
    assert hit is not None and hit.contract == "MSFT_PUT_380_20270319"
    # Released → None (CLOSE allowed).
    assert directive_holds_contract(
        "MSFT_PUT_380_20270319", ds, 0.60, 80, 400.0) is None
    # No directive on the contract → None.
    assert directive_holds_contract("NVDA_PUT_200_20260918", ds, 0.45, 58, 170) is None


# ── Action-list wiring ────────────────────────────────────────────────────


def _msft_review(capture_entry=55.0736, mid=37.35, dte=240):
    return {
        "contract": "MSFT_PUT_380_20270319", "underlying": "MSFT",
        "type": "PUT", "strike": 380.0, "expiration": "2027-03-19",
        "qty": -1, "entry_price": capture_entry, "current_mid": mid,
        "days_to_expiry": dte, "recommendation": "HOLD", "rationale": "",
    }


def _snapshot(spot=400.0):
    return {
        "balance": {"accountValue": 1_000_000.0, "cash": 100_000.0},
        "quotes": {"MSFT": {"last": spot}},
        "technicals": {},
        "_config": {"core_positions": [], "accounts": []},
        "_advisor_directives": parse_directives(_REAL_MEMORY),
    }


def test_close_suppressed_when_directive_holds():
    """The 2026-07-22 case: MSFT_PUT_380 at +32% capture, 240 DTE, spot $400
    → NO CLOSE action; a transparency footer names the suppression."""
    md = "\n".join(render_action_list(
        [], [_msft_review()], [], analytics={}, snapshot_data=_snapshot()))
    assert "**CLOSE** MSFT_PUT_380_20270319" not in md
    assert "CLOSE MSFT_PUT_380_20270319 suppressed by standing directive" in md
    assert "release conditions not met" in md


def test_close_allowed_when_release_condition_hits():
    """Capture 60% + DTE 80 (release (a)) → the CLOSE renders normally."""
    rev = _msft_review(capture_entry=55.0736, mid=22.0, dte=80)  # ~60% capture
    md = "\n".join(render_action_list(
        [], [rev], [], analytics={}, snapshot_data=_snapshot()))
    assert "**CLOSE** MSFT_PUT_380_20270319" in md
    assert "suppressed by standing directive" not in md


def test_close_allowed_when_spot_breaks_360():
    rev = _msft_review()  # +32%, 240 DTE — normally suppressed
    md = "\n".join(render_action_list(
        [], [rev], [], analytics={}, snapshot_data=_snapshot(spot=355.0)))
    assert "**CLOSE** MSFT_PUT_380_20270319" in md


# ── Stalled-aging wiring ──────────────────────────────────────────────────


def test_stalled_aging_skips_directive_suppressed():
    """A directive-suppressed rec never counts toward 'IGNORED N DAYS':
    (a) the suppressed CLOSE doesn't enter the action list, so its aging
    clock stops; (b) the stalled panel skips suppressed keys outright."""
    aged = {
        "CLOSE:MSFT_PUT_380_20270319": {
            "kind": "CLOSE", "ident": "MSFT_PUT_380_20270319",
            "summary": "", "first_flagged": "2026-07-15",
            "days_flagged": STALLED_DAYS + 1, "recon_status": "IGNORED",
        },
        "HEDGE:SPY": {
            "kind": "HEDGE", "ident": "SPY", "summary": "",
            "first_flagged": "2026-06-14",
            "days_flagged": STALLED_DAYS + 10, "recon_status": "IGNORED",
        },
    }
    md = "\n".join(render_stalled_panel(
        aged, suppressed={"CLOSE:MSFT_PUT_380_20270319"}))
    assert "MSFT_PUT_380_20270319" not in md
    assert "HEDGE SPY" in md
    # And the action-list path records the suppressed keys on aging_info.
    aging_info = {"today": "2026-07-22", "state": {}, "reconciliation": {}}
    render_action_list([], [_msft_review()], [], analytics={},
                       snapshot_data=_snapshot(), aging_info=aging_info)
    assert "CLOSE:MSFT_PUT_380_20270319" in aging_info.get(
        "directive_suppressed", set())
    # The suppressed item never entered today's actions → no aging entry.
    assert "CLOSE:MSFT_PUT_380_20270319" not in (aging_info.get("aged") or {})
