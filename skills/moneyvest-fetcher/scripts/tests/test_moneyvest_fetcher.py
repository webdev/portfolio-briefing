"""Moneyvest fetcher tests — parse on constructed DOM fixtures (NEVER live),
cache/staleness, the degraded-no-playwright path, login-state detection, and
debug-dump artifacts (task #46 + 2026-08-06 hardening)."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_moneyvest as fm  # noqa: E402


def _utcnow_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Representative rendered-DOM fixture matching the observed /shopping-list
# column structure (Stock · Price · Fair Value · Delta% · Light Buy ·
# Heavy Buy · No Brainer Buy · No Brainer 2027 PE) with two sections —
# TABLE-based layout.
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

# Same dataset rendered as a DIV-GRID (no <table> anywhere) — the layout the
# resilient parser must ALSO handle: a header row of div cells, interleaved
# single-cell section rows ("MAG7"), and div rows of cells in column order.
SHOPPING_DIV_HTML = """
<div id="root">
  <p>Last updated on August 5, 2026.</p>
  <div class="list">
    <div class="hdr">
      <div>Stock</div><div>Price</div><div>Fair Value</div><div>Delta %</div>
      <div>Light Buy</div><div>Heavy Buy</div><div>No Brainer Buy</div>
      <div>No Brainer 2027 PE</div>
    </div>
    <div class="sec"><span>MAG7</span></div>
    <div class="row"><div>NVDA <span>Nvidia</span></div><div>$182.10</div>
      <div>$152.00</div><div>-16.5%</div><div>$196.00</div><div>$160.00</div>
      <div>$130.00</div><div>24.0</div></div>
    <div class="row"><div>MSFT</div><div>$512.00</div><div>$480.00</div>
      <div>-6.3%</div><div>$470.00</div><div>$430.00</div><div>$390.00</div>
      <div>28.5</div></div>
    <div class="sec"><span>Long Term - High Quality</span></div>
    <div class="row"><div>V — Visa</div><div>$305.40</div><div>$330.00</div>
      <div>+8.1%</div><div>$298.00</div><div>$270.00</div><div>$240.00</div>
      <div>26.0</div></div>
    <div class="row"><div>— footnote row, no ticker —</div><div></div>
      <div></div><div></div><div></div><div></div><div></div><div></div></div>
  </div>
