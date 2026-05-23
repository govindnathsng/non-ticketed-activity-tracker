#!/usr/bin/env bash
# Run the weekly sync. Designed to be safe to invoke from cron/launchd.
#
# Adjust PROJECT_DIR and PYTHON if your venv lives elsewhere.

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/Documents/Non-activity-tracking}"
PYTHON="${PYTHON:-$PROJECT_DIR/.venv/bin/python}"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

cd "$PROJECT_DIR"

ts="$(date +%Y-%m-%d_%H-%M-%S)"
LOG="$LOG_DIR/sync_${ts}.log"

echo "[$(date -Iseconds)] Starting weekly sync → $LOG"
"$PYTHON" run.py run --config "$PROJECT_DIR/config.yaml" >>"$LOG" 2>&1
echo "[$(date -Iseconds)] Done."
