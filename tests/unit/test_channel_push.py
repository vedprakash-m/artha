"""
tests/unit/test_channel_push.py

Unit tests for scripts/channel_push.py (Layer 1 push hook).

Tests:
  1. run_push() returns 0 (non-blocking) even when no channels.yaml
  2. run_push() returns 0 on adapter exception (non-blocking)
  3. _apply_scope_filter() blocks family-excluded keywords for 'family' scope
  4. _apply_scope_filter() limits to standard keywords for 'standard' scope
  5. _apply_scope_filter() passes all content for 'full' scope
  6. _check_push_marker() detects recent marker as "already pushed"
  7. _check_push_marker() allows push when marker is older than window
  8. _write_push_marker() creates JSON marker file with correct structure
  9. _write_pending_push() creates a pending file in .pending_pushes/
  10. Dry-run mode: run_push(dry_run=True) does not write marker or call adapters
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root on path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import channel_push as cp


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Redirect all state/config paths to tmp_path for isolation."""
    monkeypatch.setattr(cp, "_STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(cp, "_PENDING_PUSHES_DIR", tmp_path / "state" / ".pending_pushes")
    monkeypatch.setattr(cp, "_BRIEFINGS_DIR", tmp_path / "briefings")
    monkeypatch.setattr(cp, "_AUDIT_LOG", tmp_path / "state" / "audit.md")
    # Patch CONFIG_DIR in scripts.lib.common (used by run_push health_check)
    import lib.common as common
    monkeypatch.setattr(common, "CONFIG_DIR", tmp_path / "config")
    (tmp_path / "state").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "briefings").mkdir()


# ── run_push() non-blocking ────────────────────────────────────────────────────

class TestRunPushNonBlocking:
    def test_returns_zero_when_no_channels_yaml(self):
        """Non-blocking: returns 0 even when channels.yaml is absent."""
        result = cp.run_push()
        assert result == 0

    def test_returns_zero_on_adapter_exception(self, tmp_path, monkeypatch):
        """Non-blocking: returns 0 even if an adapter raises an exception."""
        import yaml
        import lib.common as common
        cfg = {
            "defaults": {"push_enabled": True},
            "channels": {
                "telegram": {
                    "enabled": True,
                    "adapter": "scripts/channels/telegram.py",
                    "auth": {"credential_key": "test-key"},
                    "recipients": {
                        "primary": {"id": "123", "access_scope": "full"}
                    },
                }
            },
        }
        (tmp_path / "config" / "channels.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        monkeypatch.setattr(common, "CONFIG_DIR", tmp_path / "config")

        # Make adapter instantiation raise
        with patch("channels.registry.create_adapter_from_config",
                   side_effect=RuntimeError("boom")):
            result = cp.run_push()
        assert result == 0

    def test_dry_run_returns_zero_without_side_effects(self, tmp_path, monkeypatch):
        """Dry-run: returns 0 without writing marker or calling adapters."""
        marker_calls = []
        monkeypatch.setattr(cp, "_write_push_marker", lambda *a, **k: marker_calls.append(1))

        result = cp.run_push(dry_run=True)
        assert result == 0
        assert len(marker_calls) == 0


# ── _apply_scope_filter() ─────────────────────────────────────────────────────

class TestApplyScopeFilter:
    FAMILY_BLOCKED = "immigration case update due to visa status"
    STANDARD_PASS = "calendar: dentist appointment tomorrow"
    STANDARD_FAIL = "home repair estimate received"

    def test_full_scope_passes_all(self):
        text = f"{self.FAMILY_BLOCKED}\n{self.STANDARD_PASS}\n{self.STANDARD_FAIL}"
        assert cp._apply_scope_filter(text, "full") == text

    def test_family_blocks_immigration_keywords(self):
        result = cp._apply_scope_filter(self.FAMILY_BLOCKED + "\n", "family")
        assert result.strip() == ""

    def test_family_passes_non_sensitive(self):
        text = "Soccer practice at 4pm\n" + self.FAMILY_BLOCKED + "\n"
        result = cp._apply_scope_filter(text, "family")
        assert "Soccer practice" in result
        assert "immigration" not in result

    def test_standard_keeps_calendar_line(self):
        result = cp._apply_scope_filter(self.STANDARD_PASS + "\n", "standard")
        assert "dentist" in result

    def test_standard_drops_non_calendar_line(self):
        result = cp._apply_scope_filter(self.STANDARD_FAIL + "\n", "standard")
        assert "home repair" not in result


# ── _check_push_marker() ──────────────────────────────────────────────────────

class TestCheckPushMarker:
    def test_no_marker_file_allows_push(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "_STATE_DIR", tmp_path)
        already_pushed, reason, _, _ = cp._check_push_marker()
        assert already_pushed is False

    def test_fresh_marker_prevents_push(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "_STATE_DIR", tmp_path)
        now = datetime.now(timezone.utc)
        marker = {
            "pushed_at": now.isoformat(),
            "host": "test-host",
            "channels": ["telegram"],
        }
        marker_file = tmp_path / f".channel_push_marker_{now.strftime('%Y-%m-%d')}.json"
        marker_file.write_text(json.dumps(marker), encoding="utf-8")

        already_pushed, reason, marker_host, marker_time = cp._check_push_marker()
        assert already_pushed is True
        assert "already" in reason.lower() or "within" in reason.lower() or "pushed" in reason.lower()
        assert marker_host == "test-host"

    def test_old_marker_allows_push(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "_STATE_DIR", tmp_path)
        old_time = datetime.now(timezone.utc) - timedelta(hours=cp._PUSH_DEDUP_HOURS + 1)
        marker = {
            "pushed_at": old_time.isoformat(),
            "host": "test-host",
            "channels": ["telegram"],
        }
        marker_file = tmp_path / f".channel_push_marker_{old_time.strftime('%Y-%m-%d')}.json"
        marker_file.write_text(json.dumps(marker), encoding="utf-8")

        already_pushed, _, _mh, _mt = cp._check_push_marker()
        assert already_pushed is False


# ── _write_push_marker() ──────────────────────────────────────────────────────

class TestWritePushMarker:
    def test_creates_json_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cp, "_STATE_DIR", tmp_path)
        cp._write_push_marker(["telegram"])

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        marker_path = tmp_path / f".channel_push_marker_{today}.json"
        assert marker_path.exists()
        data = json.loads(marker_path.read_text())
        assert "pushed_at" in data
        assert "telegram" in data["channels"]

    def test_marker_contains_host(self, tmp_path, monkeypatch):
        import socket
        monkeypatch.setattr(cp, "_STATE_DIR", tmp_path)
        cp._write_push_marker(["telegram"])

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data = json.loads((tmp_path / f".channel_push_marker_{today}.json").read_text())
        assert data["host"] == socket.gethostname()


