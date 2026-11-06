"""Audit H2 (2026-11-05): Work state belongs to the Windows Work host; the Mac does not warn about it."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import importlib.util  # noqa: E402

import pytest  # noqa: E402

import preflight.state_checks as checks  # noqa: E402


@pytest.fixture()
def script(monkeypatch):
    """scripts/preflight.py (the CLI) is shadowed on import by the package; load it from its file."""
    spec = importlib.util.spec_from_file_location("preflight_cli_script_work", SCRIPTS / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "preflight_cli_script_work", module)
    spec.loader.exec_module(module)
    return module


def _templates(tmp_path, monkeypatch):
    state = tmp_path / "state"
    (state / "templates").mkdir(parents=True)
    for name in ("work-notes.md", "finance.md"):
        (state / "templates" / name).write_text("---\nx: 1\n---\n")
    monkeypatch.setattr(checks, "STATE_DIR", str(state))


def test_work_templates_are_not_required_off_windows(tmp_path, monkeypatch):
    _templates(tmp_path, monkeypatch)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    result = checks.check_state_templates()
    assert "work-notes.md" not in result.message and "finance.md" in result.message


def test_work_templates_are_required_on_windows(tmp_path, monkeypatch):
    _templates(tmp_path, monkeypatch)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    assert "work-notes.md" in checks.check_state_templates().message


def _registry(tmp_path, monkeypatch, module):
    (tmp_path / "config").mkdir()
    (tmp_path / "state" / "work").mkdir(parents=True)
    old = time.time() - 30 * 86400
    for rel in ("state/work/work-people.md", "state/finance.md"):
        path = tmp_path / rel
        path.write_text("x")
        os.utime(path, (old, old))
    (tmp_path / "config" / "state_registry.yaml").write_text(
        "state_files:\n  - path: state/work/work-people.md\n    max_staleness_hours: 24\n"
        "  - path: state/finance.md\n    max_staleness_hours: 24\n")
    monkeypatch.setattr(module, "ARTHA_DIR", str(tmp_path))


def test_work_staleness_is_skipped_off_windows(tmp_path, monkeypatch, script):
    _registry(tmp_path, monkeypatch, script)
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    message = script.check_state_registry_staleness().message
    assert "work-people" not in message and "finance" in message


def test_work_staleness_is_checked_on_windows(tmp_path, monkeypatch, script):
    _registry(tmp_path, monkeypatch, script)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    assert "work-people" in script.check_state_registry_staleness().message
