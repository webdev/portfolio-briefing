# Schwab brokerage support (read-only briefing) — design

Date: 2026-05-28
Status: approved (design), pending implementation plan

## Goal

Let a second user run the daily portfolio briefing against a **Charles Schwab**
account, delivered via Telegram with the same interactive code-entry auth and
6:30 scheduled fire the E*TRADE setup already has. The friend runs his **own
separate instance** (own machine, bot token, Schwab credentials, token file,
state, delivery dir). The tool stays **read-only** — no order placement (honors
CLAUDE.md hard rule #7).

## Non-negotiable invariant — do not break the E*TRADE path

This is approach B (parallel modules + thin config selector) specifically so the
existing, live E*TRADE setup keeps working bit-for-bit:

- No existing E*TRADE file is modified except the small, additive dispatch
  points listed below.
- `config["brokerage"]` defaults to `"etrade"`. With the flag absent or set to
  `etrade`, every code path is identical to today.
- The two brokerages never run in the same process (separate instances), so no
  runtime coexistence/refactor is required.
- Verification before "done": the full existing E*TRADE test suite passes
  unchanged, and a dry run with no `brokerage` key produces byte-identical
  routing to the current behavior.

## Components

All new code is a sibling of the E*TRADE equivalents; nothing is moved or
rewritten.

```
skills/daily-portfolio-briefing/scripts/
  adapters/
    schwab_adapter.py      # fetch_schwab_snapshot() -> EtradeSnapshot-shaped result
    schwab_market.py       # get_option_chain / get_option_expirations (mirror etrade_market.py)
  schwab_auth.py           # OAuth2 auth-code flow, parallel to etrade_auth.py
skills/schwab-chain-fetcher/
  SKILL.md
  scripts/fetch.py         # same public surface as etrade-chain-fetcher
```

Additive dispatch points (the only edits to existing files):

- `snapshot_inputs.py` — branch on `config["brokerage"]` to call
  `fetch_schwab_snapshot(...)` vs `fetch_etrade_snapshot(...)`.
- chain layer (`new_ideas.py` + chain-fetcher selection) — pick the Schwab vs
  E*TRADE fetcher by the same flag.
- `telegram_briefing_bot.py` — pick which auth module to drive, and generalize
  the verifier-code parsing (see §5).

**The return shape is the contract.** `schwab_adapter` produces the exact
`EtradeSnapshot` dataclass and position dicts the rest of the pipeline already
consumes, so no downstream step, renderer, or rule changes.

## 1. Schwab OAuth2 (`schwab_auth.py`)

Mirrors `etrade_auth.py`'s public surface so the bot flow is conceptually
unchanged.

- `begin_interactive()` → builds
  `https://api.schwabapi.com/v1/oauth/authorize?client_id=…&redirect_uri=…`,
  returns the URL.
- Friend opens it, logs into Schwab, approves. Schwab redirects his browser to
  the registered `redirect_uri` (for individual devs typically
  `https://127.0.0.1`) with `?code=…&session=…`. The browser shows a connection
  error; the URL bar holds the code. He copies the URL (or just the code) into
  Telegram.
- `complete_interactive(pasted)` → extracts `code=…`, URL-decodes it, exchanges
  at `POST /v1/oauth/token` (`grant_type=authorization_code`, HTTP Basic auth
  with `client_id:secret`), stores **access_token (30 min) + refresh_token
  (7 days)**.
- `get_session(try_renew=True)` → silently refreshes via
  `grant_type=refresh_token` when the access token is near expiry (~29-min
  cadence). When the 7-day refresh token is dead, signal "re-auth needed" so the
  bot re-prompts for a fresh code.

Tokens: own file, `SCHWAB_TOKEN_FILE` env (default
`~/.config/portfolio-briefing/schwab_tokens.json`, mode 0600). App credentials:
`SCHWAB_APP_KEY` / `SCHWAB_APP_SECRET`.

### Auth cadence — differs from E*TRADE (UX note)

| | E*TRADE (today) | Schwab |
|---|---|---|
| Grant | OAuth1 verifier code | OAuth2 auth-code redirect |
| Access token life | session, idle 2h, hard-expires midnight ET | 30 min |
| Auto-renew | `renew_access_token` | refresh token, silent, ~29 min |
| Manual re-login | ~daily | every 7 days |

So the friend pastes a code roughly **once a week**, not every morning.

## 2. Data adapter (`schwab_adapter.py`)

- `GET /trader/v1/accounts/accountNumbers` → map plain account numbers to the
  encrypted hash Schwab requires for per-account queries.
- `GET /trader/v1/accounts?fields=positions` → positions + balances.
- Normalize to existing shapes: equities (symbol, qty, price, marketValue,
  costBasis, gains) and options (underlying, type, strike, expiration, qty,
  premium, mid/bid/ask, Greeks, IV, OI).
- **Account scope:** Schwab has no `accountDesc` like "INDIVIDUAL". The adapter
  maps Schwab's `securitiesAccount.type` (and/or account number) into the same
  `accountDesc` field so the existing `account_desc_whitelist` config gates
  which account is in scope (honors CLAUDE.md account-scope rule). His instance
  sets the whitelist to his own account type/number.

## 3. Chain fetcher (`schwab-chain-fetcher` skill)

Same public surface as `etrade-chain-fetcher`: `is_available`,
`availability_reason`, `list_expirations`, `choose_expiration`, `get_chain`,
`find_strike_at_otm_pct`, `find_strike_near_delta`, `quote_contract`. Backed by
`GET /marketdata/v1/chains`. Schwab returns Greeks, so the delta-band covered-
call selection (rule #16) works. Keeps rule #2 satisfied for his instance —
tradeable chain prices come from the broker, never yfinance. Same fail-closed
contract: `None` means no data, never substitute (rule #10).

## 4. Telegram bot generalization

Only real change is the code-entry step. Today it matches a 5-char verifier
(`^[A-Za-z0-9]{5}$`). Generalize: when `brokerage == schwab`, accept a pasted
redirect URL or long code and parse `code=…` out of it (URL-decode). Everything
else — scheduled 6:30 fire, digest rendering, single `allowed_id`, subprocess
trigger — is unchanged. His instance sets its own `TELEGRAM_BRIEFING_BOT_TOKEN`
and `TELEGRAM_BRIEFING_ALLOWED_ID`.

## 5. Testing & fail-closed (no live creds yet)

- `schwab_fixture` path parallel to the existing `etrade_fixture`, with a
  captured/synthetic Schwab JSON response, so the whole pipeline is testable
  end-to-end before his credentials exist.
- Unit tests: OAuth token exchange/refresh parsing, redirect-code extraction,
  account-hash mapping, snapshot normalization (assert output matches the
  `EtradeSnapshot` shape and existing position dict keys).
- Regression guard: existing E*TRADE test suite passes unchanged; absent
  `brokerage` key routes exactly as today.
- **Live validation pass** deferred until he provides `SCHWAB_APP_KEY` /
  `SCHWAB_APP_SECRET` and completes the first browser auth.

## Sequencing

1. `schwab_auth.py` (OAuth2 + token store)
2. `schwab_adapter.py` + `schwab_market.py` against the fixture
3. `schwab-chain-fetcher` skill
4. config dispatch (`brokerage` flag) + bot code-entry tweak
5. live validation when credentials arrive

## Open items

- Exact `redirect_uri` he registers on the Schwab developer portal (drives the
  paste-flow parsing). Default assumption: `https://127.0.0.1`.
- Whether his account scope keys off Schwab account *type* or a specific account
  number (both supported by the normalization).
- Confirm the FMP-based intrinsic-value and yfinance-technicals steps are
  broker-agnostic (they are — they key off ticker symbols, not the broker), so
  they need no Schwab-specific work.
