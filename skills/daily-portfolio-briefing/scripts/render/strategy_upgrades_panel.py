"""Render strategy upgrades panel with covered strangles, collars, and sub-lot completions."""


def render_strategy_upgrades(upgrades: list[dict]) -> list[str]:
    """
    Render strategy upgrades panel.

    Format:
      ## Strategy Upgrades
        ### Covered Strangles (N)
        ### Collars (N)
        ### Sub-lot Completions (N)
    """

    lines = ["## Strategy Upgrades", ""]

    if not upgrades:
        lines.append("✅ No strategy upgrades available at this time.")
        lines.append("")
        return lines

    # Partition by type
    strangles = [u for u in upgrades if u.get("type") == "covered_strangle"]
    collars = [u for u in upgrades if u.get("type") == "collar"]
    new_ccs = [u for u in upgrades if u.get("type") == "write_covered_call"]
    sublots = [u for u in upgrades if u.get("type") == "sublot_completion"]

    # === NEW COVERED CALLS ===
    if new_ccs:
        from datetime import date, timedelta
        lines.append(f"### Write Covered Calls ({len(new_ccs)})")
        lines.append("")
        for cc in new_ccs:
            symbol = cc.get("underlying")
            shares = cc.get("shares_held")
            contracts = cc.get("contracts_writable")
            price = cc.get("current_price")
            strike = cc.get("target_strike")
            dte = cc.get("target_dte", 35)
            premium_per_share = cc.get("est_premium_per_share")
            premium_total = cc.get("est_premium_total")
            annualized = cc.get("est_annualized_pct")
            weight = cc.get("current_weight_pct")
            earnings_blocked = cc.get("earnings_blocked")
            earnings_date = cc.get("earnings_date")
            bid = cc.get("bid") or 0
            ask = cc.get("ask") or 0
            chain_source = cc.get("chain_source", "estimate_broker_unreachable")
            target_exp = (date.today() + timedelta(days=dte)).strftime("%a %b %d '%y")

            badge = "⛔ DEFER (earnings inside window)" if earnings_blocked else "✅ READY TO WRITE"
            lines.append(f"**{symbol} — {int(shares)} shares (no CC yet, {weight:.1f}% NLV)** {badge}")
            lines.append(
                f"  - SELL {contracts}× {symbol} ${strike:g}C exp ~{target_exp} "
                f"({dte} DTE, ~6% OTM, ~0.30 delta)"
            )
            if earnings_blocked:
                lines.append(
                    f"  - ⚠ Earnings on {earnings_date} — defer writing until after the print "
                    f"to avoid binary gap risk."
                )
            elif chain_source == "etrade_live" and bid and ask:
                spread_pct = ((ask - bid) / premium_per_share * 100) if premium_per_share else 0
                lines.append(
                    f"  - Premium: ${premium_per_share:.2f}/share (bid ${bid:.2f} / mid "
                    f"${premium_per_share:.2f} / ask ${ask:.2f}, spread {spread_pct:.0f}%) "
                    f"× 100 × {contracts} = **${premium_total:,.0f}** (~{annualized:.0f}% annualized)"
                )
                lines.append(f"  - **Source:** Live E*TRADE chain")
            else:
                lines.append(
                    f"  - Est. premium: ${premium_per_share:.2f}/share × 100 × {contracts} = "
                    f"**${premium_total:,.0f}** (~{annualized:.0f}% annualized)"
                )
                lines.append(
                    f"  - ⚠ E*TRADE chain unreachable — premium is rule-of-thumb estimate. "
                    f"Verify live bid/ask at broker before placing."
                )
            lines.append("")

    # === COVERED STRANGLES ===
    if strangles:
        lines.append(f"### Covered Strangles ({len(strangles)})")
        lines.append("")

        for s in strangles:
            symbol = s.get("underlying")
            current_calls = s.get("current_calls")
            proposed = s.get("proposed", {})
            strike = proposed.get("strike")
            qty = proposed.get("qty")
            exp = proposed.get("expiration")
            delta = proposed.get("delta")
            premium_per = proposed.get("premium_per_contract")
            total_premium = proposed.get("total_premium")
            yield_ann = s.get("yield_annualized")
            collateral = s.get("collateral_required")
            combined = s.get("combined_income", {})
            call_total = combined.get("calls")
            put_total = combined.get("puts")
            total_combined = combined.get("total")
            conc = s.get("concentration_check", {})
            blocked = conc.get("blocked")
            reason = conc.get("reason")
            rationale = s.get("rationale")

            # Format expiration with weekday
            try:
                from datetime import datetime
                exp_date = datetime.strptime(exp, "%Y-%m-%d")
                exp_fmt = exp_date.strftime("%a %b %d '%y")
            except (ValueError, TypeError):
                exp_fmt = exp

            status = "⛔ BLOCKED" if blocked else "✅ OK"

            lines.append(f"**{symbol} — {current_calls} → add {qty}x ${strike:.0f}P** {status}")

            if blocked:
                lines.append(f"  - Current: {conc.get('current_pct', 0):.0f}% NLV | Post-action: {conc.get('post_action_pct', 0):.0f}% NLV")
                lines.append(f"  - **BLOCKED**: {reason}")
            else:
                lines.append(f"  - Add {qty}× ${strike:.0f}P exp {exp_fmt} (28d) @ ${premium_per:.2f} mid (δ{delta:+.2f})")
                lines.append(f"  - New premium: ${put_total:,.0f} ({yield_ann:.0%} ann on ${collateral:,.0f})")
                lines.append(f"  - Combined: calls ${call_total:,.0f} + puts ${put_total:,.0f} = **${total_combined:,.0f} total**")

            lines.append("")

    # === COLLARS ===
    if collars:
        lines.append(f"### Collars ({len(collars)})")
        lines.append("")

        for c in collars:
            symbol = c.get("underlying")
            shares = c.get("shares_held")
            unrealized = c.get("current_unrealized_gain")
            gain_pct = c.get("gain_pct")
            proposed = c.get("proposed_put", {})
            strike = proposed.get("strike")
            qty = proposed.get("qty")
            exp = proposed.get("expiration")
            delta = proposed.get("delta")
            cost_per = proposed.get("cost_per_contract")
            total_cost = proposed.get("total_cost")
            call_offset = c.get("call_offset")
            net_cost = c.get("net_collar_cost")
            max_loss = c.get("max_loss_from_current")
            scenario = c.get("scenario_minus_20pct", {})
            rationale = c.get("rationale")

            # Format expiration
            try:
                from datetime import datetime
                exp_date = datetime.strptime(exp, "%Y-%m-%d")
                exp_fmt = exp_date.strftime("%a %b %d '%y")
            except (ValueError, TypeError):
                exp_fmt = exp

            # Net-zero indicator
            net_zero_badge = "🆓 NET-ZERO" if net_cost <= 10 else ""

            lines.append(f"**{symbol} — {shares} shares, +${unrealized:,.0f} unrealized (+{gain_pct:.0f}%)** {net_zero_badge}")

            if call_offset > 0:
                lines.append(f"  - Buy {qty}× ${strike:.0f}P exp {exp_fmt} (28d) @ ${cost_per:.2f} mid (δ{delta:+.2f})")
                lines.append(f"  - Cost: ${total_cost:,.0f} | Existing CC premium offsets: ${call_offset:,.0f} → **net cost: ${net_cost:,.0f}**")
            else:
                lines.append(f"  - Buy {qty}× ${strike:.0f}P exp {exp_fmt} (28d) @ ${cost_per:.2f} mid (δ{delta:+.2f})")
                lines.append(f"  - Cost: ${total_cost:,.0f} ({(total_cost / (shares * proposed.get('strike', 1)) * 100):.1f}% annual drag on position)")

            lines.append(f"  - Floor: ${strike:.0f} ({((strike - proposed.get('strike', strike)) / (shares * proposed.get('strike', strike)) * 100):.0f}% below current) → locks in ${unrealized - max_loss:,.0f} gain")
            lines.append(f"  - If {symbol} drops 20%: without collar **${scenario.get('gain_without_collar', 0):,.0f}** | with collar **${scenario.get('gain_with_collar', 0):,.0f}** (saves **${scenario.get('saves', 0):,.0f}**)")

            lines.append("")

    # === SUB-LOT COMPLETIONS ===
    if sublots:
        lines.append(f"### Sub-lot Completions ({len(sublots)})")
        lines.append("")

        for sub in sublots:
            symbol = sub.get("underlying")
            held = sub.get("shares_held")
            to_buy = sub.get("shares_to_buy")
            price = sub.get("current_price")
            cost = sub.get("cost")
            post_weight = sub.get("post_buy_weight_pct")

            lines.append(f"**{symbol} — {held} shares (need {to_buy} more)**")
            lines.append(f"  - Current: {held} shares @ ${price:.2f} = ${held * price:,.0f} ({held / 100 * 100:.0f}% of lot)")
            lines.append(f"  - Buy {to_buy} more = ${cost:,.0f} → {held + to_buy}-share lot ({post_weight:.1f}% NLV post-buy)")

            # Add income projection for completed lot covered calls
            completed_lot_value = (held + to_buy) * price
            # Estimate covered call premium at 0.30 delta, 30 DTE (typically 2-4% of stock price)
            estimated_call_premium_pct = 0.025  # Conservative 2.5% estimate
            estimated_monthly_income = completed_lot_value * estimated_call_premium_pct
            estimated_annualized = estimated_monthly_income * 12

            lines.append(f"  - After completion → enable 1× covered call writing")
            lines.append(f"  - Est. income: ~${estimated_monthly_income:,.0f}/mo (0.30Δ 30DTE) = **${estimated_annualized:,.0f}/yr** (~{estimated_annualized/completed_lot_value*100:.1f}% annualized)")

            lines.append("")

    return lines
