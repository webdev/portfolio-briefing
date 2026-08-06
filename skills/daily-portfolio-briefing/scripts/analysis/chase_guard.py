"""Chase guard — multi-session vertical run-up block on new put-sales
(rule #44, INTC 2026-08-05).

Observed row (2026-08-05 9:54 AM briefing, Rotation Playbook Phase 2):

    | 4 | INTC $90P Sep 11 '26 | Sell-to-Open | 1 | $5.85 GTD | +$585 |
    $9,000 | 62% | Parkev HOLD Medi · 27d · pullback zone · 🔓 unlocks
    after Phase 1 |

Reality: INTC was +24.1% in 5 sessions (+11.7% in 2 — closed $91.00 Aug 3,
$100.86 Aug 4, spot $101.64). The daily Wilder's RSI read 49.5 (in-band)
because the bars were FRESH — the vintage/staleness guard correctly did not
fire — and an RSI off an oversold base mathematically cannot flag a 24%
vertical. The $90 strike was a level INTC traded at THREE DAYS earlier.

Rule #44's text already mandates: "a same-day/2-session up-move > 5%
disqualifies a new put REGARDLESS of what the snapshot RSI says". This
module is that mandate as an INDEPENDENT gate-battery member: it measures
the 2-session and 5-session change from real daily closes + the live spot
and blocks NEW put-sales on a vertical tape. It applies to every new
put-sale surface (playbook battery, PULLBACK/PAID-TO-WAIT CSP, LT_CSP,
income opportunities, candidate trades). Covered calls are NOT affected —
verticals are exactly when CCs are favored (tier envelopes govern those).

Fail-open discipline (rule #19 — never a fabricated block): closes
unavailable → no block; but when the spot IS measurable and the closes
aren't, ``check_chase`` returns a "run-up unverifiable" caution the caller
renders on the card. Exclusions render per rule #24 (warnings footer /
blocked footers), never silently.

Config (briefing.yaml → chase_guard):
  enabled            (default true)
  two_session_pct    (default 0.05)  block when spot vs close-2-back > this
  five_session_pct   (default 0.10)  block when spot vs close-5-back > this
"""

from __future__ import annotations

DEFAULT_TWO_SESSION_PCT = 0.05
DEFAULT_FIVE_SESSION_PCT = 0.10

# Sides the guard applies to. "call" (covered calls) and "buy" are exempt.
_PUT_SIDES = {"put", "csp"}


def _f(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def load_config(config) -> dict:
    """Resolved chase-guard thresholds (config → defaults, fail-open)."""
    cg = config.get("chase_guard") if isinstance(config, dict) else None
    cg = cg if isinstance(cg, dict) else {}
    return {
        "enabled": bool(cg.get("enabled", True)),
        "two_session_pct": _f(cg.get("two_session_pct"),
                              DEFAULT_TWO_SESSION_PCT),
        "five_session_pct": _f(cg.get("five_session_pct"),
                               DEFAULT_FIVE_SESSION_PCT),
    }


def recent_run_up(closes, spot) -> dict:
    """2-session and 5-session price change from daily closes + live spot.

    ``closes``: chronological daily closes, last entry = the most recent
    close (the snapshot's ``recent_closes`` shape). ``spot``: the live (or
    best-available) price. Per-leg None when uncomputable — never estimated.

    two_session_pct = spot / closes[-2] − 1   (the INTC read: $101.64 vs the
    $91.00 close two sessions back = +11.7%)
    five_session_pct = spot / closes[-5] − 1  (+24.1% on the INTC tape)
    """
    out = {"two_session_pct": None, "five_session_pct": None}
    s = _f(spot)
    if s is None or s <= 0 or not isinstance(closes, (list, tuple)):
        return out
    vals = []
    for c in closes:
        cv = _f(c)
        vals.append(cv if cv is not None and cv > 0 else None)
    if len(vals) >= 2 and vals[-2]:
        out["two_session_pct"] = s / vals[-2] - 1.0
    if len(vals) >= 5 and vals[-5]:
        out["five_session_pct"] = s / vals[-5] - 1.0
    return out


def check_chase(ticker, *, closes=None, spot=None, five_day_ret_pct=None,
                side: str = "put", config=None) -> dict:
    """The chase-guard verdict for ONE proposed new open.

    Returns ``{"blocked", "reason", "caution", "two_session_pct",
    "five_session_pct"}``. ``blocked`` is True only on a MEASURED run-up
    past a threshold; ``caution`` carries the "run-up unverifiable" note
    when the spot is measurable but no closes (nor a 5-day-return fallback)
    exist. ``five_day_ret_pct`` (percent, e.g. the scout's
    ``fivedayret_pct``) backfills the 5-session leg when no close series is
    available. ``side`` != put/csp (covered calls, buys) is always a no-op.
    """
    cfg = load_config(config)
    res = {"blocked": False, "reason": None, "caution": None,
           "two_session_pct": None, "five_session_pct": None}
    if not cfg["enabled"] or str(side or "").lower() not in _PUT_SIDES:
        return res
    ru = recent_run_up(closes, spot)
    two, five = ru["two_session_pct"], ru["five_session_pct"]
    if five is None:
        fd = _f(five_day_ret_pct)
        if fd is not None:
            five = fd / 100.0
    res["two_session_pct"], res["five_session_pct"] = two, five
    t = str(ticker or "?").upper()
    if two is None and five is None:
        if _f(spot) is not None:
            res["caution"] = (
                f"⚠ chase guard — run-up unverifiable for {t} (no recent "
                f"daily closes this cycle); verify the tape isn't vertical "
                f"before selling a new put (rule #44)")
        return res
    hit = ((two is not None and two > cfg["two_session_pct"])
           or (five is not None and five > cfg["five_session_pct"]))
    if not hit:
        return res
    if two is not None and five is not None:
        phrase = f"{t} {five * 100:+.1f}% in 5 sessions ({two * 100:+.1f}% in 2)"
    elif five is not None:
        phrase = f"{t} {five * 100:+.1f}% in 5 sessions"
    else:
        phrase = f"{t} {two * 100:+.1f}% in 2 sessions"
    res["blocked"] = True
    res["reason"] = (
        f"🚫 chase guard — {phrase}; a new put here sets the strike against "
        f"a vertical move (rule #44). Wait for the retest.")
    return res
