"""RSI zone filtering — CSP range vs covered-call range, at a glance.

George (2026-08-10): "We need to add filtering, probably to the UI, to all
the companies, basically RSI filtering. I want to see what is within the
CSP range and what is within the covered call range, so I can clearly see
it at a glance."

Pins:
  - zone classification boundaries (non-exclusive tags — RSI 72 is in
    cc_zone AND blocked_hot; RSI 47 in csp_zone only)
  - band values match the PIPELINE's rsi_discipline (drift test)
  - route smoke: filtered pages 200 + data-rsi-zones attributes + filter
    bar with count spans
  - unknown-RSI cards get zone 'unknown' (visible under All only)
  - humanized labels — no raw zone identifiers leak into visible text
    (hard rule #31)
"""

from __future__ import annotations

import re

from app import rsi_zones


DATE = "2026-06-30"


# ─── (a) Zone classification boundaries ───────────────────────────────


def _z(rsi):
    return set(rsi_zones.zones_for(rsi))


def test_zone_boundaries_csp_band_inclusive():
    """35 ≤ RSI ≤ 55 is the favored CSP entry band (put_entry_band)."""
    assert _z(35.0) == {"csp_zone"}
    assert _z(47.0) == {"csp_zone"}          # "RSI 47 in csp_zone only"
    assert _z(55.0) == {"csp_zone"}


def test_zone_boundary_just_below_csp_band_is_wait_and_blocked_cold():
    """34.9 — below the CSP band and below the CC block (<35): oversold
    wait territory, no new covered calls."""
    assert _z(34.9) == {"wait_zone", "blocked_cold"}


def test_zone_boundary_between_bands_is_wait():
    """55.1 — above the CSP band, below the CC favored zone."""
    assert _z(55.1) == {"wait_zone"}


def test_zone_boundary_cc_favored_at_60():
    """RSI ≥ 60 is the favored covered-call write zone."""
    assert _z(60.0) == {"cc_zone"}
    assert _z(70.0) == {"cc_zone"}           # 70 is NOT yet blocked (>70)


def test_zone_overbought_is_cc_zone_AND_blocked_hot():
    """Non-exclusive tags: 'RSI 72 is in cc_zone AND blocked_hot' — CC
    writes stay favored above 70, but new puts/buys are blocked."""
    assert _z(70.1) == {"cc_zone", "blocked_hot"}
    assert _z(72.0) == {"cc_zone", "blocked_hot"}


def test_zone_falling_knife_below_25():
    """24.9 — momentum break: falling knife AND blocked cold (<35)."""
    assert _z(24.9) == {"blocked_cold", "falling_knife"}
    # 25 exactly is oversold-wait, not a knife
    assert _z(25.0) == {"wait_zone", "blocked_cold"}


def test_zone_none_is_unknown():
    """No RSI → 'unknown'; the card stays visible under All only."""
    assert _z(None) == {"unknown"}
    assert rsi_zones.zones_attr(None) == "unknown"


def test_zones_attr_is_space_separated():
    assert rsi_zones.zones_attr(72.0) == "cc_zone blocked_hot"


# ─── (b) Band values match the pipeline's rsi_discipline (drift) ──────


def test_bands_match_pipeline_rsi_discipline():
    """The webapp NEVER redefines bands — bands() must equal the values
    the pipeline's rsi_discipline resolves from briefing.yaml."""
    from app.config import briefing_config
    from analysis import rsi_discipline  # pipeline module (config bridge path)

    th = rsi_discipline.load_thresholds(briefing_config())
    b = rsi_zones.bands()
    assert b["csp_low"] == float(th["put_entry_band"][0])
    assert b["csp_high"] == float(th["put_entry_band"][1])
    assert b["cc_favored"] == float(th["call"]["favored_above"])
    assert b["put_block"] == float(th["put"]["block_above"])
    assert b["cc_block"] == float(th["call"]["block_below"])
    assert b["falling_knife"] == float(th["put"]["falling_knife_below"])


