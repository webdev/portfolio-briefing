"""Merged Ideas-and-Candidates view for the briefing page.

The pipeline writes two related lists into briefing JSON:

  - `new_ideas`    — pulled directly from the screener. When capacity is
                    gated, this is just a single placeholder ("CAPACITY
                    blocked: coverage 0.07x < 0.50x").
  - `long_term_opportunities` — the actual long-term opportunity catalog:
                    LONG_DATED_CSP, BUY, ADD, LT_ADD, PULLBACK_CSP,
                    EXIT, TRIM, SKIPPED_*, DEFERRED_ADD_HAS_CSP,
                    _FUNDING_HINT.

The briefing surface used to display ONLY `new_ideas`, which meant on a
capacity-gated day the user saw a single "blocked" row and nothing
actionable — even though `long_term_opportunities` contained ≥20 real
ideas they could plan around.

CLAUDE.md hard rule #32 (added by this module): the Ideas tab MUST never
show ONLY a "blocked" placeholder. When `new_ideas` is empty or just
placeholders, merge in the actionable opportunity kinds from
`long_term_opportunities`. Each row carries an explicit status so the
user can see *why* it's gated rather than have the opportunity hidden.

This extends hard rule #24 (never hide opportunities) to the web app.

Task #17 (tab consolidation) extends the merger: sub-lot completion
proposals from `strategy_upgrades` (type == "sublot_completion") are ALSO
merged in with kind `SUB_LOT` — a sub-lot completion IS a new-open
recommendation, so it belongs on the Ideas surface (the Strategy tab was
retired). Each merged idea additionally carries `dte` / `dte_bucket` so
the Ideas tab's client-side filter chips (Short-dated / Long-dated /
Sub-lot / Deferred / Skipped) can filter by data attribute.

Public API:
  - `IDEA_OPPORTUNITY_KINDS` — the LTO kinds we surface as ideas
  - `SUB_LOT_KIND` — the kind assigned to merged sub-lot completions
  - `build_merged_ideas(briefing)` — return list[MergedIdea]

A MergedIdea is a plain dict so it stays template-friendly. Fields:
  - source            — "new_ideas" | "long_term_opportunity" | "strategy_upgrade"
  - kind              — the action verb / opportunity kind
  - ticker            — uppercase ticker (or "—")
  - source_label      — short human attribution ("Parkev BUY", "RSI 45 pullback", etc.)
  - concrete_trade    — the trade ticket text (or "")
  - rationale         — the rationale text (or "")
  - status            — "actionable" | "capacity_blocked" | "deferred" | "skipped"
  - status_label      — human-friendly status text
  - status_class      — CSS class hint ("ok" | "warn" | "muted")
  - dte               — int days-to-expiry when the row carries one, else None
  - dte_bucket        — "short" (≤60 DTE) | "long" (>60 DTE) | "" (unknown / equity)
  - raw               — the underlying dict (for templates that want extra fields)
"""

from __future__ import annotations

from typing import Any


# Kinds we treat as "ideas" inside long_term_opportunities. EXIT and TRIM
# are sells / closes — those aren't ideas, they're position management
# (and live under different surfaces). _FUNDING_HINT is a Capital Plan
# note, not an idea.
IDEA_OPPORTUNITY_KINDS = {
    "LONG_DATED_CSP",
    "BUY",
    "ADD",
    "LT_ADD",
    "PULLBACK_CSP",
}


# Kinds we surface as "deferred" / "skipped" ideas — opportunities the
# discipline gates pulled OUT of the actionable list. Hard rule #24
# requires we still SHOW them so the user can see why.
DEFERRED_OPPORTUNITY_KINDS = {
    "DEFERRED_ADD_HAS_CSP",
    "SKIPPED_ADD",
    "SKIPPED_LT_CSP",
    "SKIPPED_RSI",
    "SKIPPED_LT_VERDICT",
}


# Task #17 — sub-lot completions merged in from strategy_upgrades.
SUB_LOT_KIND = "SUB_LOT"
_SUBLOT_STRATEGY_TYPE = "sublot_completion"

# DTE bucket boundary for the Ideas filter chips: ≤60 days = short-dated.
SHORT_DATED_MAX_DTE = 60

# Kind-based bucket fallback when a row carries no structured DTE. These
# are semantic facts about the kind itself (a LONG_DATED_CSP is by
# definition >60 DTE; a PULLBACK_CSP is a near-dated entry) — NOT parsed
# out of display text (hard rule #19: no fabricated numbers, so `dte`
# itself stays None unless the row carries a real value).
_LONG_DATED_KINDS = {"LONG_DATED_CSP", "LT_CSP"}
_SHORT_DATED_KINDS = {"PULLBACK_CSP", "NEW_CSP"}


