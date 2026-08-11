#!/usr/bin/env python3
"""
Thematic scout — research engine that runs the briefing analysis stack
across thematic universes and produces a research report.

Usage:
  python3 scripts/scout.py --themes all
  python3 scripts/scout.py --themes semis_ai,nuclear
  python3 scripts/scout.py --themes all --output ~/Documents/briefings/scout_YYYY-MM-DD.md

For each anchor ticker in each theme:
  1. Pull yfinance technicals (RSI, IV rank, 200-SMA, drawdown, spot)
  2. Pull next earnings date
  3. Optionally pull third-party rec (from snapshot if available)
  4. Pull E*TRADE chain for a target CSP entry (~10% OTM, 35 DTE) via
     the canonical etrade-chain-fetcher skill — never yfinance for chains
  5. Compute verdict (BUY / WATCH / AVOID / CSP_ENTRY)
  6. Render markdown report

The scout is read-only research, separate from the daily briefing's
"manage what you own" focus.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[3]
_THIS_DIR = Path(__file__).resolve().parent
_RULES = _THIS_DIR.parent / "references" / "theme_universes.yaml"


# --------------------------------------------------------------------------
# Lazy-load the etrade-chain-fetcher skill
# --------------------------------------------------------------------------

_CHAIN_MODULE = None
_CHAIN_CACHE = None


def _load_chain_fetcher():
    global _CHAIN_MODULE, _CHAIN_CACHE
    if _CHAIN_MODULE is not None:
        return _CHAIN_MODULE, _CHAIN_CACHE
    target = _REPO_ROOT / "skills" / "etrade-chain-fetcher" / "scripts" / "fetch.py"
    if not target.exists():
        return None, None
    spec = importlib.util.spec_from_file_location("etrade_chain_fetcher", target)
    if spec is None or spec.loader is None:
        return None, None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["etrade_chain_fetcher"] = mod
    spec.loader.exec_module(mod)
    if not mod.is_available():
        print(f"  [warn] E*TRADE chain fetcher unavailable: {mod.availability_reason()}",
              file=sys.stderr)
        return mod, None
    _CHAIN_MODULE = mod
    _CHAIN_CACHE = mod.ChainCache()
    return mod, _CHAIN_CACHE


# --------------------------------------------------------------------------
# Lazy-load the intrinsic-value module (lives in daily-portfolio-briefing)
# --------------------------------------------------------------------------

_IV_MODULE = None


def _load_intrinsic_module():
    """Load the shared intrinsic_value module by path so the standalone scout
    report uses the same fair-value logic (and cache) as the daily briefing."""
    global _IV_MODULE
    if _IV_MODULE is not None:
        return _IV_MODULE
    scripts_dir = _REPO_ROOT / "skills" / "daily-portfolio-briefing" / "scripts"
    target = scripts_dir / "analysis" / "intrinsic_value.py"
    if not target.exists():
        return None
    # Put the scripts dir on path so intrinsic_value's `from analysis import
    # rsi_discipline` resolves when loaded by file path.
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("briefing_intrinsic_value", target)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["briefing_intrinsic_value"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # pragma: no cover - defensive
        print(f"  [warn] intrinsic-value module load failed: {e}", file=sys.stderr)
        return None
    _IV_MODULE = mod
    return mod


# --------------------------------------------------------------------------
# Lazy-load the support_resistance module (lives in daily-portfolio-briefing).
# The scout reuses the briefing's S/R logic so the SAME levels/strength scoring
# show up wherever the user looks (Watch panel + Candidate Trades + WTE).
# --------------------------------------------------------------------------

_SR_MODULE = None


def _load_sr_module():
    global _SR_MODULE
    if _SR_MODULE is not None:
        return _SR_MODULE
    scripts_dir = _REPO_ROOT / "skills" / "daily-portfolio-briefing" / "scripts"
    target = scripts_dir / "analysis" / "support_resistance.py"
    if not target.exists():
        return None
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("briefing_support_resistance", target)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["briefing_support_resistance"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # pragma: no cover - defensive
        print(f"  [warn] support_resistance module load failed: {e}", file=sys.stderr)
        return None
    _SR_MODULE = mod
    return mod


# --------------------------------------------------------------------------
# Lazy-load the persistent OHLC cache (lives in daily-portfolio-briefing).
# Task #9: warm-cache runs skip the full 300d yfinance download per ticker.
# Fail-open — any load/read problem falls back to the raw yfinance call.
# --------------------------------------------------------------------------

_OHLC_MODULE = None
_OHLC_TRIED = False


def _load_ohlc_cache():
    global _OHLC_MODULE, _OHLC_TRIED
    if _OHLC_TRIED:
        return _OHLC_MODULE
    _OHLC_TRIED = True
    target = (_REPO_ROOT / "skills" / "daily-portfolio-briefing" / "scripts"
              / "analysis" / "ohlc_cache.py")
    if not target.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("briefing_ohlc_cache", target)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["briefing_ohlc_cache"] = mod
        spec.loader.exec_module(mod)
        _OHLC_MODULE = mod
    except Exception as e:  # pragma: no cover - defensive
        print(f"  [warn] ohlc_cache module load failed: {e}", file=sys.stderr)
        _OHLC_MODULE = None
    return _OHLC_MODULE


def _history_300d(ticker: str):
    """300d daily bars — persistent cache when available, raw yfinance else."""
    mod = _load_ohlc_cache()
    if mod is not None:
        try:
            return mod.cached_history(ticker, days=300)
        except Exception as e:  # noqa: BLE001 - fail-open to raw fetch
            print(f"  [warn] ohlc cache for {ticker}: {e} — uncached path",
                  file=sys.stderr)
    import yfinance as yf
    return yf.Ticker(ticker).history(period="300d")


# --------------------------------------------------------------------------
# Lazy-load the briefing's technical_indicators module (canonical IV rank —
# Task #12: one 252-obs percentile shared with snapshot_inputs + screener).
# Fail-open — if the module can't load, fall back to the inline computation.
# --------------------------------------------------------------------------

_TI_MODULE = None
_TI_TRIED = False


def _load_ti_module():
    global _TI_MODULE, _TI_TRIED
    if _TI_TRIED:
        return _TI_MODULE
    _TI_TRIED = True
    target = (_REPO_ROOT / "skills" / "daily-portfolio-briefing" / "scripts"
              / "analysis" / "technical_indicators.py")
    if not target.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "briefing_technical_indicators", target)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["briefing_technical_indicators"] = mod
        spec.loader.exec_module(mod)
        _TI_MODULE = mod
    except Exception as e:  # pragma: no cover - defensive
        print(f"  [warn] technical_indicators module load failed: {e}",
              file=sys.stderr)
        _TI_MODULE = None
    return _TI_MODULE


def _iv_rank(closes) -> float | None:
    """IV rank proxy — canonical 252-obs percentile from the briefing's
    technical_indicators.iv_rank_252 when loadable; inline fallback keeps the
    scout working standalone (fail-open, same formula minus the .tail cap)."""
    ti = _load_ti_module()
    if ti is not None:
        try:
            return ti.iv_rank_252(closes)
        except Exception as e:  # noqa: BLE001 - fail-open to inline math
            print(f"  [warn] iv_rank_252: {e} — inline fallback", file=sys.stderr)
    rets = closes.pct_change().dropna()
    if len(rets) < 21:
        return None
    rolling_vol = (rets.rolling(20).std() * math.sqrt(252)).dropna().tail(252)
    if rolling_vol.empty:
        return None
    cur = float(rolling_vol.iloc[-1])
    if math.isnan(cur):
        return None
    return round(float((rolling_vol <= cur).sum()) / len(rolling_vol) * 100.0, 1)


# --------------------------------------------------------------------------
# yfinance technical signals (same logic as snapshot_inputs._full_technicals)
# --------------------------------------------------------------------------

def _fetch_technicals(ticker: str) -> dict | None:
    """Return {iv_rank, rsi_14, sma_50, sma_200, drawdown_pct, spot, fivedayret, support_resistance}."""
    try:
        hist = _history_300d(ticker)
        if hist is None or hist.empty or len(hist) < 60:
            return None
        closes = hist["Close"].dropna()
        spot = float(closes.iloc[-1])

        # IV rank — canonical 252-obs realized-vol percentile, shared with
        # snapshot_inputs + broad-universe-screener (technical_indicators.iv_rank_252)
        iv_rank = _iv_rank(closes)

        # RSI(14) Wilder's
        rsi_14 = None
        if len(closes) >= 30:
            delta = closes.diff().dropna()
            gain = delta.clip(lower=0)
            loss = (-delta.clip(upper=0))
            avg_gain = gain.ewm(alpha=1.0/14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1.0/14, adjust=False).mean()
            last_gain = float(avg_gain.iloc[-1])
            last_loss = float(avg_loss.iloc[-1])
            if last_loss > 0:
                rs = last_gain / last_loss
                rsi_14 = round(100.0 - (100.0 / (1.0 + rs)), 1)
            elif last_gain > 0:
                rsi_14 = 100.0
            else:
                rsi_14 = 50.0

        # 50-SMA (used for S/R confluence)
        sma_50 = None
        if len(closes) >= 50:
            sma_50 = round(float(closes.tail(50).mean()), 2)

        # 200-SMA
        sma_200 = None
        if len(closes) >= 200:
            sma_200 = round(float(closes.tail(200).mean()), 2)

        # Drawdown from 252-day high
        drawdown_pct = None
        window = closes.tail(252)
        if not window.empty:
            high = float(window.max())
            if high > 0:
                drawdown_pct = round((high - spot) / high * 100.0, 1)

        # 5-day return
        five_d = None
        if len(closes) >= 6:
            prev = float(closes.iloc[-6])
            if prev:
                five_d = round((spot - prev) / prev * 100.0, 2)

        # Support / Resistance — reuse the same 300d hist that fed RSI/SMA.
        # Fail-closed (hard rule #20): missing/short hist → no SR; never a
        # fabricated level. The SR module is imported via the daily-briefing
        # package so we load it by path here (scout is a sibling skill).
        sr_payload = None
        try:
            sr_mod = _load_sr_module()
            if sr_mod is not None:
                sr_result = sr_mod.compute_sr(
                    hist, spot=float(spot), sma_50=sma_50, sma_200=sma_200,
                )
                sr_payload = sr_result.to_dict()
        except Exception as _sr_e:
            print(f"  [warn] S/R compute for {ticker}: {_sr_e}", file=sys.stderr)

        return {
            "iv_rank": iv_rank,
            "rsi_14": rsi_14,
            "sma_50": sma_50,
            "sma_200": sma_200,
            "drawdown_pct": drawdown_pct,
            "spot": round(spot, 2),
            "fivedayret_pct": five_d,
            "support_resistance": sr_payload,
        }
    except Exception as e:
        print(f"  [warn] technicals fetch for {ticker}: {e}", file=sys.stderr)
        return None


def _fetch_earnings_date(ticker: str) -> str | None:
    """Return next earnings date as YYYY-MM-DD or None."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        cal = getattr(t, "calendar", None)
        if isinstance(cal, dict):
            earnings_list = cal.get("Earnings Date", [])
            if earnings_list:
                d = earnings_list[0]
                if hasattr(d, "strftime"):
                    return d.strftime("%Y-%m-%d")
        ed = t.get_earnings_dates(limit=4)
        if ed is not None and not ed.empty:
            today = date.today()
            for idx in ed.index:
                d = idx.date() if hasattr(idx, "date") else idx
                if d >= today:
                    return d.strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# E*TRADE chain lookup for CSP entry
