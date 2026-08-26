"""US-optionable symbol-universe gate (2026-08-26 5ZM.HM bug).

Observed on the real 2026-08-26 briefing — Best Setups #1 rendered:

    **B** (74) `5ZM.HM` — SELL 1× 5ZM.HM $75P exp Fri Nov 20 '26
    (86 DTE, monthly) · 13% ann · RSI 38 prime

".HM" is a Hamburg-exchange suffix (yfinance foreign-listing notation): no
US option chain exists for that symbol, so the ticket is unfillable fiction
(rule #19 — never fabricate a chain ticket for a non-optionable symbol).
Source of the leak: the recommendation-list-fetcher's yfinance name→ticker
resolution took the FIRST search hit for "Zoom Video" — the Hamburg listing
5ZM.HM — cached it in ticker_map.json, and the rec flowed through the graded
pools as if it were a US name.

This module is the SINGLE SOURCE OF TRUTH for "may this ticker ever carry a
US option ticket?". Two enforcement points:

- ``collect_best_setups`` (analysis/setup_grade.py) — the graded-pool
  boundary: an invalid symbol is excluded with a VISIBLE one-line reason
  ("⏸ 5ZM.HM — excluded: non-US listing, no US option chain"; rule #24)
  the first time it shows up.
- ``recommendation-list-fetcher/scripts/shopping_list.py`` — the source
  screener: cached and freshly-resolved tickers are validated so a foreign
  listing is filtered silently at the source thereafter.

Universe pattern: ``^[A-Z]{1,5}$`` (standard OCC-optionable US root) plus
the known DOTTED US SHARE CLASSES (BRK.B and friends) — those are real US
listings with real US chains and must never be caught by the dot check.
"""

from __future__ import annotations

import re

# Standard US optionable root: 1-5 uppercase letters, no digits, no dots.
_US_ROOT_RE = re.compile(r"^[A-Z]{1,5}$")

# Dotted US share classes — legitimate NYSE/Nasdaq listings whose canonical
# symbol carries a class dot. Kept as an explicit allowlist (a generic
# ".<single letter>" rule would also admit yfinance exchange suffixes like
# ".F" Frankfurt / ".L" London / ".V" TSX-V — exactly the leak this module
# exists to stop).
KNOWN_DOTTED_US_CLASSES = frozenset({
    "BRK.A", "BRK.B", "BF.A", "BF.B", "HEI.A", "LEN.B", "MOG.A", "MOG.B",
    "LGF.A", "LGF.B", "CWEN.A", "UHAL.B", "WSO.B", "GEF.B", "TAP.A",
    "CRD.A", "CRD.B", "JW.A", "JW.B", "AKO.A", "AKO.B", "BIO.B", "PBR.A",
})

def is_us_optionable_symbol(ticker) -> bool:
    """True when ``ticker`` fits the US optionable-universe pattern
    (``^[A-Z]{1,5}$``) or is a known dotted US share class (BRK.B etc.).

    False for exchange-suffixed foreign listings (5ZM.HM, SAP.DE, VOD.L,
    SHOP.TO, …), digit-bearing roots, empty/None, and anything else that
    can never carry a US option chain.
    """
    tk = str(ticker or "").strip().upper()
    if not tk:
        return False
    if _US_ROOT_RE.match(tk):
        return True
    return tk in KNOWN_DOTTED_US_CLASSES


def symbol_exclusion_reason(ticker) -> str | None:
    """None when the symbol is valid; otherwise the measured one-line
    reason for the visible pool-boundary exclusion (rule #24).

    "5ZM.HM"  → "non-US listing, no US option chain"
    "5ZM"     → "not a US-optionable symbol, no US option chain"
    """
    if is_us_optionable_symbol(ticker):
        return None
    tk = str(ticker or "").strip().upper()
    if "." in tk:
        return "non-US listing, no US option chain"
    return "not a US-optionable symbol, no US option chain"
