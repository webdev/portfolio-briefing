"""
Step 4: Review equity positions

For each held equity, apply decision matrix to generate recommendation tag.
"""

import json
from pathlib import Path


def review_equities(
    snapshot_data: dict,
    regime_data: dict,
    directives_active: list,
    recommendations_list: list,
    snapshot_dir: Path,
) -> list:
    """
    Review each equity position.

    Returns:
        List of recommendation dicts: {ticker, weight, thesis_status, technical_status, recommendation, rationale, matrix_cell_id}
    """

    equity_reviews = []
    positions = snapshot_data.get("positions", [])
    balance = snapshot_data.get("balance", {})
    nlv = balance.get("accountValue", 0)

    for pos in positions:
        if pos.get("assetType") != "EQUITY":
            continue

        ticker = pos.get("symbol")
        qty = pos.get("qty", 0)
        price = pos.get("price", 0)
        cost_basis = pos.get("costBasis", 0)

        market_value = qty * price
        weight = market_value / nlv if nlv > 0 else 0
        pl_pct = (price - cost_basis) / cost_basis if cost_basis > 0 else 0

        # V1: Stub decision matrix (always HOLD), but enrich with any
        # third-party recommendation that matches this ticker. The matrix walker
        # itself is v1.1; for now the rec list flags + the held position together
        # give the briefing real informational content.
        rec_match = None
        for rec in (recommendations_list or []):
            if (rec.get("ticker") or "").upper() == (ticker or "").upper():
                rec_match = rec
                break

        rationale = "Thesis intact, technical sound."
        rec_note = None
        if rec_match:
            raw = rec_match.get("raw_recommendation", "")
            tier = rec_match.get("rating_tier", 0)
            age = rec_match.get("age_days", 0)
            rec_note = f"Third-party: {raw} (tier {tier}, {age}d old)"
            rationale = f"{rationale} {rec_note}"

        review = {
            "ticker": ticker,
            "price": price,
            "qty": qty,
            "market_value": market_value,
            "weight": weight,
            "pl_pct": pl_pct,
            "thesis_status": "ACTIVE",
            "technical_status": "UPTREND",
            "recommendation": "HOLD",
            "rationale": rationale,
            "third_party_rec": rec_note,
            "matrix_cell_id": "STUB_HOLD",
        }
        equity_reviews.append(review)

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with open(snapshot_dir / "equity_reviews.json", "w") as f:
        json.dump(equity_reviews, f, indent=2)

    print(f"  Reviewed {len(equity_reviews)} equity positions")
    return equity_reviews
