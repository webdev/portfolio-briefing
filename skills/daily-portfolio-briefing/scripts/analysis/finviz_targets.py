"""FINVIZ analyst-target chip — extends intrinsic_value with a second source.

Design mirrors `intrinsic_value.py`:
  - Data provider is a separate skill (`finviz-target-fetcher`) that pulls
    from FINVIZ public quote.ashx pages, rate-limited + 24h cached.
  - This module owns the CHIP FORMAT + annotation logic — the pure functions
    that consume already-fetched targets and stamp them onto the briefing
    markdown. No network I/O here.

Chip variants:

  Both sources present, aligned:
    📈 FINVIZ $318 (+59% · 🅰 1.8/5 Buy)

  FINVIZ only (FMP unavailable):
    📈 FINVIZ $318 (+59% · 🅰 1.8/5 Buy)

  FMP + FINVIZ diverge by > threshold (default 20%):
    📈 FINVIZ $385 (+94%) ⚠ diverges from FMP by 21%

  ETFs: skipped (basket — no per-fund analyst target)
  No data: skipped (fail-closed per hard rule #19)

Hard rules honored:
  #12 (intrinsic value) — FINVIZ extends, never replaces FMP
  #19 (no fabricated data) — every missing field → skip, never invent
  #11 (RSI discipline) — target upside does NOT override RSI gate
"""

from __future__ import annotations

import re
from typing import Any


# ─── Chip format ───────────────────────────────────────────────────────────


def _recom_label(rec: float | None) -> str | None:
    if rec is None:
        return None
    if rec <= 1.5:
        return "Strong Buy"
    if rec <= 2.5:
        return "Buy"
    if rec <= 3.5:
        return "Hold"
    if rec <= 4.5:
        return "Sell"
    return "Strong Sell"


def format_finviz_chip(
    ticker: str,
    fv_target: dict | None,
    *,
    fmp_target: float | None = None,
    divergence_threshold_pct: float = 20.0,
) -> str | None:
    """Render the FINVIZ chip for one ticker's target dict.

    Returns:
      - The chip string (with optional divergence flag), OR
      - None when there's no FINVIZ data (fail-closed).

    `fmp_target` is the FMP analyst PT for the same ticker (when available);
    used for divergence check.
    """
    if not fv_target:
        return None

    target = fv_target.get("target_price")
    spot = fv_target.get("spot_price")
    upside = fv_target.get("target_upside_pct")
    rec = fv_target.get("analyst_recommendation")

    if target is None and rec is None:
        return None  # no actionable signal

    parts: list[str] = []
    if target is not None:
        if upside is not None:
            parts.append(f"${target:,.0f} ({upside:+.0f}%)")
        else:
            parts.append(f"${target:,.0f}")

    if rec is not None:
        label = _recom_label(rec) or ""
        rec_bit = f"🅰 {rec:.1f}/5"
        if label:
            rec_bit += f" {label}"
        parts.append(rec_bit)

    chip = "📈 FINVIZ " + " · ".join(parts)

    # Divergence flag
    if target is not None and fmp_target is not None and fmp_target > 0:
        divergence = abs(target - fmp_target) / fmp_target * 100
        if divergence >= divergence_threshold_pct:
            chip += f" ⚠ diverges from FMP by {divergence:.0f}%"

    return chip


# ─── Annotator ─────────────────────────────────────────────────────────────


# Match the same recommendation-header shapes intrinsic_value does — reuse.
_HEADER_NUM_RE = re.compile(r"^\s*\d+\.\s")
_BOLD_HEADER_RE = re.compile(r"^\s*[-*]?\s*\*\*")
_REC_KEYWORDS = re.compile(
    r"\b(BUY|CSP|SELL TO OPEN|COVERED CALL|WRITE|LEAP|LONG-DATED|ENTRY|"
    r"STRANGLE|SUB-LOT|ADD|TRIM|CLOSE|ROLL|COLLAR|COMPLETE)\b",
    re.I,
)


def _is_rec_header(line: str) -> bool:
    if _HEADER_NUM_RE.match(line):
        return True
    if _BOLD_HEADER_RE.match(line) and _REC_KEYWORDS.search(line):
        return True
    return False


