"""Redeployability-aware take-profit — is there a PATH for the freed money?

George (2026-08-10): "I'm happy to exit options and close it if we have a
path to redeployment. If we don't have a path to redeployment, then it
doesn't make sense to close it."

Context: the smart take-profit stack (wheel-roll-advisor guardrails — the
65-85% sweet-spot matrix floors, the time-adjusted fast-winner layer, the
gamma escape, the hard ceiling) fires yield-motivated closes at 31-50%
capture on the premise "lock the win, redeploy the collateral." When the
entry gates are CLOSED (stress coverage below the capacity floor), that
premise is broken — the freed collateral has nowhere to go, and a healthy
winner should be held toward the 70-80% capture zone instead.

The contract:

``redeployment_path(analytics, best_setups, close_impact, config)`` returns
``(bool, reason)``. A path exists when ANY of:

  (a) entry gates are OPEN — coverage ≥ the capacity floor. Reason is ""
      (today's behavior is byte-identical; no annotation noise on every
      winner close when the gates were never the question).
  (b) closing THIS position would push post-close coverage across the floor
      (the META case — the close itself reopens the gates). Computed from
      the MEASURED stress-coverage numbers: (cash − buyback cost) /
      (total put obligation − this position's freed collateral).
  (c) at least one A/B-graded setup (analysis/setup_grade.py
      ``collect_best_setups`` output — already past the RSI hard block and
      the delivered-yield floor) is waiting today — a rotation target for
      the freed collateral even while the gates stay closed.

Scope: SHORT PUTS only. A covered-call buyback frees no cash collateral and
re-writing calls on retained shares is never capacity-gated — call-side
closes keep legacy behavior (``(True, "")``).

Fail direction (rule #19): fail-OPEN to ``(True, "")`` when the coverage
ratio can't be resolved — missing data must never trap winners at a raised
floor; legacy behavior is the safe default.

Risk-driven closes (loss stops, CLOSE NOW, earnings-window exits, gamma
escape DTE ≤ 10 @ ≥ 30%, hard ceiling ≥ 85%, crash/tail-risk) are NOT this
module's business — callers must exempt them BEFORE consulting the path.

Config: ``briefing.yaml`` → ``redeploy_aware_tp`` (enabled, hold_target_pct,
hard_ceiling_pct). ``enabled: false`` / block missing → callers skip the
gate entirely (byte-identical legacy).
"""

from __future__ import annotations

import math

try:
    from analysis.capacity_gate import coverage_ratio_from, min_coverage_ratio
except ImportError:  # pragma: no cover — standalone/test path fallback
    from capacity_gate import coverage_ratio_from, min_coverage_ratio


_DEFAULT_HOLD_TARGET = 0.75
_DEFAULT_HARD_CEILING = 0.85
# Letters that count as an actionable rotation target (setup_grade's
# letter_for emits A / A- / B / C / D; '—' is hard-blocked, 'n/a' unmeasured).
_AB_LETTERS = ("A", "A-", "B")


def _cfg(config: dict | None) -> dict:
    if not isinstance(config, dict):
        return {}
    block = config.get("redeploy_aware_tp")
    return block if isinstance(block, dict) else {}


def enabled(config: dict | None) -> bool:
    """True only when briefing.yaml opts in (default OFF → legacy)."""
    return bool(_cfg(config).get("enabled"))


def hold_target_pct(config: dict | None) -> float:
    """Raised capture floor while no path exists (fraction, default 0.75)."""
    try:
        v = float(_cfg(config).get("hold_target_pct", _DEFAULT_HOLD_TARGET))
    except (TypeError, ValueError):
        return _DEFAULT_HOLD_TARGET
    return v if 0.0 < v <= 1.0 else _DEFAULT_HOLD_TARGET


def hard_ceiling_pct(config: dict | None) -> float:
    """Capture at/above which a winner ALWAYS closes (fraction, default
    0.85 — matches the smart-take-profit hard ceiling)."""
    try:
        v = float(_cfg(config).get("hard_ceiling_pct", _DEFAULT_HARD_CEILING))
    except (TypeError, ValueError):
        return _DEFAULT_HARD_CEILING
    return v if 0.0 < v <= 1.0 else _DEFAULT_HARD_CEILING


