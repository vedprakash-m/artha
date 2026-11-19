"""preflight/backup_checks.py - P0 State File backup freshness and shrink check (state_guard)."""
from __future__ import annotations

import sys

from preflight._types import SCRIPTS_DIR, CheckResult


def check_state_backup(read_only: bool = False) -> CheckResult:
    """P0: fail loudly when there is no off-repo snapshot, it is stale, or live state lost files.

    A healthy but stale state is snapshotted automatically (outside read-only mode), so every
    preflight doubles as a backup trigger and no scheduler is needed.
    """
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    try:
        import state_guard
        code, msg = state_guard.check(snapshot_if_stale=not read_only)
    except Exception as exc:  # a broken guard must itself be loud
        return CheckResult("state backup", "P0", False,
                           f"🔴 state_guard failed: {type(exc).__name__}: {str(exc)[:100]}",
                           fix_hint="python3 scripts/state_guard.py check")
    if code == 0:
        return CheckResult("state backup", "P0", True, msg)
    return CheckResult(
        "state backup", "P0", False, msg,
        fix_hint=("Restore missing files from the newest snapshot under ~/.artha-local/state-backups/, "
                  "or after an intended deletion run: python3 scripts/state_guard.py snapshot --force"),
    )
