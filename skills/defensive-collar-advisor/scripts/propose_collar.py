"""
Defensive collar proposer.

Inputs: a position with covered call + portfolio context (NLV, IV rank, earnings).
Outputs: a 3-leg CollarProposal — buy-to-close existing call, sell-to-open higher
call, buy long put.

The decision parameters live in references/collar_decision_matrix.yaml so they
can be tuned without touching code.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Callable

import yaml

# Defaults (used if YAML cannot be loaded)
_DEFAULTS = {
    "qualify_concentration_pct": 10.0,
    "qualify_tax_exposure_pct_of_nlv": 1.5,
    "new_call_default_otm_pct": 15.0,
    "new_call_aggressive_otm_pct": 20.0,
    "strike_interval_default": 5.0,
    "put_otm_low_iv": 5.0,
    "put_otm_normal": 10.0,
    "put_otm_high_iv": 15.0,
    "put_otm_earnings": 7.0,
    "earnings_window_days": 14,
    "skip_put_iv_threshold": 70,
    "preferred_dte_min": 60,
    "preferred_dte_max": 180,
    "max_net_debit_pct_of_nlv": 1.5,
    "max_put_cost_pct_of_position": 5.0,
}


@dataclass
class CollarLeg:
    action: str  # BTC, STO, BTO, STC
    type: str    # CALL or PUT
    strike: float
    expiration: str  # ISO YYYY-MM-DD
    limit: float
    contracts: int
    price_source: str = "estimated"  # "live_chain" or "estimated"


@dataclass
class CollarProposal:
    qualified: bool
    ticker: str
    trigger_reasons: list = field(default_factory=list)
    skip_reasons: list = field(default_factory=list)
    proposed_legs: list = field(default_factory=list)
    net_cash: float = 0.0  # positive = credit, negative = debit
    tax_avoided_if_no_assignment: float = 0.0
    max_loss: float = 0.0
    max_gain_at_cap: float = 0.0
    yield_summary: dict = field(default_factory=dict)
    explanation: str = ""


def load_decision_matrix(path: Optional[Path] = None) -> dict:
    """Load the YAML decision matrix; fall back to defaults on error."""
    if path is None:
        path = Path(__file__).parent.parent / "references" / "collar_decision_matrix.yaml"
    try:
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        # Flatten the YAML into the simple dict the proposer uses
        flat = dict(_DEFAULTS)
        if "triggers" in raw and "qualify_when" in raw["triggers"]:
            for cond in raw["triggers"]["qualify_when"]:
                for k, v in cond.items():
                    if k == "concentration_pct" and isinstance(v, str):
                        flat["qualify_concentration_pct"] = float(v.lstrip(">").strip())
                    elif k == "tax_exposure_pct_of_nlv" and isinstance(v, str):
                        flat["qualify_tax_exposure_pct_of_nlv"] = float(v.lstrip(">").strip())
        if "new_call_strike" in raw:
            flat["new_call_default_otm_pct"] = raw["new_call_strike"].get(
                "default_otm_pct", flat["new_call_default_otm_pct"])
            flat["new_call_aggressive_otm_pct"] = raw["new_call_strike"].get(
                "aggressive_bullish_otm_pct", flat["new_call_aggressive_otm_pct"])
            flat["strike_interval_default"] = raw["new_call_strike"].get(
                "strike_interval_default", flat["strike_interval_default"])
            flat["strike_interval_overrides"] = raw["new_call_strike"].get(
                "strike_interval_overrides", {})
        if "put_strike" in raw and "iv_rank_bands" in raw["put_strike"]:
            for band in raw["put_strike"]["iv_rank_bands"]:
                cond = band.get("condition", "")
                pct = band.get("put_otm_pct")
                if pct is None:
                    continue
                if "<" in cond:
                    flat["put_otm_low_iv"] = pct
                elif ">" in cond:
                    flat["put_otm_high_iv"] = pct
                else:
                    flat["put_otm_normal"] = pct
            flat["earnings_window_days"] = raw["put_strike"].get(
                "earnings_window_days", flat["earnings_window_days"])
            flat["put_otm_earnings"] = raw["put_strike"].get(
                "earnings_put_otm_pct", flat["put_otm_earnings"])
        if "decision_skip_put_leg" in raw:
            for cond in raw["decision_skip_put_leg"].get("conditions", []):
                if "iv_rank" in cond and isinstance(cond["iv_rank"], str):
                    flat["skip_put_iv_threshold"] = float(cond["iv_rank"].lstrip(">").strip())
        if "target_expiration" in raw:
            flat["preferred_dte_min"] = raw["target_expiration"].get(
                "preferred_dte_min", flat["preferred_dte_min"])
            flat["preferred_dte_max"] = raw["target_expiration"].get(
                "preferred_dte_max", flat["preferred_dte_max"])
        if "cost_caps" in raw:
            flat["max_net_debit_pct_of_nlv"] = raw["cost_caps"].get(
                "max_net_debit_pct_of_nlv", flat["max_net_debit_pct_of_nlv"])
            flat["max_put_cost_pct_of_position"] = raw["cost_caps"].get(
                "max_put_cost_pct_of_position", flat["max_put_cost_pct_of_position"])
        return flat
    except (FileNotFoundError, yaml.YAMLError):
        return dict(_DEFAULTS)


def _round_to_strike(price: float, interval: float = 5.0) -> float:
    """Round to the nearest strike interval (typically $5)."""
    return round(price / interval) * interval


def _select_put_otm_pct(iv_rank: float, days_to_earnings: Optional[int], rules: dict) -> float:
    """Select put strike % below spot based on IV rank and earnings proximity."""
    if (days_to_earnings is not None
            and 0 <= days_to_earnings <= rules["earnings_window_days"]):
        return rules["put_otm_earnings"]
    if iv_rank < 30:
        return rules["put_otm_low_iv"]
    if iv_rank > 60:
        return rules["put_otm_high_iv"]
    return rules["put_otm_normal"]


def propose_collar(
    ticker: str,
    spot: float,
    shares: int,
    cost_basis: float,
    nlv: float,
    concentration_pct: float,
    is_core: bool,
    has_short_call: bool,
    current_call_strike: Optional[float],
    current_call_expiration: Optional[str],
    current_call_mid: Optional[float],
    current_call_contracts: int,
    iv_rank: float,
    days_to_earnings: Optional[int] = None,
    ltcg_rate: float = 0.238,
    aggressive_mode: bool = False,
    decision_matrix: Optional[dict] = None,
    chain_provider: Optional[Callable[[str, str, float, str], Optional[dict]]] = None,
) -> CollarProposal:
    """
    Decide whether and how to propose a defensive collar for a position.

    Args:
        ticker: Stock symbol
        spot: Current stock price
        shares: Number of shares owned
        cost_basis: Original cost per share
        nlv: Portfolio net liquidation value
        concentration_pct: Position size as % of NLV
        is_core: Whether classified as core/long-term holding
        has_short_call: Whether position has existing short call
        current_call_strike: Strike of current short call
        current_call_expiration: Expiration of current short call (ISO YYYY-MM-DD)
        current_call_mid: Current mid-price of short call
        current_call_contracts: Number of contracts for current short call
        iv_rank: IV rank percentile [0-100]
        days_to_earnings: Days until next earnings (None if not near)
        ltcg_rate: Long-term capital gains tax rate
        aggressive_mode: If True, use aggressive parameters (tighter floor)
        decision_matrix: Decision rules (auto-loads from YAML if None)
        chain_provider: Callable returning real chain data for (ticker, exp, strike, opt_type).
                       Signature: (ticker, expiration, strike, "CALL"|"PUT") -> {"bid", "mid", "ask"} or None

    Returns a CollarProposal with qualified=False if any qualifying trigger fails.
    """
    rules = decision_matrix or load_decision_matrix()
    proposal = CollarProposal(qualified=False, ticker=ticker)

    # ---- Qualification ----
    if not has_short_call:
        proposal.skip_reasons.append("no existing short call to convert")
        return proposal
    if not is_core:
        proposal.skip_reasons.append("position not classified as core (no embedded tax risk)")
        return proposal
    if concentration_pct < rules["qualify_concentration_pct"]:
        proposal.skip_reasons.append(
            f"concentration {concentration_pct:.1f}% below "
            f"{rules['qualify_concentration_pct']:.0f}% trigger"
        )
        return proposal

    # Compute embedded tax exposure
    if cost_basis and current_call_strike and current_call_strike > cost_basis:
        embedded_gain = (current_call_strike - cost_basis) * shares
        tax_exposure = embedded_gain * ltcg_rate
        tax_pct_nlv = (tax_exposure / nlv * 100) if nlv else 0
    else:
        embedded_gain = 0
        tax_exposure = 0
        tax_pct_nlv = 0

    if tax_pct_nlv < rules["qualify_tax_exposure_pct_of_nlv"]:
        proposal.skip_reasons.append(
            f"tax exposure {tax_pct_nlv:.1f}% NLV below trigger threshold "
            f"({rules['qualify_tax_exposure_pct_of_nlv']:.1f}%)"
        )
        return proposal

    proposal.qualified = True
    proposal.trigger_reasons = [
        "is_core",
        f"concentration_breach ({concentration_pct:.1f}% > {rules['qualify_concentration_pct']:.0f}%)",
        f"tax_exposure ({tax_pct_nlv:.1f}% NLV)",
    ]
    proposal.tax_avoided_if_no_assignment = tax_exposure

    # ---- Choose new call strike ----
    interval = rules.get("strike_interval_overrides", {}).get(
        ticker, rules["strike_interval_default"])
    otm_pct = (rules["new_call_aggressive_otm_pct"]
               if aggressive_mode else rules["new_call_default_otm_pct"])
    new_call_strike = _round_to_strike(spot * (1 + otm_pct / 100), interval)
    if current_call_strike and new_call_strike <= current_call_strike:
        # never below the current cap; bump to next interval
        new_call_strike = current_call_strike + interval

    # ---- Choose target expiration ----
    target_dte = max(rules["preferred_dte_min"],
                     min(rules["preferred_dte_max"],
                         (rules["preferred_dte_min"] + rules["preferred_dte_max"]) // 2))
    target_exp = (date.today() + timedelta(days=target_dte)).isoformat()

    # ---- Decide put leg ----
    skip_put = iv_rank > rules["skip_put_iv_threshold"]
    if skip_put:
        proposal.skip_reasons.append(
            f"IV rank {iv_rank:.0f} > {rules['skip_put_iv_threshold']:.0f} — puts too expensive; "
            f"proposing roll-up only"
        )

    put_strike = None
    if not skip_put:
        put_otm_pct = _select_put_otm_pct(iv_rank, days_to_earnings, rules)
        put_strike = _round_to_strike(spot * (1 - put_otm_pct / 100), interval)

    # ---- Build legs (populate mid prices from chain_provider if available) ----
    contracts = abs(int(current_call_contracts))
    legs: list = []

    # BTC leg: buy-to-close existing call
    if current_call_strike and current_call_expiration and current_call_mid:
        btc_limit = round(current_call_mid * 1.02, 2)
        btc_source = "estimated"
        legs.append(CollarLeg(
            action="BTC",
            type="CALL",
            strike=current_call_strike,
            expiration=current_call_expiration,
            limit=btc_limit,
            contracts=contracts,
            price_source=btc_source,
        ))
        btc_cost = current_call_mid * 100 * contracts
    else:
        btc_cost = 0

    # STO leg: sell-to-open new call
    estimated_new_call_mid = max(spot * 0.04, 1.0)
    sto_limit = round(estimated_new_call_mid, 2)
    sto_source = "estimated"
    if chain_provider:
        chain_data = chain_provider(ticker, target_exp, new_call_strike, "CALL")
        if chain_data and "mid" in chain_data:
            sto_limit = round(chain_data["mid"], 2)
            sto_source = "live_chain"

    legs.append(CollarLeg(
        action="STO",
        type="CALL",
        strike=new_call_strike,
        expiration=target_exp,
        limit=sto_limit,
        contracts=contracts,
        price_source=sto_source,
    ))
    sto_credit = sto_limit * 100 * contracts

    # BTO leg: buy-to-open put (if not skipped)
    bto_cost = 0
    if put_strike is not None:
        estimated_put_mid = max(spot * 0.03, 1.0)
        bto_limit = round(estimated_put_mid, 2)
        bto_source = "estimated"
        if chain_provider:
            chain_data = chain_provider(ticker, target_exp, put_strike, "PUT")
            if chain_data and "mid" in chain_data:
                bto_limit = round(chain_data["mid"], 2)
                bto_source = "live_chain"

        legs.append(CollarLeg(
            action="BTO",
            type="PUT",
            strike=put_strike,
            expiration=target_exp,
            limit=bto_limit,
            contracts=contracts,
            price_source=bto_source,
        ))
        bto_cost = bto_limit * 100 * contracts

    proposal.proposed_legs = [asdict(leg) for leg in legs]

    # ---- Cash flow calculation (now using real prices where available) ----
    proposal.net_cash = sto_credit - btc_cost - bto_cost
    proposal.max_loss = abs(proposal.net_cash) if proposal.net_cash < 0 else 0
    if put_strike:
        proposal.max_loss += (spot - put_strike) * shares  # cap downside before put
    cap_room = (new_call_strike - spot) * shares
    proposal.max_gain_at_cap = cap_room + proposal.net_cash

    # ---- Cost-cap check ----
    debit_pct_nlv = (-proposal.net_cash / nlv * 100) if (proposal.net_cash < 0 and nlv) else 0
    if debit_pct_nlv > rules["max_net_debit_pct_of_nlv"]:
        proposal.skip_reasons.append(
            f"net debit {debit_pct_nlv:.2f}% NLV exceeds cap {rules['max_net_debit_pct_of_nlv']:.1f}% — "
            f"downgrading to roll-up only"
        )
        # Strip the put leg
        proposal.proposed_legs = [
            l for l in proposal.proposed_legs if l["type"] != "PUT"
        ]

    proposal.explanation = (
        f"{ticker}: core long ({concentration_pct:.1f}% NLV) with "
        f"${tax_exposure:,.0f} embedded tax. Convert covered call to collar by "
        f"rolling cap to ${new_call_strike:.0f} "
        f"({(new_call_strike/spot-1)*100:+.1f}% OTM)"
        + (f" and adding put floor at ${put_strike:.0f} ({(spot-put_strike)/spot*100:.0f}% below spot)."
           if put_strike else " (puts too expensive at current IV; roll-up only).")
    )

    return proposal
