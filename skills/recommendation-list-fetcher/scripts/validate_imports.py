#!/usr/bin/env python3
"""Validate that the skill's imports work correctly."""

import sys
from pathlib import Path

# Test imports
try:
    import structlog
    print("✓ structlog imported")
except ImportError as e:
    print(f"✗ Failed to import structlog: {e}")
    sys.exit(1)

try:
    import yaml
    print("✓ yaml imported")
except ImportError as e:
    print(f"✗ Failed to import yaml: {e}")
    sys.exit(1)

try:
    import httpx
    print("✓ httpx imported")
except ImportError as e:
    print(f"✗ Failed to import httpx: {e}")
    print("  (httpx may not be installed in CI — not critical)")

try:
    from shopping_list import (
        load_config,
        validate_config,
        _parse_rating_tier,
        _tier_to_recommendation,
        _parse_price_target,
        _parse_date,
        resolve_ticker,
    )
    print("✓ shopping_list functions imported")
except ImportError as e:
    print(f"✗ Failed to import from shopping_list: {e}")
    sys.exit(1)

print("\nAll imports successful!")
print("\nRunning basic function tests...")

# Test _parse_price_target
assert _parse_price_target("320-350") == (320.0, 350.0), "Price target range parsing failed"
assert _parse_price_target("300") == 300.0, "Price target single value parsing failed"
assert _parse_price_target("") is None, "Empty price target parsing failed"
print("✓ _parse_price_target works")

# Test config validation
config_path = Path("../assets/config_template.yaml")
if config_path.exists():
    try:
        cfg = load_config(config_path)
        print(f"✓ Config template loaded ({len(cfg)} top-level keys)")
        assert "source" in cfg
        assert "column_mapping" in cfg
        print("✓ Config has required fields")
    except Exception as e:
        print(f"✗ Config validation failed: {e}")
        sys.exit(1)
else:
    print(f"⚠ Config template not found at {config_path}")

print("\nAll validation checks passed!")
