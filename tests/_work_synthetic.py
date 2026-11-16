"""Synthetic Work creation for protocol tests on non-Windows hosts.

This stub models successful write-once creation, not native atomicity or custody.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def synthetic_member_creation(path: Path, raw: bytes) -> None:
    from catchup_core.work_privacy import WorkPrivacyBlocked

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as member:
            member.write(raw)
    except FileExistsError as exc:
        raise WorkPrivacyBlocked('synthetic Work destination exists; review required') from exc


def install_synthetic_work_creation(monkeypatch: Any) -> None:
    if os.name == 'nt':
        return
    from catchup_core import work_privacy

    monkeypatch.setattr(work_privacy, '_native_creation_available', lambda: True)
    monkeypatch.setattr(work_privacy, '_create_member', synthetic_member_creation)


def reconcile_reviewed(protocol: Any) -> dict[str, Any]:
    """Obtain a fresh synthetic review immediately before adopting it."""
    review = protocol.preview_reconciliation()
    return protocol.reconcile(reviewed=True, expected_digest=review['inventory_sha256'])
