"""Pre-trade validator — codifies the briefing's discipline rules into a single
checkpoint that any proposed CSP/CC must pass before it shows up as actionable.

The motivating case (2026-06-15): user had a GTC SELL OPEN order for
MU $960P Aug 21 sitting at a stale $154 limit. The trade would have:
  1. Violated the earnings-window rule (MU prints ~June 24, inside the Aug 21
     contract window)
  2. Violated ENTRY GATES CLOSED (stress coverage 0.11× < 0.50×)
  3. Pushed the Aug 21 cluster from 29.5% NLV (warning) back to ~38% (critical)
  4. Been rolling FROM safer (closed $700P at 38% OTM) TO riskier ($960P at
     10% OTM) on the same name same day

Each of those checks lives in different places in the codebase (earnings in
candidate_research, gates in capital_planner, buckets in expiration_ladder,
S/R in support_resistance). This module consolidates them into one validator
so the same logic runs against:
  - Candidate Trades CSP entries (inline rendering)
  - E*TRADE pending orders (audit pass — separate wiring)
  - Manual "should I do this trade" queries

Returns structured findings (severity + reason + detail + rule_id) that
downstream renderers can format consistently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# Severity levels — same vocabulary as the briefing's Red Flags + Risk Alerts.
SEV_BLOCK = "BLOCK"   # hard discipline violation — should not execute
SEV_WARN = "WARN"     # acceptable but flag it; user judgment
SEV_OK = "OK"         # explicit pass note

_SEVERITY_RANK = {SEV_BLOCK: 0, SEV_WARN: 1, SEV_OK: 2}
_SEVERITY_EMOJI = {SEV_BLOCK: "🚫", SEV_WARN: "⚠️", SEV_OK: "✅"}


@dataclass
class TradeValidation:
    """One discipline-rule finding against a proposed trade."""
    severity: str       # BLOCK | WARN | OK
    reason: str         # one-line summary
    detail: str         # multi-sentence explanation
    rule_id: str        # which hard rule fired (for downstream filtering/test pinning)


@dataclass
class PreTradeContext:
    """All inputs needed to validate a proposed CSP or CC.

    Build this from the snapshot for the user's normal candidates, OR from
    an E*TRADE pending order payload for the audit pass.
    """
    # Trade definition
    ticker: str
    strike: float
    expiration: date
    option_type: str        # "PUT" or "CALL"
    action: str             # "SELL_OPEN", "BUY_CLOSE", etc.
    quantity: int = 1
    limit_price: Optional[float] = None

    # Underlying context
    spot: Optional[float] = None
    rsi: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    iv_rank: Optional[float] = None
    earnings_date: Optional[date] = None
    sr_payload: Optional[dict] = None  # support_resistance.to_dict() shape
    # Optional reference for limit-price sanity check (Rule 12). Callers that
    # have a fresh chain quote pass the mid; without it Rule 12 stays silent.
    current_mid_hint: Optional[float] = None

    # Portfolio state
    nlv: float = 0.0
    cash: float = 0.0
    stress_coverage: Optional[float] = None
    # Existing positions on same ticker — for roll-from-safer detection
    existing_short_puts: list = field(default_factory=list)  # [{strike, expiration, qty}, ...]
    existing_long_puts: list = field(default_factory=list)
    existing_short_calls: list = field(default_factory=list)
    held_shares: int = 0

    # Aggregate by-date put obligation — for cluster-impact check
    obligation_by_expiration: dict = field(default_factory=dict)  # date -> $$

    # Tier from third-party (Parkev) — for promotion/demotion context
    rating_tier: Optional[int] = None


def _opt_qty(ctx: PreTradeContext) -> int:
    return abs(int(ctx.quantity or 1))


def _proposed_obligation(ctx: PreTradeContext) -> float:
    """Cash-secured put obligation for the proposed trade."""
    if ctx.option_type != "PUT" or ctx.action != "SELL_OPEN":
        return 0.0
    return float(ctx.strike) * _opt_qty(ctx) * 100.0


def validate_proposed_trade(ctx: PreTradeContext, config: Optional[dict] = None) -> list[TradeValidation]:
    """Run all discipline rules against a proposed trade.

    Returns a list of findings sorted by severity (BLOCK first, then WARN,
    then OK). An empty-or-OK-only list means the trade passes all checks.
    """
    cfg = config or {}
    findings: list[TradeValidation] = []

    # ─────────────────────────────────────────────────────────────────────
    # Rule 1: Earnings window (CLAUDE.md hard rule)
    # ─────────────────────────────────────────────────────────────────────
    # Selling a new put through earnings on a single-stock name is forbidden
    # except for the explicit "earnings_crush" strategy. Even when the user
    # is willing-to-own, binary earnings risk compresses the probability
    # distribution and IV crush after the print removes the premium edge.
    if (ctx.action == "SELL_OPEN" and ctx.option_type == "PUT"
            and ctx.earnings_date and ctx.earnings_date <= ctx.expiration):
        days_to_earn = (ctx.earnings_date - date.today()).days
        if days_to_earn >= 0:
            findings.append(TradeValidation(
                severity=SEV_BLOCK,
                reason=f"Earnings inside contract window — {ctx.ticker} prints {ctx.earnings_date} ({days_to_earn}d)",
                detail=(
                    f"Proposed expiration {ctx.expiration} is AFTER earnings on "
                    f"{ctx.earnings_date}. CLAUDE.md hard rule: no new puts spanning "
                    f"earnings unless the trade is explicitly an 'earnings_crush' play. "
                    f"The binary print can gap the underlying through the strike; IV "
                    f"crush after the print removes most of the premium edge regardless."
                ),
                rule_id="EARNINGS_WINDOW",
            ))

    # ─────────────────────────────────────────────────────────────────────
    # Rule 2: Entry gates (stress coverage)
    # ─────────────────────────────────────────────────────────────────────
    sc_min = float(cfg.get("entry_gate_min_coverage", 0.50))
    if (ctx.action == "SELL_OPEN" and ctx.option_type == "PUT"
            and ctx.stress_coverage is not None and ctx.stress_coverage < sc_min):
        findings.append(TradeValidation(
            severity=SEV_BLOCK,
            reason=f"ENTRY GATES CLOSED — stress coverage {ctx.stress_coverage:.2f}× < {sc_min:.2f}× floor",
            detail=(
                f"System gate: no new put obligations until stress coverage rebuilds "
                f"to {sc_min:.2f}×. Current coverage {ctx.stress_coverage:.2f}× means "
                f"a 10-20% market drop would land the book in cash-call territory."
            ),
            rule_id="ENTRY_GATES_CLOSED",
        ))

    # ─────────────────────────────────────────────────────────────────────
    # Rule 3: Cash floor (post-margin-call discipline)
    # ─────────────────────────────────────────────────────────────────────
    cash_floor = float(cfg.get("cash_floor_pct", 0.05))
    if (ctx.action == "SELL_OPEN" and ctx.nlv > 0):
        cash_pct_nlv = ctx.cash / ctx.nlv
        if cash_pct_nlv < cash_floor:
            findings.append(TradeValidation(
                severity=SEV_BLOCK,
                reason=f"Cash floor breached — {cash_pct_nlv*100:.1f}% NLV < {cash_floor*100:.0f}% floor",
                detail=(
                    f"Cash position ${ctx.cash:,.0f} is too thin to absorb a normal-day "
                    f"mark-to-market move without margin-call risk (the Friday Jun 5 "
                    f"experience). Defer new put obligation until cash rebuilds."
                ),
                rule_id="CASH_FLOOR",
            ))

    # ─────────────────────────────────────────────────────────────────────
    # Rule 4: Expiration-bucket concentration impact
    # ─────────────────────────────────────────────────────────────────────
    new_obligation = _proposed_obligation(ctx)
    if new_obligation > 0 and ctx.nlv > 0:
        crit_pct = float(cfg.get("bucket_critical_pct", 0.30))
        warn_pct = float(cfg.get("bucket_warning_pct", 0.20))
        current_bucket = float(ctx.obligation_by_expiration.get(ctx.expiration, 0.0) or 0.0)
        projected = current_bucket + new_obligation
        proj_pct = projected / ctx.nlv
        if proj_pct >= crit_pct:
            findings.append(TradeValidation(
                severity=SEV_BLOCK,
                reason=f"{ctx.expiration} bucket → {proj_pct*100:.1f}% NLV (critical ≥{crit_pct*100:.0f}%)",
                detail=(
                    f"Current bucket: ${current_bucket:,.0f} ({current_bucket/ctx.nlv*100:.1f}% NLV). "
                    f"Adding ${new_obligation:,.0f} → ${projected:,.0f} ({proj_pct*100:.1f}% NLV). "
                    f"A single-Friday cluster ≥{crit_pct*100:.0f}% is the critical line. "
                    f"De-cluster instead — pick a different expiration."
                ),
                rule_id="EXPIRATION_BUCKET_CRITICAL",
            ))
        elif proj_pct >= warn_pct:
            findings.append(TradeValidation(
                severity=SEV_WARN,
                reason=f"{ctx.expiration} bucket → {proj_pct*100:.1f}% NLV (warning ≥{warn_pct*100:.0f}%)",
                detail=(
                    f"Current bucket: ${current_bucket:,.0f}. Adding ${new_obligation:,.0f} pushes "
                    f"this Friday past the {warn_pct*100:.0f}% warning line. Acceptable but worth "
                    f"considering a different expiration to spread the ladder."
                ),
                rule_id="EXPIRATION_BUCKET_WARNING",
            ))

    # ─────────────────────────────────────────────────────────────────────
    # Rule 5: Roll-from-safer detection (same-name strike comparison)
    # ─────────────────────────────────────────────────────────────────────
    # Catches the "you just closed a 38% OTM put and are opening a 10% OTM
    # put on the same ticker" pattern. Doesn't block — but flags loudly.
    if (ctx.action == "SELL_OPEN" and ctx.option_type == "PUT"
            and ctx.spot and ctx.existing_short_puts):
        new_otm_pct = (ctx.spot - ctx.strike) / ctx.spot
        for ep in ctx.existing_short_puts:
            try:
                ep_strike = float(ep.get("strike", 0))
                if ep_strike <= 0:
                    continue
                ep_otm = (ctx.spot - ep_strike) / ctx.spot
                # New strike is 5+ percentage points CLOSER to spot than existing
                if new_otm_pct + 0.05 < ep_otm:
                    findings.append(TradeValidation(
                        severity=SEV_WARN,
                        reason=f"Riskier than existing — ${ctx.strike:g}P is {new_otm_pct*100:.1f}% OTM vs existing ${ep_strike:g}P at {ep_otm*100:.1f}% OTM",
                        detail=(
                            f"You already hold a more conservative ${ep_strike:g}P on {ctx.ticker}. "
                            f"Adding a ${ctx.strike:g}P moves the strike closer to spot ({new_otm_pct*100:.1f}% "
                            f"OTM vs {ep_otm*100:.1f}% OTM) — that's rolling FROM safer TO riskier on "
                            f"the same name. Confirm this is a deliberate directional bet, not "
                            f"premium-chasing."
                        ),
                        rule_id="ROLL_UP_RISK_INCREASE",
                    ))
                    break
            except (TypeError, ValueError):
                continue

    # ─────────────────────────────────────────────────────────────────────
    # Rule 6: Long-put cancellation (collar floor)
    # ─────────────────────────────────────────────────────────────────────
    # If the user holds a LONG put on the name (collar floor / protective),
    # selling a short put at/near the same strike cancels the hedge.
    if (ctx.action == "SELL_OPEN" and ctx.option_type == "PUT"
            and ctx.existing_long_puts):
        for lp in ctx.existing_long_puts:
            try:
                lp_strike = float(lp.get("strike", 0))
                if lp_strike <= 0 or ctx.strike <= 0:
                    continue
                overlap = abs(lp_strike - ctx.strike) / lp_strike
                if overlap <= 0.05:
                    findings.append(TradeValidation(
                        severity=SEV_BLOCK,
                        reason=f"Would cancel LONG ${lp_strike:g}P (collar floor)",
                        detail=(
                            f"You hold a long ${lp_strike:g}P on {ctx.ticker} as downside "
                            f"protection. Selling ${ctx.strike:g}P at/near the same strike "
                            f"cancels that hedge — don't un-collar a hedged position to "
                            f"collect premium."
                        ),
                        rule_id="LONG_PUT_CANCELLATION",
                    ))
                    break
            except (TypeError, ValueError):
                continue

    # ─────────────────────────────────────────────────────────────────────
    # Rule 7: RSI overbought block for new PUT (extended-tape gate)
    # ─────────────────────────────────────────────────────────────────────
    if (ctx.action == "SELL_OPEN" and ctx.option_type == "PUT"
            and ctx.rsi is not None and ctx.rsi >= 70):
        findings.append(TradeValidation(
            severity=SEV_BLOCK,
            reason=f"RSI {ctx.rsi:.0f} overbought — collecting thinnest premium right before a reversal",
            detail=(
                f"{ctx.ticker} at RSI {ctx.rsi:.0f} is statistically extended. Selling a "
                f"put here collects minimal premium right before mean-reversion can whip "
                f"the underlying down through the strike. Wait for RSI < 60 to write puts."
            ),
            rule_id="RSI_OVERBOUGHT_PUT",
        ))

    # ─────────────────────────────────────────────────────────────────────
    # Rule 8: RSI oversold block for new CALL (don't cap a bounce)
    # ─────────────────────────────────────────────────────────────────────
    if (ctx.action == "SELL_OPEN" and ctx.option_type == "CALL"
            and ctx.rsi is not None and ctx.rsi < 35):
        findings.append(TradeValidation(
            severity=SEV_BLOCK,
            reason=f"RSI {ctx.rsi:.0f} oversold — would cap a name right before a likely bounce",
            detail=(
                f"{ctx.ticker} at RSI {ctx.rsi:.0f} is statistically oversold. Writing a "
                f"covered call here caps the upside right before mean-reversion typically "
                f"lifts the price. Wait for RSI ≥ 60 to write CCs."
            ),
            rule_id="RSI_OVERSOLD_CALL",
        ))

    # ─────────────────────────────────────────────────────────────────────
    # Rule 9: Stale limit price (CC/CSP)
    # ─────────────────────────────────────────────────────────────────────
    # If a limit price is materially different from the current chain mid,
    # the order is likely stale — won't fill OR will fill at a worse price
    # than expected. This is what bit the MU $960P case (limit $154 vs
    # current mid ~$120). We can't check this without a chain quote, but
    # callers passing `limit_price` + a `current_mid_hint` via context can
    # tack on this check. (Defer — the chain fetcher integration goes here.)

    # ─────────────────────────────────────────────────────────────────────
    # Rule 10: Strike vs S/R proximity (PUT — strike should be near/below support)
    # ─────────────────────────────────────────────────────────────────────
    if (ctx.action == "SELL_OPEN" and ctx.option_type == "PUT"
            and ctx.spot and ctx.sr_payload):
        supports = (ctx.sr_payload or {}).get("supports") or []
        if supports:
            # Find a support level within ±5% of the strike — that's an anchored entry.
            anchored = False
            for s in supports:
                try:
                    sp = float(s.get("price", 0))
                    if sp <= 0:
                        continue
                    if abs(sp - ctx.strike) / ctx.strike <= 0.05:
                        anchored = True
                        break
                except (TypeError, ValueError):
                    continue
            if not anchored:
                # Strike isn't near a support cluster — not a hard block, but worth noting.
                findings.append(TradeValidation(
                    severity=SEV_WARN,
                    reason=f"Strike ${ctx.strike} not anchored to support cluster",
                    detail=(
                        f"None of {ctx.ticker}'s identified support clusters sit within "
                        f"5% of the ${ctx.strike} strike. The cleanest CSP entries pick a "
                        f"strike at or just above a strong support — if assigned, you "
                        f"own at a real chart level. Without that anchor, assignment "
                        f"happens at an arbitrary price."
                    ),
                    rule_id="STRIKE_NOT_AT_SUPPORT",
                ))

    # ─────────────────────────────────────────────────────────────────────
    # Rule 11: Covered-call writability (NAKED CALL prevention)
    # ─────────────────────────────────────────────────────────────────────
    # A short call must be backed by 100 shares. If existing short calls plus
    # the proposed new contracts exceed the held share count / 100, the new
    # write would create naked exposure on the uncovered contracts. IRA-level
    # accounts can't write naked calls at all; even Level 3+ shouldn't.
    if (ctx.action == "SELL_OPEN" and ctx.option_type == "CALL"):
        existing_call_qty = sum(
            int(c.get("qty", 0) or 0) for c in (ctx.existing_short_calls or [])
        )
        proposed_qty = _opt_qty(ctx)
        total_call_qty = existing_call_qty + proposed_qty
        shares_needed = total_call_qty * 100
        if ctx.held_shares < shares_needed:
            uncovered = shares_needed - ctx.held_shares
            uncovered_contracts = uncovered // 100 + (1 if uncovered % 100 else 0)
            findings.append(TradeValidation(
                severity=SEV_BLOCK,
                reason=(
                    f"Naked exposure — {ctx.held_shares} shares can only cover "
                    f"{ctx.held_shares // 100} CC contracts, but you'd have "
                    f"{total_call_qty} total ({existing_call_qty} existing + "
                    f"{proposed_qty} new)"
                ),
                detail=(
                    f"Selling {proposed_qty}× new ${ctx.strike}C on top of "
                    f"{existing_call_qty} existing short call(s) requires "
                    f"{shares_needed} shares of coverage. You hold {ctx.held_shares}. "
                    f"{uncovered_contracts} contract(s) ({uncovered} shares) would "
                    f"be NAKED — unlimited upside risk. Close an existing short "
                    f"call first, or buy more shares before writing."
                ),
                rule_id="NAKED_CALL_EXPOSURE",
            ))

    # ─────────────────────────────────────────────────────────────────────
    # Rule 12: Limit-price sanity (stale-order detection)
    # ─────────────────────────────────────────────────────────────────────
    # When the user provides a limit_price AND we have a current_mid_hint
    # from the chain, flag if the limit is materially off the market. The
    # MU $154 limit case (stale after MU rallied 9% and the put collapsed
    # to $120) is the motivating example — that GTC order would have sat
    # forever without filling.
    stale_threshold = float(cfg.get("limit_sanity_pct", 0.20))
    if (ctx.limit_price is not None and ctx.current_mid_hint is not None
            and ctx.current_mid_hint > 0):
        diff_pct = abs(ctx.limit_price - ctx.current_mid_hint) / ctx.current_mid_hint
        if diff_pct >= stale_threshold:
            # Direction matters — selling above ask = order won't fill;
            # selling below bid = filling at terrible price.
            direction = "above" if ctx.limit_price > ctx.current_mid_hint else "below"
            consequence = (
                "won't fill (market won't pay your asking price)"
                if (direction == "above" and ctx.action == "SELL_OPEN")
                else "will fill at a worse price than market mid"
            )
            findings.append(TradeValidation(
                severity=SEV_WARN,
                reason=(
                    f"Limit ${ctx.limit_price:.2f} is {diff_pct*100:.0f}% "
                    f"{direction} current mid ${ctx.current_mid_hint:.2f} — likely stale"
                ),
                detail=(
                    f"Chain mid moved to ${ctx.current_mid_hint:.2f} since the "
                    f"order was placed. At ${ctx.limit_price:.2f}, the order "
                    f"{consequence}. Refresh the quote and adjust the limit "
                    f"closer to mid before re-submitting."
                ),
                rule_id="STALE_LIMIT_PRICE",
            ))

    # Sort: BLOCK first, then WARN, then OK
    findings.sort(key=lambda f: _SEVERITY_RANK.get(f.severity, 9))
    return findings


def has_blockers(findings: list[TradeValidation]) -> bool:
    """True if any BLOCK-severity finding exists."""
    return any(f.severity == SEV_BLOCK for f in findings)


def block_summary(findings: list[TradeValidation]) -> str:
    """One-line summary of BLOCK findings — used in compact renderers."""
    blocks = [f for f in findings if f.severity == SEV_BLOCK]
    if not blocks:
        return ""
    if len(blocks) == 1:
        return f"🚫 {blocks[0].reason}"
    return f"🚫 {len(blocks)} BLOCKs: " + " · ".join(b.rule_id for b in blocks)


def warn_summary(findings: list[TradeValidation]) -> str:
    """One-line summary of WARN findings."""
    warns = [f for f in findings if f.severity == SEV_WARN]
    if not warns:
        return ""
    if len(warns) == 1:
        return f"⚠️ {warns[0].reason}"
    return f"⚠️ {len(warns)} WARNs: " + " · ".join(w.rule_id for w in warns)


# ─────────────────────────────────────────────────────────────────────────────
# Context builder — bridge from snapshot shape → PreTradeContext
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(v) -> Optional[date]:
    """Accept date, ISO string, or None."""
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            y, m, d = v.split("-")
            return date(int(y), int(m), int(d))
        except (ValueError, IndexError):
            return None
    return None


def build_context_from_snapshot(
    snapshot_data: dict,
    *,
    ticker: str,
    strike: float,
    expiration,
    option_type: str,
    action: str = "SELL_OPEN",
    quantity: int = 1,
    limit_price: Optional[float] = None,
    stress_coverage: Optional[float] = None,
    analytics: Optional[dict] = None,
    recommendations_list: Optional[list] = None,
) -> PreTradeContext:
    """Construct a PreTradeContext from the briefing's snapshot_data shape.

    Pulls spot/RSI/SMA/IV/earnings/S-R from snapshot_data['technicals'][TICKER],
    pulls existing positions from snapshot_data['positions'], computes the
    obligation-by-expiration map, and threads through analytics if available.
    """
    tkr = (ticker or "").upper()
    exp = _parse_date(expiration)
    technicals = snapshot_data.get("technicals", {}) or {}
    tech = technicals.get(tkr) or technicals.get(ticker) or {}
    balance = snapshot_data.get("balance", {}) or {}
    positions = snapshot_data.get("positions", []) or []

    # NLV / cash
    nlv = float(balance.get("accountValue") or balance.get("netValue") or 0)
    cash = float(balance.get("cash") or balance.get("cashBalance") or 0)

    # Stress coverage: prefer explicit param, then analytics dict, else compute
    sc = stress_coverage
    if sc is None and isinstance(analytics, dict):
        sc_obj = analytics.get("stress_coverage")
        if isinstance(sc_obj, dict):
            sc = sc_obj.get("coverage_ratio")
        elif sc_obj is not None:
            sc = getattr(sc_obj, "coverage_ratio", None)

    # Earnings date — embedded in technicals if scout/snapshot fetched it
    earn_str = tech.get("earnings_date") or tech.get("next_earnings")
    earnings_date = _parse_date(earn_str)
    # Also accept snapshot-level earnings_calendar
    if earnings_date is None:
        cal = snapshot_data.get("earnings_calendar", {}) or {}
        earnings_date = _parse_date(cal.get(tkr))

    # Existing positions on the same ticker
    existing_short_puts = []
    existing_long_puts = []
    existing_short_calls = []
    held_shares = 0
    obligation_by_exp: dict = {}
    for p in positions:
        if (p.get("symbol") or "").startswith(f"{tkr}_"):
            pass  # symbol matching handled below per-asset
        if p.get("assetType") == "EQUITY":
            sym = (p.get("symbol") or "").upper()
            if sym == tkr:
                held_shares += int(float(p.get("qty") or 0))
        elif p.get("assetType") == "OPTION":
            qty = float(p.get("qty") or 0)
            opt = (p.get("type") or p.get("option_type") or "").upper()
            ul = (p.get("underlying") or "").upper()
            try:
                p_strike = float(p.get("strike") or 0)
            except (TypeError, ValueError):
                continue
            p_exp = _parse_date(p.get("expiration"))
            entry = {"strike": p_strike, "expiration": p.get("expiration"), "qty": abs(qty)}
            if ul == tkr and opt == "PUT" and qty < 0:
                existing_short_puts.append(entry)
            elif ul == tkr and opt == "PUT" and qty > 0:
                existing_long_puts.append(entry)
            elif ul == tkr and opt == "CALL" and qty < 0:
                existing_short_calls.append(entry)
            # Aggregate ALL short-put obligation by expiration (across tickers)
            if opt == "PUT" and qty < 0 and p_strike > 0 and p_exp is not None:
                obligation_by_exp[p_exp] = obligation_by_exp.get(p_exp, 0.0) + p_strike * abs(qty) * 100

    # Third-party rating tier
    tier = None
    for r in (recommendations_list or []):
        if isinstance(r, dict) and (r.get("ticker") or "").upper() == tkr:
            rt = r.get("rating_tier")
            if rt is not None:
                try:
                    tier = int(rt)
                except (TypeError, ValueError):
                    pass
            break

    return PreTradeContext(
        ticker=tkr,
        strike=float(strike),
        expiration=exp,
        option_type=(option_type or "PUT").upper(),
        action=(action or "SELL_OPEN").upper(),
        quantity=quantity,
        limit_price=limit_price,
        spot=tech.get("spot") or tech.get("price"),
        rsi=tech.get("rsi_14"),
        sma_50=tech.get("sma_50"),
        sma_200=tech.get("sma_200"),
        iv_rank=tech.get("iv_rank"),
        earnings_date=earnings_date,
        sr_payload=tech.get("support_resistance"),
        nlv=nlv,
        cash=cash,
        stress_coverage=sc,
        existing_short_puts=existing_short_puts,
        existing_long_puts=existing_long_puts,
        existing_short_calls=existing_short_calls,
        held_shares=held_shares,
        obligation_by_expiration=obligation_by_exp,
        rating_tier=tier,
    )


def format_findings_md(
    findings: list[TradeValidation],
    *,
    trade_label: str,
    show_passing: bool = False,
) -> list[str]:
    """Render findings as markdown lines suitable for the briefing.

    Format:
      ### Pre-trade validation: TICKER $X P EXP
      - 🚫 **BLOCK** — Earnings inside contract window
        _Detail line..._
      - ⚠️ **WARN** — Riskier than existing position
        _Detail line..._
    """
    lines: list[str] = []
    if not findings:
        if show_passing:
            lines.append(f"✅ **{trade_label}** — all discipline checks pass")
        return lines

    lines.append(f"**Pre-trade validation: {trade_label}**")
    for f in findings:
        emoji = _SEVERITY_EMOJI.get(f.severity, "•")
        lines.append(f"- {emoji} **{f.severity}** — {f.reason}")
        # Wrap detail in italics for visual hierarchy
        if f.detail:
            lines.append(f"  _{f.detail}_")
    return lines
