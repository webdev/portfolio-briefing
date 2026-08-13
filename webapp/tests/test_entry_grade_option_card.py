"""🎓 Entry-grade token on the webapp's structured option cards.

George (2026-08-13): "where in the briefing i can see the entries grades
for my current options" — the markdown Watch panel now carries a per-row
🎓 token, but the webapp's Options tab renders from the STRUCTURED
options_reviews JSON (not the md), which would silently drop it. The
pipeline attaches `entry_grade` (letter/score/entry_date/top_driver +
pre-humanized token) to each options review; the unified option card
renders the token (rules #19/#31 — explicit, humanized, never
fabricated).
"""

from __future__ import annotations

from app.models import load_briefing

_TOKEN = "🎓 entry D (30, RSI 60 off-band) · Aug 5"


def _option_review(entry_grade=None):
    r = {
        "underlying": "AMZN",
        "contract": "AMZN_PUT_245_20261016",
        "type": "PUT",
        "qty": -1.0,
        "strike": 245.0,
        "expiration": "2026-10-16",
        "current_mid": 4.45,
        "entry_price": 4.95,
        "days_to_expiry": 64,
        "recommendation": "HOLD",
        "rationale": "MODERATE OTM with low profit. Hold for more decay.",
    }
    if entry_grade is not None:
        r["entry_grade"] = entry_grade
    return r


def _entry_grade():
    return {"letter": "D", "score": 30.0, "entry_date": "2026-08-05",
            "top_driver": "RSI 60 off-band ✗", "token": _TOKEN}


def test_model_accepts_and_defaults_entry_grade():
    """Old briefings (pre-feature) must load with entry_grade == None;
    new ones carry the locked ledger payload through."""
    raw = {"date": "2026-08-13", "equity_reviews": [],
           "options_reviews": [_option_review()], "actions": []}
    b = load_briefing(raw)
    assert b.options_reviews[0].entry_grade is None

    raw["options_reviews"] = [_option_review(_entry_grade())]
    b = load_briefing(raw)
    assert b.options_reviews[0].entry_grade["letter"] == "D"
    assert b.options_reviews[0].entry_grade["token"] == _TOKEN


def test_option_card_renders_entry_grade_token(client, monkeypatch):
    """George: 'where in the briefing i can see the entries grades for my
    current options' — the Options tab card shows the pre-humanized
    token on the row (`🎓 entry D (30, RSI 60 off-band) · Aug 5`)."""
    from app import ingest

    real = ingest.load_briefing_json("2026-06-30")
    assert real, "fixture briefing_2026-06-30.json must exist"
    patched = dict(real)
    patched["options_reviews"] = [_option_review(_entry_grade())]
    monkeypatch.setattr(ingest, "load_briefing_json", lambda d: patched)
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    assert _TOKEN in r.text
    assert 'class="uc-entry-grade"' in r.text


def test_option_card_without_grade_renders_no_token(client, monkeypatch):
    """No ledger record → no token, no empty shell — never a fabricated
    grade (rule #19)."""
    from app import ingest

    real = ingest.load_briefing_json("2026-06-30")
    patched = dict(real)
    patched["options_reviews"] = [_option_review()]
    monkeypatch.setattr(ingest, "load_briefing_json", lambda d: patched)
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    assert "🎓 entry" not in r.text
    assert 'class="uc-entry-grade"' not in r.text
