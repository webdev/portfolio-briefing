"""
Self-contained E*TRADE OAuth 1.0a token management for the briefing repo.

This module owns the full token lifecycle so the briefing pipeline does not
depend on the wheelhouz repo. It provides:

  - load_tokens()                — read persisted tokens from disk
  - save_tokens()                — persist tokens to disk
  - renew_tokens()               — extend the 2-hour idle clock (no browser)
  - authenticate_interactive()   — run the full browser OAuth flow
  - get_session()                — convenience wrapper for clients

Token storage
-------------
Tokens live at $PORTFOLIO_BRIEFING_TOKEN_FILE if set, otherwise at
~/.config/portfolio-briefing/etrade_tokens.json (XDG-style). The directory
is created if missing. File mode is 0600 (owner-only) since the file
contains live OAuth secrets.

Consumer credentials
--------------------
ETRADE_CONSUMER_KEY and ETRADE_CONSUMER_SECRET are read from environment.
For convenience, this module also reads from a .env file at:
  $PORTFOLIO_BRIEFING_REPO/.env  (default: ~/workspace/portfolio-briefing/.env)

The .env path is configurable via PORTFOLIO_BRIEFING_ENV. Both the .env
file and the token file should be in .gitignore.

Token expiration model
----------------------
E*TRADE OAuth 1.0a access tokens have two expiration mechanisms:

  1. Idle timeout: 2 hours without any API call → renewable via
     /oauth/renew_access_token (this is what renew_tokens() hits).
  2. Hard expiry: midnight ET regardless of activity → requires a fresh
     browser-based OAuth handshake (this is what authenticate_interactive()
     does).

The hourly heartbeat task calls renew_tokens(); the morning re-auth (if
needed) calls authenticate_interactive().
"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyetrade  # noqa: E402


# --------------------------------------------------------------------------
# Paths and credentials
# --------------------------------------------------------------------------

_DEFAULT_REPO = Path.home() / "workspace" / "portfolio-briefing"
_DEFAULT_TOKEN_FILE = Path.home() / ".config" / "portfolio-briefing" / "etrade_tokens.json"


def _repo_root() -> Path:
    return Path(os.getenv("PORTFOLIO_BRIEFING_REPO", str(_DEFAULT_REPO))).expanduser()


def _env_file() -> Path:
    explicit = os.getenv("PORTFOLIO_BRIEFING_ENV")
    if explicit:
        return Path(explicit).expanduser()
    return _repo_root() / ".env"


def token_file_path() -> Path:
    """Resolve the canonical token file location.

    Override with PORTFOLIO_BRIEFING_TOKEN_FILE env var if you keep tokens
    elsewhere (e.g. shared between machines via a synced directory).
    """
    explicit = os.getenv("PORTFOLIO_BRIEFING_TOKEN_FILE")
    if explicit:
        return Path(explicit).expanduser()
    return _DEFAULT_TOKEN_FILE


def _load_dotenv_if_present() -> None:
    """Read .env into os.environ if dotenv is installed and the file exists.

    Falls back silently if python-dotenv isn't available — env vars set
    explicitly (e.g. by launchd, Cowork scheduled tasks) still work.
    """
    env_path = _env_file()
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(env_path, override=False)
    except ImportError:
        # Hand-parse to keep the dependency optional.
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def _consumer_credentials() -> tuple[str, str]:
    """Read the E*TRADE app's consumer key/secret. Raises if missing."""
    _load_dotenv_if_present()
    ck = os.environ.get("ETRADE_CONSUMER_KEY", "")
    cs = os.environ.get("ETRADE_CONSUMER_SECRET", "")
    if not (ck and cs):
        raise RuntimeError(
            "ETRADE_CONSUMER_KEY and ETRADE_CONSUMER_SECRET must be set "
            "(in environment or in $PORTFOLIO_BRIEFING_REPO/.env)."
        )
    return ck, cs


# --------------------------------------------------------------------------
# Session container
# --------------------------------------------------------------------------

@dataclass
class ETradeSession:
    """Authenticated E*TRADE API session with all three clients."""
    accounts: pyetrade.ETradeAccounts
    market: pyetrade.ETradeMarket
    order: pyetrade.ETradeOrder
    oauth_token: str
    oauth_secret: str
    sandbox: bool
    authenticated_at: str


