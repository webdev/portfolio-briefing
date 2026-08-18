"""
Render panel modules for briefing sections.

Each function returns a list of markdown lines.
"""

from datetime import datetime
from pathlib import Path
import re
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
    if cell == "GUARDRAIL_STRIKE_TESTED":
        # Task #38: strike-tested pre-matrix guardrail on short puts
        return "🎯 Strike tested — credit-roll window open; roll down-and-out while extrinsic is peak"

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

from analysis import rsi_discipline  # noqa: E402  (scripts dir on sys.path above)
from analysis import capacity_gate  # noqa: E402
from analysis import chase_guard  # noqa: E402
from analysis import lt_verdict_gate  # noqa: E402
from analysis import put_overlap_check  # noqa: E402

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
                        min_credit_threshold=1000.0, **kwargs):
        # Fallback: simple max-credit pick (call-side semantics). Side-aware:
        # never pick a HIGHER strike for a put (deeper ITM = more risk).
        is_put = (kwargs.get("option_type") or "CALL").upper() == "PUT"
        best = None
        for c in candidates or []:
            if c.get("id") == "A":
                continue
            if is_put:
                cur = float(c.get("current_strike") or 0)
                new = float(((c.get("instruction") or {}).get("sell_strike")) or 0)
                if cur and new > cur + 0.01:
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
    from analysis.earnings_guard import (  # type: ignore
        check_earnings_conflict,
        format_earnings_badge,
        format_new_open_block,
    )
except ImportError:
    def check_earnings_conflict(ticker, expiration, earnings_calendar, as_of):
        return {"conflict": False, "level": "none", "days_to_earnings": None,
                "message": "", "spans_expiration": False, "earnings_date": None}
    def format_earnings_badge(check_result):
        return ""
    def format_new_open_block(ticker, check_result, expiration):
        return f"🚫 BLOCK (EARNINGS_WINDOW) — {ticker} contract spans earnings"

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
    gate_state=None,
    balance: dict = None,
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
    lines.append(f"**Portfolio NLV:** ${nlv:,.0f} | **Cash:** ${cash:,.0f} ({cash_pct:.1f}%)")
    # NLV reconciliation (2026-08-04 bug: rendered $1,149,562 vs the broker's
    # own $1,082,940.74 — short-option liabilities were dropped from a
    # reconstructed NLV). When computed and broker NLV diverge > 1%, surface
    # both numbers right under the figure — never silently ship a
    # reconstruction that disagrees with broker truth (rules #10/#19).
    _rec = (balance or {}).get("nlv_reconciliation") or {}
    if _rec.get("warning"):
        lines.append(
            f"🔴 **NLV data-integrity check:** computed NLV "
            f"${_rec.get('computed_nlv', 0):,.0f} vs broker "
            f"${_rec.get('broker_nlv', 0):,.0f} — Δ ${_rec.get('delta', 0):+,.0f} "
            f"({_rec.get('pct', 0):.1f}%); using the broker figure."
        )
    # Capacity gate banner — printed daily right after the cash line
    # (06-wheel-parameters.md §7A) so the operator never has to compute
    # whether the portfolio has room for new short puts.
    if gate_state is not None and getattr(gate_state, "banner", ""):
        lines.append(f"**{gate_state.banner}**")
    lines.append(f"**Action Items:** {action_count}")

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
        lines.append("**Net Greeks** — your portfolio's directional + time + volatility exposure")
        lines.append("")
        # ── DELTA ─────────────────────────────────────────────────────
        # Directional exposure expressed in equivalent shares of underlying.
        # Equity delta = qty (a share is always 1.0 delta). Option delta is
        # signed by qty (long call positive, short put positive, etc.).
        lines.append(f"- **Δ Delta: {net_delta:+,.0f} shares** *(equity {equity_delta:+,.0f} + options {opt_delta:+,.0f})*")
        delta_sense = ("long-biased — your book gains when the market rises"
                       if net_delta > 0
                       else "short-biased — your book gains when the market falls"
                       if net_delta < 0 else "delta-neutral")
        # Approximate $ exposure assuming SPY-like price scale on the equiv share count
        lines.append(f"   - **What it means:** for every $1 move in the average underlying, "
                     f"your book moves ~${abs(net_delta):+,.0f}. You are {delta_sense}.")
        lines.append("   - **Rule of thumb:** delta close to 0 = market-neutral. Big positive delta = "
                     "you're essentially long N shares of the market.")

        # ── THETA ─────────────────────────────────────────────────────
        # Daily time decay. Positive = income (short premium), negative = cost
        # (long premium). For a wheel book this is the income engine.
        theta_sense = ("collecting" if opt_theta > 0 else "paying" if opt_theta < 0 else "neutral on")
        theta_annual = opt_theta * 252  # 252 trading days/year
        lines.append(f"- **Θ Theta: ${opt_theta:+,.0f} / day** *(annualized ≈ ${theta_annual:+,.0f})*")
        lines.append(f"   - **What it means:** options lose value as time passes — theta is the daily rate. "
                     f"You're **{theta_sense}** ${abs(opt_theta):,.0f}/day from time decay.")
        lines.append("   - **Rule of thumb:** positive theta = wheel income working. Negative theta = you "
                     "PAID for time (LEAPs, protective puts) — that's the cost of the insurance.")

        # ── VEGA ──────────────────────────────────────────────────────
        # Sensitivity to a 1% change in implied volatility.
        # Negative vega = short vol (you GAIN when IV drops, LOSE when IV spikes).
        vega_sense = ("short vol — you GAIN when IV drops, LOSE when IV spikes (e.g., VIX shock)"
                      if opt_vega < 0
                      else "long vol — you GAIN when IV spikes, LOSE when IV crushes"
                      if opt_vega > 0 else "vega-neutral")
        lines.append(f"- **ν Vega: ${opt_vega:+,.0f} per 1% IV move**")
        lines.append(f"   - **What it means:** for every 1 point IV change across your options, your book "
                     f"moves ${opt_vega:+,.0f}. You are **{vega_sense}**.")
        lines.append("   - **Rule of thumb:** short-put / short-call wheel books are naturally **short vega** "
                     "— a VIX spike from 18 → 28 (a +10 IV move) would cost you "
                     f"~${abs(opt_vega) * 10:,.0f}. Hedge with long puts when vega is large and negative.")

        # ── GAMMA ─────────────────────────────────────────────────────
        # Rate of change of delta. Negative gamma = adverse moves compound
        # (short premium pain accelerates as the underlying moves against you).
        gamma_sense = ("short gamma — adverse moves COMPOUND against you "
                       "(short-put/call pain accelerates as spot moves against your strike)"
                       if opt_gamma < 0
                       else "long gamma — favorable moves COMPOUND in your favor "
                       "(rare in wheel books)"
                       if opt_gamma > 0 else "gamma-neutral")
        lines.append(f"- **Γ Gamma: {opt_gamma:+,.2f}**")
        lines.append(f"   - **What it means:** the rate at which delta itself changes per $1 move in "
                     f"underlying. You are **{gamma_sense}**.")
        lines.append("   - **Rule of thumb:** short-gamma is the wheel-seller's hidden enemy — a -5% "
                     "market day doesn't just cost you 5×delta, it costs more because delta accelerates. "
                     "Watch this number near earnings or when puts are at-the-money.")
        if unknown_greeks:
            lines.append("")
            lines.append(f"_({unknown_greeks} contract(s) missing Greeks data — values above exclude them)_")

    lines.append("")
    return lines


def render_risk_alerts(
    equity_reviews: list, options_reviews: list, regime_data: dict,
    put_buckets: list | None = None,
    config: dict | None = None,
    credit_window_alerts: list | None = None,
) -> list:
    """Real risk alerts — surface anything material from the reviews.

    ``put_buckets`` is the list of PutBucketCluster objects from
    analyze_put_buckets — critical-severity buckets get a dedicated Risk Alert
    so users see them at the top of the briefing, distinct from the static
    stress-coverage ratio (which assumes ALL puts assign at once and overstates
    real risk on a well-laddered book).

    ``config`` carries the briefing.yaml dict — when it includes a
    ``position_tiers`` block, per-name concentration alerts switch to
    tier-aware caps (Tier A names tolerate up to ~22% NLV before warning,
    Tier B ~12%, Tier C ~8%). CLAUDE.md hard rule #29."""
    lines = ["## Risk Alerts", ""]
    alerts = []

    regime = (regime_data or {}).get("regime", "NORMAL")
    if regime in ("CAUTION", "RISK_OFF"):
        alerts.append(f"⚠️ Regime is **{regime}** — new long entries suppressed")

    # Task #38 Part 2: credit-roll window transitions (open→closing /
    # open→debit_only since the last briefing). Pre-formatted by
    # analysis.credit_windows.transition_alerts; first-run → empty (fail-open).
    for cw_alert in (credit_window_alerts or []):
        alerts.append(cw_alert)

    # Put-bucket concentration on a single Friday.
    # Critical (≥30% NLV) gets ⚠️; Warning (≥20% NLV) gets 📊. This is separate
    # from the per-name 10% cap (equity concentration) and from the stress-
    # coverage ratio (which assumes simultaneous assignment — a fantasy on a
    # laddered book).
    for bucket in (put_buckets or []):
        sev = getattr(bucket, "severity", None)
        if sev not in ("critical", "warning"):
            continue
        exp_str = bucket.expiration.strftime("%a %b %d '%y")
        names_sorted = sorted(bucket.names.items(), key=lambda kv: -kv[1])
        top_names = ", ".join(t for t, _ in names_sorted[:5])
        more = f" + {len(names_sorted) - 5} more" if len(names_sorted) > 5 else ""
        days = f", {bucket.days_to_expiry}d out" if bucket.days_to_expiry is not None else ""
        emoji = "⚠️" if sev == "critical" else "📊"
        verdict = "over 30% NLV cluster" if sev == "critical" else "approaching 30% NLV cap"
        alerts.append(
            f"{emoji} Expiration cluster on **{exp_str}**{days} — "
            f"{bucket.contract_count} short puts, ${bucket.total_obligation:,.0f} obligation "
            f"({bucket.pct_of_nlv*100:.1f}% NLV, {verdict}). Names: {top_names}{more}"
        )

    # Concentration warnings — tier-aware when `config.position_tiers` is set.
    # Tier A holdings (LT core compounders) get a higher cap (~22% NLV) than
    # the legacy 10% rule — capping a conviction compounder at 10% defeats
    # the long-term thesis (CLAUDE.md hard rule #29). When the holding is
    # within bounds for its tier, we emit a `within_bounds` informational note
    # rather than a warning so the user sees the position was checked.
    _pt = None
    _has_tier_cfg = False
    if config and isinstance(config, dict) and config.get("position_tiers"):
        try:
            from analysis import position_tiers as _pt  # type: ignore
            _has_tier_cfg = True
        except ImportError:
            _pt = None
            _has_tier_cfg = False

    for rev in equity_reviews:
        weight = rev.get("weight", 0)
        ticker = rev.get("ticker", "?")
        if _has_tier_cfg and _pt is not None:
            tier = _pt.tier_for(ticker, config)
            cap_pct = _pt.concentration_cap_for_tier(tier, config)  # e.g. 22.0
            cap_ratio = (cap_pct or 10.0) / 100.0
            warn_ratio = cap_ratio * 0.80
            within_ratio = cap_ratio * 0.50
            if weight > cap_ratio:
                alerts.append(
                    f"⚠️ {ticker} concentration {weight*100:.1f}% — BREACH of "
                    f"Tier {tier} cap ({cap_pct:.0f}% NLV)"
                )
            elif weight > warn_ratio:
                alerts.append(
                    f"📊 {ticker} concentration {weight*100:.1f}% — approaching "
                    f"Tier {tier} cap ({cap_pct:.0f}% NLV)"
                )
            elif weight > within_ratio:
                # Surface as info, not warning — Tier A's whole point is to
                # ALLOW concentration. Inline note so the user sees we tracked
                # it without false-alarming the Red Flags.
                alerts.append(
                    f"📊 {ticker} concentration {weight*100:.1f}% — within Tier "
                    f"{tier} bounds (cap {cap_pct:.0f}% NLV, tracked)"
                )
            continue
        # Legacy path — no tier config.
        if weight > 0.10:
            alerts.append(f"⚠️ {ticker} concentration {weight*100:.1f}% — over 10% NLV cap")
        elif weight > 0.08:
            alerts.append(f"📊 {ticker} concentration {weight*100:.1f}% — approaching 10% cap")

    # Options: surface urgent earnings+loss flags AND actionable recommendations
    actionable_decisions = {"CLOSE", "CLOSE_FOR_PROFIT", "ROLL_OUT", "ROLL_OUT_AND_DOWN", "ROLL_OUT_AND_UP", "TAKE_ASSIGNMENT", "LET_EXPIRE"}
    roll_decisions = {"ROLL_OUT", "ROLL_OUT_AND_DOWN", "ROLL_OUT_AND_UP"}
    for rev in options_reviews:
        rec = rev.get("recommendation", "")
        contract = rev.get("contract", "")
        rationale = rev.get("rationale", "")

        # Task #43 defect 1 (AVGO_PUT_375_20260814, 2026-07-31): the matrix
        # decision fed this alert unfiltered while the action gate demoted
        # the SAME roll ("⏸ Roll demoted (nothing to defend) …" in Watch) —
        # two surfaces contradicting each other on one position. The
        # demotion notes are computed ONCE in render_action_list (which
        # runs first and stashes them on the review dicts); the alert
        # rewrites to reflect the demotion — never hidden (rule #24).
        # Fail-open: no demotion key (e.g. standalone callers) → the
        # legacy 🎯 roll alert renders unchanged.
        _demotion_note = (rev.get("_roll_gate_demotion")
                          or rev.get("_churn_guard_demotion")
                          or rev.get("_debit_cap_demotion"))

        # 2026-08-06 defect 2: when the exit-cost verdict CHANGED vs the
        # prior snapshot (stashed by aggregate's persistence pass), the
        # alert carries a ⏰ prefix — same convention as the credit-window
        # transition alerts.
        _vc_prefix = "⏰ " if rev.get("_verdict_change") else ""

        # Check for URGENT earnings+loss flags in the rationale or commentary
        if "🚨 URGENT" in rationale or "earnings" in rationale.lower() and "capture" in rationale.lower() and ("-" in rationale):
            alerts.insert(0, f"🚨 **URGENT EARNINGS:** {contract} — {_truncate_at_word(rationale, 100)}")
        elif rec in roll_decisions and _demotion_note:
            alerts.append(
                f"{_vc_prefix}🎯 {contract} → **⏸ ROLL DEMOTED**: "
                f"{_truncate_at_word(str(_demotion_note), 120)}"
            )
        elif rec in ("CLOSE", "CLOSE_FOR_PROFIT") \
                and rev.get("_momentum_ride"):
            # Momentum-hold overlay (George 2026-08-17): the action list is
            # RIDING this winner — the alert must not contradict it with a
            # bare CLOSE_FOR_PROFIT scream (the AVGO two-surfaces lesson).
            alerts.append(
                f"{_vc_prefix}🏇 {contract} → **RIDING MOMENTUM**: "
                f"{_truncate_at_word(str(rev.get('_momentum_ride')), 120)}"
            )
        elif rec in ("CLOSE", "CLOSE_FOR_PROFIT") \
                and rev.get("_redeploy_hold_demotion"):
            # Redeploy-aware TP (George 2026-08-10): the action list held
            # this winner for more — the alert must not contradict it with
            # a bare CLOSE_FOR_PROFIT scream (the AVGO two-surfaces lesson).
            alerts.append(
                f"{_vc_prefix}⏳ {contract} → **TP HELD — no redeploy "
                f"path**: "
                f"{_truncate_at_word(str(rev.get('_redeploy_hold_demotion')), 120)}"
            )
        elif rec in actionable_decisions:
            # Task #43 fix 4 (2026-07-31): word-boundary truncation. Observed
            # '🎯 PLTR_PUT_130_20270115 → **ROLL_OUT_AND_DOWN**: 🎯 Strike
            # tested (δ 0.47 ≥ 0.45) with -4% captured and 168 DTE —
            # credit-roll wind' — the fixed-width [:80] slice cut mid-word
            # (same class as the task-#40 URGENT-title bug).
            alerts.append(f"{_vc_prefix}🎯 {contract} → **{rec}**: "
                          f"{_truncate_at_word(rationale, 80)}")
        elif rec == "ERROR":
            alerts.append(f"❌ {contract}: advisor error — "
                          f"{_truncate_at_word(rationale, 80)}")

    if not alerts:
        lines.append("✓ No urgent alerts.")
    else:
        for a in alerts:
            lines.append(f"- {a}")

    lines.append("")
    return lines


def _format_delta_line(delta: float | None, option_type: str = "",
                       strike: float | None = None,
                       spot: float | None = None) -> str:
    """Surface the option delta as an assignment-probability proxy.

    ITM/OTM is a MONEYNESS fact — strike vs spot for the option type — never
    a delta-threshold guess (task #37 fix 4b: an $850P with spot $835.83 was
    labeled "OTM" because |delta| 0.40 < 0.5). The "~N% ITM probability" is
    |delta|; the ITM/OTM tag only renders when strike AND spot are known —
    on missing data we omit the tag rather than guess (CLAUDE.md #19).
    """
    if delta is None:
        return ""
    abs_d = abs(float(delta))
    prob_pct = abs_d * 100  # rough prob of ITM at expiration
    tag = ""
    try:
        ot = (option_type or "").upper()
        s = float(strike or 0)
        p = float(spot or 0)
        if s > 0 and p > 0 and ot in ("PUT", "CALL"):
            itm = (p < s) if ot == "PUT" else (p > s)
            tag = " — ITM" if itm else " — OTM"
    except (TypeError, ValueError):
        tag = ""
    return f"Delta {delta:+.2f} (~{prob_pct:.0f}% ITM probability{tag})"


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


