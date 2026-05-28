# Schwab Brokerage Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a second user run the read-only daily briefing against a Charles Schwab account, delivered via Telegram with the same interactive code-entry auth and 6:30 fire, without changing the existing E*TRADE behavior.

**Architecture:** Approach B — parallel Schwab modules (`schwab_auth`, `schwab_market`, `schwab_adapter`) that emit the exact shapes the pipeline already consumes (`EtradeSnapshot`, `OptionChainRow`-duck-typed rows), selected at runtime by a `PORTFOLIO_BRIEFING_BROKER` env var / `briefing.yaml` `brokerage:` key that defaults to `etrade`. Existing E*TRADE files are touched only at additive dispatch points. The two brokers never run in one process (the friend runs a separate instance), so no shared-state refactor is needed.

**Tech Stack:** Python 3, `requests` (Schwab REST + OAuth2), existing pipeline conventions. No new third-party deps.

**Deviation from spec §3 (flagged for the user):** Instead of duplicating the 355-line `etrade-chain-fetcher` skill into a new `schwab-chain-fetcher` skill, this plan generalizes the *existing* chain fetcher's backend import to be env-selected. Same public surface, same fail-closed contract, single source of truth for the "never yfinance" rule — just a swappable market backend. This is strictly less code and less drift. If you'd rather have a physically separate skill directory, say so and I'll restructure Task 3.

**Invariant enforced throughout:** With `PORTFOLIO_BRIEFING_BROKER` unset or `etrade` and no `brokerage:` key in config, every code path is byte-identical to today. Each task that touches an existing file asserts the default branch is unchanged.

---

## File Structure

**New files:**
- `skills/daily-portfolio-briefing/scripts/schwab_auth.py` — OAuth2 auth-code lifecycle + token store (parallels `etrade_auth.py`).
- `skills/daily-portfolio-briefing/scripts/adapters/schwab_market.py` — chain + expirations via Schwab market-data REST (parallels `etrade_market.py`).
- `skills/daily-portfolio-briefing/scripts/adapters/schwab_adapter.py` — `fetch_schwab_snapshot()` → `EtradeSnapshot` (parallels `etrade_adapter.py`).
- `skills/daily-portfolio-briefing/scripts/adapters/broker_market.py` — env-selected re-export of the right market module (the seam for direct `etrade_market` importers).
- `skills/daily-portfolio-briefing/scripts/tests/test_schwab_auth.py`
- `skills/daily-portfolio-briefing/scripts/tests/test_schwab_market.py`
- `skills/daily-portfolio-briefing/scripts/tests/test_schwab_adapter.py`
- `skills/daily-portfolio-briefing/scripts/tests/fixtures/schwab_accounts_raw.json` — captured/synthetic raw Schwab accounts response.
- `skills/daily-portfolio-briefing/scripts/tests/test_broker_dispatch.py`
- `skills/daily-portfolio-briefing/scripts/tests/test_telegram_bot_auth_generic.py`

**Modified files (additive dispatch points only):**
- `skills/etrade-chain-fetcher/scripts/fetch.py` — env-selected backend import (Task 3).
- `skills/daily-portfolio-briefing/scripts/steps/snapshot_inputs.py:451-468` — adapter dispatch by brokerage (Task 5).
- `skills/daily-portfolio-briefing/scripts/steps/new_ideas.py:22` and `render/panels.py:172` — import market funcs from `broker_market` (Task 6).
- `skills/daily-portfolio-briefing/scripts/telegram_briefing_bot.py` — generic auth module + code extraction (Task 7).
- `skills/daily-portfolio-briefing/scripts/etrade_auth.py` — add additive `token_status()` + `extract_code()` helpers (Task 7).
- `skills/daily-portfolio-briefing/config/briefing.yaml` — document `brokerage:` key (Task 8).
- `.env.example` / docs (Task 8).

**Schwab API reference (used across tasks):**
- OAuth authorize: `GET https://api.schwabapi.com/v1/oauth/authorize?client_id=<KEY>&redirect_uri=<URI>`
- Token exchange/refresh: `POST https://api.schwabapi.com/v1/oauth/token` (HTTP Basic `KEY:SECRET`, form body).
  - exchange: `grant_type=authorization_code&code=<CODE>&redirect_uri=<URI>`
  - refresh: `grant_type=refresh_token&refresh_token=<RT>`
  - response: `{"access_token","refresh_token","expires_in":1800,"token_type":"Bearer"}` (access 30 min; refresh 7 days).
- Account hashes: `GET /trader/v1/accounts/accountNumbers` → `[{"accountNumber","hashValue"}]`.
- Positions+balances: `GET /trader/v1/accounts?fields=positions` → `[{"securitiesAccount":{...}}]`.
- Chains: `GET /marketdata/v1/chains?symbol=<SYM>&contractType=ALL&strikeCount=<N>&fromDate=&toDate=`.

---

## Task 1: `schwab_auth.py` — token store + OAuth2 lifecycle

**Files:**
- Create: `skills/daily-portfolio-briefing/scripts/schwab_auth.py`
- Test: `skills/daily-portfolio-briefing/scripts/tests/test_schwab_auth.py`

The pure helpers (`extract_code`, `_token_is_expired`, `_parse_token_response`, `token_status` logic) are tested directly; the two HTTP calls (`_post_token`, used by exchange + refresh) take an injectable `session` so tests use a fake.

- [ ] **Step 1: Write failing tests for the pure helpers**

```python
# skills/daily-portfolio-briefing/scripts/tests/test_schwab_auth.py
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import schwab_auth  # noqa: E402


def test_extract_code_from_full_redirect_url():
    url = "https://127.0.0.1/?code=C0.abc-123%40&session=xyz"
    assert schwab_auth.extract_code(url) == "C0.abc-123@"  # URL-decoded


def test_extract_code_from_bare_code():
    assert schwab_auth.extract_code("C0.abc-123@") == "C0.abc-123@"


def test_extract_code_rejects_plain_chatter():
    assert schwab_auth.extract_code("run the briefing") is None
    assert schwab_auth.extract_code("") is None
    assert schwab_auth.extract_code(None) is None


def test_token_is_expired_uses_skew():
    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    fresh = (now + timedelta(seconds=600)).isoformat()
    near = (now + timedelta(seconds=30)).isoformat()
    assert schwab_auth._token_is_expired({"access_expires_at": fresh}, now=now) is False
    assert schwab_auth._token_is_expired({"access_expires_at": near}, now=now) is True
    assert schwab_auth._token_is_expired({}, now=now) is True


def test_parse_token_response_computes_expiry():
    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
    raw = {"access_token": "AT", "refresh_token": "RT", "expires_in": 1800}
    out = schwab_auth._parse_token_response(raw, now=now)
    assert out["access_token"] == "AT"
    assert out["refresh_token"] == "RT"
    assert out["access_expires_at"] == (now + timedelta(seconds=1800)).isoformat()
    # refresh token assumed 7-day life from now
    assert out["refresh_expires_at"] == (now + timedelta(days=7)).isoformat()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_schwab_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'schwab_auth'`.

