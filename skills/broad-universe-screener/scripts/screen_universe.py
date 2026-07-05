#!/usr/bin/env python3
"""Broad Universe Screener — daily wheel-setup scan on names NOT already
tracked by Parkev's list or the Thematic Scout.

Three-stage funnel (best tool per job):
  1. FMP /stable/company-screener — broad liquid-universe scan (~1200-1500
     US names: cap > $2B, avg vol > 500K, NYSE/NASDAQ, no ETFs, actively
     trading). ONE API call per run — trivial against the FMP daily quota.
     Replaces the FINVIZ Elite CSV export (paid subscription no longer
     required; FMP_API_KEY is already required by intrinsic_value.py).
  2. Exclusion pass — drop every ticker in theme_universes.yaml (anchors +
     etfs) or Parkev's cached recommendation list. Missing sources = HARD
     error (dedup is load-bearing; see CLAUDE.md).
  3. yfinance deep-dive on the survivors ONLY (capped via deep_dive_max,
     parallelized, RSI reject-early): RSI(14) Wilder's, IV rank (252-obs
     realized-vol percentile — canonical technical_indicators.iv_rank_252,
     shared with snapshot_inputs and the Scout), support clusters via
     the briefing's support_resistance module, earnings date, options-chain
     check.

Discipline gates (all must pass): RSI 35-50, IV rank >= 60, spot within 5%
of a >=3-touch support with >=90d history, no earnings within 21 days
(unknown date fails closed), options chain exists, market cap > $2B.

Fail-closed on data (hard rule #19: no fabricated numbers), fail-open at the
pipeline level (Step 8.7 catches everything).

Usage:
    python3 skills/broad-universe-screener/scripts/screen_universe.py \
        --output reports/daily/daily_screener_2026-07-03.md
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[2]


class ScreenerError(Exception):
    """Fatal screener error — the run cannot produce trustworthy output."""


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CONFIG: dict[str, Any] = {
    "fmp_screener": {
        # FMP /stable/ API — legacy /api/v3 returns 403 for keys created
        # after 2025-08-31 (same rule as intrinsic_value.py).
        "endpoint": "https://financialmodelingprep.com/stable/company-screener",
        "market_cap_floor_usd": 2_000_000_000,  # $2B+ (options-liquidity proxy)
        "volume_floor": 500_000,                # avg daily volume > 500K
        "exchange": "NYSE,NASDAQ",
        "country": "US",
        "limit": 1500,                          # max universe pull
        "timeout_seconds": 30,
    },
    # Deep-dive load controls — FMP pre-filters less tightly than the old
    # FINVIZ RSI/SMA filters, so more survivors reach yfinance.
    "deep_dive_max": 800,    # hard cap on post-exclusion names deep-dived
    "parallel_fetch": True,  # ThreadPoolExecutor across yfinance fetches
    "gates": {
        "rsi_min": 35.0,
        "rsi_max": 50.0,
        "iv_rank_min": 60.0,
        "support_max_distance_pct": 5.0,
        "support_min_touches": 3,
        "min_hist_days": 90,
        "earnings_min_days": 21,
        "min_market_cap_usd": 2_000_000_000,
    },
    "score": {
        "rsi_weight": 3.5,
        "iv_weight": 3.5,
        "support_weight": 3.0,
        "support_strength_cap": 5.0,
    },
    "output": {"max_rows": 30},
    "exclusions": {
        "theme_universes_path": "skills/thematic-scout/references/theme_universes.yaml",
        "parkev_cache_path": "state/cache/recommendation_list.json",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:
            out[k] = v
    return out


def load_config(path: Path | None = None) -> dict:
    """Load screener_config.yaml merged over defaults. Bad/missing YAML → defaults."""
    if path is None:
        path = _HERE.parent / "config" / "screener_config.yaml"
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    try:
        import yaml
        if Path(path).exists():
            loaded = yaml.safe_load(Path(path).read_text()) or {}
            cfg = _deep_merge(cfg, loaded)
    except Exception as e:  # noqa: BLE001
        print(f"WARN: screener_config.yaml unreadable ({e}) — using defaults", file=sys.stderr)
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# Exclusion set (dedup — load-bearing, fail closed)
# ─────────────────────────────────────────────────────────────────────────────

def load_scout_tickers(theme_universes_path: Path) -> set[str]:
    """All anchors + etfs across every theme. Missing/unparseable → ScreenerError."""
    if not Path(theme_universes_path).exists():
        raise ScreenerError(f"theme universe file missing: {theme_universes_path}")
    try:
        import yaml
        data = yaml.safe_load(Path(theme_universes_path).read_text()) or {}
    except Exception as e:  # noqa: BLE001
        raise ScreenerError(f"theme universe unparseable: {e}") from e
    themes = data.get("themes") or {}
    out: set[str] = set()
    for theme in themes.values():
        if not isinstance(theme, dict):
            continue
        for key in ("anchors", "etfs"):
            for t in theme.get(key) or []:
                # str() guards the YAML-boolean trap (bare ON → True)
                out.add(str(t).upper().strip())
    if not out:
        raise ScreenerError(f"theme universe yielded no tickers: {theme_universes_path}")
    return out


def load_parkev_tickers(parkev_cache_path: Path) -> set[str]:
    """All tickers in Parkev's cached recommendation list.

    Cache shape: bare JSON list of entries with a "ticker" key (written by
    recommendation-list-fetcher); dict-with-"recommendations" also accepted.
    Missing/empty → ScreenerError (a stale-or-absent Parkev cache means the
    briefing hasn't run its fetch step — dedup would be wrong).
    """
    if not Path(parkev_cache_path).exists():
        raise ScreenerError(f"Parkev cache missing: {parkev_cache_path}")
    try:
        data = json.loads(Path(parkev_cache_path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ScreenerError(f"Parkev cache unparseable: {e}") from e
    entries = data if isinstance(data, list) else (data or {}).get("recommendations") or []
    out = {
        str(e.get("ticker")).upper().strip()
        for e in entries
        if isinstance(e, dict) and e.get("ticker")
    }
    if not out:
        raise ScreenerError(f"Parkev cache yielded no tickers: {parkev_cache_path}")
    return out


def load_exclusions(config: dict, repo_root: Path = REPO_ROOT) -> tuple[set[str], int, int]:
    """Return (exclusion_set, n_scout, n_parkev). Fail closed on missing sources."""
    exc_cfg = config.get("exclusions") or {}
    scout = load_scout_tickers(repo_root / exc_cfg["theme_universes_path"])
    parkev = load_parkev_tickers(repo_root / exc_cfg["parkev_cache_path"])
    return scout | parkev, len(scout), len(parkev)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — FMP company-screener broad scan
# ─────────────────────────────────────────────────────────────────────────────

def parse_fmp_rows(data: Any) -> list[dict]:
    """Map the FMP company-screener JSON list to the internal row format.

    Rows without a symbol are dropped; unparseable market caps stay None
    (the market-cap gate fails closed on them downstream).
    """
    if not isinstance(data, list):
        return []
    rows: list[dict] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("symbol") or "").upper().strip()
        if not ticker:
            continue
        cap = raw.get("marketCap")
        try:
            cap = float(cap) if cap is not None else None
        except (TypeError, ValueError):
            cap = None
        rows.append({
            "ticker": ticker,
            "company": str(raw.get("companyName") or ""),
            "sector": str(raw.get("sector") or ""),
            "industry": str(raw.get("industry") or ""),
            "market_cap_usd": cap,
            "volume": raw.get("volume"),
        })
    return rows


def fmp_screen(config: dict, api_key: str | None) -> tuple[list[dict], str | None]:
    """Run the FMP /stable/company-screener. Returns (rows, note).

    Contract (task #8):
    - No key → ScreenerError (fail closed, same shape as the old FINVIZ path).
    - HTTP 429 (rate limit) → ([], note) so the briefing ships with an
      explicit "scan skipped" note instead of dying.
    - Other non-200 → ([], note), logged to stderr.
    - 200 with zero rows → ScreenerError (a $2B+/500K-vol US query never
      legitimately returns nothing — the query or the API is broken).
    """
    if not api_key:
        raise ScreenerError("FMP_API_KEY not set — broad scan unavailable (fail closed)")
    fs = config["fmp_screener"]
    import requests
    params = {
        "apikey": api_key,
        "marketCapMoreThan": int(fs["market_cap_floor_usd"]),
        "volumeMoreThan": int(fs["volume_floor"]),
        "exchange": fs["exchange"],
        # Lowercase strings on purpose — Python bools serialize as True/False,
        # which the FMP API does not accept.
        "isEtf": "false",
        "isActivelyTrading": "true",
        "country": fs["country"],
        "limit": int(fs["limit"]),
    }
    try:
        resp = requests.get(fs["endpoint"], params=params,
                            timeout=float(fs["timeout_seconds"]))
    except Exception as e:  # noqa: BLE001
        raise ScreenerError(f"FMP screener request failed: {e}") from e
    if resp.status_code == 429:
        note = "FMP rate limit hit (HTTP 429) — broad scan skipped this cycle"
        print(f"  [warn] {note}", file=sys.stderr)
        return [], note
    if resp.status_code != 200:
        note = f"FMP screener HTTP {resp.status_code} — broad scan skipped this cycle"
        print(f"  [warn] {note}", file=sys.stderr)
        return [], note
    try:
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        raise ScreenerError(f"FMP screener returned non-JSON: {e}") from e
    rows = parse_fmp_rows(data)
    if not rows:
        raise ScreenerError("FMP screener returned zero rows")
    return rows, None


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — yfinance deep-dive (survivors only)
# ─────────────────────────────────────────────────────────────────────────────

_OHLC_MODULE = None
_OHLC_TRIED = False


def _load_ohlc_cache():
    """Load the briefing's persistent OHLC cache by path (task #9).

    Fail-open: any problem returns None and deep_dive uses the raw yfinance
    history call, exactly as before.
    """
    global _OHLC_MODULE, _OHLC_TRIED
    if _OHLC_TRIED:
        return _OHLC_MODULE
    _OHLC_TRIED = True
    target = (REPO_ROOT / "skills" / "daily-portfolio-briefing" / "scripts"
              / "analysis" / "ohlc_cache.py")
    if not target.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("bus_ohlc_cache", target)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bus_ohlc_cache"] = mod
        spec.loader.exec_module(mod)
        _OHLC_MODULE = mod
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] ohlc_cache load failed: {e}", file=sys.stderr)
        _OHLC_MODULE = None
    return _OHLC_MODULE


_SR_MODULE = None


def _load_sr_module():
    """Load the briefing's support_resistance module by path (sibling skill)."""
    global _SR_MODULE
    if _SR_MODULE is not None:
        return _SR_MODULE
    target = (REPO_ROOT / "skills" / "daily-portfolio-briefing" / "scripts"
              / "analysis" / "support_resistance.py")
    if not target.exists():
        return None
    spec = importlib.util.spec_from_file_location("bus_support_resistance", target)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bus_support_resistance"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] support_resistance load failed: {e}", file=sys.stderr)
        return None
    _SR_MODULE = mod
    return mod


def compute_rsi_14(closes) -> float | None:
    """RSI(14), Wilder's smoothing — identical math to the Scout's."""
    if len(closes) < 30:
        return None
    delta = closes.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / 14, adjust=False).mean()
    last_gain = float(avg_gain.iloc[-1])
    last_loss = float(avg_loss.iloc[-1])
    if last_loss > 0:
        return round(100.0 - (100.0 / (1.0 + last_gain / last_loss)), 1)
    if last_gain > 0:
        return 100.0
    return 50.0


