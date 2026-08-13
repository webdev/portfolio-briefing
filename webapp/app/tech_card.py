"""Technical-analysis ticker card — view-model builder.

Transforms the pipeline's deep technical snapshot
(``snapshot["technicals"][tk]["deep"]``, produced by
``analysis/technical_indicators.py``) into a render-ready dict for the
``tech_card`` Jinja macro (``templates/_macros/tech_card.html``). The
visual contract — chip color bands, BB marker palette, S/R track,
verdict labels — is copied from the standalone infographic the user
already trusts (``technical_analysis_infographic.html``), so the webapp
cards READ identically to that page.

Hard rules honored:
  - #19: every number comes from the deep/SR dicts computed by the
    pipeline this cycle — nothing hardcoded, nothing fabricated. A
    ticker without a deep read gets ``available=False`` and the macro
    renders "chart data unavailable, verify manually".
  - #10: fail closed — missing SR clusters render no S/R viz rather
    than synthesized levels.

All functions here are pure over the loaded JSON (I/O lives in
``ingest.load_snapshot_technicals``), so every color band is testable.
"""

from __future__ import annotations

from typing import Any

from . import ingest, rsi_zones

# ─── Chip tone bands (from the infographic's exact color usage) ──────
# Tones map to CSS classes .tc-chip-<tone>; the palette lives in app.css
# with light (infographic-exact) and dark variants.


def rsi_chip(rsi: float | None) -> dict[str, str]:
    """RSI chip: 🔵 <30 oversold · 🟡 30-40 weak · ⚪ 40-50 neutral ·
    🟢 50-60 healthy · 🟠 60-70 elevated · 🔴 ≥70 overbought."""
    if rsi is None:
        return {"icon": "⚪", "label": "RSI n/a", "tone": "neutral"}
    if rsi >= 70:
        tone, icon = "red", "🔴"
    elif rsi >= 60:
        tone, icon = "orange", "🟠"
    elif rsi >= 50:
        tone, icon = "green", "🟢"
    elif rsi >= 40:
        tone, icon = "neutral", "⚪"
    elif rsi >= 30:
        tone, icon = "yellow", "🟡"
    else:
        tone, icon = "blue", "🔵"
    return {"icon": icon, "label": f"RSI {rsi:.0f}", "tone": tone}


def bb_chip(pos_pct: float) -> dict[str, str]:
    """Bollinger position chip: 🔵 ≤20 (at/below lower band) · 🟡 20-35 ·
    ⚪ 35-60 · 🟢 60-80 · 🟠 80-100 · 🔴 >100 (pierced upper band)."""
    if pos_pct > 100:
        tone, icon = "red", "🔴"
    elif pos_pct >= 80:
        tone, icon = "orange", "🟠"
    elif pos_pct >= 60:
        tone, icon = "green", "🟢"
    elif pos_pct >= 35:
        tone, icon = "neutral", "⚪"
    elif pos_pct > 20:
        tone, icon = "yellow", "🟡"
    else:
        tone, icon = "blue", "🔵"
    return {"icon": icon, "label": f"BB {pos_pct:.0f}%", "tone": tone}


def macd_chip(hist: float, hist_5d_ago: float) -> dict[str, str]:
    """MACD chip: sign of histogram + 5-day direction.
    🟢 rising/positive · 🟡 rising/negative (turning up) ·
    🟠 falling/positive (rolling over) · 🔴 falling/negative."""
    rising = hist > hist_5d_ago
    sign = "+" if hist >= 0 else "−"
    arrow = "↗" if rising else "↘"
    if rising and hist >= 0:
        tone, icon = "green", "🟢"
    elif rising:
        tone, icon = "yellow", "🟡"
    elif hist >= 0:
        tone, icon = "orange", "🟠"
    else:
        tone, icon = "red", "🔴"
    return {"icon": icon, "label": f"MACD {arrow} {sign}", "tone": tone}


def bb_marker_class(pos_pct: float) -> str:
    """Spot-marker color on the BB track (infographic palette):
    ≤10 deep-oversold purple · ≤30 blue · <60 green · <80 amber · ≥80 red."""
    if pos_pct <= 10:
        return "tc-bbm-deep"
    if pos_pct <= 30:
        return "tc-bbm-low"
    if pos_pct < 60:
        return "tc-bbm-mid"
    if pos_pct < 80:
        return "tc-bbm-high"
    return "tc-bbm-top"


# ─── Verdict display (labels match the infographic verbatim) ─────────

