"""
Briefing-vs-yesterday diff renderer.

Compares today's action list against the previous day's briefing JSON and surfaces:
- ADDED: actions that weren't in yesterday's briefing
- REMOVED: actions that were in yesterday's briefing but aren't today
- CHANGED: same contract/ticker but different recommendation (e.g., HOLD → ROLL_OUT)
- DONE (heuristic): close winners that were recommended yesterday and the position
   no longer exists today suggests user executed the close.

This module is deterministic and pure — no I/O outside reading the previous-day JSON.
"""

from __future__ import annotations

from pathlib import Path
import json
from typing import Optional


def _signature_for_action(item_text: str) -> str:
    """Build a stable signature for an action item (used for set comparison).

    The signature is ``TYPE|IDENTIFIER`` where IDENTIFIER is the option contract
    (e.g. ``MU_PUT_700_20261218``) or, failing that, the ticker (e.g. ``TSLA``).

    Crucially it must NOT depend on volatile text like the buy-to-close limit
    price or captured-profit %, or the same recommendation flips between
    "removed" and "added" day-over-day when only the price ticks (the MU bug).
    The old regex used ``[A-Z_]+`` for the identifier, which excludes the DIGITS
    in every option contract — so the match failed and it fell back to the first
    80 chars of the line (price included). This keys on the stable parts only.
    """
    import re
    first = item_text.split("\n")[0]

    # Action TYPE = first bold token after the number ("CLOSE", "EXECUTE ROLL",
    # "ROLL_OUT_AND_UP", "HEDGE", "DEFENSIVE ROLL (core override)", ...).
    m_type = re.match(r"^\s*\d+\.\s+\*\*([^*]+)\*\*", first)
    if not m_type:
        return first[:80]
    kind = m_type.group(1).strip()
    # Render-label alias (2026-08-04): "PULLBACK CSP" was renamed to
    # "CSP — PAID-TO-WAIT" on every surface, but the INTERNAL kind stays
    # PULLBACK_CSP (too many consumers: rec_aging keys, capital-planner,
    # money-plan deploy kinds, day-over-day signatures). Normalizing here
    # keeps signatures/action-keys byte-identical across the rename.
    if kind.upper().startswith("CSP — PAID-TO-WAIT") or \
            kind.upper().startswith("CSP - PAID-TO-WAIT"):
        kind = "PULLBACK CSP"
    rest = first[m_type.end():]

    # Stable identifier: a full option contract (digits allowed!) first; else
    # the first bare ticker. Never the price.
    m_contract = re.search(r"\b[A-Z]{1,6}_(?:PUT|CALL)_\d+(?:_\d{6,8})?\b", rest)
    if m_contract:
        ident = m_contract.group(0)
    else:
        m_tk = re.search(r"\b[A-Z]{2,6}\b", rest)
        ident = m_tk.group(0) if m_tk else rest.strip()[:40]
    return f"{kind}|{ident}"


def parse_action_signatures(briefing_md: str) -> set[str]:
    """Extract a set of action signatures from a briefing markdown blob."""
    sigs: set[str] = set()
    if not briefing_md:
        return sigs
    if "## Today's Action List" not in briefing_md:
        return sigs
    section = briefing_md.split("## Today's Action List", 1)[1].split("\n## ", 1)[0]
    import re
    # Each numbered top-level line
    for line in section.split("\n"):
        if re.match(r"^\s*\d+\.\s+\*\*", line):
            sig = _signature_for_action(line)
            sigs.add(sig)
    return sigs


