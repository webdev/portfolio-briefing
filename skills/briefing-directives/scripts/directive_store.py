"""Briefing Directives — CRUD and state management.

Provides atomic read/write operations for directive YAML files and index.yaml.
All writes use tempfile + os.replace for safety.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# -- Constants ----------------------------------------------------------------

_STATUS_ORDER = ["ACTIVE", "EXPIRED", "OVERRIDDEN", "RESOLVED"]
_TERMINAL_STATUSES = {"EXPIRED", "OVERRIDDEN", "RESOLVED"}

_VALID_DIRECTIVE_TYPES = {"DEFER", "MANUAL", "OVERRIDE", "WATCH_ONLY", "SUPPRESS"}

_VALID_TRIGGERS = {
    "time_elapsed",
    "earnings_passed",
    "position_closed",
    "price_above",
    "price_below",
    "screener_drops",
    "manual_override",
    "open_ended",
}

_TARGET_KINDS = {
    "option_position",
    "position_scope",
    "new_idea",
    "symbol",
}

INDEX_FILE = "index.yaml"


def _ensure_dirs(state_dir: str | Path) -> Path:
    """Create state_dir and all subdirs if they don't exist."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ["active", "expired", "overridden", "resolved"]:
        (state_dir / subdir).mkdir(parents=True, exist_ok=True)
    return state_dir


def _short_hash(s: str, length: int = 4) -> str:
    """Generate short hash suffix for directive IDs."""
    h = hashlib.md5(s.encode()).hexdigest()
    return h[:length]


