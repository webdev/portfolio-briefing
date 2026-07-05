"""Universal 5% strike-overlap check on new short puts (CLAUDE.md hard rule #40).

Origin: analyst audit 2026-07-03 — the overlap check lived ONLY inline in the
LT_CSP path of long_term_opportunities.py, so the PULLBACK_CSP surface shipped
an AMZN $215P while the user held an AMZN $225P Aug 21 (4.4% apart — inside
the 5% band that made the LT_CSP path skip AMZN the same day with
"concentrates rather than diversifies").

This module is the single source of truth. Every generator emitting a new
short-put recommendation (LT_CSP / LONG_DATED_CSP, PULLBACK_CSP) calls
``check_strike_overlap``; the pre-trade validator enforces it late-stage as
Rule 15 ``PUT_STRIKE_OVERLAP`` (BLOCK).
"""

from __future__ import annotations


# Strikes within this fraction of an existing held short-put strike are
# "overlapping" — the same trade, not diversification.
OVERLAP_PCT = 0.05


def _normalize_strikes(ticker: str, existing_short_puts) -> list[float]:
    """Accept the shapes callers already have and return a flat strike list.

    Supported:
      - list of floats/ints:              [225.0, 190.0]
      - list of dicts with "strike":      [{"strike": 225.0, ...}, ...]
      - per-ticker map:                   {"AMZN": {"strikes": [225.0], ...}}
      - per-ticker map of lists:          {"AMZN": [225.0]}
    """
    if existing_short_puts is None:
        return []
    entry = existing_short_puts
    if isinstance(existing_short_puts, dict):
        tk = (ticker or "").upper()
        entry = existing_short_puts.get(tk)
        if entry is None:
            entry = existing_short_puts.get(ticker)
        if entry is None:
            return []
    if isinstance(entry, dict):
        entry = entry.get("strikes") or []
    strikes: list[float] = []
    for item in entry or []:
        raw = item.get("strike") if isinstance(item, dict) else item
        try:
            s = float(raw)
        except (TypeError, ValueError):
            continue
        if s > 0:
            strikes.append(s)
    return strikes


def check_strike_overlap(
    ticker: str,
    proposed_strike,
    existing_short_puts,
    overlap_pct: float = OVERLAP_PCT,
) -> dict:
    """Check a proposed new short-put strike against held short puts.

    Args:
        ticker: underlying symbol (used only for per-ticker map lookups).
        proposed_strike: the new short-put strike being considered.
        existing_short_puts: held short-put strikes for this name — see
            ``_normalize_strikes`` for accepted shapes.
        overlap_pct: overlap band as a fraction of the HELD strike (default 5%).

    Returns:
        {"overlap": bool,               # True → reject/demote the rec
         "existing_strike": float|None, # nearest held strike (None = no holdings)
         "distance_pct": float|None}    # |proposed-held|/held for that strike

    Distance is measured relative to the HELD strike (same convention as the
    original LT_CSP inline check), so results are byte-compatible with the
    prior behavior on that surface.
    """
    try:
        proposed = float(proposed_strike)
    except (TypeError, ValueError):
        return {"overlap": False, "existing_strike": None, "distance_pct": None}
    if proposed <= 0:
        return {"overlap": False, "existing_strike": None, "distance_pct": None}

    strikes = _normalize_strikes(ticker, existing_short_puts)
    if not strikes:
        return {"overlap": False, "existing_strike": None, "distance_pct": None}

    nearest = None
    nearest_dist = None
    for held in strikes:
        dist = abs(proposed - held) / held
        if nearest_dist is None or dist < nearest_dist:
            nearest = held
            nearest_dist = dist

    return {
        "overlap": bool(nearest_dist is not None and nearest_dist <= overlap_pct),
        "existing_strike": nearest,
        "distance_pct": nearest_dist,
    }
