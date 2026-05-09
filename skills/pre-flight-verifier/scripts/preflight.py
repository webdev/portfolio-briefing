"""
Pre-flight verifier — master gate orchestrator.

Runs all safety checks in priority order and produces a single consolidated
verdict: RELEASE / WARN / BLOCK. Replaces the earlier gate_and_render() so
every safety skill plugs into one hook point.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# Wire in the four downstream gates (best-effort imports)
_SKILLS_ROOT = Path(__file__).resolve().parents[2]
for sub in ("live-data-policer", "broker-position-reconciler",
            "briefing-data-verifier", "briefing-quality-gate"):
    sys.path.insert(0, str(_SKILLS_ROOT / sub / "scripts"))

try:
    from police import police_data_freshness  # type: ignore
except ImportError:
    def police_data_freshness(snapshot_data, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(verdict="PASS", live=True, panel_md="",
                               blocking_sources=[], stale_sources=[])

try:
    from reconcile import reconcile_positions  # type: ignore
except ImportError:
    def reconcile_positions(md, snapshot_positions, broker_positions, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(verified=True, mismatches=[], missing_in_snapshot=[],
                               missing_at_broker=[], panel_md="", block_actions=[])

try:
    from verify import verify_live_data  # type: ignore
except ImportError:
    def verify_live_data(md, strict_mode=False):
        from types import SimpleNamespace
        return SimpleNamespace(verified=True, panel_md="", flagged_lines=[])

try:
    from run_quality_gate import run_quality_gate  # type: ignore
except ImportError:
    def run_quality_gate(md):
        return {"passed": True, "personas": {}, "blocking_issues": [],
                "recommended_action": "release"}


@dataclass
class PreFlightResult:
    verdict: str  # RELEASE | WARN | BLOCK
    gate_results: dict = field(default_factory=dict)
    blocking_gate: Optional[str] = None
    consolidated_panel_md: str = ""
    rendered_briefing: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def run_pre_flight(
    briefing_md: str,
    snapshot_positions: Optional[list] = None,
    broker_positions: Optional[list] = None,
    snapshot_data: Optional[dict] = None,
    strict_mode: bool = False,
) -> PreFlightResult:
    """
    Run every gate in priority order. Return a unified verdict + rendered briefing.

    snapshot_data: full snapshot dict including data_provenance for the live-data
    policer. If absent, the policer is skipped (and treated as advisory).
    """
    panels: list[str] = []
    gate_results: dict = {}
    blocking_gate: Optional[str] = None

    # Gate 0: live-data policer — HIGHEST PRIORITY (no point validating cached data)
    if snapshot_data is not None:
        ldp = police_data_freshness(snapshot_data)
        gate_results["live_data_policer"] = {
            "verdict": getattr(ldp, "verdict", "PASS"),
            "live": getattr(ldp, "live", True),
            "stale_sources": getattr(ldp, "stale_sources", []),
            "blocking_sources": getattr(ldp, "blocking_sources", []),
        }
        if getattr(ldp, "panel_md", ""):
            panels.append(ldp.panel_md)
        if getattr(ldp, "verdict", "PASS") == "BLOCK":
            blocking_gate = "live_data_policer"
    else:
        gate_results["live_data_policer"] = {"verdict": "SKIPPED",
                                              "reason": "no snapshot_data provided"}

    # Gate 1: broker-position reconciler — second-highest priority
    if snapshot_positions is not None and broker_positions is not None:
        recon = reconcile_positions(briefing_md, snapshot_positions, broker_positions)
        gate_results["position_reconciler"] = {
            "verified": getattr(recon, "verified", True),
            "mismatches": getattr(recon, "mismatches", []),
            "missing_in_snapshot": getattr(recon, "missing_in_snapshot", []),
            "block_actions": getattr(recon, "block_actions", []),
        }
        if getattr(recon, "panel_md", ""):
            panels.append(recon.panel_md)
        if not getattr(recon, "verified", True):
            blocking_gate = "position_reconciler"
    else:
        gate_results["position_reconciler"] = {"verified": None, "skipped": True}
        # Warn — position freshness can't be verified
        panels.append(
            "\n## ⚠️ Position Reconciler Skipped\n\n"
            "Live broker positions were not provided. The briefing's recommendations "
            "have NOT been verified against your actual broker holdings. Treat any "
            "action that references an existing position as advisory until reconciled.\n"
        )

    # Gate 2: briefing-data-verifier — live-chain provenance
    dv = verify_live_data(briefing_md, strict_mode=strict_mode)
    gate_results["data_verifier"] = {
        "verified": getattr(dv, "verified", True),
        "panel_md": getattr(dv, "panel_md", ""),
        "flagged": getattr(dv, "flagged_lines", []),
    }
    if getattr(dv, "panel_md", ""):
        panels.append(dv.panel_md)

    # Gate 3: briefing-quality-gate — structural / persona checks
    qg = run_quality_gate(briefing_md)
    gate_results["quality_gate"] = qg
    if not qg.get("passed", True) and qg.get("blocking_issues"):
        # Critical structural failure
        if not blocking_gate:
            blocking_gate = "quality_gate"
        # Build a quality-gate panel
        qg_lines = ["", "## ⚠️ Quality Gate Issues", ""]
        for issue in qg.get("blocking_issues", []):
            qg_lines.append(f"- {issue}")
        qg_lines.append("")
        panels.append("\n".join(qg_lines))

    # Decide overall verdict
    if blocking_gate:
        verdict = "BLOCK"
    elif panels:
        verdict = "WARN"
    else:
        verdict = "RELEASE"

    consolidated = "\n".join(panels) if panels else ""

    # Render the final briefing with header
    if verdict == "BLOCK":
        header = (
            "# 🚫 PRE-FLIGHT BLOCKED — DO NOT ACT ON THIS BRIEFING\n\n"
            f"**Blocking gate:** {blocking_gate}\n\n"
            "One or more critical safety checks failed. Resolve the issues below "
            "and refresh the briefing before placing any orders.\n"
        )
        rendered = header + consolidated + "\n---\n\n" + briefing_md
    elif verdict == "WARN":
        rendered = consolidated + "\n" + briefing_md
    else:
        rendered = briefing_md

    return PreFlightResult(
        verdict=verdict,
        gate_results=gate_results,
        blocking_gate=blocking_gate,
        consolidated_panel_md=consolidated,
        rendered_briefing=rendered,
    )
