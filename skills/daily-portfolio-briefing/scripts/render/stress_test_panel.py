"""Render stress test panel — assignment exposure across market drop scenarios."""

from decimal import Decimal
from analysis.stress_coverage import StressCoverage


def render_stress_test(coverage: StressCoverage, nlv: Decimal) -> list[str]:
    """Render the stress test panel showing assignment exposure at different drops.

    Format:
      🔻 STRESS TEST — assignment exposure if market drops
        Symbol 1x $strike exp-date OTM% $ notional (% of NLV)
        ...
        Total put obligation: $X | Cash: $Y | Coverage: Z.Xx
        -10%: assigned $X → cash remaining | stocks -$Y | NLV ~$Z [SHORTFALL]
    """
    lines = ["## Stress Test", ""]
    lines.append("🔻 ASSIGNMENT EXPOSURE — if market drops")
    lines.append("")

    if not coverage or coverage.total_put_obligations == 0:
        lines.append("No short puts to stress-test.")
        lines.append("")
        return lines

    # Summary line
    cov_color = "✅" if coverage.coverage_ratio >= coverage.target_ratio else "⚠️"
    lines.append(
        f"  Total put obligation: **${coverage.total_put_obligations:,.0f}** | "
        f"Cash: **${coverage.cash:,.0f}** | Coverage: **{coverage.coverage_ratio:.2f}x** {cov_color}"
    )
    lines.append("")

    # Scenario table
    lines.append("  SPY drop | assignments | cash after | stock loss | NLV after")
    lines.append("  ────────────────────────────────────────────────────────────")

    for drop_pct in (0.10, 0.20, 0.30):
        if drop_pct not in coverage.drops:
            continue
        scenario = coverage.drops[drop_pct]

        status = "[SHORTFALL]" if scenario.is_shortfall else "[OK]"
        stock_loss = scenario.stock_loss if hasattr(scenario, 'stock_loss') else 0
        lines.append(
            f"  {drop_pct:>4.0%}    | "
            f"${scenario.assigned_obligations:>10,.0f} | "
            f"${scenario.cash_after:>10,.0f} | "
            f"-${stock_loss:>10,.0f} | "
            f"${scenario.nlv_after:>10,.0f} {status}"
        )

    lines.append("")
    return lines


def render_stress_test_details(coverage: StressCoverage, positions: list = None) -> list[str]:
    """Render detailed assigned symbols and close candidates for each scenario.

    Args:
        coverage: StressCoverage with drop scenarios
        positions: Optional list of position dicts to enrich symbol details with strike/expiration

    Format:
        At -10% drop, would assign:
          • AAPL 2x $170P exp 2026-06-18 (collateral $34,000)
          • MSFT 1x $430C exp 2026-06-25 (collateral $43,000)
    """
    lines = []

    # Build a lookup map: symbol → position details for quick enrichment
    position_map = {}
    if positions:
        for p in positions:
            if p.get("assetType") == "OPTION":
                underlying = p.get("underlying", p.get("symbol", ""))
                strike = p.get("strike", "?")
                exp = p.get("expiration", "?")
                opt_type = p.get("type", "?")
                key = f"{underlying}_{opt_type}_{strike}"
                if key not in position_map:
                    position_map[key] = []
                position_map[key].append(p)

    for drop_pct in (0.10, 0.20, 0.30):
        if drop_pct not in coverage.drops:
            continue
        scenario = coverage.drops[drop_pct]

        if scenario.assigned_symbols:
            lines.append(f"**At −{drop_pct:.0%} drop, would assign:**")
            for sym_str in scenario.assigned_symbols:
                # sym_str format: "AAPL 2x" or "MSFT 1x"
                parts = sym_str.split()
                symbol = parts[0] if parts else "?"
                contract_str = parts[1] if len(parts) > 1 else "1x"

                # Try to find matching position details
                detail_str = f"  • {sym_str}"
                for key, pos_list in position_map.items():
                    if key.startswith(symbol + "_"):
                        for p in pos_list:
                            strike = p.get("strike", "?")
                            exp = p.get("expiration", "?")
                            opt_type = p.get("type", "?")
                            qty = abs(p.get("qty", 0))
                            # Match by checking if contract_str qty matches position qty
                            contract_qty_str = contract_str.replace("x", "").strip()
                            try:
                                contract_qty = int(contract_qty_str)
                            except ValueError:
                                contract_qty = 1

                            if qty == contract_qty:
                                # Format: AAPL 2x $170P exp 2026-06-18 (collateral $34,000)
                                collateral = Decimal(strike) * Decimal(qty) * Decimal(100) if strike != "?" else Decimal(0)
                                detail_str = (
                                    f"  • {symbol} {contract_qty}x ${strike:g}{opt_type[:1]} "
                                    f"exp {exp} (collateral ${collateral:,.0f})"
                                )
                                break

                lines.append(detail_str)
            lines.append("")

    # Top 3 close candidates
    if coverage.recommended_closes:
        lines.append("**Top 3 Closes to Raise Coverage:**")
        for rec in coverage.recommended_closes[:3]:
            lines.append(
                f"  • {rec.symbol} — {rec.reason}"
            )

    return lines
