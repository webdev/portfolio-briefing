"""Single source of truth for "today's net option cash".

Origin — one-voice violation observed on the real 2026-08-07 briefing
(rule #43): the digest's 💰 Money Plan said

    "**Net option cash today:** $-948 (entry premium + roll credits − buybacks)"

while the SAME day's action list "### 📋 Total Impact" said

    "**Net cash today (mid-fills):** −$1,985"

for the SAME three actions (META BTC, VRT BTC, QCOM two-leg roll).
Root causes, measured:

  - The Money Plan summed ONLY the buyback cost of the two profit closes at
    the reviews' current_mid (META $4.50 + VRT $4.975 → −$947.5 ≈ −$948) and
    dropped the QCOM roll entirely — ROLL kinds were neither a "bank" nor a
    "deploy", so its −$2,615 net debit never entered the sum.
  - Total Impact regex-harvested "Locks $+362 profit" / "Locks $+268 profit"
    (realized P/L, NOT cash flow) and netted them against the roll's −$2,615
    debit → 362 + 268 − 2,615 = −$1,985. Mixing P/L with cash.

Neither number was the action set's actual option cash. Both surfaces now
derive their number from :func:`compute_net_option_cash` and render the SAME
composition string — one computation, spelled out per component
(e.g. "−$948 buybacks − $2,615 roll debit = −$3,563"), stated once, reused.

Fill basis: mid-fills throughout — closes at the review's ``current_mid``,
rolls at the ticket's own rendered "Net: ±$X debit/credit" (itself composed
from chain mids), deploys at the idea's mid. An action that cannot be priced
from measured data is EXCLUDED and counted in ``unpriced`` (rendered as a
visible "excluded" note) — never estimated, never $0 (rules #10/#19).
"""

from __future__ import annotations

import re

# A block containing any of these markers is NOT actionable today.
# (render/money_plan.py imports this — keep the single source here.)
SKIP_MARKERS = (
    "🚫", "⏸", "Skip —", "SKIPPED", "capacity gated", "capacity-gated",
    "EARNINGS CONFLICT", "WASH-SALE BLOCKED", "DEFERRED",
)

_DEPLOY_KINDS = {"NEW_CSP", "PULLBACK_CSP", "NEW_WEEKLY"}

# Two-leg ticket net, e.g. "Net: −$2,615 debit." (block #3 roll composition)
# or "net +$1,234 credit" (TAKE PROFIT via roll-down). The credit/debit WORD
# carries the sign — the glyph before $ varies (−, –, -, +).
_TICKET_NET_RE = re.compile(
    r"[Nn]et:?\s*[+−–-]?\$([\d,]+(?:\.\d+)?)\s*(?:net\s+)?(credit|debit)")

# "+$470 net credit" — amount BEFORE the word "net" (the URGENT — EXECUTE
# ROLL headline form, 2026-08-17 QCOM $180P). "net" between $ and the
# credit/debit word keeps it from matching per-share limit prose like
# "Start at $6.30 credit/share".
_TICKET_NET_ALT_RE = re.compile(
    r"[+−–-]?\$([\d,]+(?:\.\d+)?)\s+net\s+(credit|debit)")

_HEDGE_COST_RE = re.compile(r"cost\s*\*{0,2}~?\$([\d,]+(?:\.\d+)?)")