_TI_MODULE = None
_TI_TRIED = False


def _load_ti_module():
    """Load the briefing's technical_indicators module by path (sibling skill).

    Fail-open — compute_iv_rank falls back to its inline math when the module
    can't load (e.g. running the screener standalone outside the repo layout).
    """
    global _TI_MODULE, _TI_TRIED
    if _TI_TRIED:
        return _TI_MODULE
    _TI_TRIED = True
    target = (REPO_ROOT / "skills" / "daily-portfolio-briefing" / "scripts"
              / "analysis" / "technical_indicators.py")
    if not target.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            "bus_technical_indicators", target)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bus_technical_indicators"] = mod
        spec.loader.exec_module(mod)
        _TI_MODULE = mod
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] technical_indicators load failed: {e}", file=sys.stderr)
        _TI_MODULE = None
    return _TI_MODULE


def compute_iv_rank(closes) -> float | None:
    """IV rank proxy: 252-obs realized-vol percentile.

    Canonical implementation is the briefing's technical_indicators.iv_rank_252
    (shared with snapshot_inputs and the thematic scout — Task #12). Inline
    fallback keeps the screener working standalone; same formula incl. the
    .tail(252) window cap.
    """
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


def best_support(sr_payload: dict | None, spot: float, *,
                 max_distance_pct: float, min_touches: int) -> dict | None:
    """Strongest support cluster with >= min_touches, within max_distance_pct
    BELOW spot. None when no qualifying level (fail closed — never fabricate)."""
    if not sr_payload or not spot:
        return None
    candidates = []
    for lv in sr_payload.get("supports") or []:
        price = lv.get("price")
        touches = int(lv.get("touches") or 0)
        if price is None or price <= 0 or price > spot:
            continue
        dist_pct = (spot - float(price)) / spot * 100.0
        if dist_pct <= max_distance_pct and touches >= min_touches:
            candidates.append(lv)
    if not candidates:
        return None
    return max(candidates, key=lambda lv: float(lv.get("strength") or 0.0))


