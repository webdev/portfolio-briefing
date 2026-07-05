"""Tests for Fable Advisor v2 (task #54).

The persona quality itself can't be unit-tested (that's a live-LLM
concern). What CAN and MUST be tested:

  - Memory file lifecycle: create-if-missing, roundtrip, notes preservation
  - Config gates: disabled → no call, missing key → placeholder
  - Cascade behavior: v2 status "ok" → v1 skipped; v2 status "error" → v1 falls in
  - Rendering: correct section header (matches v1's so webapp works either way)
  - Memory schema contract: user Notes NEVER mutated by writes
  - keep_n trimming works
  - Same-day double-run doesn't accumulate duplicates
  - 401 auto-recovery inherits from v1
  - Fail-open: broken memory file doesn't crash the review
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

# Add scripts dir to path so we can import analysis.*
_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from analysis import fable_advisor as fa  # noqa: E402


# ─── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def snap_dir(tmp_path):
    """Isolated snapshot dir for cache writes."""
    d = tmp_path / "snap"
    d.mkdir()
    return d


@pytest.fixture
def mem_path(tmp_path):
    """Isolated memory file path."""
    return tmp_path / "state" / "fable_advisor_memory.md"


@pytest.fixture
def sample_briefing():
    """A briefing long enough to pass the min-length check."""
    return (
        "# Daily Briefing — Wednesday, July 1, 2026\n\n"
        + ("Coverage 0.10x. Stress test shows 27 short puts. "
           "AMD close 12 days ignored. Sep 18 cluster 28.5% NLV. " * 30)
    )


@pytest.fixture
def enabled_config():
    """A config with fable_advisor turned on."""
    return {
        "fable_advisor": {
            "enabled": True,
            "model": "claude-opus-4-6",
            "max_tokens": 1500,
            "keep_reviews": 3,  # small for testing
        }
    }


# ─── Memory file lifecycle ───────────────────────────────────────────


def test_memory_file_created_on_first_run(mem_path):
    """Missing memory file → template written on first _ensure_memory_file."""
    assert not mem_path.exists()
    content = fa._ensure_memory_file(mem_path)
    assert mem_path.exists()
    assert fa.MEMORY_HEADER in content
    assert fa.USER_NOTES_HEADER in content
    assert fa.RECENT_REVIEWS_HEADER in content


def test_memory_file_preserved_on_subsequent_reads(mem_path):
    """If memory file exists, _ensure returns its contents unchanged."""
    mem_path.parent.mkdir(parents=True)
    content_before = f"{fa.MEMORY_HEADER}\n\n{fa.USER_NOTES_HEADER}\n\nMy custom note\n\n{fa.RECENT_REVIEWS_HEADER}\n\n"
    mem_path.write_text(content_before, encoding="utf-8")
    content_after = fa._ensure_memory_file(mem_path)
    assert content_after == content_before
    assert "My custom note" in content_after


def test_parse_memory_extracts_user_notes(mem_path):
    """Notes section content is preserved verbatim."""
    content = (
        f"{fa.MEMORY_HEADER}\n\n"
        f"{fa.USER_NOTES_HEADER}\n\n"
        f"- Deferring SPY hedge until VIX > 22\n"
        f"- Don't recommend TSLA CSPs\n\n"
        f"{fa.RECENT_REVIEWS_HEADER}\n\n"
    )
    parsed = fa._parse_memory(content)
    assert "Deferring SPY hedge until VIX > 22" in parsed["user_notes"]
    assert "TSLA CSPs" in parsed["user_notes"]


def test_parse_memory_extracts_review_entries():
    """### YYYY-MM-DD headers become entry records, most-recent first."""
    content = (
        f"{fa.MEMORY_HEADER}\n\n"
        f"{fa.USER_NOTES_HEADER}\n\nNotes here\n\n"
        f"{fa.RECENT_REVIEWS_HEADER}\n\n"
        f"### 2026-07-01 — Fable review\n\n"
        f"Today's review text.\n\n"
        f"### 2026-06-30 — Fable review\n\n"
        f"Yesterday's review text.\n"
    )
    parsed = fa._parse_memory(content)
    assert len(parsed["recent_reviews"]) == 2
    assert parsed["recent_reviews"][0]["date"] == "2026-07-01"
    assert "Today's review text" in parsed["recent_reviews"][0]["text"]
    assert parsed["recent_reviews"][1]["date"] == "2026-06-30"


