"""channel/security.py — Security helpers: dedup, rate limiting, session tokens."""
from __future__ import annotations
import collections
import logging
import os
import socket
import threading
import time
from collections import deque
from pathlib import Path

from channel.audit import _audit_log

log = logging.getLogger("channel_listener")

# Security constants (pulled from channel_listener global scope)
_DEDUP_CACHE_SIZE = 1000
_RATE_LIMIT_COMMANDS = 10
_RATE_LIMIT_WINDOW_SEC = 60.0
_RATE_LIMIT_COOLDOWN_SEC = 60.0
_SESSION_TOKEN_MINUTES = 15

# Commands that require an active session token
_CRITICAL_COMMANDS = frozenset({"/domain"})



# ── _MessageDeduplicator ────────────────────────────────────────

class _MessageDeduplicator:
    """Thread-safe LRU cache for message deduplication."""

    def __init__(self, max_size: int = _DEDUP_CACHE_SIZE):
        self._seen: deque[str] = deque(maxlen=max_size)
        self._lock = threading.Lock()

    def is_duplicate(self, message_id: str) -> bool:
        with self._lock:
            if message_id in self._seen:
                return True
            self._seen.append(message_id)
            return False


# ── _RateLimiter ────────────────────────────────────────────────

class _RateLimiter:
    """Per-sender sliding-window rate limiter with cooldown."""

    def __init__(
        self,
        max_per_window: int = _RATE_LIMIT_COMMANDS,
        window_sec: float = _RATE_LIMIT_WINDOW_SEC,
        cooldown_sec: float = _RATE_LIMIT_COOLDOWN_SEC,
    ):
        self._limits: dict[str, list[float]] = collections.defaultdict(list)
        self._cooldown_until: dict[str, float] = {}
        self._max = max_per_window
        self._window = window_sec
        self._cooldown = cooldown_sec
        self._lock = threading.Lock()

    def is_rate_limited(self, sender_id: str) -> bool:
        """Return True if sender is in cooldown or has exceeded rate limit."""
        now = time.monotonic()
        with self._lock:
            # Check cooldown
            if sender_id in self._cooldown_until:
                if now < self._cooldown_until[sender_id]:
                    return True
                else:
                    del self._cooldown_until[sender_id]

            # Sliding window
            ts_list = self._limits[sender_id]
            # Remove timestamps outside the window
            cutoff = now - self._window
            while ts_list and ts_list[0] < cutoff:
                ts_list.pop(0)

            if len(ts_list) >= self._max:
                self._cooldown_until[sender_id] = now + self._cooldown
                return True

            ts_list.append(now)
            return False


# ── _SessionTokenStore ──────────────────────────────────────────

class _SessionTokenStore:
    """PIN-based session tokens with expiry (15-min default)."""

    def __init__(self, expiry_minutes: int = _SESSION_TOKEN_MINUTES):
        self._tokens: dict[str, float] = {}  # sender_id → expiry (monotonic)
        self._expiry = expiry_minutes * 60
        self._lock = threading.Lock()

    def _load_pin(self) -> str | None:
        """Load PIN from keyring (artha-channel-pin)."""
        try:
            import keyring
            return keyring.get_password("artha", "artha-channel-pin")
        except ImportError:
            pass
        return os.environ.get("ARTHA_CHANNEL_PIN", "")

    def unlock(self, sender_id: str, provided_pin: str) -> bool:
        """Verify PIN and create session token. Returns True if PIN correct."""
        stored_pin = self._load_pin()
        if not stored_pin:
            return False  # No PIN configured — unlock not available
        if str(stored_pin).strip() != str(provided_pin).strip():
            return False
        with self._lock:
            self._tokens[sender_id] = time.monotonic() + self._expiry
        return True

    def has_valid_token(self, sender_id: str) -> bool:
        """Check if sender has a valid (non-expired) session token."""
        with self._lock:
            expiry = self._tokens.get(sender_id)
            if expiry is None:
                return False
            if time.monotonic() > expiry:
                del self._tokens[sender_id]
                return False
            return True


# ── _requires_session ───────────────────────────────────────────

def _requires_session(command: str, args: list[str]) -> bool:
    """Return True if this command+args requires a session token."""
    return command in _CRITICAL_COMMANDS


# ── verify_listener_host ────────────────────────────────────────────────

def verify_listener_host(config: dict[str, Any]) -> bool:
    """Refuse to start on non-designated listener host.

    The designated host is set in channels.yaml → defaults.listener_host.
    Empty string = any host allowed (single-machine mode).

    Returns:
        True if this machine should run the listener.
        False if another machine is designated (exit gracefully).
    """
    designated = config.get("defaults", {}).get("listener_host", "").strip()
    if not designated:
        log.warning(
            "listener_host not set — assuming single-machine setup. "
            "For multi-machine safety, set defaults.listener_host in channels.yaml."
        )
        return True

    current = socket.gethostname()
    if current.lower() == designated.lower():
        log.info("Listener host check passed: %s ✓", current)
        return True

    log.info(
        "Listener host mismatch: this machine is '%s', designated listener is '%s'. "
        "Exiting cleanly (not an error).",
        current, designated,
    )
    _audit_log(
        "CHANNEL_LISTENER_SKIP",
        host=current,
        designated_host=designated,
    )
    return False
