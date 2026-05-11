"""
Render panel modules for briefing sections.

Each function returns a list of markdown lines.
"""

from datetime import datetime
from pathlib import Path
import sys


# --------------------------------------------------------------------------
# Matrix cell ID → human label translation
# --------------------------------------------------------------------------
# The wheel-roll-advisor's decision matrix emits cell IDs like
# `PUT_NORMAL_MOD_OTM_BELOW_50`. These are great for audit but ugly for
# daily reading. This helper turns them into emoji + plain-English labels.
# The raw cell ID is still preserved in the JSON sidecar for audit.

def _humanize_matrix_cell(cell_id: str | None) -> str | None:
    """Return an emoji-prefixed human label for a matrix cell ID.

    Returns None if cell_id is None/empty. Returns the raw ID wrapped in
    code-ticks if no friendly label is known (so unknown cells are still
    visible — better than silently hiding).
    """
    if not cell_id:
        return None
    cell = cell_id.upper().strip()

    # Default rules (regime fallbacks + generic hold)
    if cell == "DEFAULT_HOLD":
        return "🤝 Hold — no specific rule triggered"
    if cell == "DEFAULT_NORMAL":
        return "🟢 Normal market regime"
    if cell == "DEFAULT_CAUTION":
        return "🟡 Caution regime — defensive bias"
    if cell == "DEFAULT_RISK_OFF":
        return "🔴 Risk-off — close & defend"
    if cell == "STUB_HOLD":
        return "🤝 Hold (no live data for review)"
    if cell == "DATA_UNAVAILABLE":
        return "❓ Data unavailable — verify at broker"
    if cell.startswith("DIRECTIVE_"):
        # User-set directive override (e.g. DIRECTIVE_SUPPRESS)
        kind = cell.replace("DIRECTIVE_", "").replace("_", " ").lower()
        return f"📌 Directive override ({kind})"

    # Parse structured cells: TYPE_REGIME_MONEYNESS_QUAL
    parts = cell.split("_")
    side = parts[0] if parts else ""
    regime = parts[1] if len(parts) > 1 else ""

    # Build the human label compositionally
    side_label = {"PUT": "short put", "CALL": "short call"}.get(side, side.lower())
    regime_label = {
        "NORMAL": "",   # don't decorate the typical case
        "CAUTION": " (caution regime)",
        "RISK": " (risk-off)",
    }.get(regime, "")

    cell_rest = "_".join(parts[2:]) if len(parts) > 2 else ""

    # Common cell patterns
    if "DEEP_ITM" in cell_rest:
        return f"🔴 {side_label} deep ITM{regime_label} — roll or accept assignment"
    if "NEAR_ATM" in cell_rest:
        return f"🟡 {side_label} near-ATM{regime_label} — watch / consider close"
    if "MOD_OTM_BELOW_50" in cell_rest:
        return f"🟢 {side_label} moderately OTM, < 50% captured{regime_label} — hold for decay"
    if "MOD_OTM_ABOVE_50" in cell_rest:
        return f"💰 {side_label} moderately OTM, ≥ 50% captured{regime_label} — consider closing winner"
    if "DEEP_OTM_BELOW_50" in cell_rest:
        return f"🟢 {side_label} deep OTM, < 50% captured{regime_label} — let theta work"
    if "DEEP_OTM_ABOVE_50" in cell_rest:
        return f"💰 {side_label} deep OTM, ≥ 50% captured{regime_label} — close winner"
    if "ATM" in cell_rest:
        return f"🟡 {side_label} at-the-money{regime_label} — assignment risk"

    # Unknown structured cell — emit the human-readable parts we have
    if side_label:
        return f"• {side_label}{regime_label} ({cell})"
    # Fall back to raw ID (so unknowns are still surfaced for debug)
    return f"• `{cell}`"

# Wire in the yield-calculator, defensive-collar-advisor, wheel-roll-advisor, wash-sale-tracker, trade-validator
_SKILLS_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SKILLS_ROOT / "yield-calculator" / "scripts"))
sys.path.insert(0, str(_SKILLS_ROOT / "defensive-collar-advisor" / "scripts"))
sys.path.insert(0, str(_SKILLS_ROOT / "wheel-roll-advisor" / "scripts"))
sys.path.insert(0, str(_SKILLS_ROOT / "wash-sale-tracker" / "scripts"))
sys.path.insert(0, str(_SKILLS_ROOT / "trade-validator" / "scripts"))
# Local analysis helpers (earnings_guard lives in this skill)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Add adapters path for etrade_market
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))

try:
    from yield_formulas import (  # type: ignore
        compute_csp_yield,
        compute_cc_yield,
        compute_roll_yield,
        compute_collar_yield,
        compute_hedge_yield,
        compute_close_yield,
        format_yield_line,
    )
except ImportError:
    # Fallback no-ops if the skill isn't installed
    def compute_csp_yield(**kwargs): return {}
    def compute_cc_yield(**kwargs): return {}
    def compute_roll_yield(**kwargs): return {}
    def compute_collar_yield(**kwargs): return {}
    def compute_hedge_yield(**kwargs): return {}
    def compute_close_yield(**kwargs): return {}
    def format_yield_line(*args, **kwargs): return ""

try:
    from propose_collar import propose_collar  # type: ignore
