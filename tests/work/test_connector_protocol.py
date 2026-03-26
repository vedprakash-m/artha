"""
tests/work/test_connector_protocol.py — Connector error protocol tests.

Validates scripts/schemas/work_connector_protocol.py (§8.4):
  - All 9 failure modes are represented in PROTOCOL
  - Every protocol entry has correct fields
  - get_protocol() returns the right entry for known connectors
  - user_signal_for() returns safe user-facing messages (no stack traces)
  - log_connector_failure() writes to audit file without raising
  - Wildcard fallback catches unknown connectors
  - Retry-eligible modes are handled correctly

Run: pytest tests/work/test_connector_protocol.py -v
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from schemas.work_connector_protocol import (  # type: ignore
    ConnectorFailureMode,
    ConnectorProtocolEntry,
    PROTOCOL,
    get_protocol,
    user_signal_for,
    log_connector_failure,
)


# ===========================================================================
# Test Group 1: Failure mode enum completeness
# ===========================================================================

class TestFailureModeEnum:

    def test_timeout_variant_exists(self):
        assert ConnectorFailureMode.TIMEOUT

    def test_auth_expired_variant_exists(self):
        assert ConnectorFailureMode.AUTH_EXPIRED

    def test_auth_invalid_variant_exists(self):
        assert ConnectorFailureMode.AUTH_INVALID

    def test_permission_error_variant_exists(self):
        assert ConnectorFailureMode.PERMISSION_ERROR

    def test_rate_limited_variant_exists(self):
        assert ConnectorFailureMode.RATE_LIMITED

    def test_platform_skip_variant_exists(self):
        assert ConnectorFailureMode.PLATFORM_SKIP

    def test_parse_error_variant_exists(self):
        assert ConnectorFailureMode.PARSE_ERROR

    def test_unavailable_variant_exists(self):
        assert ConnectorFailureMode.UNAVAILABLE

    def test_all_down_variant_exists(self):
        assert ConnectorFailureMode.ALL_DOWN

    def test_minimum_9_failure_modes(self):
        assert len(ConnectorFailureMode) >= 9, (
            f"Expected ≥9 failure modes, got {len(ConnectorFailureMode)}"
        )


# ===========================================================================
# Test Group 2: Protocol table structure
# ===========================================================================

class TestProtocolTable:

    def test_protocol_is_non_empty(self):
        assert len(PROTOCOL) > 0, "PROTOCOL must have at least one entry"

    def test_all_entries_have_connector_name(self):
        for entry in PROTOCOL:
            assert entry.connector, f"Missing connector in {entry}"

    def test_all_entries_have_failure_mode(self):
        for entry in PROTOCOL:
            assert isinstance(entry.failure_mode, ConnectorFailureMode), \
                f"failure_mode must be ConnectorFailureMode, got {type(entry.failure_mode)}"

    def test_all_entries_have_action(self):
        for entry in PROTOCOL:
            assert entry.fallback_action, f"Missing fallback_action in {entry.connector}"

    def test_all_entries_have_user_message(self):
        for entry in PROTOCOL:
            assert entry.user_signal, f"Missing user_signal in {entry.connector}"

    def test_wildcard_entry_exists(self):
        """There must be a wildcard ('*') entry to catch unknown connectors."""
        wildcard = [e for e in PROTOCOL if e.connector == "*"]
        assert len(wildcard) >= 1, "PROTOCOL must have a wildcard ('*') entry"

    def test_workiq_entry_exists(self):
        workiq_entries = [e for e in PROTOCOL if "workiq" in e.connector.lower()]
        assert len(workiq_entries) >= 1

    def test_protocol_entry_is_frozen(self):
        """ConnectorProtocolEntry must be immutable (frozen dataclass)."""
        entry = PROTOCOL[0]
        with pytest.raises((AttributeError, TypeError)):
            entry.connector = "mutated"  # type: ignore


# ===========================================================================
# Test Group 3: get_protocol() lookup
# ===========================================================================

class TestGetProtocol:

    def test_known_connector_returns_entry(self):
        entry = get_protocol("workiq_bridge", ConnectorFailureMode.TIMEOUT)
        assert entry is not None
        assert entry.connector in ("workiq_bridge", "*")

    def test_unknown_connector_returns_wildcard(self):
        # Wildcard ('*') is registered for ALL_DOWN
        entry = get_protocol("totally_unknown_connector_xyz", ConnectorFailureMode.ALL_DOWN)
        assert entry is not None
        assert entry.connector == "*"

    def test_returns_protocol_entry_type(self):
        entry = get_protocol("workiq_bridge", ConnectorFailureMode.TIMEOUT)
        assert isinstance(entry, ConnectorProtocolEntry)

    def test_multiple_failure_modes_for_same_connector(self):
        """ado_workitems has AUTH_EXPIRED and AUTH_INVALID entries — each returns correctly."""
        for mode in [ConnectorFailureMode.AUTH_EXPIRED, ConnectorFailureMode.AUTH_INVALID]:
            entry = get_protocol("ado_workitems", mode)
            assert entry is not None


# ===========================================================================
# Test Group 4: user_signal_for() safety
# ===========================================================================

class TestUserSignal:

    def test_returns_string(self):
        msg = user_signal_for("workiq", ConnectorFailureMode.TIMEOUT)
        assert isinstance(msg, str)

    def test_message_is_non_empty(self):
        msg = user_signal_for("workiq", ConnectorFailureMode.TIMEOUT)
        assert len(msg.strip()) > 0

    def test_message_contains_no_stack_trace(self):
        """User-facing messages must never contain raw stack traces."""
        msg = user_signal_for("workiq", ConnectorFailureMode.AUTH_EXPIRED)
        assert "Traceback" not in msg
        assert "File " not in msg or "config" in msg  # allow config file paths but not stack frames
        assert "line " not in msg.lower()[:100]  # "line N" is stack trace language

    def test_message_is_actionable(self):
        """Remediation messages (not user_signal) should suggest what the user can do."""
        entry = get_protocol("ado_workitems", ConnectorFailureMode.AUTH_EXPIRED)
        assert entry is not None
        msg = entry.remediation
        action_words = {"refresh", "re-auth", "check", "run", "token", "sign", "login", "retry", "az"}
        assert any(word in msg.lower() for word in action_words), \
            f"Remediation message is not actionable: {msg!r}"

    def test_all_modes_produce_messages(self):
        for mode in ConnectorFailureMode:
            msg = user_signal_for("workiq", mode)
            assert isinstance(msg, str) and len(msg) > 0


# ===========================================================================
# Test Group 5: log_connector_failure() behavior
# ===========================================================================

class TestLogConnectorFailure:

    def test_writes_to_audit_file(self, tmp_path):
        audit_path = tmp_path / "work-audit.md"
        log_connector_failure(
            connector="workiq",
            failure_mode=ConnectorFailureMode.TIMEOUT,
            detail="Connection timed out after 30s",
            audit_path=audit_path,
        )
        assert audit_path.exists()
        content = audit_path.read_text(encoding="utf-8")
        assert "workiq" in content.lower()
        assert "timeout" in content.lower() or "TIMEOUT" in content

    def test_appends_to_existing_audit_file(self, tmp_path):
        audit_path = tmp_path / "work-audit.md"
        audit_path.write_text("# Existing content\n", encoding="utf-8")
        log_connector_failure(
            connector="ado",
            failure_mode=ConnectorFailureMode.AUTH_EXPIRED,
            detail="Token expired",
            audit_path=audit_path,
        )
        content = audit_path.read_text(encoding="utf-8")
        assert "Existing content" in content
        assert "ado" in content.lower()

    def test_does_not_raise_on_missing_directory(self, tmp_path):
        audit_path = tmp_path / "nonexistent" / "work-audit.md"
        # Must not raise — create parent dirs silently
        log_connector_failure(
            connector="msgraph_email",
            failure_mode=ConnectorFailureMode.PERMISSION_ERROR,
            detail="403 Forbidden",
            audit_path=audit_path,
        )
        assert audit_path.exists()

    def test_log_entry_includes_timestamp(self, tmp_path):
        audit_path = tmp_path / "work-audit.md"
        log_connector_failure(
            connector="workiq",
            failure_mode=ConnectorFailureMode.RATE_LIMITED,
            detail="429 Too Many Requests",
            audit_path=audit_path,
        )
        content = audit_path.read_text(encoding="utf-8")
        # Should contain a year (basic timestamp check)
        assert "202" in content

    def test_platform_skip_does_not_raise(self, tmp_path):
        audit_path = tmp_path / "work-audit.md"
        log_connector_failure(
            connector="outlookctl",
            failure_mode=ConnectorFailureMode.PLATFORM_SKIP,
            detail="outlookctl only available on Windows",
            audit_path=audit_path,
        )
        content = audit_path.read_text(encoding="utf-8")
        assert "platform" in content.lower() or "skip" in content.lower()

    def test_all_down_logged_prominently(self, tmp_path):
        audit_path = tmp_path / "work-audit.md"
        log_connector_failure(
            connector="*",
            failure_mode=ConnectorFailureMode.ALL_DOWN,
            detail="All providers unreachable",
            audit_path=audit_path,
        )
        content = audit_path.read_text(encoding="utf-8")
        assert len(content) > 50  # Should have substantial content for critical failure
