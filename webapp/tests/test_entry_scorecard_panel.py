"""🎓 Entry Scorecard card on the briefing page (George 2026-08-12).

George: "can we incorporate these learnings in the daily briefing? i want
to make sure we have a running score of our entries."

The pipeline writes an `entry_scorecard` JSON key
(entry_ledger.compute_scorecard: running averages / distribution / trend /
last_5 / callouts / capture-by-grade); the webapp renders a small card
under the Money Plan on /briefing/{date}. Contract pinned here:

  - the model accepts (and defaults) the new key — old briefings load fine;
  - the card renders when the scorecard has graded entries; grades are
    human-readable (no machine identifiers — hard rule #31), no raw
    markdown literals (hard rule #30);
  - no card when the briefing predates the feature (no empty shell).
"""

from __future__ import annotations

from app.models import load_briefing


def _raw(entry_scorecard=None):
    raw = {
        "date": "2026-08-12",
        "regime": "CAUTION",
        "nlv": 1_000_000,
        "cash": 50_000,
        "equity_reviews": [],
        "options_reviews": [],
        "new_ideas": [],
        "actions": [],
    }
    if entry_scorecard is not None:
        raw["entry_scorecard"] = entry_scorecard
    return raw


def _scorecard():
    return {
        "as_of": "2026-08-12",
        "total": 18, "graded": 18, "open": 16, "closed": 2,
        "averages": {
            "last_10": {"avg": 42.0, "letter": "D", "n": 10},
            "last_30d": {"avg": 41.0, "letter": "D", "n": 12},
            "all_time": {"avg": 37.0, "letter": "D", "n": 18},
        },
        "distribution": {"C": 3, "D": 15},
        "trend": {"this_week": 44.0, "prior_week": 38.0,
                  "n_this": 2, "n_prior": 3, "arrow": "↗"},
        "last_5": [{"label": "MU $950P", "entry_date": "2026-08-08",
                    "letter": "D", "score": 31.0,
                    "top_driver": "RVr 34 thin ✗", "roll": False}],
        "callouts": ["6 of last 10 put entries had IV/RV rank < 40 — "
                     "thin premium (selling cheap)"],
        "outcome": {"n": 2, "min_sample": 3, "by_bucket": None},
        "digest_line": "🎓 Entry quality: last 10 avg D (42) ↗ · "
                       "last entry MU $950P — D (RVr 34 thin ✗)",
    }


def test_model_defaults_entry_scorecard_to_empty_dict():
    """Old briefings (pre-feature) must load with entry_scorecard == {}."""
    b = load_briefing(_raw())
    assert b.entry_scorecard == {}


def test_model_accepts_entry_scorecard():
    b = load_briefing(_raw(entry_scorecard=_scorecard()))
    assert b.entry_scorecard["graded"] == 18
    assert b.entry_scorecard["averages"]["last_10"]["letter"] == "D"


def test_briefing_page_renders_entry_scorecard(client, monkeypatch):
    """Card renders on /briefing/{date} with averages, last entry,
    callout, and the small-sample outcome note — no raw `**` literals."""
    from app import ingest

    real = ingest.load_briefing_json("2026-06-30")
    assert real, "fixture briefing_2026-06-30.json must exist"
    patched = dict(real)
    patched["entry_scorecard"] = _scorecard()
    monkeypatch.setattr(ingest, "load_briefing_json", lambda d: patched)
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    assert "Entry Scorecard" in r.text
    assert "grades locked to entry-day conditions" in r.text
    assert "MU $950P" in r.text
    assert "thin premium (selling cheap)" in r.text
    assert "Outcome sample too small (n=2)" in r.text
    assert "**" not in r.text.split('id="entry-scorecard"')[1][:2000]


def test_briefing_page_without_scorecard_has_no_card(client):
    """Pre-feature briefings render without the card (no empty shell)."""
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    assert 'id="entry-scorecard"' not in r.text
