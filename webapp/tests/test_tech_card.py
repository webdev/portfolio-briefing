"""Tests for the infographic-style technical-analysis ticker card (task #7).

User request: "Every ticker card in the webapp should look and read like
the cards on technical_analysis_infographic.html" — header, returns row,
signal chips, BB track, S/R track, trend 2×2, ST/LT verdicts — rendered
on /positions/{ticker}, the briefing's 🎯 Technical Read tab, and
/setups/{date}. Missing deep data must render an explicit skeleton, not
a 500 and not fabricated numbers (hard rule #19).

Fixture: tests/fixtures/snapshots/2026-06-30/technicals.json carries
NVDA with a full `deep` + `support_resistance` payload and AMZN with
`deep: null` (the fail-closed path).
"""

from __future__ import annotations

import pytest


DATE = "2026-06-30"


# ─── Route smoke: position drill-down ────────────────────────────────


def test_position_detail_renders_tech_card(client):
    """/positions/NVDA returns 200 and contains the tech card markers."""
    r = client.get("/positions/NVDA")
    assert r.status_code == 200
    body = r.text
    assert "tech-card" in body
    assert "tc-bb-track" in body       # Bollinger visualization
    assert "tc-sr-track" in body       # Support/Resistance visualization
    assert "Technical analysis" in body


def test_position_detail_card_shows_live_numbers_not_placeholders(client):
    """Every number on the card comes from the fixture's deep payload."""
    r = client.get("/positions/NVDA")
    body = r.text
    assert "$198.82" in body                       # spot from deep
    assert "RSI 44" in body                        # RSI chip
    assert "BB 30%" in body                        # bb_position_pct 29.6 → 30
    assert "Golden cross" in body                  # trend row
    assert "Uptrend intact" in body                # LT verdict label
    assert "Stabilizing" in body                   # ST verdict label
    assert "$191.82" in body                       # nearest support (real SR cluster)


def test_position_detail_missing_deep_renders_skeleton_not_500(client):
    """AMZN has deep: null — the page must render the explicit
    'chart data unavailable' skeleton, never 500, never fake numbers."""
    r = client.get("/positions/AMZN")
    assert r.status_code == 200
    assert "tech-card-unavailable" in r.text
    assert "Chart data unavailable" in r.text


def test_position_detail_unknown_ticker_no_500(client):
    """A ticker with NO technicals entry at all still renders (skeleton)."""
    r = client.get("/positions/ZZZQ")
    assert r.status_code == 200
    assert "Chart data unavailable" in r.text


# ─── Route smoke: briefing Technical Read tab ────────────────────────


def test_briefing_has_technical_read_tab(client):
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "Technical Read" in body
    assert 'panel="technical"' in body
    assert "tech-card" in body
    # Grouped by role — fixture has NVDA + AMZN equities (their short
    # puts dedup into the equity group, so no separate put group here)
    assert "Equity holdings" in body


def test_briefing_tab_includes_skeleton_for_position_without_deep(client):
    """AMZN is a held position (equity + short put) without a deep read —
    it must appear as an explicit unavailable card, not silently vanish."""
    r = client.get(f"/briefing/{DATE}")
    assert "tech-card-unavailable" in r.text


def test_briefing_without_technicals_file_has_no_tab(client):
    """2026-05-10 has no technicals.json — tab suppressed, page still 200."""
    r = client.get("/briefing/2026-05-10")
    assert r.status_code == 200
    assert 'panel="technical"' not in r.text


def test_briefing_tab_gated_on_deep_count(client, tmp_path, monkeypatch):
    """A snapshot whose technicals.json has NO deep payloads (produced
    before the deep-read pipeline landed) must NOT show an all-skeleton
    tab — deep_count gates it."""
    from app import tech_card

    groups = tech_card.build_groups(DATE)
    assert groups["deep_count"] >= 1  # fixture sanity — tab IS shown

    # Simulate the pre-deep snapshot shape: same entries, deep stripped.
    import app.ingest as ingest_mod

    stripped = {
        tk: {**(v or {}), "deep": None}
        for tk, v in ingest_mod.load_snapshot_technicals(DATE).items()
    }
    monkeypatch.setattr(ingest_mod, "load_snapshot_technicals", lambda d: stripped)
    groups2 = tech_card.build_groups(DATE)
    assert groups2["deep_count"] == 0  # template gate suppresses the tab


# ─── Route smoke: setups enrichment ──────────────────────────────────


