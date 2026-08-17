"""Same-cycle vintage guard — never mix pre-gap technicals with live quotes.

Origin (2026-07-30): MSFT closed $390.54, gapped to $450.21 (+15.3%) intraday.
The LONG DATED CSP card rendered a LIVE chain quote next to technicals computed
from the PREVIOUS close: "✅ RSI favourable · RSI 50" (real post-gap RSI ~65-70,
which would flip the hook from promote to caution) and "⚠ LT verdict
`downtrend` (-9.8% vs 200-SMA)" (at $450 spot MSFT was ABOVE its $431 200-SMA).

The guard compares each ticker's live quote against the close the technicals
were computed from (``technicals[sym]["spot"]`` — yfinance close). When the
move exceeds ``vintage_guard.max_intraday_move_pct`` (default 5%):

  - RSI is NOT recomputed (that needs the intraday bar — never fabricate).
    Instead every RSI read on the name's new-open card lines is tagged
    "⚠ pre-gap" and any "✅ RSI favourable" promotion badge is stripped —
    a promote computed from stale RSI is downgraded to keep+caution.
  - The 200-SMA distance IS recomputed (the SMA comes from history; only the
    spot side moved) and LT-verdict annotations are rewritten at live spot.

Position-management lines are exempt — their urgency doesn't depend on RSI.
The line transforms only touch the promote badge (which the RSI hook attaches
exclusively to NEW opens) and Triggers-label card lines.

Config: ``briefing.yaml -> vintage_guard: {enabled: true,
max_intraday_move_pct: 0.05}``. Fail-open: missing quotes/technicals → no
flags, briefing unchanged.

Rule #46 (PLTR 2026-08-04): the guard is fail-SAFE, not fail-open, on the
favourable-badge side. The observed card — "PULLBACK CSP PLTR — sell $145P
... RSI 48 🟢 pullback" — shipped while PLTR was +29% intraday (spot ~$163
vs the technicals close $125.65; live RSI ~70-75, a hard block) because only
22/36 symbols got yfinance quotes and PLTR's was missing, so the guard had
no drift reference and silently skipped the name. Two additions:

  - **Broker-quote fallback** (:func:`broker_price_map`): when the yfinance
    quote is missing, the drift reference comes from the E*TRADE positions
    payload (equity ``price``/``lastTrade`` — the SAME number the card's
    "-11% below spot" math used; option positions' underlying price fields
    when present). Pass ``positions=`` to :func:`compute_flags`.
  - **Unverifiable → no favourable badge**: when NEITHER source exists, the
    vintage is truly unverifiable — the name is flagged ``unverified`` and
    every favourable RSI read ("✅ RSI favourable", "RSI 48 🟢 pullback") is
    downgraded to "RSI 48 ⚠ unverified (no live quote this cycle) — do not
    trust the favourable read" (promote → keep+caution). Management lines
    stay exempt.

:func:`resolve_new_open_rsi` is the shared generation-time resolver — the
PULLBACK CSP path and ``pre_trade_validator.build_context_from_snapshot``
use it so gates and validators receive the live/recomputed RSI, never the
stale snapshot value, on new-open decisions.
"""

from __future__ import annotations

import re

try:
    from analysis import line_exclusions
except ImportError:  # pragma: no cover - standalone fallback
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    try:
        from analysis import line_exclusions
    except ImportError:
        import line_exclusions  # type: ignore


DEFAULT_MAX_INTRADAY_MOVE_PCT = 0.05


def _cfg(config: dict | None) -> dict:
    block = (config or {}).get("vintage_guard")
    return block if isinstance(block, dict) else {}


def _live_price(q) -> float | None:
    if not isinstance(q, dict):
        return None
    for k in ("lastTrade", "last", "price"):
        try:
            v = float(q.get(k))
            if v > 0:
                return v
        except (TypeError, ValueError):
            continue
    return None


