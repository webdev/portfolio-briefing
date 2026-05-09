"""Unit tests for earnings_guard module."""

import pytest
from datetime import date
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analysis.earnings_guard import (
    parse_earnings_date,
    days_until_earnings,
    check_earnings_conflict,
    format_earnings_badge,
)


class TestParseEarningsDate:
    """Test parse_earnings_date function."""

    def test_valid_iso_date(self):
        """Parse valid ISO date string."""
        result = parse_earnings_date("2026-05-20")
        assert result == date(2026, 5, 20)

    def test_none_input(self):
        """Parse None input returns None."""
        result = parse_earnings_date(None)
        assert result is None

    def test_empty_string(self):
        """Parse empty string returns None."""
        result = parse_earnings_date("")
        assert result is None

    def test_malformed_date(self):
        """Parse malformed date returns None."""
        result = parse_earnings_date("2026-13-01")  # invalid month
        assert result is None

    def test_non_iso_format(self):
        """Parse non-ISO format returns None."""
        result = parse_earnings_date("05/20/2026")
        assert result is None

    def test_whitespace_only(self):
        """Parse whitespace-only string returns None."""
        result = parse_earnings_date("   ")
        assert result is None


class TestDaysUntilEarnings:
    """Test days_until_earnings function."""

    def test_ticker_with_future_earnings(self):
        """Calculate days to future earnings."""
        earnings_cal = {"AAPL": "2026-05-20"}
        result = days_until_earnings("AAPL", earnings_cal, date(2026, 5, 8))
        assert result == 12

    def test_ticker_with_past_earnings(self):
        """Calculate negative days for past earnings."""
        earnings_cal = {"AAPL": "2026-05-01"}
        result = days_until_earnings("AAPL", earnings_cal, date(2026, 5, 8))
        assert result == -7

    def test_ticker_missing_from_calendar(self):
        """Return None when ticker not in earnings calendar."""
        earnings_cal = {"AAPL": "2026-05-20"}
        result = days_until_earnings("MSFT", earnings_cal, date(2026, 5, 8))
        assert result is None

    def test_none_earnings_calendar(self):
        """Return None when earnings_calendar is None."""
        result = days_until_earnings("AAPL", None, date(2026, 5, 8))
        assert result is None

    def test_empty_earnings_calendar(self):
        """Return None when earnings_calendar is empty dict."""
        result = days_until_earnings("AAPL", {}, date(2026, 5, 8))
        assert result is None

    def test_same_day_earnings(self):
        """Return 0 when earnings is today."""
        earnings_cal = {"AAPL": "2026-05-08"}
        result = days_until_earnings("AAPL", earnings_cal, date(2026, 5, 8))
        assert result == 0

    def test_malformed_earnings_date(self):
        """Return None when earnings date is malformed."""
        earnings_cal = {"AAPL": "not-a-date"}
        result = days_until_earnings("AAPL", earnings_cal, date(2026, 5, 8))
        assert result is None


