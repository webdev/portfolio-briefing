"""Rule #43 bug-fix-on-sight batch — observed in the REAL 2026-08-13 briefing
(briefing_full_2026-08-13.md). Four defects, each pinned with the observed
output quoted verbatim in the test docstring:

BUG 1 — IREN card spoke with THREE voices + missing earnings precondition:
    "4. **HOLD — GTC AT 50%** IREN_PUT_47_20261218 — +44% captured; place a
    GTC buy-to-close at the 50%-capture price $9.32"
    "   - **⚖️ Verdict: ROLL, don't close** — closing pays $1,042 of panic
    premium at RVrank 99 (realized-vol proxy); ..."
  while Red Flag #1 said "Close winners with ≥30% capture" and Fable noted
  IREN earnings ~14 days away. The GTC-at-capture squeeze REQUIRES
  days_to_next_earnings > 30 (position-review squeeze precondition); with
  the print inside the window the single voice must be the earnings-aware
  close, and any surviving verdict read must be explicitly-subordinate
  context — one headline, one voice.

BUG 2 — NOK roll yield annualized the FULL new-leg premium over only the
  28d EXTENSION window:
    "Yield: **234.1%** ann. on new collateral ($11,000); net-cash +6.2%
    ann. on position. Strike cushion: 5.2% ABOVE spot (ITM)."
  $1.98/share × 10 spreads = 18% static on $11,000 collateral, annualized
  365/28 ≈ 234% — numerator window (155d of premium) didn't match the
  denominator window (28d). Honest figures: ~42% ann. over the full 155d
  new leg, and the +$50 net credit over the +28d extension (~5.9-6.2% ann).

BUG 3 — attribution surfaces didn't reconcile:
    "**Since last snapshot:** NLV +$12,607 · equity MTM +$12,119 · option
    MTM +$633 · premium +$0"
  while the cumulative "- Option premium (net): +$39,858" absorbed the new
  SOXL/GOOG opens' premium (~+$10K) across one calendar day. Root causes:
  (a) multi-day periods were endpoint-to-endpoint diffs while the daily
  line was a snapshot-pair diff — two different measurements that cannot
  reconcile (an intra-period round trip vanished from the cumulative
  premium bucket entirely; every still-open contract sat 100% in premium
  with "Option mark-to-market delta: +$0" since inception); (b) opens
  captured by an intraday rerun of the prior snapshot rendered a
  false-looking bare "+$0" with no window or timing context.

BUG 4 — the covered-call CLOSE "Gain:" boilerplate promised an immediate
  rewrite the briefing's own RSI discipline gates:
    "**Gain:** Locks $+622 profit and unlocks 100×1 shares (notional
    $71,000) for fresh covered-call premium; ..."
  on a card whose own headline read "RSI 54 🟡 mid-range" — a new CC at
  RSI 54 is in the wait-for-strength band (hard rule #11).
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import pnl_attribution as pa  # noqa: E402
from render.benchmark_panel import render_benchmark_panel  # noqa: E402
from render.panels import (  # noqa: E402
    one_voice_violations,
    render_action_list,
)
from yield_formulas import (  # noqa: E402  (sys.path set by render.panels)
    compute_roll_yield,
    format_yield_line,
)

TODAY = "2026-08-13"


# ── Shared fixtures ────────────────────────────────────────────────────────

def _snapshot(quotes=None, chains=None, iv_ranks=None, earnings=None,
              technicals=None, config=None):
    return {
        "quotes": quotes or {},
        "chains": chains or {},
        "iv_ranks": iv_ranks or {},
        "earnings_calendar": earnings or {},
        "technicals": technicals or {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }


def _iren_review(days_to_expiry=127, delta=-0.35, roll_candidates=None):
    """IREN-shaped: $47P Dec 18 '26, entry $18.64 / mid $10.43 (+44%
    captured — the observed '+44% captured ($819)'), spot $47.59 (near-money
    OTM), RVrank 99, 100% extrinsic → verdict ROLL_DONT_CLOSE, no in-tenor
    roll priced → the one-voice resolver decides GTC vs earnings-close."""
    return {
        "contract": "IREN_PUT_47_20261218",
        "underlying": "IREN", "type": "PUT", "qty": -1,
        "strike": 47.0, "expiration": "2026-12-18",
        "entry_price": 18.64, "current_mid": 10.43,
        "days_to_expiry": days_to_expiry,
        "delta": delta,
        "recommendation": "HOLD",
        "matrix_cell_id": "PUT_NORMAL_MOD_OTM_TAKEPROFIT",
        "roll_candidates": roll_candidates or [
            {"id": "A", "description": "HOLD", "instruction": None,
             "netDollars": 0, "dteExtension": 0},
        ],
    }


def _iren_snapshot(earnings_date, **config):
    return _snapshot(
        quotes={"IREN": {"last": 47.59}},
        chains={"IREN_2026-12-18": {"puts": [
            {"strike": 47.0, "bid": 10.00, "ask": 10.85}]}},
        iv_ranks={"IREN": 99},
        earnings=({"IREN": earnings_date} if earnings_date else {}),
        config=config,
    )


# ═══════════════════════════════════════════════════════════════════════════
# BUG 1 — GTC-at-capture earnings precondition + one-voice card
# ═══════════════════════════════════════════════════════════════════════════

class TestGtcEarningsPrecondition:
    def test_gtc_suppressed_and_earnings_close_fires_inside_window(self):
        """Observed: '**HOLD — GTC AT 50%** IREN_PUT_47_20261218 — +44%
        captured; place a GTC buy-to-close at the 50%-capture price $9.32'
        with IREN earnings 14d away (2026-08-27). The GTC squeeze requires
        earnings > 30d — with the print inside the window the single voice
        is CLOSE BEFORE EARNINGS, extrinsic cost stated honestly."""
        items = render_action_list(
            [], [_iren_review()], [], None,
            _iren_snapshot("2026-08-27"), date_str=TODAY)
        text = "\n".join(items)
        assert "CLOSE BEFORE EARNINGS" in text
        assert "HOLD — GTC AT 50%" not in text
        assert "prints in 14d" in text
        # The extrinsic cost of closing is stated honestly, not hidden.
        assert "Honest cost:" in text and "extrinsic" in text
        # The GTC precondition is named with its threshold.
        assert "> 30d" in text or "requires earnings > 30d" in text
        assert one_voice_violations(items) == []

    def test_earnings_close_outranks_roll_down(self):
        """Event risk beats premium mechanics (the CLOSE-INTO-RECOVERY
        precedence): even with a credit-positive IN-TENOR roll-down priced,
        earnings 14d away inside the contract resolves to CLOSE BEFORE
        EARNINGS, never TAKE PROFIT VIA ROLL-DOWN through the print."""
        rolldown = {
            "id": "B", "description": "1× $42P Jan 15 '27",
            "instruction": {"sell_strike": 42.0,
                            "sell_expiration": "2027-01-15",
                            "sell_mid": 9.80, "sell_bid": 9.60,
                            "sell_ask": 10.00},
            "netDollars": 120.0, "dteExtension": 28,
        }
        items = render_action_list(
            [], [_iren_review(roll_candidates=[rolldown])], [], None,
            _iren_snapshot("2026-08-27"), date_str=TODAY)
        text = "\n".join(items)
        assert "CLOSE BEFORE EARNINGS" in text
        assert "TAKE PROFIT VIA ROLL-DOWN" not in text
        assert one_voice_violations(items) == []

    def test_legacy_gtc_when_earnings_clear(self):
        """Earnings 63d away (> the 30d precondition) → the legacy
        HOLD — GTC AT 50% card still fires, GTC price $9.32 (entry $18.64
        × 0.5), and the ROLL_DONT_CLOSE verdict renders as SUBORDINATE
        context — never the observed full-strength
        '**⚖️ Verdict: ROLL, don't close**' second voice."""
        items = render_action_list(
            [], [_iren_review()], [], None,
            _iren_snapshot("2026-10-15"), date_str=TODAY)
        text = "\n".join(items)
        assert "HOLD — GTC AT 50%" in text
        assert "$9.32" in text
        assert "CLOSE BEFORE EARNINGS" not in text
        # One voice: the verdict is context, not a second recommendation.
        assert "**⚖️ Verdict: ROLL, don't close**" not in text
        assert "⚖️ Context (verdict engine" in text
        assert one_voice_violations(items) == []

    def test_gtc_allowed_when_earnings_after_expiry(self):
        """Earnings 25d away but the contract expires in 24d — the position
        never holds through the print, so the GTC squeeze is legitimate."""
        rev = _iren_review(days_to_expiry=24, delta=-0.44)
        items = render_action_list(
            [], [rev], [], None,
            _iren_snapshot("2026-09-07"), date_str=TODAY)  # d2e = 25 > dte
        text = "\n".join(items)
        assert "CLOSE BEFORE EARNINGS" not in text
        assert "HOLD — GTC AT 50%" in text
        assert one_voice_violations(items) == []

    def test_no_earnings_data_fails_open_to_gtc(self):
        """Unknown earnings date never blocks (fail-open, rule #10 spirit —
        but for a HOLD the safe default is the legacy GTC, not a fabricated
        close)."""
        items = render_action_list(
            [], [_iren_review()], [], None,
            _iren_snapshot(None), date_str=TODAY)
        text = "\n".join(items)
        assert "HOLD — GTC AT 50%" in text
        assert "CLOSE BEFORE EARNINGS" not in text

    def test_observed_iren_card_flagged_by_sweeper(self):
        """The sweeper now catches the OBSERVED card shape verbatim: a HOLD
        headline paired with a full-strength contradicting verdict."""
        observed = [
            "4. **HOLD — GTC AT 50%** IREN_PUT_47_20261218 — +44% captured; "
            "place a GTC buy-to-close at the 50%-capture price $9.32  · "
            "RSI 54",
            "   - **Why:** the high-extrinsic exit is expensive today "
            "(closing pays $1,042 of extrinsic — IV elevated) ...",
            "   - **⚖️ Verdict: ROLL, don't close** — closing pays $1,042 "
            "of panic premium at RVrank 99 (realized-vol proxy); a "
            "roll-down-and-out (see ROLL ANALYSIS) swaps inflated premium "
            "for inflated premium instead.",
        ]
        hits = one_voice_violations(observed)
        assert len(hits) == 1 and "HOLD — GTC AT 50%" in hits[0]

    def test_context_marked_verdict_is_not_a_violation(self):
        """A verdict rendered via the marked-context form under a HOLD
        headline is reconciled, not a violation."""
        card = [
            "4. **HOLD — GTC AT 50%** IREN_PUT_47_20261218 — +44% captured",
            "   - ⚖️ Context (verdict engine, subordinate to the headline): "
            "ROLL, don't close — closing pays $1,042 of panic premium at "
            "RVrank 99 → resolved to HOLD — GTC AT 50%: no credit-positive "
            "roll-down inside the 120d tenor cap is priced this cycle",
        ]
        assert one_voice_violations(card) == []