def _idea_dte(item: dict[str, Any]) -> int | None:
    """Structured days-to-expiry from the raw row, or None. Never parses
    display text — only real fields count."""
    for key in ("dte", "days_to_expiry"):
        value = item.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _dte_bucket(kind: str, dte: int | None) -> str:
    """Return "short" / "long" / "" for the Ideas filter chips."""
    if dte is not None:
        return "short" if dte <= SHORT_DATED_MAX_DTE else "long"
    k = (kind or "").upper()
    if k in _LONG_DATED_KINDS:
        return "long"
    if k in _SHORT_DATED_KINDS:
        return "short"
    return ""


def _classify_new_idea_status(item: dict[str, Any]) -> tuple[str, str, str]:
    """Return (status, status_label, status_class) for a NewIdea-shaped dict."""
    if item.get("capacity_blocked"):
        return (
            "capacity_blocked",
            "⏸ capacity blocked",
            "warn",
        )
    return ("actionable", "✓ actionable", "ok")


def _classify_lto_status(item: dict[str, Any]) -> tuple[str, str, str]:
    """Return (status, status_label, status_class) for an Opportunity-shaped dict."""
    kind = (item.get("kind") or "").upper()
    if kind in IDEA_OPPORTUNITY_KINDS:
        return ("actionable", "✓ actionable", "ok")
    if kind == "DEFERRED_ADD_HAS_CSP":
        return ("deferred", "⏸ deferred — held put", "warn")
    if kind == "SKIPPED_RSI":
        return ("skipped", "⏭ skipped — RSI gate", "muted")
    if kind == "SKIPPED_ADD":
        return ("skipped", "⏭ skipped — ADD discipline", "muted")
    if kind == "SKIPPED_LT_CSP":
        return ("skipped", "⏭ skipped — LT CSP discipline", "muted")
    # Catch-all for anything else we let through
    return ("skipped", f"⏭ {kind.lower()}", "muted")


def _source_label_for_new_idea(item: dict[str, Any]) -> str:
    """Short attribution for a new-idea row.

    Examples:
      "capacity_gates_blocked" → "Capacity gate"
      "screener_fmp_pullback"  → "FMP pullback"
      (falls back to raw source string)
    """
    src = (item.get("source") or "").strip()
    if not src:
        return "—"
    mapped = {
        "capacity_gates_blocked": "Capacity gate",
    }
    return mapped.get(src, src.replace("_", " "))


def _classify_sublot_status(item: dict[str, Any]) -> tuple[str, str, str]:
    """Return (status, status_label, status_class) for a sub-lot upgrade.

    Mirrors the pipeline's own gating flags (never re-derives them):
      - discipline_deferred  → deferred (has-CSP / LT-verdict gate)
      - rsi_blocked          → skipped (RSI gate, hard rule #11)
      - capacity_deferred_tag → deferred (capacity gate, hard rule #41)
    """
    if item.get("discipline_deferred"):
        return ("deferred", "⏸ deferred — discipline gate", "warn")
    if item.get("rsi_blocked"):
        return ("skipped", "⏭ skipped — RSI gate", "muted")
    if item.get("capacity_deferred_tag"):
        return ("deferred", "⏸ deferred — capacity gated", "warn")
    return ("actionable", "✓ actionable", "ok")


def _sublot_concrete_trade(item: dict[str, Any]) -> str:
    """Compose the sub-lot ticket from REAL pipeline fields only.

    Missing shares/price → "" (fail closed, hard rule #19 — never a
    plausible-looking guess)."""
    ticker = (str(item.get("underlying") or "")).strip().upper()
    shares = item.get("shares_to_buy")
    price = item.get("current_price")
    try:
        if not ticker or shares is None or price is None:
            return ""
        trade = f"BUY {int(shares)} × {ticker} @ ${float(price):,.2f}"
        cost = item.get("cost")
        if cost is not None:
            trade += f" (~${float(cost):,.0f})"
        return trade
    except (TypeError, ValueError):
        return ""


def _sublot_rationale(item: dict[str, Any]) -> str:
    """Rationale + the pipeline's own gate reason when deferred."""
    parts = [str(item.get("rationale") or "").strip()]
    reason = str(item.get("discipline_reason") or "").strip()
    if reason:
        parts.append(reason)
    return " — ".join(p for p in parts if p)


def _source_label_for_lto(item: dict[str, Any]) -> str:
    """Short attribution for a long-term-opportunity row."""
    src = (item.get("source") or "").strip()
    if src:
        return src
    # Fall back to first trigger reason if source missing
    reasons = item.get("trigger_reasons") or []
    if reasons:
        return str(reasons[0])
    return "—"


