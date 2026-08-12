"""Recommendation Grade Audit — retro-grade every option ticket the last
N briefings RECOMMENDED, and answer whether following them lands in A/B.

George (2026-08-12): "Can you look at briefings, say, for the last five
different briefings, and see what the recommendations make sense? If I
were to listen to those recommendations, I would get into the A or B
category. ... I clearly see that some recommendations recommend selling
CSPs where our [RSI] is 50/50. That doesn't sound like a good idea. Check
that we really need to validate it. Now that you have this entry time
audit table, see how recommendations make sense and grade those
recommendations."

Mirror of ``analysis/entry_audit.py`` (which grades the trades George
TOOK); this grades the trades the briefing PROPOSED:

1. EXTRACT every recommended option ticket from each audited briefing —
   prefer the structured ``briefing_<date>.json`` (income opportunities /
   new_ideas, LT_CSP + LEAP entries, CC writes incl. index CC, rotation
   playbook opens, strangle/collar legs); fall back to the full
   markdown's stable ticket pattern for the Candidate Trades cards
   (which exist only in the markdown). Record the shown status AT THE
   TIME (⏸ capacity-deferred, RSI wait, blocked, actionable).
2. GRADE each ticket with the production Setup Grade scorer
   (``analysis/setup_grade.py`` csp_setup / cc_setup) on THAT DAY's own
   snapshot conditions — the same resolution logic as the entry audit's
   retro grading (``entry_audit.entry_conditions`` is reused, not
   duplicated). Missing inputs follow the module's renormalize/cap
   rules; ungradeable recs render 'n/a' with the reason (rule #19 —
   nothing fabricated).
3. AGGREGATE: grade distribution overall and by surface, the specific
   "50/50 RSI" and thin-vol pattern counts, a cross-reference against
   the entry-grade ledger (which recs George actually TOOK), and the
   honest "would following the recs put you in A/B?" verdict.

Everything reads data already on disk — NO live fetches.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

try:  # pipeline import context (scripts/ on sys.path)
    from analysis import entry_audit as _ea
    from analysis import setup_grade as _sg
except ImportError:  # pragma: no cover — direct-script context
    import entry_audit as _ea  # type: ignore
    import setup_grade as _sg  # type: ignore

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# "SELL 1× MU $795P exp **Fri Sep 11 '26** (32 DTE)" (also
# "(37 DTE, monthly)") — the Candidate Trades / LT-CSP ticket pattern.
_TICKET_RE = re.compile(
    r"SELL\s+1×\s+([A-Z]+)\s+\$(\d+(?:\.\d+)?)([PC])\s+exp\s+"
    r"\*{0,2}([A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+'\d{2})\*{0,2}"
    r"\s+\((\d+)\s+DTE[^)]*\)")
# LEAP buy: "BUY 1× CCL $25C exp Fri Aug 20 '27 (379 DTE) ..."
_LEAP_RE = re.compile(
    r"BUY\s+1×\s+([A-Z]+)\s+\$(\d+(?:\.\d+)?)([PC])\s+exp\s+"
    r"\*{0,2}([A-Za-z]{3}\s+[A-Za-z]{3}\s+\d{1,2}\s+'\d{2})\*{0,2}")
_CAND_HDR_RE = re.compile(r"\*\*🎯 CANDIDATE · `([A-Z]+)`")
_CAND_SECTION_RE = re.compile(r"^## 🎯 Candidate Trades.*?$", re.M)
_RSI_RE = re.compile(r"\bRSI\s+(\d+(?:\.\d+)?)\b")

_DEFERRED_MARK = "Deferred (capacity gated)"

# The "50/50 problem" threshold George called out: a CSP recommendation
# at/above this RSI is selling into a coin-flip, not weakness.
RSI_5050_THRESHOLD = 48.0
# Thin-vol pattern threshold — matches setup_grade's vol_floor_rank
# default (the rank at/below which the vol component scores 0).
THIN_VOL_RANK = 40.0

_AB_LETTERS = ("A", "A-", "B")
# Pattern-check ticket lists cap at this many example lines in the
# report ("… and N more") — the counts are always complete.
MAX_PATTERN_TICKETS = 15

STATUS_LABELS = {
    "actionable": "actionable",
    "deferred_capacity": "⏸ deferred (capacity)",
    "rsi_wait": "⏸ RSI wait",
    "rsi_blocked": "⛔ RSI block",
    "earnings_blocked": "⛔ earnings block",
    "concentration_blocked": "⛔ concentration",
    "lt_wait": "⏸ LT-trend wait",
    "hard_skip": "⛔ hard-skip (shown for planning)",
    "skipped": "⛔ skipped",
}

SURFACE_LABELS = {
    "candidate": "Candidate Trades (scout)",
    "income_opportunity": "Income Opportunities (PULLBACK CSP)",
    "lt_csp": "Long-Term Opportunities (LT_CSP)",
    "leap_call": "Long-Term Opportunities (LEAP call)",
    "cc_write": "Strategy Upgrades (CC write)",
    "index_cc": "Strategy Upgrades (index CC)",
    "strangle_put": "Strategy Upgrades (strangle put add)",
    "collar_put": "Strategy Upgrades (collar protective put)",
    "playbook": "Rotation Playbook (open)",
}


# ── Date discovery ────────────────────────────────────────────────────────


def audit_dates(briefings_dir: Path, snapshots_root: Path,
                limit: int = 5) -> list[str]:
    """Last ``limit`` dates having BOTH a briefing artifact
    (briefing_<date>.json or briefing_full_<date>.md) AND a snapshot dir
    with a technicals.json (the retro-grade input)."""
    briefings_dir = Path(briefings_dir)
    out = []
    for snap_date in _ea.snapshot_dates(Path(snapshots_root)):
        has_artifact = (
            (briefings_dir / f"briefing_{snap_date}.json").exists()
            or (briefings_dir / f"briefing_full_{snap_date}.md").exists())
        has_tech = (Path(snapshots_root) / snap_date
                    / "technicals.json").exists()
        if has_artifact and has_tech:
            out.append(snap_date)
    return out[-limit:]


def _load_json(path: Path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# ── Ticket parsing helpers ────────────────────────────────────────────────


def _exp_iso(pretty: str) -> str | None:
    """"Fri Sep 11 '26" → "2026-09-11". Unparseable → None (rule #19)."""
    try:
        return datetime.strptime(pretty.strip(), "%a %b %d '%y") \
            .strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def parse_ticket(text: str) -> dict | None:
    """Parse a "SELL 1× TKR $NNNP exp Fri Mon DD 'YY (NN DTE)" ticket.
    Returns {ticker, opt_type, strike, expiration, dte} or None."""
    m = _TICKET_RE.search(text or "")
    if not m:
        return None
    return {
        "ticker": m.group(1),
        "opt_type": "PUT" if m.group(3) == "P" else "CALL",
        "strike": float(m.group(2)),
        "expiration": _exp_iso(m.group(4)),
        "dte": int(m.group(5)),
    }


def _rec(date: str, surface: str, side: str, ticker, opt_type,
         strike, expiration, *, ticket: str, status: str,
         shown_rsi=None, shown_grade=None) -> dict:
    return {
        "date": date, "surface": surface, "side": side,
        "ticker": (str(ticker) or "").upper(), "opt_type": opt_type,
        "strike": (float(strike) if strike is not None else None),
        "expiration": expiration, "ticket": ticket, "status": status,
        "shown_rsi": shown_rsi, "shown_grade": shown_grade,
    }


# ── JSON extraction ───────────────────────────────────────────────────────


def _lt_status(op: dict) -> str:
    reasons = " ".join(str(r) for r in (op.get("trigger_reasons") or []))
    if op.get("skip_reason"):
        return "skipped"
    if "hard-skip" in reasons:
        return "hard_skip"
    if op.get("rsi_wait"):
        return "rsi_wait"
    if "capacity gated" in reasons or op.get("capacity_deferred"):
        return "deferred_capacity"
    return "actionable"


def _cc_status(up: dict) -> str:
    if up.get("earnings_blocked"):
        return "earnings_blocked"
    if up.get("rsi_blocked"):
        return "rsi_blocked"
    if up.get("rsi_wait"):
        return "rsi_wait"
    if up.get("lt_secular_wait"):
        return "lt_wait"
    return "actionable"


def extract_json_recs(briefing: dict, date: str) -> list[dict]:
    """Every recommended option ticket carried by the structured
    briefing JSON. Placeholders (capacity banner rows, tier_a_no_cc
    transparency records, un-writable index CCs) are NOT recs."""
    recs: list[dict] = []
    b = briefing if isinstance(briefing, dict) else {}

    # Income opportunities / PULLBACK CSPs (new_ideas).
    for idea in b.get("new_ideas") or []:
        if not isinstance(idea, dict):
            continue
        if idea.get("source") == "capacity_gates_blocked" \
                or idea.get("strike") is None:
            continue  # the capacity banner row is not a recommendation
        if idea.get("rsi_blocked"):
            status = "rsi_blocked"
        elif idea.get("rsi_wait"):
            status = "rsi_wait"
        elif idea.get("capacity_blocked"):
            status = "deferred_capacity"
        else:
            status = "actionable"
        strike = idea.get("strike")
        recs.append(_rec(
            date, "income_opportunity", "csp", idea.get("ticker"), "PUT",
            strike, idea.get("expiration"),
            ticket=f"SELL 1× {idea.get('ticker')} ${strike:g}P "
                   f"exp {idea.get('expiration')}",
            status=status, shown_rsi=idea.get("rsi_14"),
            shown_grade=idea.get("setup_grade")))

    # Long-term opportunities: LT CSPs + LEAP calls.
    for op in b.get("long_term_opportunities") or []:
        if not isinstance(op, dict):
            continue
        kind = (op.get("kind") or "").upper()
        ct = str(op.get("concrete_trade") or "")
        if kind == "LONG_DATED_CSP":
            parsed = parse_ticket(ct)
            if not parsed:
                continue
            shown_rsi = None
            m = _RSI_RE.search(" ".join(
                str(r) for r in (op.get("trigger_reasons") or [])))
            if m:
                shown_rsi = float(m.group(1))
            recs.append(_rec(
                date, "lt_csp", "csp", op.get("ticker") or parsed["ticker"],
                parsed["opt_type"], parsed["strike"], parsed["expiration"],
                ticket=ct.split("·")[0].strip(), status=_lt_status(op),
                shown_rsi=shown_rsi))
        elif kind == "LEAP_CALL":
            m = _LEAP_RE.search(ct)
            recs.append(_rec(
                date, "leap_call", "long_call", op.get("ticker"),
                "CALL", (float(m.group(2)) if m else None),
                (_exp_iso(m.group(4)) if m else None),
                ticket=ct[:70] or f"BUY LEAP CALL {op.get('ticker')}",
                status=_lt_status(op)))

    # Strategy upgrades: CC writes, index CCs, strangle put adds,
    # collar protective puts.
    for up in b.get("strategy_upgrades") or []:
        if not isinstance(up, dict):
            continue
        t = up.get("type")
        und = up.get("underlying")
        if t == "write_covered_call" and up.get("target_strike") is not None:
            strike = up["target_strike"]
            dte = up.get("target_dte")
            n = up.get("contracts_writable") or 1
            recs.append(_rec(
                date, "cc_write", "cc", und, "CALL", strike, None,
                ticket=f"SELL {n}× {und} ${strike:g}C"
                       + (f" ({dte} DTE)" if dte is not None else ""),
                status=_cc_status(up), shown_rsi=up.get("rsi_14"),
                shown_grade=up.get("setup_grade")))
        elif t == "index_covered_call":
            if not up.get("writable") or up.get("target_strike") is None:
                continue  # un-writable index CC carries no ticket
            strike = up["target_strike"]
            dte = up.get("target_dte")
            recs.append(_rec(
                date, "index_cc", "cc", und, "CALL", strike, None,
                ticket=f"SELL {up.get('contracts_writable') or 1}× {und} "
                       f"${strike:g}C"
                       + (f" ({dte} DTE)" if dte is not None else ""),
                status=_cc_status(up), shown_rsi=up.get("rsi_14"),
                shown_grade=up.get("setup_grade")))
        elif t == "covered_strangle":
            p = up.get("proposed") or {}
            if p.get("strike") is None:
                continue
            if up.get("rsi_blocked"):
                status = "rsi_blocked"
            elif (up.get("concentration_check") or {}).get("blocked"):
                status = "concentration_blocked"
            else:
                status = "actionable"
            recs.append(_rec(
                date, "strangle_put", "csp", und, "PUT", p["strike"],
                p.get("expiration"),
                ticket=f"SELL {p.get('qty') or 1}× {und} "
                       f"${float(p['strike']):g}P exp {p.get('expiration')}",
                status=status, shown_rsi=up.get("rsi_14"),
                shown_grade=up.get("setup_grade")))
        elif t == "collar":
            p = up.get("proposed_put") or {}
            if p.get("strike") is None:
                continue
            recs.append(_rec(
                date, "collar_put", "long_put", und, "PUT", p["strike"],
                p.get("expiration"),
                ticket=f"BUY {p.get('qty') or 1}× {und} "
                       f"${float(p['strike']):g}P exp {p.get('expiration')}",
                status="actionable", shown_rsi=up.get("rsi_14")))

    # Rotation playbook composed opens (rare; defensive parse).
    rp = b.get("rotation_playbook") or {}
    for op in (rp.get("opens") or []) if isinstance(rp, dict) else []:
        if not isinstance(op, dict):
            continue
        tk = op.get("ticker") or op.get("underlying")
        strike = op.get("strike")
        if not tk or strike is None:
            continue
        typ = (op.get("opt_type") or op.get("type") or "PUT").upper()
        recs.append(_rec(
            date, "playbook", ("csp" if typ == "PUT" else "cc"), tk, typ,
            strike, op.get("expiration"),
            ticket=f"SELL 1× {tk} ${float(strike):g}"
                   f"{'P' if typ == 'PUT' else 'C'} "
                   f"exp {op.get('expiration')}",
            status="actionable", shown_rsi=op.get("rsi_14"),
            shown_grade=op.get("setup_grade")))
    return recs


# ── Markdown extraction (Candidate Trades cards — markdown-only) ─────────


def extract_candidate_recs(md: str, date: str) -> list[dict]:
    """Candidate Trades entry tickets from the full briefing markdown —
    the one recommendation surface that never lands in the JSON.
    Only the section between '## 🎯 Candidate Trades' and the next H2 is
    scanned, so LT-CSP / thin-premium 'SELL $245P' lines elsewhere are
    never double-counted."""
    m = _CAND_SECTION_RE.search(md or "")
    if not m:
        return []
    rest = md[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    section = rest[:nxt.start()] if nxt else rest
    recs: list[dict] = []
    # Split into cards on the CANDIDATE header; keep the header ticker.
    parts = _CAND_HDR_RE.split(section)
    # parts = [pre, tkr1, body1, tkr2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1]
        for line in body.splitlines():
            tm = _TICKET_RE.search(line)
            if not tm:
                continue
            status = ("deferred_capacity" if _DEFERRED_MARK in line
                      else "actionable")
            rm = _RSI_RE.search(body)
            recs.append(_rec(
                date, "candidate", "csp" if tm.group(3) == "P" else "cc",
                tm.group(1), "PUT" if tm.group(3) == "P" else "CALL",
                float(tm.group(2)), _exp_iso(tm.group(4)),
                ticket=tm.group(0).replace("**", ""), status=status,
                shown_rsi=float(rm.group(1)) if rm else None))
            break  # one entry ticket per card
    return recs


def extract_recs(date: str, briefing_json: dict | None,
                 full_md: str | None) -> list[dict]:
    """JSON surfaces + markdown-only Candidate cards, de-duplicated on
    (date, surface, contract)."""
    recs = extract_json_recs(briefing_json or {}, date)
    recs.extend(extract_candidate_recs(full_md or "", date))
    seen, out = set(), []
    for r in recs:
        key = (r["date"], r["surface"], r["ticker"], r["opt_type"],
               r["strike"], r["expiration"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ── Retro grading (reuses entry_audit's condition resolution) ─────────────


def grade_rec(rec: dict, snapshots_root: Path, iv_history: dict | None,
              ohlc_cache_dir: Path | None = None,
              config: dict | None = None,
              cond_cache: dict | None = None) -> dict:
    """Grade one rec with the production scorer on its OWN day's
    snapshot conditions (same resolution as entry_audit's retro
    grading). Mutates and returns ``rec`` with grade / conditions /
    gradeable / ungradeable_reason."""
    if cond_cache is None:
        cond_cache = {}
    if rec["side"] not in ("csp", "cc"):
        rec.update(gradeable=False, grade={"letter": "n/a", "score": None},
                   conditions=None,
                   ungradeable_reason="long leg — premium-selling grader "
                                      "objective doesn't apply")
        return rec
    if rec.get("strike") is None:
        rec.update(gradeable=False, grade={"letter": "n/a", "score": None},
                   conditions=None, ungradeable_reason="no parsed strike")
        return rec
    snap = Path(snapshots_root) / rec["date"]
    if not (snap / "technicals.json").exists():
        rec.update(gradeable=False, grade={"letter": "n/a", "score": None},
                   conditions=None,
                   ungradeable_reason=f"no snapshot for {rec['date']}")
        return rec
    ck = (rec["date"], rec["ticker"])
    if ck not in cond_cache:
        cond_cache[ck] = _ea.entry_conditions(
            Path(snapshots_root), rec["date"], rec["ticker"], iv_history,
            ohlc_cache_dir)
    cond = cond_cache[ck]
    fn = _sg.csp_setup if rec["side"] == "csp" else _sg.cc_setup
    grade = fn(
        rsi=cond.get("rsi"), iv_rank=cond.get("iv_rank"),
        iv_rank_source=cond.get("iv_source"),
        support_resistance=cond.get("support_resistance"),
        strike=rec["strike"], spot=cond.get("spot"),
        sma_200=cond.get("sma_200"), lt_verdict=cond.get("lt_verdict"),
        day_change_pct=cond.get("day_change_pct"),
        days_to_earnings=cond.get("days_to_earnings"),
        drawdown_pct=cond.get("drawdown_pct"), config=config)
    rec["conditions"] = {k: cond.get(k) for k in
                         ("rsi", "iv_rank", "iv_source", "day_change_pct",
                          "drawdown_pct", "days_to_earnings")}
    rec["grade"] = grade
    if grade.get("letter") == "n/a":
        rec.update(gradeable=False,
                   ungradeable_reason="insufficient measured data on "
                                      f"{rec['date']} (fail closed)")
    else:
        rec.update(gradeable=True, ungradeable_reason=None)
    return rec


# ── Ledger cross-reference ────────────────────────────────────────────────


def _contract_of(entry: dict):
    c = entry.get("contract") or {}
    try:
        return ((c.get("underlying") or "").upper(),
                (c.get("type") or "").upper(),
                round(float(c.get("strike")), 4),
                str(c.get("expiration")))
    except (TypeError, ValueError):
        return None


def cross_reference(recs: list[dict], ledger: dict | None,
                    dates: list[str]) -> dict:
    """Which recommended contracts George actually TOOK (entry-grade
    ledger entries in the audit window matching a rec's contract on/
    before the entry date), and which of his entries were off-list."""
    entries = [e for e in ((ledger or {}).get("entries") or [])
               if isinstance(e, dict)]
    lo = min(dates) if dates else None
    hi = max(dates) if dates else None
    in_window = [e for e in entries
                 if lo and (e.get("entry_date") or "") >= lo
                 and (e.get("entry_date") or "") <= hi]
    taken, matched_rec_ids = [], set()
    for e in in_window:
        ck = _contract_of(e)
        if ck is None:
            continue
        hit = None
        for i, r in enumerate(recs):
            if r.get("strike") is None or r.get("expiration") is None:
                continue
            rk = (r["ticker"], r["opt_type"], round(r["strike"], 4),
                  str(r["expiration"]))
            if rk == ck and r["date"] <= (e.get("entry_date") or ""):
                hit = (i, r)
                break
        if hit:
            matched_rec_ids.add(hit[0])
            taken.append({"entry": e, "rec": hit[1]})
    offlist = [e for e in in_window
               if not any(t["entry"] is e for t in taken)]
    return {"window": [lo, hi], "ledger_entries_in_window": len(in_window),
            "taken": taken, "offlist_entries": offlist,
            "untaken_rec_count": len([r for r in recs
                                      if r.get("gradeable")])
            - len(matched_rec_ids)}


def ledger_avg_score(ledger: dict | None) -> tuple[float | None, int]:
    """(avg grade score, n) across ALL ledger entries with a score —
    the running scorecard number George quoted."""
    scores = []
    for e in ((ledger or {}).get("entries") or []):
        s = ((e.get("grade") or {}).get("score")
             if isinstance(e, dict) else None)
        if isinstance(s, (int, float)):
            scores.append(float(s))
    if not scores:
        return None, 0
    return round(sum(scores) / len(scores), 1), len(scores)


# ── Aggregation ───────────────────────────────────────────────────────────


def aggregate(recs: list[dict]) -> dict:
    graded = [r for r in recs if r.get("gradeable")]
    dist = Counter((r["grade"] or {}).get("letter") for r in graded)
    by_surface: dict = {}
    for r in graded:
        s = by_surface.setdefault(r["surface"], Counter())
        s[(r["grade"] or {}).get("letter")] += 1
    scores = [r["grade"]["score"] for r in graded
              if isinstance((r["grade"] or {}).get("score"), (int, float))]
    csp = [r for r in graded if r["side"] == "csp"]

    def _cond_rsi(r):
        v = (r.get("conditions") or {}).get("rsi")
        return v if v is not None else r.get("shown_rsi")

    rsi_5050 = [r for r in csp
                if _cond_rsi(r) is not None
                and float(_cond_rsi(r)) >= RSI_5050_THRESHOLD]
    thin_vol = [r for r in csp
                if (r.get("conditions") or {}).get("iv_rank") is not None
                and float(r["conditions"]["iv_rank"]) < THIN_VOL_RANK]
    ab = [r for r in graded
          if (r["grade"] or {}).get("letter") in _AB_LETTERS]
    actionable = [r for r in graded if r["status"] == "actionable"]
    ab_actionable = [r for r in actionable
                     if (r["grade"] or {}).get("letter") in _AB_LETTERS]
    return {
        "total_recs": len(recs),
        "graded": len(graded),
        "ungraded": len(recs) - len(graded),
        "avg_score": (round(sum(scores) / len(scores), 1)
                      if scores else None),
        "distribution": dict(sorted(dist.items(),
                                    key=lambda kv: str(kv[0]))),
        "by_surface": {k: dict(sorted(v.items(),
                                      key=lambda kv: str(kv[0])))
                       for k, v in sorted(by_surface.items())},
        "status_counts": dict(Counter(r["status"] for r in recs)),
        "csp_graded": len(csp),
        "csp_rsi_5050": len(rsi_5050),
        "csp_rsi_5050_tickets": [f"{r['date']} {r['ticket']}"
                                 f" (RSI {float(_cond_rsi(r)):.0f})"
                                 for r in rsi_5050],
        "csp_thin_vol": len(thin_vol),
        "csp_thin_vol_tickets": [
            f"{r['date']} {r['ticket']} "
            f"({'IVr' if r['conditions'].get('iv_source') == 'chain' else 'RVr'}"
            f" {float(r['conditions']['iv_rank']):.0f})"
            for r in thin_vol],
        "ab_count": len(ab),
        "ab_pct": (round(100.0 * len(ab) / len(graded), 1)
                   if graded else None),
        "actionable_graded": len(actionable),
        "ab_actionable_count": len(ab_actionable),
        "ab_actionable_pct": (round(100.0 * len(ab_actionable)
                                    / len(actionable), 1)
                              if actionable else None),
    }


# ── Orchestrator ──────────────────────────────────────────────────────────


def run_audit(briefings_dir: Path, snapshots_root: Path,
              *, limit: int = 5, dates: list[str] | None = None,
              iv_history_path: Path | None = None,
              ohlc_cache_dir: Path | None = None,
              ledger_path: Path | None = None,
              config: dict | None = None) -> dict:
    briefings_dir = Path(briefings_dir)
    snapshots_root = Path(snapshots_root)
    if dates is None:
        dates = audit_dates(briefings_dir, snapshots_root, limit)
    if not dates:
        return {"error": f"no auditable briefing dates under "
                         f"{briefings_dir} + {snapshots_root}"}
    iv_history = _load_json(iv_history_path) if iv_history_path else None
    cond_cache: dict = {}
    recs: list[dict] = []
    for d in dates:
        bj = _load_json(briefings_dir / f"briefing_{d}.json")
        md_path = briefings_dir / f"briefing_full_{d}.md"
        try:
            md = md_path.read_text()
        except OSError:
            md = None
        for r in extract_recs(d, bj, md):
            grade_rec(r, snapshots_root, iv_history, ohlc_cache_dir,
                      config, cond_cache)
            recs.append(r)
    ledger = _load_json(ledger_path) if ledger_path else None
    agg = aggregate(recs)
    xref = cross_reference(recs, ledger, dates)
    l_avg, l_n = ledger_avg_score(ledger)
    return {"dates": dates, "recs": recs, "aggregate": agg,
            "ledger_xref": xref,
            "ledger_avg_score": l_avg, "ledger_scored_entries": l_n}


# ── Markdown render ───────────────────────────────────────────────────────


def _grade_cell(r: dict) -> str:
    g = r.get("grade") or {}
    letter = g.get("letter") or "n/a"
    if letter == "n/a":
        return f"n/a — {r.get('ungradeable_reason')}"
    score = g.get("score")
    return (f"**{letter}** ({score:.0f})"
            if isinstance(score, (int, float)) else f"**{letter}**")


def _drivers_cell(r: dict, n: int = 3) -> str:
    g = r.get("grade") or {}
    return " · ".join(str(d) for d in (g.get("drivers") or [])[:n]) or "—"


def _ticket_examples(tickets: list | None) -> list[str]:
    """Cap example-ticket sub-bullets at MAX_PATTERN_TICKETS; the count
    line above stays complete."""
    tickets = tickets or []
    out = [f"  - {t}" for t in tickets[:MAX_PATTERN_TICKETS]]
    if len(tickets) > MAX_PATTERN_TICKETS:
        out.append(f"  - … and {len(tickets) - MAX_PATTERN_TICKETS} more")
    return out


def _recommendation_section(result: dict, config: dict | None) -> list[str]:
    """The measured-numbers case for (or against) a minimum-grade floor
    on green-lit actionable tickets. ANALYSIS ONLY — nothing here is
    wired into any recommendation surface."""
    agg = result["aggregate"]
    if not agg.get("graded"):
        return []
    cd_pct = 100.0 - (agg.get("ab_pct") or 0.0)
    # surfaces ranked by C/D share (min 5 graded to avoid noise)
    worst = []
    for s, dist in (agg.get("by_surface") or {}).items():
        n = sum(dist.values())
        cd = sum(v for k, v in dist.items() if k in ("C", "D", "—"))
        if n >= 5:
            worst.append((round(100.0 * cd / n, 0), s, cd, n))
    worst.sort(reverse=True)
    lines = [
        "", "## RECOMMENDATION (analysis only — not implemented)", "",
        f"- **A minimum-grade floor for green-lit actionable tickets "
        f"looks warranted by the measured distribution**: "
        f"{cd_pct:.0f}% of graded recommendations land C/D/blocked, and "
        f"the machine's average ({agg.get('avg_score')}) is "
        f"statistically the same as the entry ledger George wants to "
        f"improve on ({result.get('ledger_avg_score')}). A B floor "
        f"(score ≥ 65) that demotes C/D tickets to planning rows — the "
        f"same ⏸ pattern rules #24/#41 already use — would cut the "
        f"actionable surface to the "
        f"{agg.get('ab_count')}/{agg.get('graded')} tickets that "
        f"actually grade A/A-/B, without hiding anything.",
        "- Surfaces that need it most (share of graded recs at C/D/—):",
    ]
    for pct, s, cd, n in worst:
        lines.append(f"  - {SURFACE_LABELS.get(s, s)}: {cd}/{n} "
                     f"({pct:.0f}%)")
    lines.extend([
        "- **Counter-considerations (why not just flip it on):**",
        "  - *Grade availability coverage* — "
        f"{agg.get('ungraded')}/{agg.get('total_recs')} recs were not "
        "gradable here (long legs, missing snapshot inputs); a floor "
        "must fail-open on n/a grades or it silently kills surfaces "
        "with thin data (rule #19 cuts both ways).",
        "  - *Capacity-gated anyway* — "
        f"{(agg.get('status_counts') or {}).get('deferred_capacity', 0)} "
        f"of {agg.get('total_recs')} recs were ALREADY ⏸ deferred by "
        "the capacity gate in this window; a grade floor changes "
        "little until coverage clears 0.50× and the gate reopens — "
        "which is exactly when it starts mattering.",
        "  - *Yield-floor overlap* — the delivered-yield floor (rule "
        "#44) already removes thin-premium tickets on some surfaces; "
        "the thin-vol pattern above shows what still leaks through "
        "(vol rank < 40 with acceptable dollar yield). The grade floor "
        "and the yield floor overlap but neither subsumes the other.",
        "  - The Setup Grade is already RENDERED on several surfaces "
        "(rule: grade beside the ticket, gate stands) — the open "
        "question is only whether C/D demotes from 'numbered "
        "actionable' to 'planning row', not whether to score.",
    ])
    return lines


def render_markdown(result: dict, config: dict | None = None) -> str:
    agg = result["aggregate"]
    lines = [
        f"# Recommendation Grade Audit — briefings "
        f"{result['dates'][0]} → {result['dates'][-1]}",
        "",
        "_George (2026-08-12): \"see what the recommendations make sense? "
        "If I were to listen to those recommendations, I would get into "
        "the A or B category. ... some recommendations recommend selling "
        "CSPs where our [RSI] is 50/50. That doesn't sound like a good "
        "idea.\"_",
        "",
        "Every option ticket the last "
        f"{len(result['dates'])} briefings RECOMMENDED, re-graded with "
        "the production Setup Grade scorer on that day's own snapshot "
        "conditions (same resolution as the Entry Timing Audit). "
        "'n/a' = not gradeable, with the reason (rule #19 — nothing "
        "fabricated). Long legs (LEAPs, collar puts) are listed but not "
        "graded — the premium-selling grader's objective doesn't apply.",
        "",
    ]
    for d in result["dates"]:
        day = [r for r in result["recs"] if r["date"] == d]
        lines.append(f"## {d} — {len(day)} recommended tickets")
        lines.append("")
        lines.append("| Surface | Ticket | Shown status | Grade | "
                     "Top drivers |")
        lines.append("|---|---|---|---|---|")
        for r in sorted(day, key=lambda x: (x["surface"], x["ticker"])):
            lines.append(
                f"| {SURFACE_LABELS.get(r['surface'], r['surface'])} "
                f"| {r['ticket']} "
                f"| {STATUS_LABELS.get(r['status'], r['status'])} "
                f"| {_grade_cell(r)} | {_drivers_cell(r)} |")
        lines.append("")

    lines.extend(["## Aggregate — grade the recommendations", ""])
    lines.append(f"- Recommendations extracted: {agg['total_recs']} · "
                 f"graded: {agg['graded']} · not gradable: "
                 f"{agg['ungraded']}")
    if agg.get("avg_score") is not None:
        lines.append(f"- **Average recommendation score: "
                     f"{agg['avg_score']:.1f}/100 "
                     f"(letter {_sg.letter_for(agg['avg_score'], config)})**")
    if agg.get("distribution"):
        lines.append("- Grade distribution: "
                     + " · ".join(f"{k}: {v}" for k, v in
                                  agg["distribution"].items()))
    lines.append("- Shown-status mix: "
                 + " · ".join(f"{STATUS_LABELS.get(k, k)}: {v}"
                              for k, v in sorted(
                                  agg["status_counts"].items())))
    lines.append("")
    lines.append("### By surface (which surfaces emit the C/Ds?)")
    lines.append("")
    lines.append("| Surface | A | A- | B | C | D | — (RSI-blocked) |")
    lines.append("|---|---|---|---|---|---|---|")
    for s, dist in agg["by_surface"].items():
        lines.append(f"| {SURFACE_LABELS.get(s, s)} | "
                     + " | ".join(str(dist.get(k, 0))
                                  for k in ("A", "A-", "B", "C", "D", "—"))
                     + " |")
    lines.extend([
        "",
        "### Pattern checks (the ones George called out)",
        "",
        f"- **The '50/50 problem'** — CSP recs at measured RSI ≥ "
        f"{RSI_5050_THRESHOLD:.0f}: **{agg['csp_rsi_5050']} of "
        f"{agg['csp_graded']}** graded CSP recs "
        f"({(100.0 * agg['csp_rsi_5050'] / agg['csp_graded']):.0f}%)."
        if agg["csp_graded"] else
        "- **The '50/50 problem'** — no graded CSP recs.",
    ])
    lines.extend(_ticket_examples(agg.get("csp_rsi_5050_tickets")))
    lines.append(
        f"- **Thin premium** — CSP recs with TRUE vol rank < "
        f"{THIN_VOL_RANK:.0f} (the rank where the vol component scores "
        f"0): **{agg['csp_thin_vol']} of {agg['csp_graded']}**."
        if agg["csp_graded"] else "- **Thin premium** — n/a.")
    lines.extend(_ticket_examples(agg.get("csp_thin_vol_tickets")))

    # Ledger comparison.
    xref = result.get("ledger_xref") or {}
    l_avg = result.get("ledger_avg_score")
    lines.extend(["", "### Machine recs vs George's actual entries", ""])
    if l_avg is not None:
        machine = agg.get("avg_score")
        lines.append(
            f"- Your entry-grade ledger averages **{l_avg:.0f}/100** "
            f"({_sg.letter_for(l_avg, config)}, "
            f"{result.get('ledger_scored_entries')} scored entries). The "
            f"machine's graded recommendations average "
            + (f"**{machine:.0f}/100** "
               f"({_sg.letter_for(machine, config)})."
               if machine is not None else "n/a."))
    taken = xref.get("taken") or []
    lines.append(f"- Ledger entries inside the audit window "
                 f"({xref.get('window', ['?', '?'])[0]} → "
                 f"{xref.get('window', ['?', '?'])[1]}): "
                 f"{xref.get('ledger_entries_in_window', 0)} · matched a "
                 f"recommended ticket: {len(taken)} · off-list (your own "
                 f"picks): {len(xref.get('offlist_entries') or [])}")
    for t in taken:
        e, r = t["entry"], t["rec"]
        lines.append(
            f"  - TAKEN: {e.get('label')} entered {e.get('entry_date')} "
            f"(ledger grade {((e.get('grade') or {}).get('letter'))}) ← "
            f"recommended {r['date']} on "
            f"{SURFACE_LABELS.get(r['surface'], r['surface'])} "
            f"(rec grade {_grade_cell(r)})")
    for e in (xref.get("offlist_entries") or []):
        g = e.get("grade") or {}
        lines.append(f"  - OFF-LIST: {e.get('label')} entered "
                     f"{e.get('entry_date')} — ledger grade "
                     f"{g.get('letter')}"
                     + (f" ({g.get('score'):.0f})"
                        if isinstance(g.get("score"), (int, float))
                        else ""))

    # Verdict.
    lines.extend(["", "### Verdict — would following the recs put you "
                      "in A/B?", ""])
    if agg["graded"]:
        lines.append(
            f"- Across ALL graded recommendations: **{agg['ab_count']} of "
            f"{agg['graded']} ({agg['ab_pct']:.0f}%) grade A/A-/B**.")
        if agg["actionable_graded"]:
            lines.append(
                f"- Restricted to tickets shown as ACTIONABLE (no ⏸/⛔ "
                f"tag): **{agg['ab_actionable_count']} of "
                f"{agg['actionable_graded']} "
                f"({agg['ab_actionable_pct']:.0f}%) grade A/A-/B**.")
        else:
            lines.append("- No graded rec was shown as fully actionable "
                         "in the window (capacity gates were closed).")
    else:
        lines.append("- No gradeable recommendations in the window.")
    lines.extend(_recommendation_section(result, config))
    lines.extend([
        "", "## Caveats", "",
        "- Grades use the SAME production Setup Grade scorer and the "
        "same snapshot-condition resolution as the Entry Timing Audit; "
        "unmeasured components renormalize and ≥2 missing caps the "
        "letter at B (module rules).",
        f"- TRUE chain IV rank ('IVr') needs ≥{_ea.MIN_IV_HISTORY_OBS} "
        "days of chain-IV history; many tickers' series are barely past "
        "that, so ranks are coarse (granularity ~1/len) and correlated "
        "vol moves can give many names the same rank. 'RVr' = "
        "realized-vol percentile fallback.",
        "- 'Shown status' is the tag the briefing carried AT THE TIME "
        "(⏸/⛔); the grade is measured retroactively from that day's "
        "snapshot — the two are independent reads.",
        "- The ledger cross-reference matches exact contracts "
        "(underlying/type/strike/expiration) recommended on/before the "
        "entry date; a same-name different-strike entry counts as "
        "off-list.",
    ])
    return "\n".join(lines) + "\n"
