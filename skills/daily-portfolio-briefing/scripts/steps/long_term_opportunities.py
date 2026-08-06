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
import re
import sys
from pathlib import Path
from types import ModuleType


# Quality tier ranking constants for SKIPPED_ADD recs. Used to group the
# Skipped section so the user sees which suppressed entries would be worth
# funding if cash existed, vs which are redundant with existing CSPs.
QUALITY_TOP = "TOP"            # high-quality entry — fund this first if cash exists
QUALITY_WATCH = "WATCHLIST"     # acceptable — second-tier
QUALITY_MARGINAL = "MARGINAL"   # weak setup — skip even with cash
QUALITY_DEFERRED_CSP = "DEFERRED_CSP"  # the CSP is the entry mechanism
QUALITY_RANK = {QUALITY_TOP: 0, QUALITY_WATCH: 1, QUALITY_MARGINAL: 2, QUALITY_DEFERRED_CSP: 3}


def _score_lt_add_setup(
    op: dict,
    *,
    rsi: float | None,
    sma_200: float | None,
    drawdown_pct: float | None,
    spot: float | None,
    rating_tier: int | None,
    has_existing_csp: bool,
    sr_payload: dict | None,
) -> tuple[str, list[str]]:
    """Return ``(quality_tier, notes)`` for a SKIPPED_ADD/DEFERRED_ADD_HAS_CSP rec.

    Setup quality factors (each contributes to tier classification):
      - RSI in 35-50 sweet spot vs 50-60 acceptable vs <30 or >60 disqualifying
      - Third-party tier (5/4 high conviction, 3 standard, lower = weaker)
      - Drawdown vs 200-SMA position — healthy correction vs broken trend
      - S/R proximity — spot at a strong support cluster is the cleanest entry
      - Existing CSP — defers to the CSP as the entry mechanism

    Returns the tier label and a list of reason notes (for rendering).
    """
    notes: list[str] = []

    if has_existing_csp:
        notes.append("CSP already open — the put IS the entry mechanism")
        return QUALITY_DEFERRED_CSP, notes

    # Disqualifying conditions first.
    sma_pct = None
    if sma_200 and spot:
        sma_pct = (spot - sma_200) / sma_200 * 100
    if drawdown_pct is not None and drawdown_pct >= 50 and sma_pct is not None and sma_pct < -15:
        notes.append(f"drawdown {drawdown_pct:.0f}% + {sma_pct:.0f}% below 200-SMA — trend broken")
        return QUALITY_MARGINAL, notes

    # RSI band scoring.
    rsi_score = 0
    if rsi is None:
        notes.append("no RSI available")
    elif 35 <= rsi <= 50:
        rsi_score = 3
        notes.append(f"RSI {rsi:.0f} sweet spot (pullback band)")
    elif 50 < rsi <= 55:
        rsi_score = 2
        notes.append(f"RSI {rsi:.0f} mid-range, acceptable")
    elif 55 < rsi <= 60:
        rsi_score = 1
        notes.append(f"RSI {rsi:.0f} borderline (extended)")
    elif 25 <= rsi < 35:
        rsi_score = 1
        notes.append(f"RSI {rsi:.0f} oversold — wait for base")
    else:
        notes.append(f"RSI {rsi:.0f} outside favored band")

    # Third-party tier scoring.
    tier_score = 0
    if rating_tier is None:
        pass
    elif rating_tier >= 5:
        tier_score = 3
        notes.append(f"tier {rating_tier} (high-conviction catalyst)")
    elif rating_tier >= 4:
        tier_score = 2
        notes.append(f"tier {rating_tier} (Top 15)")
    elif rating_tier >= 3:
        tier_score = 1
        notes.append(f"tier {rating_tier} (Buy)")
    else:
        notes.append(f"tier {rating_tier} (weak rec)")

    # S/R proximity scoring (within 3-5% of a strong support cluster).
    sr_score = 0
    if sr_payload and isinstance(sr_payload, dict) and spot:
        supports = sr_payload.get("supports") or []
        for sup in supports[:3]:
            try:
                sp = float(sup.get("price", 0))
                touches = int(sup.get("touches", 1))
                if sp <= 0:
                    continue
                dist_pct = (spot - sp) / spot
                if 0 < dist_pct <= 0.03 and touches >= 3:
                    sr_score = 2
                    notes.append(f"spot at ${sp:g} {touches}-touch support cluster")
                    break
                elif 0 < dist_pct <= 0.05:
                    sr_score = max(sr_score, 1)
                    notes.append(f"spot near ${sp:g} support")
                    break
            except (TypeError, ValueError):
                continue

    # Drawdown sanity.
    if drawdown_pct is not None:
        if 10 <= drawdown_pct <= 30 and sma_pct is not None and sma_pct >= -5:
            notes.append(f"drawdown {drawdown_pct:.0f}% — healthy correction (trend intact)")

    total = rsi_score + tier_score + sr_score
    if total >= 5:
        return QUALITY_TOP, notes
    if total >= 3:
        return QUALITY_WATCH, notes
    return QUALITY_MARGINAL, notes