def build_merged_ideas(briefing: Any) -> list[dict[str, Any]]:
    """Merge briefing.new_ideas + the idea-kinds of long_term_opportunities.

    The merge order is:
        1. Actionable new_ideas (the rare case it has real entries)
        2. Actionable LONG_DATED_CSP / BUY / ADD / LT_ADD / PULLBACK_CSP
        3. Actionable SUB_LOT completions (task #17 — from strategy_upgrades)
        4. Deferred sub-lots / DEFERRED_ADD_HAS_CSP
        5. Capacity-blocked new_ideas placeholders + SKIPPED_* opportunities

    De-duplication: if a (ticker, kind) appears in both new_ideas AND
    long_term_opportunities, the new_idea wins (since it's the more
    actionable surface). De-dup keys are case-insensitive ticker + kind.
    """
    new_ideas = list(getattr(briefing, "new_ideas", []) or [])
    ltos = list(getattr(briefing, "long_term_opportunities", []) or [])
    upgrades = list(getattr(briefing, "strategy_upgrades", []) or [])

    # Coerce to plain dicts for uniform handling (Pydantic V2 models)
    def _as_dict(x: Any) -> dict[str, Any]:
        if hasattr(x, "model_dump"):
            return x.model_dump()
        if isinstance(x, dict):
            return x
        # Best-effort attribute extraction
        return {k: getattr(x, k, None) for k in (
            "ticker", "name", "source", "rationale", "capacity_blocked",
            "kind", "trigger_reasons", "concrete_trade", "yield_or_cost",
            # strategy-upgrade (sub-lot) fields, task #17
            "type", "underlying", "shares_to_buy", "current_price", "cost",
            "discipline_deferred", "discipline_reason", "rsi_blocked",
            "capacity_deferred_tag", "dte", "days_to_expiry",
        )}

    seen_keys: set[tuple[str, str]] = set()
    actionable: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []

    for raw_item in new_ideas:
        item = _as_dict(raw_item)
        ticker = (item.get("ticker") or "").upper()
        # new_ideas have no `kind`; default to "NEW_CSP" for de-dup purposes
        # (the screener entries are always new CSP ideas in practice)
        kind = (item.get("kind") or "NEW_CSP").upper()
        status, status_label, status_class = _classify_new_idea_status(item)
        dte = _idea_dte(item)
        merged = {
            "source": "new_ideas",
            "kind": kind,
            "ticker": ticker or "—",
            "source_label": _source_label_for_new_idea(item),
            "concrete_trade": item.get("instruction") or "",
            "rationale": item.get("rationale") or "",
            "status": status,
            "status_label": status_label,
            "status_class": status_class,
            "dte": dte,
            "dte_bucket": _dte_bucket(kind, dte),
            "raw": item,
        }
        key = (ticker, kind)
        if ticker and ticker != "—":
            seen_keys.add(key)
        if status == "actionable":
            actionable.append(merged)
        else:
            blocked.append(merged)

    for raw_item in ltos:
        item = _as_dict(raw_item)
        kind = (item.get("kind") or "").upper()
        if kind not in IDEA_OPPORTUNITY_KINDS and kind not in DEFERRED_OPPORTUNITY_KINDS:
            continue
        ticker = (item.get("ticker") or "").upper()
        key = (ticker, kind)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        status, status_label, status_class = _classify_lto_status(item)
        dte = _idea_dte(item)
        merged = {
            "source": "long_term_opportunity",
            "kind": kind,
            "ticker": ticker or "—",
            "source_label": _source_label_for_lto(item),
            "concrete_trade": item.get("concrete_trade") or "",
            "rationale": item.get("rationale") or "",
            "status": status,
            "status_label": status_label,
            "status_class": status_class,
            "dte": dte,
            "dte_bucket": _dte_bucket(kind, dte),
            "raw": item,
        }
        if status == "actionable":
            actionable.append(merged)
        elif status == "deferred":
            deferred.append(merged)
        else:
            blocked.append(merged)

    # Task #17 — sub-lot completions are new-open BUY recommendations;
    # they belong on the Ideas surface now that the Strategy tab is gone.
    for raw_item in upgrades:
        item = _as_dict(raw_item)
        if str(item.get("type") or "").strip().lower() != _SUBLOT_STRATEGY_TYPE:
            continue
        ticker = (str(item.get("underlying") or "")).strip().upper()
        key = (ticker, SUB_LOT_KIND)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        status, status_label, status_class = _classify_sublot_status(item)
        merged = {
            "source": "strategy_upgrade",
            "kind": SUB_LOT_KIND,
            "ticker": ticker or "—",
            "source_label": "sub-lot completion",
            "concrete_trade": _sublot_concrete_trade(item),
            "rationale": _sublot_rationale(item),
            "status": status,
            "status_label": status_label,
            "status_class": status_class,
            "dte": None,          # equity buy — no expiration
            "dte_bucket": "",
            "raw": item,
        }
        if status == "actionable":
            actionable.append(merged)
        elif status == "deferred":
            deferred.append(merged)
        else:
            blocked.append(merged)

    return actionable + deferred + blocked


def merged_ideas_count(briefing: Any) -> int:
    """Total entries in the merged ideas list (for tab badge)."""
    return len(build_merged_ideas(briefing))