def render_summary_card(items: list, snapshot_data: dict | None = None,
                        options_reviews: list | None = None,
                        new_ideas: list | None = None) -> list:
    """Render a portfolio total-impact summary at the END of the action list.

    Aggregates: net cash flow today, total premium income at expiration if all
    short legs decay to zero, total tax avoided if no assignments, total upside
    protected. One-line dashboard so the user can decide do-everything vs do-top-N.

    Net cash comes from the SHARED analysis/net_option_cash.py computation —
    the same per-action numbers the 💰 Money Plan's "Net option cash today"
    line uses (one voice, rule #43). The pre-fix regex here summed "Locks
    $+X profit" strings (realized P/L, not cash) against roll debits and
    rendered −$1,985 on 2026-08-07 while the Money Plan said $-948 for the
    same three actions. The composition is now spelled out on the line.
    """
    if not items:
        return []
    # Crude regex-based extraction of dollar amounts and tax saved from rendered items.
    import re
    text = "\n".join(items)

    noc = None
    try:
        from analysis.net_option_cash import compute_net_option_cash
        noc = compute_net_option_cash(
            items, options_reviews=options_reviews, new_ideas=new_ideas)
    except Exception:
        noc = None
    if noc is not None:
        net_cash = noc["net_cash"]
        net_cash_line = (f"- **Net cash today (mid-fills):** "
                         f"{noc['composition']}")
    else:  # pragma: no cover — legacy fallback (shared module unavailable)
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
        net_cash_line = (f"- **Net cash today (mid-fills):** "
                         f"{'+' if net_cash >= 0 else '−'}${abs(net_cash):,.0f}")

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
    has_debit_roll = (noc is not None and noc["total_rolls"] < 0) or \
        bool(re.findall(r"[−-]\$([\d,]+)\s+(?:net\s+)?debit", text))
    if has_debit_roll:
        # Assume ~5% additional cost at worst-case fill on each debit roll
        worst_case_drag = -int(abs(net_cash) * 0.05) if net_cash < 0 else 0

    out = [
        "",
        "### 📋 Total Impact (if all actions executed)",
        "",
        f"- **Total actions:** {n_actions}",
        net_cash_line,
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


def _core_union_safe(config: dict | None) -> set:
    """core_positions ∪ Tier A (analysis.position_tiers.core_union), with a
    legacy fallback to the raw core_positions list when the import fails.

    2026-08-04 (PLTR): Tier A conveys core protections everywhere a consumer
    used to read `core_positions` directly — see position_tiers.core_union.
    """
    try:
        from analysis.position_tiers import core_union
        return core_union(config or {})
    except Exception:
        return set((config or {}).get("core_positions", []) or [])


def _tier_b_cap_pct(ticker: str, config: dict | None):
    """Tier B concentration cap (percent, e.g. 12.0) when the ticker is an
    EXPLICIT Tier B income name; None otherwise. Tier C (the default for
    unlisted tickers) keeps the legacy standard cap — the tier framework only
    loosens caps on an explicit conviction assignment (rule #29)."""
    try:
        from analysis.position_tiers import (
            TIER_B, concentration_cap_for_tier, tier_for,
        )
        if tier_for(ticker, config or {}) == TIER_B:
            # tier_for only returns B on explicit tier_b_income membership.
            return concentration_cap_for_tier(TIER_B, config or {})
    except Exception:
        pass
    return None


def _exit_cost_lines(
    rev: dict,
    snapshot_data: dict | None,
    equity_reviews: list | None = None,
    date_str: str | None = None,
    include_near_money: bool = False,
    verdict_context: str | None = None,
) -> list[str]:
    """Exit-cost anatomy sub-bullets for an ITM/underwater SHORT PUT action.

    The 2026-07-29 LITE $700P lesson as pipeline logic: before the user pays
    a panic ask to close, decompose the buyback into intrinsic / extrinsic /
    spread and say whether closing, rolling, or assignment is the cheaper
    exit (analysis/exit_cost.py).

    Gates (all must hold, else no lines):
      - short PUT (qty < 0) — call anatomy exists in the module but isn't
        surfaced here;
      - ITM (spot < strike) OR underwater (mid > premium received);
      - `exit_cost.enabled` (default true).

    Fail-closed on data (CLAUDE.md #10): no live chain leg for the held
    contract this cycle → a "verify at broker" note, never fabricated
    numbers. Fail-open on errors: any exception → no lines, briefing ships.
    """
    anatomy, status = _exit_cost_anatomy(rev, snapshot_data,
                                         equity_reviews, date_str,
                                         include_near_money=include_near_money)
    if status == "no_quote":
        # Fail closed — the anatomy needs a live quote fetched this cycle.
        return [
            "   - **Exit cost anatomy:** chain unavailable — verify exit "
            "cost (intrinsic vs extrinsic vs spread) at the broker before "
            "paying any ask."
        ]
    if anatomy is None:
        return []
    try:
        from analysis import exit_cost as _xc
        cfg_all = (snapshot_data or {}).get("_config", {}) or {}
        _vc_und = (rev.get("underlying")
                   or str(rev.get("contract", "")).split("_")[0])
        try:
            return _xc.format_anatomy_lines(
                anatomy, config=cfg_all,
                prior_entry=_prior_verdict_entry(
                    rev.get("contract", ""), snapshot_data),
                dte=rev.get("days_to_expiry"),
                iv_rank=((snapshot_data or {}).get("iv_ranks", {}) or {})
                .get(_vc_und),
                verdict_context=verdict_context)
        except TypeError:
            # Older exit_cost without verdict_context — legacy rendering.
            return _xc.format_anatomy_lines(
                anatomy, config=cfg_all,
                prior_entry=_prior_verdict_entry(
                    rev.get("contract", ""), snapshot_data),
                dte=rev.get("days_to_expiry"),
                iv_rank=((snapshot_data or {}).get("iv_ranks", {}) or {})
                .get(_vc_und))
    except Exception:
        return []  # advisory layer — never break the briefing


def _exit_cost_anatomy(
    rev: dict,
    snapshot_data: dict | None,
    equity_reviews: list | None = None,
    date_str: str | None = None,
    include_near_money: bool = False,
):
    """Compute the exit-cost anatomy for an ITM/underwater SHORT PUT review.

    Returns ``(anatomy | None, status)`` where status is:
      - "ok"           — anatomy computed from a live chain quote
      - "no_quote"     — position qualifies but no usable chain leg this cycle
      - "no_data"      — position qualifies on paper but spot/strike is
                          unmeasurable this cycle (missing quote) — callers
                          that surface a CLOSE must fail CLOSED, never silent
      - "otm"          — only with ``include_near_money``: genuinely OTM
                          (spot > 1.03 × strike) profitable put — no anatomy
                          needed, but the card must SAY the buyback is pure
                          time value (2026-08-05 defect 2)
      - "not_eligible" — disabled / not a short put / not qualifying /
                          any error (fail-open; the briefing must still ship)

    ``include_near_money`` (2026-08-05 defect 2, VRT $280P): the take-profit
    CLOSE path extends eligibility to NEAR-MONEY puts (spot < 1.03 × strike
    even when barely OTM and profitable). Yesterday the same VRT position was
    ITM and carried "ROLL, don't close — 89% extrinsic"; today at moneyness
    1.003 the old itm-or-underwater gate silently dropped the anatomy and the
    CLOSE card rendered with no verdict at all. Default False keeps every
    other caller (blocks #3/#4 verdict-drives-action) byte-identical.

    Task #37 fix 1 consumes the anatomy VERDICT in the action builder (the
    verdict drives the action, rule #14's defer-to-the-advisor pattern);
    :func:`_exit_cost_lines` consumes it for the rendered sub-bullets.
    """
    try:
        from analysis import exit_cost as _xc

        cfg_all = (snapshot_data or {}).get("_config", {}) or {}
        xc_cfg = cfg_all.get("exit_cost") or {}
        if not xc_cfg.get("enabled", True):
            return None, "not_eligible"
        if (rev.get("type") or "").upper() != "PUT":
            return None, "not_eligible"
        if float(rev.get("qty", 0) or 0) >= 0:
            return None, "not_eligible"
        contract = rev.get("contract", "")
        und = rev.get("underlying") or str(contract).split("_")[0]
        quotes = (snapshot_data or {}).get("quotes", {}) or {}
        spot = float((quotes.get(und) or {}).get("last") or 0)
        strike = float(rev.get("strike") or 0)
        entry = float(rev.get("entry_price") or 0)
        mid = float(rev.get("current_mid") or 0)
        if not (spot and strike):
            # Spot unmeasurable this cycle — the anatomy CANNOT rule the
            # position in or out. Callers composing a CLOSE fail closed.
            return None, "no_data"
        itm = spot < strike
        underwater = entry > 0 and mid > entry
        near_money = strike > 0 and spot < strike * _ROLL_GATE_MONEYNESS
        if not (itm or underwater):
            if not include_near_money:
                return None, "not_eligible"
            if not near_money:
                # Genuinely OTM winner (spot > 3% above strike) — buyback is
                # pure time value; no decomposition needed, but say so.
                return None, "otm"

        chains = (snapshot_data or {}).get("chains", {}) or {}
        quote = _xc.chain_quote_for_position(rev, chains)
        if quote is None:
            return None, "no_quote"

        anatomy = _xc.analyze_exit_cost(
            rev, quote, spot,
            iv_rank=((snapshot_data or {}).get("iv_ranks", {}) or {}).get(und),
            earnings_date=((snapshot_data or {}).get("earnings_calendar", {})
                           or {}).get(und),
            today=date_str,
            config=cfg_all,
            loss_stop_fired="GUARDRAIL_LOSS_STOP" in (rev.get("matrix_cell_id") or ""),
            concentration_ok=_xc.assignment_concentration_ok(
                rev, snapshot_data, equity_reviews),
        )
        if anatomy is None:
            return None, "not_eligible"
        return anatomy, "ok"
    except Exception:
        return None, "not_eligible"


def _prior_verdict_entry(contract: str, snapshot_data: dict | None):
    """Raw persisted exit-verdict entry (str or rich dict) for this contract
    from the prior snapshot's exit_verdicts.json, or None."""
    try:
        prior = (snapshot_data or {}).get("_prior_exit_verdicts") or {}
        return (prior.get("verdicts") or {}).get(contract)
    except Exception:
        return None


def _prior_verdict_bit(contract: str, snapshot_data: dict | None) -> str:
    """"; yesterday's read was ROLL, don't close" when the prior snapshot
    persisted a verdict for this contract (exit_verdicts.json), else ""."""
    try:
        from analysis.exit_cost import verdict_headline, verdict_of
        verdict = verdict_of(_prior_verdict_entry(contract, snapshot_data))
        if not verdict:
            return ""
        return f"; yesterday's read was {verdict_headline(verdict)}"
    except Exception:
        return ""


def _close_anatomy_footer(
    rev: dict,
    anatomy,
    status: str | None,
    snapshot_data: dict | None,
    equity_reviews: list | None,
    date_str: str | None,
) -> list[str]:
    """Anatomy footer for a take-profit CLOSE card — NEVER silent (2026-08-05
    defect 2).

    Observed: the VRT $280P / $270P CLOSE cards rendered with no anatomy or
    verdict line at all, while YESTERDAY the same positions carried "ROLL,
    don't close — 89% extrinsic". The old path silently fell through when the
    anatomy gate ruled the position out (or data was missing). Contract:

      - status "ok"            → the full anatomy + verdict block;
      - status "no_quote"/"no_data" (ITM-or-near-money short put, anatomy
        uncomputable this cycle) → fail-closed warning naming what was
        missing, plus yesterday's persisted verdict when available;
      - status "otm" (spot > 3% above strike) → "OTM — the buyback is pure
        time value" one-liner;
      - short CALL (anatomy module is put-only) → OTM one-liner when
        measurably OTM, otherwise the fail-closed warning.
    """
    opt_type = (rev.get("type") or "").upper()
    contract = rev.get("contract", "")
    cfg_all = (snapshot_data or {}).get("_config", {}) or {}
    if not ((cfg_all.get("exit_cost") or {}).get("enabled", True)):
        return []
    try:
        strike = float(rev.get("strike") or 0)
        und = rev.get("underlying") or str(contract).split("_")[0]
        spot = float((((snapshot_data or {}).get("quotes", {}) or {})
                      .get(und) or {}).get("last") or 0)
    except (TypeError, ValueError):
        strike, spot = 0.0, 0.0

    unavailable_line = (
        "   - _⚠ exit-cost anatomy unavailable this cycle ({why}) — verify "
        "the buyback's time-value split at the broker before closing"
        "{prior}._"
    )

    if opt_type == "PUT":
        if status is None:
            anatomy, status = _exit_cost_anatomy(
                rev, snapshot_data, equity_reviews, date_str,
                include_near_money=True)
        if status == "ok" and anatomy is not None:
            try:
                from analysis import exit_cost as _xc
                return _xc.format_anatomy_lines(
                    anatomy, config=cfg_all,
                    prior_entry=_prior_verdict_entry(contract, snapshot_data),
                    dte=rev.get("days_to_expiry"),
                    iv_rank=((snapshot_data or {}).get("iv_ranks", {}) or {})
                    .get(und))
            except Exception:
                return []
        if status in ("no_quote", "no_data"):
            why = ("no chain quote" if status == "no_quote"
                   else "no live spot quote")
            return [unavailable_line.format(
                why=why, prior=_prior_verdict_bit(contract, snapshot_data))]
        if status == "otm" and spot and strike:
            return [
                f"   - **Exit cost anatomy:** OTM — spot ${spot:,.2f} is "
                f"{(spot / strike - 1) * 100:.1f}% above the ${strike:g} "
                f"strike; the buyback is pure time value."
            ]
        return []

    if opt_type == "CALL":
        # Call anatomy isn't modeled (module is put-only) — but a short-call
        # take-profit close still must not render silent (defect 2).
        if spot and strike:
            if spot < strike * 0.97:
                return [
                    f"   - **Exit cost anatomy:** OTM — spot ${spot:,.2f} is "
                    f"{(1 - spot / strike) * 100:.1f}% below the ${strike:g} "
                    f"strike; the buyback is pure time value."
                ]
            return [unavailable_line.format(
                why="call-side anatomy not modeled; contract is near/at the "
                    "strike",
                prior=_prior_verdict_bit(contract, snapshot_data))]
        return [unavailable_line.format(
            why="no live spot quote",
            prior=_prior_verdict_bit(contract, snapshot_data))]

    return []


# Exit-cost verdicts that mean "closing is the cheap exit" — when the verdict
# drives the action (exit_cost.verdict_drives_action, default true), these
# convert a composed EXECUTE ROLL into a plain CLOSE (task #37 fix 1: the
# briefing spent $44K of roll debits on positions whose own verdict said
# CLOSE — clean exit).
_CLOSE_VERDICTS = ("CLOSE_CLEAN", "CLOSE_AFTER_CRUSH", "CLOSE_URGENT")

# CLAUDE.md rule #3 hard gate — an actionable roll/forced-decision item on a
# SHORT PUT requires GENUINE assignment risk. TSM 2026-07-30 bug: a $380P
# with spot $402.92 (6% above strike, |δ| 0.30, 100% extrinsic) rendered
# "EXECUTE ROLL — −$838 debit" because the matrix's NEAR_ATM band (8% above
# strike) fed an explicit ROLL rec into block #3, which trusted the rec and
# skipped its own moneyness gate. Rule #3 claims blocks #3+#4 enforce
# "moneyness, not P&L%" — this helper makes the claim true on EVERY path
# that surfaces an actionable short-put roll (block #3 priced candidates,
# block #4 generic roll directives, and the 4c forced-decision item).
_ROLL_GATE_MONEYNESS = 1.03   # spot/strike below this = at/past/near strike
_ROLL_GATE_DELTA = 0.40       # measured |delta| at/above this = tested
# Task #40 fix 6 (AVGO 2026-07-30): a MEASURED |delta| below this floor means
# the strike is NOT threatened regardless of moneyness — the $375P at
# moneyness 1.0297 (inside the <1.03 band) carried δ 0.10 and still got an
# $871 defensive roll. The moneyness band is the FALLBACK for missing delta,
# not an override of a measured 10-delta.
_ROLL_GATE_DELTA_FLOOR = 0.25


def _short_put_roll_gate_ok(moneyness: float, delta) -> bool:
    """True when a short put has genuine assignment risk.

    Measured-delta first (task #40 fix 6): |δ| ≥ 0.40 passes outright;
    |δ| < 0.25 FAILS regardless of moneyness (a 10-delta put has nothing to
    defend — the moneyness band exists only as the fallback when the chain
    carried no delta). In the 0.25–0.40 band, and whenever the delta is
    unmeasured, the moneyness band (spot < 1.03 × strike) decides.
    A failing gate demotes the item to a Watch-panel note — never an
    actionable ticket. Loss-stop / crash paths never consult this gate.
    Fail-open: unmeasurable moneyness (callers pass 1.0) keeps the legacy
    behavior."""
    measured = None
    try:
        measured = abs(float(delta)) if delta is not None else None
    except (TypeError, ValueError):
        measured = None
    if measured is not None:
        if measured >= _ROLL_GATE_DELTA:
            return True
        if measured < _ROLL_GATE_DELTA_FLOOR:
            return False  # measured, untested — moneyness cannot override
    try:
        if float(moneyness) < _ROLL_GATE_MONEYNESS:
            return True
    except (TypeError, ValueError):
        return True  # no measured moneyness → never invent a gate
    return False


def _roll_gate_demotion_note(moneyness, delta) -> str:
    """Accurate demotion text for a rule-#3 / delta-veto gate failure —
    names the MEASURED delta when that is what failed the gate (rule #19:
    the reason shown must be the reason measured)."""
    measured = None
    try:
        measured = abs(float(delta)) if delta is not None else None
    except (TypeError, ValueError):
        measured = None
    try:
        mny_bit = f"spot is {(float(moneyness) - 1) * 100:.1f}% above the strike"
    except (TypeError, ValueError):
        mny_bit = "spot is above the strike"
    if measured is not None and measured < _ROLL_GATE_DELTA_FLOOR:
        return (
            f"Roll demoted from the action list (rule #3 gate, measured-delta "
            f"veto): |δ| {measured:.2f} < {_ROLL_GATE_DELTA_FLOOR:.2f} — a "
            f"{measured * 100:.0f}-delta put is not threatened even with "
            f"{mny_bit}; theta is working. Re-evaluate on a genuine test."
        )
    return (
        f"Roll demoted from the action list (rule #3 gate): {mny_bit} with no "
        f"genuine strike test (|δ| < {_ROLL_GATE_DELTA:.2f}) — theta "
        f"is working; re-evaluate on a genuine test."
    )


def _nothing_to_defend_note(rev: dict, moneyness) -> str | None:
    """Task #40 fix 6 (AVGO $375P, 2026-07-30): a PROFITABLE, fully-OTM
    short put whose MEASURED |δ| is below the tested threshold (0.40 — the
    same definition exit_cost's HOLD_FOR_DECAY uses) has nothing to defend,
    even inside the <1.03 moneyness band. The real card composed an $871
    debit roll on a +15%-captured OTM put at measured δ 0.37 while the
    briefing displayed the STO leg's δ 0.10. Returns the demotion note, or
    None when the position is ITM, underwater, or the delta is unmeasured
    (fail-open — never withhold defense on missing data)."""
    try:
        if float(moneyness) <= 1.0:
            return None  # at/past the strike — genuinely ITM
    except (TypeError, ValueError):
        return None
    try:
        measured = (abs(float(rev.get("delta")))
                    if rev.get("delta") is not None else None)
    except (TypeError, ValueError):
        measured = None
    if measured is None or measured >= _ROLL_GATE_DELTA:
        return None
    try:
        entry = float(rev.get("entry_price") or 0)
        mid = float(rev.get("current_mid") or 0)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or mid > entry:
        return None  # underwater / unknown P&L → defense may be warranted
    return (
        f"Roll demoted (nothing to defend): position is OTM and profitable "
        f"({(entry - mid) / entry * 100:.0f}% captured) with measured "
        f"|δ| {measured:.2f} < {_ROLL_GATE_DELTA:.2f} — theta is doing the "
        f"job; re-evaluate on a genuine strike test."
    )


def _truncate_at_word(text: str, limit: int = 160) -> str:
    """Truncate at a WORD boundary with an ellipsis (task #40 fix 7 — URGENT
    titles rendered '…extrinsic is at its peak. Th' from a fixed-width
    slice). Text at/under the limit passes through unchanged."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;·—-") + " …"


def _verdict_close_lines(n: int, contract: str, rev: dict, anatomy,
                         snapshot_data: dict | None,
                         equity_reviews: list | None,
                         date_str: str | None,
                         config_local: dict) -> list[str]:
    """Render the CLOSE action a CLOSE_* exit-cost verdict drives (fix 1).

    BTC ticket at the measured chain mid (GTC), the anatomy block, and the
    one-line "Roll skipped" note. Used by both block #3 (priced-candidate
    rolls) and block #4 (matrix roll directives)."""
    out: list[str] = []
    qty_c = abs(rev.get("qty", 0) or 0)
    mid_c = float(anatomy.btc_mid or rev.get("current_mid") or 0)
    cost_c = mid_c * 100.0 * qty_c
    ext_pct_c = (anatomy.extrinsic_per_share / anatomy.btc_mid * 100.0
                 if anatomy.btc_mid else 0.0)
    headline_c = {
        "CLOSE_CLEAN": "clean exit (mostly intrinsic)",
        "CLOSE_AFTER_CRUSH": "close after the IV crush",
        "CLOSE_URGENT": "close before the binary",
    }.get(anatomy.verdict, "closing is the cheap exit")
    out.append(
        f"{n}. **CLOSE** {contract} — exit-cost verdict: {headline_c}; "
        f"buy-to-close {int(qty_c)}× limit ${mid_c:.2f} "
        f"(≈ ${cost_c:,.0f}), GTC"
    )
    out.append(f"   - **Why:** {anatomy.verdict_reason}")
    out.extend(_exit_cost_lines(rev, snapshot_data, equity_reviews, date_str))
    out.append(
        f"   - _Roll skipped: exit-cost verdict says closing is cheap "
        f"(extrinsic {ext_pct_c:.0f}%); re-enter on your own terms "
        f"after stabilization. Full roll menu stays in the Watch "
        f"panel's ROLL ANALYSIS table._"
    )
    out.append(
        f"   - **Account:** "
        f"{_route_account('CLOSE', rev.get('underlying', ''), rev.get('account') or rev.get('account_type'), config_local.get('accounts', []) or [])}"
    )
    return out


def _candidate_extension_days(c: dict, cur_expiration) -> int | None:
    """Days the candidate's STO leg extends past the position's CURRENT
    expiration. Prefers the advisor's measured ``dteExtension``; falls back
    to date math (sell_expiration − current expiration). None = unmeasurable
    — callers composing actionable tickets must fail closed on None."""
    ext = c.get("dteExtension")
    if ext is not None:
        try:
            return int(ext)
        except (TypeError, ValueError):
            pass
    try:
        sell_exp = (c.get("instruction") or {}).get("sell_expiration") or ""
        d_new = datetime.strptime(str(sell_exp)[:10], "%Y-%m-%d").date()
        d_cur = datetime.strptime(
            str(cur_expiration)[:10], "%Y-%m-%d").date()
        return (d_new - d_cur).days
    except (TypeError, ValueError):
        return None


def _iter_roll_down_candidates(rev: dict, max_tenor_days: int | None):
    """Yield (reduction, net, candidate) for every strictly-lower-strike,
    real-priced roll-down candidate INSIDE the tenor cap. Shared by the
    credit headline picker and the small-debit alternative picker."""
    cur_strike = _strike_from_contract(
        rev.get("contract", ""), rev.get("strike")) or 0
    for c in (rev.get("roll_candidates") or []):
        if not isinstance(c, dict) or c.get("id") == "A":
            continue
        instr = c.get("instruction") or {}
        try:
            s = float(instr.get("sell_strike") or 0)
            net = float(c.get("netDollars") or 0)
        except (TypeError, ValueError):
            continue
        if s <= 0 or not instr.get("sell_expiration"):
            continue
        if not cur_strike or s >= cur_strike:
            continue                      # DOWN only — never up / same-strike
        # Tenor cap (CLAUDE.md rule #14, 2026-08-04 regression): the composer
        # once surfaced "STO 1× VRT $240P Fri Dec 15 '28" against a Jan '27
        # position — ~700d past the current expiry, ~6× the 120d cap — because
        # the ONLY credit-positive roll-down was the 2.4-year one (the
        # max-credit trap resurrected). A candidate past the cap, or with an
        # unmeasurable tenor, is NOT ticket-eligible (fail closed).
        if max_tenor_days is not None:
            ext = _candidate_extension_days(c, rev.get("expiration"))
            if ext is None or ext > max_tenor_days:
                continue
        yield (cur_strike - s, net, c)


def _pick_roll_down_candidate(rev: dict,
                              max_tenor_days: int | None = None) -> dict | None:
    """Credit-positive roll-DOWN candidate (strictly lower strike, real
    priced STO leg, INSIDE the action tenor cap) for the one-voice
    take-profit path. Preference: biggest strike reduction first, credit
    second — the CLAUDE.md #42 put-side discipline (risk reduction over max
    credit). Returns the candidate dict or None (caller falls back to
    HOLD — GTC at 50%)."""
    scored = [t for t in _iter_roll_down_candidates(rev, max_tenor_days)
              if t[1] > 0]                # headline must be a net CREDIT
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return scored[0][2]


def _pick_debit_roll_down_alternative(
        rev: dict, max_tenor_days: int | None,
        min_strike_reduction: float = 25.0) -> dict | None:
    """Small-debit, IN-TENOR roll-down worth OFFERING (never the headline)
    under the HOLD — GTC fallback, when the strike reduction is meaningful
    (≥ $25 by default). Preference: SMALLEST debit that clears the reduction
    floor, then biggest reduction — "small-debit" means cheapest genuine risk
    cut, not deepest cut at any price. Labeled honestly with the debit by
    the renderer."""
    scored = [t for t in _iter_roll_down_candidates(rev, max_tenor_days)
              if t[1] <= 0 and t[0] >= min_strike_reduction]
    if not scored:
        return None
    scored.sort(key=lambda t: (abs(t[1]), -t[0]))
    return scored[0][2]


def _days_to_earnings_snapshot(underlying: str, snapshot_data: dict | None,
                               date_str: str | None) -> int | None:
    """Calendar days from ``date_str`` (or today) to the underlying's next
    earnings date per the snapshot's earnings_calendar. None when the date
    is missing/unparseable (fail-open — an unknown print never blocks)."""
    try:
        e = ((snapshot_data or {}).get("earnings_calendar", {}) or {}).get(
            underlying)
        if not e:
            return None
        e_d = datetime.strptime(str(e)[:10], "%Y-%m-%d").date()
        t_d = (datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
               if date_str else datetime.now().date())
        return (e_d - t_d).days
    except (TypeError, ValueError):
        return None


def _momentum_ride_lines(n: int, contract: str, rev: dict, ride_reason: str,
                         capture_pct: float) -> list[str]:
    """Render the 🏇 RIDE — momentum hold item (George 2026-08-17: "If
    something is ripping and it didn't get out to RSI, say, to 70 or
    something. Do I really need to close? ... let's make sure we squeeze as
    much as possible out of these options."). A HOLD-class action-list item
    (rule #24 — visible, never hidden) carrying the measured momentum read,
    the would-have-closed capture, and the explicit measured EXIT TRIGGER
    (first red day / RSI stall / capture ceiling — all inside
    ``ride_reason``, every number measured this cycle)."""
    qty = abs(rev.get("qty", 0) or 0)
    entry = float(rev.get("entry_price") or 0)
    mid = float(rev.get("current_mid") or 0)
    pl = (entry - mid) * 100 * qty if entry else 0.0
    out = [
        f"{n}. 🏇 **RIDE — momentum hold** {contract} — {ride_reason}",
        f"   - **Why:** yield-motivated close deferred — the underlying is "
        f"ripping and the stall trigger hasn't fired. Would have closed at "
        f"+{capture_pct:.0f}% (${pl:+,.0f}, buy-to-close mid ${mid:.2f}); "
        f"the exit trigger above is measured — next briefing where the "
        f"underlying prints red or RSI reaches the stall, the close "
        f"returns with \"(momentum stalled — take it)\".",
    ]
    return out


def _one_voice_take_profit_lines(n: int, contract: str, rev: dict, anatomy,
                                 capture_pct: float,
                                 snapshot_data: dict | None,
                                 equity_reviews: list | None,
                                 date_str: str | None) -> list[str]:
    """One-voice resolution for a PROFITABLE close whose exit-cost verdict is
    ROLL_DONT_CLOSE (2026-08-04 fix 1).

    Observed 2026-08-04, action #4: headline "**CLOSE** VRT_PUT_280_20270115
    — +30% ($+2,226)" rendered with "**⚖️ Verdict: ROLL, don't close** —
    closing pays $4,534 of panic premium at IV rank 89" inline — two
    contradictory voices on one card. The take-profit close path never
    consulted the exit-cost verdict (task #37 wired verdicts into the ROLL
    path, not the TAKE-PROFIT path). Resolution — exactly ONE recommendation:

      - a credit-positive, IN-TENOR roll-DOWN candidate exists → **TAKE
        PROFIT VIA ROLL-DOWN**: one two-leg ticket (BTC current + STO the
        roll-down), banking the risk reduction while the theta engine keeps
        running;
      - none exists → **HOLD — GTC AT 50%**: place a GTC buy-to-close at the
        50%-capture price; the high-extrinsic exit is expensive today, let
        decay pay you. A small-debit IN-TENOR roll-down (strike reduction
        ≥ $25) may render as an honest ALTERNATIVE line, never the headline.

    Tenor cap (rule #14 regression, observed 2026-08-04): the first cut of
    this composer rendered "TAKE PROFIT VIA ROLL-DOWN VRT_PUT_280_20270115 —
    BTC 1× @ $51.97 + STO 1× VRT $240P Fri Dec 15 '28 @ $75.75 → net +$2,270
    credit" — the STO leg ~700 days past the current Jan '27 expiry, ~6× the
    120d action tenor cap, because the ONLY credit-positive $240P was the
    2.4-year one. Candidate selection now enforces roll.max_action_tenor_days
    (core-union-aware ×3, same discipline as block #3 and advise.py).

    Earnings precondition (2026-08-13, IREN $47P): the GTC-at-capture
    squeeze REQUIRES the print to be comfortably beyond the squeeze window
    (days_to_next_earnings > exit_cost.gtc_min_days_to_earnings, default
    30 — the position-review squeeze-logic precondition). Observed card:
    "**HOLD — GTC AT 50%** IREN_PUT_47_20261218 — +44% captured; place a
    GTC buy-to-close at the 50%-capture price $9.32" with IREN earnings
    ~14d away — parking a hope-for-decay GTC through a binary print. When
    earnings land inside that window AND before the contract's expiry, the
    card's single voice is the earnings-aware close (**CLOSE BEFORE
    EARNINGS** — the CLOSE-INTO-RECOVERY family: removing the binary beats
    premium mechanics), with the extrinsic cost stated honestly and the
    ROLL_DONT_CLOSE verdict rendered as explicitly-subordinate context.

    The anatomy block renders under whichever single recommendation wins.
    Kill switch: exit_cost.one_voice (default true)."""
    out: list[str] = []
    qty = abs(rev.get("qty", 0) or 0)
    entry = float(rev.get("entry_price") or 0)
    mid = float(anatomy.btc_mid or rev.get("current_mid") or 0)
    pl = (entry - float(rev.get("current_mid") or 0)) * 100 * qty
    und = rev.get("underlying") or str(contract).split("_")[0]
    cur_strike = _strike_from_contract(contract, rev.get("strike")) or 0
    # Tenor cap — identical discipline to block #3's ranker call: 120d
    # default, ×3 for core (core_positions ∪ Tier A).
    _cfg_tp = (snapshot_data or {}).get("_config") or {}
    try:
        _max_tenor_tp = int(
            (_cfg_tp.get("roll") or {}).get("max_action_tenor_days", 120))
    except (TypeError, ValueError):
        _max_tenor_tp = 120
    if und in _core_union_safe(_cfg_tp):
        _max_tenor_tp *= 3
    # ── Earnings precondition on EVERY GTC-at-capture / roll resolution ──
    _xc_cfg_tp = (_cfg_tp.get("exit_cost") or {})
    try:
        _gtc_min_d2e = int(_xc_cfg_tp.get("gtc_min_days_to_earnings", 30))
    except (TypeError, ValueError):
        _gtc_min_d2e = 30
    _d2e_tp = _days_to_earnings_snapshot(und, snapshot_data, date_str)
    try:
        _dte_tp = (int(rev.get("days_to_expiry"))
                   if rev.get("days_to_expiry") is not None else None)
    except (TypeError, ValueError):
        _dte_tp = None
    _earnings_blocks_gtc = (
        _d2e_tp is not None and 0 <= _d2e_tp <= _gtc_min_d2e
        and (_dte_tp is None or _d2e_tp <= _dte_tp))
    if _earnings_blocks_gtc:
        # ── Willing-owner earnings cushion (George 2026-08-17, the IREN
        # case: "It feels like IREN is ripping... Do I really need to
        # close?"): when the assignment-basis cushion (spot vs basis =
        # strike − entry premium) is ≥ momentum_hold.
        # earnings_hold_min_cushion_pct (default 25%), the single voice
        # becomes HOLD THROUGH EARNINGS — a -25% print still assigns above
        # basis (mathematically backed: spot × (1 − thr) ≥ basis ⟺ cushion
        # ≥ thr). The close-the-binary counter-case renders as subordinate
        # context. Cushion below threshold / unmeasurable / config off →
        # close-before-earnings exactly as today. Measured numbers only
        # (rule #19). Source: analysis/momentum_hold.py::earnings_hold.
        _eh = None
        try:
            from analysis import momentum_hold as _mh_eh
            _eh = _mh_eh.earnings_hold(rev, snapshot_data, _cfg_tp)
        except Exception:
            _eh = None
        if _eh:
            _eh_thr = _eh["threshold_pct"]
            _eh_remaining = mid * 100 * qty
            _eh_extr = float(getattr(anatomy, "extrinsic_total", 0) or 0)
            _eh_stays = (f"Extrinsic ${_eh_extr:,.0f}" if _eh_extr > 0
                         else f"Remaining premium ${_eh_remaining:,.0f}")
            out.append(
                f"{n}. **HOLD THROUGH EARNINGS — willing owner** {contract} "
                f"— basis ${_eh['basis']:.2f} is {_eh['cushion_pct']:.0f}% "
                f"below spot ${_eh['spot']:.2f}; a -{_eh_thr:g}% print "
                f"still assigns above basis"
            )
            out.append(
                f"   - **Why:** willing-owner fortress — assignment basis "
                f"${_eh['basis']:.2f} (strike ${cur_strike:g} − "
                f"${entry:.2f} premium) sits {_eh['cushion_pct']:.0f}% "
                f"below spot; even a -{_eh_thr:g}% print on {und} in "
                f"{_d2e_tp}d still assigns above basis. {_eh_stays} stays "
                f"yours if held; place NO GTC through the print."
            )
            out.append(
                f"   - **Counter-case (subordinate):** protect "
                f"+{capture_pct:.0f}% (${pl:+,.0f}) by closing at mid "
                f"${mid:.2f} before the print — take it only if you are "
                f"NOT a willing owner at basis ${_eh['basis']:.2f}."
            )
            out.extend(_exit_cost_lines(
                rev, snapshot_data, equity_reviews, date_str,
                include_near_money=True,
                verdict_context=(
                    f"resolved to HOLD THROUGH EARNINGS — willing owner: "
                    f"basis cushion {_eh['cushion_pct']:.0f}% ≥ "
                    f"{_eh_thr:g}% threshold; the fortress holds through "
                    f"the {und} print in {_d2e_tp}d")))
            return out
        gtc_target = entry * 0.5 if entry > 0 else 0.0
        out.append(
            f"{n}. **CLOSE BEFORE EARNINGS** {contract} — "
            f"+{capture_pct:.0f}% captured (${pl:+,.0f}); buy-to-close at "
            f"mid ${mid:.2f} before {und} prints in {_d2e_tp}d"
        )
        out.append(
            f"   - **Why:** the GTC-at-50% squeeze requires earnings > "
            f"{_gtc_min_d2e}d away — {und} prints in {_d2e_tp}d, inside the "
            f"contract. With +{capture_pct:.0f}% already captured, removing "
            f"the binary beats squeezing the last "
            f"{max(50.0 - capture_pct, 0.0):.0f}% of decay (event risk "
            f"beats premium mechanics)."
        )
        if getattr(anatomy, "extrinsic_total", 0):
            out.append(
                f"   - **Honest cost:** closing pays "
                f"${anatomy.extrinsic_total:,.0f} of extrinsic"
                f"{' — IV ' + anatomy.iv_context if anatomy.iv_context else ''}"
                f" — that is the price of not holding a short option "
                f"through the print. (GTC reference: the 50%-capture price "
                f"is ${gtc_target:.2f}; do not park it through earnings.)"
            )
        out.extend(_exit_cost_lines(
            rev, snapshot_data, equity_reviews, date_str,
            include_near_money=True,
            verdict_context=(
                f"overridden — {und} prints in {_d2e_tp}d inside the "
                f"contract; the close removes the binary (CLOSE-INTO-"
                f"RECOVERY precedence)")))
        return out
    # HOLD_FOR_DECAY (near-money gate, 2026-08-05 defect 2): the verdict says
    # "no exit needed — theta decaying in your favor", so no roll-down is
    # hunted; resolve straight to HOLD — GTC AT 50% with a verdict-accurate
    # Why (never a CLOSE headline contradicted by a HOLD verdict inline).
    if getattr(anatomy, "verdict", None) == "HOLD_FOR_DECAY":
        gtc_hd = entry * 0.5 if entry > 0 else 0.0
        out.append(
            f"{n}. **HOLD — GTC AT 50%** {contract} — +{capture_pct:.0f}% "
            f"captured; place a GTC buy-to-close at the 50%-capture price "
            f"${gtc_hd:.2f}"
        )
        out.append(
            f"   - **Why:** exit-cost verdict HOLD — the buyback is "
            f"${anatomy.extrinsic_total:,.0f} of pure time value decaying in "
            f"your favor (strike untested); closing now pays that theta "
            f"away. Let the GTC fill when the market comes to your price."
        )
        out.extend(_exit_cost_lines(rev, snapshot_data, equity_reviews,
                                    date_str, include_near_money=True))
        return out
    _verdict_ctx_tp: str | None = None
    best = _pick_roll_down_candidate(rev, max_tenor_days=_max_tenor_tp)
    if best is not None:
        instr = best.get("instruction") or {}
        s = float(instr.get("sell_strike") or 0)
        exp_raw = instr.get("sell_expiration") or ""
        try:
            exp_pretty = datetime.strptime(
                str(exp_raw), "%Y-%m-%d").strftime("%a %b %d '%y")
        except (ValueError, TypeError):
            exp_pretty = str(exp_raw or "?")
        sell_mid = float(instr.get("sell_mid") or 0)
        net = float(best.get("netDollars") or 0)
        out.append(
            f"{n}. **TAKE PROFIT VIA ROLL-DOWN** {contract} — "
            f"+{capture_pct:.0f}% (${pl:+,.0f}) banked as one two-leg roll: "
            f"BTC {int(qty)}× @ ${mid:.2f} mid + STO {int(qty)}× {und} "
            f"${s:g}P {exp_pretty} @ ${sell_mid:.2f} mid → "
            f"net +${net:,.0f} credit"
        )
        out.append(
            f"   - **Why:** banks the risk reduction (strike ${cur_strike:g} "
            f"→ ${s:g}) AND keeps the theta engine running; closing outright "
            f"would pay ${anatomy.extrinsic_total:,.0f} of extrinsic away "
            f"(exit-cost verdict: ROLL, don't close — one card, one voice)."
        )
    else:
        gtc = entry * 0.5 if entry > 0 else 0.0
        out.append(
            f"{n}. **HOLD — GTC AT 50%** {contract} — +{capture_pct:.0f}% "
            f"captured; place a GTC buy-to-close at the 50%-capture price "
            f"${gtc:.2f}"
        )
        out.append(
            f"   - **Why:** the high-extrinsic exit is expensive today "
            f"(closing pays ${anatomy.extrinsic_total:,.0f} of extrinsic"
            f"{' — IV ' + anatomy.iv_context if anatomy.iv_context else ''}) "
            f"and no credit-positive roll-down inside the {_max_tenor_tp}d "
            f"tenor cap is priced — let decay pay you; the GTC fills when "
            f"the market comes to your price."
        )
        # One card, one voice (2026-08-13 IREN fix, part b): the raw verdict
        # is ROLL_DONT_CLOSE but no in-tenor roll is priced, so the headline
        # is HOLD — GTC. Render the verdict as SUBORDINATE context with the
        # reconciliation, never a second full-strength recommendation.
        _verdict_ctx_tp = (
            f"resolved to HOLD — GTC AT 50%: no credit-positive roll-down "
            f"inside the {_max_tenor_tp}d tenor cap is priced this cycle, "
            f"so the position holds with a GTC instead of paying the "
            f"extrinsic to close (one card, one voice)")
        # Honest ALTERNATIVE (never the headline): a small-debit IN-TENOR
        # roll-down with a meaningful strike reduction (≥ $25).
        alt = _pick_debit_roll_down_alternative(rev, _max_tenor_tp)
        if alt is not None:
            a_instr = alt.get("instruction") or {}
            try:
                a_strike = float(a_instr.get("sell_strike") or 0)
                a_mid = float(a_instr.get("sell_mid") or 0)
                a_net = float(alt.get("netDollars") or 0)
                a_exp = datetime.strptime(
                    str(a_instr.get("sell_expiration") or "")[:10],
                    "%Y-%m-%d").strftime("%a %b %d '%y")
            except (TypeError, ValueError):
                a_strike = 0.0
            if a_strike > 0:
                out.append(
                    f"   - **Alternative (in-tenor debit roll-down):** BTC "
                    f"{int(qty)}× @ ${mid:.2f} mid + STO {int(qty)}× {und} "
                    f"${a_strike:g}P {a_exp} @ ${a_mid:.2f} mid → net "
                    f"-${abs(a_net):,.0f} DEBIT — pays to cut the strike "
                    f"${cur_strike:g} → ${a_strike:g}; only take it if the "
                    f"risk reduction is worth the cost."
                )
    out.extend(_exit_cost_lines(rev, snapshot_data, equity_reviews, date_str,
                                include_near_money=True,
                                verdict_context=_verdict_ctx_tp))
    return out


