# Telegram Briefing Bot — Setup & Runbook

Always-on daemon that prompts for the E*TRADE verifier on Telegram, runs the
briefing at 6:30 local + on demand, and delivers a summary plus the full
briefing file to your phone. Uses a DEDICATED bot (@gbriefing_bot), separate
from any Claude Code telegram channel, so there is no single-consumer conflict.

## Prerequisites
- `.env` at the repo root (`/Users/gblazer/workspace/portfolio-briefing/.env`) contains:
  - `TELEGRAM_BRIEFING_BOT_TOKEN=<token from @BotFather for @gbriefing_bot>`
  - `TELEGRAM_BRIEFING_ALLOWED_ID=5244308999`  (your numeric Telegram user id)
  - the existing `ETRADE_CONSUMER_KEY` / `ETRADE_CONSUMER_SECRET` (used by the OAuth flow)
- `uv` installed (`/Users/gblazer/.local/bin/uv`) and `uv sync` run in the repo
  so `.venv` exists with all deps.

## Interpreter note (important)
The code requires Python 3.10+. Do NOT use `/usr/bin/python3` (3.9). Everything
runs through uv / the repo `.venv`. The LaunchAgent sets `PORTFOLIO_BRIEFING_PYTHON`
to `<repo>/.venv/bin/python` so the briefing subprocess uses the right interpreter too.

## Manual smoke test (before installing the agent)
From the repo root `/Users/gblazer/workspace/portfolio-briefing`:
```
uv run python skills/daily-portfolio-briefing/scripts/telegram_briefing_bot.py
```
Then in Telegram, DM @gbriefing_bot the word `run`. Expect:
1. "⏳ Running your briefing…", then a summary, then the full briefing `.md` attached.
2. Token-dead path: it sends an authorize link; tap it, log in, copy the 5-char
   code, send it back; the run proceeds.
Ctrl-C to stop.

## Install the LaunchAgent
1. `cp skills/daily-portfolio-briefing/assets/launchd/com.portfolio-briefing.telegram-bot.plist ~/Library/LaunchAgents/`
2. (Only if your paths differ from the defaults) edit the copy to match.
3. `launchctl load ~/Library/LaunchAgents/com.portfolio-briefing.telegram-bot.plist`
4. Verify: `launchctl list | grep telegram-bot` (a PID and exit code 0).
5. Logs: `~/Documents/briefings/logs/telegram_bot.{out,err}.log`.

## Crash / restart check
`kill <pid>` then `launchctl list | grep telegram-bot` — a new PID appears (KeepAlive).

## Uninstall
`launchctl unload ~/Library/LaunchAgents/com.portfolio-briefing.telegram-bot.plist`

## Notes
- @gbriefing_bot must be the ONLY consumer of its token — don't run the Claude
  Code telegram channel on it.
- At 6:30 the E*TRADE token is essentially always dead (midnight-ET expiry), so
  the morning run will prompt for a code. Midday on-demand runs usually won't —
  the hourly `renew_etrade_token.py` heartbeat keeps the token warm.
- State (offset + last fire date) and the single-instance pidfile live in
  `skills/daily-portfolio-briefing/scripts/state/`.