</div>
"""


def _assert_canonical_rows(rows, last_updated):
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


def test_parse_shopping_list_table_layout():
    rows, last_updated = fm.parse_shopping_list(SHOPPING_HTML)
    _assert_canonical_rows(rows, last_updated)


def test_parse_shopping_list_div_grid_layout():
    """The 2026-08-06 probe failure: wait_for_selector('table tr td') timed
    out — the parser must NOT assume <table>. Same rows from a div-grid."""
    rows, last_updated = fm.parse_shopping_list(SHOPPING_DIV_HTML)
    _assert_canonical_rows(rows, last_updated)


def test_parse_skips_rows_without_ticker_never_fabricates():
    for html in (SHOPPING_HTML, SHOPPING_DIV_HTML):
        rows, _ = fm.parse_shopping_list(html)
        assert all(r.get("ticker") for r in rows)
        assert len(rows) == 3  # footnote row dropped


def test_list_headers_found_on_both_layouts():
    expected = ["Stock", "Price", "Fair Value", "Delta %", "Light Buy",
                "Heavy Buy", "No Brainer Buy", "No Brainer 2027 PE"]
    assert fm.list_headers(SHOPPING_HTML) == expected
    assert fm.list_headers(SHOPPING_DIV_HTML) == expected
    assert fm.list_headers("<div><p>login wall</p></div>") == []


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


# -- Multi-section + ETF tab + date regex (2026-08-06 authenticated probe) --

# Real-page shape: section titles are plain DIVS (not h-tags), tabs sit
# above, "Last updated on <date>" is a standalone line. The probe parsed
# only 7 rows with sections: ['unknown'] against this structure.
SHOPPING_MULTISECTION_HTML = """
<div id="root">
  <div class="tabs"><button>Stocks</button><button>ETFs</button></div>
  <p>Last updated on Jun 8, 2026</p>
  <div class="section-title">MAG7</div>
  <table>
    <thead><tr><th>Stock</th><th>Price</th><th>Fair Value</th>
      <th>Delta (%)</th><th>Light Buy</th><th>Heavy Buy</th>
      <th>No Brainer Buy</th><th>No Brainer 2027 PE</th></tr></thead>
    <tbody><tr><td>NVDA</td><td>$182.10</td><td>$152.00</td><td>-16.5%</td>
      <td>$196.00</td><td>$160.00</td><td>$130.00</td><td>24.0</td></tr>
    </tbody>
  </table>
  <div class="section-title">Long Term - High Quality</div>
  <table>
    <thead><tr><th>Stock</th><th>Price</th><th>Fair Value</th>
      <th>Delta (%)</th><th>Light Buy</th><th>Heavy Buy</th>
      <th>No Brainer Buy</th><th>No Brainer 2027 PE</th></tr></thead>
    <tbody><tr><td>V — Visa</td><td>$305.40</td><td>$330.00</td><td>+8.1%</td>
      <td>$298.00</td><td>$270.00</td><td>$240.00</td><td>26.0</td></tr>
    </tbody>
  </table>
  <div class="section-title">Growth</div>
  <table>
    <thead><tr><th>Stock</th><th>Price</th><th>Fair Value</th>
      <th>Delta (%)</th><th>Light Buy</th><th>Heavy Buy</th>
      <th>No Brainer Buy</th><th>No Brainer 2027 PE</th></tr></thead>
    <tbody><tr><td>AMD</td><td>$168.00</td><td>$190.00</td><td>+13.1%</td>
      <td>$160.00</td><td>$145.00</td><td>$120.00</td><td>22.0</td></tr>
    </tbody>
  </table>
</div>
"""

ETF_TAB_HTML = """
<div id="root">
  <div class="section-title">ETFs</div>
  <table>
    <thead><tr><th>Stock</th><th>Price</th><th>Fair Value</th>
      <th>Delta (%)</th><th>Light Buy</th><th>Heavy Buy</th>
      <th>No Brainer Buy</th><th>No Brainer 2027 PE</th></tr></thead>
    <tbody>
      <tr><td>VOO</td><td>$540.00</td><td>$520.00</td><td>-3.7%</td>
        <td>$500.00</td><td>$470.00</td><td>$440.00</td><td>21.0</td></tr>
      <tr><td>QQQ</td><td>$505.00</td><td>$480.00</td><td>-5.0%</td>
        <td>$460.00</td><td>$430.00</td><td>$400.00</td><td>26.0</td></tr>
    </tbody>
  </table>
