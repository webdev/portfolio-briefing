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
    # Fall back to a flat 5% per holding (non-core) or 12% (core).
    # Core names get a higher target so the 1.5× TRIM trigger doesn't fire
    # at 7.5% NLV (which is normal for mega-cap conviction holdings).
    target_weights_cfg = (config or {}).get("target_weights", {}) or {}
    core_tickers_set = set((config or {}).get("core_positions", []) or [])
    default_core_target = float((config or {}).get("core_target_weight_pct", 12.0))
    default_target = float((config or {}).get("default_target_weight_pct", 5.0))

    target_weights: dict = {}
    for sym in positions_by_ticker:
        if sym in target_weights_cfg:
            target_weights[sym] = float(target_weights_cfg[sym])
        elif sym in core_tickers_set:
            target_weights[sym] = default_core_target
        else:
            target_weights[sym] = default_target

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
    op_dicts = [op.to_dict() for op in opportunities]

    # Filter: don't propose TRIM on core holdings. The action-list section
    # of the renderer has its own core-aware trim path (rolls covered calls
    # up instead of selling); the long-term advisor's generic "weight > 1.5×
    # target" trigger should defer to that for core names.
    filtered: list = []
    for op in op_dicts:
        if op.get("kind") == "TRIM" and op.get("ticker") in core_tickers_set:
            continue
        filtered.append(op)
    op_dicts = filtered

    # Snap each LONG_DATED_CSP / LEAP_CALL to a real chain expiration so the
    # briefing renders a concrete date instead of "~75 DTE".
    chains = snapshot_data.get("chains", {}) or {}
    _enrich_long_dated_dates(op_dicts, chains, target_dte_csp=75, target_dte_leap=365)
    # Pull REAL premium/bid/ask from live yfinance chains for each LT_CSP so
    # the briefing doesn't ship spot×2.5% rule-of-thumb estimates.
    _enrich_with_live_premiums(op_dicts)
    return op_dicts


# ---------------------------------------------------------------------------
# Expiration date enrichment
# ---------------------------------------------------------------------------

def _enrich_long_dated_dates(
    op_dicts: list,
    chains: dict,
    target_dte_csp: int = 75,
    target_dte_leap: int = 365,
    chain_match_tolerance_days: int = 21,
) -> None:
    """For each LONG_DATED_CSP / LEAP_CALL, replace '~N DTE' with a real
    expiration date.

    Selection rules:
      1. If the snapshot has a chain expiration within `chain_match_tolerance_days`
         of the target DTE, use it (Friday preferred over Thursday).
      2. Otherwise use the 3rd Friday of the target month — every listed
         equity option has this standard monthly expiration.

    The tolerance prevents snapping to a too-distant chain expiration (e.g.,
    when we only fetched chains for held positions and the available
    expirations are 6 months away from the target).
    """
    from datetime import date, timedelta

    today = date.today()

    # Group chain expirations by ticker (chain keys are TICKER_YYYY-MM-DD)
    chain_exps_by_ticker: dict[str, list[str]] = {}
    for key in chains.keys():
        if "_" not in key:
            continue
        ticker, exp = key.split("_", 1)
        try:
            date.fromisoformat(exp)  # validate
        except ValueError:
            continue
        chain_exps_by_ticker.setdefault(ticker.upper(), []).append(exp)
    for t in chain_exps_by_ticker:
        chain_exps_by_ticker[t] = sorted(chain_exps_by_ticker[t])

    for op in op_dicts:
        kind = (op.get("kind") or "").upper()
        if kind == "LONG_DATED_CSP":
            target_dte = target_dte_csp
            placeholder = "~75 DTE"
        elif kind == "LEAP_CALL":
            target_dte = target_dte_leap
            placeholder = "~365 DTE"
        else:
            continue

        ticker = (op.get("ticker") or "").upper()
        target_date = today + timedelta(days=target_dte)

        chosen = None

        # Option 1: real chain expiration within tolerance, Friday preferred.
        if ticker in chain_exps_by_ticker:
            candidates = []
            for e in chain_exps_by_ticker[ticker]:
                d = date.fromisoformat(e)
                if d < today:
                    continue
                distance = abs((d - target_date).days)
                if distance > chain_match_tolerance_days:
                    continue
                # Friday=4. Prefer Friday over weekday for cleaner ticket.
                weekday_penalty = 0 if d.weekday() == 4 else 3
                candidates.append((distance + weekday_penalty, e))
            if candidates:
                candidates.sort()
                chosen = candidates[0][1]

        # Option 2: algorithmic — 3rd Friday of target month
        if not chosen:
            chosen = _third_friday_of_month(target_date)

        actual_dte = (date.fromisoformat(chosen) - today).days
        pretty = date.fromisoformat(chosen).strftime("%a %b %d '%y")

        date_phrase = f"exp {pretty} ({actual_dte} DTE)"
        if op.get("concrete_trade"):
            op["concrete_trade"] = op["concrete_trade"].replace(placeholder, date_phrase)
        if op.get("rationale"):
            op["rationale"] = op["rationale"].replace(
                f"{target_dte}-DTE horizon", f"{actual_dte}-DTE horizon (expires {pretty})"
            )
        op["target_expiration"] = chosen
        op["target_dte"] = actual_dte


