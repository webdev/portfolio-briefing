"""Task #44 (2026-08-04) — rule-#43 batch from George's audit of the
2026-08-04 briefing ("sometimes I am confused by recommendations. we are
looking to make money here").

Observed output, verbatim:

1. Action #4: headline "**CLOSE** VRT_PUT_280_20270115 — +30% ($+2,226)"
   with inline "**⚖️ Verdict: ROLL, don't close** — closing pays $4,534 of
   panic premium at IV rank 89" — two contradictory voices on ONE card
   (the take-profit close path never consulted the exit-cost verdict).
2. Action #2: "CLOSE PLTR_CALL_200 — Loss stop 2.46x" on a 24%-OTM covered
   call — the core never-close-short-calls override didn't fire because
   PLTR is Tier A in position_tiers but absent from core_positions.
3. Action #6: "TRIM PLTR — 10.6% NLV (over 10% cap)" while Risk Alerts on
   the SAME run said "GOOG 15.5% — within Tier A bounds (cap 22%)".
4. The SPY HEDGE item nagged for ~29 consecutive sessions, outranking money
   actions every day without ever being decided.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.position_tiers import core_union, is_core_ticker  # noqa: E402
from analysis.rec_aging import render_stalled_panel  # noqa: E402
from render.panels import (  # noqa: E402
    one_voice_violations,
    render_action_list,
)

TODAY = "2026-08-04"


# ── Fixtures ───────────────────────────────────────────────────────────────

def _snapshot(quotes=None, chains=None, iv_ranks=None, earnings=None,
              config=None):
    return {
        "quotes": quotes or {},
        "chains": chains or {},
        "iv_ranks": iv_ranks or {},
        "earnings_calendar": earnings or {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }


def _vrt_review(candidates=None):
    """VRT-shaped: $280P Jan '27, spot $271 (ITM), +30% captured, buyback
    65% extrinsic at IV rank 89, DTE 164 → verdict ROLL_DONT_CLOSE while
    the capture floor fires the take-profit CLOSE path."""
    return {
        "contract": "VRT_PUT_280_20270115",
        "underlying": "VRT", "type": "PUT", "qty": -2,
        "strike": 280.0, "expiration": "2027-01-15",
        "entry_price": 37.10, "current_mid": 25.97, "days_to_expiry": 164,
        "recommendation": "HOLD",
        "matrix_cell_id": "PUT_NORMAL_ITM_NEUTRAL_HOLD",
        "roll_candidates": candidates if candidates is not None else [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            {"id": "B", "description": "2× $260P Mar 19 '27",
             "instruction": {"sell_strike": 260.0,
                             "sell_expiration": "2027-03-19",
                             "sell_mid": 27.75, "sell_bid": 27.10,
                             "sell_ask": 28.40},
             "netDollars": 356.0, "dteExtension": 63},
        ],
    }


def _vrt_snapshot(**config):
    return _snapshot(
        quotes={"VRT": {"last": 271.0}},
        chains={"VRT_2027-01-15": {"puts": [
            {"strike": 280.0, "bid": 25.50, "ask": 26.45}]}},
        iv_ranks={"VRT": 89},
        config=config,
    )


def _pltr_call_review():
    """PLTR-shaped: $200C Dec '26 covered call, spot $161 (24% OTM),
    loss-stopped at 2.46x — the observed "CLOSE PLTR_CALL_200 — Loss stop
    2.46x" card. No priced roll candidates so block #4 owns the decision."""
    return {
        "contract": "PLTR_CALL_200_20261218",
        "underlying": "PLTR", "type": "CALL", "qty": -2,
        "strike": 200.0, "expiration": "2026-12-18",
        "entry_price": 4.00, "current_mid": 9.84, "days_to_expiry": 136,
        "recommendation": "CLOSE",
        "matrix_cell_id": "GUARDRAIL_LOSS_STOP",
        "rationale": "Loss stop 2.46x premium",
        "roll_candidates": [],
    }


