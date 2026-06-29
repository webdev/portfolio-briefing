"""Per-company Candidate Research report.

A standalone dated report (candidates_DATE.md) covering EVERY company across the
Scout's themes. For each company it renders a research card — RSI, trend vs
200-SMA, IV rank, drawdown, 5-day momentum, valuation (FMP DCF + analyst target),
and the Scout's verdict — and attaches a concrete actionable entry (a CSP ticket
from the live E*TRADE chain, or a BUY note) ONLY when the setup qualifies AND
passes the RSI discipline gate.

It reuses the Scout's existing research (``scout_payload["results_by_theme"]``)
so it adds no new technical fetches; the caller supplies cached fair values.
Deterministic over its inputs; the only side effect is the per-run verdict
state file (``state/scout_verdicts.yaml``, 12-entry-pipeline-spec §4/§7) that
``render_candidate_report`` reads for the flip-audit section and rewrites so
``when_to_enter`` can align its statuses with this report's verdicts.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from analysis import rsi_discipline, intrinsic_value, verdict_state
    from analysis.capacity_gates import check_new_entry
    from steps import thematic_research as _tr
except ImportError:  # pragma: no cover - path fallback
    from analysis import rsi_discipline, intrinsic_value, verdict_state
    from analysis.capacity_gates import check_new_entry
    import thematic_research as _tr  # type: ignore


_STATUS_LABEL = {
    "candidate": "🎯 CANDIDATE",
    "held_rsi": "⏸ HELD (RSI)",
    "watch": "👀 WATCH",
    "avoid": "🔴 AVOID",
}
_STATUS_ORDER = {"candidate": 0, "held_rsi": 1, "watch": 2, "avoid": 3}


def _verdict_side(verdict: str) -> str | None:
    v = (verdict or "").upper()
    if v.startswith("CSP"):
        return rsi_discipline.PUT
    if v.startswith("BUY"):
        return rsi_discipline.BUY
    return None


def _status(r: dict, rsi_th: dict):
    """Return (status, RecVerdict|None) for one research result."""
    verdict = r.get("verdict", "")
    side = _verdict_side(verdict)
    if side is None:
        return ("avoid" if verdict.upper().startswith("AVOID") else "watch"), None
    rv = rsi_discipline.hook(side, r.get("rsi_14"), rsi_th)
    return ("held_rsi" if rv.removed else "candidate"), rv


# DCF/FV estimates farther than this from spot trigger the sanity check
# (12-entry-pipeline-spec §6).
_FV_DIVERGENCE_PCT = 0.60


def _fv_note(tk: str, spot, fv: dict | None, etf_set) -> str | None:
    """FV line with DCF sanity suppression (12-entry-pipeline-spec §6).

    When the DCF sits >60% from spot AND disagrees in direction with the
    analyst price target, the number is replaced with an explicit
    "unreliable" marker (a $11 DCF under a $138 spot with analysts at $170
    undermines every other number on the page). With no analyst target to
    arbitrate, the number is kept but flagged as a divergent model estimate.
    """
    note = intrinsic_value.format_fv_note(tk, spot, fv, etf_set=etf_set)
    if not note or not fv or not spot:
        return note
    dcf = fv.get("dcf")
    tgt = fv.get("analyst_target")
    if not dcf:
        return note
    try:
        divergence = abs(float(dcf) - float(spot)) / float(spot)
    except (TypeError, ValueError, ZeroDivisionError):
        return note
    if divergence <= _FV_DIVERGENCE_PCT:
        return note
    if tgt:
        try:
            disagrees = (float(dcf) - float(spot)) * (float(tgt) - float(spot)) < 0
        except (TypeError, ValueError):
            disagrees = False
        if disagrees:
            return "💵 FV: unreliable (model/consensus divergence)"
        return note  # divergent from spot but consensus agrees on direction
    return f"{note} (model estimate — large divergence from spot)"


def _format_card(r: dict, fv_by_ticker: dict, etf_set, rsi_th: dict,
                 gate_state=None) -> list[str]:
    tk = (r.get("ticker") or "").upper()
    spot = r.get("spot")
    spot_s = f"${spot:.2f}" if spot else "?"
    status, rv = _status(r, rsi_th)
    rsi_val = r.get("rsi_14")
    # RSI override labelling (12-entry-pipeline-spec §5): a CANDIDATE whose
    # RSI sits outside the 35-50 entry band only qualifies via the override
    # path (deep drawdown + third-party BUY) — label it explicitly instead of
    # presenting the RSI as favourable.
    is_override = (status == "candidate" and rsi_val is not None
                   and not verdict_state.rsi_in_band(rsi_val))
    ovr = (verdict_state.override_label(rsi_val, drawdown=r.get("drawdown_pct"),
                                        buy_rec=verdict_state.has_buy_rec(r))
           if is_override else "")
    badge = ""
    if is_override:
        badge = ""  # never badge an out-of-band RSI as favourable
    elif rv and rv.promoted:
        badge = " ✅ RSI favourable"
    elif rv and rv.badge:
        badge = f" {rv.badge}"

    # CLAUDE.md hard rule #25: when the scout produces an "independent
    # setup" verdict (RSI + IV qualify but no third-party BUY), surface
    # the entry but flag it so the user knows to apply their own
    # catalyst check rather than relying on Parkev's sheet.
    is_independent = "INDEPENDENT" in (r.get("verdict") or "").upper()
    if is_independent:
        badge = f"{badge} ⚠ no third-party rec — verify independently".strip()
        badge = f" {badge}" if not badge.startswith(" ") else badge

    # CLAUDE.md hard rule #26: conviction badge (only on candidates with a
    # third-party rec — INDEPENDENT setups already carry their own warning).
    tier = r.get("rating_tier") or 0
    conviction = r.get("conviction")  # "High" / "Medium" / "Low" / None
    if conviction and not is_independent and status == "candidate":
        if tier >= 4 and conviction == "High":
            badge = f"{badge} 🏆 TOP CONVICTION".strip()
        elif tier >= 4 and conviction == "Low":
            badge = f"{badge} ⚠ low-conviction (tier-4 BUT analyst soft)".strip()
        elif tier == 3 and conviction == "High":
            badge = f"{badge} 🔥 high conviction".strip()
        elif tier == 3 and conviction == "Low":
            badge = f"{badge} 🟡 low conviction — trial size".strip()
        badge = f" {badge}" if not badge.startswith(" ") else badge

    out = [f"**{_STATUS_LABEL[status]} · `{tk}` · {spot_s}**{badge}"]

    metrics = []
    if is_override:
        metrics.append(ovr)
    elif r.get("rsi_14") is not None:
        metrics.append(rsi_discipline.tag(r["rsi_14"]))  # side-agnostic read
    tp = _tr._trend_phrase(r)
    if tp:
        metrics.append(tp)
    if r.get("iv_rank") is not None:
        metrics.append(f"IV rank {r['iv_rank']:.0f}")
    hp = _tr._highs_phrase(r)
    if hp:
        metrics.append(hp)
    if r.get("fivedayret_pct") is not None:
        metrics.append(f"5d {r['fivedayret_pct']:+.1f}%")
    if metrics:
        out.append("  - " + " · ".join(metrics))

    # Concrete entry (or held-by-RSI note) FIRST, right under the RSI metrics
    # line — keeps the actionable ticket adjacent to its RSI read (also so the
    # RSI-coverage audit always finds an RSI within its window).
    #
    # IMPORTANT (2026-06-15 user pushback): when capacity gates are CLOSED
    # the system used to REPLACE the entry ticket with "— blocked: ...". The
    # user pointed out that hiding the opportunity is the wrong move — they
    # still want to SEE what the strike + premium would be so they can plan
    # for when capacity reopens. We now render the FULL entry ticket with a
    # ⏸ DEFERRED tag instead. The capacity reason itself appears once at the
    # top of the Today's Candidates section (see render_candidate_briefing).
    capacity_blocked = gate_state is not None and not gate_state.open
    if status == "candidate":
        q = r.get("csp_entry")
        if rsi_val is None:
            # A concrete ticket may never render without an RSI value
            # (12-entry-pipeline-spec §5 — fail closed, live-data rule #1).
            out.append("  - — no ticket: RSI unavailable (fail closed)")
        elif q:
            exp = q.get("expiration") or ""
            try:
                exp = date.fromisoformat(q["expiration"]).strftime("%a %b %d '%y")
            except (ValueError, KeyError, TypeError):
                pass
            ovr_s = f" · {ovr}" if is_override else ""
            tag = "⏸ **Deferred (capacity gated)** · " if capacity_blocked else "**Entry (CSP):** "
            out.append(
                f"  - {tag}SELL 1× {tk} ${q.get('strike', 0):g}P exp **{exp}** "
                f"({q.get('dte', '?')} DTE) · mid ${q.get('mid', 0):.2f} "
                f"(bid ${q.get('bid', 0):.2f} / ask ${q.get('ask', 0):.2f}) · _Live E*TRADE chain_{ovr_s}"
            )
        elif (r.get("verdict") or "").upper().startswith("BUY"):
            rsi_read = ovr if is_override else "RSI favourable"
            tag = "⏸ **Deferred (capacity gated)** · " if capacity_blocked else "**Entry (equity):** "
            out.append(f"  - {tag}BUY `{tk}` on this pullback — {rsi_read}; size per your plan.")
        else:
            out.append("  - _Entry: setup qualifies, but no live chain ticket available — verify before placing._")
    elif status == "held_rsi" and rv:
        out.append(f"  - _Held back by RSI: {rv.reason}_")

    note = _fv_note(tk, spot, fv_by_ticker.get(tk), etf_set)
    if note:
        out.append(f"  - {note}")

    if r.get("verdict"):
        rationale = "; ".join(r.get("rationale") or [])
        out.append(f"  - Verdict: {r['verdict']}" + (f" — {rationale}" if rationale else ""))

    if r.get("days_to_earnings") is not None:
        out.append(f"  - Earnings: {r.get('earnings_date')} ({r['days_to_earnings']}d away)")

    return out


def render_candidate_report(scout_payload: dict | None, *, fv_by_ticker: dict | None,
                            config: dict | None, generated_at: str,
                            gate_state=None, verdict_state_path=None) -> str:
    """Render the full per-company candidate research report as markdown.

    ``gate_state`` (optional ``analysis.capacity_gates.GateState``) makes the
    report capacity-aware (06-wheel-parameters.md §7A / 12-entry-pipeline-spec):
    the gate banner is the first line; when gates are CLOSED no entry ticket is
    rendered (cards show "— blocked: <reason>" instead); headline CANDIDATE
    cards are capped at 3 (overflow one-lined under "More qualifying names");
    and names failing the per-name/per-expiry caps render under "Blocked"
    instead of as CANDIDATE. ``gate_state=None`` preserves legacy behavior.

    Verdict-flip audit trail (12-entry-pipeline-spec §4): this run's verdict
    classes are diffed against the previous run's stored verdicts
    (``verdict_state_path``, default ``state/scout_verdicts.yaml``); any class
    change prints under "Verdict changes since last run" with the trigger
    derived from the changed key inputs. The state file is then rewritten so
    the next run — and the same-run ``when_to_enter`` renderer (§7) — reads
    THIS run's verdicts."""
    if not scout_payload:
        head = f"{gate_state.banner}\n\n" if gate_state is not None else ""
        return f"{head}# Candidate Research — {generated_at}\n\n_No scout data available._\n"

    rsi_th = rsi_discipline.load_thresholds(config)
    etf_set = intrinsic_value.default_etf_set(config)
    fv_by_ticker = {(k or "").upper(): v for k, v in (fv_by_ticker or {}).items()}
    # Theme metadata fresh from YAML (config, not data — see thematic_research).
    themes_meta = _tr._fresh_theme_meta() or scout_payload.get("themes", {})
    results_by_theme = scout_payload.get("results_by_theme", {})

    # Capacity pre-pass — per-name blocks + headline cap (only when the caller
    # supplied a gate_state; otherwise everything below is a no-op).
    blocked_by_name: dict[str, str] = {}
    headline: set | None = None
    overflow: list = []
    if gate_state is not None:
        cand_by_tk: dict[str, dict] = {}
        for results in results_by_theme.values():
            for r in results:
                if (r.get("verdict") or "").startswith("NO DATA") or r.get("spot") is None:
                    continue
                tk = (r.get("ticker") or "").upper()
                if not tk or tk in cand_by_tk:
                    continue
                if _status(r, rsi_th)[0] == "candidate":
                    cand_by_tk[tk] = r
        if gate_state.open:
            # Per-name / per-expiry caps only matter when gates are open —
            # when closed, every entry is already blocked globally.
            for tk, r in cand_by_tk.items():
                q = r.get("csp_entry") or {}
                coll = float(q.get("strike") or 0) * 100 if q else 0.0
                ok, why = check_new_entry(
                    tk, coll, q.get("expiration"),
                    gate_state.positions, gate_state.nlv, gate_state, config,
                )
                if not ok:
                    blocked_by_name[tk] = why
        passing = [(tk, r) for tk, r in cand_by_tk.items() if tk not in blocked_by_name]
        # Rank by conviction score (rating tier, then IV rank) — top 3 headline.
        passing.sort(key=lambda item: (-(item[1].get("rating_tier") or 0),
                                       -(item[1].get("iv_rank") or 0),
                                       item[0]))
        headline = {tk for tk, _ in passing[:3]}
        overflow = passing[3:]

    # Tally statuses across the universe + collect this run's verdict classes
    # (one per ticker — first occurrence wins, matching the briefing de-dup)
    # for the flip audit + the when_to_enter consistency contract (§4/§7).
    tally = {"candidate": 0, "held_rsi": 0, "watch": 0, "avoid": 0}
    total = 0
    run_date = date.today().isoformat()
    curr_verdicts: dict = {}
    for results in results_by_theme.values():
        for r in results:
            if (r.get("verdict") or "").startswith("NO DATA") or r.get("spot") is None:
                continue
            total += 1
            st = _status(r, rsi_th)[0]
            tally[st] += 1
            tk = (r.get("ticker") or "").upper()
            if tk and tk not in curr_verdicts:
                curr_verdicts[tk] = {
                    "verdict": st.upper(),
                    "date": run_date,
                    "key_inputs": verdict_state.key_inputs(r),
                }
    prev_verdicts = verdict_state.load(verdict_state_path)
    flips = verdict_state.flip_lines(prev_verdicts, curr_verdicts)

    lines = []
    if gate_state is not None:
        # Capacity banner — mandatory first line (12-entry-pipeline-spec §1).
        lines.append(gate_state.banner)
        lines.append("")
    lines += [
        f"# Candidate Research — {generated_at}",
        "",
        "_Per-company research across every Scout theme. Each company gets a card "
        "(RSI · trend · IV rank · drawdown · 5-day · valuation · verdict); a concrete "
        "entry is attached only when the setup qualifies AND passes the RSI gate. "
        "Chain tickets are live E*TRADE; valuation is FMP DCF + analyst target "
        "(single stocks only). Not advice — verify before placing._",
        "",
        f"**{total} companies analyzed:** {tally['candidate']} 🎯 CANDIDATE · "
        f"{tally['held_rsi']} ⏸ held by RSI · {tally['watch']} 👀 WATCH · "
        f"{tally['avoid']} 🔴 AVOID",
        "",
    ]

    if flips:
        # Verdict-flip audit trail (12-entry-pipeline-spec §4) — same standard
        # as the briefing's Step 7: no flip without a named trigger.
        lines.append("## Verdict changes since last run")
        lines.append("")
        for fl in flips:
            lines.append(f"- {fl}")
        lines.append("")

    current_group = None
    for theme_key, results in results_by_theme.items():
        usable = [r for r in results
                  if not (r.get("verdict") or "").startswith("NO DATA") and r.get("spot") is not None]
        if not usable:
            continue
        meta = themes_meta.get(theme_key, {})
        group = meta.get("group")
        if group and group != current_group:
            lines.append(f"# ━━━ {group} ━━━")
            lines.append("")
            current_group = group
        lines.append(f"## 🔭 {meta.get('name', theme_key)}")
        anchors = meta.get("anchors") or []
        etfs = meta.get("etfs") or []
        lines.append(
            f"_Companies: {', '.join(str(a) for a in anchors)} · "
            f"ETFs: {', '.join(str(e) for e in etfs) if etfs else '— no dedicated theme ETF'}_"
        )
        lines.append("")
        usable.sort(key=lambda r: (_STATUS_ORDER.get(_status(r, rsi_th)[0], 9), r.get("ticker", "")))
        for r in usable:
            tk = (r.get("ticker") or "").upper()
            if gate_state is not None and _status(r, rsi_th)[0] == "candidate":
                if tk in blocked_by_name:
                    continue  # rendered under "Blocked" below, never as CANDIDATE
                if headline is not None and tk not in headline:
                    continue  # over the 3-headline cap — one-lined below
            lines.extend(_format_card(r, fv_by_ticker, etf_set, rsi_th,
                                      gate_state=gate_state))
            lines.append("")

    if gate_state is not None and overflow:
        lines.append("## More qualifying names")
        lines.append("")
        lines.append("_Setup qualifies, but only the top 3 candidates get headline "
                     "cards (12-entry-pipeline-spec §3)._")
        lines.append("")
        for tk, r in overflow:
            rsi = r.get("rsi_14")
            rsi_s = f"RSI {rsi:.0f} · " if rsi is not None else ""
            lines.append(f"- `{tk}` — {r.get('verdict', '')} · {rsi_s}"
                         f"spot ${r.get('spot', 0):,.2f}")
        lines.append("")

    if gate_state is not None and blocked_by_name:
        lines.append("## Blocked — capacity gates")
        lines.append("")
        lines.append("_These names may not render as CANDIDATE — a per-name or "
                     "per-expiry capacity gate failed (06-wheel-parameters.md §7A)._")
        lines.append("")
        for tk in sorted(blocked_by_name):
            lines.append(f"- `{tk}` — blocked: {blocked_by_name[tk]}")
        lines.append("")

    # Persist this run's verdicts AFTER the diff so the next run (and the
    # same-run when_to_enter renderer, §7) reads this run's state. Fail-soft.
    verdict_state.save(curr_verdicts, verdict_state_path)

    return "\n".join(lines)


