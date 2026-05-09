"""Tests for wash_sale_check.py — IRS wash-sale rule enforcement."""

import pytest
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from wash_sale_check import (
    record_trade_close,
    is_wash_sale_blocked,
    _load_ledger,
    _save_ledger,
)


@pytest.fixture
def tmp_ledger(tmp_path):
    """Fixture: temporary ledger file for isolation."""
    ledger_file = tmp_path / "wash_sale_ledger.json"
    return str(ledger_file)


class TestRecordTradeClose:
    """Tests for record_trade_close function."""

    def test_record_loss(self, tmp_ledger):
        """Losses are recorded in the ledger."""
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        ledger = _load_ledger(tmp_ledger)
        assert len(ledger["records"]) == 1
        assert ledger["records"][0]["ticker"] == "MU"
        assert ledger["records"][0]["close_date"] == "2026-04-15"
        assert ledger["records"][0]["loss_dollars"] == -340.00

    def test_ignore_profit(self, tmp_ledger):
        """Profitable closes are not recorded."""
        record_trade_close("NVDA", "2026-04-16", 1200.00, ledger_path=tmp_ledger)
        ledger = _load_ledger(tmp_ledger)
        assert len(ledger["records"]) == 0

    def test_ignore_breakeven(self, tmp_ledger):
        """Breakeven closes (P&L == 0) are not recorded."""
        record_trade_close("AAPL", "2026-04-17", 0.0, ledger_path=tmp_ledger)
        ledger = _load_ledger(tmp_ledger)
        assert len(ledger["records"]) == 0

    def test_ticker_uppercase(self, tmp_ledger):
        """Tickers are converted to uppercase."""
        record_trade_close("mu", "2026-04-15", -500.00, ledger_path=tmp_ledger)
        ledger = _load_ledger(tmp_ledger)
        assert ledger["records"][0]["ticker"] == "MU"

    def test_multiple_losses(self, tmp_ledger):
        """Multiple losses can be recorded."""
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        record_trade_close("AAPL", "2026-04-10", -1250.00, ledger_path=tmp_ledger)
        record_trade_close("MU", "2026-04-20", -200.00, ledger_path=tmp_ledger)
        ledger = _load_ledger(tmp_ledger)
        assert len(ledger["records"]) == 3


