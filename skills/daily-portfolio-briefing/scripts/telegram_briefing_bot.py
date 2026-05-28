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
    if candidate < now:
        candidate += timedelta(days=1)
    return candidate
