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


# ── RSI one-voice sweep (rule #43, 2026-08-14 SNDK) ─────────────────────────
#
# Origin: the SAME briefing rendered SNDK with THREE RSI values — the Best
# Setups spotlight said "RSI 48 late-band" (stale scout-cache close), the
# action-list close card said "RSI 56" (snapshot technicals), and live
# intraday reality was ~76 after SNDK moved $1,367 → $1,625 (+19%) in ~2
# sessions. George: "RSI on SNDK is 76 now... What kind of recommendation is
# this? ... What kind of weakness are we talking about here?"
#
# A name must speak with ONE RSI voice per render. Mirrors the spot sweep
# above: conservative extraction, context tracked only from high-confidence
# ticker markers (backtick ticker, option ident, "### TK — " header).
# Vintage-tagged reads (pre-gap / unverified / stale / recomputed) already
# disclaim their value and are exempt; so are italic footers, table rows and
# fenced blocks. Band thresholds like "RSI 35-45" never match (lookahead).

DEFAULT_RSI_TOLERANCE_PTS = 1.0

_RSI_VALUE_RE = re.compile(
    r"\bRSI[ :]+(\d{1,3}(?:\.\d+)?)(?!\s*[-–]\s*\d)\b")

_RSI_CONTEXT_RES = [
    re.compile(r"`([A-Z][A-Z0-9.]{0,6})`"),
    re.compile(r"\b([A-Z][A-Z0-9.]{0,5})_(?:PUT|CALL)(?=_|\b)"),
    re.compile(r"^#{2,4}\s+([A-Z][A-Z0-9.]{0,6})\s+—"),
    # 2026-08-18 misattribution fix: the equity / market-overview rows
    # ("- **META** $551.51 — RSI 38", "- **SOXX** @ $528.08 — … RSI 47")
    # carry a BOLD ticker followed by a price — without this marker the
    # context lingered on the previous name and the sweep attributed
    # MSFT/NVDA/PLTR/SPY/VOO reads to META ("META — RSI 38 vs RSI 66").
    re.compile(r"\*\*([A-Z][A-Z0-9.]{0,6})\*\*\s+@?\s*\$[\d,]"),
    # New-open card headers carry a BARE ticker after the bold verb
    # ("**CSP — PAID-TO-WAIT** VRT — sell $240P", "⏸ **CSP — PAID-TO-WAIT
    # (wait)** PLTR — sell $150P") — without this marker the PLTR wait
    # card's RSI 66 lines were attributed to the previous item's NOK
    # ("NOK — RSI 37 vs RSI 66 (29 pts apart)").
    re.compile(r"\*\*\s+([A-Z][A-Z0-9.]{0,6})\s+—\s+(?:sell|buy)\b"),
]

# Bold-uppercase tokens that are verbs/labels, never tickers — guard the
# bold-context patterns above against false positives.
_RSI_CONTEXT_STOPWORDS = frozenset({
    "CSP", "CC", "RSI", "PUT", "CALL", "HOLD", "CLOSE", "ROLL", "TRIM",
    "SELL", "BUY", "EXIT", "WAIT", "WATCH", "URGENT", "GTC", "BTC", "STO",
    "LEAP", "EV", "NLV", "OTM", "ITM", "ATM", "DTE", "IV", "IVR", "RVR",
    "FV", "LB", "HB", "MV", "TOTAL", "CASH", "WHY", "ORDER",
})

# Lowercased tokens marking a read that explicitly disclaims its own
# freshness (the vintage guard's annotations) — exempt from the sweep.
_RSI_EXEMPT_TOKENS = ("pre-gap", "unverified", "stale", "recomputed",
                      "pre-move", "snapshot rsi")

# Historical entry-grade annotation ("🎓 entry D (43, RSI 37 prime) · Aug 3")
# — the parenthetical RSI is the ENTRY-time value (dated, historical by
# design), not a second voice on today's read. The fragment is stripped
# before value extraction so a legitimate current-RSI on the same line
# ("… · RSI 42 · … · 🎓 entry D (25) · Aug 12") still sweeps.
_RSI_ENTRY_GRADE_RE = re.compile(r"🎓 entry [A-F][+\-]?\s*\([^)]*\)")


def rsi_mentions(md: str) -> dict[str, list[tuple[float, str]]]:
    """{TICKER: [(rsi_value, line), ...]} for every RSI read attributable to
    a high-confidence ticker context."""
    out: dict[str, list[tuple[float, str]]] = {}
    context: str | None = None
    in_fence = False
    for line in (md or "").split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        tk_on_line = None
        for rx in _RSI_CONTEXT_RES:
            for tok in rx.findall(line):
                tok = tok.upper()
                if tok not in _RSI_CONTEXT_STOPWORDS:
                    tk_on_line = tok
                    break
            if tk_on_line:
                break
        if stripped.startswith("#"):
            context = tk_on_line     # a heading sets or clears the context
            continue
        if tk_on_line:
            context = tk_on_line
        if stripped.startswith("_") or stripped.startswith("|"):
            continue                 # transparency footers / table rows
        if not context:
            continue
        low = line.lower()
        if any(t in low for t in _RSI_EXEMPT_TOKENS):
            continue                 # the read disclaims itself already
        # Strip historical entry-grade fragments — their RSI is the dated
        # ENTRY-time value, not today's read (never a second voice).
        line = _RSI_ENTRY_GRADE_RE.sub("", line)
        for m in _RSI_VALUE_RE.finditer(line):
            try:
                val = float(m.group(1))
            except (TypeError, ValueError):
                continue
            if 0.0 < val <= 100.0:
                out.setdefault(context, []).append((val, stripped))
    return out


def rsi_disagreements(
        md: str,
        tolerance_pts: float = DEFAULT_RSI_TOLERANCE_PTS) -> list[dict]:
    """Tickers whose rendered RSI reads disagree by more than a rounding
    point (default 1.0) in the same render. Single-mention tickers never
    flag. Returns [{ticker, min, max, spread_pts, lines}] sorted by spread
    descending."""
    offenders: list[dict] = []
    for tk, mentions in rsi_mentions(md).items():
        vals = [v for v, _l in mentions]
        if len(vals) < 2:
            continue
        lo, hi = min(vals), max(vals)
        if hi - lo > tolerance_pts:
            offenders.append({
                "ticker": tk,
                "min": lo,
                "max": hi,
                "spread_pts": hi - lo,
                "lines": [line for _v, line in mentions],
            })
    offenders.sort(key=lambda o: o["spread_pts"], reverse=True)
    return offenders


def render_rsi_panel(
        offenders: list[dict],
        tolerance_pts: float = DEFAULT_RSI_TOLERANCE_PTS) -> list[str]:
    """Markdown warning panel for RSI disagreements. Empty when clean."""
    if not offenders:
        return []
    lines = [
        "## 📉 RSI Consistency Check",
        "",
        (f"_{len(offenders)} ticker(s) render RSI values that disagree by "
         f"more than {tolerance_pts:.0f} point(s) in this render — one RSI "
         f"voice per name per cycle (rule #43, the 2026-08-14 SNDK 48/56 "
         f"case): at least one surface graded on a stale RSI; trust the "
         f"live/vintage-resolved value and re-verify before acting:_"),
    ]
    for o in offenders[:10]:
        lines.append(
            f"- **{o['ticker']}** — RSI {o['min']:g} vs RSI {o['max']:g} "
            f"({o['spread_pts']:.0f} pts apart) across "
            f"{len(o['lines'])} line(s)")
    lines.append("")
    return lines
