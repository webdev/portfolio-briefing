"""Technical Read + Per-Ticker Actions sections.

Renders the deep technical snapshot (``analysis.technical_indicators``,
attached by ``snapshot_inputs`` under ``technicals[<tk>]["deep"]``) as a
compact per-ticker card for every holding + candidate, grouped by portfolio
role — then runs the entry/exit recommender
(``analysis.entry_exit_recommender``) over the same set and renders the
actionable calls sorted by urgency.

This section provides DEPTH; the Watch panel keeps the "what to do about this
position" story (per_option_commentary) — nothing there is replaced.

Fail-closed everywhere (hard rules #10/#19): a ticker whose OHLC fetch failed
gets a "chart data unavailable, verify manually" line — never fabricated
indicators. Theme-watchlist names whose deep read wasn't fetched this cycle
are skipped with an explicit count, not silently filled.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from analysis import entry_exit_recommender as eer
except ImportError:  # pragma: no cover - path fallback for standalone runs
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis import entry_exit_recommender as eer

from analysis import rsi_discipline

DEFAULT_MEMORY_PATH = Path("state/fable_advisor_memory.md")

# Verdict label → display text (emoji + words). Every key mirrors the pinned
# vocabulary in technical_indicators.py.
_ST_DISPLAY = {
    "stretched-pullback-risk": "⚠️ Stretched near-term",
    "oversold-bounce": "🟢 Oversold bounce setup",
    "bull-momentum": "🚀 Bull momentum",
    "bear-momentum": "🔻 Bear momentum",
    "stabilizing": "🩹 Stabilizing",
    "neutral": "😐 Neutral near-term",
}
_LT_DISPLAY = {
    "secular-uptrend": "📈 Secular uptrend",
    "uptrend": "📊 Uptrend intact",
    "broken": "💥 Broken chart",
    "downtrend": "📉 Downtrend",
    "recovery": "🔄 Recovery",
    "weakening": "⚠️ Weakening",
    "sideways": "↔️ Sideways",
}


def _arrow(slope_pct: float | None) -> str:
    if slope_pct is None:
        return "→"
    if slope_pct > 0.2:
        return "↗"
    if slope_pct < -0.2:
        return "↘"
    return "→"


def _bb_word(pos_pct: float) -> str:
    if pos_pct >= 80:
        return "near upper"
    if pos_pct <= 20:
        return "near lower"
    return "mid-band"


def _headline_spot(deep_spot, live_spot) -> str:
    """The header price string — ONE resolved quote per ticker (2026-08-07
    TEAM bug: the Technical Read header showed the $110.17 OHLC close while
    the live quote was $144.41 after a +31% earnings gap; other surfaces
    showed the live price, so the briefing carried three TEAM spots).

    When the live quote deviates > 2% from the technicals' close, headline
    the LIVE price and keep the close visible as the labelled reference the
    indicators were computed from. No live quote / in-tolerance → the close
    renders alone (legacy)."""
    try:
        d, lv = float(deep_spot), float(live_spot)
        if d > 0 and lv > 0 and abs(lv - d) / d * 100.0 > 2.0:
            return f"${lv:,.2f} (live · indicators from ${d:,.2f} close)"
    except (TypeError, ValueError):
        pass
    return f"${float(deep_spot):,.2f}" if deep_spot else "$?"


def _fmt_card(ticker: str, deep: dict, sr: dict | None,
              live_spot: float | None = None) -> list[str]:
    """One compact per-ticker card. Every number comes from the deep snapshot /
    SR dict computed this cycle — nothing hardcoded (hard rule #19)."""
    spot = deep.get("spot")
    st = _ST_DISPLAY.get(deep.get("short_term_verdict", ""), deep.get("short_term_verdict", "?"))
    lt = _LT_DISPLAY.get(deep.get("long_term_verdict", ""), deep.get("long_term_verdict", "?"))
    lines = [f"### {ticker} — {_headline_spot(spot, live_spot)} · {lt} · {st}"]

    # Line 1: momentum
    rsi = deep.get("rsi_14")
    rsi_str = rsi_discipline.tag(rsi)  # "RSI 72 🔴 overbought" / "RSI n/a"
    bb_pos = deep.get("bb_position_pct", 0.0)
    hist = deep.get("macd_hist", 0.0)
    hist_5d = deep.get("macd_hist_5d_ago", 0.0)
    macd_arrow = "↗" if hist > hist_5d else "↘"
    macd_sign = "+" if hist >= 0 else "−"
    lines.append(
        f"  {rsi_str} · BB {bb_pos:.0f}% ({_bb_word(bb_pos)}) · "
        f"MACD {macd_arrow} {macd_sign}{abs(hist):.2f} · ATR {deep.get('atr_pct', 0):.1f}%"
    )

    # Line 2: trend structure
    cross = "Golden cross" if deep.get("cross") == "golden" else "Death cross"
    lines.append(
        f"  50-SMA {_arrow(deep.get('sma_50_slope_pct'))} · "
        f"200-SMA {_arrow(deep.get('sma_200_slope_pct'))} · {cross} · "
        f"vs 200-SMA {deep.get('vs_sma200_pct', 0):+.1f}%"
    )

    # Line 3: S/R — only when real levels exist (fail-closed).
    spot_f = float(spot or 0)
    res = eer._nearest_resistance(sr, spot_f)
    sup = eer._nearest_support(sr, spot_f)
    sr_bits = []
    if res:
        sr_bits.append(f"↑ R ${res[0]:g} ({res[2]} touches, {res[1]:.1f}% away)")
    if sup:
        sr_bits.append(f"↓ S ${sup[0]:g} ({sup[2]} touches, {sup[1]:.1f}% away)")
    if sr_bits:
        lines.append("  " + " · ".join(sr_bits))

    # Line 4: range + returns
    ret_bits = (
        f"1w {deep.get('ret_1w_pct', 0):+.0f}% "
        f"1m {deep.get('ret_1m_pct', 0):+.0f}% "
        f"3m {deep.get('ret_3m_pct', 0):+.0f}%"
    )
    if deep.get("ret_1y_pct") is not None:
        ret_bits += f" 1y {deep['ret_1y_pct']:+.0f}%"
    range_line = (
        f"  52w range: {deep.get('yr_position_pct', 0):.0f}% · "
        f"ATH DD {deep.get('ath_dd_pct', 0):+.1f}% · {ret_bits}"
    )
    if deep.get("vol_ratio_30d") is not None:
        range_line += f" · vol {deep['vol_ratio_30d']:.1f}× avg"
    lines.append(range_line)
    return lines


def _position_states(positions: list) -> tuple[set, set, set]:
    """(equity_syms, short_put_underlyings, short_call_underlyings)."""
    equity: set = set()
    puts: set = set()
    calls: set = set()
    for p in positions or []:
        try:
            qty = float(p.get("qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0
        if p.get("assetType") == "EQUITY" and qty > 0 and p.get("symbol"):
            equity.add(str(p["symbol"]).upper())
        elif p.get("assetType") == "OPTION" and qty < 0 and p.get("underlying"):
            und = str(p["underlying"]).upper()
            if (p.get("optionType") or p.get("type") or "").upper().startswith("P"):
                puts.add(und)
            else:
                calls.add(und)
    return equity, puts, calls


def _state_for(tk: str, equity: set, puts: set, calls: set) -> str:
    if tk in equity:
        return "long_shares"
    if tk in puts:
        return "short_put"
    if tk in calls:
        return "short_call"
    return "none"


def _scout_tickers(scout_payload: dict | None) -> list[str]:
    out: list[str] = []
    seen: set = set()
    for results in ((scout_payload or {}).get("results_by_theme") or {}).values():
        for r in results or []:
            if not isinstance(r, dict):
                continue
            tk = (r.get("ticker") or "").upper()
            if tk and tk not in seen:
                seen.add(tk)
                out.append(tk)
    return out


def _candidate_tickers(scout_payload: dict | None, config: dict | None) -> list[str]:
    try:
        from steps import candidate_research as cr
        return [t.upper() for t in cr.briefing_candidate_tickers(scout_payload, config)]
    except Exception as e:  # candidate module optional in some test contexts
        print(f"[technical_read] candidate ticker lookup failed: {e}", file=sys.stderr)
        return []


def _load_directive_holds(memory_path: Path | None) -> set[str]:
    path = memory_path or DEFAULT_MEMORY_PATH
    try:
        if path.exists():
            return eer.directive_hold_tickers(path.read_text(encoding="utf-8"))
    except OSError as e:
        print(f"[technical_read] advisor memory read failed: {e}", file=sys.stderr)
    return set()


def _one_line_read(ticker: str, deep: dict, live_spot: float | None = None) -> str:
    """Compact one-liner (2026-08-06 length diet) for tickers with NO action
    anywhere in this cycle's briefing: `TICKER $spot — RSI NN · LT verdict ·
    ST verdict`. Every value measured from this cycle's deep read; the price
    is the ONE resolved live quote when it drifted >2% from the close."""
    spot = deep.get("spot")
    spot_s = f" {_headline_spot(spot, live_spot)}" if spot else ""
    st = _ST_DISPLAY.get(deep.get("short_term_verdict", ""), deep.get("short_term_verdict", "?"))
    lt = _LT_DISPLAY.get(deep.get("long_term_verdict", ""), deep.get("long_term_verdict", "?"))
    return (f"- **{ticker}**{spot_s} — {rsi_discipline.tag(deep.get('rsi_14'))} · "
            f"{lt} · {st}")


def render_technical_read_sections(
    snapshot_data: dict,
    config: dict | None,
    *,
    scout_payload: dict | None = None,
    gate_state=None,
    recommendations_list: list | None = None,
    memory_path: Path | None = None,
    compact: bool = False,
    action_tickers: set | None = None,
) -> list[str]:
    """Render "## 🎯 Technical Read" (per-ticker depth cards, grouped by role)
    followed by "## 📋 Per-Ticker Actions" (the recommender's calls).

    Both are driven entirely by ``snapshot_data["technicals"][tk]["deep"]`` —
    data fetched this cycle. Tickers without a deep read get an explicit
    "chart data unavailable" line (positions/candidates) or are skipped with a
    count (theme watchlist).

    ``compact`` + ``action_tickers`` (2026-08-06 length diet): the full
    per-ticker card renders ONLY for tickers with an action somewhere in
    this cycle's briefing (action list, actionable LTO, candidate ticket);
    every other name collapses to one measured line. ``action_tickers is
    None`` fails open to the full legacy rendering.
    """
    collapse = compact and action_tickers is not None
    action_set = {str(t).upper() for t in (action_tickers or set())}
    technicals = (snapshot_data or {}).get("technicals") or {}
    positions = (snapshot_data or {}).get("positions") or []
    quotes = (snapshot_data or {}).get("quotes") or {}
    # Canonical live-spot resolver (2026-08-07 TEAM bug) — the header price
    # comes from the ONE resolved quote; the technicals close stays visible
    # as the labelled indicator reference when the two deviate >2%.
    try:
        from analysis.price_consistency import resolve_spot as _resolve_spot
    except ImportError:
        def _resolve_spot(_tk, _q=None, _p=None):  # type: ignore
            return None
    equity, puts, calls = _position_states(positions)
    # 2026-08-04 (PLTR): core = core_positions ∪ Tier A (position_tiers).
    try:
        from analysis.position_tiers import core_union as _core_union
        core = _core_union(config or {})
    except Exception:
        core = {str(t).upper() for t in ((config or {}).get("core_positions") or [])}

    shown: set = set()

    def _take(tickers) -> list[str]:
        out = []
        for tk in tickers:
            tk = tk.upper()
            if tk and tk not in shown:
                shown.add(tk)
                out.append(tk)
        return out

    candidates = _candidate_tickers(scout_payload, config)
    theme_all = _scout_tickers(scout_payload)

    groups: list[tuple[str, list[str]]] = []
    groups.append(("Core equity holdings", _take(sorted(equity & core))))
    groups.append(("Other equity holdings", _take(sorted(equity - core))))
    groups.append(("Short-put underlyings", _take(sorted(puts))))
    groups.append(("Other option underlyings", _take(sorted(calls))))
    groups.append(("Candidate trades", _take(candidates)))
    # Theme watchlist: only names whose deep read was fetched this cycle
    # (deep is computed for held + candidate underlyings — the rest are
    # covered by the Scout pulse and skipped here with an explicit count).
    theme_left = [t for t in theme_all if t.upper() not in shown]
    theme_with_deep = _take(
        t for t in theme_left
        if isinstance(technicals.get(t.upper()), dict)
        and technicals[t.upper()].get("deep")
    )
    theme_skipped = len(theme_left) - len(theme_with_deep)
    groups.append(("Theme watchlist", theme_with_deep))

    lines: list[str] = ["## 🎯 Technical Read", ""]
    lines.append(
        "_Deep per-ticker read (BB 20/2 · MACD 12/26/9 · ATR 14 · SMA slopes · "
        "52w range · drawdown · returns), computed from this cycle's OHLC pull. "
        "Verdicts: short-term = 1-4 weeks, long-term = 3-12 months._"
    )
    lines.append("")

    any_card = False
    for title, tickers in groups:
        if not tickers:
            continue
        if lines and lines[-1] != "":
            lines.append("")  # separator after a trailing compact one-liner
        lines.append(f"**{title}**")
        lines.append("")
        for tk in tickers:
            tech = technicals.get(tk)
            deep = tech.get("deep") if isinstance(tech, dict) else None
            sr = tech.get("support_resistance") if isinstance(tech, dict) else None
            _live = _resolve_spot(tk, quotes, positions)
            if collapse and tk not in action_set and deep:
                # No action on this name this cycle → one measured line.
                lines.append(_one_line_read(tk, deep, _live))
                any_card = True
                continue  # no blank line between one-liners (list stays tight)
            # Full card / unavailable heading — needs a blank separator when
            # the previous line was a compact one-liner.
            if lines and lines[-1].startswith("- **"):
                lines.append("")
            if not deep:
                lines.append(f"### {tk} — chart data unavailable, verify manually")
            else:
                lines.extend(_fmt_card(tk, deep, sr, _live))
                any_card = True
            lines.append("")
    if lines and lines[-1] != "":
        lines.append("")  # close a trailing compact list before footers
    if collapse:
        lines.append(
            "_Compact view: full technical cards only for names with an "
            "action in this cycle's briefing; every other holding is one "
            "measured line (RSI · trend · verdict)._"
        )
        lines.append("")
    if theme_skipped > 0:
        lines.append(
            f"_Theme watchlist: deep read fetched only for held + candidate names — "
            f"{theme_skipped} other theme name(s) covered by the Thematic Scout pulse._"
        )
        lines.append("")
    if not any_card:
        lines.append("_No deep technical data available this cycle — verify data feed._")
        lines.append("")

    # ── Per-Ticker Actions (the recommender) ────────────────────────────────
    rec_cfg = (((config or {}).get("technical_analysis") or {}).get("recommender") or {})
    if not rec_cfg.get("enabled", True):
        return lines

    gate_open = bool(getattr(gate_state, "open", True)) if gate_state is not None else True
    gate_reason = None
    if gate_state is not None and getattr(gate_state, "reasons", None):
        gate_reason = gate_state.reasons[0]
    recs_map = {
        (r.get("ticker") or "").upper(): r
        for r in (recommendations_list or []) if isinstance(r, dict) and r.get("ticker")
    }
    directive_holds = _load_directive_holds(memory_path)

    recs: list[eer.Recommendation] = []
    unavailable: list[str] = []
    for tk in sorted(shown):
        tech = technicals.get(tk)
        deep = tech.get("deep") if isinstance(tech, dict) else None
        sr = tech.get("support_resistance") if isinstance(tech, dict) else None
        if not deep:
            unavailable.append(tk)
            continue
        rec = eer.recommend(
            tk, deep, _state_for(tk, equity, puts, calls), config,
            sr=sr, gate_open=gate_open, gate_reason=gate_reason,
            parkev=recs_map.get(tk), directive_holds=directive_holds,
        )
        if rec is not None:
            recs.append(rec)

    actionable = eer.sort_recommendations(recs)
    hold_count = sum(1 for r in recs if r.call == eer.HOLD)

    lines.append("## 📋 Per-Ticker Actions")
    lines.append("")
    lines.append(
        "_Entry/exit calls from the deep technical read. Sorted by urgency; "
        "HOLD names are silent (see Technical Read above). These are technical "
        "reads on the underlying, not order tickets — position-level discipline "
        "(rolls/closes) lives in the Action List and Watch panels._"
    )
    lines.append("")
    if not actionable:
        lines.append(
            f"_No urgent per-ticker calls this cycle — {hold_count} name(s) rate HOLD._"
        )
        lines.append("")
    else:
        for rec in actionable:
            flag_str = " — " + " · ".join(rec.flags) if rec.flags else ""
            lines.append(
                f"- **{rec.call}** `{rec.ticker}` ({rec.position_state}){flag_str}"
            )
            for reason in rec.reasons:
                lines.append(f"  - {reason}")
            lines.append("")
        lines.append(f"_{hold_count} other name(s) rate HOLD (not shown)._")
        lines.append("")
    if unavailable:
        lines.append(
            "_Chart data unavailable this cycle (no call emitted): "
            + ", ".join(unavailable) + " — verify manually._"
        )
        lines.append("")
    return lines
