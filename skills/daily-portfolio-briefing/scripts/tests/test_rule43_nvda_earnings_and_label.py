"""Rule #43 batch — NVDA "PULLBACK CSP" card defects (observed 2026-08-04 rerun).

Observed card:

    1. PULLBACK CSP NVDA — sell $185P exp Fri Sep 04 ... RSI 53 ...
    Trade-validator: ⚠️ MARGINAL — EV $+22 ...
    Earnings check: ⚠️ ⚠️ Earnings 22d away, -9d before expiration

Two defects:
1. NVDA prints Aug 26; the Sep 04 contract SPANS the print ("never sell puts
   through earnings") — yet the surface rendered a double-⚠️ warning + a
   MARGINAL validator verdict instead of a hard BLOCK. The old gate only
   blocked the ≤14d "imminent" level.
2. "PULLBACK CSP" read as "NVDA is in a pullback now" — it's a strategy name.
   Renamed to "CSP — PAID-TO-WAIT" with an explicit strategy explainer +
   current-state RSI read on every card.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.earnings_guard import (  # noqa: E402
    check_earnings_conflict,
    format_new_open_block,
)
from render import panels  # noqa: E402
from render.panels import render_action_list  # noqa: E402

TODAY = "2026-08-04"          # the observed rerun's date
NVDA_EARNINGS = "2026-08-26"  # prints 22d out
SPANNING_EXP = "2026-09-04"   # 9d AFTER the print — contract spans it
PRE_PRINT_EXP = "2026-08-21"  # 5d BEFORE the print — clears it


def _nvda_snap(earnings=NVDA_EARNINGS):
    return {
        "balance": {"accountValue": 1_000_000, "cash": 300_000},
        "quotes": {"NVDA": {"last": 190.4}},   # fresh vs technicals close
        "technicals": {"NVDA": {"spot": 190.0, "rsi_14": 53.0}},
        "positions": [{"symbol": "NVDA", "assetType": "EQUITY",
                       "qty": 100, "price": 190.4}],
        "earnings_calendar": ({"NVDA": earnings} if earnings else {}),
        "recommendations_list": [],
        "_config": {"core_positions": ["NVDA"], "accounts": []},
    }


def _render_nvda(monkeypatch, snap, expiration):
    monkeypatch.setattr(panels, "find_put_strike_near",
                        lambda *a, **kw: {"strike": 185.0, "mid": 4.20,
                                          "expiration": expiration})
    monkeypatch.setattr(panels, "validate_csp", lambda **kw: None)
    equity_reviews = [{"ticker": "NVDA", "price": 190.4, "weight": 0.02,
                       "pl_pct": 0.1, "recommendation": "HOLD", "qty": 100}]
    return "\n".join(render_action_list(
        equity_reviews, [], [], analytics={},
        snapshot_data=snap, date_str=TODAY))


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1 — a new-open put spanning the print is a hard BLOCK, not a warning
# ─────────────────────────────────────────────────────────────────────────────

def test_pullback_csp_spanning_earnings_blocked(monkeypatch):
    """The observed '1. PULLBACK CSP NVDA — sell $185P exp Fri Sep 04 ...
    Earnings check: ⚠️ ⚠️ Earnings 22d away, -9d before expiration' must NOT
    render as an actionable card: NVDA prints Aug 26, inside the Sep 04
    contract → 🚫 BLOCK (EARNINGS_WINDOW), demoted to the transparency footer
    (rule #24 — visible, never a green-lit ticket)."""
    md = _render_nvda(monkeypatch, _nvda_snap(), SPANNING_EXP)
    assert "**CSP — PAID-TO-WAIT** NVDA — sell" not in md   # no actionable card
    assert "CSP — PAID-TO-WAIT NVDA blocked" in md           # rule #24 footer
    assert "🚫 BLOCK (EARNINGS_WINDOW)" in md
    assert "NVDA prints Aug 26" in md
    assert "inside the Sep 04 contract" in md
    assert "pick a pre-print expiry or wait for post-print" in md


def test_pre_print_expiry_not_blocked(monkeypatch):
    """Same NVDA setup with an Aug 21 expiry (print Aug 26 lands AFTER the
    contract dies) → the earnings gate must NOT fire and the card renders
    actionable."""
    md = _render_nvda(monkeypatch, _nvda_snap(), PRE_PRINT_EXP)
    assert "**CSP — PAID-TO-WAIT** NVDA — sell $185P" in md
    assert "BLOCK (EARNINGS_WINDOW)" not in md
    assert "Earnings check:" in md            # affirmative clearance still shown


# ─────────────────────────────────────────────────────────────────────────────
# Fix 1b — human-readable phrasing (no more "-9d before expiration")
# ─────────────────────────────────────────────────────────────────────────────

def test_earnings_phrasing_human_readable(monkeypatch):
    """The observed 'Earnings 22d away, -9d before expiration' is nonsense to
    a human. The guard's message must say the print lands N days BEFORE
    expiry ('contract spans earnings'), and the signed-delta phrasing must
    not appear anywhere in a rendered briefing."""
    chk = check_earnings_conflict("NVDA", SPANNING_EXP,
                                  {"NVDA": NVDA_EARNINGS}, TODAY)
    assert chk["spans_expiration"] is True
    assert "prints 9d BEFORE expiry" in chk["message"]
    assert "contract spans earnings" in chk["message"]
    assert not re.search(r"-\d+d before expiration", chk["message"])

    # On-expiry-day print reads "ON expiry day", never "0d".
    chk_same = check_earnings_conflict("NVDA", "2026-08-26",
                                       {"NVDA": NVDA_EARNINGS}, TODAY)
    assert chk_same["spans_expiration"] is True

    # The rendered surface carries no signed-delta phrasing either.
    md = _render_nvda(monkeypatch, _nvda_snap(), SPANNING_EXP)
    assert not re.search(r"-\d+d before expiration", md)

    # And the block message renders only REAL dates (rule #19).
    msg = format_new_open_block("NVDA", chk, SPANNING_EXP)
    assert "NVDA prints Aug 26, inside the Sep 04 contract" in msg


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2 — label renamed to CSP — PAID-TO-WAIT, with a state-honest explainer
# ─────────────────────────────────────────────────────────────────────────────

def test_label_paid_to_wait_with_state_line(monkeypatch):
    """George read 'PULLBACK CSP NVDA' as 'NVDA is in a pullback now' — but
    the observed card carried RSI 53 (neutral tape). The rendered label is now
    'CSP — PAID-TO-WAIT' and every card carries a strategy explainer that
    names the stock's CURRENT state explicitly."""
    md = _render_nvda(monkeypatch, _nvda_snap(earnings=None), SPANNING_EXP)
    assert "**CSP — PAID-TO-WAIT** NVDA — sell $185P" in md
    assert "**PULLBACK CSP**" not in md                     # old label gone
    assert "Strategy: sell a put below spot" in md
    assert "does not claim the stock is currently pulling back" in md
    assert "today's state: RSI 53 (neutral)" in md


def test_label_kind_stays_pullback_csp_internally(monkeypatch):
    """Internal identifiers stay PULLBACK_CSP (too many consumers): the
    day-over-day signature and rec-aging action key derived from the NEW
    rendered header must be byte-identical to the old ones."""
    from analysis.briefing_diff import _signature_for_action
    from analysis.rec_aging import action_from_line

    line = ("1. **CSP — PAID-TO-WAIT** NVDA — sell $185P exp Fri Sep 04 "
            "for $4.20 premium")
    assert _signature_for_action(line) == "PULLBACK CSP|NVDA"
    a = action_from_line(line)
    assert a["kind"] == "PULLBACK_CSP"
    assert a["key"] == "PULLBACK_CSP:NVDA"

    # Capital planner parses the new header into the same internal kind.
    cp_dir = Path(__file__).resolve().parents[3] / "capital-planner" / "scripts"
    sys.path.insert(0, str(cp_dir))
    try:
        from plan import _parse_action_block
        act = _parse_action_block(line, "   - collateral $18,500", {})
        assert act is not None and act.kind == "NEW_CSP" and act.ticker == "NVDA"
    finally:
        sys.path.remove(str(cp_dir))
