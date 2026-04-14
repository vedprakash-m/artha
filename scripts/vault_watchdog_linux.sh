#!/usr/bin/env bash
# scripts/vault_watchdog_linux.sh
# ================================
# Linux vault watchdog for Artha (called by systemd user timer).
#
# Usage:
#   # Run once (called by systemd):
#   bash scripts/vault_watchdog_linux.sh
#
#   # Enable the systemd timer (run once during setup):
#   systemctl --user enable artha-vault-watchdog.timer
#   systemctl --user start  artha-vault-watchdog.timer
#
#   # Verify:
#   systemctl --user status artha-vault-watchdog.timer
#
# Environment variables:
#   ARTHA_WATCHDOG_INTERVAL_SECS  override stale-lock TTL for testing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARTHA_DIR="$(dirname "$SCRIPT_DIR")"
WATCHDOG_PY="$SCRIPT_DIR/vault_watchdog.py"

# Resolve Python interpreter
if [ -f "$ARTHA_DIR/.venv/bin/python" ]; then
    PYTHON="$ARTHA_DIR/.venv/bin/python"
elif command -v python3 &>/dev/null; then
    PYTHON="python3"
else
    echo "[ArthaWatchdog] ERROR: Python not found" >&2
    exit 1
fi

# Delegate to the shared cross-platform watchdog script
export PYTHONPATH="$ARTHA_DIR:$SCRIPT_DIR:${PYTHONPATH:-}"
exec "$PYTHON" "$WATCHDOG_PY"
