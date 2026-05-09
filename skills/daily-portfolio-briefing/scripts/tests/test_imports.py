"""Verify all modified modules import correctly."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

def test_imports():
    """All critical modules should import without errors."""
    # Test imports of modified modules
    from render.panels import render_health
    from render.stress_test_panel import render_stress_test, render_stress_test_details
    from steps.aggregate import aggregate_briefing

    assert callable(render_health)
    assert callable(render_stress_test)
    assert callable(render_stress_test_details)
    assert callable(aggregate_briefing)

    print("✓ All imports successful")

if __name__ == "__main__":
    test_imports()