# ═══════════════════════════════════════════════════════════════════════════
# BUG 2 — roll yield: premium numerator must match the window denominator
# ═══════════════════════════════════════════════════════════════════════════

class TestRollYieldWindowMatch:
    # The observed NOK numbers: 10× $11P, new-leg premium $1.98/share,
    # net credit +$50, spot $10.46, current DTE 127, extension +28d
    # (Fri Dec 18 '26 → Fri Jan 15 '27) → full new-leg DTE 155.
    def _nok_yield(self):
        return compute_roll_yield(
            new_premium=1.98, new_strike=11.0, new_dte=155, contracts=10,
            spot=10.46, net_credit_dollars=50.0,
            position_value=10_460.0, old_strike=11.0, option_type="PUT",
            extension_days=28,
        )

    def test_new_leg_annualizes_over_full_leg_never_extension(self):
        """Observed: 'Yield: **234.1%** ann. on new collateral ($11,000)' —
        the full $1,980 of 155d premium annualized over only 28d. The honest
        figure is 18% × 365/155 ≈ 42.4% ann."""
        r = self._nok_yield()
        y = r["all_yields"]
        assert y["new_leg_yield_ann_pct"] == pytest.approx(42.4, abs=0.2)
        assert y["new_leg_yield_ann_pct"] < 100  # never the 234% figure
        assert y["new_leg_window_days"] == 155

    def test_net_cash_annualizes_over_extension(self):
        """The incremental +$50 credit buys the incremental +28 days —
        ≈ +6.2% ann. on the position (the defensible figure the observed
        line showed beside the inflated one)."""
        r = self._nok_yield()
        y = r["all_yields"]
        assert y["net_cash_yield_ann_pct"] == pytest.approx(6.2, abs=0.3)
        assert y["net_cash_window_days"] == 28

    def test_format_labels_each_window(self):
        """The rendered line labels which figure covers which window, so
        the two annualizations can never be conflated again."""
        line = format_yield_line(self._nok_yield())
        assert "234" not in line
        assert "over the full 155d new leg" in line
        assert "over the +28d extension" in line
        assert "42.4%" in line

    def test_legacy_dict_without_windows_renders_unlabeled(self):
        """Backward compatibility: a legacy yield dict (no window keys)
        renders the legacy unlabeled line unchanged."""
        r = self._nok_yield()
        r["all_yields"].pop("new_leg_window_days")
        r["all_yields"].pop("net_cash_window_days")
        line = format_yield_line(r)
        assert "over the full" not in line and "extension" not in line
        assert "ann. on new collateral" in line

    def test_rendered_roll_ticket_uses_full_leg_window(self):
        """Sibling sweep at the render layer (block #3): an ITM short-put
        calendar roll renders its Yield line annualized over the FULL new
        leg (labeled), never the extension-only inflation."""
        rev = {
            "contract": "NOK_PUT_11_20261218",
            "underlying": "NOK", "type": "PUT", "qty": -10,
            "strike": 11.0, "expiration": "2026-12-18",
            "entry_price": 2.50, "current_mid": 1.86,
            "days_to_expiry": 127, "delta": -0.44,
            "recommendation": "ROLL_OUT",
            "matrix_cell_id": "PUT_NORMAL_ITM_NEUTRAL_ROLL_OUT",
            "roll_candidates": [
                {"id": "A", "description": "HOLD", "instruction": None,
                 "netDollars": 0, "dteExtension": 0},
                {"id": "B", "description": "same-strike calendar",
                 "instruction": {"sell_strike": 11.0,
                                 "sell_expiration": "2027-01-15",
                                 "sell_mid": 1.98, "sell_bid": 1.95,
                                 "sell_ask": 2.00},
                 "netDollars": 120.0, "dteExtension": 28},
            ],
        }
        snap = _snapshot(quotes={"NOK": {"last": 10.46}},
                         chains={"NOK_2026-12-18": {"puts": [
                             {"strike": 11.0, "bid": 1.84, "ask": 1.88}]}},
                         iv_ranks={"NOK": 77})
        items = render_action_list([], [rev], [], None, snap,
                                   date_str=TODAY)
        text = "\n".join(items)
        yield_lines = [ln for ln in items if "ann. on new collateral" in ln]
        assert yield_lines, f"roll yield line missing:\n{text}"
        line = yield_lines[0]
        assert "234" not in line
        # Full new-leg window: 2027-01-15 − 2026-08-13 = 155d.
        assert "over the full 155d new leg" in line
        assert "over the +28d extension" in line