# --------------------------------------------------------------------------

def _csp_entry_quote(ticker: str, spot: float, otm_pct: float, target_dte: int,
                     prefer_monthly: bool = False) -> dict | None:
    """Look up the closest CSP strike via the canonical E*TRADE chain fetcher.

    Returns {strike, bid, mid, ask, expiration, dte} or None if E*TRADE
    is unreachable / no strike near target.

    ``prefer_monthly`` (2026-08-10 expiration policy): prefer the standard
    monthly (3rd-Friday) expiration within the DTE band — institutional
    OI/liquidity concentrates there (tighter spreads, better fills). The
    quote gains ``exp_kind`` ("monthly"/"weekly") computed from the REAL
    selected chain date so renderers can label the ticket (rule #19).
    """
    mod, cache = _load_chain_fetcher()
    if mod is None or cache is None:
        return None
    exp_date = mod.choose_expiration(
        symbol=ticker, target_dte=target_dte, tolerance_days=14, cache=cache,
        prefer_monthly=prefer_monthly,
    )
    if exp_date is None:
        return None
    quote = mod.find_strike_at_otm_pct(
        symbol=ticker, expiration=exp_date, otm_pct=otm_pct,
        opt_type="PUT", spot=spot, cache=cache,
    )
    if not quote:
        return None
    # Add DTE for convenience
    quote["dte"] = (exp_date - date.today()).days
    if prefer_monthly and hasattr(mod, "expiration_kind"):
        try:
            quote["exp_kind"] = mod.expiration_kind(exp_date)
        except Exception:
            pass
    return quote


