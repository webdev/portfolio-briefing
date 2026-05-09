"""Concentration drift detection — flag names drifting above 10% NLV threshold."""

from dataclasses import dataclass


@dataclass
class ConcentrationAlert:
    """A concentration warning for a name."""
    symbol: str
    current_pct: float
    severity: str  # "drift", "warning", "breach"
    message: str


def detect_concentration_drift(
    positions: list[dict],
    nlv: float,
    warning_threshold: float = 0.08,
    breach_threshold: float = 0.10,
) -> list[ConcentrationAlert]:
    """Flag names approaching or exceeding concentration caps.

    - drift: > 6% but < warning_threshold
    - warning: warning_threshold to breach_threshold
    - breach: > breach_threshold
    """
    alerts = []
    drift_threshold = 0.06

    for p in positions:
        if p.get("position_type") != "long_stock":
            continue

        symbol = p.get("symbol", "?")
        market_value = p.get("market_value", 0.0)
        pct = market_value / nlv if nlv > 0 else 0.0

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
        elif pct > drift_threshold:
            alerts.append(ConcentrationAlert(
                symbol=symbol,
                current_pct=pct,
                severity="drift",
                message=f"{symbol} {pct:.1%} — drifting upward",
            ))

    alerts.sort(key=lambda a: a.current_pct, reverse=True)
    return alerts
