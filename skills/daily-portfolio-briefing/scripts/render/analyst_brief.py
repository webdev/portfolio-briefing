"""
Analyst Brief panel — structured decision summary.

Deterministic output: CLOSE winners, urgent ROLLs, earnings warnings, stress coverage,
concentration fixes, and SKIPs. No LLM call.
"""

import sys
from datetime import datetime
from pathlib import Path

try:
    from analysis import rsi_discipline
except ImportError:  # pragma: no cover - path fallback for standalone runs
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis import rsi_discipline


def _rsi_suffix(ticker_or_contract: str, technicals: dict) -> str:
    """Side-agnostic RSI tag for an analyst-brief header (e.g. ' · RSI 41')."""
    if not ticker_or_contract:
        return ""
    underlying = ticker_or_contract.split("_")[0] if "_" in ticker_or_contract else ticker_or_contract
    rsi = rsi_discipline.rsi_for(underlying, technicals)
    return f"  · {rsi_discipline.tag(rsi)}" if rsi is not None else ""


def _format_exp(exp_str: str) -> str:
    """Format expiration as 'Fri Jun 26 '26'."""
    try:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d")
        return exp_date.strftime("%a %b %d '%y")
    except (ValueError, TypeError):
        # Rule #19: never render a "?" placeholder as data.
        return exp_str or "(exp unavailable — verify at broker)"