def close_impact_from_review(rev: dict | None) -> dict | None:
    """Derive this position's close impact from an options-review dict:
    ``{"side", "freed_collateral", "btc_cost"}`` — all MEASURED (strike ×
    100 × |qty| collateral; current mid × 100 × |qty| buyback). None when
    the review shape is unusable."""
    if not isinstance(rev, dict):
        return None
    side = "put" if (rev.get("type") or "").upper() == "PUT" else "call"
    try:
        qty = abs(float(rev.get("qty", 0) or 0))
        strike = float(rev.get("strike") or 0)
        mid = float(rev.get("current_mid") or 0)
    except (TypeError, ValueError):
        return None
    freed = strike * 100.0 * qty if (side == "put" and strike > 0) else 0.0
    return {
        "side": side,
        "freed_collateral": freed,
        "btc_cost": mid * 100.0 * qty,
    }


def _stress_numbers(analytics) -> tuple[float, float] | None:
    """(cash, total_put_obligations) as floats from the analytics dict /
    StressCoverage object; None when unresolvable."""
    sc = analytics
    if isinstance(analytics, dict):
        sc = analytics.get("stress_coverage", analytics)
    if sc is None:
        return None
    cash = getattr(sc, "cash", None)
    obl = getattr(sc, "total_put_obligations", None)
    if isinstance(sc, dict):
        cash = sc.get("cash", cash)
        obl = sc.get("total_put_obligations", obl)
    try:
        cash_f = float(cash)
        obl_f = float(obl)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(cash_f) and math.isfinite(obl_f)):
        return None
    return cash_f, obl_f


def coverage_after_components(cash: float, obligation: float, *,
                              freed: float = 0.0, btc_cost: float = 0.0,
                              new_obligation: float = 0.0,
                              new_premium: float = 0.0) -> float | None:
    """The HONEST post-action coverage projection — single source of truth.

    ``(cash − buybacks + new premium) / (obligation − freed + new obligation)``

    The book is margin-secured: freed put collateral does NOT return to cash
    when a put closes (verifiable on the real snapshots — cash history did
    not drop $123K when SNDK $1230P opened, so it cannot rise $123K when it
    closes). Closing a put shrinks the OBLIGATION side; the only cash moves
    are the buyback debit and any new-open premium credit. The 2026-08-14
    briefing's "Coverage after: 0.11× → ~0.38×" treated freed collateral as
    new cash ((cash + freed) / (obl − freed)); the honest number was ~0.13×.

    Returns ``inf`` when the projected obligation is retired entirely; None
    when the inputs are unresolvable (fail-open — never fabricate)."""
    try:
        cash_f = float(cash)
        obl_f = float(obligation)
        freed_f = float(freed or 0.0)
        btc_f = float(btc_cost or 0.0)
        new_obl_f = float(new_obligation or 0.0)
        new_prem_f = float(new_premium or 0.0)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(cash_f) and math.isfinite(obl_f)):
        return None
    proj_obl = obl_f - freed_f + new_obl_f
    if proj_obl <= 0:
        return float("inf")
    return (cash_f - btc_f + new_prem_f) / proj_obl


def post_close_coverage(analytics, close_impact: dict | None) -> float | None:
    """Projected coverage ratio AFTER closing this position:
    (cash − buyback) / (obligation − freed collateral). ``inf`` when the
    close retires the last obligation; None when inputs are unresolvable
    or the position frees no collateral."""
    if not isinstance(close_impact, dict):
        return None
    try:
        freed = float(close_impact.get("freed_collateral") or 0)
        btc = float(close_impact.get("btc_cost") or 0)
    except (TypeError, ValueError):
        return None
    if freed <= 0:
        return None
    nums = _stress_numbers(analytics)
    if nums is None:
        return None
    cash, obl = nums
    return coverage_after_components(cash, obl, freed=freed, btc_cost=btc)


def ab_setups(best_setups: dict | None) -> list[dict]:
    """A/B-graded CSP setups from ``collect_best_setups`` output — the pool
    is already past the RSI hard block and the delivered-yield floor, so
    presence here means a genuine rotation target for freed put collateral
    exists today. Capacity-deferred entries COUNT (rotation is the path)."""
    if not isinstance(best_setups, dict):
        return []
    out = []
    for e in best_setups.get("csp") or []:
        if isinstance(e, dict) and e.get("letter") in _AB_LETTERS:
            out.append(e)
    return out


