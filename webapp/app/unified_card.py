"""Unified ticker-card helpers (task #10).

Every tab on /briefing/{date} renders the same infographic-style ticker
card (the tech_card macro from task #7) with a tab-specific "extras"
block overlaid on top. This module holds the small, pure helpers the
`unified_card` Jinja macro needs:

  - resolve_ticker(item, tab_kind) — pull the underlying ticker out of a
    tab-specific row (options use `underlying`, actions parse `ident`,
    ideas skip capacity placeholders, rotations use the target leg).
  - option_pl_pct(o)  — signed P&L fraction for an options review row,
    short/long aware. Returns None when inputs are missing (fail open —
    the template renders "—", never a fabricated number; hard rule #19).
  - extras_tone(kind) — accent tone for the extras block, keyed off the
    action verb / opportunity kind.

All functions are defensive: rows may be Pydantic models OR plain dicts
(merged ideas / rotation JSON), and any missing field returns None
instead of raising — a malformed row must never 500 the briefing page.
"""

from __future__ import annotations

import re
from typing import Any

from . import contract

# Plain equity tickers: letters, optional dot (BRK.B), optional digits.
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,7}$")

# Placeholder "tickers" the pipeline emits that are NOT market symbols —
# never look up tech data (or render a skeleton) for these.
_PLACEHOLDER_TICKERS = {"", "—", "-", "CAPACITY", "N/A", "NONE"}


def _get(item: Any, key: str) -> Any:
    """Field access that works for Pydantic models AND plain dicts."""
    if item is None:
        return None
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _clean_ticker(value: Any) -> str | None:
    tk = (str(value or "")).strip().upper()
    if tk in _PLACEHOLDER_TICKERS:
        return None
    return tk or None


def resolve_ticker(item: Any, tab_kind: str) -> str | None:
    """Return the underlying equity ticker for a tab row, or None.

    None means "this row has no chartable ticker" — the macro then omits
    the tech section entirely (no skeleton for a non-ticker row).
    """
    if item is None:
        return None
    kind = (tab_kind or "").lower()

    if kind == "action":
        ident = str(_get(item, "ident") or "").strip()
        parsed = contract.parse_contract(ident)
        if parsed:
            return _clean_ticker(parsed.get("ticker"))
        if _TICKER_RE.match(ident.upper() or ""):
            return _clean_ticker(ident)
        return None

    if kind in ("option", "strategy"):
        return _clean_ticker(_get(item, "underlying"))

    if kind == "rotation":
        # Equity swap rows carry {from: {...}, to: {ticker}} — chart the
        # target leg (the name being evaluated). Option rotation rows
        # carry {from: {symbol: TICKER_PUT_...}} — chart the underlying.
        to_leg = _get(item, "to")
        tk = _clean_ticker(_get(to_leg, "ticker"))
        if tk:
            return tk
        from_leg = _get(item, "from")
        parsed = contract.parse_contract(str(_get(from_leg, "symbol") or ""))
        if parsed:
            return _clean_ticker(parsed.get("ticker"))
        return _clean_ticker(_get(from_leg, "ticker"))

    # equity / longterm / idea / anything else: plain `ticker` field
    return _clean_ticker(_get(item, "ticker"))


def option_pl_pct(review: Any) -> float | None:
    """Signed P&L fraction for an options-review row.

    Short (qty < 0): profit when mid drops below entry → (entry-mid)/entry.
    Long  (qty > 0): (mid-entry)/entry.
    Missing entry/mid (or entry == 0) → None; the template renders "—".
    """
    try:
        entry = _get(review, "entry_price")
        mid = _get(review, "current_mid")
        if entry is None or mid is None:
            return None
        entry_f = float(entry)
        mid_f = float(mid)
        if entry_f == 0:
            return None
        qty = float(_get(review, "qty") or 0)
        if qty < 0:
            return (entry_f - mid_f) / abs(entry_f)
        return (mid_f - entry_f) / abs(entry_f)
    except (TypeError, ValueError):
        return None