def _find_ticker_in_line(line: str, known_tickers: list[str]) -> str | None:
    """Find the first known ticker referenced in the line. Case-insensitive.

    Boundary: allow underscore + start/end as delimiters (contract identifiers
    like ``NVDA_PUT_180_20260918`` should match NVDA). Python's ``\b`` treats
    underscore as a word char so it doesn't fire between NVDA and ``_`` — we
    use an explicit char class instead.
    """
    upper = line.upper()
    for t in known_tickers:
        # Match the ticker when surrounded by non-alphanumeric characters
        # (word-boundary that ALSO treats underscore as a boundary).
        if re.search(rf"(?:^|[^A-Z0-9]){re.escape(t)}(?:[^A-Z0-9]|$)", upper):
            return t
    return None


def annotate_finviz(
    lines: list[str],
    *,
    finviz_by_ticker: dict | None,
    fmp_targets_by_ticker: dict | None = None,
    known_tickers,
    etf_set: set[str],
    divergence_threshold_pct: float = 20.0,
) -> tuple[list[str], dict]:
    """Append a FINVIZ chip to every single-stock recommendation header.

    Returns (new_lines, stats). Skips:
      - ETFs (no per-fund analyst target)
      - lines with no FINVIZ data
      - lines already carrying a FINVIZ chip (never double-annotate)

    `fmp_targets_by_ticker` is optional; when present, used to detect
    divergence between FMP analyst PT and FINVIZ target.
    """
    if not lines:
        return lines, {"annotated": 0, "etf_skipped": 0, "no_data": 0, "diverged": 0}

    tickers_sorted = sorted(
        {(t or "").upper() for t in (known_tickers or []) if t},
        key=len, reverse=True,
    )
    finviz_by_ticker = {(k or "").upper(): v for k, v in (finviz_by_ticker or {}).items()}
    fmp_targets_by_ticker = {(k or "").upper(): v for k, v in (fmp_targets_by_ticker or {}).items()}

    out: list[str] = []
    stats = {"annotated": 0, "etf_skipped": 0, "no_data": 0, "diverged": 0}
    in_excluded = False

    for line in lines:
        # Skip within Capital Plan rollup + italic transparency notes
        if line.startswith("## Capital Plan"):
            in_excluded = True
        elif line.startswith("## "):
            in_excluded = False
        if in_excluded or line.lstrip().startswith("_"):
            out.append(line)
            continue

        # Already has FINVIZ chip — never double-annotate
        if "📈 FINVIZ" in line:
            out.append(line)
            continue

        # Not a rec header — leave alone
        if not _is_rec_header(line):
            out.append(line)
            continue

        ticker = _find_ticker_in_line(line, tickers_sorted)
        if not ticker:
            out.append(line)
            continue

        if ticker in etf_set:
            stats["etf_skipped"] += 1
            out.append(line)
            continue

        fv = finviz_by_ticker.get(ticker)
        if not fv:
            stats["no_data"] += 1
            out.append(line)
            continue

        fmp_pt = fmp_targets_by_ticker.get(ticker)
        chip = format_finviz_chip(
            ticker, fv,
            fmp_target=fmp_pt,
            divergence_threshold_pct=divergence_threshold_pct,
        )
        if not chip:
            stats["no_data"] += 1
            out.append(line)
            continue

        stats["annotated"] += 1
        if "diverges from FMP" in chip:
            stats["diverged"] += 1

        # Append chip to the end of the header line, separated by ' · '
        out.append(line.rstrip() + " · " + chip)

    return out, stats


# ─── Cross-check helper for /positions/{ticker} card ──────────────────────


def cross_check(fmp_target: float | None, finviz_target: float | None,
                *, threshold_pct: float = 20.0) -> dict:
    """Return {'diverged': bool, 'divergence_pct': float, 'label': str}."""
    if fmp_target is None or finviz_target is None:
        return {"diverged": False, "divergence_pct": None, "label": None}
    if fmp_target <= 0:
        return {"diverged": False, "divergence_pct": None, "label": None}
    div_pct = abs(finviz_target - fmp_target) / fmp_target * 100
    diverged = div_pct >= threshold_pct
    return {
        "diverged": diverged,
        "divergence_pct": round(div_pct, 1),
        "label": f"⚠ sources diverge by {div_pct:.0f}%" if diverged else "✓ sources aligned",
    }
