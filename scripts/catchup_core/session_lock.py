"""OS-owned, process-lifetime lock. Crashes release it without unsafe TTL takeover."""
from __future__ import annotations

import importlib
import os
import os as _real_os
import stat
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class SessionBusy(RuntimeError):
    pass


_active_lock_handles = threading.local()


@contextmanager
def _windows_lock(handle: BinaryIO, *, shared: bool) -> Iterator[None]:
    if sys.platform != "win32":
        raise SessionBusy("native Windows locking is unavailable on this platform")
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LockFileEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped),
    ]
    kernel.LockFileEx.restype = wintypes.BOOL
    kernel.UnlockFileEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped),
    ]
    kernel.UnlockFileEx.restype = wintypes.BOOL
    msvcrt = importlib.import_module("msvcrt")
    native = msvcrt.get_osfhandle(handle.fileno())
    overlapped = Overlapped()
    # FAIL_IMMEDIATELY; the absence of EXCLUSIVE_LOCK requests a genuine shared lock.
    flags = 0x1 | (0 if shared else 0x2)
    if not kernel.LockFileEx(native, flags, 0, 1, 0, ctypes.byref(overlapped)):
        raise SessionBusy("another Catch-up owns this ledger") from ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not kernel.UnlockFileEx(native, 0, 1, 0, ctypes.byref(overlapped)):
            raise SessionBusy("session lock release failed") from ctypes.WinError(ctypes.get_last_error())


def require_active_installation(*paths: Path) -> None:
    """Restoration never grants permission to run an installation or reuse its action approvals."""
    for path in paths:
        resolved = path.resolve()
        for parent in (resolved, *resolved.parents):
            try:
                (parent / '.artha-restored.json').lstat()
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError:
                raise SessionBusy(
                    'A restored installation cannot be ruled out; marker metadata is unavailable.') from None
            raise SessionBusy('This restored installation is quarantined; review recovery before activation.')


def _windows_open_lock(path: Path, *, created: bool) -> int:
    from catchup_core.quarantine_fs_windows import (  # noqa: PLC0415
        QuarantineFilesystemError,
        WindowsQuarantineDirectory,
        _native,
    )

    msvcrt = importlib.import_module('msvcrt')
    try:
        with (WindowsQuarantineDirectory.open(path.parent, owner_private=False) as parent,
              (parent.create_file(path.name) if created else
               parent.open_file(path.name, writable=True, private=False)) as opened):
            descriptor = _real_os.open(path, _real_os.O_RDWR)
            try:
                native = _native()
                handle = msvcrt.get_osfhandle(descriptor)
                native.verify_type(handle, directory=False)
                if native.identity(handle) != opened.identity:
                    raise SessionBusy('lock identity changed')
                return descriptor
            except BaseException:
                _real_os.close(descriptor)
                raise
    except QuarantineFilesystemError:
        raise SessionBusy('lock metadata is unavailable or unsafe') from None


@contextmanager
def session_lock(path: Path, *, shared: bool = False) -> Iterator[None]:  # noqa: PLR0912, PLR0915
    missing = []
    directory = path.parent
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        if sys.platform == 'win32':
            from catchup_core.quarantine_fs_windows import (  # noqa: PLC0415
                QuarantineFilesystemError,
                ensure_private_directory,
            )
            try:
                ensure_private_directory(directory)
            except QuarantineFilesystemError:
                raise SessionBusy('lock directory is unavailable or unsafe') from None
        else:
            directory.mkdir(mode=0o700, exist_ok=True)
    try:
        metadata = path.lstat() if path.exists() or path.is_symlink() else None
        created = metadata is None
        if metadata is not None and (not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1 or getattr(metadata, 'st_file_attributes', 0) & 0x400):
            raise SessionBusy('lock must be an ordinary unlinked file')
        descriptor = (_windows_open_lock(path, created=created) if sys.platform == 'win32' else
                      _real_os.open(path, _real_os.O_RDWR | _real_os.O_CREAT
                                    | getattr(_real_os, "O_NOFOLLOW", 0), 0o600))
    except OSError:
        raise SessionBusy('lock metadata is unavailable or unsafe') from None
    try:
        metadata = _real_os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise SessionBusy('lock must be an ordinary unlinked file')
        if sys.platform != 'win32':
            if metadata.st_uid != _real_os.getuid():
                raise SessionBusy('lock belongs to another owner')
            if created:
                _real_os.fchmod(descriptor, 0o600)
        handle = _real_os.fdopen(descriptor, 'a+b')
    except BaseException:
        _real_os.close(descriptor)
        raise
    with handle:
        if sys.platform == "win32":
            with _windows_lock(handle, shared=shared):
                if not shared:
                    handle.seek(0, _real_os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"\0")
                        handle.flush()
                key = path.resolve()
                handles = getattr(_active_lock_handles, "handles", None)
                if handles is None:
                    handles = {}
                    _active_lock_handles.handles = handles
                handles.setdefault(key, []).append(handle)
                try:
                    yield
                finally:
                    handles[key].pop()
                    if not handles[key]:
                        del handles[key]
        else:
            import fcntl  # noqa: PLC0415
            mode = (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB
            try:
                fcntl.flock(handle.fileno(), mode)
            except BlockingIOError as exc:
                raise SessionBusy("another Catch-up owns this ledger") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def session_lock_reader(path: Path) -> Iterator[BinaryIO]:
    """Read an active Windows lock through its owning handle, which avoids its locked byte range."""
    if sys.platform != "win32" or not path.name.endswith(".lock"):
        with path.open("rb") as stream:
            yield stream
        return
    key = path.resolve()
    handles = getattr(_active_lock_handles, "handles", {})
    held = handles.get(key)
    if not held:
        with path.open("rb") as stream:
            yield stream
        return
    handle = held[-1]
    try:
        opened = _real_os.fstat(handle.fileno())
        current = path.lstat()
    except OSError:
        raise SessionBusy("lock metadata is unavailable or unsafe") from None
    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
            or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
            or getattr(current, "st_file_attributes", 0) & 0x400
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
        raise SessionBusy("lock path changed or is unsafe")
    position = handle.tell()
    try:
        handle.seek(0)
        yield handle
    finally:
        handle.seek(position)


_owners = threading.local()


def default_installation_dir() -> Path:
    """The machine-local directory whose fence every installation writer shares with capture."""
    return Path(os.environ.get('ARTHA_LOCAL_DIR') or Path.home() / '.artha-local').expanduser()


@contextmanager
def installation_fence(directory: Path, *, exclusive: bool = False) -> Iterator[None]:
    """Compose nested writes while excluding capture; peer writers share the installation fence."""
    require_active_installation(directory)
    identity = directory.resolve()
    state: tuple[int, dict[Path, bool]] | None = getattr(_owners, 'state', None)
    if state is None or state[0] != os.getpid():
        state = (os.getpid(), {})
        _owners.state = state
    held = state[1]
    if identity in held:
        if exclusive and not held[identity]:
            raise SessionBusy('cannot upgrade an active installation writer into capture')
        yield
        return
    with session_lock(identity / 'installation.lock', shared=not exclusive):
        held[identity] = exclusive
        try:
            yield
        finally:
            del held[identity]


@contextmanager
def writer_fence(*paths: Path) -> Iterator[None]:
    """Shared fence for a legacy script that writes installation files: capture excludes it, a restored tree refuses it.

    The target paths are checked as well as the machine-local directory, because a restored tree can sit
    apart from the local directory the environment names.
    """
    local = default_installation_dir()
    require_active_installation(*paths, local)
    with installation_fence(local):
        yield
