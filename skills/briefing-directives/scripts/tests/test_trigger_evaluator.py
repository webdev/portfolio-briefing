"""Tests for trigger_evaluator module."""

from datetime import date, datetime, timedelta, timezone

import pytest

from trigger_evaluator import evaluate_trigger, evaluate_all_active
from directive_store import create


class TestTimeElapsed:
    """Tests for time_elapsed trigger."""

    def test_time_elapsed_fires(self, current_state_base):
        """Fires when current_date >= until_date."""
        trigger = {
            "trigger": "time_elapsed",
            "until_date": "2026-05-05",  # Past date
        }
        directive = {"directive_id": "test", "created_at": "2026-05-01T00:00:00Z"}
        current_state_base["current_date"] = date(2026, 5, 7)

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is True

    def test_time_elapsed_doesnt_fire(self, current_state_base):
        """Doesn't fire when current_date < until_date."""
        trigger = {
            "trigger": "time_elapsed",
            "until_date": "2026-05-20",  # Future date
        }
        directive = {"directive_id": "test", "created_at": "2026-05-01T00:00:00Z"}
        current_state_base["current_date"] = date(2026, 5, 7)

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is False

    def test_time_elapsed_missing_date(self, current_state_base):
        """Handles missing until_date."""
        trigger = {"trigger": "time_elapsed"}
        directive = {"directive_id": "test"}

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is False


class TestEarningsPassed:
    """Tests for earnings_passed trigger."""

    def test_earnings_passed_fires(self, current_state_base):
        """Fires when next_earnings has passed."""
        trigger = {
            "trigger": "earnings_passed",
            "symbol": "MSFT",
        }
        directive = {"directive_id": "test"}
        # MSFT earnings is 7 days from today; set current to 8 days from today
        current_state_base["current_date"] = current_state_base["earnings_calendar"]["MSFT"] + timedelta(days=1)

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is True

    def test_earnings_passed_doesnt_fire(self, current_state_base):
        """Doesn't fire when earnings still in future."""
        trigger = {
            "trigger": "earnings_passed",
            "symbol": "AAPL",
        }
        directive = {"directive_id": "test"}
        # AAPL earnings is 14 days away; keep current_date today
        current_state_base["current_date"] = date.today()

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is False

    def test_earnings_passed_missing_calendar(self, current_state_base):
        """Handles missing earnings calendar."""
        trigger = {
            "trigger": "earnings_passed",
            "symbol": "UNKNOWN",
        }
        directive = {"directive_id": "test"}

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is False
        assert "data_unavailable" in reason


class TestPositionClosed:
    """Tests for position_closed trigger."""

    def test_position_closed_fires(self, current_state_base):
        """Fires when position no longer exists."""
        trigger = {
            "trigger": "position_closed",
            "position_identifier": "AAPL  260619P00170000",
        }
        directive = {"directive_id": "test"}
        # Remove the position
        current_state_base["positions"] = [
            p for p in current_state_base["positions"]
            if p["identifier"] != "AAPL  260619P00170000"
        ]

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is True

    def test_position_closed_doesnt_fire(self, current_state_base):
        """Doesn't fire when position still exists."""
        trigger = {
            "trigger": "position_closed",
            "position_identifier": "AAPL  260619P00170000",
        }
        directive = {"directive_id": "test"}

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is False

    def test_position_closed_missing_positions(self, current_state_base):
        """Handles missing positions list."""
        trigger = {
            "trigger": "position_closed",
            "position_identifier": "AAPL  260619P00170000",
        }
        directive = {"directive_id": "test"}
        current_state_base["positions"] = []

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is True


class TestPriceAbove:
    """Tests for price_above trigger."""

    def test_price_above_fires(self, current_state_base):
        """Fires when last_close >= level."""
        trigger = {
            "trigger": "price_above",
            "symbol": "NVDA",
            "level": 180.00,
        }
        directive = {"directive_id": "test"}
        # NVDA is at 182.00
        current_state_base["last_close"]["NVDA"] = 182.00

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is True

    def test_price_above_fires_at_level(self, current_state_base):
        """Fires when price exactly at level."""
        trigger = {
            "trigger": "price_above",
            "symbol": "NVDA",
            "level": 182.00,
        }
        directive = {"directive_id": "test"}
        current_state_base["last_close"]["NVDA"] = 182.00

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is True

    def test_price_above_doesnt_fire(self, current_state_base):
        """Doesn't fire when price below level."""
        trigger = {
            "trigger": "price_above",
            "symbol": "NVDA",
            "level": 190.00,
        }
        directive = {"directive_id": "test"}
        current_state_base["last_close"]["NVDA"] = 182.00

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is False


