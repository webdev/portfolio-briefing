"""Entry/Exit recommender — consumes the deep technical snapshot
(``analysis.technical_indicators``) plus position/portfolio context and emits
a per-ticker call for the "📋 Per-Ticker Actions" section.

Calls (the full vocabulary — renderers/tests key off these exact strings):

  ENTRY_STRONG — high-conviction actionable: oversold/pullback RSI, at a
                 strong support, MACD histogram turning up, long-term
                 uptrend intact.
  ENTRY_WATCH  — setup forming but not there yet (or a qualifying entry that
                 is capacity-gated / falling-knife-gated).
  HOLD         — position is fine; no action. Renderers keep HOLD silent.
  TRIM         — long position stretched: overbought + pinned to the upper
                 Bollinger band (+ at resistance when SR confirms).
  EXIT_URGENT  — long-term structure broken: below 200-SMA + death cross +
                 falling MACD (long shares only).
  AVOID        — same broken structure on a name the user does NOT hold.

Discipline gates (all pinned by tests):
  - Capacity: qualifying ENTRY under a closed capacity gate (stress coverage
    < 0.50× etc.) downgrades to ENTRY_WATCH with a "⏸ Capacity gated" flag.
  - RSI hard rule #11: RSI > 70 never produces an ENTRY (structurally
    impossible — the entry band tops out at 55).
  - Standing directives (state/fable_advisor_memory.md): tickers with a
    documented hold directive never get TRIM — downgraded to HOLD with a flag.
  - Core holdings: EXIT_URGENT on a ``core_positions`` name overrides to TRIM
    ("core hold — override to trim, don't exit").
  - Parkev cross-connect: EXIT on a Parkev top pick (tier ≥ 4) is NOT
    suppressed — it ships with a "⚠️ Parkev disagrees (Top Pick)" flag.

Every reason cites a real number from the snapshot (hard rule #19). This
module is pure — no I/O except the optional memory-file *parser* helper,
which takes text, not a path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Call vocabulary
ENTRY_STRONG = "ENTRY_STRONG"
ENTRY_WATCH = "ENTRY_WATCH"
HOLD = "HOLD"
TRIM = "TRIM"
EXIT_URGENT = "EXIT_URGENT"
AVOID = "AVOID"

ALL_CALLS = {ENTRY_STRONG, ENTRY_WATCH, HOLD, TRIM, EXIT_URGENT, AVOID}

# Urgency order for the rendered section (HOLD is silent; AVOID renders last).
CALL_SORT_ORDER = [EXIT_URGENT, TRIM, ENTRY_STRONG, ENTRY_WATCH, AVOID]

# Tunable bands (overridable via briefing.yaml → technical_analysis.recommender)
DEFAULT_PARAMS = {
    "entry_rsi_max": 40.0,          # ENTRY_STRONG needs RSI ≤ this
    "entry_watch_rsi_max": 55.0,    # ENTRY_WATCH band upper bound
    "entry_watch_rsi_min": 30.0,
    "falling_knife_rsi": 25.0,      # below → never ENTRY_STRONG
    "support_near_pct": 3.0,        # "at support" = within this % above a level
    "support_forming_pct": 5.0,     # ENTRY_WATCH proximity band
    "trim_rsi_min": 70.0,
    "trim_bb_min": 85.0,            # BB position % near upper band
    "resistance_near_pct": 2.0,
    "exit_below_200_pct": -2.0,     # vs 200-SMA must be at/below this
    "parkev_top_pick_tier": 4,
}

_LT_UPTREND_SET = {"secular-uptrend", "uptrend", "recovery"}


@dataclass
class Recommendation:
    """One per-ticker call, with the numbers that drove it."""

    ticker: str
    call: str                       # one of ALL_CALLS
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    position_state: str = "none"    # long_shares | short_put | short_call | none

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "call": self.call,
            "reasons": list(self.reasons),
            "flags": list(self.flags),
            "position_state": self.position_state,
        }


def _g(tech, name, default=None):
    """Field access that works on both TechnicalSnapshot and its to_dict()."""
    if tech is None:
        return default
    if isinstance(tech, dict):
        v = tech.get(name, default)
    else:
        v = getattr(tech, name, default)
    return default if v is None else v


def load_params(config: dict | None) -> dict:
    """Merge briefing.yaml → technical_analysis.recommender over defaults."""
    merged = dict(DEFAULT_PARAMS)
    if isinstance(config, dict):
        user = ((config.get("technical_analysis") or {}).get("recommender") or {})
        for k, v in user.items():
            if k in merged and v is not None:
                merged[k] = float(v)
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Standing-directive parser (state/fable_advisor_memory.md)
# ─────────────────────────────────────────────────────────────────────────────

# Bold bullet headers in the memory's directive sections, e.g.
#   - **AMD_PUT_420_20261218 — hold for higher capture.** ...
#   - **TSLA (equity) — not ready to sell yet.** ...
_DIRECTIVE_HEADER = re.compile(r"^\s*-\s*\*\*([A-Z]{1,6})[\s_(]", re.MULTILINE)


def directive_hold_tickers(memory_text: str | None) -> set[str]:
    """Extract tickers with a standing hold/deferral directive from the
    fable-advisor memory markdown.

    Only the user-editable directive section (everything ABOVE the
    auto-maintained "## Recent reviews" divider) is scanned, so tickers merely
    mentioned in past LLM reviews don't become accidental TRIM suppressors.
    A bullet counts when its text signals a hold ("hold", "not ready to
    sell", "don't", "stop").

    NOTE (bug #25): this is the TICKER-level scan (TRIM/EXIT suppression,
    playbook close-side). CONTRACT-level directives with machine-readable
    release conditions ("until capture > 55% AND DTE < 90d") live in
    ``analysis.advisor_directives`` — complementary, not duplicates; see
    that module's docstring before consolidating.
    """
    if not memory_text:
        return set()
    head = memory_text.split("## Recent reviews", 1)[0]
    out: set[str] = set()
    for line in head.splitlines():
        m = _DIRECTIVE_HEADER.match(line)
        if not m:
            continue
        low = line.lower()
        if any(w in low for w in ("hold", "not ready to sell", "don't", "do not", "stop")):
            out.add(m.group(1).upper())
    return out


# ─────────────────────────────────────────────────────────────────────────────
# SR helpers
# ─────────────────────────────────────────────────────────────────────────────

def _nearest_support(sr: dict | None, spot: float) -> tuple[float, float, int] | None:
    """(price, %-below-spot, touches) of the nearest support, or None."""
    if not isinstance(sr, dict) or not spot:
        return None
    best = None
    for lv in (sr.get("supports") or []):
        try:
            price = float(lv.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= 0 or price >= spot:
            continue
        dist_pct = (spot - price) / spot * 100.0
        if best is None or dist_pct < best[1]:
            best = (price, dist_pct, int(lv.get("touches", 1)))
    return best


def _nearest_resistance(sr: dict | None, spot: float) -> tuple[float, float, int] | None:
    """(price, %-above-spot, touches) of the nearest resistance, or None."""
    if not isinstance(sr, dict) or not spot:
        return None
    best = None
    for lv in (sr.get("resistances") or []):
        try:
            price = float(lv.get("price"))
        except (TypeError, ValueError):
            continue
        if price <= spot:
            continue
        dist_pct = (price - spot) / spot * 100.0
        if best is None or dist_pct < best[1]:
            best = (price, dist_pct, int(lv.get("touches", 1)))
    return best


# ─────────────────────────────────────────────────────────────────────────────
# The recommender
# ─────────────────────────────────────────────────────────────────────────────

def recommend(
    ticker: str,
    tech_snapshot,
    position_state: str,
    config: dict | None,
    *,
    sr: dict | None = None,
    gate_open: bool = True,
    gate_reason: str | None = None,
    parkev: dict | None = None,
    directive_holds: set[str] | None = None,
) -> Recommendation | None:
    """Produce the per-ticker call. Returns None when ``tech_snapshot`` is
    missing (fail-closed — the renderer surfaces "chart data unavailable").

    Args:
        tech_snapshot: TechnicalSnapshot or its to_dict() shape.
        position_state: "long_shares" | "short_put" | "short_call" | "none".
        sr: the ticker's support_resistance dict (compute_sr().to_dict()).
        gate_open: capacity-gate state (False = stress coverage/cash gate closed).
        parkev: the ticker's recommendation dict ({recommendation, rating_tier, ...}).
        directive_holds: tickers with standing hold directives (memory file).
    """
    if tech_snapshot is None:
        return None

    ticker = (ticker or "").upper()
    params = load_params(config)
    held_long = position_state == "long_shares"
    # 2026-08-04 (PLTR): core = core_positions ∪ Tier A — the EXIT_URGENT →
    # TRIM override protects Tier A conviction names too.
    try:
        from analysis.position_tiers import core_union as _core_union
        core = _core_union(config or {})
    except Exception:
        core = {str(t).upper() for t in ((config or {}).get("core_positions") or [])}
    directive_holds = directive_holds or set()

    rsi = _g(tech_snapshot, "rsi_14")
    spot = float(_g(tech_snapshot, "spot", 0.0))
    bb_pos = float(_g(tech_snapshot, "bb_position_pct", 50.0))
    hist = float(_g(tech_snapshot, "macd_hist", 0.0))
    hist_5d = float(_g(tech_snapshot, "macd_hist_5d_ago", 0.0))
    vs_200 = float(_g(tech_snapshot, "vs_sma200_pct", 0.0))
    cross = str(_g(tech_snapshot, "cross", ""))
    lt = str(_g(tech_snapshot, "long_term_verdict", "sideways"))
    st = str(_g(tech_snapshot, "short_term_verdict", "neutral"))
    hist_rising = hist > hist_5d

    support = _nearest_support(sr, spot)
    resistance = _nearest_resistance(sr, spot)

    reasons: list[str] = []
    flags: list[str] = []

    # ── 1) Broken long-term structure → EXIT_URGENT / AVOID ────────────────
    broken = (
        vs_200 <= params["exit_below_200_pct"]
        and cross == "death"
        and hist < 0
        and not hist_rising
    )
    if broken:
        reasons.append(f"{vs_200:+.1f}% vs 200-SMA (below), Death cross")
        reasons.append(
            f"MACD hist {hist:+.2f} and falling (was {hist_5d:+.2f} 5d ago)"
        )
        reasons.append(f"long-term verdict: {lt}")
        if rsi is not None:
            reasons.append(f"RSI {rsi:.0f}")
        call = EXIT_URGENT if held_long else AVOID
        if call == EXIT_URGENT and ticker in core:
            call = TRIM
            flags.append("core hold — override to trim, don't exit")
        if call in (EXIT_URGENT, TRIM) and _parkev_top_pick(parkev, params):
            flags.append("⚠️ Parkev disagrees (Top Pick)")
        return Recommendation(ticker, call, reasons, flags, position_state)

    # ── 2) Stretched long position → TRIM ───────────────────────────────────
    if (
        held_long
        and rsi is not None
        and rsi >= params["trim_rsi_min"]
        and bb_pos >= params["trim_bb_min"]
    ):
        reasons.append(f"RSI {rsi:.0f} overbought (≥{params['trim_rsi_min']:.0f})")
        reasons.append(f"BB position {bb_pos:.0f}% (pinned to upper band)")
        if resistance and resistance[1] <= params["resistance_near_pct"]:
            reasons.append(
                f"at resistance ${resistance[0]:g} "
                f"({resistance[1]:.1f}% away, {resistance[2]} touches)"
            )
        if ticker in directive_holds:
            flags.append("standing directive in advisor memory — TRIM suppressed, holding")
            return Recommendation(ticker, HOLD, reasons, flags, position_state)
        return Recommendation(ticker, TRIM, reasons, flags, position_state)

    # ── 3) Entry setups (new exposure OR adds — RSI-gated per hard rule #11) ─
    lt_intact = lt in _LT_UPTREND_SET
    at_support = support is not None and support[1] <= params["support_near_pct"]
    near_support = support is not None and support[1] <= params["support_forming_pct"]

    if (
        rsi is not None
        and rsi <= params["entry_rsi_max"]
        and at_support
        and hist_rising
        and lt_intact
    ):
        reasons.append(f"RSI {rsi:.0f} (pullback zone, ≤{params['entry_rsi_max']:.0f})")
        reasons.append(
            f"at support ${support[0]:g} ({support[1]:.1f}% below spot, "
            f"{support[2]} touches)"
        )
        reasons.append(
            f"MACD hist turning up ({hist:+.2f} vs {hist_5d:+.2f} 5d ago)"
        )
        reasons.append(f"LT uptrend intact ({lt}, {vs_200:+.1f}% vs 200-SMA)")
        if rsi < params["falling_knife_rsi"]:
            flags.append(
                f"⚠ falling knife — RSI {rsi:.0f} < {params['falling_knife_rsi']:.0f}; "
                "wait for stabilization"
            )
            return Recommendation(ticker, ENTRY_WATCH, reasons, flags, position_state)
        if not gate_open:
            flags.append(
                "⏸ Capacity gated — " + (gate_reason or "portfolio gates closed")
            )
            return Recommendation(ticker, ENTRY_WATCH, reasons, flags, position_state)
        return Recommendation(ticker, ENTRY_STRONG, reasons, flags, position_state)

    if (
        rsi is not None
        and params["entry_watch_rsi_min"] <= rsi <= params["entry_watch_rsi_max"]
        and lt_intact
        and (hist_rising or near_support)
    ):
        reasons.append(f"RSI {rsi:.0f} (watch band)")
        if near_support:
            reasons.append(
                f"support ${support[0]:g} within {support[1]:.1f}% "
                f"({support[2]} touches)"
            )
        if hist_rising:
            reasons.append(f"MACD hist rising ({hist:+.2f} vs {hist_5d:+.2f} 5d ago)")
        reasons.append(f"LT {lt}; setup forming, not confirmed")
        return Recommendation(ticker, ENTRY_WATCH, reasons, flags, position_state)

    # ── 4) Default ───────────────────────────────────────────────────────────
    if rsi is not None:
        reasons.append(f"RSI {rsi:.0f}")
    reasons.append(f"ST {st} · LT {lt} · {vs_200:+.1f}% vs 200-SMA")
    return Recommendation(ticker, HOLD, reasons, flags, position_state)


def _parkev_top_pick(parkev: dict | None, params: dict) -> bool:
    if not isinstance(parkev, dict):
        return False
    try:
        return int(parkev.get("rating_tier") or 0) >= int(params["parkev_top_pick_tier"])
    except (TypeError, ValueError):
        return False


def sort_recommendations(recs: list[Recommendation]) -> list[Recommendation]:
    """Urgency order: EXIT_URGENT → TRIM → ENTRY_STRONG → ENTRY_WATCH → AVOID.
    HOLD is excluded (renderers keep it silent — it lives in the Technical
    Read section instead)."""
    order = {c: i for i, c in enumerate(CALL_SORT_ORDER)}
    return sorted(
        (r for r in recs if r.call != HOLD),
        key=lambda r: (order.get(r.call, 99), r.ticker),
    )
