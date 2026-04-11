"""
tests/unit/test_channel_listener.py

Unit tests for scripts/channel_listener.py (Layer 2 interactive daemon).

Tests:
  1. verify_listener_host() returns True when listener_host is empty
  2. verify_listener_host() returns True when listener_host matches this host
  3. verify_listener_host() returns False when listener_host is a different host
  4. _MessageDeduplicator.is_duplicate() returns False first time, True second time
  5. _RateLimiter.is_rate_limited() returns False below limit
  6. _RateLimiter.is_rate_limited() returns True when limit exceeded
  7. _apply_scope_filter() blocks 'family' scope keywords
  8. _apply_scope_filter() limits 'standard' scope
  9. process_message() silently rejects unknown senders (no response, audit written)
  10. process_message() sends help response for /help command
  11. _SessionTokenStore.unlock() returns False for wrong PIN
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure repo root on path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import channel_listener as cl
from channels.base import InboundMessage


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_inbound(
    sender_id: str = "123",
    sender_name: str = "test",
    command: str = "/help",
    args: list[str] | None = None,
    message_id: str = "msg-001",
) -> InboundMessage:
    from datetime import datetime, timezone
    return InboundMessage(
        sender_id=sender_id,
        sender_name=sender_name,
        command=command,
        args=args or [],
        raw_text=command,
        timestamp=datetime.now(timezone.utc).isoformat(),
        message_id=message_id,
    )


# ── verify_listener_host() ────────────────────────────────────────────────────

class TestVerifyListenerHost:
    def test_empty_host_allows_any(self):
        """Empty listener_host allows any machine to run."""
        assert cl.verify_listener_host({"defaults": {"listener_host": ""}}) is True

    def test_matching_host_allows(self):
        current = socket.gethostname()
        assert cl.verify_listener_host({"defaults": {"listener_host": current}}) is True

    def test_different_host_rejects(self):
        assert cl.verify_listener_host({"defaults": {"listener_host": "NOT-THIS-HOST-XYZ"}}) is False

    def test_missing_defaults_allows(self):
        """Missing 'defaults' key acts like empty listener_host."""
        assert cl.verify_listener_host({}) is True


# ── _MessageDeduplicator ──────────────────────────────────────────────────────

class TestMessageDeduplicator:
    def test_first_message_not_duplicate(self):
        dedup = cl._MessageDeduplicator()
        assert dedup.is_duplicate("msg-1") is False

    def test_second_same_id_is_duplicate(self):
        dedup = cl._MessageDeduplicator()
        dedup.is_duplicate("msg-1")
        assert dedup.is_duplicate("msg-1") is True

    def test_different_ids_not_duplicate(self):
        dedup = cl._MessageDeduplicator()
        assert dedup.is_duplicate("msg-a") is False
        assert dedup.is_duplicate("msg-b") is False


# ── _RateLimiter ──────────────────────────────────────────────────────────────

class TestRateLimiter:
    def test_below_limit_not_rate_limited(self):
        limiter = cl._RateLimiter(max_per_window=5)
        for _ in range(5):
            assert limiter.is_rate_limited("user-1") is False

    def test_exceeding_limit_triggers_cooldown(self):
        limiter = cl._RateLimiter(max_per_window=3, cooldown_sec=60)
        for _ in range(3):
            limiter.is_rate_limited("user-1")
        # 4th call should trigger cooldown
        assert limiter.is_rate_limited("user-1") is True
        # Subsequent calls also blocked during cooldown
        assert limiter.is_rate_limited("user-1") is True

    def test_different_senders_independent(self):
        limiter = cl._RateLimiter(max_per_window=2)
        limiter.is_rate_limited("alice")
        limiter.is_rate_limited("alice")
        limiter.is_rate_limited("alice")  # triggers alice's cooldown
        # bob should not be affected
        assert limiter.is_rate_limited("bob") is False


# ── _apply_scope_filter() ─────────────────────────────────────────────────────

class TestApplyScopeFilter:
    def test_full_scope_no_changes(self):
        text = "immigration filing update\ncalendar event"
        assert cl._apply_scope_filter(text, "full") == text

    def test_family_removes_immigration(self):
        text = "immigration filing update\ncalendar event"
        result = cl._apply_scope_filter(text, "family")
        assert "immigration" not in result
        assert "calendar" in result

    def test_standard_keeps_calendar(self):
        text = "calendar: dentist tomorrow"
        result = cl._apply_scope_filter(text, "standard")
        assert "dentist" in result

    def test_standard_removes_unrelated(self):
        text = "home repair estimate received from contractor"
        result = cl._apply_scope_filter(text, "standard")
        assert "home repair" not in result


# ── process_message() ─────────────────────────────────────────────────────────

class TestProcessMessage:
    def _make_config(self, sender_id: str = "123", scope: str = "full") -> dict:
        return {
            "channels": {
                "telegram": {
                    "recipients": {
                        "primary": {"id": sender_id, "access_scope": scope},
                    }
                }
            }
        }

    @pytest.mark.anyio
    async def test_unknown_sender_silently_rejected(self, tmp_path, monkeypatch):
        """Unknown senders: no response sent, CHANNEL_REJECT audit written."""
        # Intercept _audit_log at the module attribute level so this test is
        # immune to sys.modules["channel.audit"] stubs installed by other test
        # files (e.g. test_bridge_m2m.py) that bind cl._audit_log to a fake.
        audit_events: list[str] = []
        monkeypatch.setattr(cl, "_audit_log", lambda event_type, **kw: audit_events.append(event_type))

        adapter = MagicMock()
        msg = _make_inbound(sender_id="UNKNOWN_999")
        config = self._make_config(sender_id="123")

        await cl.process_message(
            msg, adapter, "telegram", config,
            cl._MessageDeduplicator(), cl._RateLimiter(), cl._SessionTokenStore(),
        )
        # Adapter must not have been called (no response to unknown senders)
        adapter.send_message.assert_not_called()
        # Audit log should contain CHANNEL_REJECT
        assert "CHANNEL_REJECT" in audit_events

    @pytest.mark.anyio
    async def test_known_sender_gets_help_response(self, tmp_path, monkeypatch):
        """Known sender /help: adapter.send_message is called once."""
        monkeypatch.setattr(cl, "_AUDIT_LOG", tmp_path / "audit.md")
        # Patch pii_guard
        with patch("channel_listener.sys") as mock_sys:
            # Don't interfere with actual sys.path
            pass

        with patch("builtins.__import__", side_effect=ImportError):
            # pii_guard import failure → message passes through
            pass

        adapter = MagicMock()
        msg = _make_inbound(sender_id="123", command="/help")
        config = self._make_config(sender_id="123")

        # Patch pii_guard to return identity transform
        with patch.dict("sys.modules", {
            "pii_guard": MagicMock(filter_text=lambda t: (t, {}))
        }):
            await cl.process_message(
                msg, adapter, "telegram", config,
                cl._MessageDeduplicator(), cl._RateLimiter(), cl._SessionTokenStore(),
            )
        adapter.send_message.assert_called_once()


# ── _SessionTokenStore ────────────────────────────────────────────────────────

class TestSessionTokenStore:
    def test_unlock_fails_with_wrong_pin(self, monkeypatch):
        store = cl._SessionTokenStore()
        monkeypatch.setattr(store, "_load_pin", lambda: "correct-pin")
        assert store.unlock("user-1", "wrong-pin") is False
        assert store.has_valid_token("user-1") is False

    def test_unlock_succeeds_with_correct_pin(self, monkeypatch):
        store = cl._SessionTokenStore()
        monkeypatch.setattr(store, "_load_pin", lambda: "correct-pin")
        assert store.unlock("user-1", "correct-pin") is True
        assert store.has_valid_token("user-1") is True

    def test_no_pin_configured_returns_false(self, monkeypatch):
        store = cl._SessionTokenStore()
        monkeypatch.setattr(store, "_load_pin", lambda: None)
        assert store.unlock("user-1", "any-pin") is False

    def test_expired_token_returns_false(self, monkeypatch):
        import time
        store = cl._SessionTokenStore(expiry_minutes=0)
        monkeypatch.setattr(store, "_load_pin", lambda: "pin")
        store.unlock("user-1", "pin")
        # Manually expire the token
        store._tokens["user-1"] = time.monotonic() - 1
        assert store.has_valid_token("user-1") is False


# ── Timestamp staleness (anti-replay) ─────────────────────────────────────────

class TestTimestampStaleness:
    @pytest.mark.anyio
    async def test_stale_message_skipped(self, tmp_path, monkeypatch):
        """Messages older than 5 minutes are silently dropped (no response, no audit)."""
        from datetime import datetime, timezone, timedelta

        monkeypatch.setattr(cl, "_AUDIT_LOG", tmp_path / "audit.md")
        adapter = MagicMock()

        old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        msg = InboundMessage(
            sender_id="123",
            sender_name="test",
            command="/status",
            args=[],
            raw_text="/status",
            timestamp=old_ts,
            message_id="stale-msg-001",
        )
        config = {
            "channels": {
                "telegram": {
                    "recipients": {"primary": {"id": "123", "access_scope": "full"}}
                }
            }
        }

        await cl.process_message(
            msg, adapter, "telegram", config,
            cl._MessageDeduplicator(), cl._RateLimiter(), cl._SessionTokenStore(),
        )
        # Stale message must not trigger a response
        adapter.send_message.assert_not_called()


# ── Staleness indicator appended to every response ────────────────────────────

class TestStalenessAppended:
    @pytest.mark.anyio
    async def test_staleness_appended_to_response(self, tmp_path, monkeypatch):
        """Every listener response must end with 'Last updated: ... ago'."""
        monkeypatch.setattr(cl, "_AUDIT_LOG", tmp_path / "audit.md")
        monkeypatch.setattr(cl, "_STATE_DIR", tmp_path)
        # _READABLE_STATE_FILES is built at import time; patch it too so the
        # tmp_path file is found in CI (where state/ doesn't exist).
        monkeypatch.setattr(cl, "_READABLE_STATE_FILES", {
            **cl._READABLE_STATE_FILES,
            "health_check": tmp_path / "health-check.md",
            "open_items": tmp_path / "open_items.md",
        })

        # Create a minimal health-check.md so the /status handler can read it
        (tmp_path / "health-check.md").write_text("System OK\n", encoding="utf-8")

        adapter = MagicMock()
        msg = _make_inbound(sender_id="123", command="/status", message_id="st-001")
        config = {
            "channels": {
                "telegram": {
                    "recipients": {"primary": {"id": "123", "access_scope": "full"}}
                }
            }
        }

        with patch.dict("sys.modules", {"pii_guard": MagicMock(filter_text=lambda t: (t, {}))}):
            await cl.process_message(
                msg, adapter, "telegram", config,
                cl._MessageDeduplicator(), cl._RateLimiter(), cl._SessionTokenStore(),
            )

        assert adapter.send_message.called
        sent_text = adapter.send_message.call_args[0][0].text
        assert "Last updated:" in sent_text


# ── PII redaction called on every outbound listener response ──────────────────

class TestPiiRedactionOutbound:
    @pytest.mark.anyio
    async def test_pii_redaction_outbound(self, tmp_path, monkeypatch):
        """pii_guard.filter_text() must be invoked before every adapter.send_message()."""
        monkeypatch.setattr(cl, "_AUDIT_LOG", tmp_path / "audit.md")
        monkeypatch.setattr(cl, "_STATE_DIR", tmp_path)
        monkeypatch.setattr(cl, "_READABLE_STATE_FILES", {
            **cl._READABLE_STATE_FILES,
            "health_check": tmp_path / "health-check.md",
            "open_items": tmp_path / "open_items.md",
        })
        (tmp_path / "health-check.md").write_text("OK\n", encoding="utf-8")

        pii_calls: list[str] = []

        def _fake_filter(text):
            pii_calls.append(text)
            return text, {}

        adapter = MagicMock()
        msg = _make_inbound(sender_id="123", command="/status", message_id="pii-001")
        config = {
            "channels": {
                "telegram": {
                    "recipients": {"primary": {"id": "123", "access_scope": "full"}}
                }
            }
        }

        with patch.dict("sys.modules", {"pii_guard": MagicMock(filter_text=_fake_filter)}):
            await cl.process_message(
                msg, adapter, "telegram", config,
                cl._MessageDeduplicator(), cl._RateLimiter(), cl._SessionTokenStore(),
            )

        assert len(pii_calls) >= 1, "pii_guard.filter_text() was never called"


# ── Unknown command → help message ────────────────────────────────────────────

class TestCommandWhitelistInvalid:
    @pytest.mark.anyio
    async def test_unknown_command_returns_help(self, tmp_path, monkeypatch):
        """Unknown commands return the help message (not an error or no response)."""
        monkeypatch.setattr(cl, "_AUDIT_LOG", tmp_path / "audit.md")

        adapter = MagicMock()
        msg = _make_inbound(sender_id="123", command="/unknown_command_xyz", message_id="cmd-001")
        config = {
            "channels": {
                "telegram": {
                    "recipients": {"primary": {"id": "123", "access_scope": "full"}}
                }
            }
        }

        with patch.dict("sys.modules", {"pii_guard": MagicMock(filter_text=lambda t: (t, {}))}):
            await cl.process_message(
                msg, adapter, "telegram", config,
                cl._MessageDeduplicator(), cl._RateLimiter(), cl._SessionTokenStore(),
            )

        assert adapter.send_message.called
        sent_text = adapter.send_message.call_args[0][0].text
        # Response should mention available commands or be the help message
        assert "/help" in sent_text.lower() or "unknown" in sent_text.lower() or "/status" in sent_text.lower()
