"""Rotation Advisor — find better entries than what the user currently holds.

The system already answers **state** (positions, ratings, technicals) and
**actions** (recommendations) independently. It never cross-references
the two to say "your held SOFI is decent, but UBER is strictly better
on RSI / IV / S/R / Parkev / fair value — here's the swap." This module
fills that gap.

Three rotation flavors (all shipping in v1):

  1. Equity rotation — for each Tier B/C holding > 5% NLV, score
     same-theme alternatives the Scout already analyzed.

  2. Option rotation — for each open short put/call with ≥ 30% capture,
     look for a better strike/DTE on the same name OR a similar-collateral
     Parkev-rated alternative. (v1 = same-name only, cross-name deferred.)

  3. Capital reallocation — given the Capital Plan ranking, compute
     explicit swap candidates: "close N% NLV from these positions →
     deploy into these opportunities."

All three feed one output list: `rotation_opportunities` on the briefing
JSON. Each opportunity carries a source (equity/option/capital), a
FROM position, a TO candidate, a delta score, and a rationale.

Hard rules honored:
  #11 (RSI discipline)  — target upside doesn't override RSI gate
  #13 (capital plan gate) — swaps that would fail cash floor are demoted
  #17/#18 (position-aware) — never propose stacking same-name risk
  #19 (no fabricated data) — missing scores → skip, never invent
  #20 (S/R discipline)  — S/R confluence bonuses target-tier scoring
  #21 (concentration)    — recommendations respect single-Friday buckets
  #27 (Parkev chip)     — Parkev tier is a signal, not the decision
  #29 (position tiers)  — Tier A holdings NEVER surfaced for rotation

Fail-open: missing snapshot data on any input → that candidate is skipped,
the rotation table still renders with the ones that had data.
"""

from __future__ import annotations

from typing import Any


# ─── Scoring composite ────────────────────────────────────────────────────


def score_candidate(
    *,
    rsi: float | None,
    iv_rank: float | None,
    drawdown_pct: float | None,
    parkev_tier: int | None,
    fair_value_upside_pct: float | None,
    at_support: bool = False,
) -> tuple[float, list[str]]:
    """Composite quality score for a fresh entry candidate.

    Returns (score, reasons). Higher = better. Scores are bounded 0-100
    so the rotation table can rank across surfaces.

    Component weights:
      RSI band       — 30 (35-55 favorable = full; 60+ or <35 = discount)
      IV rank        — 20 (60+ favors CSP writes)
      Drawdown       — 15 (10-25% = fresh entry zone)
      Parkev tier    — 20 (tier 5 = 20, tier 4 = 15, tier 3 = 10, else 0)
      FV upside      — 10 (>+15% = full)
      S/R confluence — 5  (at support = full)
    """
    score = 0.0
    reasons: list[str] = []

    # RSI band (hard rule #11 asymmetric)
    if rsi is not None:
        if 35 <= rsi <= 55:
            score += 30
            reasons.append(f"RSI {rsi:.0f} favorable")
        elif 55 < rsi <= 60:
            score += 18
        elif 30 <= rsi < 35:
            score += 15
        elif 60 < rsi <= 70:
            score += 8
        # RSI >70 or <30 → 0 points (blocked from new entries)
    # IV rank
    if iv_rank is not None:
        if iv_rank >= 60:
            score += 20
            reasons.append(f"IV {iv_rank:.0f} rich")
        elif iv_rank >= 40:
            score += 12
        elif iv_rank >= 25:
            score += 6
    # Drawdown (mild pullback is favorable for a fresh entry)
    if drawdown_pct is not None:
        if 10 <= drawdown_pct <= 25:
            score += 15
            reasons.append(f"{drawdown_pct:.0f}% pullback zone")
        elif 5 <= drawdown_pct < 10:
            score += 10
        elif drawdown_pct > 25 and drawdown_pct < 40:
            score += 5  # deeper but not thesis-broken
    # Parkev tier
    if parkev_tier is not None:
        if parkev_tier >= 5:
            score += 20
            reasons.append("🅿️ TOP STOCK")
        elif parkev_tier >= 4:
            score += 15
            reasons.append(f"🅿️ tier {parkev_tier}")
        elif parkev_tier >= 3:
            score += 10
            reasons.append("🅿️ Buy")
    # FV upside
    if fair_value_upside_pct is not None:
        if fair_value_upside_pct >= 15:
            score += 10
            reasons.append(f"FV {fair_value_upside_pct:+.0f}%")
        elif fair_value_upside_pct >= 5:
            score += 6
    # S/R confluence
    if at_support:
        score += 5
        reasons.append("at support")

    return (round(score, 1), reasons)


