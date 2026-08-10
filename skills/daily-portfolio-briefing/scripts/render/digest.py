"""Digest builder — two-document briefing split (2026-08-06).

User symptom (2026-08-06): "The document should be pretty quickly readable
by a person." Even in compact mode the full briefing runs 1200-1700 lines.

The fix is a two-document split:

  - ``briefing_full_<date>.md`` — the ENTIRE render, byte-identical to what
    aggregate produces. Verifiers run against it; the webapp parses it.
  - ``briefing_<date>.md`` (+ ``latest.md``) — a DIGEST derived from the full
    render (target <= 250 lines): portfolio health, Money Plan, Action List,
    Red Flags, Since Yesterday, top-N candidate entry tickets, Capital Plan,
    Benchmark & Attribution, a pointers block, and Fable's review.

Design constraints (CLAUDE.md):
  - The digest is a PURE SUBSET of the full render plus generated
    pointer/count lines. No content is rewritten — kept sections are kept
    verbatim (minus standalone italic transparency footers and the Health
    panel's boilerplate explainer sub-bullets, which are line-level drops,
    never rewrites).
  - Rule #19 — every count in a pointer line is COMPUTED from the full
    render. If a count can't be computed, the section is named without a
    count. Never a guess.
  - Rule #24 — deferred/capacity-gated candidate tickets keep their
    inline "⏸ Deferred (capacity gated)" tag in the digest, so a gated
    ticket can never read as green-lit.
  - Fail-open — if the full render can't be split (missing H1 or missing
    Action List section), ``build_digest`` returns the full render
    unchanged: the user gets the whole document, never a broken digest.

Single source of truth for the split. Wired in run_briefing.py (write +
delivery) behind ``render.digest`` (default ON).
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_MAX_CANDIDATES = 5

# Capital Plan is kept whole when at or under this many lines; above it the
# digest keeps only headers / bold summary rows / top-level bullets.
_CAPITAL_PLAN_KEEP_WHOLE_LINES = 40

# Verifier / meta panels never get a pointer line — they are pipeline
# self-checks, not content the reader needs a pointer to.
_META_SECTION_KEYS = (
    "coverage",              # S/R Coverage, RSI Coverage Check, quote coverage
    "tenorcap",
    "tenor-cap",
    "inconsistencies flagged",
    "appendix",
    "recommendation changes since last briefing",
    "live-data policy",
)

_ITALIC_LINE = re.compile(r"^_.*_\s*$")
_CANDIDATE_CARD = re.compile(r"^\*\*🎯 CANDIDATE")
_CANDIDATE_TICKET = re.compile(
    r"^\s*- .*(Deferred \(capacity gated\)|SELL \d+×|BUY `)"
)


# ── config accessors ─────────────────────────────────────────────────────


def digest_enabled(config: dict | None) -> bool:
    """render.digest — default ON. Fail-open to enabled on malformed config."""
    try:
        return bool(((config or {}).get("render") or {}).get("digest", True))
    except Exception:
        return True


def max_candidates(config: dict | None) -> int:
    """render.digest_max_candidates — default 5."""
    try:
        n = int(((config or {}).get("render") or {}).get(
            "digest_max_candidates", DEFAULT_MAX_CANDIDATES))
        return n if n > 0 else DEFAULT_MAX_CANDIDATES
    except Exception:
        return DEFAULT_MAX_CANDIDATES


# ── section splitting ────────────────────────────────────────────────────


def _norm(title: str) -> str:
    """Normalize a header line for matching: drop emoji/punct, lowercase."""
    t = title.lstrip("#").strip().lower()
    t = re.sub(r"[^a-z0-9&/'\- ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _split_sections(md: str) -> list[dict[str, Any]]:
    """Split the full render into ordered blocks at `# ` / `## ` headers.

    Returns [{"kind": "pre"|"h1"|"h2", "title": str|None, "lines": [...]}].
    Content before the first header lands in a "pre" block (the compact
    render puts Money Plan + Stalled Items BEFORE the H1, each with its own
    `## ` header, so "pre" is normally empty).
    """
    blocks: list[dict[str, Any]] = []
    cur: dict[str, Any] = {"kind": "pre", "title": None, "lines": []}
    for line in md.splitlines():
        is_h2 = line.startswith("## ")
        is_h1 = line.startswith("# ") and not is_h2
        if is_h1 or is_h2:
            blocks.append(cur)
            cur = {"kind": "h1" if is_h1 else "h2", "title": line,
                   "lines": [line]}
        else:
            cur["lines"].append(line)
    blocks.append(cur)
    return blocks


def _find(blocks: list[dict], key: str) -> dict | None:
    for b in blocks:
        if b["kind"] == "h2" and b["title"] and key in _norm(b["title"]):
            return b
    return None


def _trim_blank_edges(lines: list[str]) -> list[str]:
    out = list(lines)
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


# ── per-section filters (line-level drops, never rewrites) ───────────────


def _strip_italic_footers(lines: list[str]) -> list[str]:
    """Drop standalone italic transparency-footer lines (spec: transparency
    footers are NOT in the digest). Numbered actions / bullets untouched."""
    return [ln for ln in lines if not _ITALIC_LINE.match(ln)]


def _drop_childless_h3(lines: list[str]) -> list[str]:
    """Drop any ``###`` sub-header left with NO content beneath it (until the
    next header / end). Observed 2026-08-10: '### 🔄 Detected User-Executed
    Rolls (1)' rendered in the digest with zero bullets because its only item
    was a standalone italic line the footer strip removed — a header claiming
    a count of 1 with nothing under it. Either the items render or the header
    goes."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("### "):
            j = i + 1
            has_content = False
            while j < len(lines) and not lines[j].startswith("#"):
                if lines[j].strip():
                    has_content = True
                    break
                j += 1
            if not has_content:
                i += 1
                while i < len(lines) and not lines[i].startswith("#") \
                        and not lines[i].strip():
                    i += 1
                continue
        out.append(ln)
        i += 1
    return out


