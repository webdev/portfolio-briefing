"""Per-company Candidate Research report.

A standalone dated report (candidates_DATE.md) covering EVERY company across the
Scout's themes. For each company it renders a research card — RSI, trend vs
200-SMA, IV rank, drawdown, 5-day momentum, valuation (FMP DCF + analyst target),
and the Scout's verdict — and attaches a concrete actionable entry (a CSP ticket
from the live E*TRADE chain, or a BUY note) ONLY when the setup qualifies AND
passes the RSI discipline gate.

It reuses the Scout's existing research (``scout_payload["results_by_theme"]``)
so it adds no new technical fetches; the caller supplies cached fair values.
Deterministic and side-effect free.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from analysis import rsi_discipline, intrinsic_value
    from steps import thematic_research as _tr
except ImportError:  # pragma: no cover - path fallback
    from analysis import rsi_discipline, intrinsic_value
    import thematic_research as _tr  # type: ignore


_STATUS_LABEL = {
    "candidate": "🎯 CANDIDATE",
    "held_rsi": "⏸ HELD (RSI)",
    "watch": "👀 WATCH",
    "avoid": "🔴 AVOID",
}
_STATUS_ORDER = {"candidate": 0, "held_rsi": 1, "watch": 2, "avoid": 3}


def _verdict_side(verdict: str) -> str | None:
    v = (verdict or "").upper()
    if v.startswith("CSP"):
        return rsi_discipline.PUT
    if v.startswith("BUY"):
        return rsi_discipline.BUY
    return None


def _status(r: dict, rsi_th: dict):
    """Return (status, RecVerdict|None) for one research result."""
    verdict = r.get("verdict", "")
    side = _verdict_side(verdict)
    if side is None:
        return ("avoid" if verdict.upper().startswith("AVOID") else "watch"), None
    rv = rsi_discipline.hook(side, r.get("rsi_14"), rsi_th)
    return ("held_rsi" if rv.removed else "candidate"), rv


def _format_card(r: dict, fv_by_ticker: dict, etf_set, rsi_th: dict) -> list[str]:
    tk = (r.get("ticker") or "").upper()
    spot = r.get("spot")
    spot_s = f"${spot:.2f}" if spot else "?"
    status, rv = _status(r, rsi_th)
    badge = ""
    if rv and rv.promoted:
        badge = " ✅ RSI favourable"
    elif rv and rv.badge:
        badge = f" {rv.badge}"

    out = [f"**{_STATUS_LABEL[status]} · `{tk}` · {spot_s}**{badge}"]

    metrics = []
    if r.get("rsi_14") is not None:
        metrics.append(rsi_discipline.tag(r["rsi_14"]))  # side-agnostic read
    tp = _tr._trend_phrase(r)
    if tp:
        metrics.append(tp)
    if r.get("iv_rank") is not None:
        metrics.append(f"IV rank {r['iv_rank']:.0f}")
    hp = _tr._highs_phrase(r)
    if hp:
        metrics.append(hp)
    if r.get("fivedayret_pct") is not None:
        metrics.append(f"5d {r['fivedayret_pct']:+.1f}%")
    if metrics:
        out.append("  - " + " · ".join(metrics))

    # Concrete entry (or held-by-RSI note) FIRST, right under the RSI metrics
    # line — keeps the actionable ticket adjacent to its RSI read (also so the
    # RSI-coverage audit always finds an RSI within its window).
    if status == "candidate":
        q = r.get("csp_entry")
        if q:
            exp = q.get("expiration") or ""
            try:
                exp = date.fromisoformat(q["expiration"]).strftime("%a %b %d '%y")
            except (ValueError, KeyError, TypeError):
                pass
            out.append(
                f"  - **Entry (CSP):** SELL 1× {tk} ${q.get('strike', 0):g}P exp **{exp}** "
                f"({q.get('dte', '?')} DTE) · mid ${q.get('mid', 0):.2f} "
                f"(bid ${q.get('bid', 0):.2f} / ask ${q.get('ask', 0):.2f}) · _Live E*TRADE chain_"
            )
        elif (r.get("verdict") or "").upper().startswith("BUY"):
            out.append(f"  - **Entry (equity):** BUY `{tk}` on this pullback — RSI favourable; size per your plan.")
        else:
            out.append("  - _Entry: setup qualifies, but no live chain ticket available — verify before placing._")
    elif status == "held_rsi" and rv:
        out.append(f"  - _Held back by RSI: {rv.reason}_")

    note = intrinsic_value.format_fv_note(tk, spot, fv_by_ticker.get(tk), etf_set=etf_set)
    if note:
        out.append(f"  - {note}")

    if r.get("verdict"):
        rationale = "; ".join(r.get("rationale") or [])
        out.append(f"  - Verdict: {r['verdict']}" + (f" — {rationale}" if rationale else ""))

    if r.get("days_to_earnings") is not None:
        out.append(f"  - Earnings: {r.get('earnings_date')} ({r['days_to_earnings']}d away)")

    return out


def render_candidate_report(scout_payload: dict | None, *, fv_by_ticker: dict | None,
                            config: dict | None, generated_at: str) -> str:
    """Render the full per-company candidate research report as markdown."""
    if not scout_payload:
        return f"# Candidate Research — {generated_at}\n\n_No scout data available._\n"

    rsi_th = rsi_discipline.load_thresholds(config)
    etf_set = intrinsic_value.default_etf_set(config)
    fv_by_ticker = {(k or "").upper(): v for k, v in (fv_by_ticker or {}).items()}
    # Theme metadata fresh from YAML (config, not data — see thematic_research).
    themes_meta = _tr._fresh_theme_meta() or scout_payload.get("themes", {})
    results_by_theme = scout_payload.get("results_by_theme", {})

    # Tally statuses across the universe.
    tally = {"candidate": 0, "held_rsi": 0, "watch": 0, "avoid": 0}
    total = 0
    for results in results_by_theme.values():
        for r in results:
            if (r.get("verdict") or "").startswith("NO DATA") or r.get("spot") is None:
                continue
            total += 1
            tally[_status(r, rsi_th)[0]] += 1

    lines = [
        f"# Candidate Research — {generated_at}",
        "",
        "_Per-company research across every Scout theme. Each company gets a card "
        "(RSI · trend · IV rank · drawdown · 5-day · valuation · verdict); a concrete "
        "entry is attached only when the setup qualifies AND passes the RSI gate. "
        "Chain tickets are live E*TRADE; valuation is FMP DCF + analyst target "
        "(single stocks only). Not advice — verify before placing._",
        "",
        f"**{total} companies analyzed:** {tally['candidate']} 🎯 CANDIDATE · "
        f"{tally['held_rsi']} ⏸ held by RSI · {tally['watch']} 👀 WATCH · "
        f"{tally['avoid']} 🔴 AVOID",
        "",
    ]

    current_group = None
    for theme_key, results in results_by_theme.items():
        usable = [r for r in results
                  if not (r.get("verdict") or "").startswith("NO DATA") and r.get("spot") is not None]
        if not usable:
            continue
        meta = themes_meta.get(theme_key, {})
        group = meta.get("group")
        if group and group != current_group:
            lines.append(f"# ━━━ {group} ━━━")
            lines.append("")
            current_group = group
        lines.append(f"## 🔭 {meta.get('name', theme_key)}")
        anchors = meta.get("anchors") or []
        etfs = meta.get("etfs") or []
        lines.append(
            f"_Companies: {', '.join(str(a) for a in anchors)} · "
            f"ETFs: {', '.join(str(e) for e in etfs) if etfs else '— no dedicated theme ETF'}_"
        )
        lines.append("")
        usable.sort(key=lambda r: (_STATUS_ORDER.get(_status(r, rsi_th)[0], 9), r.get("ticker", "")))
        for r in usable:
            lines.extend(_format_card(r, fv_by_ticker, etf_set, rsi_th))
            lines.append("")

    return "\n".join(lines)


# A proposed CSP strike within this fraction of a strike the user already holds
# is treated as "the same trade" (duplicate), not a fresh candidate.
_STRIKE_OVERLAP_PCT = 0.05


def short_puts_by_ticker(positions: list | None) -> dict:
    """Tally the user's OPEN short puts from snapshot positions.

    Shape (matches the scout's put-stack guard): {TICKER: {"count": int,
    "strikes": [float]}}. Only short (qty < 0) PUT options are counted.
    """
    out: dict = {}
    for p in positions or []:
        if (p.get("assetType") or "").upper() != "OPTION":
            continue
        if (p.get("type") or "").upper() != "PUT":
            continue
        try:
            qty = float(p.get("qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty >= 0:  # long puts aren't a stacking concern for selling more
            continue
        tk = (p.get("underlying") or "").upper()
        if not tk:
            continue
        try:
            strike = float(p.get("strike", 0) or 0)
        except (TypeError, ValueError):
            strike = 0.0
        entry = out.setdefault(tk, {"count": 0, "strikes": []})
        entry["count"] += int(abs(qty)) or 1
        if strike > 0:
            entry["strikes"].append(strike)
    return out


def long_puts_by_ticker(positions: list | None) -> dict:
    """Tally the user's OPEN LONG puts (protective puts / collar floors).

    Shape mirrors short_puts_by_ticker. A long put at/near a proposed short-put
    strike means recommending the short would *cancel* the protection — that
    must be a hard block on any new-short-put surface (the META bug, where the
    user held a long $570P as a collar floor and the LT_CSP recommended selling
    a short $570P at the same strike)."""
    out: dict = {}
    for p in positions or []:
        if (p.get("assetType") or "").upper() != "OPTION":
            continue
        if (p.get("type") or "").upper() != "PUT":
            continue
        try:
            qty = float(p.get("qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty <= 0:  # we want LONG puts only
            continue
        tk = (p.get("underlying") or "").upper()
        if not tk:
            continue
        try:
            strike = float(p.get("strike", 0) or 0)
        except (TypeError, ValueError):
            strike = 0.0
        entry = out.setdefault(tk, {"count": 0, "strikes": []})
        entry["count"] += int(abs(qty)) or 1
        if strike > 0:
            entry["strikes"].append(strike)
    return out


def _held_put_overlap(ticker: str, strike, existing_short_puts: dict | None):
    """Return None if the user holds no short puts on ``ticker``; else a dict
    {count, strikes, dupe} where ``dupe`` is True when ``strike`` is within
    _STRIKE_OVERLAP_PCT of a held strike (i.e. effectively the same contract)."""
    if not existing_short_puts:
        return None
    e = existing_short_puts.get((ticker or "").upper())
    if not e or not e.get("count"):
        return None
    strikes = e.get("strikes", []) or []
    dupe = False
    if strike:
        dupe = any(s > 0 and abs(float(strike) - s) / s <= _STRIKE_OVERLAP_PCT for s in strikes)
    return {"count": int(e.get("count", 0)), "strikes": sorted(strikes), "dupe": dupe}


def _long_put_cancellation(ticker: str, strike, existing_long_puts: dict | None):
    """Return the held LONG-put strike that would be CANCELLED by selling a
    short put at ``strike`` (within _STRIKE_OVERLAP_PCT), or None if no held
    long put on this name overlaps. A non-None return is a hard reason to
    refuse the recommendation — selling the same-strike short un-hedges the
    protective long put."""
    if not existing_long_puts or not strike:
        return None
    e = existing_long_puts.get((ticker or "").upper())
    if not e or not e.get("strikes"):
        return None
    for held in e["strikes"]:
        if held > 0 and abs(float(strike) - held) / held <= _STRIKE_OVERLAP_PCT:
            return {"count": int(e.get("count", 0)), "cancels_strike": held,
                    "strikes": sorted(e["strikes"])}
    return None


def render_candidate_briefing(scout_payload: dict | None, *, fv_by_ticker: dict | None,
                              config: dict | None, generated_at: str,
                              as_section: bool = False,
                              existing_short_puts: dict | None = None,
                              existing_long_puts: dict | None = None) -> str:
    """Focused, action-first briefing built FROM the candidates: only the names
    whose setup qualifies AND passes the RSI gate (full entry cards), with the
    RSI-blocked names listed below as 'on deck'. Condensed market context up top.

    ``as_section=True`` demotes the headings one level (## / ###) so it embeds
    cleanly inside the daily briefing rather than standing alone (# / ##).

    ``existing_short_puts`` ({TICKER: {count, strikes}}, from
    ``short_puts_by_ticker``) makes the candidate list position-aware: a CSP
    candidate whose strike duplicates a put the user already holds (within
    _STRIKE_OVERLAP_PCT) is pulled out of the actionable list into an "already
    positioned" note — you can't "newly" sell a contract you're already short.
    A same-name candidate at a different strike is kept but annotated as
    stacking single-name risk."""
    _h_top = "## 🎯 Candidate Trades — Across Themes" if as_section else f"# Candidate Trade Briefing — {generated_at}"
    _h_sub = "###" if as_section else "##"
    if not scout_payload:
        return f"{_h_top}\n\n_No scout data available._\n"

    rsi_th = rsi_discipline.load_thresholds(config)
    etf_set = intrinsic_value.default_etf_set(config)
    fv_by_ticker = {(k or "").upper(): v for k, v in (fv_by_ticker or {}).items()}
    themes_meta = _tr._fresh_theme_meta() or scout_payload.get("themes", {})
    rbt = scout_payload.get("results_by_theme", {})

    # A ticker can anchor several themes (e.g. QCOM in Semis + Applications) but
    # it's one trade — de-dup the flat briefing by ticker (the full per-theme
    # report keeps it under each theme).
    cands: list[tuple[str, dict]] = []
    held: list[tuple[str, dict, object]] = []
    already_open: list[tuple[str, dict, dict]] = []  # candidate duplicates a held put
    seen: set[str] = set()
    for theme_key, results in rbt.items():
        tname = themes_meta.get(theme_key, {}).get("name", theme_key)
        for r in results:
            if (r.get("verdict") or "").startswith("NO DATA") or r.get("spot") is None:
                continue
            tk = (r.get("ticker") or "").upper()
            if tk in seen:
                continue
            status, rv = _status(r, rsi_th)
            if status == "candidate":
                seen.add(tk)
                # Position-aware: if this CSP candidate duplicates a put the
                # user already holds, it's not a new trade — pull it out.
                q = r.get("csp_entry") or {}
                # CRITICAL: long-put cancellation check first — a short put at
                # the same strike as a held LONG put cancels protection (the
                # META collar-floor case). That's a hard refuse, not a "stack."
                cancel = _long_put_cancellation(tk, q.get("strike"), existing_long_puts) if q else None
                overlap = _held_put_overlap(tk, q.get("strike"), existing_short_puts) if q else None
                if cancel:
                    # Tag the result with the cancellation context so the
                    # "Already positioned" footer can explain it precisely.
                    r = {**r, "_cancels_long_put": cancel}
                    already_open.append((tname, r, {"count": cancel["count"],
                                                    "strikes": cancel["strikes"],
                                                    "dupe": True,
                                                    "cancels_protection": True,
                                                    "cancels_strike": cancel["cancels_strike"]}))
                elif overlap and overlap["dupe"]:
                    already_open.append((tname, r, overlap))
                else:
                    cands.append((tname, r))
            elif status == "held_rsi":
                seen.add(tk)
                held.append((tname, r, rv))

    lines = [
        _h_top,
        "",
        "_Action-first: trade candidates pulled from the per-company Scout research — "
        "names where the setup qualifies AND passes the RSI gate. Each carries a live "
        "entry, RSI, valuation, and rationale. Not advice; verify before placing._",
        "",
    ]

    live = _tr._live_results(rbt)
    movers = [r for r in live if r.get("fivedayret_pct") is not None]
    if movers:
        n = len(movers)
        up = sum(1 for r in movers if r["fivedayret_pct"] > 0)
        ob = sum(1 for r in live if (r.get("rsi_14") or 0) >= 70)
        osold = sum(1 for r in live if r.get("rsi_14") is not None and r["rsi_14"] < 35)
        lines.append(
            f"**Market context:** {up}/{n} green over the past week · "
            f"{ob} overbought (>70) / {osold} oversold (<35) across {n} analyzed names."
        )
        lines.append("")

    if cands:
        lines.append(f"{_h_sub} 🎯 Today's Candidates ({len(cands)})")
        lines.append("")
        for tname, r in sorted(cands, key=lambda x: (x[0], x[1].get("ticker", ""))):
            card = _format_card(r, fv_by_ticker, etf_set, rsi_th)
            card[0] = f"{card[0]}  · _{tname}_"
            # Same-name (different-strike) stacking note — kept, but flagged.
            tk = (r.get("ticker") or "").upper()
            q = r.get("csp_entry") or {}
            ov = _held_put_overlap(tk, q.get("strike"), existing_short_puts) if q else None
            if ov:
                strikes_s = ", ".join(f"${s:g}" for s in ov["strikes"])
                card.append(
                    f"  - ⚠ **You already hold {ov['count']}× {tk} PUT** at {strikes_s} — "
                    f"this would stack single-name assignment risk; size accordingly or skip."
                )
            lines.extend(card)
            lines.append("")
    else:
        lines.append(f"{_h_sub} 🎯 Today's Candidates (0)")
        lines.append("")
        lines.append("_No RSI-favorable candidates right now — the universe is broadly extended. "
                     "The On-Deck names below would activate on a pullback into the favorable RSI zone._")
        lines.append("")

    if held:
        lines.append(f"{_h_sub} ⏸ On Deck — qualifying setup, blocked only by RSI ({len(held)})")
        lines.append("")
        for tname, r, rv in sorted(held, key=lambda x: (x[0], x[1].get("ticker", ""))):
            tk = (r.get("ticker") or "").upper()
            lines.append(
                f"- **`{tk}`** ({tname}) — {r.get('verdict', '')} · "
                f"{rsi_discipline.tag(r.get('rsi_14'))} — _{rv.reason}_"
            )
        lines.append("")

    if already_open:
        lines.append(f"{_h_sub} ⏸ Already positioned — you hold this put ({len(already_open)})")
        lines.append("_Suppressed from the actionable list: the proposed strike matches a put "
                     "you're already short, OR — worse — would cancel a protective long put._")
        lines.append("")
        for tname, r, ov in sorted(already_open, key=lambda x: (x[0], x[1].get("ticker", ""))):
            tk = (r.get("ticker") or "").upper()
            q = r.get("csp_entry") or {}
            strikes_s = ", ".join(f"${s:g}" for s in ov["strikes"])
            if ov.get("cancels_protection"):
                lines.append(
                    f"- 🛡️ **`{tk}`** ({tname}) — scout floated SELL ${q.get('strike', 0):g}P, "
                    f"but you hold a **LONG ${ov['cancels_strike']:g}P** on {tk} as downside "
                    f"protection (collar floor / protective put). Selling a short at the same "
                    f"strike would **cancel that hedge** — don't un-collar a hedged position to "
                    f"collect premium."
                )
            else:
                lines.append(
                    f"- **`{tk}`** ({tname}) — scout floated ${q.get('strike', 0):g}P; "
                    f"you already hold {ov['count']}× {tk} PUT at {strikes_s}. Manage the existing "
                    f"position (see Watch), don't re-open it."
                )
        lines.append("")

    lines.append("_Full per-company detail (WATCH/AVOID + every theme) is in the companion "
                 "candidate research report._")
    return "\n".join(lines)


def single_stock_tickers(scout_payload: dict | None, config: dict | None) -> list[str]:
    """All single-stock tickers in the scout universe (ETFs excluded) — the set
    the caller should fetch fair values for."""
    if not scout_payload:
        return []
    etf_set = intrinsic_value.default_etf_set(config)
    out: set[str] = set()
    for results in (scout_payload.get("results_by_theme") or {}).values():
        for r in results:
            tk = (r.get("ticker") or "").upper()
            if tk and not intrinsic_value.is_etf(tk, etf_set):
                out.add(tk)
    return sorted(out)


def briefing_candidate_tickers(scout_payload: dict | None, config: dict | None) -> list[str]:
    """The small single-stock set that appears in the daily briefing's candidate
    section (actionable candidates + on-deck) — worth pricing inline."""
    if not scout_payload:
        return []
    rsi_th = rsi_discipline.load_thresholds(config)
    etf_set = intrinsic_value.default_etf_set(config)
    out: list[str] = []
    seen: set[str] = set()
    for results in (scout_payload.get("results_by_theme") or {}).values():
        for r in results:
            if (r.get("verdict") or "").startswith("NO DATA") or r.get("spot") is None:
                continue
            tk = (r.get("ticker") or "").upper()
            if not tk or tk in seen or intrinsic_value.is_etf(tk, etf_set):
                continue
            if _status(r, rsi_th)[0] in ("candidate", "held_rsi"):
                seen.add(tk)
                out.append(tk)
    return out


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _skill_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    import argparse
    import json
    import os
    from datetime import datetime
    try:
        import yaml
    except Exception:
        yaml = None

    ap = argparse.ArgumentParser(
        description="Per-company candidate research across all Scout themes "
                    "(research card + RSI-gated entry + FMP valuation).")
    ap.add_argument("--refresh-scout", action="store_true",
                    help="Re-run the thematic scout now (else use the 24h scout cache).")
    ap.add_argument("--briefing", action="store_true",
                    help="Render the focused Candidate Trade Briefing (actionable candidates "
                         "+ on-deck) instead of the full per-company report.")
    ap.add_argument("--output", default=None,
                    help="Output path (default: ~/Documents/briefings/candidates[_briefing]_DATE.md).")
    ap.add_argument("--config", default=None,
                    help="Path to briefing.yaml (RSI bands + ETF list).")
    args = ap.parse_args()

    # Load .env (FMP_API_KEY etc.) the same way the briefing does, so the
    # standalone CLI picks up the key without the user exporting it.
    try:
        from etrade_auth import _load_dotenv_if_present  # type: ignore
        _load_dotenv_if_present()
    except Exception:
        pass

    skill = _skill_dir()
    snap_root = skill / "state" / "briefing_snapshots"

    config: dict = {}
    cfg_path = Path(args.config) if args.config else (skill / "config" / "briefing.yaml")
    if yaml and cfg_path.exists():
        try:
            config = yaml.safe_load(cfg_path.read_text()) or {}
        except Exception:
            config = {}

    # Scout payload — fresh research or the 24h cache.
    payload = None
    cache_file = snap_root / "scout_cache.json"
    if args.refresh_scout:
        dated = sorted([p for p in snap_root.glob("20*") if p.is_dir()]) if snap_root.exists() else []
        snap_dir = dated[-1] if dated else (snap_root / datetime.now().strftime("%Y-%m-%d"))
        scout_mod = _tr._load_scout_module()
        recs, weights, puts = {}, {}, {}
        if scout_mod and hasattr(scout_mod, "_load_recs_and_weights"):
            try:
                recs, weights, puts = scout_mod._load_recs_and_weights()
            except Exception:
                recs, weights, puts = {}, {}, {}
        print("Refreshing thematic scout (researching all theme tickers — ~30-60s)...",
              file=sys.stderr)
        payload = _tr.run_thematic_research(
            snapshot_dir=snap_dir, recs_map=recs, held_weights=weights,
            existing_short_puts=puts, refresh=True,
        )
    elif cache_file.exists():
        try:
            payload = json.loads(cache_file.read_text())
        except Exception:
            payload = None

    if not payload:
        print("No scout data found. Run the daily briefing once (it populates the "
              "scout cache), or pass --refresh-scout to research now.", file=sys.stderr)
        return 1

    fv: dict = {}
    fmp = os.getenv("FMP_API_KEY")
    tickers = single_stock_tickers(payload, config)
    if tickers and fmp:
        print(f"Fetching FMP valuation for {len(tickers)} companies (cached 24h)...",
              file=sys.stderr)
        fv = intrinsic_value.get_fair_values(
            tickers, cache_path=snap_root / "intrinsic_value_cache.json", api_key=fmp)
    elif not fmp:
        print("(FMP_API_KEY not set — valuations will show n/a.)", file=sys.stderr)

    _gen_at = datetime.now().strftime("%A, %B %d, %Y · %I:%M %p")
    render = render_candidate_briefing if args.briefing else render_candidate_report
    md = render(payload, fv_by_ticker=fv, config=config, generated_at=_gen_at)

    _stem = "candidate_briefing" if args.briefing else "candidates"
    if args.output:
        out = Path(args.output).expanduser()
    else:
        deliv = Path(os.getenv("PORTFOLIO_BRIEFING_DELIVERY_DIR",
                               str(Path.home() / "Documents" / "briefings"))).expanduser()
        out = deliv / f"{_stem}_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"Candidate research written to: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
