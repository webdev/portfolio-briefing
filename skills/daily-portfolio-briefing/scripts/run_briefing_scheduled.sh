#!/usr/bin/env bash
# Wrapper for the daily 8am ET launchd run.
#
# launchd hands us a near-empty environment (no PATH, no cwd), so this script
# pins everything explicitly: working directory, PYTHONPATH, E*TRADE token
# location, log path. Edit the variables in the "Configuration" block below
# to match your machine before installing the plist.
#
# Logs are appended to ~/Documents/briefings/logs/briefing_YYYY-MM-DD.log so
# you can read what happened without a terminal session open.

set -uo pipefail

# ---------------- Configuration ----------------
# Repo root for portfolio-briefing — drives .env and module imports
REPO_ROOT="${PORTFOLIO_BRIEFING_REPO:-$HOME/workspace/portfolio-briefing}"
# Token file location (default ~/.config/portfolio-briefing/etrade_tokens.json)
TOKEN_FILE="${PORTFOLIO_BRIEFING_TOKEN_FILE:-$HOME/.config/portfolio-briefing/etrade_tokens.json}"
# Python executable to use (override with PORTFOLIO_BRIEFING_PYTHON)
PYTHON_BIN="${PORTFOLIO_BRIEFING_PYTHON:-/usr/bin/python3}"
# Delivery target — where the released briefing lands
DELIVERY_DIR="${PORTFOLIO_BRIEFING_DELIVERY_DIR:-$HOME/Documents/briefings}"
# Log directory and file
LOG_DIR="$DELIVERY_DIR/logs"
LOG_FILE="$LOG_DIR/briefing_$(date +%Y-%m-%d).log"
# -----------------------------------------------

mkdir -p "$LOG_DIR"

# Append everything below this line to the log file
exec >> "$LOG_FILE" 2>&1

echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Daily briefing run starting"
echo "  REPO_ROOT=$REPO_ROOT"
echo "  TOKEN_FILE=$TOKEN_FILE"
echo "  PYTHON_BIN=$PYTHON_BIN"
echo "  DELIVERY_DIR=$DELIVERY_DIR"
echo "============================================================"

if [[ ! -d "$REPO_ROOT" ]]; then
    echo "FATAL: REPO_ROOT does not exist: $REPO_ROOT"
    exit 2
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "FATAL: PYTHON_BIN not executable: $PYTHON_BIN"
    exit 2
fi

cd "$REPO_ROOT/skills/daily-portfolio-briefing"

# Make the parallel-fetch knob explicit; can be tuned via launchd EnvVars
export PORTFOLIO_BRIEFING_FETCH_WORKERS="${PORTFOLIO_BRIEFING_FETCH_WORKERS:-16}"
export PORTFOLIO_BRIEFING_DELIVERY_DIR="$DELIVERY_DIR"
export PORTFOLIO_BRIEFING_REPO="$REPO_ROOT"
export PORTFOLIO_BRIEFING_TOKEN_FILE="$TOKEN_FILE"

# Run the briefing. --etrade-live pulls real positions/balance from E*TRADE.
# Stdout/stderr are already redirected to LOG_FILE above.
"$PYTHON_BIN" scripts/run_briefing.py \
    --config config/briefing.yaml \
    --etrade-live
EXIT=$?

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Briefing run exited with status $EXIT"
exit $EXIT
