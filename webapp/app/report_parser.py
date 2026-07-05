"""Structured parser for the pipeline's per-day companion reports.

Turns `candidates_<DATE>.md` and `when_to_enter_<DATE>.md` from raw markdown
into typed dicts the web app can render as proper Bloomberg/Linear-style
cards (chips, badges, action icons) — not just `<p>` and `<strong>` from
python-markdown.

Both report formats are pipeline-generated and follow strict patterns; we
parse them line-by-line rather than running a full markdown AST.

Returns one shape:

    {
        "capacity": "coverage 0.07x | cash 5.2% | obligation 76% NLV | ...",
        "title": "Candidate Research — Tuesday, June 30, 2026 · 02:42 PM",
        "subtitle": "Per-company research across every Scout theme...",
        "summary": "124 companies analyzed: 42 🎯 CANDIDATE · 1 ⏸ held by RSI...",
        "etf_line": "..." (when_to_enter only — top-of-doc one-line ETF summary)
        "sections": [
            {
                "kind": "h1" | "h2" | "h3",   # theme depth
                "title": "🔭 Semis",
                "subtitle": "Companies: NVDA, AMD... · ETFs: SMH, SOXX",
                "cards": [
                    {
                        "status": "WATCH" | "CANDIDATE" | "HELD" | "AVOID"
                                | "ENTRY_NOW" | "WAIT" | "NEUTRAL",
                        "status_icon": "👀" | "🎯" | "⏸" | "🔴" | "🟢" | "🟡",
                        "status_label": "WATCH" | "ENTRY NOW — CSP" | ...,
                        "ticker": "NVDA",
                        "price": "$200.09",
                        "is_etf": False,
                        "metrics": [           # parsed from the first bullet
                            {"label": "RSI", "value": "45", "tone": "neutral"},
                            {"label": "IV rank", "value": "81", "tone": "warn"},
                            ...
                        ],
                        "fair_value": "DCF $249 (+24% vs spot) · analyst PT $318 (+59%, n=26)",
                        "verdict": "WATCH — already concentrated...",
                        "read": "RSI 45, above 200-SMA (+5%)...",  # when_to_enter only
                        "trigger": "SELL 1× $185P exp Aug 07 '26 (38 DTE)...",
                        "earnings": "2026-08-26 (57d away)",
                        "deferred_note": "⏸ Capacity-gated...",
                        "extras": []  # any unrecognized bullets, rendered verbatim
                    },
                ],
            },
        ],
    }
"""

from __future__ import annotations

import re
from typing import Any, Literal


ReportKind = Literal["candidates", "when_to_enter"]


# ─── Patterns ──────────────────────────────────────────────────────────────

# candidates: **👀 WATCH · `AMAT` · $723.00**  (optional trailing badges)
_CAND_HEADER_RE = re.compile(
    r"^\*\*"
    r"(?P<icon>[\U00010000-\U0010ffff☀-⟿⏸]+)\s+"
    r"(?P<label>[A-Z][A-Z0-9 ()/\-—·]+?)\s*·\s*"
    r"`(?P<ticker>[A-Z][A-Z0-9.\-]{0,7})`\s*·\s*"
    r"\$(?P<price>[\d,]+(?:\.\d+)?)"
    r"\*\*"
    r"(?P<badges>.*?)\s*$"
)

# when_to_enter: #### `IGV` · $90.60 · **🟢 ENTRY NOW — CSP ⚠ no third-party rec · ⏸ DEFERRED** 🪙 **ETF**
_WTE_HEADER_RE = re.compile(
    r"^####\s+`(?P<ticker>[A-Z][A-Z0-9.\-]{0,7})`\s*·\s*"
    r"\$(?P<price>[\d,]+(?:\.\d+)?)"
    r"\s*·\s*\*\*"
    r"(?P<icon>[\U00010000-\U0010ffff☀-⟿⏸]+)\s+"
    r"(?P<label>[^*]+?)"
    r"\*\*"
    r"(?P<etf_marker>.*?)?\s*$"
)

# H1: # Title — date
_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
# H1 theme separator: # ━━━ The AI Buildout ━━━
_H1_THEME_RE = re.compile(r"^#\s+━+\s+(.+?)\s+━+\s*$")
# H2 sector: ## 🔭 Semis  or  ## 🪙 ETF Benchmarks
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
# H3 sub-section: ### Semis (2)
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")

