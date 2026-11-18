"""Foreground gate child bound to its parent's pipe, deadline and process containment.

This is lifetime control for cooperating tests, not hostile-code/credential isolation.
Windows uses KILL_ON_JOB_CLOSE; POSIX uses an exclusive process group. No watcher
exists outside this session child. Gate output and native credentials are never relayed.
"""
from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

# The directly executed worker imports only the preserved scripts beside itself.
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catchup_core.renewal_store import RenewalError

_PLUMBING = frozenset({'PATH', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA',
                      'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE', 'TMP', 'TEMP', 'TMPDIR',
                      'LANG', 'LC_ALL', 'TZ', 'DEVELOPER_DIR', 'SDKROOT', 'VIRTUAL_ENV', 'PYTHONHOME',
                      'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH',
                      'GIT_CONFIG_GLOBAL', 'GIT_CONFIG_NOSYSTEM'})


class _Basic(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64), ('flags', ctypes.c_uint32),
                ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t), ('active', ctypes.c_uint32),
                ('affinity', ctypes.c_size_t), ('priority', ctypes.c_uint32), ('scheduling', ctypes.c_uint32)]


class _Counters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read', 'write', 'other')]


class _Limits(ctypes.Structure):
    _fields_ = [('basic', _Basic), ('io', _Counters), ('process', ctypes.c_size_t), ('job', ctypes.c_size_t),
                ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]


def _windows_job(api: Any = None) -> Callable[[], None]:
    # https://learn.microsoft.com/windows/win32/procthread/job-objects
    if api is None:
        loader = getattr(ctypes, 'WinDLL', None)
        if loader is None:
            raise RenewalError('session process containment unavailable')
        api = loader('kernel32', use_last_error=True)
    for name, args, result in (
        ('CreateJobObjectW', [ctypes.c_void_p, ctypes.c_wchar_p], ctypes.c_void_p),
        ('SetInformationJobObject', [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
        ('AssignProcessToJobObject', [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
        ('GetCurrentProcess', [], ctypes.c_void_p), ('CloseHandle', [ctypes.c_void_p], ctypes.c_int),
    ):
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    handle = api.CreateJobObjectW(None, None)
    if not handle:
        raise RenewalError('session process containment unavailable')
    limits = _Limits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway flags.
    if (not api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not api.AssignProcessToJobObject(handle, api.GetCurrentProcess())):
        api.CloseHandle(handle)
        raise RenewalError('session process containment unavailable')
    def close() -> None:
        if not api.CloseHandle(handle):
            raise RenewalError('session process cleanup unavailable')
    return close


def start(command: list[str], root: Path, *, timeout: int = 1800,
          launcher: Callable[..., subprocess.Popen[bytes]] | None = None) -> subprocess.Popen[bytes]:
    if (type(timeout) is not int or not 1 <= timeout <= 1800 or not isinstance(command, list) or not command
            or any(not isinstance(arg, str) or '\0' in arg for arg in command)):
        raise RenewalError('session gate input is invalid')
    payload = json.dumps(command)
    if len(payload) > 16384:
        raise RenewalError('session gate input exceeds its bound')
    launcher = launcher or subprocess.Popen
    try:
        return launcher([sys.executable, '-I', str(Path(__file__).resolve()), payload, str(root), str(timeout),
                         str(os.getpid())], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, start_new_session=sys.platform != 'win32',
                        env={**{key: value for key, value in os.environ.items() if key.upper() in _PLUMBING},
                             'ARTHA_NO_REEXEC': '1'})
    except OSError:
        raise RenewalError('session gate process unavailable') from None


def run(command: list[str], root: Path, *, timeout: int = 1800) -> None:
    child = start(command, root, timeout=timeout)
    try:
        child.wait(timeout=timeout + 5)
        if child.stdout is None or child.stdout.read(16) != b'PASS\n':
            raise RenewalError('local delivery gates held')
    except (OSError, subprocess.TimeoutExpired):
        raise RenewalError('local delivery gates unavailable or timed out') from None
    finally:
        if child.stdin is not None:
            child.stdin.close()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            if sys.platform == 'win32':
                child.kill()  # Closing the worker's last job handle terminates descendants too.
            else:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=3)
        if child.stdout is not None:
            child.stdout.close()


class _ProcessEntry(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint32), ('usage', ctypes.c_uint32), ('pid', ctypes.c_uint32),
                ('heap', ctypes.c_size_t), ('module', ctypes.c_uint32), ('threads', ctypes.c_uint32),
                ('parent', ctypes.c_uint32), ('priority', ctypes.c_long), ('flags', ctypes.c_uint32),
                ('exe', ctypes.c_wchar * 260)]


def _windows_parent_of(pid: int, api: Any = None) -> int | None:
    # https://learn.microsoft.com/windows/win32/api/tlhelp32/nf-tlhelp32-createtoolhelp32snapshot
    if api is None:
        loader = getattr(ctypes, 'WinDLL', None)
        if loader is None:
            return None
        api = loader('kernel32', use_last_error=True)
    for name, args, result in (
        ('CreateToolhelp32Snapshot', [ctypes.c_uint32, ctypes.c_uint32], ctypes.c_void_p),
        ('Process32FirstW', [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
        ('Process32NextW', [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
        ('CloseHandle', [ctypes.c_void_p], ctypes.c_int),
    ):
        function = getattr(api, name)
        function.argtypes, function.restype = args, result
    snapshot = api.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        return None
    try:
        entry = _ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        found = api.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.pid == pid:
                return int(entry.parent)
            found = api.Process32NextW(snapshot, ctypes.byref(entry))
        return None
    finally:
        api.CloseHandle(snapshot)


def _launched_by(parent: int) -> bool:
    direct = os.getppid()
    if direct == parent:
        return True
    # A Windows venv python.exe is a redirector that starts the base interpreter as its child.
    return os.name == 'nt' and _windows_parent_of(direct) == parent


def _worker(command: list[str], root: Path, timeout: int, parent: int) -> None:
    if not _launched_by(parent):
        raise RenewalError('initiating session is no longer active')
    cleanup = _containment()
    def parent_closed() -> None:
        sys.stdin.buffer.read(1)
        cleanup()
    threading.Thread(target=parent_closed, daemon=True).start()
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run
    try:
        outcome = runner(command, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        print('PASS' if outcome.returncode == 0 else 'FAIL', flush=True)
    except (OSError, subprocess.TimeoutExpired):
        print('FAIL', flush=True)
    finally:
        cleanup()


def _containment() -> Callable[[], None]:
    if sys.platform == 'win32':
        return _windows_job()
    posix_os = cast(Any, os)
    posix_signal = cast(Any, signal)
    killpg = posix_os.killpg
    getpgrp = posix_os.getpgrp
    sigkill = posix_signal.SIGKILL
    return lambda: killpg(getpgrp(), sigkill)


if __name__ == '__main__':
    # The parent requires exactly b'PASS\n'; Windows text mode would otherwise send PASS\r\n.
    sys.stdout.reconfigure(newline='\n')  # type: ignore[union-attr]
    try:
        _worker(json.loads(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
    except (RenewalError, OSError, ValueError, IndexError):
        print('FAIL', flush=True)
        raise SystemExit(2) from None
