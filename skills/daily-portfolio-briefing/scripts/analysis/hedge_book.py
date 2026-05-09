"""Hedge book recommendation engine for portfolio protection."""

from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date


@dataclass
class HedgeRecommendation:
    """A recommended hedge instrument."""
    instrument: str  # SPY_PUT, QQQ_PUT, VIX_CALL
    target_strike: Decimal | None
    target_expiration: date | None
    target_delta: float  # negative for puts, positive for calls
    contracts: int
    estimated_cost: Decimal
    cost_pct_nlv: float
    coverage_pct: float
    rationale: str


@dataclass
class HedgeBook:
    """Current hedge state and recommendations."""
    current_coverage_pct: float  # % of long delta hedged
    target_coverage_pct: float
    current_hedges: list[dict] = field(default_factory=list)
    recommendations: list[HedgeRecommendation] = field(default_factory=list)
    total_recommended_cost: Decimal = Decimal("0")


def build_hedge_book(
    positions: list[dict],
    nlv: Decimal,
    long_delta: float = 0.0,
    macro_caution: str = "none",
    spy_price: Decimal | None = None,
) -> HedgeBook:
    """Build hedge recommendations.

    Args:
        positions: list of position dicts
        nlv: net liquidation value
        long_delta: total delta of long positions
        macro_caution: "none", "moderate", "high"
        spy_price: current SPY price for strike calculation
    """
    # Find existing hedges
    current_hedges = [
        p for p in positions
        if p.get("position_type") in ("long_put", "long_call")
    ]

    # Current hedge delta (simplified: long puts contribute negative delta)
    current_hedge_delta = sum(
        p.get("delta", 0) * abs(p.get("quantity", 0)) * 100
        for p in current_hedges
    )

    # Target coverage depends on macro state
    if macro_caution == "high":
        target_coverage = 0.30  # 30% of long delta hedged
    elif macro_caution == "moderate":
        target_coverage = 0.20  # 20%
    else:
        target_coverage = 0.10  # 10% — light, fat-tail only

    current_coverage = abs(current_hedge_delta) / long_delta if long_delta > 0 else 0.0

    # If coverage is adequate, no recommendations needed
    if current_coverage >= target_coverage:
        return HedgeBook(
            current_coverage_pct=current_coverage,
            target_coverage_pct=target_coverage,
            current_hedges=current_hedges,
            recommendations=[],
        )

    # Calculate recommended hedge
    recommendations = []
    needed_delta = max(0, long_delta * target_coverage - abs(current_hedge_delta))

    if needed_delta > 0 and spy_price and spy_price > 0:
        # Recommend SPY put for fat-tail protection
        strike = int(float(spy_price) * 0.95)  # 5% OTM
        # Each SPY put has delta ~ -0.20, so it cancels 20 shares of long delta per contract
        # times 100 multiplier = 2000 delta-shares per contract... wait that's wrong.
        # Per-contract delta-share offset: |delta| * 100 = 0.20 * 100 = 20 shares
        # So contracts needed = needed_delta / 20
        per_contract_delta_offset = 20.0  # |0.20| * 100
        contracts = max(1, int(needed_delta / per_contract_delta_offset))
        # Cost: roughly 1% of SPY price per put for ~30 DTE 5% OTM (a reasonable rule of thumb)
        cost_per_contract = max(0.5, float(spy_price) * 0.01) * 100
        estimated_cost = Decimal(str(cost_per_contract)) * Decimal(contracts)
        cost_pct = float(estimated_cost) / float(nlv) if nlv > 0 else 0.0
        coverage_pct = (contracts * per_contract_delta_offset) / long_delta if long_delta > 0 else 0.0

        # Target expiration: next monthly Friday roughly 30-45 DTE
        from datetime import timedelta
        today = date.today()
        target_exp = today + timedelta(days=35)
        # Snap to next Friday
        days_to_friday = (4 - target_exp.weekday()) % 7
        target_exp = target_exp + timedelta(days=days_to_friday)

        recommendations.append(HedgeRecommendation(
            instrument="SPY_PUT",
            target_strike=Decimal(str(strike)),
            target_expiration=target_exp,
            target_delta=-0.20,
            contracts=contracts,
            estimated_cost=estimated_cost,
            cost_pct_nlv=cost_pct,
            coverage_pct=coverage_pct,
            rationale=(
                f"Cover {coverage_pct:.0%} of long delta. SPY 5% OTM put at "
                f"~0.20 delta, ~35 DTE. Cost ~{cost_pct:.2%} of NLV."
            ),
        ))

    total_cost = sum(r.estimated_cost for r in recommendations)

    return HedgeBook(
        current_coverage_pct=current_coverage,
        target_coverage_pct=target_coverage,
        current_hedges=current_hedges,
        recommendations=recommendations,
        total_recommended_cost=total_cost,
    )
