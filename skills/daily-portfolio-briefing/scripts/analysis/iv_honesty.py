"""Post-gap IV-rank honesty — single source of truth (rule #43 batch, 2026-07-31).

The pipeline's "IV rank" is a 252-day REALIZED-vol-proxy percentile. It is
backward-looking: a big earnings gap inflates the rank BY the gap itself,
while the actual implied premium on offer crushes post-print. The observed
defect (AMZN LONG DATED CSP card, 2026-07-31 briefing line ~639):

    "Patient capital trade: elevated IV 100 + 77-DTE horizon ... = fat
     premium." while the delivered yield was $630 / $24,500 / 77d = ~12%
     annualized — thin, not fat. AMZN had gapped +13.7% on earnings the
     same day, which is what pinned the realized-vol rank at 100.

This module cross-checks any "fat premium" claim against the DELIVERED
annualized yield computed from the actual quoted premium, and rewrites the
claim honestly when claimed-fat meets delivered-thin. It also extends the
NFLX-style "thinner than the rule-of-thumb estimate — reconsider" note to
fire on ALL claimed-fat + delivered-thin cards, not just the absolute
premium < $500 path that caught NFLX.

Fail-open everywhere: missing IV rank, missing delivered yield, or missing
OHLC → no rewrite (never fabricate; never block on missing data).

Config (briefing.yaml → ``iv_gap_check``):
    min_rank: 60                  # IV rank at/above which "fat" claims are checked
    min_yield_for_fat_claim: 0.20 # delivered annualized yield below this = thin
    gap_threshold_pct: 8          # |1d or 2d move| within last 3 sessions = gap
"""

from __future__ import annotations

import re

DEFAULT_MIN_RANK = 60.0
DEFAULT_MIN_YIELD_FOR_FAT_CLAIM = 0.20   # 20% annualized
DEFAULT_GAP_THRESHOLD_PCT = 8.0

# Legacy absolute-thin floor (the check that caught NFLX's $186 premium).
LEGACY_THIN_PREMIUM_FLOOR = 500.0

_RECONSIDER = (
    "; thinner than the rule-of-thumb estimate — reconsider unless you "
    "specifically want this strike"
)

_IV_IN_RATIONALE_RE = re.compile(r"elevated IV (\d+(?:\.\d+)?)")


def load_iv_gap_config(config: dict | None) -> dict:
    """Merge briefing.yaml → ``iv_gap_check`` over defaults."""
    cfg = {
        "min_rank": DEFAULT_MIN_RANK,
        "min_yield_for_fat_claim": DEFAULT_MIN_YIELD_FOR_FAT_CLAIM,
        "gap_threshold_pct": DEFAULT_GAP_THRESHOLD_PCT,
    }
    user = (config or {}).get("iv_gap_check") or {}
    for key in cfg:
        val = user.get(key)
        if val is not None:
            try:
                cfg[key] = float(val)
            except (TypeError, ValueError):
                pass
    return cfg


def detect_recent_gap(tech_entry: dict | None, threshold_pct: float = DEFAULT_GAP_THRESHOLD_PCT) -> float | None:
    """Return the signed % of a recent gap move, or None.

    A "gap" is a |1-day or 2-day price change| ≥ ``threshold_pct`` within the
    last 3 sessions, computed from ``recent_closes`` (last ~6 closes, written
    by snapshot_inputs from the OHLC already pulled for RSI/SMA — no extra
    fetch). When ``recent_closes`` is absent (older snapshots), falls back to
    the deep read's ``ret_1w_pct`` as a coarser proxy. Fail-open: no data →
    None (never fabricated).
    """
    if not isinstance(tech_entry, dict):
        return None
    closes = tech_entry.get("recent_closes")
    if isinstance(closes, (list, tuple)) and len(closes) >= 2:
        try:
            floats = [float(c) for c in closes if c is not None and float(c) > 0]
        except (TypeError, ValueError):
            floats = []
        n = len(floats)
        # Prefer the tightest span: a qualifying 1-day move IS the gap; only
        # fall back to 2-day when no single session cleared the threshold.
        for span in (1, 2):
            best: float | None = None
            # Only changes ENDING within the last 3 sessions count.
            for i in range(max(span, n - 3), n):
                prev = floats[i - span]
                chg = (floats[i] - prev) / prev * 100.0
                if abs(chg) >= threshold_pct and (best is None or abs(chg) > abs(best)):
                    best = chg
            if best is not None:
                return round(best, 1)
        return None
    deep = tech_entry.get("deep")
    if isinstance(deep, dict):
        r1w = deep.get("ret_1w_pct")
        try:
            if r1w is not None and abs(float(r1w)) >= threshold_pct:
                return round(float(r1w), 1)
        except (TypeError, ValueError):
            pass
    return None