def _roll_target_line(opt_rev: dict) -> str:
    """Resolve the URGENT ROLLS TARGET line from the SAME ranked candidate the
    ROLL ANALYSIS table marks ✅ recommended (side-aware candidate_ranker output).

    Bug (rule #43, 2026-07-31): the old code read the legacy select_roll_target
    dict with the WRONG keys ("strike"/"expiration" vs its actual
    "strikePrice"/"expirationDate"), so the brief rendered
    "IREN ? $? PUT for $0.40 net credit" — unresolved placeholders next to a
    real credit. Now: ranked candidate first; legacy dict (correct keys) as a
    fallback; honest "target unavailable" text when neither resolves — never
    "?" placeholders (rule #19).
    """
    underlying = (opt_rev.get("contract") or "").split("_")[0]
    opt_letter = "P" if (opt_rev.get("type") or "PUT").upper() == "PUT" else "C"
    qty = abs(opt_rev.get("qty") or 0)

    candidates = opt_rev.get("roll_candidates") or []
    by_id = {c.get("id"): c for c in candidates if isinstance(c, dict)}
    chosen = by_id.get(opt_rev.get("recommended_candidate_id"))
    if not (chosen and chosen.get("instruction")):
        # Recommended is HOLD (A) — use the advisor's explicit
        # "if rolling anyway" candidate before giving up.
        chosen = by_id.get(opt_rev.get("if_rolling_anyway_candidate_id"))
    if not (chosen and chosen.get("instruction")):
        chosen = next((c for c in candidates if c.get("instruction")), None)

    if chosen and chosen.get("instruction"):
        instr = chosen["instruction"]
        strike = instr.get("sell_strike")
        exp = instr.get("sell_expiration")
        if strike and exp:
            net = float(chosen.get("netDollars") or 0)
            if qty > 0:
                per_share = net / (qty * 100)
                net_str = f"${abs(per_share):.2f} net {'credit' if per_share >= 0 else 'debit'}"
            else:
                net_str = f"${abs(net):,.0f} total net {'credit' if net >= 0 else 'debit'}"
            return (
                f"- **TARGET:** {underlying} ${float(strike):g}{opt_letter} "
                f"{_format_exp(exp)} for {net_str}"
            )

    # Legacy select_roll_target fallback — its REAL keys are
    # strikePrice/expirationDate; only render when both resolve.
    roll_target = opt_rev.get("roll_target") or {}
    strike = roll_target.get("strikePrice")
    exp = roll_target.get("expirationDate")
    if strike and exp:
        credit = float(roll_target.get("expectedNetCredit") or 0)
        return (
            f"- **TARGET:** {underlying} ${float(strike):g}{opt_letter} "
            f"{_format_exp(exp)} for ${credit:.2f} net credit"
        )

    return "- **TARGET:** unavailable — see ROLL ANALYSIS table / verify at broker"


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
    technicals = snapshot_data.get("technicals", {}) or {}

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
            lines.append(f"**{contract} — CLOSE**{_rsi_suffix(contract, technicals)}")
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
            rationale = opt_rev.get("rationale", "")
            lines.append(f"**{contract} — {rec}**{_rsi_suffix(contract, technicals)}")
            lines.append(f"- **REASON:** {rationale}")
            lines.append(_roll_target_line(opt_rev))
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
            lines.append(f"**{contract}**{_rsi_suffix(contract, technicals)}")
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
                        f"{_rsi_suffix(symbol, technicals)}"
                    )
                    if reason:
                        lines.append(f"  - {reason}")
                else:
                    # Fallback for dict format (rule #19: skip rather than
                    # render a "?" placeholder for a missing symbol)
                    symbol = close_rec.get("symbol")
                    if not symbol:
                        continue
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
                lines.append(f"- **{c['contract']}** — frees ${c['collateral_freed']:,.0f} collateral, locks +${c['profit']:+,.0f} profit ({c['capture_pct']:.0f}% capture){_rsi_suffix(c['contract'], technicals)}")

        lines.append("")
        lines.append("")

    # --- SECTION 5: CONCENTRATION (tier-aware — CLAUDE.md hard rule #29) ---
    # Bug #24 (2026-07-22): GOOG/NVDA at 14.3% surfaced "trim to 9% NLV"
    # while the Risk Alerts panel on the SAME day correctly said "within
    # Tier A bounds (cap 22% NLV, tracked)". Section 5 now uses the same
    # tier framework: Tier A/B names only fire ABOVE their tier cap (and
    # trim TO the cap, not to 9%); Tier C keeps the legacy 10% → 9% bands.
    # Fail-open: no position_tiers module/config → legacy behavior for all.
    try:
        from analysis import position_tiers as _pt
    except ImportError:  # pragma: no cover - tier framework absent
        _pt = None
    _cfg = snapshot_data.get("_config") or {}

    def _tier_and_cap(ticker: str) -> tuple[str, float | None]:
        """('A', 22.0) for tier A/B names; ('C', None) → legacy bands."""
        if _pt is None:
            return "C", None
        try:
            tier = _pt.tier_for(ticker, _cfg)
            if tier in ("A", "B"):
                return tier, float(_pt.concentration_cap_for_tier(tier, _cfg))
        except Exception:
            pass
        return "C", None

    concentrated = []
    for eq_rev in equity_reviews:
        weight = eq_rev.get("weight", 0)
        ticker = eq_rev.get("ticker")
        tier, cap_pct = _tier_and_cap(ticker or "")
        if cap_pct is not None:
            # Tier A/B: within the tier cap is BY DESIGN (concentration in
            # conviction is the strategy) — no trim line unless over cap.
            if weight * 100 <= cap_pct:
                continue
            target_pct = cap_pct
        elif weight > 0.10:
            target_pct = 9.0
        else:
            continue
        concentrated.append((ticker, weight, nlv, eq_rev.get("price", 0),
                             eq_rev.get("qty", 0), tier, target_pct))

    if concentrated:
        lines.append("### 5. CONCENTRATION TRIM")
        lines.append("")
        for ticker, weight, nlv_val, price, qty, tier, target_pct in concentrated:
            current_pct = weight * 100
            current_value = weight * nlv_val
            target_value = (target_pct / 100) * nlv_val
            sell_value = current_value - target_value

            # Check if there are existing covered calls on this ticker
            has_cc = any(
                opt.get("underlying") == ticker and opt.get("type") == "CALL" and opt.get("qty", 0) < 0
                for opt in options_reviews
            )

            if tier in ("A", "B"):
                lines.append(
                    f"**{ticker}**: {current_pct:.1f}% → trim to "
                    f"{target_pct:g}% NLV (Tier {tier} cap)"
                    f"{_rsi_suffix(ticker, technicals)}")
            else:
                lines.append(f"**{ticker}**: {current_pct:.1f}% → trim to 9% NLV{_rsi_suffix(ticker, technicals)}")

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
            ticker = idea.get("ticker")
            if not ticker:  # rule #19: never render a "?" placeholder ticker
                continue
            reason = idea.get("rationale", "Not yet actionable")
            suffix = "" if "RSI" in reason else _rsi_suffix(ticker, technicals)
            lines.append(f"- **{ticker}**: {reason}{suffix}")
        lines.append("")

    lines.append("")
    return lines