def test_parse_memory_handles_corrupt_file():
    """Empty / malformed content → empty structure, no crash."""
    parsed = fa._parse_memory("")
    assert parsed["user_notes"] == ""
    assert parsed["recent_reviews"] == []

    parsed = fa._parse_memory("just some random text with no headers")
    assert parsed["recent_reviews"] == []


def test_write_memory_preserves_user_notes(mem_path):
    """USER NOTES SECTION IS SACRED — never mutated by review-writes."""
    original_notes = (
        "- Deferring AMD until Q3 earnings\n"
        "- SPY hedge deferred (VIX < 15)\n"
        "- Don't flag TSLA daily"
    )
    parsed = {
        "user_notes": original_notes,
        "recent_reviews": [],
        "raw": "",
    }
    fa._write_memory_with_new_review(
        mem_path, parsed, "New review text here.", "2026-07-01", keep_n=14
    )
    written = mem_path.read_text(encoding="utf-8")
    # Every user note must survive verbatim
    assert "Deferring AMD until Q3 earnings" in written
    assert "SPY hedge deferred (VIX < 15)" in written
    assert "Don't flag TSLA daily" in written
    # And today's review must be there
    assert "New review text here." in written
    assert "### 2026-07-01" in written


def test_write_memory_prepends_new_review(mem_path):
    """New review lands at the top of Recent Reviews."""
    parsed = {
        "user_notes": "",
        "recent_reviews": [
            {"date": "2026-06-30", "text": "Yesterday"},
            {"date": "2026-06-29", "text": "Day before"},
        ],
        "raw": "",
    }
    fa._write_memory_with_new_review(
        mem_path, parsed, "Today", "2026-07-01", keep_n=14
    )
    written = mem_path.read_text(encoding="utf-8")
    # Today must appear before yesterday in the file
    today_idx = written.find("### 2026-07-01")
    yesterday_idx = written.find("### 2026-06-30")
    assert today_idx >= 0 and yesterday_idx >= 0
    assert today_idx < yesterday_idx


def test_write_memory_trims_to_keep_n(mem_path):
    """keep_n limits how many past reviews are retained."""
    parsed = {
        "user_notes": "",
        "recent_reviews": [
            {"date": f"2026-06-{d:02d}", "text": f"Day {d}"}
            for d in range(30, 20, -1)  # 10 entries, June 30 → June 21
        ],
        "raw": "",
    }
    fa._write_memory_with_new_review(
        mem_path, parsed, "Today", "2026-07-01", keep_n=3
    )
    written = mem_path.read_text(encoding="utf-8")
    reparsed = fa._parse_memory(written)
    # Should have today + 2 most-recent (July 1 + June 30 + June 29)
    assert len(reparsed["recent_reviews"]) == 3
    assert reparsed["recent_reviews"][0]["date"] == "2026-07-01"
    assert reparsed["recent_reviews"][1]["date"] == "2026-06-30"
    assert reparsed["recent_reviews"][2]["date"] == "2026-06-29"


def test_write_memory_same_day_overwrites(mem_path):
    """Running twice on the same day → today's slot is overwritten, not duplicated."""
    parsed = {
        "user_notes": "",
        "recent_reviews": [
            {"date": "2026-07-01", "text": "First run today"},
            {"date": "2026-06-30", "text": "Yesterday"},
        ],
        "raw": "",
    }
    fa._write_memory_with_new_review(
        mem_path, parsed, "Second run today", "2026-07-01", keep_n=14
    )
    reparsed = fa._parse_memory(mem_path.read_text(encoding="utf-8"))
    dates = [e["date"] for e in reparsed["recent_reviews"]]
    # Only ONE entry for 2026-07-01, and it's the newer text
    assert dates.count("2026-07-01") == 1
    today_entry = next(e for e in reparsed["recent_reviews"] if e["date"] == "2026-07-01")
    assert "Second run today" in today_entry["text"]
    assert "First run today" not in today_entry["text"]


