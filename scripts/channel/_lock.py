"""channel/_lock.py — Singleton lock helpers."""
from __future__ import annotations
import logging
import os
import socket
import threading
import time
from pathlib import Path

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_STATE_DIR = _ARTHA_DIR / "state"
_PID_FILE = _STATE_DIR / ".channel_listener.pid"
_SINGLETON_MUTEX_NAME = "Global\\ArthaChannelListener"
_singleton_mutex_handle = None
log = logging.getLogger("channel_listener")

def _acquire_singleton_lock() -> bool:
    """Acquire a Windows Named Mutex to guarantee only one listener runs.

    Uses ctypes to call CreateMutexW — kernel-level atomic operation with no
    race conditions. Returns True if this process is the singleton, False if
    another instance already holds the mutex.

    Also writes a PID file for operator convenience (kill/status scripts).
    """
    global _singleton_mutex_handle

    try:
        import ctypes
        ERROR_ALREADY_EXISTS = 183
        handle = ctypes.windll.kernel32.CreateMutexW(None, True, _SINGLETON_MUTEX_NAME)
        err = ctypes.windll.kernel32.GetLastError()
        if err == ERROR_ALREADY_EXISTS or handle == 0:
            # Mutex already exists — another instance owns it
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
            return False
        # We own the mutex — keep the handle alive
        _singleton_mutex_handle = handle
        # Write PID file for operator convenience
        try:
            _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            _PID_FILE.write_text(str(os.getpid()))
        except OSError:
            pass
        return True
    except Exception:
        pass

    # ctypes unavailable — fall back to PID file heuristic (best-effort, not atomic)
    try:
        _PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _PID_FILE.exists():
            existing_pid = int(_PID_FILE.read_text().strip())
            try:
                import psutil
                if psutil.pid_exists(existing_pid):
                    return False  # Another instance is running
            except ImportError:
                # psutil not available — trust the PID file
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
            ctypes.windll.kernel32.ReleaseMutex(_singleton_mutex_handle)
            ctypes.windll.kernel32.CloseHandle(_singleton_mutex_handle)
        except Exception:
            pass
        _singleton_mutex_handle = None
