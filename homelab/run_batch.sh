#!/usr/bin/env bash
# Batch runner for cron / systemd on the home lab.
# Sweeps every Google Voice voicemail accumulated since the last run into Twenty.
# Safe to fire on a schedule: flock prevents two runs from overlapping if a
# batch with several long recordings runs past the next trigger.
set -uo pipefail

# Project root = parent of this homelab/ dir (override with VOIP2CRM_HOME).
APP_DIR="${VOIP2CRM_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$APP_DIR"

VENV="${VOIP2CRM_VENV:-$APP_DIR/.venv}"
BATCH_LIMIT="${BATCH_LIMIT:-50}"          # max voicemails handled per run
LOCK="${VOIP2CRM_LOCK:-/tmp/voip2crm.lock}"
LOG_DIR="${VOIP2CRM_LOG_DIR:-$APP_DIR/data/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/batch-$(date +%Y%m%d).log"

# Single-instance guard.
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "$(date -Is) another run is active; skipping" >>"$LOG"
  exit 0
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

echo "$(date -Is) === batch start (limit=$BATCH_LIMIT) ===" >>"$LOG"
rc=0
python run.py --once --limit "$BATCH_LIMIT" -v >>"$LOG" 2>&1 || rc=$?
echo "$(date -Is) === batch done (exit $rc) ===" >>"$LOG"

# Keep ~30 days of logs.
find "$LOG_DIR" -name 'batch-*.log' -mtime +30 -delete 2>/dev/null || true
exit "$rc"