# RSI reject-early band: values this far outside the 35-50 gate can never
# pass, so the expensive S/R + earnings + chain fetches are skipped. The band
# is deliberately WIDER than the gate (25/65 vs 35/50) so borderline names
# still get the full picture in fail_reasons.
REJECT_EARLY_RSI_LOW = 25.0
REJECT_EARLY_RSI_HIGH = 65.0


def deep_dive(ticker: str, *, as_of: date | None = None) -> dict | None:
    """Fetch + compute the full technical picture for one ticker via yfinance.

    Returns a dict consumed by passes_gates(); None on total fetch failure.
    Missing individual fields stay None — gates fail closed on them.
    """
    as_of = as_of or date.today()
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        # OHLC via the persistent cache when available (task #9); fail-open
        # to the raw yfinance history call. `t` is still needed below for
        # the earnings-calendar and options-chain lookups.
        hist = None
        ohlc = _load_ohlc_cache()
        if ohlc is not None:
            try:
                hist = ohlc.cached_history(ticker, days=300)
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] ohlc cache {ticker}: {e} — uncached path",
                      file=sys.stderr)
                hist = None
        if hist is None:
            hist = t.history(period="300d")
        if hist is None or hist.empty:
            return None
        closes = hist["Close"].dropna()
        if closes.empty:
            return None
        spot = float(closes.iloc[-1])

        sma_50 = round(float(closes.tail(50).mean()), 2) if len(closes) >= 50 else None
        sma_200 = round(float(closes.tail(200).mean()), 2) if len(closes) >= 200 else None

        rsi_14 = compute_rsi_14(closes)
        # Reject-early: with a bigger FMP survivor set, don't spend the S/R +
        # earnings + chain calls on names the RSI gate will reject anyway.
        if rsi_14 is not None and (
            rsi_14 > REJECT_EARLY_RSI_HIGH or rsi_14 < REJECT_EARLY_RSI_LOW
        ):
            return {
                "spot": round(spot, 2),
                "hist_days": int(len(closes)),
                "rsi_14": rsi_14,
                "iv_rank": compute_iv_rank(closes),
                "sma_50": sma_50,
                "sma_200": sma_200,
                "support_resistance": None,
                "earnings_date": None,
                "days_to_earnings": None,
                "has_options": False,
                "expiries": [],
                "early_reject": True,
            }

        sr_payload = None
        sr_mod = _load_sr_module()
        if sr_mod is not None:
            try:
                sr_payload = sr_mod.compute_sr(
                    hist, spot=spot, sma_50=sma_50, sma_200=sma_200
                ).to_dict()
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] S/R compute {ticker}: {e}", file=sys.stderr)

        # Earnings date (next upcoming) — None when unknown (gate fails closed).
        earnings_date = None
        days_to_earnings = None
        try:
            cal = getattr(t, "calendar", None)
            if isinstance(cal, dict):
                for d in cal.get("Earnings Date") or []:
                    dd = d.date() if hasattr(d, "date") else d
                    if isinstance(dd, date) and dd >= as_of:
                        earnings_date = dd.strftime("%Y-%m-%d")
                        days_to_earnings = (dd - as_of).days
                        break
            if earnings_date is None:
                ed = t.get_earnings_dates(limit=4)
                if ed is not None and not ed.empty:
                    for idx in ed.index:
                        dd = idx.date() if hasattr(idx, "date") else idx
                        if isinstance(dd, date) and dd >= as_of:
                            earnings_date = dd.strftime("%Y-%m-%d")
                            days_to_earnings = (dd - as_of).days
                            break
        except Exception:  # noqa: BLE001
            pass

        # Options chain existence — live check, never assumed.
        expiries: list[str] = []
        try:
            expiries = list(t.options or [])
        except Exception:  # noqa: BLE001
            expiries = []

        return {
            "spot": round(spot, 2),
            "hist_days": int(len(closes)),
            "rsi_14": rsi_14,
            "iv_rank": compute_iv_rank(closes),
            "sma_50": sma_50,
            "sma_200": sma_200,
            "support_resistance": sr_payload,
            "earnings_date": earnings_date,
            "days_to_earnings": days_to_earnings,
            "has_options": bool(expiries),
            "expiries": expiries[:3],
        }
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] deep-dive {ticker}: {e}", file=sys.stderr)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Gates + score (pure — the testable core)
# ─────────────────────────────────────────────────────────────────────────────

