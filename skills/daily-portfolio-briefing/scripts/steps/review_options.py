"""
Step 5: Review options book

For each open option, invoke wheel-roll-advisor's advise.py with structured input
built from the snapshot. The advisor returns one of seven decision tags plus a
matrix_cell_id we surface in the briefing for auditability.
"""

import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[4]
_WHEEL_SKILL = _REPO_ROOT / "skills" / "wheel-roll-advisor"
_WHEEL_CLI = _WHEEL_SKILL / "scripts" / "advise.py"


def _build_advisor_input(pos: dict, snapshot: dict, regime_data: dict) -> dict | None:
    """Construct the input JSON wheel-roll-advisor expects from a snapshot position."""
    underlying = pos.get("underlying")
    if not underlying:
        return None

    quotes = snapshot.get("quotes", {})
    iv_ranks = snapshot.get("iv_ranks", {})
    chains = snapshot.get("chains", {})

    underlying_quote = quotes.get(underlying, {})
    if "last" not in underlying_quote:
        return None

    expiration = pos.get("expiration")
    chain_key = f"{underlying}_{expiration}"
    chain = chains.get(chain_key, {})

    # Build a multi-expiration chain map for the wheel-roll-advisor's roll-candidate
    # enumeration. Keys: "calls", "puts" for the held expiration (current behaviour);
    # plus "future_chains": {expiration: {calls: [...], puts: [...]}} for each future
    # expiration we pre-fetched in snapshot_inputs.
    future_chains = {}
    for k, v in (chains or {}).items():
        if k.startswith(f"{underlying}_") and k != chain_key:
            exp_str = k[len(underlying) + 1:]
            future_chains[exp_str] = {
                "calls": v.get("calls", []),
                "puts": v.get("puts", []),
            }
    chain_with_future = dict(chain or {"calls": [], "puts": []})
    chain_with_future["future_chains"] = future_chains

    # Build a "candidates" list in the shape enumerate_roll_candidates expects
    # (flattened across all available expirations for this underlying).
    is_put = pos.get("type") == "PUT"
    flat_candidates: list = []

    def _add_chain_legs(chain_dict, exp_str):
        legs = chain_dict.get("puts" if is_put else "calls", [])
        for leg in legs:
            try:
                strike = float(leg.get("strike", 0) or 0)
            except (TypeError, ValueError):
                continue
            if strike <= 0:
                continue
            bid = leg.get("bid", 0) or 0
            ask = leg.get("ask", 0) or 0
            try:
                bid_f = float(bid) if bid else 0
                ask_f = float(ask) if ask else 0
            except (TypeError, ValueError):
                bid_f = ask_f = 0
            from datetime import date as _date
            try:
                exp_d = datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte_for_leg = max(0, (exp_d - _date.today()).days)
            except ValueError:
                dte_for_leg = 0
            flat_candidates.append({
                "strikePrice": strike,
                "expirationDate": exp_str,
                "bid": bid_f,
                "ask": ask_f,
                "delta": leg.get("delta"),
                "openInterest": leg.get("openInterest", 0) or 0,
                "daysToExpiry": dte_for_leg,
            })

    if chain:
        _add_chain_legs(chain, expiration)
    for exp_str, fc in future_chains.items():
        _add_chain_legs(fc, exp_str)
    chain_with_future["candidates"] = flat_candidates

    # Days-to-expiry from today
    if expiration:
        try:
            exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
            days_to_expiry = max(0, (exp_date - date.today()).days)
        except ValueError:
            days_to_expiry = 30
    else:
        days_to_expiry = 30

    is_put = pos.get("type") == "PUT"
    qty = pos.get("qty", 0)
    side = "SHORT" if qty < 0 else "LONG"
    position_type = "SHORT_PUT" if (is_put and side == "SHORT") else (
        "SHORT_CALL_COVERED" if (not is_put and side == "SHORT") else "OTHER"
    )

    # Real entry (premium received per contract) and current mark from E*TRADE COMPLETE view
    entry_price = pos.get("premiumReceived") or pos.get("entryPrice") or 0.0
    current_mid = pos.get("currentMid") or pos.get("currentPrice") or 0.0

    # Prefer the real per-contract IV from E*TRADE over a synthetic IV-rank approximation
    iv_pct = pos.get("ivPct")  # percentage points e.g. 43.24
    iv_rank = iv_pct if iv_pct is not None else iv_ranks.get(underlying, 45)

    return {
        "position": {
            "symbol": underlying,
            "positionType": position_type,
            "optionType": "PUT" if is_put else "CALL",
            "side": side,
            "strikePrice": pos.get("strike", 0.0),
            "expirationDate": expiration,
            "quantity": abs(qty),
            "entryPrice": float(entry_price),
            "currentMid": float(current_mid),
            # Real per-position delta from E*TRADE if available
            "delta": pos.get("delta"),
            "daysToExpiry": days_to_expiry,
        },
        "underlying": {
            "symbol": underlying,
            "lastPrice": underlying_quote["last"],
            "outlook": "NEUTRAL",  # v1 default; v1.1 wires technical-analyst
            "nextEarnings": None,   # v1 default; v1.1 wires earnings-calendar
        },
        "context": {
            "ivRank": iv_rank,
            "regime": regime_data.get("regime", "NORMAL"),
            "existingOpenOrder": False,
        },
        "chain": chain_with_future,
    }


