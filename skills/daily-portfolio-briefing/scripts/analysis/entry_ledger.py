"""🎓 Entry-grade ledger — a RUNNING score of every option entry.

George (2026-08-12): "can we incorporate these learnings in the daily
briefing? i want to make sure we have a running score of our entries."

The one-shot Entry Timing Audit (``analysis/entry_audit.py``) answered
"were my CURRENT opens timed well?" — this module makes that answer
persistent and daily:

  - ``state/entry_grade_ledger.json`` holds ONE record per contract entry
    (underlying, type, strike, expiration + entry date): the side, the net
    premium, the Setup Grade assigned from ENTRY-DAY conditions, and —
    once the position closes — the measured outcome.
  - **Grades LOCK at first write.** A record's grade is never recomputed
    on later runs: the score reflects the day you traded, not hindsight.
  - Daily maintenance (run_briefing Step 7.6):
      (a) SEED — every currently-open option position not yet in the
          ledger gets its retro grade via the entry-audit logic (archive
          entry-date detection + entry-day snapshot conditions);
      (b) NEW OPENS — each open detected by the position diff
          (``briefing_diff.detect_executed_opens`` / roll STO legs) is
          graded with entry-day conditions and appended (roll legs carry
          ``roll: true``);
      (c) CLOSES — a ledger-open contract absent from today's positions
          gets its outcome filled from the last snapshot mark (labeled
          estimate — rule #19: unmeasurable fields stay None/'n/a').
  - Renders the "## 🎓 Entry Scorecard" panel (running averages, grade
    distribution, week-over-week trend, last 5 entries, discipline
    callouts with config-derived thresholds, and the capture-by-grade
    outcome read once ≥ 3 closed outcomes exist), a one-line 🎓 digest
    summary, and the entry grade on Since-Yesterday detected-open lines.

Config: briefing.yaml → ``entry_scorecard.enabled`` (in-code default
False). Disabled → no ledger writes, no panel, no digest line, no
Since-Yesterday grade lines — byte-identical legacy output.

Fail-open everywhere: any error in maintenance or rendering is a stderr
warning; the briefing always ships.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

try:  # pipeline import context (scripts/ on sys.path)
    from analysis import entry_audit as _ea
    from analysis import rsi_discipline as _rsi_mod
    from analysis import setup_grade as _sg
except ImportError:  # pragma: no cover — direct-script context
    import entry_audit as _ea  # type: ignore
    import rsi_discipline as _rsi_mod  # type: ignore
    import setup_grade as _sg  # type: ignore

LEDGER_VERSION = 1

# Outcome table renders only at/above this many closed, capture-measured
# records (small-sample honesty — below it the panel says so instead).
MIN_OUTCOME_SAMPLE = 3

# Discipline callouts look at the last N graded entries per side and fire
# only when the pattern is REPEATED (x >= _CALLOUT_MIN_HITS out of
# y >= _CALLOUT_MIN_SAMPLE measured entries) — one bad entry is noise.
CALLOUT_WINDOW = 10
_CALLOUT_MIN_HITS = 2
_CALLOUT_MIN_SAMPLE = 3

# Week-over-week trend arrow: |delta| below this many score points → "→".
_TREND_EPS = 3.0

_GRADEABLE = ("A", "A-", "B", "C", "D", "—")


# ── Config / paths ────────────────────────────────────────────────────────


def entry_scorecard_enabled(config: dict | None) -> bool:
    """briefing.yaml → entry_scorecard.enabled — in-code default False
    (flag off → every wired surface is byte-identical legacy)."""
    try:
        return bool(((config or {}).get("entry_scorecard") or {})
                    .get("enabled", False))
    except Exception:
        return False


def default_ledger_path(snapshot_dir=None) -> Path:
    """state/entry_grade_ledger.json — resolved from the snapshot root's
    parent (state/), mirroring ignored_ledger. Fixture/dry-run dirs
    (…/<date>.test) → .test ledger so test runs never touch the real
    record."""
    if snapshot_dir is not None:
        sd = Path(snapshot_dir)
        name = ("entry_grade_ledger.test.json"
                if sd.name.endswith(".test") else "entry_grade_ledger.json")
        return sd.parent.parent / name
    return Path("state/entry_grade_ledger.json")


def load_ledger(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"version": LEDGER_VERSION, "entries": []}


def save_ledger(path: Path, ledger: dict) -> None:
    """Atomic write (tempfile + os.replace) — a crash mid-write can never
    corrupt the running record."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=2)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Small helpers ─────────────────────────────────────────────────────────