# Bullets under cards begin with "  - " (candidates) or "- " (when_to_enter)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")

# Capacity banner on line 1
_CAPACITY_RE = re.compile(r"^CAPACITY:\s*(.+?)\s*$")

# Metric chip parser — pulls out RSI, IV rank, distance, drawdown, 5d move
_METRIC_PATTERNS = [
    (re.compile(r"RSI\s+(\d+)\s*([🔴🟡🟢]?\s*\w*)?"), "RSI"),
    (re.compile(r"IV rank\s+(\d+)"), "IV"),
    (re.compile(r"5d\s+([+-]?\d+(?:\.\d+)?%)"), "5d"),
    (re.compile(r"drawdown\s+(\d+%)"), "dd"),
    (re.compile(r"(\d+)%\s+(?:off highs|pullback)"), "off-hi"),
]


def _classify_status(icon: str, label: str) -> tuple[str, str]:
    """Map (icon, label) → (canonical status, css tone).

    Tones map to existing chip styles in app.css:
        ok    → green (positive setup)
        warn  → yellow (extended / wait)
        muted → gray (neutral / watch)
        bad   → red (avoid / blocked)
        info  → blue/cyan (deferred / capacity-gated)
    """
    label_upper = label.upper()
    if "AVOID" in label_upper:
        return ("AVOID", "bad")
    if "STRONG BUY" in label_upper or "TOP CONVICTION" in label_upper:
        if "DEFERRED" in label_upper or "⏸" in label:
            return ("ENTRY_NOW_DEFERRED", "info")
        return ("STRONG_BUY", "ok")
    if "ENTRY NOW" in label_upper:
        if "DEFERRED" in label_upper or "⏸" in label:
            return ("ENTRY_NOW_DEFERRED", "info")
        return ("ENTRY_NOW", "ok")
    if "TRIAL" in label_upper:
        if "DEFERRED" in label_upper or "⏸" in label:
            return ("TRIAL_DEFERRED", "info")
        return ("TRIAL", "warn")
    if "CANDIDATE" in label_upper or "🎯" in icon:
        return ("CANDIDATE", "ok")
    if "WAIT" in label_upper:
        return ("WAIT", "warn")
    if "WATCH" in label_upper or "NEUTRAL" in label_upper:
        return ("WATCH", "muted")
    if "HELD" in label_upper or "⏸" in icon:
        return ("HELD", "info")
    return (label_upper.replace(" ", "_"), "muted")


def _parse_metrics(line: str) -> list[dict[str, str]]:
    """Extract RSI / IV / drawdown / 5d / pullback chips from a metrics bullet."""
    out: list[dict[str, str]] = []
    for pat, label in _METRIC_PATTERNS:
        m = pat.search(line)
        if not m:
            continue
        value = m.group(1)
        tone = "muted"
        if label == "RSI":
            try:
                rsi = int(value)
                if rsi >= 70:
                    tone = "bad"  # overbought → block for new puts
                elif rsi >= 60:
                    tone = "warn"
                elif rsi < 30:
                    tone = "warn"  # oversold for CCs / falling-knife
                elif 35 <= rsi <= 55:
                    tone = "ok"  # favored band
            except ValueError:
                pass
        elif label == "IV":
            try:
                iv = int(value)
                if iv >= 80:
                    tone = "warn"  # elevated
                elif iv >= 50:
                    tone = "ok"
            except ValueError:
                pass
        elif label == "5d":
            tone = "ok" if value.startswith("+") else ("bad" if value.startswith("-") else "muted")
        out.append({"label": label, "value": value, "tone": tone})

    # Trend vs 200-SMA — separate parsing since it spans 2-3 patterns
    trend_match = re.search(
        r"(well above 200-SMA|above 200-SMA|near 200-SMA|below 200-SMA)\s*\(([+\-]\d+%)\)",
        line,
    )
    if trend_match:
        trend = trend_match.group(1).replace("200-SMA", "").strip()
        pct = trend_match.group(2)
        tone = "ok" if "above" in trend_match.group(1) else (
            "muted" if "near" in trend_match.group(1) else "bad"
        )
        out.append({"label": "MA200", "value": f"{pct} ({trend or 'at'})", "tone": tone})

    if "at/near 52w highs" in line:
        out.append({"label": "52w", "value": "at highs", "tone": "warn"})
    elif "at/near 52w lows" in line:
        out.append({"label": "52w", "value": "at lows", "tone": "bad"})

    return out


