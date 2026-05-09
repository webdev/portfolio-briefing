# E*TRADE MCP Server — Specification

**Status:** Draft v0.1
**Author:** George (with Claude)
**Date:** 2026-05-07
**Goal:** A Model Context Protocol server that exposes E*TRADE brokerage data as tools Claude can call directly, eliminating the need for browser automation, CSV exports, or manual transcription. Read-only first; trade-execution tools deliberately deferred.

---

## Why this exists

The `daily-portfolio-briefing` skill needs four things from a brokerage:

1. Current positions (long-term holds, options, cash)
2. Real-time quotes and recent OHLCV for held tickers
3. Full options chains for any ticker we hold short calls/puts on (the wheel roll decision needs this)
4. Open orders (so we don't double-recommend a trade we've already placed)

Alpaca MCP gives us 1, 2, and parts of 4 — but its options chain support is thin and it doesn't speak to E*TRADE accounts. Browser automation against `us.etrade.com` works but is slow, brittle to UI revisions, and forces every briefing run through OAuth re-auth. A small dedicated MCP server fixes all of this in roughly 1–2 days of work.

## Existing reference: ohenak/etrade-mcp

George pointed at [ohenak/etrade-mcp](https://github.com/ohenak/etrade-mcp). Validated against its README; here is what it covers and what it leaves out, so we know exactly what we extend.

**What ohenak already does well — we crib these patterns wholesale:**

- OAuth 1.0a flow with two-step `setup_oauth(verification_code?)` tool — call once for URL, call again with 5-character code
- Encrypted local token storage (`.etrade_tokens.enc`, key from `ETRADE_TOKEN_KEY` env var)
- Automatic token refresh with exponential backoff for rate limits and activation delays
- `sandbox` vs `production` environment switching via config
- `get_stock_quote(symbol, include_earnings?)` and `get_batch_quotes(symbols[≤25], include_earnings?)`
- `get_option_chains(symbol, expiry_year?, expiry_month?, expiry_day?, chain_type?, strike_price_near?, no_of_strikes?, include_weekly?, skip_adjusted?, option_category?, price_type?)` — returns strikes, expirations, full Greeks (delta/gamma/theta/vega/rho), bid/ask, OI, volume
- `get_option_quote(option_symbols[≤25])` for OSI-format option symbols

**What ohenak deliberately does NOT do — this is our build scope:**

- ❌ No account list / no account balance
- ❌ No positions (the centerpiece of a portfolio briefing)
- ❌ No orders (open or historical)
- ❌ No `get_option_expirations` (chains tool requires you already know expiry; we want a list-expirations helper)

**Decision:** Either (a) fork ohenak and add the missing tools, or (b) build alongside as a complementary "etrade-portfolio-mcp" server. Option (a) is simpler operationally — one MCP, one auth flow, one config — and the original is small enough to fork without burden. **Recommended: fork ohenak.**

A second relevant project is [davdunc/mcp_etrade](https://github.com/davdunc/mcp_etrade), which advertises "OAuth, account management, risk calculations, watch lists, and trading guardrails." Worth a 30-minute read before starting to see whether it's closer to our endpoint than ohenak is. If it has solid `get_positions` already, we may end up forking that instead.

---

## Authentication

E*TRADE's API uses **OAuth 1.0a** (not OAuth 2). This is annoying because:

- Tokens expire **at midnight ET each day** and **after 2 hours of inactivity**
- Renewing requires a browser round-trip to `https://api.etrade.com/oauth/authorize`
- There is no refresh token; you re-auth from scratch every session

**MCP behavior (mirroring ohenak):**

- Encrypted token storage at `.etrade_tokens.enc` with symmetric key from `ETRADE_TOKEN_KEY` env var
- Two-step `setup_oauth(verification_code?)` tool — first call returns authorize URL, second call with 5-character verifier exchanges for access token
- Automatic refresh with exponential backoff for rate limits and token activation delays
- `auth_status()` diagnostic tool to check current state without forcing a re-auth
- Tokens are environment-isolated: `ETRADE_ENV=sandbox` vs `ETRADE_ENV=production`
- **Never** log tokens or write them unencrypted to disk

**Consumer key/secret:** environment variables `ETRADE_CONSUMER_KEY` / `ETRADE_CONSUMER_SECRET`. These come from the E*TRADE developer portal (one-time application). Stored in `.env` which is gitignored.

**Re-auth UX during a briefing run:** if token expires mid-run, the skill catches `AUTH_EXPIRED`, surfaces the verifier URL to the user, and aborts that run cleanly. The user re-auths and re-runs. The briefing should not silently spin waiting for re-auth.

---

## Tool surface (read-only, v1)

All tools return JSON. Numeric fields are typed (no string-encoded numbers). Timestamps are ISO 8601 with timezone.

### Account & positions

#### `list_accounts()`
List all accounts attached to the authenticated user.

```json
[
  {
    "accountId": "12345678",
    "accountIdKey": "abc123def456",
    "accountType": "INDIVIDUAL",
    "institutionType": "BROKERAGE",
    "accountStatus": "ACTIVE",
    "accountDescription": "Brokerage Account"
  }
]
```

`accountIdKey` is what every other tool wants — `accountId` is just for display.

#### `get_account_balance(accountIdKey)`
```json
{
  "accountIdKey": "abc123def456",
  "accountType": "INDIVIDUAL",
  "totalAccountValue": 487231.45,
  "cashBalance": 24180.00,
  "marginBuyingPower": 96720.00,
  "settledCash": 24180.00,
  "unsettledCash": 0.00,
  "longMarketValue": 463051.45,
  "shortMarketValue": 0.00,
  "asOf": "2026-05-07T16:00:00-04:00"
}
```

#### `get_positions(accountIdKey, options?)`
Options: `{ symbolFilter?: string[], assetType?: "EQUITY"|"OPTION"|"ALL" }`

Returns a flat list. Equities and options come back together but with different fields populated.

```json
[
  {
    "symbol": "AAPL",
    "assetType": "EQUITY",
    "quantity": 200,
    "averageCost": 158.20,
    "marketValue": 36240.00,
    "lastPrice": 181.20,
    "unrealizedPL": 4600.00,
    "unrealizedPLPct": 14.54,
    "dayChange": 132.00,
    "dayChangePct": 0.37,
    "longShort": "LONG",
    "dateAcquired": "2024-08-12"
  },
  {
    "symbol": "AAPL  260619P00170000",
    "underlyingSymbol": "AAPL",
    "assetType": "OPTION",
    "optionType": "PUT",
    "strike": 170.00,
    "expiration": "2026-06-19",
    "daysToExpiration": 43,
    "quantity": -2,
    "averageCost": 4.85,
    "marketValue": -640.00,
    "lastPrice": 3.20,
    "unrealizedPL": 330.00,
    "longShort": "SHORT",
    "dateAcquired": "2026-04-22"
  }
]
```

The OCC option symbol format (`AAPL  260619P00170000`) is preserved exactly so it can be passed back to other tools.

### Quotes

#### `get_quote(symbol)` and `get_quotes(symbols)`
```json
{
  "symbol": "AAPL",
  "lastPrice": 181.20,
  "bid": 181.18,
  "ask": 181.22,
  "bidSize": 100,
  "askSize": 200,
  "open": 180.50,
  "high": 181.85,
  "low": 180.10,
  "previousClose": 180.05,
  "volume": 42100000,
  "timestamp": "2026-05-07T16:00:00-04:00"
}
```

E*TRADE rate-limits to ~25 quotes per request. The MCP should batch transparently.

### Options chains

This is the centerpiece for the wheel-roll decision.

#### `get_option_expirations(symbol, options?)`
Options: `{ expirationType?: "WEEKLY"|"MONTHLY"|"QUARTERLY"|"ALL" }`

```json
[
  { "expiration": "2026-05-09", "expirationType": "WEEKLY" },
  { "expiration": "2026-05-16", "expirationType": "WEEKLY" },
  { "expiration": "2026-05-23", "expirationType": "WEEKLY" },
  { "expiration": "2026-06-19", "expirationType": "MONTHLY" },
  { "expiration": "2026-07-17", "expirationType": "MONTHLY" }
]
```

#### `get_option_chain(symbol, expiration, options?)`
Options: `{ strikePriceNear?: number, strikeRange?: number, includeGreeks?: boolean }`

`strikePriceNear` + `strikeRange=10` returns 10 strikes either side. Defaults to 5 either side of the underlying price (full chain bandwidth is wasteful for 99% of queries).

```json
{
  "symbol": "AAPL",
  "expiration": "2026-06-19",
  "underlyingPrice": 181.20,
  "underlyingDayChange": 1.15,
  "calls": [
    {
      "optionSymbol": "AAPL  260619C00180000",
      "strike": 180.00,
      "bid": 5.40,
      "ask": 5.55,
      "lastPrice": 5.48,
      "volume": 1240,
      "openInterest": 8400,
      "impliedVolatility": 0.2350,
      "delta": 0.5421,
      "gamma": 0.0178,
      "theta": -0.0820,
      "vega": 0.1942,
      "intrinsicValue": 1.20,
      "extrinsicValue": 4.28
    }
  ],
  "puts": [ /* same shape */ ]
}
```

Greeks are computed by E*TRADE and returned natively when `includeGreeks=true` (default true).

### Orders

#### `list_orders(accountIdKey, options?)`
Options: `{ status?: "OPEN"|"EXECUTED"|"CANCELED"|"ALL", fromDate?, toDate?, symbolFilter? }`

Lists orders. Read-only. Default returns only OPEN orders from the last 30 days.

```json
[
  {
    "orderId": "987654",
    "status": "OPEN",
    "orderType": "LIMIT",
    "symbol": "AAPL  260619P00170000",
    "underlyingSymbol": "AAPL",
    "side": "BUY_TO_CLOSE",
    "quantity": 2,
    "limitPrice": 1.50,
    "stopPrice": null,
    "timeInForce": "GTC",
    "placedAt": "2026-05-06T09:30:15-04:00"
  }
]
```

### Diagnostics

#### `auth_status()`
```json
{
  "authenticated": true,
  "tokenExpiresAt": "2026-05-08T00:00:00-04:00",
  "environment": "production",
  "consumerKeyFingerprint": "ab3f...c2"
}
```

#### `reauthenticate(verifierCode?)`
If called with no args, returns the authorize URL. If called with the 5-digit code, exchanges for an access token.

---

## Tools deliberately NOT in v1

These exist in the E*TRADE API but I am leaving them out of the first cut:

- `place_order` / `preview_order` — too easy to wire up and accidentally fire. Defer until the briefing has run reliably for a month.
- `cancel_order` — same reason, also implies you're already trading from the briefing.
- `get_transactions` — useful for postmortem but trader-memory-core handles its own P&L. Defer.
- `get_alerts`, `get_market_movers`, etc. — ancillary, not on the critical path.

When `place_order` is added (v2), it should require an explicit `confirmationToken` parameter that the user supplies in chat — so Claude cannot fire an order from a briefing recommendation alone.

---

## Error model

All errors return a structured object the skill can react to:

```json
{
  "error": "AUTH_EXPIRED",
  "message": "OAuth token rejected by E*TRADE; reauthentication required",
  "remediation": "Call reauthenticate() to get a fresh authorization URL",
  "etrade_response_code": 401
}
```

Error codes the briefing skill needs to handle:

| Code | Meaning | Briefing behavior |
|---|---|---|
| `AUTH_EXPIRED` | Token rejected | Prompt user to re-auth, abort briefing |
| `RATE_LIMITED` | Too many requests | Back off and retry once with exponential delay |
| `SYMBOL_NOT_FOUND` | Bad ticker | Skip that position, continue with rest |
| `ACCOUNT_NOT_FOUND` | Bad accountIdKey | Re-list accounts, retry |
| `MARKET_CLOSED` | Some endpoints return stale data outside hours | Note in report header, proceed |

---

## Build plan

**Day 0 (prep, ~2 hours):** Fork ohenak/etrade-mcp. Read its source end-to-end (it's small). Clone locally, install in dev mode, run `setup_oauth` against sandbox to confirm the auth flow works on your machine.

**Day 1:** Add account-tier tools. `list_accounts`, `get_account_balance`, `get_positions`, `get_option_expirations`. These all call the E*TRADE accounts API endpoints (different from the market-data endpoints ohenak already wraps). OAuth, retry, encrypted token storage are inherited.

**Day 2:** Add `list_orders`. Sanity-check against sandbox: confirm `get_positions` parses both equity and option positions correctly (the OCC option symbol format is the gotcha). Wire the structured error model.

**Day 3:** Smoke tests against sandbox, packaging (`uv build` or `pip install -e .`), MCP server registration in Claude Desktop config. Document the daily re-auth flow in a short runbook.

**Day 4 (optional):** Production cutover. Re-run sanity checks against the live account. First briefing run.

---

## Open questions for George

1. **Sandbox vs production first?** E*TRADE has a sandbox with fake data — useful for validating tool shapes without burning real-account quota. Recommend: build & test in sandbox, switch to production only after MCP tools return clean shapes.
2. **Multi-account?** If you have IRA + taxable + joint, the briefing needs to know which to analyze. Default to "all accounts" with breakdown by account type, or pick one primary?
3. **Where does the MCP run?** Desktop Claude only, or do you want it to be runnable from Claude Code too? (Latter means packaging it for `uvx`/`pipx` install.)
4. **Re-auth UX:** Are you OK with "Claude pauses, asks you to click a link, paste a code" once a day? Or do you want to script the OAuth flow with Selenium so the briefing can run unattended? (The latter is technically against E*TRADE's TOS but everyone does it.)
