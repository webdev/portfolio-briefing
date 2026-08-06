"""Moneyvest fetcher tests — parse on constructed DOM fixtures (NEVER live),
cache/staleness, and the degraded-no-playwright path (task #46)."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_moneyvest as fm  # noqa: E402


# Representative rendered-DOM fixture matching the observed /shopping-list
# column structure (Stock · Price · Fair Value · Delta% · Light Buy ·
# Heavy Buy · No Brainer Buy · No Brainer 2027 PE) with two sections.
SHOPPING_HTML = """
<div id="root">
  <p>Last updated on August 5, 2026.</p>
  <h3>MAG7</h3>
  <table>
    <thead><tr>
      <th>Stock</th><th>Price</th><th>Fair Value</th><th>Delta %</th>
      <th>Light Buy</th><th>Heavy Buy</th><th>No Brainer Buy</th>
      <th>No Brainer 2027 PE</th>
    </tr></thead>
    <tbody>
      <tr><td>NVDA <span>Nvidia</span></td><td>$182.10</td><td>$152.00</td>
          <td>-16.5%</td><td>$196.00</td><td>$160.00</td><td>$130.00</td>
          <td>24.0</td></tr>
      <tr><td>MSFT</td><td>$512.00</td><td>$480.00</td><td>-6.3%</td>
          <td>$470.00</td><td>$430.00</td><td>$390.00</td><td>28.5</td></tr>
    </tbody>
  </table>
  <h3>Long Term - High Quality</h3>
  <table>
    <thead><tr>
      <th>Stock</th><th>Price</th><th>Fair Value</th><th>Delta %</th>
      <th>Light Buy</th><th>Heavy Buy</th><th>No Brainer Buy</th>
      <th>No Brainer 2027 PE</th>
    </tr></thead>
    <tbody>
      <tr><td>V — Visa</td><td>$305.40</td><td>$330.00</td><td>+8.1%</td>
          <td>$298.00</td><td>$270.00</td><td>$240.00</td><td>26.0</td></tr>
      <tr><td>— footnote row, no ticker —</td><td></td><td></td><td></td>
          <td></td><td></td><td></td><td></td></tr>
    </tbody>
  </table>