def _build_clients(
    oauth_token: str,
    oauth_secret: str,
    sandbox: bool,
) -> ETradeSession:
    """Create all three E*TRADE API clients from tokens."""
    ck, cs = _consumer_credentials()
    kwargs = dict(
        client_key=ck,
        client_secret=cs,
        resource_owner_key=oauth_token,
        resource_owner_secret=oauth_secret,
        dev=sandbox,
    )
    return ETradeSession(
        accounts=pyetrade.ETradeAccounts(**kwargs),
        market=pyetrade.ETradeMarket(**kwargs),
        order=pyetrade.ETradeOrder(**kwargs),
        oauth_token=oauth_token,
        oauth_secret=oauth_secret,
        sandbox=sandbox,
        authenticated_at=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------
# Token persistence
# --------------------------------------------------------------------------

def save_tokens(oauth_token: str, oauth_secret: str, sandbox: bool) -> Path:
    """Persist tokens to disk with 0600 mode."""
    path = token_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "oauth_token": oauth_token,
        "oauth_secret": oauth_secret,
        "sandbox": sandbox,
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Best-effort on systems where chmod is restricted.
    return path


def load_tokens() -> dict[str, Any] | None:
    """Load tokens from disk, or None if not present / unreadable."""
    path = token_file_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# --------------------------------------------------------------------------
# Renewal + interactive auth
# --------------------------------------------------------------------------

def renew_tokens(oauth_token: str, oauth_secret: str) -> bool:
    """Renew an existing access token to extend its 2-hour idle clock.

    Returns True on success, False if E*TRADE rejected the renewal — which
    typically means the token has hit the midnight-ET hard expiration and
    a fresh browser OAuth flow is required.
    """
    ck, cs = _consumer_credentials()
    try:
        mgr = pyetrade.ETradeAccessManager(
            client_key=ck,
            client_secret=cs,
            resource_owner_key=oauth_token,
            resource_owner_secret=oauth_secret,
        )
        result = mgr.renew_access_token()
        return bool(result)
    except Exception:
        return False


def authenticate_interactive(sandbox: bool = False) -> ETradeSession:
    """Run the one-time browser OAuth flow.

    Walks the user through:
      1. Opens the E*TRADE authorization URL in the browser.
      2. User logs in, accepts the app, and copies the verifier code.
      3. We exchange the verifier for access tokens, persist them, and
         return a built session.

    Use sandbox=True only when iterating against E*TRADE's sandbox host.
    Production briefing runs use sandbox=False.
    """
    ck, cs = _consumer_credentials()
    oauth = pyetrade.ETradeOAuth(ck, cs)
    authorize_url = oauth.get_request_token()

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

    tokens = oauth.get_access_token(verifier)
    oauth_token = tokens["oauth_token"]
    oauth_secret = tokens["oauth_token_secret"]

    save_tokens(oauth_token, oauth_secret, sandbox)
    return _build_clients(oauth_token, oauth_secret, sandbox)


# --------------------------------------------------------------------------
# Convenience wrapper
# --------------------------------------------------------------------------

def get_session(sandbox: bool = False, try_renew: bool = True) -> ETradeSession:
    """Load saved tokens and return an authenticated session.

    If try_renew is True (default) and tokens exist, hits the renewal endpoint
    first to extend the idle clock. If renewal fails, the session is still
    built — caller can decide whether to retry interactive auth.

    Raises RuntimeError if no tokens exist on disk.
    """
    saved = load_tokens()
    if not saved:
        raise RuntimeError(
            f"No saved E*TRADE tokens at {token_file_path()}. "
            "Run `python3 -m scripts.etrade_oauth_setup` to authenticate."
        )

    if try_renew:
        renew_tokens(str(saved["oauth_token"]), str(saved["oauth_secret"]))

    return _build_clients(
        oauth_token=str(saved["oauth_token"]),
        oauth_secret=str(saved["oauth_secret"]),
        sandbox=bool(saved.get("sandbox", sandbox)),
    )


# --------------------------------------------------------------------------
# CLI entry point — `python3 etrade_auth.py` runs the interactive flow.
# --------------------------------------------------------------------------

if __name__ == "__main__":
    use_sandbox = "--sandbox" in sys.argv
    try:
        sess = authenticate_interactive(sandbox=use_sandbox)
        print(f"\n✅ Authenticated. Tokens saved to {token_file_path()}")
        print(f"   sandbox: {sess.sandbox}")
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Auth failed: {e}", file=sys.stderr)
        sys.exit(1)
