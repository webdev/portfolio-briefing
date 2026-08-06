"""Rule-#43 batch — four defects observed in the 2026-08-05 briefing
(~/Documents/briefings/briefing_2026-08-05.md).

Defect 1 — NLV-correction discontinuity poisoned benchmark/attribution:
    "Unattributed (residual): -$65,345 ⚠️ (large residual — balance vs
    positions disagree; investigate)" and an alpha table (30d -1.8%,
    90d/inception -7.2%) mixing pre-correction inflated NLVs (history
    through 2026-08-03) with broker-true NLVs (2026-08-04 onward).

Defect 2 — take-profit closes with NO exit-cost anatomy (silent fail-open):
    "2. **CLOSE** VRT_PUT_280_20270115 — +35% ($+2,593); buy-to-close limit
    $50.24" rendered with no anatomy/verdict lines at all — yesterday the
    SAME position carried "ROLL, don't close — 89% extrinsic".

Defect 3 — header "Action Items: 2" while 3 numbered items rendered (count
    computed before a later composer appended the MSFT call close).

Defect 4 — stale capacity tag inside the PLTR card:
    "⏸ Deferred (capacity gated) — stress coverage 0.18× < 0.50× floor"
    while the run's header measured 0.28× (cached candidate carrying its
    generation-time gate tag).
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.benchmark_tracker import (  # noqa: E402
    balance_nlv,
    balance_option_inclusive,
    effective_rebase_date,
    load_nlv_history,
    load_nlv_history_meta,
    rebase_dates_from_config,
)
from analysis.capacity_gate import retag_capacity_lines  # noqa: E402
from analysis.pnl_attribution import compute_attribution  # noqa: E402
from recompute_nlv_history import migrate  # noqa: E402
from render.panels import (  # noqa: E402
    _exit_cost_anatomy,
    one_voice_violations,
    render_action_list,
    sync_action_item_count,
)

TODAY = "2026-08-05"


# ═══════════════════════════════════════════════════════════════════════════
# Defect 1 — NLV correction migration + option-inclusive attribution
# ═══════════════════════════════════════════════════════════════════════════

OLD_ERA_BALANCE = {
    # 2026-07-22 shape: accountValue == cash + longMarketValue (equities
    # only — short-option marks NOT subtracted → inflated).
    "totalAccountValue": 1020111.32,
    "cash": 41301.05,
    "longMarketValue": 991469.41,
    "accountValue": 1032770.46,
    "asOf": "2026-07-22T22:34:28Z",
}

OLD_ERA_POSITIONS = [
    {"symbol": "AMZN", "assetType": "EQUITY", "qty": 100.0, "price": 277.35,
     "marketValue": 27735.0, "costBasis": 210.75},
    {"symbol": "AMD_PUT_420_20261218", "assetType": "OPTION", "qty": -1.0,
     "marketValue": -94892.5, "currentMid": 948.925},
]

BROKER_TRUE_BALANCE = {
    "totalAccountValue": 1085222.21, "cash": 75767.28,
    "longMarketValue": 1077017.26, "accountValue": 1085222.21,
    "optionMarketValue": -67573.5,
    "nlv_reconciliation": {"broker_nlv": 1085222.21, "using": "broker"},
}


def _write_snapshot(root: Path, day: str, balance: dict, positions: list):
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    (d / "balance.json").write_text(json.dumps(balance))
    (d / "positions.json").write_text(json.dumps(positions))
    return d


class TestNlvMigration:
    def test_migration_writes_corrected_alongside_original(self, tmp_path):
        """Observed: "Unattributed (residual): -$65,345 ⚠️" — history through
        2026-08-03 stored accountValue WITHOUT short-option marks. The
        migration must write accountValue_corrected = accountValue + signed
        option marks ALONGSIDE the original (never overwriting it), with a
        full nlv_correction audit block."""
        _write_snapshot(tmp_path, "2026-07-22", dict(OLD_ERA_BALANCE),
                        OLD_ERA_POSITIONS)
        summary = migrate(tmp_path, quiet=True)
        assert summary["corrected"] == ["2026-07-22"]
        bal = json.loads((tmp_path / "2026-07-22" / "balance.json").read_text())
        # Original field untouched.
        assert bal["accountValue"] == 1032770.46
        # Corrected = accountValue + signed option marks (-94,892.50).
        assert abs(bal["accountValue_corrected"] - 937877.96) < 0.01
        corr = bal["nlv_correction"]
        assert corr["original"] == 1032770.46
        assert abs(corr["delta"] - (-94892.5)) < 0.01
        assert corr["corrected_at"]
        # Idempotent: a second run leaves it alone.
        summary2 = migrate(tmp_path, quiet=True)
        assert summary2["already_corrected"] == ["2026-07-22"]

    def test_migration_skips_broker_true_and_unmarked(self, tmp_path):
        """Broker-true era (optionMarketValue / nlv_reconciliation present,
        2026-08-04 onward) must be untouched; an old snapshot whose options
        carry NO usable marks (the degenerate 2026-05-08 pull) is skipped —
        fail closed, never a fabricated correction."""
        _write_snapshot(tmp_path, "2026-08-04", dict(BROKER_TRUE_BALANCE), [])
        _write_snapshot(
            tmp_path, "2026-05-08",
            {"cash": 75000.0, "longMarketValue": 56544.0,
             "accountValue": 131544.0},
            [{"symbol": "X_PUT_1_20260101", "assetType": "OPTION",
              "qty": -1.0}])  # no marketValue, no currentMid
        summary = migrate(tmp_path, quiet=True)
        assert summary["broker_true"] == ["2026-08-04"]
        assert summary["skipped"] == ["2026-05-08"]
        bal = json.loads((tmp_path / "2026-08-04" / "balance.json").read_text())
        assert "accountValue_corrected" not in bal

    def test_reader_prefers_corrected_nlv(self, tmp_path):
        """load_nlv_history / balance_nlv prefer accountValue_corrected over
        the inflated original."""
        _write_snapshot(tmp_path, "2026-07-22", dict(OLD_ERA_BALANCE),
                        OLD_ERA_POSITIONS)
        migrate(tmp_path, quiet=True)
        hist = load_nlv_history(tmp_path)
        assert abs(hist[date(2026, 7, 22)] - 937877.96) < 0.01
        bal = json.loads((tmp_path / "2026-07-22" / "balance.json").read_text())
        assert balance_option_inclusive(bal)
        assert abs(balance_nlv(bal) - 937877.96) < 0.01

    def test_rebase_fires_only_on_uncorrectable_prefix(self, tmp_path):
        """`nlv_rebase_dates: ["2026-08-04"]` is belt-and-suspenders: it must
        fire ONLY when an uncorrectable pre-rebase snapshot remains in the
        history — a fully-corrected series needs no rebase."""
        # Uncorrectable old-era snapshot (options without marks → skip).
        _write_snapshot(
            tmp_path, "2026-08-01",
            {"cash": 100000.0, "longMarketValue": 900000.0,
             "accountValue": 1000000.0},
            [{"symbol": "X_PUT_1_20260101", "assetType": "OPTION",
              "qty": -1.0}])
        _write_snapshot(tmp_path, "2026-08-04", dict(BROKER_TRUE_BALANCE), [])
        migrate(tmp_path, quiet=True)
        hist, uncorrected = load_nlv_history_meta(tmp_path)
        rb_dates = rebase_dates_from_config({"nlv_rebase_dates": ["2026-08-04"]})
        assert effective_rebase_date(hist, uncorrected, rb_dates) == date(2026, 8, 4)
        # Correct the prefix (give the option a mark) → rebase becomes a no-op.
        d = tmp_path / "2026-08-01"
        pos = [{"symbol": "X_PUT_1_20260101", "assetType": "OPTION",
                "qty": -1.0, "marketValue": -5000.0}]
        (d / "positions.json").write_text(json.dumps(pos))
        migrate(tmp_path, quiet=True)
        hist2, uncorrected2 = load_nlv_history_meta(tmp_path)
        assert effective_rebase_date(hist2, uncorrected2, rb_dates) is None


class TestAttributionResidual:
    def _snap(self, day, cash, eq_price, opt_mv, corrected=True):
        eq_mv = eq_price * 100.0
        bal = {"cash": cash, "longMarketValue": eq_mv,
               "accountValue": cash + eq_mv}
        if corrected:
            bal["accountValue_corrected"] = cash + eq_mv + opt_mv
        positions = [
            {"symbol": "AAPL", "assetType": "EQUITY", "qty": 100.0,
             "price": eq_price, "marketValue": eq_mv, "costBasis": 90.0},
            {"symbol": "AAPL_PUT_95_20270115", "assetType": "OPTION",
             "type": "PUT", "qty": -1.0, "strike": 95.0,
             "expiration": "2027-01-15", "marketValue": opt_mv,
             "premiumReceived": 60.0},
        ]
        return {"date": day, "balance": bal, "positions": positions}

    def test_residual_collapses_on_option_inclusive_endpoints(self):
        """Observed: "Unattributed (residual): -$65,345 ⚠️ (large residual —
        balance vs positions disagree; investigate)". With BOTH endpoint
        NLVs option-mark-inclusive, the explained change must include the
        option-mark delta — the residual collapses to ~0."""
        prior = self._snap("2026-08-01", 100000.0, 100.0, -5000.0)
        cur = self._snap("2026-08-05", 100000.0, 110.0, -3000.0)
        pa = compute_attribution(cur, prior, name="period")
        # ΔNLV = (100k + 11k - 3k) - (100k + 10k - 5k) = +3,000
        assert abs(pa.nlv_change - 3000.0) < 0.01
        assert abs(pa.unattributed) < 0.01

    def test_mixed_convention_period_gets_note(self):
        """A period whose START excludes option marks (pre-correction) while
        the END includes them must say so — never a silent residual."""
        prior = self._snap("2026-08-01", 100000.0, 100.0, -5000.0,
                           corrected=False)
        cur = self._snap("2026-08-05", 100000.0, 110.0, -3000.0)
        pa = compute_attribution(cur, prior, name="period")
        assert any("conventions differ" in n for n in pa.notes)


# ═══════════════════════════════════════════════════════════════════════════
# Defect 2 — take-profit closes must never render silent on exit cost
# ═══════════════════════════════════════════════════════════════════════════

def _snapshot(quotes=None, chains=None, iv_ranks=None, earnings=None,
              config=None, prior_verdicts=None):
    snap = {
        "quotes": quotes or {},
        "chains": chains or {},
        "iv_ranks": iv_ranks or {},
        "earnings_calendar": earnings or {},
        "_config": {"core_positions": [], "accounts": [], **(config or {})},
        "balance": {"accountValue": 1_000_000, "cash": 100_000},
        "positions": [],
    }
    if prior_verdicts is not None:
        snap["_prior_exit_verdicts"] = prior_verdicts
    return snap


def _vrt_280p(**over):
    """The observed VRT $280P: spot 280.90 vs strike 280 (0.3% OTM —
    NEAR-MONEY), entry $73.78 / mid $47.85 (+35% captured), DTE 163,
    measured δ -0.39."""
    rev = {
        "contract": "VRT_PUT_280_20270115",
        "underlying": "VRT", "type": "PUT", "qty": -1,
        "strike": 280.0, "expiration": "2027-01-15",
        "entry_price": 73.78, "current_mid": 47.85, "days_to_expiry": 163,
        "delta": -0.3946,
        "recommendation": "HOLD",
        "matrix_cell_id": "PUT_NORMAL_NEAR_ATM_HOLD",
        "roll_candidates": [{"id": "A", "description": "HOLD",
                             "instruction": None, "netDollars": 0,
                             "dteExtension": 0}],
    }
    rev.update(over)
    return rev


VRT_CHAIN = {"VRT_2027-01-15": {"puts": [
    {"strike": 280.0, "bid": 47.10, "ask": 48.60},
    {"strike": 270.0, "bid": 41.80, "ask": 43.05},
]}}


class TestTakeProfitAnatomyFailClosed:
    def test_near_money_close_consults_the_verdict_one_voice(self):
        """Observed: "2. **CLOSE** VRT_PUT_280_20270115 — +35% ($+2,593)"
        with NO anatomy/verdict lines — yesterday the same position read
        "ROLL, don't close — 89% extrinsic". A near-money (0.3% OTM) short
        put with a live chain leg must compute the anatomy; a ROLL_DONT_CLOSE
        verdict resolves one-voice (never a bare CLOSE, never silent)."""
        snap = _snapshot(quotes={"VRT": {"last": 280.90}}, chains=VRT_CHAIN,
                         iv_ranks={"VRT": 89},
                         earnings={"VRT": "2026-10-28"})
        items = render_action_list([], [_vrt_280p()], [], None, snap,
                                   date_str=TODAY)
        text = "\n".join(items)
        assert "ROLL, don't close" in text or "HOLD — GTC AT 50%" in text
        assert "Exit cost anatomy" in text
        assert "**CLOSE** VRT_PUT_280_20270115" not in text
        assert one_voice_violations(items) == []

    def test_near_money_no_chain_quote_fails_closed_with_yesterdays_read(self):
        """When the chain leg is missing this cycle, the CLOSE card must
        carry "⚠ exit-cost anatomy unavailable this cycle (no chain quote) —
        verify the buyback's time-value split at the broker before closing;
        yesterday's read was ROLL, don't close" — never a silent CLOSE."""
        snap = _snapshot(
            quotes={"VRT": {"last": 280.90}}, chains={},
            prior_verdicts={"date": "2026-08-04",
                            "verdicts": {"VRT_PUT_280_20270115":
                                         "ROLL_DONT_CLOSE"}})
        items = render_action_list([], [_vrt_280p()], [], None, snap,
                                   date_str=TODAY)
        text = "\n".join(items)
        assert "**CLOSE** VRT_PUT_280_20270115" in text
        assert "exit-cost anatomy unavailable this cycle (no chain quote)" in text
        assert "verify the buyback's time-value split at the broker" in text
        assert "yesterday's read was ROLL, don't close" in text

    def test_genuinely_otm_winner_says_pure_time_value(self):
        """Genuinely-OTM winners (spot > 3% above strike) may close without
        anatomy but must say "the buyback is pure time value" — never
        silent."""
        rev = _vrt_280p(contract="VRT_PUT_260_20270115", strike=260.0,
                        entry_price=65.77, current_mid=42.43, delta=-0.20)
        snap = _snapshot(quotes={"VRT": {"last": 280.90}}, chains=VRT_CHAIN)
        items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
        text = "\n".join(items)
        assert "**CLOSE** VRT_PUT_260_20270115" in text
        assert "the buyback is pure time value" in text

    def test_short_call_close_never_silent(self):
        """The MSFT $550C close card also rendered with no exit-cost read —
        a measurably-OTM short CALL close carries the OTM pure-time-value
        line (call anatomy isn't modeled, but silence is not an option)."""
        rev = {
            "contract": "MSFT_CALL_550_20260918",
            "underlying": "MSFT", "type": "CALL", "qty": -1,
            "strike": 550.0, "expiration": "2026-09-18",
            "entry_price": 5.30, "current_mid": 3.57, "days_to_expiry": 44,
            "recommendation": "HOLD", "matrix_cell_id": "CALL_NORMAL_OTM_HOLD",
        }
        snap = _snapshot(quotes={"MSFT": {"last": 500.0}})
        items = render_action_list([], [rev], [], None, snap, date_str=TODAY)
        text = "\n".join(items)
        assert "**CLOSE** MSFT_CALL_550_20260918" in text
        assert "the buyback is pure time value" in text

    def test_default_gate_unchanged_for_other_callers(self):
        """Regression guard: without include_near_money, a barely-OTM
        profitable put stays not_eligible — blocks #3/#4
        (verdict-drives-action) keep their existing behavior."""
        snap = _snapshot(quotes={"VRT": {"last": 280.90}}, chains=VRT_CHAIN)
        anatomy, status = _exit_cost_anatomy(_vrt_280p(), snap, [], TODAY)
        assert anatomy is None and status == "not_eligible"
        anatomy2, status2 = _exit_cost_anatomy(_vrt_280p(), snap, [], TODAY,
                                               include_near_money=True)
        assert anatomy2 is not None and status2 == "ok"