def test_pipeline_standard_wheel_band_values_are_pinned():
    """Standard wheel bands the UI docs/labels assume: 35/55/60/70/25/35.
    If the pipeline changes these, this fails loudly so the zone semantics
    (and this suite's boundary expectations) get revisited together."""
    from analysis import rsi_discipline

    d = rsi_discipline.DEFAULT_THRESHOLDS
    assert d["put_entry_band"] == [35.0, 55.0]
    assert d["call"]["favored_above"] == 60.0
    assert d["put"]["block_above"] == 70.0
    assert d["call"]["block_below"] == 35.0
    assert d["put"]["falling_knife_below"] == 25.0


# ─── Badge + filter chips are humanized (rule #31) ────────────────────


def test_zone_badge_humanized_and_prioritized():
    b47 = rsi_zones.zone_badge(47.0)
    assert b47["label"] == "🎯 CSP zone"
    assert b47["text"] == "RSI 47 · 🎯 CSP zone"
    # George 2026-08-10: "RSI 80 should be CC." Overbought IS prime
    # covered-call territory — the badge leads with the actionable read
    # and keeps the heat as a qualifier.
    b72 = rsi_zones.zone_badge(72.0)
    assert "CC zone" in b72["label"] and "overbought" in b72["label"]
    assert b72["zone"] == "cc_zone"
    b80 = rsi_zones.zone_badge(80.0)
    assert b80["text"] == "RSI 80 · 📞 CC zone · 🔥 overbought"
    b62 = rsi_zones.zone_badge(62.0)
    assert b62["label"] == "📞 CC zone"
    b30 = rsi_zones.zone_badge(30.0)
    assert "Oversold" in b30["label"]
    b20 = rsi_zones.zone_badge(20.0)
    assert "Falling knife" in b20["label"]
    b57 = rsi_zones.zone_badge(57.0)
    assert "Wait" in b57["label"]
    # No raw machine identifiers in any visible string
    for badge in (b47, b72, b62, b30, b20, b57):
        assert "_zone" not in badge["label"] + badge["text"]
        assert "blocked_" not in badge["label"] + badge["text"]


def test_zone_badge_none_when_rsi_unknown():
    """Rule #19 — never fabricate a zone read for a missing RSI."""
    assert rsi_zones.zone_badge(None) is None


def test_filter_chips_definitions_come_from_backend_bands():
    chips = rsi_zones.filter_chips()
    zones = [c["zone"] for c in chips]
    assert zones == ["all", "csp_zone", "cc_zone", "wait_zone",
                     "blocked_hot", "blocked_cold"]
    by_zone = {c["zone"]: c for c in chips}
    assert by_zone["csp_zone"]["label"] == "🎯 CSP zone"
    assert by_zone["cc_zone"]["label"] == "📞 CC zone"
    assert "Overbought" in by_zone["blocked_hot"]["label"]
    assert "Oversold" in by_zone["blocked_cold"]["label"]
    # Titles carry the actual band bounds (templates/JS never hardcode)
    assert "35" in by_zone["csp_zone"]["title"] and "55" in by_zone["csp_zone"]["title"]
    assert "60" in by_zone["cc_zone"]["title"]
    assert "70" in by_zone["blocked_hot"]["title"]


# ─── (c) Route smoke — filter bar + attributes render ─────────────────


