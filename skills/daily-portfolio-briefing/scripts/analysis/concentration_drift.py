"""Concentration drift detection — flag names drifting above 10% NLV threshold.

CLAUDE.md hard rule #29 — Position Tier Framework: per-tier caps override
the global 10% cap. Tier A holdings (LT core compounders) are allowed up
to ~22% before triggering a warning — capping a conviction compounder at
10% fights the strategy. Tier B uses ~12%; Tier C uses ~8%. The legacy
8% / 10% bands remain the fallback when no tier config is present.
"""

from dataclasses import dataclass

try:
    from analysis import position_tiers
except ImportError:  # pragma: no cover — supports standalone use
    position_tiers = None  # type: ignore


@dataclass
class ConcentrationAlert:
    """A concentration warning for a name."""
    symbol: str
    current_pct: float
    severity: str  # "drift", "warning", "breach", "within_bounds"
    message: str
    tier: str | None = None        # 'A' | 'B' | 'C' | None (legacy)
    tier_cap_pct: float | None = None  # the cap actually applied (% NLV)


def detect_concentration_drift(
    positions: list[dict],
    nlv: float,
    warning_threshold: float = 0.08,
    breach_threshold: float = 0.10,
    config: dict | None = None,
) -> list[ConcentrationAlert]:
    """Flag names approaching or exceeding concentration caps.

    Tier-aware: when `config` carries a `position_tiers` block, each name's
    cap comes from `concentration_caps.tier_{a,b,c}_max_pct` instead of the
    global `warning_threshold` / `breach_threshold` defaults. A tier-A
    name at 18% NLV (where the cap is 22%) emits a `within_bounds` alert
    instead of a warning — the user knows the system saw the concentration
    and intentionally allowed it.

    Legacy callers (no `config`) get the original 6% drift / 8% warning /
    10% breach behavior — backward compatible.

    Severity bands when a tier cap applies:
      - within_bounds: > 50% of cap but ≤ cap (the user can see it's tracked)
      - warning:       > 80% of cap but ≤ cap
      - breach:        > cap

    Legacy bands (no tier):
      - drift:   > 6% but ≤ warning_threshold
      - warning: > warning_threshold but ≤ breach_threshold
      - breach:  > breach_threshold
    """
    alerts = []
    legacy_drift_threshold = 0.06
    tier_cfg_present = bool(
        config and isinstance(config, dict)
        and config.get("position_tiers")
        and position_tiers is not None
    )

    for p in positions:
        if p.get("position_type") != "long_stock":
            continue

        symbol = p.get("symbol", "?")
        market_value = p.get("market_value", 0.0)
        pct = market_value / nlv if nlv > 0 else 0.0

        if tier_cfg_present:
            tier = position_tiers.tier_for(symbol, config)  # type: ignore[union-attr]
            cap_pct = position_tiers.concentration_cap_for_tier(  # type: ignore[union-attr]
                tier, config
            )  # float percent (e.g. 22.0)
            cap_ratio = cap_pct / 100.0 if cap_pct else breach_threshold
            warning_ratio = cap_ratio * 0.80
            within_bounds_ratio = cap_ratio * 0.50

            if pct > cap_ratio:
                alerts.append(ConcentrationAlert(
                    symbol=symbol,
                    current_pct=pct,
                    severity="breach",
                    message=f"{symbol} {pct:.1%} — BREACH of Tier {tier} cap ({cap_pct:.0f}%)",
                    tier=tier,
                    tier_cap_pct=cap_pct,
                ))
            elif pct > warning_ratio:
                alerts.append(ConcentrationAlert(
                    symbol=symbol,
                    current_pct=pct,
                    severity="warning",
                    message=(
                        f"{symbol} {pct:.1%} — warning "
                        f"(approaching Tier {tier} cap {cap_pct:.0f}%)"
                    ),
                    tier=tier,
                    tier_cap_pct=cap_pct,
                ))
            elif pct > within_bounds_ratio:
                # Tier A's whole point is to ALLOW concentration — surface it
                # as an informational "tracked, within bounds" note rather
                # than a warning. Same shape for tier B/C so the renderer
                # can decide whether to show it (typically only for tier A).
                alerts.append(ConcentrationAlert(
                    symbol=symbol,
                    current_pct=pct,
                    severity="within_bounds",
                    message=(
                        f"Concentration check: {symbol} at {pct:.1%} "
                        f"(Tier {tier} cap {cap_pct:.0f}%) — within bounds"
                    ),
                    tier=tier,
                    tier_cap_pct=cap_pct,
                ))
            continue

        # ─── Legacy path (no tier config) — original 6/8/10 bands ───
        if pct > breach_threshold:
            alerts.append(ConcentrationAlert(
                symbol=symbol,
                current_pct=pct,
                severity="breach",
                message=f"{symbol} {pct:.1%} — BREACH of 10% NLV cap",
            ))
        elif pct > warning_threshold:
            alerts.append(ConcentrationAlert(
                symbol=symbol,
                current_pct=pct,
                severity="warning",
                message=f"{symbol} {pct:.1%} — warning (approaching 10% cap)",
            ))
        elif pct > legacy_drift_threshold:
            alerts.append(ConcentrationAlert(
                symbol=symbol,
                current_pct=pct,
                severity="drift",
                message=f"{symbol} {pct:.1%} — drifting upward",
            ))

    alerts.sort(key=lambda a: a.current_pct, reverse=True)
    return alerts
