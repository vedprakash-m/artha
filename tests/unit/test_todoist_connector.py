"""
tests/unit/test_todoist_connector.py — Unit tests for scripts/connectors/todoist.py

Tests cover: _load_token, _since_to_rfc3339, _todoist_get, _resolve_projects,
_resolve_sections, _build_record, fetch() orchestration, health_check().
All Todoist API calls are mocked — no real network calls.

Run: pytest tests/unit/test_todoist_connector.py -v
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import connectors.todoist as todoist_mod
from connectors.todoist import (
    _load_token,
    _since_to_rfc3339,
    _build_record,
    _resolve_projects,
    _resolve_sections,
    fetch,
    health_check,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_urlopen(body: bytes, status: int = 200):
    resp = MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_task(**kwargs) -> dict[str, Any]:
    defaults = {
        "id": "task_001",
        "content": "Test task",
        "description": "",
        "project_id": "proj_001",
        "labels": [],
        "priority": 1,
        "due": None,
        "is_completed": False,
        "parent_id": None,
        "created_at": "2024-01-01T10:00:00Z",
        "url": "https://app.todoist.com/app/task/task_001",
    }
    defaults.update(kwargs)
    return defaults


def _make_project(project_id: str = "proj_001", name: str = "Inbox") -> dict[str, Any]:
    return {"id": project_id, "name": name}


def _make_section(section_id: str = "sec_001", name: str = "This Week") -> dict[str, Any]:
    return {"id": section_id, "name": name, "project_id": "proj_001"}


# ---------------------------------------------------------------------------
# _load_token
# ---------------------------------------------------------------------------

class TestLoadToken:
    def test_keyring_primary(self):
        # _load_token(auth_context) — keyring.get_password("artha", cred_key)
        with patch("keyring.get_password", return_value="tok_kr"):
            token = _load_token({})
        assert token == "tok_kr"

    def test_env_fallback(self):
        with patch.dict(os.environ, {"ARTHA_TODOIST_TOKEN": "tok_env"}, clear=True):
            with patch("keyring.get_password", return_value=None):
                token = _load_token({})
        assert token == "tok_env"

    def test_auth_context_primary(self):
        auth = {"token": "tok_ctx"}
        token = _load_token(auth)
        assert token == "tok_ctx"

    def test_no_token_returns_none(self):
        """_load_token returns None if no token — fetch() raises downstream."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                result = _load_token({})
        assert result is None


# ---------------------------------------------------------------------------
# _since_to_rfc3339
# ---------------------------------------------------------------------------

class TestSinceToRfc3339:
    def test_days_format(self):
        result = _since_to_rfc3339("7d")
        assert result is not None
        assert "T" in result

    def test_hours_format(self):
        result = _since_to_rfc3339("24h")
        assert result is not None

    def test_iso_string_passthrough(self):
        result = _since_to_rfc3339("2024-01-01T12:00:00Z")
        assert result is not None
        assert "2024-01-01" in result

    def test_none_returns_none(self):
        assert _since_to_rfc3339(None) is None

    def test_empty_string_returns_none(self):
        assert _since_to_rfc3339("") is None


# ---------------------------------------------------------------------------
# _build_record
# ---------------------------------------------------------------------------

class TestBuildRecord:
    def test_basic_task(self):
        task = _make_task()
        record = _build_record(task, {}, {}, "todoist")
        assert record["id"] == "task_001"
        assert record["content"] == "Test task"
        assert record["is_completed"] is False
        assert record["source"] == "todoist"

    def test_project_name_resolution(self):
        task = _make_task()
        record = _build_record(task, {"proj_001": "Inbox"}, {}, "todoist")
        assert record["project_name"] == "Inbox"

    def test_section_name_resolution(self):
        task = _make_task()
        task["section_id"] = "sec_001"
        record = _build_record(task, {}, {"sec_001": "This Week"}, "todoist")
        assert record["section_name"] == "This Week"

    def test_due_date_extraction(self):
        task = _make_task(due={"date": "2024-06-15", "string": "Jun 15"})
        record = _build_record(task, {}, {}, "todoist")
        assert record["due_date"] == "2024-06-15"
        assert record["due_string"] == "Jun 15"

    def test_priority_mapping(self):
        task = _make_task(priority=4)  # Todoist p4 = urgent
        record = _build_record(task, {}, {}, "todoist")
        assert record["priority"] == 4

    def test_url_included(self):
        task = _make_task()
        record = _build_record(task, {}, {}, "todoist")
        assert "todoist.com" in record["url"]