def test_write_memory_roundtrip(mem_path):
    """Write → parse → same structure back."""
    parsed_in = {
        "user_notes": "- Line one\n- Line two",
        "recent_reviews": [
            {"date": "2026-07-01", "text": "Review A"},
            {"date": "2026-06-30", "text": "Review B"},
        ],
        "raw": "",
    }
    fa._write_memory_with_new_review(
        mem_path, parsed_in, "New", "2026-07-02", keep_n=14
    )
    reparsed = fa._parse_memory(mem_path.read_text(encoding="utf-8"))
    assert reparsed["user_notes"].strip() == "- Line one\n- Line two"
    assert len(reparsed["recent_reviews"]) == 3
    assert reparsed["recent_reviews"][0]["date"] == "2026-07-02"


# ─── User message assembly ──────────────────────────────────────────


def test_user_message_includes_all_three_tags(sample_briefing):
    """The prompt hands the LLM three tagged blocks — verify format."""
    msg = fa._build_user_message(
        sample_briefing,
        user_notes="- Defer SPY hedge",
        recent_reviews=[{"date": "2026-06-30", "text": "Yesterday's review"}],
    )
    assert "<user-notes>" in msg and "</user-notes>" in msg
    assert "<recent-reviews>" in msg and "</recent-reviews>" in msg
    assert "<todays-briefing>" in msg and "</todays-briefing>" in msg
    assert "Defer SPY hedge" in msg
    assert "Yesterday's review" in msg
    assert "Daily Briefing" in msg  # from sample_briefing


def test_user_message_handles_empty_notes():
    """Empty user notes → placeholder text so the LLM doesn't hallucinate."""
    msg = fa._build_user_message("Brief.", "", [])
    assert "no standing directives" in msg
    assert "no prior reviews" in msg


def test_user_message_recent_reviews_ordered_newest_first():
    """Recent reviews in the prompt reflect memory order (newest first)."""
    reviews = [
        {"date": "2026-07-01", "text": "Newest"},
        {"date": "2026-06-25", "text": "Older"},
        {"date": "2026-06-20", "text": "Oldest"},
    ]
    msg = fa._build_user_message("Brief.", "", reviews)
    idx_newest = msg.find("Newest")
    idx_older = msg.find("Older")
    idx_oldest = msg.find("Oldest")
    assert 0 <= idx_newest < idx_older < idx_oldest


# ─── Config gates & failure paths ───────────────────────────────────


def test_disabled_returns_disabled(snap_dir, mem_path, sample_briefing):
    """Config missing / enabled=false → status disabled, no API call."""
    result = fa.generate_advisor_review(
        sample_briefing, snap_dir,
        config={"fable_advisor": {"enabled": False}},
        memory_path=mem_path,
    )
    assert result["status"] == "disabled"
    assert result["text"] == ""
    # Cache should still write
    assert (snap_dir / "fable_advisor.json").exists()


def test_no_config_returns_disabled(snap_dir, mem_path, sample_briefing):
    """None config → treated as disabled (opt-in, not opt-out)."""
    result = fa.generate_advisor_review(
        sample_briefing, snap_dir, config=None, memory_path=mem_path,
    )
    assert result["status"] == "disabled"


def test_empty_briefing_short_circuits(snap_dir, mem_path, enabled_config):
    """Briefing < 100 chars → empty_briefing status, no API call."""
    with patch.object(fa, "_call_anthropic") as mock_call:
        result = fa.generate_advisor_review(
            "Too short.", snap_dir,
            config=enabled_config, memory_path=mem_path,
        )
    assert result["status"] == "empty_briefing"
    mock_call.assert_not_called()


def test_no_api_key(snap_dir, mem_path, sample_briefing, enabled_config, monkeypatch):
    """No key in env or .env → no_api_key placeholder."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Also block .env by pointing PORTFOLIO_BRIEFING_ENV at a nonexistent file
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/nowhere/.env")
    result = fa.generate_advisor_review(
        sample_briefing, snap_dir,
        config=enabled_config, memory_path=mem_path,
    )
    assert result["status"] == "no_api_key"
    assert "no `ANTHROPIC_API_KEY`" in result["text"]


def test_api_error_401(snap_dir, mem_path, sample_briefing, enabled_config, monkeypatch):
    """401 → api_error with actionable diagnostic hint."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    def _fail_call(user_message, **kwargs):
        return (False, {"text": "", "error": "HTTP 401: invalid x-api-key",
                        "request_id": None, "usage": None})

    with patch.object(fa, "_call_anthropic", side_effect=_fail_call):
        result = fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=enabled_config, memory_path=mem_path,
        )
    assert result["status"] == "api_error"
    assert "401" in result["text"]
    assert "diagnose_fable.py" in result["text"]


