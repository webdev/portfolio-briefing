# Telegram Briefing Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an always-on Python daemon that prompts for the E*TRADE verifier code on Telegram, runs the daily briefing at 6:30 local time and on demand, and delivers a summary plus the full briefing file to the phone.

**Architecture:** A single daemon (`scripts/telegram_briefing_bot.py`) long-polls the Telegram Bot API with `requests`, reuses `etrade_auth.py` in-process for the OAuth handshake, and shells out to the existing `run_briefing_scheduled.sh`. Pure logic lives in a dependency-injected `BriefingBot` class (Telegram client, auth module, and runner are injected so they can be faked in tests). It runs under a launchd `KeepAlive` LaunchAgent; the 6:30 trigger is internal to the daemon loop.

**Tech Stack:** Python 3, `requests` (already a repo dep), `pyetrade` (via `etrade_auth`), `pytest`, launchd.

**Spec:** `docs/superpowers/specs/2026-05-28-telegram-briefing-bot-design.md`

---

## File Structure

- **Create** `scripts/telegram_briefing_bot.py` — daemon: `TelegramClient`, pure helpers (`extract_verifier`, `is_authorized`, `extract_summary`, `compute_next_fire`), `load_state`/`save_state`, `run_briefing` runner, `BriefingBot` class, `main()`.
- **Modify** `scripts/etrade_auth.py` — split the interactive OAuth flow into `begin_interactive()` / `complete_interactive()`; reimplement `authenticate_interactive()` on top of them.
- **Create** `scripts/tests/test_etrade_auth_split.py` — tests for the OAuth split.
- **Create** `scripts/tests/test_telegram_briefing_bot.py` — tests for helpers, state, and `BriefingBot`.
- **Create** `assets/launchd/com.portfolio-briefing.telegram-bot.plist` — KeepAlive LaunchAgent.
- **Create** `docs/telegram-bot-setup.md` — install/manual-test runbook.

Test convention (from existing tests): each test file starts with
```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```
then imports modules directly (e.g. `from telegram_briefing_bot import BriefingBot`). Run tests from `skills/daily-portfolio-briefing/`.

---

## Task 1: Split the E*TRADE interactive OAuth flow

**Files:**
- Modify: `scripts/etrade_auth.py` (the `authenticate_interactive` function, ~lines 224-260)
- Test: `scripts/tests/test_etrade_auth_split.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_etrade_auth_split.py
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
    # Avoid building real pyetrade clients during the test.
    monkeypatch.setattr(etrade_auth, "_build_clients", lambda *a, **k: "SESSION")

    handle = _FakeOAuth("CK", "CS")
    result = etrade_auth.complete_interactive(handle, "ABC12", sandbox=False)

    assert handle.verifier == "ABC12"
    assert result == "SESSION"
    saved = etrade_auth.load_tokens()
    assert saved["oauth_token"] == "ACCESS"
    assert saved["oauth_secret"] == "SECRET"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/tests/test_etrade_auth_split.py -v`
Expected: FAIL — `AttributeError: module 'etrade_auth' has no attribute 'begin_interactive'`.

- [ ] **Step 3: Implement the split**

In `scripts/etrade_auth.py`, add these two functions immediately above `authenticate_interactive`:

```python
def begin_interactive(sandbox: bool = False):
    """Start the OAuth flow: return (authorize_url, oauth_handle).

    The caller keeps oauth_handle and passes it to complete_interactive once the
    user supplies the verifier code. Used by the Telegram daemon, which sends the
    URL in one message and receives the code in another.
    """
    ck, cs = _consumer_credentials()
    oauth = pyetrade.ETradeOAuth(ck, cs)
    authorize_url = oauth.get_request_token()
    return authorize_url, oauth


def complete_interactive(oauth_handle, verifier: str, sandbox: bool = False) -> ETradeSession:
    """Exchange the verifier for access tokens, persist them, return a session."""
    tokens = oauth_handle.get_access_token(verifier)
    oauth_token = tokens["oauth_token"]
    oauth_secret = tokens["oauth_token_secret"]
    save_tokens(oauth_token, oauth_secret, sandbox)
    return _build_clients(oauth_token, oauth_secret, sandbox)
```