def broker_price_map(positions) -> dict[str, float]:
    """Fallback drift references from the E*TRADE positions payload.

    Equity positions carry a live-ish ``price``/``lastTrade`` (the number the
    briefing's own "-X% below spot" math uses); option positions sometimes
    carry an underlying price field. Returns {TICKER: price}. Fail-open:
    anything unusable is skipped.
    """
    out: dict[str, float] = {}
    for p in (positions or []):
        if not isinstance(p, dict):
            continue
        if p.get("assetType") == "EQUITY":
            sym = str(p.get("symbol") or "").upper()
            keys = ("price", "lastTrade", "last")
        elif p.get("assetType") == "OPTION":
            sym = str(p.get("underlying") or "").upper()
            keys = ("underlyingPrice", "underlying_price", "underlyingLastPrice")
        else:
            continue
        if not sym or sym in out:
            # Equities are appended first per payload order; first hit wins.
            pass
        for k in keys:
            try:
                v = float(p.get(k))
            except (TypeError, ValueError):
                continue
            if v > 0 and sym:
                out.setdefault(sym, v)
                break
    return out


def wilder_rsi_live(recent_closes, live_price, period: int = 14) -> float | None:
    """Wilder's RSI on the daily close series with today's LIVE price appended
    as the current bar (the same approach as the rotation playbook's gate
    battery). Returns None when the series is too short (< period+1 prices) —
    never fabricated (rule #19)."""
    if not isinstance(recent_closes, (list, tuple)) or live_price is None:
        return None
    try:
        prices = [float(c) for c in recent_closes
                  if c is not None and float(c) > 0]
        prices.append(float(live_price))
    except (TypeError, ValueError):
        return None
    if len(prices) < period + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, ls in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + ls) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def compute_flags(
    quotes: dict | None,
    technicals: dict | None,
    config: dict | None = None,
    positions: list | None = None,
) -> dict[str, dict]:
    """Return {TICKER: flag} for names whose live quote has moved more than
    the threshold from the close the technicals were computed from.

    flag = {"move_pct": +15.3, "live_spot": 450.21, "tech_close": 390.54,
            "sma_200": 431.0 | None, "vs_sma200_live_pct": +4.5 | None,
            "price_source": "quote" | "broker_position"}

    ``positions`` (rule #46): when provided, names whose yfinance quote is
    missing fall back to the E*TRADE position price for the drift reference;
    names with NEITHER source get an ``{"unverified": True}`` flag so
    :func:`annotate_briefing` can strip favourable RSI badges (a favourable
    badge requires VERIFIED freshness). Legacy 3-arg calls (positions=None)
    keep the original fail-open behavior byte-identical.

    Fail-open otherwise: disabled config, missing quotes, or missing
    technicals close → {} (no tags, briefing unchanged).
    """
    cfg = _cfg(config)
    if not cfg.get("enabled", True):
        return {}
    try:
        thr = float(cfg.get("max_intraday_move_pct", DEFAULT_MAX_INTRADAY_MOVE_PCT))
    except (TypeError, ValueError):
        thr = DEFAULT_MAX_INTRADAY_MOVE_PCT

    broker_prices = broker_price_map(positions) if positions is not None else {}

    flags: dict[str, dict] = {}
    for sym, tech in (technicals or {}).items():
        if not isinstance(tech, dict):
            continue
        try:
            tech_close = float(tech.get("spot"))
        except (TypeError, ValueError):
            continue
        if tech_close <= 0:
            continue
        live = _live_price((quotes or {}).get(sym) or (quotes or {}).get(str(sym).upper()))
        price_source = "quote"
        if live is None and broker_prices:
            live = broker_prices.get(str(sym).upper())
            price_source = "broker_position"
        if live is None:
            # Rule #46 fail-safe: with positions provided, a name with no
            # drift reference at all is UNVERIFIED — its favourable RSI
            # badges must not survive. Legacy callers (positions=None) keep
            # the old fail-open skip.
            if positions is not None and tech.get("rsi_14") is not None:
                flags[str(sym).upper()] = {
                    "unverified": True,
                    "tech_close": tech_close,
                    "move_pct": None,
                    "live_spot": None,
                }
            continue
        move = (live - tech_close) / tech_close
        if abs(move) <= thr:
            continue
        flag = {
            "move_pct": round(move * 100.0, 1),
            "live_spot": live,
            "tech_close": tech_close,
            "sma_200": None,
            "vs_sma200_live_pct": None,
            "price_source": price_source,
        }
        try:
            sma = float(tech.get("sma_200"))
            if sma > 0:
                flag["sma_200"] = sma
                flag["vs_sma200_live_pct"] = round((live - sma) / sma * 100.0, 1)
        except (TypeError, ValueError):
            pass
        flags[str(sym).upper()] = flag
    return flags


