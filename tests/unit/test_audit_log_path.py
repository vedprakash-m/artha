"""Audit C2 (2026-11-05): the operational audit log is machine-local for the installed workspace."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import local_paths  # noqa: E402


def test_installed_workspace_logs_outside_the_synced_state_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(tmp_path / "local"))
    for argument in (None, local_paths._REPOSITORY, str(local_paths._REPOSITORY)):
        path = local_paths.audit_log_path(argument)
        assert path == tmp_path / "local" / "audit.md"
        assert not path.is_relative_to(local_paths._REPOSITORY)
    assert (tmp_path / "local").is_dir()


def test_other_workspaces_keep_their_own_state_audit(tmp_path):
    assert local_paths.audit_log_path(tmp_path) == tmp_path / "state" / "audit.md"


def test_foundation_default_is_machine_local():
    import foundation
    foundation._init_config()
    assert not Path(foundation._config["AUDIT_LOG"]).is_relative_to(local_paths._REPOSITORY / "state")
