"""P/L attribution — where the return came from, snapshot-to-snapshot.

Task #16. The full trade log is NOT available inline, so attribution is
inferred from position deltas between two snapshots (positions.json +
balance.json). Every inferred number is an ESTIMATE (sale/buyback prices
are proxied by snapshot marks or cost basis) — the residual bucket exists
so estimate error and data inconsistency are visible instead of silently
absorbed. A large ``unattributed`` value means something broke: surface it.

Buckets (per period; economic reads, inferred):
  realized_equity     equity qty reductions, P/L vs cost basis (sale px
                      proxied at the PRIOR snapshot's mark)
  unrealized_equity   mark-to-market on held equities (current qty × Δprice;
                      new buys marked vs entry cost)
  option_premium_net  cash from newly opened short options (premiumReceived)
                      minus estimated buyback cost of closed shorts (prior
                      mark); expired-worthless shorts cost nothing to close
  option_mtm          Δ mark on short options held across both snapshots.
                      Since the 2026-08-04 NLV correction (and the
                      recompute_nlv_history.py migration of older
                      snapshots), NLV includes signed option marks — so the
                      option-mark delta DOES feed ΔNLV and is part of the
                      explained change in the residual reconciliation below.
  assignment_pnl      detected put assignments: shares × (spot − strike).
                      Estimate; also keeps that contract's premium.
  hedge_pnl           long options (protective puts / LEAPs): Δ mark on held
                      + prior-mark realization on disappeared longs. Fuzzy:
                      without an is_hedge flag, directional long LEAPs land
                      here too — renderer labels it "Hedge / long-option P/L".
  interest_dividends  cash change not explained by inferred trades
                      (dividends, interest, fees + trade-price estimate error
                      — not separable further without transaction history)

Reconciliation:
  ΔNLV(balance) = cash_change + Δequity_MV(positions) + unattributed
  where unattributed captures balance-vs-positions inconsistency (skipped
  prices, rounding, scope drift). Buckets themselves are estimates and are
  NOT forced to sum to ΔNLV — the renderer says so.

Cash drag (separate — opportunity cost, not a P/L bucket):
  avg short-put collateral (Σ strike × 100 × |qty|) across the period's
  snapshots × SPY return over the period: "what that collateral would have
  earned parked in SPY."

Fail-open everywhere: any exception yields an 'unavailable' report and the
briefing still ships (user rule: stability and no bugs).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from analysis.benchmark_tracker import (
    SNAPSHOT_TOLERANCE_DAYS,
    _coerce_date,
    _DATE_DIR_RE,
    balance_nlv,
    balance_option_inclusive,
    clean_nlv_history,
    effective_rebase_date,
    load_nlv_history_meta,
    nearest_value,
    rebase_dates_from_config,
)

BUCKET_KEYS = [
    "realized_equity",
    "unrealized_equity",
    "option_premium_net",
    "option_mtm",
    "assignment_pnl",
    "hedge_pnl",
    "interest_dividends",
]


@dataclass
class PeriodAttribution:
    name: str
    start_date: date | None = None
    end_date: date | None = None
    nlv_start: float | None = None
    nlv_end: float | None = None
    nlv_change: float | None = None
    buckets: dict = field(default_factory=lambda: {k: 0.0 for k in BUCKET_KEYS})
    unattributed: float = 0.0
    cash_drag: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    # Daily period only (2026-08-13 fix): the PREVIOUS snapshot-to-snapshot
    # window's premium, so the renderer can say honestly WHERE new-open
    # premium was counted when today's window shows $0 (opens captured by an
    # intraday rerun of the prior snapshot land in the prior window).
    prior_window: dict = field(default_factory=dict)
    # Chained periods (2026-08-14): how many snapshot-to-snapshot windows
    # were summed — lets the renderer derive the residual honestly ("cash
    # residual chain-summed across N windows"), so a large
    # interest_dividends value is self-explaining instead of alarming.
    n_windows: int | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "n_windows": self.n_windows,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "nlv_start": round(self.nlv_start, 2) if self.nlv_start is not None else None,
            "nlv_end": round(self.nlv_end, 2) if self.nlv_end is not None else None,
            "nlv_change": round(self.nlv_change, 2) if self.nlv_change is not None else None,
            "buckets": {k: round(v, 2) for k, v in self.buckets.items()},
            "unattributed": round(self.unattributed, 2),
            "cash_drag": self.cash_drag,
            "notes": list(self.notes),
            "prior_window": dict(self.prior_window),
        }


@dataclass
class AttributionReport:
    """status: 'ok' | 'insufficient_history' | 'unavailable'."""

    status: str
    as_of: date | None = None
    periods: list = field(default_factory=list)  # list[PeriodAttribution]
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "periods": [p.to_dict() for p in self.periods],
            "note": self.note,
        }

    def period(self, name: str):
        for p in self.periods:
            if p.name == name:
                return p
        return None


# ---------------------------------------------------------------------------
# Position helpers
# ---------------------------------------------------------------------------

def _f(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if f == f else default  # NaN guard
    except (TypeError, ValueError):
        return default


def _split_positions(positions: list) -> tuple[dict, dict]:
    """→ (equities_by_symbol, options_by_contract). Ignores malformed rows."""
    eq: dict[str, dict] = {}
    op: dict[str, dict] = {}
    for p in positions or []:
        if not isinstance(p, dict) or not p.get("symbol"):
            continue
        if p.get("assetType") == "EQUITY":
            eq[str(p["symbol"]).upper()] = p
        elif p.get("assetType") == "OPTION":
            op[str(p["symbol"]).upper()] = p
    return eq, op


def _is_hedge(pos: dict) -> bool:
    """Long options are hedges/directional longs (protective puts, LEAPs).

    Explicit ``is_hedge`` flag wins when present. Otherwise: positionType
    LONG or qty > 0.
    """
    if pos.get("is_hedge") is not None:
        return bool(pos.get("is_hedge"))
    if str(pos.get("positionType") or "").upper() == "LONG":
        return True
    return _f(pos.get("qty")) > 0


def _option_mark(pos: dict) -> float:
    """Signed market value of an option position (negative = liability)."""
    mv = pos.get("marketValue")
    if mv is not None:
        return _f(mv)
    return _f(pos.get("currentMid")) * 100.0 * _f(pos.get("qty"))


def _option_entry_cost(pos: dict) -> float:
    """Per-contract entry premium (per share) — premiumReceived preferred,
    costPerShare fallback."""
    prem = pos.get("premiumReceived")
    if prem is not None and _f(prem) != 0:
        return _f(prem)
    return _f(pos.get("costPerShare"))


def put_collateral(positions: list) -> float:
    """Σ strike × 100 × |qty| across short puts — cash-secured obligation."""
    total = 0.0
    for p in positions or []:
        if (isinstance(p, dict) and p.get("assetType") == "OPTION"
                and str(p.get("type") or "").upper() == "PUT"
                and _f(p.get("qty")) < 0):
            total += _f(p.get("strike")) * 100.0 * abs(_f(p.get("qty")))
    return total


# ---------------------------------------------------------------------------
# Core: two-snapshot attribution
# ---------------------------------------------------------------------------

def compute_attribution(current_snapshot: dict, prior_snapshot: dict,
                        name: str = "period") -> PeriodAttribution:
    """Decompose the NLV change between two snapshots into buckets.

    Snapshots are ``{"date": iso-str|date, "positions": [...],
    "balance": {...}}`` — exactly what the pipeline persists per day.
    Never raises (fail-open: returns whatever it could attribute, with
    the rest in ``unattributed`` / ``notes``).
    """
    pa = PeriodAttribution(name=name)
    try:
        pa.start_date = _coerce_date((prior_snapshot or {}).get("date"))
        pa.end_date = _coerce_date((current_snapshot or {}).get("date"))

        cur_bal = (current_snapshot or {}).get("balance") or {}
        pri_bal = (prior_snapshot or {}).get("balance") or {}
        # 2026-08-05 defect 1: prefer accountValue_corrected (option-mark-
        # inclusive recompute) so pre-correction inflated NLVs never mix
        # with broker-true NLVs inside one period.
        pa.nlv_start = _f(balance_nlv(pri_bal))
        pa.nlv_end = _f(balance_nlv(cur_bal))
        pa.nlv_change = pa.nlv_end - pa.nlv_start
        cash_change = _f(cur_bal.get("cash")) - _f(pri_bal.get("cash"))

        cur_eq, cur_op = _split_positions((current_snapshot or {}).get("positions") or [])
        pri_eq, pri_op = _split_positions((prior_snapshot or {}).get("positions") or [])

        b = pa.buckets

        # ── Assignment detection: short put gone, expiration inside the
        # period, underlying equity qty jumped by ≥ 100×|qty| ──────────────
        # assigned_map[symbol] = {"shares": n, "strike_cash": $ paid at strike}
        assigned_map: dict[str, dict] = {}
        for c, pos in pri_op.items():
            if c in cur_op or _f(pos.get("qty")) >= 0:
                continue
            if str(pos.get("type") or "").upper() != "PUT":
                continue
            exp = _coerce_date(pos.get("expiration"))
            if pa.end_date and exp and exp > pa.end_date:
                continue  # not expired — early close, handled as buyback below
            und = str(pos.get("underlying") or "").upper()
            if not und:
                continue
            shares = 100.0 * abs(_f(pos.get("qty")))
            prev_q = _f(pri_eq.get(und, {}).get("qty"))
            cur_q = _f(cur_eq.get(und, {}).get("qty"))
            if cur_q - prev_q >= shares - 1e-6:
                strike = _f(pos.get("strike"))
                spot = _f(cur_eq.get(und, {}).get("price"))
                entry = assigned_map.setdefault(und, {"shares": 0.0, "strike_cash": 0.0,
                                                      "contracts": []})
                entry["shares"] += shares
                entry["strike_cash"] += shares * strike
                entry["contracts"].append(c)
                b["assignment_pnl"] += shares * (spot - strike)
                b["option_premium_net"] += _option_entry_cost(pos) * shares
                pa.notes.append(
                    f"assignment inferred: {und} {abs(_f(pos.get('qty'))):.0f}x "
                    f"${strike:g}P → {shares:.0f} sh (estimate)")

        # ── Equities ────────────────────────────────────────────────────
        # inferred_cash tracks the estimated cash side of every inferred
        # trade (so interest_dividends is a clean residual of the CASH
        # ledger). delta_equity_mv is the exact positions-based MV change,
        # used for the balance-vs-positions residual below.
        inferred_cash = 0.0
        delta_equity_mv = 0.0
        for sym in set(cur_eq) | set(pri_eq):
            cq = _f(cur_eq.get(sym, {}).get("qty"))
            pq = _f(pri_eq.get(sym, {}).get("qty"))
            cp = _f(cur_eq.get(sym, {}).get("price"))
            pp = _f(pri_eq.get(sym, {}).get("price"))
            delta_equity_mv += cq * cp - pq * pp
            if sym in cur_eq and sym in pri_eq:
                # Exact algebra: ΔMV = cq·(cp−pp) + (cq−pq)·pp
                b["unrealized_equity"] += cq * (cp - pp)
                dq = cq - pq
                if dq < 0:  # sold — proceeds proxied at prior mark
                    sold = -dq
                    basis = _f(pri_eq[sym].get("costBasis"), pp)
                    b["realized_equity"] += sold * (pp - basis)
                    inferred_cash += sold * pp
                elif dq > 0:  # bought (possibly via assignment)
                    a = assigned_map.get(sym, {})
                    assigned = min(_f(a.get("shares")), dq)
                    bought = dq - assigned
                    if assigned > 0:
                        frac = assigned / _f(a.get("shares"), assigned)
                        inferred_cash -= _f(a.get("strike_cash")) * frac
                    if bought > 0:
                        cost = _f(cur_eq[sym].get("costBasis"), cp)
                        inferred_cash -= bought * (cost if cost > 0 else cp)
            elif sym in pri_eq:  # fully exited
                basis = _f(pri_eq[sym].get("costBasis"), pp)
                b["realized_equity"] += pq * (pp - basis)
                inferred_cash += pq * pp
                pa.notes.append(f"{sym}: full exit inferred at prior mark (estimate)")
            else:  # brand-new position this period
                a = assigned_map.get(sym, {})
                assigned = min(_f(a.get("shares")), cq)
                bought = cq - assigned
                cost = _f(cur_eq[sym].get("costBasis"), cp)
                buy_px = cost if cost > 0 else cp
                assigned_cash = 0.0
                if assigned > 0:
                    frac = assigned / _f(a.get("shares"), assigned)
                    assigned_cash = _f(a.get("strike_cash")) * frac
                entry_mv = assigned_cash + bought * buy_px
                b["unrealized_equity"] += cq * cp - entry_mv
                inferred_cash -= entry_mv

        # ── Options ─────────────────────────────────────────────────────
        for c in sorted(set(cur_op) | set(pri_op)):
            cur = cur_op.get(c)
            pri = pri_op.get(c)
            if cur is not None and pri is not None:
                delta_mark = _option_mark(cur) - _option_mark(pri)
                if _is_hedge(cur):
                    b["hedge_pnl"] += delta_mark
                else:
                    b["option_mtm"] += delta_mark
            elif cur is not None:  # newly opened this period
                qty_abs = abs(_f(cur.get("qty")))
                if _is_hedge(cur):
                    cost = _option_entry_cost(cur) * 100.0 * qty_abs
                    inferred_cash -= cost
                    b["hedge_pnl"] += _option_mark(cur) - cost
                else:
                    prem = _option_entry_cost(cur) * 100.0 * qty_abs
                    b["option_premium_net"] += prem
                    inferred_cash += prem
            else:  # disappeared this period
                if _f(pri.get("qty")) < 0:
                    und = str(pri.get("underlying") or "").upper()
                    if (str(pri.get("type") or "").upper() == "PUT"
                            and c in (assigned_map.get(und, {}).get("contracts") or [])):
                        continue  # already handled as assignment
                    exp = _coerce_date(pri.get("expiration"))
                    if exp and pa.end_date and exp <= pa.end_date:
                        # Expired worthless: liability vanished for free —
                        # the remaining mark was earned.
                        b["option_premium_net"] += -_option_mark(pri)
                        pa.notes.append(f"{c}: expired worthless (inferred)")
                    else:
                        # Bought back early: cost proxied at prior mark.
                        buyback = -_option_mark(pri)  # positive cost
                        b["option_premium_net"] -= buyback
                        inferred_cash -= buyback
                        pa.notes.append(f"{c}: buyback inferred at prior mark (estimate)")
                else:
                    # Long option gone: sold or expired — proceeds proxied at
                    # prior mark (conservative: no incremental P/L this period).
                    inferred_cash += _option_mark(pri)
                    pa.notes.append(f"{c}: long option closed at prior mark (estimate)")

        # ── Interest / dividends: cash not explained by inferred trades ──
        b["interest_dividends"] = cash_change - inferred_cash

        # ── Residual vs pipeline ΔNLV ────────────────────────────────────
        # When BOTH endpoint NLVs are option-mark-inclusive (broker-true era
        # or migrated via accountValue_corrected), the explained change is
        #   cash_change + Δequity_MV + Δoption_MV(positions)
        # — omitting the option-mark delta was the 2026-08-05 defect: the
        # briefing showed "Unattributed (residual): -$65,345 ⚠️ (large
        # residual — balance vs positions disagree; investigate)" purely
        # because the start NLV excluded short-option marks while the end
        # NLV included them. Legacy (both-exclusive) periods keep the old
        # formula; a MIXED period gets an explicit note — never silent.
        delta_option_mv = (
            sum(_option_mark(p) for p in cur_op.values())
            - sum(_option_mark(p) for p in pri_op.values())
        )
        cur_inclusive = balance_option_inclusive(cur_bal)
        pri_inclusive = balance_option_inclusive(pri_bal)
        explained = cash_change + delta_equity_mv
        if cur_inclusive and pri_inclusive:
            explained += delta_option_mv
        elif cur_inclusive != pri_inclusive:
            pa.notes.append(
                "NLV conventions differ across this period (one endpoint "
                "excludes option marks) — residual includes the option-mark "
                "gap; run scripts/recompute_nlv_history.py to correct the "
                "older snapshot")
        pa.unattributed = pa.nlv_change - explained
        return pa
    except Exception as e:  # noqa: BLE001 — fail-open
        pa.notes.append(f"attribution failed: {e}")
        return pa


def compute_chained_attribution(snapshots: list, name: str = "period",
                                max_notes: int = 30) -> PeriodAttribution:
    """Chain-sum consecutive snapshot-pair attributions across a period.

    ONE SOURCE OF TRUTH (2026-08-13 fix). Multi-day period buckets were
    computed endpoint-to-endpoint (inception snapshot vs latest snapshot),
    while the "Since last snapshot" line measured the last snapshot PAIR.
    Two different measurements meant the two surfaces could not reconcile:

      - a contract opened AND closed inside the period is absent from both
        endpoints, so its premium/buyback simply vanished from the
        cumulative ``option_premium_net`` (the 38 August round-trip closes
        contributed nothing);
      - every still-open contract landed 100% in ``option_premium_net`` at
        its full entry premium with ``option_mtm`` stuck at $0 since
        inception (the observed 2026-08-13 panel: "Option premium (net):
        +$39,858 · Option mark-to-market delta: +$0");
      - the day-over-day change of the cumulative bucket therefore did NOT
        equal the daily line (observed: cumulative absorbed the new
        SOXL/GOOG opens' premium while "Since last snapshot: … premium
        +$0" rendered — reads as a false zero).

    Chaining sums the SAME per-day (snapshot-to-snapshot) measurements the
    daily line shows, so ``cum(as of D) − cum(as of D−1) == daily(D)`` for
    every bucket, by construction. NLV endpoints telescope and are kept
    endpoint-based. Notes are capped at ``max_notes`` (+ an honest "+N
    more" marker). Fail-open like compute_attribution.
    """
    if not snapshots or len(snapshots) < 2:
        pa = PeriodAttribution(name=name)
        if snapshots:
            pa.start_date = pa.end_date = _coerce_date(snapshots[0].get("date"))
        return pa
    total = PeriodAttribution(name=name)
    try:
        total.start_date = _coerce_date(snapshots[0].get("date"))
        total.end_date = _coerce_date(snapshots[-1].get("date"))
        total.nlv_start = _f(balance_nlv((snapshots[0].get("balance") or {})))
        total.nlv_end = _f(balance_nlv((snapshots[-1].get("balance") or {})))
        total.nlv_change = total.nlv_end - total.nlv_start
        notes: list[str] = []
        total.n_windows = len(snapshots) - 1
        for prior, cur in zip(snapshots, snapshots[1:]):
            link = compute_attribution(cur, prior, name=f"{name}_link")
            for k in BUCKET_KEYS:
                total.buckets[k] += link.buckets.get(k, 0.0)
            total.unattributed += link.unattributed
            notes.extend(link.notes)
        if len(notes) > max_notes:
            extra = len(notes) - max_notes
            notes = notes[:max_notes] + [
                f"… +{extra} more inferred-trade note(s) across the chain"]
        total.notes = notes
        total.notes.append(
            f"buckets chain-summed across {len(snapshots) - 1} "
            f"snapshot-to-snapshot windows — the same per-day measurement "
            f"as the daily line (one source of truth)")
    except Exception as e:  # noqa: BLE001 — fail-open
        total.notes.append(f"chained attribution failed: {e}")
    return total


# ---------------------------------------------------------------------------
# Snapshot loading + multi-period report
# ---------------------------------------------------------------------------

def load_snapshot(snapshot_dir: Path) -> dict | None:
    """{"date", "positions", "balance"} from one dated dir. None on failure."""
    try:
        d = Path(snapshot_dir)
        positions = json.loads((d / "positions.json").read_text(encoding="utf-8"))
        balance = json.loads((d / "balance.json").read_text(encoding="utf-8"))
        if not isinstance(positions, list) or not isinstance(balance, dict):
            return None
        return {"date": d.name, "positions": positions, "balance": balance}
    except Exception:  # noqa: BLE001
        return None


def _snapshot_dates(snapshot_root: Path) -> list[date]:
    out = []
    try:
        for child in Path(snapshot_root).iterdir():
            if child.is_dir() and _DATE_DIR_RE.match(child.name):
                d = _coerce_date(child.name)
                if d and (child / "balance.json").exists():
                    out.append(d)
    except OSError:
        pass
    return sorted(out)


def _cash_drag(snapshot_root: Path, dates_in_period: list[date],
               spy_closes: dict, start: date, end: date) -> dict:
    """Opportunity cost of put collateral vs SPY over the period."""
    try:
        collaterals = []
        for d in dates_in_period:
            snap = load_snapshot(Path(snapshot_root) / d.isoformat())
            if snap:
                collaterals.append(put_collateral(snap["positions"]))
        if not collaterals:
            return {}
        avg_collateral = sum(collaterals) / len(collaterals)
        spy = {k: v for k, v in
               ((_coerce_date(kk), vv) for kk, vv in (spy_closes or {}).items())
               if k is not None}
        s = nearest_value(spy, start, 5)
        e = nearest_value(spy, end, 5)
        spy_ret = None
        if s and e and s[0] != e[0] and s[1]:
            spy_ret = (e[1] / s[1] - 1.0) * 100.0
        return {
            "avg_put_collateral": round(avg_collateral, 2),
            "spy_return_pct": round(spy_ret, 3) if spy_ret is not None else None,
            "opportunity_cost": (round(avg_collateral * spy_ret / 100.0, 2)
                                 if spy_ret is not None else None),
        }
    except Exception:  # noqa: BLE001
        return {}


def build_attribution_report(snapshot_root: Path, as_of=None,
                             spy_closes: dict | None = None,
                             min_snapshots: int = 2,
                             config: dict | None = None) -> AttributionReport:
    """Daily / 30d / since-month-end / YTD attribution from the snapshot
    store. Fail-open: 'unavailable' report on any error.

    ``config`` is the benchmark_tracking section — reads ``nlv_rebase_dates``
    (belt-and-suspenders: only fires when the NLV-correction migration could
    not cover the full history)."""
    try:
        as_of_d = _coerce_date(as_of) or date.today()
        dates = [d for d in _snapshot_dates(snapshot_root) if d <= as_of_d]
        report = AttributionReport(status="ok", as_of=as_of_d)
        hist_all, uncorrected = load_nlv_history_meta(snapshot_root)
        hist_all = {d: v for d, v in hist_all.items() if d <= as_of_d}
        # Configured rebase (2026-08-04 NLV correction): applies ONLY when an
        # uncorrectable pre-rebase snapshot remains in the series.
        rb = effective_rebase_date(hist_all, uncorrected,
                                   rebase_dates_from_config(config))
        if rb is not None:
            dropped_rb = sum(1 for d in hist_all if d < rb)
            hist_all = {d: v for d, v in hist_all.items() if d >= rb}
            dates = [d for d in dates if d >= rb]
            report.note = (f"NLV rebase applied at {rb.isoformat()} — "
                           f"{dropped_rb} uncorrectable pre-correction "
                           f"snapshot(s) excluded")
        # Exclude history before an NLV discontinuity (deposit / account-scope
        # change) — attribution across such a break is garbage. Same guard as
        # the benchmark side; visible via the note, never silent.
        cleaned, disc_note = clean_nlv_history(hist_all)
        if disc_note:
            dates = [d for d in dates if d in cleaned]
            report.note = ((report.note + "; " if report.note else "")
                           + disc_note)
        if len(dates) < min_snapshots:
            report.status = "insufficient_history"
            report.note = f"only {len(dates)} snapshot(s) — need ≥ {min_snapshots}"
            return report

        latest = dates[-1]
        cur = load_snapshot(Path(snapshot_root) / latest.isoformat())
        if cur is None:
            report.status = "unavailable"
            report.note = "latest snapshot unreadable"
            return report
        hist_map = {d: d for d in dates}

        def _period(name: str, target_start: date) -> None:
            if target_start >= latest:
                return
            snap_d = nearest_value(hist_map, target_start, SNAPSHOT_TOLERANCE_DAYS)
            if snap_d is not None:
                start_d = snap_d[0]
            else:
                # Month/year boundaries can land on long weekends — take the
                # first snapshot AFTER the target instead of dropping the period.
                after = [d for d in dates if d >= target_start]
                if not after:
                    return
                start_d = after[0]
            if start_d >= latest:
                return
            # 2026-08-13 fix: chain-sum the per-day (snapshot-to-snapshot)
            # attributions across the period instead of one endpoint diff —
            # the same measurement the daily line uses, so the cumulative
            # buckets and "Since last snapshot" reconcile by construction.
            period_dates = [d for d in dates if start_d <= d <= latest]
            snaps = []
            for d in period_dates:
                s = load_snapshot(Path(snapshot_root) / d.isoformat())
                if s is not None:
                    snaps.append(s)
            if len(snaps) < 2:
                return
            pa = compute_chained_attribution(snaps, name=name)
            pa.cash_drag = _cash_drag(
                snapshot_root, period_dates,
                spy_closes or {}, start_d, latest)
            report.periods.append(pa)

        # Daily: previous snapshot → latest.
        if len(dates) >= 2:
            prior_d = dates[-2]
            prior = load_snapshot(Path(snapshot_root) / prior_d.isoformat())
            if prior is not None:
                pa = compute_attribution(cur, prior, name="daily")
                pa.cash_drag = _cash_drag(snapshot_root, [prior_d, latest],
                                          spy_closes or {}, prior_d, latest)
                # Timing-artifact context (2026-08-13): opens executed
                # intraday on the prior date are captured by the prior
                # snapshot (possibly an intraday rerun) and counted in the
                # PRIOR window — today's honest $0 must be able to say so.
                if len(dates) >= 3:
                    prior2 = load_snapshot(
                        Path(snapshot_root) / dates[-3].isoformat())
                    if prior2 is not None:
                        pw = compute_attribution(prior, prior2,
                                                 name="prior_window")
                        pa.prior_window = {
                            "start_date": dates[-3].isoformat(),
                            "end_date": prior_d.isoformat(),
                            "option_premium_net": round(
                                pw.buckets.get("option_premium_net", 0.0), 2),
                        }
                report.periods.append(pa)

        _period("30d", latest - timedelta(days=30))
        _period("MTD", latest.replace(day=1) - timedelta(days=1))
        # YTD: earliest snapshot of the year if history reaches back that
        # far; otherwise label honestly as since-inception.
        jan1 = date(latest.year, 1, 1)
        if dates[0] <= jan1 + timedelta(days=10):
            _period("YTD", jan1)
        else:
            _period("since_inception", dates[0])
            report.note = ((report.note + "; " if report.note else "")
                           + f"YTD unavailable — history starts "
                             f"{dates[0].isoformat()}; showing since-inception")
        return report
    except Exception as e:  # noqa: BLE001
        return AttributionReport(status="unavailable",
                                 note=f"attribution report failed: {e}")
