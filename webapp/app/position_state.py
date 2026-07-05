"""Assemble the current-state summary for a single ticker's drill-down page.

The `/positions/{ticker}` page was the thinnest surface in the app —
charts + history table + briefing mentions, but NONE of the analyst
context (RSI, IV rank, MA200 distance, drawdown, S/R, Parkev rec, tier).
This module pulls all of that from the snapshot dir + briefing model
and returns a single structured dict the template can render as a
side-by-side "current state" card.

Fail-open: missing snapshot files, missing tickers, missing fields —
return None on the affected field, never an invented value (hard rule #19).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_snapshot_technicals(snapshot_dir: str | Path, ticker: str) -> dict | None:
    """Return the ticker's row from technicals.json, or None."""
    if not snapshot_dir:
        return None
    p = Path(snapshot_dir) / "technicals.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data.get(ticker.upper())


def load_snapshot_finviz_targets(snapshot_dir: str | Path) -> dict:
    """Return the FINVIZ target cache dict (ticker → target dict) from
    the finviz-target-fetcher's on-disk cache. Falls back to empty.

    The cache lives at `<snapshot_dir>/../.finviz_target_cache.json`
    OR `<snapshot_dir>/.finviz_target_cache.json` — try both.
    Absent + malformed both → {} (fail-open, hard rule #19).
    """
    if not snapshot_dir:
        return {}
    candidates = [
        Path(snapshot_dir) / ".finviz_target_cache.json",
        Path(snapshot_dir).parent / ".finviz_target_cache.json",
    ]
    for p in candidates:
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                # Strip null envelopes so the caller sees a clean {ticker: target|None}
                if isinstance(data, dict):
                    return {
                        k: (v if v and not v.get("_null") else None)
                        for k, v in data.items()
                    }
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def load_snapshot_recs(snapshot_dir: str | Path) -> dict:
    """Return the full recommendations_list dict (ticker → rec dict)."""
    if not snapshot_dir:
        return {}
    p = Path(snapshot_dir) / "recommendations_list.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    # File may be either {ticker: rec} or {"recommendations": {...}} or a list
    if isinstance(data, dict):
        if "recommendations" in data and isinstance(data["recommendations"], dict):
            return data["recommendations"]
        return data
    return {}


def _fmt_pct(v: float | None, sign: bool = True) -> str | None:
    if v is None:
        return None
    return f"{v:+.1f}%" if sign else f"{v:.1f}%"


def _rsi_tone(rsi: float | None) -> tuple[str, str]:
    """(tone, label) for the RSI chip. Asymmetric bands per hard rule #11."""
    if rsi is None:
        return ("muted", "n/a")
    if rsi >= 70:
        return ("bad", "overbought")
    if rsi >= 60:
        return ("warn", "extended")
    if rsi < 25:
        return ("bad", "falling knife")
    if rsi < 35:
        return ("warn", "oversold")
    if 35 <= rsi <= 55:
        return ("ok", "favorable")
    return ("muted", "neutral")


def _iv_tone(iv: float | None) -> tuple[str, str]:
    if iv is None:
        return ("muted", "n/a")
    if iv >= 80:
        return ("warn", "elevated")
    if iv >= 50:
        return ("ok", "rich premium")
    return ("muted", "low")


def _trend_tone(spot: float | None, sma_200: float | None) -> tuple[str, str, str | None]:
    """(tone, label, distance_pct_str)"""
    if not spot or not sma_200:
        return ("muted", "n/a", None)
    dist = (spot - sma_200) / sma_200 * 100
    dist_str = _fmt_pct(dist)
    if dist > 15:
        return ("ok", "well above 200-SMA", dist_str)
    if dist > 0:
        return ("ok", "above 200-SMA", dist_str)
    if dist > -10:
        return ("warn", "near 200-SMA", dist_str)
    return ("bad", "below 200-SMA", dist_str)


def _drawdown_tone(dd: float | None) -> tuple[str, str]:
    if dd is None:
        return ("muted", "n/a")
    if dd >= 30:
        return ("bad", "deep drawdown")
    if dd >= 15:
        return ("warn", "meaningful pullback")
    if dd >= 5:
        return ("muted", "mild pullback")
    return ("ok", "at/near highs")