def _f(v, default=None):
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _key_str(key) -> str:
    und, typ, strike, exp = key
    return f"{und}|{typ}|{strike:g}|{exp}"


def contract_label(key) -> str:
    """('MU','PUT',950.0,'2026-12-18') → 'MU $950P'."""
    und, typ, strike, _exp = key
    return f"{und} ${strike:g}{'P' if typ == 'PUT' else 'C'}"


def _side_for(pos: dict) -> str:
    typ = str(pos.get("type") or "option").lower()
    return f"{'short' if _ea.is_short(pos) else 'long'}_{typ}"


def _days_between(iso_a, iso_b) -> int | None:
    try:
        a = datetime.strptime(str(iso_a)[:10], "%Y-%m-%d").date()
        b = datetime.strptime(str(iso_b)[:10], "%Y-%m-%d").date()
        return (b - a).days
    except (ValueError, TypeError):
        return None


def _top_driver(grade: dict | None) -> str | None:
    drivers = (grade or {}).get("drivers") or []
    return str(drivers[0]) if drivers else None


def _na_grade(message: str) -> dict:
    return {"letter": "n/a", "score": None, "drivers": [],
            "hard_blocked": False, "message": message}


def _grade_subset(grade: dict | None) -> dict:
    """The locked-at-write grade payload stored on the record."""
    g = grade or {}
    return {
        "letter": g.get("letter"),
        "score": g.get("score"),
        "drivers": list(g.get("drivers") or []),
        "hard_blocked": bool(g.get("hard_blocked")),
        "message": g.get("message"),
    }


# ── Live entry-day conditions (for freshly detected opens) ────────────────


def _live_conditions(ticker: str, snapshot_data: dict | None) -> dict:
    """RSI / vol rank measured from THIS run's snapshot data — the
    entry-day conditions for an open detected today. Unmeasured → None."""
    sd = snapshot_data or {}
    tk = (ticker or "").upper()
    tech = (sd.get("technicals") or {}).get(ticker) \
        or (sd.get("technicals") or {}).get(tk) or {}
    if not isinstance(tech, dict):
        tech = {}
    iv_rank, iv_src = _sg.effective_iv(tk, sd)
    return {"rsi": _f(tech.get("rsi_14")), "iv_rank": _f(iv_rank),
            "iv_source": iv_src}


def _grade_live(pos: dict, key, snapshot_data: dict | None,
                config: dict | None) -> dict:
    """Grade a short open with entry-day (today's snapshot) conditions —
    the EXACT production scorer new trades get. Long legs → n/a."""
    if not _ea.is_short(pos):
        return _na_grade("n/a — hedge/long leg, different objective")
    side = "csp" if key[1] == "PUT" else "cc"
    grade = _sg.grade_for_new_open(
        key[0], side, snapshot_data=snapshot_data or {},
        strike=key[2], config=config)
    if grade is None:
        return _na_grade("n/a — insufficient measured data at entry "
                         "(fail closed)")
    return grade


# ── Record construction ──────────────────────────────────────────────────


def _build_record(pos: dict, key, entry_date: str, *, grade: dict,
                  conditions: dict | None, seeded: bool, roll: bool,
                  before_archive: bool = False) -> dict:
    prem, prem_source = _ea.entry_premium_per_share(pos)
    cond = conditions or {}
    return {
        "id": f"{_key_str(key)}|{entry_date}",
        "contract": {"underlying": key[0], "type": key[1],
                     "strike": key[2], "expiration": key[3]},
        "symbol": pos.get("symbol"),
        "label": contract_label(key),
        "entry_date": entry_date,
        "before_archive": bool(before_archive),
        "side": _side_for(pos),
        "qty": abs(_f(pos.get("qty"), 0.0) or 0.0),
        "premium": prem,
        "premium_source": prem_source,
        "grade": _grade_subset(grade),
        "conditions": {"rsi": _f(cond.get("rsi")),
                       "iv_rank": _f(cond.get("iv_rank")),
                       "iv_source": cond.get("iv_source")},
        "iv_source": cond.get("iv_source"),
        "seeded": bool(seeded),
        "roll": bool(roll),
        "status": "open",
        "outcome": None,
    }