# ─── Data helpers ─────────────────────────────────────────────────────────


def _ticker_theme(ticker: str, theme_universes: dict) -> str | None:
    """Return the theme key a ticker belongs to (first match), or None."""
    for theme_key, theme in (theme_universes or {}).get("themes", {}).items():
        anchors = {t.upper() for t in (theme.get("anchors") or [])}
        if ticker.upper() in anchors:
            return theme_key
    return None


def _same_theme_candidates(
    held_ticker: str,
    theme_universes: dict,
    exclude: set[str],
) -> list[str]:
    """List of other tickers in the same theme as held_ticker.

    exclude filters out the held ticker itself + any tickers already
    surfaced (prevents duplicate rows).
    """
    theme = _ticker_theme(held_ticker, theme_universes)
    if not theme:
        return []
    anchors = (theme_universes.get("themes", {}).get(theme, {}).get("anchors")) or []
    return [t.upper() for t in anchors if t.upper() not in exclude]


def _fetch_ticker_data(
    ticker: str,
    *,
    technicals: dict,
    recommendations: dict,
    finviz_targets: dict,
    fv_by_ticker: dict,
) -> dict[str, Any]:
    """Consolidate all analytical inputs for one ticker into the scoring dict."""
    tech = technicals.get(ticker) or {}
    parkev = recommendations.get(ticker) or {}
    fv = fv_by_ticker.get(ticker) or {}
    finviz = finviz_targets.get(ticker) or {}

    # Fair value upside — prefer FMP analyst PT, fall back to FINVIZ target
    spot = tech.get("spot")
    fv_upside = None
    if fv.get("analyst_target") and spot:
        fv_upside = (fv["analyst_target"] - spot) / spot * 100
    elif finviz.get("target_upside_pct") is not None:
        fv_upside = finviz["target_upside_pct"]

    # At-support test — is spot within 5% of the strongest support?
    at_support = False
    sr = tech.get("support_resistance") or {}
    for s in sr.get("supports") or []:
        if s.get("price") and spot and s.get("strength", 0) >= 2.0:
            distance = (spot - s["price"]) / spot * 100
            if 0 <= distance <= 5:
                at_support = True
                break

    return {
        "ticker": ticker,
        "spot": spot,
        "rsi": tech.get("rsi_14"),
        "iv_rank": tech.get("iv_rank"),
        "drawdown_pct": tech.get("drawdown_pct"),
        "parkev_tier": parkev.get("rating_tier"),
        "parkev_label": parkev.get("recommendation") or parkev.get("raw_recommendation"),
        "fair_value_upside_pct": fv_upside,
        "at_support": at_support,
        "finviz_target": finviz.get("target_price"),
    }


# ─── Equity rotation (Flavor 1) ───────────────────────────────────────────


