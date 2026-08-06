"""Ignored-rec forward ledger (task #44) — 🤖 vs 🧠.

Fill reconciliation (analysis/rec_aging.py, Step 7.4) already tags every
prior action EXECUTED / IGNORED / PARTIAL / UNVERIFIED daily. This module
gives those dispositions a MEMORY: every action resolved EXECUTED or
IGNORED is appended to ``state/rec_outcome_ledger.json`` with its rec-day
mark, then forward-marked at +7d / +30d on subsequent runs so the briefing
can answer "do the recommendations actually make money, and does George
beat them by ignoring?"

Honesty rules (hard rule #19):
  - Marks are SNAPSHOT marks (currentMid / position price), never fills —
    every forward mark carries ``estimated: true`` and the panel says so.
  - A contract that closed/vanished before its horizon gets ``mark: null``
    with the reason — excluded from averages, counted as unscored.
  - Sample below 20 scored recs → the panel renders the "insufficient
    sample — directional only" caveat.

Outcome semantics (scored for SHORT-option entries — the book's bread and
butter; other sides are recorded but unscored):
  - EXECUTED close-rec value = (later mark − rec mark) × 100/contract:
    positive = the option got MORE expensive after the close → closing was
    right; negative = closed early, decay was left on the table.
  - IGNORED close-rec would-have = (rec mark − later mark) × 100/contract:
    positive = holding banked the decay (ignoring worked); negative = the
    rec would have saved that much.

Fail-open everywhere: any error → no panel, briefing ships.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

MIN_SAMPLE = 20
HORIZONS = (7, 30)


def default_ledger_path(snapshot_dir=None) -> Path:
    """state/rec_outcome_ledger.json — resolved from the snapshot root's
    parent (state/). Fixture/dry-run dirs (…/<date>.test) → .test ledger so
    test runs never advance the real record."""
    if snapshot_dir is not None:
        sd = Path(snapshot_dir)
        name = ("rec_outcome_ledger.test.json"
                if sd.name.endswith(".test") else "rec_outcome_ledger.json")
        return sd.parent.parent / name
    return Path("state/rec_outcome_ledger.json")


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


def _f(v, default=None):
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _days_between(iso_a: str, iso_b: str) -> int | None:
    try:
        a = datetime.strptime(str(iso_a)[:10], "%Y-%m-%d").date()
        b = datetime.strptime(str(iso_b)[:10], "%Y-%m-%d").date()
        return (b - a).days
    except (ValueError, TypeError):
        return None


def _find_position(ident: str, positions: list | None) -> dict | None:
    ident = str(ident or "").upper()
    if not ident:
        return None
    for p in (positions or []):
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or "").upper()
        if sym == ident or (sym.startswith(ident + "_")
                            and p.get("assetType") == "OPTION"):
            return p
    return None


def _side_for(pos: dict | None, ident: str) -> str:
    """short_put / short_call / long_put / long_call / equity / unknown."""
    try:
        from analysis.rec_aging import _parse_contract
    except ImportError:  # pragma: no cover — standalone use
        _parse_contract = lambda _i: None  # noqa: E731
    if pos is not None and pos.get("assetType") == "OPTION":
        qty = _f(pos.get("qty"), 0.0) or 0.0
        typ = str(pos.get("type") or "").lower() or "option"
        return f"{'short' if qty < 0 else 'long'}_{typ}"
    if pos is not None and pos.get("assetType") == "EQUITY":
        return "equity"
    c = _parse_contract(ident) if ident else None
    if c:
        return f"unknown_{c['type'].lower()}"
    return "equity" if ident and "_" not in ident else "unknown"


def _mark_for(ident: str, positions: list | None) -> tuple[float | None, str]:
    """(per-share mark, basis). Options → currentMid; equities → price.
    Missing position / missing mark → (None, reason) — never estimated."""
    pos = _find_position(ident, positions)
    if pos is None:
        return None, "position not in snapshot (closed/expired?)"
    if pos.get("assetType") == "OPTION":
        m = _f(pos.get("currentMid"))
        if m is None:
            m = _f(pos.get("current_price"))
        return (m, "option currentMid (snapshot)") if m is not None \
            else (None, "no mark on snapshot position")
    m = _f(pos.get("price")) or _f(pos.get("lastTrade"))
    return (m, "equity price (snapshot)") if m is not None \
        else (None, "no mark on snapshot position")


# ── Recording ────────────────────────────────────────────────────────────

def record_dispositions(ledger: dict, reconciliation: dict | None,
                        prev_positions: list | None,
                        prev_date: str | None) -> int:
    """Append one ledger entry per action resolved EXECUTED or IGNORED on
    ``prev_date`` (the rec day). PARTIAL/UNVERIFIED are not outcomes —
    skipped. Deduped on (rec day, action key). Returns entries added."""
    if not reconciliation or not prev_date:
        return 0
    entries = ledger.setdefault("entries", [])
    existing = {e.get("id") for e in entries if isinstance(e, dict)}
    added = 0
    for key, status in reconciliation.items():
        if status not in ("EXECUTED", "IGNORED"):
            continue
        eid = f"{prev_date}:{key}"
        if eid in existing:
            continue
        existing.add(eid)
        kind, _, ident = str(key).partition(":")
        pos = _find_position(ident, prev_positions)
        mark, basis = _mark_for(ident, prev_positions)
        entries.append({
            "id": eid,
            "date": prev_date,
            "key": key,
            "kind": kind,
            "ident": ident,
            "side": _side_for(pos, ident),
            "disposition": status,
            "rec_mark": mark,
            "rec_mark_basis": basis,
            "marks": {},
        })
        added += 1
    return added


def forward_mark(ledger: dict, today_positions: list | None,
                 today_iso: str) -> int:
    """Stamp the +7d / +30d snapshot marks on entries whose horizon has
    arrived. A mark records the first run AT/after the horizon (approximate
    by design — labeled estimate). Returns marks written."""
    written = 0
    for e in ledger.get("entries") or []:
        if not isinstance(e, dict):
            continue
        days = _days_between(e.get("date"), today_iso)
        if days is None:
            continue
        marks = e.setdefault("marks", {})
        for h in HORIZONS:
            k = f"{h}d"
            if days < h or k in marks:
                continue
            mark, basis = _mark_for(e.get("ident"), today_positions)
            rec: dict = {"date": today_iso, "mark": mark, "basis": basis,
                         "estimated": True, "days_actual": days}
            rec_mark = _f(e.get("rec_mark"))
            if mark is not None and rec_mark:
                rec["change_ps"] = round(mark - rec_mark, 2)
                rec["change_pct"] = round((mark - rec_mark) / rec_mark * 100, 1)
            marks[k] = rec
            written += 1
    return written


# ── Stats + panel ────────────────────────────────────────────────────────

def _scored_value(entry: dict, horizon_key: str) -> float | None:
    """Per-contract dollar outcome at a horizon, for SHORT-option entries.

    EXECUTED → (later − rec) × 100 (positive = closing was right).
    IGNORED  → (rec − later) × 100 (positive = holding/ignoring paid).
    """
    if not str(entry.get("side") or "").startswith("short"):
        return None
    m = (entry.get("marks") or {}).get(horizon_key) or {}
    change_ps = _f(m.get("change_ps"))
    if change_ps is None:
        return None
    if entry.get("disposition") == "EXECUTED":
        return change_ps * 100.0
    return -change_ps * 100.0


def compute_stats(ledger: dict, horizon: int = 30) -> dict:
    hk = f"{horizon}d"
    stats = {"horizon": hk,
             "executed": {"count": 0, "scored": 0, "avg_value": None,
                          "values": []},
             "ignored": {"count": 0, "scored": 0, "avg_value": None,
                         "values": []},
             "unscored": 0, "total": 0}
    for e in ledger.get("entries") or []:
        if not isinstance(e, dict):
            continue
        disp = e.get("disposition")
        group = ("executed" if disp == "EXECUTED"
                 else "ignored" if disp == "IGNORED" else None)
        if group is None:
            continue
        stats["total"] += 1
        stats[group]["count"] += 1
        v = _scored_value(e, hk)
        if v is None:
            stats["unscored"] += 1
        else:
            stats[group]["scored"] += 1
            stats[group]["values"].append(v)
    for g in ("executed", "ignored"):
        vals = stats[g].pop("values")
        if vals:
            stats[g]["avg_value"] = round(sum(vals) / len(vals), 2)
    return stats


def render_outcomes_panel(ledger: dict, as_of: str | None = None) -> list[str]:
    """'## 🤖 vs 🧠 — Recommendation Outcomes' — rendered in the benchmark
    section. Empty when the ledger has no entries."""
    entries = [e for e in (ledger.get("entries") or []) if isinstance(e, dict)]
    if not entries:
        return []
    s30 = compute_stats(ledger, 30)
    s7 = compute_stats(ledger, 7)
    lines = ["## 🤖 vs 🧠 — Recommendation Outcomes", ""]
    lines.append(
        "_Forward-marked ledger of every prior action resolved EXECUTED or "
        "IGNORED (`state/rec_outcome_ledger.json`). All outcomes are "
        "ESTIMATES from snapshot marks — never fills._")
    lines.append("")

    def _usd(v: float) -> str:
        return f"-${abs(v):,.0f}" if v < 0 else f"+${v:,.0f}"

    def _grp(name: str, g: dict, reading: str) -> str:
        bit = f"- **{name}:** {g['count']} rec(s)"
        if g["avg_value"] is not None:
            bit += (f" · avg 30d outcome {_usd(g['avg_value'])}/contract "
                    f"({reading}; {g['scored']} scored)")
        else:
            bit += " · no 30d-scored outcomes yet"
        return bit

    lines.append(_grp(
        "Executed", s30["executed"],
        "positive = closing before the move was right"))
    lines.append(_grp(
        "Ignored", s30["ignored"],
        "would-have: positive = holding/ignoring the rec paid"))
    scored_total = s30["executed"]["scored"] + s30["ignored"]["scored"]
    if s7["executed"]["scored"] + s7["ignored"]["scored"] > scored_total:
        e7, i7 = s7["executed"], s7["ignored"]

        def _fmt7(v):
            if v is None:
                return "n/a"
            return f"-${abs(v):,.0f}" if v < 0 else f"+${v:,.0f}"

        lines.append(
            f"- 7d read: executed {_fmt7(e7['avg_value'])} · ignored "
            f"{_fmt7(i7['avg_value'])} per contract (earlier, noisier "
            f"horizon)")
    if scored_total < MIN_SAMPLE:
        lines.append(
            f"- ⚠ **insufficient sample** ({scored_total} scored < "
            f"{MIN_SAMPLE}) — directional only, not evidence")
    if s30["unscored"]:
        lines.append(
            f"- _{s30['unscored']} entr"
            f"{'y' if s30['unscored'] == 1 else 'ies'} unscored at 30d "
            f"(mark unavailable / not a short option / horizon not "
            f"reached)_")
    lines.append("")
    return lines


def update_and_render(snapshot_dir, aging_info: dict | None,
                      today_positions: list | None, today_iso: str,
                      ledger_path: Path | None = None) -> list[str]:
    """One-call orchestrator for aggregate: record yesterday's dispositions,
    forward-mark due horizons, persist, and return the panel lines."""
    path = ledger_path or default_ledger_path(snapshot_dir)
    ledger = load_ledger(path)
    changed = 0
    if aging_info:
        changed += record_dispositions(
            ledger, aging_info.get("reconciliation"),
            aging_info.get("prev_positions"), aging_info.get("prev_date"))
    changed += forward_mark(ledger, today_positions, today_iso)
    if changed:
        save_ledger(path, ledger)
    return render_outcomes_panel(ledger, as_of=today_iso)
