"""Path and config resolution for the webapp.

Resolves filesystem locations for:
  - The briefing delivery folder (~/Documents/briefings/)
  - The per-day snapshot inputs (state/briefing_snapshots/<DATE>/)
  - The local DuckDB file
  - The pipeline's `analysis/parkev_chip.py` + `position_tiers.py` so we
    can import them rather than duplicate the formatters (CLAUDE.md
    rule #27: chips must be byte-identical to the markdown's).

All paths are overridable via env vars so tests can point at fixtures.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from functools import lru_cache


# Resolve the repo root: webapp/ is a sibling of skills/.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Where the pipeline writes the daily briefing JSON+MD (Step 14 delivery).
DEFAULT_BRIEFINGS_DELIVERY = Path.home() / "Documents" / "briefings"

# Where the pipeline writes per-day snapshot inputs.
DEFAULT_SNAPSHOTS_ROOT = (
    REPO_ROOT / "skills" / "daily-portfolio-briefing" / "state" / "briefing_snapshots"
)

# Where the pipeline's analysis helpers live (we import format_parkev_chip,
# format_tier_badge etc. directly — never duplicate).
PIPELINE_SCRIPTS = (
    REPO_ROOT / "skills" / "daily-portfolio-briefing" / "scripts"
)

# Where the briefing config lives (for tier_a/b/c lists used by tier_for).
BRIEFING_CONFIG = (
    REPO_ROOT / "skills" / "daily-portfolio-briefing" / "config" / "briefing.yaml"
)

# Local DuckDB file (gitignored).
DUCKDB_PATH = Path(__file__).resolve().parent.parent / "data" / "briefings.duckdb"


def briefings_delivery() -> Path:
    """Directory containing briefing_<DATE>.{json,md}. Env override:
    PORTFOLIO_BRIEFING_DELIVERY_DIR."""
    return Path(os.environ.get(
        "PORTFOLIO_BRIEFING_DELIVERY_DIR",
        str(DEFAULT_BRIEFINGS_DELIVERY),
    )).expanduser()


def snapshots_root() -> Path:
    """Directory containing one subdir per snapshot date. Env override:
    PORTFOLIO_BRIEFING_SNAPSHOTS_DIR."""
    return Path(os.environ.get(
        "PORTFOLIO_BRIEFING_SNAPSHOTS_DIR",
        str(DEFAULT_SNAPSHOTS_ROOT),
    )).expanduser()


def duckdb_path() -> Path:
    """Local DuckDB file. Env override: PORTFOLIO_BRIEFING_DUCKDB."""
    return Path(os.environ.get(
        "PORTFOLIO_BRIEFING_DUCKDB",
        str(DUCKDB_PATH),
    )).expanduser()


# ─── Pipeline-formatter import (chips + tier badges) ──────────────────────


def _ensure_pipeline_on_path() -> None:
    """Insert the pipeline's scripts/ dir on sys.path so `from analysis...`
    imports succeed. Idempotent — safe to call repeatedly."""
    p = str(PIPELINE_SCRIPTS)
    if p not in sys.path:
        sys.path.insert(0, p)


@lru_cache(maxsize=1)
def briefing_config() -> dict:
    """Load briefing.yaml as a dict (cached). Returns {} on any failure
    — tier classification then falls back to Tier C for every ticker."""
    try:
        import yaml  # noqa: WPS433 (third-party in lru_cache)
    except ImportError:
        return {}
    path = BRIEFING_CONFIG
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}
