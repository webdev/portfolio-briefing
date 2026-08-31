"""Regression tests — hold-class directive items must never be aged (⏳).

Observed in the real 2026-08-31 briefing:

    "2. 🏇 **RIDING (directive)** MELI_PUT_1460_20270617 — +25% captured —
    exits armed: GTC@75% $24.60 (85% stretch) · stall on red day ≤-2% or
    RSI ≥ 70 · never past 85% capture · not through earnings at ≥75% ·
    [today: MELI -0.9% · RSI 60 · 25% captured — no exit trigger fired]
    ⏳ IGNORED 3 DAYS"

A directive-tracked ride is the OPPOSITE of ignoring — the operator filed
the directive, the pipeline tracks its exits every cycle, and there is
nothing to "execute today". The aging/stalled-item machinery must skip
directive-ride items (and hold-class directive items generally: 🏇 RIDING,
🏇 RIDE, 🔔 reminder-armed). "🏇 EXIT — STALL FIRED (directive)" is an
actionable exit ticket and must KEEP aging.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.rec_aging import (  # noqa: E402
    apply_aging_to_action_items,
    is_hold_class_directive,
    render_stalled_panel,
)

# The observed 2026-08-31 line, as rendered BEFORE the aging annotator ran.
_RIDING_LINE = (
    "2. 🏇 **RIDING (directive)** MELI_PUT_1460_20270617 — +25% captured — "
    "exits armed: GTC@75% $24.60 (85% stretch) · stall on red day ≤-2% or "
    "RSI ≥ 70 · never past 85% capture · not through earnings at ≥75% · "
    "[today: MELI -0.9% · RSI 60 · 25% captured — no exit trigger fired]"
)
_RIDING_KEY = "RIDING:MELI_PUT_1460_20270617"


def _aging_info():
    """State + reconciliation that, under the broken code, produced the
    observed '⏳ IGNORED 3 DAYS' on the MELI directive ride."""
    close_key = "CLOSE:AMD_PUT_420_20261218"
    return {
        "today": "2026-08-31",
        "state": {
            _RIDING_KEY: {"first_flagged": "2026-08-27", "days_flagged": 2},
            close_key: {"first_flagged": "2026-08-27", "days_flagged": 2},
        },
        "reconciliation": {
            _RIDING_KEY: "IGNORED",
            close_key: "IGNORED",
        },
    }


def test_directive_ride_never_gets_ignored_tag():
    """The observed '🏇 **RIDING (directive)** MELI_PUT_1460_20270617 …
    ⏳ IGNORED 3 DAYS' must not recur: a directive-tracked ride is being
    tracked, not ignored — no ⏳ tag ever."""
    items = [
        "1. **CLOSE** AMD_PUT_420_20261218 — +51% captured; "
        "buy-to-close limit $12.10",
        _RIDING_LINE,
    ]
    out = apply_aging_to_action_items(items, _aging_info())
    riding = next(ln for ln in out if "RIDING (directive)" in ln)
    assert "⏳" not in riding
    assert "IGNORED" not in riding


def test_normal_recs_still_age():
    """The fix must not turn off aging for real recommendations: the CLOSE
    item in the same list still ticks to '⏳ IGNORED 3 DAYS'."""
    items = [
        "1. **CLOSE** AMD_PUT_420_20261218 — +51% captured; "
        "buy-to-close limit $12.10",
        _RIDING_LINE,
    ]
    out = apply_aging_to_action_items(items, _aging_info())
    close = next(ln for ln in out if "AMD_PUT_420_20261218" in ln
                 and "**CLOSE**" in ln)
    assert "⏳ IGNORED 3 DAYS" in close


def test_directive_ride_leaves_no_aging_state():
    """The riding item must not enter the aging clock at all — no
    updated_state entry, no actions_export row — so it can never later be
    promoted to the ⛔ Stalled panel."""
    info = _aging_info()
    apply_aging_to_action_items([_RIDING_LINE], info)
    assert _RIDING_KEY not in (info.get("updated_state") or {})
    assert all(a.get("key") != _RIDING_KEY
               for a in (info.get("actions_export") or []))


def test_stalled_panel_skips_hold_class_entries():
    """Even a stale state entry carrying a RIDING kind at 7 days must never
    render in '## ⛔ Stalled Items' — the panel says items 'have been
    ignored', which is false for a tracked ride."""
    aged = {
        _RIDING_KEY: {"kind": "RIDING", "ident": "MELI_PUT_1460_20270617",
                      "days_flagged": 7, "first_flagged": "2026-08-24"},
        "TRIM:NVDA": {"kind": "TRIM", "ident": "NVDA",
                      "days_flagged": 7, "first_flagged": "2026-08-24"},
    }
    md = "\n".join(render_stalled_panel(aged))
    assert "MELI_PUT_1460_20270617" not in md
    assert "TRIM NVDA" in md


def test_hold_class_predicate_covers_the_class():
    """🏇 RIDING / 🏇 RIDE — momentum hold / 🔔 reminder-armed are all
    hold-class (skipped); 🏇 EXIT — STALL FIRED (directive) is an
    actionable exit and is NOT skipped."""
    assert is_hold_class_directive(_RIDING_LINE, "RIDING (directive)")
    assert is_hold_class_directive(
        "3. 🏇 **RIDE — momentum hold** SNDK_PUT_1230_20261016 — ride the "
        "trend", "RIDE — momentum hold")
    assert is_hold_class_directive(
        "4. 🔔 **REMINDER ARMED** MU_PUT_890_20261218 — check at earnings")
    assert not is_hold_class_directive(
        "1. 🏇 **EXIT — STALL FIRED (directive)** MELI_PUT_1460_20270617 — "
        "stall trigger fired", "EXIT — STALL FIRED (directive)")
    assert not is_hold_class_directive(
        "1. **CLOSE** AMD_PUT_420_20261218 — +51%", "CLOSE")
