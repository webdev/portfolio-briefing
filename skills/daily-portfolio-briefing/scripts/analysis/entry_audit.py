"""Entry Timing Audit — retro-grade of every OPEN option position's entry.

George (2026-08-12): "figure out whether my current options were sold or
bought at the best time. Is there any way to tell that?"

The answer is measured, not guessed: the snapshot archive
(``state/briefing_snapshots/<date>/``) is a daily record of positions,
technicals, quotes and full chains. For each option currently open we:

1. Find its ENTRY DATE — the first archive date the contract appears
   (day precision: the fill happened between the prior snapshot and this
   one). A contract already present in the EARLIEST snapshot is marked
   "opened before the archive — conditions unavailable" (rule #19 — never
   guess).
2. Reconstruct ENTRY CONDITIONS from that day's own snapshot: RSI(14),
   volatility (TRUE chain IV rank from ``state/chain_iv_history.json``
   when the history covers that date, labeled ``IVr``; else the day's
   realized-vol percentile, labeled ``RVr``), day color, 200-SMA distance,
   drawdown, S/R clusters, LT verdict, earnings distance.
3. Re-run the production Setup Grade (``analysis/setup_grade.py``
   ``csp_setup`` / ``cc_setup`` — the exact scorer new trades get) on the
   entry-date inputs. Short put → csp_setup; short call → cc_setup; long
   legs → "n/a — hedge/long leg, different objective" (entry conditions
   still shown).
4. Measure PREMIUM CAPTURE VS LOCAL PEAK: the contract's own mid across
   snapshots in entry_date ± 7 calendar days (position marks + stored
   chains) — what fraction of the local best price the fill captured.
   Fewer than 3 window points → "insufficient chain history".
5. Measure since-entry MFE / MAE / current capture from daily marks.

Everything here reads data already on disk — NO live fetches. Missing
pieces render as 'n/a', never a fabricated number (rule #19).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

try:  # pipeline import context (scripts/ on sys.path)
    from analysis import setup_grade as _sg
except ImportError:  # pragma: no cover — direct-script context
    import setup_grade as _sg  # type: ignore

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# chain_iv.attach_ranks parity: rank needs this many trailing observations.
MIN_IV_HISTORY_OBS = 20
# Local-peak window half-width (calendar days) and minimum data points.
PEAK_WINDOW_DAYS = 7
MIN_PEAK_POINTS = 3
# Wilder RSI recompute fallback needs at least this many closes.
_MIN_RSI_BARS = 30


# ── Archive access ────────────────────────────────────────────────────────


def snapshot_dates(root: Path) -> list[str]:
    """Sorted YYYY-MM-DD snapshot dirs under ``root`` that hold a
    positions.json. Non-date dirs (e.g. ``2026-06-30.test``) excluded."""
    out = []
    root = Path(root)
    if not root.is_dir():
        return out
    for child in root.iterdir():
        if (child.is_dir() and _DATE_RE.match(child.name)
                and (child / "positions.json").exists()):
            out.append(child.name)
    return sorted(out)


def _load_json(path: Path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load_positions(root: Path, date_str: str) -> list:
    data = _load_json(Path(root) / date_str / "positions.json")
    return data if isinstance(data, list) else []


def option_key(pos: dict):
    """Canonical contract identity (underlying, type, strike, expiration).

    Robust across the archive's symbol-format change
    (``AAPL_SHORT_PUT_20260618`` era vs ``AMD_PUT_440_20261016`` era).
    Returns None when any component is missing.
    """
    if not isinstance(pos, dict) or pos.get("assetType") != "OPTION":
        return None
    und = pos.get("underlying")
    typ = pos.get("type")
    strike = pos.get("strike")
    exp = pos.get("expiration")
    if not und or typ not in ("PUT", "CALL") or not strike or not exp:
        return None
    try:
        return (str(und).upper(), str(typ).upper(), float(strike), str(exp))
    except (TypeError, ValueError):
        return None


# ── Entry-date detection ──────────────────────────────────────────────────


def find_entry_date(key, dates: list[str],
                    positions_by_date: dict) -> tuple[str | None, bool]:
    """(entry_date, before_archive).

    entry_date = FIRST archive date the contract key appears. If that is
    the archive's earliest date, the true open predates the archive —
    ``before_archive=True`` and entry conditions must not be graded.
    """
    for d in dates:
        for p in positions_by_date.get(d, []):
            if option_key(p) == key:
                return d, (d == dates[0])
    return None, False


# ── Premium math ──────────────────────────────────────────────────────────


def entry_premium_per_share(pos: dict) -> tuple[float | None, str]:
    """(per-share net premium, source). Preference order:

    1. ``costPerShare`` — the broker's actual net per-share (fees in)
    2. ``premiumReceived`` — gross per-share credit
    3. ``|costBasis| / (|qty|·100)`` — only when costBasis is a real
       dollar figure (current archive writes -0.0 for options)
    Nothing usable → (None, 'n/a').
    """
    for field in ("costPerShare", "premiumReceived"):
        v = pos.get(field)
        try:
            if v is not None and float(v) > 0:
                return float(v), field
        except (TypeError, ValueError):
            pass
    try:
        cb = abs(float(pos.get("costBasis")))
        qty = abs(float(pos.get("qty")))
        if cb > 1e-9 and qty > 0:
            return cb / (qty * 100.0), "costBasis"
    except (TypeError, ValueError):
        pass
    return None, "n/a"


def is_short(pos: dict) -> bool:
    try:
        return float(pos.get("qty") or 0) < 0
    except (TypeError, ValueError):
        return False


# ── Entry conditions ──────────────────────────────────────────────────────


def wilder_rsi(closes: list, period: int = 14) -> float | None:
    """Wilder-smoothed RSI over a close series (same math as the
    pipeline's live recompute). Needs > period closes."""
    if not closes or len(closes) <= period:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        chg = closes[i] - closes[i - 1]
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss <= 1e-12:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 1)


