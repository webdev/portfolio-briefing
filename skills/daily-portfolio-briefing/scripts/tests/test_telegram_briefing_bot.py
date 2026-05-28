"""Telegram briefing daemon — pure helpers, state, and BriefingBot routing."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import telegram_briefing_bot as tb  # noqa: E402


def test_extract_verifier_accepts_5_char_alnum():
    assert tb.extract_verifier("ABC12") == "ABC12"
    assert tb.extract_verifier("  abc12  ") == "abc12"


def test_extract_verifier_rejects_non_codes():
    assert tb.extract_verifier("run") is None
    assert tb.extract_verifier("ABC123") is None
    assert tb.extract_verifier("AB!2X") is None
    assert tb.extract_verifier("") is None


def test_is_authorized_matches_allowed_id():
    upd = {"message": {"from": {"id": 5244308999}, "text": "hi"}}
    assert tb.is_authorized(upd, 5244308999) is True
    assert tb.is_authorized(upd, 111) is False
    assert tb.is_authorized({}, 5244308999) is False


def test_extract_summary_pulls_action_list_section():
    md = (
        "# Daily Briefing\n\n"
        "## Market Context\nblah\n\n"
        "## Today's Action List — Thu\n- do X\n- do Y\n\n"
        "## Watch / Portfolio Review\nstuff\n"
    )
    out = tb.extract_summary(md)
    assert "Today's Action List" in out
    assert "do X" in out and "do Y" in out
    assert "Watch / Portfolio Review" not in out


def test_extract_summary_falls_back_when_no_header():
    md = "# Daily Briefing\n\nNo action header here at all.\n"
    out = tb.extract_summary(md)
    assert out.startswith("# Daily Briefing")


def test_compute_next_fire_same_day_when_before():
    now = datetime(2026, 5, 28, 5, 0, 0)
    assert tb.compute_next_fire(now, 6, 30) == datetime(2026, 5, 28, 6, 30, 0)


def test_compute_next_fire_rolls_to_tomorrow_when_past():
    now = datetime(2026, 5, 28, 7, 0, 0)
    assert tb.compute_next_fire(now, 6, 30) == datetime(2026, 5, 29, 6, 30, 0)