class TestCheckEarningsConflict:
    """Test check_earnings_conflict function."""

    def test_no_earnings_data(self):
        """No conflict when earnings_calendar is None."""
        result = check_earnings_conflict(
            "AAPL", "2026-05-20", None, date(2026, 5, 8)
        )
        assert result["conflict"] is False
        assert result["level"] == "none"
        assert result["days_to_earnings"] is None

    def test_ticker_not_in_calendar(self):
        """No conflict when ticker not in earnings calendar."""
        result = check_earnings_conflict(
            "AAPL", "2026-05-20", {"MSFT": "2026-05-15"}, date(2026, 5, 8)
        )
        assert result["conflict"] is False
        assert result["level"] == "none"

    def test_earnings_already_passed(self):
        """No conflict when earnings already passed."""
        result = check_earnings_conflict(
            "AAPL", "2026-05-20", {"AAPL": "2026-05-01"}, date(2026, 5, 8)
        )
        assert result["conflict"] is False
        assert result["level"] == "none"
        assert result["days_to_earnings"] == -7

    def test_earnings_well_after_expiration(self):
        """No conflict when earnings > 5 days after expiration."""
        result = check_earnings_conflict(
            "AAPL", "2026-05-10", {"AAPL": "2026-05-20"}, date(2026, 5, 8)
        )
        assert result["conflict"] is False
        assert result["level"] == "none"

    def test_earnings_within_5_days_before_expiration_block(self):
        """BLOCK when earnings is imminent (<=14d away) and before expiration."""
        # Earnings 12d away, expires 15d away
        result = check_earnings_conflict(
            "AAPL", "2026-05-23", {"AAPL": "2026-05-20"}, date(2026, 5, 8)
        )
        assert result["conflict"] is True
        assert result["level"] == "block"
        assert result["days_to_earnings"] == 12

    def test_earnings_within_5_days_before_expiration_warn(self):
        """WARN when earnings overlaps expiration but >14d away."""
        # Earnings 25d away (after threshold), expires 30d away
        result = check_earnings_conflict(
            "AAPL", "2026-06-07", {"AAPL": "2026-06-02"}, date(2026, 5, 8)
        )
        assert result["conflict"] is True
        assert result["level"] == "warn"
        assert result["days_to_earnings"] == 25

    def test_earnings_on_expiration_day(self):
        """BLOCK when earnings and expiration are the same day."""
        result = check_earnings_conflict(
            "AAPL", "2026-05-20", {"AAPL": "2026-05-20"}, date(2026, 5, 8)
        )
        assert result["conflict"] is True
        assert result["level"] == "block"

    def test_earnings_one_day_before_expiration(self):
        """WARN when earnings is 1d before expiration (edge case)."""
        # Earnings 19d away, expires 20d away
        result = check_earnings_conflict(
            "AAPL", "2026-05-28", {"AAPL": "2026-05-27"}, date(2026, 5, 8)
        )
        assert result["conflict"] is True
        assert result["level"] == "warn"

    def test_imminent_earnings_far_expiration(self):
        """BLOCK when earnings is imminent even if expiration is far."""
        # Earnings 5d away (imminent), expires 60d away
        result = check_earnings_conflict(
            "AAPL", "2026-07-07", {"AAPL": "2026-05-13"}, date(2026, 5, 8)
        )
        assert result["conflict"] is True
        assert result["level"] == "block"

    def test_malformed_expiration_date(self):
        """No conflict when expiration date is malformed."""
        result = check_earnings_conflict(
            "AAPL", "not-a-date", {"AAPL": "2026-05-20"}, date(2026, 5, 8)
        )
        assert result["conflict"] is False
        assert result["level"] == "none"

    def test_malformed_earnings_date_in_calendar(self):
        """No conflict when earnings date in calendar is malformed."""
        result = check_earnings_conflict(
            "AAPL", "2026-05-30", {"AAPL": "invalid"}, date(2026, 5, 8)
        )
        assert result["conflict"] is False
        assert result["level"] == "none"


class TestFormatEarningsBadge:
    """Test format_earnings_badge function."""

    def test_no_conflict_returns_empty(self):
        """No conflict returns empty string."""
        check_result = {
            "conflict": False,
            "level": "none",
            "days_to_earnings": None,
            "message": "No earnings",
        }
        result = format_earnings_badge(check_result)
        assert result == ""

    def test_block_level_badge(self):
        """BLOCK level produces red circle badge."""
        check_result = {
            "conflict": True,
            "level": "block",
            "days_to_earnings": 12,
            "message": "BLOCK: Imminent earnings 12d away",
        }
        result = format_earnings_badge(check_result)
        assert result.startswith("🔴")
        assert "BLOCK" in result

    def test_warn_level_badge(self):
        """WARN level produces warning badge."""
        check_result = {
            "conflict": True,
            "level": "warn",
            "days_to_earnings": 25,
            "message": "⚠️ Earnings 25d away, 5d before expiration",
        }
        result = format_earnings_badge(check_result)
        assert result.startswith("⚠️")

    def test_empty_message_handled(self):
        """Empty message is handled gracefully."""
        check_result = {
            "conflict": True,
            "level": "warn",
            "days_to_earnings": 10,
            "message": "",
        }
        result = format_earnings_badge(check_result)
        assert result.startswith("⚠️")

    def test_unknown_level_returns_empty(self):
        """Unknown conflict level returns empty string."""
        check_result = {
            "conflict": True,
            "level": "unknown",
            "days_to_earnings": 10,
            "message": "some message",
        }
        result = format_earnings_badge(check_result)
        assert result == ""

    def test_conflict_false_returns_empty_regardless_of_level(self):
        """conflict=False always returns empty regardless of level."""
        check_result = {
            "conflict": False,
            "level": "block",  # Contradictory but should be ignored
            "days_to_earnings": 5,
            "message": "some message",
        }
        result = format_earnings_badge(check_result)
        assert result == ""