def rsi_from_ohlc_cache(ticker: str, entry_date: str,
                        cache_dir: Path | None) -> float | None:
    """Recompute Wilder RSI as-of entry_date from the persistent OHLC
    cache (``state/cache/ohlc/<TICKER>.csv``). Fail-closed: any problem
    (no pandas, no file, too few bars) → None."""
    if cache_dir is None:
        return None
    path = Path(cache_dir) / f"{ticker.upper()}.csv"
    if not path.exists():
        return None
    try:
        import pandas as pd  # noqa: PLC0415 — optional dependency
        df = pd.read_csv(path)
        if "Date" not in df.columns or "Close" not in df.columns:
            return None
        dts = pd.to_datetime(df["Date"], utc=True).dt.date
        cutoff = datetime.strptime(entry_date, "%Y-%m-%d").date()
        closes = [float(c) for d, c in zip(dts, df["Close"]) if d <= cutoff]
        if len(closes) < _MIN_RSI_BARS:
            return None
        return wilder_rsi(closes[-260:])
    except Exception:
        return None


def chain_iv_rank_at(history: dict | None, ticker: str,
                     date_str: str,
                     min_obs: int = MIN_IV_HISTORY_OBS) -> float | None:
    """TRUE chain IV rank at a historical date — percentile of that
    date's atm_iv_30d against its own trailing history (same formula as
    ``chain_iv.attach_ranks``). Requires the date itself in the series
    and ≥ min_obs trailing observations, else None."""
    series = ((history or {}).get("tickers") or {}).get(ticker.upper()) or {}
    entry = series.get(date_str)
    if not isinstance(entry, dict) or entry.get("atm_iv_30d") is None:
        return None
    cur = entry["atm_iv_30d"]
    trailing = [e.get("atm_iv_30d") for d, e in sorted(series.items())
                if d <= date_str and isinstance(e, dict)
                and e.get("atm_iv_30d") is not None]
    if len(trailing) < min_obs:
        return None
    return round(100.0 * sum(1 for v in trailing if v <= cur)
                 / len(trailing), 1)