def _strip_prefix(s: str, prefix: str) -> str:
    return s[len(prefix):].strip() if s.lower().startswith(prefix.lower()) else s


def _classify_bullet(text: str) -> tuple[str, str]:
    """(field_name, cleaned_value). Field names match the dict keys above."""
    t = text.strip()

    # **Read:** … / **Trigger:** … (when_to_enter)
    m = re.match(r"^\*\*([A-Za-z][\w ]*):\*\*\s*(.+)$", t)
    if m:
        key = m.group(1).strip().lower().replace(" ", "_")
        val = m.group(2).strip()
        return (key, val)

    if t.startswith("💵 FV:") or t.startswith("FV:"):
        return ("fair_value", _strip_prefix(t.lstrip("💵 ").strip(), "FV:"))
    if t.startswith("Verdict:"):
        return ("verdict", _strip_prefix(t, "Verdict:"))
    if t.startswith("Earnings:"):
        return ("earnings", _strip_prefix(t, "Earnings:"))
    if "⏸" in t and ("Capacity" in t or "capacity" in t):
        return ("deferred_note", t)

    # Metrics bullet — has RSI in it
    if "RSI" in t and ("200-SMA" in t or "IV rank" in t or "drawdown" in t):
        return ("_metrics_line", t)

    return ("extra", t)


def _parse_cards(
    lines: list[str], idx: int, header_re: re.Pattern, indent: int = 0
) -> tuple[list[dict[str, Any]], int]:
    """Walk lines starting at idx, parse all cards until the next section
    header (# / ## / ###) or EOF. Return (cards, new_idx)."""
    cards: list[dict[str, Any]] = []
    i = idx
    while i < len(lines):
        line = lines[i]
        # New section starts? Stop.
        if _H1_RE.match(line) or _H2_RE.match(line) or _H3_RE.match(line):
            break

        m = header_re.match(line)
        if not m:
            i += 1
            continue

        status, tone = _classify_status(m.group("icon"), m.group("label"))
        is_etf = False
        if header_re is _WTE_HEADER_RE:
            etf_tail = (m.group("etf_marker") or "").upper()
            is_etf = "ETF" in etf_tail
        badges_raw = ""
        if "badges" in (m.groupdict() or {}):
            badges_raw = (m.group("badges") or "").strip()
        # Parse small badges off the trailing tail
        flags = {
            "rsi_favourable": "RSI favourable" in badges_raw or "RSI favorable" in badges_raw,
            "top_conviction": "TOP CONVICTION" in badges_raw,
            "no_third_party": "no third-party rec" in (m.group("label") if m.group("label") else "")
                or "no third-party rec" in badges_raw,
        }
        card: dict[str, Any] = {
            "status": status,
            "status_tone": tone,
            "status_icon": m.group("icon"),
            "status_label": m.group("label").strip(),
            "ticker": m.group("ticker"),
            "price": "$" + m.group("price"),
            "is_etf": is_etf,
            "badges_raw": badges_raw,
            "flags": flags,
            "metrics": [],
            "fair_value": None,
            "verdict": None,
            "read": None,
            "trigger": None,
            "earnings": None,
            "deferred_note": None,
            "extras": [],
        }
        i += 1

        # Consume contiguous bullets
        while i < len(lines):
            ln = lines[i]
            if not ln.strip():
                i += 1
                continue
            bm = _BULLET_RE.match(ln)
            # Stop on next card header or section header
            if not bm:
                if (header_re.match(ln) or _H1_RE.match(ln)
                    or _H2_RE.match(ln) or _H3_RE.match(ln)):
                    break
                # Non-bullet, non-header — likely a blank prose paragraph; stop.
                break
            field, val = _classify_bullet(bm.group(1))
            if field == "_metrics_line":
                card["metrics"] = _parse_metrics(val)
            elif field in card and card[field] is None:
                card[field] = val
            elif field in card:
                # already set — append as extra
                card["extras"].append(val)
            else:
                card["extras"].append(val)
            i += 1

        cards.append(card)
    return cards, i