def _headlines(items):
    return [ln for ln in items if ln.lstrip()[:1].isdigit()]


# ── Fix 1 — one-voice rule ─────────────────────────────────────────────────

class TestOneVoice:
    def test_vrt_profitable_roll_verdict_becomes_take_profit_via_roll_down(self):
        """Observed: '**CLOSE** VRT_PUT_280_20270115 — +30% ($+2,226)' with
        '**⚖️ Verdict: ROLL, don't close**' inline. Now: ONE recommendation
        — TAKE PROFIT VIA ROLL-DOWN, a two-leg ticket to the ranked
        credit-positive roll-down candidate."""
        items = render_action_list(
            [], [_vrt_review()], [], None, _vrt_snapshot(), date_str=TODAY)
        text = "\n".join(items)
        assert "TAKE PROFIT VIA ROLL-DOWN" in text
        assert "$260P" in text or "260" in text
        assert "net +$356 credit" in text
        # No CLOSE headline on this contract.
        assert not any("**CLOSE** VRT_PUT_280" in h for h in _headlines(items))
        # Anatomy block renders under the single recommendation.
        assert "Exit cost anatomy" in text
        # Sweep: zero both-voices cards.
        assert one_voice_violations(items) == []

    def test_no_credit_positive_roll_down_falls_back_to_hold_gtc_50(self):
        """No credit-positive roll-down priced → HOLD — GTC at the
        50%-capture price (entry $37.10 → GTC $18.55), never a CLOSE that
        contradicts its own ROLL verdict."""
        rev = _vrt_review(candidates=[
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
            # Roll-down exists but costs a DEBIT — not credit-positive.
            {"id": "B", "description": "2× $260P Mar 19 '27",
             "instruction": {"sell_strike": 260.0,
                             "sell_expiration": "2027-03-19",
                             "sell_mid": 20.00, "sell_bid": 19.50,
                             "sell_ask": 20.60},
             "netDollars": -1194.0, "dteExtension": 63},
        ])
        items = render_action_list(
            [], [rev], [], None, _vrt_snapshot(), date_str=TODAY)
        text = "\n".join(items)
        assert "HOLD — GTC AT 50%" in text
        assert "$18.55" in text
        assert not any("**CLOSE** VRT_PUT_280" in h for h in _headlines(items))
        assert one_voice_violations(items) == []

    def test_close_clean_winner_unchanged(self):
        """A profitable close whose verdict AGREES with closing (mostly
        intrinsic) still renders the plain CLOSE — one-voice only rewires
        the contradiction, not the agreement."""
        rev = {
            "contract": "NVDA_PUT_190_20260918",
            "underlying": "NVDA", "type": "PUT", "qty": -1,
            "strike": 190.0, "expiration": "2026-09-18",
            "entry_price": 60.0, "current_mid": 40.0, "days_to_expiry": 45,
            "recommendation": "HOLD", "matrix_cell_id": "X",
            "roll_candidates": [],
        }
        snap = _snapshot(
            quotes={"NVDA": {"last": 150.0}},
            chains={"NVDA_2026-09-18": {"puts": [
                {"strike": 190.0, "bid": 39.6, "ask": 40.4}]}},
            iv_ranks={"NVDA": 40},
        )
        items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
        assert any("**CLOSE** NVDA_PUT_190" in h for h in _headlines(items))
        assert one_voice_violations(items) == []

    def test_kill_switch_restores_legacy_both_voices(self):
        """exit_cost.one_voice: false → the legacy combined card comes back
        (CLOSE headline + ROLL verdict) and the sweep flags it — proving
        both the switch and the sweep work."""
        snap = _vrt_snapshot(exit_cost={"one_voice": False})
        items = render_action_list(
            [], [_vrt_review()], [], None, snap, date_str=TODAY)
        text = "\n".join(items)
        assert any("**CLOSE** VRT_PUT_280" in h for h in _headlines(items))
        assert "ROLL, don't close" in text
        assert len(one_voice_violations(items)) == 1

    def test_sweep_exempts_close_into_recovery(self):
        """CLOSE INTO RECOVERY outranks ROLL_DONT_CLOSE by documented
        precedence (task #40 fix 4) — the sweep must not flag it."""
        items = [
            "1. **CLOSE INTO RECOVERY** LITE_PUT_700_20260918 — recovered",
            "   - **⚖️ Verdict: ROLL, don't close** — event risk wins here",
        ]
        assert one_voice_violations(items) == []