def _call_advisor(advisor_input: dict) -> dict:
    """Invoke advise.py via subprocess, pass JSON on stdin, return parsed response."""
    if not _WHEEL_CLI.exists():
        return {"decision": "ERROR", "rationale": f"advise.py not found at {_WHEEL_CLI}"}

    env = {**os.environ, "PYTHONPATH": str(_WHEEL_SKILL / "scripts")}
    try:
        result = subprocess.run(
            [sys.executable, str(_WHEEL_CLI)],
            input=json.dumps(advisor_input),
            text=True,
            capture_output=True,
            timeout=15,
            env=env,
            cwd=str(_WHEEL_SKILL),
        )
        if result.returncode != 0:
            return {
                "decision": "ERROR",
                "rationale": f"advise.py exit {result.returncode}: {result.stderr.strip()[:200]}",
            }
        try:
            return json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return {"decision": "ERROR", "rationale": f"non-JSON output from advise.py: {result.stdout[:200]}"}
    except subprocess.TimeoutExpired:
        return {"decision": "ERROR", "rationale": "advise.py timed out"}


def review_options(
    snapshot_data: dict,
    regime_data: dict,
    directives_active: list,
    snapshot_dir: Path,
) -> list:
    """Review each option position by calling the wheel-roll-advisor skill."""
    options_reviews = []
    positions = snapshot_data.get("positions", [])
    open_orders = snapshot_data.get("open_orders", [])
    open_order_symbols = {o.get("symbol") for o in open_orders if o.get("status") == "OPEN"}

    for pos in positions:
        if pos.get("assetType") != "OPTION":
            continue

        underlying = pos.get("underlying", "?")
        contract_label = pos.get("symbol", f"{underlying}_OPT")

        # Check directives — DEFER/MANUAL/SUPPRESS short-circuit recommendation
        directive_match = None
        for d in (directives_active or []):
            target = d.get("target", {})
            if target.get("identifier") == contract_label:
                directive_match = d
                break
        if directive_match:
            options_reviews.append({
                "underlying": underlying,
                "contract": contract_label,
                "type": pos.get("type", "?"),
                "recommendation": "DEFERRED" if directive_match["type"] == "DEFER" else directive_match["type"],
                "rationale": f"User directive {directive_match.get('reason', '')}",
                "matrix_cell_id": f"DIRECTIVE_{directive_match['type']}",
            })
            continue

        # Build advisor input from real snapshot data
        advisor_input = _build_advisor_input(pos, snapshot_data, regime_data)
        if advisor_input is None:
            options_reviews.append({
                "underlying": underlying,
                "contract": contract_label,
                "type": pos.get("type", "?"),
                "recommendation": "REVIEW",
                "rationale": "Insufficient data (missing underlying quote)",
                "matrix_cell_id": "DATA_UNAVAILABLE",
            })
            continue

        # Cross-reference open orders before calling advisor
        if contract_label in open_order_symbols:
            advisor_input["context"]["existingOpenOrder"] = True

        # Real call
        decision = _call_advisor(advisor_input)

        options_reviews.append({
            "underlying": underlying,
            "contract": contract_label,
            "type": pos.get("type", "?"),
            "qty": pos.get("qty"),
            "strike": pos.get("strike"),
            "expiration": pos.get("expiration"),
            "current_mid": advisor_input["position"]["currentMid"],
            "entry_price": advisor_input["position"]["entryPrice"],
            "days_to_expiry": advisor_input["position"]["daysToExpiry"],
            "iv_rank": advisor_input["context"]["ivRank"],
            "recommendation": decision.get("decision", "ERROR"),
            "rationale": decision.get("rationale", ""),
            "matrix_cell_id": decision.get("matrixCell") or decision.get("matrix_cell_id"),
            "roll_target": decision.get("rollTarget") or decision.get("roll_target"),
            "warnings": decision.get("warnings", []),
            # New: pass through full roll-candidate analysis from wheel-roll-advisor
            "roll_candidates": decision.get("rollCandidates") or [],
            "recommended_candidate_id": decision.get("recommendedCandidateId"),
            "if_rolling_anyway_candidate_id": decision.get("ifRollingAnywayCandidateId"),
            "if_rolling_anyway_ticket": decision.get("ifRollingAnywayTicket"),
        })

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with open(snapshot_dir / "options_reviews.json", "w") as f:
        json.dump(options_reviews, f, indent=2)

    print(f"  Reviewed {len(options_reviews)} option positions via wheel-roll-advisor")
    return options_reviews
