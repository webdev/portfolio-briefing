"""Expiration ladder analysis — detect concentration of expirations on single dates."""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class ExpirationCluster:
    """A date with significant expiration concentration."""
    expiration: date
    contract_count: int
    total_notional: float
    pct_of_nlv: float
    contracts: list[dict] = field(default_factory=list)


def analyze_expiration_ladder(
    positions: list[dict],
    nlv: float,
    concentration_threshold: float = 0.15,
) -> list[ExpirationCluster]:
    """Group option positions by expiration date; flag dates concentrating > 15% NLV."""
    clusters = {}

    for p in positions:
        if not p.get("expiration"):
            continue
        if p.get("position_type") not in ("short_put", "short_call", "long_put", "long_call"):
            continue

        exp = p.get("expiration")
        if exp not in clusters:
            clusters[exp] = {
                "contracts": [],
                "total_notional": 0.0,
            }

        # Notional: for options, strike × qty × 100
        qty = abs(p.get("quantity", 0))
        strike = p.get("strike", 0.0)
        notional = strike * qty * 100.0

        clusters[exp]["contracts"].append(p)
        clusters[exp]["total_notional"] += notional

    # Convert to sorted list of alerts for concentrated dates
    alerts = []
    for exp, data in clusters.items():
        pct = data["total_notional"] / nlv if nlv > 0 else 0.0
        if pct >= concentration_threshold:
            alerts.append(ExpirationCluster(
                expiration=exp,
                contract_count=len(data["contracts"]),
                total_notional=data["total_notional"],
                pct_of_nlv=pct,
                contracts=data["contracts"],
            ))

    alerts.sort(key=lambda a: a.expiration)
    return alerts
