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

    # RSI hook: anything the gate removed is held out of the actionable lists
    # and surfaced in a footer instead. Favored names are promoted (✅ badge).
    rsi_removed = [u for u in upgrades if u.get("rsi_blocked")]
    upgrades = [u for u in upgrades if not u.get("rsi_blocked")]

    def _promo_badge(u: dict) -> str:
        # Trailing badge for a shown upgrade based on the RSI hook decision.
        if u.get("rsi_decision") == "promote":
            return " ✅ RSI favourable"
        if u.get("rsi_badge"):
            return f" {u.get('rsi_badge')}"
        return ""

    # Partition by type (post-RSI-removal)
    strangles = [u for u in upgrades if u.get("type") == "covered_strangle"]
    collars = [u for u in upgrades if u.get("type") == "collar"]
    new_ccs = [u for u in upgrades if u.get("type") == "write_covered_call"]
    sublots = [u for u in upgrades if u.get("type") == "sublot_completion"]

    # === NEW COVERED CALLS ===
    # A mid-range-RSI write (caution zone) is not a hard block, but writing it
    # caps the name for thin premium — so it's held out of the actionable
    # "READY TO WRITE" list into a "Wait for strength" section. Only RSI-favored
    # (≥60) or RSI-unknown writes stay actionable. Oversold (<35) was already
    # pulled into the "Held back by RSI" footer via rsi_blocked above.
    cc_ready = [c for c in new_ccs if not c.get("rsi_wait")]
    cc_wait = [c for c in new_ccs if c.get("rsi_wait")]

    def _render_cc(cc: dict, header_badge: str) -> None:
        from datetime import date, timedelta
        symbol = cc.get("underlying")
        shares = cc.get("shares_held")
        contracts = cc.get("contracts_writable")
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

        # Real, measured strike geometry — never a hardcoded delta. otm_pct is
        # computed from the actual strike vs spot; delta is the chain's value
        # (or "n/a" when E*TRADE didn't populate Greeks and we fell back to %OTM).
        otm_pct = cc.get("otm_pct")
        delta = cc.get("target_delta")
        otm_str = f"{otm_pct:.1f}% OTM" if otm_pct is not None else "OTM"
        delta_str = f"δ {abs(delta):.2f}" if delta is not None else "δ n/a"

        # S/R anchor — when the strike was snapped to a real resistance cluster,
        # surface it inline so the user sees the cap is at a chart-relevant level.
        anchor = cc.get("sr_anchor") if isinstance(cc.get("sr_anchor"), dict) else None
        anchor_str = ""
        if anchor:
            touches = int(anchor.get("touches", 1) or 1)
            source = anchor.get("source", "swing")
            confluence = anchor.get("confluence") or []
            # Dedupe: source must not also appear in confluence.
            conf_clean = [c for c in confluence if c != source]
            conf_phrase = f" + {', '.join(conf_clean)}" if conf_clean else ""
            touches_phrase = f"{touches} touches" if touches >= 2 else "1 touch"
            anchor_str = f", at ${float(anchor['price']):g} resistance ({source}{conf_phrase}, {touches_phrase})"

        badge = "⛔ DEFER (earnings inside window)" if earnings_blocked else header_badge
        lines.append(f"**{symbol} — {int(shares)} shares (no CC yet, {weight:.1f}% NLV)** {badge}{_promo_badge(cc)}")
        lines.append(
            f"  - SELL {contracts}× {symbol} ${strike:g}C exp ~{target_exp} "
            f"({dte} DTE, {otm_str}, {delta_str}{anchor_str})"
        )
        if cc.get("rsi_14") is not None:
            lines.append(f"  - **RSI:** {cc.get('rsi_tag')} — {cc.get('rsi_note', '')}")
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

    if cc_ready:
        lines.append(f"### Write Covered Calls ({len(cc_ready)})")
        lines.append("")
        for cc in cc_ready:
            _render_cc(cc, "✅ READY TO WRITE")

    if cc_wait:
        lines.append(f"### ⏸ Covered calls — wait for strength ({len(cc_wait)})")
        lines.append(
            "_RSI is mid-range: premium is only average and writing now caps upside "
            "without much compensation. Shown for context — write when RSI extends (≥60)._"
        )
        lines.append("")
        for cc in cc_wait:
            _render_cc(cc, "⏸ WAIT FOR STRENGTH")

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
            conc_blocked = conc.get("blocked")
            reason = conc.get("reason")
            rationale = s.get("rationale")

            # Format expiration with weekday
            try:
                from datetime import datetime
                exp_date = datetime.strptime(exp, "%Y-%m-%d")
                exp_fmt = exp_date.strftime("%a %b %d '%y")
            except (ValueError, TypeError):
                exp_fmt = exp

            status = "⛔ BLOCKED" if conc_blocked else "✅ OK"

            lines.append(f"**{symbol} — {current_calls} → add {qty}x ${strike:.0f}P** {status}{_promo_badge(s)}")

            if s.get("rsi_14") is not None:
                lines.append(f"  - **RSI:** {s.get('rsi_tag')} — {s.get('rsi_note', '')}")

            if conc_blocked:
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

            if c.get("rsi_14") is not None:
                lines.append(f"  - RSI: {c.get('rsi_tag')} (protective put — shown for context)")

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

            lines.append(f"**{symbol} — {held} shares (need {to_buy} more)**{_promo_badge(sub)}")
            if sub.get("rsi_14") is not None:
                lines.append(f"  - **RSI:** {sub.get('rsi_tag')} — {sub.get('rsi_note', '')}")
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

    # === HELD BACK BY RSI (removed by the central hook) ===
    if rsi_removed:
        type_label = {
            "write_covered_call": "covered call",
            "covered_strangle": "strangle put leg",
            "sublot_completion": "sub-lot buy",
        }
        lines.append(f"### ⏸ Held back by RSI ({len(rsi_removed)})")
        lines.append("")
        for u in rsi_removed:
            sym = u.get("underlying")
            kind = type_label.get(u.get("type"), u.get("type"))
            lines.append(f"- **{sym}** ({kind}) — {u.get('rsi_note', u.get('rsi_tag', 'RSI unfavorable'))}")
        lines.append("")

    return lines
