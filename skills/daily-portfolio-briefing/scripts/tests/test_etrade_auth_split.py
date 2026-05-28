"""begin_interactive / complete_interactive split so a long-running daemon can
send the authorize URL in one message and exchange the verifier in another."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import etrade_auth  # noqa: E402


class _FakeOAuth:
    def __init__(self, ck, cs):
        self.ck, self.cs = ck, cs
        self.verifier = None

    def get_request_token(self):
        return "https://us.etrade.com/e/t/etws/authorize?key=CK&token=REQ"

    def get_access_token(self, verifier):
        self.verifier = verifier
        return {"oauth_token": "ACCESS", "oauth_token_secret": "SECRET"}


def test_begin_returns_url_and_handle(monkeypatch):
    monkeypatch.setattr(etrade_auth, "_consumer_credentials", lambda: ("CK", "CS"))
    monkeypatch.setattr(etrade_auth.pyetrade, "ETradeOAuth", _FakeOAuth)

    url, handle = etrade_auth.begin_interactive()

    assert "authorize" in url
    assert isinstance(handle, _FakeOAuth)


def test_complete_exchanges_and_saves(monkeypatch, tmp_path):
    monkeypatch.setattr(etrade_auth, "_consumer_credentials", lambda: ("CK", "CS"))
    monkeypatch.setattr(etrade_auth, "token_file_path", lambda: tmp_path / "tok.json")
    monkeypatch.setattr(etrade_auth, "_build_clients", lambda *a, **k: "SESSION")

    handle = _FakeOAuth("CK", "CS")
    result = etrade_auth.complete_interactive(handle, "ABC12", sandbox=False)

    assert handle.verifier == "ABC12"
    assert result == "SESSION"
    saved = etrade_auth.load_tokens()
    assert saved["oauth_token"] == "ACCESS"
    assert saved["oauth_secret"] == "SECRET"