# A proposed CSP strike within this fraction of a strike the user already holds
# is treated as "the same trade" (duplicate), not a fresh candidate.
_STRIKE_OVERLAP_PCT = 0.05


def short_puts_by_ticker(positions: list | None) -> dict:
    """Tally the user's OPEN short puts from snapshot positions.

    Shape (matches the scout's put-stack guard): {TICKER: {"count": int,
    "strikes": [float]}}. Only short (qty < 0) PUT options are counted.
    """
    out: dict = {}
    for p in positions or []:
        if (p.get("assetType") or "").upper() != "OPTION":
            continue
        if (p.get("type") or "").upper() != "PUT":
            continue
        try:
            qty = float(p.get("qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty >= 0:  # long puts aren't a stacking concern for selling more
            continue
        tk = (p.get("underlying") or "").upper()
        if not tk:
            continue
        try:
            strike = float(p.get("strike", 0) or 0)
        except (TypeError, ValueError):
            strike = 0.0
        entry = out.setdefault(tk, {"count": 0, "strikes": []})
        entry["count"] += int(abs(qty)) or 1
        if strike > 0:
            entry["strikes"].append(strike)
    return out


def long_puts_by_ticker(positions: list | None) -> dict:
    """Tally the user's OPEN LONG puts (protective puts / collar floors).

    Shape mirrors short_puts_by_ticker. A long put at/near a proposed short-put
    strike means recommending the short would *cancel* the protection — that
    must be a hard block on any new-short-put surface (the META bug, where the
    user held a long $570P as a collar floor and the LT_CSP recommended selling
    a short $570P at the same strike)."""
    out: dict = {}
    for p in positions or []:
        if (p.get("assetType") or "").upper() != "OPTION":
            continue
        if (p.get("type") or "").upper() != "PUT":
            continue
        try:
            qty = float(p.get("qty", 0) or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if qty <= 0:  # we want LONG puts only
            continue
        tk = (p.get("underlying") or "").upper()
        if not tk:
            continue
        try:
            strike = float(p.get("strike", 0) or 0)
        except (TypeError, ValueError):
            strike = 0.0
        entry = out.setdefault(tk, {"count": 0, "strikes": []})
        entry["count"] += int(abs(qty)) or 1
        if strike > 0:
            entry["strikes"].append(strike)
    return out


def _held_put_overlap(ticker: str, strike, existing_short_puts: dict | None):
    """Return None if the user holds no short puts on ``ticker``; else a dict
    {count, strikes, dupe} where ``dupe`` is True when ``strike`` is within
    _STRIKE_OVERLAP_PCT of a held strike (i.e. effectively the same contract)."""
    if not existing_short_puts:
        return None
    e = existing_short_puts.get((ticker or "").upper())
    if not e or not e.get("count"):
        return None
    strikes = e.get("strikes", []) or []
    dupe = False
    if strike:
        dupe = any(s > 0 and abs(float(strike) - s) / s <= _STRIKE_OVERLAP_PCT for s in strikes)
    return {"count": int(e.get("count", 0)), "strikes": sorted(strikes), "dupe": dupe}


def _long_put_cancellation(ticker: str, strike, existing_long_puts: dict | None):
    """Return the held LONG-put strike that would be CANCELLED by selling a
    short put at ``strike`` (within _STRIKE_OVERLAP_PCT), or None if no held
    long put on this name overlaps. A non-None return is a hard reason to
    refuse the recommendation — selling the same-strike short un-hedges the
    protective long put."""
    if not existing_long_puts or not strike:
        return None
    e = existing_long_puts.get((ticker or "").upper())
    if not e or not e.get("strikes"):
        return None
    for held in e["strikes"]:
        if held > 0 and abs(float(strike) - held) / held <= _STRIKE_OVERLAP_PCT:
            return {"count": int(e.get("count", 0)), "cancels_strike": held,
                    "strikes": sorted(e["strikes"])}
    return None


def render_candidate_briefing(scout_payload: dict | None, *, fv_by_ticker: dict | None,
                              config: dict | None, generated_at: str,
                              as_section: bool = False,
                              existing_short_puts: dict | None = None,
                              existing_long_puts: dict | None = None,
                              gate_state=None,
                              snapshot_data: dict | None = None,
                              analytics: dict | None = None,
                              recommendations_list: list | None = None) -> str:
    """Focused, action-first briefing built FROM the candidates: only the names
    whose setup qualifies AND passes the RSI gate (full entry cards), with the
    RSI-blocked names listed below as 'on deck'. Condensed market context up top.

    ``as_section=True`` demotes the headings one level (## / ###) so it embeds
    cleanly inside the daily briefing rather than standing alone (# / ##).

    ``existing_short_puts`` ({TICKER: {count, strikes}}, from
    ``short_puts_by_ticker``) makes the candidate list position-aware: a CSP
    candidate whose strike duplicates a put the user already holds (within
    _STRIKE_OVERLAP_PCT) is pulled out of the actionable list into an "already
    positioned" note — you can't "newly" sell a contract you're already short.
    A same-name candidate at a different strike is kept but annotated as
    stacking single-name risk."""
    _h_top = "## 🎯 Candidate Trades — Across Themes" if as_section else f"# Candidate Trade Briefing — {generated_at}"
    _h_sub = "###" if as_section else "##"
    if not scout_payload:
        return f"{_h_top}\n\n_No scout data available._\n"

    rsi_th = rsi_discipline.load_thresholds(config)
    etf_set = intrinsic_value.default_etf_set(config)
    fv_by_ticker = {(k or "").upper(): v for k, v in (fv_by_ticker or {}).items()}
    themes_meta = _tr._fresh_theme_meta() or scout_payload.get("themes", {})
    rbt = scout_payload.get("results_by_theme", {})

    # A ticker can anchor several themes (e.g. QCOM in Semis + Applications) but
    # it's one trade — de-dup the flat briefing by ticker (the full per-theme
    # report keeps it under each theme).
    cands: list[tuple[str, dict]] = []
    held: list[tuple[str, dict, object]] = []
    already_open: list[tuple[str, dict, dict]] = []  # candidate duplicates a held put
    seen: set[str] = set()
    for theme_key, results in rbt.items():
        tname = themes_meta.get(theme_key, {}).get("name", theme_key)
        for r in results:
            if (r.get("verdict") or "").startswith("NO DATA") or r.get("spot") is None:
                continue
            tk = (r.get("ticker") or "").upper()
            if tk in seen:
                continue
            status, rv = _status(r, rsi_th)
            if status == "candidate":
                seen.add(tk)
                # Position-aware: if this CSP candidate duplicates a put the
                # user already holds, it's not a new trade — pull it out.
                q = r.get("csp_entry") or {}
                # CRITICAL: long-put cancellation check first — a short put at
                # the same strike as a held LONG put cancels protection (the
                # META collar-floor case). That's a hard refuse, not a "stack."
                cancel = _long_put_cancellation(tk, q.get("strike"), existing_long_puts) if q else None
                overlap = _held_put_overlap(tk, q.get("strike"), existing_short_puts) if q else None
                if cancel:
                    # Tag the result with the cancellation context so the
                    # "Already positioned" footer can explain it precisely.
                    r = {**r, "_cancels_long_put": cancel}
                    already_open.append((tname, r, {"count": cancel["count"],
                                                    "strikes": cancel["strikes"],
                                                    "dupe": True,
                                                    "cancels_protection": True,
                                                    "cancels_strike": cancel["cancels_strike"]}))
                elif overlap and overlap["dupe"]:
                    already_open.append((tname, r, overlap))
                else:
                    cands.append((tname, r))
            elif status == "held_rsi":
                seen.add(tk)
                held.append((tname, r, rv))

    lines = [
        _h_top,
        "",
    ]
    if gate_state is not None:
        # Capacity banner (06-wheel-parameters.md §7A) — when CLOSED, no
        # concrete entry tickets render in the cards below.
        lines.append(f"**{gate_state.banner}**")
        lines.append("")
    lines += [
        "_Action-first: trade candidates pulled from the per-company Scout research — "
        "names where the setup qualifies AND passes the RSI gate. Each carries a live "
        "entry, RSI, valuation, and rationale. Not advice; verify before placing._",
        "",
    ]

    live = _tr._live_results(rbt)
    movers = [r for r in live if r.get("fivedayret_pct") is not None]
    if movers:
        n = len(movers)
        up = sum(1 for r in movers if r["fivedayret_pct"] > 0)
        ob = sum(1 for r in live if (r.get("rsi_14") or 0) >= 70)
        osold = sum(1 for r in live if r.get("rsi_14") is not None and r["rsi_14"] < 35)
        lines.append(
            f"**Market context:** {up}/{n} green over the past week · "
            f"{ob} overbought (>70) / {osold} oversold (<35) across {n} analyzed names."
        )
        lines.append("")

    if cands:
        lines.append(f"{_h_sub} 🎯 Today's Candidates ({len(cands)})")
        lines.append("")
        # CAPACITY status banner — when entry gates are closed, surface the
        # reason ONCE at the top of the section so the per-candidate cards
        # don't have to repeat it. User explicit rule (2026-06-15): never
        # hide good opportunities — show the full entry ticket even when
        # the system can't act on it today, so the user can plan.
        if gate_state is not None and not gate_state.open:
            first_reason = gate_state.reasons[0] if gate_state.reasons else "capacity gates closed"
            lines.append(f"> 🔒 **CAPACITY: New CSP entries blocked — {first_reason}**")
            lines.append(f"> These candidates are still SHOWN below (with full strikes + premiums) "
                         f"as DEFERRED. Free up cash via the action list closes; once stress "
                         f"coverage clears 0.50× the gate reopens and these become actionable.")
            lines.append("")
        # Lazy-load the pre-trade validator so older callers (tests) that
        # don't pass snapshot_data still work — validator findings are
        # additive context, never required for the candidate to render.
        try:
            from analysis import pre_trade_validator as _ptv
        except ImportError:
            _ptv = None
        for tname, r in sorted(cands, key=lambda x: (x[0], x[1].get("ticker", ""))):
            card = _format_card(r, fv_by_ticker, etf_set, rsi_th, gate_state=gate_state)
            card[0] = f"{card[0]}  · _{tname}_"
            # Same-name (different-strike) stacking note — kept, but flagged.
            tk = (r.get("ticker") or "").upper()
            q = r.get("csp_entry") or {}
            ov = _held_put_overlap(tk, q.get("strike"), existing_short_puts) if q else None
            if ov:
                strikes_s = ", ".join(f"${s:g}" for s in ov["strikes"])
                card.append(
                    f"  - ⚠ **You already hold {ov['count']}× {tk} PUT** at {strikes_s} — "
                    f"this would stack single-name assignment risk; size accordingly or skip."
                )
            # Pre-trade validation: run the proposed CSP through the discipline
            # rules and surface any BLOCKs/WARNs inline. Only fires when we
            # have both a concrete chain quote AND the snapshot to validate
            # against — silent otherwise (no false positives from missing data).
            if _ptv is not None and snapshot_data and q.get("strike") and q.get("expiration"):
                try:
                    ctx = _ptv.build_context_from_snapshot(
                        snapshot_data,
                        ticker=tk,
                        strike=float(q["strike"]),
                        expiration=q["expiration"],
                        option_type="PUT",
                        action="SELL_OPEN",
                        quantity=1,
                        limit_price=q.get("mid"),
                        analytics=analytics,
                        recommendations_list=recommendations_list,
                    )
                    findings = _ptv.validate_proposed_trade(ctx, config)
                    # Filter out rules ALREADY communicated elsewhere in the
                    # card to avoid duplicate noise. The capacity banner at
                    # the top of the section already states ENTRY_GATES_CLOSED;
                    # the position-aware overlap note above covers existing-
                    # short-put stacking; the RSI gate is on the metrics line;
                    # the earnings warning is on the Earnings line.
                    SILENT = {"ENTRY_GATES_CLOSED"}  # rendered in capacity banner
                    if ov:
                        SILENT.add("ROLL_UP_RISK_INCREASE")
                    visible = [f for f in findings if f.rule_id not in SILENT]
                    if visible:
                        for f in visible:
                            emoji = "🚫" if f.severity == "BLOCK" else "⚠️"
                            card.append(f"  - {emoji} **{f.severity}** ({f.rule_id}) — {f.reason}")
                except Exception:
                    # Validator is enrichment, never load-bearing — silent fail.
                    pass
            lines.extend(card)
            lines.append("")
    else:
        lines.append(f"{_h_sub} 🎯 Today's Candidates (0)")
        lines.append("")
        lines.append("_No RSI-favorable candidates right now — the universe is broadly extended. "
                     "The On-Deck names below would activate on a pullback into the favorable RSI zone._")
        lines.append("")

    if held:
        lines.append(f"{_h_sub} ⏸ On Deck — qualifying setup, blocked only by RSI ({len(held)})")
        lines.append("")
        for tname, r, rv in sorted(held, key=lambda x: (x[0], x[1].get("ticker", ""))):
            tk = (r.get("ticker") or "").upper()
            lines.append(
                f"- **`{tk}`** ({tname}) — {r.get('verdict', '')} · "
                f"{rsi_discipline.tag(r.get('rsi_14'))} — _{rv.reason}_"
            )
        lines.append("")

    if already_open:
        lines.append(f"{_h_sub} ⏸ Already positioned — you hold this put ({len(already_open)})")
        lines.append("_Suppressed from the actionable list: the proposed strike matches a put "
                     "you're already short, OR — worse — would cancel a protective long put._")
        lines.append("")
        for tname, r, ov in sorted(already_open, key=lambda x: (x[0], x[1].get("ticker", ""))):
            tk = (r.get("ticker") or "").upper()
            q = r.get("csp_entry") or {}
            strikes_s = ", ".join(f"${s:g}" for s in ov["strikes"])
            if ov.get("cancels_protection"):
                lines.append(
                    f"- 🛡️ **`{tk}`** ({tname}) — scout floated SELL ${q.get('strike', 0):g}P, "
                    f"but you hold a **LONG ${ov['cancels_strike']:g}P** on {tk} as downside "
                    f"protection (collar floor / protective put). Selling a short at the same "
                    f"strike would **cancel that hedge** — don't un-collar a hedged position to "
                    f"collect premium."
                )
            else:
                lines.append(
                    f"- **`{tk}`** ({tname}) — scout floated ${q.get('strike', 0):g}P; "
                    f"you already hold {ov['count']}× {tk} PUT at {strikes_s}. Manage the existing "
                    f"position (see Watch), don't re-open it."
                )
        lines.append("")

    lines.append("_Full per-company detail (WATCH/AVOID + every theme) is in the companion "
                 "candidate research report._")
    return "\n".join(lines)


def single_stock_tickers(scout_payload: dict | None, config: dict | None) -> list[str]:
    """All single-stock tickers in the scout universe (ETFs excluded) — the set
    the caller should fetch fair values for."""
    if not scout_payload:
        return []
    etf_set = intrinsic_value.default_etf_set(config)
    out: set[str] = set()
    for results in (scout_payload.get("results_by_theme") or {}).values():
        for r in results:
            tk = (r.get("ticker") or "").upper()
            if tk and not intrinsic_value.is_etf(tk, etf_set):
                out.add(tk)
    return sorted(out)


def briefing_candidate_tickers(scout_payload: dict | None, config: dict | None) -> list[str]:
    """The small single-stock set that appears in the daily briefing's candidate
    section (actionable candidates + on-deck) — worth pricing inline."""
    if not scout_payload:
        return []
    rsi_th = rsi_discipline.load_thresholds(config)
    etf_set = intrinsic_value.default_etf_set(config)
    out: list[str] = []
    seen: set[str] = set()
    for results in (scout_payload.get("results_by_theme") or {}).values():
        for r in results:
            if (r.get("verdict") or "").startswith("NO DATA") or r.get("spot") is None:
                continue
            tk = (r.get("ticker") or "").upper()
            if not tk or tk in seen or intrinsic_value.is_etf(tk, etf_set):
                continue
            if _status(r, rsi_th)[0] in ("candidate", "held_rsi"):
                seen.add(tk)
                out.append(tk)
    return out


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _skill_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> int:
    import argparse
    import json
    import os
    from datetime import datetime
    try:
        import yaml
    except Exception:
        yaml = None

    ap = argparse.ArgumentParser(
        description="Per-company candidate research across all Scout themes "
                    "(research card + RSI-gated entry + FMP valuation).")
    ap.add_argument("--refresh-scout", action="store_true",
                    help="Re-run the thematic scout now (else use the 24h scout cache).")
    ap.add_argument("--briefing", action="store_true",
                    help="Render the focused Candidate Trade Briefing (actionable candidates "
                         "+ on-deck) instead of the full per-company report.")
    ap.add_argument("--output", default=None,
                    help="Output path (default: ~/Documents/briefings/candidates[_briefing]_DATE.md).")
    ap.add_argument("--config", default=None,
                    help="Path to briefing.yaml (RSI bands + ETF list).")
    args = ap.parse_args()

    # Load .env (FMP_API_KEY etc.) the same way the briefing does, so the
    # standalone CLI picks up the key without the user exporting it.
    try:
        from etrade_auth import _load_dotenv_if_present  # type: ignore
        _load_dotenv_if_present()
    except Exception:
        pass

    skill = _skill_dir()
    snap_root = skill / "state" / "briefing_snapshots"

    config: dict = {}
    cfg_path = Path(args.config) if args.config else (skill / "config" / "briefing.yaml")
    if yaml and cfg_path.exists():
        try:
            config = yaml.safe_load(cfg_path.read_text()) or {}
        except Exception:
            config = {}

    # Scout payload — fresh research or the 24h cache.
    payload = None
    cache_file = snap_root / "scout_cache.json"
    if args.refresh_scout:
        dated = sorted([p for p in snap_root.glob("20*") if p.is_dir()]) if snap_root.exists() else []
        snap_dir = dated[-1] if dated else (snap_root / datetime.now().strftime("%Y-%m-%d"))
        scout_mod = _tr._load_scout_module()
        recs, weights, puts = {}, {}, {}
        if scout_mod and hasattr(scout_mod, "_load_recs_and_weights"):
            try:
                recs, weights, puts = scout_mod._load_recs_and_weights()
            except Exception:
                recs, weights, puts = {}, {}, {}
        print("Refreshing thematic scout (researching all theme tickers — ~30-60s)...",
              file=sys.stderr)
        payload = _tr.run_thematic_research(
            snapshot_dir=snap_dir, recs_map=recs, held_weights=weights,
            existing_short_puts=puts, refresh=True,
        )
    elif cache_file.exists():
        try:
            payload = json.loads(cache_file.read_text())
        except Exception:
            payload = None

    if not payload:
        print("No scout data found. Run the daily briefing once (it populates the "
              "scout cache), or pass --refresh-scout to research now.", file=sys.stderr)
        return 1

    fv: dict = {}
    fmp = os.getenv("FMP_API_KEY")
    tickers = single_stock_tickers(payload, config)
    if tickers and fmp:
        print(f"Fetching FMP valuation for {len(tickers)} companies (cached 24h)...",
              file=sys.stderr)
        fv = intrinsic_value.get_fair_values(
            tickers, cache_path=snap_root / "intrinsic_value_cache.json", api_key=fmp)
    elif not fmp:
        print("(FMP_API_KEY not set — valuations will show n/a.)", file=sys.stderr)

    _gen_at = datetime.now().strftime("%A, %B %d, %Y · %I:%M %p")
    render = render_candidate_briefing if args.briefing else render_candidate_report
    md = render(payload, fv_by_ticker=fv, config=config, generated_at=_gen_at)

    _stem = "candidate_briefing" if args.briefing else "candidates"
    if args.output:
        out = Path(args.output).expanduser()
    else:
        deliv = Path(os.getenv("PORTFOLIO_BRIEFING_DELIVERY_DIR",
                               str(Path.home() / "Documents" / "briefings"))).expanduser()
        out = deliv / f"{_stem}_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"Candidate research written to: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