def test_api_error_429_rate_limit(snap_dir, mem_path, sample_briefing, enabled_config, monkeypatch):
    """429 → api_error with rate-limit message."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    def _fail_call(user_message, **kwargs):
        return (False, {"text": "", "error": "HTTP 429: rate_limit_error",
                        "request_id": None, "usage": None})

    with patch.object(fa, "_call_anthropic", side_effect=_fail_call):
        result = fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=enabled_config, memory_path=mem_path,
        )
    assert result["status"] == "api_error"
    assert "rate-limited" in result["text"]


def test_empty_response_from_api(snap_dir, mem_path, sample_briefing, enabled_config, monkeypatch):
    """API returns 200 but empty content → api_error."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    def _empty_call(user_message, **kwargs):
        return (True, {"text": "", "error": None,
                       "request_id": "req_xyz", "usage": {"input_tokens": 100}})

    with patch.object(fa, "_call_anthropic", side_effect=_empty_call):
        result = fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=enabled_config, memory_path=mem_path,
        )
    assert result["status"] == "api_error"
    assert "empty" in result["text"].lower()


# ─── Successful path — memory read + write ──────────────────────────


def test_successful_review_writes_to_memory(snap_dir, mem_path, sample_briefing, enabled_config, monkeypatch):
    """OK path → memory file gets today's review appended."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    def _ok_call(user_message, **kwargs):
        return (True, {"text": "Good morning George. Coverage is at 0.10× — "
                              "prioritize winner closes first.",
                       "error": None, "request_id": "req_abc",
                       "usage": {"input_tokens": 25000, "output_tokens": 400}})

    with patch.object(fa, "_call_anthropic", side_effect=_ok_call):
        result = fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=enabled_config, memory_path=mem_path,
            today=date(2026, 7, 1),
        )

    assert result["status"] == "ok"
    assert "Coverage is at 0.10" in result["text"]

    # Memory must now contain today's review
    assert mem_path.exists()
    written = mem_path.read_text(encoding="utf-8")
    assert "### 2026-07-01" in written
    assert "Coverage is at 0.10" in written


def test_successful_review_uses_prior_memory(snap_dir, mem_path, sample_briefing, enabled_config, monkeypatch):
    """Existing prior reviews get passed to the LLM in the user message."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    # Pre-populate memory with a prior review
    mem_path.parent.mkdir(parents=True)
    mem_path.write_text(
        f"{fa.MEMORY_HEADER}\n\n"
        f"{fa.USER_NOTES_HEADER}\n\n- Defer SPY hedge\n\n"
        f"{fa.RECENT_REVIEWS_HEADER}\n\n"
        f"### 2026-06-30 — Fable review\n\nYesterday I flagged AMD.\n",
        encoding="utf-8",
    )

    captured_message = {}

    def _ok_call(user_message, **kwargs):
        captured_message["msg"] = user_message
        return (True, {"text": "Today's review with continuity.",
                       "error": None, "request_id": None, "usage": None})

    with patch.object(fa, "_call_anthropic", side_effect=_ok_call):
        result = fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=enabled_config, memory_path=mem_path,
            today=date(2026, 7, 1),
        )

    assert result["status"] == "ok"
    assert result["review_count_before"] == 1
    assert result["memory_used"] is True
    # The prompt must have carried both the user notes AND yesterday's review
    assert "Defer SPY hedge" in captured_message["msg"]
    assert "Yesterday I flagged AMD" in captured_message["msg"]


