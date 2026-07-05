"""Tests for analysis/pnl_attribution.py (task #16).

Pins: realized vs unrealized categorization, option premium inference
(open / buyback / expiry), assignment detection, hedge bucketing, the
interest/dividends cash residual, the balance-vs-positions unattributed
residual, cash-drag math, and fail-open behavior.
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import pnl_attribution as pa  # noqa: E402


def _eq(sym, qty, price, basis=None, **kw):
    p = {"symbol": sym, "assetType": "EQUITY", "qty": float(qty),
         "price": float(price)}
    if basis is not None:
        p["costBasis"] = float(basis)
    p.update(kw)
    return p


def _opt(contract, underlying, otype, strike, exp, qty, mid,
         premium=None, **kw):
    p = {"symbol": contract, "assetType": "OPTION", "underlying": underlying,
         "type": otype, "strike": float(strike), "expiration": exp,
         "qty": float(qty), "currentMid": float(mid),
         "marketValue": float(mid) * 100.0 * float(qty)}
    if premium is not None:
        p["premiumReceived"] = float(premium)
    p.update(kw)
    return p


def _snap(day, positions, cash, extra_mv=None):
    eq_mv = sum(p["qty"] * p["price"] for p in positions
                if p.get("assetType") == "EQUITY")
    if extra_mv is not None:
        eq_mv = extra_mv
    return {
        "date": day,
        "positions": positions,
        "balance": {"cash": float(cash),
                    "longMarketValue": round(eq_mv, 2),
                    "accountValue": round(eq_mv + cash, 2)},
    }


class TestEquityBuckets:
    def test_unrealized_only_price_move(self):
        prior = _snap("2026-07-01", [_eq("NVDA", 100, 200.0, basis=150.0)], 10_000)
        cur = _snap("2026-07-02", [_eq("NVDA", 100, 210.0, basis=150.0)], 10_000)
        r = pa.compute_attribution(cur, prior)
        assert r.buckets["unrealized_equity"] == pytest.approx(1_000.0)
        assert r.buckets["realized_equity"] == pytest.approx(0.0)
        assert r.nlv_change == pytest.approx(1_000.0)
        assert r.unattributed == pytest.approx(0.0, abs=0.02)

    def test_realized_on_partial_sale(self):
        """Sold 50 sh at ~$200 vs $150 basis → realized ≈ +$2,500."""
        prior = _snap("2026-07-01", [_eq("NVDA", 100, 200.0, basis=150.0)], 10_000)
        cur = _snap("2026-07-02", [_eq("NVDA", 50, 200.0, basis=150.0)],
                    10_000 + 50 * 200.0)
        r = pa.compute_attribution(cur, prior)
        assert r.buckets["realized_equity"] == pytest.approx(2_500.0)
        assert r.buckets["unrealized_equity"] == pytest.approx(0.0)
        assert r.nlv_change == pytest.approx(0.0)  # sale just moves MV → cash
        assert r.buckets["interest_dividends"] == pytest.approx(0.0, abs=0.02)

    def test_realized_on_full_exit(self):
        prior = _snap("2026-07-01", [_eq("SOFI", 200, 16.0, basis=12.0)], 5_000)
        cur = _snap("2026-07-02", [], 5_000 + 200 * 16.0)
        r = pa.compute_attribution(cur, prior)
        assert r.buckets["realized_equity"] == pytest.approx(800.0)
        assert any("full exit" in n for n in r.notes)

    def test_new_buy_no_phantom_pnl(self):
        """Buying at cost creates no P/L; cash moves to MV."""
        prior = _snap("2026-07-01", [], 50_000)
        cur = _snap("2026-07-02", [_eq("AMZN", 100, 240.0, basis=240.0)],
                    50_000 - 24_000)
        r = pa.compute_attribution(cur, prior)
        assert r.buckets["unrealized_equity"] == pytest.approx(0.0)
        assert r.buckets["realized_equity"] == pytest.approx(0.0)
        assert r.buckets["interest_dividends"] == pytest.approx(0.0, abs=0.02)
        assert r.nlv_change == pytest.approx(0.0)


class TestOptionBuckets:
    def test_new_short_put_premium_collected(self):
        prem = 6.0
        prior = _snap("2026-07-01", [], 100_000)
        cur = _snap("2026-07-02",
                    [_opt("NVDA_PUT_180_20260918", "NVDA", "PUT", 180,
                          "2026-09-18", -1, 6.0, premium=prem)],
                    100_000 + prem * 100)
        r = pa.compute_attribution(cur, prior)
        assert r.buckets["option_premium_net"] == pytest.approx(600.0)
        assert r.buckets["interest_dividends"] == pytest.approx(0.0, abs=0.02)

    def test_held_short_option_mtm(self):
        """Short put mid 6.0 → 3.2: liability shrank, +$280 mark gain."""
        o1 = _opt("NVDA_PUT_180_20260918", "NVDA", "PUT", 180, "2026-09-18",
                  -1, 6.0, premium=6.0, positionType="SHORT")
        o2 = _opt("NVDA_PUT_180_20260918", "NVDA", "PUT", 180, "2026-09-18",
                  -1, 3.2, premium=6.0, positionType="SHORT")
        r = pa.compute_attribution(_snap("2026-07-02", [o2], 100_000),
                                   _snap("2026-07-01", [o1], 100_000))
        assert r.buckets["option_mtm"] == pytest.approx(280.0)
        assert r.buckets["hedge_pnl"] == pytest.approx(0.0)

    def test_buyback_before_expiry(self):
        """Short put gone before expiration → buyback at prior mark."""
        o = _opt("AMD_PUT_420_20261218", "AMD", "PUT", 420, "2026-12-18",
                 -1, 50.0, premium=75.0, positionType="SHORT")
        prior = _snap("2026-07-01", [o], 100_000)
        cur = _snap("2026-07-02", [], 100_000 - 50.0 * 100)
        r = pa.compute_attribution(cur, prior)
        assert r.buckets["option_premium_net"] == pytest.approx(-5_000.0)
        assert any("buyback" in n for n in r.notes)
        assert r.buckets["interest_dividends"] == pytest.approx(0.0, abs=0.02)

    def test_expired_worthless(self):
        """Short put whose expiration passed → remaining mark earned free."""
        o = _opt("MU_PUT_100_20260702", "MU", "PUT", 100, "2026-07-02",
                 -2, 0.5, premium=3.0, positionType="SHORT")
        prior = _snap("2026-07-01", [o], 100_000)
        cur = _snap("2026-07-06", [], 100_000)
        r = pa.compute_attribution(cur, prior)
        assert r.buckets["option_premium_net"] == pytest.approx(100.0)
        assert any("expired worthless" in n for n in r.notes)


class TestHedgeBucket:
    def test_long_put_mtm_goes_to_hedge(self):
        h1 = _opt("META_PUT_570_20261218", "META", "PUT", 570, "2026-12-18",
                  1, 20.0, positionType="LONG")
        h2 = _opt("META_PUT_570_20261218", "META", "PUT", 570, "2026-12-18",
                  1, 25.0, positionType="LONG")
        r = pa.compute_attribution(_snap("2026-07-02", [h2], 100_000),
                                   _snap("2026-07-01", [h1], 100_000))
        assert r.buckets["hedge_pnl"] == pytest.approx(500.0)
        assert r.buckets["option_mtm"] == pytest.approx(0.0)

    def test_explicit_is_hedge_flag_wins(self):
        o1 = _opt("X_PUT_10_20261218", "X", "PUT", 10, "2026-12-18", -1, 1.0,
                  premium=1.0, is_hedge=True)
        o2 = _opt("X_PUT_10_20261218", "X", "PUT", 10, "2026-12-18", -1, 0.5,
                  premium=1.0, is_hedge=True)
        r = pa.compute_attribution(_snap("2026-07-02", [o2], 0),
                                   _snap("2026-07-01", [o1], 0))
        assert r.buckets["hedge_pnl"] == pytest.approx(50.0)
        assert r.buckets["option_mtm"] == pytest.approx(0.0)

    def test_new_hedge_purchase_cash_and_mark(self):
        """Buy a protective put for $20, marked at $18 → hedge P/L -$200."""
        h = _opt("SPY_PUT_600_20261218", "SPY", "PUT", 600, "2026-12-18",
                 1, 18.0, premium=20.0, positionType="LONG")
        prior = _snap("2026-07-01", [], 100_000)
        cur = _snap("2026-07-02", [h], 100_000 - 20.0 * 100)
        r = pa.compute_attribution(cur, prior)
        assert r.buckets["hedge_pnl"] == pytest.approx(-200.0)
        assert r.buckets["interest_dividends"] == pytest.approx(0.0, abs=0.02)


class TestAssignment:
    def test_put_assignment_detected(self):
        """Short put expired ITM; 100 sh appear at strike. Assignment P/L =
        shares × (spot − strike); premium kept."""
        o = _opt("NVDA_PUT_180_20260702", "NVDA", "PUT", 180, "2026-07-02",
                 -1, 8.0, premium=6.0, positionType="SHORT")
        prior = _snap("2026-07-01", [o], 100_000)
        cur = _snap("2026-07-06",
                    [_eq("NVDA", 100, 172.0, basis=174.0)],
                    100_000 - 180.0 * 100)
        r = pa.compute_attribution(cur, prior)
        assert r.buckets["assignment_pnl"] == pytest.approx(100 * (172.0 - 180.0))
        assert r.buckets["option_premium_net"] == pytest.approx(600.0)
        assert any("assignment inferred" in n for n in r.notes)
        # Cash outflow at strike is attributed to the trade, not to
        # interest/dividends.
        assert r.buckets["interest_dividends"] == pytest.approx(0.0, abs=0.02)

    def test_early_close_not_misread_as_assignment(self):
        """Put closed before expiry while shares were bought separately →
        buyback, not assignment."""
        o = _opt("MU_PUT_100_20261218", "MU", "PUT", 100, "2026-12-18",
                 -1, 5.0, premium=6.0, positionType="SHORT")
        prior = _snap("2026-07-01", [o], 100_000)
        cur = _snap("2026-07-02",
                    [_eq("MU", 100, 102.0, basis=102.0)],
                    100_000 - 5.0 * 100 - 102.0 * 100)
        r = pa.compute_attribution(cur, prior)
        assert r.buckets["assignment_pnl"] == pytest.approx(0.0)
        assert r.buckets["option_premium_net"] == pytest.approx(-500.0)


class TestResidualAndFailOpen:
    def test_unattributed_flags_balance_positions_disagreement(self):
        """Balance NLV moved but positions/cash didn't → residual."""
        prior = _snap("2026-07-01", [_eq("NVDA", 100, 200.0)], 10_000)
        cur = _snap("2026-07-02", [_eq("NVDA", 100, 200.0)], 10_000)
        cur["balance"]["accountValue"] += 5_000  # balance drifted
        r = pa.compute_attribution(cur, prior)
        assert r.unattributed == pytest.approx(5_000.0)

    def test_dividend_lands_in_interest_bucket(self):
        prior = _snap("2026-07-01", [_eq("KO", 100, 60.0)], 10_000)
        cur = _snap("2026-07-02", [_eq("KO", 100, 60.0)], 10_048.5)
        r = pa.compute_attribution(cur, prior)
        assert r.buckets["interest_dividends"] == pytest.approx(48.5)
        assert r.nlv_change == pytest.approx(48.5)

    def test_garbage_never_raises(self):
        r = pa.compute_attribution({"bogus": True}, None)
        assert isinstance(r, pa.PeriodAttribution)
        r2 = pa.compute_attribution(
            {"date": "x", "positions": [{"symbol": None}], "balance": "bad"},
            {"date": None, "positions": "nope", "balance": {}})
        assert isinstance(r2, pa.PeriodAttribution)

    def test_to_dict_json_serializable(self):
        prior = _snap("2026-07-01", [_eq("NVDA", 100, 200.0)], 10_000)
        cur = _snap("2026-07-02", [_eq("NVDA", 100, 210.0)], 10_000)
        json.dumps(pa.compute_attribution(cur, prior).to_dict())