def build_position_state(
    ticker: str,
    *,
    snapshot_dir: str | Path | None,
    briefing: Any | None,
    latest_equity: dict | None,
) -> dict[str, Any]:
    """Assemble the complete current-state view for `ticker`.

    Returns a structured dict with all the fields the template needs.
    Every field is fail-open: None when missing, never an invented value.
    """
    ticker = ticker.upper()
    tech = load_snapshot_technicals(snapshot_dir, ticker) if snapshot_dir else None
    recs = load_snapshot_recs(snapshot_dir) if snapshot_dir else {}
    parkev = recs.get(ticker) or recs.get(ticker.upper())
    finviz_all = load_snapshot_finviz_targets(snapshot_dir) if snapshot_dir else {}
    finviz = finviz_all.get(ticker) or finviz_all.get(ticker.upper())

    # Find the equity_review row (has thesis_status, recommendation, rationale)
    equity_review = None
    if briefing is not None:
        for r in getattr(briefing, "equity_reviews", []) or []:
            if getattr(r, "ticker", None) == ticker:
                equity_review = r
                break

    # Extract technicals + tone them for the chip layer
    rsi = tech.get("rsi_14") if tech else None
    iv_rank = tech.get("iv_rank") if tech else None
    sma_50 = tech.get("sma_50") if tech else None
    sma_200 = tech.get("sma_200") if tech else None
    drawdown = tech.get("drawdown_pct") if tech else None
    spot = tech.get("spot") if tech else (latest_equity.get("lastTrade") if latest_equity else None)

    rsi_tone, rsi_label = _rsi_tone(rsi)
    iv_tone, iv_label = _iv_tone(iv_rank)
    trend_tone, trend_label, trend_dist = _trend_tone(spot, sma_200)
    dd_tone, dd_label = _drawdown_tone(drawdown)

    # S/R nearest levels — one support + one resistance closest to spot
    sr = tech.get("support_resistance") if tech else None
    nearest_support = None
    nearest_resistance = None
    if sr and spot:
        supports = sorted(
            [s for s in (sr.get("supports") or []) if s.get("price") and s["price"] < spot],
            key=lambda s: spot - s["price"],
        )
        resistances = sorted(
            [r for r in (sr.get("resistances") or []) if r.get("price") and r["price"] > spot],
            key=lambda r: r["price"] - spot,
        )
        if supports:
            s = supports[0]
            nearest_support = {
                "price": s["price"],
                "distance_pct": round((spot - s["price"]) / spot * 100, 1),
                "source": s.get("source"),
                "touches": s.get("touches"),
                "strength": s.get("strength"),
                "confluence": s.get("confluence") or [],
            }
        if resistances:
            r = resistances[0]
            nearest_resistance = {
                "price": r["price"],
                "distance_pct": round((r["price"] - spot) / spot * 100, 1),
                "source": r.get("source"),
                "touches": r.get("touches"),
                "strength": r.get("strength"),
                "confluence": r.get("confluence") or [],
            }

    # Parkev chip payload
    parkev_data = None
    if parkev:
        parkev_data = {
            "rating": parkev.get("recommendation"),
            "raw": parkev.get("raw_recommendation"),
            "tier": parkev.get("rating_tier"),
            "conviction": parkev.get("conviction"),
            "age_days": parkev.get("age_days"),
            "aging": parkev.get("aging"),
        }

    # Position weight — from equity_review if available
    weight_pct = None
    market_value = None
    pl_pct = None
    if equity_review is not None:
        w = getattr(equity_review, "weight", None)
        if w is not None:
            weight_pct = round(w * 100, 1)
        market_value = getattr(equity_review, "market_value", None)
        pl_pct = getattr(equity_review, "pl_pct", None)
        if pl_pct is not None:
            pl_pct = round(pl_pct * 100, 1)

    # Recommendation + thesis (from equity_review, if present)
    recommendation = getattr(equity_review, "recommendation", None) if equity_review else None
    thesis_status = getattr(equity_review, "thesis_status", None) if equity_review else None
    technical_status = getattr(equity_review, "technical_status", None) if equity_review else None
    rationale = getattr(equity_review, "rationale", None) if equity_review else None

    # ─── FINVIZ analyst target chip (Layer 3 of FINVIZ integration) ──
    finviz_data = None
    if finviz:
        finviz_data = {
            "target_price": finviz.get("target_price"),
            "target_upside_pct": finviz.get("target_upside_pct"),
            "analyst_recommendation": finviz.get("analyst_recommendation"),
            "analyst_label": finviz.get("analyst_label"),
            "source": finviz.get("source"),
            "fetched_at": finviz.get("fetched_at"),
        }

    return {
        "ticker": ticker,
        "spot": spot,
        "recommendation": recommendation,
        "thesis_status": thesis_status,
        "technical_status": technical_status,
        "rationale": rationale,
        "weight_pct": weight_pct,
        "market_value": market_value,
        "pl_pct": pl_pct,
        "technicals": {
            "rsi": rsi, "rsi_tone": rsi_tone, "rsi_label": rsi_label,
            "iv_rank": iv_rank, "iv_tone": iv_tone, "iv_label": iv_label,
            "sma_50": sma_50, "sma_200": sma_200,
            "trend_tone": trend_tone, "trend_label": trend_label, "trend_dist": trend_dist,
            "drawdown": drawdown, "dd_tone": dd_tone, "dd_label": dd_label,
        },
        "support_resistance": {
            "support": nearest_support,
            "resistance": nearest_resistance,
        },
        "parkev": parkev_data,
        "finviz": finviz_data,
        "has_data": bool(tech) or bool(equity_review),
    }