def claimed_fat_but_thin(
    iv_rank: float | None,
    annualized_pct: float | None,
    config: dict | None = None,
) -> bool:
    """True when an elevated IV rank claims "fat premium" but the DELIVERED
    annualized yield (in percent, e.g. 12.0) is below the fat-claim floor.

    Fail-open: unknown IV rank or unknown delivered yield → False.
    """
    if iv_rank is None or annualized_pct is None:
        return False
    cfg = load_iv_gap_config(config)
    return float(iv_rank) >= cfg["min_rank"] and float(annualized_pct) < cfg["min_yield_for_fat_claim"] * 100.0


def honest_iv_text(
    iv_rank: float | None,
    gap_pct: float | None,
    annualized_pct: float | None,
) -> str:
    """The honest replacement for a "fat premium" claim on a thin delivery."""
    ann = f"~{annualized_pct:.0f}% annualized" if annualized_pct is not None else "thin"
    rank = f"{iv_rank:.0f}" if iv_rank is not None else "n/a"
    if gap_pct is not None:
        return (
            f"IV rank {rank} is inflated by the recent {gap_pct:+.0f}% move "
            f"(realized-vol proxy is backward-looking); delivered premium is thin "
            f"({ann}) — implied vol has crushed post-event."
        )
    return (
        f"IV rank {rank} overstates the premium actually on offer "
        f"(realized-vol proxy is backward-looking); delivered premium is thin ({ann})."
    )


def rewrite_fat_premium(
    rationale: str | None,
    *,
    iv_rank: float | None = None,
    annualized_pct: float | None = None,
    premium_total: float | None = None,
    tech_entry: dict | None = None,
    config: dict | None = None,
) -> str | None:
    """Rewrite a rationale's "fat premium" claim against the delivered yield.

    Returns the rewritten rationale, or None when no rewrite applies (caller
    keeps the original — fail-open).

    Two paths, first match wins:
      1. Claimed-fat + delivered-thin (universal, yield-based): IV rank ≥
         ``min_rank`` AND delivered annualized yield < ``min_yield_for_fat_claim``
         → honest IV text (gap-aware) + the reconsider note. This is the path
         the AMZN card missed (premium $630 > the old $500 absolute floor).
      2. Legacy absolute-thin (< $500 total premium) — the NFLX path, kept.
    """
    if not rationale or "fat premium" not in rationale:
        return None
    if iv_rank is None:
        m = _IV_IN_RATIONALE_RE.search(rationale)
        if m:
            try:
                iv_rank = float(m.group(1))
            except ValueError:
                iv_rank = None
    if claimed_fat_but_thin(iv_rank, annualized_pct, config):
        cfg = load_iv_gap_config(config)
        gap = detect_recent_gap(tech_entry, cfg["gap_threshold_pct"])
        return rationale.replace(
            "fat premium",
            honest_iv_text(iv_rank, gap, annualized_pct).rstrip(".") + _RECONSIDER,
        )
    if premium_total is not None and premium_total < LEGACY_THIN_PREMIUM_FLOOR:
        return rationale.replace(
            "= fat premium",
            f"= ${premium_total:.0f} premium (thinner than the rule-of-thumb estimate — "
            "reconsider unless you specifically want this strike)",
        )
    return None