Then replace the body of `authenticate_interactive` so it reuses them (keeps the CLI path working):

```python
def authenticate_interactive(sandbox: bool = False) -> ETradeSession:
    """Run the one-time browser OAuth flow (terminal/CLI path)."""
    authorize_url, oauth = begin_interactive(sandbox=sandbox)

    print("\n=== E*TRADE OAuth ===")
    print("1. A browser window will open to E*TRADE's authorization page.")
    print("2. Log in, accept the app, and you'll get a 5-character verifier code.")
    print("3. Paste the verifier code below.")
    print(f"\nAuthorization URL:\n  {authorize_url}\n")

    try:
        webbrowser.open(authorize_url)
    except Exception:
        print("(Could not open browser automatically — copy the URL above.)")

    verifier = input("Verifier code: ").strip()
    if not verifier:
        raise RuntimeError("No verifier code provided.")

    return complete_interactive(oauth, verifier, sandbox=sandbox)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/tests/test_etrade_auth_split.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/etrade_auth.py scripts/tests/test_etrade_auth_split.py
git commit -m "Split E*TRADE interactive OAuth into begin/complete steps"
```

---

## Task 2: Pure helpers (verifier, authorization, summary, schedule)

**Files:**
- Create: `scripts/telegram_briefing_bot.py`
- Test: `scripts/tests/test_telegram_briefing_bot.py`

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_telegram_briefing_bot.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'telegram_briefing_bot'`.

- [ ] **Step 3: Create the module with the helpers**

```python
# scripts/telegram_briefing_bot.py
"""Always-on Telegram daemon for the daily portfolio briefing.

Prompts for the E*TRADE verifier code over Telegram, runs the briefing at
6:30 local + on demand, and delivers a summary plus the full briefing file.

Single user, single dedicated bot. See
docs/superpowers/specs/2026-05-28-telegram-briefing-bot-design.md.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

_VERIFIER_RE = re.compile(r"^[A-Za-z0-9]{5}$")


def extract_verifier(text: str | None) -> str | None:
    """Return the 5-char alphanumeric verifier code, or None if not a code."""
    t = (text or "").strip()
    return t if _VERIFIER_RE.match(t) else None


def is_authorized(update: dict, allowed_id) -> bool:
    """True only if the update's sender matches the configured allowed user id."""
    frm = (update.get("message") or {}).get("from") or {}
    return str(frm.get("id")) == str(allowed_id)


def extract_summary(md: str, limit: int = 3500) -> str:
    """Pull the 'Today's Action List' section; fall back to the first 1500 chars."""
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and "Action List" in line:
            start = i
            break
    if start is None:
        return md[:1500].strip()
    out = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out).strip()[:limit]


def compute_next_fire(now: datetime, hour: int = 6, minute: int = 30) -> datetime:
    """Next occurrence of hour:minute at or after `now`."""
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/telegram_briefing_bot.py scripts/tests/test_telegram_briefing_bot.py
git commit -m "Add Telegram briefing bot pure helpers"
```

---

## Task 3: State persistence (offset + last fire date)

**Files:**
- Modify: `scripts/telegram_briefing_bot.py`
- Test: `scripts/tests/test_telegram_briefing_bot.py`

- [ ] **Step 1: Write the failing test (append to the test file)**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k state -v`
Expected: FAIL — `AttributeError: module 'telegram_briefing_bot' has no attribute 'load_state'`.

- [ ] **Step 3: Implement state functions (append to module, after `compute_next_fire`)**

```python
def load_state(path) -> dict:
    """Load persisted offset + last fire date; return defaults if missing/corrupt."""
    try:
        data = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"update_offset": 0, "last_fire_date": None}
    return {
        "update_offset": int(data.get("update_offset", 0)),
        "last_fire_date": data.get("last_fire_date"),
    }


def save_state(path, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k state -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/telegram_briefing_bot.py scripts/tests/test_telegram_briefing_bot.py
git commit -m "Add Telegram bot state persistence"
```

---

## Task 4: Telegram API client

**Files:**
- Modify: `scripts/telegram_briefing_bot.py`
- Test: `scripts/tests/test_telegram_briefing_bot.py`