# Extras-block accent tones. `close` = red border, `roll` = amber,
# `ok` = green, everything else neutral. Unknown verbs stay neutral —
# never crash on a new pipeline kind.
_TONE_CLOSE = {"CLOSE", "CLOSE_NOW", "EXIT", "SELL", "STOP", "AVOID"}
_TONE_ROLL = {
    "ROLL", "DEFENSIVE_ROLL", "ROLL_OUT", "ROLL_UP", "ROLL_OUT_AND_UP",
    "EXECUTE_ROLL", "HEDGE", "COLLAR", "DEFENSIVE_COLLAR", "TRIM",
}
_TONE_OK = {
    "TAKE_PROFIT", "CLOSE_FOR_PROFIT", "BUY", "ADD", "LT_ADD",
    "PULLBACK_CSP", "LONG_DATED_CSP", "NEW_CSP", "LT_CSP",
}


def extras_tone(kind: Any) -> str:
    k = str(kind or "").strip().upper().replace(" ", "_")
    if k in _TONE_CLOSE:
        return "close"
    if k in _TONE_ROLL:
        return "roll"
    if k in _TONE_OK:
        return "ok"
    return "neutral"


# ─── Task #17 — Strategy tab redistribution ───────────────────────────
# The Strategy tab is retired. Its contents redistribute by concept:
#   - write_covered_call / collar → the Options tab, as an inline
#     affordance on the option card for that underlying (or a standalone
#     "Strategy proposals" card when no open option position matches).
#   - sublot_completion → the Ideas tab (via ideas.build_merged_ideas —
#     a sub-lot completion IS a new-open recommendation).
#   - everything else (tier_a_no_cc policy notes, covered_strangle,
#     unknown future types) → standalone cards in the Options tab's
#     "Strategy proposals" subsection. Nothing is ever dropped
#     (hard rule #24 — never hide, always surface).

_AFFORDANCE_LABELS = {
    "write_covered_call": "WRITE CC",
    "collar": "COLLAR",
}
_SUBLOT_TYPE = "sublot_completion"


def strategy_affordance_label(su_type: Any) -> str:
    """Short affordance label for an attachable strategy-upgrade type."""
    return _AFFORDANCE_LABELS.get(str(su_type or "").strip().lower(), "")


def split_strategy_upgrades(briefing: Any) -> tuple[dict[str, list[Any]], list[Any]]:
    """Split briefing.strategy_upgrades for the Options tab (task #17).

    Returns (attached, standalone):
      - attached:   {UNDERLYING: [upgrade, ...]} for covered-call / collar
                    proposals whose underlying has an open options review —
                    rendered as an inline affordance on that option card.
      - standalone: every other non-sublot upgrade (including CC/collar
                    proposals with no matching option position) — rendered
                    as strategy cards in the "Strategy proposals"
                    subsection of the Options tab.

    Sub-lot completions are excluded here — they surface on the Ideas tab
    via ideas.build_merged_ideas. Defensive throughout: rows may be
    Pydantic models or dicts; malformed rows land in standalone rather
    than raising.
    """
    upgrades = list(getattr(briefing, "strategy_upgrades", []) or [])
    reviews = list(getattr(briefing, "options_reviews", []) or [])
    option_underlyings = set()
    for o in reviews:
        tk = _clean_ticker(_get(o, "underlying"))
        if tk:
            option_underlyings.add(tk)

    attached: dict[str, list[Any]] = {}
    standalone: list[Any] = []
    for su in upgrades:
        su_type = str(_get(su, "type") or "").strip().lower()
        if su_type == _SUBLOT_TYPE:
            continue  # surfaced on the Ideas tab
        tk = _clean_ticker(_get(su, "underlying"))
        if su_type in _AFFORDANCE_LABELS and tk and tk in option_underlyings:
            attached.setdefault(tk, []).append(su)
        else:
            standalone.append(su)
    return attached, standalone
