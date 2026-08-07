"""Canonical spot resolution + render-time price-consistency verifier.

Origin (2026-08-07 briefing): ONE ticker rendered THREE different spot prices —
TEAM showed $109.73 on the Candidate card (24h-old scout cache, pre-earnings),
$110.17 on the Technical Read header (previous OHLC close), and ~$144.41 on the
LT ADD row ("BUY ~$5,000 of TEAM (~34 shares @ ~$144.41)", the live E*TRADE
batch quote after a +31% post-earnings gap). The live quote was CORRECT (the
same-cycle E*TRADE chain showed the ~0.50-delta strike at $145); the candidate
card was pricing off the stale scout cache while labelling its ticket
"_Live E*TRADE chain_".

Two exports:

- ``resolve_spot`` — the ONE canonical live spot for a ticker this cycle:
  the snapshot's live quote first (E*TRADE batch / yfinance), the broker
  positions payload as fallback (same reference order as
  ``vintage_guard.broker_price_map``). Fail-closed: no live source → None,
  never a cached/fabricated value.
- ``price_disagreements`` / ``render_panel`` — a render-time sweep over the
  assembled briefing markdown that extracts per-ticker SPOT mentions from a
  tight set of header/action patterns and flags any ticker whose rendered
  spots disagree by more than the tolerance (default 2%). Deliberately
  conservative: strikes, price targets, FV notes, premiums and prose numbers
  are never matched — only spot-position prices in known surfaces.
"""

from __future__ import annotations

import math
import re

try:
    from analysis.vintage_guard import _live_price, broker_price_map
except ImportError:  # pragma: no cover - flat-path fallback
    from vintage_guard import _live_price, broker_price_map  # type: ignore

# Rendered spots for the same ticker may not disagree by more than this.
DEFAULT_TOLERANCE_PCT = 2.0

# Conservative spot-mention patterns — each captures (ticker, price) and is
# anchored to a KNOWN surface so strikes ($99P), targets, FV notes and
# premiums can't match:
#   1. Candidate card header:  **🎯 CANDIDATE · `TEAM` · $109.73** ...
#   2. Technical Read header:  ### TEAM — $110.17 · ...
#   3. Equity sizing line:     BUY ~$5,000 of TEAM (~34 shares @ ~$144.41)
_SPOT_PATTERNS = [
    re.compile(r"·\s*`([A-Z][A-Z0-9.]{0,6})`\s*·\s*\$([\d,]+(?:\.\d+)?)\*\*"),
    re.compile(r"^###\s+([A-Z][A-Z0-9.]{0,6})\s+—\s+\$([\d,]+(?:\.\d+)?)", re.M),
    re.compile(r"\bof\s+([A-Z][A-Z0-9.]{0,6})\s+\(~[\d,]+\s+shares\s+@\s+~?\$([\d,]+(?:\.\d+)?)\)"),
]


def resolve_spot(ticker: str | None, quotes: dict | None = None,
                 positions: list | None = None) -> float | None:
    """The ONE canonical live spot for ``ticker`` this cycle.

    Order: live quote map (``quotes[TK]["last"/"lastTrade"/"price"]``) →
    broker positions payload (equity ``price``/``lastTrade``, option
    underlying-price fields). Returns None when neither source has a usable
    value (fail-closed — rule #10, never substitute a cached default).
    """
    tk = (ticker or "").upper()
    if not tk:
        return None
    p = _live_price((quotes or {}).get(tk))
    if p is None and positions:
        p = broker_price_map(positions).get(tk)
    try:
        p = float(p) if p is not None else None
    except (TypeError, ValueError):
        return None
    if p is None or not math.isfinite(p) or p <= 0:
        return None
    return p


def spot_mentions(md: str) -> dict[str, list[tuple[float, str]]]:
    """{TICKER: [(price, matched_line), ...]} for every spot-position price
    mention found by the conservative patterns above."""
    out: dict[str, list[tuple[float, str]]] = {}
    for line in (md or "").split("\n"):
        for pat in _SPOT_PATTERNS:
            for m in pat.finditer(line):
                tk = m.group(1).upper()
                try:
                    price = float(m.group(2).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                if price <= 0:
                    continue
                out.setdefault(tk, []).append((price, line.strip()))
    return out


def price_disagreements(md: str,
                        tolerance_pct: float = DEFAULT_TOLERANCE_PCT) -> list[dict]:
    """Tickers whose rendered spot prices disagree by more than the tolerance.

    Returns [{ticker, min, max, spread_pct, lines}] sorted by spread
    descending. Single-mention tickers can never flag; tolerance is measured
    as (max-min)/min so a 2% band means "all rendered spots within 2% of the
    lowest one."
    """
    offenders: list[dict] = []
    for tk, mentions in spot_mentions(md).items():
        prices = [p for p, _l in mentions]
        if len(prices) < 2:
            continue
        lo, hi = min(prices), max(prices)
        if lo <= 0:
            continue
        spread_pct = (hi - lo) / lo * 100.0
        if spread_pct > tolerance_pct:
            offenders.append({
                "ticker": tk,
                "min": lo,
                "max": hi,
                "spread_pct": spread_pct,
                "lines": [line for _p, line in mentions],
            })
    offenders.sort(key=lambda o: o["spread_pct"], reverse=True)
    return offenders


def render_panel(offenders: list[dict],
                 tolerance_pct: float = DEFAULT_TOLERANCE_PCT) -> list[str]:
    """Markdown warning panel for price disagreements. Empty list when clean
    (the panel only renders on violations — same pattern as the tenor sweep)."""
    if not offenders:
        return []
    lines = [
        "## 💲 Price Consistency Check",
        "",
        (f"_{len(offenders)} ticker(s) render spot prices that disagree by "
         f"more than {tolerance_pct:.0f}% across briefing surfaces — one of "
         f"the sources is stale; trust the LIVE quote and re-verify before "
         f"acting on any line below:_"),
    ]
    for o in offenders[:10]:
        lines.append(
            f"- **{o['ticker']}** — ${o['min']:,.2f} vs ${o['max']:,.2f} "
            f"({o['spread_pct']:+.1f}% spread) across {len(o['lines'])} line(s)"
        )
    lines.append("")
    return lines