# --------------------------------------------------------------------------
# Verdict logic
# --------------------------------------------------------------------------

@dataclass
class ScoutResult:
    ticker: str
    theme: str
    spot: float | None = None
    rsi_14: float | None = None
    iv_rank: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    drawdown_pct: float | None = None
    fivedayret_pct: float | None = None
    earnings_date: str | None = None
    days_to_earnings: int | None = None
    third_party_rec: str | None = None
    # Parkev rating tier (5=Top Stock to Buy, 4=Top 12/15/25 Stock, 3=Buy,
    # 2=Borderline Buy, 1=Hold, 0=Sell). Tier ≥4 = high-conviction signal that
    # When-To-Enter surfaces with a 🌟 STRONG BUY badge distinct from a plain
    # tier-3 BUY. None when the ticker isn't in Parkev's sheet.
    rating_tier: int | None = None
    # Parkev conviction level (CLAUDE.md hard rule #26) — qualitative
    # confidence in the rating. "High" / "Medium" / "Low" / None. Modulates
    # both the badge (🔥 high conviction on tier-3 BUYs) and sizing guidance.
    # A `Buy + Low` is a soft buy; a `Buy + High` is selective conviction.
    conviction: str | None = None
    conviction_score: int | None = None  # 3 / 2 / 1 / None
    aging: bool = False           # rec is >14 days old per Parkev's date_updated
    verdict: str = "WATCH"
    rationale: list = field(default_factory=list)
    csp_entry: dict | None = None  # if CSP_ENTRY verdict
    # Support/Resistance levels for the ticker (the SupportResistance.to_dict()
    # shape — supports + resistances + pivots + confidence). When present, the
    # downstream When-To-Enter classifier substitutes real support prices into
    # the trigger text and CSP strikes anchor to support clusters. Fail-closed:
    # ``None`` means SR was unavailable for this ticker — never a fabricated
    # level (hard rule #20).
    support_resistance: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _verdict(
    tech: dict,
    earnings_date: str | None,
    rec: str | None,
    held_weight_pct: float,
    cfg: dict,
) -> tuple[str, list[str], bool]:
    """Return (verdict, rationale_lines, want_csp_quote)."""
    reasons: list[str] = []
    rec_u = (rec or "").upper()
    rsi = tech.get("rsi_14")
    dd = tech.get("drawdown_pct")
    spot = tech.get("spot")
    sma = tech.get("sma_200")
    iv = tech.get("iv_rank")

    # AVOID conditions
    if rec_u in ("SELL", "UNDERPERFORM", "STRONG_SELL"):
        return "AVOID", [f"third-party rec: {rec_u}"], False
    if dd is not None and dd > cfg["drawdown_thesis_broken_pct"] and rec_u not in ("BUY", "STRONG_BUY"):
        return "AVOID", [f"drawdown {dd:.0f}% with no third-party support"], False
    if rsi is not None and rsi > cfg["rsi_overheated"]:
        reasons.append(f"RSI {rsi:.0f} overheated")
        if dd is None or dd < 3:
            return "AVOID — overheated", reasons, False

    # Concentration override (if user-already-holds ≥ 10% NLV)
    if held_weight_pct >= 10:
        reasons.append(f"already {held_weight_pct:.1f}% NLV — don't add concentration")
        return "WATCH — already concentrated", reasons, False

    # BUY conditions
    if rec_u in ("BUY", "STRONG_BUY", "OUTPERFORM", "TOP_15"):
        if rsi is not None and rsi < cfg["rsi_oversold"]:
            reasons.append(f"RSI {rsi:.0f} oversold + BUY rec")
            return "BUY (oversold pullback)", reasons, True
        if dd is not None and dd > cfg["drawdown_oversold_pct"]:
            reasons.append(f"drawdown {dd:.0f}% + BUY rec")
            return "BUY (pullback)", reasons, True
        if sma and spot and abs(spot - sma) / sma < cfg["sma_within_pct"] / 100.0:
            reasons.append(f"within {cfg['sma_within_pct']:.0f}% of 200-SMA + BUY rec")
            return "BUY (support test)", reasons, True
        if iv is not None and iv > cfg["iv_rank_elevated"]:
            reasons.append(f"BUY rec + IV rank {iv:.0f} (elevated)")
            return "CSP ENTRY (fat premium)", reasons, True
        # BUY rec but no extra trigger
        reasons.append(f"third-party {rec_u}, technicals neutral")
        return "WATCH", reasons, False

    # ── INDEPENDENT CSP setup (CLAUDE.md hard rule #25) ─────────────────
    # No third-party BUY but the technicals say "tradeable pullback":
    # RSI in 35-55 band, elevated IV rank. The user has other places to
    # validate the catalyst — surface the entry instead of hiding it
    # behind a missing Parkev rec. The AVOID branch above already vetoed
    # broken-thesis drawdowns (>30%) and SELL/UNDERPERFORM recs, so any
    # name reaching here is technically clean.
    #
    # Distinct verdict label "CSP ENTRY (independent setup)" so the
    # renderer can attach a ⚠ badge to remind the user to validate the
    # catalyst against their other sources. The badge phrasing depends on
    # whether Parkev has ANY rec on the name (misleading to say "no rec"
    # when Parkev actually rates it HOLD — that's a false negative that
    # made the user think the ticker mapping was broken, see 2026-07-03):
    #   - Parkev HOLD → "Parkev HOLD (not a BUY catalyst)"
    #   - Parkev SELL was caught above; won't hit this branch
    #   - No rec at all → "no third-party rec"
    if (rsi is not None and 35 <= rsi <= 55
            and iv is not None and iv >= cfg["iv_rank_elevated"]):
        if rec_u == "HOLD":
            catalyst_note = "Parkev HOLD (not a BUY catalyst) — verify independently"
        else:
            catalyst_note = "no third-party rec — verify catalyst independently"
        reasons.append(
            f"RSI {rsi:.0f} in pullback band + IV rank {iv:.0f} elevated "
            f"({catalyst_note})"
        )
        return "CSP ENTRY (independent setup)", reasons, True

    # No BUY rec and technicals don't qualify — just monitor
    if rec_u == "HOLD":
        reasons.append("third-party HOLD, technicals don't qualify for independent entry")
    else:
        reasons.append("no third-party catalyst")
    return "WATCH", reasons, False


