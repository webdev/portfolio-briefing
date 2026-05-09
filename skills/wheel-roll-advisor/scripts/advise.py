"""Main advise() function and CLI entry point."""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List
from datetime import datetime, timedelta

try:
    from matrix_loader import load_all
    from decision_walker import derive_state, walk_matrix
    from guardrails import run_pre_matrix_guardrails, run_post_matrix_guardrails
    from roll_target import select_roll_target, enumerate_roll_candidates, RollCandidate
except ImportError:
    from .matrix_loader import load_all
    from .decision_walker import derive_state, walk_matrix
    from .guardrails import run_pre_matrix_guardrails, run_post_matrix_guardrails
    from .roll_target import select_roll_target, enumerate_roll_candidates, RollCandidate


def _build_order_ticket(
    existing_position: Dict[str, Any],
    chosen_candidate: RollCandidate,
) -> Dict[str, Any]:
    """Build concrete buy-to-close + sell-to-open ticket from a RollCandidate."""

    if chosen_candidate.instruction is None:
        return {}

    instr = chosen_candidate.instruction
    qty = int(existing_position.get("quantity", 1))
    current_strike = float(existing_position.get("strikePrice", 0))
    current_exp = existing_position.get("expirationDate", "")
    symbol = existing_position.get("symbol", "")

    # Walk limits for better fills
    close_ask = instr.get("sell_ask", 0.0)  # We buy at ask
    close_limit = close_ask + 0.10  # Walk closer to mid

    sell_bid = instr.get("sell_bid", 0.0)  # We sell at bid
    sell_limit = sell_bid - 0.10  # Walk lower to get better fill

    net_credit_target = chosen_candidate.net_dollars
    net_credit_per_contract = net_credit_target / qty if qty > 0 else 0.0

    return {
        "ticket_type": "diagonal_roll",
        "buy_to_close": {
            "symbol": symbol,
            "expiration": current_exp,
            "strike": current_strike,
            "quantity": qty,
            "limit_price": close_limit,
            "tif": "GTC",
            "bid_ask": {
                "bid": instr.get("sell_bid"),
                "ask": instr.get("sell_ask"),
                "mid": instr.get("sell_mid"),
            }
        },
        "sell_to_open": {
            "symbol": symbol,
            "expiration": instr.get("sell_expiration"),
            "strike": instr.get("sell_strike"),
            "quantity": qty,
            "limit_price": sell_limit,
            "tif": "GTC",
            "bid_ask": {
                "bid": instr.get("sell_bid"),
                "ask": instr.get("sell_ask"),
                "mid": instr.get("sell_mid"),
            }
        },
        "net_credit_target_per_contract": net_credit_per_contract,
        "net_dollars_total": net_credit_target,
        "walk_advice": "Start at limits above; walk closer to mid in 30-min increments if no fill",
    }


