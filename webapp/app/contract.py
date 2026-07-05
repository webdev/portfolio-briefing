"""Pretty-print option contract identifiers.

The pipeline writes contracts as `TICKER_PUT_STRIKE_YYYYMMDD` or
`TICKER_CALL_STRIKE_YYYYMMDD` — unambiguous but hard to scan visually.
This module parses those into a structured, scannable HTML form:

    LITE_PUT_660_20260918    →  LITE  $660P  Sep 18 '26
    GOOG_CALL_450_20271217   →  GOOG  $450C  Dec 17 '27
    NOW_PUT_95_20260821      →  NOW   $95P   Aug 21 '26
    BRK.B_CALL_500_20260116  →  BRK.B $500C  Jan 16 '26  (dotted tickers OK)

When a string doesn't look like a contract, it's rendered as the
original text. Fail-open: never hide content.

Output is `markupsafe.Markup` so Jinja doesn't double-escape.
"""

from __future__ import annotations

import re
from datetime import date as _date

from markupsafe import Markup, escape


# Match TICKER_TYPE_STRIKE_YYYYMMDD
# - TICKER: letters + optional . (BRK.B, BF.B, etc.) + optional digits (NOK1)
# - TYPE: PUT or CALL
# - STRIKE: digits + optional .5 / .25 (half / quarter strikes)
# - DATE: 8-digit YYYYMMDD
_CONTRACT_RE = re.compile(
    r"^([A-Z][A-Z0-9.]{0,7})_(PUT|CALL)_(\d+(?:\.\d+)?)_(\d{8})$"
)


# Compact month names: Jan, Feb, ... (3-letter, never spelled out)
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def parse_contract(symbol: str) -> dict | None:
    """Parse a TICKER_TYPE_STRIKE_YYYYMMDD symbol into its components.

    Returns dict with keys: ticker, option_type ('PUT'|'CALL'), strike,
    expiration (date object), expiration_str (e.g. "Sep 18 '26").
    Returns None if the symbol doesn't parse.
    """
    if not symbol:
        return None
    s = str(symbol).strip().upper()
    m = _CONTRACT_RE.match(s)
    if not m:
        return None
    ticker, opt_type, strike_str, date_str = m.groups()
    try:
        year = int(date_str[:4])
        month = int(date_str[4:6])
        day = int(date_str[6:8])
        exp = _date(year, month, day)
    except (ValueError, TypeError):
        return None
    # Strike: drop trailing .0 for cleaner display
    try:
        strike = float(strike_str)
        strike_display = f"{strike:g}"
    except ValueError:
        strike_display = strike_str
    return {
        "ticker": ticker,
        "option_type": opt_type,
        "strike": strike_display,
        "strike_num": strike,
        "expiration": exp,
        "expiration_str": f"{_MONTHS[month-1]} {day} '{year % 100:02d}",
    }


def pretty_contract(symbol: str | None) -> Markup:
    """Render a contract symbol as scannable inline HTML.

    Example:
        pretty_contract("LITE_PUT_660_20260918") →
            <span class="contract" title="LITE PUT $660 exp Sep 18 '26">
              <span class="contract-ticker">LITE</span>
              <span class="contract-strike contract-put">$660P</span>
              <span class="contract-exp">Sep 18 '26</span>
            </span>

    When the string doesn't parse as a contract (e.g. it's an equity ticker
    or just text), returns the original text wrapped in <code>.
    """
    if not symbol:
        return Markup("")
    s = str(symbol)
    parsed = parse_contract(s)
    if not parsed:
        # Not a contract — render as plain code element
        return Markup(f'<code>{escape(s)}</code>')
    # The 'P' or 'C' suffix lets the user tell put vs call at a glance
    type_suffix = "P" if parsed["option_type"] == "PUT" else "C"
    type_class = "contract-put" if parsed["option_type"] == "PUT" else "contract-call"
    return Markup(
        f'<span class="contract" title="{escape(s)}">'
        f'<span class="contract-ticker">{escape(parsed["ticker"])}</span>'
        f'<span class="contract-strike {type_class}">'
        f'${escape(parsed["strike"])}{type_suffix}'
        f'</span>'
        f'<span class="contract-exp">{escape(parsed["expiration_str"])}</span>'
        f'</span>'
    )


def pretty_contract_compact(symbol: str | None) -> Markup:
    """One-line compact variant for tight spaces (search results, tooltips):

        LITE_PUT_660_20260918  →  LITE $660P · Sep 18 '26

    Same parser; different layout (single line, dots-as-separators, no
    structured HTML — just a styled <code> with the parsed form as text).
    """
    if not symbol:
        return Markup("")
    s = str(symbol)
    parsed = parse_contract(s)
    if not parsed:
        return Markup(f'<code>{escape(s)}</code>')
    type_suffix = "P" if parsed["option_type"] == "PUT" else "C"
    type_class = "contract-put" if parsed["option_type"] == "PUT" else "contract-call"
    pretty = f'{parsed["ticker"]} ${parsed["strike"]}{type_suffix} · {parsed["expiration_str"]}'
    return Markup(
        f'<span class="contract-inline {type_class}" title="{escape(s)}">'
        f'{escape(pretty)}'
        f'</span>'
    )


def register_jinja_filters(env) -> None:
    """Register contract pretty-printers as Jinja filters:

        {{ a.ident | pretty_contract }}          # structured (3 spans)
        {{ a.ident | pretty_contract_compact }}  # one-line variant
    """
    env.filters["pretty_contract"] = pretty_contract
    env.filters["pretty_contract_compact"] = pretty_contract_compact