# --------------------------------------------------------------------------
# Per-ticker pipeline
# --------------------------------------------------------------------------

def _research_ticker(ticker: str, theme: str, cfg: dict,
                     recs_map: dict, held_weights: dict,
                     existing_short_puts: dict | None = None) -> ScoutResult:
    res = ScoutResult(ticker=ticker, theme=theme)
    tech = _fetch_technicals(ticker)
    if not tech:
        res.verdict = "NO DATA"
        res.rationale = ["yfinance returned no data"]
        return res

    res.spot = tech["spot"]
    res.rsi_14 = tech.get("rsi_14")
    res.iv_rank = tech.get("iv_rank")
    res.sma_50 = tech.get("sma_50")
    res.sma_200 = tech.get("sma_200")
    res.drawdown_pct = tech.get("drawdown_pct")
    res.fivedayret_pct = tech.get("fivedayret_pct")
    res.support_resistance = tech.get("support_resistance")

    earnings = _fetch_earnings_date(ticker)
    res.earnings_date = earnings
    if earnings:
        try:
            d = date.fromisoformat(earnings)
            res.days_to_earnings = (d - date.today()).days
        except ValueError:
            pass

    # recs_map values can be EITHER a plain rec string (legacy callers) OR a
    # dict carrying {recommendation, rating_tier, aging, ...} (preferred — used
    # by the briefing pipeline). Handle both shapes for backwards compatibility.
    _raw = recs_map.get(ticker.upper())
    if isinstance(_raw, dict):
        res.third_party_rec = _raw.get("recommendation")
        rt = _raw.get("rating_tier")
        res.rating_tier = int(rt) if rt is not None else None
        # Conviction (hard rule #26) — None when missing; never fabricated.
        conv = _raw.get("conviction")
        res.conviction = conv if conv in ("High", "Medium", "Low") else None
        cs = _raw.get("conviction_score")
        res.conviction_score = int(cs) if cs is not None else None
        res.aging = bool(_raw.get("aging"))
    else:
        res.third_party_rec = _raw  # legacy: string or None
        res.rating_tier = None
        res.conviction = None
        res.conviction_score = None
        res.aging = False
    held_w = held_weights.get(ticker.upper(), 0.0)

    verdict, reasons, want_csp = _verdict(tech, earnings, res.third_party_rec, held_w, cfg)
    res.verdict = verdict
    res.rationale = reasons

    # CSP entry quote when verdict warrants
    if want_csp and res.spot:
        # Existing-put-stack guard. If the user already has ≥2 short puts on
        # this name, OR a put at a strike near our proposed strike, don't
        # propose another — that's concentration, not income.
        existing = (existing_short_puts or {}).get(ticker.upper())
        MAX_EXISTING_SHORT_PUTS = 2
        STRIKE_OVERLAP_PCT = 0.05
        # Compute the proposed strike before checking
        target_strike = res.spot * (1 - cfg["csp_target_otm_pct"] / 100.0)

        if existing and existing.get("count", 0) >= MAX_EXISTING_SHORT_PUTS:
            res.verdict = "WATCH — existing puts already stacked"
            res.rationale.append(
                f"already {int(existing['count'])} short puts open at "
                f"{sorted(existing['strikes'])} — adding another concentrates risk"
            )
        elif existing and any(
                s > 0 and abs(target_strike - s) / s <= STRIKE_OVERLAP_PCT
                for s in existing.get("strikes", [])
        ):
            overlapping = next(
                s for s in existing["strikes"]
                if s > 0 and abs(target_strike - s) / s <= STRIKE_OVERLAP_PCT
            )
            res.verdict = "WATCH — strike overlaps existing put"
            res.rationale.append(
                f"proposed ~${target_strike:.0f}P within "
                f"{STRIKE_OVERLAP_PCT*100:.0f}% of existing ${overlapping:g}P "
                f"— concentrates rather than diversifies"
            )
        # Earnings guard: don't quote a CSP that spans imminent earnings
        elif res.days_to_earnings is not None and 0 < res.days_to_earnings <= cfg["csp_target_dte"]:
            res.rationale.append(
                f"earnings in {res.days_to_earnings}d inside target DTE — no CSP ticket"
            )
        else:
            quote = _csp_entry_quote(
                ticker=ticker, spot=res.spot,
                otm_pct=cfg["csp_target_otm_pct"],
                target_dte=cfg["csp_target_dte"],
                prefer_monthly=bool(cfg.get("prefer_monthly_expiration", False)),
            )
            if quote:
                res.csp_entry = quote
            else:
                res.rationale.append("E*TRADE chain unavailable — no CSP ticket")

    return res


