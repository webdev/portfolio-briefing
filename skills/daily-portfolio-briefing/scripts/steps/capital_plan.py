"""
Step 8.5: Capital plan (Wave 24)

Aggregates the full set of recommendations into a ranked, cash-flow-aware
execution plan. Uses the in-repo `capital-planner` skill — same pattern
as `long_term_opportunities.py` (loads the skill module by file path so
there are no name collisions on sys.path).

Returns a CapitalPlan dict ready for the renderer; the orchestrator just
needs to pass through the structured outputs of the prior steps.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


_REPO_ROOT = Path(__file__).resolve().parents[4]
_PLANNER = _REPO_ROOT / "skills" / "capital-planner" / "scripts"

_PLAN_MODULE: ModuleType | None = None


def _load_plan_module() -> ModuleType | None:
    global _PLAN_MODULE
    if _PLAN_MODULE is not None:
        return _PLAN_MODULE
    target = _PLANNER / "plan.py"
    if not target.exists():
        return None
    spec = importlib.util.spec_from_file_location("capital_plan_module", target)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules["capital_plan_module"] = module
    spec.loader.exec_module(module)
    _PLAN_MODULE = module
    return module


def build_capital_plan_step(
    *,
    balance: dict,
    positions: list,
    equity_reviews: list | None,
    options_reviews: list | None,
    new_ideas: list | None,
    long_term_opportunities: list | None,
    analytics: dict | None,
    recommendations_list: list | None,
    action_list_lines: list | None = None,
) -> dict | None:
    """Run the capital-planner. Returns a dict with the plan + rendered md.

    None on failure — callers treat the absence of a plan as a degraded mode
    (the briefing still renders without the panel).
    """
    mod = _load_plan_module()
    if mod is None:
        print("  [warn] capital-planner module not loadable", file=sys.stderr)
        return None

    # Derive hedge recs + coverage ratio from analytics. Both can come in
    # as dataclass instances OR plain dicts depending on the call site.
    def _attr(obj, key, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    hedge_recs: list = []
    coverage_ratio: float | None = None
    if analytics:
        hb = _attr(analytics, "hedge_book") or {}
        recommend_add = _attr(hb, "recommend_add") or []
        hedge_recs = list(recommend_add)
        sc = _attr(analytics, "stress_coverage") or {}
        coverage_ratio = _attr(sc, "coverage_ratio")

    # Map third-party recommendations to {ticker: rec_string}
    recs_map: dict = {}
    for r in (recommendations_list or []):
        t = r.get("ticker")
        rec = r.get("recommendation")
        if t and rec:
            recs_map[str(t).upper()] = str(rec).upper()

    try:
        plan = mod.build_capital_plan(
            balance=balance,
            positions=positions,
            equity_reviews=equity_reviews or [],
            options_reviews=options_reviews or [],
            new_ideas=new_ideas or [],
            long_term_opportunities=long_term_opportunities or [],
            hedge_recs=hedge_recs,
            coverage_ratio=coverage_ratio,
            third_party_recs=recs_map,
            action_list_lines=action_list_lines or [],
        )
    except Exception as e:
        print(f"  [warn] capital-planner failed: {e}", file=sys.stderr)
        return None

    md_lines = mod.format_capital_plan_md(plan)
    return {
        "plan": plan,
        "markdown": md_lines,
        "starting_cash": plan.starting_cash,
        "ending_cash_projected": plan.ending_cash_projected,
        "net_cash_change": plan.net_cash_change,
        "active_actions": len(plan.actions),
        "skipped_actions": len(plan.skipped_actions),
    }
