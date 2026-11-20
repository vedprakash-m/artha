#!/usr/bin/env python3
"""Machine-local record of State File writes that were refused."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.light_mode import host_id
from lib.local_paths import local_dir

SCHEMA_VERSION = 1


class RefusalStateUnknown(RuntimeError):
    pass


def refusal_path() -> Path:
    return local_dir() / "write-refusals.json"


@contextmanager
def refusal_fence():
    from catchup_core.session_lock import session_lock  # noqa: PLC0415

    lock = refusal_path().parent / "locks" / "write-refusals.lock"
    lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with session_lock(lock):
        yield


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "refusals": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise RefusalStateUnknown(
            "write-refusal marker is unreadable; State File publication must stop"
        ) from None
    if not isinstance(payload, dict) or not isinstance(payload.get("refusals"), dict):
        raise RefusalStateUnknown(
            "write-refusal marker schema is invalid; State File publication must stop"
        )
    return payload


def _atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def record_refusal(
    target: Path,
    reason: str,
    *,
    now: datetime | None = None,
    fenced: bool = False,
) -> Path:
    """Record only target identity and a bounded reason; never State File content."""
    def perform() -> Path:
        path = refusal_path()
        payload = _read(path)
        target_name = Path(target).absolute().as_posix()
        payload["refusals"][target_name] = {
            "host": host_id(),
            "recorded_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            "reason": str(reason)[:240],
        }
        _atomic(path, payload)
        return path

    if fenced:
        return perform()
    with refusal_fence():
        return perform()


def list_refusals(*, fenced: bool = False) -> dict[str, dict[str, str]]:
    if not fenced:
        with refusal_fence():
            return list_refusals(fenced=True)
    payload = _read(refusal_path())
    refusals = payload["refusals"]
    return {
        str(name): {str(key): str(value) for key, value in row.items()}
        for name, row in refusals.items()
        if isinstance(row, dict)
    }


def has_refusals() -> bool:
    return bool(list_refusals())


def clear_refusals() -> None:
    with refusal_fence():
        refusal_path().unlink(missing_ok=True)