def passes_gates(row: dict, config: dict) -> tuple[bool, str | None]:
    """Apply every discipline gate. Returns (passed, first_fail_reason).

    Fail closed: any None on a gated field is a failure with an explicit
    reason — never treated as a pass (hard rule #19).
    """
    g = config["gates"]

    cap = row.get("market_cap_usd")
    if cap is None:
        return False, "market cap unavailable (fail closed)"
    if cap <= float(g["min_market_cap_usd"]):
        return False, f"market cap ${cap / 1e9:.1f}B <= $2B"

    hist_days = row.get("hist_days") or 0
    if hist_days < int(g["min_hist_days"]):
        return False, f"only {hist_days}d of history (< {g['min_hist_days']}d)"

    rsi = row.get("rsi_14")
    if rsi is None:
        return False, "RSI unavailable (fail closed)"
    if not (float(g["rsi_min"]) <= rsi <= float(g["rsi_max"])):
        return False, f"RSI {rsi:.0f} outside {g['rsi_min']:.0f}-{g['rsi_max']:.0f}"

    iv = row.get("iv_rank")
    if iv is None:
        return False, "IV rank unavailable (fail closed)"
    if iv < float(g["iv_rank_min"]):
        return False, f"IV rank {iv:.0f} < {g['iv_rank_min']:.0f}"

    spot = row.get("spot")
    if not spot:
        return False, "spot unavailable (fail closed)"
    support = best_support(
        row.get("support_resistance"), spot,
        max_distance_pct=float(g["support_max_distance_pct"]),
        min_touches=int(g["support_min_touches"]),
    )
    if support is None:
        return False, (f"no support with >={g['support_min_touches']} touches "
                       f"within {g['support_max_distance_pct']:.0f}% of spot")
    row["support"] = support  # stash the qualifying level for score + render

    dte = row.get("days_to_earnings")
    if row.get("earnings_date") is None or dte is None:
        return False, "earnings date unavailable (fail closed)"
    if dte < int(g["earnings_min_days"]):
        return False, f"earnings in {dte}d (< {g['earnings_min_days']}d)"

    if not row.get("has_options"):
        return False, "no options chain"

    return True, None