def entry_conditions(root: Path, entry_date: str, underlying: str,
                     iv_history: dict | None,
                     ohlc_cache_dir: Path | None = None) -> dict:
    """Everything measurable about the underlying ON the entry date, from
    that date's own snapshot. Unmeasured fields are None (rule #19)."""
    snap = Path(root) / entry_date
    tech = _load_json(snap / "technicals.json") or {}
    quotes = _load_json(snap / "quotes.json") or {}
    earnings = _load_json(snap / "earnings.json") or {}
    t = tech.get(underlying) if isinstance(tech, dict) else None
    t = t if isinstance(t, dict) else {}
    q = quotes.get(underlying) if isinstance(quotes, dict) else None
    q = q if isinstance(q, dict) else {}

    rsi = t.get("rsi_14")
    rsi_source = "snapshot" if rsi is not None else None
    if rsi is None:
        rsi = rsi_from_ohlc_cache(underlying, entry_date, ohlc_cache_dir)
        rsi_source = "ohlc_recompute" if rsi is not None else None

    iv_rank = chain_iv_rank_at(iv_history, underlying, entry_date)
    iv_source = "chain" if iv_rank is not None else None
    if iv_rank is None and t.get("iv_rank") is not None:
        iv_rank = t.get("iv_rank")
        iv_source = "rv"  # realized-vol percentile proxy → 'RVr' label

    day_change_pct = q.get("dayChangePct")  # fraction (quotes convention)

    days_to_earnings = None
    e = earnings.get(underlying) if isinstance(earnings, dict) else None
    if isinstance(e, str) and _DATE_RE.match(e):
        try:
            delta = (datetime.strptime(e, "%Y-%m-%d").date()
                     - datetime.strptime(entry_date, "%Y-%m-%d").date()).days
            if delta >= 0:
                days_to_earnings = delta
        except ValueError:
            pass

    deep = t.get("deep") if isinstance(t.get("deep"), dict) else {}
    return {
        "rsi": rsi, "rsi_source": rsi_source,
        "iv_rank": iv_rank, "iv_source": iv_source,
        "day_change_pct": day_change_pct,
        "drawdown_pct": t.get("drawdown_pct"),
        "spot": t.get("spot"),
        "sma_200": t.get("sma_200"),
        "lt_verdict": deep.get("long_term_verdict"),
        "support_resistance": t.get("support_resistance"),
        "days_to_earnings": days_to_earnings,
    }


# ── Retro Setup Grade (production scorer, entry-date inputs) ─────────────


def retro_grade(pos: dict, cond: dict, config: dict | None = None) -> dict:
    """Run the EXACT production setup grader on the entry-date inputs.

    Short put → ``csp_setup``; short call → ``cc_setup``; long legs →
    letter 'n/a — hedge/long leg, different objective' (a long option is
    protection/directional exposure, not premium selling — the wheel
    grader's objective doesn't apply)."""
    key = option_key(pos)
    if not is_short(pos):
        return {"side": "long", "letter": "n/a", "score": None,
                "hard_blocked": False, "drivers": [], "missing": [],
                "message": "n/a — hedge/long leg, different objective"}
    fn = _sg.csp_setup if key[1] == "PUT" else _sg.cc_setup
    return fn(
        rsi=cond.get("rsi"),
        iv_rank=cond.get("iv_rank"),
        iv_rank_source=cond.get("iv_source"),
        support_resistance=cond.get("support_resistance"),
        strike=key[2],
        spot=cond.get("spot"),
        sma_200=cond.get("sma_200"),
        lt_verdict=cond.get("lt_verdict"),
        day_change_pct=cond.get("day_change_pct"),
        days_to_earnings=cond.get("days_to_earnings"),
        drawdown_pct=cond.get("drawdown_pct"),
        config=config,
    )


# ── Premium capture vs local peak ─────────────────────────────────────────


def _chain_mid_for(root: Path, date_str: str, key) -> float | None:
    """The contract's mid from the stored chain of a given day, or None."""
    und, typ, strike, exp = key
    chain = _load_json(Path(root) / date_str / "chains" / f"{und}_{exp}.json")
    if not isinstance(chain, dict):
        return None
    side = chain.get("puts" if typ == "PUT" else "calls") or []
    for c in side:
        try:
            if abs(float(c.get("strike")) - strike) < 1e-6:
                bid, ask = c.get("bid"), c.get("ask")
                if bid is not None and ask is not None and float(ask) > 0:
                    return round((float(bid) + float(ask)) / 2.0, 4)
                return None
        except (TypeError, ValueError):
            continue
    return None


def _position_mid_for(positions: list, key) -> float | None:
    for p in positions:
        if option_key(p) == key:
            v = p.get("currentMid")
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None
    return None