# ── Action-count sync (2026-08-05 defect 3) ────────────────────────────────
# The header's "**Action Items:** N" was computed from render_action_list's
# output BEFORE later composers/post-passes touched the list — the observed
# briefing said "Action Items: 2" while 3 numbered items rendered. This
# post-pass recounts the numbered items in the FINAL composed markdown and
# rewrites the header line to match. Fail-open: any shape it doesn't
# recognize leaves the markdown untouched.
_ACTION_COUNT_RE = re.compile(
    r"^(\*\*Action Items:\*\*)\s*\d+"
    r"(?:\s*\((?:\+\d+ (?:hold|deferred))(?:,\s*\+\d+ (?:hold|deferred))*\))?"
    r"\s*$", re.M)
_ACTION_SECTION_HEAD_RE = re.compile(r"^##\s+Today's Action List", re.M)
_NUMBERED_ITEM_RE = re.compile(r"^\s{0,3}\d+\.\s")
# Capacity-gated planning-card marker (hard rule #41's tag) — a numbered
# item whose block carries it is DEFERRED, not executable today.
_DEFERRED_CAPACITY_RE = re.compile(r"⏸ Deferred \(capacity gated\)")
# HOLD-class headlines (HOLD THROUGH EARNINGS / HOLD — GTC / 🏇 RIDE / 🔧
# OPTIONAL willing-owner credit extension, rule #51) — not order tickets;
# they sort BELOW executable actions, ABOVE deferred cards
# (2026-08-18 bug: "1. HOLD THROUGH EARNINGS IREN … 3. 🚨 URGENT — EXECUTE
# ROLL NOK" put the only executable item LAST; same-day NOK follow-up:
# "Why should I roll Nokia if it's in December?" — the downgraded 🔧
# OPTIONAL roll is a no-action-required item and counts under "+N hold").
_HOLD_CLASS_HEAD_RE = re.compile(
    r"^\s{0,3}\d+\.\s+\S*\s*\*\*(?:HOLD\b|RIDE\b|OPTIONAL\b)")
# 🚨 URGENT executable items rank first within the executable group.
_URGENT_HEAD_RE = re.compile(r"^\s{0,3}\d+\.\s+[^\n]*🚨")


def _split_action_section(markdown: str):
    """Split the Action List section into (before, preamble, item_blocks,
    tail, after). ``item_blocks`` are the numbered cards (header line +
    sub-lines); ``tail`` starts at the first ``###`` sub-header after the
    items (e.g. "### 📋 Total Impact"). Returns None when the shape isn't
    recognized (caller fails open)."""
    head = _ACTION_SECTION_HEAD_RE.search(markdown)
    if head is None:
        return None
    sec_start = head.start()
    after_head = markdown[head.end():]
    nxt = re.search(r"^##\s", after_head, re.M)
    sec_end = head.end() + (nxt.start() if nxt is not None else len(after_head))
    before = markdown[:sec_start]
    section = markdown[sec_start:sec_end]
    after = markdown[sec_end:]

    lines = section.splitlines()
    preamble: list[str] = []
    blocks: list[list[str]] = []
    tail: list[str] = []
    cur: list[str] | None = None
    in_tail = False
    for ln in lines:
        if in_tail:
            tail.append(ln)
            continue
        if _NUMBERED_ITEM_RE.match(ln):
            if cur is not None:
                blocks.append(cur)
            cur = [ln]
        elif cur is not None and ln.strip() and not ln[:1].isspace():
            # A non-blank COLUMN-0 line that isn't a numbered item ends the
            # items region (numbered-card sub-lines are always indented).
            # 2026-08-18 bug: the old parser only terminated at "### ", so
            # the trailing un-numbered "⏸ CSPs — wait for a pullback"
            # subsection (whose PLTR card carries the '⏸ Deferred (capacity
            # gated)' tag) was absorbed into the LAST numbered block — the
            # 🚨 URGENT NOK roll classified as deferred and sorted below a
            # planning card, and the count read "1 (+2 deferred)".
            # Trailing blank lines of the last card move to the tail so the
            # card doesn't drag the separator along when reordered.
            while cur and not cur[-1].strip():
                tail.append(cur.pop())
            blocks.append(cur)
            cur = None
            in_tail = True
            tail.append(ln)
        elif cur is not None:
            cur.append(ln)
        else:
            preamble.append(ln)
    if cur is not None:
        blocks.append(cur)
    return before, preamble, blocks, tail, after


def _classify_action_block(block: list[str]) -> str:
    """Classify a numbered action-list block: "deferred" (carries the
    rule-#41 capacity tag), "hold" (HOLD/RIDE headline — a conscious
    non-action, not an order ticket), or "executable"."""
    if _DEFERRED_CAPACITY_RE.search("\n".join(block)):
        return "deferred"
    if block and _HOLD_CLASS_HEAD_RE.match(block[0]):
        return "hold"
    return "executable"


def sort_deferred_actions(markdown: str) -> str:
    """Sort the Action List: executable actions first (🚨 URGENT at the
    top), then HOLD-class items (HOLD THROUGH EARNINGS / GTC holds / 🏇
    rides), then capacity-gated deferred planning cards — renumbering
    (2026-08-10 bug 4: the digest's action #1 was '**CSP — PAID-TO-WAIT**
    VRT … ⏸ Deferred (capacity gated)' — a non-executable planning card
    ranked ABOVE the executable CLOSE RDDT; 2026-08-18: '1. HOLD THROUGH
    EARNINGS IREN, 2. ⏸ CSP PAID-TO-WAIT VRT, 3. 🚨 URGENT — EXECUTE ROLL
    NOK' sorted the ONLY executable item LAST, below a deferred planning
    card). Stable within each group; deferred cards stay fully visible
    (rule #24/#41). Fail-open on any unrecognized shape."""
    try:
        parts = _split_action_section(markdown or "")
        if parts is None:
            return markdown
        before, preamble, blocks, tail, after = parts
        if len(blocks) < 2:
            return markdown
        executable = [b for b in blocks
                      if _classify_action_block(b) == "executable"]
        hold = [b for b in blocks if _classify_action_block(b) == "hold"]
        deferred = [b for b in blocks
                    if _classify_action_block(b) == "deferred"]
        # 🚨 URGENT executables rank first (stable within urgent/non-urgent).
        executable = ([b for b in executable if _URGENT_HEAD_RE.match(b[0])]
                      + [b for b in executable
                         if not _URGENT_HEAD_RE.match(b[0])])
        reordered = executable + hold + deferred
        if reordered == blocks:
            return markdown
        out_blocks: list[str] = []
        for i, b in enumerate(reordered, 1):
            first = re.sub(r"^(\s{0,3})\d+\.", rf"\g<1>{i}.", b[0], count=1)
            out_blocks.append("\n".join([first] + b[1:]))
        section = "\n".join(preamble + out_blocks + tail)
        # splitlines() drops trailing newlines — restore EXACTLY the raw
        # section's trailing-newline run so the pass is byte-idempotent.
        raw_section = markdown[len(before):len(markdown) - len(after)] \
            if after else markdown[len(before):]
        trailing = raw_section[len(raw_section.rstrip("\n")):]
        section = section.rstrip("\n") + trailing
        return before + section + after
    except Exception:  # noqa: BLE001 — cosmetic sort must never break the ship
        return markdown


def sync_action_item_count(markdown: str) -> str:
    """Rewrite the header's "**Action Items:** N" from the FINAL composed
    action-list section (defect 3, 2026-08-05: header said 2, list rendered
    3 — the count was taken before a later composer appended an item).

    2026-08-10 bug 4: deferred (capacity-gated) planning cards no longer
    inflate the executable count — the header renders them separately as
    "**Action Items:** 1 (+1 deferred)"."""
    try:
        if not markdown:
            return markdown
        if _ACTION_COUNT_RE.search(markdown) is None:
            return markdown
        parts = _split_action_section(markdown)
        if parts is None:
            return markdown
        _, _, blocks, _, _ = parts
        kinds = [_classify_action_block(b) for b in blocks]
        deferred = kinds.count("deferred")
        hold = kinds.count("hold")
        executable = kinds.count("executable")
        segments = ([f"+{hold} hold"] if hold else []) \
            + ([f"+{deferred} deferred"] if deferred else [])
        label = f"{executable}" + (f" ({', '.join(segments)})" if segments
                                   else "")
        return _ACTION_COUNT_RE.sub(rf"\1 {label}", markdown, count=1)
    except Exception:  # noqa: BLE001 — cosmetic sync must never break the ship
        return markdown


# One-voice sweep (2026-08-04 fix 1) — a card must never pair a CLOSE
# headline with a "ROLL, don't close" verdict. CLOSE INTO RECOVERY and CLOSE
# BEFORE EARNINGS are exempt by documented precedence (task #40 fix 4 and
# the 2026-08-13 IREN fix: event risk beats premium mechanics, and those
# cards render the verdict as explicitly-subordinate "⚖️ Context" with the
# reconciliation stated).
_ONE_VOICE_HEAD_RE = re.compile(r"^\s*\d+\.\s+(?:🚨\s*)?\*\*[^*]*CLOSE[^*]*\*\*")
# 2026-08-13 IREN extension: a HOLD headline paired with a FULL-STRENGTH
# "**⚖️ Verdict: ROLL, don't close**" (or a CLOSE verdict) is the same
# multi-voice bug from the other side — the observed card rendered
# "**HOLD — GTC AT 50%** IREN_PUT_47_20261218 — +44% captured" with
# "**⚖️ Verdict: ROLL, don't close**" inline and no reconciliation. A
# verdict rendered via the marked-context form ("⚖️ Context (verdict
# engine, subordinate to the headline): …") is NOT a violation.
_ONE_VOICE_HOLD_HEAD_RE = re.compile(
    r"^\s*\d+\.\s+(?:🚨\s*)?\*\*[^*]*HOLD[^*]*\*\*")
_FULL_STRENGTH_CONTRA_VERDICT_RE = re.compile(
    r"\*\*⚖️ Verdict: (?:ROLL, don't close|CLOSE[^*]*)\*\*")


def one_voice_violations(items) -> list[str]:
    """Sweep rendered action-list lines (or a full briefing markdown string)
    for cards that carry BOTH a CLOSE headline and a 'ROLL, don't close'
    verdict, or a HOLD headline and a full-strength contradicting
    (ROLL/CLOSE) verdict. Returns the offending headlines ([] = one-voice
    rule holds)."""
    if isinstance(items, str):
        items = items.splitlines()
    violations: list[str] = []
    head: str | None = None
    head_kind: str | None = None
    block: list[str] = []

    def _check():
        if head is None:
            return
        text = "\n".join(block)
        if head_kind == "close":
            if ("CLOSE INTO RECOVERY" in head
                    or "CLOSE BEFORE EARNINGS" in head):
                return  # documented precedence — the card explains the override
            if "ROLL, don't close" in text:
                violations.append(head.strip())
        elif head_kind == "hold":
            if _FULL_STRENGTH_CONTRA_VERDICT_RE.search(text):
                violations.append(head.strip())

    for line in items or []:
        if re.match(r"^\s*\d+\.\s", line or ""):
            _check()
            if _ONE_VOICE_HEAD_RE.match(line):
                head, head_kind = line, "close"
            elif _ONE_VOICE_HOLD_HEAD_RE.match(line):
                head, head_kind = line, "hold"
            else:
                head, head_kind = None, None
            block = [line]
        elif head is not None and (line or "").startswith("  "):
            block.append(line)
        else:
            _check()
            head = None
            head_kind = None
            block = []
    _check()
    return violations


def _verdict_hold_basis_lines(n: int, contract: str, rev: dict, anatomy,
                              snapshot_data: dict | None,
                              equity_reviews: list | None,
                              date_str: str | None,
                              churn_age_note: str | None = None) -> list[str]:
    """Render the no-ticket item a HOLD_FOR_BASIS verdict drives (task #40
    fix 1). Observed 2026-07-30: cards #2 (QCOM $185P) and #4 (VRT $280P)
    rendered '⚖️ Verdict: Assignment acceptable — hold for basis' AND an
    actionable debit-roll order — a contradiction. Same verdict-drives-action
    flow as CLOSE_CLEAN / HOLD_FOR_DECAY (kill switch:
    exit_cost.verdict_drives_action): the verdict + anatomy render, the roll
    ticket does not; the full roll menu stays in Watch."""
    out: list[str] = []
    out.append(
        f"{n}. **HOLD FOR BASIS** {contract} — assignment acceptable "
        f"(exit-cost verdict); no roll ticket"
    )
    out.extend(_exit_cost_lines(rev, snapshot_data, equity_reviews, date_str))
    basis = anatomy.assignment_basis
    pct = anatomy.basis_vs_spot_pct
    if basis is not None and pct is not None:
        rel = "below" if pct <= 0 else "above"
        out.append(
            f"   - _Roll skipped: assignment basis ${basis:,.2f} is "
            f"{abs(pct) * 100:.1f}% {rel} market — holding for basis; "
            f"the roll menu stays in Watch._"
        )
    else:
        out.append(
            "   - _Roll skipped: assignment acceptable per the exit-cost "
            "verdict — holding for basis; the roll menu stays in Watch._"
        )
    if churn_age_note:
        out.append(f"   - _{churn_age_note}_")
    return out


def _debit_capped_close_lines(n: int, contract: str, rev: dict, anatomy,
                              debit_pct: float, cap_pct: float,
                              snapshot_data: dict | None,
                              equity_reviews: list | None,
                              date_str: str | None,
                              config_local: dict) -> list[str]:
    """Render the CLOSE action left standing when the debit cap demotes the
    ranked defensive roll on a GENUINELY ITM short put (task #43 fix 2).

    Observed (NOK_PUT_11_20260918, 2026-07-31): yesterday rendered
    '**CLOSE** NOK_PUT_11_20260918 — exit-cost verdict: clean exit;
    buy-to-close 10× limit $2.28'; today the verdict slipped to NEUTRAL
    (extrinsic 15.6% of the buyback vs the 15% clean bar — a $0.03 move),
    the ranked roll-down failed the debit cap ($2,065 debit = 34% of the
    $6,000 new collateral) and the position vanished from the action list
    entirely. A demoted roll must never swallow the position's own exit
    path: the demotion note itself says 'close or take assignment
    instead' — this composes that choice as the action, priced from the
    live chain mid the anatomy measured."""
    out: list[str] = []
    qty_c = abs(rev.get("qty", 0) or 0)
    mid_c = float(anatomy.btc_mid or rev.get("current_mid") or 0)
    cost_c = mid_c * 100.0 * qty_c
    ext_pct_c = (anatomy.extrinsic_per_share / anatomy.btc_mid * 100.0
                 if anatomy.btc_mid else 0.0)
    basis = anatomy.assignment_basis
    basis_bit = (f" — or take assignment (basis ${basis:,.2f})"
                 if basis is not None else "")
    out.append(
        f"{n}. **CLOSE** {contract} — defensive roll debit-capped; "
        f"buy-to-close {int(qty_c)}× limit ${mid_c:.2f} "
        f"(≈ ${cost_c:,.0f}), GTC{basis_bit}"
    )
    out.append(
        f"   - **Why:** the ranked defensive roll was demoted "
        f"(debit {debit_pct:.0f}% of new collateral, cap {cap_pct:.0f}%) — "
        f"on a genuinely ITM put the remaining exits are closing "
        f"(extrinsic {ext_pct_c:.0f}% of the buyback) or taking assignment; "
        f"the full roll menu stays in the Watch panel's ROLL ANALYSIS table."
    )
    out.extend(_exit_cost_lines(rev, snapshot_data, equity_reviews, date_str))
    out.append(
        f"   - **Account:** "
        f"{_route_account('CLOSE', rev.get('underlying', ''), rev.get('account') or rev.get('account_type'), config_local.get('accounts', []) or [])}"
    )
    return out


def _roll_cushion_change_pct(is_put: bool, cur_strike: float,
                             new_strike: float, spot: float) -> float:
    """Signed % change in protective distance, computed from REAL strikes.

    CALL: cap headroom change = (new − cur) / spot (higher strike = more
    upside room). PUT: downside cushion change = (cur − new) / spot (LOWER
    strike = more cushion — the old call-side formula rendered a put
    roll-down as "-12.0% more cap headroom", nonsense on a put).
    """
    if not spot:
        return 0.0
    if is_put:
        return (cur_strike - new_strike) / spot * 100.0
    return (new_strike - cur_strike) / spot * 100.0


def _roll_why_text(is_put: bool, credit: float, underwater: bool,
                   unrealized_loss: float, cur_strike: float,
                   new_strike: float, spot: float, dte_added: int,
                   cur_exp_pretty: str, new_exp_pretty: str) -> str:
    """Sign- and side-aware Why text for a composed roll (task #37 fix 4c).

    A DEBIT roll must never claim "the roll books net credit" — on a put
    roll-down it pays $X of debit for $Y of strike reduction (Z% more
    cushion), and the text says exactly that with measured numbers.
    """
    parts = []
    cushion = _roll_cushion_change_pct(is_put, cur_strike, new_strike, spot)
    if underwater and credit >= 0:
        parts.append(
            f"Position underwater by ~${unrealized_loss:,.0f}; rather than "
            f"realizing that loss, the roll books net credit by extending duration"
        )
    elif credit < 0:
        reduction = (cur_strike - new_strike) if is_put else (new_strike - cur_strike)
        lead = (f"Position underwater by ~${unrealized_loss:,.0f}; the roll "
                if underwater else "The roll ")
        if is_put and reduction > 0:
            parts.append(
                f"{lead}pays ${abs(credit):,.0f} debit for ${reduction:g} of "
                f"strike reduction ({cushion:+.1f}% more downside cushion)"
            )
        elif is_put:
            parts.append(
                f"{lead}pays ${abs(credit):,.0f} debit to extend duration at "
                f"the same strike — no strike reduction; confirm it's worth it"
            )
        else:
            parts.append(
                f"{lead}pays ${abs(credit):,.0f} debit for the adjusted strike "
                f"({cushion:+.1f}% cap headroom change)"
            )
    else:
        parts.append("Best candidate captures meaningful additional premium "
                     "without giving up strike protection")
    if dte_added:
        parts.append(f"adds {dte_added} more days of theta runway "
                     f"({cur_exp_pretty} → {new_exp_pretty})")
    return "; ".join(parts) + "."


def _roll_gain_text(is_put: bool, credit: float, underwater: bool,
                    cur_strike: float, new_strike: float, spot: float,
                    dte_added: int) -> str:
    """Sign- and side-aware Gain text (task #37 fix 4d — no "cap headroom"
    call-language on put rolls; cushion delta computed from real strikes)."""
    cushion = _roll_cushion_change_pct(is_put, cur_strike, new_strike, spot)
    gain_parts = []
    if credit >= 0:
        gain_parts.append(f"${credit:,.0f} cash credited today")
    elif is_put:
        gain_parts.append(
            f"${abs(credit):,.0f} debit paid for {cushion:+.1f}% more downside "
            f"cushion (strike ${cur_strike:g} → ${new_strike:g})"
        )
    else:
        gain_parts.append(
            f"${abs(credit):,.0f} debit paid for {cushion:+.1f}% more cap headroom"
        )
    if dte_added:
        gain_parts.append(f"clock reset by {dte_added}d for continued theta capture")
    if underwater and credit >= 0:
        gain_parts.append("avoids realizing the unrealized loss while "
                          "preserving the path to break-even")
    return "; ".join(gain_parts).capitalize() + "."


