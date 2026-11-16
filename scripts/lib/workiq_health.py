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
    from catchup_core.session_lock import default_installation_dir, session_lock, writer_fence
    from lib import work_publication
    from lib.state_writer import write_atomic

    if path.is_symlink():
        raise ValueError('WorkIQ health receipt must not be a link')
    with writer_fence(path), session_lock(path.parent / 'cache.lock'):
        content = json.dumps({'available': bool(available), 'auth_valid': bool(auth_valid),
                              'platform': 'Windows', 'checked_at': datetime.now(timezone.utc).isoformat()})

        def legacy() -> None:
            # Enrollment holds this lock while it inventories the legacy receipt.
            with session_lock(default_installation_dir() / 'work-privacy.lock'):
                if work_publication.enrolled():
                    raise work_publication.WorkPublicationBlocked('Work enrollment changed during receipt publication')
                result = write_atomic(path, content)
                if not result.success:
                    raise OSError('WorkIQ health receipt could not be persisted')

        work_publication.publish_text(path, content, legacy=legacy)
