"""
Analyst Brief panel — structured decision summary.

Deterministic output: CLOSE winners, urgent ROLLs, earnings warnings, stress coverage,
concentration fixes, and SKIPs. No LLM call.
"""

from datetime import datetime


def _format_exp(exp_str: str) -> str:
    """Format expiration as 'Fri Jun 26 '26'."""
    try:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d")
        return exp_date.strftime("%a %b %d '%y")
    except (ValueError, TypeError):
        return exp_str or "?"


def render_analyst_brief(
    equity_reviews: list,
    options_reviews: list,
    snapshot_data: dict,
    analytics: dict,
    regime_data: dict,
) -> list[str]:
    """
    Render structured Analyst Brief panel.

    Args:
        equity_reviews: from step 4
        options_reviews: from step 5
        snapshot_data: portfolio snapshot
        analytics: from compute_analytics (may contain StressCoverage objects)
        regime_data: macro regime classification

    Returns:
        markdown lines
    """
    lines = ["## Analyst Brief", ""]

    # --- REGIME + PORTFOLIO HEALTH HEADER ---
    regime = regime_data.get("regime", "UNKNOWN")
    confidence = regime_data.get("confidence", "MEDIUM")
    nlv = snapshot_data.get("balance", {}).get("accountValue", 0)
    cash = snapshot_data.get("balance", {}).get("cash", 0)

    lines.append(f"**Portfolio regime:** {regime} (confidence {confidence})")
    lines.append(f"**NLV:** ${nlv:,.0f} | **Cash:** ${cash:,.0f}")
    
    # Handle StressCoverage object (has attributes, not dict)
    stress_coverage = analytics.get("stress_coverage", {})
    if hasattr(stress_coverage, "coverage_ratio"):
        coverage_ratio = stress_coverage.coverage_ratio
    else:
        coverage_ratio = stress_coverage.get("coverage_ratio", 0) if isinstance(stress_coverage, dict) else 0
    
    coverage_color = "🟢" if coverage_ratio > 0.7 else "🟡" if coverage_ratio > 0.5 else "🔴"
    lines.append(f"**Stress coverage:** {coverage_color} {coverage_ratio:.1f}x")
    lines.append("")

    # --- SECTION 1: CLOSE WINNERS ---
    close_winners = []
    for opt_rev in options_reviews:
        entry = opt_rev.get("entry_price")
        mid = opt_rev.get("current_mid")
        dte = opt_rev.get("days_to_expiry")
        if entry and mid and entry > 0:
            capture = (entry - mid) / entry * 100
            if capture >= 30 and dte and dte > 14:
                close_winners.append((opt_rev, capture))

    if close_winners:
        lines.append("### 1. CLOSE WINNERS")
        lines.append("")
        for opt_rev, capture in close_winners:
            contract = opt_rev.get("contract")
            exp = opt_rev.get("expiration")
            exp_pretty = _format_exp(exp)
            mid = opt_rev.get("current_mid", 0)
            limit_price = max(0.01, mid * 1.05)  # Buy at mid + 5% for quick fill; ensures profit
            lines.append(f"**{contract} — CLOSE**")
            lines.append(
                f"- **REASON:** +{capture:.0f}% profit captured, {opt_rev.get('days_to_expiry')}d DTE — lock theta gain early."
            )
            lines.append(
                f"- **ORDER:** Buy to Close {contract} {exp_pretty} — Limit ${limit_price:.2f} GTC (at ~mid for fast fill)"
            )
            lines.append(
                f"- **RISK:** Leaves theta on the table if underlying continues drifting; acceptable."
            )
            lines.append("")
        lines.append("")

    # --- SECTION 2: URGENT ROLLS ---
    urgent_rolls = []
    for opt_rev in options_reviews:
        rec = opt_rev.get("recommendation", "")
        if rec.startswith("ROLL_"):
            urgent_rolls.append(opt_rev)

    if urgent_rolls:
        lines.append("### 2. URGENT ROLLS")
        lines.append("")
        for opt_rev in urgent_rolls:
            contract = opt_rev.get("contract")
            rec = opt_rev.get("recommendation", "")
            exp = opt_rev.get("expiration")
            exp_pretty = _format_exp(exp)
            rationale = opt_rev.get("rationale", "")
            lines.append(f"**{contract} — {rec}**")
            lines.append(f"- **REASON:** {rationale}")
            roll_target = opt_rev.get("roll_target")
            if roll_target:
                roll_exp = _format_exp(roll_target.get("expiration", ""))
                lines.append(
                    f"- **TARGET:** {contract.split('_')[0]} {roll_exp} "
                    f"${roll_target.get('strike', '?')} PUT for ${roll_target.get('expectedNetCredit', 0):.2f} net credit"
                )
            lines.append("")
        lines.append("")

    # --- SECTION 3: EARNINGS WARNINGS ---
    earnings_warnings = []
    earnings_calendar = snapshot_data.get("earnings_calendar", {})
    for opt_rev in options_reviews:
        contract = opt_rev.get("contract")
        underlying = contract.split("_")[0] if contract and "_" in contract else ""
        exp = opt_rev.get("expiration")
        dte = opt_rev.get("days_to_expiry")
        if underlying in earnings_calendar and exp:
            try:
                earnings_date = datetime.strptime(
                    earnings_calendar[underlying], "%Y-%m-%d"
                ).date()
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                if exp_date >= earnings_date and (earnings_date - datetime.now().date()).days <= 14:
                    earnings_warnings.append((opt_rev, (earnings_date - datetime.now().date()).days))
            except (ValueError, TypeError):
                pass

    if earnings_warnings:
        lines.append("### 3. EARNINGS WARNINGS")
        lines.append("")
        for opt_rev, days_to_earnings in earnings_warnings:
            contract = opt_rev.get("contract")
            lines.append(f"**{contract}**")
            lines.append(f"- Earnings in {max(0, days_to_earnings)}d before or at expiration")
            lines.append(f"- Consider closing before IV crush to lock premium")
            lines.append("")
        lines.append("")

    # --- SECTION 4: STRESS COVERAGE / COLLATERAL ---
    if coverage_ratio < 0.7:
        lines.append("### 4. STRESS COVERAGE")
        lines.append("")
        lines.append(f"Coverage ratio {coverage_ratio:.1f}x below target 0.7x. Recommend closing:")

        # Extract recommended_closes from stress_coverage object
        recommended_closes = []
        if hasattr(stress_coverage, "recommended_closes"):
            recommended_closes = stress_coverage.recommended_closes

        # If we have recommendations, display them
        if recommended_closes:
            for close_rec in recommended_closes[:3]:  # Top 3 candidates
                # close_rec is a CloseRecommendation dataclass
                if hasattr(close_rec, "symbol"):
                    symbol = close_rec.symbol
                    collateral = float(getattr(close_rec, "collateral_freed", 0) or 0)
                    profit_pct_frac = float(getattr(close_rec, "profit_pct_captured", 0) or 0)
                    profit_dollars = float(getattr(close_rec, "profit_dollars", 0) or 0)
                    reason = getattr(close_rec, "reason", "")

                    lines.append(
                        f"- **{symbol}** — frees ${collateral:,.0f} collateral, "
                        f"locks ${profit_dollars:+,.0f} profit ({profit_pct_frac*100:.0f}% capture)"
                    )
                    if reason:
                        lines.append(f"  - {reason}")
                else:
                    # Fallback for dict format
                    symbol = close_rec.get("symbol", "?")
                    collateral = close_rec.get("collateral_freed", 0)
                    lines.append(f"- **{symbol}**: frees ${collateral:,.0f}")
        else:
            # No specific recommendations yet; compute some basic ones from options_reviews
            close_candidates = []
            for opt_rev in options_reviews:
                entry = opt_rev.get("entry_price")
                mid = opt_rev.get("current_mid")
                contract = opt_rev.get("contract", "")
                underlying = contract.split("_")[0] if contract and "_" in contract else ""
                strike = opt_rev.get("strike", 0)
                qty = abs(opt_rev.get("qty", 0))

                if entry and mid and entry > 0 and underlying:
                    capture_pct = (entry - mid) / entry * 100
                    if capture_pct >= 30:
                        collateral_freed = strike * 100 * qty if strike > 0 else 0
                        profit = (entry - mid) * 100 * qty
                        close_candidates.append({
                            "contract": contract,
                            "capture_pct": capture_pct,
                            "collateral_freed": collateral_freed,
                            "profit": profit,
                            "underlying": underlying,
                        })

            # Sort by collateral freed (descending) to show most impactful closes
            close_candidates.sort(key=lambda x: x["collateral_freed"], reverse=True)
            for c in close_candidates[:3]:
                lines.append(f"- **{c['contract']}** — frees ${c['collateral_freed']:,.0f} collateral, locks +${c['profit']:+,.0f} profit ({c['capture_pct']:.0f}% capture)")

        lines.append("")
        lines.append("")

    # --- SECTION 5: CONCENTRATION ---
    concentrated = []
    for eq_rev in equity_reviews:
        weight = eq_rev.get("weight", 0)
        if weight > 0.10:
            concentrated.append((eq_rev.get("ticker"), weight, nlv, eq_rev.get("price", 0), eq_rev.get("qty", 0)))

    if concentrated:
        lines.append("### 5. CONCENTRATION TRIM")
        lines.append("")
        for ticker, weight, nlv_val, price, qty in concentrated:
            current_pct = weight * 100
            target_pct = 9.0
            current_value = weight * nlv_val
            target_value = (target_pct / 100) * nlv_val
            sell_value = current_value - target_value

            # Check if there are existing covered calls on this ticker
            has_cc = any(
                opt.get("underlying") == ticker and opt.get("type") == "CALL" and opt.get("qty", 0) < 0
                for opt in options_reviews
            )

            lines.append(f"**{ticker}**: {current_pct:.1f}% → trim to 9% NLV")

            if has_cc:
                lines.append(f"- **Option A** (tax-deferred): Roll up existing covered calls to higher strikes → collect premium + reduce assignment ceiling")
                lines.append(f"- **Option B** (raise cash): Sell ~{int(sell_value / price)} shares (~${sell_value:,.0f}) → immediate cash but LTCG tax")
                lines.append(f"- **Recommendation:** A if tax-sensitive; B if need immediate cash/rebalance")
            else:
                lines.append(f"- Sell ~{int(sell_value / price)} shares (~${sell_value:,.0f}) to reduce concentration risk")

            lines.append("")
        lines.append("")

    # --- SECTION 6: SKIPS ---
    # Find ideas that didn't make it to actionable due to gates
    new_ideas = snapshot_data.get("new_ideas", [])
    skipped = []
    for idea in new_ideas:
        if not idea.get("instruction"):  # watch-only
            skipped.append(idea)

    if skipped:
        lines.append("### 6. WATCH-ONLY (SKIPPED)")
        lines.append("")
        for idea in skipped[:5]:  # Top 5 watch-only
            ticker = idea.get("ticker", "?")
            reason = idea.get("rationale", "Not yet actionable")
            lines.append(f"- **{ticker}**: {reason}")
        lines.append("")

    lines.append("")
    return lines
