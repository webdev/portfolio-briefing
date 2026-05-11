"""
Step 6.6: Thematic research (Wave 26)

Runs the thematic-scout skill across all configured themes and embeds the
top picks (BUY / CSP ENTRY) into the daily briefing. Caches the full result
to state/scout_cache.json so we don't repeat the 30-60s research every
morning — by default the cache is valid for 24 hours.

Pass --refresh-scout to run_briefing.py to force a fresh fetch.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCOUT_SCRIPT = _REPO_ROOT / "skills" / "thematic-scout" / "scripts" / "scout.py"
_SCOUT_RULES = _REPO_ROOT / "skills" / "thematic-scout" / "references" / "theme_universes.yaml"


def _load_scout_module():
    if not _SCOUT_SCRIPT.exists():
        return None
    spec = importlib.util.spec_from_file_location("thematic_scout", _SCOUT_SCRIPT)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["thematic_scout"] = mod
    spec.loader.exec_module(mod)
    return mod


def _result_to_dict(r) -> dict:
    """Coerce a ScoutResult dataclass instance to a JSON-safe dict."""
    if hasattr(r, "to_dict"):
        return r.to_dict()
    if hasattr(r, "__dict__"):
        return asdict(r) if hasattr(r, "__dataclass_fields__") else dict(r.__dict__)
    return dict(r) if isinstance(r, dict) else {}


def _cache_path(snapshot_dir: Path) -> Path:
    return snapshot_dir.parent / "scout_cache.json"


def _is_cache_fresh(cache_file: Path, ttl_hours: int = 24) -> bool:
    if not cache_file.exists():
        return False
    try:
        data = json.loads(cache_file.read_text())
        ts = datetime.fromisoformat(data.get("generated_at_iso", ""))
        return (datetime.now() - ts) < timedelta(hours=ttl_hours)
    except Exception:
        return False


def run_thematic_research(
    snapshot_dir: Path,
    recs_map: dict | None = None,
    held_weights: dict | None = None,
    refresh: bool = False,
    ttl_hours: int = 24,
) -> dict | None:
    """Run the scout and return the results dict. Cache to disk for ttl_hours.

    Returns:
        {
          "generated_at_iso": str,
          "themes": dict[str, dict],     # theme metadata
          "results_by_theme": dict[str, list[dict]],
          "summary": {"buys": int, "csps": int, "avoids": int, "total": int},
        }
    """
    cache_file = _cache_path(snapshot_dir)

    if not refresh and _is_cache_fresh(cache_file, ttl_hours=ttl_hours):
        try:
            return json.loads(cache_file.read_text())
        except Exception:
            pass

    scout = _load_scout_module()
    if scout is None:
        print("  [warn] thematic-scout module not loadable; skipping", file=sys.stderr)
        return None

    import yaml as _yaml
    if not _SCOUT_RULES.exists():
        print("  [warn] scout theme_universes.yaml missing", file=sys.stderr)
        return None
    rules = _yaml.safe_load(_SCOUT_RULES.read_text())
    themes_cfg = rules.get("themes", {}) or {}
    verdict_cfg = rules.get("verdict", {}) or {}
    # Defaults
    verdict_cfg.setdefault("rsi_oversold", 35)
    verdict_cfg.setdefault("rsi_overheated", 75)
    verdict_cfg.setdefault("drawdown_oversold_pct", 10)
    verdict_cfg.setdefault("drawdown_thesis_broken_pct", 30)
    verdict_cfg.setdefault("sma_within_pct", 5)
    verdict_cfg.setdefault("csp_target_otm_pct", 10)
    verdict_cfg.setdefault("csp_target_dte", 35)
    verdict_cfg.setdefault("iv_rank_elevated", 50)

    recs_map = recs_map or {}
    held_weights = held_weights or {}

    # Build job list
    jobs: list[tuple[str, str]] = []
    for theme_key, theme_data in themes_cfg.items():
        for ticker in theme_data.get("anchors", []) or []:
            jobs.append((theme_key, ticker.upper()))

    print(f"  Researching {len(jobs)} thematic tickers...")
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results_by_theme: dict[str, list[dict]] = {tk: [] for tk in themes_cfg}
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="scout") as ex:
        future_to_job = {
            ex.submit(
                scout._research_ticker, ticker, theme, verdict_cfg, recs_map, held_weights
            ): (theme, ticker)
            for (theme, ticker) in jobs
        }
        for fut in as_completed(future_to_job):
            theme, ticker = future_to_job[fut]
            try:
                r = fut.result(timeout=90)
            except Exception as e:
                print(f"    [warn] scout {ticker} ({theme}): {e}", file=sys.stderr)
                continue
            results_by_theme[theme].append(_result_to_dict(r))

    # Summary
    all_results = [r for rs in results_by_theme.values() for r in rs]
    buys = sum(1 for r in all_results if r.get("verdict", "").startswith("BUY"))
    csps = sum(1 for r in all_results if r.get("verdict", "").startswith("CSP"))
    avoids = sum(1 for r in all_results if r.get("verdict", "").startswith("AVOID"))

    payload = {
        "generated_at_iso": datetime.now().isoformat(),
        "themes": themes_cfg,
        "results_by_theme": results_by_theme,
        "summary": {"buys": buys, "csps": csps, "avoids": avoids, "total": len(all_results)},
    }

    # Cache
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(payload, indent=2, default=str))
    except Exception as e:
        print(f"  [warn] scout cache write failed: {e}", file=sys.stderr)

    print(
        f"  Scout complete: {buys} BUY · {csps} CSP ENTRY · "
        f"{avoids} AVOID · {len(all_results)} analyzed"
    )
    return payload


def render_scout_section(payload: dict | None, max_per_theme: int = 4) -> list[str]:
    """Render the thematic-research section for the daily briefing.

    Compact view — only BUY / CSP ENTRY picks are shown by default. Full
    detail (including AVOIDs and NEUTRAL/WATCH) lives in the standalone
    scout report at ~/Documents/briefings/scout_DATE.md.
    """
    if not payload:
        return []

    lines = [
        "## 🔭 Thematic Scout — Watchlist Across Themes",
        "",
    ]
    summary = payload.get("summary") or {}
    gen = payload.get("generated_at_iso", "")
    try:
        when = datetime.fromisoformat(gen).strftime("%a %b %d %H:%M")
    except Exception:
        when = gen
    lines.append(
        f"_Refreshed {when}. "
        f"{summary.get('buys', 0)} BUY · "
        f"{summary.get('csps', 0)} CSP ENTRY · "
        f"{summary.get('avoids', 0)} AVOID · "
        f"{summary.get('total', 0)} analyzed across "
        f"{len(payload.get('results_by_theme', {}))} themes._"
    )
    lines.append("")
    lines.append(
        "_Full detail (including WATCH/AVOID) in "
        "`~/Documents/briefings/scout_DATE.md`. Below is the actionable shortlist._"
    )
    lines.append("")

    themes_meta = payload.get("themes", {})
    results_by_theme = payload.get("results_by_theme", {})

    actionable_prefixes = ("BUY", "CSP")

    for theme_key, results in results_by_theme.items():
        if not results:
            continue
        meta = themes_meta.get(theme_key, {})
        title = meta.get("name", theme_key)
        # Only actionable picks
        actionable = [
            r for r in results
            if any(r.get("verdict", "").startswith(p) for p in actionable_prefixes)
        ]
        if not actionable:
            continue
        # Sort by verdict priority then ticker
        order = {"BUY (oversold pullback)": 1, "BUY (pullback)": 2,
                 "BUY (support test)": 3, "CSP ENTRY (fat premium)": 4}
        actionable.sort(key=lambda r: (order.get(r.get("verdict", ""), 9), r.get("ticker", "")))

        lines.append(f"### 🔭 {title}")
        lines.append("")

        for r in actionable[:max_per_theme]:
            emoji = "💎" if r.get("verdict", "").startswith("CSP") else "🟢"
            spot = r.get("spot")
            spot_str = f"${spot:.2f}" if spot else "?"
            lines.append(f"**{emoji} {r['verdict']} · `{r['ticker']}` · {spot_str}**")

            metrics = []
            if r.get("rsi_14") is not None:
                metrics.append(f"RSI {r['rsi_14']:.0f}")
            if r.get("iv_rank") is not None:
                metrics.append(f"IV rank {r['iv_rank']:.0f}")
            if r.get("drawdown_pct") is not None:
                metrics.append(f"drawdown {r['drawdown_pct']:.0f}%")
            if r.get("third_party_rec"):
                metrics.append(f"rec **{r['third_party_rec']}**")
            if metrics:
                lines.append(f"  - {' · '.join(metrics)}")

            if r.get("csp_entry"):
                q = r["csp_entry"]
                # Format the expiration as a real date (Fri Jun 12 '26) — same
                # convention as the rest of the briefing.
                exp_pretty = q.get("expiration") or ""
                try:
                    from datetime import date as _date
                    exp_pretty = _date.fromisoformat(q["expiration"]).strftime("%a %b %d '%y")
                except (ValueError, KeyError, TypeError):
                    pass
                lines.append(
                    f"  - **CSP entry:** SELL 1× {r['ticker']} ${q.get('strike', 0):g}P "
                    f"exp **{exp_pretty}** ({q.get('dte', '?')} DTE) · "
                    f"mid ${q.get('mid', 0):.2f} "
                    f"(bid ${q.get('bid', 0):.2f} / ask ${q.get('ask', 0):.2f}) "
                    f"· _Source: Live E*TRADE chain_"
                )

            if r.get("rationale"):
                lines.append(f"  - _Why:_ {'; '.join(r['rationale'])}")

            lines.append("")

    return lines
