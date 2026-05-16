#!/usr/bin/env bash
# scheduled_brief.sh — launchd wrapper for the Artha morning digest (F-D1)
#
# Invoked by com.artha.morning-brief.plist at 07:00 Mon–Fri.
# Logs go to ~/Library/Logs/Artha/morning-brief.log (configured in plist).
# Never exits non-zero — launchd must not retry failed briefs.

set -euo pipefail

ARTHA_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOCK_FILE="$ARTHA_DIR/tmp/scheduled_brief.lock"
LOCK_MAX_AGE_SECONDS=7200  # 2 hours

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

# ── Stale-lock eviction ───────────────────────────────────────────────────────
if [[ -f "$LOCK_FILE" ]]; then
    lock_mtime=$(stat -f %m "$LOCK_FILE" 2>/dev/null || echo 0)
    now=$(date +%s)
    age=$(( now - lock_mtime ))
    if (( age > LOCK_MAX_AGE_SECONDS )); then
        log "Stale lock detected (age=${age}s). Removing."
        rm -f "$LOCK_FILE"
    else
        log "Lock held (age=${age}s < ${LOCK_MAX_AGE_SECONDS}s). Exiting."
        exit 0
    fi
fi

# ── Activate venv ─────────────────────────────────────────────────────────────
VENV="$ARTHA_DIR/.venv/bin/activate"
if [[ ! -f "$VENV" ]]; then
    log "ERROR: venv not found at $VENV"
    exit 0
fi
# shellcheck source=/dev/null
source "$VENV"

# ── Run digest pipeline ───────────────────────────────────────────────────────
log "Starting digest brief"
cd "$ARTHA_DIR"
python scripts/pipeline.py --mode=digest --skip-vault < /dev/null
log "Digest brief complete (exit $?)"

exit 0