# ── _write_pending_push() ─────────────────────────────────────────────────────

class TestWritePendingPush:
    def test_creates_pending_file(self, tmp_path, monkeypatch):
        pending_dir = tmp_path / ".pending_pushes"
        monkeypatch.setattr(cp, "_PENDING_PUSHES_DIR", pending_dir)

        cp._write_pending_push("telegram", "123456", "test message", "full")

        files = list(pending_dir.glob("telegram_*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["channel"] == "telegram"
        assert data["recipient_id"] == "123456"
        assert data["text"] == "test message"
        assert data["scope"] == "full"


# ── push_enabled: false master kill switch ─────────────────────────────────────

class TestPushDisabledFlag:
    def test_push_disabled_master_flag(self, tmp_path, monkeypatch):
        """push_enabled: false → silent skip; adapter never called."""
        import yaml
        import lib.common as common

        cfg = {
            "defaults": {"push_enabled": False},
            "channels": {
                "telegram": {
                    "enabled": True,
                    "adapter": "scripts/channels/telegram.py",
                    "auth": {"credential_key": "test-key"},
                    "recipients": {"primary": {"id": "123", "access_scope": "full"}},
                }
            },
        }
        (tmp_path / "config" / "channels.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        monkeypatch.setattr(common, "CONFIG_DIR", tmp_path / "config")

        adapter_calls = []
        with patch("channels.registry.create_adapter_from_config",
                   side_effect=lambda *a, **k: adapter_calls.append(1) or MagicMock()):
            result = cp.run_push()

        assert result == 0
        # push_enabled: false — adapter should never be instantiated
        assert len(adapter_calls) == 0


# ── PII redaction called on every outbound push ────────────────────────────

class TestPiiRedactionCalled:
    def test_pii_redaction_called(self, tmp_path, monkeypatch):
        """pii_guard.filter_text() must be invoked for every recipient send."""
        import yaml
        import lib.common as common
        from unittest.mock import MagicMock, patch

        cfg = {
            "defaults": {"push_enabled": True, "max_push_length": 500},
            "channels": {
                "telegram": {
                    "enabled": True,
                    "adapter": "scripts/channels/telegram.py",
                    "auth": {"credential_key": "test-key"},
                    "recipients": {
                        "primary": {"id": "123", "access_scope": "full", "push": True},
                    },
                    "features": {"push": True, "buttons": False},
                }
            },
        }
        (tmp_path / "config" / "channels.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        monkeypatch.setattr(common, "CONFIG_DIR", tmp_path / "config")

        pii_calls: list[str] = []

        def _fake_filter(text):
            pii_calls.append(text)
            return text, {}

        mock_adapter = MagicMock()
        mock_adapter.send_message.return_value = True

        with patch("channels.registry.create_adapter_from_config",
                   return_value=mock_adapter):
            with patch.dict("sys.modules", {"pii_guard": MagicMock(filter_text=_fake_filter)}):
                cp.run_push()

        # PII guard must have been called at least once per recipient
        assert len(pii_calls) >= 1


# ── push_pending_actions() ──────────────────────────────────────────────────

class TestPushPendingActions:
    """Tests for Step 14.5 — push pending actions to Telegram with inline buttons."""

    def _make_proposal(self, action_type="email_send", domain="finance",
                       title="Pay electric bill", friction="standard"):
        from actions.base import ActionProposal
        import uuid
        return ActionProposal(
            id=str(uuid.uuid4()),
            action_type=action_type,
            domain=domain,
            title=title,
            description="Test action description",
            parameters={},
            friction=friction,
            min_trust=1,
            sensitivity="standard",
            reversible=False,
            undo_window_sec=None,
            expires_at=None,
        )

    def test_returns_zero_when_no_pending(self, tmp_path, monkeypatch):
        """No pending actions → 0 messages sent, no errors."""
        mock_executor = MagicMock()
        mock_executor.pending.return_value = []
        mock_ae_mod = MagicMock()
        mock_ae_mod.ActionExecutor.return_value = mock_executor

        with patch.dict("sys.modules", {"action_executor": mock_ae_mod}):
            count = cp.push_pending_actions()
        assert count == 0

    def test_sends_to_telegram_with_inline_buttons(self, tmp_path, monkeypatch):
        """Pending actions produce Telegram messages with approve/reject/defer buttons."""
        import yaml
        import lib.common as common

        proposal = self._make_proposal()
        mock_executor = MagicMock()
        mock_executor.pending.return_value = [proposal]

        cfg = {
            "defaults": {"push_enabled": True},
            "channels": {
                "telegram": {
                    "enabled": True,
                    "adapter": "telegram",
                    "auth": {"credential_key": "test-key"},
                    "recipients": {
                        "primary": {"id": "123", "push": True, "access_scope": "full"},
                    },
                    "features": {"push": True, "buttons": True},
                }
            },
        }
        (tmp_path / "config" / "channels.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        monkeypatch.setattr(common, "CONFIG_DIR", tmp_path / "config")

        mock_adapter = MagicMock()
        mock_adapter.send_message.return_value = True

        mock_ae_mod = MagicMock()
        mock_ae_mod.ActionExecutor.return_value = mock_executor

        with patch.dict("sys.modules", {"action_executor": mock_ae_mod}), \
             patch("channels.registry.create_adapter_from_config",
                   return_value=mock_adapter):
            count = cp.push_pending_actions()

        assert count == 1
        # Verify the message structure
        call_args = mock_adapter.send_message.call_args[0][0]
        assert "ACTION PENDING" in call_args.text
        assert proposal.action_type in call_args.text
        assert len(call_args.buttons) == 3
        assert call_args.buttons[0]["command"].startswith(f"act:APPROVE:{proposal.id}")
        assert call_args.buttons[1]["command"].startswith(f"act:REJECT:{proposal.id}")
        assert call_args.buttons[2]["command"].startswith(f"act:DEFER:{proposal.id}")

    def test_dry_run_does_not_send(self, tmp_path, monkeypatch):
        """Dry run counts messages but doesn't call adapter."""
        import yaml
        import lib.common as common

        proposal = self._make_proposal()
        mock_executor = MagicMock()
        mock_executor.pending.return_value = [proposal]

        cfg = {
            "defaults": {"push_enabled": True},
            "channels": {
                "telegram": {
                    "enabled": True,
                    "adapter": "telegram",
                    "auth": {"credential_key": "test-key"},
                    "recipients": {
                        "primary": {"id": "123", "push": True, "access_scope": "full"},
                    },
                    "features": {"push": True},
                }
            },
        }
        (tmp_path / "config" / "channels.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        monkeypatch.setattr(common, "CONFIG_DIR", tmp_path / "config")

        mock_adapter = MagicMock()

        mock_ae_mod = MagicMock()
        mock_ae_mod.ActionExecutor.return_value = mock_executor

        with patch.dict("sys.modules", {"action_executor": mock_ae_mod}), \
             patch("channels.registry.create_adapter_from_config",
                   return_value=mock_adapter):
            count = cp.push_pending_actions(dry_run=True)

        assert count == 1
        mock_adapter.send_message.assert_not_called()

    def test_friction_badge_in_message(self, tmp_path, monkeypatch):
        """High-friction proposals display 🔴 badge."""
        import yaml
        import lib.common as common

        proposal = self._make_proposal(friction="high")
        mock_executor = MagicMock()
        mock_executor.pending.return_value = [proposal]

        cfg = {
            "defaults": {"push_enabled": True},
            "channels": {
                "telegram": {
                    "enabled": True,
                    "adapter": "telegram",
                    "auth": {"credential_key": "test-key"},
                    "recipients": {
                        "primary": {"id": "123", "push": True, "access_scope": "full"},
                    },
                    "features": {"push": True},
                }
            },
        }
        (tmp_path / "config" / "channels.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
        monkeypatch.setattr(common, "CONFIG_DIR", tmp_path / "config")

        mock_adapter = MagicMock()
        mock_adapter.send_message.return_value = True

        mock_ae_mod = MagicMock()
        mock_ae_mod.ActionExecutor.return_value = mock_executor

        with patch.dict("sys.modules", {"action_executor": mock_ae_mod}), \
             patch("channels.registry.create_adapter_from_config",
                   return_value=mock_adapter):
            cp.push_pending_actions()

        msg = mock_adapter.send_message.call_args[0][0]
        assert "🔴" in msg.text
        assert "high" in msg.text