- [ ] **Step 3: Implement `schwab_auth.py`**

```python
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
    # Bare code heuristic: Schwab codes are long and contain no spaces.
    if " " not in t and len(t) >= 20:
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
```

- [ ] **Step 4: Run the pure-helper tests to verify they pass**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_schwab_auth.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Add HTTP-path tests with a fake session**

```python
# append to tests/test_schwab_auth.py

class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []
    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append({"url": url, "data": data})
        return _FakeResp(self._payload)


def test_complete_interactive_exchanges_and_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHWAB_TOKEN_FILE", str(tmp_path / "schwab_tokens.json"))
    monkeypatch.setenv("SCHWAB_APP_KEY", "KEY")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "SECRET")
    monkeypatch.setenv("SCHWAB_REDIRECT_URI", "https://127.0.0.1")
    fake = _FakeSession({"access_token": "AT", "refresh_token": "RT", "expires_in": 1800})
    out = schwab_auth.complete_interactive(None, "https://127.0.0.1/?code=ABCDEFGHIJKLMNOPQRST%40&session=z", session=fake)
    assert out["access_token"] == "AT"
    assert fake.calls[0]["data"]["grant_type"] == "authorization_code"
    assert fake.calls[0]["data"]["code"] == "ABCDEFGHIJKLMNOPQRST@"
    assert schwab_auth.load_tokens()["refresh_token"] == "RT"


def test_get_access_token_refreshes_when_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHWAB_TOKEN_FILE", str(tmp_path / "schwab_tokens.json"))
    monkeypatch.setenv("SCHWAB_APP_KEY", "KEY")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "SECRET")
    # Persist an expired access token with a still-live refresh token.
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    schwab_auth.save_tokens({
        "access_token": "OLD", "refresh_token": "RT",
        "access_expires_at": (now - timedelta(seconds=10)).isoformat(),
        "refresh_expires_at": (now + timedelta(days=6)).isoformat(),
    })
    fake = _FakeSession({"access_token": "NEW", "refresh_token": "RT2", "expires_in": 1800})
    assert schwab_auth.get_access_token(session=fake) == "NEW"
    assert fake.calls[0]["data"]["grant_type"] == "refresh_token"


def test_get_access_token_none_when_refresh_dead(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHWAB_TOKEN_FILE", str(tmp_path / "schwab_tokens.json"))
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    schwab_auth.save_tokens({
        "access_token": "OLD", "refresh_token": "RT",
        "access_expires_at": (now - timedelta(seconds=10)).isoformat(),
        "refresh_expires_at": (now - timedelta(seconds=10)).isoformat(),
    })
    assert schwab_auth.get_access_token() is None
```

- [ ] **Step 6: Run all `schwab_auth` tests**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_schwab_auth.py -v`
Expected: PASS (7 tests).

- [ ] **Step 7: Commit**

```bash
git add skills/daily-portfolio-briefing/scripts/schwab_auth.py \
        skills/daily-portfolio-briefing/scripts/tests/test_schwab_auth.py
git commit -m "feat(schwab): OAuth2 token lifecycle + redirect-code parsing"
```

---

## Task 2: `schwab_market.py` — chain + expirations

**Files:**
- Create: `skills/daily-portfolio-briefing/scripts/adapters/schwab_market.py`
- Test: `skills/daily-portfolio-briefing/scripts/tests/test_schwab_market.py`

Exposes the same function names + row shape the chain fetcher expects: `get_option_chain(symbol, expiry_date, strike_near, no_of_strikes, chain_type, timeout_s) -> {"put":[...], "call":[...]} | None`, `get_option_expirations(symbol, timeout_s) -> [date] | None`, and an `OptionChainRow` with attributes `strike, option_type, bid, ask, last, open_interest, delta, gamma, theta, vega, iv`. The Schwab→rows transform is a pure function tested against a captured chain payload.

- [ ] **Step 1: Write failing test for the chain parser**

```python
# skills/daily-portfolio-briefing/scripts/tests/test_schwab_market.py
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters import schwab_market  # noqa: E402


def _sample_chain_payload():
    # Shape mirrors Schwab GET /marketdata/v1/chains (trimmed).
    return {
        "callExpDateMap": {
            "2026-06-19:22": {
                "150.0": [{
                    "strikePrice": 150.0, "bid": 5.0, "ask": 5.4, "last": 5.2,
                    "openInterest": 1200, "delta": 0.32, "gamma": 0.01,
                    "theta": -0.05, "vega": 0.10, "volatility": 28.5,
                }],
            }
        },
        "putExpDateMap": {
            "2026-06-19:22": {
                "150.0": [{
                    "strikePrice": 150.0, "bid": 4.0, "ask": 4.4, "last": 4.2,
                    "openInterest": 900, "delta": -0.30, "gamma": 0.01,
                    "theta": -0.04, "vega": 0.09, "volatility": 27.0,
                }],
            }
        },
    }


def test_parse_chain_payload_builds_rows():
    out = schwab_market._parse_chain_payload(_sample_chain_payload())
    assert len(out["call"]) == 1 and len(out["put"]) == 1
    c = out["call"][0]
    assert c.strike == 150.0 and c.option_type == "CALL"
    assert c.bid == 5.0 and c.ask == 5.4 and c.delta == 0.32
    assert c.iv == 28.5  # Schwab 'volatility' is already in percent points
    p = out["put"][0]
    assert p.option_type == "PUT" and p.delta == -0.30


