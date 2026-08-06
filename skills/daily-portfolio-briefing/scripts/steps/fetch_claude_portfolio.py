"""
Step 1.8: Fetch the Claude/Autopilot portfolio (task #45).

Invokes the claude-portfolio-fetcher skill (live page fetch → 24h cache →
manual seed, provenance-labeled) and persists the payload to
<snapshot_dir>/claude_portfolio.json. Second rec source, deliberately low
weight: downstream it only adds the `🤖 CP-held` chip and the playbook's
Parkev-agreement bonus — never a standalone qualification.

Fail-open: any failure returns an empty payload; the briefing ships.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from analysis.json_utils import json_default

# this file: .../skills/daily-portfolio-briefing/scripts/steps/fetch_claude_portfolio.py
_REPO_ROOT = Path(__file__).resolve().parents[4]
_CP_SKILL = _REPO_ROOT / "skills" / "claude-portfolio-fetcher"

_EMPTY = {"provenance": "unavailable", "holdings": [], "tickers": []}


def fetch_claude_portfolio_step(snapshot_dir: Path, config: dict | None) -> dict:
    """Returns the claude-portfolio payload dict ({} shape on failure)."""
    cp_cfg = (config or {}).get("claude_portfolio") or {}
    if not cp_cfg.get("enabled", True):
        return dict(_EMPTY)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    output_path = snapshot_dir / "claude_portfolio.json"
    cli = _CP_SKILL / "scripts" / "fetch_claude_portfolio.py"
    if not cli.exists():
        print("  claude-portfolio-fetcher not installed; skipping")
        return dict(_EMPTY)
    cmd = [sys.executable, str(cli),
           "--config", str(_CP_SKILL / "config" / "claude_portfolio_config.yaml"),
           "--output", str(output_path)]
    env = {**os.environ, "PYTHONPATH": str(_CP_SKILL / "scripts")}
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                timeout=60, check=False)
        if result.returncode != 0:
            print(f"  Claude-portfolio fetch failed (exit {result.returncode}); "
                  f"continuing without")
            return dict(_EMPTY)
    except subprocess.TimeoutExpired:
        print("  Claude-portfolio fetch timed out; continuing without")
        return dict(_EMPTY)
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return dict(_EMPTY)
        n = len(payload.get("holdings") or [])
        print(f"  Claude portfolio: {n} holding(s) · "
              f"provenance={payload.get('provenance')}")
        if payload.get("stale_warning"):
            print(f"  WARNING: {payload['stale_warning']}")
        return payload
    except (OSError, json.JSONDecodeError) as e:
        print(f"  Claude-portfolio output unreadable: {e}")
        try:
            with open(output_path, "w") as f:
                json.dump(_EMPTY, f, default=json_default)
        except OSError:
            pass
        return dict(_EMPTY)
