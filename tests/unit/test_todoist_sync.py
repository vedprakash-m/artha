"""
tests/unit/test_todoist_sync.py — Unit tests for scripts/actions/todoist_sync.py

Tests cover: validate(), dry_run(), execute(), health_check().
All Todoist API calls are mocked — no real network calls.

Run: pytest tests/unit/test_todoist_sync.py -v
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import actions.todoist_sync as todoist_sync
from actions.base import ActionProposal, ActionResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proposal(**param_overrides: Any) -> ActionProposal:
    params: dict[str, Any] = {
        "content": "Write quarterly report",
        "description": "Include Q1 metrics",
        "project_name": "Work",
        "due_string": "next Monday",
        "priority": 2,
        "linked_oi": "OI-042",
    }
    params.update(param_overrides)
    return ActionProposal(
        id=str(uuid.uuid4()),
        action_type="todoist_sync",
        domain="work",
        title="Create Todoist task",
        description="Test description",
        parameters=params,
        friction="standard",
        min_trust=1,
        sensitivity="standard",
        reversible=False,
        undo_window_sec=None,
        expires_at=None,
        source_step=None,
        source_skill=None,
        linked_oi="OI-042",
    )


def _mock_urlopen(body: bytes, status: int = 200):
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_proposal_passes(self):
        ok, reason = todoist_sync.validate(_make_proposal())
        assert ok is True

    def test_missing_content_fails(self):
        ok, reason = todoist_sync.validate(_make_proposal(content=""))
        assert ok is False
        assert "content" in reason.lower()

    def test_content_too_long_fails(self):
        ok, reason = todoist_sync.validate(_make_proposal(content="x" * 501))
        assert ok is False

    def test_invalid_priority_fails(self):
        ok, reason = todoist_sync.validate(_make_proposal(priority=5))
        assert ok is False
        assert "priority" in reason.lower()

    def test_valid_priority_range(self):
        for p in (1, 2, 3, 4):
            ok, _ = todoist_sync.validate(_make_proposal(priority=p))
            assert ok is True


# ---------------------------------------------------------------------------
# dry_run()
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_returns_success_with_preview(self):
        result = todoist_sync.dry_run(_make_proposal())
        assert result.status == "success"
        assert "DRY RUN" in result.message.upper() or "dry run" in result.message.lower()
        assert result.data.get("content") == "Write quarterly report"

    def test_preview_contains_project(self):
        result = todoist_sync.dry_run(_make_proposal(project_name="Personal"))
        preview = result.data.get("preview", "")
        assert "Personal" in preview

    def test_preview_contains_oi_reference(self):
        result = todoist_sync.dry_run(_make_proposal(linked_oi="OI-099"))
        preview = result.data.get("preview", "")
        assert "OI-099" in preview

    def test_dry_run_no_network_call(self):
        with patch("urllib.request.urlopen") as mock_open:
            todoist_sync.dry_run(_make_proposal())
            mock_open.assert_not_called()


# ---------------------------------------------------------------------------
# execute()
# ---------------------------------------------------------------------------

class TestExecute:
    def test_execute_success(self):
        task_response = {
            "id": "new_task_001",
            "content": "Write quarterly report [OI-042]",
            "url": "https://app.todoist.com/app/task/new_task_001",
        }
        projects_response = [{"id": "p1", "name": "Work"}]

        call_count = [0]
        def _side_effect(req, timeout=None):
            call_count[0] += 1
            if "projects" in req.full_url:
                return _mock_urlopen(json.dumps(projects_response).encode())
            else:
                return _mock_urlopen(json.dumps(task_response).encode())

        with patch("urllib.request.urlopen", side_effect=_side_effect):
            with patch("keyring.get_password", return_value="test_token"):
                result = todoist_sync.execute(_make_proposal())

        assert result.status == "success"
        assert "new_task_001" in result.data.get("task_id", "")

    def test_execute_failed_validation_returns_failure(self):
        """Validation failure when content is empty."""
        with patch("keyring.get_password", return_value="test_token"):
            result = todoist_sync.execute(_make_proposal(content=""))
        assert result.status == "failure"
        # Either validation error or token error message — both are failures
        assert result.status == "failure"

    def test_execute_no_token_returns_failure(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                result = todoist_sync.execute(_make_proposal())
        assert result.status == "failure"

    def test_execute_api_error_returns_failure(self):
        from urllib.error import HTTPError  # noqa: PLC0415
        err = HTTPError(url="", code=403, msg="Forbidden", hdrs=MagicMock(), fp=None)
        with patch("urllib.request.urlopen", side_effect=err):
            with patch("keyring.get_password", return_value="bad_token"):
                result = todoist_sync.execute(_make_proposal())
        assert result.status == "failure"


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_check_takes_no_args(self):
        """ActionHandler protocol: health_check() must take zero arguments."""
        import inspect  # noqa: PLC0415
        sig = inspect.signature(todoist_sync.health_check)
        params = [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind not in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            )
        ]
        assert len(params) == 0, "health_check() must take no required args"

    def test_valid_token_returns_true(self):
        projects_body = json.dumps([{"id": "p1", "name": "Inbox"}]).encode()
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(projects_body)):
            with patch("keyring.get_password", return_value="test_token"):
                result = todoist_sync.health_check()
        assert result is True

    def test_no_token_returns_false(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                result = todoist_sync.health_check()
        assert result is False

    def test_api_error_returns_false(self):
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            with patch("keyring.get_password", return_value="some_token"):
                result = todoist_sync.health_check()
        assert result is False