def test_parse_expirations_payload():
    exps = schwab_market._parse_expirations(_sample_chain_payload())
    assert date(2026, 6, 19) in exps
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_schwab_market.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'adapters.schwab_market'`.

- [ ] **Step 3: Implement `schwab_market.py`**

```python
# skills/daily-portfolio-briefing/scripts/adapters/schwab_market.py
"""Charles Schwab market-data adapter — option chains via the Trader API.

Mirrors adapters/etrade_market.py's public surface so the canonical chain
fetcher can swap backends with no other changes. Fail-closed: any error or
missing data returns None; never substitute another source.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import schwab_auth  # type: ignore  # noqa: E402

_CHAINS_URL = "https://api.schwabapi.com/marketdata/v1/chains"


@dataclass
class OptionChainRow:
    strike: float
    option_type: str  # "PUT" or "CALL"
    bid: float
    ask: float
    last: float
    open_interest: int
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    iv: Optional[float] = None


def _f(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        # Schwab uses sentinel -999.0 for missing greeks/iv.
        return None if f <= -998 else f
    except (TypeError, ValueError):
        return None


def _rows_from_exp_map(exp_map: dict, opt_type: str) -> List[OptionChainRow]:
    rows: List[OptionChainRow] = []
    for _exp_key, strikes in (exp_map or {}).items():
        for _strike_key, contracts in strikes.items():
            for c in contracts:
                rows.append(OptionChainRow(
                    strike=float(c.get("strikePrice", 0)),
                    option_type=opt_type,
                    bid=float(c.get("bid", 0) or 0),
                    ask=float(c.get("ask", 0) or 0),
                    last=float(c.get("last", 0) or 0),
                    open_interest=int(c.get("openInterest", 0) or 0),
                    delta=_f(c.get("delta")),
                    gamma=_f(c.get("gamma")),
                    theta=_f(c.get("theta")),
                    vega=_f(c.get("vega")),
                    iv=_f(c.get("volatility")),
                ))
    return rows


def _parse_chain_payload(payload: dict) -> Dict[str, List[OptionChainRow]]:
    return {
        "call": _rows_from_exp_map(payload.get("callExpDateMap", {}), "CALL"),
        "put": _rows_from_exp_map(payload.get("putExpDateMap", {}), "PUT"),
    }


def _parse_expirations(payload: dict) -> List[date]:
    exps: set[date] = set()
    for key_map in (payload.get("callExpDateMap", {}), payload.get("putExpDateMap", {})):
        for exp_key in key_map.keys():
            # exp_key looks like "2026-06-19:22" (date:daysToExp)
            iso = exp_key.split(":", 1)[0]
            try:
                y, m, d = (int(x) for x in iso.split("-"))
                exps.add(date(y, m, d))
            except ValueError:
                continue
    return sorted(exps)


def _get(symbol: str, params: dict, timeout_s: float, session=None) -> Optional[dict]:
    token = schwab_auth.get_access_token()
    if not token:
        return None
    sess = session or requests
    try:
        r = sess.get(
            _CHAINS_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={"symbol": symbol, **params},
            timeout=timeout_s + 2,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def get_option_expirations(symbol: str, timeout_s: float = 3.0, session=None) -> Optional[List[date]]:
    payload = _get(symbol, {"contractType": "ALL", "strikeCount": 1}, timeout_s, session)
    if not payload:
        return None
    exps = _parse_expirations(payload)
    return exps or None


def get_option_chain(
    symbol: str,
    expiry_date: date,
    strike_near: float,
    no_of_strikes: int = 20,
    chain_type: str = "CALLPUT",
    timeout_s: float = 5.0,
    session=None,
) -> Optional[Dict[str, List[OptionChainRow]]]:
    contract_type = {"PUT": "PUT", "CALL": "CALL", "CALLPUT": "ALL"}.get(chain_type, "ALL")
    iso = expiry_date.isoformat()
    payload = _get(
        symbol,
        {
            "contractType": contract_type,
            "strikeCount": no_of_strikes,
            "fromDate": iso,
            "toDate": iso,
        },
        timeout_s,
        session,
    )
    if not payload:
        return None
    return _parse_chain_payload(payload)


def find_put_strike_near(symbol, target_otm_pct=12.0, target_dte_min=25,
                         target_dte_max=45, spot=None, session=None):
    """Parity shim for render/panels.py:172 (etrade_market.find_put_strike_near).

    Mirrors the E*TRADE adapter's behavior over Schwab chains. Returns
    {strike, expiration, bid, ask, mid, delta} or None.
    """
    if not spot or spot <= 0:
        return None
    exps = get_option_expirations(symbol, session=session)
    if not exps:
        return None
    today = date.today()
    band = [(abs((e - today).days - (target_dte_min + target_dte_max) / 2), e)
            for e in exps if target_dte_min <= (e - today).days <= target_dte_max]
    if not band:
        return None
    band.sort()
    chosen = band[0][1]
    target = spot * (1 - target_otm_pct / 100)
    chain = get_option_chain(symbol, chosen, strike_near=target,
                             no_of_strikes=20, chain_type="PUT", session=session)
    if not chain or not chain.get("put"):
        return None
    best = min(chain["put"], key=lambda r: abs(r.strike - target))
    bid, ask = best.bid or 0, best.ask or 0
    mid = (bid + ask) / 2 if bid and ask else ask or bid or 0
    return {"strike": best.strike, "expiration": chosen.isoformat(),
            "bid": bid, "ask": ask, "mid": mid, "delta": best.delta}
```

- [ ] **Step 4: Run parser tests to verify they pass**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_schwab_market.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Add an HTTP-path test with a fake session**

```python
# append to tests/test_schwab_market.py

class _FakeResp:
    def __init__(self, payload): self._payload = payload
    def raise_for_status(self): pass
    def json(self): return self._payload


class _FakeSession:
    def __init__(self, payload): self._payload = payload
    def get(self, url, headers=None, params=None, timeout=None):
        return _FakeResp(self._payload)


def test_get_option_chain_returns_none_without_token(monkeypatch):
    monkeypatch.setattr(schwab_market.schwab_auth, "get_access_token", lambda *a, **k: None)
    assert schwab_market.get_option_chain("AAPL", date(2026, 6, 19), 150.0,
                                          session=_FakeSession(_sample_chain_payload())) is None


def test_get_option_chain_parses_with_token(monkeypatch):
    monkeypatch.setattr(schwab_market.schwab_auth, "get_access_token", lambda *a, **k: "AT")
    chain = schwab_market.get_option_chain("AAPL", date(2026, 6, 19), 150.0,
                                           session=_FakeSession(_sample_chain_payload()))
    assert chain and chain["call"][0].strike == 150.0
```

- [ ] **Step 6: Run all `schwab_market` tests**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_schwab_market.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add skills/daily-portfolio-briefing/scripts/adapters/schwab_market.py \
        skills/daily-portfolio-briefing/scripts/tests/test_schwab_market.py
git commit -m "feat(schwab): market-data chain + expirations adapter"
```

---

## Task 3: Env-selected chain-fetcher backend

**Files:**
- Modify: `skills/etrade-chain-fetcher/scripts/fetch.py:44-56` (the backend import block) and `:97-108` (`availability_reason`)
- Test: `skills/daily-portfolio-briefing/scripts/tests/test_broker_dispatch.py`

Make the canonical fetcher import its market backend by env. Default (`etrade`) imports `adapters.etrade_market` exactly as today.

- [ ] **Step 1: Write a failing dispatch test**

```python
# skills/daily-portfolio-briefing/scripts/tests/test_broker_dispatch.py
import importlib.util
import sys
from pathlib import Path

_FETCH = (Path(__file__).resolve().parents[3]
          / "etrade-chain-fetcher" / "scripts" / "fetch.py")


def _load_fetch(monkeypatch, broker):
    monkeypatch.setenv("PORTFOLIO_BRIEFING_BROKER", broker)
    sys.modules.pop("etrade_chain_fetcher_test", None)
    spec = importlib.util.spec_from_file_location("etrade_chain_fetcher_test", _FETCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fetcher_uses_schwab_backend_when_env_set(monkeypatch):
    mod = _load_fetch(monkeypatch, "schwab")
    assert mod._backend_name() == "schwab"


def test_fetcher_defaults_to_etrade(monkeypatch):
    monkeypatch.delenv("PORTFOLIO_BRIEFING_BROKER", raising=False)
    mod = _load_fetch(monkeypatch, "etrade")
    assert mod._backend_name() == "etrade"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_broker_dispatch.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_backend_name'`.

- [ ] **Step 3: Replace the backend import block in `fetch.py`**

Replace lines 44-56 (the `try: from adapters.etrade_market import ... except Exception ...` block) with:

```python
import os

def _backend_name() -> str:
    return os.getenv("PORTFOLIO_BRIEFING_BROKER", "etrade").strip().lower()


try:
    if _backend_name() == "schwab":
        from adapters.schwab_market import (  # type: ignore
            get_option_chain as _adapter_get_chain,
            get_option_expirations as _adapter_get_expirations,
            OptionChainRow,
        )
    else:
        from adapters.etrade_market import (  # type: ignore
            get_option_chain as _adapter_get_chain,
            get_option_expirations as _adapter_get_expirations,
            OptionChainRow,
        )
except Exception as _e:
    _adapter_get_chain = None
    _adapter_get_expirations = None
    OptionChainRow = None
    _import_error = _e
else:
    _import_error = None
```

Then update `availability_reason()` (line ~102-108) so its message reflects the active backend:

```python
def availability_reason() -> str | None:
    if _import_error:
        return f"{_backend_name()}_market adapter import failed: {_import_error}"
    if not is_available():
        return f"{_backend_name()}_market adapter not loadable"
    return None
```

- [ ] **Step 4: Run dispatch tests + the existing E*TRADE-default regression**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_broker_dispatch.py tests/test_rsi_integration.py -v`
Expected: PASS — schwab/etrade selection works; the unset-env default still imports `etrade_market` (regression guard).

- [ ] **Step 5: Commit**

```bash
git add skills/etrade-chain-fetcher/scripts/fetch.py \
        skills/daily-portfolio-briefing/scripts/tests/test_broker_dispatch.py
git commit -m "feat(schwab): env-selected chain-fetcher market backend (default etrade)"
```

---

## Task 4: `schwab_adapter.py` — `fetch_schwab_snapshot()`

**Files:**
- Create: `skills/daily-portfolio-briefing/scripts/adapters/schwab_adapter.py`
- Create: `skills/daily-portfolio-briefing/scripts/tests/fixtures/schwab_accounts_raw.json`
- Test: `skills/daily-portfolio-briefing/scripts/tests/test_schwab_adapter.py`

Normalizes raw Schwab JSON into the exact `EtradeSnapshot` shape (reused from `etrade_adapter`). The normalization functions are pure and tested against the fixture. Account scope: Schwab's account `type` is `CASH`/`MARGIN`, not a friendly name, so `accountDesc` is taken from a config-provided label map keyed by account number (defaulting to the masked account number); `account_desc_whitelist` then gates exactly as for E*TRADE.

- [ ] **Step 1: Create the raw fixture**

```json
// skills/daily-portfolio-briefing/scripts/tests/fixtures/schwab_accounts_raw.json
[
  {
    "securitiesAccount": {
      "type": "MARGIN",
      "accountNumber": "123456789",
      "currentBalances": {
        "liquidationValue": 250000.0,
        "cashBalance": 40000.0,
        "longMarketValue": 210000.0
      },
      "positions": [
        {
          "instrument": {"assetType": "EQUITY", "symbol": "NVDA"},
          "longQuantity": 100.0, "shortQuantity": 0.0,
          "marketValue": 12000.0, "averagePrice": 95.0,
          "longOpenProfitLoss": 2500.0
        },
        {
          "instrument": {
            "assetType": "OPTION",
            "symbol": "NVDA  260619C00150000",
            "underlyingSymbol": "NVDA",
            "putCall": "CALL"
          },
          "longQuantity": 0.0, "shortQuantity": 1.0,
          "marketValue": -450.0, "averagePrice": 5.0,
          "longOpenProfitLoss": 50.0
        }
      ]
    }
  }
]
```

- [ ] **Step 2: Write failing normalization tests**

```python
# skills/daily-portfolio-briefing/scripts/tests/test_schwab_adapter.py
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from adapters import schwab_adapter  # noqa: E402

_FIX = Path(__file__).resolve().parent / "fixtures" / "schwab_accounts_raw.json"


def _raw():
    return json.loads(_FIX.read_text())


def test_parse_osi_symbol():
    sym, typ, strike, exp = schwab_adapter._parse_osi("NVDA  260619C00150000")
    assert sym == "NVDA" and typ == "CALL" and strike == 150.0 and exp == "2026-06-19"


def test_normalize_equity_position():
    pos = _raw()[0]["securitiesAccount"]["positions"][0]
    out = schwab_adapter._normalize_position(pos, "INDIVIDUAL")
    assert out["assetType"] == "EQUITY" and out["symbol"] == "NVDA"
    assert out["qty"] == 100.0 and out["accountDesc"] == "INDIVIDUAL"
    assert out["price"] == 120.0  # 12000 / 100


def test_normalize_short_option_position():
    pos = _raw()[0]["securitiesAccount"]["positions"][1]
    out = schwab_adapter._normalize_position(pos, "INDIVIDUAL")
    assert out["assetType"] == "OPTION" and out["type"] == "CALL"
    assert out["underlying"] == "NVDA" and out["strike"] == 150.0
    assert out["expiration"] == "2026-06-19"
    assert out["qty"] == -1.0           # short → negative
    assert out["positionType"] == "SHORT"
    assert out["delta"] is None         # not provided by accounts endpoint


def test_build_snapshot_scopes_by_label_whitelist():
    snap = schwab_adapter._build_snapshot(
        _raw(),
        account_labels={"123456789": "INDIVIDUAL"},
        account_desc_whitelist=["INDIVIDUAL"],
    )
    assert snap.source == "schwab"
    assert {p["symbol"] for p in snap.positions} == {"NVDA", "NVDA_CALL_150_20260619".replace("-", "")}
    assert snap.balance["accountValue"] == 250000.0
    assert len(snap.accounts) == 1 and snap.accounts[0]["accountDesc"] == "INDIVIDUAL"


def test_build_snapshot_excludes_unwhitelisted_account():
    snap = schwab_adapter._build_snapshot(
        _raw(),
        account_labels={"123456789": "JOINT"},
        account_desc_whitelist=["INDIVIDUAL"],
    )
    assert snap.positions == [] and snap.accounts == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_schwab_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'adapters.schwab_adapter'`.

- [ ] **Step 4: Implement `schwab_adapter.py`**

```python
# skills/daily-portfolio-briefing/scripts/adapters/schwab_adapter.py
"""Charles Schwab adapter — read-only portfolio snapshot via the Trader API.

