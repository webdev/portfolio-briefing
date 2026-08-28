"""Render the Benchmark & Attribution panel (task #16).

Compact markdown section: alpha-vs-SPY table for every window, P/L
attribution buckets for the longest available period, the daily read, cash
drag, and a cost-benefit summary line. Every number comes from the report
objects (never fabricated — hard rule #19); missing windows render "n/a"
with the reason.

Fail-open: any exception inside the renderer returns whatever lines were
safely built (the caller additionally wraps the whole call).
"""

from __future__ import annotations


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.1f}%"


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"-${abs(v):,.0f}" if v < 0 else f"+${v:,.0f}"


def _fmt_usd_plain(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"${v:,.0f}"


_BUCKET_LABELS = [
    ("realized_equity", "Realized equity gains"),
    ("unrealized_equity", "Unrealized equity delta"),
    ("option_premium_net", "Option premium (net)"),
    ("option_mtm", "Option mark-to-market delta"),
    ("assignment_pnl", "Assignment P/L"),
    ("hedge_pnl", "Hedge / long-option P/L"),
    ("interest_dividends", "Interest / dividends (residual cash)"),
]

# Preferred period for the attribution breakdown, longest first.
_PERIOD_PREFERENCE = ["YTD", "since_inception", "30d", "MTD", "daily"]

_PERIOD_TITLES = {
    "YTD": "YTD",
    "since_inception": "Since inception",
    "30d": "30-day",
    "MTD": "Month-to-date",
    "daily": "Daily",
}


def _get(obj, attr, default=None):
    """Attr on dataclass / key in dict — the panel accepts both shapes
    (live objects from the pipeline, plain dicts from JSON round-trips)."""
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def render_benchmark_panel(bench_report, attribution,
                           nlv_reconciliation: dict | None = None) -> list[str]:
    """Emit the '## 📊 Benchmark & Attribution' markdown section.

    Args:
        bench_report: BenchmarkReport (or its to_dict()) — may be None.
        attribution: AttributionReport (or its to_dict()) — may be None.
        nlv_reconciliation: today's `balance["nlv_reconciliation"]` record —
            when the header's 🔴 NLV data-integrity check fired WITH an
            itemization, the unattributed-residual flag cross-references it
            instead of a bare "investigate" (2026-08-28: the same $37.8K
            showed up in both surfaces with no link between them).
    """
    lines: list[str] = ["## 📊 Benchmark & Attribution", ""]
    try:
        _render_benchmark_table(lines, bench_report)
        _render_attribution(lines, attribution,
                            nlv_reconciliation=nlv_reconciliation)
    except Exception as e:  # noqa: BLE001 — never break the briefing
        lines.append(f"_⚠️ Benchmark panel partially unavailable: {e}_")
        lines.append("")
    return lines


def _render_benchmark_table(lines: list[str], report) -> None:
    if report is None:
        lines.append("_Benchmark comparison unavailable this cycle._")
        lines.append("")
        return
    status = _get(report, "status", "unavailable")
    ticker = _get(report, "benchmark_ticker", "SPY") or "SPY"
    if status == "insufficient_history":
        note = _get(report, "note", "") or ""
        suffix = f" — {note}" if note else ""
        lines.append(f"_Insufficient snapshot history for a benchmark read{suffix}._")
        lines.append("")
        return
    if status != "ok":
        note = _get(report, "note", "") or "no data"
        lines.append(f"_Benchmark comparison unavailable: {note}_")
        lines.append("")
        return

    inception = _get(report, "inception_date")
    inception_s = inception if isinstance(inception, str) else (
        inception.isoformat() if inception else "?")
    lines.append(f"**vs {ticker} (alpha):** _(tracking since {inception_s}, "
                 f"{_get(report, 'snapshot_count', 0)} snapshots)_")
    lines.append("")
    lines.append(f"| Window | Portfolio | {ticker} | Alpha |")
    lines.append("|---|---|---|---|")
    window_notes: list[str] = []
    for w in _get(report, "windows", []) or []:
        name = _get(w, "name", "?")
        p = _get(w, "portfolio_return_pct")
        s = _get(w, "spy_return_pct")
        a = _get(w, "alpha_pct")
        if p is None:
            note = _get(w, "note", "") or "no data"
            lines.append(f"| {name} | n/a — {note} | | |")
            continue
        warn = " ⚠️" if (a is not None and a < 0) else ""
        lines.append(f"| {name} | {_fmt_pct(p)} | {_fmt_pct(s)} | "
                     f"**{_fmt_pct(a)}**{warn} |")
        # A valued row can still carry a note (flow-adjusted TWR window,
        # benchmark data gap, ...) — render it, never a silent adjustment.
        w_note = _get(w, "note", "") or ""
        if w_note:
            window_notes.append(f"_{name}: {w_note}_")
    lines.append("")
    if window_notes:
        lines.extend(window_notes)
        lines.append("")
    note = _get(report, "note", "")
    if note:
        lines.append(f"_{note}_")
        lines.append("")


def _pick_period(attribution):
    periods = _get(attribution, "periods", []) or []
    by_name = {_get(p, "name"): p for p in periods}
    for name in _PERIOD_PREFERENCE:
        if name in by_name:
            return by_name[name]
    return periods[0] if periods else None


def _render_attribution(lines: list[str], attribution,
                        nlv_reconciliation: dict | None = None) -> None:
    if attribution is None or _get(attribution, "status") != "ok":
        note = _get(attribution, "note", "") if attribution is not None else ""
        lines.append(f"_P/L attribution unavailable this cycle"
                     f"{' — ' + note if note else ''}._")
        lines.append("")
        return

    main = _pick_period(attribution)
    if main is None:
        lines.append("_P/L attribution: no computable period yet._")
        lines.append("")
        return

    name = _get(main, "name", "period")
    title = _PERIOD_TITLES.get(name, name)
    start = _get(main, "start_date")
    start_s = start if isinstance(start, str) else (start.isoformat() if start else "?")
    buckets = _get(main, "buckets", {}) or {}

    lines.append(f"**{title} P/L attribution** _(since {start_s}; inferred from "
                 f"position deltas — estimates, not a trade ledger)_:")
    lines.append(f"- NLV change: **{_fmt_usd(_get(main, 'nlv_change'))}** "
                 f"({_fmt_usd_plain(_get(main, 'nlv_start'))} → "
                 f"{_fmt_usd_plain(_get(main, 'nlv_end'))})")
    nlv_start = _get(main, "nlv_start") or 0.0
    for key, label in _BUCKET_LABELS:
        if key in buckets:
            lines.append(f"- {label}: {_fmt_usd(buckets.get(key))}")
            # Derivation note (rule #43, 2026-08-14): a large residual-cash
            # bucket must be self-explaining — it is the chain-summed CASH
            # residual (measured cash change − cash inferred from position
            # deltas) per snapshot window, so dividends, interest, fees AND
            # per-window trade-price estimate error all accumulate here. It
            # is NOT a broker statement line and can move materially in one
            # day when a window's fills are proxied at snapshot marks.
            if key == "interest_dividends":
                _idv = buckets.get(key) or 0.0
                if nlv_start and abs(_idv) >= max(0.005 * nlv_start, 5000.0):
                    _nw = _get(main, "n_windows")
                    _win_txt = (f"chain-summed across {int(_nw)} snapshot "
                                f"windows" if _nw
                                else "chain-summed across the period's "
                                     "snapshot windows")
                    lines.append(
                        f"  - _derivation: per-window cash residual "
                        f"(measured cash change − cash inferred from "
                        f"position deltas), {_win_txt} — dividends, "
                        f"interest, fees AND per-window trade-price "
                        f"estimate error accumulate here; not a broker "
                        f"statement line._")

    unattr = _get(main, "unattributed", 0.0) or 0.0
    flag = ""
    if nlv_start and abs(unattr) > max(0.005 * nlv_start, 500.0):
        # Cross-reference the header's NLV data-integrity itemization when
        # it names the same gap (rule #19). 2026-08-28: 'Unattributed
        # (residual): +$37,778 ⚠️ (large residual — balance vs positions
        # disagree; investigate)' and the header's 🔴 Δ $-37,779 were the
        # SAME cash-ledger gap rendered as two unlinked mysteries.
        _rec = nlv_reconciliation or {}
        if _rec.get("warning") and (_rec.get("itemized") or []):
            flag = (f" ⚠️ (matches today's 🔴 NLV data-integrity Δ "
                    f"${_rec.get('delta', 0):+,.0f} — see the header "
                    f"itemization: {(_rec.get('itemized') or [])[0]})")
        else:
            flag = " ⚠️ (large residual — balance vs positions disagree; investigate)"
    lines.append(f"- Unattributed (residual): {_fmt_usd(unattr)}{flag}")

    # Cash drag — opportunity cost of put collateral vs SPY.
    drag = _get(main, "cash_drag", {}) or {}
    opp = drag.get("opportunity_cost")
    if opp is not None:
        lines.append(
            f"- Cash drag opportunity cost: -${abs(opp):,.0f} "
            f"(~${drag.get('avg_put_collateral', 0):,.0f} put collateral × "
            f"{_fmt_pct(drag.get('spy_return_pct'))} SPY)"
            if opp >= 0 else
            f"- Cash drag opportunity cost: +${abs(opp):,.0f} "
            f"(SPY fell {_fmt_pct(drag.get('spy_return_pct'))} — collateral "
            f"sitting out was a win)")
    lines.append("")

    # Daily one-liner (when the main period isn't already daily).
    daily = None
    for p in _get(attribution, "periods", []) or []:
        if _get(p, "name") == "daily":
            daily = p
            break
    if daily is not None and daily is not main:
        db = _get(daily, "buckets", {}) or {}
        # 2026-08-13 fix: name the measured window explicitly — "premium
        # +$0" without dates read as "no premium since the briefing I read
        # yesterday", when it actually measures prior-SNAPSHOT → latest
        # (an intraday rerun can move that baseline).
        d_start = _get(daily, "start_date")
        d_end = _get(daily, "end_date")
        d_start_s = d_start if isinstance(d_start, str) else (
            d_start.isoformat() if d_start else None)
        d_end_s = d_end if isinstance(d_end, str) else (
            d_end.isoformat() if d_end else None)
        win = (f" ({d_start_s} → {d_end_s})"
               if d_start_s and d_end_s else "")
        lines.append(
            f"**Since last snapshot{win}:** NLV {_fmt_usd(_get(daily, 'nlv_change'))} · "
            f"equity MTM {_fmt_usd(db.get('unrealized_equity'))} · "
            f"option MTM {_fmt_usd(db.get('option_mtm'))} · "
            f"premium {_fmt_usd(db.get('option_premium_net'))}")
        # Honest timing-artifact label (2026-08-13): a ~$0 premium window
        # right after a window that banked material premium is usually
        # opens captured by the prior snapshot (intraday rerun) — say where
        # the premium was counted instead of showing a bare false-looking
        # zero.
        try:
            pw = _get(daily, "prior_window", {}) or {}
            pw_prem = pw.get("option_premium_net")
            day_prem = db.get("option_premium_net")
            if (day_prem is not None and abs(day_prem) < 1.0
                    and pw_prem is not None and abs(pw_prem) >= 500.0):
                lines.append(
                    f"_premium +$0 measures the {d_start_s} → {d_end_s} "
                    f"window only — opens captured by the {d_start_s} "
                    f"snapshot (possibly an intraday rerun) were counted "
                    f"in the prior window's {_fmt_usd(pw_prem)}, not "
                    f"lost._")
        except Exception:  # noqa: BLE001 — advisory note, never break
            pass
        lines.append("")

    # Cost-benefit summary: wheel income vs collateral opportunity cost.
    wheel_income = (buckets.get("option_premium_net", 0.0)
                    + buckets.get("option_mtm", 0.0)
                    + buckets.get("assignment_pnl", 0.0))
    if opp is not None:
        net_edge = wheel_income - opp
        verdict = ("the wheel is earning its collateral" if net_edge >= 0
                   else "the collateral would have earned more parked in SPY ⚠️")
        lines.append(
            f"**Cost-benefit ({title}):** wheel P/L (premium + option marks + "
            f"assignments) {_fmt_usd(wheel_income)} vs {_fmt_usd(opp)} the put "
            f"collateral would have made in SPY → net wheel edge "
            f"**{_fmt_usd(net_edge)}** — {verdict}.")
        lines.append("")

    attr_note = _get(attribution, "note", "")
    if attr_note:
        lines.append(f"_{attr_note}_")
        lines.append("")
