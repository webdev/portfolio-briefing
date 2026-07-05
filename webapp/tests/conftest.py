"""Pytest fixtures — point the webapp at our local fixtures dir, with a
fresh DuckDB file per test session.

Run: `uv run pytest -v`
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _point_at_fixtures() -> None:
    """Override env vars BEFORE importing app.main so config.py picks them up."""
    os.environ["PORTFOLIO_BRIEFING_DELIVERY_DIR"] = str(FIXTURES)
    os.environ["PORTFOLIO_BRIEFING_SNAPSHOTS_DIR"] = str(FIXTURES / "snapshots")

    # Per-session ephemeral DuckDB — use a path that DuckDB can create from scratch.
    tmp_dir = tempfile.mkdtemp(prefix="webapp-test-")
    db_path = Path(tmp_dir) / "briefings.duckdb"
    os.environ["PORTFOLIO_BRIEFING_DUCKDB"] = str(db_path)
    yield
    try:
        if db_path.exists():
            db_path.unlink()
        Path(tmp_dir).rmdir()
    except OSError:
        pass


@pytest.fixture(scope="session")
def app():
    # Import after env vars are set
    from app.main import create_app
    return create_app()


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)
