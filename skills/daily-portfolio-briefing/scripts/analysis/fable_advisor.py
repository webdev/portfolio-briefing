"""Fable Advisor (v2) — persona + memory shape.

This is the sibling of `fable_review.py`. Both render into the same
briefing section (`## 🔍 Fable's second opinion`), so downstream consumers
(webapp, Telegram bot) don't need to know which one produced the text.

Why a second module instead of a rewrite:

  - v1 (`fable_review`) is a locked-down critic — rigid 4-section prompt,
    quote-numbers-verbatim, 350-word ceiling, one-shot POST. Works, but
    reads like a compliance reviewer. No memory across days.
  - v2 (`fable_advisor`) is a private-wealth-advisor persona with a
    memory file. Reads today's briefing PLUS the last N daily reviews
    PLUS any user notes, then writes a review that names patterns in
    the user's behavior ("this is my third day flagging X"), respects
    standing directives ("I'm deferring the SPY hedge until VIX > 22"),
    and carries open questions forward.
  - Hard rule #33 (TDD, don't break stuff): v1 stays as the fallback. If
    v2 errors, aggregate.py falls through to v1.

Design principles preserved from v1 (hard rule #37):

  - **Review, not recommender.** Persona doesn't propose new trade
    tickets. It reviews the user's *response* to the deterministic
    layer's tickets. Naming "you've deferred AMD 12 days" is inside
    the boundary; writing "sell AMZN puts" would not be.
  - **Fail-open.** Any API error / memory-file corruption / timeout →
    briefing still ships. A placeholder footer is inserted; nothing
    blocks delivery.
  - **Fresh key auto-recovery.** Same env-var → .env fallback path as
    v1 (reuses `_load_env_key` from `fable_review`).

New in v2:

  - **Memory file** at `state/fable_advisor_memory.md` — small,
    human-readable, hand-editable. Persists across days. Contains:
      * `## Notes to Fable` — user-editable freeform. Standing directives,
        deferrals with reasons, preferences. Read verbatim into every
        review's prompt.
      * `## Recent reviews` — auto-maintained, most recent first.
        Fable's own past output. Bounded to last N (default 14).
  - **Persona prompt** — advisor, not critic. Loose structure (no fixed
    section headers). 400-600 word target (real advisor notes run
    longer than 350). Emphasizes continuity, behavioral pattern-naming,
    and carrying open questions forward.
  - **Cost.** ~35K input tokens (briefing ~25K + memory ~10K) + ~800
    output ≈ $0.60/run at Opus. Marginal delta vs v1's ~$0.43.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date as _date_cls, datetime, timezone
from pathlib import Path
from typing import Any


# ─── Defaults ──────────────────────────────────────────────────────────


DEFAULT_MODEL = "claude-opus-4-6"
DEFAULT_MAX_TOKENS = 1500            # advisor voice runs longer
DEFAULT_TIMEOUT_SEC = 90             # more context = slower response
DEFAULT_MAX_INPUT_CHARS = 200_000    # briefing + memory both included
DEFAULT_KEEP_REVIEWS = 14            # rolling window of past reviews

MEMORY_HEADER = "# Fable Advisor Memory"
USER_NOTES_HEADER = "## Notes to Fable (user-editable)"
RECENT_REVIEWS_HEADER = "## Recent reviews (auto-maintained, most recent first)"

_INITIAL_MEMORY_TEMPLATE = f"""{MEMORY_HEADER}

{USER_NOTES_HEADER}

_Lines here are read by Fable in every daily review. Use this to give
standing directives, deferrals with reasons, or preferences that should
carry across sessions. Fable will respect items you've documented here
and won't repeatedly flag them — but will surface anything not documented
that looks like a pattern._

_Examples of what to put here:_
- _"Deferring SPY hedge until VIX > 22" — Fable will stop flagging hedge deferral_
- _"Don't recommend TSLA CSPs — I'm exiting the position" — Fable will skip TSLA_
- _"AMD is a long-term hold, DCF numbers are broken — ignore FV reads on AMD" — Fable will discount AMD FV noise_

(add your directives below this line)


{RECENT_REVIEWS_HEADER}

_Fable auto-appends today's review here after each daily run. Bounded to
last {DEFAULT_KEEP_REVIEWS} reviews. Do not edit — the log is what gives
Fable her continuity across sessions._

