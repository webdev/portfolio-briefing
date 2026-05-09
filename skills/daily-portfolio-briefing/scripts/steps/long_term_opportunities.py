"""
Step 6.5: Long-term opportunities (Wave 22)

Runs the long-term-opportunity-advisor across the held universe + recommended-
but-not-held tickers. Surfaces ADD / TRIM / EXIT / HOLD on equities plus
LEAP_CALL / LONG_DATED_CSP option ideas with multi-month horizons.

Inputs come from the existing snapshot:
  - positions (held weights and spots)
  - technicals (RSI / 200-SMA / drawdown_pct from snapshot_inputs)
  - iv_ranks
  - third-party recommendations from fetch_recommendations
  - target weights from config (config["target_weights"][ticker], default 5%)

Output:  list[LongTermOpportunity-as-dict]  (ready to render).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


# Path to long-term-opportunity-advisor scripts. We import its `advise.py`
# directly by file path because the repo also contains
# wheel-roll-advisor/scripts/advise.py — putting the long-term path on
# sys.path would shadow whichever was imported first.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_LT_ADVISOR = _REPO_ROOT / "skills" / "long-term-opportunity-advisor" / "scripts"

_LT_MODULE: ModuleType | None = None


def _load_lt_module() -> ModuleType | None:
    """Load the long-term-opportunity-advisor's advise.py by absolute path.

    Cached after first call. Returns None if the file is missing — the caller
    treats that as a degraded mode and skips long-term recommendations.
    """
    global _LT_MODULE
    if _LT_MODULE is not None:
        return _LT_MODULE

    target = _LT_ADVISOR / "advise.py"
    if not target.exists():
        return None

    spec = importlib.util.spec_from_file_location("lt_advise", target)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules["lt_advise"] = module  # so dataclasses can find their own module
    spec.loader.exec_module(module)
    _LT_MODULE = module
    return module


def generate_long_term_opportunities_step(
    snapshot_data: dict,
    recommendations_list: list,
    config: dict,
) -> list:
    """
    Produce a ranked list of long-term opportunity dicts for the briefing.

    Returns an empty list (not an error) when inputs are missing — long-term
    opportunities are enrichment, not load-bearing.
    """
    lt = _load_lt_module()
    if lt is None or not hasattr(lt, "generate_long_term_opportunities"):
        print("  [warn] long-term-advisor module not loadable", file=sys.stderr)
        return []
    generate_long_term_opportunities = lt.generate_long_term_opportunities

    positions = snapshot_data.get("positions", []) or []
    technicals = snapshot_data.get("technicals", {}) or {}
    iv_ranks = snapshot_data.get("iv_ranks", {}) or {}
    quotes = snapshot_data.get("quotes", {}) or {}
    balance = snapshot_data.get("balance", {}) or {}

    nlv = float(balance.get("accountValue", 0) or 0)
    if nlv <= 0:
        return []

    # Build per-ticker weight + spot dict from EQUITY positions only.
    # Aggregate across accounts (positions are already deduplicated, but be
    # defensive in case downstream changes that).
    positions_by_ticker: dict = {}
    for p in positions:
        if p.get("assetType") != "EQUITY":
            continue
        sym = p.get("symbol")
        if not sym:
            continue
        qty = float(p.get("qty", 0) or 0)
        spot = float(p.get("price") or quotes.get(sym, {}).get("last") or 0)
        market_value = qty * spot
        weight_pct = (market_value / nlv) * 100.0 if nlv else 0.0
        if sym in positions_by_ticker:
            positions_by_ticker[sym]["weight_pct"] += weight_pct
        else:
            positions_by_ticker[sym] = {"weight_pct": weight_pct, "spot": spot}

    # Pull RSI / 200-SMA / drawdown from technicals
    rsi_values: dict = {}
    sma_200_values: dict = {}
    drawdown_pcts: dict = {}
    for sym, tech in technicals.items():
        if not isinstance(tech, dict):
            continue
        if tech.get("rsi_14") is not None:
            rsi_values[sym] = tech["rsi_14"]
        if tech.get("sma_200") is not None:
            sma_200_values[sym] = tech["sma_200"]
        if tech.get("drawdown_pct") is not None:
            drawdown_pcts[sym] = tech["drawdown_pct"]

    # Map third-party recommendations: {ticker: "BUY"/"HOLD"/"SELL"}
    third_party_recs: dict = {}
    for r in recommendations_list or []:
        ticker = r.get("ticker")
        rec = r.get("recommendation")
        if ticker and rec:
            third_party_recs[ticker.upper()] = str(rec).upper()

    # Target weights — config["target_weights"] is the canonical source.
    # Fall back to a flat 5% per holding so the system is usable without
    # explicit per-ticker config.
    target_weights_cfg = (config or {}).get("target_weights", {}) or {}
    target_weights: dict = {}
    for sym in positions_by_ticker:
        target_weights[sym] = float(target_weights_cfg.get(sym, 5.0))

    # Cash-on-hand check for LONG_DATED_CSP (need collateral)
    cash = float(balance.get("cash", 0) or 0)
    has_cash = cash > 50_000  # arbitrary floor: need at least 1 chunk for CSP

    # For recommended-but-not-held tickers, the advisor needs a spot price.
    # If the recommendation came with a price target but we don't have a quote,
    # the advisor will skip them. That's fine — we just pass through what we have.
    for ticker in third_party_recs:
        if ticker not in positions_by_ticker:
            spot = quotes.get(ticker, {}).get("last")
            if spot:
                positions_by_ticker[ticker] = {"weight_pct": 0.0, "spot": float(spot)}

    try:
        opportunities = generate_long_term_opportunities(
            positions_by_ticker=positions_by_ticker,
            rsi_values=rsi_values,
            iv_ranks=iv_ranks,
            third_party_recs=third_party_recs,
            drawdown_pcts=drawdown_pcts,
            sma_200_values=sma_200_values,
            target_weights=target_weights,
            has_cash=has_cash,
        )
    except Exception as e:
        print(f"  [warn] long-term-advisor failed: {e}", file=sys.stderr)
        return []

    # Convert dataclasses to plain dicts for downstream JSON-ability + rendering
    return [op.to_dict() for op in opportunities]


def render_long_term_opportunities(opportunities: list) -> list[str]:
    """Render the LONG-TERM OPPORTUNITIES section of the briefing.

    Uses the advisor's own format_opportunity_md() if available, otherwise
    falls back to a minimal renderer.
    """
    if not opportunities:
        return [
            "## 🔭 Long-Term Opportunities (3-12mo horizon)",
            "",
            "_No long-term ADD/TRIM/EXIT or LEAP/CSP signals at current levels._",
            "_This section pairs third-party recommendations with technical setup_"
            " _(RSI, drawdown, 200-SMA, IV rank) to surface multi-month plays._",
            "",
        ]

    lt = _load_lt_module()
    format_opportunity_md = getattr(lt, "format_opportunity_md", None) if lt else None
    LongTermOpportunity = getattr(lt, "LongTermOpportunity", None) if lt else None

    lines = [
        "## 🔭 Long-Term Opportunities (3-12mo horizon)",
        "",
        f"_{len(opportunities)} signal(s) — third-party recs × RSI × drawdown × IV rank × 200-SMA._",
        "",
    ]
    for n, op in enumerate(opportunities, 1):
        if format_opportunity_md and LongTermOpportunity:
            try:
                # The advisor expects a LongTermOpportunity instance; rebuild
                # one from the dict to reuse its renderer.
                obj = LongTermOpportunity(**op)
                lines.extend(format_opportunity_md(obj, n))
                continue
            except Exception:
                pass
        # Fallback: minimal inline renderer
        emoji = {
            "ADD": "📈", "TRIM": "✂️", "EXIT": "🚪", "HOLD": "🤝",
            "LEAP_CALL": "🎯", "LONG_DATED_CSP": "💎",
            "DIAGONAL": "📐", "DIVIDEND": "💵",
        }.get(op.get("kind", ""), "•")
        lines.append(
            f"### {emoji} {n}. {op.get('kind', '?').replace('_', ' ')} · `{op.get('ticker', '?')}`"
        )
        lines.append("")
        if op.get("concrete_trade"):
            lines.append(f"**Trade:** {op['concrete_trade']}")
            lines.append("")
        if op.get("trigger_reasons"):
            lines.append(f"- **Triggers:** {'; '.join(op['trigger_reasons'])}")
        if op.get("rationale"):
            lines.append(f"- **Rationale:** {op['rationale']}")
        if op.get("yield_or_cost"):
            lines.append(f"- **Yield/Cost:** {op['yield_or_cost']}")
        if op.get("source"):
            lines.append(f"- **Source:** {op['source']}")
        lines.append("")

    return lines
