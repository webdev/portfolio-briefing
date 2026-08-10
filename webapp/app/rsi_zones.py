"""RSI zone classification — CSP range vs covered-call range, at a glance.

George (2026-08-10): "We need to add filtering, probably to the UI, to all
the companies, basically RSI filtering. I want to see what is within the
CSP range and what is within the covered call range, so I can clearly see
it at a glance."

Single source of truth for the webapp's RSI zone tags. Band bounds are NOT
defined here — they are imported from the PIPELINE's rsi_discipline
(skills/daily-portfolio-briefing/scripts/analysis/rsi_discipline.py) merged
with briefing.yaml's ``rsi_discipline`` section via the existing config
bridge (same pattern as chips.py). Any band change in the pipeline
propagates automatically; templates and JS only consume what the backend
emits (never hardcode 35/55/60/70/25 downstream).

Zones are NON-EXCLUSIVE TAGS, not exclusive buckets — RSI 72 is in
``cc_zone`` (covered-call writes still favored when extended) AND
``blocked_hot`` (no new puts/buys). The tag set (standard wheel bands):

    csp_zone       35 ≤ RSI ≤ 55   favored cash-secured-put entry band
                                    (pipeline ``put_entry_band``, rule #44)
    cc_zone        RSI ≥ 60         favored covered-call write zone
                                    (``call.favored_above``)
    wait_zone      55 < RSI < 60    between the two favored bands, or
                   25 ≤ RSI < 35    oversold-but-not-knife
    blocked_hot    RSI > 70         overbought — no new puts or buys
                                    (``put.block_above``; CC still favored)
    blocked_cold   RSI < 35         oversold — no new covered calls
                                    (``call.block_below``)
    falling_knife  RSI < 25         momentum break (``put.falling_knife_below``)
    unknown        no RSI           visible under "All" only

Every function is pure over ``bands()`` so boundaries are testable, and the
drift test (tests/test_rsi_zone_filter.py) pins our band values against the
pipeline's to prevent divergence.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from .config import _ensure_pipeline_on_path, briefing_config

_ensure_pipeline_on_path()

from analysis import rsi_discipline  # type: ignore  # noqa: E402


# Stable tag order (also the order data-rsi-zones attributes are emitted in).
ZONE_TAGS = (
    "csp_zone", "cc_zone", "wait_zone",
    "blocked_hot", "blocked_cold", "falling_knife",
)


@lru_cache(maxsize=1)
def bands() -> dict[str, float]:
    """Band bounds from the pipeline's rsi_discipline merged with
    briefing.yaml. Values with the standard wheel config:
    csp 35-55 · cc ≥60 · put block >70 · cc block <35 · knife <25."""
    th = rsi_discipline.load_thresholds(briefing_config())
    entry = th.get("put_entry_band") or [35.0, 55.0]
    return {
        "csp_low": float(entry[0]),
        "csp_high": float(entry[1]),
        "cc_favored": float(th["call"]["favored_above"]),
        "put_block": float(th["put"]["block_above"]),
        "cc_block": float(th["call"]["block_below"]),
        "falling_knife": float(th["put"]["falling_knife_below"]),
    }


def zones_for(rsi: float | None) -> list[str]:
    """Non-exclusive zone tags for an RSI value. None → ["unknown"]."""
    if rsi is None:
        return ["unknown"]
    try:
        r = float(rsi)
    except (TypeError, ValueError):
        return ["unknown"]
    b = bands()
    tags: list[str] = []
    if b["csp_low"] <= r <= b["csp_high"]:
        tags.append("csp_zone")
    if r >= b["cc_favored"]:
        tags.append("cc_zone")
    if (b["csp_high"] < r < b["cc_favored"]) or (b["falling_knife"] <= r < b["csp_low"]):
        tags.append("wait_zone")
    if r > b["put_block"]:
        tags.append("blocked_hot")
    if r < b["cc_block"]:
        tags.append("blocked_cold")
    if r < b["falling_knife"]:
        tags.append("falling_knife")
    return tags or ["unknown"]


def zones_attr(rsi: float | None) -> str:
    """Space-separated tag string for the data-rsi-zones attribute."""
    return " ".join(zones_for(rsi))


# ─── Humanized badge (rule #31 — never leak raw zone identifiers) ─────

def _zone_meta() -> dict[str, dict[str, str]]:
    """Humanized label + long title per zone, band numbers from bands()."""
    b = bands()
    return {
        "blocked_hot": {
            "label": "🔥 Overbought",
            "tone": "red", "rep_tone": "red",
            "title": (
                f"RSI > {b['put_block']:.0f} — overbought: no new puts or "
                "buys (chasing); covered-call writes still favored."
            ),
        },
        "falling_knife": {
            "label": "🧊 Falling knife",
            "tone": "blue", "rep_tone": "blue",
            "title": (
                f"RSI < {b['falling_knife']:.0f} — momentum break; wait for "
                "stabilization. No new covered calls."
            ),
        },
        "blocked_cold": {
            "label": "🧊 Oversold",
            "tone": "blue", "rep_tone": "blue",
            "title": (
                f"RSI < {b['cc_block']:.0f} — no new covered calls; put "
                f"entries favored from the {b['csp_low']:.0f}-"
                f"{b['csp_high']:.0f} band."
            ),
        },
        "cc_zone": {
            "label": "📞 CC zone",
            "tone": "orange", "rep_tone": "amber",
            "title": (
                f"RSI ≥ {b['cc_favored']:.0f} — favored covered-call write "
                "zone; new put-sales wait for a pullback."
            ),
        },
        "csp_zone": {
            "label": "🎯 CSP zone",
            "tone": "green", "rep_tone": "green",
            "title": (
                f"RSI {b['csp_low']:.0f}-{b['csp_high']:.0f} — favored "
                "cash-secured-put entry band."
            ),
        },
        "wait_zone": {
            "label": "⏸ Wait",
            "tone": "neutral", "rep_tone": "muted",
            "title": (
                f"RSI between the CSP band (≤{b['csp_high']:.0f}) and the "
                f"CC band (≥{b['cc_favored']:.0f}) — no favored side."
            ),
        },
    }


# Priority for picking the ONE badge shown on a card (the most decision-
# relevant tag wins; the data attribute still carries every tag).
_BADGE_PRIORITY = (
    "blocked_hot", "falling_knife", "blocked_cold",
    "cc_zone", "csp_zone", "wait_zone",
)


def zone_badge(rsi: float | None) -> dict[str, str] | None:
    """At-a-glance badge dict for a card: {zone, label, text, tone,
    rep_tone, title}. ``text`` is the full 'RSI 47 · 🎯 CSP zone' form for
    surfaces that don't already show an RSI chip. None when RSI is
    unknown (no badge — never a fabricated read, rule #19)."""
    tags = zones_for(rsi)
    if tags == ["unknown"]:
        return None
    meta = _zone_meta()
    for zone in _BADGE_PRIORITY:
        if zone in tags:
            m = meta[zone]
            return {
                "zone": zone,
                "label": m["label"],
                "text": f"RSI {float(rsi):.0f} · {m['label']}",
                "tone": m["tone"],
                "rep_tone": m["rep_tone"],
                "title": m["title"],
            }
    return None


def filter_chips() -> list[dict[str, str]]:
    """Chip definitions for the filter bar (All first). Labels humanized,
    titles carry the band bounds from the backend — templates/JS never
    hardcode bands."""
    meta = _zone_meta()
    chips = [{"zone": "all", "label": "All", "title": "Show every card (including cards with no RSI)"}]
    for zone in ("csp_zone", "cc_zone", "wait_zone", "blocked_hot", "blocked_cold"):
        m = meta[zone]
        chips.append({"zone": zone, "label": m["label"], "title": m["title"]})
    return chips


# ─── Card-level helpers (report cards + unified cards) ────────────────

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _metrics_rsi(card: Any) -> float | None:
    """Pull a numeric RSI out of a parsed report card's metrics chips
    (label 'RSI', value like '45' or '45 🟢 pullback')."""
    if not isinstance(card, dict):
        return None
    for m in card.get("metrics") or []:
        if isinstance(m, dict) and str(m.get("label", "")).strip().upper() == "RSI":
            match = _NUM_RE.search(str(m.get("value", "")))
            if match:
                try:
                    return float(match.group(0))
                except ValueError:
                    return None
    return None


def card_rsi(tech: Any, card: Any = None) -> float | None:
    """Best-available RSI for a card surface: the tech view-model's
    measured value first, else the report card's own RSI metric chip.
    None when neither carries one (→ 'unknown', card shows no badge)."""
    if isinstance(tech, dict) and tech.get("rsi") is not None:
        try:
            return float(tech["rsi"])
        except (TypeError, ValueError):
            pass
    return _metrics_rsi(card)


def card_zones_attr(tech: Any, card: Any = None) -> str:
    return zones_attr(card_rsi(tech, card))


def card_zone_badge(tech: Any, card: Any = None) -> dict[str, str] | None:
    return zone_badge(card_rsi(tech, card))


def register_jinja_globals(env) -> None:
    """Expose the helpers templates need (report_view card macro + the
    shared rsi_filter_bar macro)."""
    env.globals["rsi_filter_chips"] = filter_chips
    env.globals["rsi_card_zones"] = card_zones_attr
    env.globals["rsi_card_badge"] = card_zone_badge
