"""Re-exports the briefing pipeline's chip formatters.

Hard rule #27: the Parkev chip on every line in the briefing markdown
MUST look identical in the web app. We import the pipeline's helpers
directly rather than re-implement them, so any change to the chip
vocabulary propagates automatically.

Hard rule #29: every ticker line carries a tier badge (🟢/🟡/🔵 Tier A/B/C).
Tier classification depends on briefing.yaml.

M2 fix (issue #3): chip rendering is now DATE-aware. The web app must
show the rating that was in effect ON the briefing date, not today's
rating. ``parkev_chip_renderer(date)`` returns a renderer that closes
over that specific date's recommendations_list.json. ``tier_badge``
remains date-independent (tier classification is config-driven and
doesn't change day-over-day).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Callable

from .config import _ensure_pipeline_on_path, briefing_config

_ensure_pipeline_on_path()

from analysis.parkev_chip import format_parkev_chip, parkev_chip_for_ticker  # type: ignore  # noqa: E402
from analysis.position_tiers import (  # type: ignore  # noqa: E402
    tier_for,
    format_tier_badge,
    is_cc_enabled_for_tier,
    concentration_cap_for_tier,
)


__all__ = [
    "format_parkev_chip",
    "parkev_chip_for_ticker",
    "tier_for",
    "format_tier_badge",
    "is_cc_enabled_for_tier",
    "concentration_cap_for_tier",
    "tier_badge_for_ticker",
    "parkev_chip_renderer",
    "tier_badge_renderer",
    "recs_map_for_date",
]


def tier_badge_for_ticker(ticker: str) -> str:
    """Convenience: tier_for(ticker, config) → format_tier_badge."""
    cfg = briefing_config()
    return format_tier_badge(tier_for(ticker, cfg))


# ─── Date-aware recommendation lookup (M2 issue #3) ─────────────────


@lru_cache(maxsize=256)
def recs_map_for_date(date: str | None) -> tuple[tuple[str, ...], ...]:
    """Load the recommendations_list.json for one snapshot date and
    return a tuple-of-tuples (so the lru_cache key is hashable).

    Returns an empty tuple when no recs exist for that date — caller
    must handle the empty-rec case (chip renders as `🅿️ no rec`).

    Cached by date — repeated lookups within one request are free.
    Cleared by `clear_recs_cache()` after `POST /refresh`.
    """
    if not date:
        return ()
    # Import here to avoid circular module load at startup
    from . import ingest

    recs = ingest.load_snapshot_recs(date)
    if not recs:
        return ()
    # Freeze each rec into a hashable shape: (ticker, json.dumps(rec))
    # but we actually want to return the full dict back to the caller,
    # so use a tuple of (ticker, frozen_items) and reconstruct in
    # the renderer. Simpler: keep as (ticker, dict_id) tuple alongside
    # a module-level non-cached store keyed by date.
    return _store_recs(date, recs)


_RECS_STORE: dict[str, dict[str, dict]] = {}


def _store_recs(date: str, recs: list[dict]) -> tuple[tuple[str, ...], ...]:
    """Store recs in module-level map and return a frozen reference tuple."""
    m: dict[str, dict] = {}
    for r in recs:
        tk = (r.get("ticker") or "").upper()
        if tk:
            m[tk] = r
    _RECS_STORE[date] = m
    # The cache key result is just a marker tuple — we never use its contents.
    return (("__stored__", date),)


def _lookup_rec(date: str | None, ticker: str) -> dict | None:
    """Return the rec dict for (date, ticker), or None."""
    if not date or not ticker:
        return None
    # Trigger cache + store
    recs_map_for_date(date)
    return _RECS_STORE.get(date, {}).get(ticker.upper())


def clear_recs_cache() -> None:
    """Invalidate every cached recs map. Called by the refresh job after
    the pipeline writes new snapshots so the next render sees fresh data."""
    recs_map_for_date.cache_clear()
    _RECS_STORE.clear()


def parkev_chip_renderer(date: str | None = None) -> Callable[[str], str]:
    """Return a function `(ticker) -> safe HTML chip` for one specific date.

    When `date` is None, the renderer falls back to "no rec" for every
    ticker — useful for unit tests or pages with no date context.

    The HTML is escaped/safe — Jinja `| safe` filter is expected at the
    template boundary, but we don't emit any user-controlled HTML here.
    """
    def _render(ticker: str) -> str:
        if not ticker:
            return ""
        rec = _lookup_rec(date, ticker)
        chip_text = format_parkev_chip(rec)
        return f'<span class="chip chip-parkev">{chip_text}</span>'

    return _render


def tier_badge_renderer() -> Callable[[str], str]:
    """Return the tier-badge renderer. Tier is config-driven, not
    date-driven, so this doesn't take a `date` argument."""
    def _render(ticker: str) -> str:
        if not ticker:
            return ""
        badge = tier_badge_for_ticker(ticker)
        letter = "C"
        for L in ("A", "B", "C"):
            if f"Tier {L}" in badge:
                letter = L
                break
        return f'<span class="chip tier-{letter}">{badge}</span>'

    return _render
