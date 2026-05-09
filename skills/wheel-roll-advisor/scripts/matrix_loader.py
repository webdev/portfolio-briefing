"""Load and resolve parameterized YAML files for wheel-roll-advisor."""

import json
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, Set, Optional


@dataclass
class LoadedMatrix:
    """Container for loaded matrix, parameters, and tail-risk names."""
    matrix: list
    parameters: Dict[str, Any]
    tail_risk_names: Dict[str, Set[str]]


def _resolve_placeholders(obj: Any, params: Dict[str, Any]) -> Any:
    """Recursively resolve ${param_name} placeholders in YAML structure."""
    if isinstance(obj, dict):
        return {k: _resolve_placeholders(v, params) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_resolve_placeholders(item, params) for item in obj]
    elif isinstance(obj, str) and obj.startswith("${") and obj.endswith("}"):
        param_name = obj[2:-1]
        return params.get(param_name, obj)
    return obj


def load_parameters(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load wheel_parameters.yaml and return dict of all parameters."""
    if path is None:
        path = Path(__file__).parent.parent / "references" / "wheel_parameters.yaml"
    
    import yaml
    with open(path) as f:
        content = yaml.safe_load(f) or {}
    
    # Flatten nested sections into single dict
    params = {}
    for section in content.values():
        if isinstance(section, dict):
            params.update(section)
    
    return params


def load_tail_risk_names(path: Optional[Path] = None) -> Dict[str, Set[str]]:
    """Load tail_risk_names.yaml and return dict of frozensets by category."""
    if path is None:
        path = Path(__file__).parent.parent / "references" / "tail_risk_names.yaml"
    
    import yaml
    with open(path) as f:
        content = yaml.safe_load(f) or {}
    
    # Convert lists to sets
    result = {}
    for key, value in content.items():
        if isinstance(value, list):
            result[key] = set(value)
    
    return result


def load_matrix(path: Optional[Path] = None, params: Optional[Dict[str, Any]] = None) -> list:
    """Load decision_matrix.yaml, resolve placeholders, return matrix rows."""
    if path is None:
        path = Path(__file__).parent.parent / "references" / "decision_matrix.yaml"
    if params is None:
        params = load_parameters()
    
    import yaml
    with open(path) as f:
        content = yaml.safe_load(f) or {}
    
    # Extract matrix rows (typically under "cells" or top-level list)
    if isinstance(content, dict):
        matrix = content.get("cells", []) or content.get("short_put", [])
    else:
        matrix = content or []
    
    # Resolve placeholders in all rows
    return [_resolve_placeholders(row, params) for row in matrix]


def load_all(
    matrix_path: Optional[Path] = None,
    params_path: Optional[Path] = None,
    tail_risk_path: Optional[Path] = None,
) -> LoadedMatrix:
    """Convenience wrapper: load all three files."""
    params = load_parameters(params_path)
    matrix = load_matrix(matrix_path, params)
    tail_risk = load_tail_risk_names(tail_risk_path)
    
    return LoadedMatrix(matrix=matrix, parameters=params, tail_risk_names=tail_risk)
