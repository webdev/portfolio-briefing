"""Moneyvest fair-value CONVICTION modulation — single source of truth.

USER DECISION (2026-08-06). George: "Do you take into consideration
money-vested fair value prices? What are you actually using it for, other
than just displaying it?" — he approved wiring exactly TWO Moneyvest
signals into recommendation logic and explicitly DECLINED M-Score gating
and sentiment gating. Do not add those here.

Feature 1 (this module): FV conviction modulation. Mirrors the
Claude-Portfolio agreement-bonus architecture
(``rotation_playbook.cp_agreement_bonus`` — a pure, config-gated function
returning ``(score_delta, visible_note)`` that callers add BESIDE the
Parkev-tier adjustments):

- spot ≤ MV_FV × (1 − min_discount) AND an existing buy catalyst →
  **+1 conviction** with visible note ``💰 MV FV $X — spot Y% below fair
  value``.
- spot > MV_FV → NEW equity ADD/BUY recs are DEMOTED (kind →
  ``SKIPPED_MV_FV`` — visible row with reason, hard rule #24, never
  hidden) and new-CSP conviction is CAPPED: no bonus, at most one notch
  down (**never below the floor existing logic allows** — the CSP itself
  stays actionable since assignment happens below spot).
- FMP-vs-MV divergence (>40% relative, the moneyvest_chip.divergence_note
  threshold) → forfeit ANY adjustment with note ``FV sources disagree —
  no FV conviction adjustment`` (uncertainty ≠ signal; the demotion is
  forfeited too — we trust neither model).
- Fail-closed: missing MV FV / ETF row (no company FV) / missing spot →
  delta 0, no note, never a fabricated number (hard rule #19).
- NEVER overrides the RSI gate, earnings gate, or any hard gate —
  conviction only. Position management (CLOSE/ROLL/TRIM/HEDGE/EXIT) is
  never touched by this module's callers.

Feature 2 helpers (Heavy-Buy scale-in ladder) live in
``moneyvest_chip.format_hb_ladder`` (grammar) and the per-surface callers;
this module only owns the config gates for both features.

Config (``briefing.yaml``)::

    moneyvest:
      fv_conviction: {enabled: true, min_discount_pct: 10}
      hb_ladder: {enabled: true}

Both flags default OFF in code → byte-identical legacy behavior when the
config keys are absent.
"""

from __future__ import annotations

# Relative FV-model divergence beyond which we trust neither model —
# matches analysis.moneyvest_chip.divergence_note's threshold.
DIVERGENCE_THRESHOLD = 0.40

DIVERGENCE_NOTE = "FV sources disagree — no FV conviction adjustment"

DEFAULT_MIN_DISCOUNT_PCT = 10.0


def _mv_cfg(config: dict | None, key: str) -> dict:
    mv = (config or {}).get("moneyvest") if isinstance(config, dict) else None
    mv = mv if isinstance(mv, dict) else {}
    sub = mv.get(key)
    return sub if isinstance(sub, dict) else {}


def fv_conviction_enabled(config: dict | None) -> bool:
    """Feature-1 gate. Default OFF (flags-off → legacy byte-identical)."""
    return bool(_mv_cfg(config, "fv_conviction").get("enabled", False))


def hb_ladder_enabled(config: dict | None) -> bool:
    """Feature-2 gate. Default OFF (flags-off → legacy byte-identical)."""
    return bool(_mv_cfg(config, "hb_ladder").get("enabled", False))


def _f(v) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def mv_fair_value_for(row: dict | None) -> float | None:
    """Company fair value from a Moneyvest shopping-list row. ETF rows
    return None — an ETF/basket has no company fair value (rule #19)."""
    if not isinstance(row, dict):
        return None
    if "etf" in str(row.get("section", "")).lower():
        return None
    return _f(row.get("fair_value"))


def fv_sources_diverge(mv_fair_value, dcf,
                       threshold: float = DIVERGENCE_THRESHOLD) -> bool:
    """True when the MV fair value and the FMP DCF diverge > ``threshold``
    relative (symmetric: |mv − dcf| / mean — same math as
    moneyvest_chip.divergence_note). Unknown either side → False (can't
    measure divergence → no forfeit, but also no fabricated flag)."""
    mv, d = _f(mv_fair_value), _f(dcf)
    if not mv or not d:
        return False
    return abs(mv - d) / ((mv + d) / 2.0) > threshold