def _enrich_with_live_premiums(op_dicts: list) -> None:
    """Fetch real put-chain bid/mid/ask via the etrade-chain-fetcher skill.

    Per project rule: chain data for tradeable recommendations MUST come from
    E*TRADE, not yfinance. If E*TRADE is unavailable we DO NOT fall back to
    yfinance — instead the yield_or_cost line is tagged with "(est, broker
    unreachable)" so the user knows the premium is a guess.

    Runs E*TRADE chain fetches in parallel — typically ~2-4s for 8 chains.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import date as _date_class

    # Load the etrade-chain-fetcher skill module by absolute path (no name
    # collision risk since this is its own module).
    import importlib.util as _ilu
    _fetcher_path = (
        Path(__file__).resolve().parents[3]
        / "etrade-chain-fetcher" / "scripts" / "fetch.py"
    )
    if not _fetcher_path.exists():
        print("  [warn] etrade-chain-fetcher not found; LT_CSP premiums stay as estimates",
              file=sys.stderr)
        return
    spec = _ilu.spec_from_file_location("etrade_chain_fetcher", _fetcher_path)
    if spec is None or spec.loader is None:
        return
    fetcher = _ilu.module_from_spec(spec)
    sys.modules["etrade_chain_fetcher"] = fetcher
    spec.loader.exec_module(fetcher)

    if not fetcher.is_available():
        print(f"  [warn] E*TRADE chain fetcher unavailable: {fetcher.availability_reason()}",
              file=sys.stderr)
        # Tag every LT_CSP yield_or_cost as estimate
        for op in op_dicts:
            if (op.get("kind") or "").upper() == "LONG_DATED_CSP":
                if op.get("yield_or_cost") and "(est" not in op["yield_or_cost"]:
                    op["yield_or_cost"] += " — _est, broker unreachable_"
        return

    targets = []
    for op in op_dicts:
        kind = (op.get("kind") or "").upper()
        if kind != "LONG_DATED_CSP":
            continue
        ticker = op.get("ticker") or ""
        exp = op.get("target_expiration")
        import re as _re
        sm = _re.search(r"\$(\d+(?:\.\d+)?)P\b", op.get("concrete_trade", ""))
        if not (ticker and exp and sm):
            continue
        target_strike = float(sm.group(1))
        targets.append((op, ticker, exp, target_strike))

    if not targets:
        return

    # Shared cache so we don't refetch the same (ticker, expiration) tuple
    chain_cache = fetcher.ChainCache()

    def _fetch_put(ticker: str, exp: str, target_strike: float) -> dict | None:
        try:
            exp_date = _date_class.fromisoformat(exp)
        except ValueError:
            return None
        # Use spot-aware target via OTM-pct strike finder, OR exact strike
        # if it's in the chain. quote_contract handles the exact lookup.
        q = fetcher.quote_contract(
            symbol=ticker,
            strike=target_strike,
            expiration=exp_date,
            opt_type="PUT",
            cache=chain_cache,
        )
        if q:
            return q
        # Strike not listed exactly — fall back to closest-strike on the chain
        chain = fetcher.get_chain(
            symbol=ticker,
            expiration=exp_date,
            strike_near=target_strike,
            n_strikes=20,
            chain_type="PUT",
            cache=chain_cache,
        )
        if not chain or not chain.get("puts"):
            return None
        best = min(chain["puts"], key=lambda r: abs(r.strike - target_strike))
        from etrade_chain_fetcher import _row_to_dict  # type: ignore
        return _row_to_dict(best, expiration=exp_date, opt_type="PUT")

    fetched: dict = {}
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="lt-prem") as ex:
        future_to_op = {
            ex.submit(_fetch_put, ticker, exp, strike): (op, ticker, strike)
            for (op, ticker, exp, strike) in targets
        }
        for fut in as_completed(future_to_op):
            op, ticker, requested_strike = future_to_op[fut]
            try:
                data = fut.result(timeout=12)
            except Exception:
                data = None
            if data:
                fetched[id(op)] = data

    for (op, ticker, exp, requested_strike) in targets:
        data = fetched.get(id(op))
        if not data:
            # Mark estimate as such
            if op.get("yield_or_cost") and "(est)" not in op["yield_or_cost"]:
                op["yield_or_cost"] = op["yield_or_cost"].replace("~$", "~$") + " — _premium is rule-of-thumb estimate; verify at broker_"
            continue

        actual_strike = data["strike"]
        bid = data["bid"]
        mid = data["mid"]
        ask = data["ask"]
        premium_per_share = mid or bid  # prefer mid
        if premium_per_share <= 0:
            continue
        premium_total = premium_per_share * 100
        collateral = actual_strike * 100
        annualized = (premium_per_share / actual_strike) * (365 / max(op.get("target_dte", 75), 1)) * 100

        # Update concrete_trade strike if snapped to a different strike
        import re as _re
        if abs(actual_strike - requested_strike) > 0.01:
            new_strike_str = f"${int(actual_strike) if actual_strike == int(actual_strike) else actual_strike}P"
            old_strike_str = f"${int(requested_strike) if requested_strike == int(requested_strike) else requested_strike}P"
            op["concrete_trade"] = op["concrete_trade"].replace(old_strike_str, new_strike_str)

        # Rewrite yield_or_cost with real numbers + chain source for transparency
        source_tag = data.get("source", "etrade_live")
        if bid and ask:
            spread_pct = ((ask - bid) / mid * 100) if mid else 0
            op["yield_or_cost"] = (
                f"premium ${premium_total:,.0f} (mid ${mid:.2f}, "
                f"bid ${bid:.2f} / ask ${ask:.2f}, spread {spread_pct:.0f}%) · "
                f"~{annualized:.0f}% annualized · ${collateral:,.0f} cash collateral · "
                f"_Source: {'Live E*TRADE chain' if source_tag == 'etrade_live' else source_tag}_"
            )
        else:
            op["yield_or_cost"] = (
                f"premium ${premium_total:,.0f} (last ${premium_per_share:.2f}) · "
                f"~{annualized:.0f}% annualized · ${collateral:,.0f} cash collateral · "
                f"_Source: {'Live E*TRADE chain' if source_tag == 'etrade_live' else source_tag}_"
            )
        # Also update rationale's "fat premium" framing if the actual premium is thin
        if op.get("rationale") and "fat premium" in op["rationale"] and premium_total < 500:
            op["rationale"] = op["rationale"].replace(
                "= fat premium",
                f"= ${premium_total:.0f} premium (thinner than the rule-of-thumb estimate — "
                "reconsider unless you specifically want this strike)"
            )
        # Stash for downstream consumers (capital-planner, etc.)
        op["live_premium_per_share"] = premium_per_share
        op["live_bid"] = bid
        op["live_mid"] = mid
        op["live_ask"] = ask
        op["live_strike"] = actual_strike


def _third_friday_of_month(target: "date") -> str:  # noqa: F821
    """Return the 3rd Friday of the month containing `target`, as ISO date string.

    Every listed equity option has a 3rd-Friday standard monthly contract.
    If the 3rd Friday has already passed this month, return next month's
    3rd Friday so the contract is actually tradeable.
    """
    from datetime import date, timedelta
    year, month = target.year, target.month
    # First find the 3rd Friday of the target month
    first_of_month = date(year, month, 1)
    # Friday = weekday 4 (Mon=0)
    days_to_first_friday = (4 - first_of_month.weekday()) % 7
    third_friday = first_of_month + timedelta(days=days_to_first_friday + 14)
    # If the 3rd Friday is in the past, roll to next month
    today = date.today()
    if third_friday <= today:
        month += 1
        if month > 12:
            month = 1
            year += 1
        first_of_month = date(year, month, 1)
        days_to_first_friday = (4 - first_of_month.weekday()) % 7
        third_friday = first_of_month + timedelta(days=days_to_first_friday + 14)
    return third_friday.isoformat()


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