- [ ] **Step 1: Write the failing test (append)**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k client -v`
Expected: FAIL — `AttributeError: module 'telegram_briefing_bot' has no attribute 'TelegramClient'`.

- [ ] **Step 3: Implement the client (append to module). Add `import requests` to the top imports.**

```python
class TelegramClient:
    """Thin wrapper over the Telegram Bot API (long-poll + send)."""

    def __init__(self, token: str, session=None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.session = session or requests.Session()

    def get_updates(self, offset: int, timeout: int = 30):
        r = self.session.get(
            f"{self.base}/getUpdates",
            params={"offset": offset, "timeout": timeout},
            timeout=timeout + 15,
        )
        r.raise_for_status()
        return r.json().get("result", [])

    def send_message(self, chat_id, text: str):
        r = self.session.post(
            f"{self.base}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def send_document(self, chat_id, path: str, caption: str = ""):
        with open(path, "rb") as fh:
            r = self.session.post(
                f"{self.base}/sendDocument",
                data={"chat_id": chat_id, "caption": caption},
                files={"document": fh},
                timeout=60,
            )
        r.raise_for_status()
        return r.json()
```

Add to the import block at the top of the file:

```python
import requests
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k client -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/telegram_briefing_bot.py scripts/tests/test_telegram_briefing_bot.py
git commit -m "Add Telegram API client wrapper"
```

---

## Task 5: BriefingBot — construction, ensure_token, auth prompt

**Files:**
- Modify: `scripts/telegram_briefing_bot.py`
- Test: `scripts/tests/test_telegram_briefing_bot.py`

- [ ] **Step 1: Write the failing test (append). These fakes are reused by later tasks.**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k "ensure_token or start_auth" -v`
Expected: FAIL — `AttributeError: module 'telegram_briefing_bot' has no attribute 'BriefingBot'`.

- [ ] **Step 3: Implement the class skeleton (append to module)**

```python
class BriefingBot:
    """Routes Telegram updates and the 6:30 trigger through one run path."""

    def __init__(self, tg, auth, runner, allowed_id, state_path,
                 fire_hour=6, fire_minute=30):
        self.tg = tg
        self.auth = auth
        self.runner = runner
        self.allowed_id = allowed_id
        self.state_path = state_path
        self.fire_hour = fire_hour
        self.fire_minute = fire_minute

        self.state = load_state(state_path)
        self.oauth_handle = None
        self.awaiting_verifier = False
        self.pending_chat_id = None
        self.busy = False
        self.prompt_time = None
        self.reminded = False

    def ensure_token(self) -> str:
        saved = self.auth.load_tokens()
        if saved and self.auth.renew_tokens(saved["oauth_token"], saved["oauth_secret"]):
            return "ready"
        return "need_auth"

    def start_auth(self, chat_id):
        url, handle = self.auth.begin_interactive()
        self.oauth_handle = handle
        self.awaiting_verifier = True
        self.pending_chat_id = chat_id
        self.prompt_time = datetime.now()
        self.reminded = False
        self.tg.send_message(
            chat_id,
            "🔐 E*TRADE token expired. Tap to authorize, then send me the "
            f"5-char code:\n{url}",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k "ensure_token or start_auth" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/telegram_briefing_bot.py scripts/tests/test_telegram_briefing_bot.py
git commit -m "Add BriefingBot construction, ensure_token, and auth prompt"
```

---

## Task 6: BriefingBot — run_and_deliver (summary, document, log tail, busy guard)

**Files:**
- Modify: `scripts/telegram_briefing_bot.py`
- Test: `scripts/tests/test_telegram_briefing_bot.py`

- [ ] **Step 1: Write the failing test (append)**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k run_and_deliver -v`
Expected: FAIL — `AttributeError: 'BriefingBot' object has no attribute 'run_and_deliver'`.

- [ ] **Step 3: Implement run_and_deliver (append as a method of BriefingBot)**

```python
    def run_and_deliver(self, chat_id):
        if self.busy:
            self.tg.send_message(chat_id, "⏳ already running, hang tight")
            return
        self.busy = True
        try:
            self.tg.send_message(chat_id, "⏳ Running your briefing…")
            exit_code, briefing_path, log_tail = self.runner()
            if exit_code == 0 and briefing_path and Path(briefing_path).exists():
                md = Path(briefing_path).read_text()
                self.tg.send_message(chat_id, extract_summary(md))
                self.tg.send_document(chat_id, briefing_path, caption="Full briefing")
            else:
                self.tg.send_message(
                    chat_id,
                    f"⚠️ briefing failed (exit {exit_code}):\n{log_tail}",
                )
        finally:
            self.busy = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k run_and_deliver -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/telegram_briefing_bot.py scripts/tests/test_telegram_briefing_bot.py
git commit -m "Add BriefingBot run_and_deliver with summary, document, and log-tail"
```

---

## Task 7: BriefingBot — trigger_run, verifier completion, crash recovery

**Files:**
- Modify: `scripts/telegram_briefing_bot.py`
- Test: `scripts/tests/test_telegram_briefing_bot.py`

- [ ] **Step 1: Write the failing test (append)**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k "trigger_run or complete_auth" -v`
Expected: FAIL — `AttributeError: 'BriefingBot' object has no attribute 'trigger_run'`.

- [ ] **Step 3: Implement trigger_run and _complete_auth (append as BriefingBot methods)**

```python
    def trigger_run(self, chat_id):
        if self.busy:
            self.tg.send_message(chat_id, "⏳ already running, hang tight")
            return
        if self.ensure_token() == "need_auth":
            self.start_auth(chat_id)
            return
        self.run_and_deliver(chat_id)

    def _complete_auth(self, chat_id, code):
        if self.oauth_handle is None:
            self.tg.send_message(chat_id, "Session reset — here's a fresh link:")
            self.start_auth(chat_id)
            return
        try:
            self.auth.complete_interactive(self.oauth_handle, code)
        except Exception:
            self.tg.send_message(
                chat_id,
                "❌ that didn't work, re-tap the link and resend the code",
            )
            return
        self.awaiting_verifier = False
        self.oauth_handle = None
        self.run_and_deliver(chat_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k "trigger_run or complete_auth" -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/telegram_briefing_bot.py scripts/tests/test_telegram_briefing_bot.py
git commit -m "Add BriefingBot trigger_run, verifier completion, and crash recovery"
```

---

## Task 8: BriefingBot — handle_update routing

**Files:**
- Modify: `scripts/telegram_briefing_bot.py`
- Test: `scripts/tests/test_telegram_briefing_bot.py`

- [ ] **Step 1: Write the failing test (append)**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k handle_update -v`
Expected: FAIL — `AttributeError: 'BriefingBot' object has no attribute 'handle_update'`.

- [ ] **Step 3: Implement handle_update (append as a BriefingBot method)**

```python
    def handle_update(self, update):
        if not is_authorized(update, self.allowed_id):
            return
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()

        if self.awaiting_verifier:
            code = extract_verifier(text)
            if code:
                self._complete_auth(chat_id, code)
                return

        if text.lower() in ("run", "briefing", "/run", "/briefing"):
            self.trigger_run(chat_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k handle_update -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/telegram_briefing_bot.py scripts/tests/test_telegram_briefing_bot.py
git commit -m "Add BriefingBot handle_update routing"
```

---

## Task 9: BriefingBot — tick (scheduled fire + reminder)

**Files:**
- Modify: `scripts/telegram_briefing_bot.py`
- Test: `scripts/tests/test_telegram_briefing_bot.py`

- [ ] **Step 1: Write the failing test (append)**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k tick -v`
Expected: FAIL — `AttributeError: 'BriefingBot' object has no attribute 'tick'`.

- [ ] **Step 3: Implement tick (append as a BriefingBot method)**

```python
    def tick(self, now):
        # One reminder if a verifier prompt has gone unanswered ~20 min.
        if (self.awaiting_verifier and not self.reminded
                and self.prompt_time is not None
                and now - self.prompt_time >= timedelta(minutes=20)):
            self.reminded = True
            self.tg.send_message(
                self.pending_chat_id,
                "⏰ still waiting on your E*TRADE code — re-tap the link above if needed.",
            )
            return

        today = now.date().isoformat()
        fire_today = now.replace(hour=self.fire_hour, minute=self.fire_minute,
                                 second=0, microsecond=0)
        if (now >= fire_today
                and self.state.get("last_fire_date") != today
                and not self.busy
                and not self.awaiting_verifier):
            self.state["last_fire_date"] = today
            save_state(self.state_path, self.state)
            self.trigger_run(self.allowed_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k tick -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/telegram_briefing_bot.py scripts/tests/test_telegram_briefing_bot.py
git commit -m "Add BriefingBot scheduled tick and verifier reminder"
```

---

## Task 10: Runner — shell out to run_briefing_scheduled.sh

**Files:**
- Modify: `scripts/telegram_briefing_bot.py`
- Test: `scripts/tests/test_telegram_briefing_bot.py`

- [ ] **Step 1: Write the failing test (append)**

```python
def test_run_briefing_returns_exit_path_and_log_tail(tmp_path, monkeypatch):
    delivery = tmp_path / "deliv"
    logs = delivery / "logs"
    logs.mkdir(parents=True)
    today = datetime.now().strftime("%Y-%m-%d")
    (logs / f"briefing_{today}.log").write_text("\n".join(f"l{i}" for i in range(30)))

    class _Proc:
        returncode = 0

    monkeypatch.setattr(tb.subprocess, "run", lambda *a, **k: _Proc())

    code, path, tail = tb.run_briefing(
        repo_root="/repo", delivery_dir=str(delivery), log_dir=str(logs)
    )

    assert code == 0
    assert path == str(delivery / "latest.md")
    assert tail.count("\n") == 19          # last 20 lines
    assert "l29" in tail and "l0" not in tail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k run_briefing_returns -v`
Expected: FAIL — `AttributeError: module 'telegram_briefing_bot' has no attribute 'run_briefing'`.

- [ ] **Step 3: Implement run_briefing (append to module, after the helpers)**

```python
def run_briefing(repo_root, delivery_dir, log_dir):
    """Run the scheduled briefing; return (exit_code, briefing_path, log_tail)."""
    script = Path(repo_root) / "skills" / "daily-portfolio-briefing" / "scripts" / "run_briefing_scheduled.sh"
    proc = subprocess.run(["bash", str(script)])
    briefing_path = str(Path(delivery_dir) / "latest.md")
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = Path(log_dir) / f"briefing_{today}.log"
    log_tail = ""
    if log_file.exists():
        log_tail = "\n".join(log_file.read_text().splitlines()[-20:])
    return proc.returncode, briefing_path, log_tail
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k run_briefing_returns -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add scripts/telegram_briefing_bot.py scripts/tests/test_telegram_briefing_bot.py
git commit -m "Add run_briefing subprocess runner"
```

---

## Task 11: main() — env loading, single-instance lock, poll loop

**Files:**
- Modify: `scripts/telegram_briefing_bot.py`
- Test: `scripts/tests/test_telegram_briefing_bot.py`

- [ ] **Step 1: Write the failing test (append). Only the testable pieces — env config and the lock — are unit-tested; the infinite loop is covered by manual E2E.**

```python
def test_load_config_reads_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BRIEFING_BOT_TOKEN", "TOK123")
    monkeypatch.setenv("TELEGRAM_BRIEFING_ALLOWED_ID", "999")
    cfg = tb.load_config()
    assert cfg["token"] == "TOK123"
    assert cfg["allowed_id"] == "999"


def test_load_config_raises_without_token(monkeypatch):
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -k "load_config or acquire_lock" -v`
Expected: FAIL — `AttributeError: module 'telegram_briefing_bot' has no attribute 'load_config'`.

- [ ] **Step 3: Implement config, lock, and main (append to module). Add `import os` to the imports.**

```python
def load_config() -> dict:
    """Read the dedicated bot token + allowed id from env (loaded from .env)."""
    _load_dotenv()
    token = os.environ.get("TELEGRAM_BRIEFING_BOT_TOKEN", "")
    allowed_id = os.environ.get("TELEGRAM_BRIEFING_ALLOWED_ID", "")
    if not token:
        raise RuntimeError("TELEGRAM_BRIEFING_BOT_TOKEN not set (see .env)")
    if not allowed_id:
        raise RuntimeError("TELEGRAM_BRIEFING_ALLOWED_ID not set (see .env)")
    return {"token": token, "allowed_id": allowed_id}


def _load_dotenv() -> None:
    """Load the repo .env into os.environ without overriding real env vars."""
    repo = Path(os.getenv("PORTFOLIO_BRIEFING_REPO", str(Path.home() / "workspace" / "portfolio-briefing")))
    env_path = repo / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def acquire_lock(lock_path) -> bool:
    """Single-instance pidfile. True if we got the lock, False if a live holder."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text().strip())
            os.kill(pid, 0)          # raises if not alive
            return False             # a live instance holds it
        except (ValueError, ProcessLookupError, PermissionError):
            pass                     # stale or unreadable — reclaim
    lock_path.write_text(str(os.getpid()))
    return True


def main():
    cfg = load_config()
    repo = Path(os.getenv("PORTFOLIO_BRIEFING_REPO", str(Path.home() / "workspace" / "portfolio-briefing")))
    delivery_dir = os.getenv("PORTFOLIO_BRIEFING_DELIVERY_DIR", str(Path.home() / "Documents" / "briefings"))
    log_dir = str(Path(delivery_dir) / "logs")
    state_dir = repo / "skills" / "daily-portfolio-briefing" / "scripts" / "state"
    lock_path = state_dir / "telegram_bot.pid"

    if not acquire_lock(lock_path):
        print("Another telegram_briefing_bot instance is running; exiting.", file=sys.stderr)
        return 1

    import etrade_auth
    tg = TelegramClient(cfg["token"])
    bot = BriefingBot(
        tg=tg,
        auth=etrade_auth,
        runner=lambda: run_briefing(str(repo), delivery_dir, log_dir),
        allowed_id=cfg["allowed_id"],
        state_path=state_dir / "telegram_bot_state.json",
    )
    print(f"telegram briefing bot: polling, fires daily at "
          f"{bot.fire_hour:02d}:{bot.fire_minute:02d} local", file=sys.stderr)

    while True:
        try:
            updates = tg.get_updates(bot.state["update_offset"])
            for u in updates:
                bot.handle_update(u)
                bot.state["update_offset"] = u["update_id"] + 1
                save_state(bot.state_path, bot.state)
            bot.tick(datetime.now())
        except Exception as e:        # network blips etc — never die
            print(f"telegram briefing bot: loop error: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
```

Update the top import block so it reads:

```python
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add scripts/telegram_briefing_bot.py scripts/tests/test_telegram_briefing_bot.py
git commit -m "Add main loop, env config, and single-instance lock"
```

---

## Task 12: launchd LaunchAgent + setup runbook

**Files:**
- Create: `assets/launchd/com.portfolio-briefing.telegram-bot.plist`
- Create: `docs/telegram-bot-setup.md`

- [ ] **Step 1: Write the LaunchAgent plist**

Create `assets/launchd/com.portfolio-briefing.telegram-bot.plist`. Replace `USERNAME` and verify `PYTHON_BIN` matches your machine (`which python3`).

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.portfolio-briefing.telegram-bot</string>

  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/USERNAME/workspace/portfolio-briefing/skills/daily-portfolio-briefing/scripts/telegram_briefing_bot.py</string>
  </array>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PORTFOLIO_BRIEFING_REPO</key>
    <string>/Users/USERNAME/workspace/portfolio-briefing</string>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>/Users/USERNAME/Documents/briefings/logs/telegram_bot.out.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/USERNAME/Documents/briefings/logs/telegram_bot.err.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Write the setup runbook**

Create `docs/telegram-bot-setup.md`:

```markdown
# Telegram Briefing Bot — Setup & Runbook

Always-on daemon that prompts for the E*TRADE verifier on Telegram, runs the
briefing at 6:30 local + on demand, and delivers it to your phone.

## Prerequisites
- Dedicated bot created via @BotFather; token in repo `.env` as
  `TELEGRAM_BRIEFING_BOT_TOKEN`.
- `TELEGRAM_BRIEFING_ALLOWED_ID` in `.env` (your numeric Telegram user id).

## Manual smoke test (before installing the agent)
1. From `skills/daily-portfolio-briefing/`:
   `PORTFOLIO_BRIEFING_REPO=$HOME/workspace/portfolio-briefing python3 scripts/telegram_briefing_bot.py`
2. In Telegram, DM the bot `run`.
3. Confirm: a "Running…" message, then a summary, then the full `.md` attached.
4. Token-dead path: it sends an authorize link; tap it, copy the 5-char code,
   send it back; the run proceeds.
5. Ctrl-C to stop.

## Install the LaunchAgent
1. `cp assets/launchd/com.portfolio-briefing.telegram-bot.plist ~/Library/LaunchAgents/`
2. Edit the copy: replace every `USERNAME`, and set the python path to your
   `which python3` if not `/usr/bin/python3`.
3. `launchctl load ~/Library/LaunchAgents/com.portfolio-briefing.telegram-bot.plist`
4. Verify: `launchctl list | grep telegram-bot` (PID present, exit code 0).
5. Logs: `~/Documents/briefings/logs/telegram_bot.{out,err}.log`.

## Crash / restart check
- `kill <pid>` → `launchctl list | grep telegram-bot` shows a new PID (KeepAlive).

## Uninstall
- `launchctl unload ~/Library/LaunchAgents/com.portfolio-briefing.telegram-bot.plist`

## Notes
- This bot must be the ONLY consumer of its token. Do not run the Claude Code
  telegram channel on the same bot.
- At 6:30 the E*TRADE token is essentially always dead (midnight-ET expiry), so
  the morning run will prompt for a code. Midday on-demand runs usually won't
  (the hourly `renew_etrade_token.py` heartbeat keeps the token warm).
```

- [ ] **Step 3: Commit**

```bash
git add assets/launchd/com.portfolio-briefing.telegram-bot.plist docs/telegram-bot-setup.md
git commit -m "Add launchd LaunchAgent and Telegram bot setup runbook"
```

---

## Task 13: Full suite green + final commit

**Files:** none (verification)

- [ ] **Step 1: Run the new test files**

Run: `pytest scripts/tests/test_telegram_briefing_bot.py scripts/tests/test_etrade_auth_split.py -v`
Expected: PASS (all).

- [ ] **Step 2: Run the broader suite to confirm no regression in etrade_auth consumers**

Run: `pytest scripts/tests/ -q`
Expected: PASS (no new failures vs. baseline; pre-existing unrelated failures, if any, unchanged).

- [ ] **Step 3: Manual E2E** — follow `docs/telegram-bot-setup.md` "Manual smoke test," then install the LaunchAgent and confirm `launchctl list | grep telegram-bot`.

---

## Self-Review Notes

- **Spec coverage:** architecture/components (Tasks 2,4,5,12), morning+on-demand shared path (Tasks 5-8), ensure_token (Task 5), verifier handling + crash recovery (Task 7), run/deliver summary+document+log-tail (Task 6), schedule + reminder (Task 9), state/offset/last_fire (Tasks 3,9), access control (Task 8), single-instance lock (Task 11), runner over `run_briefing_scheduled.sh` (Task 10), launchd KeepAlive + runbook (Task 12), tests (every task). All spec sections mapped.
- **Type/name consistency:** `BriefingBot(tg, auth, runner, allowed_id, state_path, fire_hour, fire_minute)`; methods `ensure_token`, `start_auth`, `_complete_auth`, `trigger_run`, `run_and_deliver`, `handle_update`, `tick`; helpers `extract_verifier`, `is_authorized`, `extract_summary`, `compute_next_fire`, `load_state`, `save_state`, `run_briefing`, `load_config`, `_load_dotenv`, `acquire_lock`, `main`. Runner contract `() -> (exit_code, briefing_path, log_tail)` consistent across Tasks 6,7,10,11. `etrade_auth` surface `load_tokens`/`renew_tokens`/`begin_interactive`/`complete_interactive` consistent across Tasks 1,5,7.
- **No placeholders:** every code/test step contains complete content.
