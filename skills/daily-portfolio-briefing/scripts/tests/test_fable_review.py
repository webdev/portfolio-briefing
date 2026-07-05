"""Tests for the fable-review module.

All tests mock the Anthropic API — the real endpoint is never hit.

Coverage:
  - Disabled path (config toggle off)
  - No-API-key path (env unset)
  - Empty-briefing path (defensive)
  - Successful call path (mocked response)
  - API error path (network / HTTP failure)
  - Empty response path (model returned nothing)
  - Cache write on every path (audit trail)
  - Render section skips when disabled but shows on all failure modes
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS))

from analysis import fable_review as fr  # noqa: E402


# ─── Helpers ───────────────────────────────────────────────────────────────


@pytest.fixture
def snap_dir(tmp_path):
    d = tmp_path / "state" / "briefing_snapshots" / "2026-07-01"
    d.mkdir(parents=True)
    return d


def _big_briefing() -> str:
    """Return a large-enough briefing string to pass the length gate."""
    return "# Daily Briefing 2026-07-01\n" + ("body content\n" * 200)


def _cfg(**overrides):
    base = {
        "fable_review": {
            "enabled": True,
            "model": "claude-opus-4-6",
            "max_tokens": 1200,
            "timeout_sec": 60,
            "max_input_chars": 120_000,
        }
    }
    base["fable_review"].update(overrides)
    return base


# ─── Disabled toggle ──────────────────────────────────────────────────────


def test_disabled_via_config_returns_disabled_status_and_no_api_call(snap_dir):
    """When enabled=False, we must not make any API call at all."""
    with patch.object(fr, "_call_anthropic") as m:
        result = fr.generate_review(
            _big_briefing(), snap_dir,
            config={"fable_review": {"enabled": False}},
        )
    assert result["status"] == "disabled"
    assert m.call_count == 0
    # Cache is written even on disabled runs — for audit trail
    cache = json.loads((snap_dir / "fable_review.json").read_text())
    assert cache["status"] == "disabled"


def test_render_section_returns_empty_when_disabled(snap_dir):
    """Disabled runs produce no markdown section — the briefing tail is clean."""
    result = fr.generate_review(
        _big_briefing(), snap_dir,
        config={"fable_review": {"enabled": False}},
    )
    assert fr.render_review_section(result) == []


# ─── No API key ────────────────────────────────────────────────────────────


def test_missing_api_key_returns_status_and_helpful_text(snap_dir, monkeypatch):
    """When ANTHROPIC_API_KEY is unset AND no .env file has it, we return
    a friendly status message the briefing can render inline."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")
    result = fr.generate_review(_big_briefing(), snap_dir, config=_cfg())
    assert result["status"] == "no_api_key"
    assert "ANTHROPIC_API_KEY" in result["text"]


# ─── Empty briefing ────────────────────────────────────────────────────────


def test_empty_briefing_returns_empty_briefing_status(snap_dir, monkeypatch):
    """Defensive: don't waste tokens on a 5-char briefing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    result = fr.generate_review("tiny", snap_dir, config=_cfg())
    assert result["status"] == "empty_briefing"


# ─── Successful call ──────────────────────────────────────────────────────


def test_successful_call_returns_review_text(snap_dir, monkeypatch):
    """When the API returns a normal response, we extract the text and
    surface all audit fields (model, usage, request_id, elapsed_ms)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")

    with patch.object(fr, "_call_anthropic") as m:
        m.return_value = (True, {
            "text": "**Cross-section observations:**\n- Everything looks fine.",
            "error": None,
            "request_id": "req_abc",
            "usage": {"input_tokens": 25000, "output_tokens": 200},
        })
        result = fr.generate_review(_big_briefing(), snap_dir, config=_cfg())

    assert result["status"] == "ok"
    assert "Cross-section observations" in result["text"]
    assert result["model"] == "claude-opus-4-6"
    assert result["request_id"] == "req_abc"
    assert result["usage"]["input_tokens"] == 25000
    assert isinstance(result["elapsed_ms"], int)


def test_render_section_produces_briefing_markdown(snap_dir, monkeypatch):
    """Rendered markdown has the header, the response text, and a
    footer line with the model + latency."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    with patch.object(fr, "_call_anthropic") as m:
        m.return_value = (True, {
            "text": "**Themes I notice:**\n- Semis concentration.",
            "error": None, "request_id": "req_1",
            "usage": {"input_tokens": 100, "output_tokens": 10},
        })
        result = fr.generate_review(_big_briefing(), snap_dir, config=_cfg())

    section = "\n".join(fr.render_review_section(result))
    assert "## 🔍 Fable's second opinion" in section
    assert "Semis concentration" in section
    assert "claude-opus-4-6" in section
    assert "observations only" in section.lower()  # framing disclaimer


# ─── API errors ────────────────────────────────────────────────────────────


def test_api_error_returns_error_status_and_ships_placeholder(snap_dir, monkeypatch):
    """Fail-open: an API error MUST NOT raise; instead we return a
    placeholder the briefing renders inline. 5xx errors get a specific
    "server error" hint so the user knows to just wait for tomorrow's run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    with patch.object(fr, "_call_anthropic") as m:
        m.return_value = (False, {
            "text": "", "error": "HTTP 529: overloaded",
            "request_id": None, "usage": None,
        })
        result = fr.generate_review(_big_briefing(), snap_dir, config=_cfg())

    assert result["status"] == "api_error"
    # Post-hardening: 5xx hint says "server error" + "still shipped"
    assert "server error" in result["text"].lower()
    assert "shipped" in result["text"].lower()


