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
