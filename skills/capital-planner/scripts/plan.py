"""
Capital planner — turns the briefing's structured recommendation lists into a
ranked, cash-flow-aware execution plan.

Two input modes:
  1. Structured: equity_reviews + options_reviews + new_ideas + ... — preferred
     when the orchestrator has access to them, but the wheel-roll-advisor's
     review schema doesn't surface "this is a CLOSE because of 30% capture"
     directly (that decision is made in the renderer).
  2. Markdown extraction: pass in the rendered action_list_lines and the
     planner regex-extracts CLOSE / ROLL / HEDGE / NEW_CSP / TRIM actions.
     This is what production uses today since the renderer is the source of
     truth for "what is actually being recommended."

The planner combines both sources, dedupes by ticker+kind, and emits the plan.

Public surface:
  build_capital_plan()      — top-level entry point
  format_capital_plan_md()  — render the plan as a briefing panel
  CapitalAction / CapitalPlan dataclasses

The skill consumes structured data from the briefing pipeline (equity_reviews,
options_reviews, new_ideas, long_term_opportunities, balance, positions). It
does NOT parse the rendered markdown — that would couple the planner to the
renderer's exact output.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml


# --------------------------------------------------------------------------
# Data classes
# --------------------------------------------------------------------------

@dataclass
class CapitalAction:
    """One row in the capital plan."""
    kind: str                    # CLOSE | TRIM | ROLL | HEDGE | NEW_CSP | NEW_CC | LT_CSP | LT_LEAP | LT_ADD | LT_TRIM | LT_EXIT
    ticker: str
    description: str             # human-readable summary
    cash_in: float = 0.0         # collateral freed + premium received
    cash_out: float = 0.0        # BTC cost + debit paid + new collateral locked
    new_collateral_locked: float = 0.0
    ev: float | None = None      # from trade-validator if available
    validator_verdict: str | None = None
    weight_pct: float | None = None  # for concentration-aware actions
    tier: int = 3                # 1 = CRITICAL, 2 = IMPORTANT, 3 = OPTIONAL, 4 = DEFER
    tier_reason: str = ""
    skip_reason: str | None = None  # if non-None, the action is filtered

    @property
    def net_cash(self) -> float:
        return self.cash_in - self.cash_out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["net_cash"] = self.net_cash
        return d


@dataclass
class CapitalPlan:
    """Aggregate plan + per-tier action lists."""
    starting_cash: float
    nlv: float
    actions: list[CapitalAction] = field(default_factory=list)
    skipped_actions: list[CapitalAction] = field(default_factory=list)

    # Aggregates (computed in build_capital_plan)
    total_collateral_freed: float = 0.0
    total_collateral_locked: float = 0.0
    total_premium_received: float = 0.0
    total_btc_cost: float = 0.0
    total_debit_paid: float = 0.0
    tax_estimate_ltcg: float = 0.0

    @property
    def net_cash_change(self) -> float:
        return sum(a.net_cash for a in self.actions)

    @property
    def ending_cash_projected(self) -> float:
        return self.starting_cash + self.net_cash_change

    def actions_by_tier(self) -> dict[int, list[CapitalAction]]:
        out: dict[int, list[CapitalAction]] = {1: [], 2: [], 3: [], 4: []}
        for a in self.actions:
            out.setdefault(a.tier, []).append(a)
        return out


# --------------------------------------------------------------------------
# Rule loading
# --------------------------------------------------------------------------

_RULES_PATH = Path(__file__).resolve().parent.parent / "references" / "prioritization_rules.yaml"


def _load_rules() -> dict:
    if not _RULES_PATH.exists():
        return {}
    return yaml.safe_load(_RULES_PATH.read_text()) or {}


# --------------------------------------------------------------------------
# Helpers — extract weight % per ticker
# --------------------------------------------------------------------------

def _weights_by_ticker(positions: list[dict], nlv: float) -> dict[str, float]:
    """Sum equity market value per ticker as % of NLV."""
    weights: dict[str, float] = {}
    if nlv <= 0:
        return weights
    for p in positions:
        if p.get("assetType") != "EQUITY":
            continue
        sym = p.get("symbol")
        if not sym:
            continue
        qty = float(p.get("qty", 0) or 0)
        price = float(p.get("price", 0) or 0)
        mv = qty * price
        weights[sym] = weights.get(sym, 0) + (mv / nlv * 100.0)
    return weights


# --------------------------------------------------------------------------
# Action extractors — one function per recommendation source
# --------------------------------------------------------------------------

_CLOSE_RECS = {
    "CLOSE_NOW", "CLOSE", "TAKE_PROFIT", "CLOSE_FOR_PROFIT", "CLOSE_WINNER",
}


def _close_economics(rev: dict) -> tuple[float, float, float, float]:
    """Derive (btc_cost, collateral_freed, profit_dollars, profit_pct) from the
    real wheel-roll-advisor review schema (current_mid, entry_price, strike,
    qty, type). Falls back to direct fields if test fixture used canonical
    names. Always returns positive dollar amounts."""
    # Direct fields (test fixtures use these)
    if "btc_cost" in rev or "collateral" in rev or "profit_pct" in rev:
        return (
            abs(float(rev.get("btc_cost", 0) or 0)),
            abs(float(rev.get("collateral", 0) or 0)),
            float(rev.get("profit_dollars", 0) or 0),
            float(rev.get("profit_pct", rev.get("captured_pct", 0)) or 0),
        )

    # Real schema: per-share prices × 100 × qty
    qty = abs(float(rev.get("qty", 0) or 0))
    current_mid = float(rev.get("current_mid", 0) or 0)
    entry = float(rev.get("entry_price", 0) or 0)
    strike = float(rev.get("strike", 0) or 0)
    opt_type = (rev.get("type") or "").upper()

    btc_cost = current_mid * 100.0 * qty
    profit_dollars = (entry - current_mid) * 100.0 * qty  # short premium → profit when current < entry
    profit_pct = (profit_dollars / (entry * 100.0 * qty)) if (entry and qty) else 0.0

    # Collateral freed: only short PUTs are cash-secured (strike × 100 × qty).
    # Short calls are share-secured (no cash freed; just unlocks shares for new CCs).
    if opt_type == "PUT" and strike > 0:
        collateral_freed = strike * 100.0 * qty
    else:
        collateral_freed = 0.0
    return btc_cost, collateral_freed, profit_dollars, profit_pct


def _classify_close(rev: dict, rules: dict) -> CapitalAction | None:
    """Convert an option review with a close-the-winner recommendation into a
    CapitalAction. Reads both the canonical `action` field and the real
    `recommendation` / `matrix_cell_id` fields produced by wheel-roll-advisor."""
    action = (rev.get("action") or "").upper()
    rec = (rev.get("recommendation") or "").upper()
    cell = (rev.get("matrix_cell_id") or rev.get("cell") or "").upper()
    is_close = (
        action in _CLOSE_RECS
        or rec in _CLOSE_RECS
        or "CLOSE" in cell or "TAKE_PROFIT" in cell
    )
    if not is_close:
        return None

    contract = rev.get("contract") or rev.get("symbol") or ""
    underlying = rev.get("underlying") or contract.split("_")[0]
    btc_cost, collateral_freed, profit_locked, profit_pct = _close_economics(rev)
    dte = float(rev.get("dte", rev.get("days_to_expiry", 0)) or 0)

    cfg = (rules.get("actions", {}) or {}).get("CLOSE", {})
    threshold = cfg.get("promote_to_critical_if_capture_pct_at_or_above", 0.30)
    short_dte = cfg.get("promote_to_critical_if_dte_at_or_below", 14)

    tier = int(cfg.get("base_tier", 2))
    reason_parts = []
    if profit_pct >= threshold:
        tier = 1
        reason_parts.append(f"≥{threshold*100:.0f}% capture")
    if dte and dte <= short_dte:
        tier = 1
        reason_parts.append(f"DTE {dte:.0f}")
    tier_reason = "; ".join(reason_parts) or "winner-close discipline"

    desc = f"CLOSE {contract} — locks ${profit_locked:+,.0f}, frees ${collateral_freed:,.0f}"
    return CapitalAction(
        kind="CLOSE",
        ticker=underlying,
        description=desc,
        cash_in=collateral_freed,
        cash_out=btc_cost,
        tier=tier,
        tier_reason=tier_reason,
    )


_ROLL_RECS = {"EXECUTE_ROLL", "DEFENSIVE_ROLL", "ROLL", "ROLL_OUT", "ROLL_OUT_AND_DOWN", "ROLL_UP_AND_OUT"}


def _roll_net_cash(rev: dict) -> tuple[float, float]:
    """Return (cash_in, cash_out) for a roll proposal — positive dollars.

    Real wheel-roll-advisor schema exposes the recommended candidate via
    `recommended_candidate_id` + `roll_candidates` list. Each candidate has
    `net_credit_per_share` (negative for debits). Test fixtures may set
    `net_credit` / `net_debit` directly.
    """
    # Test-fixture path
    if "net_credit" in rev:
        v = float(rev.get("net_credit", 0) or 0)
        return (max(0, v), max(0, -v))
    if "net_debit" in rev:
        v = float(rev.get("net_debit", 0) or 0)
        return (max(0, -v), max(0, v))

    # Real schema: pull the recommended candidate
    candidate = rev.get("roll_target") or {}
    if not candidate:
        cands = rev.get("roll_candidates") or []
        rec_id = rev.get("recommended_candidate_id")
        if rec_id and cands:
            for c in cands:
                if c.get("id") == rec_id:
                    candidate = c
                    break
    if not candidate:
        return (0.0, 0.0)

    qty = abs(float(rev.get("qty", 0) or 0))
    # Per-share net is on the candidate; multiply by 100 × qty for total
    per_share = float(
        candidate.get("net_credit_per_share")
        or candidate.get("net_credit")
        or candidate.get("credit_per_share")
        or 0
    )
    total = per_share * 100.0 * qty
    if total >= 0:
        return (total, 0.0)
    return (0.0, -total)


def _classify_roll(rev: dict, rules: dict) -> CapitalAction | None:
    """Convert an option review with a roll proposal into a CapitalAction."""
    action = (rev.get("action") or "").upper()
    rec = (rev.get("recommendation") or "").upper()
    cell = (rev.get("matrix_cell_id") or rev.get("cell") or "").upper()
    is_roll = (
        action in _ROLL_RECS
        or rec in _ROLL_RECS
        or "ROLL" in cell
    )
    if not is_roll:
        return None

    underlying = rev.get("underlying") or ""
    contract = rev.get("contract") or rev.get("symbol") or ""
    cash_in, cash_out = _roll_net_cash(rev)

    days_to_earnings = rev.get("days_to_earnings")
    cfg = (rules.get("actions", {}) or {}).get("ROLL", {})
    defer_dte = cfg.get("defer_if_earnings_inside_dte", 14)

    tier = int(cfg.get("base_tier", 3))
    skip_reason = None

    if days_to_earnings is not None and 0 < days_to_earnings <= defer_dte:
        tier = 4
        skip_reason = f"earnings in {int(days_to_earnings)}d — defer until after print"
    elif cfg.get("promote_to_important_if_credit_received") and cash_in > cash_out:
        tier = 2

    validator_verdict = rev.get("validator_verdict")
    ev = rev.get("ev")

    if cash_in == 0 and cash_out == 0:
        desc = f"ROLL {contract} (advisory — see chain)"
        tier_reason = "no roll target proposed"
    elif cash_in > cash_out:
        desc = f"ROLL {contract} — net +${cash_in - cash_out:,.0f} credit"
        tier_reason = skip_reason or "credit roll"
    else:
        desc = f"ROLL {contract} — net −${cash_out - cash_in:,.0f} debit"
        tier_reason = skip_reason or "debit roll"

    return CapitalAction(
        kind="ROLL",
        ticker=underlying,
        description=desc,
        cash_in=cash_in,
        cash_out=cash_out,
        ev=ev,
        validator_verdict=validator_verdict,
        tier=tier,
        tier_reason=tier_reason,
        skip_reason=skip_reason,
    )


def _classify_trim(rev: dict, weights: dict, rules: dict) -> CapitalAction | None:
    """Convert an equity TRIM review into a CapitalAction."""
    action = (rev.get("action") or "").upper()
    if action != "TRIM":
        return None

    ticker = rev.get("ticker") or ""
    weight_pct = weights.get(ticker, float(rev.get("weight_pct", 0) or 0))
    cash_raised = float(rev.get("trim_dollar_amount", 0) or 0)
    ltcg_rate = (rules.get("ltcg") or {}).get("rate", 0.238)
    tax_cost = cash_raised * ltcg_rate

    cfg = (rules.get("actions", {}) or {}).get("TRIM", {})
    severe = cfg.get("promote_to_critical_if_weight_pct_at_or_above", 12.0)
    tier = int(cfg.get("base_tier", 2))
    if weight_pct >= severe:
        tier = 1
        tier_reason = f"weight {weight_pct:.1f}% — severe concentration breach"
    else:
        tier_reason = f"weight {weight_pct:.1f}% over cap"

    desc = f"TRIM {ticker} — raises ${cash_raised:,.0f} (LTCG ~${tax_cost:,.0f})"
    return CapitalAction(
        kind="TRIM",
        ticker=ticker,
        description=desc,
        cash_in=cash_raised,
        cash_out=0,
        weight_pct=weight_pct,
        tier=tier,
        tier_reason=tier_reason,
    )


def _classify_hedge(hedge_recs: list, coverage_ratio: float | None, rules: dict) -> list[CapitalAction]:
    """Convert hedge_book recommendations into CapitalActions."""
    out: list[CapitalAction] = []
    cfg = (rules.get("actions", {}) or {}).get("HEDGE", {})
    red = cfg.get("promote_to_critical_if_coverage_below", 0.50)
    base_tier = int(cfg.get("base_tier", 3))

    for h in (hedge_recs or []):
        cost = float(h.get("cost", 0) or 0)
        ticker = (h.get("ticker") or h.get("underlying") or "SPY").upper()
        desc = h.get("description") or f"HEDGE {ticker} — cost ${cost:,.0f}"
        tier = base_tier
        tier_reason = "fat-tail insurance"
        if coverage_ratio is not None and coverage_ratio < red:
            tier = 1
            tier_reason = f"stress coverage {coverage_ratio:.2f}× < {red:.2f}× red threshold"
        out.append(CapitalAction(
            kind="HEDGE",
            ticker=ticker,
            description=desc,
            cash_in=0,
            cash_out=cost,
            tier=tier,
            tier_reason=tier_reason,
        ))
    return out


def _classify_new_csp(idea: dict, rules: dict) -> CapitalAction:
    """Convert a new-ideas (PULLBACK CSP) entry into a CapitalAction."""
    ticker = idea.get("ticker") or ""
    strike = float(idea.get("strike", 0) or 0)
    premium = float(idea.get("premium", 0) or 0)
    contracts = float(idea.get("contracts", 1) or 1)
    collateral = float(idea.get("collateral", strike * 100 * contracts) or 0)
    verdict = idea.get("validator_verdict")
    ev = idea.get("ev")

    cfg = (rules.get("actions", {}) or {}).get("NEW_CSP", {})
    tier = int(cfg.get("base_tier", 3))
    tier_reason = "discretionary income trade"
    promote_on = cfg.get("promote_to_important_if_validator_verdict")
    if promote_on and verdict == promote_on:
        tier = 2
        tier_reason = f"validator verdict {verdict}"

    desc = f"NEW CSP {ticker} ${strike:g}P — premium ${premium:,.0f}, locks ${collateral:,.0f}"
    return CapitalAction(
        kind="NEW_CSP",
        ticker=ticker,
        description=desc,
        cash_in=premium,
        cash_out=collateral,
        new_collateral_locked=collateral,
        ev=ev,
        validator_verdict=verdict,
        tier=tier,
        tier_reason=tier_reason,
    )


def _classify_long_term_csp(op: dict, weights: dict, third_party_recs: dict, rules: dict) -> CapitalAction:
    """Convert a LONG_DATED_CSP long-term opportunity into a CapitalAction."""
    ticker = op.get("ticker") or ""
    weight_pct = weights.get(ticker, 0.0)
    rec = (third_party_recs.get(ticker) or "").upper()

    # Parse premium / collateral from yield_or_cost text — falls back gracefully
    premium, collateral = _parse_yield_or_cost(op.get("yield_or_cost", ""))

    cfg = (rules.get("actions", {}) or {}).get("LT_CSP", {})
    cap_pct = cfg.get("skip_if_weight_pct_at_or_above", 10.0)
    near_cap = cfg.get("defer_if_weight_pct_at_or_above", 8.0)
    weak_recs = set(cfg.get("skip_if_third_party_rec_in", []))

    tier = int(cfg.get("base_tier", 3))
    tier_reason = "long-term income (3-12mo horizon)"
    skip_reason: str | None = None

    if weight_pct >= cap_pct:
        tier = 4
        skip_reason = f"already {weight_pct:.1f}% NLV — over {cap_pct:.0f}% cap"
    elif weight_pct >= near_cap:
        tier = 4
        skip_reason = f"already {weight_pct:.1f}% NLV — near {cap_pct:.0f}% cap, would push over"
    elif rec in weak_recs:
        tier = 4
        skip_reason = f"third-party rec is {rec or 'NONE'} — only add on BUY/STRONG_BUY"

    desc = f"LT CSP {ticker} — {op.get('concrete_trade', '')}"
    return CapitalAction(
        kind="LT_CSP",
        ticker=ticker,
        description=desc,
        cash_in=premium,
        cash_out=collateral,
        new_collateral_locked=collateral,
        weight_pct=weight_pct,
        tier=tier,
        tier_reason=skip_reason or "long-term income",
        skip_reason=skip_reason,
    )


def _classify_long_term(op: dict, weights: dict, third_party_recs: dict, rules: dict) -> CapitalAction | None:
    """Route a LongTermOpportunity dict through the right classifier by `kind`."""
    kind = (op.get("kind") or "").upper()
    if kind == "LONG_DATED_CSP":
        return _classify_long_term_csp(op, weights, third_party_recs, rules)
    if kind == "EXIT":
        return CapitalAction(
            kind="LT_EXIT",
            ticker=op.get("ticker", ""),
            description=f"EXIT {op.get('ticker','')} — {op.get('concrete_trade','')}",
            cash_in=0,  # actual cash impact depends on the share count; kept neutral here
            cash_out=0,
            tier=1,
            tier_reason="thesis broken (third-party SELL or major drawdown without support)",
        )
    if kind == "TRIM":
        weight_pct = weights.get(op.get("ticker", ""), 0.0)
        cfg = (rules.get("actions", {}) or {}).get("LT_TRIM", {})
        severe = cfg.get("promote_to_critical_if_weight_pct_at_or_above", 12.0)
        tier = 1 if weight_pct >= severe else int(cfg.get("base_tier", 2))
        return CapitalAction(
            kind="LT_TRIM",
            ticker=op.get("ticker", ""),
            description=f"LT TRIM {op.get('ticker','')} — {op.get('concrete_trade','')}",
            tier=tier,
            tier_reason=f"weight {weight_pct:.1f}%" if weight_pct else "concentration relief",
            weight_pct=weight_pct,
        )
    if kind == "ADD":
        weight_pct = weights.get(op.get("ticker", ""), 0.0)
        cfg = (rules.get("actions", {}) or {}).get("LT_ADD", {})
        skip_at = cfg.get("skip_if_weight_pct_at_or_above", 8.0)
        skip_reason = None
        tier = int(cfg.get("base_tier", 3))
        if weight_pct >= skip_at:
            tier = 4
            skip_reason = f"already {weight_pct:.1f}% NLV — at/above add ceiling"
        return CapitalAction(
            kind="LT_ADD",
            ticker=op.get("ticker", ""),
            description=f"LT ADD {op.get('ticker','')} — {op.get('concrete_trade','')}",
            cash_out=5_000,  # advisor's default initial buy
            tier=tier,
            tier_reason=skip_reason or "long-term equity add",
            skip_reason=skip_reason,
            weight_pct=weight_pct,
        )
    if kind == "LEAP_CALL":
        weight_pct = weights.get(op.get("ticker", ""), 0.0)
        cfg = (rules.get("actions", {}) or {}).get("LT_LEAP", {})
        skip_at = cfg.get("skip_if_weight_pct_at_or_above", 6.0)
        tier = int(cfg.get("base_tier", 3))
        skip_reason = None
        if weight_pct >= skip_at:
            tier = 4
            skip_reason = f"already {weight_pct:.1f}% NLV — don't add LEAP on top"
        return CapitalAction(
            kind="LT_LEAP",
            ticker=op.get("ticker", ""),
            description=f"LT LEAP {op.get('ticker','')} — {op.get('concrete_trade','')}",
            tier=tier,
            tier_reason=skip_reason or "stock-replacement LEAP",
            skip_reason=skip_reason,
            weight_pct=weight_pct,
        )
    return None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _parse_yield_or_cost(text: str) -> tuple[float, float]:
    """Best-effort extract premium and collateral from advisor's yield_or_cost text.

    Patterns we expect:
      "~$1524 premium · ~13% annualized · $55,000 cash collateral"
      "~$987 per contract — leverages ~$5K of capital into $55,000 of exposure"

    Returns (premium, collateral). 0,0 if not parseable.
    """
    import re
    if not text:
        return 0.0, 0.0
    # Capture all $-amounts; first one is typically premium, look for "collateral" keyword.
    amounts = []
    for m in re.finditer(r"\$([\d,]+(?:\.\d+)?)([KkMm]?)", text):
        raw, suffix = m.group(1), m.group(2)
        try:
            v = float(raw.replace(",", ""))
        except ValueError:
            continue
        if suffix in ("K", "k"):
            v *= 1_000
        elif suffix in ("M", "m"):
            v *= 1_000_000
        amounts.append((m.start(), v))

    if not amounts:
        return 0.0, 0.0

    # If "collateral" appears, the closest preceding $-amount is collateral.
    collateral = 0.0
    cm = re.search(r"collateral", text, re.IGNORECASE)
    if cm:
        before = [(pos, v) for pos, v in amounts if pos < cm.start()]
        if before:
            collateral = before[-1][1]

    # Premium is typically the first amount (advisor renders premium first).
    premium = amounts[0][1] if amounts else 0.0
    if collateral and premium == collateral:
        # Single-amount text — premium is the only number, no collateral.
        collateral = 0.0
    return premium, collateral


# --------------------------------------------------------------------------
# Markdown extraction — parse the rendered action list into CapitalActions.
# This is the production path since render_action_list is the source of truth
# for what actions actually appear in the briefing.
# --------------------------------------------------------------------------

import re as _re


_ACTION_LINE_RE = _re.compile(
    r"^\s*(?P<n>\d+)\.\s+\*\*(?P<kind>[A-Z][A-Z _]+)\*\*\s+(?P<rest>.+)",
    _re.IGNORECASE,
)


def _money(s: str) -> float:
    """'$35,500' → 35500.0; '−$2,360' / '-$2,360' / '+$1,128' all handled."""
    s = s.replace(",", "").replace("−", "-").replace("$", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_action_block(head: str, body_text: str, rules: dict) -> CapitalAction | None:
    """Parse one action block (head line + indented bullets) into a CapitalAction."""
    m = _ACTION_LINE_RE.match(head)
    if not m:
        return None
    kind_raw = m.group("kind").strip().upper().replace(" ", "_")
    rest = m.group("rest").strip()
    # First word/group of capitals after kind that looks like a ticker
    ticker_m = _re.search(r"\b([A-Z]{1,6})(?:_PUT|_CALL|\b)", rest)
    ticker = ticker_m.group(1) if ticker_m else ""

    # Combine head + body for regex sweeps
    combined = head + "\n" + body_text

    # Common fields we care about (best-effort extraction)
    btc_cost = collateral_freed = collateral_locked = premium = debit = credit = 0.0
    profit_dollars = 0.0
    profit_pct = 0.0
    weight_pct = 0.0
    cost_amount = 0.0

    # CLOSE patterns
    if kind_raw == "CLOSE":
        # "+31% ($+107)" — locked profit
        pm = _re.search(r"\+(\d+)%\s+\(\$\+([\d,]+)\)", combined)
        if pm:
            profit_pct = int(pm.group(1)) / 100.0
            profit_dollars = _money(pm.group(2))
        # "buy-to-close limit $36.84" — per-share BTC cost; need contracts to scale
        bm = _re.search(r"buy-to-close limit \$([\d,.]+)", combined, _re.IGNORECASE)
        contracts = 1
        cm = _re.search(r"(\d+)\s+contracts?", combined, _re.IGNORECASE)
        if cm:
            contracts = int(cm.group(1))
        if bm:
            btc_cost = _money(bm.group(1)) * 100 * contracts
        # "frees $35,500 cash collateral"
        fm = _re.search(r"frees\s+\$([\d,]+)", combined, _re.IGNORECASE)
        if fm:
            collateral_freed = _money(fm.group(1))
        # "unlocks 100×6 shares" — covered call frees shares not cash
        if "unlocks" in combined.lower():
            pass  # cash_in stays 0

    # ROLL patterns
    elif kind_raw in ("EXECUTE_ROLL", "ROLL", "DEFENSIVE_ROLL"):
        # "−$2,360 net debit" or "+$1,128 net credit"
        dm = _re.search(r"[−-]\$([\d,]+)\s+net\s+debit", combined, _re.IGNORECASE)
        cm = _re.search(r"\+\$([\d,]+)\s+net\s+credit", combined, _re.IGNORECASE)
        if dm:
            debit = _money(dm.group(1))
        if cm:
            credit = _money(cm.group(1))

    # HEDGE patterns
    elif kind_raw == "HEDGE":
        # "(~$13,277; coverage ..."  or  "cost $13,277"
        hm = _re.search(r"~?\$([\d,]+)(?:;\s+coverage|\s+cost)", combined)
        if not hm:
            hm = _re.search(r"cost\s+\*\*\$([\d,]+)\*\*", combined, _re.IGNORECASE)
        if hm:
            cost_amount = _money(hm.group(1))

    # PULLBACK CSP / NEW CSP patterns
    elif kind_raw in ("PULLBACK_CSP", "NEW_CSP"):
        # "for $9.75 premium" — per-share; multiply by 100
        pm = _re.search(r"for\s+\$([\d,.]+)\s+premium", combined, _re.IGNORECASE)
        if pm:
            premium = _money(pm.group(1)) * 100
        # "$30,000 cash collateral" or "collateral ($30,000)"
        col_m = _re.search(r"\$([\d,]+)\s+(?:cash\s+)?collateral", combined, _re.IGNORECASE)
        if not col_m:
            col_m = _re.search(r"collateral\s+\(\$([\d,]+)\)", combined)
        if col_m:
            collateral_locked = _money(col_m.group(1))

    # TRIM patterns
    elif kind_raw == "TRIM":
        # "currently 14.8% NLV (over 10% cap); reduce to ~9% by selling ~$64,446"
        wm = _re.search(r"currently\s+([\d.]+)%\s+NLV", combined)
        if wm:
            weight_pct = float(wm.group(1))
        sm = _re.search(r"selling\s+~?\$([\d,]+)", combined, _re.IGNORECASE)
        if sm:
            collateral_freed = _money(sm.group(1))

    # Build CapitalAction
    if kind_raw == "CLOSE":
        cfg = (rules.get("actions") or {}).get("CLOSE", {})
        threshold = cfg.get("promote_to_critical_if_capture_pct_at_or_above", 0.30)
        tier = 1 if profit_pct >= threshold else int(cfg.get("base_tier", 2))
        return CapitalAction(
            kind="CLOSE",
            ticker=ticker,
            description=f"CLOSE {ticker} — locks ${profit_dollars:+,.0f}, frees ${collateral_freed:,.0f}",
            cash_in=collateral_freed,
            cash_out=btc_cost,
            tier=tier,
            tier_reason=(f"≥{threshold*100:.0f}% capture" if profit_pct >= threshold else "winner-close discipline"),
        )
    if kind_raw in ("EXECUTE_ROLL", "ROLL", "DEFENSIVE_ROLL"):
        cfg = (rules.get("actions") or {}).get("ROLL", {})
        tier = int(cfg.get("base_tier", 3))
        skip_reason: str | None = None

        # Defer rolls flagged with imminent earnings (renderer prepends
        # "🔴 BLOCK: Imminent earnings ...d away" on these positions).
        em = _re.search(r"earnings\s+(\d+)d\s+away", combined, _re.IGNORECASE)
        is_imminent = "Imminent earnings" in combined or "🔴 BLOCK" in combined
        if is_imminent and em:
            tier = 4
            skip_reason = f"earnings in {em.group(1)}d — defer until after print"
        elif cfg.get("promote_to_important_if_credit_received") and credit > debit:
            tier = 2

        net_in = credit
        net_out = debit
        desc = f"ROLL {ticker} — net "
        desc += f"+${credit - debit:,.0f} credit" if credit > debit else f"−${debit - credit:,.0f} debit"
        return CapitalAction(
            kind="ROLL",
            ticker=ticker,
            description=desc,
            cash_in=net_in,
            cash_out=net_out,
            tier=tier,
            tier_reason=(skip_reason or ("credit roll" if credit > debit else "debit roll")),
            skip_reason=skip_reason,
        )
    if kind_raw == "HEDGE":
        return CapitalAction(
            kind="HEDGE",
            ticker=ticker or "SPY",
            description=f"HEDGE {ticker or 'SPY'} — cost ${cost_amount:,.0f}",
            cash_out=cost_amount,
            tier=3,  # promoted by external coverage_ratio in build_capital_plan
            tier_reason="fat-tail insurance",
        )
    if kind_raw in ("PULLBACK_CSP", "NEW_CSP"):
        return CapitalAction(
            kind="NEW_CSP",
            ticker=ticker,
            description=f"NEW CSP {ticker} — premium ${premium:,.0f}, locks ${collateral_locked:,.0f}",
            cash_in=premium,
            cash_out=collateral_locked,
            new_collateral_locked=collateral_locked,
            tier=3,
            tier_reason="discretionary income trade",
        )
    if kind_raw == "TRIM":
        cfg = (rules.get("actions") or {}).get("TRIM", {})
        severe = cfg.get("promote_to_critical_if_weight_pct_at_or_above", 12.0)
        tier = 1 if weight_pct >= severe else int(cfg.get("base_tier", 2))
        return CapitalAction(
            kind="TRIM",
            ticker=ticker,
            description=f"TRIM {ticker} — raises ${collateral_freed:,.0f}",
            cash_in=collateral_freed,
            cash_out=0,
            weight_pct=weight_pct,
            tier=tier,
            tier_reason=(f"weight {weight_pct:.1f}% — severe concentration breach"
                         if weight_pct >= severe else f"weight {weight_pct:.1f}% over cap"),
        )

    return None


def extract_actions_from_action_list(lines: list[str], rules: dict | None = None) -> list[CapitalAction]:
    """Walk a rendered action-list (numbered items with indented bullets) and
    extract one CapitalAction per top-level item.

    The returned list mirrors what the renderer chose to show — so this is the
    canonical answer to "what actions are in today's briefing?"
    """
    rules = rules or _load_rules()
    actions: list[CapitalAction] = []
    if not lines:
        return actions

    current_head: str | None = None
    current_bullets: list[str] = []
    for line in lines:
        if _ACTION_LINE_RE.match(line):
            # Flush previous block
            if current_head is not None:
                a = _parse_action_block(current_head, "\n".join(current_bullets), rules)
                if a:
                    actions.append(a)
            current_head = line
            current_bullets = []
        elif current_head is not None and line.strip():
            current_bullets.append(line)
    if current_head is not None:
        a = _parse_action_block(current_head, "\n".join(current_bullets), rules)
        if a:
            actions.append(a)
    return actions


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def build_capital_plan(
    *,
    balance: dict,
    positions: list,
    equity_reviews: list | None = None,
    options_reviews: list | None = None,
    new_ideas: list | None = None,
    long_term_opportunities: list | None = None,
    hedge_recs: list | None = None,
    coverage_ratio: float | None = None,
    third_party_recs: dict | None = None,
    rules: dict | None = None,
    action_list_lines: list[str] | None = None,
) -> CapitalPlan:
    """Aggregate everything into a CapitalPlan.

    All inputs are optional except `balance`. Skills/steps that didn't run will
    just contribute zero actions. third_party_recs is {ticker: "BUY"/"HOLD"/...}.
    """
    rules = rules or _load_rules()
    nlv = float(balance.get("accountValue", 0) or 0)
    cash = float(balance.get("cash", 0) or 0)
    weights = _weights_by_ticker(positions or [], nlv)
    third_party_recs = {(k or "").upper(): (v or "").upper()
                        for k, v in (third_party_recs or {}).items()}

    plan = CapitalPlan(starting_cash=cash, nlv=nlv)
    raw_actions: list[CapitalAction] = []

    # 1) ACTION-LIST extraction (production path) — the renderer is the
    # source of truth for what closes/rolls/hedges/CSPs/trims are surfaced.
    extracted_kinds_seen: set[tuple[str, str]] = set()
    if action_list_lines:
        for a in extract_actions_from_action_list(action_list_lines, rules):
            # Hedge tier promotion based on coverage ratio
            if a.kind == "HEDGE" and coverage_ratio is not None:
                cfg = (rules.get("actions") or {}).get("HEDGE", {})
                red = cfg.get("promote_to_critical_if_coverage_below", 0.50)
                if coverage_ratio < red:
                    a.tier = 1
                    a.tier_reason = f"stress coverage {coverage_ratio:.2f}× < {red:.2f}× red"
            raw_actions.append(a)
            extracted_kinds_seen.add((a.kind, a.ticker))

    # 2) Structured-data fallback paths — only used when the markdown didn't
    # already cover them (avoids double-counting).
    for er in (equity_reviews or []):
        a = _classify_trim(er, weights, rules)
        if a and (a.kind, a.ticker) not in extracted_kinds_seen:
            raw_actions.append(a)
            extracted_kinds_seen.add((a.kind, a.ticker))

    # Option closes + rolls: only run structured fallback if no action_list
    # was provided (otherwise we'd double-count). Test fixtures use this path.
    if not action_list_lines:
        for orev in (options_reviews or []):
            for fn in (_classify_close, _classify_roll):
                a = fn(orev, rules)
                if a and (a.kind, a.ticker) not in extracted_kinds_seen:
                    # Roll with imminent earnings → goes straight to skipped, not raw
                    if a.tier == 4 or a.skip_reason:
                        plan.skipped_actions.append(a)
                    else:
                        raw_actions.append(a)
                    extracted_kinds_seen.add((a.kind, a.ticker))

    # New short-dated ideas (passed in as structured dicts) — only if not
    # already captured from the action list.
    for idea in (new_ideas or []):
        a = _classify_new_csp(idea, rules)
        if (a.kind, a.ticker) not in extracted_kinds_seen:
            raw_actions.append(a)
            extracted_kinds_seen.add((a.kind, a.ticker))

    # 3) Long-term opportunities — never come from the action list.
    # Suppress LT_TRIM/LT_EXIT/LT_ADD when the same ticker already has a
    # regular TRIM/EXIT from the action list (would duplicate the recommendation).
    tickers_with_regular_trim = {a.ticker for a in raw_actions if a.kind == "TRIM"}
    tickers_with_regular_exit = {a.ticker for a in raw_actions if a.kind == "EXIT"}
    for op in (long_term_opportunities or []):
        a = _classify_long_term(op, weights, third_party_recs, rules)
        if not a:
            continue
        if a.kind == "LT_TRIM" and a.ticker in tickers_with_regular_trim:
            continue
        if a.kind == "LT_EXIT" and a.ticker in tickers_with_regular_exit:
            continue
        raw_actions.append(a)

    # 4) Hedges as structured fallback (only if not already from action list)
    if not any(a.kind == "HEDGE" for a in raw_actions):
        raw_actions.extend(_classify_hedge(hedge_recs or [], coverage_ratio, rules))

    # Split into active vs skipped
    for a in raw_actions:
        if a.skip_reason or a.tier == 4:
            plan.skipped_actions.append(a)
        else:
            plan.actions.append(a)

    # Aggregates (only count active actions; skipped are not executed)
    ltcg_rate = (rules.get("ltcg") or {}).get("rate", 0.238)
    for a in plan.actions:
        if a.kind == "CLOSE":
            plan.total_btc_cost += a.cash_out
            plan.total_collateral_freed += a.cash_in
        elif a.kind == "ROLL":
            if a.cash_in > a.cash_out:
                plan.total_premium_received += (a.cash_in - a.cash_out)
            else:
                plan.total_debit_paid += (a.cash_out - a.cash_in)
        elif a.kind in ("NEW_CSP", "NEW_CC", "LT_CSP"):
            plan.total_premium_received += a.cash_in
            plan.total_collateral_locked += a.new_collateral_locked
        elif a.kind == "HEDGE":
            plan.total_debit_paid += a.cash_out
        elif a.kind == "TRIM":
            plan.total_collateral_freed += a.cash_in
            plan.tax_estimate_ltcg += a.cash_in * ltcg_rate
        elif a.kind == "LT_ADD":
            # Long-term equity adds count as debits in the aggregate
            plan.total_debit_paid += a.cash_out

    # Sort within each tier by descending net_cash (highest cash benefit first),
    # tie-break by EV descending where available.
    plan.actions.sort(key=lambda a: (a.tier, -a.net_cash, -(a.ev or 0)))

    return plan


# --------------------------------------------------------------------------
# Markdown rendering
# --------------------------------------------------------------------------

def format_capital_plan_md(plan: CapitalPlan, max_actions_per_tier: int = 20) -> list[str]:
    """Render a Capital Plan panel as a list of markdown lines."""
    lines = ["## 💰 Capital Plan", ""]

    # Header summary
    delta = plan.ending_cash_projected - plan.starting_cash
    lines.append(
        f"**Starting cash:** ${plan.starting_cash:,.0f} | "
        f"**Projected ending:** ${plan.ending_cash_projected:,.0f} "
        f"(**{'+' if delta >= 0 else '−'}${abs(delta):,.0f}**)"
    )
    lines.append(
        f"_Premium received:_ ${plan.total_premium_received:,.0f} · "
        f"_Collateral freed:_ ${plan.total_collateral_freed:,.0f} · "
        f"_New collateral locked:_ ${plan.total_collateral_locked:,.0f} · "
        f"_Debits + BTC costs:_ ${plan.total_debit_paid + plan.total_btc_cost:,.0f}"
    )
    if plan.tax_estimate_ltcg > 0:
        lines.append(f"_LTCG tax estimate on trims:_ ~${plan.tax_estimate_ltcg:,.0f} (deferred until filing)")
    lines.append("")

    tier_titles = {
        1: "🔴 **Tier 1 — CRITICAL** (do first)",
        2: "🟡 **Tier 2 — IMPORTANT**",
        3: "🟢 **Tier 3 — OPTIONAL**",
    }
    by_tier = plan.actions_by_tier()
    for t in (1, 2, 3):
        items = by_tier.get(t, [])
        if not items:
            continue
        lines.append(f"### {tier_titles[t]}")
        lines.append("")
        for a in items[:max_actions_per_tier]:
            cash_str = (
                f"+${a.net_cash:,.0f}" if a.net_cash >= 0 else f"−${abs(a.net_cash):,.0f}"
            )
            ev_str = f" · EV ${a.ev:+,.0f}" if a.ev is not None else ""
            verdict_str = f" · {a.validator_verdict}" if a.validator_verdict else ""
            lines.append(f"- {a.description} — net cash {cash_str}{ev_str}{verdict_str}")
            if a.tier_reason:
                lines.append(f"  - _{a.tier_reason}_")
        if len(items) > max_actions_per_tier:
            lines.append(f"- _… and {len(items) - max_actions_per_tier} more in this tier_")
        lines.append("")

    # Skipped section
    if plan.skipped_actions:
        lines.append("### ⏸ Skipped — see why")
        lines.append("")
        for a in plan.skipped_actions[:max_actions_per_tier]:
            lines.append(f"- **{a.kind} {a.ticker}** — {a.skip_reason or a.tier_reason}")
        lines.append("")

    return lines