Emits the exact EtradeSnapshot shape (reused from etrade_adapter) so the rest
of the pipeline is unchanged. Greeks/IV/bid/ask are NOT available on the
accounts endpoint — those fields are None and the pipeline refetches live
quotes through the chain fetcher (Schwab backend). Fail-closed: missing creds
or tokens raise; no fabricated values.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import schwab_auth  # type: ignore  # noqa: E402
from adapters.etrade_adapter import EtradeSnapshot  # type: ignore  # noqa: E402

_ACCOUNTS_URL = "https://api.schwabapi.com/trader/v1/accounts"
_ACCOUNT_NUMBERS_URL = "https://api.schwabapi.com/trader/v1/accounts/accountNumbers"

_MONTHS = None  # unused; expirations parsed numerically


def _parse_osi(osi: str) -> tuple[str, str, float, str]:
    """Parse an OSI option symbol 'ROOT  YYMMDD[C/P]XXXXXXXX' into parts.

    Returns (underlying, "CALL"|"PUT", strike, "YYYY-MM-DD").
    """
    root = osi[:6].strip()
    tail = osi[6:]
    yy, mm, dd = tail[0:2], tail[2:4], tail[4:6]
    cp = "CALL" if tail[6] == "C" else "PUT"
    strike = int(tail[7:]) / 1000.0
    exp = f"20{yy}-{mm}-{dd}"
    return root, cp, strike, exp


