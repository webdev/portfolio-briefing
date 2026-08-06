"""Parkev chip — unified rating/conviction/age annotation for every
ticker-specific line in the briefing.

CLAUDE.md hard rule #27 contract: every line that mentions a single
ticker (action list items, watch panel rows, strategy upgrades, capital
plan, red flags, long-term opps, income opps, candidate trades) MUST
carry a `format_parkev_chip(rec)` annotation so the user can see at a
glance what Parkev says about the name without scrolling to a separate
section.

Format:
    🅿️ {RATING} · {CONV-CHIP} · {AGE}

Examples:
    🅿️ TOP 12 · 🔥 High · 8d           — strongest signal (tier 4 + High)
    🅿️ TOP STOCK · 🔥 High · 3d        — the singular tier-5 call
    🅿️ BUY · ◐ Med · 12d              — standard buy + medium conviction
    🅿️ HOLD · ▽ Low · ⏰ 22d           — stale soft hold (aging flag)
    🅿️ SELL · 🔥 High · 3d             — rare confidently bearish call
    🅿️ no rec                          — ticker not in Parkev's sheet (ETFs, etc.)

Conviction chip omitted entirely when conviction is None (e.g., older
rows that pre-date the 2026-06-24 Conviction Level column add).
"""

from __future__ import annotations


PARKEV_MARK = "🅿️"


# Rating tier → compact display label (CAPS).
# Tier comes from recommendation-list-fetcher's `rating_tier` field
# (5/4/3/2/1/0). raw_recommendation is the source-of-truth for tier-4
# labels because there are three flavors: Top 12 / Top 15 / Top 25.
_TIER_LABELS = {
    5: "TOP STOCK",   # "Top Stock to Buy" — singular tier-5 call
    3: "BUY",
    2: "BDL BUY",     # "Borderline Buy"
    1: "HOLD",
    0: "SELL",
}


def _tier_label(rec: dict) -> str:
    """Map rating tier (+ raw_recommendation for tier-4 variants) to the
    compact CAPS label. Returns 'NO REC' for missing/unrated entries."""
    tier = rec.get("rating_tier")
    if tier is None:
        return "NO REC"
    if tier == 4:
        # Three flavors of tier-4 — surface the actual one (TOP 12 / 15 / 25).
        raw = (rec.get("raw_recommendation") or "").strip().upper()
        for flavor in ("TOP 12", "TOP 15", "TOP 25"):
            if flavor in raw:
                return flavor
        return "TOP TIER"  # safe fallback if a new tier-4 flavor appears
    return _TIER_LABELS.get(int(tier), "NO REC")


_CONV_CHIPS = {
    "High":   "🔥 High",
    "Medium": "◐ Med",
    "Low":    "▽ Low",
}


def format_parkev_chip(rec: dict | None) -> str:
    """Return the Parkev chip string for a rec dict, or `🅿️ no rec` for None.

    `rec` shape (matches recommendation-list-fetcher output):
        {
            "rating_tier": 4,
            "raw_recommendation": "Top 12 Stock",
            "conviction": "High" | "Medium" | "Low" | None,
            "age_days": 8,
            "aging": False,   # True when age_days > warn_age_days (default 14)
            ...
        }

    Fail-closed: missing fields produce `🅿️ no rec` rather than fabricating
    a value (hard rule #19 — no boilerplate / fabricated data in output).
    """
    if not rec or not isinstance(rec, dict):
        return f"{PARKEV_MARK} no rec"

    rating = _tier_label(rec)
    if rating == "NO REC":
        return f"{PARKEV_MARK} no rec"

    parts = [rating]

    # Conviction chip — omitted entirely when conviction is None so older
    # rows (pre 2026-06-24 sheet update) don't render `· None ·`.
    conv = rec.get("conviction")
    conv_chip = _CONV_CHIPS.get(conv) if conv else None
    if conv_chip:
        parts.append(conv_chip)

    # Age + aging clock icon. Use `aging` flag if present (canonical, comes
    # from the fetcher's freshness config), otherwise infer from age_days.
    age = rec.get("age_days")
    if age is not None:
        try:
            age_int = int(age)
            is_aging = bool(rec.get("aging")) or age_int > 14
            prefix = "⏰ " if is_aging else ""
            parts.append(f"{prefix}{age_int}d")
        except (ValueError, TypeError):
            pass

    return f"{PARKEV_MARK} " + " · ".join(parts)