def resolve_new_open_rsi(
    ticker: str,
    technicals: dict | None,
    quotes: dict | None = None,
    positions: list | None = None,
    config: dict | None = None,
) -> dict:
    """Resolve the RSI value a NEW-OPEN gate/validator may trust for ``ticker``.

    Returns a dict:
      rsi          — trusted value for gating (snapshot when fresh, live when
                     recomputed, snapshot when unverified, None when stale and
                     uncomputable)
      snapshot_rsi — the raw technicals rsi_14
      status       — "fresh" | "live" | "unverified" | "stale"
      verified     — True only for fresh / live
      note         — human annotation for the card line (None when fresh)
      live_spot / move_pct / price_source — drift details when measured

    Statuses:
      fresh      — a live price exists and moved ≤ threshold → snapshot RSI OK.
      live       — moved > threshold, live RSI recomputed (Wilder's, live
                   price as the current bar) from ``recent_closes``.
      stale      — moved > threshold, live RSI not computable. Callers must
                   treat an UP-move as a hard exclusion for new put opens
                   (the live RSI is plausibly >70 — rule #44 fail-safe).
      unverified — no live price from quotes OR positions → the favourable
                   read cannot be trusted (promote → keep+caution, rule #46).
    """
    tk = str(ticker or "").upper()
    tech = (technicals or {}).get(tk) or (technicals or {}).get(ticker) or {}
    tech = tech if isinstance(tech, dict) else {}
    snapshot_rsi = tech.get("rsi_14")
    out = {
        "rsi": snapshot_rsi, "snapshot_rsi": snapshot_rsi,
        "status": "fresh", "verified": True, "note": None,
        "live_spot": None, "move_pct": None, "price_source": None,
    }
    try:
        tech_close = float(tech.get("spot"))
    except (TypeError, ValueError):
        tech_close = 0.0

    live = _live_price((quotes or {}).get(tk) or (quotes or {}).get(ticker))
    source = "quote"
    if live is None:
        live = broker_price_map(positions).get(tk)
        source = "broker_position"
    if live is None:
        if snapshot_rsi is not None:
            out.update({
                "status": "unverified", "verified": False,
                "note": ("⚠ unverified (no live quote this cycle) — do not "
                         "trust the favourable read"),
            })
        return out
    if tech_close <= 0:
        # Live price exists but the technicals carry no reference close —
        # drift is unmeasurable; fail-open (the missing-QUOTE class is the
        # rule-#46 bug; a degenerate technicals dict is not).
        return out

    out["live_spot"] = live
    out["price_source"] = source
    cfg = _cfg(config)
    try:
        thr = float(cfg.get("max_intraday_move_pct", DEFAULT_MAX_INTRADAY_MOVE_PCT))
    except (TypeError, ValueError):
        thr = DEFAULT_MAX_INTRADAY_MOVE_PCT
    move = (live - tech_close) / tech_close
    out["move_pct"] = round(move * 100.0, 1)
    if not cfg.get("enabled", True) or abs(move) <= thr:
        return out

    prev = f"{float(snapshot_rsi):.0f}" if snapshot_rsi is not None else "n/a"
    # Prefer the threaded ~60-close series (snapshot ``rsi_closes``) — the
    # legacy ``recent_closes`` carries only ~6 closes, which can never seed
    # a 14-period Wilder RSI, so the live recompute failed exactly when it
    # was needed (2026-08-17 briefing: WDC/SNDK/MU excluded with "stale RSI
    # on a +9.4% up-move — live RSI not computable"). Fall back to
    # recent_closes for older snapshots; absent both → the legacy stale
    # fail-safe below (rule #19 — never fabricated).
    _closes = tech.get("rsi_closes") or tech.get("recent_closes")
    live_rsi = wilder_rsi_live(_closes, live)
    if live_rsi is not None:
        out.update({
            "rsi": round(float(live_rsi), 1), "status": "live", "verified": True,
            "note": (f"⚠ live RSI {live_rsi:.0f} recomputed at ${live:,.2f} "
                     f"(spot {move * 100.0:+.1f}% since the technicals close; "
                     f"snapshot RSI {prev} was pre-gap)"),
        })
    else:
        out.update({
            "rsi": None, "status": "stale", "verified": False,
            "note": (f"⚠ stale RSI — reverify (spot {move * 100.0:+.1f}% since "
                     f"the technicals close; live RSI not computable from the "
                     f"close series)"),
        })
    return out


