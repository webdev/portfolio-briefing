"""
Long-term opportunity advisor — surfaces equity ADD/TRIM/EXIT/HOLD plus multi-month
option trade ideas (LEAPs, long-dated CSPs, calendars, dividends).

Inputs: live positions + third-party recs + RSI + IV rank + drawdown + 200-SMA.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class LongTermOpportunity:
    kind: str  # ADD | TRIM | EXIT | HOLD | LEAP_CALL | LONG_DATED_CSP | DIAGONAL | DIVIDEND
    ticker: str
    trigger_reasons: list = field(default_factory=list)
    concrete_trade: str = ""
    rationale: str = ""
    yield_or_cost: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_equity_action(
    ticker: str,
    weight_pct: float,
    target_weight_pct: float,
    rsi: Optional[float],
    drawdown_pct: Optional[float],   # current price vs 52w high; 10 = 10% off high
    spot: float,
    sma_200: Optional[float],
    third_party_rec: Optional[str],  # "BUY", "HOLD", "SELL", or None
    third_party_change: Optional[str] = None,  # "UPGRADE", "DOWNGRADE", or None
) -> Optional[LongTermOpportunity]:
    """Evaluate ADD / TRIM / EXIT / HOLD on a single equity holding."""
    rec = (third_party_rec or "").upper()
    triggers = []

    # EXIT — strong sell signals
    if rec in ("SELL", "UNDERPERFORM", "STRONG_SELL"):
        triggers.append(f"third-party rec: {rec}")
        return LongTermOpportunity(
            kind="EXIT",
            ticker=ticker,
            trigger_reasons=triggers,
            concrete_trade=f"SELL {ticker} — exit position",
            rationale=f"Third-party downgrade to {rec}. Position no longer earns its weight.",
            source="recommendation-list-fetcher",
        )
    if drawdown_pct is not None and drawdown_pct > 30 and rec not in ("BUY", "STRONG_BUY"):
        triggers.append(f"drawdown {drawdown_pct:.0f}% from 52w high without rec support")
        return LongTermOpportunity(
            kind="EXIT",
            ticker=ticker,
            trigger_reasons=triggers,
            concrete_trade=f"REVIEW {ticker} — consider exit",
            rationale=f"Down {drawdown_pct:.0f}% from peak with no third-party support. "
                      "Thesis may be breaking — review fundamentals before next move.",
            source="yfinance drawdown + recommendation-list-fetcher",
        )

    # TRIM — overweight + extended OR downgrade
    if third_party_change == "DOWNGRADE":
        triggers.append("third-party downgrade")
    if weight_pct > target_weight_pct * 1.5:
        triggers.append(f"weight {weight_pct:.1f}% > {target_weight_pct*1.5:.1f}% (1.5× target)")
    if rsi is not None and rsi > 70 and weight_pct > target_weight_pct:
        triggers.append(f"RSI {rsi:.0f} extended + overweight")
    if triggers:
        sell_pct = max(0.10, (weight_pct - target_weight_pct) / weight_pct)
        return LongTermOpportunity(
            kind="TRIM",
            ticker=ticker,
            trigger_reasons=triggers,
            concrete_trade=f"TRIM {ticker} ~{sell_pct*100:.0f}% of position",
            rationale="Position is overweight or technical/rec signals weakening. "
                      "Take some risk off the table.",
            source="position weight + rsi + third-party rec",
        )

    # ADD — buy on dip with rec support
    if rec in ("BUY", "STRONG_BUY", "OUTPERFORM", "TOP_15"):
        if rsi is not None and rsi < 35:
            triggers.append(f"RSI {rsi:.0f} oversold")
        if drawdown_pct is not None and drawdown_pct > 10:
            triggers.append(f"drawdown {drawdown_pct:.0f}% from 52w high")
        if sma_200 and abs(spot - sma_200) / sma_200 < 0.05:
            triggers.append(f"price within 5% of 200-SMA (${sma_200:.0f})")
        if triggers and weight_pct < 6:
            return LongTermOpportunity(
                kind="ADD",
                ticker=ticker,
                trigger_reasons=triggers + [f"third-party {rec}"],
                concrete_trade=f"BUY ~$5,000 of {ticker} (~{int(5000/spot)} shares @ ~${spot:.2f})",
                rationale=f"Pullback in a third-party {rec} name. Weight {weight_pct:.1f}% below "
                          f"6% target — room to scale in.",
                yield_or_cost=f"$5K initial; can scale to ${target_weight_pct/100*1000000:.0f} target on further weakness",
                source="recommendation-list-fetcher + yfinance technicals",
            )

    # HOLD — no signal
    return None


def evaluate_options_idea(
    ticker: str,
    weight_pct: float,
    spot: float,
    rsi: Optional[float],
    iv_rank: Optional[float],
    sma_200: Optional[float],
    third_party_rec: Optional[str],
    has_cash: bool = True,
) -> Optional[LongTermOpportunity]:
    """Evaluate longer-dated option ideas: LEAP / long-dated CSP / diagonal / dividend."""
    rec = (third_party_rec or "").upper()

    # LEAP CALL — high-conviction + low IV + near 200-SMA
    if (rec in ("BUY", "STRONG_BUY", "OUTPERFORM")
        and iv_rank is not None and iv_rank < 30
        and sma_200 and abs(spot - sma_200) / sma_200 < 0.08
        and weight_pct < 4):
        leap_strike = round(spot * 0.85 / 5) * 5  # ITM by ~15% for stock replacement (delta ~0.70)
        leap_premium_est = spot * 0.20  # rough rule-of-thumb for 1y ITM call
        return LongTermOpportunity(
            kind="LEAP_CALL",
            ticker=ticker,
            trigger_reasons=[f"third-party {rec}", f"IV rank {iv_rank:.0f} (cheap)", "near 200-SMA"],
            concrete_trade=f"BUY 1× {ticker} ${leap_strike:.0f}C ~365 DTE (ITM, delta ~0.70)",
            rationale=f"Stock-replacement LEAP: deep-ITM call captures most upside for ~{leap_premium_est/spot*100:.0f}% "
                      f"of the cost of buying shares. Time decay is slow on long-dated ITM. Cap: small.",
            yield_or_cost=f"~${leap_premium_est*100:.0f} per contract — leverages ~$5K of capital into ${spot*100:,.0f} of exposure",
            source="recommendation-list-fetcher + yfinance IV + 200-SMA",
        )

    # LONG-DATED CSP — willing-to-acquire + elevated IV + cash on hand
    if (rec in ("BUY", "STRONG_BUY", "HOLD", "OUTPERFORM")
        and iv_rank is not None and iv_rank > 50
        and has_cash):
        csp_strike = round(spot * 0.90 / 5) * 5  # 10% below spot
        csp_premium_est = spot * 0.025  # rule-of-thumb 60-90 DTE 0.20 delta
        return LongTermOpportunity(
            kind="LONG_DATED_CSP",
            ticker=ticker,
            trigger_reasons=[f"third-party {rec}", f"IV rank {iv_rank:.0f} (elevated)", "willing-to-own at strike"],
            concrete_trade=f"SELL 1× {ticker} ${csp_strike:.0f}P ~75 DTE",
            rationale=f"Patient capital trade: elevated IV {iv_rank:.0f} + 75-DTE horizon = fat premium. "
                      f"If assigned, you own at ${csp_strike:.0f} (effective basis ${csp_strike - csp_premium_est:.0f}).",
            yield_or_cost=f"~${csp_premium_est*100:.0f} premium · ~{csp_premium_est/csp_strike*365/75*100:.0f}% annualized · ${csp_strike*100:,.0f} cash collateral",
            source="recommendation-list-fetcher + yfinance IV",
        )

    return None


def generate_long_term_opportunities(
    positions_by_ticker: dict,  # {ticker: {weight_pct, spot, ...}}
    rsi_values: dict,
    iv_ranks: dict,
    third_party_recs: dict,    # {ticker: "BUY"/"HOLD"/"SELL"}
    drawdown_pcts: dict,        # {ticker: pct off 52w high}
    sma_200_values: dict,
    target_weights: dict,        # {ticker: ideal % NLV}
    has_cash: bool = True,
) -> list:
    """
    Run the advisor across the universe (held + recommended-but-not-held tickers).

    Returns a list of LongTermOpportunity objects, sorted by priority.
    """
    opportunities = []

    # Held positions: evaluate ADD/TRIM/EXIT/HOLD
    for ticker, info in positions_by_ticker.items():
        weight_pct = info.get("weight_pct", 0)
        spot = info.get("spot", 0)
        target = target_weights.get(ticker, 5.0)  # default 5% target
        rsi = rsi_values.get(ticker)
        iv = iv_ranks.get(ticker)
        rec = third_party_recs.get(ticker)
        dd = drawdown_pcts.get(ticker)
        sma200 = sma_200_values.get(ticker)

        equity_action = evaluate_equity_action(
            ticker=ticker, weight_pct=weight_pct, target_weight_pct=target,
            rsi=rsi, drawdown_pct=dd, spot=spot, sma_200=sma200,
            third_party_rec=rec,
        )
        if equity_action:
            opportunities.append(equity_action)

        # Also evaluate options ideas on held names
        opt_idea = evaluate_options_idea(
            ticker=ticker, weight_pct=weight_pct, spot=spot,
            rsi=rsi, iv_rank=iv, sma_200=sma200,
            third_party_rec=rec, has_cash=has_cash,
        )
        if opt_idea:
            opportunities.append(opt_idea)

    # Recommended-but-not-held tickers: evaluate ADD signals
    held_tickers = set(positions_by_ticker.keys())
    for ticker, rec in third_party_recs.items():
        if ticker in held_tickers:
            continue
        rec_upper = (rec or "").upper()
        if rec_upper not in ("BUY", "STRONG_BUY", "OUTPERFORM", "TOP_15"):
            continue
        spot = positions_by_ticker.get(ticker, {}).get("spot")  # may be None
        if not spot:
            continue
        rsi = rsi_values.get(ticker)
        if rsi is None:
            continue
        if rsi < 40:
            opportunities.append(LongTermOpportunity(
                kind="ADD",
                ticker=ticker,
                trigger_reasons=[f"third-party {rec_upper}", f"RSI {rsi:.0f}",
                                  "not yet held"],
                concrete_trade=f"OPEN position in {ticker} ~$5,000 (~{int(5000/spot)} shares @ ~${spot:.2f})",
                rationale=f"Third-party {rec_upper} on a name not in portfolio. RSI {rsi:.0f} suggests "
                          "favorable entry timing.",
                yield_or_cost="$5K initial position; scale based on continued thesis support",
                source="recommendation-list-fetcher + yfinance RSI",
            ))

    # Sort: EXIT first (urgent), then TRIM, then ADD, then options ideas, then HOLD
    priority = {"EXIT": 1, "TRIM": 2, "ADD": 3, "LEAP_CALL": 4,
                "LONG_DATED_CSP": 5, "DIAGONAL": 6, "DIVIDEND": 7, "HOLD": 8}
    opportunities.sort(key=lambda o: priority.get(o.kind, 9))
    return opportunities


def format_opportunity_md(op: LongTermOpportunity, n: int) -> list[str]:
    """Render a single opportunity as markdown lines."""
    emoji = {
        "ADD": "📈", "TRIM": "✂️", "EXIT": "🚪", "HOLD": "🤝",
        "LEAP_CALL": "🎯", "LONG_DATED_CSP": "💎", "DIAGONAL": "📐", "DIVIDEND": "💵",
    }.get(op.kind, "•")
    out = [f"### {emoji} {n}. {op.kind.replace('_', ' ')} · `{op.ticker}`", ""]
    out.append(f"**Trade:** {op.concrete_trade}")
    out.append("")
    if op.trigger_reasons:
        out.append(f"- **Triggers:** {'; '.join(op.trigger_reasons)}")
    if op.rationale:
        out.append(f"- **Rationale:** {op.rationale}")
    if op.yield_or_cost:
        out.append(f"- **Yield/Cost:** {op.yield_or_cost}")
    if op.source:
        out.append(f"- **Source:** {op.source}")
    out.append("")
    return out