def parkev_chip_for_ticker(ticker: str, recs_by_ticker: dict | None) -> str:
    """Lookup-by-ticker variant — convenience for callers that have a
    `recs_by_ticker` map but no specific rec dict in hand."""
    if not ticker or not recs_by_ticker:
        return f"{PARKEV_MARK} no rec"
    rec = recs_by_ticker.get(ticker.upper())
    return format_parkev_chip(rec)


# ─── Markdown annotator — mirrors rsi_discipline.annotate_action_lines ───
import re

try:
    from analysis import line_exclusions
except ImportError:  # pragma: no cover - path fallback for standalone runs
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    try:
        from analysis import line_exclusions
    except ImportError:
        import line_exclusions  # type: ignore

# Match a header line that introduces an actionable item with a ticker:
#   "1. **CLOSE** AMD_PUT_420_20261218 — ..."
#   "- **AMZN** @ $227.72 — 2.3% (+8.0%) → **HOLD**"
#   "### 💎 4. LONG DATED CSP · `MU`"
#   "**🎯 CANDIDATE · `META` · $548.80** ✅ RSI favourable"
_TICKER_RE = re.compile(r"\b([A-Z][A-Z0-9]{0,4})\b")
# Lines that already have a Parkev chip (don't double-annotate)
_ALREADY_HAS_CHIP = lambda l: PARKEV_MARK in l
# Lines we should skip even if they have a ticker pattern (prose, headers, footers)
_SKIP_PREFIXES = (
    "#", "_", "<", ">", "|",        # section headers, italics, blockquotes, table rows
    "    ", "\t",                    # deeply indented code/data
    "  - **Earnings check:**",       # sub-bullets that aren't ticker headers
    "  - **Wash-sale check:**",
    "  - **Source:**",
    "  - **Why:**",
    "  - **Tax note:**",
    "  - **Trade-validator:**",
    "  - **Order:**",
    "  - **Order (two legs):**",
    "  - **Target:**",
    "  - ⚖️",                        # counterpoint sub-bullets
    "  - 💵",                        # FV sub-bullets (FV chip is separate)
    "  - 🎯", "  - 🚨", "  - 📊",    # red-flag-style annotations
    "  - ↳",                         # support/resistance continuation lines
    "   - ",                         # 3-space-indented sub-bullets (deeply nested)
    "  ↳",                           # S/R continuation
    "  ⏸",                           # capacity-gated continuation
    "  ⚠",                           # warning continuation
)


def _is_annotatable_header(line: str) -> bool:
    """A line is annotatable if it's a TOP-LEVEL action/position header that
    mentions a ticker but doesn't yet carry a Parkev chip.

    Sub-bullets (lines with ≥2 leading spaces or tabs) are NEVER annotatable
    — they're continuation lines under a header that already (or will) carry
    the chip. This prevents `- **Why:** META is the strongest signal` from
    getting its own chip when it's just elaboration under the parent action.
    """
    if _ALREADY_HAS_CHIP(line):
        return False
    if not line or not line.strip():
        return False
    # Rule-#27 exclusion list (shared with position_tiers / intrinsic_value):
    # labelled sub-lines ("- **Triggers:**", "- **Rationale:**",
    # "- **Yield/Cost:**", "- **Source:**", ...) and continuation glyph lines
    # describe the parent card — they must NEVER get their own chip. The
    # 2026-07-30 briefing leaked `🅿️ no rec · 🔵 Tier C` onto exactly these
    # lines via the bare-token fallback ("LT" / "TRADE" / "LEAP").
    if line_exclusions.is_excluded_line(line):
        return False
    # Sub-bullet / indented continuation — never annotate.
    # Two or more leading spaces means this is nested content.
    leading_ws = len(line) - len(line.lstrip())
    if leading_ws >= 2:
        return False
    stripped = line.lstrip()
    # Numbered action header: "1. **CLOSE** ..."
    if re.match(r"^\d+\.\s+\*\*", stripped):
        return True
    # Watch-panel equity bullet: "- **AMZN** @ ..."
    if re.match(r"^- \*\*[A-Z]", stripped):
        return True
    # Capital plan bullet: "- CLOSE AMD — ..."
    if re.match(r"^- (CLOSE|EXIT|HEDGE|TRIM|LT TRIM|LT ADD|CONVERT)\b", stripped):
        return True
    # Long-term opportunity / strategy upgrade card header: "### 💎 4. LONG DATED CSP · `MU`"
    if re.match(r"^### .* `[A-Z]", stripped) or re.match(r"^### .* (CSP|TRIM|EXIT|ADD)\b.*`[A-Z]", stripped):
        return True
    # Candidate trade card: "**🎯 CANDIDATE · `META` · $548.80**"
    if "🎯 CANDIDATE" in stripped or "🌟 STRONG BUY" in stripped or "🏆 TOP CONVICTION" in stripped:
        return True
    return False


