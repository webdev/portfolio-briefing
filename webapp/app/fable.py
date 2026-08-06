"""Extract the fable-review section from a briefing markdown, plus load
the audit metadata from the snapshot dir.

Two data sources:
  - Briefing markdown at `~/Documents/briefings/briefing_<DATE>.md` —
    carries the review text as a `## 🔍 Fable's second opinion` section.
  - Snapshot audit cache at `<snapshot_dir>/fable_review.json` — carries
    model, request_id, usage, elapsed_ms, status.

The parser splits the review into its 4 fixed sections (Cross-section
observations / Themes I notice / Contradictions or stale items / One
thing that would meaningfully improve the book) so the UI can style
each independently.

Fail-open: any missing file / parsing error → returns None. The UI
just hides the review surface when there's nothing to show.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import briefing_md_path
from . import ingest


# ─── Constants ─────────────────────────────────────────────────────────────


_SECTION_HEADER = "## 🔍 Fable's second opinion"
# The four canonical sub-sections in the review body — order matters
# because the parser walks top-to-bottom and each `**X:**` marker ends
# the previous section.
_SUB_SECTIONS = [
    ("cross_section", "Cross-section observations"),
    ("themes",         "Themes I notice"),
    ("contradictions", "Contradictions or stale items"),
    ("improvement",    "One thing that would meaningfully improve"),
]

# Match a Markdown line that starts a sub-section: `**Cross-section observations:**`
_SUB_HEADER_RE = re.compile(r"^\*\*([^*]+?):\*\*\s*(.*)$")


# ─── Public API ────────────────────────────────────────────────────────────


def extract_review_section(md: str | None) -> str | None:
    """Return the raw fable-review section text (without the H2 header)
    from a full briefing markdown, or None when not present."""
    if not md or _SECTION_HEADER not in md:
        return None
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(_SECTION_HEADER):
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start, len(lines)):
        if lines[j].startswith("## ") and not lines[j].startswith(_SECTION_HEADER):
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def parse_review_sections(review_text: str | None) -> dict[str, list[str]]:
    """Split the review body into its 4 canonical sub-sections.

    Returns dict with keys `cross_section`, `themes`, `contradictions`,
    `improvement`. Values are lists of bullet-point strings (for the
    first three) or a single-item list (for `improvement`, which is
    a single sentence).

    Missing sections come back as empty lists so the template doesn't
    have to null-check.
    """
    out: dict[str, list[str]] = {key: [] for key, _ in _SUB_SECTIONS}
    if not review_text:
        return out

    # Trim off the italic disclaimer, model-attribution footer, etc.
    # We only want the four `**Heading:**` blocks.
    current_key: str | None = None
    current_body: list[str] = []
    lines = review_text.splitlines()

    def _flush():
        nonlocal current_body
        if current_key and current_body:
            # Convert list-of-lines into list-of-bullets. Each bullet
            # starts with `- ` in the markdown; join continuation lines.
            body = "\n".join(current_body).strip()
            if not body:
                return
            if current_key == "improvement":
                # Single-sentence field — keep as one string
                out[current_key] = [body]
            else:
                bullets = []
                for chunk in body.split("\n- "):
                    chunk = chunk.strip().lstrip("- ").strip()
                    if chunk:
                        bullets.append(chunk)
                out[current_key] = bullets
        current_body = []

    for line in lines:
        m = _SUB_HEADER_RE.match(line.strip())
        if m:
            heading = m.group(1).strip().lower()
            # Match to a canonical key (fuzzy — starts-with match handles
            # phrasing drift from the model)
            matched_key = None
            for key, label in _SUB_SECTIONS:
                if label.lower() in heading:
                    matched_key = key
                    break
            if matched_key:
                _flush()
                current_key = matched_key
                # The rest of the line might already have content
                rest = m.group(2).strip()
                if rest:
                    current_body.append(rest)
                continue
        if current_key:
            current_body.append(line)
    _flush()
    return out


def load_review_metadata(date: str) -> dict[str, Any] | None:
    """Read `<snapshot_dir>/fable_review.json` for `date`.

    The path resolution matches the pipeline: `snapshots_root() / date /
    fable_review.json`. Returns None when the file is missing or
    unparseable.
    """
    if not date:
        return None
    p = ingest.snapshots_root() / date / "fable_review.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_review_for_date(date: str) -> dict[str, Any]:
    """Convenience: assemble everything the UI needs in one shot.

    Returns:
        {
          "present": bool,           # True when the section exists in .md
          "raw_text": str | None,    # full review body without the H2
          "sections": {...},         # parsed sub-sections
          "metadata": {...} | None,  # model / usage / elapsed_ms / status
        }
    """
    # Two-document split: the Fable section is in BOTH documents, but parse
    # the full render (canonical; falls back for pre-split history).
    md_path = briefing_md_path(date)
    md = None
    if md_path.exists():
        try:
            md = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            md = None
    raw = extract_review_section(md)
    sections = parse_review_sections(raw)
    return {
        "present": raw is not None,
        "raw_text": raw,
        "sections": sections,
        "metadata": load_review_metadata(date),
    }


def first_observation(review: dict[str, Any]) -> str | None:
    """Return a one-sentence preview for the Home page callout.

    Prefers the first bullet from Cross-section observations; falls back
    to the improvement statement, then the first theme.
    """
    sections = review.get("sections") or {}
    for key in ("cross_section", "themes", "improvement", "contradictions"):
        bullets = sections.get(key) or []
        if bullets:
            text = bullets[0].strip()
            # Trim to a reasonable preview length (first sentence-ish)
            m = re.search(r"[.!?](\s|$)", text[:280])
            if m:
                return text[:m.end()].strip()
            return text[:280].rstrip() + ("…" if len(text) > 280 else "")
    return None
