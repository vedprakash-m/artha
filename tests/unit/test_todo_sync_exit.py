"""WP-03 / §12.2 item 9: To Do sync must fail loudly, not report success on failure."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import todo_sync  # noqa: E402


@pytest.fixture
def run(monkeypatch):
    def _run(argv, push=None, pull=None):
        fake = types.ModuleType("setup_msgraph_oauth")
        fake.ensure_valid_token = lambda: {"access_token": "t"}
        monkeypatch.setitem(sys.modules, "setup_msgraph_oauth", fake)
        if push is not None:
            monkeypatch.setattr(todo_sync, "push_items", lambda token, dry_run=False: push)
        if pull is not None:
            monkeypatch.setattr(todo_sync, "pull_completions", lambda token, dry_run=False: pull)
        monkeypatch.setattr(sys, "argv", ["todo_sync.py", *argv])
        try:
            todo_sync.main()
        except SystemExit as exc:
            return exc.code
        return 0
    return _run


def test_push_with_failures_exits_nonzero_and_says_so(run, capsys):
    code = run(["--push"], push={"pushed": 0, "failed": 28, "skipped": 0})
    assert code == todo_sync.EXIT_SYNC_FAILED
    assert "TODO_SYNC_FAILED" in capsys.readouterr().err


def test_partial_push_failure_is_still_a_failure(run):
    assert run(["--push"], push={"pushed": 5, "failed": 1, "skipped": 0}) == todo_sync.EXIT_SYNC_FAILED


def test_clean_push_exits_zero(run):
    assert run(["--push"], push={"pushed": 5, "failed": 0, "skipped": 2}) == 0


def test_nothing_to_push_exits_zero(run):
    assert run(["--push"], push={"pushed": 0, "failed": 0, "skipped": 9}) == 0


def test_pull_failures_exit_nonzero(run):
    assert run(["--pull"], pull={"completed": 0, "still_open": 3, "failed": 2}) == todo_sync.EXIT_SYNC_FAILED


def test_dry_run_never_fails_the_exit_code(run):
    assert run(["--push", "--dry-run"], push={"pushed": 0, "failed": 4, "skipped": 0}) == 0
