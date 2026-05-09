# Daily briefing — launchd install

Schedules `run_briefing.py --etrade-live` Mon-Fri at 08:00 ET and copies the
released briefing to `~/Documents/briefings/`.

## One-time install

```bash
# 1. Make sure the wrapper is executable
chmod +x ~/workspace/portfolio-briefing/skills/daily-portfolio-briefing/scripts/run_briefing_scheduled.sh

# 2. Copy the plist into your LaunchAgents directory
cp ~/workspace/portfolio-briefing/skills/daily-portfolio-briefing/launchd/com.gblazer.daily-briefing.plist \
   ~/Library/LaunchAgents/

# 3. Register and enable it
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.gblazer.daily-briefing.plist
launchctl enable gui/$UID/com.gblazer.daily-briefing
```

## Verify

```bash
# Show registration
launchctl list | grep daily-briefing

# Print the parsed schedule
launchctl print gui/$UID/com.gblazer.daily-briefing

# Run it once right now (don't wait for 8am)
launchctl kickstart -k gui/$UID/com.gblazer.daily-briefing

# Watch logs
tail -f ~/Documents/briefings/logs/briefing_$(date +%Y-%m-%d).log
```

## Customize

Common knobs all live in EnvironmentVariables of the plist (or you can leave
them empty and use the defaults baked into `run_briefing_scheduled.sh`):

| Key                                  | Default                                                  | What it does                              |
|--------------------------------------|----------------------------------------------------------|-------------------------------------------|
| `PORTFOLIO_BRIEFING_REPO`            | `~/workspace/portfolio-briefing`                         | Where this repo lives                     |
| `PORTFOLIO_BRIEFING_TOKEN_FILE`      | `~/.config/portfolio-briefing/etrade_tokens.json`        | E*TRADE OAuth token storage               |
| `PORTFOLIO_BRIEFING_DELIVERY_DIR`    | `~/Documents/briefings`                                  | Final delivery directory                  |
| `PORTFOLIO_BRIEFING_FETCH_WORKERS`   | `16`                                                     | Parallel yfinance worker count            |
| `PORTFOLIO_BRIEFING_PYTHON`          | `/usr/bin/python3`                                       | Python interpreter (override for venvs)   |
| `TZ`                                 | `America/New_York`                                       | Timezone for StartCalendarInterval        |

E*TRADE consumer credentials (`ETRADE_CONSUMER_KEY`, `ETRADE_CONSUMER_SECRET`) live in `$PORTFOLIO_BRIEFING_REPO/.env` (gitignored).

## Uninstall

```bash
launchctl bootout gui/$UID/com.gblazer.daily-briefing
rm ~/Library/LaunchAgents/com.gblazer.daily-briefing.plist
```

## Troubleshooting

- **No file in `~/Documents/briefings/` after 8 AM** — check the day's log:
  `~/Documents/briefings/logs/briefing_YYYY-MM-DD.log`. If empty, check the
  emergency catch logs at `~/Documents/briefings/logs/launchd.{out,err}.log`.
- **E*TRADE 401** — tokens expired at midnight ET; re-authenticate via the
  in-repo flow:
  ```bash
  cd ~/workspace/portfolio-briefing/skills/daily-portfolio-briefing/scripts
  python3 etrade_auth.py
  ```
- **yfinance rate limits** — drop `PORTFOLIO_BRIEFING_FETCH_WORKERS` to 8.
