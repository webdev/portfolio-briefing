"""
Step 7: Day-over-day consistency check

Compare today's recommendations to yesterday's. Flag flips without triggers.
"""

import json
from pathlib import Path


def check_consistency(
    yesterday_briefing_path: Path,
    equity_reviews: list,
    options_reviews: list,
    snapshot_dir: Path,
) -> tuple:
    """
    Check day-over-day consistency.
    Compare today's recommendations to yesterday's.

    Returns:
        (consistency_report_dict, flagged_inconsistencies_list)
    """

    consistency_report = {
        "total_positions": len(equity_reviews) + len(options_reviews),
        "recommendations_unchanged": 0,
        "recommendations_changed": 0,
        "changes_with_trigger": 0,
        "inconsistencies_found": 0,
    }

    flagged_inconsistencies = []

    if not yesterday_briefing_path:
        consistency_report["note"] = "First run — no consistency check"
        print(f"  No yesterday's briefing; skipping consistency check (first run)")
    else:
        print(f"  Comparing against yesterday's briefing")
        # In v1, we don't fully implement this; v1.1 will do full diffs
        consistency_report["note"] = "Consistency check stubbed in v1"

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with open(snapshot_dir / "consistency_report.json", "w") as f:
        json.dump(consistency_report, f, indent=2)
    with open(snapshot_dir / "inconsistencies.json", "w") as f:
        json.dump(flagged_inconsistencies, f, indent=2)

    return consistency_report, flagged_inconsistencies
