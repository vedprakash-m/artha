"""OS-owned, process-lifetime lock. Crashes release it without unsafe TTL takeover."""
from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class SessionBusy(RuntimeError):
    pass


@contextmanager
def session_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")
            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise SessionBusy("another Catch-up owns this ledger") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # noqa: PLC0415
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SessionBusy("another Catch-up owns this ledger") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
