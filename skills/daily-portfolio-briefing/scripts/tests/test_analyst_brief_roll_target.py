"""Rule #43 fix (2026-07-31) — Analyst Brief URGENT ROLLS TARGET line rendered
unresolved placeholders.

User symptom (2026-07-31 briefing, identically on 2026-07-22):

    **IREN_PUT_47_20261218 — ROLL_OUT_AND_DOWN**  · RSI 44
    - REASON: ITM — roll down-and-out to cut assignment risk; ...
    - TARGET: IREN ? $? PUT for $0.40 net credit

Root cause: the renderer read the legacy select_roll_target dict with the
WRONG keys ("strike"/"expiration" instead of its actual
"strikePrice"/"expirationDate"), so strike and expiration fell to "?"
placeholders while the matching "expectedNetCredit" key rendered a real
number. Fix: TARGET now resolves from the SAME ranked candidate the ROLL
ANALYSIS table marks ✅ recommended; honest "unavailable" text when no
candidate resolves; never "?" placeholders (rule #19).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from render.analyst_brief import render_analyst_brief  # noqa: E402


def _snap():
    return {
        "balance": {"accountValue": 1_000_000.0, "cash": 100_000.0},
        "technicals": {},
    }


def _iren_roll_review(**overrides):
    """IREN-shaped ROLL review mirroring the observed 2026-07-31 line."""
    rev = {
        "underlying": "IREN",
        "contract": "IREN_PUT_47_20261218",
        "type": "PUT",
        "qty": -1,
        "strike": 47.0,
        "expiration": "2026-12-18",
        "current_mid": 5.10,
        "entry_price": 3.20,
        "days_to_expiry": 137,
        "recommendation": "ROLL_OUT_AND_DOWN",
        "rationale": (
            "ITM — roll down-and-out to cut assignment risk; same-strike-out "
            "only if you want the assignment at this basis."
        ),
        "recommended_candidate_id": "B",
        "roll_candidates": [
            {
                "id": "A",
                "description": "HOLD (don't roll)",
                "instruction": None,
                "netDollars": 0.0,
            },
            {
                "id": "B",
                "description": "1× $47P Jan 15 '27 @ $5.60",
                "instruction": {
                    "sell_strike": 47.0,
                    "sell_expiration": "2027-01-15",
                    "sell_mid": 5.60,
                    "sell_bid": 5.50,
                    "sell_ask": 5.70,
                },
                "closeCost": 510.0,
                "newCredit": 560.0,
                "netDollars": 50.0,
                "dteExtension": 28,
            },
        ],
        # Legacy dict (REAL select_roll_target keys) — must not be needed
        # when a ranked candidate exists, but keys are correct when it is.
        "roll_target": {
            "strikePrice": 47.0,
            "expirationDate": "2027-01-15",
            "expectedNetCredit": 0.40,
        },
    }
    rev.update(overrides)
    return rev


def _render(options_reviews):
    return "\n".join(render_analyst_brief([], options_reviews, _snap(), {},
                                          {"regime": "HOLD"}))


def test_analyst_brief_roll_target_resolves_from_ranked_candidate():
    """The observed '- TARGET: IREN ? $? PUT for $0.40 net credit' must now
    resolve strike + expiration from the ✅ recommended candidate B:
    'IREN $47P Fri Jan 15 '27 for $0.50 net credit' — no '?' anywhere."""
    md = _render([_iren_roll_review()])
    assert "URGENT ROLLS" in md
    assert "- **TARGET:** IREN $47P Fri Jan 15 '27 for $0.50 net credit" in md
    assert "? $?" not in md
    assert " ? " not in md


def test_analyst_brief_roll_target_no_candidates_honest_text():
    """Chain unreachable (no candidates, no legacy target) → honest
    'target unavailable' text, NEVER '?' placeholders (the observed
    'IREN ? $? PUT for $0.40 net credit')."""
    md = _render([_iren_roll_review(roll_candidates=[], roll_target=None,
                                    recommended_candidate_id=None)])
    assert ("- **TARGET:** unavailable — see ROLL ANALYSIS table / "
            "verify at broker") in md
    assert "?" not in md.replace(
        "unavailable — see ROLL ANALYSIS table / verify at broker", "")


def test_analyst_brief_roll_target_legacy_fallback_uses_real_keys():
    """When only the legacy select_roll_target dict is present, its REAL keys
    (strikePrice/expirationDate) must render — the bug was reading
    'strike'/'expiration' and falling to '?'."""
    md = _render([_iren_roll_review(roll_candidates=[],
                                    recommended_candidate_id=None)])
    assert "- **TARGET:** IREN $47P Fri Jan 15 '27 for $0.40 net credit" in md
    assert "? $?" not in md


def test_analyst_brief_hold_recommendation_uses_if_rolling_anyway():
    """recommended id A (HOLD) on a ROLL_* decision falls back to the
    advisor's explicit if-rolling-anyway candidate, not '?' placeholders."""
    md = _render([_iren_roll_review(recommended_candidate_id="A",
                                    if_rolling_anyway_candidate_id="B",
                                    roll_target=None)])
    assert "- **TARGET:** IREN $47P Fri Jan 15 '27 for $0.50 net credit" in md


def test_no_question_mark_placeholders_anywhere_in_brief():
    """Sweep test over a full rendered brief fixture (rolls + close winners +
    concentration + skips): no '? $?' and no bare ' ? ' placeholder may
    appear anywhere in the Analyst Brief."""
    close_winner = {
        "underlying": "AMD",
        "contract": "AMD_PUT_150_20261218",
        "type": "PUT",
        "qty": -1,
        "strike": 150.0,
        "expiration": "2026-12-18",
        "current_mid": 1.00,
        "entry_price": 3.00,
        "days_to_expiry": 137,
        "recommendation": "HOLD",
        "rationale": "profit capture",
    }
    equity = {"ticker": "TSLA", "weight": 0.143, "price": 250.0, "qty": 572}
    snap = _snap()
    snap["new_ideas"] = [
        {"ticker": "NVDA", "rationale": "RSI overbought — wait"},
        {"rationale": "no ticker — must be skipped, not rendered as ?"},
    ]
    md = "\n".join(render_analyst_brief(
        [equity], [_iren_roll_review(), close_winner], snap, {},
        {"regime": "HOLD"}))
    assert "? $?" not in md
    assert " ? " not in md
    assert "**?**" not in md