class TestCashDrag:
    def test_put_collateral_sums_short_puts_only(self):
        positions = [
            _opt("A_PUT_100_20261218", "A", "PUT", 100, "2026-12-18", -2, 1.0),
            _opt("B_CALL_50_20261218", "B", "CALL", 50, "2026-12-18", -1, 1.0),
            _opt("C_PUT_200_20261218", "C", "PUT", 200, "2026-12-18", 1, 1.0),
            _eq("D", 100, 10.0),
        ]
        # 2 × $100 × 100 = $20,000; the short call and LONG put don't count.
        assert pa.put_collateral(positions) == pytest.approx(20_000.0)

    def test_cash_drag_math(self, tmp_path):
        """$20K avg collateral × SPY +5% → $1,000 opportunity cost."""
        put = _opt("A_PUT_100_20261218", "A", "PUT", 100, "2026-12-18", -2, 1.0,
                   premium=1.0, positionType="SHORT")
        for day in ("2026-06-01", "2026-06-15", "2026-07-01"):
            d = tmp_path / day
            d.mkdir()
            (d / "positions.json").write_text(json.dumps([put]))
            (d / "balance.json").write_text(json.dumps(
                {"cash": 50_000, "longMarketValue": 0, "accountValue": 50_000}))
        spy = {date(2026, 6, 1): 500.0, date(2026, 7, 1): 525.0}
        drag = pa._cash_drag(tmp_path,
                             [date(2026, 6, 1), date(2026, 6, 15), date(2026, 7, 1)],
                             spy, date(2026, 6, 1), date(2026, 7, 1))
        assert drag["avg_put_collateral"] == pytest.approx(20_000.0)
        assert drag["spy_return_pct"] == pytest.approx(5.0)
        assert drag["opportunity_cost"] == pytest.approx(1_000.0)

    def test_cash_drag_no_spy_data(self, tmp_path):
        d = tmp_path / "2026-06-01"
        d.mkdir()
        (d / "positions.json").write_text("[]")
        (d / "balance.json").write_text(json.dumps({"cash": 1, "accountValue": 1}))
        drag = pa._cash_drag(tmp_path, [date(2026, 6, 1)], {},
                             date(2026, 6, 1), date(2026, 7, 1))
        assert drag["opportunity_cost"] is None