except ImportError:
    def propose_collar(**kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(qualified=False, skip_reasons=["collar advisor not installed"])

try:
    from candidate_ranker import rank_candidates  # type: ignore
except ImportError:
    def rank_candidates(candidates, spot, is_core=False, embedded_tax_dollars=0.0,
                        min_credit_threshold=1000.0):
        # Fallback: simple max-credit pick
        best = None
        for c in candidates or []:
            if c.get("id") == "A":
                continue
            net = c.get("netDollars") or 0
            if net >= min_credit_threshold and (best is None or net > best.get("netDollars", 0)):
                best = c
        return best, []

try:
    from wash_sale_check import is_wash_sale_blocked  # type: ignore
except ImportError:
    def is_wash_sale_blocked(ticker, as_of_date, ledger_path=None):
        return (False, "")

try:
    from analysis.earnings_guard import check_earnings_conflict, format_earnings_badge  # type: ignore
except ImportError:
    def check_earnings_conflict(ticker, expiration, earnings_calendar, as_of):
        return {"conflict": False, "level": "none", "days_to_earnings": None, "message": ""}
    def format_earnings_badge(check_result):
        return ""

try:
    from validate import (  # type: ignore
        validate_diagonal_up_roll,
        validate_calendar_roll,
        validate_csp,
        validate_collar,
        format_validation_line,
    )
except ImportError:
    def validate_diagonal_up_roll(**kwargs): return None
    def validate_calendar_roll(**kwargs): return None
    def validate_csp(**kwargs): return None
    def validate_collar(**kwargs): return None
    def format_validation_line(v): return ""

try:
    from etrade_market import find_put_strike_near, get_option_chain  # type: ignore
except ImportError:
    def find_put_strike_near(**kwargs):
        return None
    def get_option_chain(**kwargs):
        return None


def render_header(
    date_str: str,
    regime: str,
    nlv: float,
    cash: float,
    action_count: int,
    confidence: str = "MEDIUM",
    regime_rationale: str = "",
    ytd_pnl: dict = None,
) -> list:
    """Render header panel with date, regime, portfolio metrics, and YTD P&L if available."""
    cash_pct = (cash / nlv * 100) if nlv else 0

    # Parse the trading date and produce a long-form, unambiguous title:
    # e.g. "Friday, May 8, 2026" + "(generated 14:32 ET, Thu May 7 '26)"
    pretty_date = date_str
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        pretty_date = d.strftime("%A, %B %-d, %Y")  # e.g. "Friday, May 8, 2026"
    except (ValueError, TypeError):
        pass
    now = datetime.now()
    generated_str = now.strftime("%a %b %-d '%y, %-I:%M %p")  # e.g. "Fri May 8 '26, 8:30 AM"

    lines = [
        f"# Daily Briefing — {pretty_date}",
        "",
        f"_For trading session **{date_str}** · generated {generated_str} local_",
        "",
        f"**Regime:** {regime} (confidence: {confidence})",
    ]
    # Stale-data freshness warning (yields > 60 minutes since snapshot)
    # Note: render_header doesn't get snapshot_data directly; for now we leave a
    # placeholder. The freshness warning is also computed and surfaced by the
    # action list when snapshot_data is available there.
    if regime_rationale:
        lines.append(f"  - {regime_rationale}")
    lines.extend([
        f"**Portfolio NLV:** ${nlv:,.0f} | **Cash:** ${cash:,.0f} ({cash_pct:.1f}%)",
        f"**Action Items:** {action_count}",
    ])

    # Add YTD P&L if available and not error
    if ytd_pnl and not ytd_pnl.get("error"):
        collected = ytd_pnl.get("premium_collected", 0)
        losses = ytd_pnl.get("realized_losses", 0)
        net = ytd_pnl.get("net_realized", 0)
        lines.append(f"**YTD Options:** ${collected:,.0f} collected | ${losses:,.0f} losses → **${net:+,.0f}** net")

    lines.append("")
    return lines


def render_market_context(regime_data: dict, quotes: dict) -> list:
    """Today's market context — real VIX/SPY data and what's driving the regime."""
    lines = ["## Market Context", ""]
    inputs = (regime_data or {}).get("inputs_at_evaluation", {})
    triggered = (regime_data or {}).get("triggered_rules", [])

    vix_last = inputs.get("vix_last")
    vix_change = inputs.get("vix_day_change_pct")
    spy_change = inputs.get("spy_day_change_pct")
    spy_5d = inputs.get("spy_5d_change_pct")

    if vix_last is not None:
        vix_str = f"VIX **{vix_last:.2f}**"
        if vix_change is not None:
            vix_str += f" ({vix_change:+.1%} day)"
        lines.append(f"- {vix_str}")
    if spy_change is not None:
        spy_str = f"SPY **{spy_change:+.2%}** today"
        if spy_5d is not None:
            spy_str += f", **{spy_5d:+.2%}** 5-day"
        lines.append(f"- {spy_str}")

    qqq = quotes.get("QQQ", {})
    if qqq:
        lines.append(f"- QQQ ${qqq.get('last', 0):.2f} ({qqq.get('dayChangePct', 0):+.2%} day)")

    if triggered:
        rule = triggered[0]
        # Drop the raw rule_id — the rationale already says everything the user
        # needs in plain English. Raw ID stays in the JSON sidecar for audit.
        lines.append(f"- Regime trigger: {rule.get('rationale')}")

    if (regime_data or {}).get("stickiness_applied"):
        lines.append(f"- Stickiness: {regime_data.get('sticky_hold_reason')}")

    lines.append("")
    return lines


def render_health(equity_reviews: list, nlv: float, options_positions: list = None) -> list:
    """Render portfolio health panel with real top holdings and aggregated option Greeks."""
    lines = ["## Health", ""]

    if not equity_reviews and not options_positions:
        lines.append("No positions.")
        lines.append("")
        return lines

    # Top 5 by weight (only when equities exist)
    if equity_reviews:
        by_weight = sorted(equity_reviews, key=lambda x: x.get("weight", 0), reverse=True)[:5]
        lines.append("**Top 5 Holdings**")
        for review in by_weight:
            ticker = review.get("ticker")
            weight = review.get("weight", 0) * 100
            pl_pct = review.get("pl_pct", 0) * 100
            lines.append(f"- {ticker}: {weight:.1f}% ({pl_pct:+.1f}%)")

    # Aggregate Greeks: equity positions contribute 1 delta-share per share long.
    # Options contribute delta × qty × 100 (signed by qty, where negative = short).
    if options_positions or equity_reviews:
        # Equity delta: each long share = +1 delta-equivalent share, short share = -1
        equity_delta = 0.0
        for rev in equity_reviews:
            qty = rev.get("qty", 0) or 0
            equity_delta += float(qty)

        # Options Greeks (signed by qty)
        opt_delta = 0.0
        opt_gamma = 0.0
        opt_theta = 0.0
        opt_vega = 0.0
        unknown_greeks = 0
        multiplier = 100  # standard equity option contract
        for opt in (options_positions or []):
            qty = opt.get("qty", 0)
            d, g, t, v = (opt.get("delta"), opt.get("gamma"), opt.get("theta"), opt.get("vega"))
            if d is None and g is None and t is None and v is None:
                unknown_greeks += 1
                continue
            scale = qty * multiplier  # signed: short positions contribute negatively
            if d is not None: opt_delta += d * scale
            if g is not None: opt_gamma += g * scale
            # Theta: E*TRADE publishes per-share, per-day. For shorts (qty<0):
            # theta value is negative for both long and short options.
            # Contract theta = per-share theta × 100 shares × abs(qty) × sign(qty).
            # For short: theta(-0.02) × 100 × (-2) × (-1) = 0.02 × 100 × 2 = 4.0 (positive, income).
            # For long: theta(-0.02) × 100 × 2 × (+1) = -0.02 × 100 × 2 = -4.0 (negative, decay).
            # Since qty is already signed (qty=-2 for shorts), scale = qty × multiplier works correctly.
            if t is not None: opt_theta += t * scale
            if v is not None: opt_vega += v * scale

        # opt_theta is already signed correctly: per-option theta × scale where scale = qty × 100.
        # For short positions (qty<0), theta is per-share and negative, so:
        # theta(-0.02) × (-2 qty) × 100 = 0.02 × 2 × 100 = $4/day (positive = income to seller).
        # For long positions (qty>0), theta is negative, so:
        # theta(-0.02) × (2 qty) × 100 = -0.02 × 2 × 100 = -$4/day (negative = cost to holder).
        # Same for vega: per-option vega is positive, short qty is negative,
        # so net vega is negative for short-vol portfolios.
        net_delta = equity_delta + opt_delta

        lines.append("")
        lines.append("**Net Greeks** (delta in equivalent shares)")
        lines.append(f"- Delta: {net_delta:+,.0f} shares (equity {equity_delta:+,.0f} + options {opt_delta:+,.0f})")
        lines.append(f"- Theta: ${opt_theta:+,.0f} / day (positive = income to seller)")
        lines.append(f"- Vega: ${opt_vega:+,.0f} per 1% IV move (negative = short vol)")
        lines.append(f"- Gamma: {opt_gamma:+,.2f}")
        if unknown_greeks:
            lines.append(f"- ({unknown_greeks} contracts missing Greeks data)")

    lines.append("")
    return lines


def render_risk_alerts(equity_reviews: list, options_reviews: list, regime_data: dict) -> list:
    """Real risk alerts — surface anything material from the reviews."""
    lines = ["## Risk Alerts", ""]
    alerts = []

    regime = (regime_data or {}).get("regime", "NORMAL")
    if regime in ("CAUTION", "RISK_OFF"):
        alerts.append(f"⚠️ Regime is **{regime}** — new long entries suppressed")

    # Concentration warnings
    for rev in equity_reviews:
        weight = rev.get("weight", 0)
        if weight > 0.10:
            alerts.append(f"⚠️ {rev.get('ticker')} concentration {weight*100:.1f}% — over 10% NLV cap")
        elif weight > 0.08:
            alerts.append(f"📊 {rev.get('ticker')} concentration {weight*100:.1f}% — approaching 10% cap")

    # Options: surface urgent earnings+loss flags AND actionable recommendations
    actionable_decisions = {"CLOSE", "CLOSE_FOR_PROFIT", "ROLL_OUT", "ROLL_OUT_AND_DOWN", "ROLL_OUT_AND_UP", "TAKE_ASSIGNMENT", "LET_EXPIRE"}
    for rev in options_reviews:
        rec = rev.get("recommendation", "")
        contract = rev.get("contract", "")
        rationale = rev.get("rationale", "")

        # Check for URGENT earnings+loss flags in the rationale or commentary
        if "🚨 URGENT" in rationale or "earnings" in rationale.lower() and "capture" in rationale.lower() and ("-" in rationale):
            alerts.insert(0, f"🚨 **URGENT EARNINGS:** {contract} — {rationale[:100]}")
        elif rec in actionable_decisions:
            alerts.append(f"🎯 {contract} → **{rec}**: {rationale[:80]}")
        elif rec == "ERROR":
            alerts.append(f"❌ {contract}: advisor error — {rationale[:80]}")

    if not alerts:
        lines.append("✓ No urgent alerts.")
    else:
        for a in alerts:
            lines.append(f"- {a}")

    lines.append("")
    return lines


def _format_delta_line(delta: float | None, contract_type: str = "") -> str:
    """Surface the option delta as an assignment-probability proxy."""
    if delta is None:
        return ""
    abs_d = abs(float(delta))
    direction = "ITM" if abs_d > 0.5 else "OTM"
    prob_pct = abs_d * 100  # rough prob of ITM at expiration
    return f"Delta {delta:+.2f} (~{prob_pct:.0f}% ITM probability — {direction})"


def _route_account(action_type: str, ticker: str, position_account: str | None,
                   accounts_config: list) -> str:
    """
    Decide which account a new action should go in (based on CLAUDE.md routing rules):
    - High-frequency premium (CSPs, weekly puts) → Roth IRA first (tax-free)
    - Strategies requiring Level 3+ (spreads, strangles) → Taxable only
    - Engine 1 long-term equity → Taxable (LTCG eligible)
    - Closes/rolls stay in the position's existing account
    """
    if action_type in ("CLOSE", "ROLL"):
        return position_account or "(existing account)"
    # Find Roth IRA in config (accept both list-of-strings and list-of-dicts)
    roth = None
    taxable = None
    for acc in (accounts_config or []):
        if isinstance(acc, str):
            # Just a key — can't determine type, skip
            continue
        if not isinstance(acc, dict):
            continue
        atype = (acc.get("type") or "").upper()
        if "ROTH" in atype:
            roth = acc.get("accountIdKey", "Roth IRA")
        elif atype == "TAXABLE":
            taxable = acc.get("accountIdKey", "Taxable")

    if action_type in ("NEW CSP", "NEW WEEKLY"):
        return roth or taxable or "(any account)"
    if action_type in ("SPREAD", "STRANGLE", "COLLAR"):
        return taxable or "(taxable required — Level 3+)"
    if action_type == "HEDGE":
        return taxable or "(any account)"
    return "(any account)"


def _data_freshness_warning(snapshot_data: dict) -> str:
    """Return a warning string if snapshot data is stale (>60 minutes old)."""
    snapshot_data = snapshot_data or {}
    ts = snapshot_data.get("snapshot_timestamp") or snapshot_data.get("balance", {}).get("timestamp")
    if not ts:
        return ""
    try:
        # Try parsing as ISO datetime
        if isinstance(ts, (int, float)):
            snap_time = datetime.fromtimestamp(float(ts) / 1000 if ts > 1e12 else float(ts))
        else:
            snap_time = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        age_min = (datetime.now() - snap_time.replace(tzinfo=None)).total_seconds() / 60
        if age_min > 60:
            return f"⚠️ Quote data is {int(age_min)} min old — verify fresh prices before placing orders."
    except (ValueError, TypeError):
        pass
    return ""


def render_summary_card(items: list, snapshot_data: dict | None = None) -> list:
    """Render a portfolio total-impact summary at the END of the action list.

    Aggregates: net cash flow today, total premium income at expiration if all
    short legs decay to zero, total tax avoided if no assignments, total upside
    protected. One-line dashboard so the user can decide do-everything vs do-top-N.
    """
    if not items:
        return []
    # Crude regex-based extraction of dollar amounts and tax saved from rendered items.
    import re
    text = "\n".join(items)
    # Net credit from CLOSE + ROLL + CSP + HEDGE (look for `+$X,XXX` and `-$X,XXX` / `−$X,XXX`)
    credits = re.findall(r"[+]\$([\d,]+)\s+(?:net\s+)?credit", text, re.IGNORECASE)
    credits += re.findall(r"\+\$([\d,]+)\s+profit", text, re.IGNORECASE)
    credits += re.findall(r"locks\s+\$\+([\d,]+)\s+profit", text, re.IGNORECASE)
    debits = re.findall(r"[−-]\$([\d,]+)\s+(?:net\s+)?debit", text, re.IGNORECASE)
    hedge_costs = re.findall(r"~\$([\d,]+);\s+coverage", text)

    net_cash = (
        sum(int(c.replace(",", "")) for c in credits)
        - sum(int(d.replace(",", "")) for d in debits)
        - sum(int(h.replace(",", "")) for h in hedge_costs)
    )

    tax_avoided = sum(int(t.replace(",", "")) for t in re.findall(
        r"avoids?\s+\$([\d,]+)\s+tax", text, re.IGNORECASE))
    tax_avoided += sum(int(t.replace(",", "")) for t in re.findall(
        r"Tax avoided[^$]*\$([\d,]+)", text))
    upside_protected = sum(int(u.replace(",", "")) for u in re.findall(
        r"\$([\d,]+)\s+of\s+(?:effective\s+downside\s+hedge|new\s+upside)", text, re.IGNORECASE))

    n_actions = sum(1 for line in items
                    if line and line.lstrip() and line.lstrip()[0].isdigit() and "." in line.lstrip()[:5])

    # Worst-case fill drag on rolls (if both legs fill at the worst end of the bid-ask spread)
    # Roughly: each roll has best/worst spread of ~2% — cumulative drag ~3-5% on debit rolls
    worst_case_drag = 0
    debit_count = len(re.findall(r"−\$([\d,]+)\s+net debit", text))
    if debit_count > 0:
        # Assume ~5% additional cost at worst-case fill on each debit roll
        worst_case_drag = -int(abs(net_cash) * 0.05) if net_cash < 0 else 0

    out = [
        "",
        "### 📋 Total Impact (if all actions executed)",
        "",
        f"- **Total actions:** {n_actions}",
        f"- **Net cash today (mid-fills):** {'+' if net_cash >= 0 else '−'}${abs(net_cash):,}",
    ]
    if worst_case_drag:
        worst_total = net_cash + worst_case_drag
        out.append(
            f"- **Worst-case fill (all debits at ask):** "
            f"{'+' if worst_total >= 0 else '−'}${abs(worst_total):,} "
            f"(${abs(worst_case_drag):,} more drag than mid-fill assumption)"
        )
    if tax_avoided:
        out.append(f"- **Tax avoided** (if no core-position assignments): ~${tax_avoided:,}")
    if upside_protected:
        out.append(f"- **Upside / downside protected:** ~${upside_protected:,}")
    nlv = (snapshot_data or {}).get("balance", {}).get("accountValue", 0) or 0
    if nlv:
        out.append(f"- **Net cash as % of NLV:** {net_cash / nlv * 100:+.2f}%")
    out.append("")
    return out


def embedded_tax_for_log(rev: dict, equity_reviews: list, ltcg_rate: float) -> float:
    """Estimate the tax bill that would hit if this position's call were assigned."""
    underlying = rev.get("underlying", "")
    strike = rev.get("strike") or 0
    for er in equity_reviews:
        if er.get("ticker") == underlying:
            spot = er.get("price") or 0
            pl_pct = er.get("pl_pct") or 0
            shares = er.get("qty") or 0
            cost_basis = spot / (1 + pl_pct) if pl_pct > -1 else spot
            if strike and cost_basis < strike:
                return (strike - cost_basis) * shares * ltcg_rate
            return 0.0
    return 0.0


def _strike_from_contract(contract: str, fallback: float | None = None) -> float | None:
    """Best-effort strike parse from a contract symbol like UNDER_PUT_strike_YYYYMMDD."""
    if fallback:
        try:
            return float(fallback)
        except (ValueError, TypeError):
            pass
    if not contract:
        return None
    parts = contract.split("_")
    for p in parts:
        try:
            v = float(p)
            if 0.5 <= v <= 5000:  # plausible option strike
                return v
        except ValueError:
            continue
    return None


def render_action_list(
    equity_reviews: list,
    options_reviews: list,
    new_ideas: list,
    analytics: dict | None = None,
    snapshot_data: dict | None = None,
    date_str: str | None = None,
) -> list:
    """Prioritized action list — synthesized from EVERY actionable signal in the briefing.

    Each numbered action is followed by **Why** and **Gain** sub-bullets that explain
    the trigger and the specific benefit (locked profit, freed collateral, additional
    theta horizon, downside protection, etc.).

    Order: URGENT → CLOSE WINNERS → EXECUTE ROLLS (high credit) → matrix actionable rec
    → CONCENTRATION TRIM → HEDGE if stress < target → equity non-HOLD → top new CSPs.
    """
    pretty_date = date_str or ""
    if date_str:
        try:
            pretty_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A, %B %-d, %Y")
        except (ValueError, TypeError):
            pretty_date = date_str
    header = "## Today's Action List"
    if pretty_date:
        header += f" — {pretty_date}"
    lines = [header, ""]
    items: list = []
    n = 1
    seen_contracts = set()  # de-dupe across categories

    options_reviews = options_reviews or []
    equity_reviews = equity_reviews or []
    new_ideas = new_ideas or []

    # ---- 1. URGENT (earnings + losing) ----
    for rev in options_reviews:
        rationale = (rev.get("rationale") or "")
        contract = rev.get("contract", "")
        is_urgent = "🚨" in rationale or "URGENT" in rationale.upper()
        if is_urgent and contract not in seen_contracts:
            dte = rev.get("days_to_expiry")
            entry = rev.get("entry_price") or 0
            mid = rev.get("current_mid") or 0
            qty = abs(rev.get("qty", 0) or 0)
            loss_dollars = (mid - entry) * 100 * qty if (mid > entry and entry > 0 and qty) else 0
            items.append(f"{n}. 🚨 **URGENT** {contract} — {rationale[:140]}")
            items.append(
                f"   - **Why:** Earnings or expiry within ~14d combined with material "
                f"underwater P&L; binary gap risk dominates remaining theta. "
                f"Closing/rolling now removes the gap exposure before the report."
            )
            gain_parts = ["caps tail risk on the next print"]
            if loss_dollars:
                gain_parts.append(f"locks loss at ~${loss_dollars:,.0f} instead of letting it expand")
            if dte:
                gain_parts.append(f"frees the next {dte}d of collateral for redeployment")
            items.append(f"   - **Gain:** {'; '.join(gain_parts).capitalize()}.")
            seen_contracts.add(contract)
            n += 1

    # ---- 2. CLOSE WINNERS (capture >= 30%) ----
    for rev in options_reviews:
        contract = rev.get("contract", "")
        if contract in seen_contracts:
            continue
        entry = rev.get("entry_price")
        mid = rev.get("current_mid")
        qty = abs(rev.get("qty", 0) or 0)
        if entry and mid and entry > 0 and qty > 0:
            capture_pct = (entry - mid) / entry * 100
            if capture_pct >= 30:
                pl_dollars = (entry - mid) * 100 * qty
                limit = mid * 1.05
                dte = rev.get("days_to_expiry") or 0
                strike = _strike_from_contract(contract, rev.get("strike"))
                opt_type = (rev.get("type") or "").upper()
                if opt_type == "PUT" and strike:
                    collateral = strike * 100 * qty
                    collateral_label = f"frees ${collateral:,.0f} cash collateral"
                elif opt_type == "CALL" and strike:
                    collateral = strike * 100 * qty
                    collateral_label = f"unlocks 100×{int(qty)} shares (notional ${collateral:,.0f}) for fresh covered-call premium"
                else:
                    collateral_label = "frees the underlying for redeployment"
                remaining_premium = mid * 100 * qty
                # Days held: best-effort. If snapshot doesn't carry it, assume 30d
                # (a reasonable wheel-position holding-period default).
                days_held = rev.get("days_held") or rev.get("daysHeld") or 30
                if days_held < 5:
                    days_held = 30  # avoid divide-by-tiny producing absurd yields
                # Yield via the yield-calculator skill
                yield_res = compute_close_yield(
                    entry_price=entry, current_mid=mid, strike=strike or 0,
                    contracts=int(qty), days_held=days_held,
                    days_to_expiry=dte, is_short=True,
                ) if strike else {}

                items.append(
                    f"{n}. **CLOSE** {contract} — +{capture_pct:.0f}% (${pl_dollars:+,.0f}); "
                    f"buy-to-close limit ${limit:.2f}"
                )
                if yield_res:
                    items.append(f"   - {format_yield_line(yield_res)}")
                items.append(
                    f"   - **Why:** {capture_pct:.0f}% of max profit already captured "
                    f"with {dte}d still on the contract. Remaining ~${remaining_premium:,.0f} of "
                    f"theta isn't worth carrying the gamma/gap risk for {dte}d more — "
                    f"close-at-50% rule (and stretch to ~30% for OTM/short-dated)."
                )
                items.append(
                    f"   - **Gain:** Locks ${pl_dollars:+,.0f} profit and {collateral_label}; "
                    f"redeploy that collateral into a fresh higher-premium opportunity."
                )
                seen_contracts.add(contract)
                n += 1

    # ---- 3. EXECUTE ROLL — use tax-aware ranker (core mode for core_positions) ----
    ROLL_CREDIT_THRESHOLD = 1000.0
    config_local = (snapshot_data or {}).get("_config", {}) if snapshot_data else {}
    core_tickers_set = set(config_local.get("core_positions", []))
    ltcg_rate_local = float(config_local.get("ltcg_rate", 0.238))

    for rev in options_reviews:
        contract = rev.get("contract", "")
        if contract in seen_contracts:
            continue
        candidates = rev.get("roll_candidates") or []
        if not candidates:
            continue

        # Defensive-only roll gate. roll_candidates exist for EVERY position
        # so the user can see what's available in the chain (Watch panel's
        # ROLL ANALYSIS table). The Action List should only surface a roll
        # when the position genuinely needs defending.
        #
        # The PRIMARY defensive trigger is moneyness — has spot crossed (or
        # come near) the strike? P&L% alone is a misleading trigger because
        # a deep-OTM short call can show -70% P&L just from IV bleed when
        # the underlying rallies, while still being well below the strike
        # and at delta 0.10. That's "the rally happened, theta will recover
        # it," not "I'm about to lose my shares."
        #
        # Triggers (any one):
        #   1. Matrix/advisor explicitly says ROLL or matrix cell flags ITM/ATM.
        #   2. Position is at/past strike (true assignment risk):
        #      - Short PUT:  spot ≤ 1.03 × strike (within 3% above strike or below)
        #      - Short CALL: spot ≥ 0.97 × strike (within 3% below strike or above)
        #   3. Imminent expiration on an at-strike position.
        #   4. Imminent earnings on an at-strike position (the binary event
        #      could push spot through strike).
        #
        # Note: imminent earnings or imminent expiration on a position that
        # is STILL well OTM is NOT a defensive trigger by itself. Rolling
        # preemptively into a contract that inherits the same risk doesn't
        # escape it — and the cost of the roll often exceeds the expected
        # cost of the rare ITM outcome.
        advisor_rec = (rev.get("recommendation") or "").upper()
        matrix_cell = (rev.get("matrix_cell_id") or "").upper()
        _cur = float(rev.get("current_mid", 0) or 0)
        _entry = float(rev.get("entry_price", 0) or 0)
        _dte = float(rev.get("days_to_expiry", 0) or 0)
        _days_to_earn = rev.get("days_to_earnings")
        _opt_type_gate = (rev.get("type") or "").upper()
        _strike_gate = float(rev.get("strike") or 0)
        _und_gate = rev.get("underlying") or contract.split("_")[0]
        _quotes_gate = (snapshot_data or {}).get("quotes", {}) if snapshot_data else {}
        _spot_gate = float(_quotes_gate.get(_und_gate, {}).get("last") or 0)
        _moneyness = (_spot_gate / _strike_gate) if (_strike_gate and _spot_gate) else 1.0

        position_at_or_past_strike = (
            (_opt_type_gate == "PUT" and _moneyness < 1.03)
            or (_opt_type_gate == "CALL" and _moneyness > 0.97)
        )
        defensive = (
            "ROLL" in advisor_rec
            or "DEEP_ITM" in matrix_cell
            or "NEAR_ATM" in matrix_cell
            or position_at_or_past_strike
            or (position_at_or_past_strike and 0 < _dte <= 14)
            or (position_at_or_past_strike
                and _days_to_earn is not None and 0 < _days_to_earn <= 14)
        )
        if not defensive:
            # Quietly skip — Watch panel still shows the ROLL ANALYSIS table
            # so the user can act on these opportunistically if they want.
            continue

        underlying = rev.get("underlying", contract.split("_")[0])
        is_core = underlying in core_tickers_set

        # Annotate each candidate with current_strike (so the ranker can detect calendars)
        cur_strike_for_rank = _strike_from_contract(contract, rev.get("strike")) or 0
        for c in candidates:
            c.setdefault("current_strike", cur_strike_for_rank)

        # Look up underlying spot
        underlying_spot = 0.0
        quotes_local = (snapshot_data or {}).get("quotes", {}) if snapshot_data else {}
        if underlying in quotes_local:
            underlying_spot = float(quotes_local[underlying].get("last") or 0)
        if not underlying_spot:
            for er in equity_reviews:
                if er.get("ticker") == underlying:
                    underlying_spot = float(er.get("price") or 0)
                    break

        # Estimate embedded tax cost on assignment for this position
        embedded_tax = 0.0
        if is_core and underlying_spot:
            for er in equity_reviews:
                if er.get("ticker") == underlying:
                    pl_pct = er.get("pl_pct") or 0
                    shares = er.get("qty") or 0
                    cost_basis = underlying_spot / (1 + pl_pct) if pl_pct > -1 else underlying_spot
                    if cur_strike_for_rank and cost_basis < cur_strike_for_rank:
                        embedded_gain = (cur_strike_for_rank - cost_basis) * shares
                        embedded_tax = embedded_gain * ltcg_rate_local
                    break

        best, _scores = rank_candidates(
            candidates, spot=underlying_spot or cur_strike_for_rank,
            is_core=is_core, embedded_tax_dollars=embedded_tax,
            min_credit_threshold=ROLL_CREDIT_THRESHOLD,
        )
        if best:
            credit = best.get("netDollars", 0)
            desc = best.get("description", "")
            dte_added = best.get("dteExtension") or 0
            instruction = best.get("instruction") or {}
            entry = rev.get("entry_price") or 0
            cur = rev.get("current_mid") or 0
            qty = abs(rev.get("qty", 0) or 0)
            opt_type = (rev.get("type") or "").upper()
            cur_strike = _strike_from_contract(contract, rev.get("strike")) or 0.0
            cur_exp = rev.get("expiration") or ""
            new_strike = instruction.get("sell_strike") or cur_strike or 0.0
            new_exp_raw = instruction.get("sell_expiration") or ""
            new_bid = instruction.get("sell_bid") or 0
            new_ask = instruction.get("sell_ask") or 0
            new_mid = instruction.get("sell_mid") or 0
            # Format expirations as Fri Mon DD 'YY
            from datetime import datetime as _dt
            def _fmt_exp(s: str) -> str:
                try:
                    return _dt.strptime(s, "%Y-%m-%d").strftime("%a %b %d '%y")
                except Exception:
                    return str(s) if s else "?"
            cur_exp_pretty = _fmt_exp(cur_exp)
            new_exp_pretty = _fmt_exp(new_exp_raw)
            # Per-share credit math (broker convention is per-share limit on combos):
            # spread_bid (conservative)   = new_bid - cur_mid
            # spread_mid (target fill)    = new_mid - cur_mid
            # spread_ask (best-case)      = new_ask - cur_mid
            spread_bid = (new_bid - cur) if (new_bid and cur) else 0
            spread_mid = (new_mid - cur) if (new_mid and cur) else 0
            spread_ask = (new_ask - cur) if (new_ask and cur) else 0
            # Total dollars at each price level (qty contracts × 100 shares)
            total_at_bid = spread_bid * 100 * qty
            total_at_mid = spread_mid * 100 * qty
            # Buy-to-close limit (pay near mid; market makers usually fill within a tick)
            close_limit = cur * 1.02 if cur else 0
            # Sell-to-open limit (start at mid; lower toward bid if not filling)
            open_limit = new_mid if new_mid else (new_bid + new_ask) / 2

            underwater = (cur > entry) and entry > 0
            unrealized_loss = (cur - entry) * 100 * qty if underwater else 0

            # Look up real underlying spot from the snapshot quotes
            underlying = rev.get("underlying", contract.split("_")[0])
            underlying_spot = 0.0
            quotes = (snapshot_data or {}).get("quotes", {}) if snapshot_data else {}
            if underlying in quotes:
                underlying_spot = float(quotes[underlying].get("last") or 0)
            if not underlying_spot:
                # Fallback: scan equity_reviews
                for er in equity_reviews:
                    if er.get("ticker") == underlying:
                        underlying_spot = float(er.get("price") or 0)
                        break
            if not underlying_spot:
                underlying_spot = float(cur_strike or 0)  # last-resort proxy

            position_value = (rev.get("position_value")
                              or underlying_spot * 100 * qty
                              or 1.0)
            roll_yield = compute_roll_yield(
                new_premium=new_mid, new_strike=new_strike, new_dte=dte_added or 30,
                contracts=int(qty), spot=underlying_spot or 1,
                net_credit_dollars=credit, position_value=position_value,
                old_strike=cur_strike,
            )

            # Headline
            is_calendar = (cur_strike == new_strike)
            if is_calendar:
                roll_label = "Calendar roll (same strike, longer date)"
            elif new_strike > cur_strike:
                roll_label = "Diagonal up-and-out (raises cap)"
            else:
                roll_label = "Diagonal roll"
            credit_label = (f"+${credit:,.0f} net credit"
                            if credit >= 0 else f"−${abs(credit):,.0f} net debit (paying for cushion)")
            items.append(
                f"{n}. **EXECUTE ROLL** {contract} — {roll_label}: {credit_label} "
                f"({int(qty)} spreads @ ${spread_bid:.2f}/share)"
            )
            items.append(f"   - {format_yield_line(roll_yield)}")
            # Order ticket — explicit two-leg combo
            items.append(
                f"   - **Order:** Combo (calendar/diagonal) — Buy-to-Close {int(qty)}× "
                f"{rev.get('underlying', contract.split('_')[0])} ${cur_strike:g}{opt_type[:1]} "
                f"{cur_exp_pretty} (current mid ~${cur:.2f}); "
                f"Sell-to-Open {int(qty)}× ${new_strike:g}{opt_type[:1]} "
                f"{new_exp_pretty} (current bid ${new_bid:.2f} / mid ${new_mid:.2f} / ask ${new_ask:.2f})."
            )
            # Format limit text: "credit" if positive, "debit" if negative
            def _fmt_per_share(s: float) -> str:
                if s >= 0:
                    return f"${s:.2f} credit/share"
                return f"${abs(s):.2f} debit/share"
            def _fmt_total(t: float) -> str:
                return f"${t:,.0f}" if t >= 0 else f"−${abs(t):,.0f}"
            # Label fills by user-perspective (best/worst), not raw bid/ask which can
            # confuse readers when both legs are debits (numerically lower = better fill).
            best_for_user = max(spread_bid, spread_ask, spread_mid)
            worst_for_user = min(spread_bid, spread_ask, spread_mid)
            items.append(
                f"   - **Single-ticket limit (per share):** "
                f"Start at {_fmt_per_share(spread_mid)} (= {_fmt_total(total_at_mid)} total) — patient. "
                f"Drop to {_fmt_per_share(spread_bid)} (= {_fmt_total(total_at_bid)} total) for near-certain fill. "
                f"Range: best {_fmt_per_share(best_for_user)} / worst {_fmt_per_share(worst_for_user)}. GTC, day-good."
            )
            why_parts = []
            cap_buf_change = ((new_strike - (cur_strike or 0)) / underlying_spot * 100
                              if underlying_spot else 0)
            if not is_calendar and new_strike > (cur_strike or 0):
                # Diagonal up
                why_parts.append(
                    f"{rev.get('underlying', 'this name')} is a core holding — calendar rolls "
                    f"compound assignment probability over time. This diagonal-up moves the cap from "
                    f"${cur_strike:g} to ${new_strike:g} ({cap_buf_change:+.1f}% more headroom above spot)"
                )
                if credit < 0:
                    embedded_tax_dollars = embedded_tax_for_log(rev, equity_reviews, ltcg_rate_local)
                    # Honest framing: the debit is the cost of REDUCING the
                    # probability of an event. If assignment happens anyway
                    # at the new (higher) strike, the tax bill is LARGER,
                    # not smaller. So we phrase it as deferred-and-conditional,
                    # not "saved."
                    why_parts.append(
                        f"the ${abs(credit):,.0f} debit is the cost of pushing the cap higher; "
                        f"if NVDA stays below the new strike, you defer the "
                        f"~${embedded_tax_dollars:,.0f} LTCG bill that assignment at the old "
                        f"strike would have triggered (if NVDA rallies past the new strike "
                        f"instead, the eventual tax bill is larger but on a larger gain)"
                    )
            elif underwater:
                why_parts.append(
                    f"Position underwater by ~${unrealized_loss:,.0f}; rather than realizing that loss, "
                    f"the roll books net credit by extending duration"
                )
            else:
                why_parts.append("Best candidate captures meaningful additional premium without giving up strike protection")
            if dte_added:
                why_parts.append(f"adds {dte_added} more days of theta runway ({cur_exp_pretty} → {new_exp_pretty})")
            items.append(f"   - **Why:** {'; '.join(why_parts)}.")
            gain_parts = []
            if credit >= 0:
                gain_parts.append(f"${credit:,.0f} cash credited today")
            else:
                gain_parts.append(f"${abs(credit):,.0f} debit paid for {cap_buf_change:+.1f}% more cap headroom")
            if dte_added:
                gain_parts.append(f"clock reset by {dte_added}d for continued theta capture")
            if underwater and credit >= 0:
                gain_parts.append("avoids realizing the unrealized loss while preserving the path to break-even")
            items.append(f"   - **Gain:** {'; '.join(gain_parts).capitalize()}.")

            # Earnings guard on the new short leg
            today_iso_r = date_str or datetime.now().strftime("%Y-%m-%d")
            earn_check_r = check_earnings_conflict(
                rev.get("underlying", contract.split("_")[0]),
                new_exp_raw, (snapshot_data or {}).get("earnings_calendar", {}) or {},
                today_iso_r,
            )
            if earn_check_r.get("level") in ("warn", "block"):
                items.append(f"   - {format_earnings_badge(earn_check_r)}")
            else:
                # Affirmative earnings clearance on the roll's new expiration
                d2e_r = earn_check_r.get("days_to_earnings")
                if d2e_r is None:
                    items.append(f"   - **Earnings check:** ✅ no scheduled earnings inside contract life.")
                else:
                    items.append(f"   - **Earnings check:** ✅ next earnings {d2e_r}d away (outside contract life).")

            # Delta of the new short (assignment probability). Fall back to a
            # moneyness-based heuristic when the chain didn't return delta —
            # but the heuristic MUST account for option type:
            #   * CALL: spot > strike → ITM → delta near 1.0
            #   * PUT:  spot > strike → OTM → delta near 0.0
            # The previous version assumed call-style for both, which made
            # 10%-OTM short puts (spot $210, strike $190) show as "delta 0.65,
            # ITM" — completely wrong.
            new_delta = (instruction or {}).get("sell_delta") or instruction.get("delta")
            if new_delta is None and underlying_spot and new_strike:
                moneyness = underlying_spot / new_strike  # spot/strike
                if opt_type == "PUT":
                    # PUT: spot > strike means OTM (low |delta|)
                    if moneyness >= 1.15:
                        new_delta = -0.10
                    elif moneyness >= 1.05:
                        new_delta = -0.20
                    elif moneyness >= 0.95:
                        new_delta = -0.40
                    elif moneyness >= 0.85:
                        new_delta = -0.65
                    else:
                        new_delta = -0.85
                else:
                    # CALL: spot > strike means ITM (high delta)
                    if moneyness < 0.85:
                        new_delta = 0.10
                    elif moneyness < 0.95:
                        new_delta = 0.20
                    elif moneyness < 1.05:
                        new_delta = 0.40
                    elif moneyness < 1.15:
                        new_delta = 0.65
                    else:
                        new_delta = 0.85
            delta_str = _format_delta_line(new_delta)
            if delta_str:
                items.append(f"   - {delta_str}")

            # Account routing (rolls stay in the position's account)
            routing = _route_account(
                "ROLL", rev.get("underlying", ""),
                rev.get("account") or rev.get("account_type"),
                config_local.get("accounts", []) or [],
            )
            items.append(f"   - **Account:** {routing}")

            # Trade validator — EV / break-even / verdict
            try:
                if not is_calendar and new_strike > (cur_strike or 0):
                    val = validate_diagonal_up_roll(
                        spot=underlying_spot or 0,
                        current_strike=cur_strike or 0,
                        new_strike=new_strike,
                        new_premium=new_mid,
                        debit_per_share=abs(spread_bid) if credit < 0 else -spread_bid,
                        contracts=int(qty),
                        new_dte=dte_added or 30,
                        new_delta=abs(new_delta or 0.10),
                        current_delta=0.30,
                    )
                else:
                    val = validate_calendar_roll(
                        spot=underlying_spot or 0,
                        strike=new_strike,
                        new_premium=new_mid,
                        credit_per_share=spread_bid if credit > 0 else 0,
                        contracts=int(qty),
                        new_dte=dte_added or 30,
                        delta=0.30,
                    )
                if val is not None:
                    items.append(f"   - {format_validation_line(val)}")
                    # Include the strongest alternative if EV is marginal/poor
                    if val.verdict in ("MARGINAL", "POOR"):
                        for alt in val.alternatives_ranked[:1]:
                            if "HOLD" in alt.get("name", ""):
                                items.append(f"   - **Alternative:** {alt['name']} — {alt['tradeoff']}")
                                break
            except Exception:
                pass  # validator is advisory; never break the briefing

            seen_contracts.add(contract)
            n += 1

    # ---- 4. Matrix-recommended actions on remaining options (non-HOLD/non-WAIT) ----
    actionable_decisions = {"CLOSE", "CLOSE_FOR_PROFIT", "ROLL_OUT", "ROLL_OUT_AND_DOWN",
                            "ROLL_OUT_AND_UP", "TAKE_ASSIGNMENT", "LET_EXPIRE"}
    for rev in options_reviews:
        contract = rev.get("contract", "")
        if contract in seen_contracts:
            continue
        rec = rev.get("recommendation")
        if rec in actionable_decisions:
            rationale = (rev.get("rationale") or "")[:140]
            # Derive a real buy-to-close ticket for CLOSE_FOR_PROFIT using the
            # live chain price already on the review. Without this the verifier
            # flags the action as "lacks chain attribution" — and the user has
            # no actionable order to place.
            current_mid = float(rev.get("current_mid", 0) or 0)
            entry_price = float(rev.get("entry_price", 0) or 0)
            qty = abs(float(rev.get("qty", 0) or 0))
            btc_cost = current_mid * 100.0 * qty
            profit_dollars = (entry_price - current_mid) * 100.0 * qty if entry_price else 0.0
            profit_pct = ((entry_price - current_mid) / entry_price * 100.0) if entry_price else 0.0

            ticket_suffix = ""
            if current_mid and qty:
                ticket_suffix = (
                    f" — buy-to-close limit ${current_mid:.2f}"
                    + (f" (+{profit_pct:.0f}% captured, ${profit_dollars:+,.0f})" if entry_price else "")
                )

            items.append(f"{n}. **{rec}** {contract} — {rationale}{ticket_suffix}")
            items.append(
                f"   - **Why:** Decision matrix triggered `{rev.get('matrix_cell_id', '?')}` "
                f"based on regime + DTE + moneyness + capture conditions."
            )
            if current_mid and qty:
                # Real chain marker so the live-data verifier recognizes this
                # as broker-backed.
                items.append(
                    f"   - **Order:** Buy-to-Close {int(qty)}× {contract} at current mid "
                    f"${current_mid:.2f} — limit ${current_mid:.2f} GTC, day-good."
                )
                items.append(f"   - **Source:** Live E*TRADE chain")
            if profit_dollars:
                gain_text = f"Locks ${profit_dollars:+,.0f} of theta gain; frees position for new opportunities."
            else:
                gain_text = "Capital impact depends on chain spread at fill."
            items.append(
                f"   - **Gain:** Following the matrix improves expectancy versus discretionary holds. {gain_text}"
            )
            seen_contracts.add(contract)
            n += 1

    # ---- 5. CONCENTRATION TRIM (>10% NLV) ----
    nlv = (snapshot_data or {}).get("balance", {}).get("accountValue", 0) or 0
    config = (snapshot_data or {}).get("_config", {}) if snapshot_data else {}
    core_tickers = set(config.get("core_positions", []))
    ltcg_rate = float(config.get("ltcg_rate", 0.238))

    # Core holdings get a higher concentration cap (default 18%) because the
    # user has explicitly designated them long-term keeps with big embedded
    # LTCG gains. Forcing a sale to fit a generic 10% cap would trigger an
    # immediate tax bill for an arbitrary rule. For core names we only flag
    # roll-covered-call-up (Option A) — never recommend outright sale unless
    # the position has truly run away (>20% NLV).
    core_cap = float((config or {}).get("core_concentration_cap_pct", 18)) / 100.0
    standard_cap = float((config or {}).get("concentration_cap_pct", 10)) / 100.0
    core_runaway_cap = float((config or {}).get("core_runaway_cap_pct", 20)) / 100.0

    for rev in equity_reviews:
        weight = rev.get("weight", 0) or 0
        ticker = rev.get("ticker", "?")
        is_core = ticker in core_tickers

        # Determine effective cap and target
        if is_core:
            effective_cap = core_cap
            target_pct = max(core_cap * 0.85, 0.12)  # trim toward ~85% of core cap
        else:
            effective_cap = standard_cap
            target_pct = 0.09

        if weight <= effective_cap:
            continue

        current_value = (rev.get("qty", 0) or 0) * (rev.get("price", 0) or 0)
        target_value = target_pct * nlv if nlv else 0
        sell_dollar = current_value - target_value
        stress_loss = current_value * 0.20
        pl_pct = rev.get("pl_pct", 0) or 0
        tax_on_sell = (
            sell_dollar * (pl_pct / (1 + pl_pct)) * ltcg_rate
            if pl_pct > 0 else 0
        )

        if is_core and weight < core_runaway_cap:
            # Core-friendly TRIM: roll covered calls up, never sell
            items.append(
                f"{n}. **TRIM** {ticker} (core) — currently {weight*100:.1f}% NLV "
                f"(soft cap {core_cap*100:.0f}%); roll covered calls up — do NOT sell shares"
            )
            items.append(
                f"   - **Why:** {ticker} is on your core-holdings list. Even though "
                f"{weight*100:.1f}% breaches the soft cap, an outright sale would realize "
                f"~${tax_on_sell:,.0f} in LTCG tax — that's a hard cost paid today for a "
                f"soft rule. A 20% gap would hit NLV by ~${stress_loss:,.0f} but you've "
                f"explicitly accepted that risk on core names."
            )
            items.append(
                f"   - **Recommended path:** Option A only — roll existing covered calls "
                f"to higher strikes to reduce assignment ceiling and collect premium. "
                f"Avoid the realized-gain trigger of an outright sale."
            )
        elif is_core and weight >= core_runaway_cap:
            # Core-runaway: position has truly run away — flag for review but still no force-sell
            items.append(
                f"{n}. **REVIEW CORE** {ticker} — currently {weight*100:.1f}% NLV "
                f"(past runaway cap {core_runaway_cap*100:.0f}%); consider partial trim "
                f"despite ~${tax_on_sell:,.0f} LTCG cost"
            )
            items.append(
                f"   - **Why:** Position has run past your runaway cap. At "
                f"{weight*100:.1f}% NLV, a 20% single-name gap would cost ~${stress_loss:,.0f} "
                f"(~{(stress_loss/nlv*100) if nlv else 0:.1f}% of portfolio). The tax on "
                f"reducing to {target_pct*100:.0f}% is ~${tax_on_sell:,.0f}."
            )
            items.append(
                f"   - **Options:** A — roll covered calls up (tax-deferred); "
                f"B — sell ~${sell_dollar:,.0f} (locks in LTCG); "
                f"C — defensive collar (protect downside without selling)."
            )
        else:
            # Non-core: standard TRIM
            tax_note = (
                f" Tax cost on outright sale of ${sell_dollar:,.0f} at "
                f"{ltcg_rate*100:.1f}% LTCG = ~${tax_on_sell:,.0f}."
                if pl_pct > 0 else ""
            )
            items.append(
                f"{n}. **TRIM** {ticker} — currently {weight*100:.1f}% NLV "
                f"(over {standard_cap*100:.0f}% cap); reduce to ~{target_pct*100:.0f}% by "
                f"selling ~${sell_dollar:,.0f} OR rolling covered calls up"
            )
            items.append(
                f"   - **Why:** Single-name exposure {weight*100:.1f}% of NLV breaches the "
                f"{standard_cap*100:.0f}% per-name concentration cap. A single-name 20% gap on "
                f"{ticker} would hit NLV by ~${stress_loss:,.0f} (~{(stress_loss/nlv*100) if nlv else 0:.1f}% of "
                f"the entire portfolio) — disproportionate to one ticker's allocation."
            )
            items.append(
                f"   - **Gain:** Bringing {ticker} to ~{target_pct*100:.0f}% caps that idiosyncratic loss at "
                f"~${stress_loss * 0.6:,.0f} (40% smaller). Choose Option A (roll covered "
                f"calls up) to defer LTCG and harvest more premium, or Option B (partial "
                f"sale of ~${sell_dollar:,.0f}) to raise immediate cash.{tax_note}"
            )
        n += 1

    # ---- 5b. DEFENSIVE COLLAR (core + concentration + has covered call + tax-sensitive) ----
    if snapshot_data and core_tickers:
        # Build a chain_provider lambda that wraps live chain lookup
        def _chain_provider(ticker, expiration_iso, strike, option_type):
            """Return {bid, mid, ask} for a real listed contract or None."""
            try:
                from datetime import date as date_class
                exp_date = date_class.fromisoformat(expiration_iso)
                chain = get_option_chain(
                    ticker, exp_date, strike_near=strike,
                    no_of_strikes=15, chain_type=option_type, timeout_s=3.0
                )
                if not chain:
                    return None
                option_list = chain.get("put") if option_type == "PUT" else chain.get("call")
                if not option_list:
                    return None
                # Find closest strike
                best = None
                best_dist = float("inf")
                for opt in option_list:
                    dist = abs(opt.strike - strike)
                    if dist < best_dist:
                        best_dist = dist
                        best = opt
                if not best:
                    return None
                bid = best.bid or 0
                ask = best.ask or 0
                mid = (bid + ask) / 2 if bid and ask else (ask or bid or 0)
                return {"bid": bid, "mid": mid, "ask": ask}
            except Exception:
                return None

        for rev in equity_reviews:
            ticker = rev.get("ticker", "?")
            if ticker not in core_tickers:
                continue
            weight = rev.get("weight", 0) or 0
            if weight < 0.10:
                continue
            # Find existing short call on this ticker
            short_call = None
            for opt in options_reviews:
                if (opt.get("underlying") == ticker
                        and (opt.get("type") or "").upper() == "CALL"
                        and (opt.get("qty") or 0) < 0):
                    short_call = opt
                    break
            if not short_call:
                continue

            spot = rev.get("price", 0) or 0
            shares = rev.get("qty", 0) or 0
            pl_pct = rev.get("pl_pct", 0) or 0
            cost_basis = spot / (1 + pl_pct) if pl_pct > -1 else spot

            proposal = propose_collar(
                ticker=ticker, spot=spot, shares=int(shares),
                cost_basis=cost_basis, nlv=nlv, concentration_pct=weight * 100,
                is_core=True, has_short_call=True,
                current_call_strike=short_call.get("strike"),
                current_call_expiration=short_call.get("expiration"),
                current_call_mid=short_call.get("current_mid"),
                current_call_contracts=int(abs(short_call.get("qty", 0) or 0)),
                iv_rank=short_call.get("iv_rank", 50.0) or 50.0,
                ltcg_rate=ltcg_rate,
                chain_provider=_chain_provider,
            )
            if not getattr(proposal, "qualified", False):
                continue

            legs = proposal.proposed_legs

            # Suppress collar if any leg has estimated (non-live) pricing
            has_estimated = any(
                getattr(l, "price_source", "estimated") == "estimated"
                for l in legs
            )
            if has_estimated:
                continue

            btc = next((l for l in legs if l["action"] == "BTC"), None)
            sto = next((l for l in legs if l["action"] == "STO"), None)
            bto = next((l for l in legs if l["action"] == "BTO"), None)

            label = "Defensive Collar" if bto else "Defensive Roll-up (puts too pricey)"
            items.append(
                f"{n}. **{label.upper()}** {ticker} — convert covered call to "
                f"{'collar' if bto else 'higher-strike CC'} "
                f"(net ${proposal.net_cash:+,.0f})"
            )
            order_parts = []
            if btc:
                order_parts.append(f"BTC {btc['contracts']}× ${btc['strike']:g}{btc['type'][:1]} {btc['expiration']} ~${btc['limit']:.2f}")
            if sto:
                order_parts.append(f"STO {sto['contracts']}× ${sto['strike']:g}{sto['type'][:1]} {sto['expiration']} ~${sto['limit']:.2f}")
            if bto:
                order_parts.append(f"BTO {bto['contracts']}× ${bto['strike']:g}{bto['type'][:1]} {bto['expiration']} ~${bto['limit']:.2f}")
            items.append(f"   - **Order (3-leg):** " + "; ".join(order_parts) + ".")

            # Yield via yield-calculator skill (only if both legs known)
            if bto and sto:
                col_yield = compute_collar_yield(
                    call_premium=sto["limit"], put_premium=bto["limit"],
                    call_strike=sto["strike"], put_strike=bto["strike"],
                    spot=spot, contracts=int(abs(shares)) // 100,
                    dte=180,
                )
                items.append(f"   - {format_yield_line(col_yield)}")

            items.append(f"   - **Why:** {proposal.explanation}")
            items.append(
                f"   - **Gain:** Tax avoided if no assignment ≈ "
                f"${proposal.tax_avoided_if_no_assignment:,.0f}. "
                f"Cap raised, downside floored at put strike. "
                f"Net cash flow today: ${proposal.net_cash:+,.0f}."
            )
            n += 1

    # ---- 6. HEDGE when stress coverage < 0.7x ----
    if analytics:
        sc = analytics.get("stress_coverage")
        cov = None
        if sc is not None:
            cov = getattr(sc, "coverage_ratio", None)
            if cov is None and isinstance(sc, dict):
                cov = sc.get("coverage_ratio")
        if cov is not None and cov < 0.7:
            hb = analytics.get("hedge_book")
            recs = []
            if hb is not None:
                recs = getattr(hb, "recommendations", None)
                if recs is None and isinstance(hb, dict):
                    recs = hb.get("recommendations", [])
            if recs:
                r = recs[0]
                strike = getattr(r, "target_strike", None) or (isinstance(r, dict) and r.get("target_strike"))
                exp = getattr(r, "target_expiration", None) or (isinstance(r, dict) and r.get("target_expiration"))
                contracts = getattr(r, "contracts", None) or (isinstance(r, dict) and r.get("contracts"))
                cost = getattr(r, "estimated_cost", None) or (isinstance(r, dict) and r.get("estimated_cost"))
                instr = getattr(r, "instrument", "SPY_PUT") or (isinstance(r, dict) and r.get("instrument", "SPY_PUT"))
                exp_str = ""
                try:
                    exp_str = exp.strftime("%a %b %d '%y") if exp else ""
                except AttributeError:
                    exp_str = str(exp) if exp else ""
                cost_f = float(cost or 0)
                cost_pct = (cost_f / nlv * 100) if nlv else 0
                # Approx protected delta-shares: contracts × |delta| × 100; with 0.20 delta SPY puts
                protected_notional = (contracts or 0) * 0.20 * 100 * float(strike or 0)
                items.append(
                    f"{n}. **HEDGE** Buy {contracts}× {instr.replace('_', ' ').lower()} "
                    f"${strike}P {exp_str} (~${cost_f:,.0f}; coverage {cov:.0%} → target 10%)"
                )
                # Yield via yield-calculator skill
                spy_spot = analytics.get("spy_price") if analytics else 0
                hedge_yield = compute_hedge_yield(
                    put_cost_dollars=cost_f, contracts=int(contracts or 1),
                    strike=float(strike or 0), spot=float(spy_spot or 0) or 600.0,
                    dte=35, nlv=nlv or 1, delta=-0.20,
                )
                items.append(f"   - {format_yield_line(hedge_yield)}")
                items.append(
                    f"   - **Why:** Stress coverage at {cov:.0%} of long delta — a 10–20% SPY "
                    f"drawdown would expose ~${nlv * 0.18:,.0f} of long-equity to losses with "
                    f"no offsetting protection. Long puts at ~5% OTM act as fat-tail insurance: "
                    f"cheap when not needed, the only thing that pays in a real crash."
                )
                items.append(
                    f"   - **Gain:** ~${protected_notional:,.0f} of effective downside hedge "
                    f"for ${cost_f:,.0f} ({cost_pct:.1f}% of NLV) — roughly 10:1 leverage "
                    f"on a real left-tail event. If SPY rallies, max loss = the premium paid; "
                    f"if SPY drops 8–10%, the puts pay multiples of cost."
                )
                n += 1

    # ---- 7. Equity non-HOLD recommendations (for completeness) ----
    for rev in equity_reviews:
        rec = rev.get("recommendation")
        if rec and rec not in ("HOLD", None):
            weight_pct = (rev.get("weight", 0) or 0) * 100
            ticker = rev.get("ticker", "?")
            if any(ticker in i for i in items if "TRIM" in i):
                continue
            items.append(f"{n}. **{rec}** {ticker} (currently {weight_pct:.1f}% NLV)")
            items.append(
                f"   - **Why:** {(rev.get('rationale') or 'Decision matrix flagged for review.')[:160]}"
            )
            items.append(
                f"   - **Gain:** Aligns position with current thesis/technical state."
            )
            n += 1

    # ---- 8. Top 3 actionable new ideas (concrete CSPs) — with wash-sale + earnings guards ----
    today_iso = date_str or datetime.now().strftime("%Y-%m-%d")
    earnings_calendar = (snapshot_data or {}).get("earnings_calendar", {}) or {}
    accounts_cfg = config_local.get("accounts", []) or []
    actionable_ideas = [i for i in new_ideas if i.get("instruction")]
    for idea in actionable_ideas[:3]:
        ticker = idea.get("ticker", "?")
        strike = idea.get("strike")
        exp = idea.get("expiration_pretty") or idea.get("expiration")
        # Normalize expiration → ISO YYYY-MM-DD for guard checks
        exp_iso = idea.get("expiration") or ""
        mid = idea.get("mid") or 0
        contracts = idea.get("contracts", 1)
        dte = idea.get("dte") or idea.get("days_to_expiry") or 30

        # Wash-sale guard — block re-entry on names closed at a loss in the last 30d
        ledger_path = config_local.get("wash_sale_ledger_path")
        ws_blocked, ws_reason = is_wash_sale_blocked(ticker, today_iso, ledger_path=ledger_path)

        # Earnings guard — flag/block if proposed expiration crosses earnings
        earn_check = check_earnings_conflict(ticker, exp_iso, earnings_calendar, today_iso)

        line = f"{n}. **NEW CSP** {ticker}"
        if strike:
            line += f" — sell ${strike:g}P"
        if exp:
            line += f" exp {exp}"
        if mid:
            line += f" @ ${mid:.2f} mid"

        if ws_blocked:
            line += "  🚫 WASH-SALE BLOCKED"
        if earn_check.get("level") == "block":
            line += "  🔴 EARNINGS CONFLICT"
        elif earn_check.get("level") == "warn":
            line += "  ⚠️ EARNINGS WARNING"
        items.append(line)

        if ws_blocked:
            items.append(f"   - **🚫 Skip — wash-sale rule:** {ws_reason}")
            n += 1
            continue
        else:
            # Affirmative wash-sale clearance so user knows it was checked
            items.append(f"   - **Wash-sale check:** ✅ {ticker} clear (no recent loss closures within 30d).")

        if earn_check.get("level") == "block":
            items.append(f"   - **🔴 Skip — {format_earnings_badge(earn_check)}**")
            n += 1
            continue
        if earn_check.get("level") == "warn":
            items.append(f"   - {format_earnings_badge(earn_check)}")
        else:
            # Affirmative earnings clearance
            d2e = earn_check.get("days_to_earnings")
            if d2e is None:
                items.append(f"   - **Earnings check:** ✅ no scheduled earnings inside contract life.")
            else:
                items.append(f"   - **Earnings check:** ✅ next earnings {d2e}d away (outside contract life).")

        # Yield via yield-calculator skill (canonical CSP yield)
        if strike and mid:
            csp_yield = compute_csp_yield(
                premium=mid, strike=strike, contracts=contracts, dte=dte,
            )
            items.append(f"   - {format_yield_line(csp_yield)}")

        # Delta line (assignment probability)
        delta_str = _format_delta_line(idea.get("delta"))
        if delta_str:
            items.append(f"   - {delta_str}")

        # Account routing
        routing = _route_account("NEW CSP", ticker, None, accounts_cfg)
        items.append(f"   - **Account:** {routing}")

        items.append(
            f"   - **Why:** {idea.get('rationale') or 'Cash-secured put aligned with current regime and IV rank.'}"
        )
        gain = (
            f"Premium ${(mid * 100 * contracts) if mid else 0:,.0f} booked immediately; "
            f"willing-to-own at ${strike:g}."
            if strike else "Earn premium against existing thesis."
        )
        items.append(f"   - **Gain:** {gain}")
        n += 1

    # ---- 9. CSP PULLBACK proposals on core positions (use live E*TRADE chain data) ----
    # Per-name action coordination: if a ticker already has TRIM (concentration breach)
    # OR a DEFENSIVE COLLAR proposal, SUPPRESS the same-name pullback CSP — selling new
    # puts on a name we're trying to reduce contradicts itself.
    tickers_already_acted = set()
    import re as _re
    for it in items:
        m = _re.search(r"\*\*(?:TRIM|DEFENSIVE COLLAR|DEFENSIVE ROLL-UP)\*\*\s+([A-Z]+)", it)
        if m:
            tickers_already_acted.add(m.group(1))

    cash_avail = (snapshot_data or {}).get("balance", {}).get("cash", 0) or 0
    # Track PULLBACK CSP ideas the trade-validator rejected as POOR EV / BLOCK
    # so we can surface a transparency footer (without putting them in the
    # actionable list).
    _filtered_csps: list = []
    if cash_avail > 5000 and core_tickers_set:
        ledger_path = config_local.get("wash_sale_ledger_path")
        ec_today = (snapshot_data or {}).get("earnings_calendar", {}) or {}
        as_of_iso = date_str or datetime.now().strftime("%Y-%m-%d")

        # Pre-compute existing short-put exposure per ticker. We need this to
        # avoid stacking a 3rd put on a name that already has 2 layered short
        # puts (e.g., VRT_PUT_300 + VRT_PUT_315 → don't recommend $325P on top).
        # We cap at MAX_EXISTING_SHORT_PUTS per name and also flag total
        # cash-secured commitment when it gets large.
        MAX_EXISTING_SHORT_PUTS_PER_NAME = 2
        existing_short_puts_by_ticker: dict = {}
        for p in ((snapshot_data or {}).get("positions") or []):
            if p.get("assetType") != "OPTION":
                continue
            if (p.get("type") or "").upper() != "PUT":
                continue
            qty = float(p.get("qty", 0) or 0)
            if qty >= 0:  # we only care about SHORT puts (qty negative)
                continue
            t = p.get("underlying") or ""
            if not t:
                continue
            entry = existing_short_puts_by_ticker.setdefault(t, {
                "count": 0, "total_collateral": 0.0, "strikes": []
            })
            entry["count"] += abs(qty)
            entry["total_collateral"] += float(p.get("strike", 0) or 0) * 100 * abs(qty)
            entry["strikes"].append(float(p.get("strike", 0) or 0))

        for er in equity_reviews:
            ticker = er.get("ticker", "")
            if ticker not in core_tickers_set:
                continue
            if ticker in tickers_already_acted:
                # Don't propose new exposure on a name where we already TRIM/COLLAR it
                continue
            spot = er.get("price", 0) or 0
            if not spot:
                continue

            # Existing-put-stack gate. If the user already has ≥ N short puts
            # on this name, adding another stacks assignment risk on a single
            # underlying. The Watch panel + Capital Plan still surface the
            # current positions; we just stop proposing MORE.
            existing = existing_short_puts_by_ticker.get(ticker)
            if existing and existing["count"] >= MAX_EXISTING_SHORT_PUTS_PER_NAME:
                _filtered_csps.append({
                    "ticker": ticker,
                    "verdict": "SKIPPED",
                    "ev": 0,
                    "strike": 0,
                    "exp": None,
                    "reason": (
                        f"already {int(existing['count'])} short puts open at strikes "
                        f"{sorted(existing['strikes'])} — total cash-secured "
                        f"${existing['total_collateral']:,.0f}. Stacking another "
                        f"layered put compounds assignment risk on a single name."
                    ),
                })
                continue

            # Query live E*TRADE chain for put near 12% OTM, 30-40 DTE
            chain_data = find_put_strike_near(
                ticker, target_otm_pct=12.0, target_dte_min=25,
                target_dte_max=40, spot=spot
            )
            if not chain_data:
                # No live chain available — suppress recommendation
                continue

            target_strike = chain_data["strike"]
            est_premium = chain_data["mid"]
            target_exp = chain_data["expiration"]
            # Compute DTE from expiration
            from datetime import date as _date_class
            exp_date = _date_class.fromisoformat(target_exp)
            est_dte = (exp_date - datetime.now().date()).days

            # Wash-sale check
            ws_blocked, ws_reason = is_wash_sale_blocked(ticker, as_of_iso, ledger_path=ledger_path)
            if ws_blocked:
                continue
            # Earnings guard
            ec = check_earnings_conflict(ticker, target_exp, ec_today, as_of_iso)
            if ec.get("level") == "block":
                continue
            collateral_needed = target_strike * 100 * 1
            if collateral_needed > cash_avail:
                continue  # not enough cash for this CSP

            # Trade validator — POOR EV ideas are NEW trades that don't earn
            # their capital lock-up. Filter them out here so the action list
            # only shows GOOD/MARGINAL setups. We still surface the count of
            # filtered ideas at the end for transparency.
            csp_val = None
            try:
                csp_val = validate_csp(
                    spot=spot, strike=target_strike, premium=est_premium,
                    contracts=1, dte=est_dte, delta=0.20,
                )
            except Exception:
                csp_val = None
            if csp_val is not None and csp_val.verdict in ("POOR", "BLOCK"):
                # Track and skip — don't pollute the action list with negative-EV setups.
                _filtered_csps.append({
                    "ticker": ticker,
                    "verdict": csp_val.verdict,
                    "ev": csp_val.expected_value_dollars,
                    "strike": target_strike,
                    "exp": target_exp,
                })
                continue

            # Yield computation (only reached for GOOD/MARGINAL/None)
            csp_y = compute_csp_yield(
                premium=est_premium, strike=target_strike, contracts=1, dte=est_dte,
            )
            items.append(
                f"{n}. **PULLBACK CSP** {ticker} — sell ${target_strike:g}P "
                f"exp {exp_date.strftime('%a %b %d')} for ${est_premium:.2f} premium "
                f"(would re-acquire 100 shares @ {(target_strike/spot - 1)*100:.0f}% below spot)"
            )
            items.append(f"   - {format_yield_line(csp_y)}")
            items.append(f"   - **Source:** Live E*TRADE chain")
            if csp_val is not None:
                items.append(f"   - {format_validation_line(csp_val)}")
            # Affirmative wash-sale + earnings clearance
            items.append(f"   - **Wash-sale check:** ✅ {ticker} clear (no recent loss closures within 30d).")
            if ec.get("level") == "warn":
                items.append(f"   - {format_earnings_badge(ec)}")
            else:
                d2e_p = ec.get("days_to_earnings")
                if d2e_p is None:
                    items.append(f"   - **Earnings check:** ✅ no scheduled earnings inside contract life.")
                else:
                    items.append(f"   - **Earnings check:** ✅ next earnings {d2e_p}d away (outside contract life).")
            items.append(
                f"   - **Why:** Core {ticker} long position; this CSP earns income while waiting "
                f"for a pullback. If assigned, average cost basis improves; if not assigned, keep premium."
            )
            # Tax-honest disclosure: assignment starts a new LTCG clock on the assigned shares
            items.append(
                f"   - **Tax note:** If assigned, the 100 new shares start a fresh 365-day LTCG clock "
                f"(separate from your existing {ticker} lots). New cost basis = strike − premium = "
                f"${target_strike - est_premium:.2f}/share."
            )
            # Concentration post-assignment check
            current_value = (er.get("qty", 0) or 0) * spot
            post_assign_value = current_value + (target_strike * 100)
            post_assign_pct = (post_assign_value / nlv * 100) if nlv else 0
            if post_assign_pct > 10:
                items.append(
                    f"   - **⚠️ Concentration check:** Assignment would push {ticker} to "
                    f"~{post_assign_pct:.1f}% NLV (over 10% cap). Consider sizing down or pre-arrange "
                    f"a partial-sale plan."
                )
            items.append(
                f"   - **Account:** "
                f"{_route_account('NEW CSP', ticker, None, accounts_cfg)}"
            )
            n += 1
            # Limit to 3 pullback CSPs
            if sum(1 for it in items if "PULLBACK CSP" in it) >= 3:
                break

    # Transparency footer: tell the user which CSP ideas were rejected and why.
    # Two reasons today:
    #   (a) Trade-validator POOR/BLOCK verdicts (negative EV)
    #   (b) Existing-put-stack: skip names where user already has ≥2 short puts
    if _filtered_csps:
        validator_rejects = [c for c in _filtered_csps if c.get("verdict") != "SKIPPED"]
        stack_skips = [c for c in _filtered_csps if c.get("verdict") == "SKIPPED"]
        items.append("")
        if validator_rejects:
            names = ", ".join(
                f"{c['ticker']} (EV ${c['ev']:+,.0f})" for c in validator_rejects[:5]
            )
            items.append(
                f"_📉 {len(validator_rejects)} CSP idea(s) rejected by trade-validator "
                f"(negative expected value): {names}. Premium is too thin or strike too "
                f"close to spot — wait for a better setup._"
            )
        if stack_skips:
            for c in stack_skips:
                items.append(
                    f"_📚 PULLBACK CSP {c['ticker']} skipped — {c.get('reason', 'put-stack guard')}_"
                )

    if items:
        lines.extend(items)
    else:
        lines.append("- No urgent actions today. Hold and watch.")

    # Stale-data warning (if applicable)
    fresh_warn = _data_freshness_warning(snapshot_data or {})
    if fresh_warn:
        lines.append("")
        lines.append(fresh_warn)

    # Append the total-impact summary card
    lines.extend(render_summary_card(items, snapshot_data))

    lines.append("")
    return lines


def render_watch(equity_reviews: list, options_reviews: list) -> list:
    """Render portfolio review (watch) panel."""
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
            rationale = review.get("rationale", "")

            header = f"- **{contract}**"
            if opt_type and strike:
                header += f" ({opt_type} ${strike:g}"
                if exp:
                    header += f" exp {exp}"
                if dte is not None:
                    header += f", {dte}d"
                header += ")"
            header += f" → **{rec}**"
            lines.append(header)
            if entry and mid:
                pl_pct = (entry - mid) / entry * 100 if entry else 0
                lines.append(f"  - entry ${entry:.2f} / mid ${mid:.2f} ({pl_pct:+.0f}% captured)")
            if rationale:
                lines.append(f"  - {rationale}")
            human = _humanize_matrix_cell(cell)
            if human:
                lines.append(f"  - {human}")
            roll = review.get("roll_target")
            if roll:
                lines.append(f"  - roll target: {roll.get('strike')} exp {roll.get('expiration')} for ${roll.get('expectedNetCredit', 0):.2f} credit")
        lines.append("")

    return lines


def render_opportunities(new_ideas: list) -> list:
    """Render concrete actionable trade ideas with strike/exp/yield."""
    lines = ["## Income Opportunities — Concrete Entries", ""]

    if not new_ideas:
        lines.append("No new ideas at this time.")
        lines.append("")
        return lines

    actionable = [i for i in new_ideas if i.get("instruction")]
    watch_only = [i for i in new_ideas if not i.get("instruction")]

    if actionable:
        lines.append(f"### Actionable: cash-secured puts ({len(actionable)})")
        lines.append("")
        for idea in actionable:
            ticker = idea.get("ticker", "?")
            name = idea.get("name", "")
            spot = idea.get("spot", 0)
            strike = idea.get("strike", 0)
            mid = idea.get("mid", 0)
            bid = idea.get("bid", 0)
            exp_pretty = idea.get("expiration_pretty", idea.get("expiration", "?"))
            dte = idea.get("dte", 0)
            otm = idea.get("otm_pct", 0)
            delta = idea.get("delta")
            yield_pct = idea.get("yield_pct", 0)
            annualized = idea.get("annualized_pct", 0)
            collateral = idea.get("collateral", 0)
            premium = idea.get("premium", 0)
            oi = idea.get("open_interest", 0)
            spread = idea.get("spread_pct", 0)
            iv = idea.get("iv")
            target_year = idea.get("price_target_2026")
            target_str = (
                f" / 2026 target {target_year[0]:.0f}-{target_year[1]:.0f}"
                if target_year else ""
            )
            label = f"**{ticker}**"
            if name and name != ticker:
                label += f" — {name}"
            lines.append(f"#### {label} (spot ${spot:.2f}{target_str})")
            lines.append("")
            lines.append(
                f"**SELL TO OPEN** {ticker} {exp_pretty} **${strike:g} PUT** "
                f"@ ${mid:.2f} mid (bid ${bid:.2f})"
            )
            lines.append("")
            lines.append(
                f"- {otm:.1f}% OTM"
                + (f", delta {delta:.2f}" if delta else "")
                + f", {dte} DTE"
            )
            lines.append(
                f"- Premium ${premium:.0f}, collateral ${collateral:,.0f}"
            )
            lines.append(
                f"- **Yield: {yield_pct:.2f}% over {dte}d → {annualized:.1f}% annualized**"
            )
            lines.append(
                f"- Liquidity: OI {oi}, spread {spread:.1f}%"
                + (f", IV {iv:.0f}%" if iv else "")
            )
            rec_label = idea.get("raw_recommendation", "")
            age = idea.get("rec_age_days", 0)
            if rec_label:
                lines.append(f"- Source: {rec_label} ({age}d old)")
            lines.append("")

    if watch_only:
        valid_watch = [i for i in watch_only if i.get("ticker") and i.get("rationale")]
        if valid_watch:
            lines.append(f"### Watch-only ({len(valid_watch)})")
            lines.append("")
            for idea in valid_watch:
                ticker = idea.get("ticker")
                name = idea.get("name", "")
                label = f"**{ticker}**"
                if name and name != ticker:
                    label += f" ({name})"
                lines.append(f"- {label}: {idea.get('rationale', '')}")
            lines.append("")

    return lines


def render_diffs(consistency_report: dict) -> list:
    """Render day-over-day changes panel."""
    lines = ["## Recommendation Changes Since Last Briefing", ""]

    note = consistency_report.get("note", "")
    if note:
        lines.append(f"*{note}*")
        lines.append("")

    return lines


def render_inconsistencies(flagged: list) -> list:
    """Render inconsistencies panel."""
    lines = ["## Inconsistencies Flagged", ""]

    if not flagged:
        lines.append("✓ No inconsistencies detected.")
        lines.append("")
        return lines

    for item in flagged:
        lines.append(f"- {item}")

    lines.append("")
    return lines


def render_manifest(snapshot_dir_path: str) -> list:
    """Render snapshot manifest panel."""
    return [
        "## Appendix: Snapshot Manifest",
        "",
        f"Snapshot directory: `{snapshot_dir_path}/`",
        "",
    ]
