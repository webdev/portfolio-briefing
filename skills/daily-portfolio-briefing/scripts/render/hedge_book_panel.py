"""Render hedge book panel with recommendations and scenarios."""

from decimal import Decimal
from analysis.hedge_book import HedgeBook


def render_hedge_book(hedge: HedgeBook, nlv: Decimal, spy_price: Decimal | None = None) -> list[str]:
    """Render hedge book panel with recommendations.

    Format:
      🛡️ HEDGE BOOK
        Current coverage: X% of long delta hedged (target Y%)
        Active hedges: [list or none]

        RECOMMEND ADD: [concrete ticket]
    """
    lines = ["## Hedge Book", ""]
    lines.append("🛡️ HEDGE PROTECTION")

    cov_pct = hedge.current_coverage_pct * 100
    target_pct = hedge.target_coverage_pct * 100

    cov_color = "✅" if cov_pct >= target_pct else "🟡"
    lines.append(
        f"  {cov_color} Current coverage: **{cov_pct:.0f}%** of long delta hedged "
        f"(target **{target_pct:.0f}%**)"
    )

    # Current hedges
    if hedge.current_hedges:
        hedge_desc = []
        for h in hedge.current_hedges:
            qty = abs(h.get("quantity", 0))
            symbol = h.get("symbol", "?")
            strike = h.get("strike", "?")
            opt_type = "P" if h.get("position_type") == "long_put" else "C"
            exp = h.get("expiration")
            if exp:
                exp_str = exp.strftime("%a %b %d '%y")
            else:
                exp_str = "?"
            delta = h.get("delta", 0.0)
            hedge_desc.append(f"{qty}x {symbol} ${strike}{opt_type} {exp_str} ({delta:+.2f}Δ)")
        lines.append(f"  Active hedges: {', '.join(hedge_desc)}")
    else:
        lines.append(f"  Active hedges: none")

    lines.append("")

    # Recommendations
    if hedge.recommendations:
        lines.append("  **RECOMMEND ADD:**")
        for rec in hedge.recommendations:
            label = rec.instrument.replace("_", " ").lower()
            if rec.target_strike and rec.target_expiration:
                exp_str = rec.target_expiration.strftime("%a %b %d '%y")
                opt_letter = "P" if rec.target_delta < 0 else "C"
                lines.append(
                    f"    • {label} **${rec.target_strike:.0f}{opt_letter}** {exp_str} — "
                    f"**{rec.contracts}x** — "
                    f"cost **${rec.estimated_cost:,.0f}** ({rec.cost_pct_nlv:.2%} NLV)"
                )
            lines.append(f"      Why: {rec.rationale}")
    else:
        lines.append("  ✅ No recommended additions — coverage at target.")

    lines.append("")
    return lines
