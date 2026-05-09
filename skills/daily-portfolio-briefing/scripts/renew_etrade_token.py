#!/usr/bin/env python3
"""
E*TRADE token heartbeat renewer.

E*TRADE OAuth 1.0a access tokens expire in two ways:
  1. Idle timeout: 2 hours without API activity → renewable.
  2. Hard expiry: midnight ET regardless of activity → requires browser re-auth.

This script calls /oauth/renew_access_token to reset the idle clock. Run it
hourly during the trading day to keep the token warm so the morning briefing
and any ad-hoc API calls succeed without manual intervention.

Behavior:
  * Success → log "renewed" and exit 0.
  * Token already expired (past midnight ET) → exit 2, write a notice file at
    ~/Documents/briefings/logs/etrade_token_dead.txt so you see it next morning.
  * Other failure (env, missing file) → exit 1.

The renewal endpoint does NOT extend the midnight-ET hard limit — it only
resets the idle timer. So this script keeps the token alive through the
trading day, but you still need to do a fresh browser OAuth flow once per
day (typically right after midnight ET when yesterday's token dies).

Usage:
  python3 renew_etrade_token.py

Environment:
  PORTFOLIO_BRIEFING_REPO       briefing repo root (default ~/workspace/portfolio-briefing)
  PORTFOLIO_BRIEFING_TOKEN_FILE override token file location
                                 (default ~/.config/portfolio-briefing/etrade_tokens.json)
  PORTFOLIO_BRIEFING_LOG_DIR    where to write logs / notices
                                 (default ~/Documents/briefings/logs)
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# Make the in-repo etrade_auth module importable regardless of cwd.
_THIS = Path(__file__).resolve()
sys.path.insert(0, str(_THIS.parent))


def _log_dir() -> Path:
    p = Path(
        os.getenv(
            "PORTFOLIO_BRIEFING_LOG_DIR",
            str(Path.home() / "Documents" / "briefings" / "logs"),
        )
    ).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_dead_token_notice(log_dir: Path, reason: str, token_file: Path) -> None:
    """Drop a single human-readable file the user will see next morning."""
    notice = log_dir / "etrade_token_dead.txt"
    notice.write_text(
        "E*TRADE access token is dead — renewal failed.\n"
        f"Time: {datetime.now(timezone.utc).isoformat()}\n"
        f"Token file: {token_file}\n"
        f"Reason: {reason}\n\n"
        "To recover, run the in-repo interactive auth flow:\n"
        "  cd ~/workspace/portfolio-briefing/skills/daily-portfolio-briefing/scripts\n"
        "  python3 etrade_auth.py\n\n"
        "After authenticating, this file is automatically cleared on the next successful renewal.\n"
    )


def _clear_dead_token_notice(log_dir: Path) -> None:
    notice = log_dir / "etrade_token_dead.txt"
    if notice.exists():
        notice.unlink()


def _append_history(log_dir: Path, status: str, detail: str = "") -> None:
    """Append a one-line record to today's renewer log for after-the-fact debugging."""
    today = datetime.now().strftime("%Y-%m-%d")
    log = log_dir / f"renewer_{today}.log"
    with log.open("a") as f:
        ts = datetime.now().isoformat(timespec="seconds")
        f.write(f"{ts}\t{status}\t{detail}\n")


def main() -> int:
    log_dir = _log_dir()

    try:
        from etrade_auth import load_tokens, renew_tokens, token_file_path  # type: ignore
    except Exception as e:
        _append_history(log_dir, "FAIL_IMPORT", str(e))
        print(f"FATAL: cannot import etrade_auth: {e}", file=sys.stderr)
        return 1

    token_file = token_file_path()
    if not token_file.exists():
        _append_history(log_dir, "FAIL_NO_TOKENS", str(token_file))
        _write_dead_token_notice(log_dir, f"token file missing", token_file)
        print(f"FATAL: no tokens at {token_file}", file=sys.stderr)
        return 2

    saved = load_tokens()
    if not saved:
        _append_history(log_dir, "FAIL_LOAD", "load_tokens returned None")
        _write_dead_token_notice(log_dir, "load_tokens returned None", token_file)
        return 2

    try:
        ok = renew_tokens(str(saved["oauth_token"]), str(saved["oauth_secret"]))
    except Exception as e:
        _append_history(log_dir, "FAIL_RENEW_EXC", str(e))
        _write_dead_token_notice(log_dir, f"renewal raised: {e}", token_file)
        traceback.print_exc()
        return 2

    if ok:
        _append_history(log_dir, "OK", "")
        _clear_dead_token_notice(log_dir)
        # Stay quiet on success — hourly cron output noise is annoying.
        return 0
    else:
        # Token rejected by E*TRADE — usually means past midnight ET.
        _append_history(log_dir, "FAIL_RENEW", "renew_tokens returned False")
        _write_dead_token_notice(
            log_dir,
            "renew_tokens returned False — token likely expired past midnight ET. "
            "Run interactive auth.",
            token_file,
        )
        print("Token renewal rejected — re-auth required.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