def _since_yesterday_filter(lines: list[str]) -> list[str]:
    """Since Yesterday: strip italic footers, then drop headers the strip
    left childless (2026-08-10 bug 2)."""
    return _drop_childless_h3(_strip_italic_footers(lines))


def _strip_health_explainers(lines: list[str]) -> list[str]:
    """Drop the Greeks panel's fixed educational sub-bullets ("What it
    means" / "Rule of thumb") — identical boilerplate every day; the
    measured values stay."""
    out = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("- **What it means:**") or s.startswith("- **Rule of thumb:**"):
            continue
        out.append(ln)
    return out


def _summarize_capital_plan(lines: list[str]) -> list[str]:
    """Capital Plan > ~40 lines → keep headers, bold summary rows and
    top-level bullets; drop indented sub-detail. <= 40 lines → whole.
    If the summary rows themselves overflow, truncate with a COMPUTED
    "…and N more rows" line (rule #19 — the count is measured, and the
    full rows all live in the full briefing)."""
    if len(lines) <= _CAPITAL_PLAN_KEEP_WHOLE_LINES:
        return lines
    kept = []
    for ln in lines:
        if (not ln.strip() or ln.startswith("#") or ln.startswith("**")
                or ln.startswith("_") or ln.startswith("- ")):
            kept.append(ln)
    max_rows = 30
    if len(kept) > max_rows:
        cut = kept[:max_rows - 2]
        dropped = sum(1 for ln in kept[max_rows - 2:] if ln.strip())
        kept = cut + ["", f"_…and {dropped} more Capital Plan row(s) — "
                          "see the full briefing._"]
    else:
        kept.append("")
        kept.append("_Capital Plan sub-detail trimmed — full version in "
                    "the full briefing._")
    return kept


# ── candidates top-N ─────────────────────────────────────────────────────


def _candidates_digest(block: dict | None, n: int, date: str | None) -> list[str]:
    """Top-N candidate entry tickets from the Candidate Trades section.

    Each card contributes its bold header line + its entry-ticket line
    (which carries the inline ⏸ Deferred tag when capacity-gated — rule
    #24 preserved verbatim). Remaining candidates collapse to one count
    line. If parsing fails, only a pointer line is emitted — never a
    partial guess.
    """
    date_sfx = f"_{date}" if date else ""
    pointer_only = [
        "## 🎯 Candidate Trades",
        "",
        f"_See the Candidate Trades section in briefing_full{date_sfx}.md "
        f"and candidates{date_sfx}.md._",
    ]
    if block is None:
        return []

    body = "\n".join(block["lines"])
    total: int | None = None
    m = re.search(r"Today's Candidates \((\d+)\)", body)
    if m:
        total = int(m.group(1))

    # Parse cards: a card starts at a `**🎯 CANDIDATE` line and runs until
    # the next card / sub-header / blank-separated block end.
    cards: list[tuple[str, str | None]] = []
    header: str | None = None
    ticket: str | None = None
    for ln in block["lines"]:
        if _CANDIDATE_CARD.match(ln):
            if header is not None:
                cards.append((header, ticket))
            header, ticket = ln, None
        elif header is not None and ticket is None and _CANDIDATE_TICKET.match(ln):
            ticket = ln
    if header is not None:
        cards.append((header, ticket))

    if not cards:
        return pointer_only

    if total is None:
        total = len(cards)
    shown = cards[:n]
    out = [f"## 🎯 Candidate Trades — Top {len(shown)} of {total}", ""]
    # Preserve the capacity banner (subset line) so deferred tickets can't
    # read as green-lit even at a glance.
    for ln in block["lines"]:
        if ln.startswith("**CAPACITY:"):
            out.append(ln)
            out.append("")
            break
    for hdr, tkt in shown:
        out.append(hdr)
        if tkt:
            out.append(tkt)
    remaining = total - len(shown)
    if remaining > 0:
        out.append("")
        out.append(
            f"_…and {remaining} more candidates — see "
            f"briefing_full{date_sfx}.md / candidates{date_sfx}.md_")
    return out


