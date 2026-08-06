"""💪 Program Edge card — real portfolio vs no-options ghost on the Home page.

Feature (2026-08-06): the ghost-portfolio "program edge" (cumulative $ the
options program has netted vs a ghost that closed every option at inception)
is surfaced daily at the top of the dashboard front page:

  - headline cumulative gap + 1-day delta + month-to-date
  - gap sparkline via the existing Plotly chart bootstrapper
  - real-vs-ghost table (7d / 30d / inception)
  - links to the Benchmark tab of the latest briefing
  - fail-open: missing/short/corrupt ghost file renders
    "building history (N days)" — never a 500 (house rule: state
    corruption recovers on the next request).
"""

from __future__ import annotations

import json
from pathlib import Path

from app import program_edge as pe


FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ─── Dashboard rendering (fixture ghost file, wired in conftest) ──────────


def test_dashboard_renders_program_edge_card(client):
    """GET / renders the 💪 Program Edge card with the fixture's numbers:
    total gap +$11,500 since May 9, +$1,200 today, +$11,500 MTD."""
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "💪 Program Edge" in body
    assert "+$11,500" in body                      # headline cumulative gap
    assert "since May 9" in body                   # inception label
    assert "+$1,200 today" in body                 # 1-day delta
    assert "month-to-date" in body
    # real-vs-ghost window table
    assert "7 days" in body
    assert "30 days" in body
    assert "Inception (May 9)" in body
    assert "Ghost" in body


def test_card_links_to_benchmark_tab(client):
    """The card links to the Benchmark tab of the latest briefing
    (briefing.html's hash-activated sl-tab mapping handles #benchmark)."""
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/briefing/2026-06-30#benchmark"' in r.text


def test_card_uses_existing_chart_bootstrapper(client):
    """Template asset contract (house rule #33): the sparkline uses the
    existing .chart-target[data-chart-url] pattern that /static/app.js
    boots — no new <script> tag is introduced, and app.js is loaded."""
    r = client.get("/")
    assert r.status_code == 200
    assert 'data-chart-url="/charts/program-edge.json"' in r.text
    # the div must carry the bootstrapper's hook class
    assert "chart-target pe-spark" in r.text
    # and the bootstrapper script itself is on the page (via base.html)
    assert "/static/app.js" in r.text


# ─── Fail-open: missing / corrupt / short ghost state ─────────────────────


def test_missing_ghost_file_never_500(client, monkeypatch):
    """Ghost file absent → dashboard still 200s and the card shows
    'building history (0 days)' instead of numbers it doesn't have."""
    monkeypatch.setenv("PORTFOLIO_BRIEFING_GHOST_FILE", "/nonexistent/ghost.json")
    r = client.get("/")
    assert r.status_code == 200
    assert "building history (0 days)" in r.text


def test_corrupt_ghost_file_never_500(client, monkeypatch, tmp_path):
    """Corrupt JSON in the ghost file must not 500 the front page —
    state corruption recovers transparently (house rule)."""
    bad = tmp_path / "ghost_portfolio.json"
    bad.write_text("{{{ not json", encoding="utf-8")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_GHOST_FILE", str(bad))
    r = client.get("/")
    assert r.status_code == 200
    assert "building history (0 days)" in r.text


def test_short_series_shows_building(client, monkeypatch, tmp_path):
    """Fewer than MIN_DAYS points → 'building history (N days)' with the
    real count, no fabricated headline numbers."""
    short = {
        "status": "ok",
        "series": {
            "2026-08-01": {"ghost_nav": 1.0, "real_nav": 2.0, "gap": 1.0, "gap_delta_1d": None},
            "2026-08-02": {"ghost_nav": 1.0, "real_nav": 3.0, "gap": 2.0, "gap_delta_1d": 1.0},
            "2026-08-03": {"ghost_nav": 1.0, "real_nav": 4.0, "gap": 3.0, "gap_delta_1d": 1.0},
        },
    }
    f = tmp_path / "ghost_portfolio.json"
    f.write_text(json.dumps(short), encoding="utf-8")
    monkeypatch.setenv("PORTFOLIO_BRIEFING_GHOST_FILE", str(f))
    r = client.get("/")
    assert r.status_code == 200
    assert "building history (3 days)" in r.text
    assert "+$" not in r.text.split("Program Edge")[1].split("</section>")[0]


# ─── Chart endpoint ───────────────────────────────────────────────────────


def test_chart_endpoint_returns_plotly_json(client):
    r = client.get("/charts/program-edge.json")
    assert r.status_code == 200
    spec = r.json()
    assert "data" in spec and "layout" in spec
    trace = spec["data"][0]
    assert trace["y"][-1] == 11500.0          # last gap point from fixture
    assert trace["x"][0] == "2026-05-09"      # inception


def test_chart_endpoint_missing_file_placeholder(client, monkeypatch):
    """Missing ghost file → empty-figure placeholder, never 500."""
    monkeypatch.setenv("PORTFOLIO_BRIEFING_GHOST_FILE", "/nonexistent/ghost.json")
    r = client.get("/charts/program-edge.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["data"] == []
    assert "building" in spec["layout"]["annotations"][0]["text"]


# ─── Pure card math ───────────────────────────────────────────────────────


def _fixture_state() -> dict:
    return json.loads((FIXTURES / "ghost_portfolio.json").read_text())


def test_build_card_window_math():
    """7d baseline = last point ≤ (as_of − 7d) = 2026-05-15 → edge
    11,500 − 6,200 = +$5,300 (matches summary.week_gap); 30d and
    inception both fall back to the first point → +$11,500."""
    card = pe.build_card(_fixture_state())
    assert card["status"] == "ok"
    assert card["days"] == 11
    assert card["headline"] == "+$11,500"
    assert card["delta_1d"] == "+$1,200"
    assert card["mtd"] == "+$11,500"
    by_label = {w["label"]: w for w in card["windows"]}
    assert by_label["7 days"]["edge"] == "+$5,300"
    assert by_label["7 days"]["real"] == "+$10,800"   # 1,018,500 − 1,007,700
    assert by_label["7 days"]["ghost"] == "+$5,500"   # 1,007,000 − 1,001,500
    assert by_label["30 days"]["edge"] == "+$11,500"
    assert by_label["Inception (May 9)"]["edge"] == "+$11,500"


def test_build_card_handles_none_and_negative():
    assert pe.build_card(None) == {"status": "building", "days": 0}
    assert pe._fmt_usd(-1204.4) == "-$1,204"
    assert pe._fmt_usd(None) == "n/a"
