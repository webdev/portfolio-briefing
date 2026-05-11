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
# yfinance technical signals (same logic as snapshot_inputs._full_technicals)
# --------------------------------------------------------------------------

def _fetch_technicals(ticker: str) -> dict | None:
    """Return {iv_rank, rsi_14, sma_200, drawdown_pct, spot, fivedayret}."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        hist = t.history(period="300d")
        if hist.empty or len(hist) < 60:
            return None
        closes = hist["Close"].dropna()
        spot = float(closes.iloc[-1])

        # IV rank (252d realized-vol percentile)
        rets = closes.pct_change().dropna()
        iv_rank = None
        if len(rets) >= 21:
            rolling_vol = (rets.rolling(20).std() * math.sqrt(252)).dropna()
            if not rolling_vol.empty:
                cur = float(rolling_vol.iloc[-1])
                if not math.isnan(cur):
                    iv_rank = round(
                        float((rolling_vol <= cur).sum()) / len(rolling_vol) * 100.0, 1
                    )

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

        return {
            "iv_rank": iv_rank,
            "rsi_14": rsi_14,
            "sma_200": sma_200,
            "drawdown_pct": drawdown_pct,
            "spot": round(spot, 2),
            "fivedayret_pct": five_d,
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

def _csp_entry_quote(ticker: str, spot: float, otm_pct: float, target_dte: int) -> dict | None:
    """Look up the closest CSP strike via the canonical E*TRADE chain fetcher.

    Returns {strike, bid, mid, ask, expiration, dte} or None if E*TRADE
    is unreachable / no strike near target.
    """
    mod, cache = _load_chain_fetcher()
    if mod is None or cache is None:
        return None
    exp_date = mod.choose_expiration(
        symbol=ticker, target_dte=target_dte, tolerance_days=14, cache=cache
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
    sma_200: float | None = None
    drawdown_pct: float | None = None
    fivedayret_pct: float | None = None
    earnings_date: str | None = None
    days_to_earnings: int | None = None
    third_party_rec: str | None = None
    verdict: str = "WATCH"
    rationale: list = field(default_factory=list)
    csp_entry: dict | None = None  # if CSP_ENTRY verdict

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

    # No BUY rec — just monitor
    reasons.append("no third-party catalyst")
    return "WATCH", reasons, False


# --------------------------------------------------------------------------
# Per-ticker pipeline
# --------------------------------------------------------------------------

def _research_ticker(ticker: str, theme: str, cfg: dict,
                     recs_map: dict, held_weights: dict) -> ScoutResult:
    res = ScoutResult(ticker=ticker, theme=theme)
    tech = _fetch_technicals(ticker)
    if not tech:
        res.verdict = "NO DATA"
        res.rationale = ["yfinance returned no data"]
        return res

    res.spot = tech["spot"]
    res.rsi_14 = tech.get("rsi_14")
    res.iv_rank = tech.get("iv_rank")
    res.sma_200 = tech.get("sma_200")
    res.drawdown_pct = tech.get("drawdown_pct")
    res.fivedayret_pct = tech.get("fivedayret_pct")

    earnings = _fetch_earnings_date(ticker)
    res.earnings_date = earnings
    if earnings:
        try:
            d = date.fromisoformat(earnings)
            res.days_to_earnings = (d - date.today()).days
        except ValueError:
            pass

    res.third_party_rec = recs_map.get(ticker.upper())
    held_w = held_weights.get(ticker.upper(), 0.0)

    verdict, reasons, want_csp = _verdict(tech, earnings, res.third_party_rec, held_w, cfg)
    res.verdict = verdict
    res.rationale = reasons

    # CSP entry quote when verdict warrants
    if want_csp and res.spot:
        # Earnings guard: don't quote a CSP that spans imminent earnings
        if res.days_to_earnings is not None and 0 < res.days_to_earnings <= cfg["csp_target_dte"]:
            res.rationale.append(
                f"earnings in {res.days_to_earnings}d inside target DTE — no CSP ticket"
            )
        else:
            quote = _csp_entry_quote(
                ticker=ticker, spot=res.spot,
                otm_pct=cfg["csp_target_otm_pct"],
                target_dte=cfg["csp_target_dte"],
            )
            if quote:
                res.csp_entry = quote
            else:
                res.rationale.append("E*TRADE chain unavailable — no CSP ticket")

    return res


# --------------------------------------------------------------------------
# Recommendations + held-weight inputs (optional integration with briefing)
# --------------------------------------------------------------------------

def _load_recs_and_weights() -> tuple[dict, dict]:
    """Best-effort: pull third-party recs + held weights from the latest
    daily-portfolio-briefing snapshot. Falls back to empty dicts."""
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
                    recs[t.upper()] = str(rec).upper()
        except Exception:
            pass

    weights: dict = {}
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
                    if p.get("assetType") != "EQUITY":
                        continue
                    sym = (p.get("symbol") or "").upper()
                    qty = float(p.get("qty", 0) or 0)
                    price = float(p.get("price", 0) or 0)
                    if sym and qty > 0 and price > 0:
                        weights[sym] = weights.get(sym, 0) + (qty * price / nlv * 100)
        except Exception:
            pass
    return recs, weights


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


def _render_report(results_by_theme: dict[str, list[ScoutResult]],
                   theme_meta: dict, generated_at: str) -> str:
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
            lines.append(f"### {emoji} {r.verdict} · `{r.ticker}` · {spot_str}")

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
    recs_map, held_weights = _load_recs_and_weights()
    if recs_map:
        print(f"  Loaded {len(recs_map)} third-party recs from latest briefing snapshot")
    if held_weights:
        print(f"  Loaded {len(held_weights)} held-weight entries from latest briefing snapshot")

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
            ex.submit(_research_ticker, ticker, theme, verdict_cfg, recs_map, held_weights):
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

    # Render
    generated_at = datetime.now().strftime("%A, %B %d, %Y · %I:%M %p")
    md = _render_report(results_by_theme, themes_cfg, generated_at)

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
