"""Task #45 — claude-portfolio-fetcher.

Pins: flight-data parsing (escaped JSON in the landing page), sanity-bound
rejection (never partial fabrication), the manual-seed fallback path with
staleness flag, the cache path, and the honest-empty payload when no source
is reachable.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_claude_portfolio as fcp  # noqa: E402


FLIGHT_SNIPPET = (
    'self.__next_f.push([1,"...'
    '{\\"assetKey\\":123,\\"name\\":\\"SERVICENOW, INC.\\",\\"symbol\\":\\"NOW\\",'
    '\\"pictureUrl\\":\\"https://x/NOW.png\\",\\"percentOfPortfolio\\":0.0909},'
    '{\\"assetKey\\":2057,\\"name\\":\\"DHT HOLDINGS, INC.\\",\\"symbol\\":\\"DHT\\",'
    '\\"pictureUrl\\":\\"https://x/DHT.png\\",\\"percentOfPortfolio\\":0.0481},'
    '{\\"assetKey\\":9,\\"name\\":\\"iShares 0-3 Month Treasury Bond ETF\\",'
    '\\"symbol\\":\\"SGOV\\",\\"pictureUrl\\":\\"https://x/SGOV.png\\",'
    '\\"percentOfPortfolio\\":0.8500}"])'
)


def test_parse_flight_data_holdings():
    holdings = fcp.parse_holdings(FLIGHT_SNIPPET)
    assert [h["ticker"] for h in holdings] == ["SGOV", "NOW", "DHT"]  # by weight
    now = next(h for h in holdings if h["ticker"] == "NOW")
    assert now["weight_pct"] == 9.09
    assert now["name"] == "SERVICENOW, INC."


def test_parse_rejects_out_of_bounds_weights():
    """A parse whose weights don't sum near 100% is a FAILED parse (wrong
    blob / layout change) — [] out, never partial truth."""
    snippet = ('{\\"assetKey\\":1,\\"name\\":\\"X\\",\\"symbol\\":\\"XX\\",'
               '\\"pictureUrl\\":\\"u\\",\\"percentOfPortfolio\\":0.02}')
    assert fcp.parse_holdings(snippet) == []


def test_parse_empty_html_gives_empty():
    assert fcp.parse_holdings("") == []
    assert fcp.parse_holdings("<html>no holdings here</html>") == []


def test_parse_json_body_shape():
    body = json.dumps([
        {"symbol": "NOW", "name": "ServiceNow", "percentOfPortfolio": 0.60},
        {"symbol": "MU", "name": "Micron", "percentOfPortfolio": 0.40},
    ])
    holdings = fcp.parse_holdings(body)
    assert [h["ticker"] for h in holdings] == ["NOW", "MU"]
    assert holdings[0]["weight_pct"] == 60.0


def test_manual_seed_fallback(tmp_path, monkeypatch):
    """Live fetch failing → manual_seed provenance, tickers from the YAML,
    nothing fabricated."""
    seed = tmp_path / "manual.yaml"
    seed.write_text(
        "as_of: '2026-08-01'\nholdings:\n"
        "  - {ticker: NOW, weight_pct: 9.1}\n"
        "  - {ticker: mu, weight_pct: 5.7}\n")
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        f"source_url: 'https://127.0.0.1:1/none'\ntimeout_sec: 1\n"
        f"cache_path: '{tmp_path / 'cache.json'}'\n"
        f"manual_seed_path: '{seed}'\n")
    monkeypatch.setattr(fcp, "fetch_live",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("down")))
    payload = fcp.fetch_claude_portfolio(cfg_file, force_refresh=True)
    assert payload["provenance"] == "manual_seed"
    assert payload["tickers"] == ["NOW", "MU"]
    assert payload["as_of"] == "2026-08-01"


def test_manual_seed_staleness_flagged(tmp_path, monkeypatch):
    seed = tmp_path / "manual.yaml"
    seed.write_text("as_of: '2025-01-01'\nholdings:\n"
                    "  - {ticker: NOW, weight_pct: 9.1}\n")
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        f"cache_path: '{tmp_path / 'cache.json'}'\n"
        f"manual_seed_path: '{seed}'\nmanual_seed_max_age_days: 45\n")
    monkeypatch.setattr(fcp, "fetch_live",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("down")))
    payload = fcp.fetch_claude_portfolio(cfg_file, force_refresh=True)
    assert payload["provenance"] == "manual_seed"
    assert "refresh" in payload.get("stale_warning", "")


def test_unavailable_is_honest_empty(tmp_path, monkeypatch):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        f"cache_path: '{tmp_path / 'cache.json'}'\n"
        f"manual_seed_path: '{tmp_path / 'missing.yaml'}'\n")
    monkeypatch.setattr(fcp, "fetch_live",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("down")))
    payload = fcp.fetch_claude_portfolio(cfg_file, force_refresh=True)
    assert payload["provenance"] == "unavailable"
    assert payload["holdings"] == [] and payload["tickers"] == []


def test_live_fetch_caches_and_cache_is_served(tmp_path, monkeypatch):
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(f"cache_path: '{tmp_path / 'cache.json'}'\n"
                        f"manual_seed_path: '{tmp_path / 'missing.yaml'}'\n")
    monkeypatch.setattr(fcp, "fetch_live", lambda *_a, **_k: FLIGHT_SNIPPET)
    p1 = fcp.fetch_claude_portfolio(cfg_file, force_refresh=True)
    assert p1["provenance"] == "live_fetch"
    assert (tmp_path / "cache.json").exists()
    # Second call (no refresh, live now down) serves the cache.
    monkeypatch.setattr(fcp, "fetch_live",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("down")))
    p2 = fcp.fetch_claude_portfolio(cfg_file)
    assert p2["provenance"] == "cache"
    assert p2["tickers"] == p1["tickers"]
