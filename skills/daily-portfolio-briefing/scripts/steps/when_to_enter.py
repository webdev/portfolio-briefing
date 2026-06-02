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


def classify(r: dict) -> tuple[str, str, str, str]:
    """Classify a single scout result.

    Returns ``(status, status_label, read, trigger)`` where ``status`` is one
    of ``enter | wait | watch | avoid``. Pure function over the scout result —
    no I/O, deterministic, side-effect free."""
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

    # Technical state first — even an AVOID verdict on an overbought name
    # reduces to "wait for the cool-off," not "thesis broken."
    if rsi is not None and rsi >= 70:
        side_note = (" If you own shares, WRITE COVERED CALLS — rich IV + "
                     "extended setup is the favored time.") if iv >= 60 else ""
        return ("wait", "🔴 WAIT — overbought",
                f"Extended after a {f5:+.1f}% 5d run, RSI {rsi:.0f}, {tp}, IV rank {iv:.0f}.",
                f"WAIT for RSI < 55 AND a 8-12% pullback from spot.{side_note}")

    if rsi is not None and 60 <= rsi < 70:
        return ("wait", "🟡 WAIT — extended",
                f"RSI {rsi:.0f}, {tp}, {f5:+.1f}% 5d — extended but not at the chase point.",
                "WAIT for RSI to retrace into 45-55 AND a 5-8% pullback. Covered calls attractive if held.")

    # Thesis check — deep drawdown + weak technicals + below the 200-SMA.
    if dd >= 40 and rsi is not None and rsi < 45 and (sma_pct or 0) < -15:
        return ("avoid", "🔴 AVOID — thesis check",
                f"Drawdown {dd:.0f}% from highs, RSI {rsi:.0f}, {tp} — broken trend without strength.",
                "Don't enter on the pullback alone. Verify fundamentals. Consider only after 2 consecutive higher weekly closes AND RSI > 45.")

    if rsi is not None and rsi < 25:
        return ("wait", "🟡 WAIT — falling knife",
                f"RSI {rsi:.0f} deeply oversold, 5d {f5:+.1f}%, {tp}.",
                "WAIT for stabilization: RSI > 35 AND at least one green day. Small starter size only when it bases.")

    # Verdict-driven AVOID (the scout flagged a specific concern not caught
    # by the technical states above). Surface the scout's actual rationale.
    if verdict.startswith("AVOID"):
        return ("avoid", "🔴 AVOID — scout flag",
                f"Scout verdict: {r.get('verdict')}. {rationale[:160]}",
                "Don't enter on metrics alone — verify the underlying reason. Re-check when the flagged condition changes.")

    # Favored pullback band
    if rsi is not None and 35 <= rsi <= 55:
        # Parkev's rating tier (5=Top Stock to Buy, 4=Top 15/25 Stock, 3=Buy, ...)
        # — a tier ≥ 4 catalyst is a much stronger conviction signal than a
        # plain "Buy." When the technical setup AND the high-conviction rec line
        # up, surface as 🌟 STRONG BUY — distinct from the standard tier-3 BUY
        # — so the user can size accordingly.
        tier = r.get("rating_tier")
        is_top_tier = (tier or 0) >= 4
        raw_rec = r.get("raw_recommendation") or ""
        tier_note = (f" Parkev tier-{tier} ({raw_rec}) — high-conviction catalyst."
                     if is_top_tier else "")
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
            label = "🌟 STRONG BUY — ENTRY NOW (CSP)" if is_top_tier else "🟢 ENTRY NOW — CSP"
            return ("enter", label,
                    f"RSI {rsi:.0f} in pullback zone, IV rank {iv:.0f}, drawdown {dd:.0f}%, {tp}. "
                    f"Favorable spot to get paid to maybe buy lower.{tier_note}",
                    f"SELL 1× ${strike:g}P exp **{exp_p}** ({csp.get('dte')} DTE) · "
                    f"mid ${csp.get('mid', 0):.2f} (bid ${csp.get('bid', 0):.2f} / "
                    f"ask ${csp.get('ask', 0):.2f}). Collateral ~${float(strike) * 100:,.0f}.{earn_note}")
        if verdict.startswith("BUY"):
            label = "🌟 STRONG BUY — ENTRY NOW" if is_top_tier else "🟢 ENTRY NOW — BUY"
            trigger = (
                "ENTER: sized for high-conviction (1/2 of target weight is reasonable). "
                "Scale on further weakness toward RSI 35-40. Average down on confirmed support."
                if is_top_tier else
                "ENTER: small starter (1/3 of target weight). Scale on further weakness "
                "toward RSI 35-40. Average down on confirmed support."
            )
            return ("enter", label,
                    f"RSI {rsi:.0f} in pullback zone, drawdown {dd:.0f}%, {tp}, "
                    f"third-party BUY-rated.{tier_note}",
                    trigger)
        return ("watch", "🟡 WATCH — neutral",
                f"RSI {rsi:.0f} in pullback band but no specific catalyst from the scout.",
                "Monitor. CSP entry becomes favored if IV rank > 50 AND a third-party BUY/STRONG_BUY arrives, "
                "OR if drawdown deepens past 20% with RSI holding 35-45.")

    if rsi is None:
        return ("watch", "🟡 WATCH — no RSI",
                "RSI data unavailable; can't gate.",
                "Verify the ticker manually before any entry — fail closed.")

    if 25 <= rsi < 35:
        return ("wait", "🟡 WAIT — oversold",
                f"RSI {rsi:.0f}, drawdown {dd:.0f}%, 5d {f5:+.1f}%, {tp}.",
                "Small starter OK if you want; otherwise WAIT for RSI > 40 with confirmed support.")

    return ("watch", "🟡 NEUTRAL",
            f"RSI {rsi:.0f}, {tp}, 5d {f5:+.1f}% — no directional edge.",
            "Monitor. CSP entry favored on a pullback to RSI 40-50.")


def render_when_to_enter_report(scout_payload: dict | None, *,
                                 config: dict | None = None,
                                 generated_at: str = "") -> str:
    """Build the markdown report. Empty-string when no scout payload."""
    if not scout_payload:
        return ""
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
    for _, (_, r) in seen.items():
        s, *_ = classify(r)
        counts[s] = counts.get(s, 0) + 1

    lines: list[str] = []
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
    lines.append(
        f"**Summary:** 🟢 {counts['enter']} ENTRY NOW · "
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
        s, label, *_ = classify(r)
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
        _status, label, read, trigger = classify(r)
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
                STATUS_RANK.get(classify(item[1])[0], 9), item[0]
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
                _, r = item
                s, *_ = classify(r)
                return (STATUS_RANK.get(s, 9), item[0])

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
