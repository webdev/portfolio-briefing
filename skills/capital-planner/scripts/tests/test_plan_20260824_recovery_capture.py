"""Rule #19/#14 one-voice regression — the 2026-08-24 Capital Plan split.

Observed on the real 2026-08-24 briefing: the Capital Plan row said

    "- CLOSE IREN — locks $+0, frees $4,700 — net cash +$4,700"

while the SAME briefing's Money Plan banked IREN at ~$556 inside
"Bank today: 6 close(s) → $+6,706 realized". Root cause: the action-list
card rendered

    "8. **CLOSE INTO RECOVERY** IREN_PUT_47_20261218 — recovered to
     ~breakeven (+29.9% of premium) before the Aug 27 print; buy-to-close
     1× limit $13.07 (≈ $1,308), GTC"

and the close-profit regex only matched "+31% ($+107)" / "+39% captured
($+722)" — an integer percent with at most ONE word before the parens —
so "(+29.9% of premium)" (decimal pct, percent INSIDE the parens, no
dollar figure at all) parsed to $0. Both surfaces must derive from the
same measured capture: the recovery card now renders the parseable
"+29.9% of premium ($+557)" form, and the parser accepts decimal
percents with up to two connecting words.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from plan import extract_actions_from_action_list  # noqa: E402


RECOVERY_BLOCK = [
    "8. **CLOSE INTO RECOVERY** IREN_PUT_47_20261218 — recovered to "
    "+29.9% of premium ($+557) before the Aug 27 print; buy-to-close 1× "
    "limit $13.07 (≈ $1,308), GTC  · RSI 47 🟢 pullback",
    "   - **Why:** position recovered to +29.9% of premium ($+557) before "
    "the Aug 27 print — exiting locks the gain and removes the binary.",
    "   - **Earnings check:** 🔴 BLOCK: Imminent earnings 3d away, "
    "expires 144d",
]

FORTRESS_BLOCK = [
    "8. **HOLD THROUGH EARNINGS — willing owner** IREN_PUT_47_20261218 — "
    "basis $28.36 is 29% below spot $40.06; a -25% print still assigns "
    "above basis",
    "   - **Why:** willing-owner fortress — assignment basis $28.36 "
    "(strike $47 − $18.64 premium) sits 29% below spot.",
]


def test_recovery_close_capture_parses_to_measured_dollars():
    """'CLOSE IREN — locks $+0' while the Money Plan banked ~$556: the
    plan must lock the MEASURED capture from the rendered card."""
    actions = extract_actions_from_action_list(RECOVERY_BLOCK)
    close = next(a for a in actions if a.kind == "CLOSE")
    assert "locks $+557" in close.description
    assert "locks $+0" not in close.description
    # collateral still derived from the contract ident's own strike
    assert "frees $4,700" in close.description


def test_legacy_close_forms_still_parse():
    """The pre-existing '+39% captured ($+722)' and '+31% ($+107)' forms
    keep parsing unchanged (the regex widening is backward-compatible)."""
    legacy = [
        "3. **CLOSE BEFORE EARNINGS** IREN_PUT_47_20261218 — +39% captured "
        "($+722); buy-to-close at mid $11.40 before IREN prints in 10d",
    ]
    actions = extract_actions_from_action_list(legacy)
    close = next(a for a in actions if a.kind == "CLOSE")
    assert "locks $+722" in close.description


def test_fortress_hold_never_enters_the_plan_as_a_close():
    """After the rule-#51 precedence fix IREN renders 'HOLD THROUGH
    EARNINGS — willing owner' — a hold, not a close: the Capital Plan must
    not emit a CLOSE row for it at all (the hold-never-banked invariant)."""
    actions = extract_actions_from_action_list(FORTRESS_BLOCK)
    assert not [a for a in actions if a.kind == "CLOSE"]