def test_setups_cards_enriched_with_tech_card(client):
    """NVDA is a 🎯 CANDIDATE in the candidates fixture AND has a deep
    read — its setups card must carry the technical read INLINE (task #18
    made it always visible, matching the unified card on /briefing)."""
    r = client.get(f"/setups/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert "tech-card" in body
    assert "tc-bb-track" in body
    # Task #18: the tech card is no longer hidden behind a collapsed
    # <sl-details class="tc-details"> disclosure.
    assert "tc-details" not in body


def test_candidates_page_still_renders_without_tech_cards(client):
    """/candidates/{date} doesn't pass tech_cards — the shared card macro
    must default gracefully (regression guard for the default arg)."""
    r = client.get(f"/candidates/{DATE}")
    assert r.status_code == 200


# ─── View-model unit tests: chip color bands (infographic mapping) ───


def test_rsi_chip_bands():
    from app.tech_card import rsi_chip

    assert rsi_chip(None) == {"icon": "⚪", "label": "RSI n/a", "tone": "neutral"}
    assert rsi_chip(20)["tone"] == "blue"       # oversold
    assert rsi_chip(34)["tone"] == "yellow"
    assert rsi_chip(44)["tone"] == "neutral"
    assert rsi_chip(55)["tone"] == "green"
    assert rsi_chip(66)["tone"] == "orange"
    assert rsi_chip(75)["tone"] == "red"        # overbought
    assert rsi_chip(75)["label"] == "RSI 75"


def test_bb_chip_bands():
    from app.tech_card import bb_chip

    assert bb_chip(-15)["tone"] == "blue"       # pierced lower band
    assert bb_chip(18)["tone"] == "blue"
    assert bb_chip(25)["tone"] == "yellow"
    assert bb_chip(49)["tone"] == "neutral"
    assert bb_chip(65)["tone"] == "green"
    assert bb_chip(84)["tone"] == "orange"
    assert bb_chip(107)["tone"] == "red"        # pierced upper band


def test_macd_chip_quadrants():
    from app.tech_card import macd_chip

    assert macd_chip(0.5, 0.2)["tone"] == "green"     # rising, positive
    assert macd_chip(-0.4, -1.1)["tone"] == "yellow"  # rising, negative
    assert macd_chip(0.3, 0.9)["tone"] == "orange"    # falling, positive
    assert macd_chip(-0.9, -0.2)["tone"] == "red"     # falling, negative
    assert "↗" in macd_chip(0.5, 0.2)["label"]
    assert "↘" in macd_chip(-0.9, -0.2)["label"]


def test_bb_marker_palette_matches_infographic():
    from app.tech_card import bb_marker_class

    assert bb_marker_class(7) == "tc-bbm-deep"    # AVGO 9 / QCOM 7 → purple
    assert bb_marker_class(18) == "tc-bbm-low"    # NVDA 18 → blue
    assert bb_marker_class(49) == "tc-bbm-mid"    # GOOG 49 → green
    assert bb_marker_class(65) == "tc-bbm-high"   # SPY 65 → amber
    assert bb_marker_class(98) == "tc-bbm-top"    # PATH 98 → red


# ─── View-model unit tests: builder ──────────────────────────────────


def _fixture_entry():
    import json
    from pathlib import Path

    p = (Path(__file__).parent / "fixtures" / "snapshots" / DATE
         / "technicals.json")
    return json.loads(p.read_text())


def test_build_card_full_payload():
    from app.tech_card import build_card

    vm = build_card("NVDA", _fixture_entry()["NVDA"], name="NVIDIA")
    assert vm["available"] is True
    assert vm["spot_str"] == "$198.82"
    assert vm["name"] == "NVIDIA"
    # Marker position is the raw bb position (clamped 0-100)
    assert vm["bb"]["marker_left"] == pytest.approx(29.6)
    # SR: nearest support below spot is the $191.82 pivot (3 touches)
    assert "191.82" in vm["sr"]["nearest_sup"]
    assert "3× touched" in vm["sr"]["nearest_sup"]
    # Nearest resistance above spot is $210.13
    assert "210.13" in vm["sr"]["nearest_res"]
    # Verdicts mapped to infographic labels
    assert vm["st"]["label"] == "🌅 Stabilizing"
    assert vm["lt"]["label"] == "📊 Uptrend intact"
    # Trend row derives from real slopes — no hardcoded strings
    assert "+2.4%/mo" in vm["trend"]["cross_str"]
    assert vm["trend"]["vs200_str"] == "+4.0%"


def test_build_card_marker_clamps_outside_band():
    from app.tech_card import build_card

    entry = _fixture_entry()["NVDA"]
    entry["deep"]["bb_position_pct"] = 107.0
    vm = build_card("NVDA", entry)
    assert vm["bb"]["marker_left"] == 100.0
    entry["deep"]["bb_position_pct"] = -15.0
    vm = build_card("NVDA", entry)
    assert vm["bb"]["marker_left"] == 0.0


def test_build_card_missing_deep_is_skeleton():
    from app.tech_card import build_card

    vm = build_card("AMZN", _fixture_entry()["AMZN"])
    assert vm["available"] is False
    assert vm["ticker"] == "AMZN"
    # spot from the shallow technicals still shown on the skeleton header
    assert vm["spot_str"] == "$242.10"


def test_build_card_no_sr_fails_closed():
    """No SR clusters → sr is None (macro renders 'unavailable', never a
    synthesized level — hard rule #19/#10)."""
    from app.tech_card import build_card

    entry = _fixture_entry()["NVDA"]
    entry["support_resistance"] = None
    vm = build_card("NVDA", entry)
    assert vm["available"] is True
    assert vm["sr"] is None


def test_load_ticker_technicals_helper():
    from app import ingest

    deep = ingest.load_ticker_technicals(DATE, "nvda")
    assert deep is not None and deep["spot"] == 198.82
    assert ingest.load_ticker_technicals(DATE, "AMZN") is None
    assert ingest.load_ticker_technicals("2026-05-10", "NVDA") is None
    assert ingest.load_ticker_technicals("", "NVDA") is None


def test_build_groups_roles():
    from app import tech_card

    groups = tech_card.build_groups(DATE)
    titles = {g["title"]: [c["ticker"] for c in g["cards"]] for g in groups["groups"]}
    # NVDA + AMZN are equities; both also have short puts, but equity
    # grouping wins (each ticker appears exactly once).
    assert titles["Equity holdings"] == ["AMZN", "NVDA"]
    all_tickers = [t for tks in titles.values() for t in tks]
    assert len(all_tickers) == len(set(all_tickers))
    assert groups["count"] == 2
    assert groups["deep_count"] == 1  # only NVDA carries a deep read