def test_429_rate_limit_gets_specific_hint(snap_dir, monkeypatch):
    """Rate-limit errors get their own hint so the user knows it's
    transient — auto-retry on tomorrow's run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    with patch.object(fr, "_call_anthropic") as m:
        m.return_value = (False, {
            "text": "", "error": "HTTP 429: rate_limit_error",
            "request_id": None, "usage": None,
        })
        result = fr.generate_review(_big_briefing(), snap_dir, config=_cfg())
    assert result["status"] == "api_error"
    assert "rate-limited" in result["text"].lower() or "rate limit" in result["text"].lower()


def test_empty_response_falls_into_api_error_status(snap_dir, monkeypatch):
    """Model returned an empty text block — treat as an error path."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    with patch.object(fr, "_call_anthropic") as m:
        m.return_value = (True, {
            "text": "", "error": None,
            "request_id": "req_z", "usage": {},
        })
        result = fr.generate_review(_big_briefing(), snap_dir, config=_cfg())
    assert result["status"] == "api_error"


# ─── Cache writes on every path ────────────────────────────────────────────


def test_cache_is_written_even_on_failure(snap_dir, monkeypatch):
    """Every path — success, error, disabled — writes fable_review.json
    so we always have an audit record."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    with patch.object(fr, "_call_anthropic") as m:
        m.return_value = (False, {"text": "", "error": "boom",
                                    "request_id": None, "usage": None})
        fr.generate_review(_big_briefing(), snap_dir, config=_cfg())
    cache_path = snap_dir / "fable_review.json"
    assert cache_path.exists()
    cache = json.loads(cache_path.read_text())
    assert cache["status"] == "api_error"
    assert cache["error"] == "boom"


# ─── Config override ──────────────────────────────────────────────────────


def test_config_can_override_model(snap_dir, monkeypatch):
    """Setting `fable_review.model: claude-haiku-4-5-20251001` cuts cost."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    called_with = {}
    with patch.object(fr, "_call_anthropic") as m:
        def _capture(*args, **kwargs):
            called_with.update(kwargs)
            return (True, {"text": "ok", "error": None,
                            "request_id": None, "usage": {}})
        m.side_effect = _capture
        fr.generate_review(
            _big_briefing(), snap_dir,
            config=_cfg(model="claude-haiku-4-5-20251001"),
        )
    assert called_with["model"] == "claude-haiku-4-5-20251001"


def test_config_can_disable_via_enabled_false(snap_dir):
    """Explicit disable path is respected even when everything else is set."""
    result = fr.generate_review(
        _big_briefing(), snap_dir, config=_cfg(enabled=False),
    )
    assert result["status"] == "disabled"


# ─── Env-key loader ───────────────────────────────────────────────────────


def test_load_env_key_prefers_environment_over_dotenv(monkeypatch, tmp_path):
    """If ANTHROPIC_API_KEY is set in the env, that wins by default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert fr._load_env_key() == ("from-env", "env")


def test_load_env_key_reads_from_dotenv_when_env_unset(monkeypatch, tmp_path):
    """Falls back to parsing a .env file — matches etrade_auth.py behavior."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text('ANTHROPIC_API_KEY="from-dotenv"\nOTHER=x\n')
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", str(dotenv))
    assert fr._load_env_key() == ("from-dotenv", "dotenv")


def test_load_env_key_prefer_dotenv_overrides_env_var(monkeypatch, tmp_path):
    """Task #52: prefer='dotenv' forces the .env value to win.
    Used for auto-recovery when the shell-exported env var is stale."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stale-from-shell")
    dotenv = tmp_path / ".env"
    dotenv.write_text("ANTHROPIC_API_KEY=fresh-from-dotenv\n")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", str(dotenv))
    assert fr._load_env_key(prefer="dotenv") == ("fresh-from-dotenv", "dotenv")


def test_load_env_key_returns_none_when_nothing_available(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")
    assert fr._load_env_key() == (None, None)


def test_load_env_key_handles_export_prefix(monkeypatch, tmp_path):
    """User's .env may use bash `export KEY=value` syntax."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("export ANTHROPIC_API_KEY=sk-ant-fromexport\n")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", str(dotenv))
    assert fr._load_env_key() == ("sk-ant-fromexport", "dotenv")


