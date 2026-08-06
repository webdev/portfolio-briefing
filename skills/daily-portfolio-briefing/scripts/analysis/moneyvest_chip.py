"""Moneyvest integration — 💰 MV chip, FV-divergence flag, index context
(task #46).

Data source: the moneyvest-fetcher skill's cache
(``state/cache/moneyvest.json`` at repo root), refreshed by run_briefing
Step 1.9 on a 20h TTL. Everything here is fail-open and n/a-safe: no cache
/ no entry for a ticker → no chip, never a fabricated number (hard rule
#19).

Attribution decoration (2026-08-06, user: "it's unclear what is Moneyvest
information and what is not — decorate it so it's clear"): ONE chip grammar
everywhere, from THIS module only —

    💰 MV: FV $152 · LB $196 · HB $160 · M 4.05

Missing fields are omitted (fail-closed, rule #19 — never a placeholder).
Any block that is WHOLLY Moneyvest-sourced (index sentiment line, regime
advisory) starts with ``💰 Moneyvest — ``. Strike-anchor notes route
through ``format_mv_anchor`` (``💰 MV Light Buy $182``). The one-line
source legend for the briefing header is ``source_legend_lines``.

Surfaces:
  - ``format_mv_chip`` / ``annotate_mv_chips`` — parkev_chip-pattern
    post-pass appending ``💰 MV: FV $152 · LB $196 · HB $160 · M 4.05``
    to every line already carrying the 🅿️ marker. ETF shopping-list rows
    get index-only treatment (no per-stock chip — an ETF has no company
    fair value).
  - FV divergence (hard-rule-#12 companion): when MV fair value and the
    FMP DCF on the SAME line diverge > 40% relative, append
    "⚠ models diverge (MV $152 vs DCF $242) — trust neither blindly".
  - ``index_context_lines`` — "💰 Moneyvest — sentiment: 3.82 OPTIMISTIC
    (S&P)" for the Market Context section, plus a contrarian-caution
    advisory on OPTIMISTIC/GREED/EUPHORIA readings. ADVISORY only —
    never a regime change.
"""

from __future__ import annotations

import re
from pathlib import Path

MV_MARK = "💰"
PARKEV_MARK = "🅿️"

_CONTRARIAN_LABELS = {"OPTIMISTIC", "GREED", "EUPHORIA"}
_DCF_RE = re.compile(r"DCF \$([0-9][0-9,]*(?:\.[0-9]+)?)")

DEFAULT_CACHE_PATH = (Path(__file__).resolve().parents[4]
                      / "state" / "cache" / "moneyvest.json")


def load_moneyvest(config: dict | None = None) -> dict:
    """Load the fetcher's cache. Empty payload on any failure (fail-open)."""
    import json
    cfg = ((config or {}).get("moneyvest") or {}) if isinstance(config, dict) else {}
    path = Path(cfg.get("cache_path") or DEFAULT_CACHE_PATH)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def mv_by_ticker(payload: dict | None) -> dict:
    """{TICKER: shopping-list row} — uppercased, ETF sections included
    (callers decide the ETF treatment)."""
    out: dict = {}
    for row in ((payload or {}).get("shopping_list") or []):
        t = (row.get("ticker") or "").upper()
        if t:
            out[t] = row
    return out


def _is_etf_row(row: dict | None) -> bool:
    return "etf" in str((row or {}).get("section", "")).lower()


def format_mv_chip(row: dict | None, m_score: float | None = None) -> str:
    """``💰 MV: FV $152 · LB $196 · HB $160 · M 4.05`` — the ONE Moneyvest
    chip grammar (2026-08-06 attribution decoration). n/a-safe, only
    measured parts; missing fields are omitted, never placeholder-filled
    (rule #19).

    Returns "" when there is nothing real to show (no chip beats a chip of
    fabricated values). ETF rows return "" — index-only treatment.
    ``NB`` (No-Brainer) renders only when neither LB nor HB is present
    (deepest ladder rung as last resort, keeps the chip short).
    """
    if _is_etf_row(row):
        return ""
    bits: list[str] = []
    if row:
        fv = row.get("fair_value")
        lb = row.get("light_buy")
        hb = row.get("heavy_buy")
        nb = row.get("no_brainer")
        if fv:
            bits.append(f"FV ${fv:,.0f}")
        if lb:
            bits.append(f"LB ${lb:,.0f}")
        if hb:
            bits.append(f"HB ${hb:,.0f}")
        if nb and not (lb or hb):
            bits.append(f"NB ${nb:,.0f}")
    if m_score is not None:
        bits.append(f"M {m_score:.2f}")
    if not bits:
        return ""
    return f"{MV_MARK} MV: " + " · ".join(bits)


def format_mv_anchor(label: str, price: float) -> str:
    """``💰 MV Light Buy $182`` — the ONE grammar for strike-anchor notes
    ("Strike anchored to …"). Single source of truth so every surface
    (new_ideas CSP composer, LT-opportunity advisor) decorates Moneyvest
    ladder anchors identically."""
    return f"{MV_MARK} MV {label} ${price:,.0f}"