_ST_DISPLAY: dict[str, tuple[str, str]] = {
    "stretched-pullback-risk": ("⚠️ Stretched — pullback risk", "warn"),
    "oversold-bounce": ("💚 Oversold — bounce setup", "pos"),
    "bull-momentum": ("🚀 Bullish momentum", "pos"),
    "bear-momentum": ("🔻 Bearish momentum", "neg"),
    "stabilizing": ("🌅 Stabilizing", "neutral"),
    "neutral": ("➖ Neutral / consolidating", "neutral"),
}

_LT_DISPLAY: dict[str, tuple[str, str]] = {
    "secular-uptrend": ("📈 Secular uptrend", "pos"),
    "uptrend": ("📊 Uptrend intact", "ok"),
    "broken": ("💥 Broken chart", "neg"),
    "downtrend": ("📉 Confirmed downtrend", "neg"),
    "recovery": ("🔄 Recovery attempt", "warn"),
    "weakening": ("⚠️ Weakening", "orange"),
    "sideways": ("↔️ Sideways / range-bound", "neutral"),
}


def _verdict(mapping: dict, key: str | None) -> dict[str, str]:
    label, tone = mapping.get(key or "", (key or "n/a", "neutral"))
    return {"label": label, "tone": tone}


# ─── Helpers ──────────────────────────────────────────────────────────


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _ret_item(label: str, val: float | None) -> dict[str, Any] | None:
    if val is None:
        return None
    tone = "pos" if val > 0 else ("neg" if val < 0 else "neutral")
    return {"label": label, "value": f"{val:+.1f}%", "tone": tone}


def _range_pct(price: float, lo: float, hi: float) -> float | None:
    if hi <= lo:
        return None
    return _clamp((price - lo) / (hi - lo) * 100.0)


def _slope_arrow(slope: float | None) -> str:
    if slope is None:
        return "→"
    if slope > 0.2:
        return "↗"
    if slope < -0.2:
        return "↘"
    return "→"


def _build_sr(sr: dict | None, deep: dict) -> dict[str, Any] | None:
    """S/R track view-model from the pipeline's compute_sr clusters,
    positioned on the 52-week range. Fail closed: no SR result → None
    (macro renders an explicit 'S/R unavailable' note, never a
    synthesized level)."""
    if not isinstance(sr, dict):
        return None
    supports = [s for s in (sr.get("supports") or []) if isinstance(s, dict) and s.get("price")]
    resistances = [r for r in (sr.get("resistances") or []) if isinstance(r, dict) and r.get("price")]
    if not supports and not resistances:
        return None

    spot = float(deep.get("spot") or 0)
    yr_lo = float(deep.get("yr_lo") or 0)
    yr_hi = float(deep.get("yr_hi") or 0)
    if not spot or yr_hi <= yr_lo:
        return None

    # Strongest first; cap at 4 lines per side (matches the infographic's
    # visual density).
    supports = sorted(supports, key=lambda s: -(s.get("strength") or 0))[:4]
    resistances = sorted(resistances, key=lambda r: -(r.get("strength") or 0))[:4]

    sup_lines = [p for p in (_range_pct(float(s["price"]), yr_lo, yr_hi) for s in supports) if p is not None]
    res_lines = [p for p in (_range_pct(float(r["price"]), yr_lo, yr_hi) for r in resistances) if p is not None]

    def _nearest(levels: list[dict], below: bool) -> dict | None:
        cands = [
            lv for lv in levels
            if (float(lv["price"]) < spot) == below and float(lv["price"]) != spot
        ]
        if not cands:
            return None
        return min(cands, key=lambda lv: abs(float(lv["price"]) - spot))

    def _note(lv: dict | None, below: bool) -> str | None:
        if not lv:
            return None
        price = float(lv["price"])
        dist = abs(price - spot) / spot * 100.0
        touches = lv.get("touches")
        touch_str = f", {touches:.0f}× touched" if touches else ""
        word = "below" if below else "above"
        arrow = "↓ Support" if below else "↑ Resistance"
        return f"{arrow} ${price:,.2f} ({dist:.1f}% {word}{touch_str})"

    return {
        "sup_lines": sup_lines,
        "res_lines": res_lines,
        "marker_left": _range_pct(spot, yr_lo, yr_hi),
        "lo_str": f"52w Low ${yr_lo:,.0f}",
        "hi_str": f"52w High ${yr_hi:,.0f}",
        "nearest_sup": _note(_nearest(supports, below=True), below=True),
        "nearest_res": _note(_nearest(resistances, below=False), below=False),
    }


