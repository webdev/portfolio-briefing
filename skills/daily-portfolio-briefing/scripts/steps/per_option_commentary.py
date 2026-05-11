"""
Per-option rich commentary generation.

For each option position, detect meaningful scenarios and generate 0-3 short notes.
Patterns: earnings imminent, profit captured, ITM covered calls, etc.
"""

from datetime import datetime


def _get_earnings_days_away(underlying: str, snapshot_data: dict) -> int | None:
    """Extract next earnings date for an underlying from snapshot, return days away."""
    earnings_calendar = snapshot_data.get("earnings_calendar", {})
    if underlying not in earnings_calendar:
        return None
    earnings_date_str = earnings_calendar[underlying]
    try:
        earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
        days_away = (earnings_date - datetime.now().date()).days
        return max(0, days_away)
    except (ValueError, TypeError):
        return None


def generate_commentary(
    option_review: dict,
    snapshot_data: dict,
    equity_reviews: list | None = None,
) -> list[str]:
    """
    Generate 0-3 short commentary lines per option.

    Args:
        option_review: from options_reviews (keys: contract, type, underlying, strike,
                       expiration, dte, entry_price, current_mid, recommendation, etc.)
        snapshot_data: portfolio snapshot with quotes, earnings_calendar, etc.
        equity_reviews: list of equity reviews (for detecting "shares up sharply" patterns)

    Returns:
        list of markdown strings (no bullet points; caller adds them)
    """
    notes = []

    # Extract key fields
    contract = option_review.get("contract", "")
    underlying = contract.split("_")[0] if "_" in contract else ""
    opt_type = option_review.get("type", "").upper()
    strike = option_review.get("strike")
    expiration = option_review.get("expiration")
    dte = option_review.get("days_to_expiry")
    entry_price = option_review.get("entry_price")
    current_mid = option_review.get("current_mid")
    recommendation = option_review.get("recommendation", "")
    qty = option_review.get("qty", -1)  # negative = short

    # Compute capture percentage
    if entry_price and current_mid and entry_price > 0:
        capture_pct = (entry_price - current_mid) / entry_price * 100
    else:
        capture_pct = 0

    # Get earnings info
    earnings_days = _get_earnings_days_away(underlying, snapshot_data) if underlying else None

    # Get underlying price for ITM checks
    quotes = snapshot_data.get("quotes", {})
    underlying_quote = quotes.get(underlying, {})
    underlying_price = underlying_quote.get("last")

    # Get underlying day change
    underlying_day_change = underlying_quote.get("dayChangePct", 0)

    # Get corresponding equity review for checking if shares up despite ITM short call
    equity_review = None
    if equity_reviews:
        for eq in equity_reviews:
            if eq.get("ticker") == underlying:
                equity_review = eq
                break

    # --- PATTERN 1: URGENT - Earnings imminent + short option with BAD capture (<-50%) ---
    if earnings_days is not None and earnings_days <= 30 and capture_pct < -50 and qty < 0:
        if opt_type in ("PUT", "CALL"):
            notes.append(
                f"🚨 URGENT: Earnings in {earnings_days}d with {capture_pct:.0f}% capture — "
                "close BEFORE report or roll up & out to avoid binary gap risk"
            )

    # --- PATTERN 1b: Earnings imminent + short option with good profit ---
    elif earnings_days is not None and earnings_days <= 30 and capture_pct >= 30:
        if opt_type in ("PUT", "CALL"):
            notes.append(
                f"Earnings in {earnings_days}d with {capture_pct:.0f}% captured. "
                "Consider closing before report to lock profit, then re-sell after IV crush"
            )

    # --- PATTERN 2: Earnings imminent + short option underwater (but not desperate) ---
    elif earnings_days is not None and earnings_days <= 30 and capture_pct < 0 and capture_pct >= -50:
        if opt_type in ("PUT", "CALL"):
            notes.append(
                f"Earnings in {earnings_days}d and position is underwater. "
                "Close before report to limit loss"
            )

    # --- PATTERN 2b: General earnings flag (not imminent but worth noting) ---
    elif earnings_days is not None and earnings_days <= 14 and opt_type in ("PUT", "CALL"):
        if "Earnings" not in "\n".join(notes):  # Don't repeat if already mentioned
            notes.append(f"Earnings in {earnings_days}d")

    # --- PATTERN 3: Covered call (short call) + earnings nearby ---
    elif (
        opt_type == "CALL"
        and earnings_days is not None
        and earnings_days <= 30
        and qty < 0  # short call
    ):
        notes.append(
            f"Earnings in {earnings_days}d — covered call, so assignment risk is fine "
            "(you keep premium + strike gain). If you want to keep shares through earnings, roll up and out"
        )

    # --- PATTERN 4: ITM short call but shares up more (shares profited more) ---
    elif (
        opt_type == "CALL"
        and strike is not None
        and underlying_price is not None
        and underlying_price > strike  # ITM
        and capture_pct < 0  # underwater on the short call
        and equity_review is not None
    ):
        eq_pl_pct = equity_review.get("pl_pct", 0) * 100
        if eq_pl_pct > 0:
            notes.append(
                f"Call is ITM (option at {capture_pct:.0f}%) but shares gained more — "
                "combined position is profitable. Let it get called away at "
                f"${strike:g} + keep premium, or roll up and out if you want to keep shares"
            )

    # --- PATTERN 5: High capture + long DTE ---
    elif capture_pct >= 30 and dte and dte > 14:
        notes.append(
            f"+{capture_pct:.0f}% captured with {dte}d left — consider closing winners early"
        )

    # --- PATTERN 6: TV-aligned moves (simple proxy: >2% day move) ---
    if underlying_day_change > 0.02 and opt_type == "CALL" and qty < 0:
        notes.append(
            "Shares up sharply — bearish momentum favors your short call. "
            "Hold unless stock reverses sharply"
        )
    elif underlying_day_change < -0.02 and opt_type == "PUT" and qty < 0:
        notes.append(
            "Shares down sharply — crowd is bearish. Tighten your stop or close if price breaks support"
        )

    return notes