"""


# The persona. Terse; every extra sentence is a chance for drift. Named
# "Fable" for continuity with the user's mental model, but the prompt
# doesn't dwell on the persona — it dwells on the goal.
SYSTEM_PROMPT = """\
You are Fable, a private wealth advisor reviewing your client George's
daily portfolio briefing. Your job is to give him the read a good advisor
would give — with continuity from prior sessions.

In your user message you'll receive three things, in order:

  1. `<user-notes>` — George's standing directives. Deferrals with
     reasons, preferences, positions he's decided to hold despite the
     pipeline's flags. RESPECT these. Do not flag anything he has
     documented a reason for.

  2. `<recent-reviews>` — your own past reviews from the last two weeks,
     most recent first. This is your memory. Use it. If you flagged
     something three days ago and it's still in today's briefing,
     surface it as "third day I've flagged this" — that carries weight
     that a fresh observation cannot. If George has NOT acted on a
     recurring flag AND has NOT added a directive explaining why, name
     the deferral as a pattern in his behavior.

  3. `<todays-briefing>` — the full markdown briefing.

Write today's review as prose, addressed to George directly, opening
with continuity if there is any (e.g., "Some continuity before I get
into today's briefing:") and then today's read. What earns space:

  - **Continuity with prior sessions.** Repeat flags gain weight from
    repetition. Ignored recommendations get named. Prior open questions
    that carry forward get repeated until answered.
  - **Patterns in George's behavior.** Deferral rates, ignored winners,
    hedges never placed. These are what a real advisor notices.
  - **What matters most today.** Order by priority, not by section. Lead
    with the one thing that would make the biggest difference to the
    book's health today.
  - **Named open questions.** End with 1-3 questions you'd want George
    to answer, especially ones that will carry to tomorrow's memo if he
    doesn't. Phrase them so a "yes/no/here's why" answer works.

Boundaries (hard):

  - **You do not recommend new trades.** The deterministic pipeline has
    already run and produced its recommendations. Your job is to help
    George *respond* to those recommendations, not to add to them. Never
    write "sell X at $Y" or "roll to Z" — instead review whether he's
    responding to what the pipeline is telling him.
  - **Quote numbers from today's briefing accurately.** If a number
    surprised you and might be stale, flag it as suspect ("the DCF read
    of $49 has been the same for at least the 12 days I've been
    tracking this — worth verifying against a fresh pull") but don't
    guess at what it should be.
  - **Respect standing directives.** If a note says "deferring AMD
    until X", DO NOT flag AMD as ignored. Score of continuity: you
    should recognize deferrals George has documented.
  - **No categorical market predictions.** Setups precede outcomes
    probabilistically. Never "SPY will drop" — instead "this setup
    historically precedes drawdowns."
  - **Length.** Aim for what a client would actually read — 400-600
    words is the sweet spot. Longer only when today has genuinely more
    surface area. Shorter when there's not much to say.

Sign off with "— Fable" so it reads as a person's note, not a report.
"""


# ─── Memory file ───────────────────────────────────────────────────────


def _default_memory_path() -> Path:
    """Resolve `<repo>/state/fable_advisor_memory.md`, creating the state
    dir if it doesn't exist."""
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate / "state" / "fable_advisor_memory.md"
    # Fallback: home dir if we can't find the repo root
    return Path.home() / ".fable_advisor_memory.md"


def _ensure_memory_file(path: Path) -> str:
    """Create the memory file with the initial template if it doesn't
    exist. Returns the file contents (either the fresh template or the
    existing content).
    """
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_INITIAL_MEMORY_TEMPLATE, encoding="utf-8")
        except OSError as e:
            print(f"[fable-advisor] could not create memory file at {path}: {e}",
                  file=sys.stderr)
            return _INITIAL_MEMORY_TEMPLATE
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"[fable-advisor] could not read memory file at {path}: {e}",
              file=sys.stderr)
        return _INITIAL_MEMORY_TEMPLATE