def _compute_funding_hint(snapshot_data: dict) -> dict:
    """Compute approximate funding sources for re-deploying capital.

    Walks the snapshot's option positions and surfaces:
      - sum of cash that would be freed by closing all SHORT options at ≥30% profit
      - count of such positions
      - top 3 by absolute freed-cash contribution
    """
    positions = (snapshot_data or {}).get("positions") or []
    candidates: list[dict] = []
    for p in positions:
        if p.get("assetType") != "OPTION":
            continue
        qty = float(p.get("qty", 0) or 0)
        if qty >= 0:
            continue  # only short positions free collateral on close

        # E*TRADE snapshot fields: costPerShare = entry premium per share;
        # currentMid = current option price; totalGainPct = capture %.
        # For a SHORT, capture = (entry - current) / entry. When totalGainPct
        # is negative on a short, that's a LOSS (current > entry), so flip sign.
        entry = float(p.get("costPerShare") or p.get("premiumReceived") or 0)
        current = float(p.get("currentMid") or 0)
        if entry <= 0 or current < 0:
            continue
        # For shorts: profit when current < entry (we sold high, can buy back lower).
        capture_pct = (entry - current) / entry
        if capture_pct < 0.30:
            continue
        strike = float(p.get("strike", 0) or 0)
        is_put = (p.get("type") or "").upper() == "PUT"
        # Put collateral = strike × |qty| × 100. Calls don't tie up cash; they
        # cap shares. We surface puts here because the user's question is
        # "where can I free CASH for entries?"
        freed_cash = strike * abs(qty) * 100 if is_put else 0
        contracts = abs(qty)
        sym = p.get("symbol") or "?"
        profit_per_contract = (entry - current) * 100
        locked_profit = profit_per_contract * contracts
        candidates.append({
            "symbol": sym,
            "freed_cash": freed_cash,
            "locked_profit": locked_profit,
            "capture_pct": capture_pct,
            "is_put": is_put,
        })
    candidates.sort(key=lambda c: -c["freed_cash"])
    total_freed = sum(c["freed_cash"] for c in candidates)
    total_profit = sum(c["locked_profit"] for c in candidates)
    return {
        "total_freed_cash": total_freed,
        "total_locked_profit": total_profit,
        "count": len(candidates),
        "top3": candidates[:3],
    }

try:
    from analysis import iv_honesty, rsi_discipline
    from analysis.put_overlap_check import check_strike_overlap as _check_strike_overlap