def _generate_id(target: dict, directive_type: str) -> str:
    """Generate directive_id: dir_YYYYMMDD_<target_slug>_<short_hash>."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")

    # Build target slug
    kind = target.get("kind", "unknown")
    if kind == "option_position":
        slug = target.get("identifier", "unknown").replace(" ", "_")[:20]
    elif kind == "position_scope":
        symbol = target.get("symbol", "unknown")
        pos_type = target.get("position_type", "any")
        slug = f"{symbol}_{pos_type}"
    elif kind == "new_idea":
        slug = target.get("symbol", "unknown")
    elif kind == "symbol":
        slug = target.get("symbol", "unknown")
    else:
        slug = "unknown"

    # Shorten slug
    slug = slug[:20].lower()

    # Hash on entire target + type for uniqueness
    target_hash = _short_hash(json.dumps(target, sort_keys=True) + directive_type)

    return f"dir_{date_str}_{slug}_{target_hash}"


def _validate_directive(directive: dict) -> None:
    """Validate directive structure before save."""
    required_top = {"type", "target", "reason", "expires"}
    if not required_top.issubset(directive.keys()):
        raise ValueError(
            f"Directive missing required fields. Need: {required_top}, got: {set(directive.keys())}"
        )

    directive_type = directive.get("type")
    if directive_type not in _VALID_DIRECTIVE_TYPES:
        raise ValueError(f"Invalid type: {directive_type}. Must be one of {_VALID_DIRECTIVE_TYPES}")

    target = directive.get("target", {})
    if not isinstance(target, dict):
        raise ValueError("target must be a dict")

    kind = target.get("kind")
    if kind not in _TARGET_KINDS:
        raise ValueError(
            f"Invalid target.kind: {kind}. Must be one of {_TARGET_KINDS}"
        )

    # Validate kind-specific required fields
    if kind == "option_position":
        if "identifier" not in target:
            raise ValueError("option_position target must have 'identifier'")
    elif kind == "position_scope":
        if "symbol" not in target:
            raise ValueError("position_scope target must have 'symbol'")
    elif kind == "new_idea":
        if "symbol" not in target:
            raise ValueError("new_idea target must have 'symbol'")
        if "source_screener" not in target:
            raise ValueError("new_idea target must have 'source_screener'")
    elif kind == "symbol":
        if "symbol" not in target:
            raise ValueError("symbol target must have 'symbol'")

    expires = directive.get("expires", {})
    if not isinstance(expires, dict):
        raise ValueError("expires must be a dict")

    trigger = expires.get("trigger")
    if trigger not in _VALID_TRIGGERS:
        raise ValueError(
            f"Invalid trigger: {trigger}. Must be one of {_VALID_TRIGGERS}"
        )

    # OVERRIDE requires parameter and new_value
    if directive_type == "OVERRIDE":
        if "parameter" not in directive or "new_value" not in directive:
            raise ValueError("OVERRIDE directive must have 'parameter' and 'new_value'")


def _load_index(state_dir: Path) -> dict:
    """Load index.yaml. Returns empty dict if not found."""
    index_path = state_dir / INDEX_FILE
    if not index_path.exists():
        return {}
    with open(index_path) as f:
        return yaml.safe_load(f) or {}


def _save_index(state_dir: Path, index: dict) -> None:
    """Save index.yaml atomically."""
    index_path = state_dir / INDEX_FILE
    with tempfile.NamedTemporaryFile(mode="w", dir=state_dir, delete=False, suffix=".yaml") as tmp:
        yaml.dump(index, tmp, default_flow_style=False)
        tmp_path = tmp.name
    os.replace(tmp_path, index_path)
    logger.debug(f"Saved index: {index_path}")


def _load_directive(directive_path: Path) -> dict:
    """Load a single directive YAML file."""
    with open(directive_path) as f:
        return yaml.safe_load(f)


def _save_directive(directive_path: Path, directive: dict) -> None:
    """Save a directive YAML file atomically."""
    _validate_directive(directive)
    directive_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=directive_path.parent, delete=False, suffix=".yaml"
    ) as tmp:
        yaml.dump(directive, tmp, default_flow_style=False, sort_keys=False)
        tmp_path = tmp.name
    os.replace(tmp_path, directive_path)
    logger.debug(f"Saved directive: {directive_path}")


# -- Public API ---------------------------------------------------------------


def create(state_dir: str | Path, directive: dict) -> dict:
    """Create a new ACTIVE directive.

    Args:
        state_dir: Path to state/directives/
        directive: Dict with type, target, reason, expires, and optional old_value (for OVERRIDE)

    Returns:
        The saved directive dict (with directive_id and created_at added)

    Raises:
        ValueError: If directive is invalid
    """
    state_dir = _ensure_dirs(state_dir)

    # Validate
    _validate_directive(directive)

    # Generate ID and timestamps
    directive_id = _generate_id(directive["target"], directive["type"])
    now_iso = datetime.now(timezone.utc).isoformat()

    # Add system fields
    directive["directive_id"] = directive_id
    directive["created_at"] = now_iso
    directive["status"] = "ACTIVE"
    directive["status_history"] = [
        {
            "at": now_iso,
            "status": "ACTIVE",
            "reason": "created",
        }
    ]
    directive["created_via"] = directive.get("created_via", "programmatic")

    # Save to active/
    directive_path = state_dir / "active" / f"{directive_id}.yaml"
    _save_directive(directive_path, directive)

    # Update index
    index = _load_index(state_dir)
    index[directive_id] = {
        "file": f"active/{directive_id}.yaml",
        "status": "ACTIVE",
        "type": directive["type"],
        "target_kind": directive["target"]["kind"],
        "target_summary": _target_summary(directive["target"]),
        "created_at": now_iso,
    }
    _save_index(state_dir, index)

    logger.info(f"Created directive: {directive_id} ({directive['type']})")
    return directive


def transition(
    state_dir: str | Path,
    directive_id: str,
    new_status: str,
    reason: str,
) -> dict:
    """Transition a directive to a new status.

    Valid transitions:
    - ACTIVE → EXPIRED, OVERRIDDEN, RESOLVED
    - Any non-terminal → (terminal is idempotent)

    Args:
        state_dir: Path to state/directives/
        directive_id: ID of directive to transition
        new_status: Target status
        reason: Reason for transition

    Returns:
        The updated directive

    Raises:
        ValueError: If transition is invalid
    """
    if new_status not in _STATUS_ORDER:
        raise ValueError(f"Invalid status: {new_status}")

    state_dir = _ensure_dirs(state_dir)
    index = _load_index(state_dir)

    if directive_id not in index:
        raise ValueError(f"Directive not found: {directive_id}")

    entry = index[directive_id]
    current_status = entry["status"]

    # Only terminal → terminal is allowed (idempotent for already-expired, etc.)
    if current_status in _TERMINAL_STATUSES and new_status not in _TERMINAL_STATUSES:
        raise ValueError(
            f"Cannot transition {current_status} → {new_status}. "
            f"Terminal statuses are final."
        )

    # Load directive from old location
    old_path = Path(state_dir) / entry["file"]
    directive = _load_directive(old_path)

    # Update status and history
    now_iso = datetime.now(timezone.utc).isoformat()
    directive["status"] = new_status
    directive["status_history"].append(
        {
            "at": now_iso,
            "status": new_status,
            "reason": reason,
        }
    )

    # Move file to new subdir
    new_subdir = new_status.lower()
    new_path = Path(state_dir) / new_subdir / f"{directive_id}.yaml"
    _save_directive(new_path, directive)

    # Delete old file
    old_path.unlink()

    # Update index
    index[directive_id]["file"] = f"{new_subdir}/{directive_id}.yaml"
    index[directive_id]["status"] = new_status
    _save_index(state_dir, index)

    logger.info(f"Transitioned {directive_id}: {current_status} → {new_status}")
    return directive


def list(state_dir: str | Path, status: str | None = None) -> list[dict]:
    """List directives, optionally filtered by status.

    Args:
        state_dir: Path to state/directives/
        status: Filter by status (None = all)

    Returns:
        List of directive dicts
    """
    state_dir = Path(state_dir)
    index = _load_index(state_dir)

    directives = []
    for directive_id, entry in index.items():
        if status is not None and entry["status"] != status:
            continue

        directive_path = state_dir / entry["file"]
        if directive_path.exists():
            directive = _load_directive(directive_path)
            directives.append(directive)

    return directives


def get(state_dir: str | Path, directive_id: str) -> dict | None:
    """Load a single directive by ID.

    Args:
        state_dir: Path to state/directives/
        directive_id: Directive ID

    Returns:
        Directive dict, or None if not found
    """
    state_dir = Path(state_dir)
    index = _load_index(state_dir)

    if directive_id not in index:
        return None

    entry = index[directive_id]
    directive_path = state_dir / entry["file"]

    if directive_path.exists():
        return _load_directive(directive_path)

    return None


def find_matching(state_dir: str | Path, target: dict) -> list[dict]:
    """Find all ACTIVE directives matching a target.

    A match is either:
    - Exact match on kind + all target fields
    - Broader match where directive target is a superset scope
      (e.g., a directive on all MSFT positions matches a specific MSFT short call)

    Args:
        state_dir: Path to state/directives/
        target: Target dict with kind + fields

    Returns:
        List of matching ACTIVE directives
    """
    state_dir = Path(state_dir)
    active_directives = list(state_dir, status="ACTIVE")

    matches = []
    for directive in active_directives:
        if _targets_match(directive["target"], target):
            matches.append(directive)

    return matches


def _targets_match(directive_target: dict, candidate_target: dict) -> bool:
    """Check if directive_target matches/applies to candidate_target.

    Rules:
    - If directive_target.kind == candidate_target.kind:
        - Must match all key fields (symbol, identifier, position_type, source_screener)
    - If directive has broader scope (e.g., symbol-level SUPPRESS):
        - Matches any candidate with that symbol
    """
    directive_kind = directive_target.get("kind")
    candidate_kind = candidate_target.get("kind")

    # Exact match on kind
    if directive_kind == candidate_kind:
        # Compare all fields in directive_target (excluding kind)
        for key, value in directive_target.items():
            if key == "kind":
                continue
            if candidate_target.get(key) != value:
                return False
        return True

    # Broader match: directive on symbol-level can match option_position
    if directive_kind == "symbol" and candidate_kind == "option_position":
        return directive_target.get("symbol") == candidate_target.get("symbol")

    # Broader match: directive on symbol-level can match new_idea
    if directive_kind == "symbol" and candidate_kind == "new_idea":
        return directive_target.get("symbol") == candidate_target.get("symbol")

    # Broader match: directive on position_scope can match specific option_position
    if directive_kind == "position_scope" and candidate_kind == "option_position":
        # Must match symbol; position_type is optional but if present must match
        if directive_target.get("symbol") != candidate_target.get("symbol"):
            return False
        directive_pos_type = directive_target.get("position_type")
        if directive_pos_type is not None:
            candidate_pos_type = candidate_target.get("position_type")
            if candidate_pos_type != directive_pos_type:
                return False
        return True

    return False


def _target_summary(target: dict) -> str:
    """Generate a human-readable summary of a target for the index."""
    kind = target.get("kind")
    if kind == "option_position":
        return target.get("identifier", "unknown")
    elif kind == "position_scope":
        symbol = target.get("symbol", "?")
        pos_type = target.get("position_type", "any")
        return f"{symbol} {pos_type}"
    elif kind == "new_idea":
        symbol = target.get("symbol", "?")
        screener = target.get("source_screener", "?")
        return f"{symbol} ({screener})"
    elif kind == "symbol":
        return target.get("symbol", "?")
    return "unknown"
