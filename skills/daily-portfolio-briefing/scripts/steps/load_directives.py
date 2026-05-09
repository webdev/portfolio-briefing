"""
Step 1.5: Load and evaluate directives

Reads active directives, evaluates triggers, transitions expired ones.
"""

import json
import yaml
from pathlib import Path
from datetime import datetime


def load_directives(snapshot_dir: Path) -> tuple:
    """
    Load active directives from state/directives/index.yaml.
    Evaluate triggers. Transition expired directives.

    Returns:
        (directives_active_list, directives_expired_list)
    """
    directives_dir = Path("state/directives")
    directives_dir.mkdir(parents=True, exist_ok=True)

    index_file = directives_dir / "index.yaml"

    directives_active = []
    directives_expired = []

    if not index_file.exists():
        print(f"  No directives index found at {index_file} (first run)")
        return directives_active, directives_expired

    with open(index_file) as f:
        index = yaml.safe_load(f) or {}

    # In v1, we load directives but don't evaluate triggers yet
    # (that's a v1.1 feature when we have live market data)
    directives = index.get("directives", [])
    for d in directives:
        if d.get("status") == "ACTIVE":
            directives_active.append(d)
        elif d.get("status") == "EXPIRED":
            directives_expired.append(d)

    print(f"  Loaded {len(directives_active)} active directives")
    print(f"  {len(directives_expired)} expired directives carried forward")

    # Write to snapshot
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with open(snapshot_dir / "directives_active.json", "w") as f:
        json.dump(directives_active, f, indent=2)
    with open(snapshot_dir / "directives_expired_today.json", "w") as f:
        json.dump(directives_expired, f, indent=2)

    return directives_active, directives_expired