# Tokens that look like tickers but aren't. Without this filter the bare-token
# fallback would catch things like "RSI", "BUY", "CLOSE" and render `🅿️ no rec`
# on every action verb in the briefing. Common stoplist of words that match
# the ticker regex but are domain vocabulary, not stocks.
_NON_TICKER_TOKENS = frozenset({
    # Action verbs & rec verbs
    "CLOSE", "OPEN", "ROLL", "BUY", "SELL", "HOLD", "EXIT", "HEDGE", "TRIM",
    "ADD", "EXEC", "ENTER", "WAIT", "WATCH", "AVOID", "DEFER", "SKIP",
    # Option types & states
    "PUT", "CALL", "ITM", "OTM", "ATM", "CSP", "CC", "DTE", "ER",
    # Indicator / metric names
    "RSI", "SMA", "EMA", "ATR", "ADX", "MACD", "PE", "PEG", "EPS", "FCF",
    "IV", "DCF", "FV", "PT", "EV", "PL", "ROI",
    # Trade-validator and matrix outputs
    "PASS", "FAIL", "BLOCK", "WARN", "OK", "GOOD", "BAD", "MEH",
    "TOP", "MED", "LOW", "HIGH", "NEW", "OLD", "GTC", "DAY",
    # Section / status words
    "NA", "TBD", "ER", "FOMC", "CPI", "PPI", "GDP", "PMI",
    # Misc CAPS abbreviations seen in briefings
    "NLV", "MV", "BTC", "STO", "BTO", "STC",  # BTC=buy-to-close in this context
    "LTCG", "STCG", "IRA", "ROTH", "API", "ETF",
    "MCP", "FMP", "FAQ", "AKA", "TLDR", "TLR",
    # Months (could appear inline in trigger text)
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    # Days
    "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
    # Currencies / units
    "USD", "EUR", "JPY", "GBP", "CAD",
    # Misc
    "AI", "ML", "LLM", "AGI", "OS", "UX", "UI", "VR", "AR", "MR",
    "S", "R",  # support / resistance single letters that show up in S: / R:
    "P", "C",  # PUT/CALL shorthand sometimes
    "A", "B", "K", "M", "T",  # K/M/T suffixes; single-letter false positives
    # Briefing vocabulary that leaked as tickers on sub-lines (2026-07-30):
    # "⚠ LT verdict" → LT, "E*TRADE" → TRADE, "Stock-replacement LEAP" → LEAP,
    # "TOP STOCK" chip → STOCK, "S/R" → SR.
    "LT", "LEAP", "TRADE", "STOCK", "SR",
})