except ImportError:  # pragma: no cover - path fallback for standalone runs
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis import iv_honesty, rsi_discipline
    from analysis.put_overlap_check import check_strike_overlap as _check_strike_overlap


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
    gate_state=None,
) -> list:
    """
    Produce a ranked list of long-term opportunity dicts for the briefing.

    Returns an empty list (not an error) when inputs are missing — long-term
    opportunities are enrichment, not load-bearing.

    ``gate_state`` (optional ``analysis.capacity_gates.GateState``): when
    provided and the portfolio capacity gates are closed (stress coverage
    below the 0.50× floor), every surviving new-open rec (LONG_DATED_CSP /
    ADD) carries the ``⏸ Deferred (capacity gated)`` tag — shown, never
    hidden (hard rules #24 / #41). ``gate_state=None`` preserves legacy
    behavior exactly.
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
    # 2026-08-04 (PLTR): core = core_positions ∪ Tier A. PLTR (Tier A, not
    # on core_positions) shipped a "TRIM PLTR — 10.6% NLV" while Risk Alerts
    # said "within Tier A bounds (cap 22%)" — the union makes the TRIM
    # filter and the core target weight agree with the tier framework.
    try:
        from analysis.position_tiers import core_union as _core_union
        core_tickers_set = _core_union(config or {})
    except Exception:
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

    # Build sr_by_ticker from the snapshot's technicals so LT_CSP strike
    # selection can anchor to real support clusters (hard rule #20).
    sr_by_ticker: dict = {}
    for sym, tech in technicals.items():
        if not isinstance(tech, dict):
            continue
        sr = tech.get("support_resistance")
        if isinstance(sr, dict):
            sr_by_ticker[str(sym).upper()] = sr

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
            sr_by_ticker=sr_by_ticker,
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
        if op.get("kind") == "TRIM":
            _t_trim = op.get("ticker")
            if _t_trim in core_tickers_set:
                continue
            # 2026-08-04: explicit Tier B income names defer to their tier
            # cap (concentration_cap_for_tier, 12% default) — the drift
            # alert reads the tier cap, so the TRIM generator must agree
            # (rule #29). Tier C keeps legacy behavior. Fail-open.
            try:
                from analysis.position_tiers import (
                    TIER_B, concentration_cap_for_tier, tier_for,
                )
                if tier_for(_t_trim, config) == TIER_B:
                    _w_trim = float((positions_by_ticker.get(_t_trim) or {})
                                    .get("weight_pct") or 0)
                    if _w_trim <= concentration_cap_for_tier(TIER_B, config):
                        continue
            except Exception:
                pass
        filtered.append(op)
    op_dicts = filtered

    # LT-verdict discipline gate (hard rule #39, audit 2026-07-03). The
    # pipeline computes long_term_verdict for every ticker but this step
    # never consumed it — ZS (LT `broken`, -28% below a falling 200-SMA)
    # shipped both a LONG_DATED_CSP and an ADD. Block (demote to
    # SKIPPED_LT_VERDICT, never hide) any new-open rec on a name whose LT
    # verdict is broken/downtrend/weakening AND spot < 200-SMA. A fresh
    # (≤14d) tier ≥4 BUY overrides with a visible LT-warning annotation.
    try:
        from analysis import lt_verdict_gate as _lvg
    except ImportError:
        _lvg = None
    if _lvg is not None:
        _rec_by_ticker: dict = {}
        for r in (recommendations_list or []):
            if isinstance(r, dict) and r.get("ticker"):
                _rec_by_ticker[str(r["ticker"]).upper()] = r
        _lt_gated: list = []
        for op in op_dicts:
            kind = (op.get("kind") or "").upper()
            if kind not in ("LONG_DATED_CSP", "ADD"):
                _lt_gated.append(op)
                continue
            t = (op.get("ticker") or "").upper()
            g = _lvg.check_lt_verdict_gate(t, snapshot_data, _rec_by_ticker.get(t))
            if not g["pass"]:
                op["skip_reason"] = g["reason"]
                op["kind_when_skipped"] = kind
                op["kind"] = "SKIPPED_LT_VERDICT"
                op["lt_verdict"] = g.get("verdict")
            elif g.get("warning"):
                op["lt_verdict"] = g.get("verdict")
                op["lt_verdict_warning"] = g["warning"]
                op.setdefault("trigger_reasons", []).insert(0, f"⚠ {g['warning']}")
            _lt_gated.append(op)
        op_dicts = _lt_gated

    # Filter: respect existing short-put positions for LONG_DATED_CSP.
    # The advisor doesn't see the user's existing short put exposure, so
    # without this gate it can suggest stacking a 3rd put on a name where
    # they already have 2 open, OR suggest a strike very close to an
    # existing position (which compounds risk without diversifying).
    MAX_EXISTING_SHORT_PUTS_PER_NAME = 2
    STRIKE_OVERLAP_PCT = 0.05  # 5% — strikes within this range are "overlapping"
    existing_puts_by_ticker: dict = {}
    # CRITICAL: a LONG put on the underlying is downside protection (collar floor
    # or protective put). Selling a SHORT put at the same strike cancels that
    # hedge. Track long puts separately so the LT_CSP path can refuse to
    # recommend a short put that would un-collar an existing position. (The
    # original code only tracked shorts via `if qty >= 0: continue`.)
    long_puts_by_ticker: dict = {}
    for p in (positions or []):
        if p.get("assetType") != "OPTION":
            continue
        if (p.get("type") or "").upper() != "PUT":
            continue
        qty = float(p.get("qty", 0) or 0)
        t = (p.get("underlying") or "").upper()
        if not t:
            continue
        strike = float(p.get("strike", 0) or 0)
        if qty < 0:
            entry = existing_puts_by_ticker.setdefault(t, {"count": 0, "strikes": []})
            entry["count"] += abs(qty)
            entry["strikes"].append(strike)
        elif qty > 0:
            lentry = long_puts_by_ticker.setdefault(t, {"count": 0, "strikes": []})
            lentry["count"] += abs(qty)
            lentry["strikes"].append(strike)

    filtered_again: list = []
    for op in op_dicts:
        if op.get("kind") != "LONG_DATED_CSP":
            filtered_again.append(op)
            continue
        t = (op.get("ticker") or "").upper()

        # Long-put cancellation check (HIGHEST priority): if the user holds a
        # LONG put on this name at/near the proposed strike, that's downside
        # protection (collar floor / protective put). Selling a short put at the
        # same strike cancels the hedge — refuse the recommendation outright.
        import re as _re
        sm = _re.search(r"\$(\d+(?:\.\d+)?)P\b", op.get("concrete_trade", ""))
        proposed_strike = float(sm.group(1)) if sm else None
        long_held = long_puts_by_ticker.get(t)
        if long_held and proposed_strike is not None:
            cancelling_strike = None
            for held_strike in long_held["strikes"]:
                if held_strike > 0 and abs(proposed_strike - held_strike) / held_strike <= STRIKE_OVERLAP_PCT:
                    cancelling_strike = held_strike
                    break
            if cancelling_strike is not None:
                op["skip_reason"] = (
                    f"you already hold a LONG ${cancelling_strike:g}P on {t} as downside "
                    f"protection (collar floor / protective put). Selling a "
                    f"${proposed_strike:g}P at the same strike would cancel that hedge — "
                    f"don't un-collar a hedged position to collect premium."
                )
                op["kind_when_skipped"] = "LONG_DATED_CSP"
                op["kind"] = "SKIPPED_LT_CSP"
                filtered_again.append(op)
                continue

        existing = existing_puts_by_ticker.get(t)
        if not existing:
            filtered_again.append(op)
            continue
        # Already-stacked check
        if existing["count"] >= MAX_EXISTING_SHORT_PUTS_PER_NAME:
            op["skip_reason"] = (
                f"already {int(existing['count'])} short puts open at strikes "
                f"{sorted(existing['strikes'])} — stacking another compounds "
                f"single-name assignment risk"
            )
            op["kind_when_skipped"] = "LONG_DATED_CSP"
            op["kind"] = "SKIPPED_LT_CSP"
            filtered_again.append(op)
            continue
        # Strike-overlap check (hard rule #40): parse proposed strike from
        # concrete_trade and run the shared analysis.put_overlap_check —
        # the same function that gates PULLBACK_CSP and validator Rule 15.
        import re as _re
        sm = _re.search(r"\$(\d+(?:\.\d+)?)P\b", op.get("concrete_trade", ""))
        if sm:
            proposed_strike = float(sm.group(1))
            ov = _check_strike_overlap(
                t, proposed_strike, existing["strikes"],
                overlap_pct=STRIKE_OVERLAP_PCT,
            )
            if ov["overlap"]:
                op["skip_reason"] = (
                    f"proposed ${proposed_strike:g}P is within "
                    f"{STRIKE_OVERLAP_PCT*100:.0f}% of existing "
                    f"${ov['existing_strike']:g}P — concentrates rather than diversifies"
                )
                op["kind_when_skipped"] = "LONG_DATED_CSP"
                op["kind"] = "SKIPPED_LT_CSP"
        filtered_again.append(op)
    op_dicts = filtered_again

    # LT_ADD discipline gate (hard rule #22). $5K "starter" ADD recs are
    # filler when:
    #   (a) the user already has an open CSP on the name — the CSP IS the
    #       entry mechanism, equity adds redundant exposure at a worse price
    #   (b) stress coverage is below 0.30x — the system is in defensive mode,
    #       new long exposure compounds the problem we're trying to fix
    #   (c) cash floor < 5% NLV — recently-experienced margin call lesson;
    #       deploying cash into low-conviction starters is the wrong move
    # When tier >= 4 (Top 15 / Top Stock to Buy per Parkev's ladder) AND
    # NO existing CSP, PROMOTE the starter from $5K to $20K so the size
    # expresses the conviction.
    add_cfg = (config or {}).get("lt_add_discipline", {}) or {}
    suppress_coverage_below = float(add_cfg.get("suppress_below_coverage", 0.30))
    suppress_cash_below_pct = float(add_cfg.get("suppress_below_cash_pct", 0.05))
    promote_tier_min = int(add_cfg.get("promote_tier_min", 4))
    promote_size = float(add_cfg.get("promote_to_size_usd", 20_000))

    # Snapshot the gate inputs.
    _cash = float(balance.get("cash", 0) or 0)
    _cash_pct = (_cash / nlv) if nlv > 0 else 0.0
    _coverage = None  # the long-term-advisor step doesn't take analytics yet;
                      # safest default = unknown → don't suppress on coverage.
                      # When analytics is available upstream, plumb it here.
    # Snapshot recommendations_list for tier lookup.
    _tier_by_ticker: dict = {}
    for r in (recommendations_list or []):
        if isinstance(r, dict):
            tk = (r.get("ticker") or "").upper()
            rt = r.get("rating_tier")
            if tk and rt is not None:
                try:
                    _tier_by_ticker[tk] = int(rt)
                except (TypeError, ValueError):
                    pass

    add_demoted: list = []
    for op in op_dicts:
        if (op.get("kind") or "").upper() != "ADD":
            add_demoted.append(op)
            continue
        t = (op.get("ticker") or "").upper()

        # Gate 1: suppress when cash floor breached (post-margin-call discipline).
        if _cash_pct < suppress_cash_below_pct:
            op["skip_reason"] = (
                f"cash floor breached — only {_cash_pct*100:.1f}% NLV in cash "
                f"(< {suppress_cash_below_pct*100:.0f}% floor). Deploying capital "
                f"into low-conviction starter positions when defensive room is thin "
                f"is the lesson Friday's margin call taught us. Skip until cash "
                f"is rebuilt."
            )
            op["kind_when_skipped"] = "ADD"
            op["kind"] = "SKIPPED_ADD"
            # Score the setup quality so the renderer can tier these into
            # TOP picks (worth funding) vs WATCHLIST vs MARGINAL vs DEFERRED_CSP.
            # IMPORTANT: include has_existing_csp here. Even when cash floor
            # suppresses the rec, the user wants to see that the CSP is the
            # entry mechanism so they don't think "free cash = buy these" when
            # really "your CSP is already doing this."
            sr_payload = (technicals.get(t) or {}).get("support_resistance") \
                         if isinstance(technicals.get(t), dict) else None
            spot_for_score = positions_by_ticker.get(t, {}).get("spot")
            _csp_here = existing_puts_by_ticker.get(t)
            _has_csp = bool(_csp_here and _csp_here.get("count", 0) > 0)
            tier, q_notes = _score_lt_add_setup(
                op,
                rsi=rsi_values.get(t),
                sma_200=sma_200_values.get(t),
                drawdown_pct=drawdown_pcts.get(t),
                spot=spot_for_score,
                rating_tier=_tier_by_ticker.get(t),
                has_existing_csp=_has_csp,
                sr_payload=sr_payload,
            )
            op["quality_tier"] = tier
            op["quality_notes"] = q_notes
            add_demoted.append(op)
            continue

        # Gate 2: suppress when stress coverage is in defensive territory.
        if _coverage is not None and _coverage < suppress_coverage_below:
            op["skip_reason"] = (
                f"stress coverage {_coverage:.2f}× < {suppress_coverage_below:.2f}× "
                f"defensive floor. Adding long equity exposure while in defensive "
                f"posture compounds the problem; rebuild coverage first."
            )
            op["kind_when_skipped"] = "ADD"
            op["kind"] = "SKIPPED_ADD"
            add_demoted.append(op)
            continue

        # Gate 3: demote when user already has a short put on this name.
        # The CSP IS the entry mechanism — equity here double-pays at a worse
        # cost basis than the put strike.
        existing_csp = existing_puts_by_ticker.get(t)
        if existing_csp and existing_csp.get("count", 0) > 0:
            strikes = sorted(set(existing_csp.get("strikes", [])))
            strikes_str = ", ".join(f"${s:g}" for s in strikes)
            op["skip_reason"] = (
                f"you already have {int(existing_csp['count'])} short put(s) on {t} "
                f"at strike(s) {strikes_str} — the CSP IS the entry mechanism for "
                f"this name. Adding equity at spot pays MORE per share than your "
                f"put-assignment cost basis. Let the put do the work; if you want "
                f"more exposure, sell another CSP at a lower strike rather than "
                f"buying equity at the higher current price."
            )
            op["kind_when_skipped"] = "ADD"
            op["kind"] = "DEFERRED_ADD_HAS_CSP"
            op["quality_tier"] = QUALITY_DEFERRED_CSP
            op["quality_notes"] = [f"existing CSP at {strikes_str} — CSP is the entry"]
            add_demoted.append(op)
            continue

        # Gate 4 (promote, not suppress): tier 4-5 high-conviction ADD with NO
        # existing CSP gets a meaningful size, not the default $5K filler.
        tier = _tier_by_ticker.get(t)
        if tier is not None and tier >= promote_tier_min:
            # Recompute share count for the promoted size.
            try:
                spot_str = re.search(r"~\$([\d,.]+)\)", op.get("concrete_trade", "") or "")
                spot_for_shares = float(spot_str.group(1).replace(",", "")) if spot_str else 0.0
            except (ValueError, AttributeError):
                spot_for_shares = 0.0
            new_shares = int(promote_size / spot_for_shares) if spot_for_shares > 0 else 0
            old_size_str = re.search(r"~\$[\d,]+", op.get("concrete_trade", "") or "")
            if old_size_str and new_shares > 0:
                op["concrete_trade"] = (
                    op["concrete_trade"].replace(
                        old_size_str.group(0),
                        f"~${promote_size:,.0f}"
                    ).replace(
                        re.search(r"\(~\d+ shares", op["concrete_trade"]).group(0),
                        f"(~{new_shares} shares"
                    )
                )
                op["promoted_tier"] = tier
                op.setdefault("trigger_reasons", []).insert(
                    0, f"🌟 Parkev tier-{tier} high-conviction — sized meaningfully"
                )

        add_demoted.append(op)
    op_dicts = add_demoted

    # Pre-trade validator pass — for every surviving LONG_DATED_CSP, run the
    # discipline checkpoint (earnings window, bucket concentration, etc.)
    # and attach findings to the op so the renderer can surface BLOCKs/WARNs.
    # This catches LT_CSP recs that the per-stage gates upstream don't fully
    # cover (e.g., earnings 9 days out on a 75-DTE contract — the cluster
    # check + existing-put guard don't see it).
    try:
        from analysis import pre_trade_validator as _ptv
        for op in op_dicts:
            if (op.get("kind") or "").upper() != "LONG_DATED_CSP" or op.get("skip_reason"):
                continue
            ticker = (op.get("ticker") or "").upper()
            # Parse strike + expiration from the concrete_trade text (e.g.
            # "SELL 1× AMD $460P exp Fri Aug 21 '26" → strike=460, exp later
            # filled in via _enrich_long_dated_dates). Best-effort.
            ct = op.get("concrete_trade") or ""
            sm = re.search(r"\$(\d+(?:\.\d+)?)P\b", ct)
            if not sm:
                continue
            try:
                strike = float(sm.group(1))
            except ValueError:
                continue
            # Use the expiration the enrichment pass picked, fall back to
            # an offset from today if none yet assigned.
            from datetime import date as _date, timedelta as _td
            exp_iso = op.get("expiration") or op.get("expiration_iso")
            if exp_iso:
                try:
                    y, m, d = str(exp_iso).split("-")
                    exp_d = _date(int(y), int(m), int(d))
                except (ValueError, AttributeError):
                    exp_d = _date.today() + _td(days=75)
            else:
                exp_d = _date.today() + _td(days=75)
            try:
                ctx = _ptv.build_context_from_snapshot(
                    snapshot_data,
                    ticker=ticker, strike=strike, expiration=exp_d,
                    option_type="PUT", action="SELL_OPEN", quantity=1,
                    recommendations_list=recommendations_list,
                )
                findings = _ptv.validate_proposed_trade(ctx, config)
                if findings:
                    op["validator_findings"] = [
                        {"severity": f.severity, "reason": f.reason,
                         "rule_id": f.rule_id, "detail": f.detail}
                        for f in findings
                    ]
                    # Rule #43 (RDDT): an UNKNOWN earnings date is a WARN,
                    # not a silent pass — surface it ON the card so the
                    # ticket can't read as earnings-clean when the calendar
                    # simply had nothing.
                    if any(f.rule_id == "EARNINGS_DATE_UNKNOWN"
                           for f in findings):
                        triggers = op.setdefault("trigger_reasons", [])
                        if not any("earnings unverified" in str(x)
                                   for x in triggers):
                            triggers.insert(0, (
                                f"⚠ earnings unverified — no {ticker} "
                                f"earnings date from the calendar; verify "
                                f"no print before {exp_d} at the broker "
                                f"before placing"))
                    # If any BLOCK fires, demote the rec — it's not safely actionable.
                    if _ptv.has_blockers(findings):
                        block_reasons = "; ".join(
                            f.rule_id for f in findings if f.severity == _ptv.SEV_BLOCK
                        )
                        op["skip_reason"] = f"pre-trade validator BLOCK: {block_reasons}"
                        op["kind_when_skipped"] = "LONG_DATED_CSP"
                        op["kind"] = "SKIPPED_LT_CSP"
            except Exception:
                # Validator is enrichment — silent on errors.
                pass
    except ImportError:
        pass

    # RSI discipline — a LONG_DATED_CSP is a put-sale, so an overbought tape
    # (RSI > 70) hard-blocks the new open, consistent with the rest of the
    # briefing. Then annotate EVERY surviving opportunity with its RSI so the
    # value shows on the line (added to trigger_reasons, which both the
    # advisor's renderer and the fallback renderer print).
    rsi_th = rsi_discipline.load_thresholds(config)

    def _rsi_of(ticker: str):
        return rsi_values.get(ticker) or rsi_values.get((ticker or "").upper())

    # kind → wheel side for the gate. LONG_DATED_CSP sells a put; ADD buys
    # shares; everything else (TRIM/EXIT/HOLD/LEAP) is management/other.
    _SIDE_BY_KIND = {"LONG_DATED_CSP": "put", "ADD": "buy"}
    if rsi_th.get("enabled", True):
        for op in op_dicts:
            side = _SIDE_BY_KIND.get((op.get("kind") or "").upper())
            if not side or op.get("skip_reason"):
                continue
            a = rsi_discipline.assess(_rsi_of(op.get("ticker")), side, rsi_th)
            if a.blocked:
                op["skip_reason"] = a.reason
                op["kind_when_skipped"] = (op.get("kind") or "").upper()
                op["kind"] = "SKIPPED_RSI" if side == "buy" else "SKIPPED_LT_CSP"
            elif side == "put":
                # Extended-band demotion (rule #43, AMZN 2026-07-31): RSI
                # 60-70 = "extended — wait for a pullback." The rec keeps its
                # full ticket (chain enrichment still runs) but the renderer
                # moves it into the "⏸ CSPs — wait for a pullback" subsection
                # instead of a numbered actionable rec. Config-nullable via
                # rsi_discipline.put_extended_wait_band.
                w = rsi_discipline.put_extended_wait(_rsi_of(op.get("ticker")), rsi_th)
                if w:
                    op["rsi_wait"] = True
                    op["rsi_wait_reason"] = w

    for op in op_dicts:
        rsi = _rsi_of(op.get("ticker"))
        if rsi is None:
            continue
        triggers = op.setdefault("trigger_reasons", [])
        if any("RSI" in str(x) for x in triggers):
            continue
        side = _SIDE_BY_KIND.get((op.get("kind") or "").upper())
        label = rsi_discipline.tag(rsi)
        if side and rsi_discipline.hook(side, rsi, rsi_th).promoted:
            label = "✅ RSI favourable · " + label
        triggers.insert(0, label)

    # Snap each LONG_DATED_CSP / LEAP_CALL to a real chain expiration so the
    # briefing renders a concrete date instead of "~75 DTE".
    chains = snapshot_data.get("chains", {}) or {}
    _enrich_long_dated_dates(op_dicts, chains, target_dte_csp=75, target_dte_leap=365)
    # Pull REAL premium/bid/ask from live yfinance chains for each LT_CSP so
    # the briefing doesn't ship spot×2.5% rule-of-thumb estimates.
    _enrich_with_live_premiums(
        op_dicts, iv_ranks=iv_ranks, technicals=technicals, config=config,
        chain_iv=snapshot_data.get("chain_iv"),
    )

    # Universal capacity-gate DEFERRED tag (hard rules #24 / #41, audit
    # 2026-07-03 finding #7): when stress coverage sits below the 0.50×
    # floor, every SURVIVING new-open rec here carries the deferred tag —
    # the full ticket still renders (never hidden), but it can't read as a
    # green-lit trade while the book can't cover what it already has.
    try:
        from analysis import capacity_gate as _cap
        _cap_tag = _cap.capacity_deferred_tag(gate_state, config)
        if _cap_tag:
            for op in op_dicts:
                if ((op.get("kind") or "").upper() in ("LONG_DATED_CSP", "ADD")
                        and not op.get("skip_reason")):
                    op["capacity_deferred"] = True
                    triggers = op.setdefault("trigger_reasons", [])
                    if not any(_cap.DEFERRED_TAG in str(x) for x in triggers):
                        triggers.insert(0, _cap_tag)
    except ImportError:
        pass

    # Rule #43 (GOOG 2026-07-31): every LONG_DATED_CSP card carries the
    # equity-stacking read the Rotation Playbook enforces — the LTO section
    # must never recommend a short put the playbook two sections later
    # hard-skips for equity concentration, without saying so on the card.
    _annotate_equity_stacking(op_dicts, snapshot_data, config)

    # Rule #43 (GOOG 2026-08-03): a fully-gated card must not hold a numbered
    # "Trade:" slot. Marks reference_demoted on cards whose disqualifiers
    # overcome the numbered-Trade-card framing; the renderer moves them to
    # the unnumbered "📎 Shown for reference" subsection (rule #24 — full
    # ticket preserved, never hidden).
    _mark_reference_demotions(op_dicts, snapshot_data, config)

    # Compute a funding-hint footer so the Skipped section can tell the user
    # how much cash they could free by closing their high-capture short puts
    # today (the path back to deploying the suppressed ADD recs). Stashed
    # under a sentinel kind that the renderer will pull out before rendering.
    funding = _compute_funding_hint(snapshot_data)
    if funding["total_freed_cash"] > 0 or funding["total_locked_profit"] > 0:
        op_dicts.append({
            "kind": "_FUNDING_HINT",
            "ticker": "",
            "funding": funding,
        })
    return op_dicts


def _annotate_equity_stacking(
    op_dicts: list,
    snapshot_data: dict,
    config: dict | None,
) -> None:
    """Rule #43 (GOOG 2026-07-31) — annotate every LONG_DATED_CSP card with
    the equity-stacking read from the shared ``analysis.equity_stacking``
    module (the same gate the Rotation Playbook enforces).

    Observed defect: the LTO section rendered "💎 6. LONG DATED CSP · GOOG —
    SELL 1× GOOG $330P" with ✅ RSI favourable and NO concentration context,
    while the playbook hard-skipped the identical trade ("⛔ GOOG $330P
    skipped — holds 15.9% NLV in GOOG equity (≥ 10% NLV hard-skip)").

    ≥10% NLV held → prominent ⛔ hard-skip-zone annotation; 5-10% → modest
    ⚠ warning; <5% → nothing. The card stays visible either way (hard rule
    #24) — it just can't carry ✅-favorable framing without the context.
    Fail-open: missing positions/NLV data → no annotation.
    """
    try:
        from analysis.equity_stacking import (
            equity_pct_by_ticker, stacking_card_annotation)
    except ImportError:
        return
    try:
        balance = (snapshot_data or {}).get("balance") or {}
        nlv = float(balance.get("accountValue") or balance.get("netValue") or 0)
        eq_pcts = equity_pct_by_ticker(
            (snapshot_data or {}).get("positions") or [], nlv)
        if not eq_pcts:
            return
        es_cfg = (((config or {}).get("rotation_playbook") or {})
                  .get("equity_stacking") or {})
        for op in op_dicts:
            kind = (op.get("kind_when_skipped") or op.get("kind") or "").upper()
            if kind != "LONG_DATED_CSP":
                continue
            tk = (op.get("ticker") or "").upper()
            note = stacking_card_annotation(tk, eq_pcts.get(tk), es_cfg)
            if not note:
                continue
            triggers = op.setdefault("trigger_reasons", [])
            if not any("equity-stacking" in str(x) or "equity concentration"
                       in str(x) for x in triggers):
                triggers.insert(0, note)
    except Exception as e:
        print(f"  [warn] equity-stacking annotation failed (non-fatal): {e}",
              file=sys.stderr)


def _mark_reference_demotions(
    op_dicts: list,
    snapshot_data: dict,
    config: dict | None,
) -> None:
    """Rule #43 (GOOG 2026-08-03) — fully-gated cards lose the numbered
    "Trade:" slot.

    Observed defect: "### 💎 6. LONG DATED CSP · GOOG — Trade: SELL 1× GOOG
    $330P..." rendered as a numbered opportunity while carrying FOUR
    disqualifiers (⛔ equity-stacking hard-skip at 16.2% NLV, ⏸ capacity
    gated, RSI 48 ⚠ pre-gap stale on a +8.8% move, thin-premium reconsider).
    The annotations don't overcome the numbered-Trade-card framing — George
    read it as a recommendation to sell a put on a green day.

    A surviving new-open card (LONG_DATED_CSP / ADD) is demoted to the
    unnumbered "📎 Shown for reference — not actionable today" subsection
    when ANY of:
      (a) the equity-stacking ⛔ hard-skip fired on the card;
      (b) the vintage guard stale-tagged the RSI that was its qualifying
          trigger (ticker moved past vintage_guard.max_intraday_move_pct
          since the RSI was computed — the promote/keep read is stale);
      (c) ≥2 independent hard gates fired. The capacity gate counts here
          but NEVER demotes alone — deferred-for-capacity cards keep their
          numbered planning value (rules #24 / #41).

    Config: ``lto_reference_demotion: {enabled, stale_rsi_demotes,
    hard_skip_demotes}`` (all default true). The per-gate toggles disable
    the single-gate demotion path (a)/(b); the ≥2 combination rule (c)
    still counts a fired gate factually. Fail-open: any error → no card
    is ever demoted on missing data.
    """
    cfg = ((config or {}).get("lto_reference_demotion") or {})
    if not isinstance(cfg, dict):
        cfg = {}
    if not cfg.get("enabled", True):
        return
    stale_rsi_demotes = bool(cfg.get("stale_rsi_demotes", True))
    hard_skip_demotes = bool(cfg.get("hard_skip_demotes", True))
    try:
        try:
            from analysis import vintage_guard as _vg
            vg_flags = _vg.compute_flags(
                (snapshot_data or {}).get("quotes") or {},
                (snapshot_data or {}).get("technicals") or {},
                config if isinstance(config, dict) else None,
            )
        except Exception:
            vg_flags = {}
        for op in op_dicts:
            kind = (op.get("kind") or "").upper()
            if kind not in ("LONG_DATED_CSP", "ADD"):
                continue
            if op.get("skip_reason") or op.get("rsi_wait"):
                continue  # already demoted by a stronger surface
            triggers = [str(t) for t in (op.get("trigger_reasons") or [])]
            gates: list[tuple[str, str]] = []
            if any("⛔ equity-stacking hard-skip" in t for t in triggers):
                gates.append(("hard_skip", "equity-stacking hard-skip"))
            flag = vg_flags.get((op.get("ticker") or "").upper())
            if flag and any("RSI" in t for t in triggers):
                gates.append((
                    "stale_rsi",
                    f"stale qualifying RSI (spot moved "
                    f"{flag['move_pct']:+.1f}% since computation)",
                ))
            if op.get("capacity_deferred"):
                gates.append(("capacity", "capacity gated"))
            fired = {g for g, _ in gates}
            demote = (
                ("hard_skip" in fired and hard_skip_demotes)
                or ("stale_rsi" in fired and stale_rsi_demotes)
                or len(gates) >= 2
            )
            if demote:
                op["reference_demoted"] = True
                op["reference_reason"] = " + ".join(lbl for _, lbl in gates)
    except Exception as e:
        print(f"  [warn] reference-demotion marking failed (non-fatal): {e}",
              file=sys.stderr)


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
        # Skip ones already marked as skipped (no need to enrich)
        if op.get("skip_reason"):
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


def _enrich_with_live_premiums(
    op_dicts: list,
    iv_ranks: dict | None = None,
    technicals: dict | None = None,
    config: dict | None = None,
    chain_iv: dict | None = None,
) -> None:
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
        if op.get("skip_reason"):
            continue  # already marked as skipped — no chain fetch needed
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
        # Post-gap IV-rank honesty (rule #43, AMZN 2026-07-31): cross-check any
        # "fat premium" claim against the DELIVERED annualized yield. Claimed-fat
        # (IV rank ≥ 60) + delivered-thin (< 20% annualized) → honest gap-aware
        # text + the universal reconsider note; otherwise the legacy < $500
        # absolute-thin check (the NFLX path) still applies. Single source of
        # truth: analysis/iv_honesty.py. Fail-open on missing data.
        if op.get("rationale") and "fat premium" in op["rationale"]:
            _t = (op.get("ticker") or "").upper()
            _iv = (iv_ranks or {}).get(_t) or (iv_ranks or {}).get(op.get("ticker"))
            # Task #43: prefer the TRUE chain-implied rank when measured this
            # cycle — post-crush it is already low, so a "fat premium" claim
            # is checked against reality instead of the gap-inflated
            # realized-vol proxy. The delivered-yield cross-check below stays
            # as belt-and-suspenders either way.
            _civ_m = (chain_iv or {}).get(_t)
            if isinstance(_civ_m, dict) and _civ_m.get("iv_rank") is not None:
                _iv = _civ_m["iv_rank"]
            _tech = (technicals or {}).get(_t) or (technicals or {}).get(op.get("ticker"))
            rewritten = iv_honesty.rewrite_fat_premium(
                op["rationale"],
                iv_rank=_iv,
                annualized_pct=annualized,
                premium_total=premium_total,
                tech_entry=_tech if isinstance(_tech, dict) else None,
                config=config,
            )
            if rewritten:
                op["rationale"] = rewritten
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

    # Partition into active opportunities vs ones we skipped because of
    # existing positions / strike overlaps. Skipped ones get a compact footer.
    # _FUNDING_HINT is metadata, not a renderable opportunity — pull it out.
    funding_hint = None
    cleaned: list = []
    for op in opportunities:
        if (op.get("kind") or "") == "_FUNDING_HINT":
            funding_hint = op.get("funding")
            continue
        cleaned.append(op)
    opportunities = cleaned

    active = [op for op in opportunities if not (op.get("kind") or "").startswith("SKIPPED")
              and (op.get("kind") or "") != "DEFERRED_ADD_HAS_CSP"]
    skipped = [op for op in opportunities if (op.get("kind") or "").startswith("SKIPPED")
               or (op.get("kind") or "") == "DEFERRED_ADD_HAS_CSP"]

    # Extended-band demotion (rule #43): rsi_wait recs never render as numbered
    # actionable recs — they move to a "⏸ CSPs — wait for a pullback"
    # subsection below, full ticket shown (hard rule #24, never hidden).
    rsi_wait_ops = [op for op in active if op.get("rsi_wait")]
    active = [op for op in active if not op.get("rsi_wait")]

    # Reference demotion (rule #43, GOOG 2026-08-03): fully-gated cards
    # (equity-stacking hard-skip / stale qualifying RSI / ≥2 hard gates)
    # never hold a numbered "Trade:" slot. They render unnumbered at the
    # END of the section with the full ticket preserved (rule #24). Pulling
    # them out BEFORE enumerate() keeps the numbered slots gap-free.
    reference_ops = [op for op in active if op.get("reference_demoted")]
    active = [op for op in active if not op.get("reference_demoted")]

    lines = [
        "## 🔭 Long-Term Opportunities (3-12mo horizon)",
        "",
        f"_{len(active)} signal(s) — third-party recs × RSI × drawdown × IV rank × 200-SMA._",
        "",
    ]
    for n, op in enumerate(active, 1):
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

    # ⏸ CSPs — wait for a pullback (rule #43): extended-band (RSI 60-70) new
    # put-sales. Full ticket rendered (rule #24) with the ⏸ reason — shown for
    # planning, never a numbered green-lit rec. The >70 hard block is separate
    # (those land in Skipped).
    if rsi_wait_ops:
        lines.append("### ⏸ CSPs — wait for a pullback")
        lines.append("")
        lines.append("_New put-sales with RSI 60-70 (extended). Full ticket shown — "
                     "never hidden — but selling into a green streak sets the strike "
                     "against an inflated spot. Re-check on a red day / RSI 35-55._")
        lines.append("")
        for op in rsi_wait_ops:
            emoji = {"LONG_DATED_CSP": "💎"}.get(op.get("kind", ""), "•")
            lines.append(
                f"#### ⏸ {emoji} {op.get('kind', '?').replace('_', ' ')} · "
                f"`{op.get('ticker', '?')}` — wait for a pullback"
            )
            lines.append("")
            if op.get("rsi_wait_reason"):
                lines.append(f"- **{op['rsi_wait_reason']}**")
            if op.get("concrete_trade"):
                lines.append(f"- **Trade (when RSI cools):** {op['concrete_trade']}")
            if op.get("trigger_reasons"):
                lines.append(f"- **Triggers:** {'; '.join(op['trigger_reasons'])}")
            if op.get("rationale"):
                lines.append(f"- **Rationale:** {op['rationale']}")
            if op.get("yield_or_cost"):
                lines.append(f"- **Yield/Cost:** {op['yield_or_cost']}")
            if op.get("source"):
                lines.append(f"- **Source:** {op['source']}")
            lines.append("")

    # Footer: surface skipped recs, but GROUP and RANK them so the user can
    # see which are worth re-considering vs which are redundant. The
    # quality_tier attached during the gating step drives the grouping:
    #   - TOP picks (would be the cleanest entries if you free up cash)
    #   - WATCHLIST (acceptable second-tier)
    #   - DEFERRED to CSP (you already have a put — the CSP IS the entry)
    #   - MARGINAL / RSI / position guards (skip even with cash)
    if skipped:
        # Bucket by quality tier (only ADDs have tiers; CSPs go to "other").
        by_tier: dict = {QUALITY_TOP: [], QUALITY_WATCH: [],
                         QUALITY_DEFERRED_CSP: [], QUALITY_MARGINAL: [], "_OTHER": []}
        for op in skipped:
            kind_w = (op.get("kind_when_skipped") or "").upper()
            if kind_w == "ADD" and op.get("quality_tier"):
                by_tier[op["quality_tier"]].append(op)
            else:
                by_tier["_OTHER"].append(op)

        lines.append("### ⏸ Skipped (position + RSI discipline guards)")
        lines.append("")

        if by_tier[QUALITY_TOP]:
            lines.append("**🥇 Top entries if you free cash** — clean RSI band + favorable third-party rec + near support")
            lines.append("")
            for op in by_tier[QUALITY_TOP]:
                t = op.get("ticker", "?")
                notes = "; ".join(op.get("quality_notes") or [])
                lines.append(f"- **{t}** — {notes}")
            lines.append("")

        if by_tier[QUALITY_WATCH]:
            lines.append("**🥈 Watchlist** — acceptable but not the cleanest setup")
            lines.append("")
            for op in by_tier[QUALITY_WATCH]:
                t = op.get("ticker", "?")
                notes = "; ".join(op.get("quality_notes") or [])
                lines.append(f"- **{t}** — {notes}")
            lines.append("")

        if by_tier[QUALITY_DEFERRED_CSP]:
            lines.append("**🛡️ Deferred to existing CSP** — the put IS your entry; don't double-pay")
            lines.append("")
            for op in by_tier[QUALITY_DEFERRED_CSP]:
                t = op.get("ticker", "?")
                notes = "; ".join(op.get("quality_notes") or [])
                lines.append(f"- **{t}** — {notes}")
            lines.append("")

        if by_tier[QUALITY_MARGINAL]:
            lines.append("**⚠️ Marginal** — broken trend or weak setup; skip even with cash")
            lines.append("")
            for op in by_tier[QUALITY_MARGINAL]:
                t = op.get("ticker", "?")
                notes = "; ".join(op.get("quality_notes") or [])
                lines.append(f"- **{t}** — {notes}")
            lines.append("")

        if by_tier["_OTHER"]:
            for op in by_tier["_OTHER"]:
                ticker = op.get("ticker", "?")
                reason = op.get("skip_reason") or "duplicate or overlapping exposure"
                kind = (op.get("kind_when_skipped") or "LT_CSP").replace("LONG_DATED_CSP", "LT_CSP")
                lines.append(f"- **{ticker} {kind}** — {reason}")
            lines.append("")

        # Funding hint footer — show the user HOW to free cash if they want
        # to act on the Top picks. This converts the "skip until cash rebuilt"
        # vagueness into an actionable next step.
        if funding_hint and (funding_hint.get("total_freed_cash", 0) > 0 or
                              funding_hint.get("total_locked_profit", 0) > 0):
            total_cash = funding_hint["total_freed_cash"]
            total_profit = funding_hint["total_locked_profit"]
            n = funding_hint["count"]
            lines.append(f"**💰 How to fund deployment:** Closing your {n} short put(s) at ≥30% capture "
                         f"would free **${total_cash:,.0f}** of collateral and lock **${total_profit:,.0f}** "
                         f"of theta. Top contributors:")
            for c in funding_hint.get("top3", []):
                lines.append(f"  - `{c['symbol']}` — frees ${c['freed_cash']:,.0f} + locks ${c['locked_profit']:,.0f} "
                             f"({c['capture_pct']*100:.0f}% captured)")
            lines.append("")

    # 📎 Shown for reference (rule #43, GOOG 2026-08-03): fully-gated cards.
    # Unnumbered, at the END of the section, with a one-line reason and the
    # full ticket preserved (rule #24) — planning value without the
    # numbered-Trade-card framing that reads as a recommendation.
    if reference_ops:
        lines.append("### 📎 Shown for reference — not actionable today")
        lines.append("")
        lines.append("_These cards carry hard disqualifiers (equity-stacking "
                     "hard-skip, stale qualifying RSI, or stacked gates) that "
                     "overcome the trade framing. Full ticket kept for planning "
                     "— this is NOT a recommendation to place today._")
        lines.append("")
        for op in reference_ops:
            emoji = {
                "ADD": "📈", "LONG_DATED_CSP": "💎",
            }.get(op.get("kind", ""), "•")
            lines.append(
                f"#### 📎 {emoji} {op.get('kind', '?').replace('_', ' ')} · "
                f"`{op.get('ticker', '?')}` — reference only"
            )
            lines.append("")
            if op.get("reference_reason"):
                lines.append(f"- **Why not actionable:** {op['reference_reason']}")
            if op.get("concrete_trade"):
                lines.append(f"- **Trade (reference only):** {op['concrete_trade']}")
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
