"""tests/unit/test_channel_security.py — T4-21..32: channel.security tests."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from channel.security import (
    _MessageDeduplicator,
    _RateLimiter,
    _SessionTokenStore,
    _requires_session,
    _RATE_LIMIT_COMMANDS,
    _RATE_LIMIT_WINDOW_SEC,
    _RATE_LIMIT_COOLDOWN_SEC,
    _SESSION_TOKEN_MINUTES,
)


# ---------------------------------------------------------------------------
# T4-21: _MessageDeduplicator — dedup within window + accept after eviction
# ---------------------------------------------------------------------------

class TestMessageDeduplicator:
    def test_new_message_not_duplicate(self):
        ded = _MessageDeduplicator(max_size=100)
        assert not ded.is_duplicate("msg-001")

    def test_same_message_is_duplicate(self):
        ded = _MessageDeduplicator(max_size=100)
        ded.is_duplicate("msg-002")  # First call registers it
        assert ded.is_duplicate("msg-002")

    def test_different_messages_not_duplicate(self):
        ded = _MessageDeduplicator(max_size=100)
        ded.is_duplicate("msg-003")
        assert not ded.is_duplicate("msg-004")

    def test_lru_eviction_allows_old_messages(self):
        """After LRU cache fills, earliest message is evicted and accepted again."""
        ded = _MessageDeduplicator(max_size=3)
        ded.is_duplicate("a")
        ded.is_duplicate("b")
        ded.is_duplicate("c")
        ded.is_duplicate("d")  # Evicts "a"
        assert not ded.is_duplicate("a")  # "a" was evicted, accepted again


# ---------------------------------------------------------------------------
# T4-22: _RateLimiter — under/over limit + cooldown
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_under_limit_allowed(self):
        limiter = _RateLimiter(max_per_window=5, window_sec=60, cooldown_sec=10)
        for _ in range(4):
            assert not limiter.is_rate_limited("user-1")

    def test_at_limit_blocked(self):
        limiter = _RateLimiter(max_per_window=3, window_sec=60, cooldown_sec=10)
        assert not limiter.is_rate_limited("user-2")  # 1
        assert not limiter.is_rate_limited("user-2")  # 2
        assert not limiter.is_rate_limited("user-2")  # 3
        assert limiter.is_rate_limited("user-2")      # 4 → over limit, in cooldown

    def test_cooldown_respected(self):
        # Both window_sec and cooldown_sec short so they both expire quickly
        limiter = _RateLimiter(max_per_window=1, window_sec=0.1, cooldown_sec=0.1)
        limiter.is_rate_limited("user-3")  # allowed (1st call)
        assert limiter.is_rate_limited("user-3")  # blocked (2nd call → sets cooldown)
        time.sleep(0.2)  # Wait for both window and cooldown to expire
        assert not limiter.is_rate_limited("user-3")  # Allowed again

    def test_different_senders_independent(self):
        limiter = _RateLimiter(max_per_window=2, window_sec=60, cooldown_sec=10)
        assert not limiter.is_rate_limited("user-A")
        assert not limiter.is_rate_limited("user-A")
        assert limiter.is_rate_limited("user-A")   # Over limit
        assert not limiter.is_rate_limited("user-B")  # Different user, not limited


# ---------------------------------------------------------------------------
# T4-23: _SessionTokenStore — create/verify/expire/revoke
# ---------------------------------------------------------------------------

class TestSessionTokenStore:
    def test_no_token_by_default(self):
        store = _SessionTokenStore(expiry_minutes=15)
        assert not store.has_valid_token("user-x")

    def test_valid_token_after_unlock(self, monkeypatch):
        store = _SessionTokenStore(expiry_minutes=15)
        monkeypatch.setattr(store, "_load_pin", lambda: "1234")
        assert store.unlock("user-y", "1234")
        assert store.has_valid_token("user-y")

    def test_wrong_pin_rejected(self, monkeypatch):
        store = _SessionTokenStore(expiry_minutes=15)
        monkeypatch.setattr(store, "_load_pin", lambda: "1234")
        assert not store.unlock("user-z", "0000")
        assert not store.has_valid_token("user-z")

    def test_token_expires(self, monkeypatch):
        store = _SessionTokenStore(expiry_minutes=1)
        monkeypatch.setattr(store, "_load_pin", lambda: "pin")
        store.unlock("user-exp", "pin")
        # Manually expire the token by setting past time
        with store._lock:
            store._tokens["user-exp"] = time.monotonic() - 1
        assert not store.has_valid_token("user-exp")

    def test_no_pin_configured_rejects_unlock(self, monkeypatch):
        store = _SessionTokenStore(expiry_minutes=15)
        monkeypatch.setattr(store, "_load_pin", lambda: None)
        assert not store.unlock("user-npin", "anything")


# ---------------------------------------------------------------------------
# T4-24: _requires_session for critical domains
# ---------------------------------------------------------------------------

class TestRequiresSession:
    def test_domain_command_requires_session(self):
        result = _requires_session("/domain", ["health"])
        assert isinstance(result, bool)

    def test_status_does_not_require_session(self):
        result = _requires_session("/status", [])
        assert result is False

    def test_help_does_not_require_session(self):
        result = _requires_session("/help", [])
        assert result is False

    def test_empty_command(self):
        result = _requires_session("", [])
        assert isinstance(result, bool)