def _extract_ticker(line: str) -> str | None:
    """Find the first plausible ticker on the line via structural patterns.

    Does NOT require the ticker to be in any known set — the caller (the
    annotator) decides whether to render the chip or `🅿️ no rec` based on
    lookup hit/miss. This is what enables EVERY ticker line to carry a
    chip per CLAUDE.md hard rule #27.

    Preference order (least → most ambiguous):
      1. Backtick-quoted: `META`
      2. Bold: **META**
      3. Options-symbol prefix: META_PUT_525_20260821 → META
      4. Bare uppercase token, filtered against _NON_TICKER_TOKENS stoplist
    """
    # Backticks — least ambiguous.
    m = re.search(r"`([A-Z][A-Z0-9]{0,4})`", line)
    if m:
        return m.group(1)
    # Bold (**META**, **CLOSE**, etc.) — filter against stoplist.
    m = re.search(r"\*\*([A-Z][A-Z0-9]{0,4})\*\*", line)
    if m and m.group(1) not in _NON_TICKER_TOKENS:
        return m.group(1)
    # Options-symbol prefix: AMD_PUT_420_20261218 → AMD (matches both real
    # broker symbols and shortened test forms like AMD_PUT). NOTE: `\b`
    # doesn't work after PUT/CALL because `_` is a word char in Python
    # regex — `PUT_525` has no boundary. Use explicit lookahead.
    m = re.search(r"\b([A-Z][A-Z0-9]{0,4})_(?:PUT|CALL)(?=_|\s|$)", line)
    if m and m.group(1) not in _NON_TICKER_TOKENS:
        return m.group(1)
    # Bare uppercase token — filtered against stoplist. Last resort because
    # uppercase tokens are everywhere in briefing prose ("RSI", "BUY", etc.)
    for tok in _TICKER_RE.findall(line):
        if tok not in _NON_TICKER_TOKENS and len(tok) >= 2:
            return tok
    return None


def annotate_parkev_chips(md: str, recs_by_ticker: dict | None,
                          cp_tickers: set | None = None) -> str:
    """Walk the rendered briefing markdown and append a Parkev chip to EVERY
    action-list / watch-row / capital-plan / candidate-card header that
    mentions a ticker, regardless of whether the ticker is in Parkev's
    sheet (CLAUDE.md hard rule #27 — EVERY ticker gets a chip).

    Behavior:
      - Ticker IS in recs_by_ticker → render full chip
        (`🅿️ TOP 12 · 🔥 High · 8d`)
      - Ticker NOT in recs_by_ticker → render `🅿️ no rec`
        (so the user knows the absence is INTENTIONAL, not a missing
        annotation — this is what the ARM case caught Jun 2026)
      - Ticker in ``cp_tickers`` (task #45 — the Autopilot Claude
        portfolio) → ` · 🤖 CP-held` appended AFTER the Parkev chip
        (`🅿️ BUY · 🔥 High · 3d · 🤖 CP-held`). Corroboration only —
        an anonymous source never changes the Parkev read itself.
      - Lines already carrying a 🅿️ marker → never double-annotated
      - Sub-bullets / S/R continuation lines / prose → skipped (no chip)

    Empty `recs_by_ticker` → no-op (returns the input unchanged) — covers
    the case where the recommendation fetcher fails entirely.
    """
    if not md:
        return md
    # An empty or missing recs_by_ticker means the fetcher hasn't produced
    # any data yet (or failed entirely). In that case we have no way to
    # distinguish "ticker isn't in Parkev's sheet" from "we never asked
    # Parkev" — fall back to no-op so the briefing isn't filled with
    # misleading `🅿️ no rec` chips on a fetcher failure. Once the fetcher
    # returns ANY entries, we trust the absence signal and annotate
    # per-ticker accordingly.
    if not recs_by_ticker:
        return md
    # Normalize map keys to UPPER for case-insensitive lookup
    recs_norm = {t.upper(): v for t, v in recs_by_ticker.items() if t}
    if not recs_norm:
        return md
    cp_norm = {str(t).upper() for t in (cp_tickers or set()) if t}
    out_lines = []
    for line in md.splitlines():
        if _is_annotatable_header(line):
            tk = _extract_ticker(line)
            if tk:
                # Look up; if absent, format_parkev_chip(None) → "🅿️ no rec"
                rec = recs_norm.get(tk)
                chip = format_parkev_chip(rec)
                if tk in cp_norm:
                    chip += " · 🤖 CP-held"
                line = f"{line}  · {chip}"
        out_lines.append(line)
    return "\n".join(out_lines)
