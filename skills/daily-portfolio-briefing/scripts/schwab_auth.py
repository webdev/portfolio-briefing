# skills/daily-portfolio-briefing/scripts/schwab_auth.py
"""
Self-contained Charles Schwab OAuth2 token management for the briefing repo.

Parallels etrade_auth.py so the Telegram daemon can drive either broker through
the same surface. Read-only use only — no order placement.

Public surface (mirrors etrade_auth where the daemon depends on it):
  - load_tokens() / save_tokens(payload)
  - begin_interactive()        -> (authorize_url, None)
  - complete_interactive(handle, pasted)  -> dict (tokens), persisted
  - token_status()             -> "ready" | "need_auth"
  - get_access_token()         -> str | None   (refreshes silently if near expiry)
  - extract_code(text)         -> str | None   (parses Schwab redirect code)
  - authenticate_interactive() -> dict (CLI path)

Token model: access_token ~30 min (silently refreshed via refresh_token),
refresh_token ~7 days (forces a fresh browser auth when dead).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.parse
import webbrowser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

_DEFAULT_REPO = Path.home() / "workspace" / "portfolio-briefing"
_DEFAULT_TOKEN_FILE = Path.home() / ".config" / "portfolio-briefing" / "schwab_tokens.json"

_AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
_DEFAULT_REDIRECT_URI = "https://127.0.0.1"
_EXPIRY_SKEW_S = 120  # refresh this many seconds before actual expiry


def _repo_root() -> Path:
    return Path(os.getenv("PORTFOLIO_BRIEFING_REPO", str(_DEFAULT_REPO))).expanduser()


def _env_file() -> Path:
    explicit = os.getenv("PORTFOLIO_BRIEFING_ENV")
    return Path(explicit).expanduser() if explicit else _repo_root() / ".env"


def _load_dotenv_if_present() -> None:
    env_path = _env_file()
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def token_file_path() -> Path:
    explicit = os.getenv("SCHWAB_TOKEN_FILE")
    return Path(explicit).expanduser() if explicit else _DEFAULT_TOKEN_FILE


def redirect_uri() -> str:
    _load_dotenv_if_present()
    return os.environ.get("SCHWAB_REDIRECT_URI", _DEFAULT_REDIRECT_URI)


def _app_credentials() -> tuple[str, str]:
    _load_dotenv_if_present()
    key = os.environ.get("SCHWAB_APP_KEY", "")
    secret = os.environ.get("SCHWAB_APP_SECRET", "")
    if not (key and secret):
        raise RuntimeError(
            "SCHWAB_APP_KEY and SCHWAB_APP_SECRET must be set "
            "(in environment or $PORTFOLIO_BRIEFING_REPO/.env)."
        )
    return key, secret


def _basic_auth_header() -> dict[str, str]:
    key, secret = _app_credentials()
    raw = f"{key}:{secret}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def extract_code(text: str | None) -> str | None:
    """Pull the OAuth code from a pasted redirect URL or a bare code string.

    Schwab redirects to <redirect_uri>?code=<URL-encoded>&session=... ; the
    code commonly ends in %40 (an '@'). Accept either the whole URL or just
    the code. Returns None for ordinary chat text.
    """
    t = (text or "").strip()
    if not t:
        return None
    if "code=" in t:
        parsed = urllib.parse.urlparse(t)
        qs = urllib.parse.parse_qs(parsed.query) if parsed.query else urllib.parse.parse_qs(t)
        codes = qs.get("code")
        return codes[0] if codes else None
    # Bare code heuristic: a Schwab auth code is a single whitespace-free token
    # and is long (50+ chars in practice). Require length >= 20 so short
    # one-word bot commands ("run", "briefing") are NOT misread as codes — the
    # Telegram daemon routes every awaiting-auth message through here.
    if t.split() == [t] and len(t) >= 20:
        return urllib.parse.unquote(t)
    return None


def _parse_token_response(raw: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    expires_in = int(raw.get("expires_in", 1800))
    return {
        "access_token": raw["access_token"],
        "refresh_token": raw["refresh_token"],
        "access_expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
        "refresh_expires_at": (now + timedelta(days=7)).isoformat(),
        "saved_at": now.isoformat(),
    }


def _token_is_expired(tokens: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    exp = tokens.get("access_expires_at")
    if not exp:
        return True
    return datetime.fromisoformat(exp) - timedelta(seconds=_EXPIRY_SKEW_S) <= now


def _refresh_token_dead(tokens: dict, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    exp = tokens.get("refresh_expires_at")
    if not exp:
        return True
    return datetime.fromisoformat(exp) <= now


def save_tokens(payload: dict) -> Path:
    path = token_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load_tokens() -> dict[str, Any] | None:
    path = token_file_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _post_token(form: dict, session: "requests.Session | None" = None) -> dict:
    sess = session or requests
    r = sess.post(
        _TOKEN_URL,
        headers={**_basic_auth_header(), "Content-Type": "application/x-www-form-urlencoded"},
        data=form,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def begin_interactive():
    """Return (authorize_url, None). No live handle needed for OAuth2 auth-code."""
    key, _ = _app_credentials()
    params = urllib.parse.urlencode({"client_id": key, "redirect_uri": redirect_uri()})
    return f"{_AUTHORIZE_URL}?{params}", None


def complete_interactive(handle, pasted: str, session=None) -> dict:
    """Exchange a pasted redirect code for tokens, persist, return the token dict."""
    code = extract_code(pasted)
    if not code:
        raise RuntimeError("Could not find an authorization code in the pasted text.")
    raw = _post_token(
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri()},
        session=session,
    )
    tokens = _parse_token_response(raw)
    save_tokens(tokens)
    return tokens


def get_access_token(session=None) -> str | None:
    """Return a usable bearer token, refreshing silently if near expiry.

    Returns None when no tokens exist or the 7-day refresh token is dead
    (the caller must re-run interactive auth). Fail-closed: never invents one.
    """
    tokens = load_tokens()
    if not tokens:
        return None
    if not _token_is_expired(tokens):
        return tokens["access_token"]
    if _refresh_token_dead(tokens):
        return None
    try:
        raw = _post_token(
            {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
            session=session,
        )
    except Exception:
        return None
    new_tokens = _parse_token_response(raw)
    # Schwab returns a fresh refresh_token on refresh; keep its 7-day window.
    save_tokens(new_tokens)
    return new_tokens["access_token"]


def token_status() -> str:
    """'ready' if a (refreshable) token exists, else 'need_auth'. Used by the bot."""
    return "ready" if get_access_token() else "need_auth"


def authenticate_interactive(session=None) -> dict:
    url, _ = begin_interactive()
    print("\n=== Schwab OAuth2 ===")
    print("1. A browser opens to Schwab's authorization page.")
    print("2. Log in and approve; your browser redirects to your callback URL.")
    print("3. Copy the WHOLE redirected URL (it shows a connection error) and paste it.")
    print(f"\nAuthorization URL:\n  {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        print("(Could not open browser automatically — copy the URL above.)")
    pasted = input("Paste redirect URL (or code): ").strip()
    return complete_interactive(None, pasted, session=session)


if __name__ == "__main__":
    try:
        authenticate_interactive()
        print(f"\nAuthenticated. Tokens saved to {token_file_path()}")
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception as e:
        print(f"\nAuth failed: {e}", file=sys.stderr)
        sys.exit(1)