class TestIsWashSaleBlocked:
    """Tests for is_wash_sale_blocked function."""

    def test_no_losses_not_blocked(self, tmp_ledger):
        """Ticker with no loss history is not blocked."""
        blocked, reason = is_wash_sale_blocked("TSLA", "2026-05-01", ledger_path=tmp_ledger)
        assert blocked is False
        assert reason == ""

    def test_boundary_day_30_blocked(self, tmp_ledger):
        """On day 30, the ticker is still blocked."""
        # Loss on April 15; check on May 15 (exactly 30 days later)
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("MU", "2026-05-15", ledger_path=tmp_ledger)
        assert blocked is True
        assert "MU" in reason
        assert "30 days ago" in reason
        assert "2026-05-16" in reason

    def test_boundary_day_31_allowed(self, tmp_ledger):
        """On day 31, the ticker is no longer blocked."""
        # Loss on April 15; check on May 16 (31 days later)
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("MU", "2026-05-16", ledger_path=tmp_ledger)
        assert blocked is False
        assert reason == ""

    def test_same_day_not_blocked(self, tmp_ledger):
        """The day of the loss close itself is not blocked (0 days have passed)."""
        record_trade_close("AAPL", "2026-04-15", -500.00, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("AAPL", "2026-04-15", ledger_path=tmp_ledger)
        assert blocked is False
        assert reason == ""

    def test_day_1_blocked(self, tmp_ledger):
        """Day 1 after loss is blocked (1 day has passed)."""
        record_trade_close("AAPL", "2026-04-15", -500.00, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("AAPL", "2026-04-16", ledger_path=tmp_ledger)
        assert blocked is True
        assert "1 days ago" in reason or "1 day ago" in reason

    def test_reason_format(self, tmp_ledger):
        """Reason message includes ticker, loss amount, days ago, and unblock date."""
        record_trade_close("MU", "2026-04-15", -340.50, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("MU", "2026-05-05", ledger_path=tmp_ledger)
        assert blocked is True
        assert "MU" in reason
        assert "-340" in reason or "-$340" in reason  # loss amount
        assert "20 days ago" in reason
        assert "2026-05-16" in reason  # unblock date

    def test_multiple_losses_same_ticker_uses_most_recent(self, tmp_ledger):
        """When multiple losses exist for a ticker, use the most recent."""
        record_trade_close("MU", "2026-04-01", -500.00, ledger_path=tmp_ledger)
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        # Check on May 10 (25 days after April 15 loss, 39 days after April 1 loss)
        blocked, reason = is_wash_sale_blocked("MU", "2026-05-10", ledger_path=tmp_ledger)
        assert blocked is True
        assert "2026-04-15" in reason  # Should reference the April 15 loss, not April 1
        assert "25 days ago" in reason

    def test_case_insensitive_ticker(self, tmp_ledger):
        """Ticker matching is case-insensitive."""
        record_trade_close("mu", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("MU", "2026-05-05", ledger_path=tmp_ledger)
        assert blocked is True

    def test_invalid_date_format_returns_not_blocked(self, tmp_ledger):
        """Invalid date format defaults to not blocked (safe)."""
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("MU", "invalid-date", ledger_path=tmp_ledger)
        assert blocked is False
        assert reason == ""

    def test_different_tickers_independent(self, tmp_ledger):
        """Loss on one ticker doesn't affect another."""
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("TSLA", "2026-05-05", ledger_path=tmp_ledger)
        assert blocked is False
        assert reason == ""

    def test_before_loss_date_not_blocked(self, tmp_ledger):
        """Checking before the loss close is not blocked."""
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("MU", "2026-04-10", ledger_path=tmp_ledger)
        assert blocked is False
        assert reason == ""

    def test_long_after_loss_not_blocked(self, tmp_ledger):
        """Long after the 30-day window, ticker is not blocked."""
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("MU", "2026-06-15", ledger_path=tmp_ledger)
        assert blocked is False
        assert reason == ""


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_ledger_file_created_if_missing(self, tmp_ledger):
        """If ledger file doesn't exist, it's created on first save."""
        # Don't create file; just use the path
        assert not Path(tmp_ledger).exists()
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        assert Path(tmp_ledger).exists()

    def test_large_loss_amount(self, tmp_ledger):
        """Large loss amounts are recorded accurately."""
        record_trade_close("NVDA", "2026-04-15", -50000.00, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("NVDA", "2026-05-05", ledger_path=tmp_ledger)
        assert blocked is True
        assert "-50000" in reason or "-$50000" in reason

    def test_small_loss_amount(self, tmp_ledger):
        """Small loss amounts (cents) are recorded accurately."""
        record_trade_close("SPY", "2026-04-15", -0.50, ledger_path=tmp_ledger)
        blocked, reason = is_wash_sale_blocked("SPY", "2026-05-05", ledger_path=tmp_ledger)
        assert blocked is True

    def test_leap_year_boundary(self, tmp_ledger):
        """Wash-sale window works correctly across leap year boundaries."""
        # Feb 28, 2026 loss (2026 is not a leap year, but let's test a 30-day span)
        record_trade_close("MU", "2026-02-15", -340.00, ledger_path=tmp_ledger)
        # Check on March 16 (29 days later)
        blocked, reason = is_wash_sale_blocked("MU", "2026-03-16", ledger_path=tmp_ledger)
        assert blocked is True
        # Check on March 17 (30 days later)
        blocked, reason = is_wash_sale_blocked("MU", "2026-03-17", ledger_path=tmp_ledger)
        assert blocked is True
        # Check on March 18 (31 days later)
        blocked, reason = is_wash_sale_blocked("MU", "2026-03-18", ledger_path=tmp_ledger)
        assert blocked is False

    def test_month_boundary(self, tmp_ledger):
        """30-day window works correctly across month boundaries."""
        record_trade_close("AAPL", "2026-04-30", -200.00, ledger_path=tmp_ledger)
        # Check on May 30 (30 days later)
        blocked, reason = is_wash_sale_blocked("AAPL", "2026-05-30", ledger_path=tmp_ledger)
        assert blocked is True
        # Check on May 31 (31 days later)
        blocked, reason = is_wash_sale_blocked("AAPL", "2026-05-31", ledger_path=tmp_ledger)
        assert blocked is False


class TestIntegration:
    """Integration tests mimicking real usage."""

    def test_real_world_scenario_multiple_trades(self, tmp_ledger):
        """Scenario: trader closes multiple losses and checks re-entry timing."""
        # April 10: close MU at -$340
        record_trade_close("MU", "2026-04-10", -340.00, ledger_path=tmp_ledger)
        # April 15: close AAPL at -$1000
        record_trade_close("AAPL", "2026-04-15", -1000.00, ledger_path=tmp_ledger)

        # April 20: check if can re-enter MU (10 days after close)
        blocked, reason = is_wash_sale_blocked("MU", "2026-04-20", ledger_path=tmp_ledger)
        assert blocked is True

        # May 11: check if can re-enter MU (31 days after close)
        blocked, reason = is_wash_sale_blocked("MU", "2026-05-11", ledger_path=tmp_ledger)
        assert blocked is False

        # May 15: check if can re-enter AAPL (30 days after close)
        blocked, reason = is_wash_sale_blocked("AAPL", "2026-05-15", ledger_path=tmp_ledger)
        assert blocked is True

        # May 16: check if can re-enter AAPL (31 days after close)
        blocked, reason = is_wash_sale_blocked("AAPL", "2026-05-16", ledger_path=tmp_ledger)
        assert blocked is False

    def test_briefing_integration_pattern(self, tmp_ledger):
        """Pattern: briefing checks before recommending a new trade."""
        # Simulate a loss close
        record_trade_close("TSLA", "2026-05-01", -500.00, ledger_path=tmp_ledger)

        # Briefing checks before recommending a new CSP on TSLA
        def can_recommend_csp(ticker, check_date):
            blocked, _ = is_wash_sale_blocked(ticker, check_date, ledger_path=tmp_ledger)
            return not blocked

        # May 5: cannot recommend (5 days after loss)
        assert can_recommend_csp("TSLA", "2026-05-05") is False

        # June 2: can recommend (32 days after loss)
        assert can_recommend_csp("TSLA", "2026-06-02") is True


class TestLedgerPersistence:
    """Tests for ledger file I/O."""

    def test_ledger_persists_across_calls(self, tmp_ledger):
        """Ledger persists across function calls."""
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        # Read ledger directly
        ledger = _load_ledger(tmp_ledger)
        assert len(ledger["records"]) == 1
        # Add another record
        record_trade_close("AAPL", "2026-04-10", -500.00, ledger_path=tmp_ledger)
        # Verify both are there
        ledger = _load_ledger(tmp_ledger)
        assert len(ledger["records"]) == 2

    def test_ledger_json_format(self, tmp_ledger):
        """Ledger is valid JSON with expected structure."""
        record_trade_close("MU", "2026-04-15", -340.00, ledger_path=tmp_ledger)
        import json
        with open(tmp_ledger, "r") as f:
            data = json.load(f)  # Should not raise
        assert data["version"] == 1
        assert isinstance(data["records"], list)
        assert len(data["records"]) == 1
