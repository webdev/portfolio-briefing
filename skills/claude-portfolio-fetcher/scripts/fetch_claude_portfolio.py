#!/usr/bin/env python3
"""Fetch the public Autopilot "Claude Portfolio" holdings.

Source cascade (provenance-labeled, NEVER fabricated):
  1. live_fetch — the joinautopilot.com portfolio landing page; holdings
     (symbol + percentOfPortfolio) are embedded in its Next.js flight data.
  2. cache — a successful live fetch younger than cache_hours (default 24).
  3. manual_seed — config/claude_portfolio_manual.yaml (pasted from the
     app, with an as_of date; stale seeds are flagged, not hidden).
  4. unavailable — empty holdings, still a valid payload (fail-open).

See SKILL.md for the downstream weighting discipline (corroboration only —
the agreement bonus fires only when Parkev ALSO says BUY).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_ROOT / "config" / "claude_portfolio_config.yaml"
DEFAULT_SOURCE_URL = "https://www.joinautopilot.com/landing/1/950048"

_USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36")

# Holdings objects inside the (unescaped) flight data:
#   {"assetKey":2057,"name":"...","symbol":"DHT","pictureUrl":"...",
#    "percentOfPortfolio":0.0481}
_HOLDING_RE = re.compile(
    r'\{"assetKey":\d+,"name":"([^"]*)","symbol":"([A-Z][A-Z0-9.\-]{0,5})",'
    r'"pictureUrl":"[^"]*","percentOfPortfolio":([0-9.eE+-]+)\}')

# Sanity bounds — a parse outside these is treated as a FAILED parse (the
# page layout changed / we grabbed the wrong blob), never partial truth.
_MIN_HOLDINGS, _MAX_HOLDINGS = 1, 60
_MIN_WEIGHT_SUM, _MAX_WEIGHT_SUM = 40.0, 130.0   # percent


def _load_yaml(path: Path) -> dict:
    import yaml
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, Exception):
        return {}


def load_config(path: Path | None = None) -> dict:
    cfg = _load_yaml(path or DEFAULT_CONFIG)
    cfg.setdefault("source_url", DEFAULT_SOURCE_URL)
    cfg.setdefault("timeout_sec", 20)
    cfg.setdefault("cache_hours", 24)
    cfg.setdefault("cache_path", "state/claude_portfolio_cache.json")
    cfg.setdefault("manual_seed_path", "config/claude_portfolio_manual.yaml")
    cfg.setdefault("manual_seed_max_age_days", 45)
    return cfg


def parse_holdings(text: str) -> list[dict]:
    """Extract holdings from the landing-page HTML (flight data) or a raw
    JSON body. Returns [] on any parse/sanity failure — never partial
    fabrication."""
    if not text:
        return []
    # A future JSON endpoint: [{"symbol": ..., "percentOfPortfolio": ...}]
    stripped = text.lstrip()
    if stripped.startswith(("[", "{")):
        try:
            data = json.loads(stripped)
            rows = data if isinstance(data, list) else data.get("holdings") or []
            parsed = []
            for r in rows:
                if not isinstance(r, dict) or not r.get("symbol"):
                    continue
                parsed.append((str(r.get("name") or ""), str(r["symbol"]),
                               str(r.get("percentOfPortfolio") or r.get("weight_pct") or 0)))
            return _normalize(parsed, pct_scale=None)
        except (ValueError, AttributeError):
            pass
    unescaped = text.replace('\\"', '"').replace("\\u0026", "&")
    return _normalize(_HOLDING_RE.findall(unescaped), pct_scale=100.0)


def _normalize(rows: list[tuple], pct_scale: float | None) -> list[dict]:
    seen: dict[str, dict] = {}
    for name, sym, pct in rows:
        sym = sym.upper().strip()
        if not sym or sym in seen:
            continue
        try:
            w = float(pct)
        except (TypeError, ValueError):
            continue
        if pct_scale is not None:
            w *= pct_scale
        elif w <= 1.5:            # JSON path: 0.0481 style → percent
            w *= 100.0
        seen[sym] = {"ticker": sym, "name": name.strip(),
                     "weight_pct": round(w, 2)}
    holdings = sorted(seen.values(), key=lambda h: -h["weight_pct"])
    if not (_MIN_HOLDINGS <= len(holdings) <= _MAX_HOLDINGS):
        return []
    total = sum(h["weight_pct"] for h in holdings)
    if not (_MIN_WEIGHT_SUM <= total <= _MAX_WEIGHT_SUM):
        return []
    return holdings


def fetch_live(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def load_manual_seed(path: Path, max_age_days: int = 45) -> dict | None:
    data = _load_yaml(path)
    rows = data.get("holdings")
    if not isinstance(rows, list) or not rows:
        return None
    holdings = []
    for r in rows:
        if isinstance(r, dict) and r.get("ticker"):
            holdings.append({
                "ticker": str(r["ticker"]).upper(),
                "name": str(r.get("name") or ""),
                "weight_pct": float(r.get("weight_pct") or 0),
            })
    if not holdings:
        return None
    as_of = str(data.get("as_of") or "")
    stale = None
    if as_of:
        try:
            age = (datetime.now().date()
                   - datetime.strptime(as_of[:10], "%Y-%m-%d").date()).days
            if age > max_age_days:
                stale = (f"manual seed is {age}d old (> {max_age_days}d) — "
                         f"refresh config/claude_portfolio_manual.yaml")
        except ValueError:
            pass
    payload = {"as_of": as_of or None, "holdings": holdings}
    if stale:
        payload["stale_warning"] = stale
    return payload


def _cache_path(cfg: dict) -> Path:
    p = Path(cfg["cache_path"])
    return p if p.is_absolute() else SKILL_ROOT / p


def _load_cache(cfg: dict) -> dict | None:
    try:
        data = json.loads(_cache_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("holdings"):
        return None
    try:
        fetched = datetime.fromisoformat(str(data.get("fetched_at")).rstrip("Z"))
    except (ValueError, TypeError):
        return None
    if datetime.utcnow() - fetched > timedelta(hours=float(cfg["cache_hours"])):
        return None
    return data


def _save_cache(cfg: dict, payload: dict) -> None:
    try:
        p = _cache_path(cfg)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def fetch_claude_portfolio(config_path: Path | None = None,
                           force_refresh: bool = False) -> dict:
    """Full cascade → payload dict (always returns; fail-open)."""
    cfg = load_config(config_path)
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    today = datetime.now().strftime("%Y-%m-%d")

    if not force_refresh:
        cached = _load_cache(cfg)
        if cached:
            out = dict(cached)
            out["provenance"] = "cache"
            return out

    # 1. Live fetch
    try:
        html = fetch_live(cfg["source_url"], timeout=float(cfg["timeout_sec"]))
        holdings = parse_holdings(html)
    except Exception as e:
        print(f"[claude-portfolio] live fetch failed: {e}", file=sys.stderr)
        holdings = []
    if holdings:
        payload = {
            "fetched_at": now_iso, "as_of": today,
            "provenance": "live_fetch", "source_url": cfg["source_url"],
            "holdings": holdings,
            "tickers": [h["ticker"] for h in holdings],
        }
        _save_cache(cfg, payload)
        return payload

    # 2. Manual seed
    seed_path = Path(cfg["manual_seed_path"])
    if not seed_path.is_absolute():
        seed_path = SKILL_ROOT / seed_path
    seed = load_manual_seed(seed_path,
                            int(cfg["manual_seed_max_age_days"]))
    if seed:
        payload = {
            "fetched_at": now_iso, "as_of": seed.get("as_of"),
            "provenance": "manual_seed", "source_url": cfg["source_url"],
            "holdings": seed["holdings"],
            "tickers": [h["ticker"] for h in seed["holdings"]],
        }
        if seed.get("stale_warning"):
            payload["stale_warning"] = seed["stale_warning"]
        return payload

    # 3. Nothing — honest empty (never fabricated).
    return {"fetched_at": now_iso, "as_of": None,
            "provenance": "unavailable", "source_url": cfg["source_url"],
            "holdings": [], "tickers": []}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch the public Autopilot Claude Portfolio holdings")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()

    payload = fetch_claude_portfolio(args.config,
                                     force_refresh=args.force_refresh)
    n = len(payload.get("holdings") or [])
    print(f"Claude portfolio: {n} holding(s) · "
          f"provenance={payload.get('provenance')}"
          + (f" · as_of={payload.get('as_of')}" if payload.get("as_of") else ""),
          file=sys.stderr)
    if payload.get("stale_warning"):
        print(f"  WARNING: {payload['stale_warning']}", file=sys.stderr)
    text = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
