"""Shared line-exclusion rules for markdown annotators (CLAUDE.md rule #27).

Single source of truth for "which rendered briefing lines must NEVER receive
per-line annotations" (Parkev chips, tier badges, FV notes, ...). The rule-#27
exclusion list — Source:/Why:/Rationale:/Triggers:/Yield lines plus
continuation sub-bullets — previously lived as divergent copies inside each
annotator, and the gaps leaked: the 2026-07-30 briefing rendered `🅿️ no rec ·
🔵 Tier C` chips on LTO `- **Triggers:**` / `- **Yield/Cost:**` bullets (the
bare-token fallback extracted "LT" / "TRADE" / "LEAP" from prose) and attached
AT&T's fair value to MSFT's Triggers line (ticker `T` matched the capital T in
"Triggers").

Both chip annotators (`parkev_chip.annotate_parkev_chips`,
`position_tiers.annotate_tier_badges`) and `intrinsic_value.annotate_intrinsic`
import `is_excluded_line` from here — a labelled sub-line or continuation line
gets NO annotation, period. It inherits context from its card header.
"""

from __future__ import annotations

import re

# Bold-label sub-lines: "- **Triggers:** ...", "**Rationale:** ...".
# The label is everything inside the leading bold span, ending with a colon.
_LABEL_RE = re.compile(r"^\s*[-*]?\s*\*\*([^*\n]+?):?\*\*")

# Labels that mark a continuation/detail line under a card header. These lines
# describe the parent recommendation — they carry no ticker identity of their
# own and must never be annotated (rule #27 exclusion list).
EXCLUDED_LABELS = frozenset({
    "triggers",
    "rationale",
    "yield/cost",
    "yield",
    "cost",
    "source",
    "why",
    "reason",
    "risk",
    "note",
    "tax note",
    "earnings check",
    "wash-sale check",
    "trade-validator",
    "order",
    "order (two legs)",
    "target",
})

# Leading glyphs that mark continuation / annotation sub-lines.
_CONTINUATION_PREFIXES = ("↳", "⏸", "⚠", "⚖️", "⚖", "💵", "🎯", "🚨", "📊", "🔒")


def label_of(line: str) -> str | None:
    """Return the lower-cased bold label of a `- **Label:** ...` line, or None."""
    m = _LABEL_RE.match(line or "")
    if not m:
        return None
    return m.group(1).strip().rstrip(":").lower()


def is_excluded_line(line: str) -> bool:
    """True when this rendered line must not receive per-line annotations.

    Excluded (rule #27):
      - labelled sub-lines: Triggers:/Rationale:/Yield/Cost:/Source:/Why:/...
      - indented continuation lines (>= 2 leading spaces or a tab)
      - continuation glyph lines (↳ / ⏸ / ⚠ / ⚖️ / 💵 / ...)
      - italic transparency footers (_..._), tables, blockquotes, HTML
    NOT excluded: card headers, numbered action headers, `**Trade:**` ticket
    lines (those carry the card's ticker explicitly).
    """
    if not line or not line.strip():
        return True
    if line.startswith(("\t", "  ")):
        return True
    stripped = line.lstrip()
    if stripped.startswith(("_", "<", ">", "|")):
        return True
    if stripped.startswith("- "):
        stripped_body = stripped[2:].lstrip()
    else:
        stripped_body = stripped
    if stripped_body.startswith(_CONTINUATION_PREFIXES):
        return True
    label = label_of(line)
    if label is not None and label in EXCLUDED_LABELS:
        return True
    return False
