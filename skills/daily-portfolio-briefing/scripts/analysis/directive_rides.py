"""Directive rides — track a position directive's OWN exit conditions
(2026-08-26 MELI split brain).

Observed on the real 2026-08-26 briefing: George's MELI override (filed
2026-08-25 — momentum ride, exits GTC 75%/85%, red-day or RSI-70 stall,
over-cap accepted) was honored by Fable ("tracking the exits, not
re-litigating the close") while the ACTION LIST still rendered
"2. **CLOSE** MELI_PUT_1460_20270617 — +34% ($+3,435) … ⏳ IGNORED 3 DAYS"
and the Money Plan still banked it ("Bank today: 1 close(s) → $+3,435
realized (MELI $1460P)"). Root cause: the override lived only in Fable
memory — the pipeline's canonical directive loader
(steps/load_directives.py ← state/directives/index.yaml) never received it.

This module is the single source of truth for the GENERIC pattern (rule
#51 appendix): any pipeline directive carrying a machine-readable
``exit_conditions`` block (gtc_capture / gtc_limit_price /
gtc_stretch_capture / red_day_pct / rsi_stall / max_capture /
earnings_min_capture — format documented in the index.yaml header) gets
its recommendation-class CLOSE suppressed AND its exits TRACKED with
measured values every cycle:

    🏇 RIDING (directive) — exits armed: GTC@75% $24.60 · stall on red
    day ≤-2% or RSI ≥ 70 · [today: MELI -2.3% — STALL TRIGGER FIRED,
    exit per directive]

Rules #19/#24: every number on the line is measured this cycle — a
condition with no measurement is reported unmeasured, never fabricated —
and when a stall fires the render states it loudly (the exit line IS the
action item). While riding, the close is NEVER banked in the Money Plan
(``_directive_ride`` demotion flag, analysis/net_option_cash.py).
"""

from __future__ import annotations

_EXIT_KEYS = ("gtc_capture", "gtc_limit_price", "gtc_stretch_capture",
              "red_day_pct", "rsi_stall", "max_capture",
              "earnings_min_capture")

_DEFAULT_EARNINGS_WINDOW_DAYS = 7