# ═══════════════════════════════════════════════════════════════════════════
# Defect 3 — header count == rendered numbered items
# ═══════════════════════════════════════════════════════════════════════════

FIXTURE_MD = """# Daily Briefing — Wednesday, August 5, 2026

**Portfolio NLV:** $1,086,091 | **Cash:** $53,831 (5.0%)
**Action Items:** 2

## Today's Action List — Wednesday, August 5, 2026

1. **CLOSE** MSFT_CALL_550_20260918 — +34% ($+181); buy-to-close limit $3.75
   - **Why:** appended by a later composer AFTER the count was taken.
2. **CLOSE** VRT_PUT_280_20270115 — +35% ($+2,593); buy-to-close limit $50.24
   - **Gain:** Locks $+2,593 profit.
3. **CLOSE** VRT_PUT_270_20270115 — +35% ($+2,334); buy-to-close limit $44.55

## Watch

1. Not an action item — different section, must not be counted.
"""


class TestActionCountSync:
    def test_header_count_recomputed_from_final_list(self):
        """Observed: header "**Action Items:** 2" while 3 numbered items
        rendered (the MSFT call close was appended after the count was
        computed). The sync pass recounts the FINAL composed list."""
        out = sync_action_item_count(FIXTURE_MD)
        assert "**Action Items:** 3" in out
        assert "**Action Items:** 2" not in out

    def test_count_ignores_other_sections(self):
        """Numbered lines outside the Action List section (Watch etc.) must
        not inflate the count."""
        out = sync_action_item_count(FIXTURE_MD)
        assert "**Action Items:** 3" in out  # not 4

    def test_missing_section_leaves_markdown_untouched(self):
        md = "# Daily Briefing\n\n**Action Items:** 2\n\n## Watch\n"
        assert sync_action_item_count(md) == md


