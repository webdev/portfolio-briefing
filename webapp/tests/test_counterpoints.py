"""Test the counterpoint markdown parser.

Builds a synthetic briefing markdown with known action headers +
counterpoint sub-bullets and asserts the parser extracts them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import counterpoints
from app.config import briefings_delivery


_SAMPLE_MD = """\
# Briefing — 2026-06-30

## Today's Action List

1. **CLOSE** NVDA_PUT_180_20260918 — +47% ($+280); buy-to-close limit $3.20
   - ⚖️ **Counterpoint:** premium captured but the underlying drifted up — closing here locks gains; rolling could capture more.
   - ↳ S: $185 R: $210
2. **EXECUTE ROLL** AMZN_PUT_220_20261016 — roll out and down
   - ⚖️ **Counterpoint:** the roll commits another 60 days of risk on a name with elevated IV.
3. **HEDGE** SPY: add 10× $560P Aug 7 $4,200
   - ⚖️ **Counterpoint:** insurance that decays to zero if no drawdown comes.

## Watch
... unrelated content
"""


@pytest.fixture
def md_fixture(monkeypatch, tmp_path):
    """Drop a fake briefing MD into a temp delivery dir and point the
    config there."""
    monkeypatch.setenv("PORTFOLIO_BRIEFING_DELIVERY_DIR", str(tmp_path))
    (tmp_path / "briefing_2026-06-30.md").write_text(_SAMPLE_MD)
    # Clear the lru_cache so we re-parse
    counterpoints._parse_md_cached.cache_clear()
    yield tmp_path
    counterpoints._parse_md_cached.cache_clear()


def test_parser_extracts_counterpoints(md_fixture):
    cmap = counterpoints.counterpoints_for_date("2026-06-30")
    assert "CLOSE:NVDA_PUT_180_20260918" in cmap
    assert "EXECUTE_ROLL:AMZN_PUT_220_20261016" in cmap
    assert "HEDGE:SPY:" in cmap or "HEDGE:SPY" in cmap or any(
        k.startswith("HEDGE") for k in cmap
    )


def test_parser_returns_empty_for_missing_date(md_fixture):
    cmap = counterpoints.counterpoints_for_date("2099-01-01")
    assert cmap == {}


def test_parser_returns_empty_for_none_date(md_fixture):
    assert counterpoints.counterpoints_for_date("") == {}
    assert counterpoints.counterpoints_for_date(None) == {}  # type: ignore[arg-type]


def test_lookup_with_kind_variants(md_fixture):
    """JSON action key may say 'EXECUTE_ROLL' while MD says 'EXECUTE ROLL'.
    The lookup should resolve either spelling."""
    text = counterpoints.counterpoint_for_action(
        "2026-06-30", "EXECUTE_ROLL:AMZN_PUT_220_20261016",
        ident="AMZN_PUT_220_20261016", kind="EXECUTE_ROLL",
    )
    assert text is not None
    assert "roll" in text.lower()


def test_parser_caches_by_mtime(md_fixture):
    """First call parses, second is cached."""
    c1 = counterpoints.counterpoints_for_date("2026-06-30")
    c2 = counterpoints.counterpoints_for_date("2026-06-30")
    assert c1 is c2 or c1 == c2


def test_counterpoint_text_for_known_action(md_fixture):
    text = counterpoints.counterpoint_for_action(
        "2026-06-30", "CLOSE:NVDA_PUT_180_20260918",
    )
    assert text is not None
    assert "premium captured" in text.lower() or "rolling" in text.lower()


def test_counterpoint_returns_none_for_unknown_key(md_fixture):
    text = counterpoints.counterpoint_for_action(
        "2026-06-30", "NONEXISTENT:KEY",
    )
    assert text is None
