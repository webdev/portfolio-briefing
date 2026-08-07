"""Benchmark tracking — portfolio vs SPY alpha across standard windows.

Task #16. The wheel strategy targets 15-25% alpha over SPY; the actual
1-year number came in at +1%. Without a daily surface the underperformance
is invisible until year-end. This module answers "am I beating SPY today,
this week, this quarter, YTD?" from data the pipeline already has:

  - NLV history: state/briefing_snapshots/<DATE>/balance.json (accountValue)
  - SPY history: analysis/ohlc_cache.cached_history("SPY", days=400)

Design rules (mirrors technical_indicators.py conventions):
  - Pure-math core (`compute_benchmark`) takes plain dicts — no I/O, no
    pandas — so tests pin exact numbers.
  - I/O wrappers (`load_nlv_history`, `fetch_benchmark_closes`,
    `compute_benchmark_from_root`) fail OPEN: any exception returns an
    empty/insufficient report; the briefing must still ship (user rule:
    stability and no bugs).
  - Never fabricate: a window whose start predates the earliest snapshot
    returns None returns (rendered as "n/a — history starts <date>").
  - Snapshots are missing on weekends/holidays/skipped days: the NLV
    lookup snaps to the nearest available snapshot within ±3 days.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

# Nearest-snapshot tolerance (days) when a window's target start date has
# no snapshot (weekend / holiday / pipeline didn't run).
SNAPSHOT_TOLERANCE_DAYS = 3
# SPY trades every business day; allow a slightly wider snap for long
# holiday weekends.
BENCHMARK_TOLERANCE_DAYS = 5

DEFAULT_MIN_SNAPSHOTS = 5
DEFAULT_BENCHMARK_TICKER = "SPY"

# (name, calendar-days lookback). YTD and inception are handled specially.
DEFAULT_WINDOWS: list[tuple[str, int | None]] = [
    ("30d", 30),
    ("90d", 90),
    ("6m", 182),
    ("YTD", None),        # mode: ytd
    ("1y", 365),
    ("inception", None),  # mode: inception
]

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class WindowResult:
    """One benchmark window. All *_pct values are percent (2.1 == +2.1%).

    ``portfolio_return_pct`` is None when the window start predates the
    earliest snapshot (never fabricated). ``spy_return_pct``/``alpha_pct``
    are None when benchmark data is unavailable.
    """

    name: str
    target_start: date | None = None       # the ideal window start date
    snapshot_start: date | None = None     # actual snapshot date used
    nlv_start: float | None = None
    portfolio_return_pct: float | None = None
    spy_return_pct: float | None = None
    alpha_pct: float | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "target_start": self.target_start.isoformat() if self.target_start else None,
            "snapshot_start": self.snapshot_start.isoformat() if self.snapshot_start else None,
            "nlv_start": round(self.nlv_start, 2) if self.nlv_start is not None else None,
            "portfolio_return_pct": (round(self.portfolio_return_pct, 3)
                                     if self.portfolio_return_pct is not None else None),
            "spy_return_pct": (round(self.spy_return_pct, 3)
                               if self.spy_return_pct is not None else None),
            "alpha_pct": round(self.alpha_pct, 3) if self.alpha_pct is not None else None,
            "note": self.note,
        }


@dataclass
class BenchmarkReport:
    """Full benchmark comparison. status: 'ok' | 'insufficient_history' |
    'unavailable' (loader/compute error — briefing still ships)."""

    status: str
    as_of: date | None = None
    benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER
    current_nlv: float | None = None
    snapshot_count: int = 0
    inception_date: date | None = None
    windows: list[WindowResult] = field(default_factory=list)
    # Small normalized series for the webapp chart: {dates, portfolio_pct, spy_pct}
    series: dict = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "benchmark_ticker": self.benchmark_ticker,
            "current_nlv": round(self.current_nlv, 2) if self.current_nlv is not None else None,
            "snapshot_count": self.snapshot_count,
            "inception_date": self.inception_date.isoformat() if self.inception_date else None,
            "windows": [w.to_dict() for w in self.windows],
            "series": self.series,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _coerce_date(d) -> date | None:
    """ISO string / datetime / date → date. None on anything else."""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        try:
            return datetime.strptime(d[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
    return None


def nearest_value(history: dict[date, float], target: date,
                  tolerance_days: int = SNAPSHOT_TOLERANCE_DAYS) -> tuple[date, float] | None:
    """Find the (date, value) in ``history`` nearest to ``target`` within
    ±tolerance_days. Ties prefer the EARLIER date (window start should not
    silently shrink the window). None when nothing is in range."""
    if not history:
        return None
    best: tuple[int, date] | None = None
    for d in history:
        dist = abs((d - target).days)
        if dist > tolerance_days:
            continue
        if best is None or dist < best[0] or (dist == best[0] and d < best[1]):
            best = (dist, d)
    if best is None:
        return None
    return best[1], history[best[1]]


# A day-over-day NLV move beyond this fraction is not a market return — it's
# a deposit/withdrawal or an account-scope change (the real 2026-05-08
# snapshot showed $131K vs $1.12M the next day: a degenerate first pull).
# Ratio-based returns across such a break are garbage, so history before the
# LAST discontinuity is excluded — with a visible note, never silently.
DISCONTINUITY_THRESHOLD = 0.40


def clean_nlv_history(hist: dict[date, float],
                      threshold: float = DISCONTINUITY_THRESHOLD
                      ) -> tuple[dict[date, float], str]:
    """Drop history up to (and including) the last NLV discontinuity.

    Returns (cleaned_history, note). Note is "" when nothing was dropped.
    Fail-open: on any error, returns the input unchanged.
    """
    try:
        days = sorted(hist)
        if len(days) < 2:
            return hist, ""
        break_after: date | None = None
        for prev, cur in zip(days, days[1:]):
            a, b = hist[prev], hist[cur]
            if a > 0 and abs(b / a - 1.0) > threshold:
                break_after = cur
        if break_after is None:
            return hist, ""
        cleaned = {d: v for d, v in hist.items() if d >= break_after}
        dropped = len(hist) - len(cleaned)
        note = (f"NLV discontinuity (deposit/scope change) detected — "
                f"return baseline starts {break_after.isoformat()}; "
                f"{dropped} earlier snapshot(s) excluded from return math")
        return cleaned, note
    except Exception:  # noqa: BLE001
        return hist, ""


# A single-day NLV move beyond this fraction that PERSISTS (the level does
# not mean-revert over the next snapshots) is treated as an external flow
# (deposit / withdrawal / account-scope change), not a market return. Below
# the clean_nlv_history 40% baseline-reset threshold; above any plausible
# one-day market move for this book (worst genuine day on record: -6.3%,
# 2026-07-29). Rule #19: a number that looks like performance must BE
# performance — flow-driven return must never render as alpha.
FLOW_JUMP_THRESHOLD = 0.08


def detect_flow_days(hist: dict[date, float],
                     threshold: float = FLOW_JUMP_THRESHOLD
                     ) -> dict[date, float]:
    """Detect persistent single-day NLV level shifts → external flows.

    Returns {flow_date: estimated_flow_usd}. A jump only counts when the
    following snapshots (up to 3) stay nearer the NEW level than the old —
    a V-shaped dip that mean-reverts is market noise / an intraday artifact,
    not a flow (no false positive on volatile-but-clean series). A jump on
    the last snapshot (no persistence evidence yet) is NOT flagged —
    fail-open, never mark a flow on unconfirmed data.
    """
    flows: dict[date, float] = {}

    def _median(vals: list[float]) -> float | None:
        vals = sorted(v for v in vals if v)
        return vals[len(vals) // 2] if vals else None

    try:
        days = sorted(hist)
        for i in range(1, len(days)):
            a, b = hist[days[i - 1]], hist[days[i]]
            if not a or not b or a <= 0:
                continue
            if abs(b / a - 1.0) <= threshold:
                continue
            # Persistence: the LEVEL around the jump must actually shift.
            # Median of up to 5 preceding vs up to 3 following snapshots —
            # a V-dip (and its recovery leg) leaves the level unchanged and
            # is NOT a flow; a deposit/withdrawal/scope change shifts it.
            pre = _median([hist[d] for d in days[max(0, i - 5):i]])
            post = _median([hist[d] for d in days[i + 1:i + 4]])
            if pre is None or post is None or pre <= 0:
                continue  # cannot confirm persistence — fail-open
            if abs(post - pre) <= threshold * pre:
                continue  # mean-reverted → market noise, not a flow
            flows[days[i]] = b - a
    except Exception:  # noqa: BLE001 — fail-open
        return {}
    return flows


def _chained_return(hist: dict[date, float], current_nlv: float,
                    start: date, end: date,
                    flow_days: set[date]) -> float | None:
    """TWR-style chain-linked return over [start, end], excluding the
    day-over-day move onto each flow day (external flows are not
    performance). Percent; None when uncomputable."""
    try:
        pts = {d: float(v) for d, v in hist.items()
               if start <= d <= end and v and float(v) > 0}
        if current_nlv and float(current_nlv) > 0:
            pts[end] = float(current_nlv)
        days = sorted(pts)
        if len(days) < 2:
            return None
        growth = 1.0
        for prev, cur in zip(days, days[1:]):
            if cur in flow_days:
                continue
            growth *= pts[cur] / pts[prev]
        return (growth - 1.0) * 100.0
    except Exception:  # noqa: BLE001
        return None


def _fmt_flow(amt: float) -> str:
    sign = "+" if amt >= 0 else "-"
    return f"{sign}${abs(amt):,.0f}"


def _apply_flow_adjustment(w: "WindowResult", current_nlv: float, as_of: date,
                           hist: dict[date, float],
                           flows: dict[date, float]) -> None:
    """Replace a window's simple end/start return with the flow-adjusted
    TWR when detected flow days fall inside the window; re-derives alpha
    and annotates the window note (never a silent adjustment)."""
    if w.portfolio_return_pct is None or not flows or w.snapshot_start is None:
        return
    in_window = {d: amt for d, amt in flows.items()
                 if w.snapshot_start < d <= as_of}
    if not in_window:
        return
    twr = _chained_return(hist, current_nlv, w.snapshot_start, as_of,
                          set(in_window))
    if twr is None:
        return
    w.portfolio_return_pct = twr
    if w.spy_return_pct is not None:
        w.alpha_pct = w.portfolio_return_pct - w.spy_return_pct
    flows_s = ", ".join(f"{d.isoformat()} ({_fmt_flow(amt)})"
                        for d, amt in sorted(in_window.items()))
    w.note = ((w.note + "; " if w.note else "")
              + f"flow-adjusted (TWR) — excluded external flow day(s): {flows_s}")


def _pct(end: float, start: float) -> float | None:
    """(end/start - 1) * 100, None when start is unusable."""
    try:
        if start is None or end is None or float(start) == 0:
            return None
        return (float(end) / float(start) - 1.0) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _first_on_or_after(history: dict[date, float], floor: date,
                       ceiling: date) -> tuple[date, float] | None:
    """Earliest entry with floor <= date <= ceiling."""
    candidates = sorted(d for d in history if floor <= d <= ceiling)
    if not candidates:
        return None
    return candidates[0], history[candidates[0]]


def _window_result(name: str, target: date, current_nlv: float, as_of: date,
                   nlv_history: dict[date, float],
                   spy_closes: dict[date, float],
                   earliest_snapshot: date) -> WindowResult:
    w = WindowResult(name=name, target_start=target)

    # Don't fabricate: window starts before recorded history → None.
    if target < earliest_snapshot - timedelta(days=SNAPSHOT_TOLERANCE_DAYS):
        w.note = f"history starts {earliest_snapshot.isoformat()}"
        return w

    snap = nearest_value(nlv_history, target, SNAPSHOT_TOLERANCE_DAYS)
    if snap is None:
        w.note = "no snapshot within ±3 days of window start"
        return w

    w.snapshot_start, w.nlv_start = snap
    w.portfolio_return_pct = _pct(current_nlv, w.nlv_start)

    spy_start = nearest_value(spy_closes, w.snapshot_start, BENCHMARK_TOLERANCE_DAYS)
    spy_end = nearest_value(spy_closes, as_of, BENCHMARK_TOLERANCE_DAYS)
    if spy_start and spy_end and spy_start[0] != spy_end[0]:
        w.spy_return_pct = _pct(spy_end[1], spy_start[1])
    else:
        w.note = (w.note + "; " if w.note else "") + "benchmark data unavailable"

    if w.portfolio_return_pct is not None and w.spy_return_pct is not None:
        w.alpha_pct = w.portfolio_return_pct - w.spy_return_pct
    return w


def _build_series(current_nlv: float, as_of: date,
                  nlv_history: dict[date, float],
                  spy_closes: dict[date, float], days: int = 90) -> dict:
    """Normalized cumulative-% series for the webapp chart (both start 0%)."""
    cutoff = as_of - timedelta(days=days)
    pts: list[tuple[date, float]] = sorted(
        (d, v) for d, v in nlv_history.items() if cutoff <= d <= as_of and v
    )
    if as_of not in dict(pts) and current_nlv:
        pts.append((as_of, float(current_nlv)))
        pts.sort()
    if len(pts) < 2:
        return {}
    base_nlv = pts[0][1]
    spy_base = nearest_value(spy_closes, pts[0][0], BENCHMARK_TOLERANCE_DAYS)
    dates, port, spy = [], [], []
    for d, v in pts:
        p = _pct(v, base_nlv)
        if p is None:
            continue
        s = None
        if spy_base:
            spy_pt = nearest_value(spy_closes, d, BENCHMARK_TOLERANCE_DAYS)
            if spy_pt:
                s = _pct(spy_pt[1], spy_base[1])
        dates.append(d.isoformat())
        port.append(round(p, 3))
        spy.append(round(s, 3) if s is not None else None)
    if not dates:
        return {}
    return {"dates": dates, "portfolio_pct": port, "spy_pct": spy}


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------

def compute_benchmark(current_nlv: float,
                      nlv_history: dict,
                      spy_closes: dict,
                      as_of=None,
                      windows: list[tuple[str, int | None]] | None = None,
                      min_snapshots: int = DEFAULT_MIN_SNAPSHOTS,
                      benchmark_ticker: str = DEFAULT_BENCHMARK_TICKER) -> BenchmarkReport:
    """Compute portfolio-vs-benchmark alpha for each window.

    Args:
        current_nlv: today's NLV (pipeline balance.accountValue).
        nlv_history: {date|iso-str: nlv} from prior snapshots.
        spy_closes: {date|iso-str: close} for the benchmark ticker.
        as_of: report date (defaults to today).
        windows: [(name, calendar_days_or_None)] — None days means the name
            selects a mode: "YTD" or "inception".

    Returns a BenchmarkReport; never raises on bad inputs (fail-open).
    """
    try:
        as_of_d = _coerce_date(as_of) or date.today()
        hist = {k: float(v) for k, v in
                ((_coerce_date(kk), vv) for kk, vv in (nlv_history or {}).items())
                if k is not None and v is not None and float(v) > 0}
        spy = {k: float(v) for k, v in
               ((_coerce_date(kk), vv) for kk, vv in (spy_closes or {}).items())
               if k is not None and v is not None and float(v) > 0}

        hist, discontinuity_note = clean_nlv_history(hist)
        # External flows below the 40% baseline-reset threshold (deposits /
        # withdrawals / scope changes that persist as a level shift) —
        # windowed returns are flow-adjusted (TWR) so a flow never renders
        # as alpha (rule #19).
        flows = detect_flow_days(hist)
        if flows:
            flows_s = ", ".join(f"{d.isoformat()} ({_fmt_flow(amt)})"
                                for d, amt in sorted(flows.items()))
            flow_note = (f"external flow(s) detected (persistent level shift "
                         f"> {FLOW_JUMP_THRESHOLD:.0%} d/d): {flows_s} — "
                         f"windowed returns are flow-adjusted (TWR)")
            discontinuity_note = ((discontinuity_note + "; "
                                   if discontinuity_note else "") + flow_note)

        report = BenchmarkReport(status="ok", as_of=as_of_d,
                                 benchmark_ticker=benchmark_ticker,
                                 current_nlv=float(current_nlv) if current_nlv else None,
                                 snapshot_count=len(hist),
                                 note=discontinuity_note)
        if len(hist) < min_snapshots:
            report.status = "insufficient_history"
            report.note = (f"only {len(hist)} snapshot(s) on record — "
                           f"need ≥ {min_snapshots} for a benchmark read")
            return report
        if not current_nlv or float(current_nlv) <= 0:
            report.status = "unavailable"
            report.note = "current NLV unavailable"
            return report

        earliest = min(hist)
        report.inception_date = earliest

        for name, days in (windows or DEFAULT_WINDOWS):
            mode = name.lower()
            if days is not None:
                target = as_of_d - timedelta(days=int(days))
                report.windows.append(_window_result(
                    name, target, float(current_nlv), as_of_d, hist, spy, earliest))
            elif mode == "ytd":
                # First market-open of the current calendar year. Honest rule:
                # if history starts mid-year, YTD is not computable — render
                # n/a rather than fabricate a partial-year number.
                jan1 = date(as_of_d.year, 1, 1)
                w = WindowResult(name=name, target_start=jan1)
                snap = _first_on_or_after(hist, jan1, jan1 + timedelta(days=SNAPSHOT_TOLERANCE_DAYS + 7))
                if earliest > jan1 + timedelta(days=SNAPSHOT_TOLERANCE_DAYS + 7):
                    w.note = f"history starts {earliest.isoformat()} — YTD baseline missing"
                elif snap is None:
                    w.note = "no snapshot near start of year"
                else:
                    w.snapshot_start, w.nlv_start = snap
                    w.portfolio_return_pct = _pct(float(current_nlv), w.nlv_start)
                    spy_start = _first_on_or_after(spy, jan1, jan1 + timedelta(days=10))
                    spy_end = nearest_value(spy, as_of_d, BENCHMARK_TOLERANCE_DAYS)
                    if spy_start and spy_end:
                        w.spy_return_pct = _pct(spy_end[1], spy_start[1])
                    if w.portfolio_return_pct is not None and w.spy_return_pct is not None:
                        w.alpha_pct = w.portfolio_return_pct - w.spy_return_pct
                report.windows.append(w)
            elif mode == "inception":
                w = _window_result(name, earliest, float(current_nlv), as_of_d,
                                   hist, spy, earliest)
                report.windows.append(w)
            else:
                # Unknown mode — skip silently rather than crash.
                continue

        # Flow-adjust every computed window (TWR) — after the loop so all
        # three window modes (days / YTD / inception) get the same treatment.
        for w in report.windows:
            _apply_flow_adjustment(w, float(current_nlv), as_of_d, hist, flows)

        report.series = _build_series(float(current_nlv), as_of_d, hist, spy)
        return report
    except Exception as e:  # noqa: BLE001 — fail-open, briefing must ship
        return BenchmarkReport(status="unavailable",
                               as_of=_coerce_date(as_of) or date.today(),
                               benchmark_ticker=benchmark_ticker,
                               note=f"benchmark compute failed: {e}")


# ---------------------------------------------------------------------------
# I/O wrappers (fail-open)
# ---------------------------------------------------------------------------

def balance_nlv(bal: dict) -> float | None:
    """Preferred NLV from a balance.json dict — broker truth beats
    reconstruction (same principle as ``_compose_balance``, 2026-08-04).

    2026-08-07 defect: the briefing rendered "30d | Portfolio +20.8% | SPY
    +3.2% | Alpha +17.6%" off a 2026-07-08 baseline of $901,634 — an
    ``accountValue_corrected`` artifact. The migration rebuilt NLV as
    ``cash + longMV + position marks``, but old-era ``cash`` is E*TRADE's
    ``cashAvailableForInvestment`` — a margin-availability figure that
    swings with collateral holds, NOT actual cash — so the corrected series
    carried fake ±5-12% daily volatility. The broker's own
    ``totalAccountValue`` for 2026-07-08 was $1,002,569 (true 30d return
    +8.6%), and it matches the corrected recompute to the penny on days the
    marks were clean, proving it is the same INDIVIDUAL-scoped NLV.

    Preference order:
      1. ``totalAccountValue`` — E*TRADE's own real-time net account value
         (option-mark-inclusive by construction; present in every live
         snapshot).
      2. ``accountValue_corrected`` (scripts/recompute_nlv_history.py).
      3. ``accountValue``.
      4. longMarketValue + cash (last resort).
    Never overwrites anything — this is a reader-side preference only.
    """
    try:
        nlv = None
        raw_tav = bal.get("totalAccountValue")
        if raw_tav is not None:
            try:
                tav = float(raw_tav)
                if tav > 0:
                    nlv = tav
            except (TypeError, ValueError):
                nlv = None
        if nlv is None:
            nlv = bal.get("accountValue_corrected")
        if nlv is None:
            nlv = bal.get("accountValue")
        if nlv is None:
            nlv = (bal.get("longMarketValue") or 0) + (bal.get("cash") or 0)
        return float(nlv)
    except (TypeError, ValueError):
        return None


def balance_option_inclusive(bal: dict) -> bool:
    """True when this balance's NLV already includes signed option marks —
    a broker ``totalAccountValue`` (which :func:`balance_nlv` now prefers,
    and which is option-mark-inclusive by construction), the broker-true
    era (optionMarketValue / nlv_reconciliation present, 2026-08-04
    onward), or a migrated snapshot (accountValue_corrected present)."""
    if not isinstance(bal, dict):
        return False
    try:
        tav = float(bal.get("totalAccountValue") or 0)
    except (TypeError, ValueError):
        tav = 0.0
    return (tav > 0
            or bal.get("accountValue_corrected") is not None
            or bal.get("optionMarketValue") is not None
            or bool(bal.get("nlv_reconciliation")))


def load_nlv_history_meta(snapshot_root: Path) -> tuple[dict[date, float], set]:
    """Like :func:`load_nlv_history` but also returns the set of dates whose
    NLV is NOT option-mark-inclusive (old-era snapshots the migration could
    not correct). Used by the ``nlv_rebase_dates`` belt-and-suspenders."""
    out: dict[date, float] = {}
    uncorrected: set = set()
    try:
        root = Path(snapshot_root)
        if not root.exists():
            return out, uncorrected
        for child in root.iterdir():
            if not child.is_dir() or not _DATE_DIR_RE.match(child.name):
                continue
            d = _coerce_date(child.name)
            if d is None:
                continue
            bal_path = child / "balance.json"
            try:
                bal = json.loads(bal_path.read_text(encoding="utf-8"))
                nlv = balance_nlv(bal)
                if nlv is not None and nlv > 0:
                    out[d] = nlv
                    if not balance_option_inclusive(bal):
                        uncorrected.add(d)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] benchmark_tracker: NLV history load failed: {e}",
              file=sys.stderr)
    return out, uncorrected


def load_nlv_history(snapshot_root: Path) -> dict[date, float]:
    """Read {date: NLV} from every dated snapshot dir's balance.json.

    Prefers ``accountValue_corrected`` (option-mark-inclusive recompute) over
    the original ``accountValue`` — mixing the two conventions poisoned the
    alpha table and attribution residual (2026-08-05 defect: -$65,345
    unattributed residual from an inflated pre-correction start NLV).
    Skips ``.test`` dirs (fixture/dry-run snapshots must never pollute the
    return series), non-date dirs, and unreadable/zero balances. Never raises.
    """
    return load_nlv_history_meta(snapshot_root)[0]


def rebase_dates_from_config(cfg: dict | None) -> list[date]:
    """Parse ``benchmark_tracking.nlv_rebase_dates`` (ISO strings) → dates."""
    out: list[date] = []
    for raw in ((cfg or {}).get("nlv_rebase_dates") or []):
        d = _coerce_date(raw)
        if d is not None:
            out.append(d)
    return sorted(out)


def effective_rebase_date(hist: dict, uncorrected: set,
                          rebase_dates: list) -> date | None:
    """The rebase date to apply, or None.

    A configured rebase date fires ONLY when the correction could not cover
    the full history — i.e. some snapshot BEFORE the rebase date is still
    uncorrected (not option-mark-inclusive). When the migration corrected
    everything, the series is consistent end-to-end and no rebase is needed.
    """
    best: date | None = None
    for rb in rebase_dates or []:
        if any(d < rb for d in uncorrected if d in hist):
            if best is None or rb > best:
                best = rb
    return best


def fetch_benchmark_closes(ticker: str = DEFAULT_BENCHMARK_TICKER,
                           days: int = 400) -> dict[date, float]:
    """SPY (or configured benchmark) daily closes via the persistent OHLC
    cache, raw-yfinance fallback. {} on any failure — the report renders
    portfolio returns with 'benchmark data unavailable'."""
    try:
        try:
            from analysis.ohlc_cache import cached_history
            hist = cached_history(ticker, days=days)
        except ImportError:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(period=f"{days}d")
        if hist is None or getattr(hist, "empty", True) or "Close" not in hist.columns:
            return {}
        out: dict[date, float] = {}
        for idx, close in hist["Close"].items():
            try:
                c = float(close)
                if c > 0:
                    out[idx.date()] = c
            except (TypeError, ValueError, AttributeError):
                continue
        return out
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] benchmark_tracker: {ticker} history fetch failed: {e}",
              file=sys.stderr)
        return {}


def _windows_from_config(cfg: dict) -> list[tuple[str, int | None]] | None:
    """briefing.yaml → benchmark_tracking.windows override. None → defaults."""
    raw = (cfg or {}).get("windows")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[tuple[str, int | None]] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"])
        mode = str(item.get("mode") or "").lower()
        days = item.get("days")
        if mode in ("ytd", "inception"):
            out.append((mode.upper() if mode == "ytd" else mode, None))
        elif days is not None:
            try:
                out.append((name, int(days)))
            except (TypeError, ValueError):
                continue
    return out or None


def compute_benchmark_from_root(current_nlv: float, snapshot_root: Path,
                                as_of=None, config: dict | None = None) -> BenchmarkReport:
    """Load history + benchmark closes and compute the report. Fail-open."""
    try:
        cfg = config or {}
        ticker = str(cfg.get("benchmark_ticker") or DEFAULT_BENCHMARK_TICKER)
        min_snaps = int(cfg.get("min_snapshots_for_report") or DEFAULT_MIN_SNAPSHOTS)
        hist, uncorrected = load_nlv_history_meta(snapshot_root)
        # Belt-and-suspenders (nlv_rebase_dates): when the NLV-correction
        # migration could NOT cover the full history, treat the configured
        # rebase date as a hard baseline so pre-correction (inflated) NLVs
        # never mix into the return math. No-op when everything is corrected.
        rebase_note = ""
        rb = effective_rebase_date(hist, uncorrected, rebase_dates_from_config(cfg))
        if rb is not None:
            dropped = sum(1 for d in hist if d < rb)
            hist = {d: v for d, v in hist.items() if d >= rb}
            rebase_note = (f"NLV rebase applied at {rb.isoformat()} — {dropped} "
                           f"uncorrectable pre-correction snapshot(s) excluded")
        spy = fetch_benchmark_closes(ticker, days=400)
        report = compute_benchmark(
            current_nlv, hist, spy, as_of=as_of,
            windows=_windows_from_config(cfg),
            min_snapshots=min_snaps, benchmark_ticker=ticker,
        )
        if rebase_note:
            report.note = (report.note + "; " if report.note else "") + rebase_note
        return report
    except Exception as e:  # noqa: BLE001
        return BenchmarkReport(status="unavailable",
                               note=f"benchmark report failed: {e}")
