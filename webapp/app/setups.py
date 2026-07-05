"""Consolidated setups view — merges candidates + when-to-enter into ONE
per-ticker card carrying everything both sources know.

The pipeline emits two markdown reports:
  - candidates_<DATE>.md   → research card with FV, verdict, earnings
  - when_to_enter_<DATE>.md → entry framework with concrete triggers

Both cover ~90% of the same tickers with 90% overlapping data (RSI, IV,
drawdown, trend, Parkev). Rendering them as separate pages doubles the
user's scrolling for no informational gain.

This module parses BOTH, dedupes by ticker, and returns a single unified
card list — losing nothing, showing each ticker once. Downstream template
renders "Setups" as the one page.

Design mirrors report_parser.py's shape but joins across the two sources.
"""

from __future__ import annotations

from typing import Any

from . import md_render, report_parser


# ─── Merging ───────────────────────────────────────────────────────────────


def _merge_cards(cand_card: dict, wte_card: dict | None) -> dict:
    """Combine a candidates card + optional when-to-enter card for the
    same ticker. Fields present in either source propagate; fields
    present in BOTH prefer the when-to-enter version (fresher / more
    detailed by design)."""
    if not wte_card:
        return {**cand_card, "sources": ["candidates"]}

    merged = dict(cand_card)  # start with candidates as base
    # WTE fields that override
    for field in ("read", "trigger", "status", "status_tone", "status_icon",
                  "status_label", "deferred_note"):
        if wte_card.get(field):
            merged[field] = wte_card[field]

    # Merge extras + badges
    merged["extras"] = (cand_card.get("extras") or []) + (wte_card.get("extras") or [])
    cand_flags = cand_card.get("flags") or {}
    wte_flags = wte_card.get("flags") or {}
    merged["flags"] = {**cand_flags, **wte_flags}

    # Merge metrics — take the union (WTE tends to have more)
    seen_labels = {m["label"] for m in cand_card.get("metrics") or []}
    extra_metrics = [m for m in (wte_card.get("metrics") or []) if m["label"] not in seen_labels]
    merged["metrics"] = (cand_card.get("metrics") or []) + extra_metrics

    # WTE-only field: ETF marker
    if wte_card.get("is_etf"):
        merged["is_etf"] = True

    merged["sources"] = ["candidates", "when_to_enter"]
    return merged


def _normalize_section_title(title: str | None) -> str:
    """Merge-key normalization.

    The two source reports use different section-title conventions:
      - candidates_report.md: ``## 🔭 Robotics & Autonomy``
      - when_to_enter.md:     ``## Robotics & Autonomy (10)``

    Both refer to the same theme. Without normalization, the merger keys
    them as different sections and tickers get scattered across adjacent
    duplicate cards. Fix: strip leading emoji/prefix + trailing
    ``(N)`` count parenthetical before keying.
    """
    if not title:
        return ""
    import re
    t = title.strip()
    # Strip leading emoji/decorative chars (anything outside basic ASCII letters/digits)
    t = re.sub(r"^[^A-Za-z0-9]+", "", t)
    # Strip trailing "(N)" count marker
    t = re.sub(r"\s*\(\d+\)\s*$", "", t)
    return t.strip().lower()


def _sections_index_by_ticker(report: dict[str, Any]) -> dict[str, dict]:
    """Flatten a parsed report into {ticker → card} for O(1) merge lookup."""
    out: dict[str, dict] = {}
    for sec in report.get("sections", []):
        for card in sec.get("cards", []):
            tk = (card.get("ticker") or "").upper()
            if tk and tk not in out:
                out[tk] = dict(card)
                # Remember the section this card belonged to so the
                # merged view can preserve theme grouping. Store BOTH
                # the display title and a normalized merge key so we
                # can join across candidates_report + when_to_enter
                # even when their section-header styles differ.
                out[tk]["_section_title"] = sec.get("title")
                out[tk]["_section_key"] = _normalize_section_title(sec.get("title"))
                out[tk]["_section_theme"] = sec.get("theme")
    return out