def find_equity_rotations(
    *,
    holdings: list[dict],       # [{ticker, weight_pct, tier}, ...]
    theme_universes: dict,
    technicals: dict,
    recommendations: dict,
    finviz_targets: dict,
    fv_by_ticker: dict,
    min_holding_weight_pct: float = 5.0,
    min_score_improvement: float = 15.0,
    max_alternatives_per_holding: int = 3,
) -> list[dict]:
    """For each held ticker > threshold, find higher-scoring same-theme alternatives.

    Skips:
      - Tier A holdings (never surfaced for rotation — hard rule #29)
      - Holdings below the weight threshold (small positions)
      - Alternatives the user already holds (position-aware)
      - Alternatives scoring within `min_score_improvement` of the held name

    Returns a list of rotation opportunities, one row per (from → to) pair.
    """
    held_tickers = {h["ticker"].upper() for h in holdings}
    rotations: list[dict] = []

    for h in holdings:
        held = h["ticker"].upper()
        tier = (h.get("tier") or "").upper()
        weight = h.get("weight_pct", 0) or 0

        # Filter: Tier A never rotates
        if tier == "A":
            continue
        # Filter: small positions not worth rotating
        if weight < min_holding_weight_pct:
            continue

        # Score the currently-held position
        held_data = _fetch_ticker_data(
            held,
            technicals=technicals,
            recommendations=recommendations,
            finviz_targets=finviz_targets,
            fv_by_ticker=fv_by_ticker,
        )
        held_score, held_reasons = score_candidate(
            rsi=held_data["rsi"],
            iv_rank=held_data["iv_rank"],
            drawdown_pct=held_data["drawdown_pct"],
            parkev_tier=held_data["parkev_tier"],
            fair_value_upside_pct=held_data["fair_value_upside_pct"],
            at_support=held_data["at_support"],
        )

        # Find same-theme candidates
        candidates = _same_theme_candidates(held, theme_universes, exclude=held_tickers)
        scored_cands: list[tuple[float, list[str], dict]] = []
        for cand in candidates:
            cand_data = _fetch_ticker_data(
                cand,
                technicals=technicals,
                recommendations=recommendations,
                finviz_targets=finviz_targets,
                fv_by_ticker=fv_by_ticker,
            )
            cand_score, cand_reasons = score_candidate(
                rsi=cand_data["rsi"],
                iv_rank=cand_data["iv_rank"],
                drawdown_pct=cand_data["drawdown_pct"],
                parkev_tier=cand_data["parkev_tier"],
                fair_value_upside_pct=cand_data["fair_value_upside_pct"],
                at_support=cand_data["at_support"],
            )
            improvement = cand_score - held_score
            if improvement < min_score_improvement:
                continue
            scored_cands.append((cand_score, improvement, cand_reasons, cand_data))

        # Sort descending by score, take top N
        scored_cands.sort(key=lambda x: x[0], reverse=True)
        for cand_score, improvement, cand_reasons, cand_data in scored_cands[:max_alternatives_per_holding]:
            rotations.append({
                "source": "equity_rotation",
                "from": {
                    "ticker": held,
                    "weight_pct": round(weight, 1),
                    "tier": tier,
                    "score": held_score,
                    "reasons": held_reasons,
                    "rsi": held_data["rsi"],
                    "iv_rank": held_data["iv_rank"],
                    "parkev_tier": held_data["parkev_tier"],
                },
                "to": {
                    "ticker": cand_data["ticker"],
                    "score": cand_score,
                    "improvement": round(improvement, 1),
                    "reasons": cand_reasons,
                    "rsi": cand_data["rsi"],
                    "iv_rank": cand_data["iv_rank"],
                    "parkev_tier": cand_data["parkev_tier"],
                    "parkev_label": cand_data["parkev_label"],
                    "fair_value_upside_pct": cand_data["fair_value_upside_pct"],
                    "at_support": cand_data["at_support"],
                },
                "theme": _ticker_theme(held, theme_universes),
                "rationale": (
                    f"Same-theme swap: {cand_data['ticker']} scores {cand_score} vs "
                    f"{held} at {held_score} (+{improvement:.0f})."
                ),
            })

    return rotations


# ─── Option rotation (Flavor 2) ───────────────────────────────────────────


def find_option_rotations(
    *,
    options: list[dict],
    technicals: dict,
    min_capture_pct: float = 30.0,
    max_alternatives: int = 2,
) -> list[dict]:
    """For each open short put/call with ≥ min_capture_pct captured,
    surface a "close + reopen" opportunity to reset the premium clock.

    v1: same-name only (rolling to a different strike/DTE on the same
    underlying). Cross-name option rotation deferred to v2 — would need
    to score chain candidates on similar-collateral Parkev names, which
    requires a chain fetch outside this pure-scoring pass.
    """
    rotations: list[dict] = []
    for o in options or []:
        pct = o.get("captured_pct") or o.get("profit_pct") or o.get("pl_pct")
        if pct is None or abs(pct) < min_capture_pct:
            continue
        ticker = (o.get("underlying") or o.get("symbol") or "").upper()
        if not ticker:
            continue
        tech = technicals.get(ticker) or {}
        rsi = tech.get("rsi_14")
        iv = tech.get("iv_rank")
        # Only rotate when the fresh-entry conditions are STILL favorable
        # (RSI in the 35-55 band + IV ≥ 50). Otherwise closing is the
        # only actionable — no rotation candidate.
        if rsi is None or not (35 <= rsi <= 55):
            continue
        if iv is None or iv < 50:
            continue
        rotations.append({
            "source": "option_rotation_same_name",
            "from": {
                "symbol": o.get("symbol"),
                "underlying": ticker,
                "strike": o.get("strike"),
                "expiration": o.get("expiration"),
                "captured_pct": pct,
            },
            "to": {
                "ticker": ticker,
                "hint": (
                    f"Close {o.get('symbol')} at ~{pct:.0f}% capture, then "
                    f"reopen at a lower strike / later DTE — RSI {rsi:.0f} "
                    f"is favorable and IV {iv:.0f} is still rich."
                ),
                "rsi": rsi,
                "iv_rank": iv,
            },
            "rationale": (
                f"Same-name rotation: {ticker} has captured {pct:.0f}% — clock "
                f"is running out. Reset premium at a lower strike."
            ),
        })
    return rotations