def composite_score(row: dict, config: dict) -> float:
    """0-10 composite: RSI proximity to 40 + IV rank + support strength."""
    s = config["score"]
    g = config["gates"]
    rsi = float(row["rsi_14"])
    iv = float(row["iv_rank"])
    support = row.get("support") or {}

    # RSI: 40 is the sweet spot; edges of the 35-50 band score lowest.
    half_band = max(abs(40.0 - float(g["rsi_min"])), abs(float(g["rsi_max"]) - 40.0))
    rsi_c = max(0.0, 1.0 - abs(rsi - 40.0) / half_band)

    # IV: linear from the gate floor to 100.
    iv_floor = float(g["iv_rank_min"])
    iv_c = max(0.0, min(1.0, (iv - iv_floor) / max(1.0, 100.0 - iv_floor)))

    # Support: strength (recency-weighted touches + confluence), capped.
    strength = float(support.get("strength") or 0.0)
    sup_c = max(0.0, min(1.0, strength / float(s["support_strength_cap"])))

    total_w = float(s["rsi_weight"]) + float(s["iv_weight"]) + float(s["support_weight"])
    raw = (rsi_c * float(s["rsi_weight"]) + iv_c * float(s["iv_weight"])
           + sup_c * float(s["support_weight"]))
    return round(raw / total_w * 10.0, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScreenResult:
    hits: list[dict] = field(default_factory=list)      # ranked, capped
    scanned: int = 0                                     # FMP screener rows
    after_exclusion: int = 0                             # survived dedup
    deep_dived: int = 0
    passed_gates: int = 0
    n_scout_excluded: int = 0                            # size of Scout exclusion set
    n_parkev_excluded: int = 0                           # size of Parkev exclusion set
    fail_reasons: dict[str, str] = field(default_factory=dict)  # ticker → reason
    note: str | None = None                              # e.g. FMP 429 → scan skipped


def run_screener(
    config: dict,
    *,
    api_key: str | None = None,
    repo_root: Path = REPO_ROOT,
    universe_rows: list[dict] | None = None,
    deep_dive_fn: Callable[[str], dict | None] | None = None,
) -> ScreenResult:
    """Full three-stage run. ``universe_rows`` / ``deep_dive_fn`` are
    injectable for tests (no network in CI)."""
    exclusions, n_scout, n_parkev = load_exclusions(config, repo_root)

    note: str | None = None
    if universe_rows is None:
        universe_rows, note = fmp_screen(config, api_key or os.getenv("FMP_API_KEY"))
    result = ScreenResult(scanned=len(universe_rows), note=note,
                          n_scout_excluded=n_scout, n_parkev_excluded=n_parkev)

    survivors = [r for r in universe_rows if r["ticker"] not in exclusions]
    result.after_exclusion = len(survivors)

    max_dd = int(config.get("deep_dive_max", 800))
    survivors = survivors[:max_dd]  # scale-trap guard
    dd = deep_dive_fn or deep_dive

    # The FMP universe is 5-10x the old FINVIZ pre-filtered one, so the
    # deep-dive fans out across threads (yfinance is I/O-bound). map()
    # preserves input order — results stay deterministic.
    if bool(config.get("parallel_fetch", True)) and len(survivors) > 1:
        from concurrent.futures import ThreadPoolExecutor
        workers = min(8, len(survivors))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            techs = list(ex.map(lambda r: dd(r["ticker"]), survivors))
    else:
        techs = [dd(r["ticker"]) for r in survivors]

    passed: list[dict] = []
    for u_row, tech in zip(survivors, techs):
        result.deep_dived += 1
        if tech is None:
            result.fail_reasons[u_row["ticker"]] = "yfinance returned no data (fail closed)"
            continue
        row = {**u_row, **tech}
        ok, why = passes_gates(row, config)
        if not ok:
            result.fail_reasons[row["ticker"]] = why or "failed gates"
            continue
        row["score"] = composite_score(row, config)
        passed.append(row)

    result.passed_gates = len(passed)
    passed.sort(key=lambda r: (-r["score"], r["ticker"]))
    result.hits = passed[: int(config["output"]["max_rows"])]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Output cache (task #9) — skip the whole FMP + yfinance run when a fresh
# same-config result for today already exists. The cache stores the RAW
# ScreenResult (pre fair-value annotation, pre render) so the capacity banner
# and FV notes are always re-computed fresh — a cache hit changes nothing but
# runtime. Fail-open on every path: any cache problem → full screener run.
# ─────────────────────────────────────────────────────────────────────────────

def screener_config_hash(config: dict) -> str:
    """Deterministic hash of the screener config — config change ⇒ cache miss."""
    import hashlib
    blob = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _output_cache_path(cache_dir: Path, date_str: str) -> Path:
    return Path(cache_dir) / f"broad_screener_output_{date_str}.json"


def load_cached_result(cache_dir: Path, date_str: str, config: dict,
                       *, ttl_hours: float = 6.0) -> ScreenResult | None:
    """Return the cached ScreenResult, or None on miss/stale/config-change/
    corruption. Never raises."""
    path = _output_cache_path(cache_dir, date_str)
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if data.get("date") != date_str:
            return None
        if data.get("config_hash") != screener_config_hash(config):
            return None
        from datetime import datetime, timedelta
        generated = datetime.fromisoformat(data["generated_at"])
        if (datetime.now() - generated) >= timedelta(hours=float(ttl_hours)):
            return None
        return ScreenResult(**data["result"])
    except Exception as e:  # noqa: BLE001 - fail-open: treat as cache miss
        print(f"  [warn] screener output cache unreadable ({e}) — full run",
              file=sys.stderr)
        return None


def save_cached_result(cache_dir: Path, date_str: str, config: dict,
                       result: ScreenResult) -> None:
    """Persist the result for reuse within the TTL. Fail-open on write errors.

    Degraded results (note set, e.g. FMP 429 → scan skipped) are NOT cached —
    caching them would suppress the retry on the next run.
    """
    if result.note or result.scanned == 0:
        return
    try:
        from dataclasses import asdict
        from datetime import datetime
        path = _output_cache_path(cache_dir, date_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "date": date_str,
            "config_hash": screener_config_hash(config),
            "generated_at": datetime.now().isoformat(),
            "result": asdict(result),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str))
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] screener output cache write failed: {e}", file=sys.stderr)