# ── Rendered-markdown post-pass ─────────────────────────────────────────────

# Card headers that establish the current ticker context.
_HEADER_BACKTICK_RE = re.compile(r"`([A-Z][A-Z0-9]{0,4})`")
_HEADER_BOLD_RE = re.compile(r"\*\*([A-Z][A-Z0-9]{0,4})\*\*")
_OPT_PREFIX_RE = re.compile(r"\b([A-Z][A-Z0-9]{0,4})_(?:PUT|CALL)(?=_|\s|$)")

_BADGE_RSI_RE = re.compile(r"✅ RSI favourable(?:\s*·\s*RSI (\d+(?:\.\d+)?))?")
_BARE_RSI_RE = re.compile(r"\bRSI (\d+(?:\.\d+)?)\b(?! ⚠)")
_SMA_DIST_RE = re.compile(r"\(([+-]?\d+(?:\.\d+)?)% vs 200-SMA\)")
# rsi_discipline.tag()'s favoured badge (e.g. "RSI 48 🟢 pullback") — a
# favourable read that must not survive on a stale/unverified vintage.
_FAV_TAG_RE = re.compile(r"\bRSI (\d+(?:\.\d+)?) 🟢 [a-z][a-z -]*")


def _context_ticker(line: str, flagged: set[str]) -> str | None:
    """High-confidence ticker on a header-ish line, restricted to flagged names."""
    for rx in (_HEADER_BACKTICK_RE, _HEADER_BOLD_RE, _OPT_PREFIX_RE):
        for tok in rx.findall(line):
            if tok in flagged:
                return tok
    return None


def _names_other_ticker(line: str, flagged: set[str]) -> bool:
    """True when the line names a high-confidence ticker that is NOT flagged —
    used to clear a stale context so a flagged name's tags never bleed onto a
    neighbouring card (e.g. consecutive action-list items)."""
    for rx in (_HEADER_BACKTICK_RE, _OPT_PREFIX_RE):
        for tok in rx.findall(line):
            if tok not in flagged:
                return True
    return False


def _stale_rsi_note(flag: dict) -> str:
    if flag.get("unverified"):
        return ("⚠ unverified (no live quote this cycle) — do not trust the "
                "favourable read")
    return (f"⚠ pre-gap (spot has moved {flag['move_pct']:+.1f}% since RSI "
            f"computation — treat as stale; do not trust a favourable read)")


