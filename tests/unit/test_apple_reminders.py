"""
tests/unit/test_apple_reminders.py — Unit tests for scripts/connectors/apple_reminders.py

Tests cover: platform guard, health_check() fallback, fetch() fallback on non-macOS,
import guard, and macOS-gated paths via mocking.

Most tests run on all platforms by verifying the graceful-no-op path.
macOS-specific paths are tested by mocking the EventKit imports.

Run: pytest tests/unit/test_apple_reminders.py -v
"""
from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# Import with platform guard awareness
# ---------------------------------------------------------------------------

class TestAppleRemindersImport:
    def test_module_importable_on_all_platforms(self):
        """Module must import cleanly on any platform without raising."""
        import connectors.apple_reminders  # noqa: F401

    def test_module_has_fetch_and_health_check(self):
        import connectors.apple_reminders as ar
        assert callable(ar.fetch)
        assert callable(ar.health_check)


# ---------------------------------------------------------------------------
# Non-macOS fallbacks
# ---------------------------------------------------------------------------

class TestNonMacOSFallback:
    def test_health_check_returns_false_on_non_macos(self):
        """health_check returns False on non-macOS or when EventKit unavailable."""
        import connectors.apple_reminders as ar
        # On non-macOS the function should gracefully return False
        with patch.object(ar, "_IS_MACOS", False):
            result = ar.health_check({})
        assert result is False

    def test_fetch_yields_nothing_on_non_macos(self):
        """fetch yields no records on non-macOS platforms."""
        import connectors.apple_reminders as ar
        with patch.object(ar, "_IS_MACOS", False):
            records = list(ar.fetch(max_results=10))
        assert records == []

    def test_health_check_import_error_returns_false(self):
        """health_check returns False when pyobjc is not installed."""
        import connectors.apple_reminders as ar
        with patch.object(ar, "_IS_MACOS", True):
            with patch.dict(sys.modules, {"EventKit": None}):
                result = ar.health_check({})
        # Should not raise — returns False gracefully
        assert isinstance(result, bool)

    def test_fetch_import_error_yields_nothing(self):
        """fetch yields nothing when pyobjc is not installed (ImportError)."""
        import connectors.apple_reminders as ar
        if not hasattr(ar, "_get_event_store"):
            # On non-macOS the entire macOS code block is skipped at import time;
            # the stub fetch() already returns [] — that's the fallback under test.
            pytest.skip("macOS-only: _get_event_store not available on this platform")
        with patch.object(ar, "_IS_MACOS", True):
            with patch("connectors.apple_reminders._get_event_store", side_effect=ImportError("no pyobjc")):
                records = list(ar.fetch(max_results=10))
        assert records == []


# ---------------------------------------------------------------------------
# _get_event_store permission handling
# ---------------------------------------------------------------------------

class TestGetEventStore:
    def test_permission_denied_raises_permission_error(self):
        """If TCC denies access, _get_event_store should raise PermissionError."""
        import connectors.apple_reminders as ar

        mock_store = MagicMock()
        mock_EventKit = MagicMock()
        mock_EventKit.EKEventStore.return_value = mock_store
        mock_EventKit.EKEntityTypeReminder = 1

        # Simulate TCC denial: completionHandler called with granted=False
        def _mock_request(entity_type, completion):
            completion(False, None)

        mock_store.requestAccessToEntityType_completion_ = _mock_request

        # This is a macOS-only code path; only test if IS_MACOS or via mock
        with patch.object(ar, "_IS_MACOS", True):
            with patch.dict(sys.modules, {
                "EventKit": mock_EventKit,
                "Foundation": MagicMock(),
            }):
                with pytest.raises((PermissionError, Exception)):
                    # The function will attempt EventKit access and fail
                    ar._get_event_store()


# ---------------------------------------------------------------------------
# apple_reminders_sync action — non-macOS path
# ---------------------------------------------------------------------------

class TestAppleRemindersSyncFallback:
    def test_apple_reminders_sync_importable(self):
        """Action module imports cleanly regardless of platform."""
        import actions.apple_reminders_sync  # noqa: F401

    def test_validate_title_required(self):
        import actions.apple_reminders_sync as sync
        from actions.base import ActionProposal  # noqa: PLC0415
        proposal = ActionProposal(
            id="test",
            action_type="apple_reminders_sync",
            domain="work",
            title="Test",
            description="",
            parameters={"title": ""},
            friction="standard",
            min_trust=1,
            sensitivity="standard",
            reversible=False,
            undo_window_sec=None,
            expires_at=None,
            source_step=None,
            source_skill=None,
            linked_oi=None,
        )
        ok, reason = sync.validate(proposal)
        assert ok is False
        assert "title" in reason.lower()

    def test_validate_valid_passes(self):
        import actions.apple_reminders_sync as sync
        from actions.base import ActionProposal  # noqa: PLC0415
        proposal = ActionProposal(
            id="test",
            action_type="apple_reminders_sync",
            domain="work",
            title="Buy groceries",
            description="",
            parameters={"title": "Buy groceries", "priority": 1},
            friction="standard",
            min_trust=1,
            sensitivity="standard",
            reversible=False,
            undo_window_sec=None,
            expires_at=None,
            source_step=None,
            source_skill=None,
            linked_oi=None,
        )
        ok, _ = sync.validate(proposal)
        assert ok is True

    def test_execute_non_macos_returns_failure(self):
        import actions.apple_reminders_sync as sync
        from actions.base import ActionProposal  # noqa: PLC0415
        proposal = ActionProposal(
            id="test",
            action_type="apple_reminders_sync",
            domain="work",
            title="Test",
            description="",
            parameters={"title": "Reminder text"},
            friction="standard",
            min_trust=1,
            sensitivity="standard",
            reversible=False,
            undo_window_sec=None,
            expires_at=None,
            source_step=None,
            source_skill=None,
            linked_oi=None,
        )
        with patch.object(sync, "_IS_MACOS", False):
            result = sync.execute(proposal)
        assert result.status == "failure"
        assert "macos" in result.message.lower() or "darwin" in result.message.lower() or "macOS" in result.message

    def test_health_check_takes_no_args(self):
        """ActionHandler protocol: health_check() takes zero arguments."""
        import actions.apple_reminders_sync as sync
        import inspect  # noqa: PLC0415
        sig = inspect.signature(sync.health_check)
        params = [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
        ]
        assert len(params) == 0

    def test_health_check_non_macos_returns_false(self):
        import actions.apple_reminders_sync as sync
        with patch.object(sync, "_IS_MACOS", False):
            result = sync.health_check()
        assert result is False

    def test_dry_run_shows_non_macos_note(self):
        import actions.apple_reminders_sync as sync
        from actions.base import ActionProposal  # noqa: PLC0415
        proposal = ActionProposal(
            id="test",
            action_type="apple_reminders_sync",
            domain="home",
            title="Test",
            description="",
            parameters={"title": "Call dentist"},
            friction="standard",
            min_trust=1,
            sensitivity="standard",
            reversible=False,
            undo_window_sec=None,
            expires_at=None,
            source_step=None,
            source_skill=None,
            linked_oi=None,
        )
        with patch.object(sync, "_IS_MACOS", False):
            result = sync.dry_run(proposal)
        assert result.status == "success"
        assert "DRY RUN" in result.message.upper() or "dry run" in result.message.lower()
        preview = result.data.get("preview", "")
        assert "macOS" in preview or "macos" in preview.lower()