# --------------------------------------------------------------------------
# Recommendations + held-weight inputs (optional integration with briefing)
# --------------------------------------------------------------------------

def _load_recs_and_weights() -> tuple[dict, dict, dict]:
    """Best-effort: pull third-party recs + held weights + existing short puts
    from the latest daily-portfolio-briefing snapshot. Falls back to empty dicts.

    Returns (recs_by_ticker, weights_by_ticker, existing_short_puts_by_ticker).
    existing_short_puts shape: {ticker: {"count": int, "strikes": [float]}}
    """
    snap_root = (
        _REPO_ROOT / "skills" / "daily-portfolio-briefing"
        / "state" / "briefing_snapshots"
    )
    if not snap_root.exists():
        return {}, {}
    # Latest dated dir
    dirs = sorted([p for p in snap_root.iterdir() if p.is_dir()])
    if not dirs:
        return {}, {}
    latest = dirs[-1]

    recs: dict = {}
    rec_file = latest / "recommendations_list.json"
    if rec_file.exists():
        import json
        try:
            data = json.loads(rec_file.read_text())
            for r in data.get("recommendations", []) or []:
                t = r.get("ticker")
                rec = r.get("recommendation")
                if t and rec:
                    # Carry the full rec dict so the scout can surface
                    # rating_tier / aging downstream (parity with the briefing
                    # pipeline's recs_map shape).
                    recs[t.upper()] = {
                        "recommendation": str(rec).upper(),
                        "rating_tier": r.get("rating_tier"),
                        "raw_recommendation": r.get("raw_recommendation"),
                        # CLAUDE.md hard rule #26 — propagate conviction so
                        # the standalone scout path matches the briefing path.
                        "conviction": r.get("conviction"),
                        "conviction_score": r.get("conviction_score"),
                        "aging": bool(r.get("aging")),
                        "age_days": r.get("age_days"),
                        "date_updated": r.get("date_updated"),
                    }
        except Exception:
            pass

    weights: dict = {}
    existing_short_puts: dict = {}
    pos_file = latest / "positions.json"
    bal_file = latest / "balance.json"
    if pos_file.exists() and bal_file.exists():
        import json
        try:
            positions = json.loads(pos_file.read_text())
            balance = json.loads(bal_file.read_text())
            nlv = float(balance.get("accountValue", 0) or 0)
            if nlv > 0:
                for p in positions:
                    if p.get("assetType") == "EQUITY":
                        sym = (p.get("symbol") or "").upper()
                        qty = float(p.get("qty", 0) or 0)
                        price = float(p.get("price", 0) or 0)
                        if sym and qty > 0 and price > 0:
                            weights[sym] = weights.get(sym, 0) + (qty * price / nlv * 100)
                    elif p.get("assetType") == "OPTION" and (p.get("type") or "").upper() == "PUT":
                        qty = float(p.get("qty", 0) or 0)
                        if qty >= 0:
                            continue  # only short puts
                        t = (p.get("underlying") or "").upper()
                        if not t:
                            continue
                        entry = existing_short_puts.setdefault(
                            t, {"count": 0, "strikes": []}
                        )
                        entry["count"] += abs(qty)
                        entry["strikes"].append(float(p.get("strike", 0) or 0))
        except Exception:
            pass
    return recs, weights, existing_short_puts


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------

