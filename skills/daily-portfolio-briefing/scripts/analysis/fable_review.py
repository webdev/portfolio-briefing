"""Fable review — LLM second-opinion pass over the finished briefing.

Runs AFTER all deterministic rules have shaped the briefing. Reads the
rendered markdown, sends it to the Anthropic API (default: Haiku 4.5 —
the "fable-tier" closest equivalent since fable is a Cowork/CLI alias
not a public API model), and gets a short observations-only review.

Design principles:

  - **Reviews, doesn't recommend.** The system prompt forbids trade
    recommendations. Only observations, contradictions, cross-section
    pattern-matching, thematic risks. The deterministic rules stay the
    source of truth for actionables.

  - **Fail-open.** Any API error / timeout / rate-limit means the
    briefing still ships. A "🔍 Fable review unavailable this cycle"
    footer is inserted; nothing blocks delivery.

  - **Numbers must be quoted directly.** Prompt rule: don't restate
    numbers, quote them. Cuts hallucination surface substantially.

  - **Cached alongside briefing.** Every run saves the request + response
    to `<snapshot_dir>/fable_review.json` for audit. Lets you look back
    six months later and check if fable ever misread the data.

  - **Explicit toggle.** `briefing.yaml → fable_review.enabled: true`.
    Off by default in fresh installs so the pipeline never accidentally
    burns tokens for someone who hasn't opted in.

  - **Position at BOTTOM of briefing.** So it doesn't compete with the
    rule-based sections for attention. It's a footer, not a headliner.

Cost note: ~25K input tokens + ~500 output tokens per run. Haiku 4.5
pricing is ~$1/MTok input, $5/MTok output → about $0.03/day = ~$10/year.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ─── Config ─────────────────────────────────────────────────────────────


# Model: default is Opus 4.6 because the whole point of this review is
# cross-section REASONING (thematic pattern matching, contradiction
# detection, coverage/capacity alignment) — not classification. Haiku is
# fine for tagging; Opus is where the actual synthesis lives.
# Cost: ~25K input + ~700 output tokens per run. At Opus pricing
# (~$15/MTok in, $75/MTok out) that's ~$0.43/run = ~$155/year on a
# daily cadence. Override to sonnet/haiku in briefing.yaml if that hurts.
DEFAULT_MODEL = "claude-opus-4-6"
DEFAULT_MAX_TOKENS = 1200            # give opus room for actual reasoning
DEFAULT_TIMEOUT_SEC = 60             # opus is slower than haiku
DEFAULT_MAX_INPUT_CHARS = 120_000    # cap before send; larger briefings truncated


# The system prompt is the discipline. It's terse on purpose — every
# extra sentence is a chance for the model to drift. Rules are:
#   1. Observations only, no recommendations
#   2. Quote numbers directly from the briefing
#   3. Cross-section pattern matching is the job
#   4. Max 200 words
#   5. Format is fixed sections so downstream renderers can parse
SYSTEM_PROMPT = """\
You are reviewing a daily portfolio briefing for a wheel-strategy trader.

Your job is OBSERVATIONS, not RECOMMENDATIONS. The rule-based sections above
you are the source of truth for what to do. Your value-add is spotting
patterns and inconsistencies the rules structurally miss because they each
look at their own slice.

HARD RULES for your output:
1. Never recommend specific trades. No "sell X" or "roll Y". You review.
2. Quote numbers directly from the briefing — never restate or approximate.
   If uncertain, write "the briefing shows X" rather than a number.
3. Focus on cross-section patterns: contradictions between sections,
   sector/theme concentration, stale-vs-current inconsistencies,
   coverage/capacity misalignment.
4. 350 WORDS MAXIMUM. Prefer sharp, specific observations over long lists.
   If your draft exceeds that, delete the weakest points.
5. Fixed output format (four sections, in this order — omit any section
   that has nothing meaningful to say):