# ── Fix 2 — Tier A carries core protections ────────────────────────────────

class TestTierAUnifiedWithCore:
    def test_core_union_includes_tier_a(self):
        cfg = {"core_positions": ["GOOG", "NVDA"],
               "position_tiers": {"tier_a_core": ["PLTR", "NVDA"]}}
        assert core_union(cfg) == {"GOOG", "NVDA", "PLTR"}
        assert is_core_ticker("PLTR", cfg)
        assert is_core_ticker("goog", cfg)
        assert not is_core_ticker("TSLA", cfg)
        assert core_union({}) == set()

    def test_pltr_loss_stop_becomes_defensive_roll_core_override(self):
        """Observed: 'CLOSE PLTR_CALL_200 — Loss stop 2.46x'. PLTR is Tier A
        (position_tiers) though not on core_positions — the loss-stop CLOSE
        must vanish, pivoted to DEFENSIVE ROLL (core override)."""
        snap = _snapshot(
            quotes={"PLTR": {"last": 161.0}},
            config={"position_tiers": {"tier_a_core": ["PLTR"]}},
        )
        items = render_action_list(
            [], [_pltr_call_review()], [], None, snap, date_str=TODAY)
        text = "\n".join(items)
        assert "DEFENSIVE ROLL (core override)" in text
        assert not any("**CLOSE** PLTR_CALL_200" in h
                       for h in _headlines(items))

    def test_non_tier_a_loss_stop_close_still_renders(self):
        """Regression: without a tier/core designation the loss-stop CLOSE
        renders as before (standard non-core behavior)."""
        rev = dict(_pltr_call_review())
        snap = _snapshot(quotes={"PLTR": {"last": 161.0}})
        items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
        assert any("**CLOSE** PLTR_CALL_200" in h for h in _headlines(items))

    def test_goog_core_positions_override_unchanged(self):
        """Regression: a name on core_positions (no tier config) keeps the
        existing core override."""
        rev = dict(_pltr_call_review())
        rev.update({"contract": "GOOG_CALL_240_20261218", "underlying": "GOOG",
                    "strike": 240.0})
        snap = _snapshot(quotes={"GOOG": {"last": 210.0}},
                         config={"core_positions": ["GOOG"]})
        items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
        assert "DEFENSIVE ROLL (core override)" in "\n".join(items)

    def test_pltr_10_6_pct_no_trim(self):
        """Observed: 'TRIM PLTR — 10.6% NLV (over 10% cap)' while Risk
        Alerts said 'within Tier A bounds (cap 22%)'. Tier A names use the
        core soft-cap path — 10.6% → NO trim."""
        er = {"ticker": "PLTR", "weight": 0.106, "qty": 600, "price": 161.0,
              "pl_pct": 1.2, "recommendation": "HOLD"}
        snap = _snapshot(config={"position_tiers": {"tier_a_core": ["PLTR"]}})
        items = render_action_list([er], [], [], None, snap, date_str=TODAY)
        assert "TRIM" not in "\n".join(_headlines(items))

    def test_plain_ticker_over_cap_still_trims(self):
        """Regression: an unlisted (Tier C default) name over the 10% cap
        still gets the TRIM."""
        er = {"ticker": "IREN", "weight": 0.115, "qty": 1000, "price": 100.0,
              "pl_pct": 0.5, "recommendation": "HOLD"}
        items = render_action_list([er], [], [], None, _snapshot(),
                                   date_str=TODAY)
        assert any("TRIM" in h and "IREN" in h for h in _headlines(items))

    def test_tier_b_uses_tier_cap(self):
        """Explicit Tier B (cap 12%) — 11.5% no trim, 13% trims."""
        cfg = {"position_tiers": {"tier_b_income": ["MU"]}}
        er = {"ticker": "MU", "weight": 0.115, "qty": 100, "price": 900.0,
              "pl_pct": 0.4, "recommendation": "HOLD"}
        items = render_action_list([er], [], [], None, _snapshot(config=cfg),
                                   date_str=TODAY)
        assert "TRIM" not in "\n".join(_headlines(items))
        er2 = dict(er, weight=0.13)
        items2 = render_action_list([er2], [], [], None, _snapshot(config=cfg),
                                    date_str=TODAY)
        assert any("TRIM" in h and "MU" in h for h in _headlines(items2))