def build_setups(date: str) -> dict[str, Any]:
    """Load both markdown reports for `date`, parse, merge by ticker,
    return a unified structure keyed by section.

    Returns the same top-level shape as report_parser.parse_report — the
    template can consume either without branching.

    Empty when NEITHER file exists on disk.
    """
    cand_text = md_render.load_report("candidates", date) or ""
    wte_text = md_render.load_report("when_to_enter", date) or ""
    if not cand_text and not wte_text:
        return {
            "capacity": None,
            "title": f"Setups — {date}",
            "subtitle": None,
            "summary": None,
            "sections": [],
        }

    # Parse both — use their existing patterns
    cand = report_parser.parse_report(cand_text, "candidates") if cand_text else {"sections": []}
    wte = report_parser.parse_report(wte_text, "when_to_enter") if wte_text else {"sections": []}

    cand_by_tk = _sections_index_by_ticker(cand)
    wte_by_tk = _sections_index_by_ticker(wte)

    all_tickers = set(cand_by_tk) | set(wte_by_tk)

    # Build a display-title map keyed by normalized section key. Candidates
    # report is canonical (has emoji prefix) — WTE display titles are used
    # only when a section is WTE-only. This is the fix for the split-section
    # bug: keying by title string caused "🔭 Robotics & Autonomy" and
    # "Robotics & Autonomy (10)" to render as two adjacent duplicate sections.
    display_title_by_key: dict[str, str] = {}
    theme_by_key: dict[str, str | None] = {}
    for src in (cand, wte):  # candidates first — its title wins on ties
        for sec in src.get("sections", []):
            title = sec.get("title") or "Other"
            key = _normalize_section_title(title)
            if key and key not in display_title_by_key:
                display_title_by_key[key] = title
                theme_by_key[key] = sec.get("theme")

    # Group merged cards by NORMALIZED section key (not raw title)
    section_cards: dict[str, list[dict]] = {}
    for tk in sorted(all_tickers):
        cand_c = cand_by_tk.get(tk)
        wte_c = wte_by_tk.get(tk)
        if cand_c:
            merged = _merge_cards(cand_c, wte_c)
            sec_key = cand_c.get("_section_key") or "other"
        else:
            # WTE-only ticker (e.g. an ETF Candidates doesn't cover)
            merged = dict(wte_c)
            merged["sources"] = ["when_to_enter"]
            sec_key = wte_c.get("_section_key") or "other"
        section_cards.setdefault(sec_key, []).append(merged)

    # Build the final sections list, preserving order from candidates → WTE
    section_order: list[str] = []  # list of normalized keys
    seen = set()
    for src in (cand, wte):
        for sec in src.get("sections", []):
            k = _normalize_section_title(sec.get("title") or "Other") or "other"
            if k not in seen:
                seen.add(k)
                section_order.append(k)

    sections = []
    for key in section_order:
        cards = section_cards.get(key) or []
        if not cards:
            continue
        sections.append({
            "kind": "h2",
            "title": display_title_by_key.get(key) or key.title(),
            "theme": theme_by_key.get(key),
            "subtitle": None,
            "cards": cards,
        })

    # Capacity + summary — prefer the WTE side (it's the entry-focused doc)
    capacity = (wte.get("capacity") or cand.get("capacity"))
    summary = (wte.get("summary") or cand.get("summary"))

    return {
        "capacity": capacity,
        "title": f"Setups — {date}",
        "subtitle": (
            "Consolidated view of Candidates + When-to-Enter — one card per "
            "ticker with all the research, technicals, fair value, and (when "
            "applicable) the concrete entry trigger."
        ),
        "summary": summary,
        "sections": sections,
    }


def summary_counts_split(report: dict[str, Any]) -> dict[str, int]:
    """Aggregate card counts by status (same as report_parser.summary_counts
    but works on the merged report)."""
    return report_parser.summary_counts(report)