# ═══════════════════════════════════════════════════════════════════════════
# BUG 3 — daily premium line and cumulative buckets must reconcile
# ═══════════════════════════════════════════════════════════════════════════

def _eq(sym, qty, price, basis=None):
    return {"symbol": sym, "assetType": "EQUITY", "qty": qty, "price": price,
            "costBasis": basis if basis is not None else price}


def _short_put(sym, strike, exp, qty, mid, premium):
    return {"symbol": sym, "assetType": "OPTION", "underlying":
            sym.split("_")[0], "type": "PUT", "strike": strike,
            "expiration": exp, "qty": qty, "currentMid": mid,
            "marketValue": mid * 100.0 * qty, "premiumReceived": premium,
            "positionType": "SHORT"}


class TestAttributionReconciliation:
    def _write(self, root, day, nlv, cash, positions):
        d = root / day
        d.mkdir(parents=True, exist_ok=True)
        (d / "positions.json").write_text(json.dumps(positions))
        (d / "balance.json").write_text(json.dumps(
            {"cash": cash, "longMarketValue": nlv - cash,
             "accountValue": nlv}))

    def _three_day_root(self, tmp_path):
        """Day 1: equity only. Day 2: a short put opened intraday (premium
        $200 banked, captured by day 2's snapshot — the SOXL/GOOG shape).
        Day 3: the put is still held, mark decayed."""
        eq = _eq("NVDA", 100, 900.0, basis=800.0)
        put_d2 = _short_put("SOXL_PUT_80_20261120", 80.0, "2026-11-20",
                            -1, 2.00, 2.00)
        put_d3 = _short_put("SOXL_PUT_80_20261120", 80.0, "2026-11-20",
                            -1, 1.50, 2.00)
        self._write(tmp_path, "2026-08-11", 100_000, 10_000, [eq])
        self._write(tmp_path, "2026-08-12", 100_200, 10_200, [eq, put_d2])
        self._write(tmp_path, "2026-08-13", 100_250, 10_200, [eq, put_d3])
        return tmp_path

    def test_daily_zero_is_honest_and_cumulative_reconciles(self, tmp_path):
        """Observed: '**Since last snapshot:** … premium +$0' while the
        cumulative 'Option premium (net)' absorbed the new opens' premium
        across one day. With chained periods, cum(as of D) − cum(as of D−1)
        == daily(D) for the premium bucket, by construction."""
        root = self._three_day_root(tmp_path)
        rep_d3 = pa.build_attribution_report(root, as_of="2026-08-13")
        rep_d2 = pa.build_attribution_report(root, as_of="2026-08-12")
        daily = rep_d3.period("daily")
        cum_d3 = rep_d3.period("since_inception")
        cum_d2 = rep_d2.period("since_inception")
        assert daily is not None and cum_d3 is not None and cum_d2 is not None
        # The open was captured by the 08-12 snapshot → today's window
        # genuinely banked $0 of new premium…
        assert daily.buckets["option_premium_net"] == pytest.approx(0.0)
        # …and the cumulative absorbed it YESTERDAY, so today's cumulative
        # delta matches today's daily line exactly (one source of truth).
        assert cum_d2.buckets["option_premium_net"] == pytest.approx(200.0)
        assert (cum_d3.buckets["option_premium_net"]
                - cum_d2.buckets["option_premium_net"]
                ) == pytest.approx(daily.buckets["option_premium_net"])
        # The prior-window context travels with the daily period so the
        # renderer can label the timing honestly.
        assert daily.prior_window.get("option_premium_net") == pytest.approx(200.0)
        assert daily.prior_window.get("start_date") == "2026-08-11"
        assert daily.prior_window.get("end_date") == "2026-08-12"

    def test_mtm_decay_lands_in_option_mtm_not_frozen_at_zero(self, tmp_path):
        """Observed: 'Option mark-to-market delta: +$0' since inception —
        endpoint diffs put every opened-inside-period contract 100% in the
        premium bucket forever. Chained buckets accrue the held-contract
        decay into option_mtm ($2.00 → $1.50 short mark = +$50)."""
        root = self._three_day_root(tmp_path)
        cum = pa.build_attribution_report(
            root, as_of="2026-08-13").period("since_inception")
        assert cum.buckets["option_mtm"] == pytest.approx(50.0)

    def test_intra_period_round_trip_no_longer_vanishes(self, tmp_path):
        """A contract opened AND closed inside the period is absent from
        both endpoints — the old endpoint diff dropped its premium AND its
        buyback from the cumulative entirely (38 August round-trip closes
        contributed nothing to 'Option premium (net)'). Chained buckets
        keep it: +$200 premium − $180 buyback = +$20 net."""
        eq = _eq("NVDA", 100, 900.0)
        put_open = _short_put("RDDT_PUT_140_20260911", 140.0, "2026-09-11",
                              -1, 1.80, 2.00)
        self._write(tmp_path, "2026-08-11", 100_000, 10_000, [eq])
        self._write(tmp_path, "2026-08-12", 100_100, 10_200, [eq, put_open])
        self._write(tmp_path, "2026-08-13", 100_120, 10_020, [eq])
        cum = pa.build_attribution_report(
            tmp_path, as_of="2026-08-13").period("since_inception")
        assert cum.buckets["option_premium_net"] == pytest.approx(20.0)

    def test_renderer_labels_window_and_timing_artifact(self):
        """Observed line rendered a bare false-looking zero: '**Since last
        snapshot:** NLV +$12,607 · equity MTM +$12,119 · option MTM +$633 ·
        premium +$0'. The line now names the measured window, and when the
        prior window banked material premium the honest timing note renders."""
        daily = {
            "name": "daily", "start_date": "2026-08-12",
            "end_date": "2026-08-13", "nlv_change": 12_607.0,
            "buckets": {"unrealized_equity": 12_119.0, "option_mtm": 633.0,
                        "option_premium_net": 0.0},
            "unattributed": 0.0, "cash_drag": {},
            "prior_window": {"start_date": "2026-08-11",
                             "end_date": "2026-08-12",
                             "option_premium_net": 11_997.0},
        }
        main = {
            "name": "since_inception", "start_date": "2026-05-09",
            "end_date": "2026-08-13", "nlv_start": 1_043_276.0,
            "nlv_end": 1_109_858.0, "nlv_change": 66_582.0,
            "buckets": {"option_premium_net": 39_858.0}, "unattributed": 0.0,
            "cash_drag": {},
        }
        attribution = {"status": "ok", "periods": [main, daily], "note": ""}
        text = "\n".join(render_benchmark_panel(None, attribution))
        assert "Since last snapshot (2026-08-12 → 2026-08-13):" in text
        assert "counted in the prior window's +$11,997" in text
        assert "intraday rerun" in text

    def test_renderer_no_timing_note_when_premium_measured(self):
        """A genuinely measured nonzero daily premium (or a quiet prior
        window) renders no timing note — the label only fires on the
        zero-after-material-prior-window shape."""
        daily = {
            "name": "daily", "start_date": "2026-08-12",
            "end_date": "2026-08-13", "nlv_change": 216.0,
            "buckets": {"unrealized_equity": -2_889.0, "option_mtm": 604.0,
                        "option_premium_net": 11_997.0},
            "unattributed": 0.0, "cash_drag": {},
            "prior_window": {"start_date": "2026-08-11",
                             "end_date": "2026-08-12",
                             "option_premium_net": 970.0},
        }
        main = {
            "name": "since_inception", "start_date": "2026-05-09",
            "end_date": "2026-08-13", "nlv_change": 66_582.0,
            "buckets": {"option_premium_net": 39_858.0}, "unattributed": 0.0,
            "cash_drag": {},
        }
        attribution = {"status": "ok", "periods": [main, daily], "note": ""}
        text = "\n".join(render_benchmark_panel(None, attribution))
        assert "premium +$11,997" in text
        assert "intraday rerun" not in text

    def test_chained_periods_match_endpoint_for_simple_held_book(self, tmp_path):
        """Sanity: for a plain held-throughout equity book, chained sums
        telescope to the endpoint numbers — legacy values unchanged."""
        self._write(tmp_path, "2026-08-11", 100_000, 10_000,
                    [_eq("NVDA", 100, 900.0, basis=800.0)])
        self._write(tmp_path, "2026-08-12", 100_200, 10_000,
                    [_eq("NVDA", 100, 902.0, basis=800.0)])
        self._write(tmp_path, "2026-08-13", 105_000, 10_000,
                    [_eq("NVDA", 100, 950.0, basis=800.0)])
        cum = pa.build_attribution_report(
            tmp_path, as_of="2026-08-13").period("since_inception")
        assert cum.buckets["unrealized_equity"] == pytest.approx(5_000.0)
        assert cum.nlv_change == pytest.approx(5_000.0)


