"""
Long-term opportunity advisor — surfaces equity ADD/TRIM/EXIT/HOLD plus multi-month
option trade ideas (LEAPs, long-dated CSPs, calendars, dividends).

Inputs: live positions + third-party recs + RSI + IV rank + drawdown + 200-SMA.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional

# ONE Moneyvest anchor grammar (2026-08-06 attribution decoration) — the
# single source of truth is moneyvest_chip.format_mv_anchor in the
# daily-portfolio-briefing skill. This module also runs standalone, so the
# fallback mirrors that grammar exactly ("💰 MV Light Buy $182").
try:
    from analysis.moneyvest_chip import format_mv_anchor as _fmt_mv_anchor
except ImportError:  # pragma: no cover - standalone runs
    def _fmt_mv_anchor(label: str, price: float) -> str:
        return f"💰 MV {label} ${price:,.0f}"

try:
    from analysis.moneyvest_chip import format_hb_ladder as _fmt_hb_ladder
except ImportError:  # pragma: no cover - standalone runs (mirror the grammar)
    def _fmt_hb_ladder(lb, hb):
        try:
            lb_f = float(lb) if lb else None
            hb_f = float(hb) if hb else None
        except (TypeError, ValueError):
            return None
        if not lb_f or not hb_f or hb_f >= lb_f:
            return None
        return (f"Ladder: 💰 LB ${lb_f:,.0f} (first tranche) → "
                f"HB ${hb_f:,.0f} (scale-in)")


def _safe_shares(dollars: float, spot: float | None) -> int:
    """Fail-open share count. Returns 0 when spot is unusable (NaN, None, ≤0).

    Was the source of `TypeError: cannot convert float NaN to integer`
    when yfinance returned NaN spot on holidays / stale bars.
    """
    if spot is None:
        return 0
    try:
        if math.isnan(spot) or spot <= 0:
            return 0
        return int(dollars / spot)
    except (TypeError, ValueError):
        return 0


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
                concrete_trade=f"BUY ~$5,000 of {ticker} (~{_safe_shares(5000, spot)} shares @ ~${spot:.2f})",
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
    sr_levels: Optional[list] = None,
    mv_ladder: Optional[dict] = None,
    hb_ladder: bool = False,
) -> Optional[LongTermOpportunity]:
    """Evaluate longer-dated option ideas: LEAP / long-dated CSP / diagonal / dividend.

    ``sr_levels`` is an optional list of support/resistance level dicts (the
    same shape returned by ``support_resistance.SupportResistance.to_dict()``
    under ``supports`` + ``resistances``). When provided, LT_CSP strike
    selection snaps to a real support cluster within the 7-13% OTM band
    instead of the legacy "spot × 0.90, round to $5" heuristic.

    ``mv_ladder`` (task #46) is an optional Moneyvest shopping-list row
    ({"light_buy": 196.0, "heavy_buy": ..., "no_brainer": ...}). When a
    ladder price falls inside the same 7-13% OTM discipline band, the CSP
    strike anchors at/just-below it (Light Buy preferred) and the rationale
    renders "strike anchored to 💰 Light Buy $196". Anchor selection ONLY —
    it never widens the band and never overrides RSI/chase/earnings gates
    (those run in the calling pipeline).

    ``hb_ladder`` (USER DECISION 2026-08-06, feature 2 — gated by
    ``moneyvest.hb_ladder.enabled``; default False = legacy LB-first
    behavior byte-identical). JUDGMENT CALL, documented: the LT_CSP band
    (7-13% OTM) is by construction a deep-pullback SECOND-tranche
    acquisition zone — the shallower first tranche is the new_ideas
    PULLBACK CSP / equity starter. Legacy iteration order (LB → HB → NB,
    first in-band wins) let Light Buy SHADOW Heavy Buy even when both sat
    in the band, so assignment anchored at the first-tranche price. With
    the flag on, when BOTH LB and HB fall inside the band the DEEPER rung
    (Heavy Buy) wins and the anchor note names it; No-Brainer stays a
    last-resort fallback. When the anchor is Light Buy and a real HB
    exists below it, the rationale appends the LB→HB scale-in ladder line
    so the second rung is always visible.
    """
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
            # "target δ~0.70" is the SELECTION target, not a measurement —
            # the briefing pipeline replaces it with the MEASURED chain delta
            # (or demotes the ticket when no live quote exists). Rendering
            # "delta ~0.70" here read as a measured value (rule #19 bug,
            # 2026-08-07 MELI LEAP).
            concrete_trade=f"BUY 1× {ticker} ${leap_strike:.0f}C ~365 DTE (ITM, target δ~0.70)",
            rationale=f"Stock-replacement LEAP: deep-ITM call captures most upside for ~{leap_premium_est/spot*100:.0f}% "
                      f"of the cost of buying shares. Time decay is slow on long-dated ITM. Cap: small.",
            yield_or_cost=f"~${leap_premium_est*100:.0f} per contract (rule-of-thumb est — live quote required before placing) — "
                          f"leverages that capital into ${spot*100:,.0f} of exposure",
            source="recommendation-list-fetcher + yfinance IV + 200-SMA",
        )

    # LONG-DATED CSP — willing-to-acquire + elevated IV + cash on hand
    if (rec in ("BUY", "STRONG_BUY", "HOLD", "OUTPERFORM")
        and iv_rank is not None and iv_rank > 50
        and has_cash):
        # Default: 10% OTM, rounded to nearest $5 (the legacy heuristic).
        csp_strike = round(spot * 0.90 / 5) * 5
        strike_anchor_note = ""
        # Moneyvest buy-ladder anchoring (task #46): when a ladder price
        # falls inside the 7-13% OTM discipline band, anchor the strike
        # at/just-below it — assignment then happens at a level the
        # valuation model already calls a buy. Light Buy preferred, then
        # Heavy Buy, then No-Brainer. Takes precedence over the S/R anchor
        # (a valuation-model level beats a chart cluster for PATIENT
        # capital); never widens the band.
        mv_anchored = False
        if mv_ladder:
            band_lo = spot * 0.87
            band_hi = spot * 0.93

            def _rung(field):
                try:
                    v = mv_ladder.get(field)
                    v = float(v) if v else None
                except (TypeError, ValueError):
                    v = None
                return v

            _lb, _hb, _nb = (_rung("light_buy"), _rung("heavy_buy"),
                             _rung("no_brainer"))
            in_band = [(p, lbl) for p, lbl in
                       ((_lb, "Light Buy"), (_hb, "Heavy Buy"),
                        (_nb, "No-Brainer Buy"))
                       if p and band_lo <= p <= band_hi]
            chosen = None
            if in_band:
                if hb_ladder:
                    # Feature 2 (2026-08-06) — deeper-rung preference: the
                    # LT_CSP band is a second-tranche zone, so when both LB
                    # and HB sit in-band, anchor at the DEEPER Heavy Buy
                    # (see docstring judgment call). NB stays last resort.
                    non_nb = [c for c in in_band if c[1] != "No-Brainer Buy"]
                    chosen = (min(non_nb, key=lambda c: c[0]) if non_nb
                              else in_band[0])
                else:
                    # Legacy: LB → HB → NB, first in-band wins.
                    chosen = in_band[0]
            if chosen:
                _mv_price, _mv_label = chosen
                # At/just-below the ladder price ($5 strike granularity).
                import math as _math
                csp_strike = int(_math.floor(_mv_price / 5) * 5)
                strike_anchor_note = (
                    f" Strike anchored to "
                    f"{_fmt_mv_anchor(_mv_label, _mv_price)}.")
                # When the anchor is the FIRST-tranche rung and a real
                # deeper rung exists, surface the scale-in ladder so the
                # second tranche is always visible (real values only).
                if hb_ladder and _mv_label == "Light Buy":
                    _lad = _fmt_hb_ladder(_lb, _hb)
                    if _lad:
                        strike_anchor_note += f" {_lad}."
                mv_anchored = True
        # S/R-aware refinement (hard rule #20): when a real support cluster
        # sits within the 7-13% OTM band, snap the strike to it. Assignment
        # then puts you at a chart-relevant level rather than a round-number
        # spot * 0.90 estimate.
        if sr_levels and not mv_anchored:
            band_lo = spot * 0.87
            band_hi = spot * 0.93
            in_band = [
                lv for lv in sr_levels
                if isinstance(lv, dict)
                and lv.get("side") == "support"
                and band_lo <= float(lv.get("price", 0)) <= band_hi
                and float(lv.get("strength", 0)) >= 1.5
            ]
            if in_band:
                anchor = max(in_band, key=lambda lv: float(lv.get("strength", 0)))
                anchor_price = float(anchor["price"])
                # Round anchor to the nearest $5 to match listed-strike granularity.
                csp_strike = round(anchor_price / 5) * 5
                touches = int(anchor.get("touches", 1))
                confluence = anchor.get("confluence") or []
                source = anchor.get("source", "swing")
                # Dedupe: source must not also appear in confluence ("sma_50 + sma_50").
                conf_clean = [c for c in confluence if c != source]
                conf_phrase = f" + {', '.join(conf_clean)}" if conf_clean else ""
                touches_phrase = f"{touches} touches" if touches >= 2 else "1 touch"
                strike_anchor_note = (
                    f" Strike anchored to ${csp_strike:.0f} support ({source}{conf_phrase}, "
                    f"{touches_phrase})."
                )
        csp_premium_est = spot * 0.025  # rule-of-thumb 60-90 DTE 0.20 delta
        return LongTermOpportunity(
            kind="LONG_DATED_CSP",
            ticker=ticker,
            trigger_reasons=[f"third-party {rec}", f"IV rank {iv_rank:.0f} (elevated)", "willing-to-own at strike"],
            concrete_trade=f"SELL 1× {ticker} ${csp_strike:.0f}P ~75 DTE",
            rationale=f"Patient capital trade: elevated IV {iv_rank:.0f} + 75-DTE horizon = fat premium. "
                      f"If assigned, you own at ${csp_strike:.0f} (effective basis ${csp_strike - csp_premium_est:.0f}).{strike_anchor_note}",
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
    sr_by_ticker: Optional[dict] = None,
    mv_ladders: Optional[dict] = None,
    hb_ladder: bool = False,
) -> list:
    """
    Run the advisor across the universe (held + recommended-but-not-held tickers).

    ``sr_by_ticker`` optionally maps uppercase ticker → support_resistance dict
    (the snapshot's ``to_dict()`` shape). When supplied, LT_CSP strike picking
    anchors to a real support cluster rather than the spot×0.90 heuristic
    (hard rule #20 — S/R discipline).

    ``mv_ladders`` (task #46) optionally maps uppercase ticker → Moneyvest
    shopping-list row; LT_CSP strikes anchor at/just-below the Light Buy
    price when it falls in the discipline band.

    Returns a list of LongTermOpportunity objects, sorted by priority.
    """
    opportunities = []
    sr_by_ticker = sr_by_ticker or {}
    mv_ladders = mv_ladders or {}

    def _sr_levels_for(t: str) -> Optional[list]:
        """Concatenate supports + resistances into one list for the option
        evaluator (the CSP path filters to side=='support' itself)."""
        sr = sr_by_ticker.get((t or "").upper())
        if not isinstance(sr, dict):
            return None
        out: list = []
        out.extend(sr.get("supports") or [])
        out.extend(sr.get("resistances") or [])
        return out or None

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
            sr_levels=_sr_levels_for(ticker),
            mv_ladder=mv_ladders.get((ticker or "").upper()),
            hb_ladder=hb_ladder,
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
                concrete_trade=f"OPEN position in {ticker} ~$5,000 (~{_safe_shares(5000, spot)} shares @ ~${spot:.2f})",
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
