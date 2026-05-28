"""Always-on Telegram daemon for the daily portfolio briefing.

Prompts for the E*TRADE verifier code over Telegram, runs the briefing at
6:30 local + on demand, and delivers a summary plus the full briefing file.

Single user, single dedicated bot. See
docs/superpowers/specs/2026-05-28-telegram-briefing-bot-design.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

_VERIFIER_RE = re.compile(r"^[A-Za-z0-9]{5}$")

_MDV2_SPECIALS = set(r"_*[]()~`>#+-=|{}.!")
_HEADER_RE = re.compile(r"^#{1,6}\s+(.*)$")
_INLINE_RE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")
_DIGEST_SECTIONS = ("Action List", "Red Flags", "Capital Plan")


def _escape_mdv2(text: str) -> str:
    """Escape Telegram MarkdownV2 special chars in plain text."""
    out = []
    for ch in text:
        if ch == "\\" or ch in _MDV2_SPECIALS:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _format_inline(text: str) -> str:
    """Convert **bold** and `code` spans to MarkdownV2, escaping plain runs."""
    out = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        out.append(_escape_mdv2(text[pos:m.start()]))
        if m.group(1) is not None:            # **bold**
            out.append("*" + _escape_mdv2(m.group(1).strip()) + "*")
        else:                                  # `code`
            code = m.group(2).replace("\\", "\\\\").replace("`", "\\`")
            out.append("`" + code + "`")
        pos = m.end()
    out.append(_escape_mdv2(text[pos:]))
    return "".join(out)


def _format_line_mdv2(line: str) -> str:
    """One briefing line -> MarkdownV2. Headers become bold; bodies get inline formatting."""
    h = _HEADER_RE.match(line)
    if h:
        return "*" + _escape_mdv2(h.group(1)) + "*"
    return _format_inline(line)


def _extract_section(md: str, needle: str) -> str | None:
    """Return the '## ...<needle>...' section (header through line before next '## '), or None."""
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and needle in line:
            start = i
            break
    if start is None:
        return None
    out = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out).strip()


def build_digest_messages(md: str, limit: int = 3900) -> list[str]:
    """MarkdownV2 messages for the digest sections, chunked on line boundaries under `limit`."""
    msgs = []
    for needle in _DIGEST_SECTIONS:
        section = _extract_section(md, needle)
        if not section:
            continue
        formatted_lines = [_format_line_mdv2(l) for l in section.splitlines()]
        cur = ""
        for fl in formatted_lines:
            if cur and len(cur) + 1 + len(fl) > limit:
                msgs.append(cur)
                cur = fl
            else:
                cur = fl if not cur else cur + "\n" + fl
        if cur:
            msgs.append(cur)
    return msgs


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
    section = _extract_section(md, "Action List")
    if section is None:
        return md[:1500].strip()
    return section[:limit]


def compute_next_fire(now: datetime, hour: int = 6, minute: int = 30) -> datetime:
    """Next occurrence of hour:minute at or after `now`."""
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate < now:
        candidate += timedelta(days=1)
    return candidate


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


def run_briefing(repo_root, delivery_dir, log_dir):
    """Run the scheduled briefing; return (exit_code, briefing_path, log_tail)."""
    script = Path(repo_root) / "skills" / "daily-portfolio-briefing" / "scripts" / "run_briefing_scheduled.sh"
    today = datetime.now().strftime("%Y-%m-%d")
    env = os.environ.copy()
    # The daemon runs under the project venv (has all deps); make the briefing
    # subprocess use the same interpreter instead of falling back to system py3.
    env.setdefault("PORTFOLIO_BRIEFING_PYTHON", sys.executable)
    proc = subprocess.run(["bash", str(script)], env=env)
    briefing_path = str(Path(delivery_dir) / "latest.md")
    log_file = Path(log_dir) / f"briefing_{today}.log"
    log_tail = ""
    if log_file.exists():
        log_tail = "\n".join(log_file.read_text().splitlines()[-20:])
    return proc.returncode, briefing_path, log_tail


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

    def send_message(self, chat_id, text: str, parse_mode=None):
        data = {"chat_id": chat_id, "text": text}
        if parse_mode:
            data["parse_mode"] = parse_mode
        r = self.session.post(
            f"{self.base}/sendMessage",
            data=data,
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
                chunks = build_digest_messages(md)
                if chunks:
                    for chunk in chunks:
                        self.tg.send_message(chat_id, chunk, parse_mode="MarkdownV2")
                else:
                    self.tg.send_message(chat_id, extract_summary(md))
                self.tg.send_document(chat_id, briefing_path, caption="Full briefing")
            else:
                self.tg.send_message(
                    chat_id,
                    f"⚠️ briefing failed (exit {exit_code}):\n{log_tail}",
                )
        finally:
            self.busy = False

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


def acquire_lock(lock_path) -> bool:
    """Single-instance pidfile. True if we got the lock, False if a live holder."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text().strip())
            os.kill(pid, 0)          # raises if not alive
            return False             # a live instance holds it
        except PermissionError:
            return False             # process alive but owned by another user
        except (ValueError, ProcessLookupError):
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
        allowed_id=int(cfg["allowed_id"]),
        state_path=state_dir / "telegram_bot_state.json",
    )
    print(f"telegram briefing bot: polling, fires daily at "
          f"{bot.fire_hour:02d}:{bot.fire_minute:02d} local", file=sys.stderr)

    while True:
        try:
            updates = tg.get_updates(bot.state["update_offset"])
            for u in updates:
                # Advance + persist the offset BEFORE handling so a poison
                # message is skipped on the next poll, never retried forever.
                bot.state["update_offset"] = u["update_id"] + 1
                save_state(bot.state_path, bot.state)
                try:
                    bot.handle_update(u)
                except Exception as e:
                    print(
                        f"telegram briefing bot: handle_update error on "
                        f"update {u.get('update_id')}: {e}",
                        file=sys.stderr,
                    )
            if not updates:
                time.sleep(1)
            bot.tick(datetime.now())
        except Exception as e:        # network blips etc — never die
            print(f"telegram briefing bot: loop error: {e}", file=sys.stderr)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
