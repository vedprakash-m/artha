"""
tests/unit/test_calendar_handler.py — Tests for calendar_create and calendar_modify handlers.

Gmail Calendar API is mocked throughout — no real network calls.

Ref: specs/act.md §8.2
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

import actions.calendar_create as calendar_create_handler
import actions.calendar_modify as calendar_modify_handler
from actions.base import ActionProposal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_create_proposal(**param_overrides) -> ActionProposal:
    params = {
        "summary": "Team Meeting",
        "start": "2026-03-25T14:00:00",
        "end": "2026-03-25T15:00:00",
        "calendar_id": "primary",
    }
    params.update(param_overrides)
    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type="calendar_create",
        domain="work",
        title="Create team meeting",
        description="",
        parameters=params,
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=True,
        undo_window_sec=300,
        expires_at=None,
        source_step=None,
        source_skill=None,
        linked_oi=None,
    )


def _make_modify_proposal(**param_overrides) -> ActionProposal:
    params = {
        "event_id": "event123",
        "calendar_id": "primary",
        "updates": {"summary": "Updated Meeting"},
    }
    params.update(param_overrides)
    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type="calendar_modify",
        domain="work",
        title="Modify calendar event",
        description="",
        parameters=params,
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=True,
        undo_window_sec=300,
        expires_at=None,
        source_step=None,
        source_skill=None,
        linked_oi=None,
    )


# ---------------------------------------------------------------------------
# calendar_create tests
# ---------------------------------------------------------------------------

class TestCalendarCreateValidate:
    def test_valid_proposal_passes(self):
        p = _make_create_proposal()
        ok, reason = calendar_create_handler.validate(p)
        assert ok is True

    def test_missing_summary_fails(self):
        p = _make_create_proposal(summary="")
        ok, reason = calendar_create_handler.validate(p)
        assert ok is False
        assert "'summary'" in reason

    def test_missing_start_fails(self):
        p = _make_create_proposal(start="")
        ok, reason = calendar_create_handler.validate(p)
        assert ok is False
        assert "'start'" in reason

    def test_invalid_start_format_fails(self):
        p = _make_create_proposal(start="not-a-date")
        ok, reason = calendar_create_handler.validate(p)
        assert ok is False
        assert "format" in reason.lower() or "unrecognised" in reason.lower()

    def test_start_after_end_fails(self):
        p = _make_create_proposal(
            start="2026-03-25T15:00:00",
            end="2026-03-25T14:00:00",
        )
        ok, reason = calendar_create_handler.validate(p)
        assert ok is False
        assert "before" in reason.lower() or "start" in reason.lower()

    def test_all_day_event_valid(self):
        p = _make_create_proposal(start="2026-03-25", end="2026-03-26")
        ok, reason = calendar_create_handler.validate(p)
        assert ok is True


class TestCalendarCreateDryRun:
    def test_dry_run_returns_preview(self):
        p = _make_create_proposal()
        result = calendar_create_handler.dry_run(p)
        assert result.status == "success"
        assert result.data.get("preview_mode") is True
        assert "event" in result.data
        assert result.data["event"]["summary"] == "Team Meeting"


class TestCalendarCreateExecute:
    def test_execute_creates_event(self):
        p = _make_create_proposal()
        mock_service = MagicMock()
        mock_service.events.return_value.insert.return_value.execute.return_value = {
            "id": "event123",
            "summary": "Team Meeting",
            "htmlLink": "https://calendar.google.com/event?id=event123",
        }

        with patch("actions.calendar_create.build_service", return_value=mock_service):
            result = calendar_create_handler.execute(p)

        assert result.status == "success"
        assert result.data.get("event_id") == "event123"
        assert result.reversible is True

    def test_execute_api_error_returns_failure(self):
        p = _make_create_proposal()
        with patch("actions.calendar_create.build_service", side_effect=Exception("auth")):
            result = calendar_create_handler.execute(p)
        assert result.status == "failure"


class TestCalendarCreateReverse:
    def test_build_reverse_proposal_creates_delete_action(self):
        p = _make_create_proposal()
        result_data = {"event_id": "event123", "calendar_id": "primary"}
        reverse = calendar_create_handler.build_reverse_proposal(p, result_data)
        assert reverse.action_type == "calendar_create_undo"
        assert reverse.parameters["event_id"] == "event123"

    def test_build_reverse_missing_event_id_raises(self):
        p = _make_create_proposal()
        with pytest.raises(ValueError, match="event_id"):
            calendar_create_handler.build_reverse_proposal(p, {})


# ---------------------------------------------------------------------------
# calendar_modify tests
# ---------------------------------------------------------------------------

class TestCalendarModifyValidate:
    def test_valid_proposal_passes(self):
        p = _make_modify_proposal()
        ok, reason = calendar_modify_handler.validate(p)
        assert ok is True

    def test_missing_event_id_fails(self):
        p = _make_modify_proposal(event_id="")
        ok, reason = calendar_modify_handler.validate(p)
        assert ok is False
        assert "event_id" in reason

    def test_empty_updates_fails(self):
        p = _make_modify_proposal(updates={})
        ok, reason = calendar_modify_handler.validate(p)
        assert ok is False
        assert "empty" in reason.lower()

    def test_invalid_update_field_fails(self):
        p = _make_modify_proposal(updates={"unknown_field": "value"})
        ok, reason = calendar_modify_handler.validate(p)
        assert ok is False
        assert "unsupported" in reason.lower()

    def test_valid_datetime_update_passes(self):
        p = _make_modify_proposal(updates={
            "start": "2026-03-26T10:00:00",
            "end": "2026-03-26T11:00:00",
        })
        ok, reason = calendar_modify_handler.validate(p)
        assert ok is True


class TestCalendarModifyDryRun:
    def test_dry_run_returns_diff(self):
        p = _make_modify_proposal()
        mock_service = MagicMock()
        mock_service.events.return_value.get.return_value.execute.return_value = {
            "id": "event123",
            "summary": "Old Summary",
        }

        with patch("actions.calendar_modify.build_service", return_value=mock_service):
            result = calendar_modify_handler.dry_run(p)

        assert result.status == "success"
        assert result.data.get("diff") is not None
        assert "summary" in result.data["diff"]


class TestCalendarModifyExecute:
    def test_execute_patches_event(self):
        p = _make_modify_proposal()
        mock_service = MagicMock()
        mock_service.events.return_value.get.return_value.execute.return_value = {
            "id": "event123",
            "summary": "Old Summary",
        }
        mock_service.events.return_value.patch.return_value.execute.return_value = {
            "id": "event123",
            "summary": "Updated Meeting",
            "htmlLink": "https://calendar.google.com/event?id=event123",
        }

        with patch("actions.calendar_modify.build_service", return_value=mock_service):
            result = calendar_modify_handler.execute(p)

        assert result.status == "success"
        assert result.data.get("event_id") == "event123"
        # Original values stored for undo
        assert "_original_values" in result.data

    def test_execute_api_error_returns_failure(self):
        p = _make_modify_proposal()
        with patch("actions.calendar_modify.build_service", side_effect=Exception("not found")):
            result = calendar_modify_handler.execute(p)
        assert result.status == "failure"


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

class TestCalendarHealthChecks:
    def test_create_health_check_true_when_token_present(self):
        with patch(
            "actions.calendar_create.check_stored_credentials",
            return_value={"google_token_stored": True},
        ):
            assert calendar_create_handler.health_check() is True

    def test_modify_health_check_false_on_import_error(self):
        with patch(
            "actions.calendar_modify.check_stored_credentials",
            side_effect=ImportError,
        ):
            assert calendar_modify_handler.health_check() is False