</div>
"""


def test_all_sections_parsed_with_div_section_titles():
    """Real probe bug: only MAG7's 7 rows parsed, sections: ['unknown'] —
    section names are plain divs preceding each table, not h-tags."""
    rows, lu = fm.parse_shopping_list(SHOPPING_MULTISECTION_HTML)
    assert lu == "Jun 8, 2026"
    by = {r["ticker"]: r["section"] for r in rows}
    assert by == {"NVDA": "MAG7", "V": "Long Term - High Quality",
                  "AMD": "Growth"}


def test_last_updated_captures_date_only_not_run_on_text():
    """Real probe over-read: list updated came back as
    'Jun 8, 2026 MAG7 Stock Price Fair Value Delta (%) Light Buy'."""
    html = ("<div><p>Last updated on Jun 8, 2026 MAG7 Stock Price "
            "Fair Value Delta (%) Light Buy</p></div>")
    _, lu = fm.parse_shopping_list(html)
    assert lu == "Jun 8, 2026"


def test_asset_type_labels_stock_and_etf():
    rows, _ = fm.parse_shopping_list(SHOPPING_HTML)
    assert {r["asset_type"] for r in rows} == {"stock"}
    etf_rows, _ = fm.parse_shopping_list(ETF_TAB_HTML, section_hint="ETFs",
                                         asset_type="etf")
    assert {r["ticker"] for r in etf_rows} == {"VOO", "QQQ"}
    assert {r["asset_type"] for r in etf_rows} == {"etf"}
    assert {r["section"] for r in etf_rows} == {"ETFs"}


# -- Index gauge: active label vs legend (the PANIC bug) --------------------

INDEX_LEGEND_TEXT = (
    "Moneyvest Index\nS&P 500 NASDAQ 100\n3.82\nOPTIMISTIC\n"
    "Updated hourly\n"
    "PANIC 0.00 - 2.00\nFEAR 2.00 - 2.50\nUNCERTAINTY 2.50 - 3.00\n"
    "NEUTRAL 3.00 - 3.50\nOPTIMISTIC 3.50 - 4.00\nGREED 4.00 - 4.50\n"
    "EUPHORIA 4.50 - 5.00\n")


def test_index_active_label_beats_legend_panic_bug():
    """Real probe bug: index came back '3.8 PANIC' — the parser grabbed
    PANIC from the legend list while the active badge read OPTIMISTIC."""
    got = fm.parse_index_text(INDEX_LEGEND_TEXT)
    assert got["value"] == 3.82
    assert got["label"] == "OPTIMISTIC"
    assert "band_warning" not in got


def test_index_bands_scraped_and_label_derived():
    bands = fm.parse_index_bands(INDEX_LEGEND_TEXT)
    assert len(bands) == 7
    assert bands["OPTIMISTIC"] == (3.50, 4.00)
    assert fm.derive_index_label(3.82, bands) == "OPTIMISTIC"
    assert fm.derive_index_label(None, bands) is None
    assert fm.derive_index_label(3.82, {}) is None


def test_index_band_mismatch_warns_and_prefers_derived():
    """Cross-check: scraped badge disagrees with the value's legend band →
    the DERIVED label wins and a band_warning names both. Priority flipped
    2026-08-06 after the live page proved the bare-badge heuristic
    unreliable (legend labels and ranges sit in separate DOM nodes, so
    'PANIC' scraped as active while the gauge read OPTIMISTIC at 3.8 —
    'index: 3.8 PANIC' in the real probe). Value + bands are both scraped;
    deriving fabricates nothing."""
    text = INDEX_LEGEND_TEXT.replace("3.82\nOPTIMISTIC\n", "3.82\nGREED\n")
    got = fm.parse_index_text(text)
    assert got["label"] == "OPTIMISTIC"  # derived from 3.82 ∈ 3.50-4.00
    assert "GREED" in got["band_warning"]


def test_index_label_derived_from_bands_when_no_bare_badge():
    """No bare badge occurrence (all labels carry legend ranges) → derive
    the label from value + scraped bands instead of guessing."""
    text = INDEX_LEGEND_TEXT.replace("OPTIMISTIC\nUpdated hourly\n",
                                     "Updated hourly\n")
    got = fm.parse_index_text(text)
    assert got == {"value": 3.82, "label": "OPTIMISTIC"}


# -- Login-state detection (the real 2026-08-06 failure mode #1) -----------

def test_detect_login_state_signed_out_page():
    body = "Moneyvest\nSign In\nSign Up\nThe smarter way to invest"
    assert fm.detect_login_state(body, f"{fm.BASE_URL}/shopping-list") == "logged_out"


def test_detect_login_state_signed_in_page():
    body = "Moneyvest\nShopping List\nGeorge Blazer\nSign Out\nFair Value"
    assert fm.detect_login_state(body, f"{fm.BASE_URL}/shopping-list") == "logged_in"
    assert fm.detect_user_name(body) == "George Blazer"


def test_detect_login_state_redirect_to_landing():
    assert fm.detect_login_state("Welcome", f"{fm.BASE_URL}/") == "logged_out"
    assert fm.detect_login_state("", f"{fm.BASE_URL}/login") == "logged_out"


def test_detect_user_name_fail_open_none():
    assert fm.detect_user_name("Sign In Sign Up") is None
    assert fm.detect_user_name(None) is None


def test_not_logged_in_msg_is_actionable():
    assert "MONEYVEST_EMAIL/MONEYVEST_PASSWORD" in fm.NOT_LOGGED_IN_MSG
    assert "--headed" in fm.NOT_LOGGED_IN_MSG


# -- Debug artifacts -------------------------------------------------------

class _StubPage:
    def content(self):
        return "<html><body>dump</body></html>"

    def screenshot(self, path, full_page=True):
        Path(path).write_bytes(b"\x89PNG-stub")


def test_dump_debug_writes_artifacts_and_prunes_to_last_3(tmp_path):
    root = tmp_path / "moneyvest_debug"
    dirs = []
    for i in range(5):
        d = fm.dump_debug(_StubPage(), [f"stage {i}"], error=f"boom {i}",
                          debug_dir=root)
        assert d is not None
        dirs.append(d)
    assert (dirs[-1] / "page.html").read_text().startswith("<html>")
    assert (dirs[-1] / "screenshot.png").exists()
    log = (dirs[-1] / "console.log").read_text()
    assert "stage 4" in log and "ERROR: boom 4" in log
    remaining = sorted(p.name for p in root.iterdir() if p.is_dir())
    assert len(remaining) == 3
    assert remaining == sorted(p.name for p in dirs[-3:])


def test_dump_debug_never_raises_on_broken_page(tmp_path):
    class _Broken:
        def content(self):
            raise RuntimeError("browser gone")

        def screenshot(self, path, full_page=True):
            raise RuntimeError("browser gone")

    d = fm.dump_debug(_Broken(), ["stage"], error="x",
                      debug_dir=tmp_path / "dbg")
    assert d is not None
    assert (d / "console.log").exists()


# -- Cache / orchestration -------------------------------------------------

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


def test_utcnow_is_timezone_aware_sourced():
    """datetime.utcnow() is deprecated — _utcnow must come from an aware
    clock (naive-UTC by convention) and match wall-clock UTC."""
    got = fm._utcnow()
    assert got.tzinfo is None
    assert abs((got - _utcnow_naive()).total_seconds()) < 5


def test_run_returns_fresh_cache_without_fetch(tmp_path, monkeypatch):
    p = tmp_path / "mv.json"
    fm.write_cache(p, {"as_of": _utcnow_naive().isoformat() + "Z",
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


def test_not_logged_in_error_falls_back_and_surfaces_message(
        tmp_path, monkeypatch, capsys):
    """The login-wall failure mode: fetch_live raises NOT_LOGGED_IN_MSG →
    run() keeps the prior cache AND the message reaches stderr verbatim."""
    p = tmp_path / "mv.json"
    fm.write_cache(p, {"as_of": "2026-08-05T06:00:00Z",
                       "shopping_list": [{"ticker": "V"}],
                       "index": {}, "m_scores": {}, "provenance": "live"})
    monkeypatch.setattr(fm, "playwright_available", lambda: True)
    monkeypatch.setattr(
        fm, "fetch_live",
        lambda **k: (_ for _ in ()).throw(RuntimeError(fm.NOT_LOGGED_IN_MSG)))
    got = fm.run(output=p, now=datetime(2026, 8, 7, 6, 0))
    assert got["provenance"] == "stale_cache"
    assert "MONEYVEST_EMAIL/MONEYVEST_PASSWORD" in capsys.readouterr().err


def test_run_threads_dump_flag_to_fetch_live(tmp_path, monkeypatch):
    """--dump must reach fetch_live so the debug bundle saves on SUCCESS."""
    seen = {}

    def fake(**k):
        seen.update(k)
        return {"as_of": "x", "shopping_list": [{"ticker": "NVDA"}],
                "index": {}, "m_scores": {}, "provenance": "live"}

    monkeypatch.setattr(fm, "playwright_available", lambda: True)
    monkeypatch.setattr(fm, "fetch_live", fake)
    fm.run(output=tmp_path / "mv.json", force=True, dump=True)
    assert seen.get("dump") is True


def test_login_submit_poll_is_patient():
    """The fixed 4s post-submit wait falsely rejected mid-'Signing In...' —
    the poll window must be at least 20s."""
    assert fm.LOGIN_SUBMIT_TIMEOUT_S >= 20


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
