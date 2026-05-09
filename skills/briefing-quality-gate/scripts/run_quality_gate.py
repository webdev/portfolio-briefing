"""Orchestrator for briefing quality gate checks."""

import re
import sys
from pathlib import Path
from typing import Optional

from personas import (
    financial_advisor_check,
    options_trader_check,
    tax_cpa_check,
    risk_manager_check,
)

# Wire in the briefing-data-verifier (live-data provenance check)
_DV_PATH = Path(__file__).resolve().parents[2] / "briefing-data-verifier" / "scripts"
sys.path.insert(0, str(_DV_PATH))
try:
    from verify import verify_live_data  # type: ignore
except ImportError:
    def verify_live_data(md, strict_mode=False):
        from types import SimpleNamespace
        return SimpleNamespace(verified=True, live_actions=0, stubbed_actions=0,
                                flagged_lines=[], panel_md="")


def run_quality_gate(briefing_md: str) -> dict:
    """Run all four persona checks on the briefing markdown.

    Args:
        briefing_md: rendered briefing markdown string

    Returns:
        {
            "passed": bool,
            "personas": {
                "financial_advisor": {score, issues, strengths},
                "options_trader": {score, issues, strengths},
                "tax_cpa": {score, issues, strengths},
                "risk_manager": {score, issues, strengths},
            },
            "blocking_issues": [str],  # critical failures
            "recommended_action": "release" | "auto-fix" | "block-and-show-issues"
        }
    """
    # Run all four persona checks
    fa_result = financial_advisor_check(briefing_md)
    ot_result = options_trader_check(briefing_md)
    tc_result = tax_cpa_check(briefing_md)
    rm_result = risk_manager_check(briefing_md)

    results = {
        "financial_advisor": fa_result.to_dict(),
        "options_trader": ot_result.to_dict(),
        "tax_cpa": tc_result.to_dict(),
        "risk_manager": rm_result.to_dict(),
    }

    # Collect all critical issues
    blocking_issues = []
    for persona_name, persona_result in [
        ("financial_advisor", fa_result),
        ("options_trader", ot_result),
        ("tax_cpa", tc_result),
        ("risk_manager", rm_result),
    ]:
        for issue in persona_result.issues:
            if issue.severity == "critical":
                blocking_issues.append(f"[{persona_name}] {issue.text}")

    # Determine pass/fail
    # Any critical issue auto-fails the gate
    if blocking_issues:
        passed = False
    else:
        all_pass = all(
            result["score"] >= 70
            for result in results.values()
        )
        passed = all_pass

    # Recommend action
    if passed:
        recommended_action = "release"
    elif blocking_issues:
        recommended_action = "block-and-show-issues"
    else:
        recommended_action = "auto-fix"

    return {
        "passed": passed,
        "personas": results,
        "blocking_issues": blocking_issues,
        "recommended_action": recommended_action,
    }


def gate_and_render(briefing_md: str) -> str:
    """Run quality gate and prepend warnings panel if failed.

    Args:
        briefing_md: rendered briefing markdown

    Returns:
        Either unchanged briefing_md (if passed) or markdown with warnings panel prepended
    """
    gate_result = run_quality_gate(briefing_md)

    # Live-data verification — prepend a panel if any actions are stub-derived.
    dv_result = verify_live_data(briefing_md, strict_mode=False)
    dv_panel = getattr(dv_result, "panel_md", "") if dv_result is not None else ""

    if gate_result["passed"] and not dv_panel:
        return briefing_md
    if gate_result["passed"] and dv_panel:
        # Pass overall, but live-data has notes — prepend them.
        return dv_panel + "\n" + briefing_md

    # Build warnings panel
    warnings_lines = ["## ⚠️ Quality Gate Issues", ""]

    if gate_result["blocking_issues"]:
        warnings_lines.append("### Critical Issues (must fix before release)")
        warnings_lines.append("")
        for issue in gate_result["blocking_issues"]:
            warnings_lines.append(f"- {issue}")
        warnings_lines.append("")

    # Add persona scores
    warnings_lines.append("### Persona Scores")
    warnings_lines.append("")
    for persona_name, result in gate_result["personas"].items():
        score = result["score"]
        status = "✓ PASS" if score >= 70 else "✗ FAIL"
        warnings_lines.append(f"- **{persona_name.replace('_', ' ').title()}:** {score}/100 {status}")
        if result["issues"]:
            for issue in result["issues"]:
                severity_emoji = {"critical": "🔴", "major": "🟠", "minor": "🟡"}
                emoji = severity_emoji.get(issue["severity"], "•")
                warnings_lines.append(f"  {emoji} {issue['text']}")
    warnings_lines.append("")

    # Add recommendation
    warnings_lines.append("### Recommended Action")
    warnings_lines.append(f"- {gate_result['recommended_action'].upper()}")
    warnings_lines.append("")

    warnings_panel = "\n".join(warnings_lines)

    # If live-data verifier flagged stubs, prepend that panel too
    if dv_panel:
        return dv_panel + "\n" + warnings_panel + "\n" + briefing_md
    return warnings_panel + "\n" + briefing_md


if __name__ == "__main__":
    # Simple CLI for testing
    if len(sys.argv) > 1:
        input_file = Path(sys.argv[1])
        with open(input_file) as f:
            md = f.read()
    else:
        md = sys.stdin.read()

    result = run_quality_gate(md)
    print("=== QUALITY GATE RESULT ===")
    print(f"Passed: {result['passed']}")
    print(f"Recommendation: {result['recommended_action']}")
    print("\nPersona Scores:")
    for name, scores in result["personas"].items():
        print(f"  {name}: {scores['score']}/100")
    if result["blocking_issues"]:
        print("\nBlocking Issues:")
        for issue in result["blocking_issues"]:
            print(f"  - {issue}")