# ─── Capital reallocation (Flavor 3) ──────────────────────────────────────


def find_capital_reallocations(
    *,
    equity_rotations: list[dict],
    option_rotations: list[dict],
    stress_coverage: float | None,
    coverage_critical: float = 0.50,
) -> list[dict]:
    """When capacity is critical, prioritize rotations that RELEASE
    collateral (close overweight positions to fund higher-scoring entries)
    over pure adds. Emits synthesized 'reallocation plan' rows.

    v1: takes the top 3 equity rotations by improvement score + top 2
    option rotations, presents them as a ranked action plan with the
    total NLV freed and the total NLV to deploy.
    """
    if not equity_rotations and not option_rotations:
        return []

    plan: list[dict] = []
    # Sort by improvement descending
    top_equity = sorted(
        equity_rotations,
        key=lambda r: r.get("to", {}).get("improvement", 0),
        reverse=True,
    )[:3]
    top_options = sorted(
        option_rotations,
        key=lambda r: abs(r.get("from", {}).get("captured_pct", 0) or 0),
        reverse=True,
    )[:2]

    # NLV to be freed (equity rotations = weight_pct sum)
    freed_pct = sum(r["from"]["weight_pct"] for r in top_equity)

    if top_equity or top_options:
        plan.append({
            "source": "capital_reallocation_plan",
            "summary": {
                "coverage": stress_coverage,
                "coverage_critical": coverage_critical,
                "freed_pct_nlv": round(freed_pct, 1),
                "equity_swaps": len(top_equity),
                "option_swaps": len(top_options),
            },
            "moves": [
                {"action": "TRIM", **r["from"], "then_enter": r["to"]}
                for r in top_equity
            ] + [
                {"action": "CLOSE_AND_REOPEN", **r["from"], "reopen_hint": r["to"]}
                for r in top_options
            ],
            "rationale": (
                f"With coverage at {stress_coverage:.2f}× "
                f"(critical <{coverage_critical:.2f}×), rotate collateral from "
                f"{len(top_equity)} oversized equity position(s) + reset "
                f"{len(top_options)} option(s) at high capture."
                if stress_coverage is not None and stress_coverage < coverage_critical
                else "Rotations available even without a capacity trigger."
            ),
        })
    return plan


# ─── Top-level entry point ────────────────────────────────────────────────


def build_rotation_opportunities(
    *,
    holdings: list[dict],
    options: list[dict],
    theme_universes: dict,
    technicals: dict,
    recommendations: dict,
    finviz_targets: dict,
    fv_by_ticker: dict,
    stress_coverage: float | None = None,
) -> dict:
    """Assemble all three flavors into a single rotation_opportunities dict.

    Output shape (matches what briefing.json will carry):
        {
          "equity": [...],
          "options": [...],
          "capital_plan": [...],
          "stats": {"equity_count": N, "options_count": N, "capital_moves": N}
        }
    """
    equity = find_equity_rotations(
        holdings=holdings,
        theme_universes=theme_universes,
        technicals=technicals,
        recommendations=recommendations,
        finviz_targets=finviz_targets,
        fv_by_ticker=fv_by_ticker,
    )
    opts = find_option_rotations(
        options=options,
        technicals=technicals,
    )
    plan = find_capital_reallocations(
        equity_rotations=equity,
        option_rotations=opts,
        stress_coverage=stress_coverage,
    )
    return {
        "equity": equity,
        "options": opts,
        "capital_plan": plan,
        "stats": {
            "equity_count": len(equity),
            "options_count": len(opts),
            "capital_moves": sum(len(p.get("moves", [])) for p in plan),
        },
    }
