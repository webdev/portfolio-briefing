# skills/daily-portfolio-briefing/scripts/tests/test_schwab_market.py
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters import schwab_market  # noqa: E402


def _sample_chain_payload():
    # Shape mirrors Schwab GET /marketdata/v1/chains (trimmed).
    return {
        "callExpDateMap": {
            "2026-06-19:22": {
                "150.0": [{
                    "strikePrice": 150.0, "bid": 5.0, "ask": 5.4, "last": 5.2,
                    "openInterest": 1200, "delta": 0.32, "gamma": 0.01,
                    "theta": -0.05, "vega": 0.10, "volatility": 28.5,
                }],
            }
        },
        "putExpDateMap": {
            "2026-06-19:22": {
                "150.0": [{
                    "strikePrice": 150.0, "bid": 4.0, "ask": 4.4, "last": 4.2,
                    "openInterest": 900, "delta": -0.30, "gamma": 0.01,
                    "theta": -0.04, "vega": 0.09, "volatility": 27.0,
                }],
            }
        },
    }


def test_parse_chain_payload_builds_rows():
    out = schwab_market._parse_chain_payload(_sample_chain_payload())
    assert len(out["call"]) == 1 and len(out["put"]) == 1
    c = out["call"][0]
    assert c.strike == 150.0 and c.option_type == "CALL"
    assert c.bid == 5.0 and c.ask == 5.4 and c.delta == 0.32
    assert c.iv == 28.5  # Schwab 'volatility' is already in percent points
    p = out["put"][0]
    assert p.option_type == "PUT" and p.delta == -0.30


def test_parse_expirations_payload():
    exps = schwab_market._parse_expirations(_sample_chain_payload())
    assert date(2026, 6, 19) in exps


class _FakeResp:
    def __init__(self, payload): self._payload = payload
    def raise_for_status(self): pass
    def json(self): return self._payload


class _FakeSession:
    def __init__(self, payload): self._payload = payload
    def get(self, url, headers=None, params=None, timeout=None):
        return _FakeResp(self._payload)


def test_get_option_chain_returns_none_without_token(monkeypatch):
    monkeypatch.setattr(schwab_market.schwab_auth, "get_access_token", lambda *a, **k: None)
    assert schwab_market.get_option_chain("AAPL", date(2026, 6, 19), 150.0,
                                          session=_FakeSession(_sample_chain_payload())) is None


def test_get_option_chain_parses_with_token(monkeypatch):
    monkeypatch.setattr(schwab_market.schwab_auth, "get_access_token", lambda *a, **k: "AT")
    chain = schwab_market.get_option_chain("AAPL", date(2026, 6, 19), 150.0,
                                           session=_FakeSession(_sample_chain_payload()))
    assert chain and chain["call"][0].strike == 150.0