def test_setups_page_renders_rsi_filter_bar_with_counts(client):
    """/setups/{date} → 200, filter bar present with zone chips + count
    spans, and rep-cards carry data-rsi-zones."""
    r = client.get(f"/setups/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'class="uc-sort rsi-filter-bar"' in body
    assert 'data-rsi-cards=".rep-card"' in body
    assert 'data-zone="csp_zone"' in body
    assert 'data-zone="cc_zone"' in body
    assert 'data-zone="blocked_hot"' in body
    assert '<span class="rsi-count">' in body
    assert "🎯 CSP zone" in body
    assert "📞 CC zone" in body
    # Every rep-card carries the attribute
    cards = re.findall(r'<article class="rep-card[^"]*"[^>]*>', body)
    assert cards, "expected rep-cards in the fixture setups page"
    assert all("data-rsi-zones=" in c for c in cards)


def test_setups_nvda_card_is_in_csp_zone(client):
    """Fixture NVDA has RSI 44 (deep read) → csp_zone tag on its card,
    and the humanized badge renders — 'so I can clearly see it at a
    glance.'"""
    r = client.get(f"/setups/{DATE}")
    body = r.text
    assert 'data-rsi-zones="csp_zone"' in body
    assert "🎯 CSP zone" in body


def test_briefing_technical_tab_has_rsi_filter_and_zone_chip(client):
    """The Technical Read tab gets a filter bar scoped to .tech-card, and
    NVDA's tech card (RSI 44) carries the zone attribute + badge chip."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'data-rsi-key="briefing-technical"' in body
    assert 'data-rsi-cards=".tech-card"' in body
    # tech-card article stamped with the zone
    tech_cards = re.findall(r'<article class="tech-card[^"]*"[^>]*>', body)
    assert tech_cards
    assert any('data-rsi-zones="csp_zone"' in c for c in tech_cards)
    # zone chip rendered next to the RSI chip
    assert "tc-zone-chip" in body
    assert "🎯 CSP zone" in body


def test_briefing_ideas_tab_has_rsi_filter_bar(client):
    """The Ideas & Candidates tab gets its own filter bar scoped to
    .uc-card, spanning all three group grids."""
    r = client.get(f"/briefing/{DATE}")
    assert r.status_code == 200
    body = r.text
    assert 'data-rsi-key="briefing-ideas"' in body
    assert 'data-rsi-cards=".uc-card"' in body
    # unified cards carry the attribute (zones or unknown)
    uc_cards = re.findall(r'<article class="uc-card[^"]*"[^>]*>', body, re.S)
    assert uc_cards
    assert all("data-rsi-zones=" in c for c in uc_cards)


# ─── (d) Unknown-RSI card handling ────────────────────────────────────


def test_cards_without_rsi_get_unknown_zone(client):
    """AMAT/SMCI have no deep read in the fixture — their setup cards
    classify from the report's own RSI metric when present, else render
    data-rsi-zones='unknown' (visible under All only, never fabricated)."""
    r = client.get(f"/setups/{DATE}")
    body = r.text
    # The skeleton tech card path itself is 'unknown'
    assert rsi_zones.card_zones_attr(None, None) == "unknown"
    assert rsi_zones.card_zones_attr({"available": False, "rsi": None}, {}) == "unknown"
    # And no crash / 500 with mixed known+unknown cards on the page
    assert r.status_code == 200


def test_metrics_rsi_fallback_parses_report_chip():
    """A rep-card without a tech view-model still classifies off its own
    parsed 'RSI' metric chip (e.g. value '45 🟢')."""
    card = {"metrics": [{"label": "RSI", "value": "45 🟢 pullback", "tone": "ok"}]}
    assert rsi_zones.card_rsi(None, card) == 45.0
    assert rsi_zones.card_zones_attr(None, card) == "csp_zone"
    card_hot = {"metrics": [{"label": "RSI", "value": "72"}]}
    assert rsi_zones.card_zones_attr(None, card_hot) == "cc_zone blocked_hot"
    assert rsi_zones.card_rsi(None, {"metrics": [{"label": "IV rank", "value": "80"}]}) is None


def test_no_raw_zone_identifiers_in_visible_text(client):
    """Rule #31 — machine identifiers live in attributes only, never as
    rendered body text (e.g. '>csp_zone<')."""
    for url in (f"/setups/{DATE}", f"/briefing/{DATE}"):
        body = client.get(url).text
        for ident in ("csp_zone", "cc_zone", "wait_zone",
                      "blocked_hot", "blocked_cold", "falling_knife"):
            assert f">{ident}<" not in body, f"{ident} leaked as text on {url}"
