"""Portfolio capacity gates — HARD blocks on new short-put entries.

Implements 06-wheel-parameters.md §7A (added 2026-06-11). Two layers:

1. Portfolio-level gates (``evaluate_gates``) — block ALL new short puts when
   stress coverage < 0.50x, cash < 5% NLV, or total put obligation > 80% NLV.
   The resulting ``GateState`` carries a one-line banner printed in the
   briefing header and at the top of the candidates / when-to-enter reports.

2. Per-candidate gates (``check_new_entry``) — per-name put-count cap (max 1,
   with a narrow second-put exception), per-name collateral cap (6% NLV), and
   the expiry-cluster cap (no single expiration's put obligation > 25% NLV).

Obligation math is NOT duplicated here — it reuses
``analysis.stress_coverage.compute_stress_coverage`` (cash, total obligation,
coverage ratio), and short puts are identified the same way that module does
(``position_type == "short_put"`` with a positive strike).

Config: ``briefing.yaml`` → ``capacity_gates:``. Every key is read with a
default so the module works unchanged when the block is missing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

try:
    from analysis.stress_coverage import compute_stress_coverage
except ImportError:  # pragma: no cover - path fallback for standalone runs
    from stress_coverage import compute_stress_coverage


_DEFAULTS = {
    "min_stress_coverage_for_new_puts": 0.50,
    "min_cash_pct_for_new_puts": 0.05,
    "max_short_puts_per_name": 1,
    "second_put_exception_max_collateral_pct": 0.06,
    "second_put_exception_min_expiry_gap_days": 60,
    "max_collateral_per_name_pct": 0.06,
    "max_obligation_per_expiry_pct": 0.25,
    "max_total_put_obligation_pct": 0.80,
}


@dataclass
class GateState:
    """Result of the portfolio-level capacity-gate evaluation."""
    open: bool                  # all portfolio-level gates pass
    coverage_ratio: float
    cash_pct: float             # cash / NLV
    obligation_pct: float       # total put obligation / NLV
    reasons: list[str] = field(default_factory=list)  # empty when open
    banner: str = ""
    # Context carried along so per-candidate checks (check_new_entry) can run
    # downstream without re-plumbing positions/NLV through every renderer.
    positions: list = field(default_factory=list)
    nlv: float = 0.0


def _gate_cfg(config: dict | None) -> dict:
    """Read the capacity_gates block with .get() defaults (works without it)."""
    block = {}
    if isinstance(config, dict):
        block = config.get("capacity_gates") or {}
    return {k: block.get(k, v) for k, v in _DEFAULTS.items()}


def _normalize_positions(positions: list | None) -> list:
    """Ensure position_type is present — same derivation as compute_analytics.

    Snapshot positions carry assetType/type/qty; the analytics enrichment that
    adds position_type may not have run yet when the gates are evaluated.
    Non-destructive: positions that already carry position_type pass through.
    """
    out = []
    for p in positions or []:
        if not isinstance(p, dict):
            continue
        if not p.get("position_type"):
            p = dict(p)
            asset = (p.get("assetType") or "").upper()
            if asset == "EQUITY":
                p["position_type"] = "long_stock"
                p.setdefault("underlying_price", p.get("price", 0.0))
            elif asset == "OPTION":
                qty = p.get("qty", p.get("quantity", 0)) or 0
                opt_type = (p.get("type") or p.get("option_type") or "").lower()
                p["position_type"] = ("short_" if qty < 0 else "long_") + opt_type
        out.append(p)
    return out


def _short_puts(positions: list) -> list:
    """Short puts, identified the same way stress_coverage.py does."""
    return [
        p for p in positions
        if p.get("position_type") == "short_put" and p.get("strike", 0) > 0
    ]


def _underlying(p: dict) -> str:
    return (p.get("underlying") or p.get("symbol") or "").upper()


def _contracts(p: dict) -> int:
    qty = abs(p.get("qty", p.get("quantity", 0)) or 0)
    return int(qty) or 1


def _collateral(p: dict) -> float:
    return float(p.get("strike", 0) or 0) * _contracts(p) * 100


def _to_date(d) -> date | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    try:
        return date.fromisoformat(str(d)[:10])
    except (ValueError, TypeError):
        return None


def _fmt_cov(coverage: float) -> str:
    return "∞" if math.isinf(coverage) else f"{coverage:.2f}x"


def evaluate_gates(positions: list, cash, nlv, config: dict | None) -> GateState:
    """Evaluate the portfolio-level capacity gates.

    Gate order per §7A: stress-coverage floor (coverage OR cash) →
    total-obligation ceiling. All failures are collected so the banner /
    blocked notes can name every breached gate, first one headlining.
    """
    cash_d = Decimal(str(cash or 0))
    nlv_d = Decimal(str(nlv or 0))
    # Guard against NaN/Infinity Decimals — `Decimal(str("nan"))` is a real
    # NaN, and `nan > 0` raises decimal.InvalidOperation. Upstream snapshot
    # has been seen to produce NaN NLV when E*TRADE returns malformed
    # account totals (~2026-06-18 run). Fail closed: treat as zero so the
    # gate evaluates as CLOSED instead of crashing the pipeline.
    if not cash_d.is_finite():
        cash_d = Decimal(0)
    if not nlv_d.is_finite():
        nlv_d = Decimal(0)
    cfg = _gate_cfg(config)

    norm = _normalize_positions(positions)
    sc = compute_stress_coverage(norm, cash_d, nlv_d)

    coverage = sc.coverage_ratio
    cash_pct = float(cash_d / nlv_d) if nlv_d > 0 else 0.0
    obligation_pct = float(sc.total_put_obligations / nlv_d) if nlv_d > 0 else 0.0

    min_cov = float(cfg["min_stress_coverage_for_new_puts"])
    min_cash = float(cfg["min_cash_pct_for_new_puts"])
    max_obl = float(cfg["max_total_put_obligation_pct"])

    reasons: list[str] = []
    if coverage < min_cov:
        reasons.append(f"coverage {_fmt_cov(coverage)} < {min_cov:.2f}x")
    if cash_pct < min_cash:
        reasons.append(f"cash {cash_pct * 100:.1f}% < {min_cash * 100:.0f}% NLV")
    if obligation_pct > max_obl:
        reasons.append(
            f"obligation {obligation_pct * 100:.0f}% NLV > {max_obl * 100:.0f}% NLV"
        )

    is_open = not reasons
    state_s = "OPEN" if is_open else f"CLOSED ({reasons[0]})"
    banner = (
        f"CAPACITY: coverage {_fmt_cov(coverage)} | cash {cash_pct * 100:.1f}% | "
        f"obligation {obligation_pct * 100:.0f}% NLV | ENTRY GATES: {state_s}"
    )

    return GateState(
        open=is_open,
        coverage_ratio=coverage,
        cash_pct=cash_pct,
        obligation_pct=obligation_pct,
        reasons=reasons,
        banner=banner,
        positions=norm,
        nlv=float(nlv_d),
    )


def check_new_entry(
    symbol: str,
    collateral,
    expiry,
    positions: list,
    nlv,
    gate_state: GateState | None,
    config: dict | None,
) -> tuple[bool, str]:
    """Per-candidate capacity check for ONE proposed new short put.

    Args:
        symbol: underlying ticker of the proposed put
        collateral: strike × contracts × 100 of the proposed put (dollars)
        expiry: proposed expiration (date or ISO string)
        positions: current positions (raw snapshot or enriched — both work)
        nlv: net liquidation value
        gate_state: optional portfolio GateState (closed → hard fail)
        config: briefing config (capacity_gates block, defaults if missing)

    Returns (ok, reason); reason is "" when ok.
    """
    cfg = _gate_cfg(config)
    sym = (symbol or "").upper()
    nlv_f = float(nlv or 0)
    new_collateral = float(collateral or 0)

    if gate_state is not None and not gate_state.open:
        first = gate_state.reasons[0] if gate_state.reasons else "capacity"
        return False, f"entry gates closed ({first})"

    shorts = _short_puts(_normalize_positions(positions))
    name_puts = [p for p in shorts if _underlying(p) == sym]
    name_count = sum(_contracts(p) for p in name_puts)
    name_collateral = sum(_collateral(p) for p in name_puts)

    max_per_name = int(cfg["max_short_puts_per_name"])
    exc_max_coll_pct = float(cfg["second_put_exception_max_collateral_pct"])
    exc_min_gap = int(cfg["second_put_exception_min_expiry_gap_days"])
    max_name_coll_pct = float(cfg["max_collateral_per_name_pct"])
    max_expiry_pct = float(cfg["max_obligation_per_expiry_pct"])

    new_exp = _to_date(expiry)

    # Per-name put-count cap (max 1; a 2nd allowed only under the exception:
    # combined collateral ≤ 6% NLV AND expirations ≥ 60 days apart).
    if name_count > max_per_name:
        return False, (
            f"already {name_count} short puts open on {sym} (max {max_per_name})"
        )
    if name_count == max_per_name and name_count > 0:
        combined = name_collateral + new_collateral
        if nlv_f > 0 and combined > exc_max_coll_pct * nlv_f:
            return False, (
                f"2nd {sym} put blocked — combined collateral ${combined:,.0f} "
                f"> {exc_max_coll_pct:.0%} NLV"
            )
        existing_exps = [_to_date(p.get("expiration")) for p in name_puts]
        existing_exps = [e for e in existing_exps if e is not None]
        if new_exp is None or not existing_exps:
            # Fail closed — the exception requires a verifiable expiry gap.
            return False, (
                f"2nd {sym} put blocked — expiry gap unverifiable "
                f"(need ≥ {exc_min_gap}d apart)"
            )
        min_gap = min(abs((new_exp - e).days) for e in existing_exps)
        if min_gap < exc_min_gap:
            return False, (
                f"2nd {sym} put blocked — expirations {min_gap}d apart "
                f"(need ≥ {exc_min_gap}d)"
            )
        # Exception satisfied — fall through to the remaining gates.

    # Per-name collateral cap.
    if nlv_f > 0 and (name_collateral + new_collateral) > max_name_coll_pct * nlv_f:
        return False, (
            f"{sym} collateral ${name_collateral + new_collateral:,.0f} "
            f"> {max_name_coll_pct:.0%} NLV per-name cap"
        )

    # Expiry-cluster cap — no single expiration's put obligation > 25% NLV.
    if nlv_f > 0 and new_exp is not None:
        cluster = sum(
            _collateral(p) for p in shorts if _to_date(p.get("expiration")) == new_exp
        )
        cluster_pct = (cluster + new_collateral) / nlv_f
        if cluster_pct > max_expiry_pct:
            return False, (
                f"expiry cluster {new_exp.isoformat()} would reach "
                f"{cluster_pct:.0%} NLV (> {max_expiry_pct:.0%} cap)"
            )

    return True, ""
