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
        self.messages = []   # (chat_id, text, parse_mode)
        self.documents = []  # (chat_id, path, caption)

    def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append((chat_id, text, parse_mode))

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


def _msg(uid, text, update_id=1, chat_id=None):
    return {
        "update_id": update_id,
        "message": {
            "from": {"id": uid},
            "chat": {"id": chat_id if chat_id is not None else uid},
            "text": text,
        },
    }


def test_handle_update_ignores_unauthorized(tmp_path):
    tg = FakeTelegram()
    bot = _bot(tmp_path, tg, FakeAuth())
    bot.handle_update(_msg(111, "run"))   # wrong id
    assert tg.messages == [] and tg.documents == []


def test_handle_update_run_command_triggers(tmp_path):
    briefing = tmp_path / "latest.md"
    briefing.write_text("## Today's Action List — Thu\n- go\n")
    tg = FakeTelegram()
    bot = _bot(tmp_path, tg, FakeAuth(renew_ok=True), runner=lambda: (0, str(briefing), ""))
    bot.handle_update(_msg(999, "run"))
    assert tg.documents


def test_handle_update_code_while_awaiting_completes(tmp_path):
    briefing = tmp_path / "latest.md"
    briefing.write_text("## Today's Action List — Thu\n- go\n")
    tg = FakeTelegram()
    auth = FakeAuth()
    bot = _bot(tmp_path, tg, auth, runner=lambda: (0, str(briefing), ""))
    bot.awaiting_verifier = True
    bot.oauth_handle = object()
    bot.handle_update(_msg(999, "ABC12"))
    assert auth.completed == ["ABC12"]


def test_handle_update_code_when_not_awaiting_is_ignored(tmp_path):
    tg = FakeTelegram()
    auth = FakeAuth()
    bot = _bot(tmp_path, tg, auth)
    bot.handle_update(_msg(999, "ABC12"))   # not awaiting, not a command
    assert auth.completed == []
    assert tg.documents == []


def test_tick_fires_at_6_30_once_per_day(tmp_path):
    briefing = tmp_path / "latest.md"
    briefing.write_text("## Today's Action List — Thu\n- go\n")
    tg = FakeTelegram()
    bot = _bot(tmp_path, tg, FakeAuth(renew_ok=True), runner=lambda: (0, str(briefing), ""))

    bot.tick(datetime(2026, 5, 28, 6, 30, 5))
    assert tg.documents                      # fired
    assert bot.state["last_fire_date"] == "2026-05-28"

    tg.documents.clear()
    bot.tick(datetime(2026, 5, 28, 6, 31, 0))   # same day restart
    assert tg.documents == []                # no double-fire


def test_tick_does_not_fire_before_6_30(tmp_path):
    tg = FakeTelegram()
    bot = _bot(tmp_path, tg, FakeAuth(renew_ok=True))
    bot.tick(datetime(2026, 5, 28, 6, 29, 0))
    assert tg.messages == [] and bot.state["last_fire_date"] is None


def test_tick_sends_one_reminder_while_awaiting(tmp_path):
    tg = FakeTelegram()
    bot = _bot(tmp_path, tg, FakeAuth())
    bot.awaiting_verifier = True
    bot.pending_chat_id = 999
    bot.prompt_time = datetime(2026, 5, 28, 6, 30, 0)

    bot.tick(datetime(2026, 5, 28, 6, 55, 0))   # 25 min later
    reminders = [m for m in tg.messages if "waiting" in m[1].lower()]
    assert len(reminders) == 1

    bot.tick(datetime(2026, 5, 28, 7, 30, 0))   # later still
    reminders = [m for m in tg.messages if "waiting" in m[1].lower()]
    assert len(reminders) == 1                  # still only one


def test_run_briefing_returns_exit_path_and_log_tail(tmp_path, monkeypatch):
    delivery = tmp_path / "deliv"
    logs = delivery / "logs"
    logs.mkdir(parents=True)
    today = datetime.now().strftime("%Y-%m-%d")
    (logs / f"briefing_{today}.log").write_text("\n".join(f"l{i}" for i in range(30)))

    captured = {}

    class _Proc:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return _Proc()

    monkeypatch.delenv("PORTFOLIO_BRIEFING_PYTHON", raising=False)
    monkeypatch.setattr(tb.subprocess, "run", _fake_run)

    code, path, tail = tb.run_briefing(
        repo_root="/repo", delivery_dir=str(delivery), log_dir=str(logs)
    )

    assert code == 0
    assert path == str(delivery / "latest.md")
    assert tail.count("\n") == 19
    assert "l29" in tail and "l0" not in tail
    # The subprocess must run under the daemon's own interpreter, not system py3.
    assert captured["env"]["PORTFOLIO_BRIEFING_PYTHON"] == tb.sys.executable