def _f(v, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _is_hold_kind(kind: str) -> bool:
    """HOLD / GTC verbs — a resting order, not a fill (rule #43, 2026-08-14:
    'HOLD — GTC AT 50% NOK $11P' is NOT banked today and NOT a buyback;
    counting it dragged the Money Plan to −$7,100 buybacks while Total
    Impact honestly said −$5,320)."""
    k = (kind or "").upper()
    return "GTC" in k or k.startswith("HOLD")


def _is_close_kind(kind: str) -> bool:
    k = (kind or "").upper()
    if _is_hold_kind(k):
        return False  # a GTC/hold never fills at mid today
    return "CLOSE" in k or k.startswith("TAKE_PROFIT")


def gtc_hold_idents(action_list_lines: list) -> set:
    """Contract idents the composed action list resolved to a HOLD / GTC
    verb. Shared by every surface that folds in playbook closes, so a
    contract the briefing says to HOLD can never be counted as a close."""
    return {b["ident"] for b in actionable_blocks(action_list_lines or [])
            if _is_hold_kind(b["kind"])}


# Render-time demotion flags set by render/panels.py on an options review
# whose close was pulled OUT of the action list (redeploy-aware hold,
# dollar-floor convenience close). Rule #43 (2026-08-17): the 08-14 NOK
# lesson generalized — ANY demotion path, not just HOLD/GTC verbs.
_DEMOTION_FLAGS = ("_redeploy_hold_demotion", "_close_floor_demotion")


def demoted_close_idents(options_reviews: list | None) -> set:
    """Contract idents whose review carries a render-time close DEMOTION.

    Observed on the real 2026-08-17 briefing: the Money Plan said
    "Bank today: 4 close(s) → $+4,637 realized (SMH $710C, SNDK $1230P,
    SOXX $520P, IREN $47P)" while the SAME block's Blocked money said
    "2 winners held for 75%+ (no redeploy path: SNDK $1230P, SOXX $520P)"
    — the playbook fold-in banked SNDK/SOXX although the redeploy-aware
    hold had demoted both out of the action list. The bank/buyback lines
    must key on the FINAL rendered action set: a hold-demoted winner is
    NEVER banked."""
    out: set = set()
    for r in (options_reviews or []):
        if not isinstance(r, dict) or not r.get("contract"):
            continue
        if any(r.get(k) for k in _DEMOTION_FLAGS):
            out.add(r["contract"])
    return out


def held_back_idents(action_list_lines: list,
                     options_reviews: list | None = None) -> set:
    """HOLD/GTC verbs ∪ render-time demotions — every contract the FINAL
    action set decided NOT to close today. Single source for every surface
    that folds in playbook closes (one voice, rule #43)."""
    return gtc_hold_idents(action_list_lines) \
        | demoted_close_idents(options_reviews)


def _is_roll_kind(kind: str) -> bool:
    return "ROLL" in (kind or "").upper()


def fmt_money(v: float) -> str:
    """−$1,234 / +$1,234 (U+2212 minus, matching the briefing's glyphs).

    Rounds half-AWAY-from-zero (Decimal ROUND_HALF_UP on the magnitude),
    not Python's banker's rounding: with mid-derived halves like −$947.50
    and −$3,562.50 the default half-even produced "−$948 … = −$3,562" — a
    composition whose terms (948 + 2,615 = 3,563) visibly disagreed with
    its own total by $1."""
    from decimal import ROUND_HALF_UP, Decimal
    mag = int(Decimal(str(abs(v))).quantize(Decimal("1"),
                                            rounding=ROUND_HALF_UP))
    sign = "−" if v < 0 else "+"
    return f"{sign}${mag:,}"


def actionable_blocks(action_list_lines: list) -> list[dict]:
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
        if any(m in text for m in SKIP_MARKERS):
            continue
        out.append({"kind": a["kind"], "ident": a["ident"], "text": text})
    return out


def compute_net_option_cash(
    action_list_lines: list,
    options_reviews: list | None = None,
    new_ideas: list | None = None,
    playbook: dict | None = None,
) -> dict:
    """Compute today's net option cash from the composed action list.

    Components (all mid-fill, all measured this cycle):
      buybacks — CLOSE / TAKE PROFIT blocks: −(review current_mid × 100 × |qty|)
      rolls    — any block carrying a two-leg "Net: ±$X debit/credit" ticket
      deploys  — NEW_CSP / PULLBACK_CSP / NEW_WEEKLY: +(idea mid × 100 × n)
      hedge    — HEDGE blocks: −(rendered "cost $X")
    ``playbook`` (optional) adds the Rotation Playbook's composed closes and
    opens, de-duplicated against action-list components, each labelled
    "(playbook)" so a caller that can't pass the playbook (the Total Impact
    card renders before the playbook composes) stays reconcilable line-by-line.

    Returns a dict with ``components`` (list of {group, label, cash}),
    per-group totals, ``net_cash``, ``unpriced`` (labels of actions excluded
    because no measured price exists — never estimated, never $0), and
    ``composition`` — the human equation both surfaces render verbatim.
    """
    reviews_by_contract = {
        r.get("contract"): r for r in (options_reviews or [])
        if isinstance(r, dict) and r.get("contract")
    }
    ideas_by_ticker: dict[str, dict] = {}
    for i in (new_ideas or []):
        if isinstance(i, dict) and i.get("ticker"):
            ideas_by_ticker.setdefault(str(i["ticker"]).upper(), i)

    components: list[dict] = []
    unpriced: list[str] = []
    counted_close_idents: set = set()
    counted_deploy_tickers: set = set()

    for blk in actionable_blocks(action_list_lines):
        kind, ident, text = blk["kind"], blk["ident"], blk["text"]
        label = f"{kind} {ident}".strip()

        # Two-leg ticket net (rolls, and TAKE PROFIT via roll-down): the
        # rendered "Net: ±$X debit/credit" IS the block's cash — both legs
        # already netted from chain mids.
        m = _TICKET_NET_RE.search(text) or _TICKET_NET_ALT_RE.search(text)
        if m and (_is_roll_kind(kind)
                  or (_is_close_kind(kind) and "roll" in text.lower())):
            amt = _f(m.group(1).replace(",", ""))
            cash = amt if m.group(2).lower() == "credit" else -amt
            components.append({"group": "rolls", "label": label, "cash": cash})
            counted_close_idents.add(ident)
            continue

        if _is_roll_kind(kind):
            # A roll without a parseable net ticket cannot be priced —
            # excluded and flagged, never guessed (rule #19).
            unpriced.append(label)
            continue

        if _is_close_kind(kind):
            rev = reviews_by_contract.get(ident)
            mid = _f(rev.get("current_mid")) if isinstance(rev, dict) else 0.0
            qty = abs(_f(rev.get("qty"))) if isinstance(rev, dict) else 0.0
            if mid > 0 and qty > 0:
                components.append({
                    "group": "buybacks", "label": label,
                    "cash": round(-(mid * 100.0 * qty), 2)})
                counted_close_idents.add(ident)
            else:
                unpriced.append(label)
            continue

        if kind in _DEPLOY_KINDS:
            idea = ideas_by_ticker.get(str(ident).upper())
            mid = _f(idea.get("mid")) if isinstance(idea, dict) else 0.0
            contracts = (_f(idea.get("contracts")) or 1) if isinstance(idea, dict) else 0
            if mid > 0 and str(ident).upper() not in counted_deploy_tickers:
                counted_deploy_tickers.add(str(ident).upper())
                components.append({
                    "group": "deploys", "label": label,
                    "cash": round(mid * 100.0 * contracts, 2)})
            elif mid <= 0:
                unpriced.append(label)
            continue

        if kind.upper() == "HEDGE":
            hm = _HEDGE_COST_RE.search(text)
            if hm:
                components.append({
                    "group": "hedge", "label": label,
                    "cash": -_f(hm.group(1).replace(",", ""))})
            else:
                unpriced.append(label)
            continue
        # Other kinds (equity TRIM/EXIT, WATCH…) are not option cash.

    # ── Playbook composition (when the caller has it) ────────────────────
    # A playbook close whose contract the action list resolved to HOLD/GTC
    # OR demoted at render time (redeploy hold, dollar floor) is dropped —
    # the briefing's own FINAL verdict wins (one voice, rule #43; the
    # 2026-08-17 SNDK/SOXX bank-while-held contradiction).
    hold_idents = held_back_idents(action_list_lines, options_reviews)
    pb = playbook if isinstance(playbook, dict) else {}
    for c in pb.get("closes") or []:
        if not isinstance(c, dict) or c.get("contract") in counted_close_idents:
            continue
        if c.get("contract") in hold_idents:
            continue
        btc_mid = _f(c.get("buy_to_close_mid"))
        qty = abs(_f(c.get("qty")) or 1)
        if btc_mid > 0:
            components.append({
                "group": "buybacks",
                "label": f"{c.get('ticker', '?')} (playbook)",
                "cash": round(-(btc_mid * 100.0 * qty), 2)})
    for o in pb.get("opens") or []:
        if not isinstance(o, dict):
            continue
        tk = str(o.get("ticker") or "").upper()
        prem = _f(o.get("premium"))
        if tk and tk not in counted_deploy_tickers and prem > 0:
            counted_deploy_tickers.add(tk)
            components.append({
                "group": "deploys", "label": f"{tk} (playbook)",
                "cash": prem})

    totals = {g: 0.0 for g in ("buybacks", "rolls", "deploys", "hedge")}
    for comp in components:
        totals[comp["group"]] += comp["cash"]
    net_cash = sum(totals.values())

    return {
        "components": components,
        "total_buybacks": round(totals["buybacks"], 2),
        "total_rolls": round(totals["rolls"], 2),
        "total_deploys": round(totals["deploys"], 2),
        "total_hedge": round(totals["hedge"], 2),
        "net_cash": round(net_cash, 2),
        "unpriced": unpriced,
        "composition": format_composition(totals, net_cash, unpriced,
                                          components),
    }


def format_composition(totals: dict, net_cash: float, unpriced: list,
                       components: list) -> str:
    """The spelled-out equation both surfaces render verbatim, e.g.
    "−$948 buybacks − $2,615 roll debit = −$3,563". Every term is a real
    per-action sum; unpriced actions are called out, never folded in."""
    n_buybacks = sum(1 for c in components if c["group"] == "buybacks")
    parts: list[str] = []
    if totals.get("buybacks"):
        parts.append(f"{fmt_money(totals['buybacks'])} "
                     f"buyback{'s' if n_buybacks != 1 else ''}")
    if totals.get("rolls"):
        word = "roll debit" if totals["rolls"] < 0 else "roll credit"
        parts.append(f"{fmt_money(totals['rolls'])} {word}")
    if totals.get("deploys"):
        parts.append(f"{fmt_money(totals['deploys'])} new premium")
    if totals.get("hedge"):
        parts.append(f"{fmt_money(totals['hedge'])} hedge cost")

    if not parts:
        s = "$0 (no priced option-cash actions today)"
    elif len(parts) == 1:
        s = f"{parts[0]} = {fmt_money(net_cash)}"
    else:
        s = " ".join(parts) + f" = {fmt_money(net_cash)}"
    if unpriced:
        s += (f" · {len(unpriced)} action(s) unpriced — excluded, "
              "verify at broker")
    return s