class TestIntegration:
    """Integration tests combining multiple functions."""

    def test_full_workflow_imminent_earnings(self):
        """Test full workflow: earnings imminent, should block."""
        earnings_cal = {"NVDA": "2026-05-15"}
        as_of = date(2026, 5, 8)
        expiration = "2026-05-22"

        days = days_until_earnings("NVDA", earnings_cal, as_of)
        assert days == 7

        check = check_earnings_conflict("NVDA", expiration, earnings_cal, as_of)
        assert check["conflict"] is True
        assert check["level"] == "block"

        badge = format_earnings_badge(check)
        assert badge.startswith("🔴")
        assert "BLOCK" in badge

    def test_full_workflow_future_earnings(self):
        """Test full workflow: earnings distant, no conflict."""
        earnings_cal = {"TSLA": "2026-07-20"}
        as_of = date(2026, 5, 8)
        expiration = "2026-05-22"

        days = days_until_earnings("TSLA", earnings_cal, as_of)
        assert days == 73

        check = check_earnings_conflict("TSLA", expiration, earnings_cal, as_of)
        assert check["conflict"] is False
        assert check["level"] == "none"

        badge = format_earnings_badge(check)
        assert badge == ""

    def test_full_workflow_missing_earnings(self):
        """Test full workflow: no earnings data, no conflict."""
        earnings_cal = None
        as_of = date(2026, 5, 8)
        expiration = "2026-05-22"

        days = days_until_earnings("AAPL", earnings_cal, as_of)
        assert days is None

        check = check_earnings_conflict("AAPL", expiration, earnings_cal, as_of)
        assert check["conflict"] is False
        assert check["level"] == "none"

        badge = format_earnings_badge(check)
        assert badge == ""


# Edge cases and boundary conditions
class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_leap_year_date(self):
        """Parse a leap year date."""
        result = parse_earnings_date("2024-02-29")
        assert result == date(2024, 2, 29)

    def test_year_boundary(self):
        """Handle year boundary transitions."""
        result = days_until_earnings(
            "AAPL", {"AAPL": "2027-01-01"}, date(2026, 12, 31)
        )
        assert result == 1

    def test_very_far_future_earnings(self):
        """Handle earnings far in the future."""
        result = days_until_earnings(
            "AAPL", {"AAPL": "2026-12-31"}, date(2026, 5, 8)
        )
        assert result == 237

    def test_negative_days_calculation(self):
        """Verify negative days are calculated correctly."""
        result = days_until_earnings(
            "AAPL", {"AAPL": "2025-01-01"}, date(2026, 5, 8)
        )
        assert result < 0
        assert result == -492  # 2025-01-01 to 2026-05-08

    def test_mixed_case_ticker(self):
        """Ticker is case-sensitive in calendar lookup."""
        earnings_cal = {"AAPL": "2026-05-20"}
        result = days_until_earnings("aapl", earnings_cal, date(2026, 5, 8))
        assert result is None  # Should not find lowercase

    def test_ticker_with_special_chars(self):
        """Ticker with special characters not found."""
        earnings_cal = {"AAPL": "2026-05-20"}
        result = days_until_earnings("AAPL.X", earnings_cal, date(2026, 5, 8))
        assert result is None