def test_load_config_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTFOLIO_BRIEFING_REPO", str(tmp_path))  # no .env here
    monkeypatch.setenv("TELEGRAM_BRIEFING_BOT_TOKEN", "TOK123")
    monkeypatch.setenv("TELEGRAM_BRIEFING_ALLOWED_ID", "999")
    cfg = tb.load_config()
    assert cfg["token"] == "TOK123"
    assert cfg["allowed_id"] == "999"


def test_load_config_raises_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTFOLIO_BRIEFING_REPO", str(tmp_path))  # no .env here
    monkeypatch.delenv("TELEGRAM_BRIEFING_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_BRIEFING_ALLOWED_ID", "999")
    try:
        tb.load_config()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


def test_acquire_lock_blocks_second_instance(tmp_path):
    lock = tmp_path / "bot.pid"
    assert tb.acquire_lock(lock) is True       # first wins
    assert tb.acquire_lock(lock) is False      # second sees a live pid


def test_acquire_lock_reclaims_stale(tmp_path):
    lock = tmp_path / "bot.pid"
    lock.write_text("999999999")               # almost certainly dead pid
    assert tb.acquire_lock(lock) is True


def test_acquire_lock_blocks_when_kill_raises_permission_error(tmp_path, monkeypatch):
    lock = tmp_path / "bot.pid"
    lock.write_text("4242")
    monkeypatch.setattr(tb.os, "kill", lambda pid, sig: (_ for _ in ()).throw(PermissionError()))
    assert tb.acquire_lock(lock) is False


def test_escape_mdv2_escapes_specials():
    assert tb._escape_mdv2("a.b-c!") == "a\\.b\\-c\\!"
    assert tb._escape_mdv2("100%") == "100%"


def test_format_line_header_becomes_bold():
    assert tb._format_line_mdv2("## Today's Action List") == "*Today's Action List*"


def test_format_inline_bold_and_code():
    assert tb._format_line_mdv2("- **DO** `TSLA` now.") == "\\- *DO* `TSLA` now\\."


def test_build_digest_orders_three_sections():
    md = (
        "# Daily Briefing\n\n"
        "## Today's Action List\n- buy X\n\n"
        "## 🚦 Red Flags & Priorities\n- risk Y\n\n"
        "## 💰 Capital Plan\n- plan Z\n"
    )
    msgs = tb.build_digest_messages(md)
    assert len(msgs) == 3
    assert "Action List" in msgs[0] and "buy X" in msgs[0]
    assert "Red Flags" in msgs[1] and "risk Y" in msgs[1]
    assert "Capital Plan" in msgs[2] and "plan Z" in msgs[2]


def test_build_digest_skips_missing_sections():
    md = "## Today's Action List\n- only this\n"
    msgs = tb.build_digest_messages(md)
    assert len(msgs) == 1


def test_run_and_deliver_sends_markdownv2_digest(tmp_path):
    briefing = tmp_path / "latest.md"
    briefing.write_text(
        "## Today's Action List\n- buy X\n\n## 🚦 Red Flags & Priorities\n- risk Y\n"
    )
    tg = FakeTelegram()
    bot = _bot(tmp_path, tg, FakeAuth(), runner=lambda: (0, str(briefing), ""))
    bot.run_and_deliver(999)
    digest = [m for m in tg.messages if m[2] == "MarkdownV2"]
    assert len(digest) == 2
    assert tg.documents


def test_format_inline_strips_padded_bold():
    # Telegram 400s on bold entities with leading/trailing whitespace.
    assert tb._format_line_mdv2("x ** y ** z") == "x *y* z"


# ─── Webapp notification (task #46) ────────────────────────────────────────