def _normalize_position(p: Dict[str, Any], acct_desc: str) -> Dict[str, Any]:
    inst = p.get("instrument", {})
    long_q = float(p.get("longQuantity", 0) or 0)
    short_q = float(p.get("shortQuantity", 0) or 0)
    qty = long_q - short_q  # short positions are negative
    market_value = float(p.get("marketValue", 0) or 0)
    avg = float(p.get("averagePrice", 0) or 0)
    gain = float(p.get("longOpenProfitLoss", 0) or 0)

    if inst.get("assetType") == "OPTION":
        underlying, cp, strike, exp = _parse_osi(inst.get("symbol", ""))
        per_share = abs(market_value / qty) / 100 if qty else 0.0
        return {
            "symbol": f"{underlying}_{cp}_{int(strike)}_{exp}".replace("-", ""),
            "assetType": "OPTION",
            "underlying": underlying,
            "type": cp,
            "strike": strike,
            "expiration": exp,
            "qty": qty,
            "marketValue": market_value,
            "premiumReceived": round(avg, 4),
            "costPerShare": round(avg, 4),
            "currentMid": round(per_share, 4),
            "bid": None,
            "ask": None,
            "totalGain": gain,
            "totalGainPct": 0.0,
            "delta": None, "gamma": None, "theta": None, "vega": None, "rho": None,
            "ivPct": None,
            "openInterest": None,
            "symbolDescription": inst.get("description"),
            "accountDesc": acct_desc,
            "positionType": "SHORT" if qty < 0 else "LONG",
        }

    price = market_value / qty if qty else 0.0
    return {
        "symbol": inst.get("symbol"),
        "assetType": "EQUITY",
        "qty": qty,
        "price": round(price, 2),
        "marketValue": market_value,
        "costBasis": avg,
        "totalGain": gain,
        "totalGainPct": 0.0,
        "accountDesc": acct_desc,
    }


def _build_snapshot(
    raw_accounts: List[Dict[str, Any]],
    account_labels: Optional[Dict[str, str]] = None,
    account_desc_whitelist: Optional[List[str]] = None,
) -> EtradeSnapshot:
    account_labels = account_labels or {}
    warnings: List[str] = []
    fetched_at = datetime.utcnow().isoformat() + "Z"
    whitelist = ({d.strip().upper() for d in account_desc_whitelist}
                 if account_desc_whitelist else None)

    flat_accounts: List[Dict[str, Any]] = []
    all_positions: List[Dict[str, Any]] = []
    agg = {"totalAccountValue": 0.0, "cash": 0.0, "longMarketValue": 0.0}

    for entry in raw_accounts:
        sa = entry.get("securitiesAccount", {})
        acct_num = str(sa.get("accountNumber", ""))
        masked = ("…" + acct_num[-4:]) if len(acct_num) >= 4 else acct_num
        acct_desc = account_labels.get(acct_num, masked)
        if whitelist is not None and acct_desc.strip().upper() not in whitelist:
            continue

        bal = sa.get("currentBalances", {}) or {}
        nlv = float(bal.get("liquidationValue", 0) or 0)
        cash = float(bal.get("cashBalance", 0) or 0)
        lmv = float(bal.get("longMarketValue", 0) or 0)
        agg["totalAccountValue"] += nlv
        agg["cash"] += cash
        agg["longMarketValue"] += lmv

        flat_accounts.append({
            "accountIdKey": acct_num,
            "accountId": acct_num,
            "accountType": sa.get("type"),
            "accountDesc": acct_desc,
            "institutionType": "BROKERAGE",
            "accountStatus": "ACTIVE",
            "nlv": nlv,
            "cash": cash,
        })

        for pos in sa.get("positions", []) or []:
            try:
                all_positions.append(_normalize_position(pos, acct_desc))
            except Exception as e:
                warnings.append(f"position normalize failed in {acct_desc}: {str(e)[:80]}")

    return EtradeSnapshot(
        accounts=flat_accounts,
        positions=all_positions,
        balance={
            "totalAccountValue": round(agg["totalAccountValue"], 2),
            "cash": round(agg["cash"], 2),
            "longMarketValue": round(agg["longMarketValue"], 2),
            "accountValue": round(agg["totalAccountValue"], 2),
        },
        open_orders=[],
        source="schwab",
        fetched_at=fetched_at,
        warnings=warnings,
    )


