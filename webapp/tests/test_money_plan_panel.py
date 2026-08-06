"""💰 Money Plan panel on the briefing page (2026-08-04 fix 3).

The pipeline writes a `money_plan` JSON key (build_money_plan rollup with
pre-rendered "lines"); the webapp renders the same panel at the top of
/briefing/{date}. Contract pinned here:

  - the model accepts (and defaults) the new key — old briefings load fine;
  - the panel renders when lines are present, styled via inline_md (hard
    rule #30 — no raw `**` markdown literals in the UI);
  - no panel when the briefing predates the feature.
"""

from __future__ import annotations

from app.models import load_briefing


def _raw(money_plan=None):
    raw = {
        "date": "2026-08-04",
        "regime": "CAUTION",
        "nlv": 1_000_000,
        "cash": 50_000,
        "equity_reviews": [],
        "options_reviews": [],
        "new_ideas": [],
        "actions": [],
    }
    if money_plan is not None:
        raw["money_plan"] = money_plan
    return raw


def test_model_defaults_money_plan_to_empty_dict():
    """Old briefings (pre-feature) must load with money_plan == {}."""
    b = load_briefing(_raw())
    assert b.money_plan == {}


def test_model_accepts_money_plan():
    plan = {
        "total_realized": 2226.0,
        "lines": ["**Bank today:** 1 close(s) → $+2,226 realized "
                  "(VRT $280P via roll-down)"],
    }
    b = load_briefing(_raw(money_plan=plan))
    assert b.money_plan["total_realized"] == 2226.0
    assert b.money_plan["lines"]


def test_briefing_page_renders_money_plan(client, monkeypatch):
    """Panel renders at the top of /briefing/{date}; markdown bold is
    converted to HTML — never raw `**` literals (hard rule #30)."""
    from app import ingest

    real = ingest.load_briefing_json("2026-06-30")
    assert real, "fixture briefing_2026-06-30.json must exist"
    patched = dict(real)
    patched["money_plan"] = {
        "total_realized": 2226.0,
        "lines": [
            "**Bank today:** 1 close(s) → $+2,226 realized "
            "(VRT $280P via roll-down)",
            "**Deploy today:** 2 entries → +$1,238 premium (RDDT, NOW)",
        ],
    }
    monkeypatch.setattr(ingest, "load_briefing_json", lambda d: patched)
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    assert "Money Plan" in r.text
    assert "Bank today:" in r.text
    assert "**Bank today:**" not in r.text  # no raw markdown literals


def test_briefing_page_without_money_plan_has_no_panel(client):
    """Pre-feature briefings render without the panel (no empty shell)."""
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    assert 'id="money-plan"' not in r.text
