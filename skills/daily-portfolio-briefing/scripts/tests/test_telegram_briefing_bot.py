"""Telegram briefing daemon — pure helpers, state, and BriefingBot routing."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import telegram_briefing_bot as tb  # noqa: E402


def test_extract_verifier_accepts_5_char_alnum():
    assert tb.extract_verifier("ABC12") == "ABC12"
    assert tb.extract_verifier("  abc12  ") == "abc12"


def test_extract_verifier_rejects_non_codes():
    assert tb.extract_verifier("run") is None
    assert tb.extract_verifier("ABC123") is None
    assert tb.extract_verifier("AB!2X") is None
    assert tb.extract_verifier("") is None


def test_is_authorized_matches_allowed_id():
    upd = {"message": {"from": {"id": 5244308999}, "text": "hi"}}
    assert tb.is_authorized(upd, 5244308999) is True
    assert tb.is_authorized(upd, 111) is False
    assert tb.is_authorized({}, 5244308999) is False


def test_extract_summary_pulls_action_list_section():
    md = (
        "# Daily Briefing\n\n"
        "## Market Context\nblah\n\n"
        "## Today's Action List — Thu\n- do X\n- do Y\n\n"
        "## Watch / Portfolio Review\nstuff\n"
    )
    out = tb.extract_summary(md)
    assert "Today's Action List" in out
    assert "do X" in out and "do Y" in out
    assert "Watch / Portfolio Review" not in out


def test_extract_summary_falls_back_when_no_header():
    md = "# Daily Briefing\n\nNo action header here at all.\n"
    out = tb.extract_summary(md)
    assert out.startswith("# Daily Briefing")


def test_compute_next_fire_same_day_when_before():
    now = datetime(2026, 5, 28, 5, 0, 0)
    assert tb.compute_next_fire(now, 6, 30) == datetime(2026, 5, 28, 6, 30, 0)


def test_compute_next_fire_rolls_to_tomorrow_when_past():
    now = datetime(2026, 5, 28, 7, 0, 0)
    assert tb.compute_next_fire(now, 6, 30) == datetime(2026, 5, 29, 6, 30, 0)


def test_compute_next_fire_returns_same_time_at_exact_equality():
    now = datetime(2026, 5, 28, 6, 30, 0)
    assert tb.compute_next_fire(now, 6, 30) == datetime(2026, 5, 28, 6, 30, 0)


def test_load_state_defaults_when_missing(tmp_path):
    state = tb.load_state(tmp_path / "nope.json")
    assert state == {"update_offset": 0, "last_fire_date": None}


def test_save_then_load_roundtrips(tmp_path):
    p = tmp_path / "state.json"
    tb.save_state(p, {"update_offset": 42, "last_fire_date": "2026-05-28"})
    assert tb.load_state(p) == {"update_offset": 42, "last_fire_date": "2026-05-28"}


def test_load_state_recovers_from_corrupt(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{ not json")
    assert tb.load_state(p) == {"update_offset": 0, "last_fire_date": None}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self):
        self.calls = []
        self.next_payload = {"ok": True, "result": []}

    def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        return _FakeResp(self.next_payload)

    def post(self, url, data=None, files=None, timeout=None):
        self.calls.append(("POST", url, data, bool(files)))
        return _FakeResp({"ok": True, "result": {}})


def test_client_get_updates_passes_offset():
    sess = _FakeSession()
    sess.next_payload = {"ok": True, "result": [{"update_id": 7}]}
    client = tb.TelegramClient("TOK", session=sess)

    result = client.get_updates(offset=5)

    assert result == [{"update_id": 7}]
    method, url, params = sess.calls[0]
    assert method == "GET" and url.endswith("/getUpdates")
    assert params["offset"] == 5


def test_client_send_message_posts_text():
    sess = _FakeSession()
    client = tb.TelegramClient("TOK", session=sess)
    client.send_message(123, "hello")
    method, url, data, has_files = sess.calls[0]
    assert method == "POST" and url.endswith("/sendMessage")
    assert data["chat_id"] == 123 and data["text"] == "hello"


class FakeTelegram:
    def __init__(self):
        self.messages = []   # (chat_id, text)
        self.documents = []  # (chat_id, path, caption)

    def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    def send_document(self, chat_id, path, caption=""):
        self.documents.append((chat_id, path, caption))


class FakeAuth:
    def __init__(self, renew_ok=True):
        self.renew_ok = renew_ok
        self.saved = {"oauth_token": "T", "oauth_secret": "S"}
        self.begun = 0
        self.completed = []
        self.complete_raises = False

    def load_tokens(self):
        return self.saved

    def renew_tokens(self, tok, sec):
        return self.renew_ok

    def begin_interactive(self):
        self.begun += 1
        return ("https://authorize.example/url", object())

    def complete_interactive(self, handle, code, sandbox=False):
        if self.complete_raises:
            raise RuntimeError("bad verifier")
        self.completed.append(code)
        return "SESSION"


def _bot(tmp_path, tg, auth, runner=None):
    return tb.BriefingBot(
        tg=tg,
        auth=auth,
        runner=runner or (lambda: (0, str(tmp_path / "latest.md"), "")),
        allowed_id=999,
        state_path=tmp_path / "state.json",
        fire_hour=6,
        fire_minute=30,
    )


def test_ensure_token_ready_when_renew_succeeds(tmp_path):
    bot = _bot(tmp_path, FakeTelegram(), FakeAuth(renew_ok=True))
    assert bot.ensure_token() == "ready"


def test_ensure_token_need_auth_when_renew_fails(tmp_path):
    bot = _bot(tmp_path, FakeTelegram(), FakeAuth(renew_ok=False))
    assert bot.ensure_token() == "need_auth"


def test_start_auth_sends_link_and_sets_awaiting(tmp_path):
    tg = FakeTelegram()
    auth = FakeAuth()
    bot = _bot(tmp_path, tg, auth)
    bot.start_auth(999)
    assert bot.awaiting_verifier is True
    assert auth.begun == 1
    assert "authorize.example" in tg.messages[-1][1]


def test_run_and_deliver_success_sends_summary_and_document(tmp_path):
    briefing = tmp_path / "latest.md"
    briefing.write_text(
        "# Daily Briefing\n\n## Today's Action List — Thu\n- buy X\n\n## Watch\nx\n"
    )
    tg = FakeTelegram()
    runner = lambda: (0, str(briefing), "")
    bot = _bot(tmp_path, tg, FakeAuth(), runner=runner)

    bot.run_and_deliver(999)

    assert any("Running your briefing" in m[1] for m in tg.messages)
    assert any("buy X" in m[1] for m in tg.messages)
    assert tg.documents and tg.documents[-1][1] == str(briefing)
    assert bot.busy is False


def test_run_and_deliver_failure_sends_log_tail(tmp_path):
    tg = FakeTelegram()
    runner = lambda: (2, str(tmp_path / "missing.md"), "line1\nFATAL boom")
    bot = _bot(tmp_path, tg, FakeAuth(), runner=runner)

    bot.run_and_deliver(999)

    assert any("failed" in m[1] and "FATAL boom" in m[1] for m in tg.messages)
    assert tg.documents == []
    assert bot.busy is False


def test_run_and_deliver_busy_guard(tmp_path):
    tg = FakeTelegram()
    bot = _bot(tmp_path, tg, FakeAuth())
    bot.busy = True
    bot.run_and_deliver(999)
    assert any("already running" in m[1] for m in tg.messages)


def test_trigger_run_prompts_auth_when_token_dead(tmp_path):
    tg = FakeTelegram()
    auth = FakeAuth(renew_ok=False)
    bot = _bot(tmp_path, tg, auth)
    bot.trigger_run(999)
    assert bot.awaiting_verifier is True
    assert auth.begun == 1


def test_trigger_run_runs_when_token_ready(tmp_path):
    briefing = tmp_path / "latest.md"
    briefing.write_text("## Today's Action List — Thu\n- go\n")
    tg = FakeTelegram()
    bot = _bot(tmp_path, tg, FakeAuth(renew_ok=True), runner=lambda: (0, str(briefing), ""))
    bot.trigger_run(999)
    assert tg.documents  # delivered, no auth prompt
    assert bot.awaiting_verifier is False


def test_complete_auth_runs_after_valid_code(tmp_path):
    briefing = tmp_path / "latest.md"
    briefing.write_text("## Today's Action List — Thu\n- go\n")
    tg = FakeTelegram()
    auth = FakeAuth()
    bot = _bot(tmp_path, tg, auth, runner=lambda: (0, str(briefing), ""))
    bot.start_auth(999)
    bot._complete_auth(999, "ABC12")
    assert auth.completed == ["ABC12"]
    assert bot.awaiting_verifier is False
    assert tg.documents


def test_complete_auth_bad_code_stays_awaiting(tmp_path):
    tg = FakeTelegram()
    auth = FakeAuth()
    auth.complete_raises = True
    bot = _bot(tmp_path, tg, auth)
    bot.start_auth(999)
    bot._complete_auth(999, "BADXX")
    assert bot.awaiting_verifier is True
    assert any("didn't work" in m[1] for m in tg.messages)


def test_complete_auth_no_handle_resets_session(tmp_path):
    tg = FakeTelegram()
    auth = FakeAuth()
    bot = _bot(tmp_path, tg, auth)
    bot.awaiting_verifier = True       # simulate restart mid-auth
    bot.oauth_handle = None
    bot._complete_auth(999, "ABC12")
    assert any("reset" in m[1].lower() for m in tg.messages)
    assert auth.begun == 1             # fresh link issued