def annotate_briefing(md: str, flags: dict[str, dict]) -> tuple[str, dict]:
    """Rewrite stale-vintage reads on flagged tickers' card lines.

    - "✅ RSI favourable · RSI 50" → "RSI 50 ⚠ pre-gap (...)" (badge stripped)
    - bare "RSI 50" on a Triggers-label line → "RSI 50 ⚠ pre-gap"
    - "(-9.8% vs 200-SMA)" on LT-verdict lines → recomputed at live spot, with
      an explicit note when the sign flipped (spot reclaimed the 200-SMA).

    Returns (new_md, stats). No flags → (md, zero-stats) unchanged.
    """
    stats = {"rsi_tagged": 0, "sma_recomputed": 0, "tickers": sorted(flags or {})}
    if not md or not flags:
        return md, stats

    flagged = set(flags)
    out: list[str] = []
    context: str | None = None
    for line in md.splitlines():
        stripped = line.lstrip()
        # Context tracking: headers and top-level card lines that name a
        # flagged ticker set the context; other headings clear it.
        tk_on_line = _context_ticker(line, flagged)
        if stripped.startswith("#"):
            context = tk_on_line
        elif line_exclusions.is_excluded_line(line):
            pass  # continuation/label lines inherit context, never change it
        elif tk_on_line:
            context = tk_on_line
        elif _names_other_ticker(line, flagged):
            context = None  # a different name's card starts — stop tagging
        tk = tk_on_line or context
        if not tk or tk not in flags:
            out.append(line)
            continue
        flag = flags[tk]
        new_line = line

        # 1) Strip the promote badge; tag the RSI value as pre-gap (or
        #    unverified — rule #46: a favourable badge requires VERIFIED
        #    freshness). The badge only ever decorates NEW-open lines (the
        #    hook never promotes management), so management lines are
        #    naturally exempt.
        if "✅ RSI favourable" in new_line:
            def _badge_sub(m: re.Match) -> str:
                if m.group(1):
                    return f"RSI {m.group(1)} {_stale_rsi_note(flag)}"
                return f"⚠ RSI stale {_stale_rsi_note(flag)}"
            new_line = _BADGE_RSI_RE.sub(_badge_sub, new_line)
            stats["rsi_tagged"] += 1
        elif (not flag.get("unverified")
              and line_exclusions.label_of(new_line) == "triggers"
              and "pre-gap" not in new_line and _BARE_RSI_RE.search(new_line)):
            new_line = _BARE_RSI_RE.sub(
                lambda m: f"RSI {m.group(1)} ⚠ pre-gap", new_line, count=1)
            stats["rsi_tagged"] += 1

        # 1b) rsi_discipline.tag()'s favoured badge ("RSI 48 🟢 pullback") is
        #     just as favourable as the ✅ promote badge — downgrade it the
        #     same way on stale/unverified vintages (the PLTR card shape).
        if "🟢" in new_line and _FAV_TAG_RE.search(new_line):
            new_line = _FAV_TAG_RE.sub(
                lambda m: f"RSI {m.group(1)} {_stale_rsi_note(flag)}", new_line)
            stats["rsi_tagged"] += 1

        # 2) Recompute the 200-SMA distance at live spot on LT-verdict reads.
        if "LT verdict" in new_line and flag.get("vs_sma200_live_pct") is not None:
            live_pct = flag["vs_sma200_live_pct"]

            def _sma_sub(m: re.Match) -> str:
                try:
                    old = float(m.group(1))
                except (TypeError, ValueError):
                    old = None
                note = (f"({live_pct:+.1f}% vs 200-SMA at live spot "
                        f"${flag['live_spot']:,.2f}; pre-gap read was "
                        f"{m.group(1)}%)")
                if old is not None and old < 0 <= live_pct:
                    note += (" — recomputed at live spot: ABOVE the 200-SMA; "
                             "the pre-gap downtrend condition no longer holds")
                return note

            rewritten = _SMA_DIST_RE.sub(_sma_sub, new_line)
            if rewritten != new_line:
                new_line = rewritten
                stats["sma_recomputed"] += 1

        out.append(new_line)

    return "\n".join(out), stats


def footer(flags: dict[str, dict], config: dict | None = None) -> str | None:
    """One-line transparency footer summarizing the vintage guard's action."""
    if not flags:
        return None
    cfg = _cfg(config)
    try:
        thr = float(cfg.get("max_intraday_move_pct", DEFAULT_MAX_INTRADAY_MOVE_PCT))
    except (TypeError, ValueError):
        thr = DEFAULT_MAX_INTRADAY_MOVE_PCT
    movers = {t: f for t, f in flags.items() if not f.get("unverified")}
    unverified = sorted(t for t, f in flags.items() if f.get("unverified"))
    parts: list[str] = []
    if movers:
        names = " · ".join(
            f"{t} {f['move_pct']:+.1f}%" for t, f in sorted(movers.items()))
        parts.append(
            f"{len(movers)} name(s) moved >{thr * 100:.0f}% vs the "
            f"technicals' reference close ({names}); RSI reads tagged pre-gap, "
            f"200-SMA distances recomputed at live spot")
    if unverified:
        parts.append(
            f"{len(unverified)} name(s) had no live quote this cycle "
            f"({' · '.join(unverified)}); favourable RSI badges withheld "
            f"(rule #46)")
    if not parts:
        return None
    return f"_⏱ Vintage guard: {'. '.join(parts)}._"