def mv_fv_adjustment(spot, mv_fair_value,
                     fmp_divergence_flag: bool = False,
                     has_buy_catalyst: bool = False,
                     config: dict | None = None) -> tuple[float, str | None]:
    """The pure FV-conviction adjustment — CP-agreement-bonus pattern.

    Returns ``(delta, note)``:

    - ``(+1.0, "💰 MV FV $X — spot Y% below fair value")`` when spot is at
      least ``min_discount_pct`` below MV FV AND a buy catalyst exists.
      The bonus CORROBORATES an existing catalyst; a discount alone never
      qualifies a candidate (same discipline as the CP bonus).
    - ``(-1.0, "💰 spot above MV FV $X — conviction capped (no FV bonus)")``
      when spot > MV FV. This is the new-CSP conviction CAP — callers
      clamp so it never pushes a qualifying candidate below their floor,
      and the CSP stays actionable (assignment happens below spot).
      Equity-ADD surfaces use :func:`add_demotion_reason` instead.
    - ``(0.0, DIVERGENCE_NOTE)`` when ``fmp_divergence_flag`` and an
      adjustment would otherwise have fired (uncertainty ≠ signal).
    - ``(0.0, None)`` otherwise: feature disabled, missing FV/spot (fail-
      closed — never a fabricated number), or no condition met.

    Conviction only — never call this on position management, and never
    let the delta override the RSI / earnings / any hard gate.
    """
    if not fv_conviction_enabled(config):
        return 0.0, None
    s, fv = _f(spot), _f(mv_fair_value)
    if not s or not fv:
        return 0.0, None  # fail-closed: no MV FV / ETF / no spot

    min_disc = _mv_cfg(config, "fv_conviction").get("min_discount_pct",
                                                    DEFAULT_MIN_DISCOUNT_PCT)
    try:
        min_disc = float(min_disc)
    except (TypeError, ValueError):
        min_disc = DEFAULT_MIN_DISCOUNT_PCT

    if s <= fv * (1.0 - min_disc / 100.0):
        if fmp_divergence_flag:
            return 0.0, DIVERGENCE_NOTE
        if not has_buy_catalyst:
            return 0.0, None
        pct_below = (fv - s) / fv * 100.0
        return 1.0, f"💰 MV FV ${fv:,.0f} — spot {pct_below:.0f}% below fair value"

    if s > fv:
        if fmp_divergence_flag:
            return 0.0, DIVERGENCE_NOTE
        return -1.0, f"💰 spot above MV FV ${fv:,.0f} — conviction capped (no FV bonus)"

    return 0.0, None


def add_demotion_reason(spot, mv_fair_value,
                        fmp_divergence_flag: bool = False,
                        config: dict | None = None) -> str | None:
    """Reason string when a NEW equity ADD/BUY should be demoted to a
    SKIPPED/deferred row (rule #24 — visible, never hidden), else None.

    Fires only when spot > MV FV, the feature is enabled, and the two FV
    models do NOT diverge (divergence → trust neither → no demotion)."""
    if not fv_conviction_enabled(config):
        return None
    s, fv = _f(spot), _f(mv_fair_value)
    if not s or not fv or s <= fv:
        return None
    if fmp_divergence_flag:
        return None  # uncertainty ≠ signal — no demotion on disagreeing models
    return (f"spot ${s:,.2f} above 💰 MV FV ${fv:,.0f} — the valuation model "
            f"calls it rich; wait for a pullback toward FV before adding equity")


def apply_mv_fv_gate_to_lto(op_dicts: list, *, mv_rows: dict | None,
                            spots: dict | None,
                            recs_normalized: dict | None,
                            config: dict | None,
                            dcf_by_ticker: dict | None = None) -> list:
    """LTO-surface wiring (mutates ops in place, returns the list).

    - kind ``ADD`` above MV FV → kind → ``SKIPPED_MV_FV`` with visible
      ``skip_reason`` (rule #24 — renders in the Skipped section, never
      hidden).
    - kind ``LONG_DATED_CSP`` above MV FV → stays actionable; the cap
      note is inserted into ``trigger_reasons``.
    - either kind at ≥ min_discount below MV FV with a BUY catalyst →
      the +1 bonus note is inserted into ``trigger_reasons``.
    - every other kind (TRIM / EXIT / management) is untouched — the FV
      signal never fires on position management.
    - feature off / no MV rows → identity (byte-identical legacy).
    """
    if not fv_conviction_enabled(config) or not op_dicts:
        return op_dicts
    mv_rows = mv_rows or {}
    spots = spots or {}
    recs_normalized = recs_normalized or {}
    dcf_by_ticker = dcf_by_ticker or {}
    for op in op_dicts:
        kind = (op.get("kind") or "").upper()
        if kind not in ("ADD", "LONG_DATED_CSP"):
            continue
        t = (op.get("ticker") or "").upper()
        fv = mv_fair_value_for(mv_rows.get(t))
        spot = spots.get(t)
        if fv is None or not _f(spot):
            continue  # fail-closed — no note, no fabricated number
        diverge = fv_sources_diverge(fv, dcf_by_ticker.get(t))
        catalyst = str(recs_normalized.get(t) or "").upper() == "BUY"
        delta, note = mv_fv_adjustment(
            spot, fv, fmp_divergence_flag=diverge,
            has_buy_catalyst=catalyst, config=config)
        if kind == "ADD" and _f(spot) > fv and not diverge:
            op["skip_reason"] = add_demotion_reason(
                spot, fv, fmp_divergence_flag=diverge, config=config)
            op["kind_when_skipped"] = "ADD"
            op["kind"] = "SKIPPED_MV_FV"
            continue
        if note:
            op.setdefault("trigger_reasons", []).append(note)
            if delta > 0:
                op["mv_fv_bonus"] = delta
    return op_dicts