def render_watch_with_commentary(
    equity_reviews: list,
    options_reviews: list,
    snapshot_data: dict,
) -> list[str]:
    """
    Render watch panel with per-option commentary inline.

    Args:
        equity_reviews, options_reviews: from briefing steps
        snapshot_data: portfolio snapshot

    Returns:
        markdown lines ready to concatenate
    """
    lines = ["## Watch / Portfolio Review", ""]

    if equity_reviews:
        lines.append("### Equities")
        lines.append("")
        for review in equity_reviews:
            ticker = review.get("ticker")
            price = review.get("price", 0)
            weight = review.get("weight", 0) * 100
            pl_pct = review.get("pl_pct", 0) * 100
            rec = review.get("recommendation")
            third_party = review.get("third_party_rec")
            lines.append(f"- **{ticker}** @ ${price:.2f} — {weight:.1f}% ({pl_pct:+.1f}%) → **{rec}**")
            if third_party:
                lines.append(f"  - {third_party}")
        lines.append("")

    if options_reviews:
        lines.append("### Options")
        lines.append("")
        for review in options_reviews:
            contract = review.get("contract")
            rec = review.get("recommendation")
            opt_type = review.get("type", "")
            strike = review.get("strike")
            exp = review.get("expiration")
            dte = review.get("days_to_expiry")
            mid = review.get("current_mid")
            entry = review.get("entry_price")
            cell = review.get("matrix_cell_id")
            qty = review.get("qty", 0)

            # Compute P&L for the entire position: (entry − mid) × 100 × |qty|.
            # This is from a SHORT seller's perspective: positive = profit.
            # (entry − mid) is per-share gain; × 100 = per-contract; × |qty| = per-position.
            if entry and mid:
                contracts = abs(int(qty)) if qty else 1
                pl_dollars = (entry - mid) * 100 * contracts
                pl_pct = (entry - mid) / entry * 100 if entry else 0
            else:
                pl_dollars = None
                pl_pct = None

            # Build header with emoji based on state
            emoji = "⚠️" if rec in ("CLOSE", "ROLL_OUT", "ROLL_OUT_AND_DOWN", "ROLL_OUT_AND_UP") else "📌"
            header = f"{emoji} **{contract}**"
            if opt_type and strike:
                header += f" {opt_type} ${strike:g}"
                if exp:
                    # Format: "Fri Jun 26 '26"
                    try:
                        exp_date = datetime.strptime(exp, "%Y-%m-%d")
                        exp_pretty = exp_date.strftime("%a %b %d '%y")
                        header += f" {exp_pretty}"
                    except ValueError:
                        header += f" exp {exp}"
                if dte is not None:
                    header += f", {dte}d left"

            lines.append(header)

            # P&L line
            if pl_dollars is not None and pl_pct is not None:
                lines.append(f"  P&L: +${pl_dollars:,.0f} ({pl_pct:+.0f}%) | {dte}d left" if pl_dollars > 0
                            else f"  P&L: ${pl_dollars:,.0f} ({pl_pct:.0f}%) | {dte}d left")

            # Commentary
            commentary = generate_commentary(review, snapshot_data, equity_reviews)
            for note in commentary:
                lines.append(f"  • {note}")

            # Fall-back rationale if no commentary
            if not commentary and review.get("rationale"):
                lines.append(f"  - {review.get('rationale')}")

            # Cell ID humanized (raw cell preserved in JSON sidecar for audit)
            if cell:
                # Import locally to avoid circular import
                from render.panels import _humanize_matrix_cell
                human = _humanize_matrix_cell(cell)
                if human:
                    lines.append(f"  - {human}")

            # Roll target (legacy single-target)
            roll = review.get("roll_target")
            if roll and not review.get("roll_candidates"):
                lines.append(f"  - roll target: {roll.get('strike')} exp {roll.get('expiration')} for ${roll.get('expectedNetCredit', 0):.2f} credit")

            # NEW: Multi-candidate roll-analysis table from wheel-roll-advisor
            candidates = review.get("roll_candidates") or []
            if candidates:
                rec_id = review.get("recommended_candidate_id")
                if_anyway = review.get("if_rolling_anyway_candidate_id")
                lines.append("")
                lines.append("  **ROLL ANALYSIS:**")
                lines.append("")
                lines.append("  | id | Action | Net | Notes |")
                lines.append("  |----|--------|-----|-------|")
                for c in candidates:
                    cid = c.get("id", "?")
                    action = c.get("description", "")
                    net = c.get("netDollars", 0) or 0
                    notes = c.get("notes", "")
                    is_rec = cid == rec_id
                    is_anyway = cid == if_anyway
                    badge = ""
                    if is_rec:
                        badge = " ✅ recommended"
                    elif is_anyway:
                        badge = " ⤴️ if rolling anyway"
                    if net > 0:
                        net_str = f"+${net:,.0f} credit"
                    elif net < 0:
                        net_str = f"-${abs(net):,.0f} debit"
                    else:
                        net_str = "$0"
                    lines.append(f"  | {cid}{badge} | {action} | {net_str} | {notes} |")

                # If recommended is HOLD but there's a fallback ticket, show it
                ticket = review.get("if_rolling_anyway_ticket")
                if ticket and rec_id == "A" and if_anyway:
                    btc = ticket.get("buy_to_close", {})
                    sto = ticket.get("sell_to_open", {})
                    btc_exp = btc.get("expiration", "")
                    sto_exp = sto.get("expiration", "")
                    btc_strike = btc.get("strike", 0)
                    sto_strike = sto.get("strike", 0)
                    btc_lim = btc.get("limit_price", 0)
                    sto_lim = sto.get("limit_price", 0)
                    btc_qty = btc.get("quantity", 1)
                    net_cred = ticket.get("net_dollars_total", 0)

                    # Format expirations as "Fri Jun 26 '26"
                    def _fmt_exp(s):
                        try:
                            return datetime.strptime(s, "%Y-%m-%d").strftime("%a %b %d '%y")
                        except (ValueError, TypeError):
                            return s

                    lines.append("")
                    lines.append(f"  **IF ROLLING anyway, prefer {if_anyway}:**")
                    lines.append("")
                    lines.append("  ```")
                    lines.append(f"  BUY TO CLOSE   {btc_qty} × {review.get('underlying', '?')}  {_fmt_exp(btc_exp)}  ${btc_strike:g} {review.get('type', 'CALL')}   Limit ${btc_lim:.2f}  GTC")
                    lines.append(f"  SELL TO OPEN   {btc_qty} × {review.get('underlying', '?')}  {_fmt_exp(sto_exp)}  ${sto_strike:g} {review.get('type', 'CALL')}   Limit ${sto_lim:.2f}  GTC")
                    lines.append(f"  NET CREDIT TARGET: ${net_cred:,.0f}")
                    lines.append("  ```")

            lines.append("")

        lines.append("")

    return lines
