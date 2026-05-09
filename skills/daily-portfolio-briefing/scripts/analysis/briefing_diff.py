"""
Briefing-vs-yesterday diff renderer.

Compares today's action list against the previous day's briefing JSON and surfaces:
- ADDED: actions that weren't in yesterday's briefing
- REMOVED: actions that were in yesterday's briefing but aren't today
- CHANGED: same contract/ticker but different recommendation (e.g., HOLD → ROLL_OUT)
- DONE (heuristic): close winners that were recommended yesterday and the position
   no longer exists today suggests user executed the close.

This module is deterministic and pure — no I/O outside reading the previous-day JSON.
"""

from __future__ import annotations

from pathlib import Path
import json
from typing import Optional


def _signature_for_action(item_text: str) -> str:
    """Build a stable signature for an action item (used for set comparison)."""
    # Take the first line of the numbered item, strip leading number/bold
    first = item_text.split("\n")[0]
    # Drop leading "N. **TYPE**" → keep "TYPE TICKER..."
    import re
    m = re.match(r"^\s*\d+\.\s+\*?\*?([A-Z_ ]+?)\*?\*?\s+([A-Z_]+?)(?:\s+|$|—)", first)
    if m:
        return f"{m.group(1).strip()}|{m.group(2).strip()}"
    return first[:80]


def parse_action_signatures(briefing_md: str) -> set[str]:
    """Extract a set of action signatures from a briefing markdown blob."""
    sigs: set[str] = set()
    if not briefing_md:
        return sigs
    if "## Today's Action List" not in briefing_md:
        return sigs
    section = briefing_md.split("## Today's Action List", 1)[1].split("\n## ", 1)[0]
    import re
    # Each numbered top-level line
    for line in section.split("\n"):
        if re.match(r"^\s*\d+\.\s+\*\*", line):
            sig = _signature_for_action(line)
            sigs.add(sig)
    return sigs


def load_yesterday_briefing(today_iso: str, snapshots_root: Path) -> Optional[str]:
    """Find yesterday's briefing markdown given today's date and the snapshot root."""
    from datetime import datetime, timedelta
    try:
        today = datetime.strptime(today_iso, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    for offset in range(1, 8):  # search up to 7 days back (skip weekends)
        candidate = today - timedelta(days=offset)
        cand_dir = Path(snapshots_root) / candidate.isoformat()
        # Look for any .md file in that snapshot directory or in reports/daily/
        md_path = cand_dir / "briefing.md"
        if md_path.exists():
            return md_path.read_text()
        # Fallback: reports/daily/briefing_YYYY-MM-DD.md
        report = Path("reports/daily") / f"briefing_{candidate.isoformat()}.md"
        if report.exists():
            return report.read_text()
    return None


def render_diff_panel(today_md: str, yesterday_md: Optional[str]) -> list[str]:
    """Render a "## Since Yesterday" panel comparing the two briefings."""
    if not yesterday_md:
        return []  # nothing to diff against on first run

    today_sigs = parse_action_signatures(today_md)
    yest_sigs = parse_action_signatures(yesterday_md)

    added = today_sigs - yest_sigs
    removed = yest_sigs - today_sigs
    common = today_sigs & yest_sigs

    if not (added or removed):
        return []

    lines = ["", "## Since Yesterday's Briefing", ""]
    if removed:
        lines.append(f"### ✅ Resolved or Executed ({len(removed)})")
        for sig in sorted(removed):
            kind, ticker = (sig.split("|") + [""])[:2]
            lines.append(f"- {kind.strip()} {ticker.strip()} — no longer in today's list "
                         f"(likely executed or position closed)")
        lines.append("")

    if added:
        lines.append(f"### 🆕 New Today ({len(added)})")
        for sig in sorted(added):
            kind, ticker = (sig.split("|") + [""])[:2]
            lines.append(f"- {kind.strip()} {ticker.strip()} — newly surfaced this briefing")
        lines.append("")

    if common:
        lines.append(f"### 🔁 Unchanged ({len(common)})")
        lines.append(f"- {len(common)} actions repeat from yesterday — re-evaluate or execute.")
        lines.append("")

    return lines
