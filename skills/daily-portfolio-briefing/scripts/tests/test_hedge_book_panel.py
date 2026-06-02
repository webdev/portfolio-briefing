"""Hedge-book panel rendering — must handle string OR date expirations without
crashing. E*TRADE returns expirations as ISO strings on some paths, so
``exp.strftime(...)`` would AttributeError — the recurring bug pattern."""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.hedge_book import HedgeBook, HedgeRecommendation  # noqa: E402
from render.hedge_book_panel import render_hedge_book, _fmt_exp  # noqa: E402


def test_fmt_exp_handles_date_str_and_none():
    assert _fmt_exp(date(2026, 7, 3)) == "Fri Jul 03 '26"
    assert _fmt_exp("2026-07-03") == "Fri Jul 03 '26"          # ISO str
    assert _fmt_exp("2026-07-03T00:00:00") == "Fri Jul 03 '26"  # datetime-ish str
    assert _fmt_exp("") == "?"
    assert _fmt_exp(None) == "?"
    # Unknown-shape input falls back to string repr, never crashes.
    assert _fmt_exp("garbage") == "garbage"


def test_render_hedge_book_with_str_expirations_does_not_crash():
    """The actual crash that blocked the briefing: hedges/recs with str expirations."""
    hedge = HedgeBook(
        current_coverage_pct=0.05,
        target_coverage_pct=0.10,
        current_hedges=[
            {"quantity": -1, "symbol": "SPY", "strike": 700.0,
             "position_type": "long_put", "expiration": "2026-07-03",
             "delta": -0.20},
        ],
        recommendations=[
            HedgeRecommendation(
                instrument="SPY_PUT",
                target_strike=Decimal("711"),
                target_expiration="2026-07-03",   # str, not date
                target_delta=-0.20,
                contracts=13,
                estimated_cost=Decimal("9740"),
                cost_pct_nlv=0.0086,
                coverage_pct=0.10,
                rationale="SPY 5% OTM put at ~0.20 delta, ~35 DTE",
            ),
        ],
    )
    md = "\n".join(render_hedge_book(hedge, Decimal("1000000"), Decimal("750")))
    # The dates render through _fmt_exp; no exception bubbles up.
    assert "Fri Jul 03 '26" in md
    assert "$711P" in md
    assert "**13x**" in md


def test_render_hedge_book_with_date_expirations_still_works():
    """Regression guard: date-object path (the original happy case)."""
    hedge = HedgeBook(
        current_coverage_pct=0.10,
        target_coverage_pct=0.10,
        current_hedges=[
            {"quantity": -1, "symbol": "SPY", "strike": 700.0,
             "position_type": "long_put", "expiration": date(2026, 7, 3),
             "delta": -0.20},
        ],
        recommendations=[],
    )
    md = "\n".join(render_hedge_book(hedge, Decimal("1000000")))
    assert "Fri Jul 03 '26" in md
    assert "Active hedges:" in md
