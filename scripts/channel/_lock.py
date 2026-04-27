"""channel/_lock.py — Singleton lock helpers."""
from __future__ import annotations
import logging
import os
from pathlib import Path

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_STATE_DIR = _ARTHA_DIR / "state"
_PID_FILE = _STATE_DIR / ".channel_listener.pid"
# Local\ namespace: per-session scope, no elevated privileges required.
# Global\ requires SeCreateGlobalPrivilege and is not needed here.
_SINGLETON_MUTEX_NAME = "Local\\ArthaChannelListener"
_singleton_mutex_handle = None
log = logging.getLogger("channel_listener")

def _acquire_singleton_lock() -> bool:
    """Acquire a Windows Named Mutex to guarantee only one listener runs per session.

    Uses ctypes to call CreateMutexW — kernel-level atomic operation with no
    race conditions. Returns True if this process is the singleton, False if
    another instance already holds the mutex.

    Error handling:
    - ERROR_ALREADY_EXISTS (183): another instance owns the mutex → return False.
    - Any other error (NULL handle, unexpected Win32 error): log and raise —
      silently proceeding would allow duplicate listeners.

    Also writes a PID file for operator convenience (kill/status scripts).
    """
    global _singleton_mutex_handle

    try:
        import ctypes
        ERROR_ALREADY_EXISTS = 183
        # use_last_error=True: ctypes captures GetLastError() atomically right
        # after the Win32 call, before any Python bookkeeping clears it.
        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _k32.CreateMutexW.restype = ctypes.c_void_p
        _k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        handle = _k32.CreateMutexW(None, True, _SINGLETON_MUTEX_NAME)
        err = ctypes.get_last_error()

        if err == ERROR_ALREADY_EXISTS:
            # Another instance owns the mutex — expected "already running" case.
            if handle:
                _k32.CloseHandle(ctypes.c_void_p(handle))
            return False

        if not handle:
            # CreateMutexW failed for an unexpected reason (e.g. access denied,
            # invalid name). Do NOT silently fall through — that would allow
            # duplicate listeners. Raise so the caller sees a real error.
            raise RuntimeError(
                f"CreateMutexW({_SINGLETON_MUTEX_NAME!r}) failed: "
                f"{ctypes.WinError(err)}"
            )

        # We own the mutex — keep the handle alive for the process lifetime.
        _singleton_mutex_handle = handle
        # Write PID file for operator convenience (kill/status scripts).
        try:
            _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PID_FILE.write_text(str(os.getpid()))
        except OSError:
            pass
        return True

    except RuntimeError:
        raise  # Re-raise our own errors
    except Exception:
        # ctypes unavailable (non-Windows or import error) — fall through to
        # PID file heuristic only on non-Windows platforms.
        import sys
        if sys.platform == "win32":
            # On Windows, ctypes should always be available. If we get here,
            # something unexpected happened. Raise rather than allow duplicates.
            raise

    # Non-Windows fallback: PID file heuristic (best-effort, not atomic).
    try:
        _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _PID_FILE.exists():
            existing_pid = int(_PID_FILE.read_text().strip())
            try:
                import psutil
                if psutil.pid_exists(existing_pid):
                    return False
            except ImportError:
                return False
        _PID_FILE.write_text(str(os.getpid()))
        return True
    except (OSError, ValueError):
        pass

    return True


def _release_singleton_lock() -> None:
    """Release the mutex and remove the PID file on clean exit."""
    global _singleton_mutex_handle
    try:
        if _PID_FILE.exists() and _PID_FILE.read_text().strip() == str(os.getpid()):
            _PID_FILE.unlink()
    except OSError:
        pass
    if _singleton_mutex_handle is not None:
        try:
            import ctypes
            _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            _k32.ReleaseMutex(ctypes.c_void_p(_singleton_mutex_handle))
            _k32.CloseHandle(ctypes.c_void_p(_singleton_mutex_handle))
        except Exception:
            pass
        _singleton_mutex_handle = None
