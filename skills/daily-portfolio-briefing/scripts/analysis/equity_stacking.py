"""Equity-stacking gate — single source of truth (task #30 + rule #43).

Origin of the shared-module move (2026-07-31 briefing): the LTO section
rendered "💎 6. LONG DATED CSP · GOOG — SELL 1× GOOG $330P" with
"✅ RSI favourable" and NO mention of equity concentration, while the
Rotation Playbook two sections later hard-skipped the identical trade:
"⛔ GOOG $330P skipped — holds 15.9% NLV in GOOG equity (≥ 10% NLV
hard-skip)". Same document, same trade — recommend + refuse.

The check now lives here and is consumed by BOTH sides:
  - rotation_playbook (score penalty / hard skip, task #30 — unchanged
    semantics, delegated here)
  - the LTO / new-CSP card surfaces, which render a matching annotation so
    a card can never carry ✅-favorable framing without the concentration
    context. The card stays visible (planning value, hard rule #24).

Bands (fractions of NLV held in the underlying's EQUITY; config:
``rotation_playbook.equity_stacking``):
  < 2%    indifferent — no penalty, no annotation
  2-5%    modest      — −1 playbook penalty; no card annotation (<5% clean)
  5-10%   concerning  — −3 playbook penalty; ⚠ card warning
  ≥ 10%   hard skip   — playbook excludes (visible in warnings footer);
                        card renders the ⛔ hard-skip-zone annotation
All fail-open on missing data: unknown held % → no penalty, no annotation.
"""

from __future__ import annotations

DEFAULT_MODEST_BAND = (0.02, 0.05)
DEFAULT_CONCERNING_BAND = (0.05, 0.10)
DEFAULT_HARD_SKIP_PCT = 0.10
DEFAULT_MODEST_PENALTY = 1.0
DEFAULT_CONCERNING_PENALTY = 3.0


def _f(v, default):
    try:
        out = float(v)
    except (TypeError, ValueError):
        return default
    return out if out == out else default


def equity_pct_by_ticker(positions, nlv) -> dict[str, float]:
    """Held EQUITY market value per ticker as a fraction of NLV.

    ``positions`` is the snapshot ``positions`` list (dicts with assetType /
    symbol / market_value or price×qty). Empty dict when unmeasurable
    (fail-open — no penalty or annotation is ever applied on missing data).
    """
    try:
        nlv = float(nlv)
    except (TypeError, ValueError):
        return {}
    if not nlv or nlv <= 0:
        return {}
    out: dict[str, float] = {}
    for p in positions or []:
        if not isinstance(p, dict) or p.get("assetType") != "EQUITY":
            continue
        mv = _f(p.get("market_value"), None)
        if mv is None:
            price = _f(p.get("price"), None)
            qty = _f(p.get("qty"), None)
            mv = price * qty if price is not None and qty is not None else None
        tk = str(p.get("symbol") or p.get("ticker") or "").upper()
        if tk and mv is not None:
            out[tk] = out.get(tk, 0.0) + mv
    return {tk: mv / nlv for tk, mv in out.items()}


def stacking_penalty(
    ticker: str,
    held_equity_pct: float | None,
    cfg: dict,
) -> tuple[float, str | None, bool]:
    """Task #30 — the playbook's score gate. Returns
    ``(score_delta, tag_or_None, hard_skip)``:

      held < 2% NLV          → (0, None, False)      small — indifferent
      2-5% NLV               → (−1, modest tag, False)
      5-10% NLV              → (−3, concerning tag, False)
      ≥10% NLV               → (0, skip reason, True) — HARD SKIP, always
                               surfaced in the warnings footer, never silent.
                               A per-ticker ``force_include`` entry (kill
                               switch) downgrades to the −3 penalty instead.

    ``held_equity_pct`` None (missing data) → no penalty (fail-open).
    """
    if held_equity_pct is None:
        return 0.0, None, False
    cfg = cfg if isinstance(cfg, dict) else {}
    modest = cfg.get("modest_band_pct")
    modest_lo = _f(modest[0] if isinstance(modest, (list, tuple))
                   and len(modest) == 2 else None, DEFAULT_MODEST_BAND[0])
    concerning = cfg.get("concerning_band_pct")
    concerning_lo = _f(concerning[0] if isinstance(concerning, (list, tuple))
                       and len(concerning) == 2 else None,
                       DEFAULT_CONCERNING_BAND[0])
    hard = _f(cfg.get("hard_skip_pct"), DEFAULT_HARD_SKIP_PCT)
    p_modest = _f(cfg.get("modest_penalty"), DEFAULT_MODEST_PENALTY)
    p_concern = _f(cfg.get("concerning_penalty"), DEFAULT_CONCERNING_PENALTY)
    forced = {str(t).upper() for t in cfg.get("force_include") or []}
    pct_txt = f"{held_equity_pct * 100:.1f}% NLV held"
    if held_equity_pct >= hard:
        if ticker.upper() in forced:
            return -p_concern, (
                f"⚠ stacks equity concentration ({pct_txt}; "
                f"force-included past the {hard * 100:.0f}% hard-skip)"), False
        return 0.0, (
            f"holds {pct_txt.replace(' held', '')} in {ticker} equity "
            f"(≥ {hard * 100:.0f}% NLV hard-skip — adding a short put "
            f"stacks single-name risk)"), True
    if held_equity_pct >= concerning_lo:
        return -p_concern, f"⚠ stacks equity concentration ({pct_txt})", False
    if held_equity_pct >= modest_lo:
        return -p_modest, f"⚠ modest equity concentration ({pct_txt})", False
    return 0.0, None, False


def stacking_card_annotation(
    ticker: str | None,
    held_equity_pct: float | None,
    cfg: dict | None = None,
) -> str | None:
    """Rule #43 (GOOG 2026-07-31) — the card-surface annotation that keeps
    the LTO / new-CSP cards consistent with the playbook's gate:

      ≥ hard_skip_pct (10%)  → the prominent ⛔ hard-skip-zone text (the
                               playbook WILL exclude this trade — say so on
                               the card that recommends it)
      concerning band (5-10%)→ the modest ⚠ warning
      below 5% / unknown     → None (nothing to annotate)
    """
    if held_equity_pct is None or not ticker:
        return None                   # fail-open — never annotate on a guess
    cfg = cfg if isinstance(cfg, dict) else {}
    hard = _f(cfg.get("hard_skip_pct"), DEFAULT_HARD_SKIP_PCT)
    concerning = cfg.get("concerning_band_pct")
    concerning_lo = _f(concerning[0] if isinstance(concerning, (list, tuple))
                       and len(concerning) == 2 else None,
                       DEFAULT_CONCERNING_BAND[0])
    tk = str(ticker).upper()
    pct = held_equity_pct * 100
    if held_equity_pct >= hard:
        return (f"⛔ equity-stacking hard-skip zone — you hold {pct:.1f}% "
                f"NLV in {tk} equity; a new short put stacks single-name "
                f"risk (playbook will exclude this trade)")
    if held_equity_pct >= concerning_lo:
        return (f"⚠ equity concentration — you hold {pct:.1f}% NLV in {tk} "
                f"equity; a new short put adds single-name risk")
    return None
