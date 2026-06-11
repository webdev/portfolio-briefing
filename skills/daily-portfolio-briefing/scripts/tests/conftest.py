"""Shared test fixtures.

Redirects the entry-pipeline verdict state file (12-entry-pipeline-spec §4/§7)
to a per-test temp path so test renders of the candidates report never write
to — or read flips from — the REAL ``state/scout_verdicts.yaml`` the daily
pipeline diffs against.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import verdict_state  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_verdict_state(tmp_path, monkeypatch):
    monkeypatch.setattr(verdict_state, "DEFAULT_STATE_PATH",
                        tmp_path / "scout_verdicts.yaml")