def local_peak_capture(root: Path, dates: list, positions_by_date: dict,
                       key, entry_date: str, entry_prem: float | None,
                       short: bool) -> dict:
    """Contract mid across snapshots in entry_date ± PEAK_WINDOW_DAYS.

    Each window day's mid comes from the day's position mark
    (``currentMid``) when held, else the stored chain. < MIN_PEAK_POINTS
    usable points → insufficient (never fabricate a peak).

    Short: capture_pct = entry premium / window max mid — % of the local
    peak the fill captured. Long: pct_above_trough = how far above the
    window's min mid the fill paid.
    """
    try:
        e = datetime.strptime(entry_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return {"insufficient": True, "mids": {}, "points": 0}
    lo = e - timedelta(days=PEAK_WINDOW_DAYS)
    hi = e + timedelta(days=PEAK_WINDOW_DAYS)
    mids: dict = {}
    for d in dates:
        dd = datetime.strptime(d, "%Y-%m-%d").date()
        if dd < lo or dd > hi:
            continue
        mid = _position_mid_for(positions_by_date.get(d, []), key)
        if mid is None:
            mid = _chain_mid_for(root, d, key)
        if mid is not None and mid > 0:
            mids[d] = mid
    out: dict = {"mids": mids, "points": len(mids),
                 "insufficient": len(mids) < MIN_PEAK_POINTS}
    if out["insufficient"] or entry_prem is None:
        return out
    if short:
        peak = max(mids.values())
        out["window_max_mid"] = peak
        out["capture_pct"] = (round(100.0 * entry_prem / peak, 1)
                              if peak > 0 else None)
    else:
        trough = min(mids.values())
        out["window_min_mid"] = trough
        out["pct_above_trough"] = (
            round(100.0 * (entry_prem - trough) / trough, 1)
            if trough > 0 else None)
    return out


# ── Since-entry MFE / MAE ─────────────────────────────────────────────────


def _daily_gain(pos: dict, entry_prem: float | None) -> float | None:
    """Per-day P&L mark for the contract: broker ``totalGain`` when
    present, else computed from the entry premium and the day's mid."""
    tg = pos.get("totalGain")
    try:
        if tg is not None:
            return float(tg)
    except (TypeError, ValueError):
        pass
    mid = pos.get("currentMid")
    qty = pos.get("qty")
    try:
        if entry_prem is None or mid is None or qty is None:
            return None
        qty = float(qty)
        mid = float(mid)
        if qty < 0:  # short: profit when mid falls
            return (entry_prem - mid) * abs(qty) * 100.0
        return (mid - entry_prem) * qty * 100.0
    except (TypeError, ValueError):
        return None


def mfe_mae(dates: list, positions_by_date: dict, key,
            entry_date: str, entry_prem: float | None) -> dict:
    """MFE/MAE/current from daily marks since entry (day precision)."""
    best = worst = current = None
    best_d = worst_d = None
    current_mid = None
    for d in dates:
        if d < entry_date:
            continue
        for p in positions_by_date.get(d, []):
            if option_key(p) != key:
                continue
            g = _daily_gain(p, entry_prem)
            if g is None:
                continue
            if best is None or g > best:
                best, best_d = g, d
            if worst is None or g < worst:
                worst, worst_d = g, d
            current = g
            try:
                if p.get("currentMid") is not None:
                    current_mid = float(p["currentMid"])
            except (TypeError, ValueError):
                pass
    out = {"mfe": best, "mfe_date": best_d, "mae": worst,
           "mae_date": worst_d, "current_gain": current}
    if entry_prem and current_mid is not None:
        out["current_capture_pct"] = round(
            100.0 * (entry_prem - current_mid) / entry_prem, 1)
    else:
        out["current_capture_pct"] = None
    return out


# ── Orchestrator ──────────────────────────────────────────────────────────


def _verdict_line(card: dict) -> str:
    """One-line measured verdict — every clause traces to a number."""
    cond = card.get("conditions") or {}
    grade = card.get("grade") or {}
    short = card["short"]
    if card.get("before_archive"):
        return (f"opened on or before {card['entry_date']} (earliest "
                "archive day) — entry conditions unavailable, not graded")
    bits = []
    rsi = cond.get("rsi")
    if rsi is not None:
        verb = "sold" if short else "bought"
        if rsi >= 60:
            bits.append(f"{verb} into strength at RSI {rsi:.0f}")
        elif rsi <= 50:
            bits.append(f"{verb} into weakness at RSI {rsi:.0f}")
        else:
            bits.append(f"{verb} mid-range at RSI {rsi:.0f}")
    letter = grade.get("letter")
    if letter and letter != "n/a":
        bits.append(f"{letter} entry" if letter != "—"
                    else "entry inside an RSI hard block")
    peak = card.get("peak") or {}
    if short and peak.get("capture_pct") is not None:
        bits.append(f"premium {peak['capture_pct']:.0f}% of local peak")
    elif not short and peak.get("pct_above_trough") is not None:
        bits.append(f"paid {peak['pct_above_trough']:.0f}% above local trough")
    elif peak.get("insufficient"):
        bits.append("local-peak read: insufficient chain history")
    if not bits:
        return "insufficient measured data for a verdict"
    return "; ".join(bits)


def audit_position(pos: dict, root: Path, dates: list,
                   positions_by_date: dict, iv_history: dict | None,
                   ohlc_cache_dir: Path | None = None,
                   config: dict | None = None) -> dict:
    key = option_key(pos)
    entry_date, before = find_entry_date(key, dates, positions_by_date)
    short = is_short(pos)
    # Premium from the ENTRY-day record (closest to the actual fill).
    entry_pos = pos
    if entry_date:
        for p in positions_by_date.get(entry_date, []):
            if option_key(p) == key:
                entry_pos = p
                break
    prem, prem_source = entry_premium_per_share(entry_pos)
    if prem is None:  # older records may lack fields the latest has
        prem, prem_source = entry_premium_per_share(pos)
    card: dict = {
        "key": key,
        "symbol": pos.get("symbol"),
        "description": pos.get("symbolDescription") or pos.get("symbol"),
        "qty": pos.get("qty"),
        "short": short,
        "entry_date": entry_date,
        "before_archive": before,
        "entry_premium": prem,
        "entry_premium_source": prem_source,
        "conditions": None, "grade": None, "peak": None,
    }
    if before or entry_date is None:
        card["grade"] = {"letter": "n/a", "score": None,
                         "message": "opened before the archive — "
                                    "conditions unavailable"}
        card["excursion"] = mfe_mae(dates, positions_by_date, key,
                                    entry_date or dates[0], prem)
        card["verdict"] = _verdict_line(card)
        return card
    cond = entry_conditions(root, entry_date, key[0], iv_history,
                            ohlc_cache_dir)
    card["conditions"] = cond
    card["grade"] = retro_grade(pos, cond, config)
    card["peak"] = local_peak_capture(root, dates, positions_by_date, key,
                                      entry_date, prem, short)
    card["excursion"] = mfe_mae(dates, positions_by_date, key,
                                entry_date, prem)
    card["verdict"] = _verdict_line(card)
    return card


def aggregate(cards: list) -> dict:
    """Grade distribution + avg current capture per grade bucket, over
    the graded SHORT positions (long legs and pre-archive opens excluded
    — they carry no comparable grade)."""
    graded = [c for c in cards
              if c.get("short") and not c.get("before_archive")
              and (c.get("grade") or {}).get("letter")
              not in (None, "n/a")]
    dist: dict = {}
    buckets: dict = {}
    scores = []
    for c in graded:
        letter = c["grade"]["letter"]
        dist[letter] = dist.get(letter, 0) + 1
        if c["grade"].get("score") is not None:
            scores.append(c["grade"]["score"])
        cap = (c.get("excursion") or {}).get("current_capture_pct")
        if cap is not None:
            buckets.setdefault(letter, []).append(cap)
    bucket_avg = {k: round(sum(v) / len(v), 1)
                  for k, v in buckets.items() if v}
    return {
        "graded": len(graded),
        "ungraded": len(cards) - len(graded),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "distribution": dict(sorted(dist.items())),
        "capture_by_grade": bucket_avg,
        "capture_counts": {k: len(v) for k, v in buckets.items()},
    }


def run_audit(snapshots_root: Path, as_of: str | None = None,
              iv_history_path: Path | None = None,
              ohlc_cache_dir: Path | None = None,
              config: dict | None = None) -> dict:
    """Audit every open option position in the latest (or --as-of)
    snapshot. Returns {as_of, earliest, cards, aggregate}."""
    root = Path(snapshots_root)
    dates = snapshot_dates(root)
    if not dates:
        return {"error": f"no snapshot archive under {root}"}
    target = as_of if as_of in dates else dates[-1]
    dates = [d for d in dates if d <= target]
    positions_by_date = {d: load_positions(root, d) for d in dates}
    iv_history = _load_json(iv_history_path) if iv_history_path else None
    cards = [
        audit_position(p, root, dates, positions_by_date, iv_history,
                       ohlc_cache_dir, config)
        for p in positions_by_date[target]
        if option_key(p) is not None
    ]
    cards.sort(key=lambda c: (c["key"][0], c["key"][3], c["key"][2]))
    return {"as_of": target, "earliest": dates[0], "cards": cards,
            "aggregate": aggregate(cards)}


def companion_pointer(date_str: str, *report_dirs) -> str | None:
    """Filename of an already-written audit report for ``date_str`` in
    any of ``report_dirs`` — used by run_briefing to add the report to
    the digest's 📎 Full Detail companion_files list. The audit is a
    standalone tool, so the pointer only appears when today's report
    actually exists (never a dangling reference)."""
    name = f"entry_timing_audit_{date_str}.md"
    for d in report_dirs:
        try:
            if d is not None and (Path(d) / name).exists():
                return name
        except OSError:
            continue
    return None


# ── Markdown render ───────────────────────────────────────────────────────


def _fmt(v, spec="{:.1f}", na="n/a"):
    if v is None:
        return na
    try:
        return spec.format(float(v))
    except (TypeError, ValueError):
        return na


def _iv_str(cond: dict) -> str:
    if cond.get("iv_rank") is None:
        return "vol n/a"
    label = "IVr" if cond.get("iv_source") == "chain" else "RVr"
    return f"{label} {cond['iv_rank']:.0f}"


def _day_color(cond: dict) -> str:
    v = cond.get("day_change_pct")
    if v is None:
        return "day move n/a"
    try:
        pct = float(v) * 100.0
    except (TypeError, ValueError):
        return "day move n/a"
    color = "red" if pct < 0 else ("green" if pct > 0 else "flat")
    return f"{color} day ({pct:+.1f}%)"


def render_markdown(result: dict) -> str:
    lines = [
        f"# Entry Timing Audit — open options as of {result['as_of']}",
        "",
        "_George (2026-08-12): \"figure out whether my current options were "
        "sold or bought at the best time. Is there any way to tell that?\"_",
        "",
        "Every open option, graded with the production Setup Grade scorer "
        "re-run on its ENTRY-DATE snapshot conditions. All numbers measured "
        "from the archive; 'n/a' = not measurable (rule #19 — nothing "
        "fabricated).",
        "",
    ]
    for c in result["cards"]:
        prem = c.get("entry_premium")
        qty = c.get("qty")
        try:
            total_prem = (abs(float(qty)) * 100.0 * prem
                          if prem is not None and qty is not None else None)
        except (TypeError, ValueError):
            total_prem = None
        side = ("short " if c["short"] else "long ") + c["key"][1].lower()
        lines.append(f"## {c['description']}  ({side}, qty "
                     f"{_fmt(qty, '{:g}')})")
        if c.get("before_archive"):
            lines.append(
                f"- **Entry:** opened on or before {result['earliest']} — "
                "present in the earliest snapshot; entry conditions "
                "unavailable")
        else:
            lines.append(
                f"- **Entry:** {c['entry_date']} (± 1 day — archive is "
                "day-precision)")
        lines.append(
            f"- **Net premium:** {_fmt(prem, '${:.2f}')}/sh"
            + (f" (${total_prem:,.0f} total)" if total_prem is not None
               else "")
            + f" — source: {c.get('entry_premium_source')}")
        cond = c.get("conditions")
        if cond:
            dd = cond.get("drawdown_pct")
            cbits = [f"RSI {_fmt(cond.get('rsi'), '{:.0f}')}"
                     + (" (recomputed from OHLC cache)"
                        if cond.get("rsi_source") == "ohlc_recompute"
                        else ""),
                     _iv_str(cond), _day_color(cond),
                     f"drawdown {_fmt(dd, '{:.0f}%')}"]
            if cond.get("days_to_earnings") is not None:
                cbits.append(f"earnings in {cond['days_to_earnings']}d")
            lines.append("- **Entry conditions:** " + " · ".join(cbits))
        g = c.get("grade") or {}
        if (g.get("letter") == "n/a" and not c["short"]
                and not c.get("before_archive")):
            lines.append("- **Retro setup grade:** n/a — hedge/long leg, "
                         "different objective")
        elif g.get("letter"):
            drv = "; ".join(g.get("drivers") or [])
            lines.append(
                f"- **Retro setup grade:** {g['letter']}"
                + (f" ({g['score']:.0f}/100)"
                   if g.get("score") is not None else "")
                + (f" — {drv}" if drv else "")
                + (f" — {g.get('message')}"
                   if g.get("letter") == "n/a" and g.get("message") else ""))
        peak = c.get("peak") or {}
        if peak.get("insufficient"):
            n = peak.get("points", 0)
            lines.append("- **Local-peak capture:** insufficient chain "
                         f"history ({n} point{'s' if n != 1 else ''} in ±"
                         f"{PEAK_WINDOW_DAYS}d window)")
        elif c["short"] and peak.get("capture_pct") is not None:
            lines.append(
                f"- **Local-peak capture:** sold at {_fmt(prem, '${:.2f}')} "
                "vs window max mid "
                f"{_fmt(peak.get('window_max_mid'), '${:.2f}')} → "
                f"**{peak['capture_pct']:.0f}% of local peak** "
                f"({peak['points']} points)")
        elif not c["short"] and peak.get("pct_above_trough") is not None:
            lines.append(
                f"- **Local-trough read:** paid {_fmt(prem, '${:.2f}')} vs "
                "window min mid "
                f"{_fmt(peak.get('window_min_mid'), '${:.2f}')} → "
                f"{peak['pct_above_trough']:.0f}% above local trough "
                f"({peak['points']} points)")
        exc = c.get("excursion") or {}
        if exc.get("mfe") is not None:
            cap = exc.get("current_capture_pct")
            lines.append(
                f"- **Since entry:** MFE {_fmt(exc['mfe'], '${:+,.0f}')} "
                f"({exc.get('mfe_date')}) · MAE "
                f"{_fmt(exc.get('mae'), '${:+,.0f}')} "
                f"({exc.get('mae_date')}) · now "
                f"{_fmt(exc.get('current_gain'), '${:+,.0f}')}"
                + (f" ({cap:.0f}% captured)" if cap is not None else ""))
        lines.append(f"- **Verdict:** {c.get('verdict')}")
        lines.append("")
    agg = result.get("aggregate") or {}
    lines.extend(["## Aggregate", ""])
    lines.append(f"- Graded (short, in-archive): {agg.get('graded')} · "
                 f"not gradable: {agg.get('ungraded')} "
                 "(long legs / pre-archive opens / n-a grades)")
    if agg.get("avg_score") is not None:
        lines.append(f"- Average entry score: {agg['avg_score']:.1f}/100 "
                     f"(letter {_sg.letter_for(agg['avg_score'])})")
    if agg.get("distribution"):
        lines.append("- Grade distribution: "
                     + " · ".join(f"{k}: {v}"
                                  for k, v in agg["distribution"].items()))
    if agg.get("capture_by_grade"):
        lines.append("")
        lines.append("| Grade | Positions w/ capture | "
                     "Avg current capture % |")
        lines.append("|---|---|---|")
        for k in sorted(agg["capture_by_grade"]):
            lines.append(f"| {k} | {agg['capture_counts'].get(k)} | "
                         f"{agg['capture_by_grade'][k]:.1f}% |")
        lines.append("")
        lines.append("_Capture-by-grade is a small-sample read — "
                     "correlation, not causation._")
    lines.extend([
        "", "## Caveats", "",
        "- Entry dates are DAY-precision: the fill happened between the "
        "prior snapshot and the entry-date snapshot; intraday timing is "
        "not recoverable from the archive.",
        f"- True chain IV rank ('IVr') needs ≥{MIN_IV_HISTORY_OBS} days of "
        "chain-IV history at the entry date; earlier entries fall back to "
        "the labeled realized-vol percentile ('RVr') or 'n/a'.",
        f"- Contracts already open on {result['earliest']} (earliest "
        "archive day) predate the record — their entry conditions are "
        "unavailable and they are excluded from the aggregate.",
        "- Local-peak capture compares the contract's RAW mid across the "
        "window; the peak can belong to a different underlying-price / "
        "moneyness regime (e.g. a pre-earnings-gap ITM state), so it is a "
        "premium-level read, not a risk-adjusted one. Captures >100% mean "
        "the fill beat every daily snapshot mid in the window.",
    ])
    return "\n".join(lines) + "\n"
