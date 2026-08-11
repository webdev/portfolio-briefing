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

try:
    from analysis import rsi_discipline
except ImportError:  # pragma: no cover - path fallback for standalone runs
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from analysis import rsi_discipline


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
    existing_short_puts: dict | None = None,
    refresh: bool = False,
    ttl_hours: int = 24,
    parallel: bool = True,
    max_workers: int = 8,
    prefer_monthly: bool = False,
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
    # Expiration policy (2026-08-10): prefer standard monthly (3rd-Friday)
    # expirations for CSP entry tickets. Driven by briefing.yaml
    # `expiration_policy.prefer_monthly` (theme_universes.yaml may override).
    verdict_cfg.setdefault("prefer_monthly_expiration", bool(prefer_monthly))

    recs_map = recs_map or {}
    held_weights = held_weights or {}
    existing_short_puts = existing_short_puts or {}

    # Build job list
    jobs: list[tuple[str, str]] = []
    for theme_key, theme_data in themes_cfg.items():
        for ticker in theme_data.get("anchors", []) or []:
            jobs.append((theme_key, ticker.upper()))

    # Parallel by default (thematic_scout.parallel / .max_workers in
    # briefing.yaml). parallel=False or max_workers<=1 → sequential loop with
    # IDENTICAL per-ticker logic — a safe fallback if yfinance ever starts
    # rate-limiting the concurrent fetches.
    use_parallel = bool(parallel) and int(max_workers) > 1 and len(jobs) > 1
    mode = f"workers={int(max_workers)}" if use_parallel else "sequential"
    print(f"  Researching {len(jobs)} thematic tickers ({mode})...")
    results_by_theme: dict[str, list[dict]] = {tk: [] for tk in themes_cfg}
    if use_parallel:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=int(max_workers),
                                thread_name_prefix="scout") as ex:
            future_to_job = {
                ex.submit(
                    scout._research_ticker, ticker, theme, verdict_cfg,
                    recs_map, held_weights, existing_short_puts
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
    else:
        for theme, ticker in jobs:
            try:
                r = scout._research_ticker(
                    ticker, theme, verdict_cfg,
                    recs_map, held_weights, existing_short_puts,
                )
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


# --------------------------------------------------------------------------
# Market-read helpers (deterministic — synthesize a "what's happening" read
# from the technicals the scout already computed: spot, rsi_14, iv_rank,
# sma_200, drawdown_pct, fivedayret_pct). No network, no LLM.
# --------------------------------------------------------------------------

def _vs_sma_pct(r: dict) -> float | None:
    sma = r.get("sma_200")
    spot = r.get("spot")
    if sma and spot:
        return (spot - sma) / sma * 100.0
    return None


def _trend_phrase(r: dict) -> str | None:
    pct = _vs_sma_pct(r)
    if pct is None:
        return None
    if pct >= 15:
        return f"well above 200-SMA (+{pct:.0f}%)"
    if pct >= 0:
        return f"above 200-SMA (+{pct:.0f}%)"
    if pct > -10:
        return f"below 200-SMA ({pct:.0f}%)"
    return f"well below 200-SMA ({pct:.0f}%)"


def _rsi_phrase(rsi: float | None) -> str | None:
    if rsi is None:
        return None
    if rsi >= 70:
        return f"RSI {rsi:.0f} overbought"
    if rsi >= 60:
        return f"RSI {rsi:.0f} strong"
    if rsi >= 45:
        return f"RSI {rsi:.0f} neutral"
    if rsi >= 35:
        return f"RSI {rsi:.0f} soft"
    return f"RSI {rsi:.0f} oversold"


def _highs_phrase(r: dict) -> str | None:
    dd = r.get("drawdown_pct")
    if dd is None:
        return None
    if dd <= 3:
        return "at/near 52w highs"
    if dd <= 10:
        return f"{dd:.0f}% off highs"
    if dd <= 25:
        return f"{dd:.0f}% pullback"
    return f"{dd:.0f}% drawdown"


def _setup_label(r: dict) -> str:
    """One-word characterization of the ticker's market posture."""
    rsi = r.get("rsi_14")
    dd = r.get("drawdown_pct")
    five = r.get("fivedayret_pct")
    vs_sma = _vs_sma_pct(r)
    # Extended: strong + stretched, near highs
    if rsi is not None and rsi >= 70 and (dd is None or dd <= 6):
        return "Extended"
    # Breaking out: moving up hard and near highs
    if five is not None and five >= 4 and (dd is not None and dd <= 8):
        return "Breaking out"
    # Oversold bounce candidate
    if rsi is not None and rsi < 35:
        return "Oversold"
    if dd is not None and dd >= 25:
        return "Deep pullback"
    if five is not None and five <= -4:
        return "Selling off"
    if rsi is not None and rsi >= 55 and (vs_sma is None or vs_sma >= 0):
        return "Trending up"
    if vs_sma is not None and vs_sma < -10:
        return "Downtrend"
    return "Range-bound"


def _market_setup_read(r: dict) -> str:
    """Synthesize 'Label: trend, RSI, position-vs-highs[, IV]' for one ticker."""
    bits = [p for p in (_trend_phrase(r), _rsi_phrase(r.get("rsi_14")), _highs_phrase(r)) if p]
    iv = r.get("iv_rank")
    if iv is not None and iv >= 60:
        bits.append(f"IV rank {iv:.0f} (rich premium)")
    body = ", ".join(bits)
    return f"{_setup_label(r)}: {body}" if body else _setup_label(r)


def _action_read(r: dict) -> str:
    """Translate a ticker's state into an entry/exit/manage verdict — NO state
    is left merely descriptive (see CLAUDE.md 'Everything actionable'). Read
    asymmetrically per the RSI discipline: overbought blocks new buys/CSPs but
    favours covered-call writing / trimming; the pullback zone favours entry;
    a falling knife warns. Generic across holding status (entry if you don't
    own it, manage/exit if you do)."""
    rsi = r.get("rsi_14")
    iv = r.get("iv_rank")
    dd = r.get("drawdown_pct")
    rich = " (rich IV)" if (iv is not None and iv >= 60) else ""

    # Thesis-broken: deep drawdown with no strength → review/exit, not a fresh entry.
    if dd is not None and dd >= 30 and (rsi is None or rsi < 45):
        return "thesis check — deep drawdown without strength; if held, review/trim, not a fresh entry"
    if rsi is None:
        return "no RSI read — confirm momentum before entering or exiting"
    if rsi >= 70:
        return f"overheated — no new buy/CSP (chasing); if held, WRITE COVERED CALLS{rich} or TRIM into strength"
    if rsi >= 60:
        return f"extended — wait for a pullback to enter; if held, covered calls attractive{rich}"
    if rsi >= 50:
        return "neutral-to-firm — no entry edge yet; hold/monitor"
    if rsi >= 35:
        return "pullback zone — favourable for a CSP/BUY entry; confirm support"
    if rsi >= 25:
        return "oversold — entry favoured but momentum weak; size small / confirm a base"
    return "falling knife (RSI <25) — wait for stabilization before entry; if held, stay defensive"


def _action_tag(rsi: float | None) -> str:
    """Compact entry/exit verb for the theme-pulse leader (concise form of
    _action_read)."""
    if rsi is None:
        return "verify"
    if rsi >= 70:
        return "calls/trim"      # overbought — write calls or trim, no new buy
    if rsi >= 60:
        return "wait"            # extended — entry on a pullback
    if rsi >= 50:
        return "monitor"
    if rsi >= 35:
        return "entry zone"      # pullback — CSP/BUY favoured
    if rsi >= 25:
        return "entry (small)"
    return "falling knife"


def _fresh_theme_meta() -> dict | None:
    """Read theme metadata (name/group/anchors/etfs) fresh from theme_universes.yaml
    so config edits show immediately. Returns None if the rules file isn't readable."""
    try:
        import yaml as _yaml
        if _SCOUT_RULES.exists():
            data = _yaml.safe_load(_SCOUT_RULES.read_text()) or {}
            return data.get("themes") or None
    except Exception:
        return None
    return None


def _live_results(results_by_theme: dict) -> list[dict]:
    """All usable results, de-duplicated by ticker.

    A ticker can anchor several themes (e.g. ARM in semis + applications, AVGO
    in semis + applications, CRWD in applications + cybersecurity). The
    cross-theme aggregate (breadth, hottest/cooling movers, RSI posture) must
    count each name once; the per-theme pulse below still uses the raw
    per-theme lists so a name correctly shows up under each of its themes.
    """
    out = []
    seen: set[str] = set()
    for results in results_by_theme.values():
        for r in results:
            if (r.get("verdict") or "").startswith("NO DATA"):
                continue
            if r.get("spot") is None:
                continue
            tk = (r.get("ticker") or "").upper()
            if tk in seen:
                continue
            seen.add(tk)
            out.append(r)
    return out


def _theme_avg_5d(results: list[dict]) -> float | None:
    vals = [r["fivedayret_pct"] for r in results
            if r.get("fivedayret_pct") is not None]
    return sum(vals) / len(vals) if vals else None


def _render_market_pulse(results_by_theme: dict, themes_meta: dict) -> list[str]:
    """Cross-theme narrative + hottest/cooling movers + per-theme pulse.

    This is the 'learn what's going on in the market' read the user asked for:
    it characterizes each ticker's own market setup (independent of holdings).
    """
    live = _live_results(results_by_theme)
    movers = [r for r in live if r.get("fivedayret_pct") is not None]
    if not movers:
        return []

    lines: list[str] = ["### 📊 Market Pulse", ""]

    # Breadth + RSI posture across the analyzed universe
    n = len(movers)
    up = sum(1 for r in movers if r["fivedayret_pct"] > 0)
    overbought = [r for r in live if (r.get("rsi_14") or 0) >= 70]
    oversold = [r for r in live if r.get("rsi_14") is not None and r["rsi_14"] < 35]

    # Per-theme average momentum → hottest / coldest theme
    theme_avgs: list[tuple[str, float]] = []
    for theme_key, results in results_by_theme.items():
        avg = _theme_avg_5d([r for r in results if r.get("spot") is not None])
        if avg is not None and results:
            name = themes_meta.get(theme_key, {}).get("name", theme_key)
            theme_avgs.append((name, avg))
    theme_avgs.sort(key=lambda t: t[1], reverse=True)

    breadth_pct = up / n * 100.0
    breadth_word = ("broadly bid" if breadth_pct >= 65 else
                    "mixed" if breadth_pct >= 40 else "broadly soft")
    narrative = (
        f"_Across {n} names: **{up}/{n} ({breadth_pct:.0f}%) green over the past week** "
        f"— breadth is {breadth_word}._"
    )
    lines.append(narrative)
    if theme_avgs:
        hot = theme_avgs[0]
        cold = theme_avgs[-1]
        lines.append(
            f"_Hottest theme: **{hot[0]}** ({hot[1]:+.1f}% avg 5d). "
            f"Coldest: **{cold[0]}** ({cold[1]:+.1f}% avg 5d)._"
        )
    lines.append(
        f"_RSI posture: {len(overbought)} overbought (>70), "
        f"{len(oversold)} oversold (<35) — "
        + ("plenty stretched, chase carefully." if len(overbought) > len(oversold)
           else "more washed-out than frothy." if len(oversold) > len(overbought)
           else "balanced.") + "_"
    )
    lines.append("")

    # Hottest names (top positive movers)
    movers.sort(key=lambda r: r["fivedayret_pct"], reverse=True)
    hottest = [r for r in movers if r["fivedayret_pct"] > 0][:8]
    if hottest:
        lines.append("**🔥 Hottest names** (5-day momentum):")
        lines.append("")
        for r in hottest:
            lines.append(
                f"- 🔥 `{r['ticker']}` · ${r['spot']:.2f} · "
                f"**{r['fivedayret_pct']:+.1f}% 5d** — {_market_setup_read(r)}"
            )
            lines.append(f"  - 🎬 **Action:** {_action_read(r)}")
        lines.append("")

    # Cooling / pulling back (most negative movers)
    cooling = [r for r in reversed(movers) if r["fivedayret_pct"] < 0][:6]
    if cooling:
        lines.append("**🧊 Cooling / pulling back:**")
        lines.append("")
        for r in cooling:
            lines.append(
                f"- 🧊 `{r['ticker']}` · ${r['spot']:.2f} · "
                f"**{r['fivedayret_pct']:+.1f}% 5d** — {_market_setup_read(r)}"
            )
            lines.append(f"  - 🎬 **Action:** {_action_read(r)}")
        lines.append("")

    # Theme-by-theme one-line pulse, grouped
    lines.append("**Theme-by-theme pulse:**")
    lines.append("")
    current_group: str | None = None
    for theme_key, results in results_by_theme.items():
        usable = [r for r in results if r.get("spot") is not None]
        if not usable:
            continue
        meta = themes_meta.get(theme_key, {})
        name = meta.get("name", theme_key)
        group = meta.get("group")
        if group and group != current_group:
            lines.append(f"_{group}_")
            current_group = group
        avg = _theme_avg_5d(usable)
        leader = max(
            (r for r in usable if r.get("fivedayret_pct") is not None),
            key=lambda r: r["fivedayret_pct"], default=None,
        )
        tag = ("running hot" if (avg is not None and avg >= 2) else
               "cooling" if (avg is not None and avg <= -2) else "mixed")
        avg_str = f"{avg:+.1f}% avg 5d" if avg is not None else "n/a"
        if leader is not None and leader.get("fivedayret_pct") is not None:
            _lret = leader["fivedayret_pct"]
            _lrsi = leader.get("rsi_14")
            if _lrsi is not None:
                lead_str = (f"leader `{leader['ticker']}` {_lret:+.1f}%, "
                            f"RSI {_lrsi:.0f} → {_action_tag(_lrsi)}")
            else:
                lead_str = f"leader `{leader['ticker']}` {_lret:+.1f}%"
        else:
            lead_str = "no clear leader"
        lines.append(f"- **{name}** — {avg_str} ({tag}); {lead_str}")
        # Constituent companies (the curated anchors) + the ETFs that cover them.
        anchors = meta.get("anchors") or []
        if anchors:
            shown = ", ".join(str(a) for a in anchors[:12])
            extra = f" +{len(anchors) - 12} more" if len(anchors) > 12 else ""
            lines.append(f"  - Companies: {shown}{extra}")
        etfs = meta.get("etfs") or []
        lines.append(
            "  - ETFs: " + (", ".join(str(e) for e in etfs) if etfs
                            else "— (no dedicated theme ETF; constituents sit in broad sector funds)")
        )
    lines.append("")

    return lines


def render_scout_section(payload: dict | None, max_per_theme: int = 4,
                         config: dict | None = None,
                         include_shortlist: bool = True) -> list[str]:
    """Render the thematic-research section for the daily briefing.

    Two parts:
      1. Market Pulse — a deterministic 'what's happening across themes' read:
         breadth, hottest/coldest theme, RSI posture, hottest & cooling movers
         (each with its own market-setup read), and a per-theme pulse line.
      2. Actionable shortlist — only BUY / CSP ENTRY picks, RSI-hook gated.

    Full detail (including AVOIDs and NEUTRAL/WATCH) lives in the standalone
    scout report at ~/Documents/briefings/scout_DATE.md.
    """
    if not payload:
        return []

    lines = [
        "## 🔭 Thematic Scout — Market Read Across Themes",
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
    if include_shortlist:
        lines.append(
            "_Full detail (including WATCH/AVOID) in "
            "`~/Documents/briefings/scout_DATE.md`. Market read first, then the "
            "actionable shortlist._"
        )
    else:
        lines.append(
            "_This is the **market read** (context, not orders): breadth, rotation, and "
            "each name's own setup. Actionable trades are in the **🎯 Candidate Trades** "
            "section below. Full per-company detail in `~/Documents/briefings/scout_DATE.md`._"
        )
    lines.append(
        "_📋 Per-company candidate research (all theme companies — RSI-gated entries "
        "+ FMP valuation): `~/Documents/briefings/candidates_DATE.md`._"
    )
    lines.append(
        "_🎯 When-to-enter triggers (every theme company → ENTRY NOW / WAIT-for-X / "
        "WATCH / AVOID, with the explicit trigger condition): "
        "`~/Documents/briefings/when_to_enter_DATE.md`._"
    )
    lines.append("")

    # Theme metadata (name / group / anchors / etfs) is CONFIG, not data — read
    # it fresh from the YAML so edits (e.g. newly-verified ETFs) show on the next
    # run without waiting for the 24h scout cache to expire. The cached payload
    # still supplies the per-ticker results.
    themes_meta = _fresh_theme_meta() or payload.get("themes", {})
    results_by_theme = payload.get("results_by_theme", {})

    # Part 1: Market Pulse — the "what's happening across the market" read.
    lines.extend(_render_market_pulse(results_by_theme, themes_meta))

    # Part 2: Actionable shortlist (RSI-hook gated). Skipped when the daily
    # briefing carries a richer Candidate Trades section instead (the standalone
    # scout report / web app still renders the shortlist via the default).
    if not include_shortlist:
        return lines
    lines.append("### 🎯 Actionable shortlist")
    lines.append("")

    actionable_prefixes = ("BUY", "CSP")

    # RSI hook: CSP entries are put-sales, BUY picks are equity buys. The hook
    # removes RSI-unfavorable picks (overbought) into a footer and promotes
    # favored ones. Standard wheel bands unless config overrides them.
    rsi_th = rsi_discipline.load_thresholds(config)
    rsi_gate_on = rsi_th.get("enabled", True)
    rsi_removed: list[dict] = []

    # Group themes by their `group:` field for visual hierarchy in the report.
    # Preserves YAML ordering within each group.
    current_group: str | None = None
    for theme_key, results in results_by_theme.items():
        if not results:
            continue
        meta = themes_meta.get(theme_key, {})
        title = meta.get("name", theme_key)
        group = meta.get("group")

        # Render group divider when entering a new group
        if group and group != current_group:
            lines.append(f"### ━━━ {group} ━━━")
            lines.append("")
            current_group = group
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
            verdict = r.get("verdict", "")
            side = "put" if verdict.startswith("CSP") else "buy"
            rv = rsi_discipline.hook(side, r.get("rsi_14"), rsi_th)
            if rsi_gate_on and rv.removed:
                rsi_removed.append({"ticker": r.get("ticker"), "verdict": verdict,
                                    "reason": rv.reason})
                continue
            emoji = "💎" if verdict.startswith("CSP") else "🟢"
            spot = r.get("spot")
            spot_str = f"${spot:.2f}" if spot else "?"
            promo = " ✅ RSI favourable" if rv.promoted else ""
            lines.append(f"**{emoji} {verdict} · `{r['ticker']}` · {spot_str}**{promo}")

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
                # Monthly/weekly kind — computed at selection time from the
                # REAL chain date (rule #19); absent when policy disabled.
                _kind_seg = f", {q['exp_kind']}" if q.get("exp_kind") else ""
                lines.append(
                    f"  - **CSP entry:** SELL 1× {r['ticker']} ${q.get('strike', 0):g}P "
                    f"exp **{exp_pretty}** ({q.get('dte', '?')} DTE{_kind_seg}) · "
                    f"mid ${q.get('mid', 0):.2f} "
                    f"(bid ${q.get('bid', 0):.2f} / ask ${q.get('ask', 0):.2f}) "
                    f"· _Source: Live E*TRADE chain_"
                )

            if r.get("rationale"):
                lines.append(f"  - _Why:_ {'; '.join(r['rationale'])}")

            lines.append("")

    if rsi_removed:
        lines.append("### ⏸ Held back by RSI")
        lines.append("")
        for c in rsi_removed:
            lines.append(f"- **{c['ticker']}** ({c['verdict']}) — {c['reason']}")
        lines.append("")

    return lines
