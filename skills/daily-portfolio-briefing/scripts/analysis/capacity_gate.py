"""Universal capacity-gate DEFERRED tag (CLAUDE.md hard rule #41).

Origin: analyst audit 2026-07-03, finding #7 — new_ideas correctly blocked
new CSPs ("coverage 0.16x < 0.50x"), yet 4 actionable new short puts shipped
via the LTO / actions surfaces with no DEFERRED tag (MU + ZS LT_CSPs,
AMZN + META PULLBACK_CSPs).

The contract: when stress coverage sits below the capacity-gate floor
(default 0.50×), EVERY new-open recommendation surface must carry the
``⏸ Deferred (capacity gated)`` tag. The rec is NEVER hidden (hard rule #24 —
the user needs the full ticket to decide whether to rotate positions to make
room); it is TAGGED so it can't be mistaken for a green-lit trade.

Fail-open: when the coverage ratio can't be resolved (no analytics / no
gate_state / unparseable shape), no tag is emitted — a rec is never marked
capacity-gated on missing data.
"""

from __future__ import annotations

DEFERRED_TAG = "⏸ Deferred (capacity gated)"

_DEFAULT_MIN_COVERAGE = 0.50


def coverage_ratio_from(analytics) -> float | None:
    """Extract the stress-coverage ratio from the shapes callers carry.

    Accepted:
      - GateState / StressCoverage-like objects (``.coverage_ratio`` attr)
      - analytics dict: {"stress_coverage": StressCoverage | {"ratio": ...} |
        {"coverage_ratio": ...}}
      - bare dict: {"ratio": ...} or {"coverage_ratio": ...}

    Returns None when nothing resolvable is found (fail-open upstream).
    """
    if analytics is None:
        return None
    # Object with a coverage_ratio attribute (GateState, StressCoverage).
    attr = getattr(analytics, "coverage_ratio", None)
    if attr is not None:
        try:
            return float(attr)
        except (TypeError, ValueError):
            return None
    if not isinstance(analytics, dict):
        return None
    sc = analytics.get("stress_coverage", analytics)
    if sc is None:
        return None
    inner_attr = getattr(sc, "coverage_ratio", None)
    if inner_attr is not None:
        try:
            return float(inner_attr)
        except (TypeError, ValueError):
            return None
    if isinstance(sc, dict):
        for key in ("ratio", "coverage_ratio"):
            if sc.get(key) is not None:
                try:
                    return float(sc[key])
                except (TypeError, ValueError):
                    return None
    return None


def min_coverage_ratio(config: dict | None) -> float:
    """Read the capacity-gate floor: ``capacity_gates.min_coverage_ratio``
    with fallback to the existing ``min_stress_coverage_for_new_puts`` key,
    default 0.50."""
    block = {}
    if isinstance(config, dict):
        block = config.get("capacity_gates") or {}
    raw = block.get("min_coverage_ratio",
                    block.get("min_stress_coverage_for_new_puts",
                              _DEFAULT_MIN_COVERAGE))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MIN_COVERAGE


def capacity_deferred_tag(analytics, config: dict | None = None) -> str | None:
    """Return the DEFERRED tag string when the capacity gates are closed.

    Args:
        analytics: anything ``coverage_ratio_from`` understands (analytics
            dict, GateState, StressCoverage, plain dict).
        config: briefing config (reads ``capacity_gates.min_coverage_ratio``).

    Returns:
        The tag string (with the measured ratio, per "no hardcoded
        boilerplate" rule #19) when coverage < floor; None otherwise —
        callers simply append when non-None.
    """
    ratio = coverage_ratio_from(analytics)
    if ratio is None:
        return None
    floor = min_coverage_ratio(config)
    if ratio >= floor:
        return None
    return (
        f"{DEFERRED_TAG} — stress coverage {ratio:.2f}× < {floor:.2f}× floor; "
        f"shown for planning, not a green light (rule #41)"
    )
