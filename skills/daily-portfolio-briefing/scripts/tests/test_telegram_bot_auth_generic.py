import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import etrade_auth  # noqa: E402
import telegram_briefing_bot as bot  # noqa: E402


def test_etrade_extract_code_matches_5char():
    assert etrade_auth.extract_code("ABC12") == "ABC12"
    assert etrade_auth.extract_code("not a code here") is None


class _FakeAuth:
    """Stand-in auth module exposing the generic surface."""
    def __init__(self):
        self.completed_with = None
    def token_status(self):
        return "need_auth"
    def begin_interactive(self):
        return ("https://example/authorize", "HANDLE")
    def complete_interactive(self, handle, code):
        self.completed_with = (handle, code)
    def extract_code(self, text):
        return text if text and text.startswith("CODE") else None


class _FakeTg:
    def __init__(self):
        self.sent = []
    def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append(text)
    def send_document(self, *a, **k):
        pass


def _make_bot(tmp_path):
    auth = _FakeAuth()
    b = bot.BriefingBot(
        tg=_FakeTg(), auth=auth, runner=lambda: (0, None, ""),
        allowed_id=42, state_path=tmp_path / "state.json",
    )
    return b, auth


def test_ensure_token_delegates_to_auth_module(tmp_path):
    b, _ = _make_bot(tmp_path)
    assert b.ensure_token() == "need_auth"


def test_handle_update_uses_auth_extract_code(tmp_path):
    b, auth = _make_bot(tmp_path)
    b.awaiting_verifier = True
    b.oauth_handle = "HANDLE"
    update = {"message": {"from": {"id": 42}, "chat": {"id": 42}, "text": "CODE-xyz"}}
    b.handle_update(update)
    assert auth.completed_with == ("HANDLE", "CODE-xyz")
