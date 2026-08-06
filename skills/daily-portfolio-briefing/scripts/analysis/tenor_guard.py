"""Tenor-cap sweep over a rendered briefing (CLAUDE.md rule #14 backstop).

Observed 2026-08-04: the one-voice take-profit composer rendered
"TAKE PROFIT VIA ROLL-DOWN VRT_PUT_280_20270115 — BTC 1× @ $51.97 + STO 1×
VRT $240P Fri Dec 15 '28 @ $75.75 → net +$2,270 credit" — the STO leg
~700 days past the position's current Jan '27 expiry, ~6× the 120d Tier C
action tenor cap. The composer path bypassed the ranker's max_tenor_days
filter, resurrecting the rule-#14 max-credit trap through a new surface.

This module is the render-time backstop: scan the FINISHED markdown for any
actionable ticket (action list, playbook, take-profit rolls, roll-analysis
"✅ recommended" rows) whose STO leg expiration exceeds the applicable tenor
cap measured against the position's CURRENT expiry. Exempt (reference, not
tickets): menu-table rows carrying the "⚠ far past the Nd tenor cap" warning,
plain menu-table reference rows, italic transparency footers, and directive
templates.

Wired into the briefing's verifier pass in steps/aggregate.py — a violation
renders a 🔴 warning panel so a regression can never ship silently again.
"""

from __future__ import annotations

import re
from datetime import date, datetime

# TICKER_PUT_280_20270115-style contract token → (ticker, current expiry).
_CONTRACT_RE = re.compile(
    r"\b([A-Z][A-Z0-9]{0,9})_(?:PUT|CALL)_\d+(?:\.\d+)?_(\d{8})\b")

# Rendered expirations: "Fri Dec 15 '28" / "Dec 15 '28" (the bare form also
# matches inside the weekday form) and raw ISO "2028-12-15".
_PRETTY_DATE_RE = re.compile(r"\b([A-Z][a-z]{2})\s+(\d{1,2})\s+'(\d{2})\b")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# Lines that carry an actionable STO leg.
_STO_MARKERS = ("STO ", "Sell-to-Open", "Sell to Open")


def _line_dates(line: str) -> list[date]:
    out: list[date] = []
    for mon, day, yy in _PRETTY_DATE_RE.findall(line):
        try:
            out.append(datetime.strptime(
                f"{mon} {day} {yy}", "%b %d %y").date())
        except ValueError:
            continue
    for y, m, d in _ISO_DATE_RE.findall(line):
        try:
            out.append(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return out


def ticket_tenor_violations(md, max_action_tenor_days: int = 120,
                            core_tickers=None,
                            core_multiplier: int = 3) -> list[str]:
    """Sweep rendered briefing markdown (string or list of lines) for
    actionable tickets whose STO-leg expiration extends MORE than the
    applicable tenor cap past the position's current expiry.

    Cap: ``max_action_tenor_days`` (default 120), ×``core_multiplier`` for
    tickers in ``core_tickers`` (core_positions ∪ Tier A — same discipline
    as block #3's ranker call and advise.py). Returns the offending lines
    ([] = the tenor discipline holds everywhere).
    """
    if isinstance(md, str):
        lines = md.splitlines()
    else:
        lines = list(md or [])
    core = {str(t).upper() for t in (core_tickers or [])}
    violations: list[str] = []
    ctx_ticker: str | None = None
    ctx_exp: date | None = None

    for raw in lines:
        line = raw or ""
        stripped = line.strip()

        m = _CONTRACT_RE.search(line)
        if m:
            ctx_ticker = m.group(1)
            try:
                ctx_exp = datetime.strptime(m.group(2), "%Y%m%d").date()
            except ValueError:
                ctx_exp = None

        # ── Exemptions (reference surfaces, not tickets) ──────────────────
        if "far past the" in line and "tenor cap" in line:
            continue                     # warning-carrying menu reference row
        if stripped.startswith("_"):
            continue                     # italic transparency footer
        if "DIRECTIVE:" in line:
            continue                     # ready-to-paste directive template
        is_table_row = stripped.startswith("|")
        if is_table_row and "✅ recommended" not in line:
            continue                     # menu reference row — not a ticket

        # ── Ticket detection ──────────────────────────────────────────────
        is_ticket = any(mk in line for mk in _STO_MARKERS) or (
            is_table_row and "✅ recommended" in line)
        if not is_ticket or ctx_exp is None or ctx_ticker is None:
            continue

        cap = max_action_tenor_days
        if ctx_ticker.upper() in core:
            cap = max_action_tenor_days * core_multiplier
        for d in _line_dates(line):
            if (d - ctx_exp).days > cap:
                violations.append(stripped)
                break
    return violations
