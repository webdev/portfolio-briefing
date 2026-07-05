#!/usr/bin/env python3
"""Fable-review key diagnostic.

Reads ANTHROPIC_API_KEY the same way the briefing pipeline does,
then makes a MINIMAL API call to Anthropic to confirm the key is valid.

Usage:
    uv run python skills/daily-portfolio-briefing/scripts/diagnose_fable.py

Exit codes:
    0 = key works, API responded normally
    1 = key found but rejected by Anthropic (401 / auth error)
    2 = key not found in env or .env
    3 = network / other error
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _mask(key: str) -> str:
    """Mask the key so a screenshot doesn't leak it. Show first 8 + last 4."""
    if not key:
        return "<empty>"
    if len(key) < 16:
        return "<too short: " + str(len(key)) + " chars>"
    return f"{key[:8]}...{key[-4:]}  (length {len(key)})"


def main() -> int:
    # Import the SAME loader the pipeline uses so we diagnose the real path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from analysis.fable_review import _load_env_key

    print("─── Fable-review key diagnostic ───\n")
    key, source = _load_env_key()

    if not key:
        print("❌ NO KEY FOUND")
        print()
        print("The loader checked, in order:")
        print("  1. Environment variable  ANTHROPIC_API_KEY")
        print("  2. Env file at           ", _resolve_env_path())
        print()
        print("Fix:")
        print("  echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env")
        print()
        print("Or set it inline for one run:")
        print("  ANTHROPIC_API_KEY=sk-ant-... uv run python scripts/...")
        return 2

    source_label = {
        "env": "environment variable (os.environ / shell export)",
        "dotenv": f"file: {_resolve_env_path()}",
    }.get(source, source or "unknown")
    print(f"✓ Key loaded: {_mask(key)}")
    print(f"  Source:     {source_label}")
    print()

    # If key came from env-var AND a DIFFERENT value exists in .env,
    # warn — this is the exact scenario where the pipeline hits 401 but
    # the CLI works: the env var is stale, the .env value is fresh.
    try:
        from analysis.fable_review import _read_dotenv_key, _default_env_path
        dp = _default_env_path()
        if source == "env" and dp:
            dotenv_val = _read_dotenv_key(dp)
            if dotenv_val and dotenv_val != key:
                print("⚠  MISMATCH between env-var value and .env value")
                print(f"   env-var: {_mask(key)}")
                print(f"   .env  : {_mask(dotenv_val)}")
                print(f"   These are DIFFERENT keys. If the env-var key fails")
                print(f"   with 401, the pipeline auto-retries with the .env")
                print(f"   value (task #52). To make the .env key primary, ")
                print(f"   remove the stale ANTHROPIC_API_KEY export from")
                print(f"   your shell rc file (~/.zshrc / ~/.bashrc / launchd)")
                print()
    except Exception:
        pass

    # Sanity-check the format Anthropic uses today (may change over time)
    if not key.startswith("sk-ant-"):
        print("⚠  WARNING: key does not start with 'sk-ant-'.")
        print("   Anthropic keys today are in the format sk-ant-api03-<random>.")
        print("   If yours starts with something else, it might be for a")
        print("   different provider (OpenAI = sk-, Google = AIza, etc.).")
        print()

    # Check for common contamination
    if key.startswith(("'", '"')) or key.endswith(("'", '"')):
        print("⚠  WARNING: key starts or ends with a quote character.")
        print("   The .env parser strips MATCHED wrapping quotes, but if the")
        print("   value has mixed or leftover quotes it may be malformed.")
        print()
    if any(c in key for c in (" ", "\t", "\n", "\r")):
        print("⚠  WARNING: key contains whitespace. This will cause 401.")
        print()

    # Minimal API probe — 1 output token, verifies auth without spending
    print("Attempting minimal API call to https://api.anthropic.com/v1/messages ...")
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",   # cheapest for a probe
        "max_tokens": 4,
        "messages": [{"role": "user", "content": "hi"}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        print(f"✅ SUCCESS — Anthropic accepted the key.")
        print(f"   Model: {data.get('model')}")
        print(f"   Response id: {data.get('id')}")
        if data.get("usage"):
            print(f"   Usage: {data['usage']}")
        return 0
    except urllib.error.HTTPError as e:
        try:
            body_txt = e.read().decode("utf-8")
        except Exception:
            body_txt = ""
        print(f"❌ HTTP {e.code}: {body_txt[:400]}")
        print()
        if e.code == 401:
            print("This is an authentication error. The key was reachable but rejected.")
            print("Most likely causes:")
            print("  • Key was revoked/rotated in the Anthropic console")
            print("  • Key belongs to a different Anthropic org than expected")
            print("  • Key has a typo (missing chars, transposed chars)")
            print("  • You pasted an OpenAI/Google key by mistake")
            print()
            print("Where to check / rotate: https://console.anthropic.com/settings/keys")
            return 1
        return 3
    except urllib.error.URLError as e:
        print(f"❌ Network error: {e.reason}")
        return 3
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return 3


def _resolve_env_path() -> str:
    """Repeat the same resolution the loader does, for the diagnostic message."""
    import os
    p = os.environ.get("PORTFOLIO_BRIEFING_ENV")
    if p:
        return p
    here = Path(__file__).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").exists():
            return str(candidate / ".env")
    return "(no repo root found)"


if __name__ == "__main__":
    sys.exit(main())