def _fill_outcome(record: dict, prev_positions: list | None,
                  close_date: str, *, roll: bool) -> None:
    """Close a record with the MEASURED outcome from its last snapshot
    sighting (the day before the close). Nothing measurable → None fields
    with the reason (rule #19 — never a guessed fill)."""
    key = tuple([record["contract"]["underlying"],
                 record["contract"]["type"],
                 float(record["contract"]["strike"]),
                 record["contract"]["expiration"]])
    prev = None
    for p in (prev_positions or []):
        if _ea.option_key(p) == key:
            prev = p
            break
    realized = capture = None
    basis = "not measurable (no prior-day mark)"
    if prev is not None:
        realized = _f(prev.get("totalGain"))
        prem = _f(record.get("premium"))
        last_mid = _f(prev.get("currentMid"))
        if prem and last_mid is not None \
                and str(record.get("side", "")).startswith("short"):
            capture = round((prem - last_mid) / prem * 100.0, 1)
        if realized is not None or capture is not None:
            basis = "last snapshot mark before close (estimate, not a fill)"
    record["status"] = "closed"
    record["outcome"] = {
        "close_date": close_date,
        "realized_pnl": realized,
        "capture_pct": capture,
        "days_held": _days_between(record.get("entry_date"), close_date),
        "roll": bool(roll),
        "estimated": True,
        "basis": basis,
    }


# ── Daily maintenance orchestrator ────────────────────────────────────────


