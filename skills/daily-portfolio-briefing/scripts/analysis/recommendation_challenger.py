"""Recommendation challenger — the briefing's built-in devil's advocate.

A disciplined process never relays a recommendation at face value: it states the
counter-case. This module runs a deterministic, multi-perspective challenge over
every actionable item in the Action List and surfaces objections both inline
(a "⚖️ Counterpoint:" sub-bullet under each action) and in a consolidated
"⚖️ Counterpoints / Second Opinion" panel.

Perspectives (per action kind):
  - Consistency    — does the action contradict the position's own advisor?
  - Opportunity cost — what upside / theta / capital does it give up?
  - Tenor          — does it lock the position for an excessively long horizon?
  - Tax            — does it realize a loss or a short-term gain?
  - Concentration  — does it add to an already-heavy name?
  - Valuation      — price vs intrinsic value (DCF + analyst target).

Deterministic and side-effect free. Parses the rendered Action List (the source
of truth for what is being recommended) and enriches each item with structured
context passed in by the caller. Prompted by the 2026-05-22 SMH miss — see
CLAUDE.md "Challenge every recommendation — multi-perspective".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

try:
    from analysis import rsi_discipline
except ImportError:  # pragma: no cover
    import rsi_discipline  # type: ignore


_ACTION_HEADER_RE = re.compile(r"^\s*(\d+)\.\s+\*\*([A-Z][A-Z _]+)\*\*\s+(.*)")


def _money(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"([\d,]+(?:\.\d+)?)", text.replace("$", ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _first(pattern: str, text: str, group: int = 1, flags: int = 0):
    m = re.search(pattern, text, flags)
    return m.group(group) if m else None


@dataclass
class Challenge:
    n: int                 # action number
    kind: str              # CLOSE / EXECUTE ROLL / HEDGE / TRIM / NEW CSP / ...
    ticker: str
    points: list[str] = field(default_factory=list)   # counterpoint sentences


# ---------------------------------------------------------------------------
# Per-action-kind challenge rules
# ---------------------------------------------------------------------------

def _ctx_get(ctx: dict, key: str, default=None):
    return (ctx or {}).get(key, default) if ctx else default


def _fv_note(ticker: str, ctx: dict) -> str | None:
    """Short valuation phrase from intrinsic value, or None."""
    fv = (_ctx_get(ctx, "fv_by_ticker", {}) or {}).get(ticker)
    spot = (_ctx_get(ctx, "spot_by_ticker", {}) or {}).get(ticker)
    if not fv or not spot:
        return None
    bits = []
    dcf = fv.get("dcf")
    tgt = fv.get("analyst_target")
    if dcf:
        bits.append(f"DCF ${dcf:,.0f} ({(dcf - spot) / spot * 100:+.0f}%)")
    if tgt:
        bits.append(f"analyst target ${tgt:,.0f} ({(tgt - spot) / spot * 100:+.0f}%)")
    return " · ".join(bits) if bits else None


def _challenge_roll(ticker: str, header: str, body: str, ctx: dict) -> list[str]:
    cps: list[str] = []
    text = header + "\n" + body
    contract = _first(r"\b([A-Z]{1,6}_(?:PUT|CALL)_[0-9_]+)", text) or ""
    orev = (_ctx_get(ctx, "options_by_contract", {}) or {}).get(contract, {})
    opt_type = (orev.get("type") or ("CALL" if "_CALL_" in text else "PUT" if "_PUT_" in text else "")).upper()

    # Consistency — should not contradict an explicit HOLD (guard; Part A prevents it).
    advisor = (orev.get("recommendation") or "").upper()
    if "HOLD" in advisor or orev.get("recommended_candidate_id") == "A":
        cps.append("the position's own roll advisor recommends HOLD — rolling here "
                   "contradicts it; confirm the roll is truly needed.")

    # Tenor — long lock.
    days = _first(r"adds?\s+(\d[\d,]*)\s+more days", text) or _first(r"(\d[\d,]*)d\s+of theta runway", text)
    tenor = _money(days)
    if tenor and tenor > 180:
        cps.append(f"locks the position for ~{int(tenor)} more days — a shorter roll keeps "
                   "flexibility to ratchet the strike up as the stock moves.")

    # Opportunity cost / cap (covered calls).
    if opt_type == "CALL":
        new_strike = _money(_first(r"Sell-to-Open[^$]*\$([\d,]+(?:\.\d+)?)C", text))
        credit = _money(_first(r"([\-−]?\$[\d,]+)\s+net credit", text))
        if new_strike and credit:
            qty = _money(_first(r"(\d+)\s+spreads", text)) or 1
            ceiling = new_strike + (credit / (100 * qty))
            cps.append(f"this caps {ticker} at ${new_strike:,.0f}; your effective ceiling is "
                       f"~${ceiling:,.0f} (strike + premium) — above that you forgo the upside.")
        else:
            cps.append(f"a covered-call roll caps {ticker}'s upside at the new strike for the "
                       "roll's life — if you're bullish, prefer a roll-up or no cap.")
        fv = _fv_note(ticker, ctx)
        if fv:
            cps.append(f"valuation: {fv} — weigh the cap against where analysts/DCF see it.")
    elif opt_type == "PUT":
        cps.append(f"rolling the put defers assignment but extends the capital lock-up; if you'd "
                   f"be happy owning {ticker} at the strike, taking assignment can be cheaper.")
    return cps


def _challenge_close(ticker: str, header: str, body: str, ctx: dict) -> list[str]:
    cps: list[str] = []
    text = header + "\n" + body
    pct = _money(_first(r"\+(\d+)%", text))
    remaining = _money(_first(r"[Rr]emaining ~?\$([\d,]+)", text))
    dte = _money(_first(r"(\d+)d (?:still|left)", text))
    if pct is not None and pct < 50 and (dte or 0) >= 14:
        rem = f"~${remaining:,.0f} of " if remaining else ""
        cps.append(f"only {int(pct)}% captured with {int(dte)}d left — closing now leaves {rem}theta "
                   "on the table; a GTC limit at a higher capture lets it ride risk-free.")
    return cps


def _challenge_hedge(ticker: str, header: str, body: str, ctx: dict) -> list[str]:
    cps: list[str] = []
    text = header + "\n" + body
    # Grab the parenthetical cost "(~$9,700; coverage …" — NOT the strike ($709P).
    cost = _money(_first(r"\(~?\$([\d,]+)", text)) or _money(_first(r"\$([\d,]+)\s*;", text))
    cov = _ctx_get(ctx, "coverage_ratio")
    if cost:
        cps.append(f"the hedge costs ~${cost:,.0f} and decays to zero if the drawdown never "
                   "comes — it's insurance, not a position; size it as such.")
    if cov is not None and cov < 0.7:
        cps.append("closing winners to free collateral raises stress coverage directly — "
                   "consider doing that before paying for protection.")
    return cps


def _challenge_trim(ticker: str, header: str, body: str, ctx: dict) -> list[str]:
    cps: list[str] = []
    text = header + "\n" + body
    tax = _money(_first(r"LTCG ~?\$([\d,]+)", text)) or _money(_first(r"~\$([\d,]+)\s*\)", text))
    cps.append(f"trimming {ticker} realizes a taxable gain"
               + (f" (~${tax:,.0f} LTCG)" if tax else "")
               + " — rolling a covered call up defers the tax while still capping risk.")
    fv = _fv_note(ticker, ctx)
    if fv:
        cps.append(f"valuation: {fv} — if it still has room to target, trimming purely on size "
                   "may cut a winner early.")
    return cps


def _challenge_new_put(ticker: str, header: str, body: str, ctx: dict) -> list[str]:
    cps: list[str] = []
    text = header + "\n" + body
    strike = _money(_first(r"\$([\d,]+(?:\.\d+)?)P", text))
    weight = (_ctx_get(ctx, "weights", {}) or {}).get(ticker)
    cps.append(f"selling this put commits you to buying {ticker}"
               + (f" at ${strike:,.0f}" if strike else "")
               + " — only sell it if you'd genuinely want the shares at that price.")
    fv = _fv_note(ticker, ctx)
    if fv:
        cps.append(f"valuation: {fv} — make sure the strike is a price you'd defend.")
    if weight and weight >= 8:
        cps.append(f"{ticker} is already ~{weight:.0f}% of NLV; assignment concentrates it further.")
    return cps


def _challenge_buy(ticker: str, header: str, body: str, ctx: dict) -> list[str]:
    cps: list[str] = []
    fv = _fv_note(ticker, ctx)
    if fv:
        cps.append(f"valuation: {fv} — buying above fair value chases the move; "
                   "a pullback may offer a better entry.")
    rsi = (_ctx_get(ctx, "rsi_by_ticker", {}) or {}).get(ticker)
    if rsi is not None and rsi >= 65:
        cps.append(f"RSI {rsi:.0f} is extended — scaling in or waiting for a dip reduces entry risk.")
    return cps


_DISPATCH = [
    # Match ROLL, ROLL_OUT, ROLL_OUT_AND_UP, EXECUTE_ROLL, DEFENSIVE_ROLL — the
    # kind is a controlled action token, so a substring match is safe (\bROLL\b
    # missed ROLL_OUT because "_" is a word char).
    (re.compile(r"ROLL", re.I), _challenge_roll),
    (re.compile(r"\bCLOSE\b", re.I), _challenge_close),
    (re.compile(r"\bHEDGE\b", re.I), _challenge_hedge),
    (re.compile(r"\bTRIM\b|REVIEW CORE", re.I), _challenge_trim),
    (re.compile(r"\b(?:PULLBACK )?CSP\b|NEW CSP", re.I), _challenge_new_put),
    (re.compile(r"\b(?:BUY|ADD|COMPLETE)\b", re.I), _challenge_buy),
]


def _challenge_one(kind: str, ticker: str, header: str, body: str, ctx: dict) -> list[str]:
    for rx, fn in _DISPATCH:
        if rx.search(kind):
            return fn(ticker, header, body, ctx)
    return []


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

def challenge_action_list(lines: list[str], context: dict | None = None) -> tuple[list[str], list[str]]:
    """Insert inline '⚖️ Counterpoint:' bullets into the Action List and return
    (new_lines, panel_lines). The panel is the consolidated 'Second Opinion'.

    Only the Action List section is processed (numbered ``N. **KIND** …`` items
    between the action-list header and the next ``## `` section).
    """
    context = context or {}
    known = sorted(
        {(t or "").upper() for t in (context.get("known_tickers") or []) if t},
        key=len, reverse=True,
    )

    out: list[str] = []
    challenges: list[Challenge] = []
    in_action_list = False
    cur: Challenge | None = None
    cur_body: list[str] = []

    def _flush(insert_into: list[str]):
        nonlocal cur, cur_body
        if cur is None:
            return
        pts = _challenge_one(cur.kind, cur.ticker, "", "\n".join(cur_body), context)
        if pts:
            cur.points = pts
            challenges.append(cur)
            # Inline: the single most important objection, under the action block.
            insert_into.append(f"   - ⚖️ **Counterpoint:** {pts[0]}")
        cur = None
        cur_body = []

    for line in lines:
        s = line.strip()
        if s.startswith("## "):
            if in_action_list:
                _flush(out)            # close the last item before leaving the section
            in_action_list = "Action List" in s
            out.append(line)
            continue

        if in_action_list:
            m = _ACTION_HEADER_RE.match(line)
            if m:
                _flush(out)            # close previous item
                kind = m.group(2).strip().upper()
                rest = m.group(3)
                ticker = rsi_discipline.first_known_ticker(line, known) or (
                    _first(r"\b([A-Z]{1,6})(?:_PUT|_CALL|\b)", rest) or "")
                # Normalize a full option contract (SMH_CALL_595_…) down to its
                # underlying so messages/labels read "SMH", not the contract.
                if ticker and ("_PUT" in ticker or "_CALL" in ticker):
                    ticker = ticker.split("_")[0]
                cur = Challenge(n=int(m.group(1)), kind=kind, ticker=ticker)
                cur_body = [line]
                out.append(line)
                continue
            if cur is not None and (s.startswith("-") or s == "" or line.startswith("   ")):
                cur_body.append(line)
                out.append(line)
                continue
            # A non-indented, non-header line ends the current item.
            _flush(out)
        out.append(line)

    if in_action_list:
        _flush(out)

    panel = _render_panel(challenges)
    return out, panel


def _render_panel(challenges: list[Challenge]) -> list[str]:
    if not challenges:
        return []
    lines = [
        "## ⚖️ Counterpoints / Second Opinion",
        "",
        "_Every action above, stress-tested from the other side. Read the objection "
        "before you place the trade — the briefing's job is to argue against itself._",
        "",
    ]
    for c in challenges:
        label = f"**{c.n}. {c.kind} {c.ticker}**".rstrip()
        lines.append(label)
        for p in c.points:
            lines.append(f"- {p}")
        lines.append("")
    return lines