def test_notify_webapp_returns_true_on_success(monkeypatch):
    """Task #46+#50: after briefing completes, _notify_webapp() POSTs
    and returns (True, payload_dict) with snapshot count + checksum."""
    class _MockResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return {"snapshots_loaded": 42, "checksum": "abc12345",
                    "latest_date": "2026-07-01"}

    def _mock_post(url, timeout=15):
        assert url == "http://127.0.0.1:17776/refresh/notify"
        return _MockResp()

    monkeypatch.setattr(tb.requests, "post", _mock_post)
    monkeypatch.setenv("PB_WEBAPP_URL", "http://127.0.0.1:17776")
    ok, payload = tb._notify_webapp()
    assert ok is True
    assert payload["snapshots_loaded"] == 42
    assert payload["checksum"] == "abc12345"


def test_notify_webapp_swallows_network_error(monkeypatch):
    """When the webapp is unreachable, _notify_webapp fails open —
    logs to stderr but doesn't raise. Returns (False, {'error': ...})."""
    def _mock_post(url, timeout=15):
        raise tb.requests.ConnectionError("connection refused")

    monkeypatch.setattr(tb.requests, "post", _mock_post)
    monkeypatch.setenv("PB_WEBAPP_URL", "http://127.0.0.1:17776")
    ok, payload = tb._notify_webapp()
    assert ok is False
    assert "connection refused" in payload.get("error", "")


def test_notify_webapp_respects_disabled_via_empty_url(monkeypatch):
    """Setting PB_WEBAPP_URL='' explicitly disables the notify."""
    monkeypatch.setenv("PB_WEBAPP_URL", "")
    ok, payload = tb._notify_webapp()
    assert ok is False
    assert "disabled" in payload.get("error", "")


def test_notify_webapp_uses_default_when_env_unset(monkeypatch):
    """PB_WEBAPP_URL unset → default http://127.0.0.1:17776."""
    calls = []
    class _R:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self): return {"snapshots_loaded": 1, "checksum": "xxx"}
    def _mock_post(url, timeout=15):
        calls.append(url)
        return _R()
    monkeypatch.setattr(tb.requests, "post", _mock_post)
    monkeypatch.delenv("PB_WEBAPP_URL", raising=False)
    ok, _ = tb._notify_webapp()
    assert ok is True
    assert calls[0] == "http://127.0.0.1:17776/refresh/notify"


def test_briefing_checksum_computes_stable_hash(tmp_path):
    """_briefing_checksum returns first 8 hex chars of SHA-256, stable
    across calls on the same content, changes when content changes."""
    p = tmp_path / "briefing.md"
    p.write_text("# briefing content v1\n")
    chk1 = tb._briefing_checksum(str(p))
    chk2 = tb._briefing_checksum(str(p))
    assert chk1 == chk2
    assert len(chk1) == 8
    p.write_text("# briefing content v2 — one char changed\n")
    chk3 = tb._briefing_checksum(str(p))
    assert chk3 != chk1


def test_briefing_checksum_returns_none_when_file_missing():
    """Fail-open: missing file → None, never raises."""
    assert tb._briefing_checksum("/nonexistent/path.md") is None


# ─── Fable section extractor (task #51) ────────────────────────────────────


def test_extract_fable_section_pulls_review_from_briefing():
    """The bot's follow-up Telegram message uses this to send just the
    Fable review — not the whole briefing."""
    md = """# Daily Briefing

## Actions
1. CLOSE X

## 🔍 Fable's second opinion

_Automated LLM review._

**Cross-section observations:**
- point A
- point B

_Model: opus._

## Appendix
Some other content"""
    section = tb._extract_fable_section(md)
    assert section is not None
    assert "Fable's second opinion" in section
    assert "point A" in section
    assert "Appendix" not in section    # cut at the next H2


def test_extract_fable_section_returns_none_when_absent():
    """When the review section doesn't exist (disabled, error, or
    older briefing), the extractor cleanly returns None so the bot
    skips the follow-up message."""
    assert tb._extract_fable_section("# Briefing\n\n## Actions\n- close") is None
    assert tb._extract_fable_section("") is None
    assert tb._extract_fable_section(None) is None


def test_extract_fable_section_reaches_end_of_document():
    """When Fable review IS the last section, we consume to EOF."""
    md = """# Briefing

## Actions
1. HOLD

## 🔍 Fable's second opinion

**Themes I notice:**
- Semis concentration on Sep 18
"""
    section = tb._extract_fable_section(md)
    assert section is not None
    assert "Semis concentration" in section