def _f(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _frac(v):
    """Normalize a capture threshold to a 0..1 fraction (75 → 0.75)."""
    f = _f(v)
    if f is None:
        return None
    return f / 100.0 if abs(f) > 1.5 else f


def exit_conditions(directive: dict | None) -> dict | None:
    """The directive's normalized machine-readable exit conditions, or
    None when the directive carries none (→ no ride tracking; the legacy
    DEFER/OVERRIDE short-circuit stands on its own)."""
    if not isinstance(directive, dict):
        return None
    raw = directive.get("exit_conditions")
    if not isinstance(raw, dict):
        return None
    out = {
        "gtc_capture": _frac(raw.get("gtc_capture")),
        "gtc_limit_price": _f(raw.get("gtc_limit_price")),
        "gtc_stretch_capture": _frac(raw.get("gtc_stretch_capture")),
        "red_day_pct": _f(raw.get("red_day_pct")),
        "rsi_stall": _f(raw.get("rsi_stall")),
        "max_capture": _frac(raw.get("max_capture")),
        "earnings_min_capture": _frac(raw.get("earnings_min_capture")),
    }
    if not any(v is not None for v in out.values()):
        return None
    return out


def measured_day_move_pct(ticker: str, snapshot_data: dict | None):
    """The underlying's measured day move (%): live/broker price vs the
    technicals reference close — the same rule-#47 drift resolution the
    vintage guard uses. None when either side is unmeasured (rule #19 —
    never a fabricated day color)."""
    tk = str(ticker or "").upper()
    sd = snapshot_data or {}
    tech = (sd.get("technicals") or {}).get(tk) or {}
    ref = _f(tech.get("spot") if isinstance(tech, dict) else None)
    q = (sd.get("quotes") or {}).get(tk) or {}
    live = None
    if isinstance(q, dict):
        live = _f(q.get("last")) or _f(q.get("lastTrade")) \
            or _f(q.get("price"))
        # A quote payload may carry its own measured day change.
        if live is None or (ref is None or ref <= 0):
            dcp = _f(q.get("dayChangePct"))
            if dcp is not None:
                # yfinance snapshot stores a fraction (-0.0885 = -8.85%)
                return round(dcp * 100.0, 1) if abs(dcp) <= 1.0 \
                    else round(dcp, 1)
    if live is None:
        try:
            from analysis.vintage_guard import broker_price_map
            live = broker_price_map(sd.get("positions") or []).get(tk)
        except Exception:
            live = None
    if live is None or ref is None or ref <= 0:
        return None
    return round((float(live) - ref) / ref * 100.0, 1)


def _pct_label(frac) -> str:
    return f"{frac * 100:g}%"


def exits_armed_line(cond: dict) -> str:
    """The 'exits armed' segment composed ONLY from conditions the
    directive actually carries — e.g. "GTC@75% $24.60 · stall on red day
    ≤-2% or RSI ≥ 70 · never past 85% capture"."""
    segs: list[str] = []
    if cond.get("gtc_capture") is not None:
        s = f"GTC@{_pct_label(cond['gtc_capture'])}"
        if cond.get("gtc_limit_price") is not None:
            s += f" ${cond['gtc_limit_price']:,.2f}"
        if cond.get("gtc_stretch_capture") is not None:
            s += f" ({_pct_label(cond['gtc_stretch_capture'])} stretch)"
        segs.append(s)
    stall_parts: list[str] = []
    if cond.get("red_day_pct") is not None:
        stall_parts.append(f"red day ≤{cond['red_day_pct']:g}%")
    if cond.get("rsi_stall") is not None:
        stall_parts.append(f"RSI ≥ {cond['rsi_stall']:g}")
    if stall_parts:
        segs.append("stall on " + " or ".join(stall_parts))
    if cond.get("max_capture") is not None:
        segs.append(f"never past {_pct_label(cond['max_capture'])} capture")
    if cond.get("earnings_min_capture") is not None:
        segs.append(f"not through earnings at "
                    f"≥{_pct_label(cond['earnings_min_capture'])}")
    return " · ".join(segs)


def evaluate_ride(directive: dict | None, *, ticker: str,
                  capture_pct=None, day_move_pct=None, rsi=None,
                  days_to_earnings=None, config: dict | None = None) -> dict | None:
    """Evaluate a directive's exit conditions against this cycle's
    MEASURED values.

    Returns None when the directive carries no machine-readable exits.
    Otherwise a dict:
      exits_line — "GTC@75% $24.60 · stall on red day ≤-2% or RSI ≥ 70 …"
      fired      — list of measured trigger strings (empty → still riding)
      stalled    — bool (any exit condition fired)
      today_line — "[today: MELI -2.3% — STALL TRIGGER FIRED, exit per
                    directive]" when fired; otherwise the measured
                    no-trigger read; "exits unmeasured this cycle" when
                    nothing was measurable (rule #19 — never fabricated).

    Fail direction: an unmeasured input skips ONLY that condition — a
    missing quote never fires (or suppresses) a stall.
    """
    cond = exit_conditions(directive)
    if cond is None:
        return None
    tk = str(ticker or "").upper()
    cap = _frac(capture_pct)
    day = _f(day_move_pct)
    rsi_v = _f(rsi)
    fired: list[str] = []

    if cond.get("red_day_pct") is not None and day is not None \
            and day <= cond["red_day_pct"]:
        fired.append(f"{tk} {day:+.1f}% "
                     f"(red-day stall ≤ {cond['red_day_pct']:g}%)")
    if cond.get("rsi_stall") is not None and rsi_v is not None \
            and rsi_v >= cond["rsi_stall"]:
        fired.append(f"{tk} RSI {rsi_v:.0f} ≥ {cond['rsi_stall']:g} stall")
    if cond.get("max_capture") is not None and cap is not None \
            and cap >= cond["max_capture"]:
        fired.append(f"capture {cap * 100:.0f}% ≥ "
                     f"{_pct_label(cond['max_capture'])} hard cap")
    if cond.get("earnings_min_capture") is not None and cap is not None \
            and days_to_earnings is not None:
        try:
            window = int(((config or {}).get("directives") or {})
                         .get("earnings_release_days",
                              _DEFAULT_EARNINGS_WINDOW_DAYS))
        except (TypeError, ValueError, AttributeError):
            window = _DEFAULT_EARNINGS_WINDOW_DAYS
        if 0 <= int(days_to_earnings) <= window \
                and cap >= cond["earnings_min_capture"]:
            fired.append(
                f"earnings in {int(days_to_earnings)}d at "
                f"{cap * 100:.0f}% captured (≥ "
                f"{_pct_label(cond['earnings_min_capture'])} — don't hold "
                f"through the print)")

    measured: list[str] = []
    if day is not None:
        measured.append(f"{tk} {day:+.1f}%")
    if rsi_v is not None:
        measured.append(f"RSI {rsi_v:.0f}")
    if cap is not None:
        measured.append(f"{cap * 100:.0f}% captured")

    if fired:
        today_line = (f"[today: {fired[0]} — STALL TRIGGER FIRED, "
                      f"exit per directive]")
    elif measured:
        today_line = (f"[today: {' · '.join(measured)} — no exit trigger "
                      f"fired]")
    else:
        today_line = "[today: exits unmeasured this cycle — verify manually]"

    return {
        "exits_line": exits_armed_line(cond),
        "fired": fired,
        "stalled": bool(fired),
        "today_line": today_line,
        "conditions": cond,
    }