# ── Fix 4 — hedge-nag resolution ───────────────────────────────────────────

def _hedge_analytics():
    return {
        "stress_coverage": {"coverage_ratio": 0.30},
        "hedge_book": {
            "recommendations": [{
                "target_strike": 560, "target_expiration": "2026-09-18",
                "contracts": 3, "estimated_cost": 2100.0,
                "instrument": "SPY_PUT",
            }],
            "current_coverage_pct": 0.0, "target_coverage_pct": 0.10,
        },
        "spy_price": 620.0,
    }


def _aging(days: int):
    return {
        "today": TODAY,
        "state": {"HEDGE:SPY": {"first_flagged": "2026-07-01",
                                "days_flagged": days,
                                "last_status": "IGNORED",
                                "last_aged": "2026-08-03"}},
        "reconciliation": {"HEDGE:SPY": "IGNORED"},
    }


class TestHedgeNag:
    def test_29_day_hedge_vacates_numbered_list(self):
        """Observed: the SPY HEDGE item nagged ~29 consecutive sessions,
        outranking money actions daily. At/after hedge_nag_days it (a)
        vacates the numbered slots, (b) emits both ready-to-paste directive
        templates, (c) keeps aging so the ⛔ Stalled Items entry remains."""
        aging = _aging(29)
        items = render_action_list(
            [], [], [], _hedge_analytics(), _snapshot(), date_str=TODAY,
            aging_info=aging)
        text = "\n".join(items)
        assert not any("**HEDGE**" in h for h in _headlines(items))
        assert "DEFER hedge until stress coverage ≥ 0.5×" in text
        assert "HEDGE NOW at half size — buy 2× SPY put $560P" in text
        # Aging clock kept ticking via the synthetic action (29 → 30).
        aged = aging.get("aged") or {}
        assert aged.get("HEDGE:SPY", {}).get("days_flagged") == 30
        # Stalled panel entry remains — aging is aging.
        stalled = "\n".join(render_stalled_panel(aged))
        assert "HEDGE SPY" in stalled
        # Money Plan hook recorded.
        assert aging.get("hedge_nag", {}).get("days") == 29

    def test_below_threshold_hedge_still_numbered(self):
        """Regression: 5 ignored days (< hedge_nag_days 14) → the numbered
        HEDGE item renders exactly as before."""
        items = render_action_list(
            [], [], [], _hedge_analytics(), _snapshot(), date_str=TODAY,
            aging_info=_aging(5))
        assert any("**HEDGE**" in h for h in _headlines(items))

    def test_config_override_hedge_nag_days(self):
        """hedge_nag_days: 40 → 29 ignored days stays numbered."""
        items = render_action_list(
            [], [], [], _hedge_analytics(),
            _snapshot(config={"hedge_nag_days": 40}), date_str=TODAY,
            aging_info=_aging(29))
        assert any("**HEDGE**" in h for h in _headlines(items))

    def test_no_aging_info_legacy_numbered(self):
        """Fail-open: legacy callers without aging_info keep the numbered
        item — days can't be measured, so nothing vacates."""
        items = render_action_list(
            [], [], [], _hedge_analytics(), _snapshot(), date_str=TODAY)
        assert any("**HEDGE**" in h for h in _headlines(items))