# ═══════════════════════════════════════════════════════════════════════════
# Defect 4 — capacity tags re-rendered from the CURRENT run's gate state
# ═══════════════════════════════════════════════════════════════════════════

STALE_TAG_MD = (
    "- ⏸ **CSP — PAID-TO-WAIT (wait)** PLTR — sell $140P exp Fri Sep 04\n"
    "   - **⏸ Deferred (capacity gated) — stress coverage 0.18× < 0.50× "
    "floor; shown for planning, not a green light (rule #41)**\n"
)


class TestCapacityRetag:
    def test_stale_cached_tag_retagged_with_current_ratio(self):
        """Observed: the PLTR card said "⏸ Deferred (capacity gated) — stress
        coverage 0.18× < 0.50× floor" while the run's header measured 0.28×.
        The tag is presentation, not data — re-rendered from the CURRENT
        run's gate state at compose time."""
        out = retag_capacity_lines(
            STALE_TAG_MD, {"stress_coverage": {"ratio": 0.28}}, {})
        assert "stress coverage 0.28× < 0.50× floor" in out
        assert "0.18×" not in out

    def test_gate_open_run_clears_the_stale_tag_honestly(self):
        """When today's coverage clears the floor, a cached DEFERRED tag must
        not survive as-is — it is replaced with an explicit 'gate OPEN this
        run' note (never silently promoted to green-lit)."""
        out = retag_capacity_lines(
            STALE_TAG_MD, {"stress_coverage": {"ratio": 0.62}}, {})
        assert "capacity gate OPEN this run" in out
        assert "0.62× ≥ 0.50× floor" in out
        assert "Deferred (capacity gated)" not in out

    def test_unresolvable_ratio_leaves_markdown_untouched(self):
        """Fail-open (rule #41): no resolvable coverage ratio → never rewrite
        the tag with nothing."""
        assert retag_capacity_lines(STALE_TAG_MD, None, {}) == STALE_TAG_MD
        assert retag_capacity_lines(STALE_TAG_MD, {}, {}) == STALE_TAG_MD