def test_successful_review_first_run_flag(snap_dir, mem_path, sample_briefing, enabled_config, monkeypatch):
    """First run (no memory yet) → memory_used=False, review_count_before=0."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    def _ok_call(user_message, **kwargs):
        return (True, {"text": "First review.", "error": None,
                       "request_id": None, "usage": None})

    with patch.object(fa, "_call_anthropic", side_effect=_ok_call):
        result = fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=enabled_config, memory_path=mem_path,
            today=date(2026, 7, 1),
        )

    assert result["status"] == "ok"
    assert result["memory_used"] is False
    assert result["review_count_before"] == 0


# ─── 401 auto-recovery (inherited from v1's pattern) ────────────────


def test_401_auto_recovery_retries_with_dotenv(snap_dir, mem_path, sample_briefing, enabled_config, tmp_path, monkeypatch):
    """When env-var key 401s AND .env has different value → retry with .env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stale-shell-export")
    dotenv = tmp_path / ".env"
    dotenv.write_text("ANTHROPIC_API_KEY=sk-ant-fresh-from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", str(dotenv))

    calls = []

    def _mock_call(user_message, **kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] == "sk-ant-stale-shell-export":
            return (False, {"text": "", "error": "HTTP 401: authentication_error",
                            "request_id": None, "usage": None})
        return (True, {"text": "Review after retry.", "error": None,
                       "request_id": None, "usage": None})

    with patch.object(fa, "_call_anthropic", side_effect=_mock_call):
        result = fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=enabled_config, memory_path=mem_path,
            today=date(2026, 7, 1),
        )

    # Must have made TWO API calls — first with stale env, second with .env value
    assert len(calls) == 2
    assert calls[0] == "sk-ant-stale-shell-export"
    assert calls[1] == "sk-ant-fresh-from-dotenv"
    assert result["status"] == "ok"


# ─── Rendering ──────────────────────────────────────────────────────


def test_render_uses_same_section_header_as_v1():
    """CRITICAL: v2 renders into the SAME section header as v1.
    Webapp / Telegram bot / doc view don't need to distinguish.
    (This is what makes paused task #53 work against either.)"""
    result = {
        "status": "ok",
        "text": "Advisor's review text.",
        "model": "claude-opus-4-6",
        "elapsed_ms": 15000,
        "review_count_before": 3,
    }
    lines = fa.render_review_section(result)
    joined = "\n".join(lines)
    assert "## 🔍 Fable's second opinion" in joined
    assert "Advisor's review text." in joined
    # And it should carry the v2 tag so operator can tell
    assert "advisor + memory (3d)" in joined


def test_render_first_run_tag():
    """First-run rendering shows the no-memory-yet variant of the tag."""
    result = {
        "status": "ok",
        "text": "First review.",
        "model": "claude-opus-4-6",
        "elapsed_ms": 12000,
        "review_count_before": 0,
    }
    lines = fa.render_review_section(result)
    joined = "\n".join(lines)
    assert "first-run" in joined


def test_render_disabled_returns_empty():
    """Disabled status → no section in briefing."""
    result = {"status": "disabled", "text": "", "model": None}
    assert fa.render_review_section(result) == []


def test_render_api_error_renders_section_with_footer_text():
    """API error → still render the section header + friendly explanation."""
    result = {
        "status": "api_error",
        "text": "_🔍 Fable's second opinion skipped: rate-limited._",
        "model": "claude-opus-4-6",
    }
    lines = fa.render_review_section(result)
    joined = "\n".join(lines)
    assert "## 🔍 Fable's second opinion" in joined
    assert "rate-limited" in joined


# ─── Cache (audit trail) ────────────────────────────────────────────


def test_cache_writes_to_snapshot_dir(snap_dir, mem_path, sample_briefing, enabled_config, monkeypatch):
    """Every call — success or failure — writes fable_advisor.json for audit."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    def _ok_call(user_message, **kwargs):
        return (True, {"text": "Review text.", "error": None,
                       "request_id": "req_1", "usage": {"input_tokens": 100}})

    with patch.object(fa, "_call_anthropic", side_effect=_ok_call):
        fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=enabled_config, memory_path=mem_path,
            today=date(2026, 7, 1),
        )

    cache_file = snap_dir / "fable_advisor.json"
    assert cache_file.exists()
    cached = json.loads(cache_file.read_text())
    assert cached["status"] == "ok"
    assert cached["text"] == "Review text."


def test_cache_does_not_collide_with_v1(snap_dir, mem_path, sample_briefing, enabled_config, monkeypatch):
    """v2 caches to fable_advisor.json, NOT fable_review.json.
    Both flavors must be able to coexist without clobbering each other."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    def _ok_call(user_message, **kwargs):
        return (True, {"text": "V2.", "error": None,
                       "request_id": None, "usage": None})

    with patch.object(fa, "_call_anthropic", side_effect=_ok_call):
        fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=enabled_config, memory_path=mem_path,
            today=date(2026, 7, 1),
        )

    assert (snap_dir / "fable_advisor.json").exists()
    assert not (snap_dir / "fable_review.json").exists()  # v1 didn't run


