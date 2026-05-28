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