def redeployment_path(analytics=None, best_setups: dict | None = None,
                      close_impact: dict | None = None,
                      config: dict | None = None) -> tuple[bool, str]:
    """Does the freed money from closing this winner have somewhere to go?

    Returns ``(path_exists, reason)``. ``reason`` is "" when the answer is
    the uninteresting one (gates open / fail-open / call-side) so callers
    can annotate only the cases worth a line.
    """
    side = (close_impact or {}).get("side")
    if side and side != "put":
        # Call-side closes free shares, not gated cash — legacy behavior.
        return True, ""
    ratio = coverage_ratio_from(analytics)
    if ratio is None:
        # Fail-OPEN: unresolvable coverage must never trap winners at a
        # raised floor (rule #19 fail direction — prefer legacy behavior).
        return True, ""
    floor = min_coverage_ratio(config)
    if ratio >= floor:
        return True, ""  # (a) gates open — today's behavior, no annotation
    # (b) the close itself reopens the gates (the META case).
    cov_after = post_close_coverage(analytics, close_impact)
    if cov_after is not None and cov_after >= floor:
        freed = float((close_impact or {}).get("freed_collateral") or 0)
        cov_s = "∞" if math.isinf(cov_after) else f"{cov_after:.2f}×"
        return True, (f"closing frees ${freed:,.0f} → coverage {cov_s} "
                      f"(gates reopen)")
    # (c) an A/B-graded setup above the yield floor is waiting today.
    ab = ab_setups(best_setups)
    if ab:
        names = ", ".join(
            f"{e.get('ticker', '?')} {e.get('letter', '?')}" for e in ab[:3])
        return True, (f"{len(ab)} A/B setup{'s' if len(ab) != 1 else ''} "
                      f"waiting ({names})")
    return False, (f"gates closed {ratio:.2f}×; no A/B setups above floor")


def over_cap_risk_exemption(rev: dict | None, snapshot_data: dict | None,
                            config: dict | None) -> str | None:
    """RISK-EXEMPTION for the no-redeploy-path hold (rule #43, 2026-08-17).

    Observed on the real 2026-08-17 briefing: SNDK $1230P (+62%, $123K
    obligation → 11.0% of NLV) and SOXX $520P (equity 6.5% + $52K put
    obligation → 11.1% of NLV) were demoted to "2 winners held for 75%+
    (no redeploy path)" while red flags #9/#10 explicitly instructed
    "Close or roll down the SNDK/SOXX put(s) to bring obligation-inclusive
    exposure under 8%." A close that REDUCES an over-cap concentration is
    risk-driven — same class as loss stops / earnings exits / the hard
    ceiling — and must never be trapped by the yield-motivated hold.

    Returns the visible risk tag when the underlying's CURRENT
    obligation-inclusive concentration (position_tiers.
    projected_name_concentration with ZERO new contracts — the red-flags 4c
    math, reused not duplicated) is over its tier cap; None otherwise.
    Fail direction: any missing input → None (no exemption is ever
    fabricated from missing data — rule #19; the hold logic keeps its own
    fail-open behavior).
    """
    if not isinstance(rev, dict) or not isinstance(snapshot_data, dict):
        return None
    if (rev.get("type") or "").upper() != "PUT":
        return None  # the hold gate is put-side only; calls never reach here
    ticker = (rev.get("underlying")
              or str(rev.get("contract", "")).split("_")[0] or "").upper()
    if not ticker:
        return None
    try:
        from analysis.position_tiers import projected_name_concentration
    except ImportError:  # pragma: no cover — standalone/test path fallback
        try:
            from position_tiers import projected_name_concentration
        except ImportError:
            return None
    balance = snapshot_data.get("balance") or {}
    try:
        nlv = float(balance.get("accountValue") or 0)
        strike = float(rev.get("strike") or 0)
    except (TypeError, ValueError):
        return None
    if nlv <= 0 or strike <= 0:
        return None
    res = projected_name_concentration(
        ticker, strike, 0, nlv, snapshot_data.get("positions"), config)
    if res is None or not res.over:
        return None
    return (f"risk: {ticker} at {res.pct:.1f}% of NLV over the "
            f"{res.cap_pct:g}% Tier {res.tier} cap — close restores "
            f"compliance")


def hold_note(capture_pct: float, reason: str, target_pct: float) -> str:
    """The visible hold-for-more line (rule #24 — never silently
    suppressed). Every number is measured this cycle."""
    return (f"⏳ Holding for {target_pct * 100:.0f}%+ — no redeployment "
            f"path ({reason}). Would close at {capture_pct:.0f}% if a "
            f"path opens.")