# ─── Card builder ─────────────────────────────────────────────────────


def skeleton(ticker: str, name: str | None = None, spot: float | None = None) -> dict[str, Any]:
    """Explicit 'no data' card — rendered when the deep read is missing
    (hard rule #19: say so, never fabricate)."""
    return {
        "ticker": ticker.upper(),
        "name": name,
        "available": False,
        "spot_str": f"${spot:,.2f}" if spot else None,
        # RSI zone filtering (2026-08-10): no deep read → no RSI → the
        # card is 'unknown' and stays visible only under the All filter.
        "rsi": None,
        "rsi_zones": rsi_zones.zones_attr(None),
        "rsi_zone_badge": None,
    }


def build_card(
    ticker: str,
    entry: dict[str, Any] | None,
    name: str | None = None,
) -> dict[str, Any]:
    """One ticker's full card view-model from its technicals.json entry
    (the dict holding ``deep`` + ``support_resistance``). Falls back to
    the skeleton when the deep read is missing."""
    ticker = (ticker or "").upper()
    entry = entry if isinstance(entry, dict) else {}
    deep = entry.get("deep")
    if not isinstance(deep, dict) or deep.get("spot") is None:
        return skeleton(ticker, name=name, spot=entry.get("spot"))

    spot = float(deep["spot"])
    bb_pos = float(deep.get("bb_position_pct") or 0.0)
    hist = float(deep.get("macd_hist") or 0.0)
    hist_5d = float(deep.get("macd_hist_5d_ago") or 0.0)

    returns = [
        _ret_item("1w", deep.get("ret_1w_pct")),
        _ret_item("1m", deep.get("ret_1m_pct")),
        (_ret_item("1y", deep.get("ret_1y_pct"))
         or _ret_item("3m", deep.get("ret_3m_pct"))),
        _ret_item("ATH DD", deep.get("ath_dd_pct")),
    ]

    cross_golden = deep.get("cross") == "golden"
    slope_200 = deep.get("sma_200_slope_pct")
    vs200 = deep.get("vs_sma200_pct")

    vol_bits = f"{deep.get('atr_pct', 0):.1f}% daily"
    if deep.get("vol_ratio_30d") is not None:
        vol_bits += f" · vol {deep['vol_ratio_30d']:.1f}× avg"

    rsi_val = deep.get("rsi_14")
    try:
        rsi_val = float(rsi_val) if rsi_val is not None else None
    except (TypeError, ValueError):
        rsi_val = None

    return {
        "ticker": ticker,
        "name": name,
        "available": True,
        "spot_str": f"${spot:,.2f}",
        # RSI zone filtering (George 2026-08-10) — non-exclusive zone tags
        # + humanized at-a-glance badge; single source: rsi_zones.py.
        "rsi": rsi_val,
        "rsi_zones": rsi_zones.zones_attr(rsi_val),
        "rsi_zone_badge": rsi_zones.zone_badge(rsi_val),
        "returns": [r for r in returns if r],
        "chips": [
            # One-voice RSI (George 2026-08-13): the RSI chip and the zone
            # badge MUST derive from the same coerced value — a chip saying
            # "RSI 54" beside a zone badge computed from a different read
            # is exactly the "indicators feel wrong" failure mode.
            rsi_chip(rsi_val),
            bb_chip(bb_pos),
            macd_chip(hist, hist_5d),
        ],
        "bb": {
            "lower_str": f"Lower ${deep.get('bb_lower', 0):,.0f}",
            "mid_str": f"Mid ${deep.get('bb_mid', 0):,.0f}",
            "upper_str": f"Upper ${deep.get('bb_upper', 0):,.0f}",
            "marker_left": _clamp(bb_pos),
            "marker_class": bb_marker_class(bb_pos),
            "width_str": f"Volatility: {deep.get('bb_width_pct', 0):.0f}% width",
        },
        "sr": _build_sr(entry.get("support_resistance"), deep),
        "trend": {
            "cross_str": (
                f"{'🟢 Golden cross' if cross_golden else '🔴 Death cross'}"
                f" · 200-SMA {_slope_arrow(slope_200)} "
                f"{(slope_200 or 0):+.1f}%/mo"
            ),
            "vs200_str": f"{(vs200 or 0):+.1f}%" if vs200 is not None else "n/a",
            "vs200_tone": ("pos" if (vs200 or 0) > 0 else ("neg" if (vs200 or 0) < 0 else "neutral")),
            "range_str": f"{deep.get('yr_position_pct', 0):.0f}% of range",
            "vol_str": vol_bits,
        },
        "st": _verdict(_ST_DISPLAY, deep.get("short_term_verdict")),
        "lt": _verdict(_LT_DISPLAY, deep.get("long_term_verdict")),
    }


