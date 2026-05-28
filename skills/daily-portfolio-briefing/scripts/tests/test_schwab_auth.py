# skills/daily-portfolio-briefing/scripts/tests/test_schwab_auth.py
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import schwab_auth  # noqa: E402


def test_extract_code_from_full_redirect_url():
    url = "https://127.0.0.1/?code=C0.abc-123%40&session=xyz"
    assert schwab_auth.extract_code(url) == "C0.abc-123@"  # URL-decoded


def test_extract_code_from_bare_code():
    assert schwab_auth.extract_code("C0.abc-123@") == "C0.abc-123@"


def test_extract_code_rejects_plain_chatter():
    assert schwab_auth.extract_code("run the briefing") is None
    assert schwab_auth.extract_code("") is None
    assert schwab_auth.extract_code(None) is None


def test_token_is_expired_uses_skew():
    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    fresh = (now + timedelta(seconds=600)).isoformat()
    near = (now + timedelta(seconds=30)).isoformat()
    assert schwab_auth._token_is_expired({"access_expires_at": fresh}, now=now) is False
    assert schwab_auth._token_is_expired({"access_expires_at": near}, now=now) is True
    assert schwab_auth._token_is_expired({}, now=now) is True


def test_parse_token_response_computes_expiry():
    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    raw = {"access_token": "AT", "refresh_token": "RT", "expires_in": 1800}
    out = schwab_auth._parse_token_response(raw, now=now)
    assert out["access_token"] == "AT"
    assert out["refresh_token"] == "RT"
    assert out["access_expires_at"] == (now + timedelta(seconds=1800)).isoformat()
    # refresh token assumed 7-day life from now
    assert out["refresh_expires_at"] == (now + timedelta(days=7)).isoformat()


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []
    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append({"url": url, "data": data})
        return _FakeResp(self._payload)


def test_complete_interactive_exchanges_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHWAB_TOKEN_FILE", str(tmp_path / "schwab_tokens.json"))
    monkeypatch.setenv("SCHWAB_APP_KEY", "KEY")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "SECRET")
    monkeypatch.setenv("SCHWAB_REDIRECT_URI", "https://127.0.0.1")
    fake = _FakeSession({"access_token": "AT", "refresh_token": "RT", "expires_in": 1800})
    out = schwab_auth.complete_interactive(None, "https://127.0.0.1/?code=ABCDEFGHIJKLMNOPQRST%40&session=z", session=fake)
    assert out["access_token"] == "AT"
    assert fake.calls[0]["data"]["grant_type"] == "authorization_code"
    assert fake.calls[0]["data"]["code"] == "ABCDEFGHIJKLMNOPQRST@"
    assert schwab_auth.load_tokens()["refresh_token"] == "RT"


def test_get_access_token_refreshes_when_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHWAB_TOKEN_FILE", str(tmp_path / "schwab_tokens.json"))
    monkeypatch.setenv("SCHWAB_APP_KEY", "KEY")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "SECRET")
    # Persist an expired access token with a still-live refresh token.
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    schwab_auth.save_tokens({
        "access_token": "OLD", "refresh_token": "RT",
        "access_expires_at": (now - timedelta(seconds=10)).isoformat(),
        "refresh_expires_at": (now + timedelta(days=6)).isoformat(),
    })
    fake = _FakeSession({"access_token": "NEW", "refresh_token": "RT2", "expires_in": 1800})
    assert schwab_auth.get_access_token(session=fake) == "NEW"
    assert fake.calls[0]["data"]["grant_type"] == "refresh_token"


def test_get_access_token_none_when_refresh_dead(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHWAB_TOKEN_FILE", str(tmp_path / "schwab_tokens.json"))
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    schwab_auth.save_tokens({
        "access_token": "OLD", "refresh_token": "RT",
        "access_expires_at": (now - timedelta(seconds=10)).isoformat(),
        "refresh_expires_at": (now - timedelta(seconds=10)).isoformat(),
    })
    assert schwab_auth.get_access_token() is None