def test_load_env_key_strips_inline_comment(monkeypatch, tmp_path):
    """Trailing `  # comment` on an unquoted value must be cut."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("ANTHROPIC_API_KEY=sk-ant-abc  # this is my key\n")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", str(dotenv))
    assert fr._load_env_key() == ("sk-ant-abc", "dotenv")


def test_load_env_key_strips_matching_double_quotes(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text('ANTHROPIC_API_KEY="sk-ant-quoted"\n')
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", str(dotenv))
    assert fr._load_env_key() == ("sk-ant-quoted", "dotenv")


def test_load_env_key_strips_matching_single_quotes(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("ANTHROPIC_API_KEY='sk-ant-singled'\n")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", str(dotenv))
    assert fr._load_env_key() == ("sk-ant-singled", "dotenv")


def test_load_env_key_handles_crlf_line_endings(monkeypatch, tmp_path):
    """Files edited on Windows have CRLF; we must not carry \\r into the key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_bytes(b"ANTHROPIC_API_KEY=sk-ant-crlf\r\n")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", str(dotenv))
    assert fr._load_env_key() == ("sk-ant-crlf", "dotenv")


def test_load_env_key_strips_environment_whitespace(monkeypatch):
    """A key set via env var with trailing whitespace still cleans up."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  sk-ant-envws  ")
    assert fr._load_env_key() == ("sk-ant-envws", "env")


def test_401_auto_recovery_retries_with_dotenv_key(snap_dir, monkeypatch, tmp_path):
    """Task #52: when the env-var-sourced key gets a 401 AND a
    DIFFERENT value exists in .env, the pipeline auto-retries with the
    .env key. This handles the "stale shell export" scenario:
    user's ~/.zshrc has an old export, the .env has the fresh key,
    diagnostic works from a clean shell but the Telegram bot inherits
    the stale env-var → pipeline sees 401."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stale-from-shell")
    dotenv = tmp_path / ".env"
    dotenv.write_text("ANTHROPIC_API_KEY=sk-ant-fresh-from-dotenv\n")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", str(dotenv))

    call_count = [0]
    def _mock_call(briefing_text, **kwargs):
        call_count[0] += 1
        used_key = kwargs.get("api_key")
        # First attempt uses the stale env-var key → 401
        if call_count[0] == 1:
            assert used_key == "sk-ant-stale-from-shell"
            return (False, {
                "text": "",
                "error": ('HTTP 401: {"type":"error","error":{"type":'
                          '"authentication_error"}}'),
                "request_id": None, "usage": None,
            })
        # Second attempt uses the fresh .env key → success
        assert used_key == "sk-ant-fresh-from-dotenv"
        return (True, {
            "text": "**Themes I notice:**\n- recovery worked",
            "error": None, "request_id": "req_recovered", "usage": {},
        })

    with patch.object(fr, "_call_anthropic", side_effect=_mock_call):
        result = fr.generate_review(_big_briefing(), snap_dir, config=_cfg())

    assert call_count[0] == 2, "auto-recovery should retry once with .env value"
    assert result["status"] == "ok", (
        "recovery must yield a normal ok status so the user sees the review"
    )
    assert "recovery worked" in result["text"]


def test_401_no_recovery_when_dotenv_value_matches_env(snap_dir, monkeypatch, tmp_path):
    """If the env-var key and the .env key are THE SAME value, don't
    retry — the retry would fail identically and just double the cost."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-same-value")
    dotenv = tmp_path / ".env"
    dotenv.write_text("ANTHROPIC_API_KEY=sk-ant-same-value\n")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", str(dotenv))

    call_count = [0]
    def _mock_call(briefing_text, **kwargs):
        call_count[0] += 1
        return (False, {
            "text": "",
            "error": "HTTP 401: authentication_error",
            "request_id": None, "usage": None,
        })

    with patch.object(fr, "_call_anthropic", side_effect=_mock_call):
        result = fr.generate_review(_big_briefing(), snap_dir, config=_cfg())

    assert call_count[0] == 1, "should not retry when values match"
    assert result["status"] == "api_error"


def test_401_no_recovery_when_only_env_var_present(snap_dir, monkeypatch):
    """If the env var is set but there's no .env fallback, we just fail."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stale")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_ENV", "/nonexistent/.env")

    call_count = [0]
    def _mock_call(briefing_text, **kwargs):
        call_count[0] += 1
        return (False, {
            "text": "", "error": "HTTP 401: authentication_error",
            "request_id": None, "usage": None,
        })

    with patch.object(fr, "_call_anthropic", side_effect=_mock_call):
        result = fr.generate_review(_big_briefing(), snap_dir, config=_cfg())

    assert call_count[0] == 1
    assert result["status"] == "api_error"


def test_401_error_yields_actionable_diagnosis_text(snap_dir, monkeypatch):
    """User feedback (2026-07-01): initial 401 error gave a raw HTTP dump.
    The message must now name the issue + how to diagnose."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    with patch.object(fr, "_call_anthropic") as m:
        m.return_value = (False, {
            "text": "",
            "error": ('HTTP 401: {"type":"error","error":{"type":'
                      '"authentication_error","message":"invalid x-api-key"}}'),
            "request_id": None,
            "usage": None,
        })
        result = fr.generate_review(_big_briefing(), snap_dir, config=_cfg())

    assert result["status"] == "api_error"
    assert "rejected" in result["text"].lower()
    assert "diagnose_fable" in result["text"]
