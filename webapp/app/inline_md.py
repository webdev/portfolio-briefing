"""Inline markdown → HTML for briefing summaries.

The pipeline writes action / opportunity / strategy summaries as markdown
strings — `**CLOSE** AMD_PUT_420 — +51% ...`, `\\`AMD\\`` for tickers, etc.
When Jinja renders these directly the asterisks and backticks leak through
as literal characters, making the UI look like a markdown source view.

This module:
1. `inline_md(text)` — converts the small set of markdown patterns used in
   briefing summaries to safe HTML (bold, inline code, em-dash spacing).
2. `strip_action_prefix(summary, kind, ident)` — when the summary starts
   with the redundant `**VERB** IDENT — ` pattern (we already show that in
   the badge above), drop it so the summary is just the details.
3. `summary_html(summary, kind=None, ident=None)` — combined: strips the
   redundant prefix AND renders inline markdown. The one filter templates
   should usually use.

All output is `markupsafe.Markup` so Jinja doesn't double-escape. Input is
escaped FIRST via `markupsafe.escape`, then approved patterns are
re-inserted as raw HTML. Safe against injection.
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape


# Bold: **X** → <strong>X</strong>  (non-greedy, prevents matching across paragraphs)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

# Inline code: `X` → <code>X</code>  (no backticks inside)
_CODE_RE = re.compile(r"`([^`]+)`")

# Italic: *X* → <em>X</em>  (must NOT be **X** — those are already bold;
# require single asterisk not adjacent to another asterisk)
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\s][^*]*?[^*\s]|[^*\s])\*(?!\*)")


def inline_md(text: str | None) -> Markup:
    """Convert inline markdown to safe HTML.

    Supported patterns (everything else passes through as plain text):
        **bold**      → <strong>bold</strong>
        `code`        → <code>code</code>
        *italic*      → <em>italic</em>

    The input is fully HTML-escaped first; only the approved markdown
    patterns are then re-rendered as HTML tags. Safe against XSS.
    """
    if not text:
        return Markup("")
    # Escape first so any < > & in raw text becomes &lt; &gt; &amp;
    escaped = str(escape(str(text)))
    # Now re-render the markdown patterns. Order matters: code first (so
    # the contents of `**X**` inside backticks stays literal), then bold,
    # then italic.
    out = _CODE_RE.sub(r'<code>\1</code>', escaped)
    out = _BOLD_RE.sub(r'<strong>\1</strong>', out)
    out = _ITALIC_RE.sub(r'<em>\1</em>', out)
    return Markup(out)


def strip_action_prefix(summary: str | None, kind: str | None = None,
                         ident: str | None = None) -> str:
    """Strip the redundant `**VERB** IDENT — ` prefix from a summary.

    The pipeline's action lines have the verb + identifier baked in:
        `**CLOSE** AMD_PUT_420_20261218 — +51% ($+3,823); ...`

    But the web app renders the verb + identifier as a BADGE above the
    summary line, so showing them again is redundant. This helper drops
    the prefix when it matches the action's known verb + ident, returning
    just the details (`+51% ($+3,823); ...`).

    Falls back to the original summary on any parse mismatch — fail-open
    so we never accidentally hide useful content.
    """
    if not summary:
        return ""
    text = str(summary)

    # Try: **VERB** IDENT — details
    if kind and ident:
        prefix_patterns = [
            f"**{kind}** {ident} — ",
            f"**{kind}** {ident} - ",
            f"**{kind}** `{ident}` — ",
            f"**{kind}** `{ident}` - ",
        ]
        for p in prefix_patterns:
            if text.startswith(p):
                return text[len(p):]

    # Fallback: just strip a leading **VERB** at the very start of the line
    # if the verb is known (handles cases where ident differs by case).
    if kind and text.startswith(f"**{kind}**"):
        after = text[len(f"**{kind}**"):].lstrip()
        # Drop a leading code-wrapped ident if present
        m = re.match(r"^`[^`]+`\s*[—-]\s*", after)
        if m:
            return after[m.end():]
        # Drop a leading bare ident + dash
        m = re.match(r"^[A-Z0-9_]+\s*[—-]\s*", after)
        if m:
            return after[m.end():]
        return after

    return text


def summary_html(summary: str | None, kind: str | None = None,
                  ident: str | None = None) -> Markup:
    """Combined: strip redundant action prefix AND render inline markdown.

    The canonical filter templates should use for any action / opportunity
    / strategy summary line. Usage:

        {{ a.summary | summary_html(a.kind, a.ident) }}
    """
    stripped = strip_action_prefix(summary, kind, ident)
    return inline_md(stripped)


def register_jinja_filters(env) -> None:
    """Register inline-markdown helpers as Jinja filters."""
    env.filters["inline_md"] = inline_md
    env.filters["strip_action_prefix"] = strip_action_prefix
    env.filters["summary_html"] = summary_html