def run_screener_cached(
    config: dict,
    *,
    date_str: str,
    cache_dir: Path,
    cache_enabled: bool = True,
    ttl_hours: float = 6.0,
    **kwargs: Any,
) -> tuple[ScreenResult, bool]:
    """run_screener with an output cache in front. Returns (result, from_cache).

    Cache hit ⇒ zero FMP / yfinance calls (the hit is checked BEFORE
    exclusions are loaded). ``kwargs`` pass through to run_screener
    (api_key / repo_root / universe_rows / deep_dive_fn for tests).
    """
    if cache_enabled:
        cached = load_cached_result(cache_dir, date_str, config, ttl_hours=ttl_hours)
        if cached is not None:
            return cached, True
    result = run_screener(config, **kwargs)
    if cache_enabled:
        save_cached_result(cache_dir, date_str, config, result)
    return result, False


# ─────────────────────────────────────────────────────────────────────────────
# Optional FMP fair-value annotation (fail-soft — never a gate)
# ─────────────────────────────────────────────────────────────────────────────

def annotate_fair_values(hits: list[dict], *, api_key: str | None,
                         cache_path: Path | None = None) -> None:
    """Attach an 'fv_note' to each hit via the briefing's intrinsic_value
    module. Any failure → no note (never a fabricated number)."""
    if not api_key or not hits:
        return
    try:
        target = (REPO_ROOT / "skills" / "daily-portfolio-briefing" / "scripts"
                  / "analysis" / "intrinsic_value.py")
        spec = importlib.util.spec_from_file_location("bus_intrinsic_value", target)
        if spec is None or spec.loader is None:
            return
        iv_mod = importlib.util.module_from_spec(spec)
        sys.modules["bus_intrinsic_value"] = iv_mod
        spec.loader.exec_module(iv_mod)
        tickers = [h["ticker"] for h in hits]
        kwargs: dict = {"api_key": api_key}
        if cache_path is not None:
            kwargs["cache_path"] = cache_path
        fv = iv_mod.get_fair_values(tickers, **kwargs)
        for h in hits:
            v = (fv or {}).get(h["ticker"])
            if v:
                try:
                    h["fv_note"] = iv_mod.format_fv_note(v, h.get("spot"))
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] FMP fair-value annotation skipped: {e}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────