def _section_header(line: str) -> tuple[str, str] | None:
    """Return (kind, title) for a section heading, or None if not a header."""
    m = _H1_THEME_RE.match(line)
    if m:
        return ("h1", m.group(1))
    m = _H1_RE.match(line)
    if m:
        return ("h1_title", m.group(1))
    m = _H2_RE.match(line)
    if m:
        return ("h2", m.group(1))
    m = _H3_RE.match(line)
    if m:
        return ("h3", m.group(1))
    return None


def parse_report(text: str, kind: ReportKind) -> dict[str, Any]:
    """Top-level entry point. Returns the structured dict described above."""
    out: dict[str, Any] = {
        "capacity": None,
        "title": None,
        "subtitle": None,
        "summary": None,
        "etf_line": None,
        "sections": [],
    }
    if not text:
        return out

    header_re = _CAND_HEADER_RE if kind == "candidates" else _WTE_HEADER_RE
    lines = text.splitlines()

    # Top-of-doc: capacity banner + title + subtitle + summary
    i = 0
    while i < len(lines):
        ln = lines[i]
        cap_m = _CAPACITY_RE.match(ln)
        if cap_m and out["capacity"] is None:
            out["capacity"] = cap_m.group(1)
            i += 1
            continue
        # First H1 (real title, not the ━━━ separator)
        if out["title"] is None and ln.startswith("# ") and "━" not in ln:
            out["title"] = ln[2:].strip()
            i += 1
            # Subtitle in italics underneath
            while i < len(lines) and not lines[i].strip():
                i += 1
            if i < len(lines) and lines[i].startswith("_"):
                out["subtitle"] = lines[i].strip("_").strip()
                i += 1
            continue
        # Summary line (**N companies analyzed:** ... or **Summary:** ...)
        if out["summary"] is None and ln.startswith("**") and (
            "companies analyzed" in ln or "Summary:" in ln
        ):
            out["summary"] = ln.strip("*").strip()
            i += 1
            continue
        # ETF benchmarks one-liner (when_to_enter)
        if out["etf_line"] is None and ln.startswith("**🪙 ETF benchmarks"):
            out["etf_line"] = ln.strip("*").strip()
            i += 1
            continue
        # First section header → bail out of preamble
        if _section_header(ln):
            break
        i += 1

    # Now walk sections + cards
    current_h1 = None  # theme
    current_h2 = None  # sector
    current_h3 = None  # subsection

    while i < len(lines):
        ln = lines[i]
        hdr = _section_header(ln)
        if hdr:
            kind_, title_ = hdr
            if kind_ == "h1" or kind_ == "h1_title":
                current_h1 = title_
                current_h2 = None
                current_h3 = None
                # H1 title (post-preamble) — usually shouldn't repeat; skip
                i += 1
                continue
            if kind_ == "h2":
                current_h2 = title_
                current_h3 = None
                # Read optional subtitle line (italic with Companies:/ETFs:)
                i += 1
                subtitle = None
                while i < len(lines) and not lines[i].strip():
                    i += 1
                if i < len(lines) and lines[i].startswith("_"):
                    subtitle = lines[i].strip("_").strip()
                    i += 1
                cards, i = _parse_cards(lines, i, header_re)
                out["sections"].append({
                    "kind": "h2",
                    "theme": current_h1,
                    "title": title_,
                    "subtitle": subtitle,
                    "cards": cards,
                })
                continue
            if kind_ == "h3":
                current_h3 = title_
                i += 1
                cards, i = _parse_cards(lines, i, header_re)
                out["sections"].append({
                    "kind": "h3",
                    "theme": current_h1,
                    "parent": current_h2,
                    "title": title_,
                    "subtitle": None,
                    "cards": cards,
                })
                continue
        i += 1

    return out


# ─── Summary helpers for the page header strip ────────────────────────────


def summary_counts(report: dict[str, Any]) -> dict[str, int]:
    """Aggregate card counts by status for the page-header summary strip."""
    counts: dict[str, int] = {}
    for sec in report.get("sections", []):
        for card in sec.get("cards", []):
            status = card.get("status", "UNKNOWN")
            counts[status] = counts.get(status, 0) + 1
    return counts
