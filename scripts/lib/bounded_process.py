"""Bounded subprocess execution for connector command adapters."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import math
from dataclasses import dataclass
from numbers import Real
from typing import BinaryIO, Mapping, Sequence

_CHUNK_SIZE = 16 * 1024


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool


def _drain(stream: BinaryIO, limit: int, state: dict[str, object]) -> None:
    data = bytearray()
    truncated = False
    try:
        while True:
            chunk = stream.read(_CHUNK_SIZE)
            if not chunk:
                break
            remaining = limit - len(data)
            if remaining > 0:
                data.extend(chunk[:remaining])
            if len(chunk) > max(0, remaining):
                truncated = True
    except (OSError, ValueError):
        pass
    finally:
        state["data"] = bytes(data)
        state["truncated"] = truncated


def _feed(stream: BinaryIO, data: bytes) -> None:
    try:
        pending = memoryview(data)
        while pending:
            written = stream.write(pending)
            if written is None:
                break
            pending = pending[written:]
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


class _ProcessTree:
    """Native process-tree handle that remains valid after the root exits."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc
        self._job: int | None = None
        self._kernel = None
        self._pgid = proc.pid
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self._kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self._kernel.CreateJobObjectW.restype = wintypes.HANDLE
            self._kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self._kernel.AssignProcessToJobObject.restype = wintypes.BOOL
            self._kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            self._kernel.TerminateJobObject.restype = wintypes.BOOL
            self._kernel.QueryInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ]
            self._kernel.QueryInformationJobObject.restype = wintypes.BOOL
            self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self._kernel.CloseHandle.restype = wintypes.BOOL
            self._job = self._kernel.CreateJobObjectW(None, None)
            if not self._job:
                raise ctypes.WinError(ctypes.get_last_error())
            if not self._kernel.AssignProcessToJobObject(self._job, int(proc._handle)):
                error = ctypes.get_last_error()
                self.close()
                proc.kill()
                raise ctypes.WinError(error)

    def active(self) -> bool:
        if self._job is not None:
            import ctypes
            from ctypes import wintypes

            class ProcessIdList(ctypes.Structure):
                _fields_ = [
                    ("assigned_processes", wintypes.DWORD),
                    ("listed_processes", wintypes.DWORD),
                    ("process_ids", ctypes.c_size_t * 1024),
                ]

            processes = ProcessIdList()
            returned = wintypes.DWORD()
            if not self._kernel.QueryInformationJobObject(
                self._job, 3, ctypes.byref(processes), ctypes.sizeof(processes),
                ctypes.byref(returned),
            ):
                raise OSError("connector process tree could not be inspected")
            if processes.assigned_processes > 1024:
                return True
            root_running = self._proc.poll() is None
            return any(
                pid != self._proc.pid or root_running
                for pid in processes.process_ids[:processes.listed_processes]
            )
        try:
            os.killpg(self._pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def terminate(self) -> None:
        if self._job is not None:
            if self._kernel.TerminateJobObject(self._job, 1):
                return
            raise OSError("connector process tree could not be terminated")
        try:
            os.killpg(self._pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def close(self) -> None:
        if self._job is not None:
            self._kernel.CloseHandle(self._job)
            self._job = None


def run_bounded(
    command: Sequence[str],
    *,
    timeout: float,
    stdout_limit: int = 1024 * 1024,
    stderr_limit: int = 128 * 1024,
    cleanup_grace: float = 1.0,
    input: bytes | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
) -> BoundedProcessResult:
    """Run a command with bounded lifetime and drained, capped pipe output."""
    if (
        not isinstance(timeout, Real)
        or not isinstance(cleanup_grace, Real)
        or isinstance(timeout, bool)
        or isinstance(cleanup_grace, bool)
        or not math.isfinite(float(timeout))
        or not math.isfinite(float(cleanup_grace))
        or not isinstance(stdout_limit, int)
        or not isinstance(stderr_limit, int)
        or timeout <= 0
        or cleanup_grace <= 0
        or isinstance(stdout_limit, bool)
        or isinstance(stderr_limit, bool)
        or stdout_limit < 0
        or stderr_limit < 0
        or (input is not None and len(input) > 2_000_000)
    ):
        raise ValueError("process bounds must be positive")
    creationflags = 0
    start_new_session = False
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        start_new_session = True
    proc = subprocess.Popen(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        env=env,
        cwd=cwd,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    tree: _ProcessTree | None = None
    try:
        tree = _ProcessTree(proc)
    except BaseException:
        try:
            proc.kill()
            proc.wait(timeout=cleanup_grace)
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
        raise
    if proc.stdout is None or proc.stderr is None or (input is not None and proc.stdin is None):
        tree.terminate()
        proc.kill()
        tree.close()
        raise RuntimeError("process pipes unavailable")
    stdout_state: dict[str, object] = {"data": b"", "truncated": False}
    stderr_state: dict[str, object] = {"data": b"", "truncated": False}
    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, stdout_limit, stdout_state), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, stderr_limit, stderr_state), daemon=True),
    ]
    for thread in threads:
        thread.start()
    writer = None
    if input is not None:
        writer = threading.Thread(target=_feed, args=(proc.stdin, input), daemon=True)
        writer.start()

    deadline = time.monotonic() + timeout
    try:
        timed_out = False
        try:
            returncode = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
        if not timed_out:
            while any(thread.is_alive() for thread in threads):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                for thread in threads:
                    thread.join(min(0.05, remaining))
            while not timed_out and tree.active():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                time.sleep(min(0.01, remaining))
        if timed_out:
            cleanup_deadline = time.monotonic() + cleanup_grace
            tree.terminate()
            try:
                proc.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
            except subprocess.TimeoutExpired as cleanup_error:
                raise RuntimeError("connector process tree could not be terminated") from cleanup_error
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            for thread in [*threads, *([writer] if writer is not None else [])]:
                thread.join(max(0.0, cleanup_deadline - time.monotonic()))
            if any(thread.is_alive() for thread in threads):
                raise RuntimeError("connector process output could not be collected")
            raise subprocess.TimeoutExpired(
                list(command),
                timeout,
                output=stdout_state["data"],
                stderr=stderr_state["data"],
            )
    finally:
        if proc.poll() is None or tree.active():
            try:
                tree.terminate()
            except OSError:
                pass
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        tree.close()
        join_deadline = time.monotonic() + cleanup_grace
        for thread in threads:
            thread.join(max(0.0, join_deadline - time.monotonic()))
        if writer is not None:
            writer.join(max(0.0, join_deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError("connector process output could not be collected")
        if writer is not None and writer.is_alive():
            raise RuntimeError("connector process input could not be closed")
    return BoundedProcessResult(
        returncode=returncode,
        stdout=bytes(stdout_state["data"]),
        stderr=bytes(stderr_state["data"]),
        stdout_truncated=bool(stdout_state["truncated"]),
        stderr_truncated=bool(stderr_state["truncated"]),
    )
