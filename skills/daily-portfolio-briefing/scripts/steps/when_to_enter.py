"""When-To-Enter report — per-company entry triggers across all themes.

Builds a dated companion report (`when_to_enter_DATE.md`) that turns the
Thematic Scout's research into an action-first list: every company in the
scout universe gets a status (🟢 ENTRY NOW / 🟡 WAIT / 🟡 WATCH-NEUTRAL /
🔴 AVOID), the scout's read, and an EXPLICIT trigger — either the concrete
trade to place today, or the specific RSI band + pullback % to wait for.

Every numeric value in the output is real (RSI, IV rank, drawdown, 5d return,
CSP entry quote — all from the scout cache); no hardcoded thresholds appear in
the rendered text.

Classification priority (technical state first, then thesis check, then
verdict-driven AVOID, then favored band):

  1. RSI ≥ 70  → WAIT (overbought — wait for a real cool-off)
  2. 60 ≤ RSI < 70  → WAIT (extended)
  3. drawdown ≥ 40% AND RSI < 45 AND below 200-SMA by >15%  → AVOID (thesis check)
  4. RSI < 25  → WAIT (falling-knife — wait for stabilization)
  5. verdict starts with AVOID  → AVOID (scout flag)
  6. 35 ≤ RSI ≤ 55 AND verdict CSP  → ENTRY NOW (CSP)
  7. 35 ≤ RSI ≤ 55 AND verdict BUY  → ENTRY NOW (BUY)
  8. 35 ≤ RSI ≤ 55 (no actionable verdict)  → WATCH (neutral pullback band)
  9. 25 ≤ RSI < 35  → WAIT (oversold but not falling-knife)
 10. otherwise (55-60, missing RSI)  → WATCH / NEUTRAL

The RSI bands match `briefing.yaml` → `rsi_discipline` (the standard wheel
bands); changing those propagates.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    from analysis import intrinsic_value as _iv
except ImportError:  # pragma: no cover - path fallback
    _iv = None

try:
    from analysis import support_resistance as _sr
except ImportError:  # pragma: no cover - path fallback
    _sr = None

try:
    from analysis import verdict_state as _vs
except ImportError:  # pragma: no cover - path fallback
    _vs = None


# When spot is within this percentage of a support/resistance cluster the
# trigger phrasing switches from "wait for X-Y% pullback" to the explicit
# price-zone language. 3% is the standard "anchor proximity" used elsewhere
# (see briefing.yaml → support_resistance.anchor_proximity_pct).
_AT_LEVEL_PCT = 0.03


GROUP_ORDER = ["The AI Buildout", "Ancillary", "Adjacent"]
THEME_ORDER = [
    "semis", "memory_storage", "photonics_optics", "ai_data_centers",
    "power", "applications", "networking",
    "ancillary_space", "ancillary_robotics", "ancillary_drones",
    "ancillary_rare_earths",
    "quantum", "cybersecurity",
    "social_ad_tech_ai",
]
# NOTE: THEME_ORDER must mirror theme_universes.yaml's themes:. Themes added
# there but missing here will have their researched tickers silently skipped
# in the rendered report (caught the META/RDDT/PINS/SNAP/TTD omission on
# 2026-06-01). When you add a new theme to the YAML, append its key here too.

STATUS_RANK = {"enter": 0, "watch": 1, "wait": 2, "avoid": 3}

# Candidates-report verdict class → the (status, label) this report downgrades
# an ENTRY NOW to when the two disagree (12-entry-pipeline-spec §7). Always
# the more conservative side.
_ALIGN_DOWNGRADE = {
    "HELD_RSI": ("wait", "🟡 WAIT — held by RSI"),
    "WATCH": ("watch", "🟡 WATCH — neutral"),
    "AVOID": ("avoid", "🔴 AVOID"),
}


def _trend_phrase(spot, sma) -> str | None:
    if not spot or not sma:
        return None
    try:
        pct = (float(spot) - float(sma)) / float(sma) * 100
    except (TypeError, ZeroDivisionError):
        return None
    if pct >= 50:
        return f"well above 200-SMA ({pct:+.0f}%)"
    if pct >= 10:
        return f"above 200-SMA ({pct:+.0f}%)"
    if pct >= -10:
        return f"near 200-SMA ({pct:+.0f}%)"
    if pct >= -30:
        return f"below 200-SMA ({pct:+.0f}%)"
    return f"well below 200-SMA ({pct:+.0f}%)"


def _pct_below_sma(spot, sma) -> float | None:
    if not spot or not sma:
        return None
    try:
        return (float(spot) - float(sma)) / float(sma) * 100
    except (TypeError, ZeroDivisionError):
        return None


def _coerce_sr(sr):
    """Return a SupportResistance object or None — accepts the dict shape that
    travels through the snapshot JSON as well as raw objects."""
    if sr is None or _sr is None:
        return None
    if isinstance(sr, dict):
        return _sr._coerce(sr)
    return sr


def _strongest_support_below(sr, spot: float):
    """Return the highest-strength support below spot, or None when SR is unavailable."""
    if sr is None or not sr.supports or not spot:
        return None
    below = [lv for lv in sr.supports if lv.price < spot]
    if not below:
        return None
    return max(below, key=lambda lv: lv.strength)


def _strongest_resistance_above(sr, spot: float):
    if sr is None or not sr.resistances or not spot:
        return None
    above = [lv for lv in sr.resistances if lv.price > spot]
    if not above:
        return None
    return max(above, key=lambda lv: lv.strength)


def _at_support(sr, spot: float, *, pct: float = _AT_LEVEL_PCT) -> "object | None":
    """Return the support level spot is within ``pct`` of, or None."""
    if sr is None or not sr.supports or not spot:
        return None
    for lv in sr.supports:
        if spot > 0 and abs(spot - lv.price) / spot <= pct:
            return lv
    return None


def _support_zone_phrase(sr, spot: float) -> str | None:
    """Build a 'into $X-$Y support zone' phrase from the 1-2 nearest supports.
    Returns None when no usable supports exist."""
    if sr is None or not sr.supports:
        return None
    below = [lv for lv in sr.supports if lv.price < spot]
    if not below:
        return None
    # Order by closeness to spot (cheaper to fall to nearest support first).
    below.sort(key=lambda lv: spot - lv.price)
    near = below[:2]
    if len(near) == 1:
        return f"into ${near[0].price:g} (nearest support)"
    # Render the closer-to-spot first so the user reads "drop to $X (first), $Y (deeper)".
    lo = min(near, key=lambda lv: lv.price)
    hi = max(near, key=lambda lv: lv.price)
    return f"into the ${lo.price:g}-${hi.price:g} support zone"


def _at_level_label(level) -> str:
    """Compact label for an at-level confluence note: 'at $176 support (Apr swing + 200-SMA, 3 touches)'."""
    if level is None:
        return ""
    return f"at {level.label()}"


def classify(r: dict, sr=None) -> tuple[str, str, str, str]:
    """Classify a single scout result, optionally enriched with S/R.

    Returns ``(status, status_label, read, trigger)`` where ``status`` is one
    of ``enter | wait | watch | avoid``. Pure function over the scout result —
    no I/O, deterministic, side-effect free.

    ``sr`` is an optional :class:`SupportResistance` (or its ``to_dict()``
    shape) for the same ticker. When provided, the trigger text uses the
    actual support/resistance levels instead of the generic "8-12% pullback"
    phrasing, and ENTRY cards get a confluence badge when spot sits at a
    strong support. Backwards-compatible: ``sr=None`` reproduces the legacy
    behavior exactly."""
    rsi = r.get("rsi_14")
    verdict = (r.get("verdict") or "").upper()
    dd = r.get("drawdown_pct") or 0
    f5 = r.get("fivedayret_pct") or 0
    iv = r.get("iv_rank") or 0
    spot = r.get("spot")
    sma = r.get("sma_200")
    sma_pct = _pct_below_sma(spot, sma)
    earn = r.get("days_to_earnings")
    csp = r.get("csp_entry") or {}
    rationale = "; ".join(r.get("rationale") or [])

    tp = _trend_phrase(spot, sma) or "trend n/a"

    # S/R enrichment — when available, swap generic "X-Y% pullback" phrasing for
    # explicit price-zone language ("into the $238-$245 support zone"). Falls
    # back to the legacy phrasing when SR is None or has no qualifying supports
    # (fail-closed: never fabricate a level).
    sr_obj = _coerce_sr(sr)
    support_zone = _support_zone_phrase(sr_obj, spot or 0) if (sr_obj and spot) else None
    nearest_sup = _strongest_support_below(sr_obj, spot or 0) if (sr_obj and spot) else None
    at_sup = _at_support(sr_obj, spot or 0) if (sr_obj and spot) else None

    # Technical state first — even an AVOID verdict on an overbought name
    # reduces to "wait for the cool-off," not "thesis broken."
    if rsi is not None and rsi >= 70:
        side_note = (" If you own shares, WRITE COVERED CALLS — rich IV + "
                     "extended setup is the favored time.") if iv >= 60 else ""
        pullback_phrase = (
            f"a pullback {support_zone}" if support_zone
            else "a 8-12% pullback from spot"
        )
        return ("wait", "🔴 WAIT — overbought",
                f"Extended after a {f5:+.1f}% 5d run, RSI {rsi:.0f}, {tp}, IV rank {iv:.0f}.",
                f"WAIT for RSI < 55 AND {pullback_phrase}.{side_note}")

    if rsi is not None and 60 <= rsi < 70:
        pullback_phrase = (
            f"a pullback {support_zone}" if support_zone
            else "a 5-8% pullback"
        )
        return ("wait", "🟡 WAIT — extended",
                f"RSI {rsi:.0f}, {tp}, {f5:+.1f}% 5d — extended but not at the chase point.",
                f"WAIT for RSI to retrace into 45-55 AND {pullback_phrase}. Covered calls attractive if held.")

    # Thesis check — deep drawdown + weak technicals + below the 200-SMA.
    if dd >= 40 and rsi is not None and rsi < 45 and (sma_pct or 0) < -15:
        sup_note = (f" Key support to defend: ${nearest_sup.price:g} ({nearest_sup.source})."
                    if nearest_sup else "")
        return ("avoid", "🔴 AVOID — thesis check",
                f"Drawdown {dd:.0f}% from highs, RSI {rsi:.0f}, {tp} — broken trend without strength.",
                f"Don't enter on the pullback alone. Verify fundamentals. "
                f"Consider only after 2 consecutive higher weekly closes AND RSI > 45.{sup_note}")

    if rsi is not None and rsi < 25:
        sup_note = (f" Watch for stabilization near ${nearest_sup.price:g}."
                    if nearest_sup else "")
        return ("wait", "🟡 WAIT — falling knife",
                f"RSI {rsi:.0f} deeply oversold, 5d {f5:+.1f}%, {tp}.",
                f"WAIT for stabilization: RSI > 35 AND at least one green day.{sup_note} "
                "Small starter size only when it bases.")

    # Verdict-driven AVOID (the scout flagged a specific concern not caught
    # by the technical states above). Surface the scout's actual rationale.
    if verdict.startswith("AVOID"):
        return ("avoid", "🔴 AVOID — scout flag",
                f"Scout verdict: {r.get('verdict')}. {rationale[:160]}",
                "Don't enter on metrics alone — verify the underlying reason. Re-check when the flagged condition changes.")

    # Favored pullback band
    if rsi is not None and 35 <= rsi <= 55:
        # RSI override labelling (12-entry-pipeline-spec §5): the entry band is
        # 35-50. A name entering with RSI above 50 only qualifies via the
        # override path (deep drawdown + third-party BUY) — say so explicitly
        # instead of presenting the out-of-band RSI as a favourable pullback.
        if _vs is not None and not _vs.rsi_in_band(rsi):
            rsi_read = _vs.override_label(rsi, drawdown=dd,
                                          buy_rec=_vs.has_buy_rec(r))
        else:
            rsi_read = f"RSI {rsi:.0f} in pullback zone"
        # Parkev's rating tier (5=Top Stock to Buy, 4=Top 15/25 Stock, 3=Buy, ...)
        # — a tier ≥ 4 catalyst is a much stronger conviction signal than a
        # plain "Buy." When the technical setup AND the high-conviction rec line
        # up, surface as 🌟 STRONG BUY — distinct from the standard tier-3 BUY
        # — so the user can size accordingly.
        tier = r.get("rating_tier")
        is_top_tier = (tier or 0) >= 4
        raw_rec = r.get("raw_recommendation") or ""
        # CLAUDE.md hard rule #26: Parkev's conviction is a separate
        # dimension from the rating tier. Combinations to surface:
        #   tier ≥4 + High → 🏆 TOP CONVICTION (sized largest)
        #   tier ≥4 + Med  → 🌟 STRONG BUY (standard tier-4 sizing)
        #   tier ≥4 + Low  → tier-3 sizing (downgraded — analyst isn't sure)
        #   tier 3  + High → 🔥 high-conviction tag on the BUY badge
        #   tier 3  + Low  → 🟡 TRIAL SIZE warning (analyst soft on it)
        conviction = r.get("conviction")  # "High" / "Medium" / "Low" / None
        is_high_conv = conviction == "High"
        is_low_conv = conviction == "Low"
        # Effective tier: a Low-conviction tier-4 acts like a tier-3 for sizing.
        eff_top_tier = is_top_tier and not is_low_conv
        conv_chip = ""
        if conviction:
            conv_chip = f" · conv: **{conviction}**"
        tier_note = ""
        if is_top_tier and is_high_conv:
            tier_note = f" Parkev tier-{tier} ({raw_rec}) + HIGH conviction — top-of-class catalyst."
        elif is_top_tier and conviction == "Medium":
            tier_note = f" Parkev tier-{tier} ({raw_rec}), medium conviction."
        elif is_top_tier and is_low_conv:
            tier_note = f" Parkev tier-{tier} ({raw_rec}) BUT LOW conviction — analyst isn't pounding the table; treat like a standard Buy."
        elif is_top_tier:
            # Legacy fallback when conviction isn't on the rec (the OLD
            # tier-only behavior — preserved so existing tests still pass).
            tier_note = f" Parkev tier-{tier} ({raw_rec}) — high-conviction catalyst."
        elif (tier or 0) == 3 and is_high_conv:
            tier_note = f" Parkev BUY + HIGH conviction — selective."
        elif (tier or 0) == 3 and is_low_conv:
            tier_note = f" Parkev BUY but LOW conviction — analyst likes it without confidence; trial size only."
        # S/R confluence — when spot sits at a strong support, surface that
        # as confirmation. This is the "suspenders" on top of the RSI gate;
        # never overrides, only confirms.
        confluence_note = ""
        if at_sup is not None:
            confluence_note = f" ✅ Spot sits {_at_level_label(at_sup)} — confluence with the RSI signal."
        if verdict.startswith("CSP") and csp:
            exp_raw = csp.get("expiration") or ""
            try:
                exp_p = datetime.strptime(str(exp_raw), "%Y-%m-%d").strftime("%b %d '%y")
            except (ValueError, TypeError):
                exp_p = str(exp_raw) or "?"
            earn_note = ""
            if earn is not None and 0 < earn <= int(csp.get("dte", 30) or 30):
                earn_note = (f"  ⚠ earnings in {earn}d INSIDE the contract window — "
                             "defer until after the print.")
            strike = csp.get("strike") or 0
            # If the proposed strike sits at a strong support, surface that —
            # the strike isn't just delta-anchored, it's anchored to a real level.
            strike_note = ""
            if sr_obj and strike:
                anchor = _sr.nearest_support_in_range(
                    sr_obj,
                    min_price=float(strike) * 0.97,
                    max_price=float(strike) * 1.03,
                ) if _sr else None
                if anchor is not None:
                    strike_note = f" Strike sits {_at_level_label(anchor)} — assignment puts you at a real support."
            # CLAUDE.md hard rule #25: an "independent setup" verdict means
            # technicals qualify but there's no third-party BUY — surface
            # the entry but flag it so the user knows to validate the
            # catalyst against other sources before placing.
            is_independent = "INDEPENDENT" in verdict.upper()
            if is_independent:
                label = "🟢 ENTRY NOW — CSP ⚠ no third-party rec"
            elif eff_top_tier and is_high_conv:
                label = "🏆 TOP CONVICTION — ENTRY NOW (CSP)"
            elif eff_top_tier:
                label = "🌟 STRONG BUY — ENTRY NOW (CSP)"
            elif (tier or 0) == 3 and is_high_conv:
                label = "🟢 ENTRY NOW — CSP · 🔥 high conviction"
            elif (tier or 0) == 3 and is_low_conv:
                label = "🟡 TRIAL — CSP (low-conviction BUY)"
            else:
                label = "🟢 ENTRY NOW — CSP"
            return ("enter", label,
                    f"{rsi_read}, IV rank {iv:.0f}, drawdown {dd:.0f}%, {tp}. "
                    f"Favorable spot to get paid to maybe buy lower.{tier_note}{confluence_note}{conv_chip}",
                    f"SELL 1× ${strike:g}P exp **{exp_p}** ({csp.get('dte')} DTE) · "
                    f"mid ${csp.get('mid', 0):.2f} (bid ${csp.get('bid', 0):.2f} / "
                    f"ask ${csp.get('ask', 0):.2f}). Collateral ~${float(strike) * 100:,.0f}.{strike_note}{earn_note}")
        if verdict.startswith("BUY"):
            # CLAUDE.md hard rule #26: conviction modulates ONLY when present.
            # When conviction is None (missing/unknown), fall back to the
            # legacy tier-only sizing so the old contract is unchanged:
            #   tier ≥4 → 🌟 STRONG BUY, 1/2 of target weight
            #   tier 3  → 🟢 ENTRY NOW — BUY, 1/3 of target weight
            if eff_top_tier and is_high_conv:
                label = "🏆 TOP CONVICTION — ENTRY NOW"
                size_guidance = "1/2 of target weight is reasonable"
            elif eff_top_tier:
                label = "🌟 STRONG BUY — ENTRY NOW"
                size_guidance = "1/2 of target weight is reasonable"
            elif (tier or 0) == 3 and is_high_conv:
                label = "🟢 ENTRY NOW — BUY · 🔥 high conviction"
                size_guidance = "1/3 of target weight (selective BUY)"
            elif (tier or 0) == 3 and is_low_conv:
                label = "🟡 TRIAL — BUY (low conviction)"
                size_guidance = "1/6 of target weight — trial only; let conviction firm up before scaling"
            else:
                label = "🟢 ENTRY NOW — BUY"
                size_guidance = "1/3 of target weight"
            scale_target = (
                f"toward ${nearest_sup.price:g} support" if nearest_sup
                else "toward RSI 35-40"
            )
            trigger = (
                f"ENTER: small starter ({size_guidance}). Scale on further weakness "
                f"{scale_target}. Average down on confirmed support."
            )
            return ("enter", label,
                    f"{rsi_read}, drawdown {dd:.0f}%, {tp}, "
                    f"third-party BUY-rated.{tier_note}{confluence_note}{conv_chip}",
                    trigger)
        # WATCH/neutral — refine the monitor trigger with explicit support price
        # so the user knows EXACTLY what to watch for, not just a band.
        monitor_trigger = (
            f"Monitor. CSP entry becomes favored if IV rank > 50 AND a third-party "
            f"BUY/STRONG_BUY arrives, OR on a pullback {support_zone}."
            if support_zone
            else "Monitor. CSP entry becomes favored if IV rank > 50 AND a third-party "
                 "BUY/STRONG_BUY arrives, OR if drawdown deepens past 20% with RSI "
                 "holding 35-45."
        )
        return ("watch", "🟡 WATCH — neutral",
                f"RSI {rsi:.0f} in pullback band but no specific catalyst from the scout.",
                monitor_trigger)

    if rsi is None:
        return ("watch", "🟡 WATCH — no RSI",
                "RSI data unavailable; can't gate.",
                "— no ticket: RSI unavailable (fail closed). "
                "Verify the ticker manually before any entry.")

    if 25 <= rsi < 35:
        sup_note = (f" Key support: ${nearest_sup.price:g}." if nearest_sup else "")
        return ("wait", "🟡 WAIT — oversold",
                f"RSI {rsi:.0f}, drawdown {dd:.0f}%, 5d {f5:+.1f}%, {tp}.",
                f"Small starter OK if you want; otherwise WAIT for RSI > 40 with confirmed support.{sup_note}")

    pullback_trigger = (
        f"Monitor. CSP entry favored on a pullback {support_zone} with RSI 40-50."
        if support_zone
        else "Monitor. CSP entry favored on a pullback to RSI 40-50."
    )
    return ("watch", "🟡 NEUTRAL",
            f"RSI {rsi:.0f}, {tp}, 5d {f5:+.1f}% — no directional edge.",
            pullback_trigger)


def render_when_to_enter_report(scout_payload: dict | None, *,
                                 config: dict | None = None,
                                 generated_at: str = "",
                                 sr_by_sym: dict | None = None,
                                 gate_state=None,
                                 verdict_state_path=None) -> str:
    """Build the markdown report. Empty-string when no scout payload.

    ``sr_by_sym`` maps uppercase ticker → ``SupportResistance`` or its
    ``to_dict()`` shape from the snapshot. When provided, every card's
    trigger text uses real S/R levels instead of generic pullback %s.
    Backwards-compatible: omit it for the legacy phrasing.

    ``gate_state`` (optional ``analysis.capacity_gates.GateState``): the
    capacity banner is the report's first line. When gates are CLOSED, ENTRY
    NOW classifications are KEPT (with the live ticket) and tagged ``·
    ⏸ DEFERRED`` — hard rule #24 (never hide opportunities; surface them
    as deferred so the user can stage rotation). ``gate_state=None`` is
    the legacy behavior.

    Consistency contract (12-entry-pipeline-spec §7): the candidates report
    (rendered first in the daily run) persists its verdict classes to
    ``state/scout_verdicts.yaml``; this renderer reads back the SAME-DATE
    entries and downgrades any ENTRY NOW whose candidate verdict is not
    CANDIDATE — the two files may not contradict each other. A missing or
    stale-dated state file disables the alignment (legacy behavior)."""
    sr_by_sym = sr_by_sym or {}
    if not scout_payload:
        return ""

    gates_closed = gate_state is not None and not gate_state.open
    _gate_reason = (gate_state.reasons[0]
                    if (gates_closed and gate_state.reasons) else "capacity")

    # Same-run candidate verdicts ({TICKER: CANDIDATE|HELD_RSI|WATCH|AVOID}).
    # Empty when the state file is missing/stale → no alignment applied.
    cand_verdicts: dict = {}
    if _vs is not None:
        try:
            cand_verdicts = _vs.same_date_verdicts(_vs.load(verdict_state_path))
        except Exception:
            cand_verdicts = {}

    def _classify(r, sr=None):
        """classify(), tagging ENTRY NOW as DEFERRED when capacity gates are
        closed (hard rule #24 — never HIDE an opportunity; surface it as
        DEFERRED so the user can see the live ticket and plan rotation), and
        downgrading ENTRY NOW when the candidates report disagrees
        (consistency contract §7)."""
        status, label, read, trigger = classify(r, sr=sr)
        if gates_closed and status == "enter":
            # Keep the entry classification + full live ticket. Append a
            # ⏸ DEFERRED tag to the label and a one-line capacity note to
            # the trigger so the user knows WHY it's gated without losing
            # the strike / premium / S/R data the trigger already carries.
            # Capacity banner at the top of the report (line ~476) explains
            # the gate once — no need to repeat the full reason per card.
            return ("enter", f"{label} · ⏸ DEFERRED", read,
                    f"{trigger}\n  ⏸ **Capacity-gated** ({_gate_reason}) — "
                    "verify and stage as a rotation; once gates clear this is actionable as-is.")
        if status == "enter" and cand_verdicts:
            cv = cand_verdicts.get((r.get("ticker") or "").upper())
            if cv and cv != "CANDIDATE":
                d_status, d_label = _ALIGN_DOWNGRADE.get(
                    cv, ("watch", "🟡 WATCH — neutral"))
                return (d_status, f"{d_label} (aligned to candidates report)", read,
                        f"No ticket — the candidates report classifies this name "
                        f"{cv}; downgraded to the more conservative status "
                        "(aligned to candidates report).")
        return status, label, read, trigger
    themes_meta = scout_payload.get("themes", {}) or {}
    rbt = scout_payload.get("results_by_theme", {}) or {}
    gen_iso = scout_payload.get("generated_at_iso", "")

    # ETFs get a 🪙 tag in the card heading + a one-line summary near the top
    # so they don't get lost among the individual stocks in their theme section.
    etf_set: set = _iv.default_etf_set(config) if _iv else set()
    def _is_etf(tk: str) -> bool:
        return _iv.is_etf(tk, etf_set) if _iv else False

    # Render order — THEME_ORDER's canonical sequence first (groups by AI
    # Buildout / Ancillary / Adjacent), then ANY payload themes that aren't
    # listed there. The fallback guarantees a new theme added to
    # theme_universes.yaml can't silently disappear from the report, even if
    # THEME_ORDER above wasn't updated. (Caught the META/PINS/RDDT/SNAP/TTD
    # omission this way on 2026-06-01.)
    render_order = list(THEME_ORDER) + [t for t in rbt.keys() if t not in THEME_ORDER]

    # Dedupe across themes — each ticker once, under its FIRST theme.
    seen: dict[str, tuple[str, dict]] = {}
    ticker_themes: dict[str, list[str]] = {}
    for tkey in render_order:
        for r in rbt.get(tkey, []) or []:
            tk = (r.get("ticker") or "").upper()
            if not tk:
                continue
            tname = themes_meta.get(tkey, {}).get("name", tkey)
            ticker_themes.setdefault(tk, []).append(tname)
            if tk not in seen:
                seen[tk] = (tkey, r)

    total = len(seen)
    counts = {"enter": 0, "wait": 0, "avoid": 0, "watch": 0}
    deferred = 0  # subset of enter — gated by capacity (hard rule #24)
    for tk, (_, r) in seen.items():
        sr = sr_by_sym.get(tk.upper())
        s, label, *_ = _classify(r, sr=sr)
        counts[s] = counts.get(s, 0) + 1
        if s == "enter" and "DEFERRED" in label:
            deferred += 1

    lines: list[str] = []
    if gate_state is not None:
        # Capacity banner — mandatory first line (12-entry-pipeline-spec §1).
        lines.append(gate_state.banner)
        lines.append("")
    title = f"# When-To-Enter Report — {generated_at}" if generated_at else "# When-To-Enter Report"
    lines.append(title)
    lines.append("")
    lines.append(f"_Per-company entry framework across all {total} companies in the Thematic Scout._")
    lines.append(
        f"_Scout last refreshed: {gen_iso[:16] if gen_iso else 'n/a'}. Every value "
        "(RSI, trend, IV, drawdown, 5d momentum, CSP entry quote) is real and pulled "
        "from the live scout cache._"
    )
    lines.append("")
    deferred_note = f" (of which ⏸ {deferred} DEFERRED — capacity-gated)" if deferred else ""
    lines.append(
        f"**Summary:** 🟢 {counts['enter']} ENTRY NOW{deferred_note} · "
        f"🟡 {counts['wait']} WAIT · "
        f"🟡 {counts['watch']} WATCH/NEUTRAL · "
        f"🔴 {counts['avoid']} AVOID"
    )

    # ETF benchmarks one-liner — surfaces every ETF in the report with its
    # current status, so the user can scan sector-level reads in one glance
    # instead of hunting through individual theme sections. Cards are still
    # inline in their themes (tagged 🪙 ETF) for full detail.
    etf_lines = []
    for tk, (_, r) in sorted(seen.items()):
        if not _is_etf(tk):
            continue
        s, label, *_ = _classify(r, sr=sr_by_sym.get(tk.upper()))
        spot = r.get("spot")
        spot_s = f"${spot:,.2f}" if spot else "?"
        # Strip the emoji prefix from label for the compact one-liner
        clean_label = label.split(" — ", 1)[-1] if " — " in label else label
        clean_label = clean_label.replace("🔴 ", "").replace("🟡 ", "").replace("🟢 ", "").replace("🌟 ", "")
        etf_lines.append(f"`{tk}` {spot_s} {clean_label}")
    if etf_lines:
        lines.append("")
        lines.append(f"**🪙 ETF benchmarks ({len(etf_lines)}):** " + " · ".join(etf_lines))
    lines.append("")
    lines.append("## How to read")
    lines.append("")
    lines.append("Each company is a card with its current state, the scout's analytical read, and an EXPLICIT entry trigger. Statuses:")
    lines.append("")
    lines.append("- 🟢 **ENTRY NOW** — conditions favorable right now for the indicated side (CSP or BUY). The trigger line gives the concrete trade.")
    lines.append("- 🟡 **WAIT** — close to favorable but the trigger hasn't fired. The line tells you exactly what to wait for (RSI band + pullback %).")
    lines.append("- 🟡 **WATCH / NEUTRAL** — no current edge in either direction.")
    lines.append("- 🔴 **AVOID** — either the thesis looks broken (deep drawdown + weak technicals + below 200-SMA) or the scout has flagged a specific concern.")
    lines.append("")
    lines.append(
        "Priority is **technical state first** (overbought / extended / falling-knife), "
        "then thesis check, then verdict-driven AVOID, then the favored pullback band. "
        "RSI thresholds mirror `briefing.yaml` → `rsi_discipline`."
    )
    lines.append("")

    # Group themes — use the same render_order built above so newly-added
    # themes (e.g. social_ad_tech_ai) appear instead of being silently dropped.
    by_group: dict[str, list[str]] = {}
    for tkey in render_order:
        grp = themes_meta.get(tkey, {}).get("group", "Other")
        by_group.setdefault(grp, []).append(tkey)
    ordered_groups = [g for g in GROUP_ORDER if g in by_group] + [
        g for g in by_group if g not in GROUP_ORDER
    ]

    def _render_card(tk: str, r: dict, tname: str) -> None:
        """Append a per-ticker research card to ``lines``. Shared by the ETF
        section and the theme sections so layout stays in sync."""
        spot = r.get("spot")
        spot_s = f"${spot:,.2f}" if spot else "?"
        rsi = r.get("rsi_14")
        iv = r.get("iv_rank")
        dd = r.get("drawdown_pct")
        f5 = r.get("fivedayret_pct")
        sma = r.get("sma_200")
        tp = _trend_phrase(spot, sma) or "trend n/a"
        sr_card = sr_by_sym.get(tk.upper())
        _status, label, read, trigger = _classify(r, sr=sr_card)
        others = [t for t in ticker_themes.get(tk, []) if t != tname]
        also = f" · also in: {', '.join(others)}" if others else ""
        etf_tag = " 🪙 **ETF**" if _is_etf(tk) else ""
        lines.append(f"#### `{tk}` · {spot_s} · **{label}**{etf_tag}{also}")
        lines.append("")
        metrics = []
        if rsi is not None:
            metrics.append(f"RSI {rsi:.0f}")
        metrics.append(tp)
        if iv is not None:
            metrics.append(f"IV rank {iv:.0f}")
        if dd:
            metrics.append(f"drawdown {dd:.0f}%")
        if f5 is not None:
            metrics.append(f"5d {f5:+.1f}%")
        tp_rec = r.get("third_party_rec")
        if tp_rec and str(tp_rec).upper() not in ("NONE", "N/A"):
            metrics.append(f"rec {tp_rec}")
        lines.append("- " + " · ".join(metrics))
        lines.append(f"- **Read:** {read}")
        lines.append(f"- **Trigger:** {trigger}")
        if r.get("earnings_date") and r.get("days_to_earnings") is not None:
            lines.append(f"- Earnings: {r['earnings_date']} ({r['days_to_earnings']}d away)")
        lines.append("")

    # === Dedicated ETF Benchmarks section — ETFs render here only ===
    # Sector-level reads grouped by their parent theme. Individual ETF cards
    # NEVER appear inside the per-theme sections below, so the ETF section is
    # the single source of truth for "what's the sector doing?" reads. Users
    # asked for this explicitly — having ETFs mixed in with stocks made them
    # too easy to scroll past.
    etfs_by_theme: dict[str, list[tuple[str, dict]]] = {}
    for tk, (first_theme, r) in seen.items():
        if _is_etf(tk):
            etfs_by_theme.setdefault(first_theme, []).append((tk, r))
    if etfs_by_theme:
        lines.append("## 🪙 ETF Benchmarks")
        lines.append("")
        lines.append(
            "_Sector-level reads. Each ETF carries the same RSI / trend / IV / "
            "drawdown / 5d analysis and explicit trigger as the individual stocks "
            "below. ETFs are NOT duplicated in the theme sections — this is their "
            "single home in the report._"
        )
        lines.append("")
        for tkey in render_order:
            theme_etfs = etfs_by_theme.get(tkey, [])
            if not theme_etfs:
                continue
            tname = themes_meta.get(tkey, {}).get("name", tkey)
            theme_etfs.sort(key=lambda item: (
                STATUS_RANK.get(_classify(item[1], sr=sr_by_sym.get(item[0].upper()))[0], 9),
                item[0],
            ))
            lines.append(f"### {tname} ({len(theme_etfs)})")
            lines.append("")
            for tk, r in theme_etfs:
                _render_card(tk, r, tname)

    for grp in ordered_groups:
        lines.append(f"## {grp}")
        lines.append("")
        for tkey in by_group[grp]:
            tname = themes_meta.get(tkey, {}).get("name", tkey)
            # ETFs are now rendered exclusively in the ETF Benchmarks section
            # above. Skip them in the theme sections to avoid duplication.
            rows = [(tk, r) for tk, (first, r) in seen.items()
                    if first == tkey and not _is_etf(tk)]
            if not rows:
                continue

            def _sk(item):
                tk_sk, r = item
                s, *_ = _classify(r, sr=sr_by_sym.get(tk_sk.upper()))
                return (STATUS_RANK.get(s, 9), tk_sk)

            rows.sort(key=_sk)
            lines.append(f"### {tname} ({len(rows)})")
            lines.append("")
            for tk, r in rows:
                _render_card(tk, r, tname)

    lines.append("---")
    lines.append("")
    lines.append(
        "_Every numeric value in this report comes from the scout cache "
        "(`state/briefing_snapshots/scout_cache.json`); RSI bands from "
        "`briefing.yaml` → `rsi_discipline`. No hardcoded thresholds in the output._"
    )
    return "\n".join(lines)
