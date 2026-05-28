# Telegram Briefing Bot — Design

**Date:** 2026-05-28
**Status:** Approved (design); pending implementation plan
**Author:** George + Claude

## Problem

The daily portfolio briefing needs to be controllable from the phone:

1. Run automatically at **6:30 in the morning (Mac local time)**.
2. Deliver the finished briefing to the phone.
3. Allow on-demand runs ("run it now") at any time.

The hard constraint is E*TRADE OAuth 1.0a. Access tokens die at **midnight ET**
(hard expiry), and reviving one requires the interactive browser flow: log into
E*TRADE, accept the app, and copy a **5-character verifier code**. There is no
API to obtain that code — it is irreducibly manual. So a fully unattended 6:30am
run is impossible; the best achievable is ~30 seconds of tapping on the phone
(tap link, log in, copy code, send it back), after which everything is automatic.

The hourly heartbeat (`renew_etrade_token.py`) only resets the 2-hour *idle*
clock; it cannot revive a token past the midnight-ET hard expiry. So the morning
path will almost always need a fresh code; midday on-demand runs often will not
(the token is still warm).

### Why not the Claude Code Telegram channel plugin

It was evaluated and rejected for this job:

- It is **per-session and CLI-flag-only** (`claude --channels
  plugin:telegram@claude-plugins-official`) — an unattended 6:30 job would need a
  Claude session sitting open with that flag.
- It is **single-consumer**: only one process may poll a bot token's
  `getUpdates`. Running Claude across ~10 workspaces means every session spawns a
  poller that 409-fights for the slot.

The plugin is fine for *interactive* "chat with Claude from my phone." The
scheduled briefing needs an independent, always-on owner instead.

## Decisions (from brainstorming)

| Question | Decision |
|----------|----------|
| Trigger model | **Always-on service**: auto at 6:30 + on-demand "run" |
| Bot token | **Dedicated new bot** (e.g. `@wheelhouz_briefing_bot`), separate from `@wheelhouzzz_bot` so it never collides with the Claude Code channel |
| Delivery format | **Short summary inline + full briefing `.md` attached** |
| Schedule | **6:30 Mac-local time; wait for the code** (single reminder, no hard timeout) |
| Access | Hard-locked to one Telegram user ID (`5244308999`) |

## Approach

**Python long-poller daemon** (chosen over forking the grammy/TS plugin server,
and over a two-piece launchd-cron + poller split).

A single always-on Python script in the briefing repo that:

- long-polls the Telegram Bot API directly with `requests` (no bun, no grammy, no
  new framework dependency),
- reuses `etrade_auth.py` **in-process** (no IPC bridge),
- shells out to the existing `run_briefing_scheduled.sh`,
- runs under a launchd `KeepAlive` LaunchAgent (always-on, self-restarting).

## Architecture & Components

New file: `scripts/telegram_briefing_bot.py` — the daemon. Thin Telegram I/O
shell around pure, unit-testable logic.

- **Telegram I/O:** raw `requests` calls to `getUpdates` (long poll),
  `sendMessage`, `sendDocument`.
- **Auth bridge:** refactor `etrade_auth.py` to split the interactive flow:
  - `begin_interactive() -> (authorize_url, oauth_handle)`
  - `complete_interactive(oauth_handle, verifier, sandbox=False) -> ETradeSession`
    (exchanges the verifier and calls `save_tokens`)
  - existing `authenticate_interactive()` is reimplemented to call both around
    its `input()` prompt, so the CLI path is unchanged.
- **Runner:** subprocess call to `run_briefing_scheduled.sh` (already pins env,
  `--etrade-live`, logging), then read `~/Documents/briefings/latest.md`.
- **Summary extractor:** a small function that pulls the top action-list /
  headline section from `latest.md`; falls back to the first ~1500 chars if the
  expected header isn't found.
- **launchd:** a `KeepAlive` LaunchAgent plist (loads at login, restarts on
  crash). The 6:30 trigger lives *inside* the daemon loop, not in launchd.
- **Access control:** every update's sender ID is checked against the configured
  allowed ID; non-matches are silently dropped (logged).

## Morning Flow & On-Demand (shared path)

The daemon loop tracks `next_fire = today 6:30 local` and recomputes after each
fire. Both the 6:30 trigger and a "run"/"briefing" text funnel into one sequence:

1. **`ensure_token()`** — load saved tokens, try `renew_tokens()`.
   - Renewal succeeds (token still warm, e.g. midday on-demand) → go to step 4.
   - Renewal fails (typical 6:30 case, token died at midnight ET) → step 2.
2. **Send auth prompt** — `begin_interactive()` yields the authorize URL; DM:
   > 🔐 E*TRADE token expired. Tap to authorize, then send me the 5-char code:
   > `<authorize_url>`

   Store `oauth_handle` in memory; set state `awaiting_verifier`. A single
   reminder fires after ~20 min if unanswered; otherwise wait (no hard timeout).