# ── omitted-section counts (rule #19: computed or omitted, never guessed) ─


def _count_watch(body: str) -> str | None:
    eq = len(re.findall(r"^- \*\*[A-Z][A-Z0-9.\-]*\*\* @ ", body, re.M))
    opts = len(re.findall(r"^📌 ", body, re.M))
    if not eq and not opts:
        return None
    need = len([v for v in re.findall(r"→ \*\*([A-Za-z_ ]+)\*\*", body)
                if v.strip().upper() != "HOLD"])
    s = f"{eq} equities · {opts} options"
    if need:
        s += f" · {need} need action"
    return s


def _count_lto(body: str) -> str | None:
    actionable = len(re.findall(r"^### \S+ \d+\. ", body, re.M))
    if not actionable and "signal(s)" not in body:
        return None
    gated = len(re.findall(r"^#### ⏸", body, re.M))
    skipped_at = body.find("Skipped")
    if skipped_at >= 0:
        gated += len(re.findall(r"^- \*\*", body[skipped_at:], re.M))
    s = f"{actionable} actionable"
    if gated:
        s += f" · {gated} gated"
    return s


def _count_scout(body: str) -> str | None:
    m = re.search(r"(\d+) analyzed across (\d+) themes", body)
    if m:
        return f"{m.group(1)} names · {m.group(2)} themes"
    return None


def _count_h3(body: str) -> str | None:
    n = len(re.findall(r"^### ", body, re.M))
    return f"{n} names" if n else None


def _count_bullets(body: str, label: str) -> str | None:
    n = len(re.findall(r"^- ", body, re.M))
    return f"{n} {label}" if n else None


def _count_strategy_upgrades(body: str) -> str | None:
    nums = re.findall(r"^### .*\((\d+)\)\s*$", body, re.M)
    if not nums:
        return None
    return f"{sum(int(x) for x in nums)} items"


_COUNTERS = [
    ("watch", _count_watch),
    ("long-term opportunities", _count_lto),
    ("thematic scout", _count_scout),
    ("technical read", _count_h3),
    ("per-ticker actions", lambda b: _count_bullets(b, "calls")),
    ("risk alerts", lambda b: _count_bullets(b, "alerts")),
    ("strategy upgrades", _count_strategy_upgrades),
]


def _section_pointer(title: str, body: str) -> str:
    label = title.lstrip("#").strip()
    norm = _norm(title)
    for key, fn in _COUNTERS:
        if key in norm:
            try:
                count = fn(body)
            except Exception:
                count = None  # rule #19 — omit, don't guess
            if count:
                return f"{label} ({count})"
            break
    return label


def _pointers_block(blocks: list[dict], kept_ids: set[int],
                    extras: dict | None) -> list[str]:
    extras = extras or {}
    out = ["## 📎 Full Detail", ""]
    files = [str(f) for f in (extras.get("companion_files") or []) if f]
    if files:
        out.append("_Full detail: " + " · ".join(files) + "_")
    omitted_parts: list[str] = []
    for i, b in enumerate(blocks):
        if b["kind"] != "h2" or i in kept_ids or not b["title"]:
            continue
        norm = _norm(b["title"])
        if any(k in norm for k in _META_SECTION_KEYS):
            continue
        if "candidate trades" in norm:
            continue  # already summarized as top-N above
        omitted_parts.append(
            _section_pointer(b["title"], "\n".join(b["lines"])))
    if omitted_parts:
        out.append("")
        out.append("_Also in the full briefing: "
                   + " · ".join(omitted_parts) + "_")
    return out


# ── main entry point ─────────────────────────────────────────────────────


def build_digest(full_md: str, config: dict | None = None,
                 extras: dict | None = None) -> str:
    """Derive the readable digest from the assembled full briefing markdown.

    Pure post-pass: splits ``full_md`` by section headers and keeps the
    decision-critical sections; everything else collapses into computed
    pointer/count lines. Fail-open — any structural surprise returns
    ``full_md`` unchanged (the user always gets a complete document).
    """
    try:
        return _build_digest_inner(full_md, config, extras)
    except Exception:
        return full_md