class TestPriceBelow:
    """Tests for price_below trigger."""

    def test_price_below_fires(self, current_state_base):
        """Fires when last_close <= level."""
        trigger = {
            "trigger": "price_below",
            "symbol": "NVDA",
            "level": 185.00,
        }
        directive = {"directive_id": "test"}
        current_state_base["last_close"]["NVDA"] = 182.00

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is True

    def test_price_below_doesnt_fire(self, current_state_base):
        """Doesn't fire when price above level."""
        trigger = {
            "trigger": "price_below",
            "symbol": "NVDA",
            "level": 180.00,
        }
        directive = {"directive_id": "test"}
        current_state_base["last_close"]["NVDA"] = 182.00

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is False


class TestScreenerDrops:
    """Tests for screener_drops trigger."""

    def test_screener_drops_fires(self, current_state_base):
        """Fires when symbol no longer in screener output."""
        trigger = {
            "trigger": "screener_drops",
            "symbol": "AAPL",
            "screener_name": "vcp-screener",
        }
        directive = {"directive_id": "test"}
        # Remove AAPL from screener output
        current_state_base["screener_outputs"]["vcp-screener"] = ["MSFT", "NVDA"]

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is True

    def test_screener_drops_doesnt_fire(self, current_state_base):
        """Doesn't fire when symbol still in screener."""
        trigger = {
            "trigger": "screener_drops",
            "symbol": "AAPL",
            "screener_name": "vcp-screener",
        }
        directive = {"directive_id": "test"}

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is False


class TestManualOverride:
    """Tests for manual_override trigger."""

    def test_manual_override_never_fires(self, current_state_base):
        """manual_override never fires automatically."""
        trigger = {"trigger": "manual_override"}
        directive = {"directive_id": "test"}

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is False


class TestOpenEnded:
    """Tests for open_ended trigger."""

    def test_open_ended_never_fires(self, current_state_base):
        """open_ended never fires automatically."""
        trigger = {"trigger": "open_ended"}
        directive = {
            "directive_id": "test",
            "created_at": "2026-05-01T00:00:00Z",
        }
        current_state_base["current_date"] = date(2026, 5, 7)

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is False

    def test_open_ended_note_renewal_after_30(self, current_state_base):
        """Logs renewal prompt after 30 days."""
        trigger = {"trigger": "open_ended"}
        directive = {
            "directive_id": "test",
            "created_at": "2026-04-01T00:00:00Z",
        }
        current_state_base["current_date"] = date(2026, 5, 7)  # 36 days later

        fired, reason = evaluate_trigger(trigger, directive, current_state_base)
        assert fired is False
        assert "renewal" in reason.lower()


class TestEvaluateAllActive:
    """Tests for evaluate_all_active integration."""

    def test_evaluate_all_active_expires_ready(self, state_dir, sample_directive_defer, current_state_base):
        """evaluate_all_active expires directives when triggers fire."""
        # Create a directive that will expire
        directive = create(state_dir, sample_directive_defer)

        # Set earnings to past
        current_state_base["earnings_calendar"]["AAPL"] = date(2026, 5, 1)
        current_state_base["current_date"] = date(2026, 5, 7)

        expired = evaluate_all_active(state_dir, current_state_base)

        assert len(expired) == 1
        assert expired[0]["status"] == "EXPIRED"

    def test_evaluate_all_active_keeps_active(self, state_dir, sample_directive_defer, current_state_base):
        """evaluate_all_active keeps directives when triggers don't fire."""
        create(state_dir, sample_directive_defer)

        # Earnings still in future
        current_state_base["current_date"] = date.today()

        expired = evaluate_all_active(state_dir, current_state_base)

        assert len(expired) == 0

        # Verify directive still ACTIVE
        from directive_store import list as list_directives
        active = list_directives(state_dir, status="ACTIVE")
        assert len(active) == 1

    def test_evaluate_all_active_multiple(self, state_dir, sample_directive_defer, sample_directive_watch, current_state_base):
        """evaluate_all_active handles multiple directives."""
        create(state_dir, sample_directive_defer)
        create(state_dir, sample_directive_watch)

        # Set up for both to expire
        current_state_base["earnings_calendar"]["AAPL"] = date(2026, 5, 1)
        current_state_base["last_close"]["NVDA"] = 186.00  # Above trigger
        current_state_base["current_date"] = date(2026, 5, 7)

        expired = evaluate_all_active(state_dir, current_state_base)

        assert len(expired) == 2