3. **Receive code** — a 5-char alphanumeric message while `awaiting_verifier` →
   `complete_interactive(oauth_handle, code)` → exchange + `save_tokens`.
   - Bad code → "❌ that didn't work, re-tap the link and resend the code,"
     stays awaiting. → step 4 on success.
4. **Run** — reply "⏳ Running your briefing…", subprocess
   `run_briefing_scheduled.sh`, capture exit code.
5. **Deliver** —
   - success → `sendMessage` the extracted summary, then `sendDocument` the full
     `latest.md`.
   - failure (non-zero exit) → send "⚠️ briefing failed" + tail of today's log.
6. **Re-arm** — recompute `next_fire` for tomorrow 6:30. On-demand runs do not
   disturb the schedule.

## State, Persistence & Error Handling

**In-memory state:**
- `oauth_handle` — `pyetrade` request-token object; valid only between sending
  the link and receiving the code.
- `awaiting_verifier`, `pending_chat_id` — auth state.
- `next_fire` — next 6:30 local. `busy` — guards a second run while one is in
  flight.

**Persisted state** — `state/telegram_bot_state.json`:
- `update_offset` — Telegram `getUpdates` offset; a restart must not reprocess
  old messages or replay yesterday's "run."
- `last_fire_date` — prevents a 6:31 restart from double-firing today.
- Tokens stay in `~/.config/portfolio-briefing/etrade_tokens.json` (0600, via
  `etrade_auth.save_tokens`). The dedicated bot token + allowed user ID live in
  the repo `.env` (gitignored): `TELEGRAM_BRIEFING_BOT_TOKEN`,
  `TELEGRAM_BRIEFING_ALLOWED_ID`.

**Error handling:**
- **Crash mid-auth:** `oauth_handle` is in-memory only; after a restart an
  incoming code cannot match. Detect "code arrived, no live handle" → reply
  "session reset" and send a **fresh** link (old code is useless).
- **Bad/expired verifier:** caught → "❌ try again," stays awaiting.
- **Network errors on `getUpdates`:** retry with backoff; the loop never dies.
- **Single-instance lock:** a pidfile guard so two daemons never poll the bot at
  once (the 409 problem). With a dedicated bot there is nothing else to collide
  with, but the guard stays.
- **Briefing failure:** non-zero exit → send log tail; daemon keeps running.
- **Unauthorized senders:** user ID ≠ allowed ID → silently dropped (logged).
- **Logs:** append to `~/Documents/briefings/logs/telegram_bot_YYYY-MM-DD.log`.

## Testing

**Unit tests** (`scripts/tests/test_telegram_briefing_bot.py`, pytest; Telegram
API, `etrade_auth`, and the briefing subprocess all mocked):
- Access control: allowed ID processed; other IDs dropped.
- `ensure_token`: renew-succeeds → no prompt; renew-fails → prompt +
  `awaiting_verifier`.
- Verifier handling: valid code while awaiting → completes + runs; bad code →
  error, stays awaiting; 5-char code while NOT awaiting → treated as normal text.
- Crash-mid-auth recovery: code with no in-memory handle → "session reset" +
  fresh link.
- Schedule arithmetic: `next_fire` rolls to tomorrow 6:30; `last_fire_date`
  blocks a 6:31 double-fire.
- Offset persistence: processed `update_id` advances `update_offset`; updates
  below the offset are skipped.
- Busy guard: "run" while running → "already running," no second subprocess.
- Delivery: success → summary + document; failure → log-tail message.

**Manual end-to-end** (documented; not unit-testable):
1. Start daemon by hand → text "run" → link arrives → code → briefing delivers
   with summary + attached `.md`.
2. Kill mid-auth → resend code → "session reset" + fresh link.
3. Install LaunchAgent → survives logout/login and a crash (`kill` pid → respawn).
4. Temporarily set the fire time to the near future → morning path runs end to end.

**Not building:** a fake Telegram server or live E*TRADE integration tests —
boundaries are mocked; real wiring is covered by the manual checklist.

## Out of scope

- Multi-user access (single user only).
- Sharing one bot between the briefing daemon and the Claude Code channel
  (dedicated bot avoids it).
- Placing trades / any write action against brokerages (read-only, per project
  rules).
- Replacing the hourly `renew_etrade_token.py` heartbeat (it stays).

## Prerequisites for implementation

- A dedicated Telegram bot created via @BotFather; its token placed in `.env` as
  `TELEGRAM_BRIEFING_BOT_TOKEN`.
- `TELEGRAM_BRIEFING_ALLOWED_ID=5244308999` in `.env`.