# ═══════════════════════════════════════════════════════════════════════════
# BUG 4 — CC-close Gain line: no unconditional "fresh covered-call premium"
# ═══════════════════════════════════════════════════════════════════════════

class TestCcCloseGainLineHonesty:
    def _smh_review(self):
        """SMH-shaped: $710C Nov 20 '26, +31% captured ($622), 99 DTE —
        the observed CLOSE card."""
        return {
            "contract": "SMH_CALL_710_20261120",
            "underlying": "SMH", "type": "CALL", "qty": -1,
            "strike": 710.0, "expiration": "2026-11-20",
            "entry_price": 20.06, "current_mid": 13.84,
            "days_to_expiry": 99,
            "recommendation": "CLOSE_FOR_PROFIT",
            "matrix_cell_id": "CALL_NORMAL_OTM_TAKEPROFIT",
        }

    def _snap(self, rsi):
        tech = {"SMH": {"rsi_14": rsi}} if rsi is not None else {}
        return _snapshot(quotes={"SMH": {"last": 590.35}}, technicals=tech)

    def test_gain_line_gated_when_rsi_mid_band(self):
        """Observed: '**Gain:** Locks $+622 profit and unlocks 100×1 shares
        (notional $71,000) for fresh covered-call premium; …' on a card
        whose headline read 'RSI 54 🟡 mid-range' — the briefing's own RSI
        discipline demotes a fresh CC below 60 to wait-for-strength. The
        Gain line now carries the measured, config-derived condition."""
        items = render_action_list([], [self._smh_review()], [], None,
                                   self._snap(54.0), date_str=TODAY)
        gain = [ln for ln in items if "**Gain:**" in ln]
        assert gain, "CLOSE card Gain line missing"
        assert "rewrite gated today (RSI 54 mid-range" in gain[0]
        assert "RSI ≥ 60" in gain[0]

    def test_gain_line_gated_when_rsi_oversold(self):
        """RSI below the hard block (35) → the gate note names the measured
        oversold state."""
        items = render_action_list([], [self._smh_review()], [], None,
                                   self._snap(30.0), date_str=TODAY)
        gain = [ln for ln in items if "**Gain:**" in ln]
        assert gain and "rewrite gated today (RSI 30 oversold" in gain[0]

    def test_gain_line_clean_when_rsi_strong(self):
        """RSI 65 (≥ 60 strength floor) — the rewrite is genuinely
        available; legacy label unchanged."""
        items = render_action_list([], [self._smh_review()], [], None,
                                   self._snap(65.0), date_str=TODAY)
        gain = [ln for ln in items if "**Gain:**" in ln]
        assert gain and "for fresh covered-call premium" in gain[0]
        assert "rewrite gated" not in gain[0]

    def test_gain_line_unknown_rsi_never_fabricates_gate(self):
        """RSI unavailable → no gate note (rule #19: never fabricate a
        measured condition)."""
        items = render_action_list([], [self._smh_review()], [], None,
                                   self._snap(None), date_str=TODAY)
        gain = [ln for ln in items if "**Gain:**" in ln]
        assert gain and "rewrite gated" not in gain[0]
