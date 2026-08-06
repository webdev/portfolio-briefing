"""Rule-#43 urgent (2026-08-04, second pass) — the one-voice TAKE PROFIT VIA
ROLL-DOWN composer violated the roll tenor cap (CLAUDE.md rule #14).

Observed output, verbatim (latest rendered briefing):

    TAKE PROFIT VIA ROLL-DOWN VRT_PUT_280_20270115 — BTC 1× @ $51.97 +
    STO 1× VRT $240P Fri Dec 15 '28 @ $75.75 → net +$2,270 credit

The STO leg is Dec 2028 — ~700 days past the current Jan '27 expiry, ~6× the
120d Tier C action tenor cap (roll.max_action_tenor_days; core/Tier A 360d).
The composer hunted for a net-credit roll-down and the only credit-positive
$240P was the 2.4-year one — the rule #14 max-credit trap resurrected
through the new path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.tenor_guard import ticket_tenor_violations  # noqa: E402
from render.panels import (  # noqa: E402
    one_voice_violations,
    render_action_list,
)

TODAY = "2026-08-04"


# ── Fixtures ───────────────────────────────────────────────────────────────

def _snapshot(quotes=None, chains=None, iv_ranks=None, config=None):
    return {
        "quotes": quotes or {},
        "chains": chains or {},
        "iv_ranks": iv_ranks or {},
        "earnings_calendar": {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }


FAR_240P = {
    # The observed offender: $240P Dec 15 '28 — the ONLY credit-positive
    # roll-down, ~700d past the Jan '27 expiry.
    "id": "C", "description": "1× $240P Dec 15 '28",
    "instruction": {"sell_strike": 240.0, "sell_expiration": "2028-12-15",
                    "sell_mid": 75.75, "sell_bid": 74.90, "sell_ask": 76.60},
    "netDollars": 2270.0, "dteExtension": 700,
}

NEAR_260P = {
    "id": "B", "description": "1× $260P Mar 19 '27",
    "instruction": {"sell_strike": 260.0, "sell_expiration": "2027-03-19",
                    "sell_mid": 55.53, "sell_bid": 54.90, "sell_ask": 56.20},
    "netDollars": 356.0, "dteExtension": 63,
}

HOLD_A = {"id": "A", "description": "HOLD", "instruction": None,
          "netDollars": 0, "dteExtension": 0}


def _vrt_review(candidates):
    """VRT-shaped: $280P Jan '27, spot $271 (ITM), entry $75.00 / mid $51.97
    (+31% captured, the observed 'BTC 1× @ $51.97'), 83% extrinsic at IV
    rank 89, DTE 164 → verdict ROLL_DONT_CLOSE while the capture floor fires
    the take-profit path."""
    return {
        "contract": "VRT_PUT_280_20270115",
        "underlying": "VRT", "type": "PUT", "qty": -1,
        "strike": 280.0, "expiration": "2027-01-15",
        "entry_price": 75.00, "current_mid": 51.97, "days_to_expiry": 164,
        "recommendation": "HOLD",
        "matrix_cell_id": "PUT_NORMAL_ITM_NEUTRAL_HOLD",
        "roll_candidates": candidates,
    }


def _vrt_snapshot(**config):
    return _snapshot(
        quotes={"VRT": {"last": 271.0}},
        chains={"VRT_2027-01-15": {"puts": [
            {"strike": 280.0, "bid": 51.50, "ask": 52.45}]}},
        iv_ranks={"VRT": 89},
        config=config,
    )


def _headlines(items):
    return [ln for ln in items if ln.lstrip()[:1].isdigit()]


# ── Tests ──────────────────────────────────────────────────────────────────

class TestTakeProfitTenorCap:
    def test_take_profit_rolldown_respects_tenor_cap(self):
        """Observed: 'TAKE PROFIT VIA ROLL-DOWN VRT_PUT_280_20270115 — BTC 1×
        @ $51.97 + STO 1× VRT $240P Fri Dec 15 '28 @ $75.75 → net +$2,270
        credit' — a 700d STO leg, ~6× the 120d cap. With an IN-TENOR credit
        roll-down ($260P Mar 19 '27, +63d, +$356) also priced, the composer
        must pick it and NEVER the Dec '28 leg."""
        rev = _vrt_review([HOLD_A, NEAR_260P, FAR_240P])
        items = render_action_list(
            [], [rev], [], None, _vrt_snapshot(), date_str=TODAY)
        text = "\n".join(items)
        assert "TAKE PROFIT VIA ROLL-DOWN" in text
        assert "$260P" in text
        assert "net +$356 credit" in text
        assert "Dec 15 '28" not in text          # the offending leg is gone
        assert "$240P" not in text
        assert one_voice_violations(items) == []
        assert ticket_tenor_violations(text) == []

    def test_fallback_hold_gtc_when_no_intenor_credit(self):
        """VRT-shaped: the ONLY credit-positive roll-down is the observed
        700d-out '$240P Fri Dec 15 '28 @ $75.75 → net +$2,270 credit' →
        the mandatory fallback is HOLD — GTC AT 50% (entry $75.00 → GTC
        $37.50). An in-tenor small-debit roll-down (strike cut ≥ $25) may
        render as the honestly-labeled ALTERNATIVE line, never the
        headline."""
        debit_250p = {
            "id": "D", "description": "1× $250P Mar 19 '27",
            "instruction": {"sell_strike": 250.0,
                            "sell_expiration": "2027-03-19",
                            "sell_mid": 47.40, "sell_bid": 46.80,
                            "sell_ask": 48.10},
            "netDollars": -450.0, "dteExtension": 63,
        }
        rev = _vrt_review([HOLD_A, FAR_240P, debit_250p])
        items = render_action_list(
            [], [rev], [], None, _vrt_snapshot(), date_str=TODAY)
        text = "\n".join(items)
        assert "HOLD — GTC AT 50%" in text
        assert "$37.50" in text
        assert "TAKE PROFIT VIA ROLL-DOWN" not in text
        assert "Dec 15 '28" not in text
        # The in-tenor debit roll-down is OFFERED (sub-bullet), honest debit.
        assert "Alternative (in-tenor debit roll-down)" in text
        assert "$250P" in text and "-$450" in text and "DEBIT" in text
        assert not any("Alternative" in h for h in _headlines(items))
        assert one_voice_violations(items) == []
        assert ticket_tenor_violations(text) == []

    def test_core_names_360d_cap(self):
        """Core names (core_positions ∪ Tier A) get the ×3 allowance: a
        +300d credit roll-down is in-tenor for a core VRT (cap 360d) but
        past cap for a non-core VRT (cap 120d)."""
        mid_300d = {
            "id": "E", "description": "1× $250P Nov 12 '27",
            "instruction": {"sell_strike": 250.0,
                            "sell_expiration": "2027-11-12",
                            "sell_mid": 60.97, "sell_bid": 60.30,
                            "sell_ask": 61.70},
            "netDollars": 900.0, "dteExtension": 300,
        }
        # Non-core: 300d > 120d → falls back to HOLD — GTC.
        items = render_action_list(
            [], [_vrt_review([HOLD_A, mid_300d])], [], None,
            _vrt_snapshot(), date_str=TODAY)
        assert "HOLD — GTC AT 50%" in "\n".join(items)
        # Core: 300d ≤ 360d → the roll-down headline is allowed.
        items_core = render_action_list(
            [], [_vrt_review([HOLD_A, mid_300d])], [], None,
            _vrt_snapshot(core_positions=["VRT"]), date_str=TODAY)
        text_core = "\n".join(items_core)
        assert "TAKE PROFIT VIA ROLL-DOWN" in text_core
        assert "$250P" in text_core
        assert ticket_tenor_violations(
            text_core, core_tickers={"VRT"}) == []

    def test_sweep_no_ticket_past_tenor_anywhere(self):
        """Full rendered fixture briefing carries NO ticket with an STO leg
        past the cap — and the sweep genuinely catches the observed VRT
        Dec '28 ticket when fed verbatim."""
        rev = _vrt_review([HOLD_A, NEAR_260P, FAR_240P])
        items = render_action_list(
            [], [rev], [], None, _vrt_snapshot(), date_str=TODAY)
        assert ticket_tenor_violations("\n".join(items)) == []
        observed = (
            "4. **TAKE PROFIT VIA ROLL-DOWN** VRT_PUT_280_20270115 — "
            "BTC 1× @ $51.97 + STO 1× VRT $240P Fri Dec 15 '28 @ $75.75 "
            "→ net +$2,270 credit"
        )
        hits = ticket_tenor_violations(observed)
        assert len(hits) == 1 and "Dec 15 '28" in hits[0]

    def test_menu_reference_rows_exempt(self):
        """ROLL ANALYSIS menu-table reference rows (including those carrying
        the '⚠ far past the 120d tenor cap' warning) are reference, not
        tickets — exempt. A '✅ recommended' table row past the cap IS a
        ticket and gets flagged."""
        md = "\n".join([
            "🟡 **VRT_PUT_280_20270115**",
            "  **ROLL ANALYSIS:**",
            "  | id | Action | Net | Notes |",
            "  |----|--------|-----|-------|",
            "  | C | 1× $240P Dec 15 '28 | +$2,270 credit | ⚠ far past the "
            "120d tenor cap — shown for completeness, not recommended |",
            "  | D | 1× $240P Dec 15 '28 | +$2,270 credit | plain reference |",
        ])
        assert ticket_tenor_violations(md) == []
        md_bad = md + ("\n  | E ✅ recommended | 1× $240P Dec 15 '28 | "
                       "+$2,270 credit | |")
        hits = ticket_tenor_violations(md_bad)
        assert len(hits) == 1 and "✅ recommended" in hits[0]