def load_yesterday_briefing(today_iso: str, snapshots_root: Path) -> Optional[str]:
    """Find yesterday's briefing markdown given today's date and the snapshot root."""
    from datetime import datetime, timedelta
    try:
        today = datetime.strptime(today_iso, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    for offset in range(1, 8):  # search up to 7 days back (skip weekends)
        candidate = today - timedelta(days=offset)
        cand_dir = Path(snapshots_root) / candidate.isoformat()
        # Look for any .md file in that snapshot directory or in reports/daily/
        md_path = cand_dir / "briefing.md"
        if md_path.exists():
            return md_path.read_text()
        # Fallback: reports/daily/. Two-document split (2026-08-06) —
        # prefer the FULL render (briefing_full_<date>.md); the digest at
        # briefing_<date>.md keeps the Action List verbatim, so it remains
        # a correct diff source for pre-split history.
        for name in (f"briefing_full_{candidate.isoformat()}.md",
                     f"briefing_{candidate.isoformat()}.md"):
            report = Path("reports/daily") / name
            if report.exists():
                return report.read_text()
    return None


def _month_abbrev(exp_iso: str) -> str:
    """'2026-11-20' → 'Nov'; unparseable → the raw string (never invented)."""
    try:
        from datetime import datetime as _dt
        return _dt.strptime(str(exp_iso)[:10], "%Y-%m-%d").strftime("%b")
    except (ValueError, TypeError):
        return str(exp_iso or "?")


def detect_executed_rolls(prev_positions, today_positions) -> list[dict]:
    """Detect user-executed rolls from the position diff (task #40 fix 9).

    A roll pattern = same underlying + option type, old SHORT contract gone,
    NEW short contract (different strike and/or expiry) appeared today.
    Pairs greedily by closest strike when several legs changed on one name
    (the META 575/580 → 570/575 case). Returns [] on missing snapshots —
    fail-open, never a guessed roll.

    Each entry: {old_symbol, new_symbol, underlying, type, old_strike,
    new_strike, old_exp, new_exp, same_strike, new_premium (measured
    premiumReceived on the new leg, or None)}.
    """
    if not prev_positions or not today_positions:
        return []

    def _shorts(positions):
        out = {}
        for p in positions or []:
            try:
                if (p.get("assetType") or "").upper() != "OPTION":
                    continue
                if float(p.get("qty", 0) or 0) >= 0:
                    continue
                sym = str(p.get("symbol") or "")
                if sym:
                    out[sym] = p
            except (TypeError, ValueError):
                continue
        return out

    prev_by_sym = _shorts(prev_positions)
    today_by_sym = _shorts(today_positions)
    gone = {s: p for s, p in prev_by_sym.items() if s not in today_by_sym}
    new = {s: p for s, p in today_by_sym.items() if s not in prev_by_sym}
    if not gone or not new:
        return []

    def _key(p):
        return (str(p.get("underlying") or ""), (p.get("type") or "").upper())

    rolls: list[dict] = []
    used_new: set = set()
    for old_sym, old_p in sorted(gone.items()):
        candidates = [
            (abs(float(np.get("strike", 0) or 0)
                 - float(old_p.get("strike", 0) or 0)), ns, np)
            for ns, np in new.items()
            if ns not in used_new and _key(np) == _key(old_p)
        ]
        if not candidates:
            continue
        candidates.sort(key=lambda t: (t[0], t[1]))
        _, new_sym, new_p = candidates[0]
        used_new.add(new_sym)
        try:
            old_strike = float(old_p.get("strike", 0) or 0)
            new_strike = float(new_p.get("strike", 0) or 0)
        except (TypeError, ValueError):
            continue
        premium = new_p.get("premiumReceived")
        try:
            premium = float(premium) if premium else None
        except (TypeError, ValueError):
            premium = None
        rolls.append({
            "old_symbol": old_sym,
            "new_symbol": new_sym,
            "underlying": old_p.get("underlying"),
            "type": (old_p.get("type") or "").upper(),
            "old_strike": old_strike,
            "new_strike": new_strike,
            "old_exp": old_p.get("expiration") or "",
            "new_exp": new_p.get("expiration") or "",
            "same_strike": abs(old_strike - new_strike) < 0.005,
            "new_premium": premium,
        })
    return rolls


def detect_executed_opens(prev_positions, today_positions,
                          nlv: float | None = None,
                          cash: float | None = None) -> list[dict]:
    """Detect user-executed OPENS from the position diff (2026-08-10 bug 1a).

    A fresh open = a NEW short option position present today, absent
    yesterday, NOT explicable as the STO leg of a roll (the roll detector
    pairs each disappeared short with the closest-strike new short on the
    same underlying + type; whatever new shorts remain unpaired are fresh
    opens). Observed miss: George sold MELI $1460P Jun '27 on Friday —
    a $146,000 obligation, 13.3% of NLV — and 'Since Yesterday' said
    nothing because it detected closes + rolls only.

    Rule #19 — every attached number is MEASURED:
      - ``obligation`` = strike × 100 × |qty| (short PUTs only; None on calls)
      - ``pct_of_nlv`` only when ``nlv`` was provided
      - ``coverage_with`` / ``coverage_without`` = today's cash vs today's
        total short-put obligation with / without this open (a same-day
        counterfactual — both legs from THIS snapshot, nothing estimated)

    Returns [] on missing snapshots — fail-open, never a guessed open.
    """
    if not prev_positions or not today_positions:
        return []

    def _shorts(positions):
        out = {}
        for p in positions or []:
            try:
                if (p.get("assetType") or "").upper() != "OPTION":
                    continue
                if float(p.get("qty", 0) or 0) >= 0:
                    continue
                sym = str(p.get("symbol") or "")
                if sym:
                    out[sym] = p
            except (TypeError, ValueError):
                continue
        return out

    prev_by_sym = _shorts(prev_positions)
    today_by_sym = _shorts(today_positions)
    new = {s: p for s, p in today_by_sym.items() if s not in prev_by_sym}
    if not new:
        return []

    # New shorts consumed as the STO leg of a detected roll are NOT opens.
    roll_legs = {r.get("new_symbol")
                 for r in detect_executed_rolls(prev_positions,
                                                today_positions)}

    # Today's TOTAL short-put obligation — denominator for the measured
    # coverage counterfactual.
    total_put_oblig = 0.0
    for p in today_by_sym.values():
        if (p.get("type") or "").upper() != "PUT":
            continue
        try:
            total_put_oblig += (float(p.get("strike", 0) or 0) * 100
                                * abs(float(p.get("qty", 0) or 0)))
        except (TypeError, ValueError):
            continue

    opens: list[dict] = []
    for sym in sorted(new):
        if sym in roll_legs:
            continue
        p = new[sym]
        opt_type = (p.get("type") or "").upper()
        try:
            strike = float(p.get("strike", 0) or 0)
            qty = abs(float(p.get("qty", 0) or 0))
        except (TypeError, ValueError):
            continue
        obligation = strike * 100 * qty if opt_type == "PUT" else None
        pct_of_nlv = None
        if obligation and nlv:
            try:
                pct_of_nlv = obligation / float(nlv) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                pct_of_nlv = None
        coverage_with = coverage_without = None
        if (obligation and cash is not None and total_put_oblig > 0
                and total_put_oblig > obligation):
            try:
                coverage_with = float(cash) / total_put_oblig
                coverage_without = float(cash) / (total_put_oblig - obligation)
            except (TypeError, ValueError, ZeroDivisionError):
                coverage_with = coverage_without = None
        opens.append({
            "symbol": sym,
            "underlying": p.get("underlying"),
            "type": opt_type,
            "strike": strike,
            "expiration": p.get("expiration") or "",
            "qty": qty,
            "obligation": obligation,
            "pct_of_nlv": pct_of_nlv,
            "coverage_with": coverage_with,
            "coverage_without": coverage_without,
            "total_put_obligations": total_put_oblig,
        })
    return opens


def render_executed_open_lines(opens: list[dict],
                               entry_grades: dict | None = None) -> list[str]:
    """Bullet lines for detected user-executed opens — measured obligation,
    % of NLV, and the same-day coverage counterfactual (rule #19: values
    that weren't measured are simply omitted, never guessed).

    ``entry_grades`` (George 2026-08-12 — "a running score of our
    entries", from ``analysis.entry_ledger.maintain``) maps position
    symbol → {letter, score, top_driver}: each detected open gains its
    just-assigned Setup Grade as a sub-bullet. No grade for a symbol →
    no line (never fabricated)."""
    lines: list[str] = []
    for o in opens or []:
        und = o.get("underlying") or "?"
        t_letter = "P" if o.get("type") == "PUT" else "C"
        qty = o.get("qty") or 0
        head = (f"- 🆕 **{o.get('symbol')}** — new short "
                f"{o.get('type', '?')} opened at the broker "
                f"({und} ${o.get('strike'):g}{t_letter} "
                f"{_month_abbrev(o.get('expiration'))}, {qty:g}×)")
        oblig = o.get("obligation")
        if oblig:
            head += f" · obligation **${oblig:,.0f}**"
            pct = o.get("pct_of_nlv")
            if pct is not None:
                head += f" (**{pct:.1f}% of NLV**)"
        lines.append(head)
        g = (entry_grades or {}).get(o.get("symbol"))
        if g and g.get("letter") not in (None, "n/a"):
            seg = f"  - 🎓 Entry grade: **{g['letter']}**"
            try:
                if g.get("score") is not None:
                    seg += f" ({float(g['score']):.0f}/100)"
            except (TypeError, ValueError):
                pass
            if g.get("top_driver"):
                seg += f" — {g['top_driver']}"
            seg += " · locked to entry-day conditions"
            lines.append(seg)
        cw, cwo = o.get("coverage_with"), o.get("coverage_without")
        if cw is not None and cwo is not None and oblig:
            lines.append(
                f"  - Coverage impact: **{cwo:.2f}× → {cw:.2f}×** largely "
                f"from this open — it adds ${oblig:,.0f} of the "
                f"${o.get('total_put_obligations', 0):,.0f} total short-put "
                f"obligation (today's cash vs obligation, with/without it)."
            )
    return lines


def render_executed_roll_directives(rolls: list[dict],
                                    today_iso: str | None = None) -> list[str]:
    """Ready-to-paste directive templates for detected user-executed rolls
    (task #40 fix 9) — so decisions made on purpose stop generating
    next-day nags. The template targets state/fable_advisor_memory.md and
    includes 'hold' phrasing so analysis.advisor_directives parses it as a
    hold directive. Basis = strike − measured premiumReceived on the new
    leg; omitted when the broker payload carried no premium (rule #19).

    Lines render as BULLETS ("- 🔄 _…_"), not standalone italics — the
    digest strips standalone italic footers, which left the '### 🔄
    Detected User-Executed Rolls (1)' header with zero items beneath it
    (observed 2026-08-10, digest lines 111-112)."""
    lines: list[str] = []
    for r in rolls or []:
        und = r.get("underlying") or "?"
        t_letter = "P" if r.get("type") == "PUT" else "C"
        move = (f"{_month_abbrev(r.get('old_exp'))}→"
                f"{_month_abbrev(r.get('new_exp'))}")
        if r.get("same_strike"):
            shape = "same strike"
            shape_word = "same-strike"
        else:
            shape = (f"${r.get('old_strike'):g}→${r.get('new_strike'):g}")
            shape_word = ("down" if r.get("new_strike", 0)
                          < r.get("old_strike", 0) else "up")
        hold_bit = "hold."
        if r.get("type") == "PUT" and r.get("new_premium") is not None:
            basis = float(r["new_strike"]) - float(r["new_premium"])
            hold_bit = f"hold — willing to own at ~${basis:,.0f} basis."
        date_bit = str(today_iso or "")
        lines.append(
            f"- 🔄 _Detected roll: {und} ${r.get('old_strike'):g}{t_letter} "
            f"{move} ({shape}). If deliberate, add to "
            f"state/fable_advisor_memory.md: "
            f"`- **{r.get('new_symbol')}** — rolled {shape_word} "
            f"{date_bit} deliberately; {hold_bit} "
            f"Re-flag only if delta > 0.60.` "
            f"Add \"through earnings\" to the directive to also hold "
            f"across an earnings print (otherwise a profitable hold "
            f"pauses when a report is imminent)._"
        )
    return lines


_RECON_STATUS_LABELS = {
    "EXECUTED": "✅ EXECUTED (position diff confirms; or overtaken by events)",
    "PARTIAL": "◐ PARTIAL (position reduced, not fully closed)",
    "IGNORED": "✗ NOT FILLED (position unchanged — dropped without execution)",
    "UNVERIFIED": "❔ UNVERIFIED (insufficient broker data — do not assume executed)",
}


def render_diff_panel(today_md: str, yesterday_md: Optional[str],
                      recon_status: Optional[dict] = None,
                      executed_rolls: Optional[list] = None,
                      today_iso: Optional[str] = None,
                      executed_opens: Optional[list] = None,
                      entry_grades: Optional[dict] = None) -> list[str]:
    """Render a "## Since Yesterday" panel comparing the two briefings.

    ``recon_status`` (optional) maps action keys (``KIND:IDENT``, from
    analysis.rec_aging.reconcile) to fill-reconciliation statuses. When
    provided, removed items render a definitive status instead of the legacy
    "likely executed" guess; when absent, the old wording is kept as fallback.

    ``executed_rolls`` (task #40 fix 9, from :func:`detect_executed_rolls`)
    adds ready-to-paste directive templates for rolls the user executed at
    the broker, so deliberate decisions stop generating next-day nags.

    ``executed_opens`` (2026-08-10 bug 1a, from :func:`detect_executed_opens`)
    surfaces fresh short opens the user executed at the broker — with the
    measured obligation, % of NLV, and same-day coverage counterfactual —
    so a $146K MELI put can never appear silently.

    ``entry_grades`` (George 2026-08-12, from ``entry_ledger.maintain``)
    adds each detected open's just-assigned Setup Grade sub-bullet.
    """
    if not yesterday_md:
        return []  # nothing to diff against on first run

    today_sigs = parse_action_signatures(today_md)
    yest_sigs = parse_action_signatures(yesterday_md)

    added = today_sigs - yest_sigs
    removed = yest_sigs - today_sigs
    common = today_sigs & yest_sigs

    if not (added or removed or executed_rolls or executed_opens):
        return []

    lines = ["", "## Since Yesterday's Briefing", ""]

    if executed_opens:
        lines.append(f"### 🆕 Detected User-Executed Opens "
                     f"({len(executed_opens)})")
        lines.extend(render_executed_open_lines(executed_opens,
                                                entry_grades=entry_grades))
        lines.append("")
    if executed_rolls:
        lines.append(f"### 🔄 Detected User-Executed Rolls "
                     f"({len(executed_rolls)})")
        lines.extend(render_executed_roll_directives(executed_rolls,
                                                     today_iso))
        lines.append("")
    if removed:
        lines.append(f"### ✅ Resolved or Executed ({len(removed)})")
        for sig in sorted(removed):
            kind, ticker = (sig.split("|") + [""])[:2]
            status_note = None
            if recon_status:
                try:
                    from analysis.rec_aging import key_from_signature
                    status = recon_status.get(key_from_signature(sig))
                except Exception:
                    status = None
                if status:
                    status_note = _RECON_STATUS_LABELS.get(
                        status, f"❔ {status}")
            if status_note:
                lines.append(f"- {kind.strip()} {ticker.strip()} — {status_note}")
            else:
                lines.append(f"- {kind.strip()} {ticker.strip()} — no longer in today's list "
                             f"(likely executed or position closed)")
        lines.append("")

    if added:
        lines.append(f"### 🆕 New Today ({len(added)})")
        for sig in sorted(added):
            kind, ticker = (sig.split("|") + [""])[:2]
            lines.append(f"- {kind.strip()} {ticker.strip()} — newly surfaced this briefing")
        lines.append("")

    if common:
        lines.append(f"### 🔁 Unchanged ({len(common)})")
        lines.append(f"- {len(common)} actions repeat from yesterday — re-evaluate or execute.")
        lines.append("")

    return lines
