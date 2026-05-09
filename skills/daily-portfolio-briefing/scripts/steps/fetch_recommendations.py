"""
Step 1.6: Fetch third-party recommendations

Calls recommendation-list-fetcher to get BUY/SELL/HOLD list from Google Sheet.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


# Path to the recommendation-list-fetcher skill, relative to this skill's root
# this file: .../skills/daily-portfolio-briefing/scripts/steps/fetch_recommendations.py
# parents:    [0] steps/ [1] scripts/ [2] daily-portfolio-briefing/ [3] skills/ [4] portfolio-briefing/
_REPO_ROOT = Path(__file__).resolve().parents[4]
_REC_SKILL = _REPO_ROOT / "skills" / "recommendation-list-fetcher"


def fetch_recommendations(snapshot_dir: Path) -> list:
    """
    Fetch recommendations from recommendation-list-fetcher skill.

    Returns:
        List of normalized recommendation dicts. Empty list on failure
        (briefing degrades gracefully — recommendations are enrichment, not load-bearing).
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    output_path = snapshot_dir / "recommendations_list.json"
    config_path = _REC_SKILL / "config" / "recommendation_list_config.yaml"

    # If user hasn't created a config yet, fall back to the template
    if not config_path.exists():
        template = _REC_SKILL / "assets" / "config_template.yaml"
        if template.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy(template, config_path)
        else:
            print(f"  Recommendation skill not configured; skipping (no template at {template})")
            with open(output_path, "w") as f:
                json.dump({"recommendations": [], "skipped": "no_config"}, f)
            return []

    # Invoke the recommendation-list-fetcher CLI
    cli = _REC_SKILL / "scripts" / "fetch_recommendations.py"
    cmd = [
        sys.executable,
        str(cli),
        "--config", str(config_path),
        "--output", str(output_path),
    ]
    env = {**os.environ, "PYTHONPATH": str(_REC_SKILL / "scripts")}

    try:
        result = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=120, check=False
        )
        if result.returncode != 0:
            print(f"  Recommendation fetch failed (exit {result.returncode}); continuing without")
            print(f"    stderr: {result.stderr.strip()[:200]}")
            with open(output_path, "w") as f:
                json.dump({"recommendations": [], "skipped": "fetch_failed"}, f)
            return []
    except subprocess.TimeoutExpired:
        print("  Recommendation fetch timed out; continuing without")
        with open(output_path, "w") as f:
            json.dump({"recommendations": [], "skipped": "timeout"}, f)
        return []

    # Load the JSON the fetcher produced and pluck out recommendations
    try:
        with open(output_path) as f:
            data = json.load(f)
        recs = data.get("recommendations", [])
        summary = data.get("summary", {})
        buy = summary.get("buy_count", 0)
        sell = summary.get("sell_count", 0)
        hold = summary.get("hold_count", 0)
        print(f"  Fetched {len(recs)} recommendations: {buy} BUY, {sell} SELL, {hold} HOLD")
        return recs
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"  Recommendation output unreadable: {e}")
        return []