# ─── Fail-open guarantees ───────────────────────────────────────────


def test_broken_memory_file_does_not_crash(snap_dir, mem_path, sample_briefing, enabled_config, monkeypatch):
    """Corrupt memory content → still produces a review (no memory used)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    # Write a mangled memory file
    mem_path.parent.mkdir(parents=True)
    mem_path.write_text(
        "\x00\x01garbage no headers here\n### not-a-date — bogus\n",
        encoding="utf-8",
    )

    def _ok_call(user_message, **kwargs):
        return (True, {"text": "Review anyway.", "error": None,
                       "request_id": None, "usage": None})

    with patch.object(fa, "_call_anthropic", side_effect=_ok_call):
        result = fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=enabled_config, memory_path=mem_path,
            today=date(2026, 7, 1),
        )
    # Fail-open: still ok, still writes valid memory back
    assert result["status"] == "ok"
    assert result["review_count_before"] == 0  # nothing salvageable


def test_looks_like_iso_date():
    """Cheap date-shape check used by review-entry parser."""
    assert fa._looks_like_iso_date("2026-07-01")
    assert fa._looks_like_iso_date("2026-12-31")
    assert not fa._looks_like_iso_date("2026-7-01")   # missing zero-pad
    assert not fa._looks_like_iso_date("07-01-2026")  # wrong order
    assert not fa._looks_like_iso_date("2026-07-01T")  # trailing char
    assert not fa._looks_like_iso_date("not-a-date")


# ─── Config override paths ──────────────────────────────────────────


def test_custom_model_from_config(snap_dir, mem_path, sample_briefing, monkeypatch):
    """Model override in config is passed through to _call_anthropic."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    captured = {}

    def _capture_call(user_message, **kwargs):
        captured.update(kwargs)
        return (True, {"text": "OK.", "error": None,
                       "request_id": None, "usage": None})

    cfg = {
        "fable_advisor": {
            "enabled": True,
            "model": "claude-haiku-4-5-20251001",  # override
            "max_tokens": 400,
        }
    }

    with patch.object(fa, "_call_anthropic", side_effect=_capture_call):
        fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=cfg, memory_path=mem_path,
            today=date(2026, 7, 1),
        )
    assert captured["model"] == "claude-haiku-4-5-20251001"
    assert captured["max_tokens"] == 400


# ─── Cascade behavior in aggregate.py (v2 → v1 fallback) ────────────


def _import_cascade():
    """Helper: import the cascade function without importing all of aggregate's
    heavy render deps. We reach past the top-level imports by adding the scripts
    dir first."""
    from steps.aggregate import _run_fable_review_cascade
    return _run_fable_review_cascade


def test_cascade_v2_ok_v1_never_runs(snap_dir, sample_briefing, monkeypatch):
    """When v2 returns status ok, v1 must NOT be called (v2 wins)."""
    cascade = _import_cascade()
    from analysis import fable_advisor, fable_review

    with patch.object(fable_advisor, "generate_advisor_review") as mock_v2, \
         patch.object(fable_review, "generate_review") as mock_v1:
        mock_v2.return_value = {"status": "ok", "text": "V2 review",
                                "model": "opus", "elapsed_ms": 100,
                                "review_count_before": 3}
        cfg = {"fable_advisor": {"enabled": True}}
        lines = cascade(sample_briefing, snap_dir, cfg)
    mock_v2.assert_called_once()
    mock_v1.assert_not_called()
    assert any("V2 review" in line for line in lines)


