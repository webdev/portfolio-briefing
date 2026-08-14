#!/usr/bin/env bash
# Wrapper for the weekday 6:30 AM scout pre-warm launchd job.
#
# OPTIONAL — the briefing pipeline self-refreshes the scout whenever the
# cache is older than briefing.yaml `scout.cache_ttl_hours` (default 6h) or
# predates today (`scout.force_refresh_morning`). This job just pre-warms
# state/briefing_snapshots/scout_cache.json so the scheduled briefing run
# (8:00 AM / 6:48 checks) starts hot instead of spending 30-60s on the
# scout research. If this job never runs, nothing breaks.
#
# launchd hands us a near-empty environment (no PATH, no cwd), so this script
# pins everything explicitly, matching run_briefing_scheduled.sh. Logs are
# appended to ~/Documents/briefings/logs/scout_prewarm_YYYY-MM-DD.log.

set -uo pipefail

# ---------------- Configuration ----------------
# Repo root for portfolio-briefing — drives .env and module imports
REPO_ROOT="${PORTFOLIO_BRIEFING_REPO:-$HOME/workspace/portfolio-briefing}"
# Python executable to use (override with PORTFOLIO_BRIEFING_PYTHON)
PYTHON_BIN="${PORTFOLIO_BRIEFING_PYTHON:-/usr/bin/python3}"
# Delivery target — only used for the log directory here
DELIVERY_DIR="${PORTFOLIO_BRIEFING_DELIVERY_DIR:-$HOME/Documents/briefings}"
# Log directory and file
LOG_DIR="$DELIVERY_DIR/logs"
LOG_FILE="$LOG_DIR/scout_prewarm_$(date +%Y-%m-%d).log"
# -----------------------------------------------

mkdir -p "$LOG_DIR"

# Append everything below this line to the log file
exec >> "$LOG_FILE" 2>&1

echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Scout pre-warm starting"
echo "  REPO_ROOT=$REPO_ROOT"
echo "  PYTHON_BIN=$PYTHON_BIN"
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

export PORTFOLIO_BRIEFING_REPO="$REPO_ROOT"

# Refresh ONLY the scout cache; stdout/stderr already go to LOG_FILE.
"$PYTHON_BIN" scripts/refresh_scout.py \
    --config config/briefing.yaml
EXIT=$?

echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] Scout pre-warm exited with status $EXIT"
exit $EXIT
