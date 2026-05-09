"""
Step 8: Aggregate and render

Combines all step outputs into final markdown and JSON.
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from render.panels import (
    render_header,
    render_market_context,
    render_health,
    render_risk_alerts,
    render_action_list,
    render_watch,
    render_opportunities,
    render_diffs,
    render_inconsistencies,
    render_manifest,
)
from render.stress_test_panel import render_stress_test, render_stress_test_details
from render.expiration_panel import render_expiration_clusters
from render.hedge_book_panel import render_hedge_book
from render.strategy_upgrades_panel import render_strategy_upgrades
from render.analyst_brief import render_analyst_brief
from steps.compute_analytics import compute_analytics
from steps.per_option_commentary import render_watch_with_commentary
from steps.strategy_upgrades import compute_strategy_upgrades
from analysis.briefing_diff import render_diff_panel, load_yesterday_briefing

# Wire in the briefing-quality-gate skill (deterministic structural validator)
_GATE_PATH = Path(__file__).resolve().parents[3] / "briefing-quality-gate" / "scripts"
sys.path.insert(0, str(_GATE_PATH))
try:
    from run_quality_gate import gate_and_render  # type: ignore
except ImportError:
    def gate_and_render(md: str) -> str:
        return md  # no-op fallback

# Wire in the pre-flight-verifier (master gate orchestrator)
_PREFLIGHT_PATH = Path(__file__).resolve().parents[3] / "pre-flight-verifier" / "scripts"
sys.path.insert(0, str(_PREFLIGHT_PATH))
try:
    from preflight import run_pre_flight  # type: ignore
except ImportError:
    def run_pre_flight(md, snapshot_positions=None, broker_positions=None, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(verdict="RELEASE", rendered_briefing=md,
                               consolidated_panel_md="", gate_results={})


def aggregate_briefing(
    date_str: str,
    config: dict,
    snapshot_data: dict,
    regime_data: dict,
    equity_reviews: list,
    options_reviews: list,
    new_ideas: list,
    consistency_report: dict,
    flagged_inconsistencies: list,
    directives_active: list,
    directives_expired: list,
    snapshot_dir: Path,
    long_term_opportunities: list | None = None,
) -> tuple:
    """
    Aggregate all step outputs into final briefing markdown and JSON.

    Returns:
        (briefing_markdown_str, briefing_json_dict)
    """

    balance = snapshot_data.get("balance", {})
    quotes = snapshot_data.get("quotes", {})
    ytd_pnl = snapshot_data.get("ytd_pnl", {})
    nlv = balance.get("accountValue", 0)
    cash = balance.get("cash", 0)

    regime = regime_data.get("regime", "UNKNOWN")
    confidence = regime_data.get("confidence", "MEDIUM")
    triggered = regime_data.get("triggered_rules") or []
    regime_rationale = triggered[0].get("rationale", "") if triggered else ""

    # Compute analytics: stress coverage, concentration, expirations, hedges
    macro_caution = "high" if regime in ("CAUTION", "RISK_OFF") else "none"
    analytics = compute_analytics(snapshot_data, config, macro_caution=macro_caution)

    # Make config visible to render layer (used for core_positions/ltcg_rate/etc.)
    snapshot_data["_config"] = config

    # Generate action list to count items (must happen before header render)
    action_list_lines = render_action_list(equity_reviews, options_reviews, new_ideas, analytics, snapshot_data, date_str=date_str)
    # Count actual numbered items in action list (lines starting with "N."; strip whitespace first)
    action_count = sum(1 for line in action_list_lines if line and line.lstrip() and line.lstrip()[0].isdigit() and "." in line.lstrip()[:5])

    # Build markdown
    lines = []
    lines.extend(render_header(date_str, regime, nlv, cash, action_count, confidence, regime_rationale, ytd_pnl))
    lines.extend(render_market_context(regime_data, quotes))
    # Pass option positions (with real Greeks from E*TRADE) for the net-Greeks aggregate.
    # If theta is missing on positions, estimate it from current_mid + days_to_expiry as a
    # last-resort proxy (theta ≈ -mid / dte for short-dated options) so the briefing surfaces
    # a non-zero theta number rather than $0.
    options_positions = []
    for p in (snapshot_data.get("positions") or []):
        if p.get("assetType") != "OPTION":
            continue
        # Estimate theta if not provided
        if p.get("theta") is None:
            mid = p.get("currentMid") or p.get("current_price") or 0
            dte = p.get("days_to_expiry") or 30
            try:
                exp = p.get("expiration")
                if exp and not p.get("days_to_expiry"):
                    from datetime import datetime as _dt, date as _d
                    exp_d = _dt.strptime(exp, "%Y-%m-%d").date() if isinstance(exp, str) else exp
                    if isinstance(exp_d, _d):
                        dte = max(1, (exp_d - _d.today()).days)
            except (ValueError, TypeError):
                pass
            if mid and dte > 0:
                # Per-share theta (negative, decay): roughly -mid / dte for short-dated
                p["theta"] = -float(mid) / max(dte, 1)
        options_positions.append(p)
    lines.extend(render_health(equity_reviews, nlv, options_positions))

    # NEW: Render stress test panel
    lines.extend(render_stress_test(analytics["stress_coverage"], analytics["nlv"]))

    # NEW: Render stress test details (which positions get assigned)
    all_positions = snapshot_data.get("positions", [])
    lines.extend(render_stress_test_details(analytics["stress_coverage"], all_positions))

    # NEW: Render expiration clusters
    lines.extend(render_expiration_clusters(analytics["expirations"]))

    # NEW: Render hedge book
    lines.extend(render_hedge_book(analytics["hedge_book"], analytics["nlv"], analytics["spy_price"]))

    lines.extend(render_risk_alerts(equity_reviews, options_reviews, regime_data))
    lines.extend(action_list_lines)
    
    # MODIFIED: Use render_watch_with_commentary instead of plain render_watch
    lines.extend(render_watch_with_commentary(equity_reviews, options_reviews, snapshot_data))

    lines.extend(render_opportunities(new_ideas))

    # NEW (Wave 22): Long-term opportunities — ADD/TRIM/EXIT/HOLD + LEAPs + long-dated CSPs
    if long_term_opportunities:
        from steps.long_term_opportunities import render_long_term_opportunities
        lines.extend(render_long_term_opportunities(long_term_opportunities))

    # NEW: Add Strategy Upgrades panel
    upgrades = compute_strategy_upgrades(snapshot_data, equity_reviews, options_reviews, config)
    lines.extend(render_strategy_upgrades(upgrades))

    # NEW: Add Analyst Brief before diffs
    lines.extend(render_analyst_brief(equity_reviews, options_reviews, snapshot_data, analytics, regime_data))
    
    lines.extend(render_diffs(consistency_report))
    lines.extend(render_inconsistencies(flagged_inconsistencies))

    # Insert "Since Yesterday" diff panel (best-effort — silent on first run)
    try:
        snapshot_root = snapshot_dir.parent if snapshot_dir else Path("state/briefing_snapshots")
        yesterday_md = load_yesterday_briefing(date_str, snapshot_root)
        # Build the today_md from what we have so far so we can diff
        today_so_far = "\n".join(lines)
        diff_panel = render_diff_panel(today_so_far, yesterday_md)
        if diff_panel:
            # Insert near the top, after the header but before market context
            # For simplicity, append at the end before manifest
            lines.extend(diff_panel)
    except Exception:
        pass  # diff is best-effort; never break the briefing

    lines.extend(render_manifest(str(snapshot_dir)))

    briefing_markdown = "\n".join(lines)

    # Pre-flight verifier — master gate orchestrator. Runs:
    #   1. Broker-position reconciler (catches stale snapshot data)
    #   2. Live-data verifier (catches stub-derived prices)
    #   3. Quality gate (4-persona structural checks)
    # Returns RELEASE / WARN / BLOCK and prepends consolidated warning panels.
    snapshot_positions = snapshot_data.get("positions", []) or []
    broker_positions = snapshot_data.get("broker_positions")  # optional — None if not provided
    pf = run_pre_flight(
        briefing_markdown,
        snapshot_positions=snapshot_positions,
        broker_positions=broker_positions,
        snapshot_data=snapshot_data,  # for the live-data policer
    )
    briefing_markdown = pf.rendered_briefing

    # Build JSON companion
    briefing_json = {
        "date": date_str,
        "regime": regime,
        "nlv": nlv,
        "cash": cash,
        "equity_reviews": equity_reviews,
        "options_reviews": options_reviews,
        "new_ideas": new_ideas,
        "long_term_opportunities": long_term_opportunities or [],
        "strategy_upgrades": upgrades,
        "consistency_report": consistency_report,
        "directives_active_count": len(directives_active),
        "directives_expired_count": len(directives_expired),
        "snapshot_dir": str(snapshot_dir),
    }

    return briefing_markdown, briefing_json
