"""Technical WorkIQ probe receipts. Source content and probe identities are never persisted."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def read_receipt(path: Path, *, max_age: int) -> dict | None:
    try:
        if path.is_symlink() or path.stat().st_size > 2048:
            return None
        with path.open('rb') as handle:
            raw = handle.read(2049)
        if len(raw) > 2048:
            return None
        value = json.loads(raw)
        if (not isinstance(value, dict) or set(value) != {'available', 'auth_valid', 'platform', 'checked_at'}
                or type(value['available']) is not bool or type(value['auth_valid']) is not bool
                or value['platform'] != 'Windows'):
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(value['checked_at'])).total_seconds()
        return value if 0 <= age < max_age else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def write_receipt(path: Path, *, available: bool, auth_valid: bool) -> None:
    from lib.state_writer import write_atomic
    if path.is_symlink():
        raise ValueError('WorkIQ health receipt must not be a link')
    from catchup_core.session_lock import session_lock, require_active_installation
    require_active_installation(path)
    with session_lock(path.parent / 'cache.lock'):
        result = write_atomic(path, json.dumps({'available': bool(available), 'auth_valid': bool(auth_valid),
                                      'platform': 'Windows', 'checked_at': datetime.now(timezone.utc).isoformat()}))
        if not result.success:
            raise OSError('WorkIQ health receipt could not be persisted')