# ─── Page-level assembly ──────────────────────────────────────────────


def _names_map(date: str) -> dict[str, str]:
    """ticker → company name, from the day's Parkev recs (the only
    per-ticker name source the snapshots carry). Missing names are fine —
    the macro just omits the name span."""
    out: dict[str, str] = {}
    for r in ingest.load_snapshot_recs(date):
        tk = (r.get("ticker") or "").upper()
        nm = r.get("name")
        if tk and nm:
            out[tk] = str(nm)
    return out


def build_for_ticker(date: str | None, ticker: str) -> dict[str, Any]:
    """Card view-model for one ticker on one date (skeleton if no data)."""
    ticker = (ticker or "").upper()
    if not date:
        return skeleton(ticker)
    entry = ingest.load_snapshot_technicals(date).get(ticker)
    return build_card(ticker, entry, name=_names_map(date).get(ticker))


def build_map(date: str | None) -> dict[str, dict[str, Any]]:
    """ticker → card view-model for every ticker whose deep read exists
    on `date`. Tickers without a deep payload are omitted (callers that
    must show absence explicitly use build_for_ticker / build_groups)."""
    if not date:
        return {}
    names = _names_map(date)
    out: dict[str, dict[str, Any]] = {}
    for tk, entry in ingest.load_snapshot_technicals(date).items():
        if isinstance(entry, dict) and isinstance(entry.get("deep"), dict):
            out[tk] = build_card(tk, entry, name=names.get(tk))
    return out


def build_groups(date: str | None) -> dict[str, Any]:
    """Cards for the briefing's "🎯 Technical Read" tab, grouped by
    portfolio role (mirrors steps/technical_read.py's grouping):
      Equity holdings · Short-put underlyings · Other option underlyings ·
      Candidates & watchlist (remaining technicals.json names with a deep
      read).

    Position tickers WITHOUT a deep read still get an explicit skeleton
    card (a held name silently missing is a data gap); watchlist names
    without one are skipped with a count.
    """
    empty = {"groups": [], "count": 0, "deep_count": 0, "skipped": 0}
    if not date:
        return empty
    technicals = ingest.load_snapshot_technicals(date)
    if not technicals:
        return empty
    names = _names_map(date)
    positions = ingest.load_snapshot_positions(date)

    equity: set[str] = set()
    puts: set[str] = set()
    calls: set[str] = set()
    for p in positions:
        try:
            qty = float(p.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if p.get("assetType") != "OPTION" and qty > 0 and p.get("symbol"):
            equity.add(str(p["symbol"]).upper())
        elif p.get("assetType") == "OPTION" and qty < 0 and (p.get("underlying") or p.get("symbol")):
            und = str(p.get("underlying") or p.get("symbol")).upper()
            # Contract symbols look like NVDA_PUT_190_20260821 — parse side
            side = (p.get("type") or p.get("optionType") or "").upper()
            if not side and "_PUT_" in str(p.get("symbol", "")):
                side = "PUT"
            if side.startswith("P"):
                puts.add(und)
            else:
                calls.add(und)

    shown: set[str] = set()

    def _cards(tickers: list[str], *, include_skeletons: bool) -> list[dict]:
        cards = []
        for tk in tickers:
            if tk in shown:
                continue
            entry = technicals.get(tk)
            has_deep = isinstance(entry, dict) and isinstance(entry.get("deep"), dict)
            if not has_deep and not include_skeletons:
                continue
            shown.add(tk)
            cards.append(build_card(tk, entry, name=names.get(tk)))
        return cards

    groups = []
    for title, tks, skel in (
        ("Equity holdings", sorted(equity), True),
        ("Short-put underlyings", sorted(puts), True),
        ("Other option underlyings", sorted(calls), True),
        ("Candidates & watchlist", sorted(set(technicals) - shown - equity - puts - calls), False),
    ):
        cards = _cards(tks, include_skeletons=skel)
        if cards:
            groups.append({"title": title, "cards": cards})

    all_cards = [c for g in groups for c in g["cards"]]
    watch_universe = set(technicals) - shown
    return {
        "groups": groups,
        "count": len(all_cards),
        "deep_count": sum(1 for c in all_cards if c["available"]),
        # Watchlist names whose deep read wasn't fetched this cycle
        "skipped": len(watch_universe),
    }
