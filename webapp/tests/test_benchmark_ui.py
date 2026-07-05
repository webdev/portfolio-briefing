"""Task #16 — 📊 Benchmark tab UI tests.

Pins the hard requirements:
  - /briefing/{date} renders the Benchmark tab (route smoke, 200)
  - a briefing WITHOUT benchmark data does not 500 — it renders the
    "insufficient / no data" placeholder instead of hiding or crashing
  - a briefing WITH benchmark_report renders the alpha table + attribution
  - /charts/benchmark.json never 500s (empty figure on missing data)
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _benchmark_report() -> dict:
    """A realistic benchmark_report payload (shape = pipeline to_dict())."""
    return {
        "benchmark": {
            "status": "ok",
            "as_of": "2026-06-30",
            "benchmark_ticker": "SPY",
            "current_nlv": 1_000_000.0,
            "snapshot_count": 20,
            "inception_date": "2026-05-11",
            "windows": [
                {"name": "30d", "target_start": "2026-05-31",
                 "snapshot_start": "2026-06-01", "nlv_start": 980_000.0,
                 "portfolio_return_pct": 2.041, "spy_return_pct": 1.4,
                 "alpha_pct": 0.641, "note": ""},
                {"name": "90d", "target_start": "2026-04-01",
                 "snapshot_start": None, "nlv_start": None,
                 "portfolio_return_pct": None, "spy_return_pct": None,
                 "alpha_pct": None, "note": "history starts 2026-05-11"},
                {"name": "inception", "target_start": "2026-05-11",
                 "snapshot_start": "2026-05-11", "nlv_start": 1_050_000.0,
                 "portfolio_return_pct": -4.762, "spy_return_pct": 1.2,
                 "alpha_pct": -5.962, "note": ""},
            ],
            "series": {
                "dates": ["2026-06-01", "2026-06-15", "2026-06-30"],
                "portfolio_pct": [0.0, 1.2, 2.041],
                "spy_pct": [0.0, 0.9, 1.4],
            },
            "note": "",
        },
        "attribution": {
            "status": "ok",
            "as_of": "2026-06-30",
            "periods": [
                {"name": "daily", "start_date": "2026-06-29",
                 "end_date": "2026-06-30", "nlv_start": 995_000.0,
                 "nlv_end": 1_000_000.0, "nlv_change": 5_000.0,
                 "buckets": {"realized_equity": 0.0,
                             "unrealized_equity": 4_200.0,
                             "option_premium_net": 800.0,
                             "option_mtm": 150.0,
                             "assignment_pnl": 0.0,
                             "hedge_pnl": -50.0,
                             "interest_dividends": 0.0},
                 "unattributed": 0.0,
                 "cash_drag": {"avg_put_collateral": 500_000.0,
                               "spy_return_pct": 0.2,
                               "opportunity_cost": 1_000.0},
                 "notes": []},
            ],
            "note": "",
        },
    }


def _briefing_with_benchmark() -> dict:
    raw = json.loads((FIXTURES / "briefing_2026-06-30.json").read_text())
    raw["benchmark_report"] = _benchmark_report()
    return raw


# ─── Route smoke ─────────────────────────────────────────────────────────

def test_briefing_detail_has_benchmark_tab(client):
    """The 📊 Benchmark tab renders on every briefing page (route smoke)."""
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    html = r.text
    assert 'panel="benchmark"' in html
    assert "Benchmark" in html


def test_missing_benchmark_data_does_not_500(client):
    """User symptom to prevent: a briefing without benchmark_report (older
    snapshot / feature off) must render the placeholder, never 500."""
    r = client.get("/briefing/2026-06-30")   # fixture has no benchmark_report
    assert r.status_code == 200
    assert "No benchmark data for this briefing" in r.text


def test_v1_briefing_without_field_does_not_500(client):
    """Pre-task-16 V1 briefing (2026-05-10) also renders cleanly."""
    r = client.get("/briefing/2026-05-10")
    assert r.status_code == 200
    assert 'panel="benchmark"' in r.text


def test_benchmark_tab_renders_alpha_table(client, monkeypatch):
    """With benchmark_report present: alpha table + attribution render,
    negative alpha carries the warning marker, n/a windows show the reason."""
    from app import ingest as ingest_mod

    raw = _briefing_with_benchmark()
    monkeypatch.setattr(ingest_mod, "load_briefing_json",
                        lambda d: raw if d == "2026-06-30" else None)
    r = client.get("/briefing/2026-06-30")
    assert r.status_code == 200
    html = r.text
    # Alpha table values
    assert "+2.0%" in html            # portfolio 30d
    assert "+0.6%" in html            # alpha 30d
    assert "⚠️" in html               # negative inception alpha flagged
    assert "history starts 2026-05-11" in html  # n/a window shows reason
    # Attribution buckets
    assert "P/L attribution" in html
    assert "$+4,200" in html          # unrealized equity
    assert "$+800" in html            # premium net
    # Cash drag renders as a cost
    assert "-$1,000" in html
    # Chart target wired to the chart endpoint
    assert "/charts/benchmark.json?date=2026-06-30" in html


# ─── Chart endpoint ──────────────────────────────────────────────────────

def test_chart_endpoint_missing_data_returns_empty_figure(client):
    r = client.get("/charts/benchmark.json?date=2026-06-30")
    assert r.status_code == 200
    fig = r.json()
    assert "data" in fig and "layout" in fig  # empty figure, not an error

def test_chart_endpoint_unknown_date_no_500(client):
    r = client.get("/charts/benchmark.json?date=1999-01-01")
    assert r.status_code == 200


def test_chart_endpoint_with_series(client, monkeypatch):
    from app import ingest as ingest_mod

    raw = _briefing_with_benchmark()
    monkeypatch.setattr(ingest_mod, "load_briefing_json",
                        lambda d: raw if d == "2026-06-30" else None)
    r = client.get("/charts/benchmark.json?date=2026-06-30")
    assert r.status_code == 200
    fig = r.json()
    names = [t.get("name") for t in fig["data"]]
    assert "Portfolio" in names
    assert "SPY" in names
    port = next(t for t in fig["data"] if t["name"] == "Portfolio")
    assert port["y"][-1] == 2.041


# ─── Model schema ────────────────────────────────────────────────────────

def test_model_defaults_empty_dict_for_old_briefings():
    from app.models import load_briefing

    raw = json.loads((FIXTURES / "briefing_2026-05-10.json").read_text())
    b = load_briefing(raw)
    assert b.benchmark_report == {}


def test_model_round_trips_benchmark_report():
    from app.models import load_briefing

    b = load_briefing(_briefing_with_benchmark())
    assert b.benchmark_report["benchmark"]["status"] == "ok"
    assert len(b.benchmark_report["attribution"]["periods"]) == 1