class TestReportBuilder:
    def _write_snap(self, root, day, nlv, cash, positions):
        d = root / day
        d.mkdir(parents=True, exist_ok=True)
        (d / "positions.json").write_text(json.dumps(positions))
        (d / "balance.json").write_text(json.dumps(
            {"cash": cash, "longMarketValue": nlv - cash, "accountValue": nlv}))

    def test_periods_built(self, tmp_path):
        eq0 = _eq("NVDA", 100, 900.0, basis=800.0)
        eq1 = _eq("NVDA", 100, 950.0, basis=800.0)
        self._write_snap(tmp_path, "2026-06-01", 100_000, 10_000, [eq0])
        self._write_snap(tmp_path, "2026-06-30", 102_000, 10_000, [eq0])
        self._write_snap(tmp_path, "2026-07-02", 104_000, 10_000, [eq0])
        self._write_snap(tmp_path, "2026-07-03", 107_000, 10_000, [eq1])
        rep = pa.build_attribution_report(tmp_path, as_of="2026-07-03")
        assert rep.status == "ok"
        names = {p.name for p in rep.periods}
        assert "daily" in names
        assert "30d" in names
        assert "MTD" in names
        daily = rep.period("daily")
        assert daily.start_date == date(2026, 7, 2)
        assert daily.buckets["unrealized_equity"] == pytest.approx(5_000.0)

    def test_insufficient_history(self, tmp_path):
        self._write_snap(tmp_path, "2026-07-03", 100_000, 10_000, [])
        rep = pa.build_attribution_report(tmp_path, as_of="2026-07-03")
        assert rep.status == "insufficient_history"

    def test_missing_root_fails_open(self, tmp_path):
        rep = pa.build_attribution_report(tmp_path / "nope", as_of="2026-07-03")
        assert rep.status in ("insufficient_history", "unavailable")