def _pre_print_roll_alternative(candidates: list, cur_strike: float,
                                opt_type: str, underlying: str,
                                earnings_calendar: dict, today_iso: str,
                                spot: float) -> dict | None:
    """Find a roll candidate whose STO expiration CLEARS the earnings print.

    Task #37 fix 2: when the ranked best roll's STO leg spans an imminent
    earnings print (earnings guard BLOCK), prefer an alternative expiration
    that expires BEFORE the print — with viable premium — over deferring the
    roll entirely. Returns the best qualifying candidate dict or None.
    """
    try:
        earnings_str = (earnings_calendar or {}).get(underlying)
        earn_date = None
        if earnings_str:
            try:
                earn_date = datetime.strptime(str(earnings_str)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                earn_date = None
        viable = []
        for c in candidates or []:
            if c.get("id") == "A":
                continue
            ins = c.get("instruction") or {}
            exp = ins.get("sell_expiration") or ""
            if not exp:
                continue
            # Put discipline: never swap to a HIGHER strike (deeper ITM).
            if (opt_type == "PUT" and cur_strike
                    and float(ins.get("sell_strike") or 0) > cur_strike + 0.01):
                continue
            # Must genuinely clear the print: expiration strictly before the
            # earnings date, and the guard must not flag it.
            try:
                exp_d = datetime.strptime(str(exp)[:10], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            if earn_date is not None and exp_d >= earn_date:
                continue
            chk = check_earnings_conflict(underlying, exp,
                                          earnings_calendar or {}, today_iso)
            if chk.get("level") == "block":
                continue
            # Viable premium on the STO leg (fix 3's floor, applied here too).
            if float(ins.get("sell_bid") or 0) <= 0:
                continue
            if float(ins.get("sell_mid") or 0) < 0.10:
                continue
            viable.append(c)
        if not viable:
            return None
        best_alt, _ = rank_candidates(
            viable, spot=spot or cur_strike or 1.0,
            min_credit_threshold=0.0,
            option_type=opt_type or "CALL",
        )
        return best_alt
    except Exception:
        return None


def _hedge_nag_vacate(items: list, aging_info: dict | None,
                      config_local: dict, *, instr, strike, exp_str,
                      contracts, cost_f) -> bool:
    """Fix 4 (2026-08-04): hedge-nag resolution.

    After `hedge_nag_days` (config, default 14) consecutive IGNORED sessions
    the HEDGE item vacates the action list's numbered slots — it stops
    outranking money actions — while:
      (a) its aging clock keeps ticking via a synthetic action, so the
          ⛔ Stalled Items entry REMAINS (aging is aging);
      (b) the 💰 Money Plan carries it once in the Blocked-money line as
          "hedge undecided Nd — standing question" (aging_info["hedge_nag"]);
      (c) ready-to-paste directive templates render (both variants: defer
          until coverage ≥ 0.5×, or hedge now at half size) so the user can
          DECIDE it instead of re-reading it a 30th time.

    Returns True when vacated. Fail-open: no aging_info / unmeasurable prior
    days → False and the legacy numbered item renders."""
    if aging_info is None:
        return False
    try:
        nag_days = int((config_local or {}).get("hedge_nag_days", 14) or 14)
    except (TypeError, ValueError):
        nag_days = 14
    ident = str(instr or "SPY_PUT").split("_")[0].upper()
    key = f"HEDGE:{ident}"
    try:
        prior_days = int(((aging_info.get("state") or {}).get(key) or {})
                         .get("days_flagged", 0) or 0)
    except (TypeError, ValueError):
        prior_days = 0
    if prior_days < nag_days:
        return False
    summary = (f"HEDGE Buy {contracts}× {ident} put ${strike}P {exp_str} "
               f"(~${cost_f:,.0f})")
    aging_info.setdefault("synthetic_actions", []).append(
        {"key": key, "kind": "HEDGE", "ident": ident, "summary": summary})
    aging_info["hedge_nag"] = {
        "key": key, "days": prior_days, "contracts": contracts,
        "cost": cost_f, "instrument": ident, "strike": strike,
        "expiration": exp_str, "summary": summary,
    }
    items.append("")
    items.append(
        f"_⏸ HEDGE ({ident} put) vacated from the numbered list — undecided "
        f"{prior_days} consecutive sessions (hedge_nag_days={nag_days}). It "
        f"remains in ⛔ Stalled Items and the 💰 Money Plan as a standing "
        f"question; decide it with a directive:_"
    )
    items.append(
        "_📋 `DIRECTIVE: DEFER hedge until stress coverage ≥ 0.5×; "
        "auto-revisit when coverage crosses the floor.`_"
    )
    if contracts:
        try:
            half = max(1, int(round(float(contracts) / 2.0)))
            items.append(
                f"_📋 `DIRECTIVE: HEDGE NOW at half size — buy {half}× "
                f"{ident} put ${strike}P {exp_str}; revisit the remainder "
                f"next cycle.`_"
            )
            return True
        except (TypeError, ValueError):
            pass
    items.append(
        f"_📋 `DIRECTIVE: HEDGE NOW at half size — halve the recommended "
        f"{ident} put ticket; revisit the remainder next cycle.`_"
    )
    return True


def render_action_list(
    equity_reviews: list,
    options_reviews: list,
    new_ideas: list,
    analytics: dict | None = None,
    snapshot_data: dict | None = None,
    date_str: str | None = None,
    aging_info: dict | None = None,
) -> list:
    """Prioritized action list — synthesized from EVERY actionable signal in the briefing.

    Each numbered action is followed by **Why** and **Gain** sub-bullets that explain
    the trigger and the specific benefit (locked profit, freed collateral, additional
    theta horizon, downside protection, etc.).

    Order: URGENT → CLOSE WINNERS → EXECUTE ROLLS (high credit) → matrix actionable rec
    → CONCENTRATION TRIM → HEDGE if stress < target → equity non-HOLD → top new CSPs.

    When ``aging_info`` is provided (Step 7.5 fill reconciliation + recommendation
    aging), each item is aged against state/rec_aging.yaml: day-3+ IGNORED repeats
    get a ⏳ tag and sort first; day-5+ items render a binary execute-or-directive
    prompt; the headline is capped at 5 items with the rest under
    "### Appendix: Full Action Queue". ``aging_info`` is mutated in place with
    "aged" / "updated_state" / "actions_export" for the stalled panel, the JSON
    sidecar and state persistence. ``aging_info=None`` → legacy behavior.
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

    # Bug #25 — contract-level standing directives (state/fable_advisor_
    # memory.md, parsed by analysis.advisor_directives and stashed on
    # snapshot_data by aggregate). A directive that HOLDS a contract
    # suppresses its CLOSE action (transparency footer instead) and keeps
    # the rec-aging clock from ticking — following a documented directive
    # is not "ignoring" a recommendation. Fail-open: no directives → no-op.
    _adv_directives = (snapshot_data or {}).get("_advisor_directives") or []
    _directive_suppressed: list[dict] = []
    # Task #43 defect 3: directive suppressions PAUSED by an imminent
    # earnings print (capture ≥ 25%) — the CLOSE surfaces normally and the
    # footer explains why the directive didn't hold it this cycle.
    _directive_paused: list[str] = []
    # Directive-held tested/urgent contracts — transparency notes rendered in
    # the footer (rule #24: demote visibly, never hide). Shared by block #3's
    # urgent strike-tested path and block 4c's forced-decision path.
    _tested_directive_notes: list[str] = []

    def _close_held_by_directive(contract: str, rev: dict,
                                 capture_pct: float, dte) -> bool:
        """True when a standing directive suppresses CLOSE on this contract
        (release conditions not met). Records the suppression for the
        transparency footer + aging skip."""
        if not _adv_directives:
            return False
        try:
            from analysis.advisor_directives import directive_holds_contract
            und = rev.get("underlying") or str(contract).split("_")[0]
            spot = float((((snapshot_data or {}).get("quotes") or {})
                          .get(und) or {}).get("last") or 0) or None
            d = directive_holds_contract(
                contract, _adv_directives, capture_pct / 100.0,
                int(dte or 0), spot)
        except Exception:
            return False                 # fail-open — never crash the list
        if d is None:
            return False
        # Task #43 defect 3 (AMD_PUT_420_20261218, 2026-07-31): universal
        # implicit release — a hold directive that predates an imminent
        # earnings print is PAUSED (not deleted) when the position is
        # profitable (capture ≥ 25%). The CLOSE surfaces so the user can
        # decide before the binary; "through earnings" in the directive
        # text is exempt (explicit intent wins). Fail-open toward the
        # directive on any error / missing earnings date.
        try:
            from analysis.advisor_directives import earnings_pause
            _d2e = _days_to_earnings(und)
            if earnings_pause(d, capture_pct / 100.0, _d2e,
                              (snapshot_data or {}).get("_config") or {}):
                _directive_paused.append(
                    f"_📋 Directive on {contract} paused: earnings in "
                    f"{int(_d2e)}d with {capture_pct:+.0f}% captured — the "
                    f"directive predates this print; close before the "
                    f"report or reaffirm the hold (add \"through earnings\" "
                    f"to the directive)._"
                )
                return False
        except Exception:
            pass                         # fail toward respecting the directive
        _directive_suppressed.append({
            "contract": contract, "capture_pct": capture_pct,
            "dte": int(dte or 0),
        })
        return True

    # ---- 1. URGENT (earnings + losing) ----
    # Days-to-earnings lookup for condition-specific Why text (task #40 fix 3:
    # every URGENT item rendered "Earnings or expiry within ~14d …" — false
    # for MU with earnings 55d away; the wrapper was reusing a pre-earnings
    # template on strike-tested items).
    _earn_cal_urgent = (snapshot_data or {}).get("earnings_calendar", {}) or {}
    _today_urgent = None
    try:
        _today_urgent = datetime.strptime(
            (date_str or datetime.now().strftime("%Y-%m-%d")), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        pass

    def _days_to_earnings(underlying: str):
        try:
            e = _earn_cal_urgent.get(underlying)
            if not e or _today_urgent is None:
                return None
            e_d = datetime.strptime(str(e)[:10], "%Y-%m-%d").date()
            return (e_d - _today_urgent).days
        except (ValueError, TypeError):
            return None

    for rev in options_reviews:
        rationale = (rev.get("rationale") or "")
        contract = rev.get("contract", "")
        is_urgent = "🚨" in rationale or "URGENT" in rationale.upper()
        if not is_urgent or contract in seen_contracts:
            continue
        # Task #40 fix 8: strike-tested urgency (GUARDRAIL_STRIKE_TESTED) is
        # a ROLL directive — surfacing urgency with NO order was the bug
        # (items #9/#10/#12-15 on 2026-07-30). Hand these to block #3/#4,
        # which compose the full two-leg combo ticket (or the correct
        # earnings/churn deferral) and carry the 🚨 URGENT branding forward.
        if "STRIKE_TESTED" in (rev.get("matrix_cell_id") or "").upper():
            rev["_urgent_strike_tested"] = True
            continue
        dte = rev.get("days_to_expiry")
        entry = rev.get("entry_price") or 0
        mid = rev.get("current_mid") or 0
        qty = abs(rev.get("qty", 0) or 0)
        loss_dollars = (mid - entry) * 100 * qty if (mid > entry and entry > 0 and qty) else 0
        # Fix 7: word-boundary truncation, never a mid-word slice.
        items.append(f"{n}. 🚨 **URGENT** {contract} — {_truncate_at_word(rationale, 140)}")
        # Fix 3: the Why must state the condition that actually fired.
        _d2e_u = _days_to_earnings(
            rev.get("underlying") or contract.split("_")[0])
        _pre_print = _d2e_u is not None and 0 <= _d2e_u <= 14
        if _pre_print:
            items.append(
                f"   - **Why:** Earnings in {_d2e_u}d combined with material "
                f"underwater P&L; binary gap risk dominates remaining theta. "
                f"Closing/rolling now removes the gap exposure before the report."
            )
        elif dte is not None and 0 <= int(dte) <= 14:
            items.append(
                f"   - **Why:** Expiry in {int(dte)}d with material underwater "
                f"P&L — gamma compounds daily into expiration; act before "
                f"the final week."
            )
        else:
            items.append(
                f"   - **Why:** {_truncate_at_word(rationale, 220)} "
                f"(review cell `{rev.get('matrix_cell_id', '?')}` — no "
                f"imminent earnings/expiry; act on the trigger named above)."
            )
        gain_parts = []
        if _pre_print:
            gain_parts.append("caps tail risk on the next print")
        else:
            gain_parts.append("resolves the flagged risk on your terms")
        if loss_dollars:
            gain_parts.append(f"locks loss at ~${loss_dollars:,.0f} instead of letting it expand")
        if dte:
            gain_parts.append(f"frees the next {dte}d of collateral for redeployment")
        items.append(f"   - **Gain:** {'; '.join(gain_parts).capitalize()}.")
        seen_contracts.add(contract)
        n += 1

    # ---- 2. CLOSE WINNERS (capture >= 30%; pre-print discipline relaxes
    #         the floor to 20% when the print is <= 1 day away) ----
    # Task #43 defect 2 (PLTR $200C, 2026-08-03): Friday's "+37% CLOSE"
    # (4th consecutive session flagged, earnings then 3d) decayed to +28.6%
    # by Monday and slipped under the 30% floor — vanishing from the action
    # list on the DAY of the print, when its urgency peaked. Invariant: a
    # profitable short option on an underlying with earnings ≤ 1 day away
    # must surface a close-or-directive decision item (the wheelhouz
    # "close before print, re-sell after crush" rule).
    _PRE_PRINT_CLOSE_MIN_CAPTURE = 20.0
    for rev in options_reviews:
        contract = rev.get("contract", "")
        if contract in seen_contracts:
            continue
        entry = rev.get("entry_price")
        mid = rev.get("current_mid")
        qty = abs(rev.get("qty", 0) or 0)
        if entry and mid and entry > 0 and qty > 0:
            capture_pct = (entry - mid) / entry * 100
            _d2e_close = _days_to_earnings(
                rev.get("underlying") or contract.split("_")[0])
            _print_imminent = _d2e_close is not None and 0 <= _d2e_close <= 1
            if capture_pct >= 30 or (
                    _print_imminent
                    and capture_pct >= _PRE_PRINT_CLOSE_MIN_CAPTURE):
                pl_dollars = (entry - mid) * 100 * qty
                limit = mid * 1.05
                dte = rev.get("days_to_expiry") or 0
                # 2026-08-06 defect 1: gamma-escape close — DTE ≤ 10 with
                # ≥30% captured (or the guardrail cell itself fired). Side-
                # agnostic: puts AND calls.
                _gamma_escape = (
                    "GAMMA_ESCAPE" in (rev.get("matrix_cell_id") or "").upper()
                    or (dte is not None and 0 <= int(dte) <= 10
                        and capture_pct >= 30))
                # Bug #25: standing directive → suppress the CLOSE entirely
                # (footer transparency line instead; aging clock stops).
                if _close_held_by_directive(contract, rev, capture_pct, dte):
                    seen_contracts.add(contract)
                    continue
                # ── ONE-VOICE rule (2026-08-04 fix 1) ────────────────────
                # A card carries exactly ONE recommendation. The observed
                # briefing rendered "**CLOSE** VRT_PUT_280_20270115 — +30%
                # ($+2,226)" with "**⚖️ Verdict: ROLL, don't close**" inline;
                # the take-profit path never consulted the verdict (task #37
                # wired it into the ROLL path only). A profitable close whose
                # verdict is ROLL_DONT_CLOSE resolves to ONE recommendation:
                # TAKE PROFIT VIA ROLL-DOWN (two-leg ticket) or, with no
                # credit-positive roll-down priced, HOLD — GTC at 50%.
                # CLOSE_* verdicts (they agree with closing) are unchanged.
                # Kill switch: exit_cost.one_voice (default true).
                _ov_anatomy = None
                _ov_status = None
                _ov_anatomy_computed = False
                _ov_cfg = (((snapshot_data or {}).get("_config", {}) or {})
                           .get("exit_cost") or {})
                if _ov_cfg.get("one_voice", True):
                    # 2026-08-05 defect 2 (VRT $280P/$270P): near-money puts
                    # are IN scope — yesterday's ITM "ROLL, don't close"
                    # position drifting 0.3% OTM must not silently lose its
                    # anatomy/verdict on today's CLOSE card.
                    _ov_anatomy, _ov_status = _exit_cost_anatomy(
                        rev, snapshot_data, equity_reviews, date_str,
                        include_near_money=True)
                    _ov_anatomy_computed = True
                    if (_ov_anatomy is not None
                            and _ov_anatomy.verdict in ("ROLL_DONT_CLOSE",
                                                        "HOLD_FOR_DECAY")):
                        # One card, one voice: ROLL_DONT_CLOSE resolves to
                        # TAKE PROFIT VIA ROLL-DOWN / HOLD — GTC; a
                        # HOLD_FOR_DECAY verdict (newly reachable via the
                        # near-money gate) resolves to HOLD — GTC directly —
                        # never a CLOSE headline contradicted by a HOLD
                        # verdict inline.
                        items.extend(_one_voice_take_profit_lines(
                            n, contract, rev, _ov_anatomy, capture_pct,
                            snapshot_data, equity_reviews, date_str))
                        seen_contracts.add(contract)
                        n += 1
                        continue
                # ── Dollar action floor (rule #43 micro-fix, 2026-08-04) ──
                # Observed: "CLOSE AMZN_CALL_330_20260904 — +30% ($+27);
                # buy-to-close limit $0.66 ... Remaining ~$63 of theta" — a
                # recommendation to bank twenty-seven dollars. Churn noise:
                # commissions/spread eat a meaningful fraction, and it
                # occupies an action slot. A take-profit close whose banked
                # profit is below close_winner_min_dollars (default $100)
                # demotes to a Watch-panel note (rule #24 — visible, never
                # hidden). Exceptions that ALWAYS surface: pre-print closes
                # (earnings ≤ 2d), loss-stops, CLOSE_URGENT/recovery
                # verdicts, gamma-escape (DTE ≤ 10, capture ≥ 30%) closes on
                # ANY short option — put or CALL. 2026-08-06 defect 1: the
                # put-only exception floored the SPY_CALL_784_20260811
                # gamma-escape (77% captured, $87) two days running while
                # Risk Alerts screamed CLOSE_FOR_PROFIT.
                # (CLOSE INTO RECOVERY and the URGENT block are separate
                # composers — never floored.) Config 0 disables.
                try:
                    _cw_floor = float(
                        ((snapshot_data or {}).get("_config", {}) or {})
                        .get("close_winner_min_dollars", 100))
                except (TypeError, ValueError):
                    _cw_floor = 100.0
                if _cw_floor > 0 and pl_dollars < _cw_floor:
                    _cw_pre_print = (_d2e_close is not None
                                     and 0 <= _d2e_close <= 2)
                    _cw_gamma_escape = _gamma_escape
                    _cw_loss_stop = "LOSS_STOP" in (
                        rev.get("matrix_cell_id") or "").upper()
                    if not _ov_anatomy_computed:
                        try:
                            _ov_anatomy, _ov_status = _exit_cost_anatomy(
                                rev, snapshot_data, equity_reviews, date_str,
                                include_near_money=True)
                        except Exception:
                            _ov_anatomy = None
                    _cw_urgent_verdict = (
                        _ov_anatomy is not None
                        and _ov_anatomy.verdict == "CLOSE_URGENT")
                    if not (_cw_pre_print or _cw_gamma_escape
                            or _cw_loss_stop or _cw_urgent_verdict):
                        rev["_close_floor_demotion"] = (
                            f"+{capture_pct:.0f}% captured but only "
                            f"${pl_dollars:,.0f} — below the "
                            f"${_cw_floor:,.0f} action floor; let it decay "
                            f"or close at your convenience (buy-to-close "
                            f"~${limit:.2f})."
                        )
                        seen_contracts.add(contract)
                        continue
                # ── Momentum-hold overlay (George 2026-08-17: "If
                # something is ripping and it didn't get out to RSI, say,
                # to 70 or something. Do I really need to close? ... let's
                # make sure we squeeze as much as possible out of these
                # options.") — a yield-motivated winner close on a SHORT
                # PUT defers to 🏇 RIDE while the measured trend is UP
                # (day ≥ 0 AND 3-session > 0), vintage-resolved RSI <
                # stall, OTM cushion ≥ floor, capture < ceiling. Risk-
                # driven closes always override (over-cap concentration,
                # loss stops, urgent verdicts, earnings inside window,
                # gamma escape, hard ceiling — momentum_hold.risk_exempt
                # reuses the existing exemption lists). Fail-open FALSE on
                # unmeasurable momentum. When a riding-class position
                # stalls (red day / RSI ≥ stall), the returning CLOSE
                # carries "(momentum stalled — take it)". Config-gated
                # (momentum_hold.enabled). Source: analysis/momentum_hold.py.
                _mh_stall = ""
                try:
                    from analysis import momentum_hold as _mh
                    _mh_cfg = (snapshot_data or {}).get("_config", {}) or {}
                    _mh_on = (_mh.enabled(_mh_cfg)
                              and (rev.get("type") or "").upper() == "PUT")
                except Exception:
                    _mh_on = False
                if _mh_on:
                    try:
                        if not _ov_anatomy_computed:
                            try:
                                _ov_anatomy, _ov_status = _exit_cost_anatomy(
                                    rev, snapshot_data, equity_reviews,
                                    date_str, include_near_money=True)
                                _ov_anatomy_computed = True
                            except Exception:
                                pass
                        _mh_risk = _mh.risk_exempt(
                            rev, snapshot_data, _mh_cfg,
                            capture_pct=capture_pct,
                            days_to_earnings=_d2e_close,
                            dte=(int(dte) if dte is not None else None),
                            anatomy_verdict=getattr(_ov_anatomy, "verdict",
                                                    None),
                            gamma_escape=_gamma_escape)
                        if _mh_risk is None:
                            _mh_ride, _mh_why = _mh.momentum_ride(
                                rev, snapshot_data, _mh_cfg)
                        else:
                            _mh_ride, _mh_why = False, ""
                    except Exception:
                        _mh_ride, _mh_why = False, ""
                    if _mh_ride:
                        rev["_momentum_ride"] = _mh_why
                        items.extend(_momentum_ride_lines(
                            n, contract, rev, _mh_why, capture_pct))
                        seen_contracts.add(contract)
                        n += 1
                        continue
                    if _mh_why.startswith("stalled"):
                        _mh_stall = " (momentum stalled — take it)"
                # ── Redeployability-aware take-profit (George 2026-08-10:
                # "I'm happy to exit options and close it if we have a path
                # to redeployment. If we don't have a path to redeployment,
                # then it doesn't make sense to close it.") ─────────────────
                # A yield-motivated winner close on a SHORT PUT demotes to a
                # visible "⏳ Holding for 75%+" Watch note when the freed
                # collateral has nowhere to go: gates closed, the close
                # itself would NOT reopen them, and no A/B setup is waiting.
                # Risk-driven closes are exempt and fire exactly as today:
                # pre-print (earnings ≤ 2d), gamma escape, loss-stop /
                # crash / tail-risk cells, CLOSE_URGENT verdicts, and the
                # hard ceiling / hold target (capture already at the raised
                # floor). Config-gated (redeploy_aware_tp.enabled); fail-open
                # on unresolvable coverage — missing data never traps a
                # winner at a raised floor. Source: analysis/redeploy_path.py.
                _rd_reason = None
                try:
                    from analysis import redeploy_path as _rdp
                    _rd_cfg_all = (snapshot_data or {}).get("_config", {}) or {}
                    if _rdp.enabled(_rd_cfg_all) \
                            and (rev.get("type") or "").upper() == "PUT":
                        _rd_target = _rdp.hold_target_pct(_rd_cfg_all)
                        _rd_pre_print = (_d2e_close is not None
                                         and 0 <= _d2e_close <= 2)
                        _rd_cell = (rev.get("matrix_cell_id") or "").upper()
                        _rd_risk_cell = any(t in _rd_cell for t in (
                            "LOSS_STOP", "HARD_CEILING", "GAMMA_ESCAPE",
                            "EARNINGS_IMMINENT", "CRASH_STOP", "TAIL_RISK"))
                        _rd_ceiling = (capture_pct
                                       >= _rdp.hard_ceiling_pct(_rd_cfg_all)
                                       * 100.0)
                        if not _ov_anatomy_computed:
                            try:
                                _ov_anatomy, _ov_status = _exit_cost_anatomy(
                                    rev, snapshot_data, equity_reviews,
                                    date_str, include_near_money=True)
                                _ov_anatomy_computed = True
                            except Exception:
                                pass
                        _rd_urgent = (_ov_anatomy is not None
                                      and getattr(_ov_anatomy, "verdict", None)
                                      == "CLOSE_URGENT")
                        # Over-cap concentration exemption (rule #43,
                        # 2026-08-17 SNDK/SOXX): a close that reduces an
                        # over-cap obligation-inclusive name concentration
                        # is RISK-driven — never held for yield.
                        _rd_overcap = _rdp.over_cap_risk_exemption(
                            rev, snapshot_data, _rd_cfg_all)
                        if _rd_overcap:
                            _rd_reason = _rd_overcap
                        elif not (_rd_pre_print or _gamma_escape
                                  or _rd_risk_cell
                                  or _rd_ceiling or _rd_urgent
                                  or capture_pct >= _rd_target * 100.0):
                            _rd_ok, _rd_why = _rdp.redeployment_path(
                                analytics,
                                (snapshot_data or {}).get(
                                    "_redeploy_best_setups"),
                                _rdp.close_impact_from_review(rev),
                                config=_rd_cfg_all)
                            if not _rd_ok:
                                rev["_redeploy_hold_demotion"] = _rdp.hold_note(
                                    capture_pct, _rd_why, _rd_target)
                                seen_contracts.add(contract)
                                continue
                            if _rd_why:
                                _rd_reason = _rd_why
                except Exception:
                    _rd_reason = None  # advisory — never break the list
                strike = _strike_from_contract(contract, rev.get("strike"))
                opt_type = (rev.get("type") or "").upper()
                if opt_type == "PUT" and strike:
                    collateral = strike * 100 * qty
                    collateral_label = f"frees ${collateral:,.0f} cash collateral"
                elif opt_type == "CALL" and strike:
                    collateral = strike * 100 * qty
                    collateral_label = f"unlocks 100×{int(qty)} shares (notional ${collateral:,.0f}) for fresh covered-call premium"
                    # ── Honest Gain line (2026-08-13 SMH bug): "for fresh
                    # covered-call premium" is boilerplate that promises an
                    # immediate rewrite the briefing's OWN RSI discipline
                    # gates — new CC writes below the strength floor (RSI
                    # < call.caution_below, default 60) sit in the
                    # wait-for-strength band (hard rule #11). When the
                    # measured RSI is in the wait/blocked band, say so with
                    # the config-derived threshold; RSI-unknown keeps the
                    # legacy label (never fabricate a gate — rule #19).
                    try:
                        from analysis import rsi_discipline as _rsi_cc
                        _cc_cfg_all = ((snapshot_data or {}).get("_config", {})
                                       or {})
                        _cc_th = _rsi_cc.load_thresholds(_cc_cfg_all)
                        _cc_rsi = _rsi_cc.rsi_for(
                            rev.get("underlying")
                            or contract.split("_")[0],
                            (snapshot_data or {}).get("technicals"))
                        if _cc_rsi is not None:
                            _cc_assess = _rsi_cc.assess(_cc_rsi, "call",
                                                        _cc_th)
                            if _cc_assess.zone in ("blocked", "caution"):
                                _cc_floor = float(
                                    (_cc_th.get("call") or {}).get(
                                        "caution_below", 60))
                                collateral_label += (
                                    f" — rewrite gated today (RSI "
                                    f"{_cc_rsi:.0f} {_cc_assess.label}; "
                                    f"a fresh CC waits for strength, RSI "
                                    f"≥ {_cc_floor:.0f})")
                    except Exception:
                        pass  # advisory — never break the close card
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
                    f"buy-to-close limit ${limit:.2f}{_mh_stall}"
                )
                if yield_res:
                    items.append(f"   - {format_yield_line(yield_res)}")
                if _print_imminent:
                    items.append(
                        f"   - **Why:** Earnings print in {_d2e_close}d with "
                        f"{capture_pct:.0f}% of max profit captured — close "
                        f"BEFORE the print to lock the gain, re-sell after the "
                        f"IV crush (pre-print discipline: a profitable short "
                        f"option doesn't hold through the binary unmanaged)."
                    )
                elif _gamma_escape:
                    # 2026-08-06 defect 1: the gamma rationale travels with
                    # the action — a gamma-escape close is about the risk
                    # zone, not the close-at-50% rule.
                    items.append(
                        f"   - **Why:** Gamma-escape — DTE {int(dte)} ≤ 10 with "
                        f"{capture_pct:.0f}% captured. The remaining "
                        f"~${remaining_premium:,.0f} of theta rides final-week "
                        f"gamma, where one adverse gap erases weeks of decay — "
                        f"lock the win before the gamma-risk zone."
                    )
                else:
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
                # Redeploy-aware TP: when the gates are closed but a path
                # exists, the rec line carries the MEASURED path reason
                # (George 2026-08-10 — the close is justified BY the path).
                if _rd_reason:
                    _rd_lbl = ("Risk-driven close"
                               if _rd_reason.startswith("risk:")
                               else "Redeploy path")
                    items.append(f"   - **{_rd_lbl}:** {_rd_reason}")
                # Exit-cost anatomy footer — NEVER silent (2026-08-05 defect
                # 2): full anatomy when computable (incl. near-money), a
                # fail-closed "verify at broker" warning with yesterday's
                # verdict when it isn't, "OTM — pure time value" for genuine
                # OTM winners.
                if not _ov_anatomy_computed:
                    _ov_anatomy, _ov_status = _exit_cost_anatomy(
                        rev, snapshot_data, equity_reviews, date_str,
                        include_near_money=True)
                items.extend(_close_anatomy_footer(
                    rev, _ov_anatomy, _ov_status, snapshot_data,
                    equity_reviews, date_str))
                seen_contracts.add(contract)
                n += 1

    # ---- 3. EXECUTE ROLL — use tax-aware ranker (core mode for core_positions) ----
    ROLL_CREDIT_THRESHOLD = 1000.0
    config_local = (snapshot_data or {}).get("_config", {}) if snapshot_data else {}
    # Cluster-aware roll selection (2026-08-17 QCOM bug: action #2 rolled
    # the $180P INTO Thu Jun 17 '27 — the exact $257K / 22.9%-NLV bucket
    # red flag #5 said "Don't: roll multiple positions INTO this date").
    # The ranker deprioritizes candidates landing on ≥warning buckets; when
    # the chosen best STILL lands on one, the ticket renders the measured
    # cluster warning line. Fail-open: no analytics / import failure → the
    # legacy ranking, no warning fabricated (rule #19).
    _put_buckets_al = (analytics or {}).get("put_buckets") or []
    try:
        _nlv_al = float((analytics or {}).get("nlv") or 0) or None
    except (TypeError, ValueError):
        _nlv_al = None
    try:
        _bucket_warning_pct = float(
            (config_local.get("expiration_bucket") or {}).get(
                "warning_pct", 0.20))
    except (TypeError, ValueError):
        _bucket_warning_pct = 0.20
    _roll_cfg_top = (config_local.get("roll") or {})
    try:
        _cluster_credit_tol = float(_roll_cfg_top.get(
            "cluster_avoidance_credit_tolerance", 0.20))
    except (TypeError, ValueError):
        _cluster_credit_tol = 0.20
    _bucket_map_al: dict = {}
    try:
        from analysis.expiration_ladder import (
            bucket_pct_by_exp as _bucket_pct_by_exp_fn,
            roll_into_cluster_warning as _roll_cluster_warning_fn,
        )
        _bucket_map_al = _bucket_pct_by_exp_fn(_put_buckets_al)
    except Exception:
        _bucket_map_al = {}
        _roll_cluster_warning_fn = None
    # 2026-08-04 (PLTR): "core" = core_positions ∪ Tier A — a Tier A name
    # gets the same roll tenor allowance / tax-aware core ranking.
    core_tickers_set = _core_union_safe(config_local)
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
        # Genuine assignment risk = actually AT/PAST the strike, not merely within
        # the loose 3% buffer band.
        genuinely_itm = (
            (_opt_type_gate == "PUT" and 0 < _moneyness <= 1.0)
            or (_opt_type_gate == "CALL" and _moneyness >= 1.0)
        )
        # DEFER TO THE POSITION'S OWN ADVISOR. If the wheel-roll-advisor
        # explicitly recommends HOLD (recommendation == HOLD, or its recommended
        # candidate is "A" = don't roll), the loose NEAR_ATM / 3%-buffer trigger
        # must NOT override it — surfacing EXECUTE ROLL here would contradict the
        # ROLL ANALYSIS table's own recommendation. Only an explicit ROLL rec,
        # genuine ITM, or an at-strike short-dated/earnings position overrides a
        # HOLD. (See CLAUDE.md "Challenge every recommendation — multi-perspective".)
        advisor_says_hold = (
            "HOLD" in advisor_rec
            or rev.get("recommended_candidate_id") == "A"
            or advisor_rec in ("", "WAIT", "WATCH")
        )
        defensive = (
            "ROLL" in advisor_rec
            or "DEEP_ITM" in matrix_cell
            or genuinely_itm
            or (position_at_or_past_strike and 0 < _dte <= 14)
            or (position_at_or_past_strike
                and _days_to_earn is not None and 0 < _days_to_earn <= 14)
            or (not advisor_says_hold
                and ("NEAR_ATM" in matrix_cell or position_at_or_past_strike))
        )
        if not defensive:
            # Quietly skip — Watch panel still shows the ROLL ANALYSIS table
            # so the user can act on these opportunistically if they want.
            continue

        # Rule #3 hard gate (TSM 2026-07-30): even an explicit matrix ROLL
        # rec cannot surface an actionable short-put roll without genuine
        # assignment risk — moneyness < 1.03 OR measured |δ| ≥ 0.40. Demote
        # to the Watch panel (note stashed on the review), never a ticket.
        if _opt_type_gate == "PUT" and not _short_put_roll_gate_ok(
                _moneyness, rev.get("delta")):
            rev["_roll_gate_demotion"] = _roll_gate_demotion_note(
                _moneyness, rev.get("delta"))
            seen_contracts.add(contract)
            continue

        # Task #40 fix 6 (continued): profitable + OTM + measured δ below
        # the tested threshold → nothing to defend (the AVGO shape).
        if _opt_type_gate == "PUT":
            _ntd = _nothing_to_defend_note(rev, _moneyness)
            if _ntd:
                rev["_roll_gate_demotion"] = _ntd
                seen_contracts.add(contract)
                continue

        # Task #40 fix 8: an URGENT strike-tested contract covered by a
        # standing directive gets the transparency note, not the nag —
        # the user's decision is on file.
        if rev.get("_urgent_strike_tested") and _adv_directives:
            try:
                from analysis.advisor_directives import directive_holds_contract
                _cap_u = 0.0
                _e_u = float(rev.get("entry_price") or 0)
                _m_u = float(rev.get("current_mid") or 0)
                if _e_u > 0:
                    _cap_u = (_e_u - _m_u) / _e_u
                _held_u = directive_holds_contract(
                    contract, _adv_directives, _cap_u,
                    int(rev.get("days_to_expiry") or 0), _spot_gate or None)
            except Exception:
                _held_u = None
            if _held_u is not None:
                _tested_directive_notes.append(
                    f"_⏸ {contract} strike-tested (urgent) — held by "
                    f"standing directive; decision on file_"
                )
                seen_contracts.add(contract)
                continue

        # Task #40 fix 2: churn guard — no roll recommendation on a contract
        # opened within roll.min_position_age_days (default 5 trading days).
        # VRT_PUT_280 was filled at ~2:23 PM and the 2:54 PM run recommended
        # re-rolling it for -$2,740; QCOM $185P opened the same morning got a
        # same-day re-roll. Safety always wins: loss-stop/crash cells are
        # exempt inside check_churn_guard. Fail-open on unmeasurable age.
        try:
            from analysis.churn_guard import check_churn_guard
            _churn_note = check_churn_guard(
                contract, rev.get("matrix_cell_id"),
                (snapshot_data or {}).get("_position_ages"), config_local)
        except Exception:
            _churn_note = None

        # ── Task #37 fix 1: the exit-cost VERDICT drives the action ──────
        # (rule #14's pattern — the action list defers to the position's own
        # advisor). When analysis/exit_cost.py says CLOSE — clean exit
        # (mostly intrinsic), composing a large-debit roll on top of it is a
        # contradiction: the 2026-07-30 briefing spent $44K of roll debits
        # while five of those positions' own verdicts said closing was cheap.
        # Kill switch: exit_cost.verdict_drives_action (default true).
        _xc_cfg = (config_local.get("exit_cost") or {})
        _verdict_drives = bool(_xc_cfg.get("verdict_drives_action", True))
        _anatomy = None
        if _verdict_drives:
            _anatomy, _ = _exit_cost_anatomy(rev, snapshot_data,
                                             equity_reviews, date_str)
        if _anatomy is not None and _anatomy.verdict in _CLOSE_VERDICTS:
            items.extend(_verdict_close_lines(
                n, contract, rev, _anatomy, snapshot_data,
                equity_reviews, date_str, config_local))
            seen_contracts.add(contract)
            n += 1
            continue
        if _anatomy is not None and _anatomy.verdict == "HOLD_FOR_DECAY":
            # HOLD_FOR_DECAY suppresses BOTH the close and the roll ticket —
            # the position renders in Watch with the verdict note only
            # (per_option_commentary surfaces it there).
            seen_contracts.add(contract)
            continue
        if _anatomy is not None and _anatomy.verdict == "HOLD_FOR_BASIS":
            # Task #40 fix 1: HOLD_FOR_BASIS suppresses the roll ticket
            # exactly like CLOSE_CLEAN / HOLD_FOR_DECAY drive theirs — the
            # 2026-07-30 briefing rendered "Assignment acceptable — hold for
            # basis" UNDER an actionable $1,027/$2,740 debit-roll order on
            # QCOM $185P / VRT $280P. Verdict + anatomy render; no ticket.
            items.extend(_verdict_hold_basis_lines(
                n, contract, rev, _anatomy, snapshot_data,
                equity_reviews, date_str, churn_age_note=_churn_note))
            seen_contracts.add(contract)
            n += 1
            continue

        # Task #40 fix 2 (continued): a measurable age below the churn floor
        # demotes the roll to a Watch note — the position the user JUST
        # opened gets a settling period before any re-roll recommendation.
        if _churn_note:
            rev["_churn_guard_demotion"] = _churn_note
            seen_contracts.add(contract)
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

        # Tenor cap — don't surface a roll that locks the cap for years just to
        # harvest long-dated time value. Core names (rolled year after year) may
        # extend longer than wheel names.
        _roll_cfg = (config_local.get("roll") or {})
        _max_action_tenor = int(_roll_cfg.get("max_action_tenor_days", 120))
        _max_tenor_for_pos = _max_action_tenor * 3 if is_core else _max_action_tenor

        # Task #40 fix 8: on an URGENT strike-tested put with an OPEN credit
        # window, rank among the same-or-lower-strike CREDIT candidates —
        # the whole point of the strike-test alarm is "roll while the credit
        # window is open"; picking a debit roll there contradicts it.
        candidates_for_rank = candidates
        if rev.get("_urgent_strike_tested") and _opt_type_gate == "PUT":
            _cw_u = (((snapshot_data or {}).get("_credit_windows") or {})
                     .get(contract)) or {}
            if _cw_u.get("state") == "open":
                _credit_cands = [
                    c for c in candidates
                    if c.get("id") != "A"
                    and float(c.get("netDollars") or 0) > 0
                    and float((c.get("instruction") or {}).get("sell_strike")
                              or 0) <= (cur_strike_for_rank or 0) + 0.01
                ]
                if _credit_cands:
                    candidates_for_rank = _credit_cands

        try:
            best, _scores = rank_candidates(
                candidates_for_rank, spot=underlying_spot or cur_strike_for_rank,
                is_core=is_core, embedded_tax_dollars=embedded_tax,
                min_credit_threshold=ROLL_CREDIT_THRESHOLD,
                max_tenor_days=_max_tenor_for_pos,
                # Side-aware ranking (CLAUDE.md #42): defensive short-PUT rolls
                # rank by strike reduction, never max credit — a put roll-up is
                # deeper ITM, not "more upside".
                option_type=_opt_type_gate or "CALL",
                # Cluster-aware selection (2026-08-17 QCOM/Jun-17-'27 bug):
                # deprioritize STO legs landing on ≥warning put buckets when
                # a non-clustered alternative is within the credit tolerance.
                bucket_pct_by_exp=_bucket_map_al,
                bucket_warning_pct=_bucket_warning_pct,
                cluster_credit_tolerance=_cluster_credit_tol,
            )
        except TypeError:
            # Older ranker without the cluster kwargs — legacy call.
            best, _scores = rank_candidates(
                candidates_for_rank, spot=underlying_spot or cur_strike_for_rank,
                is_core=is_core, embedded_tax_dollars=embedded_tax,
                min_credit_threshold=ROLL_CREDIT_THRESHOLD,
                max_tenor_days=_max_tenor_for_pos,
                option_type=_opt_type_gate or "CALL",
            )
        if best:
            # ── Task #37 fix 2: earnings guard runs BEFORE the ticket is
            # composed. A 🔴 BLOCK on the STO leg (it would span an imminent
            # print) must never render under a fully actionable limit order
            # ("NEVER sell puts through earnings"). Preference order:
            #   1. Swap to an alternative candidate whose expiration CLEARS
            #      the print (expires before earnings) with viable premium;
            #   2. else demote to a visible, non-actionable ROLL DEFERRED
            #      entry (rule #24 — demote, never hide).
            _today_iso_gate = date_str or datetime.now().strftime("%Y-%m-%d")
            _earn_cal_gate = (snapshot_data or {}).get("earnings_calendar", {}) or {}
            earnings_deferred = False
            pre_print_swap = False
            _gate_exp = (best.get("instruction") or {}).get("sell_expiration") or ""
            _gate_check = check_earnings_conflict(
                rev.get("underlying", contract.split("_")[0]),
                _gate_exp, _earn_cal_gate, _today_iso_gate)
            if _gate_check.get("level") == "block":
                _alt = _pre_print_roll_alternative(
                    candidates, cur_strike_for_rank,
                    _opt_type_gate or "PUT",
                    rev.get("underlying", contract.split("_")[0]),
                    _earn_cal_gate, _today_iso_gate, underlying_spot)
                if _alt is not None and _alt is not best:
                    best = _alt
                    pre_print_swap = True
                else:
                    earnings_deferred = True

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
            # Monthly/weekly label on the roll's STO leg — computed from the
            # REAL candidate expiration (rule #19); no-op when the expiration
            # policy is disabled (legacy byte-identical output).
            try:
                from analysis.expiration_policy import label_exp as _lbl_b3, policy_enabled as _pe_b3
                new_exp_pretty = _lbl_b3(
                    new_exp_pretty, new_exp_raw,
                    enabled=_pe_b3((snapshot_data or {}).get("_config", {}) or {}),
                )
            except Exception:
                pass
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
            # ── Rule #19 (2026-08-13 NOK bug): the new leg's FULL premium
            # must be annualized over the FULL new-leg DTE, never the
            # extension window. Passing `new_dte=dte_added` (28d) annualized
            # $1.98/share of 155d premium over 28 days and rendered
            # "Yield: **234.1%** ann. on new collateral ($11,000)" — the
            # honest figures are ~42% ann. (full premium / full 155d leg)
            # and the net-credit-over-extension read (+6.2% ann.), which
            # compute_roll_yield now separates via `extension_days`.
            _new_leg_dte = 0
            try:
                _ry_today = (datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
                             if date_str else datetime.now().date())
                _new_leg_dte = (datetime.strptime(
                    str(new_exp_raw)[:10], "%Y-%m-%d").date() - _ry_today).days
            except (TypeError, ValueError):
                _new_leg_dte = 0
            if _new_leg_dte <= 0:
                try:
                    _new_leg_dte = int(rev.get("days_to_expiry") or 0) + int(dte_added or 0)
                except (TypeError, ValueError):
                    _new_leg_dte = 0
            _ry_kwargs = dict(
                new_premium=new_mid, new_strike=new_strike,
                new_dte=_new_leg_dte or dte_added or 30,
                contracts=int(qty), spot=underlying_spot or 1,
                net_credit_dollars=credit, position_value=position_value,
                old_strike=cur_strike,
            )
            try:
                roll_yield = compute_roll_yield(
                    option_type=opt_type or "CALL",
                    extension_days=int(dte_added) if dte_added else None,
                    **_ry_kwargs,
                )
            except TypeError:
                # Older yield-calculator without extension_days/option_type —
                # fall back progressively (full-DTE window either way).
                try:
                    roll_yield = compute_roll_yield(
                        option_type=opt_type or "CALL", **_ry_kwargs)
                except TypeError:
                    roll_yield = compute_roll_yield(**_ry_kwargs)

            # Headline — option-type aware. For CALLs, higher strike = more
            # cap headroom (defensive). For PUTs, higher strike = closer to
            # ATM = MORE assignment risk (offensive, not defensive). Don't
            # mislabel.
            is_calendar = (cur_strike == new_strike)
            is_put = opt_type == "PUT"
            higher_strike = new_strike > cur_strike

            # CLAUDE.md #14: a covered call that's still OTM has no real
            # assignment risk — only a genuine roll-UP (higher strike) belongs
            # in the action list. A same-strike calendar or down-roll just
            # re-caps a bullish name to harvest premium (the SMH/SOXX bug,
            # where the only credit-positive candidate was a same-strike
            # re-cap mislabeled as "up-and-out"). Defer to HOLD here; the
            # Watch ROLL ANALYSIS table still shows the full menu.
            if opt_type == "CALL" and not genuinely_itm and not higher_strike:
                seen_contracts.add(contract)
                continue

            if is_put and higher_strike and not is_calendar:
                # Surfacing a put-roll UP would guarantee/accelerate assignment.
                # Skip this candidate entirely — the ranker shouldn't have
                # surfaced it as a defensive move in the first place.
                seen_contracts.add(contract)
                continue

            if is_calendar:
                roll_label = "Calendar roll (same strike, longer date)"
            elif higher_strike:
                # CALL only at this point (puts handled above)
                roll_label = "Diagonal up-and-out (raises cap)"
            else:
                # Lower strike: for CALLs this is offensive (lowers cap), for
                # PUTs this is defensive (further OTM = less assignment risk)
                roll_label = (
                    "Diagonal down-and-out (reduces assignment risk)"
                    if is_put else "Diagonal down-and-out (lowers cap — offensive)"
                )
            credit_label = (f"+${credit:,.0f} net credit"
                            if credit >= 0 else f"−${abs(credit):,.0f} net debit (paying for cushion)")

            if earnings_deferred:
                # Task #37 fix 2 — demote visibly (rule #24): keep the
                # analysis (anatomy, why, reference strikes) but strip every
                # executable order/limit line.
                _earn_str = _earn_cal_gate.get(
                    rev.get("underlying", contract.split("_")[0]))
                try:
                    _earn_pretty = datetime.strptime(
                        str(_earn_str)[:10], "%Y-%m-%d").strftime("%b %d")
                except (ValueError, TypeError):
                    _earn_pretty = str(_earn_str or "the upcoming")

                # ── Task #40 fix 4: CLOSE INTO RECOVERY ──────────────────
                # When the roll is earnings-blocked AND the position has
                # recovered to better than recovery_close.max_loss_pct
                # (default -5% of premium) AND earnings are ≤ 14d away,
                # exit flat pre-print instead of surfacing nothing (the LITE
                # $700P case: underwater by ~$2 after a +14% bounce, earnings
                # 12d away, roll correctly blocked — and NO action surfaced).
                # PRECEDENCE: this outranks the ROLL_DONT_CLOSE exit-cost
                # verdict — that verdict optimizes premium mechanics
                # (swap inflated IV for inflated IV); it does not price the
                # binary event risk of holding a big short put through a
                # print. Removing the binary at ~zero cost wins.
                _rc_cfg = (config_local.get("recovery_close") or {})
                try:
                    _rc_max_loss = abs(float(
                        _rc_cfg.get("max_loss_pct", 0.05)))
                except (TypeError, ValueError):
                    _rc_max_loss = 0.05
                _loss_frac = ((cur - entry) / entry) if entry > 0 else None
                _d2e_rc = None
                try:
                    if _earn_str:
                        _e_rc = datetime.strptime(
                            str(_earn_str)[:10], "%Y-%m-%d").date()
                        _t_rc = datetime.strptime(
                            _today_iso_gate, "%Y-%m-%d").date()
                        _d2e_rc = (_e_rc - _t_rc).days
                except (ValueError, TypeError):
                    _d2e_rc = None
                if (_loss_frac is not None and _loss_frac <= _rc_max_loss
                        and _d2e_rc is not None and 0 < _d2e_rc <= 14):
                    _btc_cost_rc = cur * 100.0 * qty
                    _pl_word = (f"{-_loss_frac * 100:+.1f}% of premium"
                                if _loss_frac else "flat")
                    items.append(
                        f"{n}. **CLOSE INTO RECOVERY** {contract} — "
                        f"recovered to ~breakeven ({_pl_word}) before the "
                        f"{_earn_pretty} print; buy-to-close {int(qty)}× "
                        f"limit ${cur:.2f} (≈ ${_btc_cost_rc:,.0f}), GTC"
                    )
                    items.append(
                        f"   - **Why:** position recovered to ~breakeven "
                        f"before the {_earn_pretty} print — exiting flat "
                        f"removes the binary; re-enter post-print on your "
                        f"own terms. (The roll path is earnings-blocked; "
                        f"a ROLL-don't-close verdict optimizes premium "
                        f"mechanics, not event risk — event risk wins here.)"
                    )
                    items.append(
                        f"   - **Earnings check:** "
                        f"{format_earnings_badge(_gate_check)}")
                    items.extend(_exit_cost_lines(rev, snapshot_data,
                                                  equity_reviews, date_str))
                    # Portfolio-intent note when the ticker is flagged for
                    # deconcentration in the standing directives.
                    _und_rc = rev.get("underlying", contract.split("_")[0])
                    try:
                        _decon = any(
                            _und_rc in (d.raw_text or "")
                            and any(k in (d.raw_text or "").lower()
                                    for k in ("deconcentrat", "reduce",
                                              "trim", "lighten"))
                            for d in _adv_directives)
                    except Exception:
                        _decon = False
                    if _decon:
                        items.append(
                            f"   - _Portfolio intent: {_und_rc} is flagged "
                            f"for deconcentration in your directives — "
                            f"closing (not rolling) also serves the "
                            f"reduction plan._"
                        )
                    items.append(
                        f"   - **Account:** "
                        f"{_route_account('CLOSE', _und_rc, rev.get('account') or rev.get('account_type'), config_local.get('accounts', []) or [])}"
                    )
                    seen_contracts.add(contract)
                    n += 1
                    continue
                _def_prefix = ("🚨⏸ **URGENT — ROLL DEFERRED (earnings block)**"
                               if rev.get("_urgent_strike_tested")
                               else "⏸ **ROLL DEFERRED (earnings block)**")
                items.append(
                    f"{n}. {_def_prefix} {contract} — "
                    f"{roll_label}: {credit_label} — analysis only, no order"
                )
                items.append(
                    f"   - **Earnings check:** {format_earnings_badge(_gate_check)}")
                items.append(
                    f"   - Reference legs (NOT an order): BTC {int(qty)}× "
                    f"${cur_strike:g}{opt_type[:1]} {cur_exp_pretty} "
                    f"(mid ~${cur:.2f}) → STO {int(qty)}× "
                    f"${new_strike:g}{opt_type[:1]} {new_exp_pretty} "
                    f"(bid ${new_bid:.2f} / mid ${new_mid:.2f})."
                )
                items.extend(_exit_cost_lines(rev, snapshot_data,
                                              equity_reviews, date_str))
                items.append("   - **Why (for reference):** " + _roll_why_text(
                    is_put, credit, underwater, unrealized_loss,
                    cur_strike, new_strike, underlying_spot, dte_added,
                    cur_exp_pretty, new_exp_pretty))
                items.append(
                    f"   - _STO leg spans the {_earn_pretty} print — "
                    f"re-evaluate after earnings or pick a post-print "
                    f"expiration._"
                )
                seen_contracts.add(contract)
                n += 1
                continue

            # ── Task #40 fix 5: debit-to-collateral sanity cap ────────────
            # IREN: a $652 roll debit on $3,700 of new collateral = 18% —
            # disproportionate. Above roll.max_debit_pct_of_collateral
            # (default 8%) the roll demotes to a Watch note; the exit-cost
            # verdict (already rendered there) proposes the alternative.
            if is_put and credit < 0:
                try:
                    _max_debit_frac = float(
                        _roll_cfg.get("max_debit_pct_of_collateral", 0.08))
                except (TypeError, ValueError):
                    _max_debit_frac = 0.08
                _new_collateral = (new_strike or 0) * 100.0 * qty
                if (_new_collateral > 0
                        and abs(credit) > _max_debit_frac * _new_collateral):
                    _debit_pct = abs(credit) / _new_collateral * 100.0
                    rev["_debit_cap_demotion"] = (
                        f"Roll demoted (debit cap): ${abs(credit):,.0f} "
                        f"debit is {_debit_pct:.0f}% of the "
                        f"${_new_collateral:,.0f} new collateral "
                        f"(cap {_max_debit_frac * 100:.0f}%) — "
                        f"disproportionate; close or take assignment "
                        f"instead (see the exit-cost verdict)."
                    )
                    seen_contracts.add(contract)
                    # ── Task #43 fix 2 (NOK $11P, 2026-07-31) ─────────────
                    # A demoted roll must never swallow the position's own
                    # exit path: control falls to the exit-cost verdict.
                    # CLOSE_* composes the close ticket (safety net — the
                    # upstream verdict check normally catches these first);
                    # NEUTRAL on a GENUINELY ITM put still surfaces the
                    # close-or-take-assignment choice the demotion note
                    # points at (the NOK shape: verdict slipped CLOSE_CLEAN
                    # → NEUTRAL on a $0.03 move and the item vanished).
                    # ROLL_DONT_CLOSE / HOLD_* keep the Watch-note-only
                    # demotion. Fail-open: no anatomy → Watch note only.
                    if _verdict_drives and _anatomy is not None:
                        if _anatomy.verdict in _CLOSE_VERDICTS:
                            items.extend(_verdict_close_lines(
                                n, contract, rev, _anatomy, snapshot_data,
                                equity_reviews, date_str, config_local))
                            n += 1
                            continue
                        if _anatomy.verdict == "NEUTRAL" and genuinely_itm:
                            items.extend(_debit_capped_close_lines(
                                n, contract, rev, _anatomy, _debit_pct,
                                _max_debit_frac * 100.0, snapshot_data,
                                equity_reviews, date_str, config_local))
                            n += 1
                            continue
                    continue

            # Task #40 fix 8: strike-tested urgency carries its 🚨 branding
            # into the composed two-leg ticket.
            # ── Willing-owner downgrade (rule #51 extension, NOK 2026-08-18:
            # "Why should I roll Nokia if it's in December? It's kind of
            # hard."). The strike-tested URGENT tier protects unwilling
            # owners; for a WILLING owner (deep assignment-basis cushion, no
            # near event, long DTE) a same-strike/down CREDIT roll is an
            # optional income optimization, not urgent. Risk-driven cells
            # (loss-stop / crash / tail-risk / earnings) never downgrade;
            # the full combo ticket stays on the card (rule #24). Config
            # roll.willing_owner_downgrade — code default OFF (legacy).
            _wo = None
            try:
                from analysis.willing_owner import (
                    OPTIONAL_PREFIX as _WO_PREFIX,
                    assess_willing_owner_roll as _wo_assess,
                    format_willing_owner_line as _wo_line,
                )
                _d2e_wo = rev.get("days_to_earnings")
                if _d2e_wo is None:
                    _d2e_wo = _days_to_earnings(
                        rev.get("underlying") or contract.split("_")[0])
                _wo = _wo_assess(
                    option_type=opt_type,
                    strike=cur_strike,
                    entry_premium=entry,
                    spot=underlying_spot,
                    dte=rev.get("days_to_expiry"),
                    days_to_earnings=_d2e_wo,
                    matrix_cell_id=rev.get("matrix_cell_id"),
                    credit_dollars=credit,
                    new_strike=new_strike,
                    config=config_local,
                )
            except Exception:
                _wo = None
            if _wo is not None:
                _urgent_prefix = _WO_PREFIX
            else:
                _urgent_prefix = ("🚨 **URGENT — EXECUTE ROLL**"
                                  if rev.get("_urgent_strike_tested")
                                  else "**EXECUTE ROLL**")
            items.append(
                f"{n}. {_urgent_prefix} {contract} — {roll_label}: {credit_label} "
                f"({int(qty)} spreads @ ${spread_bid:.2f}/share)"
            )
            if _wo is not None:
                # Honest willing-owner framing — measured numbers only
                # (rule #19): basis, cushion, credit, extension, 50% GTC.
                try:
                    _wo_exp_month = datetime.strptime(
                        str(cur_exp)[:10], "%Y-%m-%d").strftime("%b")
                except (ValueError, TypeError):
                    _wo_exp_month = None
                items.append(
                    "   - **Why (optional):** "
                    + _wo_line(_wo, credit, dte_added,
                               exp_month=_wo_exp_month))
                # The credit-window state line stays — the honest "this
                # offer expires" context on an otherwise-optional roll.
                _cw_line_wo = None
                try:
                    from analysis.credit_windows import format_credit_window_line
                    _cw_line_wo = format_credit_window_line(
                        (((snapshot_data or {}).get("_credit_windows") or {})
                         .get(contract)) or {})
                except Exception:
                    _cw_line_wo = None
                if _cw_line_wo:
                    items.append(f"   - {_cw_line_wo}")
            elif rev.get("_urgent_strike_tested"):
                # Condition-specific urgency rationale (task #40 fix 3):
                # the strike-test trigger, verbatim from the guardrail —
                # measured δ / capture / DTE — never the earnings template.
                items.append(
                    f"   - **Why (urgent):** "
                    f"{_truncate_at_word(rev.get('rationale') or '', 220)}")
                _cw_line_u = None
                try:
                    from analysis.credit_windows import format_credit_window_line
                    _cw_line_u = format_credit_window_line(
                        (((snapshot_data or {}).get("_credit_windows") or {})
                         .get(contract)) or {})
                except Exception:
                    _cw_line_u = None
                if _cw_line_u:
                    items.append(f"   - {_cw_line_u}")
            items.append(f"   - {format_yield_line(roll_yield)}")
            # Order ticket — explicit two-leg combo
            items.append(
                f"   - **Order:** Combo (calendar/diagonal) — Buy-to-Close {int(qty)}× "
                f"{rev.get('underlying', contract.split('_')[0])} ${cur_strike:g}{opt_type[:1]} "
                f"{cur_exp_pretty} (current mid ~${cur:.2f}); "
                f"Sell-to-Open {int(qty)}× ${new_strike:g}{opt_type[:1]} "
                f"{new_exp_pretty} (current bid ${new_bid:.2f} / mid ${new_mid:.2f} / ask ${new_ask:.2f})."
            )
            # Cluster warning — the chosen STO leg still lands on a
            # warning/critical put bucket (it was the only viable roll):
            # render the MEASURED bucket math, never hide (rule #24).
            if opt_type == "PUT" and _roll_cluster_warning_fn is not None:
                try:
                    _cl_line = _roll_cluster_warning_fn(
                        new_exp_raw, new_strike, qty, _put_buckets_al,
                        _nlv_al, warning_pct=_bucket_warning_pct)
                except Exception:
                    _cl_line = None
                if _cl_line:
                    items.append(f"   - {_cl_line}")
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
            # Exit-cost anatomy on the BTC leg (ITM/underwater short puts):
            # shows what closing outright would pay away vs what the roll
            # swaps, plus the assignment basis (the 2026-07-29 LITE lesson).
            items.extend(_exit_cost_lines(rev, snapshot_data,
                                          equity_reviews, date_str))
            # Side-aware cushion math (task #37 fix 4d): calls measure cap
            # headroom above spot; puts measure downside cushion below spot.
            cap_buf_change = _roll_cushion_change_pct(
                is_put, cur_strike or 0, new_strike, underlying_spot)
            if not is_calendar and new_strike > (cur_strike or 0):
                # Diagonal up (CALL only at this point — put roll-ups were
                # skipped above)
                why_parts = []
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
                if dte_added:
                    why_parts.append(
                        f"adds {dte_added} more days of theta runway "
                        f"({cur_exp_pretty} → {new_exp_pretty})")
                items.append(f"   - **Why:** {'; '.join(why_parts)}.")
            else:
                # Sign-aware Why (task #37 fix 4c): a DEBIT roll never claims
                # "books net credit".
                items.append("   - **Why:** " + _roll_why_text(
                    is_put, credit, underwater, unrealized_loss,
                    cur_strike or 0, new_strike, underlying_spot, dte_added,
                    cur_exp_pretty, new_exp_pretty))
            if pre_print_swap:
                items.append(
                    "   - _Expiration chosen to clear the earnings print "
                    "(the ranked best roll's STO leg would have spanned it)._"
                )
            items.append("   - **Gain:** " + _roll_gain_text(
                is_put, credit, underwater, cur_strike or 0, new_strike,
                underlying_spot, dte_added))

            # Earnings guard on the new short leg
            today_iso_r = date_str or datetime.now().strftime("%Y-%m-%d")
            earn_check_r = check_earnings_conflict(
                rev.get("underlying", contract.split("_")[0]),
                new_exp_raw, (snapshot_data or {}).get("earnings_calendar", {}) or {},
                today_iso_r,
            )
            # Earnings check line — ALWAYS prefixed "Earnings check:" so the
            # tax_cpa quality_gate persona's grep finds it (block/warn cases
            # used to render only the badge, which failed the gate).
            if earn_check_r.get("level") in ("warn", "block"):
                items.append(f"   - **Earnings check:** {format_earnings_badge(earn_check_r)}")
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
            # MEASURED delta only (chain-sourced) — the moneyness heuristic
            # below is a display approximation and must never feed the
            # trade-validator's EV / P(assignment) math (rule #19).
            measured_delta = new_delta if new_delta is not None else best.get("deltaChange")
            try:
                measured_delta = float(measured_delta) if measured_delta else None
            except (TypeError, ValueError):
                measured_delta = None
            if new_delta is None and measured_delta is not None:
                new_delta = measured_delta
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
            delta_str = _format_delta_line(new_delta, opt_type,
                                           strike=new_strike,
                                           spot=underlying_spot)
            if delta_str:
                items.append(f"   - {delta_str}")

            # Account routing (rolls stay in the position's account)
            routing = _route_account(
                "ROLL", rev.get("underlying", ""),
                rev.get("account") or rev.get("account_type"),
                config_local.get("accounts", []) or [],
            )
            items.append(f"   - **Account:** {routing}")

            # Trade validator — EV / break-even / verdict.
            # Task #37 fix 4a: the old path fed hardcoded delta=0.30 and
            # credit=0 on debit rolls, so all 14 cards rendered the same
            # "EV $+0 — P(assignment) 30%" — a constant masquerading as
            # data. Now the validator only runs on inputs it can actually
            # measure: a real credit AND a chain-measured delta. Debit
            # rolls / missing delta → honest "n/a", never a fake number.
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
                        new_delta=abs(measured_delta) if measured_delta else 0.10,
                        current_delta=0.30,
                    )
                elif credit <= 0:
                    val = None
                    items.append(
                        "   - **Trade-validator:** EV n/a (not computed for "
                        "debit rolls — see the exit-cost verdict above)"
                    )
                elif not measured_delta:
                    val = None
                    items.append(
                        "   - **Trade-validator:** EV n/a (chain carried no "
                        "delta for the STO leg — verify P(assignment) at the "
                        "broker)"
                    )
                else:
                    val = validate_calendar_roll(
                        spot=underlying_spot or 0,
                        strike=new_strike,
                        new_premium=new_mid,
                        credit_per_share=spread_bid,
                        contracts=int(qty),
                        new_dte=dte_added or 30,
                        delta=abs(measured_delta),
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
    # Per CLAUDE.md (Core holdings — no force-sell): for SHORT CALLs on a
    # ticker in core_positions, NEVER recommend CLOSE. The user has explicitly
    # said they roll core CCs year after year and don't want to realize the
    # short-call loss. Pivot CLOSE → DEFENSIVE ROLL UP-AND-OUT.
    # 2026-08-04 (PLTR): the observed briefing rendered "CLOSE PLTR_CALL_200
    # — Loss stop 2.46x" on a 24%-OTM covered call because PLTR is Tier A in
    # position_tiers but absent from core_positions. Core protections key off
    # the UNION (core_positions ∪ tier_a_core) — single source of conviction.
    core_tickers_actionable = _core_union_safe(
        (snapshot_data or {}).get("_config", {}) or {})

    actionable_decisions = {"CLOSE", "CLOSE_FOR_PROFIT", "ROLL_OUT", "ROLL_OUT_AND_DOWN",
                            "ROLL_OUT_AND_UP", "TAKE_ASSIGNMENT", "LET_EXPIRE"}
    for rev in options_reviews:
        contract = rev.get("contract", "")
        if contract in seen_contracts:
            continue
        rec = rev.get("recommendation")

        # CORE override: a CLOSE on a SHORT CALL of a core holding is wrong.
        # Skip the CLOSE rendering — the upstream DEFENSIVE ROLL section
        # (block #3) already would have surfaced a roll path if the matrix
        # had recommended one. If block #3 didn't surface, render a stub
        # here explaining why we won't CLOSE and pointing at the roll table.
        opt_type = (rev.get("type") or "").upper()
        underlying = rev.get("underlying") or contract.split("_")[0]
        is_core_short_call = (
            opt_type == "CALL"
            and float(rev.get("qty", 0) or 0) < 0
            and underlying in core_tickers_actionable
        )
        if is_core_short_call and rec in ("CLOSE", "CLOSE_FOR_PROFIT") and "GUARDRAIL_LOSS_STOP" in (rev.get("matrix_cell_id") or ""):
            qty_local = abs(float(rev.get("qty", 0) or 0))
            cur_local = float(rev.get("current_mid", 0) or 0)
            ent_local = float(rev.get("entry_price", 0) or 0)
            loss_dollars = (ent_local - cur_local) * 100.0 * qty_local if ent_local else 0
            cur_strike_local = float(rev.get("strike") or 0)

            # Pull real chain quotes via the canonical etrade-chain-fetcher
            # for two roll candidates:
            #   1. Same-strike +1yr calendar roll (max income, cap unchanged)
            #   2. +$15-strike diagonal up +1yr (smaller credit, more cap)
            same_strike_quote = None
            up_strike_quote = None
            up_strike_value = cur_strike_local + 15 if cur_strike_local else 0
            try:
                import importlib.util as _ilu
                fetcher_path = Path(__file__).resolve().parents[3] / "etrade-chain-fetcher" / "scripts" / "fetch.py"
                if fetcher_path.exists():
                    spec = _ilu.spec_from_file_location("etrade_chain_fetcher", fetcher_path)
                    mod = _ilu.module_from_spec(spec)
                    sys.modules["etrade_chain_fetcher"] = mod
                    spec.loader.exec_module(mod)
                    if mod.is_available():
                        cache = mod.ChainCache()
                        # ±60 day tolerance for the 1-year roll target — equity
                        # chains often have quarterly LEAPS rather than every
                        # week, so a 30-day window can miss. Expiration policy
                        # (2026-08-10): prefer the standard monthly / LEAP-cycle
                        # (3rd-Friday) expiration in the band.
                        _ep_cfg = ((snapshot_data or {}).get("_config", {}) or {}
                                   ).get("expiration_policy") or {}
                        target_exp = mod.choose_expiration(
                            symbol=underlying, target_dte=365, tolerance_days=60, cache=cache,
                            prefer_monthly=bool(_ep_cfg.get("prefer_monthly")),
                        )
                        if target_exp and cur_strike_local:
                            same_strike_quote = mod.quote_contract(
                                symbol=underlying, strike=cur_strike_local,
                                expiration=target_exp, opt_type="CALL", cache=cache,
                            )
                            up_strike_quote = mod.quote_contract(
                                symbol=underlying, strike=up_strike_value,
                                expiration=target_exp, opt_type="CALL", cache=cache,
                            )
            except Exception:
                pass

            items.append(
                f"{n}. **DEFENSIVE ROLL (core override)** {contract} — "
                f"loss-stop triggered at ${cur_local:.2f}, but {underlying} is a core "
                f"holding (policy: do NOT close; roll up-and-out year after year)"
            )
            items.append(
                f"   - **Why:** Closing would realize a ${abs(loss_dollars):,.0f} short-term "
                f"loss AND leave the {qty_local:.0f}00 shares uncapped. For core "
                f"holdings with large embedded LTCG gains, the right move is a "
                f"diagonal-up or same-strike calendar roll."
            )

            # Monthly/weekly kind labeler for the roll STO legs — computed
            # from the REAL selected chain date (rule #19); no-op when the
            # expiration policy is disabled (legacy byte-identical output).
            def _label_roll_exp(pretty: str, exp_iso_s: str) -> str:
                try:
                    from analysis.expiration_policy import label_exp, policy_enabled
                    return label_exp(
                        pretty, exp_iso_s,
                        enabled=policy_enabled(
                            (snapshot_data or {}).get("_config", {}) or {}),
                    )
                except Exception:
                    return pretty

            # Concrete order tickets with real bid/mid/ask
            if same_strike_quote:
                exp_iso = same_strike_quote["expiration"]
                exp_pretty = exp_iso
                try:
                    exp_pretty = datetime.strptime(exp_iso, "%Y-%m-%d").strftime("%a %b %d '%y")
                except Exception:
                    pass
                exp_pretty = _label_roll_exp(exp_pretty, exp_iso)
                sto_bid = same_strike_quote["bid"]
                sto_mid = same_strike_quote["mid"]
                sto_ask = same_strike_quote["ask"]
                net_per_share = sto_mid - cur_local
                net_total = net_per_share * 100 * qty_local
                items.append(
                    f"   - **Option A (same-strike calendar, max income):** "
                    f"BTC {int(qty_local)}× {underlying} ${cur_strike_local:g}C @ ${cur_local:.2f} mid; "
                    f"STO {int(qty_local)}× {underlying} ${cur_strike_local:g}C exp **{exp_pretty}** "
                    f"@ ${sto_mid:.2f} mid (bid ${sto_bid:.2f} / ask ${sto_ask:.2f}). "
                    f"Net **{'+' if net_total >= 0 else '−'}${abs(net_total):,.0f} {'credit' if net_total >= 0 else 'debit'}** "
                    f"({net_per_share:+.2f}/share). Cap stays ${cur_strike_local:g}."
                )
            if up_strike_quote and up_strike_value:
                exp_iso = up_strike_quote["expiration"]
                exp_pretty = exp_iso
                try:
                    exp_pretty = datetime.strptime(exp_iso, "%Y-%m-%d").strftime("%a %b %d '%y")
                except Exception:
                    pass
                exp_pretty = _label_roll_exp(exp_pretty, exp_iso)
                sto_bid = up_strike_quote["bid"]
                sto_mid = up_strike_quote["mid"]
                sto_ask = up_strike_quote["ask"]
                net_per_share = sto_mid - cur_local
                net_total = net_per_share * 100 * qty_local
                items.append(
                    f"   - **Option B (diagonal up-and-out, +${int(up_strike_value-cur_strike_local)} cap):** "
                    f"BTC {int(qty_local)}× {underlying} ${cur_strike_local:g}C @ ${cur_local:.2f} mid; "
                    f"STO {int(qty_local)}× {underlying} ${up_strike_value:g}C exp **{exp_pretty}** "
                    f"@ ${sto_mid:.2f} mid (bid ${sto_bid:.2f} / ask ${sto_ask:.2f}). "
                    f"Net **{'+' if net_total >= 0 else '−'}${abs(net_total):,.0f} {'credit' if net_total >= 0 else 'debit'}** "
                    f"({net_per_share:+.2f}/share). Cap rises to ${up_strike_value:g}."
                )
            if same_strike_quote or up_strike_quote:
                items.append(f"   - **Source:** Live E*TRADE chain")
            else:
                items.append(
                    f"   - ⚠ E*TRADE chain unavailable for roll candidates — "
                    f"verify quotes at broker before placing."
                )
            items.append(
                f"   - **Tax framing:** rolling defers the realized loss; assignment "
                f"at a higher strike means larger LTCG but on more cash."
            )
            seen_contracts.add(contract)
            n += 1
            continue

        # A ROLL is a TWO-leg trade. Block #3 renders rolls that have priced
        # candidates; if we reach here the matrix recommended a roll but no live
        # target was available — render a roll DIRECTIVE, never a bare
        # buy-to-close (which reads as a close and gives up the shares/cap).
        _ROLL_DECISIONS = {"ROLL_OUT", "ROLL_OUT_AND_UP", "ROLL_UP_AND_OUT", "ROLL_OUT_AND_DOWN"}
        if rec in _ROLL_DECISIONS:
            # CLAUDE.md #14: defer to the position's own advisor. We only reach
            # here when block #3 found no genuine credit-positive roll-up. For a
            # covered call that's still OTM (no real assignment risk), that
            # means the only "rolls" are same-strike re-caps or HOLD — so don't
            # surface a roll directive that contradicts the advisor's HOLD (the
            # SPY bug, where recommendedCandidateId was "A"). Real assignment
            # risk (spot at/through the strike) still surfaces the directive.
            _otype = (rev.get("type") or "").upper()
            _strike_g = float(rev.get("strike") or 0)
            _und_g = rev.get("underlying") or contract.split("_")[0]
            _q_g = (snapshot_data or {}).get("quotes", {}) if snapshot_data else {}
            _spot_g = float((_q_g.get(_und_g) or {}).get("last") or 0)
            _call_itm = (_otype == "CALL" and _spot_g and _strike_g and _spot_g >= _strike_g)
            if _otype == "CALL" and not _call_itm:
                seen_contracts.add(contract)
                continue

            # Rule #3 hard gate (TSM 2026-07-30) — same gate as block #3:
            # an actionable roll directive on a short PUT needs genuine
            # assignment risk (moneyness < 1.03 OR measured |δ| ≥ 0.40).
            # Demote to a Watch note otherwise.
            if _otype == "PUT":
                _mny4 = (_spot_g / _strike_g) if (_spot_g and _strike_g) else 1.0
                if not _short_put_roll_gate_ok(_mny4, rev.get("delta")):
                    rev["_roll_gate_demotion"] = _roll_gate_demotion_note(
                        _mny4, rev.get("delta"))
                    seen_contracts.add(contract)
                    continue
                # Task #40 fix 6 (continued): the AVGO nothing-to-defend
                # demotion on the directive path too.
                _ntd4 = _nothing_to_defend_note(rev, _mny4)
                if _ntd4:
                    rev["_roll_gate_demotion"] = _ntd4
                    seen_contracts.add(contract)
                    continue

            # Task #40 fix 2: churn guard on the roll-directive path too —
            # a contract the user just opened gets a settling period.
            try:
                from analysis.churn_guard import check_churn_guard
                _churn_note4 = check_churn_guard(
                    contract, rev.get("matrix_cell_id"),
                    (snapshot_data or {}).get("_position_ages"),
                    (snapshot_data or {}).get("_config", {}) or {})
            except Exception:
                _churn_note4 = None

            # Task #37 fix 1 (block #4 path): the exit-cost verdict drives
            # the action here too — a CLOSE_* verdict converts the roll
            # directive into a plain CLOSE with the anatomy block, and a
            # HOLD_FOR_DECAY verdict suppresses the ticket entirely (Watch
            # note only).
            _xc_cfg4 = ((snapshot_data or {}).get("_config", {}) or {}
                        ).get("exit_cost") or {}
            if _xc_cfg4.get("verdict_drives_action", True):
                _an4, _ = _exit_cost_anatomy(rev, snapshot_data,
                                             equity_reviews, date_str)
                if _an4 is not None and _an4.verdict in _CLOSE_VERDICTS:
                    items.extend(_verdict_close_lines(
                        n, contract, rev, _an4, snapshot_data,
                        equity_reviews, date_str,
                        (snapshot_data or {}).get("_config", {}) or {}))
                    seen_contracts.add(contract)
                    n += 1
                    continue
                if _an4 is not None and _an4.verdict == "HOLD_FOR_DECAY":
                    seen_contracts.add(contract)
                    continue
                if _an4 is not None and _an4.verdict == "HOLD_FOR_BASIS":
                    # Task #40 fix 1 (block #4 path): same verdict-driven
                    # suppression as block #3 — no roll ticket on a
                    # hold-for-basis position.
                    items.extend(_verdict_hold_basis_lines(
                        n, contract, rev, _an4, snapshot_data,
                        equity_reviews, date_str,
                        churn_age_note=_churn_note4))
                    seen_contracts.add(contract)
                    n += 1
                    continue

            # Task #40 fix 2 (block #4 path): churn-guard demotion.
            if _churn_note4:
                rev["_churn_guard_demotion"] = _churn_note4
                seen_contracts.add(contract)
                continue

            qty = abs(float(rev.get("qty", 0) or 0))
            cur_mid = float(rev.get("current_mid", 0) or 0)
            up = ("UP" in rec)
            down = ("DOWN" in rec)
            direction = ("UP and out — raise the strike to preserve upside" if up
                         else "DOWN and out — lower the strike to cut assignment risk" if down
                         else "OUT in time — keep the strike")
            new_side = "higher" if up else "lower" if down else "same"
            _rec_label = (f"🚨 **URGENT — {rec}**"
                          if rev.get("_urgent_strike_tested")
                          else f"**{rec}**")
            # Willing-owner downgrade (rule #51 extension) needs the REAL
            # net credit, which is only measured after the live STO quote
            # below — remember the headline index so it can be patched.
            _wo4_head_idx = len(items)
            items.append(f"{n}. {_rec_label} {contract} — roll {direction}")
            if rev.get("_urgent_strike_tested"):
                items.append(
                    f"   - **Why (urgent):** "
                    f"{_truncate_at_word(rev.get('rationale') or '', 220)}")
            items.append(
                f"   - **Why:** Decision matrix triggered `{rev.get('matrix_cell_id', '?')}` "
                f"(regime + DTE + moneyness)."
            )
            # Fetch a REAL STO leg quote via the canonical chain fetcher — no
            # more "pick from the ROLL ANALYSIS table" hand-wave. Delta-first
            # (volatility-adaptive ~0.25), %OTM fallback. Tenor ≤ 120d
            # (matches the action-list roll cap). Fail-soft: if the chain is
            # unreachable, we surface "live chain unavailable, verify manually"
            # rather than make up numbers.
            sto_strike = sto_exp_pretty = None
            sto_bid = sto_mid = sto_ask = sto_dte = None
            sto_delta = None
            try:
                import importlib.util as _ilu_r
                _fp = Path(__file__).resolve().parents[3] / "etrade-chain-fetcher" / "scripts" / "fetch.py"
                if _fp.exists():
                    _spec = _ilu_r.spec_from_file_location("etrade_chain_fetcher", _fp)
                    _mod = _ilu_r.module_from_spec(_spec)
                    sys.modules["etrade_chain_fetcher"] = _mod
                    _spec.loader.exec_module(_mod)
                    if _mod.is_available():
                        _c = _mod.ChainCache()
                        # Target ~90d DTE within the 120d action-list cap.
                        # Expiration policy (2026-08-10): prefer the standard
                        # monthly (3rd-Friday) in the band; the tenor cap
                        # (roll.max_action_tenor_days) is a hard bound the
                        # monthly preference NEVER violates.
                        _cfg_b4 = (snapshot_data or {}).get("_config", {}) or {}
                        _ep_b4 = _cfg_b4.get("expiration_policy") or {}
                        _pm_b4 = bool(_ep_b4.get("prefer_monthly"))
                        _tenor_cap_b4 = int(
                            (_cfg_b4.get("roll") or {}).get(
                                "max_action_tenor_days", 120) or 120)
                        _target_exp = _mod.choose_expiration(
                            symbol=_und_g, target_dte=90, tolerance_days=45, cache=_c,
                            prefer_monthly=_pm_b4,
                            max_dte=_tenor_cap_b4 if _pm_b4 else None,
                        )
                        if _target_exp and _spot_g:
                            _qd = None
                            if up:
                                # Roll-UP: target ~0.25 delta on the new CALL
                                # (volatility-adaptive; further OTM on high-IV
                                # names, closer on low-IV — exactly the rule we
                                # apply to new CC writes).
                                _qd = _mod.find_strike_near_delta(
                                    symbol=_und_g, expiration=_target_exp,
                                    target_delta=0.25,
                                    opt_type=_otype, spot=_spot_g,
                                    tolerance=0.12, cache=_c,
                                )
                                if not _qd:
                                    # %OTM fallback when chain has no usable deltas
                                    _qd = _mod.find_strike_at_otm_pct(
                                        symbol=_und_g, expiration=_target_exp,
                                        otm_pct=6.0, opt_type=_otype, spot=_spot_g,
                                        cache=_c,
                                    )
                            elif down:
                                # Roll-DOWN (defensive on a PUT): same ~0.25
                                # delta target, just below spot.
                                _qd = _mod.find_strike_near_delta(
                                    symbol=_und_g, expiration=_target_exp,
                                    target_delta=0.25,
                                    opt_type=_otype, spot=_spot_g,
                                    tolerance=0.12, cache=_c,
                                )
                            else:
                                # Out in time (same strike — only valid here
                                # if there's a genuine reason; block #3's
                                # discipline normally would have handled it).
                                _qd = _mod.quote_contract(
                                    symbol=_und_g, strike=_strike_g,
                                    expiration=_target_exp, opt_type=_otype, cache=_c,
                                )
                            if _qd:
                                sto_strike = _qd.get("strike")
                                sto_bid = _qd.get("bid")
                                sto_mid = _qd.get("mid")
                                sto_ask = _qd.get("ask")
                                _d = _qd.get("delta")
                                sto_delta = abs(float(_d)) if _d is not None else None
                                _exp_iso = _qd.get("expiration") or ""
                                from datetime import datetime as _dtR
                                try:
                                    sto_exp_pretty = _dtR.strptime(str(_exp_iso)[:10], "%Y-%m-%d").strftime("%a %b %d '%y")
                                except Exception:
                                    sto_exp_pretty = str(_exp_iso)
                                # Monthly/weekly label from the REAL selected
                                # date (rule #19); no-op when policy disabled.
                                try:
                                    from analysis.expiration_policy import label_exp as _lbl_ep
                                    if _pm_b4:
                                        sto_exp_pretty = _lbl_ep(
                                            sto_exp_pretty, _exp_iso, enabled=True)
                                except Exception:
                                    pass
                                try:
                                    _ed = _dtR.strptime(str(_exp_iso)[:10], "%Y-%m-%d").date()
                                    from datetime import date as _dateR
                                    sto_dte = (_ed - _dateR.today()).days
                                except Exception:
                                    sto_dte = None
            except Exception:
                pass

            if sto_strike and sto_bid is not None and sto_ask is not None:
                _delta_s = f", δ {sto_delta:.2f}" if sto_delta is not None else ", δ n/a"
                _net = ((sto_mid or 0) - cur_mid) * 100 * int(qty)
                _net_s = f"+${_net:,.0f} credit" if _net >= 0 else f"−${abs(_net):,.0f} debit"
                items.append(
                    f"   - **Order (two legs):** Buy-to-Close {int(qty)}× {contract} (current mid "
                    f"${cur_mid:.2f}) **and** Sell-to-Open {int(qty)}× **${sto_strike:g}{_otype[:1]} "
                    f"{sto_exp_pretty}** — bid ${sto_bid:.2f} / mid ${sto_mid:.2f} / ask ${sto_ask:.2f}"
                    f" ({sto_dte}d{_delta_s}). Net: {_net_s}. Do NOT place the BTC alone."
                )
                items.append(f"   - **Source:** Live E*TRADE chain")
                # Cluster warning (block #4 parity with block #3): a PUT
                # STO leg landing on a warning/critical bucket renders the
                # measured cluster math (2026-08-17 QCOM/Jun-17-'27 bug).
                if (_otype or "").upper() == "PUT" \
                        and _roll_cluster_warning_fn is not None:
                    try:
                        _cl_line_b4 = _roll_cluster_warning_fn(
                            _exp_iso, sto_strike, qty, _put_buckets_al,
                            _nlv_al, warning_pct=_bucket_warning_pct)
                    except Exception:
                        _cl_line_b4 = None
                    if _cl_line_b4:
                        items.append(f"   - {_cl_line_b4}")
                # ── Willing-owner downgrade (rule #51 extension, NOK
                # 2026-08-18) — block #4 parity with block #3: with the
                # REAL net measured from the live chain, a same-strike/
                # down CREDIT roll on a willing-owner put (deep basis
                # cushion, long DTE, no near earnings, non-risk cell)
                # downgrades to 🔧 OPTIONAL. The full two-leg ticket stays
                # (rule #24); roll-UPs and debit nets never downgrade.
                _wo4 = None
                if not up:
                    try:
                        from analysis.willing_owner import (
                            OPTIONAL_PREFIX as _WO_PREFIX4,
                            assess_willing_owner_roll as _wo_assess4,
                            format_willing_owner_line as _wo_line4,
                        )
                        _d2e_wo4 = rev.get("days_to_earnings")
                        if _d2e_wo4 is None:
                            _d2e_wo4 = _days_to_earnings(_und_g)
                        _wo4 = _wo_assess4(
                            option_type=_otype,
                            strike=_strike_g,
                            entry_premium=rev.get("entry_price"),
                            spot=_spot_g,
                            dte=rev.get("days_to_expiry"),
                            days_to_earnings=_d2e_wo4,
                            matrix_cell_id=rev.get("matrix_cell_id"),
                            credit_dollars=_net,
                            new_strike=sto_strike,
                            config=(snapshot_data or {}).get(
                                "_config", {}) or {},
                        )
                    except Exception:
                        _wo4 = None
                if _wo4 is not None:
                    _ext4 = None
                    try:
                        _cur_dte4 = int(rev.get("days_to_expiry") or 0)
                        if sto_dte is not None and _cur_dte4 > 0 \
                                and int(sto_dte) > _cur_dte4:
                            _ext4 = int(sto_dte) - _cur_dte4
                    except (TypeError, ValueError):
                        _ext4 = None
                    try:
                        _wo4_month = datetime.strptime(
                            str(rev.get("expiration") or "")[:10],
                            "%Y-%m-%d").strftime("%b")
                    except (ValueError, TypeError):
                        _wo4_month = None
                    items[_wo4_head_idx] = (
                        f"{n}. {_WO_PREFIX4} {contract} — roll {direction}")
                    # A downgraded card must not lead with urgency — the
                    # strike-test rationale stays as context (one voice).
                    for _j in range(_wo4_head_idx + 1, len(items)):
                        if items[_j].startswith("   - **Why (urgent):**"):
                            items[_j] = items[_j].replace(
                                "**Why (urgent):**",
                                "**Context (strike test):**", 1)
                            break
                    items.insert(
                        _wo4_head_idx + 1,
                        "   - **Why (optional):** "
                        + _wo_line4(_wo4, _net, _ext4,
                                    exp_month=_wo4_month))
            else:
                items.append(
                    f"   - **Order (two legs):** Buy-to-Close {int(qty)}× {contract} (current mid "
                    f"${cur_mid:.2f}) **and** Sell-to-Open {int(qty)}× a {new_side}-strike "
                    f"call further out — **live chain unavailable, verify the STO leg at the broker "
                    f"before placing**. Do NOT place the BTC on its own."
                )

            # Exit-cost anatomy on the BTC leg (ITM/underwater short puts).
            items.extend(_exit_cost_lines(rev, snapshot_data,
                                          equity_reviews, date_str))

            if up:
                items.append(
                    "   - Prefer a strike above spot for headroom and a tenor ≤120 days; "
                    "a same-strike roll just re-caps you at today's level."
                )
            seen_contracts.add(contract)
            n += 1
            continue

        if rec in actionable_decisions:
            rationale = _truncate_at_word(rev.get("rationale") or "", 140)
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

            # Bug #25: standing directive suppresses matrix CLOSE recs too
            # (same contract, same directive, same release conditions).
            if rec in ("CLOSE", "CLOSE_FOR_PROFIT") and _close_held_by_directive(
                    contract, rev, profit_pct, rev.get("days_to_expiry")):
                seen_contracts.add(contract)
                continue

            # ── Momentum-hold overlay (George 2026-08-17) — the block-#4
            # twin of the CLOSE WINNERS ride gate: a yield-motivated
            # CLOSE_FOR_PROFIT on a SHORT PUT defers to 🏇 RIDE while the
            # measured trend is up and the stall trigger hasn't fired.
            # Risk-driven cells / over-cap / earnings window always close
            # (momentum_hold.risk_exempt — same exemption lists). A stalled
            # rider's returning close carries "(momentum stalled — take
            # it)". Fail-open everywhere. Source: analysis/momentum_hold.py.
            _mh4_stall = ""
            if rec == "CLOSE_FOR_PROFIT":
                try:
                    from analysis import momentum_hold as _mh4
                    _mh4_cfg = (snapshot_data or {}).get("_config", {}) or {}
                    if _mh4.enabled(_mh4_cfg) \
                            and (rev.get("type") or "").upper() == "PUT":
                        _mh4_risk = _mh4.risk_exempt(
                            rev, snapshot_data, _mh4_cfg,
                            capture_pct=profit_pct,
                            days_to_earnings=_days_to_earnings(
                                rev.get("underlying")
                                or contract.split("_")[0]),
                            dte=(int(rev.get("days_to_expiry"))
                                 if rev.get("days_to_expiry") is not None
                                 else None))
                        if _mh4_risk is None:
                            _mh4_ride, _mh4_why = _mh4.momentum_ride(
                                rev, snapshot_data, _mh4_cfg)
                            if _mh4_ride:
                                rev["_momentum_ride"] = _mh4_why
                                items.extend(_momentum_ride_lines(
                                    n, contract, rev, _mh4_why, profit_pct))
                                seen_contracts.add(contract)
                                n += 1
                                continue
                            if _mh4_why.startswith("stalled"):
                                _mh4_stall = " (momentum stalled — take it)"
                except Exception:
                    _mh4_stall = ""  # advisory — never break the list

            # ── Redeployability-aware take-profit (George 2026-08-10) — the
            # block-#4 twin of the CLOSE WINNERS gate: a yield-motivated
            # CLOSE_FOR_PROFIT (matrix 50-65% floors, GUARDRAIL_TIME_ADJUSTED
            # fast-winner layer) on a SHORT PUT demotes to the visible
            # hold-for-more note when no redeployment path exists.
            # Risk-driven cells (loss stop, hard ceiling, gamma escape,
            # earnings imminent, crash, tail risk) and plain CLOSE recs are
            # exempt and fire exactly as today. Fail-open everywhere.
            _rd4_reason = None
            if rec == "CLOSE_FOR_PROFIT":
                try:
                    from analysis import redeploy_path as _rdp4
                    _cfg4 = (snapshot_data or {}).get("_config", {}) or {}
                    if _rdp4.enabled(_cfg4) \
                            and (rev.get("type") or "").upper() == "PUT":
                        _cell4 = (rev.get("matrix_cell_id") or "").upper()
                        _risk4 = any(t in _cell4 for t in (
                            "LOSS_STOP", "HARD_CEILING", "GAMMA_ESCAPE",
                            "EARNINGS_IMMINENT", "CRASH_STOP", "TAIL_RISK"))
                        _tgt4 = _rdp4.hold_target_pct(_cfg4)
                        # Over-cap concentration exemption (rule #43,
                        # 2026-08-17 SNDK/SOXX) — risk-driven, never held.
                        _overcap4 = _rdp4.over_cap_risk_exemption(
                            rev, snapshot_data, _cfg4)
                        if _overcap4:
                            _rd4_reason = _overcap4
                        elif not _risk4 and profit_pct < _tgt4 * 100.0 \
                                and profit_pct < (_rdp4.hard_ceiling_pct(
                                    _cfg4) * 100.0):
                            _ok4, _why4 = _rdp4.redeployment_path(
                                analytics,
                                (snapshot_data or {}).get(
                                    "_redeploy_best_setups"),
                                _rdp4.close_impact_from_review(rev),
                                config=_cfg4)
                            if not _ok4:
                                rev["_redeploy_hold_demotion"] = \
                                    _rdp4.hold_note(profit_pct, _why4, _tgt4)
                                seen_contracts.add(contract)
                                continue
                            if _why4:
                                _rd4_reason = _why4
                except Exception:
                    _rd4_reason = None  # advisory — never break the list

            ticket_suffix = ""
            if current_mid and qty:
                ticket_suffix = (
                    f" — buy-to-close limit ${current_mid:.2f}"
                    + (f" (+{profit_pct:.0f}% captured, ${profit_dollars:+,.0f})" if entry_price else "")
                )

            items.append(f"{n}. **{rec}** {contract} — {rationale}"
                         f"{ticket_suffix}{_mh4_stall}")
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
            # Exit-cost anatomy on the CLOSE ticket (ITM/underwater short
            # puts): decompose intrinsic vs panic-IV extrinsic vs spread
            # before the user pays the ask (the 2026-07-29 LITE lesson).
            # Never overrides the guardrail — CLOSE stays the action; the
            # verdict guides timing/pricing or points at the defensive roll.
            items.extend(_exit_cost_lines(rev, snapshot_data,
                                          equity_reviews, date_str))
            if profit_dollars:
                gain_text = f"Locks ${profit_dollars:+,.0f} of theta gain; frees position for new opportunities."
            else:
                gain_text = "Capital impact depends on chain spread at fill."
            items.append(
                f"   - **Gain:** Following the matrix improves expectancy versus discretionary holds. {gain_text}"
            )
            if _rd4_reason:
                _rd4_lbl = ("Risk-driven close"
                            if _rd4_reason.startswith("risk:")
                            else "Redeploy path")
                items.append(f"   - **{_rd4_lbl}:** {_rd4_reason}")
            seen_contracts.add(contract)
            n += 1

    # ---- 4c. FORCED DECISION — tested short puts inside the gamma window ----
    # Task #38 Part 3: any SHORT PUT that is GENUINELY tested (rule #3 gate:
    # spot < 1.03 × strike, or measured |δ| ≥ 0.40) with DTE ≤ forced_decision_dte
    # (default 21) gets a mandatory decision item — roll, close, or file an
    # accept-assignment directive. The strike-tested guardrail deliberately
    # stops at 21 DTE; this is the hand-off. A directive-held contract renders
    # the transparency note but NOT the nag (following a documented directive
    # is not indecision). Fail-open: no measured spot → no item (rule #19).
    _st_cfg = (config_local.get("strike_tested") or {})
    _forced_dte = int(_st_cfg.get("forced_decision_dte", 21))
    for rev in options_reviews:
        contract = rev.get("contract", "")
        if not contract or contract in seen_contracts:
            continue  # an actionable item already demands the decision
        if (rev.get("type") or "").upper() != "PUT":
            continue
        if float(rev.get("qty", 0) or 0) >= 0:
            continue  # short puts only
        _dte_f = rev.get("days_to_expiry")
        if _dte_f is None or int(_dte_f) > _forced_dte or int(_dte_f) < 0:
            continue
        _strike_f = float(rev.get("strike") or 0)
        _und_f = rev.get("underlying") or contract.split("_")[0]
        _spot_f = float((((snapshot_data or {}).get("quotes") or {})
                         .get(_und_f) or {}).get("last") or 0)
        if not (_strike_f and _spot_f):
            continue  # no measured spot → never guess "tested"
        # Rule #3 gate (TSM 2026-07-30): "tested" means genuine assignment
        # risk — moneyness < 1.03 OR measured |δ| ≥ 0.40 — not merely inside
        # the walker's 8% NEAR_ATM band. A 6%-above δ-0.10 put is theta's
        # job, not a forced decision.
        if not _short_put_roll_gate_ok(_spot_f / _strike_f, rev.get("delta")):
            continue
        _entry_f = float(rev.get("entry_price") or 0)
        _mid_f = float(rev.get("current_mid") or 0)
        _capture_f = ((_entry_f - _mid_f) / _entry_f * 100.0) if _entry_f else 0.0

        # Standing directive → note, not nag.
        _held_f = None
        if _adv_directives:
            try:
                from analysis.advisor_directives import directive_holds_contract
                _held_f = directive_holds_contract(
                    contract, _adv_directives, _capture_f / 100.0,
                    int(_dte_f), _spot_f or None)
            except Exception:
                _held_f = None
        if _held_f is not None:
            _tested_directive_notes.append(
                f"_⏸ {contract} tested at ≤{_forced_dte} DTE "
                f"(spot ${_spot_f:,.2f} vs strike ${_strike_f:,.2f}, "
                f"{int(_dte_f)}d left, {_capture_f:.0f}% captured) — held by "
                f"standing directive; decision on file_"
            )
            seen_contracts.add(contract)
            continue

        _itm_f = _spot_f < _strike_f
        _state_word = "ITM" if _itm_f else "at the strike"
        items.append(
            f"{n}. ⛔ **TESTED ≤{_forced_dte} DTE** {contract} — {_state_word} "
            f"with {int(_dte_f)}d left: roll, close, or file an "
            f"accept-assignment directive before gamma week"
        )
        items.append(
            f"   - **Why:** spot ${_spot_f:,.2f} vs strike ${_strike_f:,.2f} "
            f"({(_spot_f / _strike_f - 1) * 100:+.1f}%) with {_capture_f:.0f}% "
            f"captured and {int(_dte_f)}d left — inside 21 DTE gamma compounds "
            f"daily and roll credits shrink toward debits."
        )
        # Credit-window state (computed by aggregate before this list) — the
        # roll-economics read that makes the decision concrete.
        _cw_f = (((snapshot_data or {}).get("_credit_windows") or {})
                 .get(contract))
        if _cw_f:
            try:
                from analysis.credit_windows import format_credit_window_line
                _cw_line_f = format_credit_window_line(_cw_f)
                if _cw_line_f:
                    items.append(f"   - {_cw_line_f}")
            except Exception:
                pass
        items.append(
            "   - **⛔ DECISION REQUIRED:** Execute today, or file a directive "
            "(DEFER / accept-assignment with reason) — this item will not "
            "silently repeat."
        )
        seen_contracts.add(contract)
        n += 1

    # ---- 5. CONCENTRATION TRIM (>10% NLV) ----
    nlv = (snapshot_data or {}).get("balance", {}).get("accountValue", 0) or 0
    config = (snapshot_data or {}).get("_config", {}) if snapshot_data else {}
    # 2026-08-04 (PLTR): "TRIM PLTR — 10.6% NLV (over 10% cap)" fired while
    # Risk Alerts on the SAME run said "within Tier A bounds (cap 22%)".
    # Core = core_positions ∪ Tier A; explicitly-tiered names (A/B) use
    # concentration_cap_for_tier, so this generator can never contradict the
    # tier-aware drift alert.
    core_tickers = _core_union_safe(config)
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
            # Explicit Tier B income names use their tier cap (12% default)
            # when looser than the standard cap — the tier assignment is a
            # deliberate conviction call (rule #29); the drift alert already
            # reads the tier cap, this generator must agree with it.
            _tb_cap = _tier_b_cap_pct(ticker, config)
            if _tb_cap is not None:
                effective_cap = max(effective_cap, _tb_cap / 100.0)
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
                # Fix 4 (2026-08-04): after hedge_nag_days consecutive
                # ignored sessions the hedge vacates the numbered slots
                # (directive templates + Money Plan standing question
                # instead; stalled panel entry remains via a synthetic
                # aging action). See _hedge_nag_vacate.
                if _hedge_nag_vacate(items, aging_info, config_local,
                                     instr=instr, strike=strike,
                                     exp_str=exp_str, contracts=contracts,
                                     cost_f=cost_f):
                    recs = []  # numbered rendering below is skipped
            if recs:
                cost_pct = (cost_f / nlv * 100) if nlv else 0
                # Approx protected delta-shares: contracts × |delta| × 100; with 0.20 delta SPY puts
                protected_notional = (contracts or 0) * 0.20 * 100 * float(strike or 0)
                # Task #37 fix 4e: the old label printed the STRESS-coverage
                # ratio (0.10×) as "coverage 10%" next to "target 10%" —
                # conflating two different metrics and reading as already
                # resolved while the Hedge Book showed 0% hedged. Use the
                # hedge book's OWN current/target coverage (delta
                # neutralization), re-read from CURRENT data every day.
                # (When current >= target the hedge book emits no
                # recommendations at all, so this item auto-resolves.)
                hb_cur = getattr(hb, "current_coverage_pct", None)
                if hb_cur is None and isinstance(hb, dict):
                    hb_cur = hb.get("current_coverage_pct")
                hb_tgt = getattr(hb, "target_coverage_pct", None)
                if hb_tgt is None and isinstance(hb, dict):
                    hb_tgt = hb.get("target_coverage_pct")
                if hb_cur is not None and hb_tgt is not None:
                    cov_label = (f"hedge coverage {float(hb_cur):.0%} → "
                                 f"target {float(hb_tgt):.0%}; "
                                 f"stress coverage {cov:.2f}×")
                else:
                    cov_label = f"stress coverage {cov:.2f}× — hedge to target"
                items.append(
                    f"{n}. **HEDGE** Buy {contracts}× {instr.split('_')[0].upper()} put "
                    f"${strike}P {exp_str} (~${cost_f:,.0f}; {cov_label})"
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

        # A NEW-open put whose contract spans the print is a hard BLOCK at any
        # distance — never just a warning (domain rule: never sell puts
        # through earnings; same treatment as the CSP — PAID-TO-WAIT surface).
        _nc_block = (earn_check.get("level") == "block"
                     or earn_check.get("spans_expiration"))
        if ws_blocked:
            line += "  🚫 WASH-SALE BLOCKED"
        if _nc_block:
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

        if _nc_block:
            items.append(
                f"   - **Earnings check:** "
                f"{format_new_open_block(ticker, earn_check, exp_iso)}"
            )
            n += 1
            continue
        # Earnings check line — ALWAYS prefixed "Earnings check:" so the
        # tax_cpa quality_gate persona's grep finds it (warn case used to
        # render only the badge, which failed the gate).
        if earn_check.get("level") == "warn":
            items.append(f"   - **Earnings check:** {format_earnings_badge(earn_check)}")
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
        delta_str = _format_delta_line(
            idea.get("delta"), "PUT", strike=strike,
            spot=((snapshot_data or {}).get("quotes", {}).get(ticker) or {}).get("last"))
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
    # Extended-band (RSI 60-70) PULLBACK CSPs demoted to a "⏸ CSPs — wait for
    # a pullback" subsection (rule #43): full ticket shown (rule #24), never a
    # numbered actionable rec.
    _wait_csp_blocks: list = []
    if cash_avail > 5000 and core_tickers_set:
        ledger_path = config_local.get("wash_sale_ledger_path")
        ec_today = (snapshot_data or {}).get("earnings_calendar", {}) or {}
        as_of_iso = date_str or datetime.now().strftime("%Y-%m-%d")
        csp_technicals = (snapshot_data or {}).get("technicals", {}) or {}
        csp_rsi_th = rsi_discipline.load_thresholds(config_local)
        csp_rsi_gate_on = csp_rsi_th.get("enabled", True)

        # Parkev rec map for the LT-verdict gate's fresh-tier-≥4 override
        # (hard rule #39). Recs ride along in snapshot_data.
        _csp_rec_by_ticker: dict = {}
        for _r in ((snapshot_data or {}).get("recommendations_list") or []):
            if isinstance(_r, dict) and _r.get("ticker"):
                _csp_rec_by_ticker[str(_r["ticker"]).upper()] = _r

        # Universal capacity-gate DEFERRED tag (hard rules #24 / #41): when
        # stress coverage is below the floor, every PULLBACK CSP that still
        # renders carries the tag — shown for planning, never a green light.
        _csp_capacity_tag = capacity_gate.capacity_deferred_tag(analytics, config_local)

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

            # RSI discipline gate. A pullback CSP is a put-sale; an overbought
            # RSI (>70) blocks the new open (thin premium right before a
            # reversal can whip the stock through the strike). Surface in the
            # transparency footer rather than the action list.
            #
            # Rule #46 (PLTR 2026-08-04): the gate reads the RESOLVED RSI, not
            # the raw snapshot value. Observed card: "PULLBACK CSP PLTR — sell
            # $145P ... RSI 48 🟢 pullback ... ✅ GOOD TRADE" while PLTR was
            # +29% intraday (RSI 48 was computed through YESTERDAY's close
            # $125.65; live RSI ~70-75 = hard block). The resolver falls back
            # to the E*TRADE position price when the yfinance quote is
            # missing, recomputes a live Wilder RSI when the close series
            # allows, and reports unverifiable vintages so no favourable
            # badge can render on them.
            _csp_res = None
            try:
                from analysis import vintage_guard as _vgp
                _csp_res = _vgp.resolve_new_open_rsi(
                    ticker, csp_technicals,
                    quotes=(snapshot_data or {}).get("quotes"),
                    positions=(snapshot_data or {}).get("positions"),
                    config=config_local,
                )
            except Exception:
                _csp_res = None
            if _csp_res is not None:
                _csp_rsi = _csp_res.get("rsi")
                _csp_rsi_status = _csp_res.get("status", "fresh")
                _csp_rsi_note = _csp_res.get("note")
            else:
                _csp_rsi = rsi_discipline.rsi_for(ticker, csp_technicals)
                _csp_rsi_status = "fresh"
                _csp_rsi_note = None
            # Fail-safe: spot gapped UP past the vintage threshold and the
            # live RSI is not computable → the live RSI is plausibly >70;
            # a new put open may not fire on the stale favourable read.
            if (csp_rsi_gate_on and _csp_rsi_status == "stale"
                    and (_csp_res.get("move_pct") or 0) > 0):
                _filtered_csps.append({
                    "ticker": ticker,
                    "verdict": "RSI_BLOCK",
                    "reason": (
                        f"{_csp_rsi_note or 'stale RSI — reverify'}; snapshot "
                        f"RSI is pre-gap and the live RSI is plausibly >70 "
                        f"(rule #44 fail-safe)"
                    ),
                })
                continue
            _csp_rsi_assess = rsi_discipline.assess(_csp_rsi, "put", csp_rsi_th)
            if csp_rsi_gate_on and _csp_rsi_assess.blocked:
                _blk_reason = _csp_rsi_assess.reason
                if _csp_rsi_status == "live" and _csp_rsi_note:
                    _blk_reason = f"{_blk_reason} ({_csp_rsi_note})"
                _filtered_csps.append({
                    "ticker": ticker,
                    "verdict": "RSI_BLOCK",
                    "reason": _blk_reason,
                })
                continue
            # Extended-band demotion (rule #43): RSI 60-70 → the full ticket
            # still renders below, but into the wait subsection, not the
            # numbered action list. Computed here; applied after the block is
            # fully built (so the wait card carries every check line).
            _csp_wait_reason = (
                rsi_discipline.put_extended_wait(_csp_rsi, csp_rsi_th)
                if csp_rsi_gate_on else None
            )

            # Chase guard (rule #44, INTC 2026-08-05): a multi-session
            # vertical baked into FRESH daily bars passes the RSI gate (RSI
            # off an oversold base can't flag a 24% run) — measure the tape
            # directly. Blocked → transparency footer (rule #24).
            _csp_tech_entry = csp_technicals.get(ticker) or {}
            _csp_chase = chase_guard.check_chase(
                ticker,
                closes=_csp_tech_entry.get("recent_closes")
                if isinstance(_csp_tech_entry, dict) else None,
                spot=spot, config=config_local,
            )
            if _csp_chase["blocked"]:
                _filtered_csps.append({
                    "ticker": ticker,
                    "verdict": "CHASE_GUARD",
                    "reason": _csp_chase["reason"],
                })
                continue
            _csp_chase_caution = _csp_chase["caution"]

            # LT-verdict discipline gate (hard rule #39, audit 2026-07-03).
            # No new put on a name whose long_term_verdict is broken/
            # downtrend/weakening while spot sits below the 200-SMA. A fresh
            # (≤14d) tier ≥4 BUY overrides — kept, but with a visible
            # LT-trend warning line (the META $515P case).
            _lt_gate = lt_verdict_gate.check_lt_verdict_gate(
                ticker, snapshot_data or {}, _csp_rec_by_ticker.get(ticker)
            )
            if not _lt_gate["pass"]:
                _filtered_csps.append({
                    "ticker": ticker,
                    "verdict": "LT_VERDICT",
                    "reason": _lt_gate["reason"],
                })
                continue
            _lt_warning = _lt_gate.get("warning")

            # Query live E*TRADE chain for put near 12% OTM, 30-40 DTE.
            # Expiration policy (2026-08-10): prefer the standard monthly
            # (3rd-Friday) inside the band — the PEP Sep 11 weekly
            # ($0.14/$0.45, 107% spread) case; the band is the tenor bound.
            chain_data = find_put_strike_near(
                ticker, target_otm_pct=12.0, target_dte_min=25,
                target_dte_max=40, spot=spot,
                prefer_monthly=bool(
                    (config_local.get("expiration_policy") or {}).get("prefer_monthly")
                ),
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

            # Universal 5% strike-overlap check (hard rule #40, audit
            # 2026-07-03: AMZN $215P proposed while holding $225P — 4.4%
            # apart). The put-stack guard above only fires at ≥2 held puts;
            # this catches the single-held-put duplicate the same way the
            # LT_CSP path always has.
            _held_strikes = (existing or {}).get("strikes") or []
            _ov = put_overlap_check.check_strike_overlap(
                ticker, target_strike, _held_strikes
            )
            if _ov["overlap"]:
                _filtered_csps.append({
                    "ticker": ticker,
                    "verdict": "SKIPPED",
                    "ev": 0,
                    "strike": target_strike,
                    "exp": target_exp,
                    "reason": (
                        f"proposed ${target_strike:g}P is "
                        f"{(_ov['distance_pct'] or 0)*100:.1f}% from existing "
                        f"${_ov['existing_strike']:g}P (inside "
                        f"{put_overlap_check.OVERLAP_PCT*100:.0f}% band) — "
                        f"concentrates rather than diversifies"
                    ),
                })
                continue

            # Wash-sale check
            ws_blocked, ws_reason = is_wash_sale_blocked(ticker, as_of_iso, ledger_path=ledger_path)
            if ws_blocked:
                continue
            # Earnings guard — a NEW-open put whose underlying prints before
            # (or on) the contract's expiry SPANS the print → hard BLOCK
            # (domain rule: never sell puts through earnings). Observed
            # 2026-08-04 NVDA card: prints Aug 26, expiry Sep 04 — the old
            # code only blocked the ≤14d "imminent" level and rendered the
            # spanning contract as an actionable card with a double-⚠️
            # "Earnings 22d away, -9d before expiration" warning. Demoted to
            # the transparency footer, never silently hidden (rule #24).
            ec = check_earnings_conflict(ticker, target_exp, ec_today, as_of_iso)
            if ec.get("level") == "block" or ec.get("spans_expiration"):
                _filtered_csps.append({
                    "ticker": ticker,
                    "verdict": "EARNINGS_WINDOW",
                    "reason": format_new_open_block(ticker, ec, target_exp),
                })
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
            _blk_start = len(items)
            # Label renamed from "PULLBACK CSP" (2026-08-04): George read
            # "PULLBACK CSP NVDA" as "NVDA is in a pullback now". It's a
            # STRATEGY name (sell a below-spot put; get paid to wait for a
            # pullback fill) — "CSP — PAID-TO-WAIT" says what the trade is,
            # and the explainer line below states the stock's CURRENT state
            # explicitly. Internal kind stays PULLBACK_CSP (label aliased in
            # briefing_diff/capital-planner so keys/kinds are unchanged).
            # Expiration policy label: when the selector recorded the kind,
            # render weekday+YEAR + "(monthly)"/"(weekly)" (rules #6/#19);
            # policy off → legacy byte-identical string.
            if chain_data.get("exp_kind"):
                _csp_exp_txt = (exp_date.strftime("%a %b %d '%y")
                                + f" ({chain_data['exp_kind']})")
            else:
                _csp_exp_txt = exp_date.strftime('%a %b %d')
            _csp_header = (
                f"{n}. **CSP — PAID-TO-WAIT** {ticker} — sell ${target_strike:g}P "
                f"exp {_csp_exp_txt} for ${est_premium:.2f} premium "
                f"(would re-acquire 100 shares @ {(target_strike/spot - 1)*100:.0f}% below spot)"
            )
            # Rule #46: non-fresh RSI vintages carry their read inline so the
            # aggregate annotator never re-attaches a favourable 🟢 tag. A
            # verified-fresh name renders unchanged (tagged downstream).
            if _csp_rsi_status == "live" and _csp_rsi is not None:
                _csp_header += (
                    f"  · {rsi_discipline.tag(_csp_rsi, 'put', csp_rsi_th)} "
                    f"({_csp_rsi_note})"
                )
            elif _csp_rsi_status == "unverified" and _csp_rsi is not None:
                _csp_header += (
                    f"  · RSI {_csp_rsi:.0f} ⚠ unverified (no live quote this "
                    f"cycle) — do not trust the favourable read"
                )
            elif _csp_rsi_status == "stale" and _csp_rsi_note:
                _csp_header += f"  · {_csp_rsi_note}"
            items.append(_csp_header)
            # Strategy explainer — the label names the STRATEGY, not the
            # stock's state; the current state is stated explicitly so
            # "paid-to-wait" can never be read as "this name is pulling
            # back now" (the 2026-08-04 NVDA RSI-53 confusion).
            _csp_state_txt = (
                f"RSI {_csp_rsi:.0f} ({rsi_discipline.market_state(_csp_rsi)})"
                if _csp_rsi is not None else "RSI unavailable"
            )
            # 2026-08-10 bug 5: the old explainer appended meta-commentary
            # about the recommendation's NAME ("The name does not claim the
            # stock is currently pulling back; today's state: …") — awkward
            # boilerplate. State only the strategy + today's measured state.
            items.append(
                f"   - _Paid-to-wait: keep the premium if no dip comes, or "
                f"re-acquire at {(target_strike/spot - 1)*100:.0f}% below "
                f"spot if one does. Today: {_csp_state_txt}._"
            )
            if _csp_chase_caution:
                # Rule #44 fail-open path: spot measurable but no close
                # series — the run-up can't be verified, say so on the card.
                items.append(f"   - {_csp_chase_caution}")
            items.append(f"   - {format_yield_line(csp_y)}")
            # Setup Grade (George 2026-08-10) — entry-timing grade + clear
            # message on the ticket. Uses the vintage-RESOLVED RSI this
            # block already computed (rule #46), never the raw snapshot.
            # Config-gated; fail-open → no line, legacy byte-identical.
            # B floor (George 2026-08-12: "recommendations are A or B, not
            # D") — below-floor tickets demote to the wait/planning
            # subsection with the measured note (rule #24, never hidden);
            # ungradeable tickets stay actionable with a verify note
            # (fail-OPEN, rule #19).
            _csp_floor_note = None
            try:
                from analysis import setup_grade as _sgm
                if _sgm.setup_grade_enabled(config_local):
                    _csp_sg = _sgm.grade_for_new_open(
                        ticker, "csp", snapshot_data=snapshot_data or {},
                        strike=float(target_strike), spot=spot,
                        rsi=_csp_rsi, thresholds=csp_rsi_th,
                        config=config_local)
                    if _csp_sg:
                        items.append(f"   - {_sgm.format_grade_note(_csp_sg)}")
                        _fl_b, _fl_n = _sgm.below_actionable_floor(
                            _csp_sg, config_local)
                        if _fl_b:
                            _csp_floor_note = _fl_n
                    elif _sgm.actionable_floor_enabled(config_local):
                        items.append(f"   - {_sgm.GRADE_NA_NOTE}")
            except Exception:
                pass
            items.append(f"   - **Source:** Live E*TRADE chain")
            # Capacity-gate DEFERRED tag (hard rule #41) — the ticket still
            # renders in full, but never reads as a green light while stress
            # coverage sits below the floor.
            if _csp_capacity_tag:
                items.append(f"   - **{_csp_capacity_tag}**")
            # LT-trend warning for fresh-tier-≥4 overrides (hard rule #39) —
            # the catalyst kept the rec; the trend contradiction stays visible.
            if _lt_warning:
                items.append(f"   - **⚠ LT-trend note:** {_lt_warning}")
            if csp_val is not None:
                items.append(f"   - {format_validation_line(csp_val)}")
            # Affirmative wash-sale + earnings clearance
            items.append(f"   - **Wash-sale check:** ✅ {ticker} clear (no recent loss closures within 30d).")
            # Earnings check line — ALWAYS prefixed "Earnings check:" so the
            # tax_cpa quality_gate persona's grep finds it (warn case was
            # rendering only the badge, blocking the briefing on 2026-06-26).
            if ec.get("level") == "warn":
                items.append(f"   - **Earnings check:** {format_earnings_badge(ec)}")
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
            # Concentration post-assignment check — tier-aware (rule #29).
            # 2026-08-04 (PLTR): the card said "~12.6% NLV (over 10% cap)" on
            # a Tier A name whose cap is 22% — the same run's Risk Alerts said
            # "within Tier A bounds". Explicit Tier A/B names use
            # concentration_cap_for_tier; core names (core_positions ∪ Tier A
            # via core_union) use at least the core soft cap. Legacy 10% text
            # only when neither applies.
            current_value = (er.get("qty", 0) or 0) * spot
            post_assign_value = current_value + (target_strike * 100)
            post_assign_pct = (post_assign_value / nlv * 100) if nlv else 0
            _pc_cap = 10.0
            _pc_label = "10% cap"
            _pc_tier = None
            try:
                from analysis.position_tiers import (
                    TIER_A as _PC_A, TIER_B as _PC_B,
                    concentration_cap_for_tier as _pc_cap_for,
                    tier_for as _pc_tier_for,
                )
                if (config_local or {}).get("position_tiers"):
                    _t = _pc_tier_for(ticker, config_local)
                    if _t in (_PC_A, _PC_B):
                        _pc_tier = _t
                        _pc_cap = _pc_cap_for(_t, config_local)
                        _pc_label = f"Tier {_t} cap {_pc_cap:.0f}%"
            except Exception:
                pass
            if _pc_tier is None and ticker in core_tickers_set:
                _core_cap = float(
                    (config_local or {}).get("core_concentration_cap_pct", 18))
                if _core_cap > _pc_cap:
                    _pc_cap = _core_cap
                    _pc_label = f"core soft cap {_pc_cap:.0f}%"
            # Projected per-name concentration (2026-08-13 SNDK gap) —
            # obligation-inclusive: existing equity MV + held short-put
            # obligations + NEW strike×100, vs the SAME effective cap this
            # surface already grants the name (tier cap / core soft cap).
            # Over cap → measured warning line on the card AND demotion to
            # the wait/planning subsection (rule #24 — never hidden, never
            # green-lit). Fail-open: missing NLV / any error → no note.
            _csp_size_note = None
            try:
                from analysis import position_tiers as _pt_size
                _csp_size_note = _pt_size.size_warning_line(
                    _pt_size.projected_name_concentration(
                        ticker, target_strike, 1, nlv,
                        (snapshot_data or {}).get("positions") or [],
                        config_local,
                        min_cap_pct=_pc_cap, min_cap_label=_pc_label))
            except Exception:
                _csp_size_note = None
            if _csp_size_note:
                # Obligation-inclusive size warning supersedes the legacy
                # equity-only post-assignment line (same-card duplicate
                # would double-report the same breach).
                items.append(f"   - **{_csp_size_note}**")
            elif post_assign_pct > _pc_cap:
                items.append(
                    f"   - **⚠️ Concentration check:** Assignment would push {ticker} to "
                    f"~{post_assign_pct:.1f}% NLV (over {_pc_label}). Consider sizing down or pre-arrange "
                    f"a partial-sale plan."
                )
            elif post_assign_pct > 10:
                _pc_bounds = (f"Tier {_pc_tier} bounds" if _pc_tier
                              else "core bounds")
                items.append(
                    f"   - **📊 Concentration check:** Assignment would push {ticker} to "
                    f"~{post_assign_pct:.1f}% NLV — within {_pc_bounds} "
                    f"(cap {_pc_cap:.0f}%)."
                )
            items.append(
                f"   - **Account:** "
                f"{_route_account('NEW CSP', ticker, None, accounts_cfg)}"
            )
            if _csp_wait_reason or _csp_floor_note or _csp_size_note:
                # Move the fully-built block into the wait subsection: swap the
                # numbered header for a ⏸ one and keep every check line (full
                # ticket, rule #24). The numbered action list never sees it.
                # The B floor (George 2026-08-12) composes with the RSI wait
                # AND the capacity tag (already inside the block) — every
                # applicable reason renders once. The size demotion
                # (2026-08-13 SNDK gap) composes the same way: the measured
                # ⚠ Size line is already inside the block.
                blk = items[_blk_start:]
                del items[_blk_start:]
                _demote_label = ("wait" if _csp_wait_reason
                                 else "below setup floor" if _csp_floor_note
                                 else "over size cap")
                blk[0] = blk[0].replace(
                    f"{n}. **CSP — PAID-TO-WAIT** {ticker}",
                    f"- ⏸ **CSP — PAID-TO-WAIT ({_demote_label})** {ticker}",
                )
                _ins = 1
                if _csp_wait_reason:
                    blk.insert(_ins, f"   - **{_csp_wait_reason}**")
                    _ins += 1
                if _csp_floor_note:
                    blk.insert(_ins, f"   - **{_csp_floor_note}**")
                _wait_csp_blocks.extend(blk)
            else:
                n += 1
            # Limit to 3 pullback CSPs (actionable + wait combined)
            if (sum(1 for it in items if "CSP — PAID-TO-WAIT" in it)
                    + sum(1 for it in _wait_csp_blocks if "CSP — PAID-TO-WAIT" in it)) >= 3:
                break

    # ⏸ CSPs — wait for a pullback (rule #43): extended-band (RSI 60-70)
    # PULLBACK CSPs, full ticket shown but never numbered/green-lit.
    if _wait_csp_blocks:
        items.append("")
        items.append("**⏸ CSPs — wait for a pullback / below setup floor** "
                     "(full ticket shown, never green-lit — each carries its "
                     "measured reason)")
        items.extend(_wait_csp_blocks)

    # Transparency footer: tell the user which CSP ideas were rejected and why.
    # Two reasons today:
    #   (a) Trade-validator POOR/BLOCK verdicts (negative EV)
    #   (b) Existing-put-stack: skip names where user already has ≥2 short puts
    if _filtered_csps:
        validator_rejects = [c for c in _filtered_csps if c.get("verdict") not in
                             ("SKIPPED", "RSI_BLOCK", "LT_VERDICT",
                              "EARNINGS_WINDOW", "CHASE_GUARD")]
        stack_skips = [c for c in _filtered_csps if c.get("verdict") == "SKIPPED"]
        rsi_blocks = [c for c in _filtered_csps if c.get("verdict") == "RSI_BLOCK"]
        lt_blocks = [c for c in _filtered_csps if c.get("verdict") == "LT_VERDICT"]
        earnings_blocks = [c for c in _filtered_csps if c.get("verdict") == "EARNINGS_WINDOW"]
        chase_blocks = [c for c in _filtered_csps if c.get("verdict") == "CHASE_GUARD"]
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
        if earnings_blocks:
            # The reason already leads with "🚫 BLOCK (EARNINGS_WINDOW)" — no
            # second glyph (the observed card's "⚠️ ⚠️" doubling, inverted).
            for c in earnings_blocks:
                items.append(
                    f"_CSP — PAID-TO-WAIT {c['ticker']} blocked — "
                    f"{c.get('reason', '🚫 contract spans the earnings print')}_"
                )
        if rsi_blocks:
            for c in rsi_blocks:
                items.append(
                    f"_📊 CSP — PAID-TO-WAIT {c['ticker']} blocked — {c.get('reason', 'RSI overbought')}_"
                )
        if chase_blocks:
            # Rule #44 chase guard — vertical multi-session tape (never
            # silently hidden, rule #24).
            for c in chase_blocks:
                items.append(
                    f"_CSP — PAID-TO-WAIT {c['ticker']} blocked — "
                    f"{c.get('reason', '🚫 chase guard (rule #44)')}_"
                )
        if lt_blocks:
            for c in lt_blocks:
                items.append(
                    f"_📉 CSP — PAID-TO-WAIT {c['ticker']} blocked — {c.get('reason', 'LT-verdict gate (rule #39)')}_"
                )
        if stack_skips:
            for c in stack_skips:
                items.append(
                    f"_📚 CSP — PAID-TO-WAIT {c['ticker']} skipped — {c.get('reason', 'put-stack guard')}_"
                )

    # Bug #25 transparency footer: every CLOSE suppressed by a standing
    # directive is named (never silently hidden — rule #24), with the
    # measured capture/DTE so the user can see the release conditions
    # haven't fired.
    if _directive_suppressed:
        items.append("")
        for _ds in _directive_suppressed:
            items.append(
                f"_📋 CLOSE {_ds['contract']} suppressed by standing directive "
                f"(capture {_ds['capture_pct']:.0f}%, DTE {_ds['dte']}d — "
                f"release conditions not met)_"
            )

    # Task #43 defect 3: directive suppressions paused by an imminent
    # earnings print — the CLOSE rendered above; this note explains why
    # the standing directive didn't hold it this cycle (rule #24: the
    # user sees the pause, never a silent override).
    if _directive_paused:
        items.append("")
        items.extend(_directive_paused)

    # Task #38 Part 3: tested-at-≤21-DTE contracts held by a standing
    # directive — the note renders (rule #24, never hidden), the ⛔ nag does
    # not (the user's decision is on file).
    if _tested_directive_notes:
        items.append("")
        items.extend(_tested_directive_notes)

    # RSI discipline display: append a side-aware RSI tag to every numbered
    # action line. Annotate a display copy so the summary card still parses the
    # un-annotated originals.
    _rsi_tech = (snapshot_data or {}).get("technicals", {}) or {}
    _rsi_th = rsi_discipline.load_thresholds(config_local)
    display_items = rsi_discipline.annotate_action_lines(items, _rsi_tech, _rsi_th)

    # Step 7.5: recommendation aging — ⏳ tags, day-5 binary prompts, headline
    # cap of 5 + appendix. Runs only when the orchestrator passed aging_info
    # (legacy callers get unchanged behavior). Fail-soft: aging must never
    # break the briefing.
    if aging_info is not None:
        try:
            from analysis.rec_aging import action_key, apply_aging_to_action_items
            # Bug #25: directive-suppressed CLOSEs must not age — record their
            # keys so the stalled panel (and any other aging consumer) skips
            # them. The user is FOLLOWING the directive, not ignoring the rec.
            aging_info["directive_suppressed"] = {
                action_key("CLOSE", _ds["contract"])
                for _ds in _directive_suppressed
            } | {
                action_key("CLOSE_FOR_PROFIT", _ds["contract"])
                for _ds in _directive_suppressed
            }
            display_items = apply_aging_to_action_items(display_items, aging_info)
        except Exception as _age_e:
            print(f"[action_list] recommendation aging failed: {_age_e}", file=sys.stderr)

    if display_items:
        lines.extend(display_items)
    else:
        lines.append("- No urgent actions today. Hold and watch.")

    # Stale-data warning (if applicable)
    fresh_warn = _data_freshness_warning(snapshot_data or {})
    if fresh_warn:
        lines.append("")
        lines.append(fresh_warn)

    # Append the total-impact summary card (uses un-annotated items).
    # options_reviews / new_ideas feed the SHARED net-option-cash computation
    # so this card and the 💰 Money Plan render the same number (rule #43).
    lines.extend(render_summary_card(items, snapshot_data,
                                     options_reviews=options_reviews,
                                     new_ideas=new_ideas))

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
                # Legacy select_roll_target keys are strikePrice/expirationDate
                # (rule #43, 2026-07-31: the old strike/expiration lookups
                # rendered None/? placeholders). Fail closed when unresolved.
                r_strike = roll.get("strikePrice") or roll.get("strike")
                r_exp = roll.get("expirationDate") or roll.get("expiration")
                if r_strike and r_exp:
                    lines.append(f"  - roll target: ${float(r_strike):g} exp {r_exp} for ${roll.get('expectedNetCredit', 0):.2f} credit")
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

    # Extended-band demotion (rule #43): rsi_wait ideas keep the full ticket
    # but render under "⏸ CSPs — wait for a pullback", never as actionable.
    rsi_wait_ideas = [i for i in actionable if i.get("rsi_wait")]
    actionable = [i for i in actionable if not i.get("rsi_wait")]

    # B floor (George 2026-08-12: "recommendations are A or B, not D") —
    # below-floor tickets render under "⏸ CSPs — below setup floor" with
    # the measured demotion note, never as actionable. An idea that is BOTH
    # rsi_wait and below-floor stays in the wait subsection and carries the
    # floor note there too (both reasons shown once each).
    floor_ideas = [i for i in actionable if i.get("setup_floor_demoted")]
    actionable = [i for i in actionable if not i.get("setup_floor_demoted")]

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
            promo = " ✅ RSI favourable" if idea.get("rsi_decision") == "promote" else (
                f" {idea.get('rsi_badge')}" if idea.get("rsi_badge") else "")
            lines.append(f"#### {label} (spot ${spot:.2f}{target_str}){promo}")
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
            # Setup Grade (George 2026-08-10) — the composed entry-timing
            # line rides on the idea dict (single source: setup_grade.py).
            # Absent (flag off / ungraded) → byte-identical legacy card.
            if idea.get("setup_grade_line"):
                lines.append(f"- {idea['setup_grade_line']}")
            elif idea.get("setup_floor_na"):
                # Fail-OPEN under the actionable floor (rule #19): the
                # ungradeable ticket stays actionable with the note.
                lines.append("- 🏁 grade n/a — verify setup manually")
            if idea.get("rsi_14") is not None:
                lines.append(
                    f"- **RSI:** {idea.get('rsi_tag')} — {idea.get('rsi_note', '')}"
                )
            rec_label = idea.get("raw_recommendation", "")
            age = idea.get("rec_age_days", 0)
            if rec_label:
                lines.append(f"- Source: {rec_label} ({age}d old)")
            lines.append("")

    if rsi_wait_ideas:
        lines.append(f"### ⏸ CSPs — wait for a pullback ({len(rsi_wait_ideas)})")
        lines.append("")
        lines.append("_RSI 60-70 extended — full ticket shown (never hidden), but "
                     "selling into a green streak sets the strike against an inflated "
                     "spot. Re-check on a red day / RSI 35-55._")
        lines.append("")
        for idea in rsi_wait_ideas:
            ticker = idea.get("ticker", "?")
            spot = idea.get("spot", 0)
            strike = idea.get("strike", 0)
            mid = idea.get("mid", 0)
            exp_pretty = idea.get("expiration_pretty", idea.get("expiration", "?"))
            dte = idea.get("dte", 0)
            annualized = idea.get("annualized_pct", 0)
            collateral = idea.get("collateral", 0)
            lines.append(
                f"- ⏸ **{ticker}** (spot ${spot:.2f}) — SELL TO OPEN {exp_pretty} "
                f"**${strike:g} PUT** @ ${mid:.2f} mid · {dte} DTE · "
                f"{annualized:.1f}% annualized · collateral ${collateral:,.0f}"
            )
            lines.append(f"  - **{idea.get('rsi_wait_reason', '')}**")
            if idea.get("setup_floor_note"):
                # Composes with the RSI wait — both reasons, once each.
                lines.append(f"  - **{idea['setup_floor_note']}**")
            if idea.get("setup_grade_line"):
                lines.append(f"  - {idea['setup_grade_line']}")
        lines.append("")

    if floor_ideas:
        # B floor (George 2026-08-12: "we absolutely need to fix the right
        # recommendations for both CSPs and CCs so that recommendations
        # are A or B, not D"). Full ticket shown (rule #24) — never
        # green-lit while the setup grades below the floor.
        lines.append(f"### ⏸ CSPs — below setup floor ({len(floor_ideas)})")
        lines.append("")
        lines.append("_Setup Grade below the actionable B floor — full "
                     "ticket shown for planning (never hidden), but the "
                     "entry timing doesn't earn a green light today._")
        lines.append("")
        for idea in floor_ideas:
            ticker = idea.get("ticker", "?")
            spot = idea.get("spot", 0)
            strike = idea.get("strike", 0)
            mid = idea.get("mid", 0)
            exp_pretty = idea.get("expiration_pretty", idea.get("expiration", "?"))
            dte = idea.get("dte", 0)
            annualized = idea.get("annualized_pct", 0)
            collateral = idea.get("collateral", 0)
            lines.append(
                f"- ⏸ **{ticker}** (spot ${spot:.2f}) — SELL TO OPEN {exp_pretty} "
                f"**${strike:g} PUT** @ ${mid:.2f} mid · {dte} DTE · "
                f"{annualized:.1f}% annualized · collateral ${collateral:,.0f}"
            )
            lines.append(f"  - **{idea.get('setup_floor_note', '')}**")
            if idea.get("capacity_blocked") and idea.get("capacity_reason"):
                # Composes with the capacity gate — both reasons, once each.
                lines.append(f"  - **⏸ Deferred (capacity gated)** — "
                             f"{idea['capacity_reason']}")
            if idea.get("setup_grade_line"):
                lines.append(f"  - {idea['setup_grade_line']}")
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
                rsi_suffix = ""
                if idea.get("rsi_14") is not None and "RSI" not in idea.get("rationale", ""):
                    rsi_suffix = f"  · {idea.get('rsi_tag')}"
                # B floor composes with capacity/RSI blocks (George
                # 2026-08-12) — a watch-only ticket that ALSO grades below
                # the floor shows both reasons, once each.
                floor_suffix = (f"  · {idea['setup_floor_note']}"
                                if idea.get("setup_floor_note") else "")
                lines.append(f"- {label}: {idea.get('rationale', '')}{rsi_suffix}{floor_suffix}")
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


def render_manifest(snapshot_dir_path: str, data_provenance: dict | None = None) -> list:
    """Render snapshot manifest panel.

    When the snapshot's data_provenance carries the task-#36 routing counts,
    render the measured chain/quote source split on EVERY briefing — the
    2026-08-06 lesson: the split lived only in console stdout and the
    (conditionally-rendered) policer panel, so a healthy-looking run gave no
    briefing-visible evidence that E*TRADE routing engaged at all.
    """
    lines = [
        "## Appendix: Snapshot Manifest",
        "",
        f"Snapshot directory: `{snapshot_dir_path}/`",
        "",
    ]
    prov = data_provenance or {}
    _ch = prov.get("chains") or {}
    _q = prov.get("quotes") or {}
    if _ch.get("routing_attempted") or _q.get("routing_attempted"):
        lines.insert(3, (
            f"Data routing (measured this cycle): "
            f"chains {_ch.get('etrade', 0)} etrade · "
            f"{_ch.get('yfinance', 0)} yfinance-fallback — "
            f"quotes {_q.get('etrade', 0)} etrade · "
            f"{_q.get('yfinance', 0)} yfinance-fallback"
            + (" — ⚠ E*TRADE routing fell back wholesale: "
               + str(_ch.get("wholesale_fallback_reason")
                     or _q.get("wholesale_fallback_reason"))
               if (_ch.get("wholesale_fallback_reason")
                   or _q.get("wholesale_fallback_reason")) else "")
        ))
        lines.insert(4, "")
    return lines