def source_legend_lines() -> list[str]:
    """One-line source legend, rendered ONCE near the top of the briefing
    (Market Context). Explains every attribution mark so Moneyvest data is
    never confused with Parkev / FMP / Claude-portfolio data."""
    return [
        "_Sources: 🅿️ Parkev rec · 🤖 CP-held = Claude Portfolio · "
        "💰 MV = Moneyvest (FV fair value · LB/HB light/heavy buy · "
        "M M-Score) · 💵 FV = FMP DCF + analyst target_",
        "",
    ]


def divergence_note(mv_fair_value: float | None,
                    dcf: float | None,
                    threshold: float = 0.40) -> str | None:
    """'⚠ models diverge (MV $152 vs DCF $242) — trust neither blindly'
    when the two fair-value models diverge > ``threshold`` relative
    (symmetric: |mv − dcf| / mean)."""
    if not mv_fair_value or not dcf or mv_fair_value <= 0 or dcf <= 0:
        return None
    rel = abs(mv_fair_value - dcf) / ((mv_fair_value + dcf) / 2.0)
    if rel <= threshold:
        return None
    return (f"⚠ models diverge (MV ${mv_fair_value:,.0f} vs "
            f"DCF ${dcf:,.0f}) — trust neither blindly")


def annotate_mv_chips(md: str, payload: dict | None) -> str:
    """Post-pass over the rendered briefing (parkev_chip pattern):

    - Append the 💰 chip to every line already carrying the 🅿️ Parkev
      marker (same targeting discipline as tier badges — a chip-less
      header is something the chip annotator intentionally skipped).
    - On lines that ALSO carry an FMP ``DCF $N`` note, append the
      model-divergence warning when MV FV and DCF diverge > 40%.
    - Never double-annotates (lines already carrying 💰 are left alone).
    """
    if not md or not payload:
        return md
    rows = mv_by_ticker(payload)
    m_scores = {str(k).upper(): v
                for k, v in ((payload.get("m_scores") or {}).items())}
    if not rows and not m_scores:
        return md

    try:
        from analysis.parkev_chip import _extract_ticker
    except ImportError:
        from parkev_chip import _extract_ticker  # type: ignore

    out: list[str] = []
    for line in md.splitlines():
        if PARKEV_MARK not in line or MV_MARK in line:
            out.append(line)
            continue
        # Italic transparency footers / the source legend are prose, not
        # ticker headers — never chip them (mirrors the tier-badge guard).
        if line.lstrip().startswith("_"):
            out.append(line)
            continue
        ticker = _extract_ticker(line)
        if not ticker:
            out.append(line)
            continue
        t = ticker.upper()
        row = rows.get(t)
        chip = format_mv_chip(row, m_scores.get(t))
        extra = f" · {chip}" if chip else ""
        # FV divergence — only when this line carries the FMP DCF note.
        if row and row.get("fair_value"):
            m = _DCF_RE.search(line)
            if m:
                try:
                    dcf = float(m.group(1).replace(",", ""))
                except ValueError:
                    dcf = None
                note = divergence_note(row.get("fair_value"), dcf)
                if note:
                    extra += f" · {note}"
        out.append(line + extra if extra else line)
    joined = "\n".join(out)
    if md.endswith("\n") and not joined.endswith("\n"):
        joined += "\n"
    return joined


def index_context_lines(payload: dict | None) -> list[str]:
    """Market Context lines for the Moneyvest sentiment gauge.

    Returns [] when no index data (fail-open). Adds the contrarian-caution
    ADVISORY on OPTIMISTIC/GREED/EUPHORIA — flags a condition, never
    predicts direction and never changes the regime (hard rule #9)."""
    idx = ((payload or {}).get("index") or {})
    bits: list[str] = []
    labels: set[str] = set()
    for key, name in (("sp500", "S&P"), ("ndx", "NDX")):
        g = idx.get(key) or {}
        val, label = g.get("value"), g.get("label")
        if val is None and not label:
            continue
        val_txt = f"{val:.2f} " if isinstance(val, (int, float)) else ""
        bits.append(f"{val_txt}{label or '?'} ({name})")
        if label:
            labels.add(str(label).upper())
    if not bits:
        return []
    stale = ""
    if (payload or {}).get("provenance") == "stale_cache":
        sh = (payload or {}).get("stale_hours")
        stale = f" _(stale cache{f' {sh:.0f}h' if sh else ''})_"
    # Wholly-Moneyvest block → starts with the "💰 Moneyvest — " marker
    # (2026-08-06 attribution decoration).
    lines = [f"- {MV_MARK} Moneyvest — sentiment: **{' · '.join(bits)}**{stale}"]
    if labels & _CONTRARIAN_LABELS:
        lines.append(
            "  - ⚠ advisory: OPTIMISTIC/GREED readings are contrarian "
            "caution for NEW premium-selling — crowded optimism has "
            "historically preceded pullbacks; regime unchanged")
    return lines


def regime_advisory(payload: dict | None) -> str | None:
    """One-line advisory note for the regime classifier (no regime change)."""
    idx = ((payload or {}).get("index") or {})
    sp = idx.get("sp500") or {}
    label = str(sp.get("label") or "").upper()
    if not label:
        return None
    val = sp.get("value")
    val_txt = f"{val:.2f} " if isinstance(val, (int, float)) else ""
    note = (f"{MV_MARK} Moneyvest — sentiment {val_txt}{label} (S&P) — "
            f"advisory only")
    if label in _CONTRARIAN_LABELS:
        note += "; contrarian caution on new short-put opens"
    return note