def _parse_memory(content: str) -> dict[str, Any]:
    """Split the memory file into its two logical sections.

    Returns:
        {
          "user_notes": str,       # verbatim contents of `## Notes to Fable`
          "recent_reviews": [       # parsed review entries, most recent first
            {"date": "YYYY-MM-DD", "text": "..."},
            ...
          ],
          "raw": str,               # the full file contents (for round-trip)
        }

    Parsing is deliberately forgiving — the file is user-editable, and
    any parsing error should degrade gracefully. Worst case, we send an
    empty memory to the LLM (the review is still produced).
    """
    result: dict[str, Any] = {
        "user_notes": "",
        "recent_reviews": [],
        "raw": content,
    }

    # Find the two section boundaries
    notes_idx = content.find(USER_NOTES_HEADER)
    reviews_idx = content.find(RECENT_REVIEWS_HEADER)

    if notes_idx >= 0 and reviews_idx > notes_idx:
        notes_body = content[notes_idx + len(USER_NOTES_HEADER):reviews_idx].strip()
        result["user_notes"] = notes_body
    elif notes_idx >= 0:
        # No reviews section — everything after Notes header is user notes
        result["user_notes"] = content[notes_idx + len(USER_NOTES_HEADER):].strip()

    if reviews_idx >= 0:
        reviews_body = content[reviews_idx + len(RECENT_REVIEWS_HEADER):]
        result["recent_reviews"] = _parse_review_entries(reviews_body)

    return result


def _parse_review_entries(reviews_body: str) -> list[dict[str, str]]:
    """Extract individual review entries from the Recent Reviews section.

    Format expected (what we write):
        ### 2026-07-01 — Fable review
        <body text>

        ### 2026-06-30 — Fable review
        <body text>

    Returns list of {"date": "YYYY-MM-DD", "text": "..."} dicts in the
    order they appear in the file (most recent first, since we prepend).
    """
    entries: list[dict[str, str]] = []
    lines = reviews_body.splitlines()
    current_date: str | None = None
    current_body: list[str] = []

    def _flush():
        if current_date:
            text = "\n".join(current_body).strip()
            if text:
                entries.append({"date": current_date, "text": text})

    for line in lines:
        stripped = line.strip()
        # Match `### YYYY-MM-DD — anything`
        if stripped.startswith("### ") and len(stripped) >= 14:
            candidate = stripped[4:14]
            if _looks_like_iso_date(candidate):
                _flush()
                current_date = candidate
                current_body = []
                continue
        current_body.append(line)
    _flush()
    return entries


def _looks_like_iso_date(s: str) -> bool:
    """Cheap check: YYYY-MM-DD shape. Doesn't validate calendar validity."""
    if len(s) != 10 or s[4] != "-" or s[7] != "-":
        return False
    return s[:4].isdigit() and s[5:7].isdigit() and s[8:10].isdigit()


