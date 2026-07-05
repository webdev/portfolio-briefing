"""Counterpoint extractor for briefing actions.

The pipeline writes the counterpoint layer inline in the briefing
markdown (look for ``⚖️ **Counterpoint:**`` sub-bullets under each
numbered action). It does NOT currently store them in the JSON.

Until the JSON schema gains a ``counterpoint`` field on each action,
we parse the markdown lazily per request. The work is small (~25 KB
per briefing, parsed once and cached in-memory per file path + mtime).

Returned shape:
    {
        action_key: "concise counterpoint sentence",
        ...
    }

action_key is the same ``KIND:IDENT`` used in the JSON ``actions`` list
so the route can look up a counterpoint by the action it's rendering.

Empty-state: if a markdown is missing or unparseable, returns {} and
the disclosure simply doesn't render.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .config import briefings_delivery


# Numbered action header: "1. **CLOSE** AMD_PUT_420_20261218 — ..."
_ACTION_HEADER_RE = re.compile(
    r"^\s*\d+\.\s+\*\*([^*]+)\*\*\s+([A-Z0-9_:.\-]+)"
)
# Counterpoint sub-bullet: "   - ⚖️ **Counterpoint:** the hedge costs..."
_COUNTERPOINT_RE = re.compile(
    r"^\s+-\s+⚖️\s+\*\*Counterpoint:\*\*\s+(.+)$"
)


@lru_cache(maxsize=64)
def _parse_md_cached(path_str: str, mtime: float) -> dict[str, str]:  # noqa: ARG001
    """Parse one briefing markdown. Cached by (path, mtime) — if the
    file changes the cache key changes and we re-parse."""
    path = Path(path_str)
    try:
        md = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out: dict[str, str] = {}
    current_key: str | None = None
    for line in md.splitlines():
        m_header = _ACTION_HEADER_RE.match(line)
        if m_header:
            kind = m_header.group(1).strip().split(" ")[0]  # "EXECUTE ROLL" → "EXECUTE"
            # Use the full first word(s) of kind matching the JSON shape.
            # JSON shapes: CLOSE / HEDGE / ROLL_OUT_AND_UP / DEFENSIVE_ROLL etc.
            # MD shapes:   "CLOSE" / "HEDGE" / "EXECUTE ROLL" / "DEFENSIVE ROLL"
            kind_full = m_header.group(1).strip().replace(" ", "_")
            ident = m_header.group(2)
            current_key = f"{kind_full}:{ident}"
            continue
        m_cp = _COUNTERPOINT_RE.match(line)
        if m_cp and current_key:
            out[current_key] = m_cp.group(1).strip()
            current_key = None  # one counterpoint per action
    return out


def counterpoints_for_date(date: str) -> dict[str, str]:
    """Return the counterpoint map for one briefing date, keyed by
    action key. Returns {} when no markdown exists.

    The map keys are ``KIND:IDENT`` but the action's JSON ``key`` field
    may use a slightly different KIND spelling (EXECUTE vs EXECUTE_ROLL).
    Callers should try both lookups: first by exact `key`, then by
    `KIND:IDENT` fallback.
    """
    if not date:
        return {}
    path = briefings_delivery() / f"briefing_{date}.md"
    if not path.exists():
        return {}
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    return _parse_md_cached(str(path), mtime)


def counterpoint_for_action(date: str, action_key: str, ident: str | None = None,
                             kind: str | None = None) -> str | None:
    """Look up one counterpoint. Tries exact action_key, then variants."""
    if not date or not action_key:
        return None
    cmap = counterpoints_for_date(date)
    if action_key in cmap:
        return cmap[action_key]
    # Try kind variants — JSON may say "EXECUTE_ROLL" while MD says "EXECUTE ROLL"
    if kind and ident:
        variants = [
            f"{kind}:{ident}",
            f"{kind.replace('_', ' ')}:{ident}",
            f"{kind.replace(' ', '_')}:{ident}",
        ]
        for k in variants:
            if k in cmap:
                return cmap[k]
    return None
