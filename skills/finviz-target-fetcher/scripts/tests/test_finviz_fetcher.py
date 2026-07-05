"""Test suite for the FINVIZ public target fetcher.

TDD discipline (CLAUDE.md hard rule #33):
- Every parser behaviour pinned by a test against fixture HTML
- Every fail-mode (captcha / non-200 / parse miss / ETF / cache TTL /
  daily cap) covered
- No network in any test — fetch_one is exercised via responses mock

Tests live alongside the scripts; run with:
    python3 -m pytest skills/finviz-target-fetcher/scripts/tests/ -v
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS))

from fetch_finviz_targets import (  # noqa: E402
    _cache_fresh,
    _load_cache,
    _save_cache,
    fetch_targets,
)
from public_fetcher import (  # noqa: E402
    _parse_float,
    _recommendation_label,
    parse_quote_page,
)


FIXTURES = Path(__file__).parent / "fixtures"


# ─── _parse_float ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("s, expected", [
    ("318.50", 318.5),
    ("1,234.56", 1234.56),
    ("4.10%", 4.10),
    ("24.30B", 24_300_000_000.0),
    ("210.40M", 210_400_000.0),
    ("180,500,000", 180_500_000.0),
    ("-15.00%", -15.0),
    ("-", None),
    ("", None),
    ("N/A", None),
    (None, None),
    ("garbage", None),
])
def test_parse_float(s, expected):
    assert _parse_float(s) == expected


# ─── _recommendation_label ─────────────────────────────────────────────────


@pytest.mark.parametrize("score, expected", [
    (1.0, "Strong Buy"),
    (1.5, "Strong Buy"),
    (1.8, "Buy"),
    (2.5, "Buy"),
    (3.0, "Hold"),
    (3.5, "Hold"),
    (4.0, "Sell"),
    (4.5, "Sell"),
    (5.0, "Strong Sell"),
    (None, None),
])
def test_recommendation_label(score, expected):
    assert _recommendation_label(score) == expected


# ─── parse_quote_page — fixture round-trip ────────────────────────────────


def test_parse_nvda_fixture_returns_full_dict():
    html = (FIXTURES / "sample_quote_nvda.html").read_text()
    out = parse_quote_page(html, "NVDA")
    assert out is not None
    assert out["ticker"] == "NVDA"
    assert out["spot_price"] == 200.09
    assert out["target_price"] == 318.50
    assert out["target_upside_pct"] == pytest.approx(59.2, abs=0.1)
    assert out["analyst_recommendation"] == 1.80
    assert out["analyst_label"] == "Buy"
    assert out["p_e"] == 64.20
    assert out["fwd_p_e"] == 38.10
    assert out["eps_growth_next_y_pct"] == 24.50
    assert out["source"] == "finviz_public"
    assert "fetched_at" in out


def test_parse_captcha_or_blocked_page_returns_none():
    """When FINVIZ serves a captcha or block page, no snapshot-table2
    will be present. The parser must return None — never an invented value."""
    assert parse_quote_page("<html><body>Captcha</body></html>", "NVDA") is None
    assert parse_quote_page("", "NVDA") is None
    assert parse_quote_page(None, "NVDA") is None


def test_parse_page_with_no_target_or_recom_returns_none():
    """A snapshot page with no analyst data has nothing to surface."""
    html = """
    <table class="snapshot-table2"><tr>
      <td class="snapshot-td2-cp">P/E</td><td>10.5</td>
      <td class="snapshot-td2-cp">Market Cap</td><td>1.2B</td>
    </tr></table>
    """
    assert parse_quote_page(html, "TEST") is None


def test_parse_target_without_spot_keeps_target_skips_upside():
    """Should still return target_price even when spot is unavailable —
    upside just stays None. Fail-soft, not fail-closed: the target is
    actionable on its own."""
    html = """
    <table class="snapshot-table2"><tr>
      <td class="snapshot-td2-cp">Target Price</td><td>100</td>
      <td class="snapshot-td2-cp">Recom</td><td>2.0</td>
    </tr></table>
    """
    out = parse_quote_page(html, "TEST")
    assert out is not None
    assert out["target_price"] == 100.0
    assert out["spot_price"] is None
    assert out["target_upside_pct"] is None
    assert out["analyst_label"] == "Buy"


# ─── _cache_fresh ─────────────────────────────────────────────────────────


def test_cache_fresh_returns_true_within_ttl():
    entry = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    assert _cache_fresh(entry, ttl_hours=24) is True


def test_cache_fresh_returns_false_when_stale():
    old = datetime.now(timezone.utc) - timedelta(hours=25)
    entry = {"fetched_at": old.isoformat(timespec="seconds")}
    assert _cache_fresh(entry, ttl_hours=24) is False


def test_cache_fresh_returns_false_on_missing_or_bad_timestamp():
    assert _cache_fresh({}, ttl_hours=24) is False
    assert _cache_fresh({"fetched_at": "not-a-date"}, ttl_hours=24) is False


# ─── fetch_targets — batch + cache + ETF filter + daily cap ──────────────


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    d = tmp_path / "state"
    d.mkdir()
    return d


def test_fetch_targets_filters_known_etfs_without_calling_network(cache_dir):
    """ETFs return None immediately; never hit the network."""
    cfg = {"known_etfs": ["SPY", "QQQ"], "daily_request_cap": 60}
    with patch("fetch_finviz_targets.fetch_one") as m_fetch:
        out, stats = fetch_targets(["SPY", "QQQ"], cache_dir=cache_dir, config=cfg)
    assert out == {"SPY": None, "QQQ": None}
    assert stats.requests_made == 0
    assert m_fetch.call_count == 0


def test_fetch_targets_serves_cache_when_fresh(cache_dir):
    """A fresh cache entry skips the network entirely."""
    fresh = {
        "ticker": "NVDA",
        "target_price": 318.5,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save_cache(cache_dir, {"NVDA": fresh})

    with patch("fetch_finviz_targets.fetch_one") as m_fetch:
        out, stats = fetch_targets(["NVDA"], cache_dir=cache_dir, config={
            "known_etfs": [],
            "daily_request_cap": 60,
            "cache_ttl_hours": 24,
        })
    assert out["NVDA"] == fresh
    assert m_fetch.call_count == 0


def test_fetch_targets_refresh_bypasses_cache(cache_dir):
    """--refresh forces a network call even when cache is fresh."""
    fresh = {
        "ticker": "NVDA",
        "target_price": 100.0,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _save_cache(cache_dir, {"NVDA": fresh})

    new_value = {"ticker": "NVDA", "target_price": 318.5, "fetched_at": "now"}
    with patch("fetch_finviz_targets.fetch_one", return_value=new_value):
        out, _ = fetch_targets(["NVDA"], cache_dir=cache_dir, refresh=True, config={
            "known_etfs": [],
            "daily_request_cap": 60,
            "cache_ttl_hours": 24,
        })
    assert out["NVDA"]["target_price"] == 318.5


def test_fetch_targets_respects_daily_cap(cache_dir):
    """Once daily_request_cap is hit, additional tickers get None
    (and we DON'T call fetch_one for them)."""
    call_log = []

    def fake_fetch(ticker, **_):
        call_log.append(ticker)
        return {"ticker": ticker, "target_price": 1.0, "fetched_at": "now"}

    with patch("fetch_finviz_targets.fetch_one", side_effect=fake_fetch):
        out, stats = fetch_targets(
            ["A", "B", "C", "D"],
            cache_dir=cache_dir,
            config={"known_etfs": [], "daily_request_cap": 2, "cache_ttl_hours": 24},
        )
    assert len(call_log) == 2  # only first two fetched
    assert stats.daily_cap_hit is True
    # Tickers above cap return None
    assert sum(1 for v in out.values() if v is None) == 2


def test_fetch_targets_caches_none_to_avoid_re_probing(cache_dir):
    """When a fetch returns None (blocked/no-data), cache a null
    envelope so the next batch within TTL doesn't waste a request
    retrying. The cache entry is the envelope; the function output
    is still None."""
    with patch("fetch_finviz_targets.fetch_one", return_value=None):
        fetch_targets(["XYZ"], cache_dir=cache_dir, config={
            "known_etfs": [], "daily_request_cap": 60, "cache_ttl_hours": 24,
        })
    cache = _load_cache(cache_dir)
    assert "XYZ" in cache
    # Null envelope carries fetched_at (so TTL works) + _null marker
    assert cache["XYZ"].get("_null") is True
    assert "fetched_at" in cache["XYZ"]

    # Second batch: cache envelope is fresh → no network call
    with patch("fetch_finviz_targets.fetch_one", return_value=None) as m:
        out, _ = fetch_targets(["XYZ"], cache_dir=cache_dir, config={
            "known_etfs": [], "daily_request_cap": 60, "cache_ttl_hours": 24,
        })
    assert m.call_count == 0
    assert out["XYZ"] is None


def test_fetch_targets_skips_foreign_tickers(cache_dir):
    """From live-run 2026-06-30: FINVIZ tried 0A5W.IL / 5ZM.HM
    (London / Hamburg) and burned rate budget on 404s. Filter
    non-US tickers: anything with .XX suffix or leading digit."""
    # return_value=None so cached results serialize cleanly (null envelope)
    with patch("fetch_finviz_targets.fetch_one", return_value=None) as m_fetch:
        out, stats = fetch_targets(
            ["NVDA", "0A5W.IL", "5ZM.HM", "BRK.B", "AAPL"],
            cache_dir=cache_dir,
            config={"known_etfs": [], "daily_request_cap": 60, "cache_ttl_hours": 24},
        )
    # Foreign tickers get None
    assert out["0A5W.IL"] is None
    assert out["5ZM.HM"] is None
    # BRK.B also looks foreign to the filter (`.B` = single letter), a
    # known edge case. Acceptable: don't scrape BRK.B either; add it
    # back manually if the user cares. Prefer false-negatives on
    # foreign than false-positives that trigger captchas.
    fetched_names = [call.args[0] for call in m_fetch.call_args_list]
    assert "0A5W.IL" not in fetched_names
    assert "5ZM.HM" not in fetched_names
    assert "BRK.B" not in fetched_names
    assert "NVDA" in fetched_names
    assert "AAPL" in fetched_names


def test_parse_works_with_html_parser_fallback():
    """From live-run 2026-06-30: pipeline crashed when lxml wasn't
    installed. public_fetcher.parse_quote_page must fall back to
    html.parser (stdlib) so the FINVIZ step degrades gracefully."""
    from unittest.mock import patch as _patch
    import public_fetcher as _pf
    html = (FIXTURES / "sample_quote_nvda.html").read_text()

    # Simulate lxml missing by making the first BeautifulSoup() call
    # raise, forcing the fallback branch.
    original_bs = _pf.BeautifulSoup if hasattr(_pf, "BeautifulSoup") else None
    call_count = [0]
    real_bs = None
    try:
        from bs4 import BeautifulSoup as real_bs
    except ImportError:
        pytest.skip("beautifulsoup4 not installed")

    def _mock_bs(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1 and kwargs.get("features") is None and (len(args) > 1 and args[1] == "lxml"):
            raise Exception("Couldn't find lxml")
        # Second call (or fallback): use html.parser
        return real_bs(args[0], "html.parser")

    with _patch("bs4.BeautifulSoup", side_effect=_mock_bs):
        # Should not raise — falls back to html.parser
        out = _pf.parse_quote_page(html, "NVDA")
    assert out is not None
    assert out["target_price"] == 318.50


def test_fetch_targets_returns_every_requested_ticker_as_key(cache_dir):
    """Output dict MUST contain every requested ticker — value is None
    for ETFs / blocked / no-data, but the key is always there. This is
    what the briefing post-pass expects."""
    with patch("fetch_finviz_targets.fetch_one", return_value=None):
        out, _ = fetch_targets(
            ["AAPL", "MSFT", "SPY"],
            cache_dir=cache_dir,
            config={"known_etfs": ["SPY"], "daily_request_cap": 60, "cache_ttl_hours": 24},
        )
    assert set(out.keys()) == {"AAPL", "MSFT", "SPY"}
    assert out["SPY"] is None
