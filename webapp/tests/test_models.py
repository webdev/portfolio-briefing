"""Test V1 → V2 forward coercion of briefing JSON via Pydantic."""

from __future__ import annotations

import json
from pathlib import Path

from app.models import load_briefing


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_v1_briefing_coerces_to_v2():
    """V1 (no actions array) should validate as Briefing with actions=[]."""
    raw = _load_fixture("briefing_2026-05-10.json")
    assert "actions" not in raw, "V1 fixture should not have actions"
    briefing = load_briefing(raw)
    assert briefing.date.isoformat() == "2026-05-10"
    assert briefing.actions == []
    assert briefing.actionable_count() == 0
    assert briefing.nlv == 950000.00
    assert len(briefing.equity_reviews) == 2


def test_v2_briefing_has_actions():
    raw = _load_fixture("briefing_2026-06-30.json")
    briefing = load_briefing(raw)
    assert len(briefing.actions) == 2
    assert briefing.actions[0].kind == "CLOSE"
    assert briefing.actions[1].kind == "HEDGE"
    assert briefing.actionable_count() == 2


def test_cash_pct_derives_correctly():
    raw = _load_fixture("briefing_2026-06-30.json")
    briefing = load_briefing(raw)
    assert abs(briefing.cash_pct - 0.05) < 1e-6   # 50K / 1M = 5%


def test_top_holdings_sorted():
    raw = _load_fixture("briefing_2026-06-30.json")
    briefing = load_briefing(raw)
    top = briefing.top_holdings(5)
    assert top[0].ticker == "NVDA"
    assert top[1].ticker == "AMZN"


def test_missing_optional_fields_default_safely():
    """Bare-minimum briefing should still validate."""
    minimal = {"date": "2026-01-01"}
    briefing = load_briefing(minimal)
    assert briefing.equity_reviews == []
    assert briefing.options_reviews == []
    assert briefing.actions == []
    assert briefing.nlv is None
    assert briefing.cash_pct is None