def _one_line_why(row: dict) -> str:
    return (f"RSI {row['rsi_14']:.0f} pullback into support with "
            f"IV rank {row['iv_rank']:.0f} premium")


def render_report(result: ScreenResult, *, date_str: str,
                  capacity_banner: str | None = None,
                  capacity_open: bool = True) -> str:
    """Markdown report — mirrors the candidates_<DATE>.md style, simpler."""
    lines: list[str] = []
    if capacity_banner:
        lines += [capacity_banner, ""]
    lines += [
        f"# Broad Universe Screener — {date_str}",
        "",
        (f"Scanned {result.scanned} candidates from the FMP company-screener "
         f"universe (cap > $2B, avg vol > 500K, NYSE/NASDAQ, US, no ETFs — "
         f"approximates S&P 500 + Nasdaq 100 + Russell 1000). "
         f"{result.after_exclusion} passed the exclusion filter; "
         f"{result.deep_dived} deep-dived; "
         f"{result.passed_gates} passed discipline gates. Excluded "
         f"already-covered names ({result.n_parkev_excluded} Parkev + "
         f"{result.n_scout_excluded} Scout)."),
        "",
    ]
    if result.note:
        lines += [f"_⚠ {result.note}_", ""]
    if not result.hits:
        lines += ["_No setups passed all discipline gates today._", ""]
        return "\n".join(lines)

    lines += ["## Top setups (ranked by composite score)", ""]
    for i, row in enumerate(result.hits, 1):
        sup = row.get("support") or {}
        deferred = "" if capacity_open else " · ⏸ Deferred (capacity gated)"
        lines.append(f"### {i}. {row['ticker']} — ${row['spot']:.2f} · "
                     f"{_one_line_why(row)}{deferred}")
        age = sup.get("age_days")
        hist_bits = f"{sup.get('touches', '?')} touches"
        if age is not None:
            hist_bits += f", last touch {int(age)}d ago"
        hist_bits += f", {row.get('hist_days', '?')}d history"
        sup_price = sup.get("price")
        sup_s = f"${sup_price:.2f}" if sup_price is not None else "n/a"
        lines.append(f"- RSI {row['rsi_14']:.0f} · IV rank {row['iv_rank']:.0f} · "
                     f"at support {sup_s} ({hist_bits})")
        lines.append(f"- Score: {row['score']:.1f}/10")
        lines.append("- Not in Scout themes · Parkev: no coverage")
        lines.append(f"- Earnings: {row['days_to_earnings']}d away "
                     f"({row['earnings_date']} — safe)")
        exp_s = ", ".join(row.get("expiries") or [])
        lines.append(f"- Options chain: yes ({exp_s} expiries available)" if exp_s
                     else "- Options chain: yes")
        if row.get("fv_note"):
            lines.append(f"- {row['fv_note']}")
        lines.append("")
    lines += [
        "---",
        "_Every row is backed by data fetched this cycle (FMP screen + "
        "yfinance OHLC/earnings/chain). Names with missing data were dropped, "
        "never defaulted. Verify the chain at the broker before placing — "
        "this report proposes no ticket._",
        "",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Broad-universe wheel-setup screener")
    p.add_argument("--config", type=Path, default=None,
                   help="screener_config.yaml path (default: skill config)")
    p.add_argument("--output", type=Path, default=None,
                   help="Markdown output path (default: stdout)")
    p.add_argument("--date", type=str, default=None, help="Report date (YYYY-MM-DD)")
    args = p.parse_args(argv)

    config = load_config(args.config)
    date_str = args.date or date.today().isoformat()
    try:
        result = run_screener(config)
    except ScreenerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    annotate_fair_values(result.hits, api_key=os.getenv("FMP_API_KEY"))
    md = render_report(result, date_str=date_str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(md)
        print(f"Wrote {args.output} ({len(result.hits)} setups)")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
