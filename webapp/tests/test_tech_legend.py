"""Task #13 — persistent tech-indicator legend on every page.

Guards:
- Legend renders on briefing, position detail, and setups/report_view pages
- Legend collapsed by default (uses sl-details without `open` attribute)
- Every documented indicator (RSI, BB, MACD, SMA, S/R, ATR, 52w range, ATH DD)
  has a legend row so users don't have to remember any meaning

Task #Counterpoint-fix — the HTMX trigger uses `sl-after-show` + `intersect`
fallback (not `sl-show once`), so the "Loading..." never gets stuck when
Shoelace's autoloader promotes the custom element after HTMX scans the DOM.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _fresh_client():
    """Fresh app + reset ingest caches so tests don't leak state."""
    from app import ingest
    ingest._PROJECTIONS_READY = False
    if ingest._INMEM_CONN is not None:
        try:
            ingest._INMEM_CONN.close()
        except Exception:
            pass
        ingest._INMEM_CONN = None
    ingest._INMEM_MATERIALIZED = False
    from app.main import create_app
    return TestClient(create_app(), raise_server_exceptions=False)


# ─── Legend renders on every tech-card-bearing page ───


def test_briefing_page_includes_legend():
    """/briefing/{date} shows the 'How to read the technical signals' panel."""
    c = _fresh_client()
    r = c.get("/briefing/2026-06-30")
    assert r.status_code == 200
    assert "How to read the technical signals" in r.text
    assert "tc-legend-details" in r.text


def test_position_detail_includes_legend():
    """/positions/{ticker} shows the legend."""
    c = _fresh_client()
    r = c.get("/positions/NVDA")
    assert r.status_code == 200
    assert "How to read the technical signals" in r.text
    assert "tc-legend-details" in r.text


def test_setups_page_includes_legend():
    """/setups/{date} (report_view) shows the legend."""
    c = _fresh_client()
    r = c.get("/setups/2026-06-30")
    assert r.status_code == 200
    assert "How to read the technical signals" in r.text


def test_legend_covers_every_indicator():
    """Every tech-card signal has a legend entry — regression against a
    future card change adding a new indicator we forget to document."""
    macro = Path(__file__).parent.parent / "app" / "templates" / "_macros" / "tech_legend.html"
    body = macro.read_text()
    # Every named signal on the tech card
    required = [
        "RSI", "Bollinger Bands", "MACD",
        "50 / 200-SMA", "Support / Resistance",
        "ATR", "52-week range", "ATH drawdown",
        "Short-term (1-4 weeks)", "Long-term (3-12 months)",
    ]
    for term in required:
        assert term in body, f"legend missing '{term}' — add a row for it"


def test_legend_collapsed_by_default():
    """No `open` attribute on sl-details — must click to expand.
    Prevents legend from eating screen real estate."""
    macro = Path(__file__).parent.parent / "app" / "templates" / "_macros" / "tech_legend.html"
    body = macro.read_text()
    # The sl-details opening tag must NOT include the open attribute
    assert '<sl-details class="tc-legend-details" summary=' in body
    # But we tolerate any indentation — assert the exact class attribute
    # and no attribute called `open` on the opening tag
    open_tag_start = body.find("<sl-details")
    open_tag_end = body.find(">", open_tag_start)
    open_tag = body[open_tag_start:open_tag_end + 1]
    assert " open" not in open_tag, f"legend must be collapsed by default; got: {open_tag}"


# ─── Counterpoint HTMX trigger fix ───


def test_counterpoint_trigger_uses_sl_after_show():
    """`sl-show once` had a race with Shoelace autoloader — trigger fires
    before the custom element is fully promoted, HTMX never gets a
    subsequent event, and the button stays 'Loading...' forever.

    Fixed by switching to `sl-after-show from:closest sl-details once` +
    an `intersect once` fallback so it also fires when scrolled into view.
    """
    macro = Path(__file__).parent.parent / "app" / "templates" / "_macros" / "unified_card.html"
    body = macro.read_text()
    # The bad trigger must NOT be present
    assert '"sl-show once"' not in body, (
        "Old sl-show once trigger reintroduced — has the known race with "
        "Shoelace autoloader. Use sl-after-show from:closest sl-details."
    )
    # The good trigger MUST be present
    assert "sl-after-show" in body
    assert "intersect once" in body  # fallback path


def test_counterpoint_endpoint_still_returns_200():
    """The fragment endpoint hasn't changed — sanity check."""
    from app import counterpoints
    c = _fresh_client()
    cmap = counterpoints.counterpoints_for_date("2026-07-03")
    if not cmap:
        pytest.skip("no counterpoints for 2026-07-03 fixture")
    key = next(iter(cmap.keys()))
    r = c.get(f"/fragment/counterpoint/2026-07-03/{key}")
    assert r.status_code == 200
    assert "counterpoint-body" in r.text