def _build_digest_inner(full_md: str, config: dict | None,
                        extras: dict | None) -> str:
    extras = extras or {}
    date = extras.get("date")
    blocks = _split_sections(full_md)

    h1_idx = next((i for i, b in enumerate(blocks) if b["kind"] == "h1"), None)
    action_block = _find(blocks, "action list")
    if h1_idx is None or action_block is None:
        # Can't establish the document shape — deliver the full render.
        return full_md

    kept_ids: set[int] = set()
    out_lines: list[str] = []

    def _emit(block: dict | None,
              filt=None) -> None:
        if block is None:
            return
        kept_ids.add(id_by_block[id(block)])
        lines = block["lines"]
        if filt is not None:
            lines = filt(lines)
        out_lines.extend(_trim_blank_edges(lines))
        out_lines.append("")

    id_by_block = {id(b): i for i, b in enumerate(blocks)}

    # 1.+2. Header zone, in DOCUMENT order: every H1 block (there can be
    # more than one — a "🚫 PRE-FLIGHT BLOCKED" banner H1 precedes the
    # Daily Briefing H1 when a blocking gate fires, and BOTH must survive)
    # plus the Money Plan / Stalled Items sections that render around them.
    last_h1 = max(i for i, b in enumerate(blocks) if b["kind"] == "h1")
    for i, b in enumerate(blocks[:last_h1 + 1]):
        if b["kind"] == "h1":
            kept_ids.add(i)
            out_lines.extend(_trim_blank_edges(b["lines"]))
            out_lines.append("")
        elif b["kind"] == "h2" and b["title"] and any(
                k in _norm(b["title"]) for k in ("money plan", "stalled items")):
            _emit(b)
        elif b["kind"] == "pre" and any(ln.strip() for ln in b["lines"]):
            # Unexpected prose before the first header — keep it (subset).
            out_lines.extend(_trim_blank_edges(b["lines"]))
            out_lines.append("")
    # Money Plan / Stalled Items rendered AFTER the last H1 (legacy shapes).
    for key in ("money plan", "stalled items"):
        blk = _find(blocks, key)
        if blk is not None and id_by_block[id(blk)] not in kept_ids:
            _emit(blk)

    # 3. Portfolio health (top holdings + net Greeks, minus boilerplate
    #    explainer sub-bullets)
    _emit(_find(blocks, "health"), _strip_health_explainers)

    # 3b. Sector Exposure one-line summary (2026-08-07 — George: "It seems
    #     like I'm pretty heavily invested in tech. Is it the right
    #     thing?"). Pure subset: the panel's bold ``**🧭 …**`` summary line
    #     is kept verbatim in the Health zone; the full table stays in the
    #     full briefing (the pointers block still lists the section).
    _sector_blk = _find(blocks, "sector exposure")
    if _sector_blk is not None:
        _sector_sum = next((ln for ln in _sector_blk["lines"]
                            if ln.startswith("**🧭")), None)
        if _sector_sum:
            out_lines.append(_sector_sum)
            out_lines.append("")

    # 4. Today's Action List — kept in full (minus standalone italic
    #    transparency footers). Watch-panel URGENT/CLOSE/ROLL items surface
    #    here by design, so the digest never loses an action.
    _emit(action_block, _strip_italic_footers)

    # 5. Red Flags & Priorities
    _emit(_find(blocks, "red flags"))

    # 6. Since Yesterday (if present), minus transparency footers; headers
    #    left childless by the strip are dropped too (2026-08-10 bug 2)
    _emit(_find(blocks, "since yesterday"), _since_yesterday_filter)

    # 7. Top-N candidate entry tickets
    cand_block = _find(blocks, "candidate trades")
    if cand_block is not None:
        kept_ids.add(id_by_block[id(cand_block)])
        cand_lines = _candidates_digest(
            cand_block, max_candidates(config), date)
        if cand_lines:
            out_lines.extend(cand_lines)
            out_lines.append("")

    # 8. Capital Plan (whole if short, summary rows if long)
    _emit(_find(blocks, "capital plan"), _summarize_capital_plan)

    # 9. Benchmark & Attribution
    _emit(_find(blocks, "benchmark"))

    # 10. Pointers block (companion files + computed counts for everything
    #     omitted)
    fable_block = _find(blocks, "fable")
    if fable_block is not None:
        kept_ids.add(id_by_block[id(fable_block)])
    out_lines.extend(_pointers_block(blocks, kept_ids, extras))
    out_lines.append("")

    # 11. Fable's second opinion (short, and the Telegram bot extracts it)
    if fable_block is not None:
        out_lines.extend(_trim_blank_edges(fable_block["lines"]))
        out_lines.append("")

    # Collapse runs of blank lines.
    digest: list[str] = []
    for ln in out_lines:
        if not ln.strip() and digest and not digest[-1].strip():
            continue
        digest.append(ln)
    return "\n".join(digest).rstrip() + "\n"