def maintain(*, snapshot_dir, snapshot_data: dict, prev_positions,
             today_iso: str, config: dict | None = None,
             ledger_path: Path | None = None,
             snapshots_root: Path | None = None,
             iv_history_path: Path | None = None,
             ohlc_cache_dir: Path | None = None) -> dict | None:
    """The Step 7.6 maintenance pass: close-fill → grade detected opens →
    seed the rest. Returns the update summary (scorecard + per-symbol
    grades for Since Yesterday) or None when the feature is disabled.

    Grade lock: an existing record is NEVER regraded — only ``status`` /
    ``outcome`` ever change after first write.
    """
    if not entry_scorecard_enabled(config):
        return None

    path = Path(ledger_path) if ledger_path else default_ledger_path(snapshot_dir)
    ledger = load_ledger(path)
    entries = ledger.setdefault("entries", [])
    ledger.setdefault("version", LEDGER_VERSION)

    today_positions = (snapshot_data or {}).get("positions") or []
    today_options = {}
    for p in today_positions:
        key = _ea.option_key(p)
        if key is not None:
            today_options[key] = p

    open_by_key = {}
    existing_ids = set()
    for e in entries:
        if not isinstance(e, dict):
            continue
        existing_ids.add(e.get("id"))
        if e.get("status") == "open":
            c = e.get("contract") or {}
            try:
                k = (str(c["underlying"]).upper(), str(c["type"]).upper(),
                     float(c["strike"]), str(c["expiration"]))
            except (KeyError, TypeError, ValueError):
                continue
            open_by_key[k] = e

    changed = False
    closed_n = 0
    new_n = 0
    seeded_n = 0
    new_open_grades: dict = {}

    # Position diff — detected opens + rolls (fail-open on missing prev).
    try:
        from analysis.briefing_diff import (detect_executed_opens,
                                            detect_executed_rolls)
    except ImportError:  # pragma: no cover
        from briefing_diff import (detect_executed_opens,  # type: ignore
                                   detect_executed_rolls)
    rolls = detect_executed_rolls(prev_positions, today_positions) or []
    opens = detect_executed_opens(prev_positions, today_positions) or []
    detected_symbols = {o.get("symbol") for o in opens}
    roll_new_symbols = {r.get("new_symbol") for r in rolls}
    roll_old_symbols = {r.get("old_symbol") for r in rolls}

    # (c) CLOSES — ledger-open contracts absent from today's book. Guarded
    # on a non-empty positions list: an account that failed to load is a
    # data gap, not a mass exit.
    if today_options:
        for k, rec in open_by_key.items():
            if k in today_options:
                continue
            was_roll = False
            for p in (prev_positions or []):
                if _ea.option_key(p) == k:
                    was_roll = str(p.get("symbol") or "") in roll_old_symbols
                    break
            _fill_outcome(rec, prev_positions, today_iso, roll=was_roll)
            closed_n += 1
            changed = True

    # (b) NEW OPENS detected by the diff (fresh shorts + roll STO legs) —
    # graded with entry-day (today's) conditions.
    for k, pos in today_options.items():
        if k in open_by_key:
            continue  # grade already locked — never regraded
        sym = str(pos.get("symbol") or "")
        if sym not in detected_symbols and sym not in roll_new_symbols:
            continue  # handled by the seed pass below
        rid = f"{_key_str(k)}|{today_iso}"
        if rid in existing_ids:
            continue
        grade = _grade_live(pos, k, snapshot_data, config)
        cond = _live_conditions(k[0], snapshot_data)
        rec = _build_record(pos, k, today_iso, grade=grade, conditions=cond,
                            seeded=False, roll=(sym in roll_new_symbols))
        entries.append(rec)
        open_by_key[k] = rec
        existing_ids.add(rid)
        new_n += 1
        changed = True
        if grade.get("letter") not in (None, "n/a"):
            new_open_grades[sym] = {
                "letter": grade.get("letter"),
                "score": grade.get("score"),
                "top_driver": _top_driver(grade),
                "label": rec["label"],
            }

    # (a) SEED — every remaining open option position not in the ledger,
    # retro-graded via the entry-audit archive logic. Archive access is
    # lazy: skipped entirely when nothing needs seeding.
    unseeded = [(k, p) for k, p in today_options.items()
                if k not in open_by_key]
    if unseeded:
        root = Path(snapshots_root) if snapshots_root \
            else Path(snapshot_dir).parent
        skill_state = Path(snapshot_dir).parent.parent if snapshot_dir \
            else Path("state")
        if iv_history_path is None:
            iv_history_path = skill_state / "chain_iv_history.json"
        if ohlc_cache_dir is None:
            _repo_guess = Path(__file__).resolve().parents[4] \
                / "state" / "cache" / "ohlc"
            ohlc_cache_dir = _repo_guess if _repo_guess.is_dir() else None
        dates = [d for d in _ea.snapshot_dates(root) if d <= today_iso]
        positions_by_date = {d: _ea.load_positions(root, d) for d in dates}
        iv_history = None
        try:
            iv_history = json.loads(
                Path(iv_history_path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            iv_history = None
        for k, pos in unseeded:
            try:
                entry_date, before = (
                    _ea.find_entry_date(k, dates, positions_by_date)
                    if dates else (None, False))
                if entry_date is None:
                    # Not in the archive at all (first run / .test dir) —
                    # entry is today; grade from the live snapshot.
                    grade = _grade_live(pos, k, snapshot_data, config)
                    cond = _live_conditions(k[0], snapshot_data)
                    rec = _build_record(pos, k, today_iso, grade=grade,
                                        conditions=cond, seeded=True,
                                        roll=False)
                elif before:
                    rec = _build_record(
                        pos, k, entry_date,
                        grade=_na_grade("opened before the archive — "
                                        "conditions unavailable"),
                        conditions=None, seeded=True, roll=False,
                        before_archive=True)
                else:
                    cond = _ea.entry_conditions(
                        root, entry_date, k[0], iv_history, ohlc_cache_dir)
                    # Premium from the ENTRY-day record when available.
                    entry_pos = pos
                    for p2 in positions_by_date.get(entry_date, []):
                        if _ea.option_key(p2) == k:
                            entry_pos = p2
                            break
                    grade = _ea.retro_grade(pos, cond, config)
                    rec = _build_record(entry_pos, k, entry_date,
                                        grade=grade, conditions=cond,
                                        seeded=True, roll=False)
                    rec["symbol"] = pos.get("symbol")
                if rec["id"] in existing_ids:
                    continue
                entries.append(rec)
                open_by_key[k] = rec
                existing_ids.add(rec["id"])
                seeded_n += 1
                changed = True
            except Exception as _se:  # one bad position never stops the pass
                import sys as _sys
                print(f"[entry-ledger] seed failed for {k}: {_se}",
                      file=_sys.stderr)

    if changed:
        save_ledger(path, ledger)

    scorecard = compute_scorecard(ledger, today_iso, config)
    return {
        "enabled": True,
        "ledger_path": str(path),
        "seeded": seeded_n,
        "new_opens": new_n,
        "closed": closed_n,
        "total": len(entries),
        "new_open_grades": new_open_grades,
        "scorecard": scorecard,
    }


# ── Scorecard math ────────────────────────────────────────────────────────


def _avg_block(scores: list, config: dict | None) -> dict | None:
    if not scores:
        return None
    avg = sum(scores) / len(scores)
    return {"avg": round(avg, 1), "letter": _sg.letter_for(avg, config),
            "n": len(scores)}


def _bucket(letter: str) -> str:
    if letter in ("A", "A-", "B"):
        return "A/B"
    if letter == "C":
        return "C"
    return "D"  # D and hard-blocked '—'


def compute_scorecard(ledger: dict, today_iso: str,
                      config: dict | None = None) -> dict:
    """Every number the panel / digest / webapp renders — measured from
    the ledger, thresholds from config (rule #19)."""
    entries = [e for e in (ledger.get("entries") or [])
               if isinstance(e, dict)]
    entries.sort(key=lambda e: str(e.get("entry_date") or ""))
    graded = [e for e in entries
              if _f((e.get("grade") or {}).get("score")) is not None]

    def _score(e):
        return float(e["grade"]["score"])

    try:
        today = datetime.strptime(str(today_iso)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        today = None

    def _within(e, lo_days, hi_days) -> bool:
        if today is None:
            return False
        d = _days_between(e.get("entry_date"), today_iso)
        return d is not None and lo_days <= d < hi_days

    last10 = graded[-10:]
    last30 = [e for e in graded if _within(e, 0, 30)]
    averages = {
        "last_10": _avg_block([_score(e) for e in last10], config),
        "last_30d": _avg_block([_score(e) for e in last30], config),
        "all_time": _avg_block([_score(e) for e in graded], config),
    }

    distribution: dict = {}
    for e in graded:
        letter = e["grade"]["letter"]
        distribution[letter] = distribution.get(letter, 0) + 1

    # Week-over-week trend — needs at least one graded entry in EACH
    # window, else None (never an arrow on missing data).
    this_week = [_score(e) for e in graded if _within(e, 0, 7)]
    prior_week = [_score(e) for e in graded if _within(e, 7, 14)]
    trend = None
    if this_week and prior_week:
        a = sum(this_week) / len(this_week)
        b = sum(prior_week) / len(prior_week)
        arrow = ("↗" if a - b >= _TREND_EPS
                 else "↘" if b - a >= _TREND_EPS else "→")
        trend = {"this_week": round(a, 1), "prior_week": round(b, 1),
                 "n_this": len(this_week), "n_prior": len(prior_week),
                 "arrow": arrow}

    last5 = [{
        "label": e.get("label"),
        "entry_date": e.get("entry_date"),
        "letter": e["grade"]["letter"],
        "score": e["grade"]["score"],
        "top_driver": _top_driver(e.get("grade")),
        "roll": bool(e.get("roll")),
    } for e in reversed(graded[-5:])]

    callouts = _discipline_callouts(graded, config)

    # Outcomes — closed, graded, with a measured capture.
    closed = [e for e in graded if e.get("status") == "closed"
              and _f((e.get("outcome") or {}).get("capture_pct")) is not None]
    outcome: dict = {"n": len(closed), "min_sample": MIN_OUTCOME_SAMPLE,
                     "by_bucket": None}
    if len(closed) >= MIN_OUTCOME_SAMPLE:
        buckets: dict = {}
        for e in closed:
            buckets.setdefault(_bucket(e["grade"]["letter"]), []).append(
                float(e["outcome"]["capture_pct"]))
        outcome["by_bucket"] = {
            b: {"avg_capture_pct": round(sum(v) / len(v), 1), "n": len(v)}
            for b, v in buckets.items()}

    # Open book (George 2026-08-13: "where in the briefing i can see the
    # entries grades for my current options") — grade distribution + avg
    # over the CURRENTLY OPEN graded entries only, so the scorecard
    # answers the question at a glance. None when nothing open+graded.
    open_graded = [e for e in graded if e.get("status") == "open"]
    open_book = None
    if open_graded:
        ob_dist: dict = {}
        for e in open_graded:
            letter = e["grade"]["letter"]
            ob_dist[letter] = ob_dist.get(letter, 0) + 1
        ob_avg = sum(_score(e) for e in open_graded) / len(open_graded)
        _order = {g: i for i, g in enumerate(_GRADEABLE)}
        ob_sorted = sorted(ob_dist.items(),
                           key=lambda kv: _order.get(kv[0], 99))
        open_book = {
            "n": len(open_graded),
            "avg": round(ob_avg, 1),
            "letter": _sg.letter_for(ob_avg, config),
            "distribution": dict(ob_sorted),
            "line": (" · ".join(f"{v} {k}" for k, v in ob_sorted)
                     + f" · avg {ob_avg:.0f}/100"),
        }

    sc = {
        "as_of": today_iso,
        "total": len(entries),
        "graded": len(graded),
        "open": sum(1 for e in entries if e.get("status") == "open"),
        "closed": sum(1 for e in entries if e.get("status") == "closed"),
        "open_book": open_book,
        "averages": averages,
        "distribution": dict(sorted(distribution.items())),
        "trend": trend,
        "last_5": last5,
        "callouts": callouts,
        "outcome": outcome,
    }
    sc["digest_line"] = _digest_line(sc)
    return sc


def _discipline_callouts(graded: list, config: dict | None) -> list[str]:
    """Measured repeat-pattern callouts over the last CALLOUT_WINDOW
    entries per side. Every threshold in the text is CONFIG-derived
    (setup_grade vol floor, rsi_discipline bands) — rule #19."""
    cfg = _sg.load_setup_grade_config(config)
    th = _rsi_mod.load_thresholds(config)
    vol_floor = float(cfg["vol_floor_rank"])
    put_lo, put_hi = _rsi_mod.put_entry_band(th)
    cc_favored = float((th.get("call") or {}).get("favored_above", 60.0))

    puts = [e for e in graded if e.get("side") == "short_put"][-CALLOUT_WINDOW:]
    calls = [e for e in graded if e.get("side") == "short_call"][-CALLOUT_WINDOW:]
    out: list[str] = []

    def _measured(pool, field):
        vals = []
        for e in pool:
            v = _f((e.get("conditions") or {}).get(field))
            if v is not None:
                vals.append(v)
        return vals

    ivs = _measured(puts, "iv_rank")
    if len(ivs) >= _CALLOUT_MIN_SAMPLE:
        x = sum(1 for v in ivs if v < vol_floor)
        if x >= _CALLOUT_MIN_HITS:
            out.append(f"{x} of last {len(ivs)} put entries had IV/RV rank "
                       f"< {vol_floor:.0f} — thin premium (selling cheap)")
    rsis = _measured(puts, "rsi")
    if len(rsis) >= _CALLOUT_MIN_SAMPLE:
        x = sum(1 for v in rsis if v < put_lo or v > put_hi)
        if x >= _CALLOUT_MIN_HITS:
            out.append(f"{x} of last {len(rsis)} put entries had RSI outside "
                       f"the {put_lo:.0f}-{put_hi:.0f} entry band — sell "
                       f"weakness, not strength")
    cc_rsis = _measured(calls, "rsi")
    if len(cc_rsis) >= _CALLOUT_MIN_SAMPLE:
        x = sum(1 for v in cc_rsis if v < cc_favored)
        if x >= _CALLOUT_MIN_HITS:
            out.append(f"{x} of last {len(cc_rsis)} covered-call entries "
                       f"were written below RSI {cc_favored:.0f} — mid-band "
                       f"writes cap upside for average premium")
    return out


# ── Rendering ─────────────────────────────────────────────────────────────


def _digest_line(sc: dict) -> str | None:
    """'🎓 Entry quality: last 10 avg C (52) ↘ · last entry MU $950P — D
    (RVr 34 thin ✗)' — every clause measured, absent when unmeasured."""
    l10 = (sc.get("averages") or {}).get("last_10")
    if not l10:
        return None
    bits = [f"🎓 Entry quality: last 10 avg {l10['letter']} "
            f"({l10['avg']:.0f})"]
    trend = sc.get("trend")
    if trend:
        bits[0] += f" {trend['arrow']}"
    last5 = sc.get("last_5") or []
    if last5:
        le = last5[0]
        seg = f"last entry {le['label']} — {le['letter']}"
        if le.get("top_driver"):
            seg += f" ({le['top_driver']})"
        bits.append(seg)
    return " · ".join(bits)


def render_scorecard_panel(sc: dict | None) -> list[str]:
    """'## 🎓 Entry Scorecard' — rendered in the benchmark zone of the
    FULL briefing. Empty when nothing is graded yet (never an empty
    shell)."""
    if not sc or not sc.get("graded"):
        return []
    lines = ["## 🎓 Entry Scorecard", ""]
    dl = sc.get("digest_line")
    if dl:
        lines.append(f"**{dl}**")
        lines.append("")
    lines.append(
        "_Running score of every option entry (George 2026-08-12), graded "
        "on ENTRY-DAY conditions by the production Setup Grade scorer and "
        "LOCKED at first write — the score reflects the day you traded. "
        "Ledger: state/entry_grade_ledger.json. Unmeasured fields are "
        "n/a, never fabricated (rule #19)._")
    lines.append("")

    def _ab(block):
        if not block:
            return "n/a"
        return f"**{block['letter']}** ({block['avg']:.0f}/100, n={block['n']})"

    av = sc.get("averages") or {}
    lines.append(f"- **Running averages:** last 10: {_ab(av.get('last_10'))}"
                 f" · last 30d: {_ab(av.get('last_30d'))}"
                 f" · all-time: {_ab(av.get('all_time'))}")
    # George 2026-08-13 — "where in the briefing i can see the entries
    # grades for my current options": one-line open-book read; the
    # per-position detail lives on each Watch row (🎓 tokens).
    ob = sc.get("open_book")
    if ob:
        lines.append("- **Current open positions by entry grade:** "
                     f"Open book: {ob['line']} (n={ob['n']}) — "
                     "per-position 🎓 tokens on the Watch rows")
    dist = sc.get("distribution") or {}
    if dist:
        lines.append("- **Grade distribution:** "
                     + " · ".join(f"{k}: {v}" for k, v in dist.items()))
    trend = sc.get("trend")
    if trend:
        lines.append(
            f"- **This week vs prior:** {trend['this_week']:.0f} "
            f"(n={trend['n_this']}) vs {trend['prior_week']:.0f} "
            f"(n={trend['n_prior']}) {trend['arrow']}")
    last5 = sc.get("last_5") or []
    if last5:
        lines.append("- **Last 5 entries:**")
        for le in last5:
            seg = (f"  - {le['label']} · {le['entry_date']} · "
                   f"**{le['letter']}**")
            if _f(le.get("score")) is not None:
                seg += f" ({le['score']:.0f}/100)"
            if le.get("top_driver"):
                seg += f" — {le['top_driver']}"
            if le.get("roll"):
                seg += " · roll"
            lines.append(seg)
    for c in sc.get("callouts") or []:
        lines.append(f"- **Discipline callout:** {c}")
    oc = sc.get("outcome") or {}
    if oc.get("by_bucket"):
        parts = []
        for b in ("A/B", "C", "D"):
            v = oc["by_bucket"].get(b)
            if v:
                parts.append(f"{b}: {v['avg_capture_pct']:+.0f}% avg capture "
                             f"(n={v['n']})")
        lines.append("- **Outcomes (capture % by grade, closed entries):** "
                     + " · ".join(parts))
        lines.append("  - _Capture from last snapshot marks (estimates, "
                     "not fills) — correlation, not causation._")
    elif oc.get("n", 0) > 0:
        lines.append(f"- **Outcomes:** outcome sample too small "
                     f"(n={oc['n']}) — capture table renders at "
                     f"{oc.get('min_sample', MIN_OUTCOME_SAMPLE)}+ closed "
                     f"entries")
    lines.append("")
    return lines


# ── Watch-panel entry tokens (George 2026-08-13) ─────────────────────────
# "where in the briefing i can see the entries grades for my current
# options" — every option row/block in the Watch panel carries a compact
# 🎓 token looked up from the ledger by the SAME canonical contract key
# the maintenance pass uses. All fail-open, never fabricated (rule #19).


def resolve_ledger_path(snapshot_data: dict | None) -> Path:
    """Ledger path for a READ-ONLY lookup: the live run's maintenance
    stash first, then the snapshot dir, then the cwd-relative default."""
    up = (snapshot_data or {}).get("entry_ledger_update")
    if isinstance(up, dict) and up.get("ledger_path"):
        return Path(up["ledger_path"])
    sd = (snapshot_data or {}).get("_snapshot_dir")
    if sd:
        return default_ledger_path(sd)
    return default_ledger_path(None)


def open_grade_index(snapshot_data: dict | None,
                     config: dict | None = None) -> dict | None:
    """One-shot, config-gated ledger load for the Watch panel.

    Returns None when entry_scorecard is disabled (legacy byte-identical)
    OR the ledger file exists but is unreadable/corrupt (fail-open: no
    tokens at all — a broken ledger must not paint every row 'n/a').
    A missing/empty ledger → {} (readable absence: full blocks render an
    explicit `🎓 entry n/a`, rule #19)."""
    cfg = config if config is not None \
        else (snapshot_data or {}).get("_config")
    if not entry_scorecard_enabled(cfg):
        return None
    try:
        path = resolve_ledger_path(snapshot_data)
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError:
            return {}  # no ledger yet — readable absence
        try:
            data = json.loads(text)
        except ValueError:
            return None  # corrupt file — no tokens, no crash
        if not (isinstance(data, dict)
                and isinstance(data.get("entries"), list)):
            return None
        idx: dict = {}
        for e in data.get("entries") or []:
            if not isinstance(e, dict) or e.get("status") != "open":
                continue
            c = e.get("contract") or {}
            try:
                k = (str(c["underlying"]).upper(), str(c["type"]).upper(),
                     float(c["strike"]), str(c["expiration"]))
            except (KeyError, TypeError, ValueError):
                continue
            idx[k] = e
        return idx
    except Exception:
        return {}


def review_contract_key(review: dict | None):
    """Canonical contract key from a Watch options-review dict — the same
    (UND, TYPE, strike, ISO-exp) tuple ``entry_audit.option_key`` builds
    from a position, so the ledger lookup can never drift."""
    r = review or {}
    und = r.get("underlying") or (
        str(r.get("contract") or "").split("_")[0])
    typ = str(r.get("type") or "").upper()
    strike = r.get("strike")
    exp = str(r.get("expiration") or "")[:10]
    if not und or typ not in ("PUT", "CALL") or not strike or not exp:
        return None
    try:
        return (str(und).upper(), typ, float(strike), exp)
    except (TypeError, ValueError):
        return None


def _entry_date_short(iso) -> str | None:
    try:
        d = datetime.strptime(str(iso)[:10], "%Y-%m-%d")
        return f"{d.strftime('%b')} {d.day}"
    except (ValueError, TypeError):
        return None


def _clean_driver(text: str) -> str:
    """Driver text minus the ✓/✗ marks — parenthetical-compact, never
    rephrased (the measured driver string is the source of truth)."""
    return " ".join(str(text).replace("✓", "").replace("✗", "").split())


def watch_entry_token(record: dict | None, *, full: bool) -> str | None:
    """Compact 🎓 token for one Watch option row.

    - graded record → ``🎓 entry D (30) · Aug 5``; the FULL block adds the
      locked top driver: ``🎓 entry D (30, RSI 60 off-band) · Aug 5``
    - record graded n/a (hedge/long leg, before-archive, insufficient
      data) → full block only: ``🎓 entry n/a — hedge/long leg`` (the
      ledger's own convention); one-liners stay clean
    - no ledger record → full block only: ``🎓 entry n/a``
    Rule #19: every value is the LOCKED ledger record — never recomputed,
    never fabricated."""
    if record is None:
        return "🎓 entry n/a" if full else None
    g = record.get("grade") or {}
    letter = g.get("letter")
    score = _f(g.get("score"))
    if letter in (None, "n/a") or score is None:
        if not full:
            return None
        if "hedge" in str(g.get("message") or ""):
            return "🎓 entry n/a — hedge/long leg"
        return "🎓 entry n/a"
    token = f"🎓 entry {letter} ({score:.0f}"
    if full:
        td = _top_driver(g)
        if td:
            token += f", {_clean_driver(td)}"
    token += ")"
    ed = _entry_date_short(record.get("entry_date"))
    if ed:
        token += f" · {ed}"
    return token