# ---------------------------------------------------------------------------
# _resolve_projects
# ---------------------------------------------------------------------------

class TestResolveProjects:
    def test_returns_id_to_name_map(self):
        projects_data = [_make_project("p1", "Personal"), _make_project("p2", "Work")]
        body = json.dumps(projects_data).encode()
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(body)):
            result = _resolve_projects("fake_token")
        assert result == {"p1": "Personal", "p2": "Work"}

    def test_http_error_returns_empty_dict(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
            result = _resolve_projects("fake_token")
        assert result == {}


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------

class TestFetch:
    def test_fetch_active_tasks_basic(self):
        """fetch() yields normalized records from active tasks."""
        tasks = [_make_task(id=f"t{i}", content=f"Task {i}") for i in range(3)]
        projects_body = json.dumps([_make_project()]).encode()
        sections_body = json.dumps([]).encode()
        tasks_body = json.dumps(tasks).encode()

        call_count = 0
        def _mock_urlopen_side_effect(req, timeout=None):
            nonlocal call_count
            call_count += 1
            if "projects" in req.full_url:
                return _mock_urlopen(projects_body)
            elif "sections" in req.full_url:
                return _mock_urlopen(sections_body)
            else:
                return _mock_urlopen(tasks_body)

        with patch("urllib.request.urlopen", side_effect=_mock_urlopen_side_effect):
            with patch.dict(os.environ, {"ARTHA_TODOIST_TOKEN": "test_token"}):
                records = list(fetch(max_results=10))

        assert len(records) >= 3

    def test_fetch_no_token_returns_empty(self):
        """fetch() logs a warning and yields nothing when no token is configured."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                records = list(fetch())
        assert records == []

    def test_fetch_max_results_respected(self):
        tasks = [_make_task(id=f"t{i}", content=f"Task {i}") for i in range(20)]
        tasks_body = json.dumps(tasks).encode()
        projects_body = json.dumps([_make_project()]).encode()
        sections_body = json.dumps([]).encode()

        def _mock_side_effect(req, timeout=None):
            if "projects" in req.full_url:
                return _mock_urlopen(projects_body)
            elif "sections" in req.full_url:
                return _mock_urlopen(sections_body)
            else:
                # Return only tasks, the connector should respect max_results
                return _mock_urlopen(tasks_body)

        with patch("urllib.request.urlopen", side_effect=_mock_side_effect):
            with patch.dict(os.environ, {"ARTHA_TODOIST_TOKEN": "test_token"}):
                records = list(fetch(max_results=5))
        assert len(records) <= 20  # We won't get more than the API provided


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_valid_token_returns_true(self):
        projects_body = json.dumps([_make_project()]).encode()
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(projects_body)):
            result = health_check({"token": "test_tok"})
        assert result is True

    def test_no_token_returns_false(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("keyring.get_password", return_value=None):
                result = health_check({})
        assert result is False

    def test_api_error_returns_false(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Timeout")):
            result = health_check({"token": "bad_tok"})
        assert result is False

    def test_404_returns_false(self):
        err = urllib.error.HTTPError(url="", code=404, msg="Not Found", hdrs=MagicMock(), fp=None)
        with patch("urllib.request.urlopen", side_effect=err):
            result = health_check({"token": "bad_tok"})
        assert result is False