def advise(
    position: Dict[str, Any],
    underlying: Dict[str, Any],
    context: Dict[str, Any],
    chain: Dict[str, Any],
    matrix_path: str = None,
    params_path: str = None,
    tail_risk_path: str = None,
) -> Dict[str, Any]:
    """Main advise function: returns structured decision with multi-candidate roll analysis."""

    # Load matrix, parameters, tail-risk names
    loaded = load_all(matrix_path, params_path, tail_risk_path)
    params = loaded.parameters
    matrix = loaded.matrix
    tail_risk = loaded.tail_risk_names

    # Merge chain data into position
    if "ivRank" not in context and "ivRank" not in position:
        context["ivRank"] = 45  # Default

    position["ivRank"] = context.get("ivRank")
    position["underlyingPrice"] = underlying.get("lastPrice", 0)
    position["outlook"] = underlying.get("outlook", "NEUTRAL")

    # Derive state
    state = derive_state(position, underlying, context, params)

    # Run pre-matrix guardrails
    guardrail_decision = run_pre_matrix_guardrails(position, underlying, context, params)

    if guardrail_decision:
        decision = guardrail_decision
    else:
        # Walk matrix
        decision = walk_matrix(matrix, state)

    # Run post-matrix guardrails (may override), passing state for LEAP override
    post_decision = run_post_matrix_guardrails(
        decision, position, underlying, context, params, tail_risk, state
    )
    if post_decision:
        decision = post_decision

    # Enumerate roll candidates whenever the user might want to consider a roll:
    # - Decision is ROLL_*
    # - Position has unrealized loss > 10% (worth showing alternatives)
    # - LEAP override fired (we held but the user wants to know what rolling looks like)
    enumerate = decision.decision.startswith("ROLL_")
    if not enumerate:
        entry = float(position.get("entryPrice", 0) or 0)
        current = float(position.get("currentMid", 0) or 0)
        if entry > 0:
            loss_pct = (current - entry) / entry  # short side: positive when option went up = loss
            if loss_pct > 0.10:
                enumerate = True
        # Always enumerate when LEAP override fired
        if decision.warnings and "leap_override_applied" in decision.warnings:
            enumerate = True

    roll_candidates: List[RollCandidate] = []
    if enumerate:
        roll_candidates = enumerate_roll_candidates(position, chain, params)

    # Build output
    output = {
        "decision": decision.decision,
        "matrixCell": decision.matrix_cell,
        "rationale": decision.rationale,
        "warnings": decision.warnings or [],
        "nextReviewDate": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
        "position": position,
        "state": {
            "moneyness": state.moneyness,
            "dteBand": state.dte_band,
            "ivRegime": state.iv_regime,
            "profitCapturedPct": round(state.profit_captured_pct, 3),
            "outlook": state.outlook,
            "regime": state.regime,
        },
    }

    # Add roll candidates if enumerated
    if roll_candidates:
        output["rollCandidates"] = [
            {
                "id": c.id,
                "description": c.description,
                "instruction": c.instruction,
                "closeCost": c.close_cost,
                "newCredit": c.new_credit,
                "netDollars": c.net_dollars,
                "dteExtension": c.dte_extension,
                "deltaChange": c.delta_change,
                "notes": c.notes,
            }
            for c in roll_candidates
        ]

        # Recommend the first non-HOLD candidate if decision is ROLL_*.
        # Use the tax-aware candidate_ranker when context flags is_core.
        recommended_id = "A"  # Default to HOLD
        if decision.decision.startswith("ROLL_") and len(roll_candidates) > 1:
            try:
                from candidate_ranker import rank_candidates as _rank
            except ImportError:
                _rank = None
            if _rank is not None:
                # Convert dataclass candidates to dicts the ranker expects
                cand_dicts = [
                    {
                        "id": c.id,
                        "description": c.description,
                        "netDollars": c.net_dollars,
                        "dteExtension": c.dte_extension,
                        "instruction": c.instruction,
                        "current_strike": position.get("strike"),
                    }
                    for c in roll_candidates
                ]
                spot = (underlying or {}).get("price") or position.get("underlyingPrice") or 0
                is_core_flag = bool(context.get("is_core") if isinstance(context, dict) else False)
                tax_est = float(context.get("embedded_tax_dollars", 0)) if isinstance(context, dict) else 0
                best, _scores = _rank(
                    cand_dicts, spot=spot, is_core=is_core_flag,
                    embedded_tax_dollars=tax_est,
                )
                if best is not None:
                    recommended_id = best["id"]
            else:
                # Fallback to old "B" preference
                for cand in roll_candidates:
                    if cand.id == "B":
                        recommended_id = "B"
                        break

        output["recommendedCandidateId"] = recommended_id

        # If recommended is HOLD but decision is ROLL_*, provide "if rolling anyway" fallback
        if recommended_id == "A" and decision.decision.startswith("ROLL_"):
            output["ifRollingAnywayCandidateId"] = "B"
            if len(roll_candidates) > 1:
                fallback_cand = next((c for c in roll_candidates if c.id == "B"), None)
                if fallback_cand:
                    output["ifRollingAnywayTicket"] = _build_order_ticket(position, fallback_cand)
        else:
            # If decision is not ROLL_, no fallback needed
            output["orderTicket"] = None

    # Backward compatibility: single roll_target (best candidate if ROLL_*)
    roll_target = None
    if decision.decision.startswith("ROLL_"):
        roll_target = select_roll_target(position, chain, params)
    output["rollTarget"] = roll_target

    return output


def main():
    """CLI entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Wheel-roll-advisor decision engine")
    parser.add_argument("--input", type=str, help="Input JSON file (default: stdin)")
    parser.add_argument("--output", type=str, help="Output JSON file (default: stdout)")
    parser.add_argument("--matrix", type=str, help="Path to decision_matrix.yaml")
    parser.add_argument("--params", type=str, help="Path to wheel_parameters.yaml")
    parser.add_argument("--tail-risk", type=str, help="Path to tail_risk_names.yaml")
    
    args = parser.parse_args()
    
    # Read input
    if args.input:
        with open(args.input) as f:
            input_data = json.load(f)
    else:
        input_data = json.load(sys.stdin)
    
    # Extract components
    position = input_data.get("position", {})
    underlying = input_data.get("underlying", {})
    context = input_data.get("context", {})
    chain = input_data.get("chain", {})
    
    # Advise
    result = advise(
        position=position,
        underlying=underlying,
        context=context,
        chain=chain,
        matrix_path=args.matrix,
        params_path=args.params,
        tail_risk_path=args.tail_risk,
    )
    
    # Write output
    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
    else:
        json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
