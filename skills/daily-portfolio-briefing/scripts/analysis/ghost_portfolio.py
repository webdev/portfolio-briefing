"""👻 Ghost portfolio — the options-stripped counterfactual NAV (task #41).

Answers George's question: **"is it the market or is it my moves?"**

Ghost NAV_t = what the account would be worth if, at inception, every open
option had been closed at that day's marks and NO option had ever been
traded again — equities (and external deposits/withdrawals) mirrored from
the real account, ALL option cash flows stripped. Then:

    Real NAV_t − Ghost NAV_t  =  the option program's cumulative net
    contribution since inception (premium banked − upside capped − crash
    amplification − roll debits), marked to market daily.

Construction rules (these ARE the assumptions — rendered as a footnote):

1. **Ghost equity holdings** mirror the real account's equity share-count
   timeline (day-over-day diffs of EQUITY rows). Option-CAUSED equity
   changes are NOT mirrored: a share change matching an option that
   disappeared ITM at expiry (±1 day) — short-put assignment (+100·N sh) or
   called-away short call (−100·N sh) — stays out of the ghost. (History
   note: no assignments through 2026-08; this future-proofs the series.)
2. **Ghost cash** starts as real cash + the signed option book's mark at
   inception (the ghost "closes" every option at inception marks, so
   Ghost NAV_0 == Real NAV_0 and the gap starts at exactly $0). After
   that: mirrored equity trade flows (Δshares × that day's mark) and
   mirrored external flows (a real-NAV day-over-day residual beyond
   ``FLOW_THRESHOLD_PCT`` that equity+option marks can't explain — the
   known May NLV discontinuity shape). No premium received, no buybacks
   paid — ever. Ghost cash MAY go negative when a real equity buy was
   premium-funded; that is kept honest, not clamped.
3. **Ghost NAV_t** = ghost cash + Σ(ghost shares × equity mark_t) using each
   snapshot's own marks (positions price, quotes fallback, carry-forward
   last known mark when a sold name has no quote — noted, never invented).
   Real NAV uses the ``accountValue_corrected`` conventions via
   ``benchmark_tracker.balance_nlv``.
4. The series persists to ``state/ghost_portfolio.json`` (sibling of
   ``briefing_snapshots/``) as date → {ghost_nav, real_nav, gap,
   gap_delta_1d}; the backfill (scripts/build_ghost_history.py) is a full
   idempotent recompute — re-running it is always safe.

Fail-open discipline: snapshot days missing positions/balance are skipped
(the next day's gap_delta simply spans them); any exception returns an
``unavailable`` report and the briefing ships without the panel.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

# A real-NAV day-over-day residual (after subtracting the equity + option
# mark-to-market move) larger than this fraction of NAV is treated as an
# external deposit/withdrawal/scope-change and mirrored into ghost cash.
# Normal market days leave the residual near zero.
FLOW_THRESHOLD_PCT = 0.08

# Real-NAV discontinuity used to pick inception (same convention as
# benchmark_tracker.clean_nlv_history): a >40% day-over-day break is a
# scope change / degenerate first pull, not a return.
DISCONTINUITY_THRESHOLD = 0.40

# Assignment detection: option must have disappeared with expiration within
# ±1 day of the snapshot boundary to count as expiry-driven.
ASSIGNMENT_EXPIRY_TOLERANCE_DAYS = 1

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ASSUMPTION_FOOTNOTE = (
    "Ghost = all options closed at inception marks, never traded again; "
    "equity trades + external flows mirrored at each day's marks; ALL "
    "option cash flows stripped (no premium banked, no buybacks paid — "
    "equity buys are mirrored even where the real account funded them with "
    "option premium); option-caused share changes (assignment/called-away "
    "at expiry) are NOT mirrored."
)


def _f(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _coerce_date(d) -> date | None:
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


@dataclass
class GhostDay:
    """One day of the ghost series. Dollar values rounded at serialization."""

    date: date
    ghost_nav: float
    real_nav: float
    gap: float                      # real − ghost (options program net)
    gap_delta_1d: float | None      # vs previous SNAPSHOT day (may span >1d)

    def to_dict(self) -> dict:
        return {
            "ghost_nav": round(self.ghost_nav, 2) + 0.0,   # +0.0 kills -0.0
            "real_nav": round(self.real_nav, 2) + 0.0,
            "gap": round(self.gap, 2) + 0.0,
            "gap_delta_1d": (round(self.gap_delta_1d, 2) + 0.0
                             if self.gap_delta_1d is not None else None),
        }


@dataclass
class GhostReport:
    """status: 'ok' | 'insufficient_history' | 'unavailable'."""

    status: str
    inception: date | None = None
    as_of: date | None = None
    days: list[GhostDay] = field(default_factory=list)
    events: list[str] = field(default_factory=list)   # mirrored/skipped moves
    notes: list[str] = field(default_factory=list)    # data-quality caveats
    footnote: str = ASSUMPTION_FOOTNOTE

    # ── Derived reads ────────────────────────────────────────────────────
    @property
    def latest(self) -> GhostDay | None:
        return self.days[-1] if self.days else None

    def gap_change(self, start: date, end: date | None = None) -> float | None:
        """Gap movement between the snapshot ON/AFTER ``start`` and the last
        snapshot ON/BEFORE ``end`` (default: latest). None when either
        endpoint has no snapshot in range — never extrapolated."""
        if not self.days:
            return None
        end = end or self.days[-1].date
        starts = [d for d in self.days if d.date >= start]
        ends = [d for d in self.days if d.date <= end]
        if not starts or not ends:
            return None
        d0, d1 = starts[0], ends[-1]
        if d1.date < d0.date:
            return None
        return d1.gap - d0.gap

    def to_dict(self) -> dict:
        summ = summary_figures(self)
        return {
            "status": self.status,
            "inception": self.inception.isoformat() if self.inception else None,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "series": {d.date.isoformat(): d.to_dict() for d in self.days},
            "summary": {k: (round(v, 2) if isinstance(v, float) else v)
                        for k, v in summ.items()},
            "events": self.events,
            "notes": self.notes,
            "footnote": self.footnote,
        }


# ---------------------------------------------------------------------------
# Snapshot readers (pure given loaded dicts)
# ---------------------------------------------------------------------------

def _equity_map(positions: list) -> dict[str, dict]:
    """{symbol: {"qty": float, "price": float|None}} aggregated over EQUITY
    rows (qty summed; price = last seen non-null)."""
    out: dict[str, dict] = {}
    for p in positions or []:
        if not isinstance(p, dict) or p.get("assetType") != "EQUITY":
            continue
        sym = str(p.get("symbol") or "").upper()
        qty = _f(p.get("qty"))
        if not sym or qty is None:
            continue
        row = out.setdefault(sym, {"qty": 0.0, "price": None})
        row["qty"] += qty
        price = _f(p.get("price"))
        if price:
            row["price"] = price
    return out


def _option_rows(positions: list) -> dict[str, dict]:
    """{option symbol: row} for OPTION rows carrying enough fields for
    assignment detection."""
    out: dict[str, dict] = {}
    for p in positions or []:
        if not isinstance(p, dict) or p.get("assetType") != "OPTION":
            continue
        sym = str(p.get("symbol") or "")
        if sym:
            out[sym] = p
    return out


def _price_map(positions: list, quotes: dict) -> dict[str, float]:
    """Equity marks for the day: positions price first, quotes.last fallback."""
    out: dict[str, float] = {}
    for sym, q in (quotes or {}).items():
        last = _f(q.get("last")) if isinstance(q, dict) else None
        if last:
            out[str(sym).upper()] = last
    for sym, row in _equity_map(positions).items():
        if row["price"]:
            out[sym] = row["price"]
    return out


def _option_book_mark(balance: dict, positions: list) -> float | None:
    """Signed option-book market value: balance.optionMarketValue when the
    broker provided it, else summed OPTION marketValue rows, else summed
    currentMid × qty × 100. None when nothing is measurable."""
    v = _f((balance or {}).get("optionMarketValue"))
    if v is not None:
        return v
    total, seen = 0.0, False
    for p in positions or []:
        if not isinstance(p, dict) or p.get("assetType") != "OPTION":
            continue
        mv = _f(p.get("marketValue"))
        if mv is None:
            mid, qty = _f(p.get("currentMid")), _f(p.get("qty"))
            if mid is not None and qty is not None:
                mv = mid * qty * 100.0
        if mv is not None:
            total += mv
            seen = True
    return total if seen else None


def _assignment_share_deltas(prev_opts: dict[str, dict],
                             cur_opts: dict[str, dict],
                             cur_date: date,
                             prices: dict[str, float],
                             prev_prices: dict[str, float]) -> dict[str, float]:
    """Expected option-CAUSED share change per underlying between two
    snapshots: short puts that vanished ITM at expiry → +100·N shares
    (assignment buy); short calls vanished ITM at expiry → −100·N
    (called away). These share deltas must NOT be mirrored into the ghost."""
    out: dict[str, float] = {}
    for sym, row in prev_opts.items():
        if sym in cur_opts:
            continue
        exp = _coerce_date(row.get("expiration"))
        if exp is None:
            continue
        if exp > cur_date + timedelta(days=ASSIGNMENT_EXPIRY_TOLERANCE_DAYS):
            # Option bought back / rolled before expiry — a normal option
            # trade, cash-stripped anyway; not a share event.
            continue
        if (cur_date - exp).days > 7:
            continue  # long-gone contract lingering in a stale snapshot
        qty = _f(row.get("qty"), 0.0) or 0.0
        if qty >= 0:
            continue  # long options: exercise is a deliberate trade — mirror
        und = str(row.get("underlying") or "").upper()
        strike = _f(row.get("strike"))
        otype = str(row.get("type") or "").upper()
        spot = prices.get(und) or prev_prices.get(und)
        if not und or strike is None or spot is None:
            continue
        contracts = abs(qty)
        if otype == "PUT" and spot < strike:
            out[und] = out.get(und, 0.0) + 100.0 * contracts
        elif otype == "CALL" and spot > strike:
            out[und] = out.get(und, 0.0) - 100.0 * contracts
    return out


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------

def compute_ghost_series(snapshots: list[dict],
                         flow_threshold_pct: float = FLOW_THRESHOLD_PCT,
                         discontinuity_threshold: float = DISCONTINUITY_THRESHOLD,
                         ) -> GhostReport:
    """Build the ghost series from loaded snapshots.

    Each snapshot dict: {"date": date|iso, "positions": [...],
    "balance": {...}, "quotes": {...}}. Days missing positions or a usable
    NAV are skipped (fail-open). Never raises.
    """
    try:
        from analysis.benchmark_tracker import balance_nlv
    except ImportError:                                   # standalone import
        from benchmark_tracker import balance_nlv         # type: ignore

    try:
        rows = []
        for s in snapshots or []:
            d = _coerce_date(s.get("date"))
            bal = s.get("balance")
            pos = s.get("positions")
            if d is None or not isinstance(bal, dict) \
                    or not isinstance(pos, list) or not pos:
                continue
            nav = balance_nlv(bal)
            if nav is None or nav <= 0:
                continue
            rows.append({"date": d, "balance": bal, "positions": pos,
                         "quotes": s.get("quotes") or {}, "nav": nav})
        rows.sort(key=lambda r: r["date"])
        if len(rows) < 2:
            return GhostReport(status="insufficient_history",
                               notes=["need ≥ 2 usable snapshots"])

        # Inception = day after the LAST real-NAV discontinuity (the May
        # scope-change shape) — ratio math across such a break is garbage.
        start_idx = 0
        for i in range(1, len(rows)):
            a, b = rows[i - 1]["nav"], rows[i]["nav"]
            if a > 0 and abs(b / a - 1.0) > discontinuity_threshold:
                start_idx = i
        rows = rows[start_idx:]
        if len(rows) < 2:
            return GhostReport(status="insufficient_history",
                               notes=["history after the last NAV "
                                      "discontinuity is too short"])

        report = GhostReport(status="ok", inception=rows[0]["date"],
                             as_of=rows[-1]["date"])
        if start_idx:
            report.notes.append(
                f"{start_idx} snapshot(s) before the "
                f"{rows[0]['date'].isoformat()} NAV discontinuity excluded")

        # ── Inception state ─────────────────────────────────────────────
        first = rows[0]
        ghost_shares = {s: r["qty"] for s, r in
                        _equity_map(first["positions"]).items() if r["qty"]}
        last_marks = _price_map(first["positions"], first["quotes"])
        cash0 = _f(first["balance"].get("cash"), 0.0) or 0.0
        opt_mark0 = _option_book_mark(first["balance"], first["positions"])
        ghost_cash = cash0 + (opt_mark0 or 0.0)
        if opt_mark0 is None:
            report.notes.append(
                "inception option book unmeasurable — ghost starts at real "
                "cash (gap at inception may not be exactly $0)")
        else:
            report.events.append(
                f"{first['date'].isoformat()}: ghost closes the option book "
                f"at inception marks ({opt_mark0:+,.0f})")

        def ghost_nav_at(marks: dict[str, float]) -> tuple[float, list[str]]:
            nav, stale = ghost_cash, []
            for sym, sh in ghost_shares.items():
                m = marks.get(sym) or last_marks.get(sym)
                if m is None:
                    stale.append(sym)
                    continue
                nav += sh * m
            return nav, stale

        nav0, _ = ghost_nav_at(last_marks)
        prev = first
        prev_gap = first["nav"] - nav0
        report.days.append(GhostDay(first["date"], nav0, first["nav"],
                                    prev_gap, None))

        # ── Walk forward ────────────────────────────────────────────────
        stale_ever: set[str] = set()
        for cur in rows[1:]:
            marks = _price_map(cur["positions"], cur["quotes"])
            prev_marks = dict(last_marks)
            cur_eq = _equity_map(cur["positions"])
            prev_eq = _equity_map(prev["positions"])

            # 1. Option-caused share changes (NOT mirrored).
            assign = _assignment_share_deltas(
                _option_rows(prev["positions"]), _option_rows(cur["positions"]),
                cur["date"], marks, prev_marks)

            # 2. Mirror the remaining real equity trades at today's marks.
            for sym in set(prev_eq) | set(cur_eq):
                dq = (cur_eq.get(sym, {}).get("qty", 0.0)
                      - prev_eq.get(sym, {}).get("qty", 0.0))
                if not dq:
                    continue
                a = assign.get(sym, 0.0)
                if a:
                    # Strip the option-caused portion (same sign, capped).
                    if a > 0:
                        stripped = min(dq, a) if dq > 0 else 0.0
                    else:
                        stripped = max(dq, a) if dq < 0 else 0.0
                    if stripped:
                        report.events.append(
                            f"{cur['date'].isoformat()}: {sym} "
                            f"{stripped:+,.0f} sh option-caused "
                            f"(assignment/called-away) — not mirrored")
                    dq -= stripped
                if not dq:
                    continue
                mark = (marks.get(sym) or prev_marks.get(sym))
                if mark is None:
                    report.notes.append(
                        f"{cur['date'].isoformat()}: {sym} trade of "
                        f"{dq:+,.0f} sh has no mark — mirrored share count "
                        f"only (cash flow unknown)")
                    ghost_shares[sym] = ghost_shares.get(sym, 0.0) + dq
                    continue
                ghost_shares[sym] = ghost_shares.get(sym, 0.0) + dq
                ghost_cash -= dq * mark
                report.events.append(
                    f"{cur['date'].isoformat()}: mirrored {sym} "
                    f"{dq:+,.0f} sh @ ${mark:,.2f}")
            ghost_shares = {s: q for s, q in ghost_shares.items() if q}

            # 3. External flows: real-NAV residual beyond the mark move.
            mkt_move = 0.0
            for sym, row in prev_eq.items():
                p0, p1 = prev_marks.get(sym), marks.get(sym)
                if p0 is not None and p1 is not None:
                    mkt_move += row["qty"] * (p1 - p0)
            om0 = _option_book_mark(prev["balance"], prev["positions"])
            om1 = _option_book_mark(cur["balance"], cur["positions"])
            if om0 is not None and om1 is not None:
                mkt_move += om1 - om0
            residual = (cur["nav"] - prev["nav"]) - mkt_move
            if prev["nav"] > 0 and \
                    abs(residual) > flow_threshold_pct * prev["nav"]:
                ghost_cash += residual
                report.events.append(
                    f"{cur['date'].isoformat()}: external flow "
                    f"{residual:+,.0f} mirrored (real-NAV residual beyond "
                    f"the mark move)")

            # 4. Mark the ghost.
            last_marks.update(marks)
            g_nav, stale = ghost_nav_at(marks)
            stale_ever.update(stale)
            gap = cur["nav"] - g_nav
            report.days.append(GhostDay(cur["date"], g_nav, cur["nav"],
                                        gap, gap - prev_gap))
            prev_gap = gap
            prev = cur

        if stale_ever:
            report.notes.append(
                "carry-forward marks used for: " + ", ".join(sorted(stale_ever)))
        return report
    except Exception as e:  # noqa: BLE001 — fail-open, briefing must ship
        return GhostReport(status="unavailable",
                           notes=[f"ghost compute failed: {e}"])


# ---------------------------------------------------------------------------
# I/O wrappers (fail-open)
# ---------------------------------------------------------------------------

def load_snapshots(snapshot_root: Path) -> list[dict]:
    """Load {date, positions, balance, quotes} from every dated snapshot dir
    (skips ``.test`` and non-date dirs; unreadable files → day skipped)."""
    out: list[dict] = []
    try:
        root = Path(snapshot_root)
        if not root.exists():
            return out
        for child in sorted(root.iterdir()):
            if not child.is_dir() or not _DATE_DIR_RE.match(child.name):
                continue
            snap: dict = {"date": child.name}
            try:
                snap["positions"] = json.loads(
                    (child / "positions.json").read_text(encoding="utf-8"))
                snap["balance"] = json.loads(
                    (child / "balance.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue                      # fail-open: skip the day
            try:
                snap["quotes"] = json.loads(
                    (child / "quotes.json").read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                snap["quotes"] = {}
            out.append(snap)
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] ghost_portfolio: snapshot load failed: {e}",
              file=sys.stderr)
    return out


def state_path(snapshot_root: Path) -> Path:
    """state/ghost_portfolio.json — sibling of briefing_snapshots/."""
    return Path(snapshot_root).parent / "ghost_portfolio.json"


def persist(report: GhostReport, snapshot_root: Path) -> Path | None:
    """Atomically write the series (idempotent full overwrite). None on
    failure — never raises."""
    try:
        path = state_path(snapshot_root)
        payload = dict(report.to_dict())
        payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=1)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return path
    except Exception as e:  # noqa: BLE001
        print(f"    [warn] ghost_portfolio: persist failed: {e}",
              file=sys.stderr)
        return None


def build_ghost_report(snapshot_root: Path,
                       persist_state: bool = True) -> GhostReport:
    """Full recompute from snapshot history (idempotent), optional persist.
    Fail-open: always returns a report; 'unavailable' on any error."""
    try:
        report = compute_ghost_series(load_snapshots(snapshot_root))
        if persist_state and report.status == "ok":
            persist(report, snapshot_root)
        return report
    except Exception as e:  # noqa: BLE001
        return GhostReport(status="unavailable",
                           notes=[f"ghost report failed: {e}"])


# ---------------------------------------------------------------------------
# Summary figures for the renderers
# ---------------------------------------------------------------------------

def summary_figures(report: GhostReport) -> dict:
    """The compact read: total gap since inception + this-month + 7d gap
    moves. Values are None when unmeasurable (never fabricated)."""
    out = {"total_gap": None, "month_gap": None, "week_gap": None,
           "inception": None, "as_of": None}
    if report.status != "ok" or not report.days:
        return out
    latest = report.days[-1]
    out["total_gap"] = latest.gap
    out["inception"] = report.inception.isoformat() if report.inception else None
    out["as_of"] = latest.date.isoformat()
    month_start = latest.date.replace(day=1)
    out["month_gap"] = report.gap_change(month_start)
    out["week_gap"] = report.gap_change(latest.date - timedelta(days=7))
    return out
