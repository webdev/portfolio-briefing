"""
Step 1: Pre-flight check

Reads config, checks auth, loads yesterday's briefing.
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
import yaml


def run_preflight(config_path: str, etrade_fixture: str = None) -> tuple:
    """
    Pre-flight check.

    Returns:
        (config_dict, yesterday_briefing_path_or_none)
    """
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    print(f"  Config loaded from {config_path}")

    # For now, check if fixture is provided for mock mode
    if etrade_fixture:
        fixture_path = Path(etrade_fixture)
        if not fixture_path.exists():
            raise FileNotFoundError(f"E*TRADE fixture not found: {etrade_fixture}")
        print(f"  Using mock E*TRADE fixture: {etrade_fixture}")
    else:
        print(f"  E*TRADE MCP mode (not yet fully integrated in v1)")

    # Try to load yesterday's briefing
    today = datetime.now()
    yesterday = today - timedelta(days=1)
    yesterday_date_str = yesterday.strftime("%Y-%m-%d")

    reports_dir = Path("reports/daily")
    reports_dir.mkdir(parents=True, exist_ok=True)

    yesterday_briefing = None
    for pattern in [
        reports_dir / f"briefing_{yesterday_date_str}.json",
        reports_dir / f"briefing_{yesterday_date_str}.DRAFT.json",
    ]:
        if pattern.exists():
            yesterday_briefing = pattern
            print(f"  Yesterday's briefing found: {pattern}")
            break

    if not yesterday_briefing:
        print(f"  No yesterday's briefing found (first run or weekend)")

    return config, yesterday_briefing