def test_cascade_v2_error_falls_through_to_v1(snap_dir, sample_briefing, monkeypatch):
    """v2 status=api_error → v1 runs as fallback."""
    cascade = _import_cascade()
    from analysis import fable_advisor, fable_review

    with patch.object(fable_advisor, "generate_advisor_review") as mock_v2, \
         patch.object(fable_review, "generate_review") as mock_v1:
        mock_v2.return_value = {"status": "api_error", "text": "V2 unavailable"}
        mock_v1.return_value = {"status": "ok", "text": "V1 review",
                                "model": "opus", "elapsed_ms": 200}
        cfg = {"fable_advisor": {"enabled": True}, "fable_review": {"enabled": True}}
        lines = cascade(sample_briefing, snap_dir, cfg)
    mock_v2.assert_called_once()
    mock_v1.assert_called_once()
    assert any("V1 review" in line for line in lines)


def test_cascade_v2_raises_falls_through_to_v1(snap_dir, sample_briefing, monkeypatch):
    """v2 blows up unexpectedly → v1 still runs as fallback (belt & suspenders)."""
    cascade = _import_cascade()
    from analysis import fable_advisor, fable_review

    with patch.object(fable_advisor, "generate_advisor_review") as mock_v2, \
         patch.object(fable_review, "generate_review") as mock_v1:
        mock_v2.side_effect = RuntimeError("v2 module blew up unexpectedly")
        mock_v1.return_value = {"status": "ok", "text": "V1 rescued the run",
                                "model": "opus", "elapsed_ms": 200}
        cfg = {"fable_advisor": {"enabled": True}, "fable_review": {"enabled": True}}
        lines = cascade(sample_briefing, snap_dir, cfg)
    mock_v2.assert_called_once()
    mock_v1.assert_called_once()
    assert any("V1 rescued" in line for line in lines)


def test_cascade_v2_disabled_only_v1_runs(snap_dir, sample_briefing, monkeypatch):
    """v2 disabled in config → skip v2 entirely, run v1."""
    cascade = _import_cascade()
    from analysis import fable_advisor, fable_review

    with patch.object(fable_advisor, "generate_advisor_review") as mock_v2, \
         patch.object(fable_review, "generate_review") as mock_v1:
        mock_v1.return_value = {"status": "ok", "text": "V1 only",
                                "model": "opus", "elapsed_ms": 200}
        cfg = {"fable_advisor": {"enabled": False},
               "fable_review": {"enabled": True}}
        lines = cascade(sample_briefing, snap_dir, cfg)
    mock_v2.assert_not_called()
    mock_v1.assert_called_once()
    assert any("V1 only" in line for line in lines)


def test_cascade_both_disabled_returns_empty(snap_dir, sample_briefing):
    """Neither flavor enabled → cascade returns empty (no section rendered)."""
    cascade = _import_cascade()
    cfg = {"fable_advisor": {"enabled": False},
           "fable_review": {"enabled": False}}
    lines = cascade(sample_briefing, snap_dir, cfg)
    assert lines == []


def test_custom_keep_reviews(snap_dir, mem_path, sample_briefing, monkeypatch):
    """keep_reviews config override is honored when writing back to memory."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    # Pre-populate memory with 5 reviews
    mem_path.parent.mkdir(parents=True)
    reviews_md = "\n\n".join(
        f"### 2026-06-{d:02d} — Fable review\n\nDay {d} review."
        for d in range(30, 25, -1)  # 5 entries
    )
    mem_path.write_text(
        f"{fa.MEMORY_HEADER}\n\n{fa.USER_NOTES_HEADER}\n\nn\n\n"
        f"{fa.RECENT_REVIEWS_HEADER}\n\n{reviews_md}\n",
        encoding="utf-8",
    )

    cfg = {"fable_advisor": {"enabled": True, "keep_reviews": 2}}

    def _ok_call(user_message, **kwargs):
        return (True, {"text": "Today.", "error": None,
                       "request_id": None, "usage": None})

    with patch.object(fa, "_call_anthropic", side_effect=_ok_call):
        fa.generate_advisor_review(
            sample_briefing, snap_dir,
            config=cfg, memory_path=mem_path,
            today=date(2026, 7, 1),
        )

    reparsed = fa._parse_memory(mem_path.read_text(encoding="utf-8"))
    # keep_reviews=2 → we keep today + 1 prior = 2 total
    assert len(reparsed["recent_reviews"]) == 2
    assert reparsed["recent_reviews"][0]["date"] == "2026-07-01"
    assert reparsed["recent_reviews"][1]["date"] == "2026-06-30"