_VERDICT_EMOJI = {
    "BUY (oversold pullback)": "🟢",
    "BUY (pullback)": "🟢",
    "BUY (support test)": "🟢",
    "CSP ENTRY (fat premium)": "💎",
    "WATCH": "👀",
    "WATCH — already concentrated": "🟡",
    "AVOID": "🔴",
    "AVOID — overheated": "🔥",
    "NO DATA": "❓",
}


def _fv_note_for(iv_mod, r, fv_by_ticker, etf_set, fmp_available) -> str | None:
    """Fair-value note for a single-stock recommendation header. None → skip."""
    if iv_mod is None:
        return None
    tk = (r.ticker or "").upper()
    if iv_mod.is_etf(tk, etf_set):
        return iv_mod.format_fv_note(tk, r.spot, None, etf_set=etf_set)
    if not fmp_available:
        return None  # no FMP key — don't spam per-line n/a; footer explains
    return iv_mod.format_fv_note(tk, r.spot, (fv_by_ticker or {}).get(tk), etf_set=etf_set)


def _render_report(results_by_theme: dict[str, list[ScoutResult]],
                   theme_meta: dict, generated_at: str,
                   iv_mod=None, fv_by_ticker: dict | None = None,
                   etf_set=None, fmp_available: bool = False) -> str:
    lines = [f"# Thematic Scout Report — {generated_at}", ""]
    lines.append(
        "_Read-only research across thematic universes. Each ticker analyzed "
        "with yfinance technicals + E*TRADE chain (for CSP entries) + "
        "third-party recs (if available)._"
    )
    lines.append("")

    # Top-line summary
    all_results = [r for rs in results_by_theme.values() for r in rs]
    buys = [r for r in all_results if r.verdict.startswith("BUY")]
    csps = [r for r in all_results if r.verdict.startswith("CSP")]
    avoids = [r for r in all_results if r.verdict.startswith("AVOID")]
    lines.append(
        f"**Summary:** {len(buys)} BUY · {len(csps)} CSP ENTRY · "
        f"{len(avoids)} AVOID · {len(all_results)} analyzed"
    )
    lines.append("")

    for theme_key, results in results_by_theme.items():
        meta = theme_meta.get(theme_key, {})
        title = meta.get("name", theme_key)
        notes = meta.get("notes", "")
        etfs = meta.get("etfs") or []
        lines.append(f"## 🔭 {title}")
        if notes:
            lines.append(f"_{notes}_")
        if etfs:
            lines.append(f"_Benchmarks: {', '.join(etfs)}_")
        lines.append("")

        # Sort: BUY first, then CSP, then WATCH, then AVOID, then NO DATA
        order = {"BUY": 1, "CSP": 2, "WATCH": 3, "AVOID": 4, "NO DATA": 5}

        def _bucket(v: str) -> int:
            for k, n in order.items():
                if v.startswith(k):
                    return n
            return 6

        for r in sorted(results, key=lambda x: (_bucket(x.verdict), x.ticker)):
            emoji = _VERDICT_EMOJI.get(r.verdict, "•")
            spot_str = f"${r.spot:.2f}" if r.spot else "?"
            header = f"### {emoji} {r.verdict} · `{r.ticker}` · {spot_str}"
            # Fair value on actionable single-stock recommendations (BUY / CSP).
            if r.verdict.startswith("BUY") or r.verdict.startswith("CSP"):
                note = _fv_note_for(iv_mod, r, fv_by_ticker, etf_set, fmp_available)
                if note:
                    header += f"  · {note}"
            lines.append(header)

            metrics = []
            if r.rsi_14 is not None:
                metrics.append(f"RSI **{r.rsi_14:.0f}**")
            if r.iv_rank is not None:
                metrics.append(f"IV rank **{r.iv_rank:.0f}**")
            if r.drawdown_pct is not None:
                metrics.append(f"drawdown **{r.drawdown_pct:.0f}%**")
            if r.sma_200 and r.spot:
                pct = (r.spot - r.sma_200) / r.sma_200 * 100
                metrics.append(f"vs 200-SMA **{pct:+.0f}%**")
            if r.fivedayret_pct is not None:
                metrics.append(f"5d **{r.fivedayret_pct:+.1f}%**")
            if metrics:
                lines.append("- " + " · ".join(metrics))

            if r.third_party_rec:
                lines.append(f"- Third-party: **{r.third_party_rec}**")

            if r.days_to_earnings is not None:
                lines.append(f"- Earnings: {r.earnings_date} ({r.days_to_earnings}d away)")

            if r.csp_entry:
                q = r.csp_entry
                exp_pretty = q.get("expiration") or ""
                try:
                    exp_pretty = date.fromisoformat(q["expiration"]).strftime("%a %b %d '%y")
                except (ValueError, KeyError, TypeError):
                    pass
                lines.append(
                    f"- **CSP entry ticket:** SELL 1× {r.ticker} ${q['strike']:g}P "
                    f"exp **{exp_pretty}** ({q.get('dte', '?')} DTE) · "
                    f"mid ${q.get('mid', 0):.2f} "
                    f"(bid ${q.get('bid', 0):.2f} / ask ${q.get('ask', 0):.2f}) · "
                    f"_Source: Live E*TRADE chain_"
                )

            if r.rationale:
                lines.append(f"- _Why:_ {'; '.join(r.rationale)}")

            lines.append("")

    if iv_mod is not None:
        if fmp_available:
            lines.append(
                "_💵 Intrinsic value (FMP DCF + analyst price targets) shown on "
                "actionable single-stock picks; ETFs marked basket._"
            )
        else:
            lines.append(
                "_💵 Intrinsic value unavailable — FMP_API_KEY not configured; "
                "values not fabricated (fail-closed)._"
            )
        lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Thematic scout — research across themes")
    parser.add_argument("--themes", default="all",
                        help='Comma-separated theme keys or "all"')
    parser.add_argument("--output", default=None,
                        help="Output markdown path (default: ~/Documents/briefings/scout_DATE.md)")
    parser.add_argument("--rules", default=str(_RULES),
                        help="Path to theme_universes.yaml")
    parser.add_argument("--max-workers", type=int, default=8,
                        help="Parallel research workers (default 8)")
    args = parser.parse_args()

    rules = yaml.safe_load(Path(args.rules).read_text())
    themes_cfg = rules.get("themes", {}) or {}
    verdict_cfg = rules.get("verdict", {}) or {}

    # Defaults if YAML is partial
    verdict_cfg.setdefault("rsi_oversold", 35)
    verdict_cfg.setdefault("rsi_overheated", 75)
    verdict_cfg.setdefault("drawdown_oversold_pct", 10)
    verdict_cfg.setdefault("drawdown_thesis_broken_pct", 30)
    verdict_cfg.setdefault("sma_within_pct", 5)
    verdict_cfg.setdefault("csp_target_otm_pct", 10)
    verdict_cfg.setdefault("csp_target_dte", 35)
    verdict_cfg.setdefault("iv_rank_elevated", 50)
    # Prefer standard monthly (3rd-Friday) expirations for CSP tickets —
    # institutional OI/liquidity (2026-08-10). Standalone CLI default: on.
    verdict_cfg.setdefault("prefer_monthly_expiration", True)

    # Resolve theme keys
    requested = (args.themes or "all").strip()
    if requested == "all":
        theme_keys = list(themes_cfg.keys())
    else:
        theme_keys = [k.strip() for k in requested.split(",") if k.strip()]
    if not theme_keys:
        print("No themes specified", file=sys.stderr)
        return 1

    # Optional inputs from latest briefing snapshot
    recs_map, held_weights, existing_short_puts = _load_recs_and_weights()
    if recs_map:
        print(f"  Loaded {len(recs_map)} third-party recs from latest briefing snapshot")
    if held_weights:
        print(f"  Loaded {len(held_weights)} held-weight entries from latest briefing snapshot")
    if existing_short_puts:
        print(f"  Loaded existing short puts for {len(existing_short_puts)} tickers (put-stack guard active)")

    # Build flat job list
    jobs: list[tuple[str, str]] = []
    for tk in theme_keys:
        if tk not in themes_cfg:
            print(f"  [warn] unknown theme: {tk}", file=sys.stderr)
            continue
        for ticker in themes_cfg[tk].get("anchors", []) or []:
            jobs.append((tk, ticker.upper()))

    if not jobs:
        print("No tickers to analyze", file=sys.stderr)
        return 1

    print(f"  Researching {len(jobs)} tickers across {len(theme_keys)} themes "
          f"(workers={args.max_workers})...")

    results_by_theme: dict[str, list[ScoutResult]] = {tk: [] for tk in theme_keys}
    started = datetime.now()
    with ThreadPoolExecutor(max_workers=args.max_workers, thread_name_prefix="scout") as ex:
        future_to_job = {
            ex.submit(_research_ticker, ticker, theme, verdict_cfg,
                      recs_map, held_weights, existing_short_puts):
                (theme, ticker)
            for (theme, ticker) in jobs
        }
        for fut in as_completed(future_to_job):
            theme, ticker = future_to_job[fut]
            try:
                r = fut.result(timeout=90)
            except Exception as e:
                print(f"  [warn] {ticker} ({theme}) failed: {e}", file=sys.stderr)
                continue
            results_by_theme[theme].append(r)

    elapsed = (datetime.now() - started).total_seconds()
    print(f"  Research complete in {elapsed:.1f}s")

    # Intrinsic value (fail-closed; shares the daily-briefing 24h cache).
    iv_mod = _load_intrinsic_module()
    fv_map: dict = {}
    etf_set: set = set()
    fmp_available = False
    if iv_mod is not None:
        etf_set = iv_mod.default_etf_set(None)
        fmp_key = os.getenv("FMP_API_KEY")
        fmp_available = bool(fmp_key)
        if fmp_key:
            rec_tickers = {
                r.ticker.upper()
                for rs in results_by_theme.values() for r in rs
                if r.ticker
                and (r.verdict.startswith("BUY") or r.verdict.startswith("CSP"))
                and not iv_mod.is_etf(r.ticker.upper(), etf_set)
            }
            if rec_tickers:
                cache_path = (_REPO_ROOT / "skills" / "daily-portfolio-briefing"
                              / "state" / "intrinsic_value_cache.json")
                fv_map = iv_mod.get_fair_values(
                    sorted(rec_tickers), cache_path=cache_path, api_key=fmp_key,
                )

    # Render
    generated_at = datetime.now().strftime("%A, %B %d, %Y · %I:%M %p")
    md = _render_report(
        results_by_theme, themes_cfg, generated_at,
        iv_mod=iv_mod, fv_by_ticker=fv_map, etf_set=etf_set,
        fmp_available=fmp_available,
    )

    # Output
    if args.output:
        out_path = Path(args.output).expanduser()
    else:
        delivery_dir = Path(os.getenv(
            "PORTFOLIO_BRIEFING_DELIVERY_DIR",
            str(Path.home() / "Documents" / "briefings"),
        )).expanduser()
        delivery_dir.mkdir(parents=True, exist_ok=True)
        out_path = delivery_dir / f"scout_{date.today().isoformat()}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)
    print(f"\nScout report written to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