**Cross-section observations:** <bullet list, 1-3 items, each 2-3 sentences.
Look for signals that only appear when you read multiple sections together —
e.g. "the action queue and the coverage panel are giving different urgency
signals" or "the stale-items list is 8 days old but the take-profit rules
changed 2 days ago".>

**Themes I notice:** <bullet list, 1-2 items. Sector/thematic concentration,
correlation risk, hidden factor exposures that no single position flags on
its own.>

**Contradictions or stale items to review:** <bullet list, 0-3 items. Where
the rule-based sections disagree with each other, or where an item on the
queue is clearly obsolete given fresher data further down.>

**One thing that would meaningfully improve the book:** <one sentence,
observation not recommendation. E.g. "the Sep 18 cluster carries five of
six positions in AI-semis names — a sector event on that Friday would
concentrate the pain, not diversify it." Do NOT phrase as a trade rec.>

Do NOT add sections beyond these four. Do NOT add a summary or conclusion.
"""


# ─── Anthropic client wrapper ───────────────────────────────────────────


def _read_dotenv_key(env_path: str) -> str | None:
    """Parse ANTHROPIC_API_KEY out of a .env file. Handles all the common
    conventions: bash `export`, quoted values, inline comments, CRLF."""
    if not env_path or not Path(env_path).exists():
        return None
    try:
        raw = Path(env_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        k, _, v = line.partition("=")
        if k.strip() != "ANTHROPIC_API_KEY":
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        else:
            import re as _re
            m = _re.search(r"\s+#", v)
            if m:
                v = v[:m.start()]
        v = v.strip()
        return v if v else None
    return None


def _default_env_path() -> str | None:
    """Repeat the etrade_auth-style .env resolution."""
    p = os.environ.get("PORTFOLIO_BRIEFING_ENV")
    if p:
        return p
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists():
            return str(candidate / ".env")
    return None


def _load_env_key(prefer: str = "auto") -> tuple[str | None, str | None]:
    """Read ANTHROPIC_API_KEY. Returns (key, source) — source is the
    string "env" or "dotenv" so callers can diagnose which value was used.

    Precedence rules (task-specific):

      - prefer="auto" (default): env var wins IF it exists, else dotenv.
      - prefer="dotenv": .env wins IF it has a value, else env var.

    The "dotenv" preference is what recovery mode uses when the env-var
    key gets rejected as invalid — a common scenario when the process is
    launched from a shell that has a stale `export ANTHROPIC_API_KEY=...`
    in its rc file. Rather than force the user to hunt for the export,
    we simply retry with the .env value.
    """
    env_val = os.environ.get("ANTHROPIC_API_KEY")
    if env_val:
        env_val = env_val.strip()
    dotenv_path = _default_env_path()
    dotenv_val = _read_dotenv_key(dotenv_path) if dotenv_path else None

    if prefer == "dotenv":
        if dotenv_val:
            return dotenv_val, "dotenv"
        if env_val:
            return env_val, "env"
        return None, None

    # "auto" — env first, then dotenv
    if env_val:
        return env_val, "env"
    if dotenv_val:
        return dotenv_val, "dotenv"
    return None, None


def _call_anthropic(
    briefing_text: str,
    *,
    model: str,
    max_tokens: int,
    timeout_sec: float,
    api_key: str,
) -> tuple[bool, dict[str, Any]]:
    """POST to Anthropic Messages API. Returns (ok, payload).

    payload keys:
      - text: str — the model's response text (empty on failure)
      - error: str | None — human-readable error message on failure
      - request_id: str | None — Anthropic request-id for support
      - usage: dict | None — token usage from the response
    """
    try:
        import urllib.request as _url
        import urllib.error as _urlerr
    except ImportError as e:
        return (False, {"text": "", "error": f"stdlib import failed: {e}",
                        "request_id": None, "usage": None})

    body = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM_PROMPT,
        "messages": [{
            "role": "user",
            "content": f"Here is today's briefing markdown. Give your review per your rules.\n\n{briefing_text}",
        }],
    }).encode("utf-8")

    req = _url.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with _url.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            req_id = resp.headers.get("request-id")
    except _urlerr.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = ""
        return (False, {
            "text": "",
            "error": f"HTTP {e.code}: {err_body[:200]}",
            "request_id": None,
            "usage": None,
        })
    except _urlerr.URLError as e:
        return (False, {"text": "", "error": f"network error: {e.reason}",
                        "request_id": None, "usage": None})
    except (TimeoutError, OSError) as e:
        return (False, {"text": "", "error": f"timeout or OSError: {e}",
                        "request_id": None, "usage": None})

    # Extract the response text from Anthropic's content-block format
    content = data.get("content") or []
    text = "".join(
        block.get("text", "") for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()
    return (True, {
        "text": text,
        "error": None,
        "request_id": req_id,
        "usage": data.get("usage"),
    })


# ─── Top-level entry point ──────────────────────────────────────────────


def generate_review(
    briefing_markdown: str,
    snapshot_dir: str | Path,
    *,
    config: dict | None = None,
) -> dict[str, Any]:
    """Generate the fable review over a finished briefing.

    Returns a dict with:
      - status: "ok" | "disabled" | "no_api_key" | "api_error" | "empty_briefing"
      - text: str — the review text to embed in the briefing (may be an
        error/status message on the failure paths, always safe to render)
      - model: str — which model was used
      - request_id, usage, cached_to: audit fields

    Cached to `<snapshot_dir>/fable_review.json` on every call (including
    failures) so the audit trail is complete.
    """
    cfg = (config or {}).get("fable_review") or {}
    enabled = bool(cfg.get("enabled", False))
    if not enabled:
        result = {
            "status": "disabled",
            "text": "",
            "model": None,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _cache(snapshot_dir, result)
        return result

    if not briefing_markdown or len(briefing_markdown.strip()) < 100:
        result = {
            "status": "empty_briefing",
            "text": "",
            "model": None,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _cache(snapshot_dir, result)
        return result

    api_key, key_source = _load_env_key()
    if not api_key:
        result = {
            "status": "no_api_key",
            "text": (
                "_🔍 Fable review skipped: no `ANTHROPIC_API_KEY` in the "
                "environment or `.env`. Add it to enable._"
            ),
            "model": None,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _cache(snapshot_dir, result)
        return result

    model = str(cfg.get("model", DEFAULT_MODEL))
    max_tokens = int(cfg.get("max_tokens", DEFAULT_MAX_TOKENS))
    timeout = float(cfg.get("timeout_sec", DEFAULT_TIMEOUT_SEC))
    max_input = int(cfg.get("max_input_chars", DEFAULT_MAX_INPUT_CHARS))

    # Truncate very large briefings — the deterministic sections are what
    # matter most, and the front of the markdown carries the most signal.
    input_text = briefing_markdown[:max_input]
    truncated = len(briefing_markdown) > max_input

    started_at = time.time()
    ok, payload = _call_anthropic(
        input_text,
        model=model,
        max_tokens=max_tokens,
        timeout_sec=timeout,
        api_key=api_key,
    )
    elapsed_ms = int((time.time() - started_at) * 1000)

    # Auto-recovery: if the env-var-sourced key got a 401 AND a DIFFERENT
    # value is present in .env, retry with the .env value. This handles
    # the common scenario where the process is launched from a shell that
    # has a stale `export ANTHROPIC_API_KEY=...` in its rc file — the .env
    # value is the freshly-configured one; the shell's exported value is
    # months old and revoked. Task #51 auto-recovery. Log both attempts.
    if not ok and key_source == "env":
        err = payload.get("error", "")
        if "401" in err or "authentication_error" in err:
            fallback_key, fallback_source = _load_env_key(prefer="dotenv")
            if fallback_key and fallback_source == "dotenv" and fallback_key != api_key:
                print("[fable-review] env-var key failed with 401; retrying "
                      "with .env value (stale shell export detected)",
                      file=sys.stderr)
                started_at = time.time()
                ok, payload = _call_anthropic(
                    input_text,
                    model=model,
                    max_tokens=max_tokens,
                    timeout_sec=timeout,
                    api_key=fallback_key,
                )
                elapsed_ms = int((time.time() - started_at) * 1000)
                if ok:
                    key_source = "dotenv_after_env_401"

    if not ok:
        err = payload.get("error", "unknown error")
        # Add actionable guidance for the most common failure — invalid key
        if "HTTP 401" in err or "authentication_error" in err or "invalid x-api-key" in err:
            hint = (
                "_🔍 Fable review skipped: your `ANTHROPIC_API_KEY` was rejected "
                "by Anthropic (HTTP 401 authentication_error). Common causes: "
                "(1) key copied with extra whitespace or quotes — check the "
                "raw value in `.env`; (2) key was revoked or belongs to a "
                "different org; (3) key doesn't start with `sk-ant-`. Diagnose "
                "with `uv run python skills/daily-portfolio-briefing/scripts/"
                "diagnose_fable.py`._"
            )
        elif "HTTP 429" in err or "rate_limit" in err:
            hint = (
                "_🔍 Fable review skipped: Anthropic rate-limited us "
                "(HTTP 429). Briefing still shipped. Auto-retry on tomorrow's run._"
            )
        elif "HTTP 5" in err:
            hint = (
                f"_🔍 Fable review skipped: Anthropic server error ({err[:80]}). "
                f"Briefing still shipped._"
            )
        else:
            hint = f"_🔍 Fable review unavailable this cycle ({err[:120]})._"
        result = {
            "status": "api_error",
            "text": hint,
            "model": model,
            "error": err,
            "elapsed_ms": elapsed_ms,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _cache(snapshot_dir, result)
        return result

    review_text = payload.get("text", "").strip()
    if not review_text:
        result = {
            "status": "api_error",
            "text": "_🔍 Fable review returned empty response._",
            "model": model,
            "elapsed_ms": elapsed_ms,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _cache(snapshot_dir, result)
        return result

    result = {
        "status": "ok",
        "text": review_text,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "request_id": payload.get("request_id"),
        "usage": payload.get("usage"),
        "truncated": truncated,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _cache(snapshot_dir, result)
    return result


def render_review_section(result: dict) -> list[str]:
    """Render the review as briefing markdown lines (ready to append).

    Returns a list of lines with the section header + body. Empty list
    when the review is disabled or should be hidden.
    """
    status = result.get("status")
    if status == "disabled":
        return []
    if status == "ok":
        return [
            "",
            "## 🔍 Fable's second opinion",
            "",
            "_Automated LLM review of today's briefing — observations only, "
            "not trade recommendations. Fail-open: any API error is silently "
            "logged, not blocking._",
            "",
            result.get("text", ""),
            "",
            f"_Model: `{result.get('model')}` · "
            f"generated in {result.get('elapsed_ms', 0)} ms._",
            "",
        ]
    # Status is one of no_api_key / api_error / empty_briefing — text field
    # already contains a user-friendly single-line explanation.
    text = result.get("text") or ""
    if not text:
        return []
    return ["", "## 🔍 Fable's second opinion", "", text, ""]


# ─── Cache ──────────────────────────────────────────────────────────────


def _cache(snapshot_dir: str | Path, result: dict) -> None:
    """Save the review payload to `<snapshot_dir>/fable_review.json`.

    Fail-open: any I/O error is swallowed. The briefing pipeline should
    never crash because of an audit-cache write failure.
    """
    try:
        p = Path(snapshot_dir) / "fable_review.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"[fable-review] cache write failed: {e}", file=sys.stderr)
