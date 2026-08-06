"""Put credit spreads — reference-first pilot (task #42).

Defined-risk income variants for names George trades for premium only
(Tier C). A put credit spread consumes ~(width − credit) of buying power
instead of the full cash-secured collateral, which is exactly the
coverage-gate constraint the capacity gates keep hitting.

Discipline:
  - The SHORT leg is the CSP's own strike — the spread variant is only
    composed for a CSP candidate that ALREADY passed the full gate battery
    (RSI band, chase, earnings, stacking, yield floor). This module never
    originates a strike.
  - The LONG leg is the nearest liquid strike ≈ ``spread.width_target``
    below the short strike (default $20, or 10% of the short strike for
    high-priced names), quoted from the SAME snapshot chain. Zero-bid or
    missing long-leg quotes → skip with reason (hard rule #19 — never a
    fabricated quote).
  - Quality floor: ``credit_pct_of_width`` must be ≥
    ``spread.min_credit_pct_of_width`` (default 0.15) or the spread is
    skipped with the measured value shown.
  - Tier A/B names are excluded — spreads are for income names; conviction
    names keep the assignment path.
  - ``spread.mode: reference`` (default) renders a paper-watch card
    side-by-side with the CSP and logs entries to
    ``state/spread_paper_ledger.json``; ``mode: live`` renders actionable
    two-leg tickets (built, not default).
  - Reference mode does NOT change any gate — coverage math is surfaced
    as an informational line in the Money Plan blocked-money bullet.

Fail-open everywhere: any error → no section, briefing ships.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

DEFAULT_WIDTH_TARGET = 20.0
DEFAULT_HIGH_PRICE_WIDTH_PCT = 0.10   # names where 10% of strike > width_target
DEFAULT_LOW_PRICE_WIDTH_CAP_PCT = 0.30  # width never exceeds 30% of the strike
DEFAULT_MIN_CREDIT_PCT_OF_WIDTH = 0.15
_CHAIN_EXP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_EXP_SNAP_DAYS = 10               # chain expiration may differ this much


def load_spread_config(config: dict | None) -> dict:
    cfg = (config or {}).get("spread") if isinstance(config, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "mode": str(cfg.get("mode", "reference")).lower(),
        "width_target": _f(cfg.get("width_target"), DEFAULT_WIDTH_TARGET),
        "high_price_width_pct": _f(cfg.get("high_price_width_pct"),
                                   DEFAULT_HIGH_PRICE_WIDTH_PCT),
        "low_price_width_cap_pct": _f(cfg.get("low_price_width_cap_pct"),
                                      DEFAULT_LOW_PRICE_WIDTH_CAP_PCT),
        "min_credit_pct_of_width": _f(cfg.get("min_credit_pct_of_width"),
                                      DEFAULT_MIN_CREDIT_PCT_OF_WIDTH),
    }


def _f(v, default=0.0):
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def target_width(short_strike: float, cfg: dict) -> float:
    """$20 default; 10% of the short strike for high-priced names (whichever
    is larger) — a $950 strike gets a ~$95 width, not a $20 sliver. Low-
    priced sanity cap: never wider than 30% of the strike — a $20 width on
    a $17 strike isn't defined-risk income, it's nearly the whole CSP."""
    base = _f(cfg.get("width_target"), DEFAULT_WIDTH_TARGET)
    if short_strike <= 0:
        return base
    pct = _f(cfg.get("high_price_width_pct"), DEFAULT_HIGH_PRICE_WIDTH_PCT)
    cap_pct = _f(cfg.get("low_price_width_cap_pct"),
                 DEFAULT_LOW_PRICE_WIDTH_CAP_PCT)
    width = max(base, short_strike * pct)
    return min(width, short_strike * cap_pct)


def _days_between(iso_a: str, iso_b: str) -> int | None:
    try:
        a = datetime.strptime(str(iso_a)[:10], "%Y-%m-%d").date()
        b = datetime.strptime(str(iso_b)[:10], "%Y-%m-%d").date()
        return (b - a).days
    except (ValueError, TypeError):
        return None


def _chain_for(ticker: str, expiration: str, chains: dict) -> dict | None:
    """Find the snapshot chain for (ticker, expiration): exact key first,
    then the nearest same-ticker expiration within ±10 days."""
    if not chains or not ticker:
        return None
    key = f"{ticker}_{expiration}"
    ch = chains.get(key)
    if isinstance(ch, dict):
        return ch
    best: tuple[int, dict] | None = None
    prefix = f"{ticker}_"
    for k, c in chains.items():
        if not isinstance(c, dict) or not str(k).startswith(prefix):
            continue
        exp = str(k)[len(prefix):]
        if not _CHAIN_EXP_RE.match(exp):
            continue
        d = _days_between(expiration, exp)
        if d is None or abs(d) > _MAX_EXP_SNAP_DAYS:
            continue
        if best is None or abs(d) < best[0]:
            best = (abs(d), c)
    return best[1] if best else None


def _row_mid(row: dict) -> float | None:
    """Mid from a chain row. Zero/missing bid → None (zero-bid skip —
    a spread priced against a no-bid long leg is fiction)."""
    bid = _f(row.get("bid"), 0.0)
    ask = _f(row.get("ask"), 0.0)
    if bid <= 0:
        return None
    if ask > 0 and ask >= bid:
        return (bid + ask) / 2.0
    last = _f(row.get("lastPrice") or row.get("last"), 0.0)
    return last if last > 0 else bid


def _put_rows(chain: dict) -> list[dict]:
    rows = chain.get("puts") or chain.get("put") or []
    return [r for r in rows if isinstance(r, dict) and _f(r.get("strike")) > 0]


def compose_spread(idea: dict, chains: dict, config: dict | None = None,
                   today: date | None = None) -> dict:
    """Compose the put-credit-spread variant of a qualified CSP candidate.

    ``idea`` is a concrete CSP dict (new_ideas / concrete_trade shape):
    ticker, strike, expiration (ISO), dte, mid, premium, collateral,
    annualized_pct. Returns a dict with status "ok" (full spread math) or
    "skipped" (reason shown — hard rule #24, never silent)."""
    cfg = load_spread_config(config)
    ticker = str(idea.get("ticker") or "").upper()
    short_strike = _f(idea.get("strike"))
    expiration = str(idea.get("expiration") or "")[:10]

    def _skip(reason: str) -> dict:
        return {"status": "skipped", "ticker": ticker,
                "short_strike": short_strike, "expiration": expiration,
                "reason": reason}

    if not ticker or short_strike <= 0 or not expiration:
        return _skip("candidate missing ticker/strike/expiration")

    chain = _chain_for(ticker, expiration, chains or {})
    if chain is None:
        return _skip("no snapshot chain for this expiration (±10d)")
    chain_exp = str(chain.get("expiration") or expiration)[:10]
    rows = _put_rows(chain)
    if not rows:
        return _skip("snapshot chain has no put rows")

    # Short-leg mid. Same-expiration chain → trust the candidate's own
    # (E*TRADE) mid; a snapped chain must requote BOTH legs from the same
    # chain so the credit is internally consistent.
    requoted = chain_exp != expiration
    if requoted:
        short_row = min(rows, key=lambda r: abs(_f(r.get("strike")) - short_strike))
        if abs(_f(short_row.get("strike")) - short_strike) > 0.02 * short_strike:
            return _skip(f"short strike ${short_strike:g} not on the "
                         f"{chain_exp} snapshot chain")
        short_strike = _f(short_row.get("strike"))
        short_mid = _row_mid(short_row)
        if short_mid is None:
            return _skip("short leg zero-bid on the snapshot chain")
    else:
        short_mid = _f(idea.get("mid"))
        if short_mid <= 0:
            return _skip("candidate carries no short-leg mid")

    # Long leg: nearest liquid strike ≈ width_target below the short strike.
    width = target_width(short_strike, cfg)
    target_long = short_strike - width
    candidates = [r for r in rows if _f(r.get("strike")) < short_strike]
    if not candidates:
        return _skip("no strikes below the short strike on the chain")
    candidates.sort(key=lambda r: abs(_f(r.get("strike")) - target_long))
    long_row = None
    long_mid = None
    for r in candidates:
        m = _row_mid(r)
        if m is not None:
            long_row, long_mid = r, m
            break
    if long_row is None:
        return _skip("long-leg quotes missing/zero-bid near target width")

    long_strike = _f(long_row.get("strike"))
    width_actual = short_strike - long_strike
    if width_actual <= 0:
        return _skip("no usable long strike below the short strike")

    net_credit_ps = short_mid - long_mid
    if net_credit_ps <= 0:
        return _skip("net credit non-positive at snapshot quotes")
    credit_pct_of_width = net_credit_ps / width_actual
    floor = _f(cfg.get("min_credit_pct_of_width"),
               DEFAULT_MIN_CREDIT_PCT_OF_WIDTH)
    if credit_pct_of_width < floor:
        return _skip(f"credit {credit_pct_of_width * 100:.1f}% of width "
                     f"< {floor * 100:.0f}% floor")

    dte = idea.get("dte")
    if requoted or not dte:
        d = _days_between((today or date.today()).isoformat(), chain_exp)
        dte = d if d and d > 0 else _f(dte, 0) or None
    max_loss_ps = width_actual - net_credit_ps
    max_loss = max_loss_ps * 100.0
    credit = net_credit_ps * 100.0
    ann = None
    if dte and max_loss > 0:
        ann = (credit / max_loss) * (365.0 / float(dte)) * 100.0

    return {
        "status": "ok",
        "ticker": ticker,
        "expiration": chain_exp,
        "dte": int(dte) if dte else None,
        "short_strike": round(short_strike, 2),
        "long_strike": round(long_strike, 2),
        "width": round(width_actual, 2),
        "short_mid": round(short_mid, 2),
        "long_mid": round(long_mid, 2),
        "net_credit_ps": round(net_credit_ps, 2),
        "credit": round(credit, 2),
        "max_loss": round(max_loss, 2),
        "buying_power": round(max_loss, 2),
        "credit_pct_of_width": round(credit_pct_of_width, 4),
        "annualized_on_bp_pct": round(ann, 1) if ann is not None else None,
        "requoted_from_chain": requoted,
        "csp": {
            "premium": _f(idea.get("premium")),
            "collateral": _f(idea.get("collateral")),
            "annualized_pct": _f(idea.get("annualized_pct")),
        },
    }


def compose_spreads(new_ideas: list | None, chains: dict | None,
                    config: dict | None,
                    today: date | None = None) -> dict:
    """Spread variants for every ACTIONABLE CSP idea (instruction set, not
    RSI-wait/blocked). Tier A/B names are excluded (spreads are for income
    names). Returns {"spreads", "skips", "tier_excluded"}."""
    try:
        from analysis.position_tiers import tier_for
    except ImportError:  # pragma: no cover — standalone use
        try:
            from position_tiers import tier_for  # type: ignore
        except ImportError:
            def tier_for(_t, _c):
                return "C"
    spreads: list[dict] = []
    skips: list[dict] = []
    tier_excluded: list[str] = []
    for idea in (new_ideas or []):
        if not isinstance(idea, dict) or not idea.get("instruction"):
            continue
        if idea.get("rsi_wait") or idea.get("capacity_blocked") \
                or idea.get("rsi_blocked"):
            continue
        tk = str(idea.get("ticker") or "").upper()
        if not tk:
            continue
        tier = tier_for(tk, config)
        if tier in ("A", "B"):
            tier_excluded.append(f"{tk} (Tier {tier})")
            continue
        result = compose_spread(idea, chains or {}, config, today=today)
        (spreads if result.get("status") == "ok" else skips).append(result)
    return {"spreads": spreads, "skips": skips,
            "tier_excluded": tier_excluded}


def render_spreads_section(composed: dict, config: dict | None = None) -> list[str]:
    """The '📐 SPREADS — reference (paper-watch)' subsection. Empty list
    when nothing composed AND nothing was skipped (no noise)."""
    cfg = load_spread_config(config)
    spreads = composed.get("spreads") or []
    skips = composed.get("skips") or []
    tier_excluded = composed.get("tier_excluded") or []
    if not spreads and not skips:
        return []
    mode = cfg.get("mode", "reference")
    lines: list[str] = []
    if mode == "live":
        lines.append("### 📐 SPREADS — live (defined-risk tickets)")
        lines.append("")
    else:
        lines.append("### 📐 SPREADS — reference (paper-watch)")
        lines.append("")
        lines.append(
            "_Defined-risk variants of today's qualified CSP entries — "
            "paper-tracked in `state/spread_paper_ledger.json`, NOT order "
            "tickets and NOT a gate change. Tier A/B names excluded "
            "(conviction names keep the assignment path). Long legs quoted "
            "from this cycle's snapshot chains._")
        lines.append("")
    for s in spreads:
        csp = s.get("csp") or {}
        exp = _fmt_exp(s.get("expiration"))
        dte = s.get("dte")
        dte_bit = f" ({dte} DTE)" if dte else ""
        ann = s.get("annualized_on_bp_pct")
        ann_bit = f" · {ann:.0f}% ann on BP" if ann is not None else ""
        head = (f"- **{s['ticker']} ${s['short_strike']:g}P/"
                f"${s['long_strike']:g}P {exp}{dte_bit}** — "
                f"CSP: ${csp.get('premium', 0):,.0f} premium / "
                f"${csp.get('collateral', 0):,.0f} BP · "
                f"{csp.get('annualized_pct', 0):.1f}% ann **vs** "
                f"SPREAD ${s['short_strike']:g}/${s['long_strike']:g}: "
                f"${s['credit']:,.0f} credit / ${s['max_loss']:,.0f} max loss"
                f"{ann_bit} · credit "
                f"{s['credit_pct_of_width'] * 100:.1f}% of width")
        lines.append(head)
        if mode == "live":
            lines.append(
                f"  - **Order (two legs):** SELL TO OPEN 1× {s['ticker']} "
                f"{exp} ${s['short_strike']:g} PUT @ ${s['short_mid']:.2f} "
                f"mid / BUY TO OPEN 1× {s['ticker']} {exp} "
                f"${s['long_strike']:g} PUT @ ${s['long_mid']:.2f} mid · "
                f"net credit ${s['net_credit_ps']:.2f}/sh")
        if s.get("requoted_from_chain"):
            lines.append(
                f"  - ⚠ both legs requoted from the {exp} snapshot chain "
                f"(candidate expiration differed) — verify at the broker")
    if skips:
        lines.append("")
        for sk in skips:
            lines.append(
                f"- _⏭ {sk.get('ticker', '?')} "
                f"${_f(sk.get('short_strike')):g}P spread skipped — "
                f"{sk.get('reason', 'unknown')}_")
    if tier_excluded:
        lines.append(
            f"- _Tier-excluded (assignment path preferred): "
            f"{', '.join(tier_excluded)}_")
    lines.append("")
    return lines


def _fmt_exp(exp_iso) -> str:
    try:
        d = datetime.strptime(str(exp_iso)[:10], "%Y-%m-%d")
        return d.strftime("%a %b %d '%y").replace(" 0", " ")
    except (ValueError, TypeError):
        return str(exp_iso or "?")


# ── Money Plan informational rollup ──────────────────────────────────────

def spread_reference_summary(gated_entries: list | None, chains: dict | None,
                             config: dict | None,
                             today: date | None = None) -> dict | None:
    """Informational only (reference mode changes NO gate): for today's
    capacity-gated entries, what buying power would spread-mode need vs the
    cash-secured collateral? Only entries whose long leg is REAL (composed
    OK from a snapshot chain) count — never an extrapolated total.

    Returns {"count", "spread_bp", "csp_collateral"} or None when nothing
    composed."""
    count = 0
    spread_bp = 0.0
    csp_coll = 0.0
    for op in (gated_entries or []):
        if not isinstance(op, dict):
            continue
        src = op.get("concrete_trade") if isinstance(op.get("concrete_trade"), dict) else op
        result = compose_spread(src, chains or {}, config, today=today)
        if result.get("status") != "ok":
            continue
        coll = _f(src.get("collateral"))
        if coll <= 0:
            coll = _f(src.get("strike")) * 100.0
        if coll <= 0:
            continue
        count += 1
        spread_bp += _f(result.get("buying_power"))
        csp_coll += coll
    if not count:
        return None
    return {"count": count, "spread_bp": round(spread_bp, 2),
            "csp_collateral": round(csp_coll, 2)}


# ── Paper ledger ─────────────────────────────────────────────────────────

def default_ledger_path(snapshot_dir=None) -> Path:
    """state/spread_paper_ledger.json — resolved relative to the snapshot
    root's parent (state/) so test tmp dirs never touch the real ledger.
    Fixture/dry-run snapshot dirs (…/<date>.test) get a .test ledger."""
    if snapshot_dir is not None:
        sd = Path(snapshot_dir)
        name = ("spread_paper_ledger.test.json"
                if sd.name.endswith(".test") else "spread_paper_ledger.json")
        return sd.parent.parent / name
    return Path("state/spread_paper_ledger.json")


def load_ledger(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"entries": []}


def save_ledger(path: Path, ledger: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def update_paper_ledger(path: Path, spreads: list | None, date_str: str,
                        spot_by_ticker: dict | None = None) -> dict:
    """Track daily paper outcomes: (a) log today's composed spreads as
    entry marks (deduped per date+contract), (b) close out entries whose
    expiration has passed with an ESTIMATED settle from the latest spot
    (labeled — snapshot marks, not expiry closes; hard rule #19).

    Returns {"added", "closed", "open"} and persists the ledger."""
    ledger = load_ledger(path)
    entries = ledger["entries"]
    existing_ids = {e.get("id") for e in entries if isinstance(e, dict)}
    added = 0
    for s in (spreads or []):
        if not isinstance(s, dict) or s.get("status") != "ok":
            continue
        eid = (f"{date_str}:{s['ticker']}:{s['short_strike']:g}/"
               f"{s['long_strike']:g}:{s['expiration']}")
        if eid in existing_ids:
            continue
        existing_ids.add(eid)
        entries.append({
            "id": eid, "entry_date": date_str,
            "ticker": s["ticker"],
            "short_strike": s["short_strike"],
            "long_strike": s["long_strike"],
            "expiration": s["expiration"], "dte": s.get("dte"),
            "net_credit_ps": s["net_credit_ps"], "credit": s["credit"],
            "width": s["width"], "max_loss": s["max_loss"],
            "credit_pct_of_width": s["credit_pct_of_width"],
            "status": "open",
        })
        added += 1
    closed = 0
    spot_by = {str(k).upper(): _f(v) for k, v in (spot_by_ticker or {}).items()}
    for e in entries:
        if not isinstance(e, dict) or e.get("status") != "open":
            continue
        exp = str(e.get("expiration") or "")[:10]
        if not exp or exp >= str(date_str)[:10]:
            continue
        spot = spot_by.get(str(e.get("ticker") or "").upper())
        if spot and spot > 0:
            short_s = _f(e.get("short_strike"))
            long_s = _f(e.get("long_strike"))
            intrinsic = max(0.0, short_s - spot) - max(0.0, long_s - spot)
            pnl = (_f(e.get("net_credit_ps")) - intrinsic) * 100.0
            e["status"] = "expired_estimate"
            e["settle_date"] = date_str
            e["settle_spot"] = round(spot, 2)
            e["est_pnl"] = round(pnl, 2)
            e["settle_note"] = ("estimate — spot at first check after "
                                "expiry, not the expiry close")
            closed += 1
        else:
            # No spot this cycle — stay open, try again next run. Never a
            # fabricated settle (rule #19).
            continue
    save_ledger(path, ledger)
    open_count = sum(1 for e in entries
                     if isinstance(e, dict) and e.get("status") == "open")
    return {"added": added, "closed": closed, "open": open_count}
