"""💰 Money Plan — the briefing's opening panel: "what makes me money today."

Origin: George's audit of the 2026-08-04 briefing — "sometimes I am confused
by recommendations. we are looking to make money here." The plan answers the
money question in ≤6 lines at the VERY TOP of the briefing (above Stalled
Items), before any risk plumbing.

Every number is measured this cycle (hard rule #19 — no fabrication):
  - **Bank / Deploy today** — ONLY actionable numbered items from the
    composed action list (deferred/gated/skipped blocks are excluded),
    cross-referenced against options_reviews / new_ideas for the dollar
    math; plus the Rotation Playbook's composed closes/opens.
  - **Coverage after** — the playbook's existing projected math
    (coverage_before → coverage_after); falls back to the current stress
    coverage ratio when no playbook composed this cycle.
  - **Month so far** — MATCHED per-contract realized P/L on option
    positions closed this month, from position diffs across
    ``state/briefing_snapshots/*/positions.json`` (the same source
    briefing_diff uses): realized = entry credit (premiumReceived) −
    last-known buyback mark, summed by close date. NEVER the raw
    option cash-flow number — buybacks paid this month against credits
    collected last month rendered a profitable harvest as "$-27,868
    option premium" (rule #43 follow-up, 2026-08-04). Contracts whose
    entry credit isn't recoverable are EXCLUDED with a "(N of M closes
    matched)" note; below half matched → "n/a (ledger pending — matched
    P/L needs entry credits)" — honest, never a fabricated number.
  - **Blocked money** — capacity-gated/deferred entries (with the measured
    gate ratio) + the hedge-nag standing question (fix 4).

Fail-open everywhere: any error → the panel is omitted and the briefing
ships. The section is exempt from the RSI coverage audit (a rollup of items
detailed — with RSI — below, same as the Capital Plan).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


# Action kinds that BANK money today (close/lock a winner). URGENT/loss-stop
# closes realize losses and are risk actions, not money-plan banks.
_BANK_KIND_RE = re.compile(
    r"^(CLOSE|CLOSE_FOR_PROFIT|CLOSE_WINNER|CLOSE_INTO_RECOVERY"
    r"|TAKE_PROFIT.*)$")
_DEPLOY_KINDS = {"NEW_CSP", "PULLBACK_CSP", "NEW_WEEKLY"}

# A block containing any of these markers is NOT actionable today.
# Single source: analysis/net_option_cash.py (the shared net-cash module) —
# imported so the Money Plan and the Total Impact card can never disagree on
# what counts as actionable. Fallback tuple kept for standalone use.
try:
    from analysis.net_option_cash import SKIP_MARKERS as _SKIP_MARKERS
except ImportError:  # pragma: no cover — standalone use
    _SKIP_MARKERS = (
        "🚫", "⏸", "Skip —", "SKIPPED", "capacity gated", "capacity-gated",
        "EARNINGS CONFLICT", "WASH-SALE BLOCKED", "DEFERRED",
    )

_NET_CREDIT_RE = re.compile(r"net \+\$([\d,]+(?:\.\d+)?) credit")


def _f(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _contract_label(rev: dict) -> str:
    """Compact human label for a contract review: 'VRT $280P'."""
    und = rev.get("underlying") or str(rev.get("contract", "")).split("_")[0]
    strike = _f(rev.get("strike"))
    side = "P" if (rev.get("type") or "").upper() == "PUT" else "C"
    if strike:
        return f"{und} ${strike:g}{side}"
    return und or str(rev.get("contract", ""))


def _actionable_blocks(action_list_lines: list) -> list[dict]:
    """Parse the composed action list into actionable {kind, ident, text}
    dicts — numbered blocks only, skip-marked blocks excluded."""
    try:
        from analysis.rec_aging import _split_action_blocks, action_from_line
    except ImportError:  # pragma: no cover — standalone use
        return []
    blocks, _footer = _split_action_blocks(list(action_list_lines or []))
    out: list[dict] = []
    for b in blocks:
        a = action_from_line(b[0])
        if not a:
            continue
        text = "\n".join(b)
        if any(m in text for m in _SKIP_MARKERS):
            continue
        out.append({"kind": a["kind"], "ident": a["ident"], "text": text})
    return out


def _coverage_now(analytics: dict | None):
    sc = (analytics or {}).get("stress_coverage")
    if sc is None:
        return None
    cov = getattr(sc, "coverage_ratio", None)
    if cov is None and isinstance(sc, dict):
        cov = sc.get("coverage_ratio")
    try:
        return float(cov) if cov is not None else None
    except (TypeError, ValueError):
        return None


def _theta_per_day(snapshot_data: dict | None):
    """Portfolio theta engine in $/day from the snapshot's option positions
    (theta per share × 100 × qty; short options carry negative theta and
    negative qty → positive daily income). None when no measured thetas."""
    total = 0.0
    measured = False
    for p in (snapshot_data or {}).get("positions") or []:
        if p.get("assetType") != "OPTION":
            continue
        th = p.get("theta")
        if th is None:
            continue
        total += _f(th) * 100.0 * _f(p.get("qty"))
        measured = True
    return total if measured else None


_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_positions(day_dir: Path) -> list | None:
    try:
        with open(day_dir / "positions.json") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("positions") or []
    return None


def _mtd_matched_realized(snapshot_dir, month_prefix: str):
    """Matched per-contract realized P/L for option positions closed during
    the current month, detected via position diffs across the snapshot
    history (same source as briefing_diff).

    For each close event (contract present in one snapshot, gone or reduced
    in the next, with the diff day inside ``month_prefix``):
      realized = entry credit (premiumReceived per share, from the position
                 record while it was open) − last-known mark (currentMid on
                 the last snapshot it appeared — the buyback proxy when the
                 diff-day fill isn't recorded), × 100 × closed qty.
    Longs mirror (mark − costPerShare). A close whose entry credit or exit
    mark isn't recoverable is EXCLUDED and counted unmatched — never
    estimated (rule #19). Equity closes are summed the same way
    (price − costBasis per share) when derivable.

    Returns {"realized", "matched", "closes", "equity_realized"} or None
    when snapshot history is unreadable (< 2 snapshot days)."""
    try:
        root = Path(snapshot_dir).parent
        days = sorted(
            d for d in root.iterdir()
            if d.is_dir() and _DATE_DIR_RE.match(d.name)
            and (d / "positions.json").exists())
    except (OSError, TypeError):
        return None
    if len(days) < 2:
        return None
    realized = 0.0
    equity_realized = 0.0
    matched = 0
    closes = 0
    for prev_dir, curr_dir in zip(days, days[1:]):
        if not curr_dir.name.startswith(month_prefix):
            continue  # close attributed to the diff day — must be in-month
        prev = _load_positions(prev_dir)
        curr = _load_positions(curr_dir)
        if prev is None or curr is None:
            continue
        curr_by_sym = {p.get("symbol"): p for p in curr
                       if isinstance(p, dict) and p.get("symbol")}
        for p in prev:
            if not isinstance(p, dict) or not p.get("symbol"):
                continue
            q_prev = _f(p.get("qty"))
            c = curr_by_sym.get(p["symbol"])
            q_curr = _f(c.get("qty")) if isinstance(c, dict) else 0.0
            closed_qty = abs(q_prev) - abs(q_curr)
            if closed_qty <= 0:
                continue
            if p.get("assetType") == "OPTION":
                closes += 1
                exit_mid = p.get("currentMid")
                is_short = q_prev < 0
                entry = (p.get("premiumReceived") if is_short else None)
                if entry is None:
                    entry = p.get("costPerShare")
                if exit_mid is None or entry is None or _f(entry) <= 0:
                    continue  # unmatched — excluded, never estimated
                per_share = (_f(entry) - _f(exit_mid)) if is_short \
                    else (_f(exit_mid) - _f(entry))
                realized += per_share * 100.0 * closed_qty
                matched += 1
            elif p.get("assetType") == "EQUITY":
                px, cb = p.get("price"), p.get("costBasis")
                if px is not None and cb is not None and _f(cb) > 0:
                    equity_realized += (_f(px) - _f(cb)) * closed_qty
    return {"realized": realized, "matched": matched, "closes": closes,
            "equity_realized": equity_realized}


def _gated_entries(new_ideas: list | None,
                   long_term_opportunities: list | None) -> list[dict]:
    """Deferred/skipped new-open entries (capacity or discipline gated)."""
    out: list[dict] = []
    for op in list(long_term_opportunities or []) + list(new_ideas or []):
        if not isinstance(op, dict):
            continue
        kind = str(op.get("kind") or "").upper()
        if kind.startswith("SKIPPED") or kind.startswith("DEFERRED") \
                or kind == "CAPACITY_GATES_BLOCKED":
            out.append(op)
    return out


def _monthly_unlock(gated: list[dict]) -> float | None:
    """Σ premium×30/dte over gated entries that carry MEASURED premium+dte.
    None when nothing is measurable (the clause is then omitted — rule #19)."""
    total = 0.0
    measured = False
    for op in gated:
        src = op.get("concrete_trade") if isinstance(op.get("concrete_trade"), dict) else op
        prem = _f(src.get("premium") or 0)
        if not prem:
            mid = _f(src.get("mid") or 0)
            contracts = _f(src.get("contracts") or 0) or 1
            prem = mid * 100.0 * contracts
        dte = _f(src.get("dte") or src.get("days_to_expiry") or 0)
        if prem > 0 and dte > 0:
            total += prem * 30.0 / dte
            measured = True
    return total if measured else None


def build_money_plan(
    *,
    date_str: str | None,
    action_list_lines: list,
    options_reviews: list | None,
    new_ideas: list | None,
    playbook: dict | None,
    analytics: dict | None,
    snapshot_data: dict | None,
    config: dict | None,
    attribution: dict | None = None,  # accepted, IGNORED for the month line
    long_term_opportunities: list | None = None,
    aging_info: dict | None = None,
    snapshot_dir=None,
    spread_reference: dict | None = None,
) -> tuple[list[str], dict]:
    """Compose the 💰 Money Plan. Returns (markdown_lines, json_dict);
    ([], {}) when nothing is measurable. Never raises past its own guard —
    callers still wrap it fail-open."""
    reviews_by_contract = {
        r.get("contract"): r for r in (options_reviews or [])
        if isinstance(r, dict) and r.get("contract")
    }
    ideas_by_ticker: dict[str, dict] = {}
    for i in (new_ideas or []):
        if isinstance(i, dict) and i.get("ticker") and i.get("instruction"):
            ideas_by_ticker.setdefault(str(i["ticker"]).upper(), i)

    # ── Bank / Deploy from the composed action list ──────────────────────
    banks: list[dict] = []       # {label, realized, btc_cost, roll_credit}
    deploys: list[dict] = []     # {label, premium, dte, source}
    seen_deploy_tickers: set = set()
    for blk in _actionable_blocks(action_list_lines):
        kind, ident, text = blk["kind"], blk["ident"], blk["text"]
        if _BANK_KIND_RE.match(kind):
            rev = reviews_by_contract.get(ident)
            if not rev:
                continue
            entry = _f(rev.get("entry_price"))
            mid = _f(rev.get("current_mid"))
            qty = abs(_f(rev.get("qty")))
            if entry <= 0 or qty <= 0:
                continue
            realized = (entry - mid) * 100.0 * qty
            if realized <= 0:
                # Loss closes (loss-stop / urgent) are RISK actions, not
                # banks — counting them here would distort "what makes me
                # money today" (the PLTR $200C loss-stop close dragged the
                # 2026-08-04 bank line from +$4,814 to +$159).
                continue
            label = _contract_label(rev)
            roll_credit = 0.0
            if kind.startswith("TAKE_PROFIT"):
                label += " via roll-down"
                m = _NET_CREDIT_RE.search(text)
                if m:
                    roll_credit = _f(m.group(1).replace(",", ""))
            banks.append({
                "label": label, "realized": realized,
                "btc_cost": mid * 100.0 * qty, "roll_credit": roll_credit,
                "kind": kind, "ident": ident,
            })
        elif kind in _DEPLOY_KINDS:
            idea = ideas_by_ticker.get(ident)
            if not idea:
                continue
            mid = _f(idea.get("mid"))
            contracts = _f(idea.get("contracts")) or 1
            dte = int(_f(idea.get("dte") or idea.get("days_to_expiry")) or 0)
            if mid <= 0:
                continue
            if ident in seen_deploy_tickers:
                continue
            seen_deploy_tickers.add(ident)
            deploys.append({
                "label": ident, "premium": mid * 100.0 * contracts,
                "dte": dte, "source": "action_list",
            })

    # ── Playbook opens/closes (the composed rotation) ────────────────────
    pb = playbook if isinstance(playbook, dict) else {}
    pb_close_contracts = {b.get("ident") for b in banks}
    for c in pb.get("closes") or []:
        if not isinstance(c, dict):
            continue
        if c.get("contract") in pb_close_contracts:
            continue
        realized = _f(c.get("realized_profit"))
        banks.append({
            "label": f"{c.get('ticker', '?')} ${_f(c.get('strike')):g}P",
            "realized": realized,
            "btc_cost": _f(c.get("buy_to_close_mid")) * 100.0 * abs(_f(c.get("qty")) or 1),
            "roll_credit": 0.0,
            "kind": "PLAYBOOK_CLOSE", "ident": c.get("contract"),
        })
    pb_open_tickers: list[str] = []
    for o in pb.get("opens") or []:
        if not isinstance(o, dict):
            continue
        tk = str(o.get("ticker") or "").upper()
        if not tk or tk in seen_deploy_tickers:
            continue
        prem = _f(o.get("premium"))
        if prem <= 0:
            continue
        seen_deploy_tickers.add(tk)
        pb_open_tickers.append(tk)
        deploys.append({
            "label": tk, "premium": prem,
            "dte": int(_f(o.get("dte")) or 0), "source": "playbook",
        })

    # ── Rollups ──────────────────────────────────────────────────────────
    total_realized = sum(b["realized"] for b in banks)
    total_premium = sum(d["premium"] for d in deploys)

    # Net option cash — ONE shared computation with the action list's
    # 📋 Total Impact card (analysis/net_option_cash.py). Rule #43 bug on the
    # 2026-08-07 briefing: this panel said "$-948" (buybacks of the two
    # profit closes only — the QCOM roll's −$2,615 net debit was dropped
    # because ROLL kinds were neither a bank nor a deploy) while Total
    # Impact said "−$1,985" (it netted "Locks $+X profit" P/L against the
    # roll debit). One number now, composition spelled out, reused verbatim
    # by both surfaces.
    noc = None
    try:
        from analysis.net_option_cash import compute_net_option_cash
        noc = compute_net_option_cash(
            action_list_lines, options_reviews=options_reviews,
            new_ideas=new_ideas, playbook=playbook)
    except Exception:
        noc = None
    if noc is not None:
        net_cash = noc["net_cash"]
    else:  # pragma: no cover — legacy fallback (shared module unavailable)
        total_btc = sum(b["btc_cost"] for b in banks)
        total_roll_credit = sum(b["roll_credit"] for b in banks)
        net_cash = total_premium + total_roll_credit - total_btc
    dtes = [d["dte"] for d in deploys if d["dte"] > 0]
    avg_dte = round(sum(dtes) / len(dtes)) if dtes else None

    cov_now = _coverage_now(analytics)
    cov_after = pb.get("coverage_after") if (banks or deploys) else None
    try:
        cov_after = float(cov_after) if cov_after is not None else None
    except (TypeError, ValueError):
        cov_after = None

    # Month so far — matched per-contract realized P/L, NEVER the raw
    # option cash-flow (the attribution's option_premium_net rendered a
    # profitable July-credit/August-buyback harvest as "$-27,868 option
    # premium · pace $-6,967/day"). ``attribution`` is deliberately unused.
    mtd = None
    elapsed_days = None
    if snapshot_dir is not None and date_str:
        try:
            month_prefix = str(date_str)[:7]
            elapsed_days = max(1, int(str(date_str)[8:10]))
            mtd = _mtd_matched_realized(snapshot_dir, month_prefix)
        except (TypeError, ValueError):
            mtd = None
    theta_day = _theta_per_day(snapshot_data)

    gated = _gated_entries(new_ideas, long_term_opportunities)
    unlock = _monthly_unlock(gated)
    # B floor (George 2026-08-12: "recommendations are A or B, not D") —
    # count of new-open recs demoted below the setup floor this cycle so
    # the Blocked-money line explains why the deploy line is smaller.
    below_floor_n = sum(
        1 for i in (new_ideas or [])
        if isinstance(i, dict) and i.get("setup_floor_demoted"))
    below_floor_n += sum(
        1 for op in (long_term_opportunities or [])
        if isinstance(op, dict)
        and str(op.get("kind") or "").upper() == "SKIPPED_SETUP_FLOOR")
    hedge_nag = (aging_info or {}).get("hedge_nag") if aging_info else None
    hedge_days = None
    if isinstance(hedge_nag, dict):
        hedge_days = hedge_nag.get("days")
        # Prefer the aged (post-tick) count when available.
        aged = (aging_info or {}).get("aged") or {}
        a = aged.get(hedge_nag.get("key"))
        if isinstance(a, dict) and a.get("days_flagged"):
            hedge_days = a["days_flagged"]

    # Redeploy-aware TP (George 2026-08-10: "If we don't have a path to
    # redeployment, then it doesn't make sense to close it.") — winners the
    # action list held for the 75%+ zone are NOT banked closes; they show up
    # in Blocked money so the plan explains why the bank line is smaller.
    held_for_more = [
        r for r in (options_reviews or [])
        if isinstance(r, dict) and r.get("_redeploy_hold_demotion")
    ]
    try:
        _rd_target = float(((config or {}).get("redeploy_aware_tp") or {})
                           .get("hold_target_pct", 0.75))
    except (TypeError, ValueError):
        _rd_target = 0.75

    if not (banks or deploys or gated or hedge_nag or mtd or held_for_more
            or below_floor_n):
        return [], {}

    # ── Render (≤6 lines) ────────────────────────────────────────────────
    pretty = ""
    month_name = "Month"
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            pretty = d.strftime(" — %A, %b %-d")
            month_name = d.strftime("%B")
        except ValueError:
            pretty = f" — {date_str}"
    lines = [f"## 💰 Money Plan{pretty}", ""]

    if banks:
        labels = ", ".join(b["label"] for b in banks[:4])
        if len(banks) > 4:
            labels += ", …"
        lines.append(
            f"- **Bank today:** {len(banks)} close(s) → "
            f"${total_realized:+,.0f} realized ({labels})")
    else:
        lines.append("- **Bank today:** none actionable this cycle")

    if deploys:
        labels = ", ".join(d["label"] for d in deploys[:4])
        if len(deploys) > 4:
            labels += ", …"
        src_note = " — playbook" if any(
            d["source"] == "playbook" for d in deploys) else ""
        dte_note = f" over ~{avg_dte}d" if avg_dte else ""
        lines.append(
            f"- **Deploy today:** {len(deploys)} entr"
            f"{'y' if len(deploys) == 1 else 'ies'} → "
            f"+${total_premium:,.0f} premium{dte_note} ({labels}{src_note})")
    else:
        lines.append("- **Deploy today:** none actionable this cycle")

    if noc is not None:
        cash_line = (f"- **Net option cash today (mid-fills):** "
                     f"{noc['composition']}")
    else:  # pragma: no cover — legacy fallback
        cash_line = (f"- **Net option cash today:** ${net_cash:+,.0f} "
                     f"(entry premium + roll credits − buybacks)")
    if cov_now is not None and cov_after is not None \
            and abs(cov_after - cov_now) >= 0.005:
        cash_line += f" · **Coverage after:** {cov_now:.2f}× → ~{cov_after:.2f}×"
    elif cov_now is not None:
        cash_line += f" · **Coverage:** {cov_now:.2f}×"
    lines.append(cash_line)

    if mtd is None:
        lines.append(f"- **{month_name} so far:** n/a (ledger pending)")
    elif mtd["closes"] and mtd["matched"] * 2 < mtd["closes"]:
        # Fewer than half the closes have recoverable entry credits —
        # never the raw cash-flow number, never a fabricated total.
        lines.append(
            f"- **{month_name} so far:** n/a (ledger pending — matched "
            f"P/L needs entry credits; {mtd['matched']} of "
            f"{mtd['closes']} closes matched)")
    else:
        mtd_total = mtd["realized"] + mtd["equity_realized"]
        month_bit = (
            f"- **{month_name} so far:** ${mtd['realized']:+,.0f} realized "
            f"on {mtd['matched']} closed contract"
            f"{'' if mtd['matched'] == 1 else 's'}")
        if mtd["matched"] < mtd["closes"]:
            month_bit += (f" ({mtd['matched']} of {mtd['closes']} "
                          "closes matched)")
        if mtd["equity_realized"]:
            month_bit += (f" · ${mtd['equity_realized']:+,.0f} equity "
                          "realized")
        if elapsed_days:
            month_bit += f" · ${mtd_total / elapsed_days:,.0f}/day pace"
            if theta_day is not None:
                month_bit += f" vs ${theta_day:,.0f}/day theta engine"
        lines.append(month_bit)

    blocked_bits: list[str] = []
    if gated:
        gate_bit = f"{len(gated)} entr{'y' if len(gated) == 1 else 'ies'} gated"
        if cov_now is not None:
            try:
                floor = float(((config or {}).get("capacity_gates") or {})
                              .get("min_coverage_ratio", 0.50))
            except (TypeError, ValueError):
                floor = 0.50
            if cov_now < floor:
                gate_bit += f" (coverage {cov_now:.2f}× < {floor:.2f}×)"
        blocked_bits.append(gate_bit)
    # Task #42 — informational only (reference mode changes NO gate): what
    # buying power would spread-mode need for today's gated entries vs the
    # cash-secured collateral? Only entries with a REAL composed long leg
    # count (spread_composer.spread_reference_summary), never extrapolated.
    if isinstance(spread_reference, dict) and spread_reference.get("count"):
        _sr_n = int(spread_reference["count"])
        blocked_bits.append(
            f"spread-mode would run today's {_sr_n} gated "
            f"entr{'y' if _sr_n == 1 else 'ies'} at "
            f"~${_f(spread_reference.get('spread_bp')):,.0f} BP vs "
            f"${_f(spread_reference.get('csp_collateral')):,.0f} "
            f"cash-secured")
    if held_for_more:
        _hl = ", ".join(_contract_label(r) for r in held_for_more[:4])
        blocked_bits.append(
            f"{len(held_for_more)} winner"
            f"{'' if len(held_for_more) == 1 else 's'} held for "
            f"{_rd_target * 100:.0f}%+ (no redeploy path: {_hl})")
    if below_floor_n:
        blocked_bits.append(
            f"{below_floor_n} rec(s) below setup floor")
    if hedge_nag and hedge_days:
        blocked_bits.append(
            f"hedge undecided {int(hedge_days)}d — standing question")
    if unlock is not None and unlock > 0:
        blocked_bits.append(
            f"biggest unlock: +${unlock:,.0f} premium/mo when gates open")
    if blocked_bits:
        lines.append("- **Blocked money** (why not more): "
                     + " · ".join(blocked_bits))
    lines.append("")

    plan_json = {
        "banks": banks,
        "deploys": deploys,
        "total_realized": round(total_realized, 2),
        "total_premium": round(total_premium, 2),
        "net_cash": round(net_cash, 2),
        # Shared per-component breakdown (analysis/net_option_cash.py) —
        # the same dict the Total Impact card derives its line from.
        "net_cash_components": noc,
        "avg_deploy_dte": avg_dte,
        "coverage_now": cov_now,
        "coverage_after": cov_after,
        "mtd": ({"basis": "matched_realized",
                 "realized": round(mtd["realized"], 2),
                 "matched": mtd["matched"], "closes": mtd["closes"],
                 "equity_realized": round(mtd["equity_realized"], 2),
                 "elapsed_days": elapsed_days} if mtd else None),
        "theta_per_day": (round(theta_day, 2)
                          if theta_day is not None else None),
        "gated_entry_count": len(gated),
        # B floor (George 2026-08-12) — recs demoted below the setup floor.
        "below_setup_floor_count": below_floor_n,
        # Redeploy-aware TP: winners held for the raised capture floor
        # because no redeployment path exists (not banked closes).
        "held_for_more": [
            {"label": _contract_label(r),
             "note": r.get("_redeploy_hold_demotion")}
            for r in held_for_more
        ],
        # Task #42 — spread-mode BP rollup for gated entries (informational).
        "spread_reference": spread_reference,
        "monthly_unlock": (round(unlock, 2) if unlock is not None else None),
        "hedge_nag": hedge_nag,
        # Bullet lines (without the '- ' prefix) for the webapp panel.
        "lines": [ln[2:] for ln in lines if ln.startswith("- ")],
    }
    return lines, plan_json