def _write_memory_with_new_review(
    path: Path,
    parsed: dict[str, Any],
    new_review_text: str,
    new_review_date: str,
    keep_n: int,
) -> None:
    """Prepend today's review to the Recent Reviews section and rewrite
    the file. Trims to `keep_n` most recent.

    The Notes section is preserved verbatim — Fable never edits it. This
    is a hard contract: the user's standing directives must survive every
    memory-write, exactly as they typed them.

    Fail-open: any I/O error is swallowed. The briefing still ships; the
    memory just doesn't update this cycle.
    """
    try:
        new_entries: list[dict[str, str]] = [
            {"date": new_review_date, "text": new_review_text.strip()}
        ]
        # Filter existing entries for that same date (in case pipeline
        # runs twice in one day — overwrite, don't accumulate)
        for entry in parsed.get("recent_reviews") or []:
            if entry.get("date") != new_review_date:
                new_entries.append(entry)
        new_entries = new_entries[:keep_n]

        # Reassemble the file
        user_notes = (parsed.get("user_notes") or "").strip()
        rendered_reviews_body = "\n\n".join(
            f"### {e['date']} — Fable review\n\n{e['text']}"
            for e in new_entries
        )
        content = (
            f"{MEMORY_HEADER}\n\n"
            f"{USER_NOTES_HEADER}\n\n"
            f"{user_notes}\n\n\n"
            f"{RECENT_REVIEWS_HEADER}\n\n"
            f"{rendered_reviews_body}\n"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        print(f"[fable-advisor] memory write failed: {e}", file=sys.stderr)


# ─── LLM call ──────────────────────────────────────────────────────────


def _build_user_message(
    briefing_text: str,
    user_notes: str,
    recent_reviews: list[dict[str, str]],
) -> str:
    """Assemble the user message with the three tagged sections the
    system prompt references."""
    notes_block = user_notes.strip() or "(no standing directives)"

    if recent_reviews:
        reviews_block = "\n\n".join(
            f"[{e['date']}] {e['text']}" for e in recent_reviews
        )
    else:
        reviews_block = ("(no prior reviews — this is your first daily "
                         "review for this client)")

    return (
        f"<user-notes>\n{notes_block}\n</user-notes>\n\n"
        f"<recent-reviews>\n{reviews_block}\n</recent-reviews>\n\n"
        f"<todays-briefing>\n{briefing_text}\n</todays-briefing>"
    )


def _call_anthropic(
    user_message: str,
    *,
    model: str,
    max_tokens: int,
    timeout_sec: float,
    api_key: str,
) -> tuple[bool, dict[str, Any]]:
    """POST to Anthropic Messages API. Returns (ok, payload).

    Payload shape matches v1's `_call_anthropic` so the two modules can
    share the fallback dance in aggregate.py.
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
        "messages": [{"role": "user", "content": user_message}],
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


# ─── Public entry point ────────────────────────────────────────────────


def generate_advisor_review(
    briefing_markdown: str,
    snapshot_dir: str | Path,
    *,
    config: dict | None = None,
    memory_path: str | Path | None = None,
    today: _date_cls | None = None,
) -> dict[str, Any]:
    """Generate the advisor-style review over a finished briefing.

    Args:
        briefing_markdown: full briefing text.
        snapshot_dir: dir for audit cache (`<dir>/fable_advisor.json`).
        config: full config dict (reads `fable_advisor` section).
        memory_path: override for memory file location (tests).
        today: override for today's date (tests).

    Returns dict with:
        - status: "ok" | "disabled" | "no_api_key" | "api_error" | "empty_briefing"
        - text: str — review text ready to embed
        - model: str — model used
        - memory_used: bool — whether prior reviews were fed to LLM
        - review_count_before: int — how many past reviews were in memory
        - request_id, usage, elapsed_ms, generated_at — audit fields

    Fail-open: any error results in status != "ok" and a placeholder
    text that renders as a footer, not a blocker. Aggregate.py will
    then fall through to v1.
    """
    cfg = (config or {}).get("fable_advisor") or {}
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

    # Reuse v1's key loader — same env-var → .env fallback logic.
    try:
        from analysis.fable_review import _load_env_key  # type: ignore
    except ImportError:
        from .fable_review import _load_env_key  # type: ignore
    api_key, key_source = _load_env_key()
    if not api_key:
        result = {
            "status": "no_api_key",
            "text": (
                "_🔍 Fable's second opinion skipped: no `ANTHROPIC_API_KEY` "
                "in the environment or `.env`. Add it to enable._"
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
    keep_n = int(cfg.get("keep_reviews", DEFAULT_KEEP_REVIEWS))
    mem_path = Path(memory_path) if memory_path else _default_memory_path()

    # Load memory
    mem_content = _ensure_memory_file(mem_path)
    parsed_memory = _parse_memory(mem_content)
    user_notes = parsed_memory["user_notes"]
    recent_reviews = parsed_memory["recent_reviews"]
    review_count_before = len(recent_reviews)

    # Truncate briefing so the whole message fits within budget
    input_briefing = briefing_markdown[:max_input]
    truncated = len(briefing_markdown) > max_input

    user_message = _build_user_message(input_briefing, user_notes, recent_reviews)

    started_at = time.time()
    ok, payload = _call_anthropic(
        user_message,
        model=model,
        max_tokens=max_tokens,
        timeout_sec=timeout,
        api_key=api_key,
    )
    elapsed_ms = int((time.time() - started_at) * 1000)

    # Same 401 auto-recovery as v1 — retry with .env value if env-var
    # was rejected AND .env has a different value.
    if not ok and key_source == "env":
        err = payload.get("error", "")
        if "401" in err or "authentication_error" in err:
            try:
                from analysis.fable_review import _load_env_key as _lk  # type: ignore
            except ImportError:
                from .fable_review import _load_env_key as _lk  # type: ignore
            fallback_key, fallback_source = _lk(prefer="dotenv")
            if fallback_key and fallback_source == "dotenv" and fallback_key != api_key:
                print("[fable-advisor] env-var key failed with 401; retrying "
                      "with .env value (stale shell export detected)",
                      file=sys.stderr)
                started_at = time.time()
                ok, payload = _call_anthropic(
                    user_message,
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
        if "HTTP 401" in err or "authentication_error" in err or "invalid x-api-key" in err:
            hint = (
                "_🔍 Fable's second opinion skipped: `ANTHROPIC_API_KEY` "
                "was rejected (HTTP 401). Diagnose with "
                "`uv run python skills/daily-portfolio-briefing/scripts/"
                "diagnose_fable.py`._"
            )
        elif "HTTP 429" in err or "rate_limit" in err:
            hint = (
                "_🔍 Fable's second opinion skipped: rate-limited (HTTP 429). "
                "Briefing still shipped._"
            )
        elif "HTTP 5" in err:
            hint = (
                f"_🔍 Fable's second opinion skipped: Anthropic server error "
                f"({err[:80]}). Briefing still shipped._"
            )
        else:
            hint = f"_🔍 Fable's second opinion unavailable this cycle ({err[:120]})._"
        result = {
            "status": "api_error",
            "text": hint,
            "model": model,
            "error": err,
            "elapsed_ms": elapsed_ms,
            "memory_used": review_count_before > 0,
            "review_count_before": review_count_before,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _cache(snapshot_dir, result)
        return result

    review_text = payload.get("text", "").strip()
    if not review_text:
        result = {
            "status": "api_error",
            "text": "_🔍 Fable's second opinion returned empty response._",
            "model": model,
            "elapsed_ms": elapsed_ms,
            "memory_used": review_count_before > 0,
            "review_count_before": review_count_before,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _cache(snapshot_dir, result)
        return result

    # SUCCESS — write today's review back to memory
    today_str = (today or _date_cls.today()).isoformat()
    _write_memory_with_new_review(
        mem_path, parsed_memory, review_text, today_str, keep_n
    )

    result = {
        "status": "ok",
        "text": review_text,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "request_id": payload.get("request_id"),
        "usage": payload.get("usage"),
        "truncated": truncated,
        "memory_used": review_count_before > 0,
        "review_count_before": review_count_before,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _cache(snapshot_dir, result)
    return result


def render_review_section(result: dict) -> list[str]:
    """Render as briefing markdown lines. Same section header as v1 so
    the webapp / Telegram bot / doc view don't need to distinguish.

    The one signal that v2 was used: a small `advisor + memory (Nd)`
    tag in the footer instead of `generated in Nms`. That's how you can
    tell from the delivered briefing which flavor produced it.
    """
    status = result.get("status")
    if status == "disabled":
        return []
    if status == "ok":
        review_count = int(result.get("review_count_before") or 0)
        memory_tag = (
            f"advisor + memory ({review_count}d)" if review_count > 0
            else "advisor · first-run (no memory yet)"
        )
        return [
            "",
            "## 🔍 Fable's second opinion",
            "",
            "_A private-wealth-advisor read over today's briefing, with "
            "continuity from prior sessions. Observations only, not new "
            "trade recommendations._",
            "",
            result.get("text", ""),
            "",
            f"_Model: `{result.get('model')}` · {memory_tag} · "
            f"generated in {result.get('elapsed_ms', 0)} ms._",
            "",
        ]
    # Failure modes — text carries a user-friendly explanation
    text = result.get("text") or ""
    if not text:
        return []
    return ["", "## 🔍 Fable's second opinion", "", text, ""]


# ─── Cache (audit trail) ───────────────────────────────────────────────


def _cache(snapshot_dir: str | Path, result: dict) -> None:
    """Save the review payload to `<snapshot_dir>/fable_advisor.json`.

    Separate from v1's `fable_review.json` so both can coexist during
    the transition — if we ever run both in the same cycle for
    comparison, neither clobbers the other.
    """
    try:
        p = Path(snapshot_dir) / "fable_advisor.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"[fable-advisor] cache write failed: {e}", file=sys.stderr)