</div>
"""


def test_parse_shopping_list_rows_and_sections():
    rows, last_updated = fm.parse_shopping_list(SHOPPING_HTML)
    assert last_updated == "August 5, 2026"
    by_ticker = {r["ticker"]: r for r in rows}
    assert set(by_ticker) == {"NVDA", "MSFT", "V"}
    nvda = by_ticker["NVDA"]
    assert nvda["section"] == "MAG7"
    assert nvda["price"] == 182.10
    assert nvda["fair_value"] == 152.00
    assert nvda["light_buy"] == 196.00
    assert nvda["heavy_buy"] == 160.00
    assert nvda["no_brainer"] == 130.00
    assert nvda["pe_2027"] == 24.0
    assert by_ticker["V"]["section"] == "Long Term - High Quality"
    assert by_ticker["V"]["light_buy"] == 298.00


def test_parse_skips_rows_without_ticker_never_fabricates():
    rows, _ = fm.parse_shopping_list(SHOPPING_HTML)
    assert all(r.get("ticker") for r in rows)
    assert len(rows) == 3  # footnote row dropped


def test_parse_money_edge_cases():
    assert fm.parse_money("$1,234.50") == 1234.50
    assert fm.parse_money("n/a") is None
    assert fm.parse_money(None) is None
    assert fm.parse_money("") is None


def test_parse_index_text():
    got = fm.parse_index_text(
        "Moneyvest Index\nS&P 500\n3.82\nOPTIMISTIC\nupdated hourly")
    assert got == {"value": 3.82, "label": "OPTIMISTIC"}
    assert fm.parse_index_text("no gauge here at all") is None
    # Label-only still returns the label (value fail-open None)
    got2 = fm.parse_index_text("Sentiment: FEAR")
    assert got2["label"] == "FEAR"


def test_parse_m_score_badge():
    assert fm.parse_m_score("blah M-Score · 4.05/5.00 blah") == 4.05
    assert fm.parse_m_score("M Score: 3.2/5") == 3.2
    assert fm.parse_m_score("no score") is None


def test_cache_freshness_20h_ttl(tmp_path):
    p = tmp_path / "mv.json"
    payload = {"as_of": "2026-08-06T06:00:00Z", "shopping_list": [{"ticker": "NVDA"}],
               "index": {}, "m_scores": {}, "provenance": "live"}
    fm.write_cache(p, payload)
    now_fresh = datetime(2026, 8, 6, 20, 0)   # 14h later
    now_stale = datetime(2026, 8, 7, 8, 0)    # 26h later
    cached = fm.load_cache(p)
    assert fm.cache_is_fresh(cached, now=now_fresh) is True
    assert fm.cache_is_fresh(cached, now=now_stale) is False
    assert fm.staleness_hours(cached, now=now_stale) == 26.0


def test_run_returns_fresh_cache_without_fetch(tmp_path, monkeypatch):
    p = tmp_path / "mv.json"
    fm.write_cache(p, {"as_of": datetime.utcnow().isoformat() + "Z",
                       "shopping_list": [{"ticker": "NVDA"}],
                       "index": {}, "m_scores": {}, "provenance": "live"})

    def _boom(*a, **k):
        raise AssertionError("must not fetch when cache is fresh")
    monkeypatch.setattr(fm, "fetch_live", _boom)
    got = fm.run(output=p)
    assert got["provenance"] == "live"
    assert got["shopping_list"][0]["ticker"] == "NVDA"


def test_degraded_no_playwright_keeps_prior_cache(tmp_path, monkeypatch, capsys):
    """Playwright missing → stale-cache mode with staleness label; never
    raises, never blocks the briefing."""
    p = tmp_path / "mv.json"
    fm.write_cache(p, {"as_of": "2026-08-01T06:00:00Z",
                       "shopping_list": [{"ticker": "MSFT"}],
                       "index": {}, "m_scores": {}, "provenance": "live"})
    monkeypatch.setattr(fm, "playwright_available", lambda: False)
    got = fm.run(output=p, now=datetime(2026, 8, 6, 12, 0))
    assert got["provenance"] == "stale_cache"
    assert got["stale_hours"] > 100
    assert got["shopping_list"][0]["ticker"] == "MSFT"
    assert "playwright not installed" in capsys.readouterr().err


def test_degraded_no_playwright_no_cache_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "playwright_available", lambda: False)
    got = fm.run(output=tmp_path / "missing.json")
    assert got["provenance"] == "unavailable"
    assert got["shopping_list"] == []


def test_fetch_error_falls_back_to_stale_cache(tmp_path, monkeypatch):
    p = tmp_path / "mv.json"
    fm.write_cache(p, {"as_of": "2026-08-05T06:00:00Z",
                       "shopping_list": [{"ticker": "V"}],
                       "index": {}, "m_scores": {}, "provenance": "live"})
    monkeypatch.setattr(fm, "playwright_available", lambda: True)
    monkeypatch.setattr(fm, "fetch_live",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    got = fm.run(output=p, now=datetime(2026, 8, 7, 6, 0))
    assert got["provenance"] == "stale_cache"
    assert got["shopping_list"][0]["ticker"] == "V"


def test_empty_scrape_is_treated_as_failure(tmp_path, monkeypatch):
    """A zero-row scrape is a parse/auth failure — keep prior cache."""
    p = tmp_path / "mv.json"
    fm.write_cache(p, {"as_of": "2026-08-05T06:00:00Z",
                       "shopping_list": [{"ticker": "V"}],
                       "index": {}, "m_scores": {}, "provenance": "live"})
    monkeypatch.setattr(fm, "playwright_available", lambda: True)
    monkeypatch.setattr(fm, "fetch_live", lambda **k: {
        "as_of": "x", "shopping_list": [], "index": {}, "m_scores": {},
        "provenance": "live"})
    got = fm.run(output=p, now=datetime(2026, 8, 7, 6, 0))
    assert got["provenance"] == "stale_cache"
