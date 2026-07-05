"""Render the pipeline's companion markdown reports (candidates_*.md,
when_to_enter_*.md) as HTML for the web app.

The pipeline ships two per-day markdown files alongside the briefing JSON:

    ~/Documents/briefings/candidates_<DATE>.md
    ~/Documents/briefings/when_to_enter_<DATE>.md

These are full per-company research/entry-trigger documents. The web app
exposes them as `/candidates/<DATE>` and `/when-to-enter/<DATE>` so the
user can browse them inside the same Pico/Shoelace shell, without
context-switching to a terminal/editor.

This module owns:
- `available_dates(kind)` — list dates with a report for the given kind
- `latest_date(kind)` — most recent date with a report
- `load_report(kind, date)` — return raw markdown bytes (None when missing)
- `render(text)` — markdown → HTML (markupsafe.Markup, XSS-safe-ish)

Notes:
- Uses the `markdown` library (~30KB, mature). Extensions enabled: `fenced_code`,
  `tables`, `sane_lists`, `attr_list` so tables and code blocks render properly.
- Emojis (🅿️ 🏆 🔥 ⏸ etc.) pass through unmodified.
- Contract IDs in the source markdown (e.g. `LITE_PUT_660_20260918`) stay as
  literal text inside `<code>` — they do NOT get the `pretty_contract` filter
  (v1 trade-off; the source already prefixes them with the readable chip).
- HARD RULE: never invent date / file content. When the file isn't on disk we
  return None and the route renders a 404 page.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from markupsafe import Markup

from .config import briefings_delivery


ReportKind = Literal["candidates", "when_to_enter"]


# ─── Filename conventions ──────────────────────────────────────────────────

# candidates_2026-06-30.md / when_to_enter_2026-06-30.md
_CANDIDATES_PREFIX = "candidates_"
_WHEN_TO_ENTER_PREFIX = "when_to_enter_"
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


def _prefix_for(kind: ReportKind) -> str:
    if kind == "candidates":
        return _CANDIDATES_PREFIX
    if kind == "when_to_enter":
        return _WHEN_TO_ENTER_PREFIX
    raise ValueError(f"Unknown report kind: {kind}")


def _report_path(kind: ReportKind, date: str) -> Path:
    """Resolve the on-disk path for a (kind, date) pair. Does NOT check existence."""
    prefix = _prefix_for(kind)
    return briefings_delivery() / f"{prefix}{date}.md"


# ─── Discovery ─────────────────────────────────────────────────────────────


def available_dates(kind: ReportKind) -> list[str]:
    """Return YYYY-MM-DD strings (descending) for which a report exists.

    Returns an empty list when the delivery dir is missing.
    """
    prefix = _prefix_for(kind)
    delivery = briefings_delivery()
    if not delivery.exists():
        return []
    out: list[str] = []
    for path in delivery.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if not name.startswith(prefix):
            continue
        tail = name[len(prefix):]
        m = _DATE_RE.match(tail)
        if m:
            out.append(m.group(1))
    out.sort(reverse=True)
    return out


def latest_date(kind: ReportKind) -> str | None:
    """The most recent date with a report of this kind, or None."""
    dates = available_dates(kind)
    return dates[0] if dates else None


def neighbor_dates(kind: ReportKind, date: str) -> dict[str, str | None]:
    """Return {'prev': YYYY-MM-DD or None, 'next': YYYY-MM-DD or None}.

    Mirrors the briefing-page navigation contract: `prev` is the older date,
    `next` is the newer date (since list is DESC).
    """
    dates = available_dates(kind)
    out: dict[str, str | None] = {"prev": None, "next": None}
    if date not in dates:
        return out
    idx = dates.index(date)
    if idx + 1 < len(dates):
        out["prev"] = dates[idx + 1]
    if idx - 1 >= 0:
        out["next"] = dates[idx - 1]
    return out


# ─── Load + render ─────────────────────────────────────────────────────────


def load_report(kind: ReportKind, date: str) -> str | None:
    """Read the markdown file for (kind, date). Returns None when missing.

    Fail-closed: no fabricated content (CLAUDE.md hard rule #19).
    """
    path = _report_path(kind, date)
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def report_path_str(kind: ReportKind, date: str) -> str:
    """The absolute path as a string — for footer attribution."""
    return str(_report_path(kind, date))


def render(text: str | None) -> Markup:
    """Convert markdown text to safe HTML.

    Enabled extensions:
        fenced_code — ``` blocks
        tables      — pipe-delimited tables
        sane_lists  — list items + paragraphs play nicely
        attr_list   — ``{: .css-class}`` on elements
        nl2br       — preserve single newlines as <br> (briefing source uses
                       hard wraps for emphasis); the cards depend on this
                       to keep each bullet on its own line in HTML view.

    Output is wrapped in `markupsafe.Markup` so Jinja doesn't double-escape.
    The `markdown` library itself does NOT sanitize HTML inside the source,
    but our source is generated by the pipeline (trusted) — not user input.
    """
    if not text:
        return Markup("")
    # Lazy-import so the module loads even if the dep is missing at install
    # time (tests can still run). The route will fail loudly if absent.
    import markdown as _md

    html = _md.markdown(
        text,
        extensions=["fenced_code", "tables", "sane_lists", "attr_list", "nl2br"],
        output_format="html",
    )
    return Markup(html)