def _get(url: str, token: str, params: dict | None = None, session=None) -> Any:
    sess = session or requests
    r = sess.get(url, headers={"Authorization": f"Bearer {token}"},
                 params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_schwab_snapshot(
    accounts_filter: Optional[List[str]] = None,
    account_desc_whitelist: Optional[List[str]] = None,
    account_labels: Optional[Dict[str, str]] = None,
    session=None,
) -> EtradeSnapshot:
    """Pull a real read-only portfolio snapshot from Schwab.

    account_labels maps Schwab account numbers to friendly descs so the
    existing account_desc_whitelist scoping works (Schwab exposes only
    CASH/MARGIN as the account 'type', not a nickname).
    """
    token = schwab_auth.get_access_token(session=session)
    if not token:
        raise RuntimeError(
            "Schwab tokens missing or expired. Run the interactive auth flow: "
            "`python3 scripts/schwab_auth.py` (or send a code over Telegram)."
        )
    raw_accounts = _get(f"{_ACCOUNTS_URL}?fields=positions", token, session=session)
    if isinstance(raw_accounts, dict):
        raw_accounts = [raw_accounts]
    if accounts_filter:
        raw_accounts = [
            a for a in raw_accounts
            if str(a.get("securitiesAccount", {}).get("accountNumber")) in accounts_filter
        ]
    return _build_snapshot(raw_accounts, account_labels, account_desc_whitelist)
```

- [ ] **Step 5: Run normalization tests to verify they pass**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_schwab_adapter.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add skills/daily-portfolio-briefing/scripts/adapters/schwab_adapter.py \
        skills/daily-portfolio-briefing/scripts/tests/test_schwab_adapter.py \
        skills/daily-portfolio-briefing/scripts/tests/fixtures/schwab_accounts_raw.json
git commit -m "feat(schwab): read-only portfolio snapshot adapter (EtradeSnapshot shape)"
```

---

## Task 5: Brokerage dispatch in `snapshot_inputs.py`

**Files:**
- Modify: `skills/daily-portfolio-briefing/scripts/steps/snapshot_inputs.py:451-468`
- Test: extend `skills/daily-portfolio-briefing/scripts/tests/test_broker_dispatch.py`

- [ ] **Step 1: Write a failing test for the adapter selector**

```python
# append to tests/test_broker_dispatch.py
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from steps import snapshot_inputs  # noqa: E402


def test_select_snapshot_adapter_defaults_to_etrade():
    fn = snapshot_inputs._select_snapshot_adapter({})
    assert fn.__name__ == "fetch_etrade_snapshot"


def test_select_snapshot_adapter_schwab():
    fn = snapshot_inputs._select_snapshot_adapter({"brokerage": "schwab"})
    assert fn.__name__ == "fetch_schwab_snapshot"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_broker_dispatch.py -v`
Expected: FAIL — `AttributeError: module 'steps.snapshot_inputs' has no attribute '_select_snapshot_adapter'`.

- [ ] **Step 3: Add the selector and use it in the live branch**

Add this helper near the top of `snapshot_inputs.py` (after imports):

```python
def _select_snapshot_adapter(config: dict):
    """Return the live-snapshot fetcher for the configured brokerage.

    Defaults to E*TRADE so existing runs are unchanged. The Schwab fetcher is
    wrapped so it receives the account-label map from config.
    """
    broker = (config.get("brokerage") or "etrade").strip().lower()
    if broker == "schwab":
        import os
        os.environ.setdefault("PORTFOLIO_BRIEFING_BROKER", "schwab")
        from adapters.schwab_adapter import fetch_schwab_snapshot
        labels = config.get("schwab_account_labels") or {}

        def fetch_schwab_snapshot_wrapped(account_desc_whitelist=None):
            return fetch_schwab_snapshot(
                account_desc_whitelist=account_desc_whitelist,
                account_labels=labels,
            )
        fetch_schwab_snapshot_wrapped.__name__ = "fetch_schwab_snapshot"
        return fetch_schwab_snapshot_wrapped

    from adapters.etrade_adapter import fetch_etrade_snapshot
    return fetch_etrade_snapshot
```

Then in the `if etrade_live:` block (lines ~451-468), replace the direct import + call:

```python
    if etrade_live:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        account_desc_whitelist = config.get("account_desc_whitelist") or ["INDIVIDUAL"]
        fetch_snapshot = _select_snapshot_adapter(config)
        broker_label = (config.get("brokerage") or "etrade").strip().lower()
        print(f"  Pulling REAL holdings from {broker_label.upper()} "
              f"(scoped to: {account_desc_whitelist})...")
        snap = fetch_snapshot(account_desc_whitelist=account_desc_whitelist)
        accounts = snap.accounts
        positions = snap.positions
        base_balance = snap.balance
        open_orders = snap.open_orders
        source = snap.source
        snapshot_warnings = snap.warnings

        positions = _deduplicate_positions(positions)
        print(f"  {source}: {len(accounts)} accounts, {len(positions)} unique symbols, "
              f"NLV ${base_balance.get('accountValue', 0):,.0f}")
        for w in snapshot_warnings:
            print(f"    [warn] {w}")
```

(The `--etrade-live` CLI flag keeps its name; it now means "live broker pull" generically. The fixture branch is unchanged — fixtures are already in normalized post-adapter shape, so they're brokerage-agnostic.)

- [ ] **Step 4: Run dispatch + a snapshot fixture regression test**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_broker_dispatch.py -v`
Expected: PASS — both selector tests green; default still returns `fetch_etrade_snapshot`.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-portfolio-briefing/scripts/steps/snapshot_inputs.py \
        skills/daily-portfolio-briefing/scripts/tests/test_broker_dispatch.py
git commit -m "feat(schwab): brokerage-selected snapshot adapter in snapshot_inputs"
```

---

## Task 6: `broker_market.py` seam for direct market importers

**Files:**
- Create: `skills/daily-portfolio-briefing/scripts/adapters/broker_market.py`
- Modify: `skills/daily-portfolio-briefing/scripts/steps/new_ideas.py:22`
- Modify: `skills/daily-portfolio-briefing/scripts/render/panels.py:172`
- Test: extend `skills/daily-portfolio-briefing/scripts/tests/test_broker_dispatch.py`

Two call sites import `etrade_market` directly (not through the chain fetcher). Route them through an env-selected re-export so the Schwab instance uses Schwab chains there too.

- [ ] **Step 1: Write a failing test**

```python
# append to tests/test_broker_dispatch.py
import importlib


def test_broker_market_reexports_by_env(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_BRIEFING_BROKER", "etrade")
    from adapters import broker_market
    importlib.reload(broker_market)
    assert hasattr(broker_market, "get_option_chain")
    assert hasattr(broker_market, "get_option_expirations")
    assert hasattr(broker_market, "find_put_strike_near")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_broker_dispatch.py::test_broker_market_reexports_by_env -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'adapters.broker_market'`.

- [ ] **Step 3: Implement `broker_market.py`**

```python
# skills/daily-portfolio-briefing/scripts/adapters/broker_market.py
"""Env-selected market backend re-export.

Importers that previously did `from adapters.etrade_market import ...` now
import from here; the active backend follows PORTFOLIO_BRIEFING_BROKER
(default 'etrade'). Keeps the Schwab instance off yfinance for chain prices.
"""
import os

if os.getenv("PORTFOLIO_BRIEFING_BROKER", "etrade").strip().lower() == "schwab":
    from adapters.schwab_market import (  # noqa: F401
        get_option_chain,
        get_option_expirations,
        find_put_strike_near,
        OptionChainRow,
    )
else:
    from adapters.etrade_market import (  # noqa: F401
        get_option_chain,
        get_option_expirations,
        find_put_strike_near,
        OptionChainRow,
    )
```

- [ ] **Step 4: Repoint the two direct importers**

In `steps/new_ideas.py` line 22, change:

```python
from adapters.etrade_market import get_option_chain, get_option_expirations
```
to:
```python
from adapters.broker_market import get_option_chain, get_option_expirations
```

In `render/panels.py` line 172, change:

```python
    from etrade_market import find_put_strike_near, get_option_chain  # type: ignore
```
to:
```python
    from broker_market import find_put_strike_near, get_option_chain  # type: ignore
```

(Note: `panels.py:95` adds the `adapters` dir to `sys.path`, so the bare `broker_market` import resolves the same way `etrade_market` did.)

- [ ] **Step 5: Run the dispatch test + new_ideas/panels import smoke**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_broker_dispatch.py -v && python3 -c "import sys; sys.path.insert(0,'.'); from steps import new_ideas; print('new_ideas import ok')"`
Expected: PASS, and `new_ideas import ok` prints (default etrade backend still loads).

- [ ] **Step 6: Commit**

```bash
git add skills/daily-portfolio-briefing/scripts/adapters/broker_market.py \
        skills/daily-portfolio-briefing/scripts/steps/new_ideas.py \
        skills/daily-portfolio-briefing/scripts/render/panels.py \
        skills/daily-portfolio-briefing/scripts/tests/test_broker_dispatch.py
git commit -m "feat(schwab): route direct market importers through broker_market seam"
```

---

## Task 7: Generic auth in the Telegram bot

**Files:**
- Modify: `skills/daily-portfolio-briefing/scripts/etrade_auth.py` (add additive `token_status()`, `extract_code()`)
- Modify: `skills/daily-portfolio-briefing/scripts/telegram_briefing_bot.py` (use injected auth module's `token_status` + `extract_code`; select module by env; generic prompt text)
- Test: `skills/daily-portfolio-briefing/scripts/tests/test_telegram_bot_auth_generic.py`

- [ ] **Step 1: Write failing tests for the generic auth seam**

```python
# skills/daily-portfolio-briefing/scripts/tests/test_telegram_bot_auth_generic.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import etrade_auth  # noqa: E402
import telegram_briefing_bot as bot  # noqa: E402


def test_etrade_extract_code_matches_5char():
    assert etrade_auth.extract_code("ABC12") == "ABC12"
    assert etrade_auth.extract_code("not a code here") is None


class _FakeAuth:
    """Stand-in auth module exposing the generic surface."""
    def __init__(self):
        self.completed_with = None
    def token_status(self):
        return "need_auth"
    def begin_interactive(self):
        return ("https://example/authorize", "HANDLE")
    def complete_interactive(self, handle, code):
        self.completed_with = (handle, code)
    def extract_code(self, text):
        return text if text and text.startswith("CODE") else None


class _FakeTg:
    def __init__(self):
        self.sent = []
    def send_message(self, chat_id, text, parse_mode=None):
        self.sent.append(text)
    def send_document(self, *a, **k):
        pass


def _make_bot(tmp_path):
    auth = _FakeAuth()
    b = bot.BriefingBot(
        tg=_FakeTg(), auth=auth, runner=lambda: (0, None, ""),
        allowed_id=42, state_path=tmp_path / "state.json",
    )
    return b, auth


def test_ensure_token_delegates_to_auth_module(tmp_path):
    b, _ = _make_bot(tmp_path)
    assert b.ensure_token() == "need_auth"


def test_handle_update_uses_auth_extract_code(tmp_path):
    b, auth = _make_bot(tmp_path)
    b.awaiting_verifier = True
    b.oauth_handle = "HANDLE"
    update = {"message": {"from": {"id": 42}, "chat": {"id": 42}, "text": "CODE-xyz"}}
    b.handle_update(update)
    assert auth.completed_with == ("HANDLE", "CODE-xyz")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_telegram_bot_auth_generic.py -v`
Expected: FAIL — `etrade_auth` has no `extract_code`; `ensure_token` still calls `renew_tokens` (not the fake's `token_status`).

- [ ] **Step 3: Add additive helpers to `etrade_auth.py`**

Append to `etrade_auth.py` (before the `if __name__` block):

```python
import re as _re

_VERIFIER_RE = _re.compile(r"^[A-Za-z0-9]{5}$")


def extract_code(text: str | None) -> str | None:
    """Return the 5-char E*TRADE verifier, or None. Generic-auth surface."""
    t = (text or "").strip()
    return t if _VERIFIER_RE.match(t) else None


def token_status() -> str:
    """'ready' if saved tokens renew, else 'need_auth'. Generic-auth surface."""
    saved = load_tokens()
    if saved and renew_tokens(str(saved["oauth_token"]), str(saved["oauth_secret"])):
        return "ready"
    return "need_auth"
```

- [ ] **Step 4: Make the bot delegate to the auth module**

In `telegram_briefing_bot.py`:

Change `ensure_token` (lines 228-232) to:

```python
    def ensure_token(self) -> str:
        return self.auth.token_status()
```

Change `handle_update`'s verifier branch (lines 305-309) to use the injected module's extractor:

```python
        if self.awaiting_verifier:
            code = self.auth.extract_code(text)
            if code:
                self._complete_auth(chat_id, code)
                return
```

Make the auth-prompt text broker-neutral in `start_auth` (lines 241-245):

```python
        self.tg.send_message(
            chat_id,
            "🔐 Broker authorization needed. Tap to authorize, then send me the "
            f"code (or paste the redirected URL):\n{url}",
        )
```

In `main()` (around lines 393-401), select the auth module by env:

```python
    broker = os.environ.get("PORTFOLIO_BRIEFING_BROKER", "etrade").strip().lower()
    if broker == "schwab":
        import schwab_auth as auth_mod
    else:
        import etrade_auth as auth_mod
    tg = TelegramClient(cfg["token"])
    bot = BriefingBot(
        tg=tg,
        auth=auth_mod,
        runner=lambda: run_briefing(str(repo), delivery_dir, log_dir),
        allowed_id=int(cfg["allowed_id"]),
        state_path=state_dir / "telegram_bot_state.json",
    )
```

(The module-level `extract_verifier` and `_VERIFIER_RE` in the bot stay for the existing tests; they're just no longer on the auth path.)

- [ ] **Step 5: Run the generic-auth tests + the existing bot tests**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/test_telegram_bot_auth_generic.py -v && python3 -m pytest tests/ -k telegram -v`
Expected: PASS — new tests green; any existing telegram bot tests still pass.

- [ ] **Step 6: Commit**

```bash
git add skills/daily-portfolio-briefing/scripts/etrade_auth.py \
        skills/daily-portfolio-briefing/scripts/telegram_briefing_bot.py \
        skills/daily-portfolio-briefing/scripts/tests/test_telegram_bot_auth_generic.py
git commit -m "feat(schwab): broker-neutral auth in the Telegram daemon"
```

---

## Task 8: Config, env, and docs wiring

**Files:**
- Modify: `skills/daily-portfolio-briefing/config/briefing.yaml`
- Modify (or create): `.env.example`
- Modify: `CLAUDE.md` (document the brokerage selection rule)

- [ ] **Step 1: Document the `brokerage` key in `briefing.yaml`**

Add near the `account_desc_whitelist` block (line ~9):

```yaml
# Brokerage backend for live pulls + chain data. "etrade" (default) or "schwab".
# Set to "schwab" only on a Schwab-backed instance. When "schwab", also set
# schwab_account_labels so account_desc_whitelist can match (Schwab exposes
# only CASH/MARGIN as the account 'type', not a nickname).
brokerage: etrade
# schwab_account_labels:
#   "123456789": INDIVIDUAL
```

- [ ] **Step 2: Add Schwab env vars to `.env.example`**

Append (create the file if absent):

```bash
# --- Schwab instance only (leave unset for the E*TRADE setup) ---
# PORTFOLIO_BRIEFING_BROKER=schwab
# SCHWAB_APP_KEY=your_app_key
# SCHWAB_APP_SECRET=your_app_secret
# SCHWAB_REDIRECT_URI=https://127.0.0.1
# SCHWAB_TOKEN_FILE=~/.config/portfolio-briefing/schwab_tokens.json
```

- [ ] **Step 3: Document the rule in `CLAUDE.md`**

Add a short subsection under the chain-data rules:

```markdown
## Brokerage backend — E*TRADE (default) or Schwab, selected by config

The pipeline is broker-pluggable. `briefing.yaml` → `brokerage:` (or env
`PORTFOLIO_BRIEFING_BROKER`) selects the backend; default `etrade` keeps the
existing path byte-identical. `schwab` routes the live snapshot through
`adapters/schwab_adapter.py`, chain data through the env-selected backend in
the canonical chain fetcher and `adapters/broker_market.py`, and the Telegram
daemon's auth through `schwab_auth.py` (OAuth2; re-auth ~weekly). Read-only on
both brokers (hard rule #7). Schwab account scope keys off
`schwab_account_labels` (account number → desc) so `account_desc_whitelist`
gates the same way.
```

- [ ] **Step 4: Verify config still loads**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -c "import yaml; d=yaml.safe_load(open('../config/briefing.yaml')); print('brokerage =', d.get('brokerage'))"`
Expected: `brokerage = etrade`.

- [ ] **Step 5: Commit**

```bash
git add skills/daily-portfolio-briefing/config/briefing.yaml CLAUDE.md .env.example
git commit -m "docs(schwab): document brokerage selection + Schwab env vars"
```

---

## Task 9: Full-suite regression + end-to-end fixture run

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite (default E*TRADE — the invariant check)**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 -m pytest tests/ -v`
Expected: PASS — all pre-existing tests plus the new Schwab tests. Any failure here means an existing path changed; fix before proceeding.

- [ ] **Step 2: Run a fixture briefing end-to-end with the default backend**

Run: `cd skills/daily-portfolio-briefing/scripts && python3 run_briefing.py --etrade-fixture tests/fixtures/<existing_portfolio_fixture>.json --no-deliver 2>&1 | tail -30`
(Use whichever fixture/flag the repo already uses for offline runs; confirm via `python3 run_briefing.py --help`.)
Expected: a briefing renders with no Schwab code on the path (brokerage defaults to etrade).

- [ ] **Step 3: Smoke-test the Schwab backend selection without live creds**

Run:
```bash
cd skills/daily-portfolio-briefing/scripts && \
PORTFOLIO_BRIEFING_BROKER=schwab python3 -c "
import sys; sys.path.insert(0,'.')
from adapters import broker_market
print('schwab backend OptionChainRow:', broker_market.OptionChainRow.__module__)
import schwab_auth
print('token_status (no creds):', schwab_auth.token_status())
"
```
Expected: `...schwab_market` as the module, and `token_status (no creds): need_auth` (fail-closed, no crash).

- [ ] **Step 4: Final commit (if any verification fixups were needed)**

```bash
git add -A && git commit -m "test(schwab): full-suite regression + fixture verification"
```

---

## Self-Review

**Spec coverage:**
- Spec §1 Components → Tasks 1, 2, 4, 6 (parallel modules) + Task 3 (chain backend).
- Spec §1 OAuth2 module → Task 1 (begin/complete/get_access_token/token_status, 30-min/7-day model, own token file/env).
- Spec §2 data adapter (account-numbers→hash, positions/balances, type→accountDesc scope) → Task 4. *Note:* the hash endpoint is wired in `fetch_schwab_snapshot` only where per-account queries need it; the `?fields=positions` bulk call already returns all accounts, so the hash call is optional and omitted unless a single-account query is required — documented in the adapter.
- Spec §3 chain fetcher → Task 3 (generalized in place; deviation flagged at top).
- Spec §4 Telegram bot code-entry generalization → Task 7.
- Spec §5 testing/fail-closed → fixtures + unit tests in Tasks 1-4, regression in Task 9.
- Spec §6 sequencing → Task order matches.
- Spec open items (redirect_uri, account scope by type vs number) → handled via `SCHWAB_REDIRECT_URI` env (Task 1) and `schwab_account_labels` (Tasks 4, 8).

**Placeholder scan:** No TBD/TODO; every code step shows complete code; the only `<placeholder>` is the existing-fixture filename in Task 9 Step 2, which is intentional (depends on the repo's current offline-run fixture — resolved via `--help` in the same step).

**Type consistency:** `OptionChainRow` attributes (`strike, option_type, bid, ask, last, open_interest, delta, gamma, theta, vega, iv`) match `etrade_market`'s and `fetch.py::_row_to_dict`'s duck-typed access. `EtradeSnapshot` is imported and reused (not redefined) in `schwab_adapter`. `fetch_schwab_snapshot` is called with `account_desc_whitelist=` only (the wrapper in Task 5 closes over `account_labels`), matching the E*TRADE call signature the live branch uses. `token_status()`/`extract_code()` exist on both auth modules (Tasks 1, 7) and are the only auth methods the bot calls generically besides `begin_interactive`/`complete_interactive`/`load_tokens`.
