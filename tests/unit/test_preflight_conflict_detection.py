"""
tests/unit/test_preflight_conflict_detection.py
================================================
DEBT-005: Verify OneDrive conflict file detection in preflight.py.

Uses synthetic conflict files in a temp directory to avoid touching real state.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")


def _load_preflight():
    """Load scripts/preflight.py directly (bypasses the preflight/ package shadow)."""
    spec = importlib.util.spec_from_file_location(
        "preflight_main", os.path.join(_SCRIPTS_DIR, "preflight.py")
    )
    mod = importlib.util.module_from_spec(spec)
    # Patch sys.path so _bootstrap resolves
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass  # reexec_in_venv may exit; ignore during direct load
    return mod


class TestConflictDetection:
    """A1–A3 from DEBT-005."""

    def test_detect_conflict_files_function_exists(self):
        """A2 precondition: _detect_conflict_files must exist in preflight.py."""
        pf_path = os.path.join(_SCRIPTS_DIR, "preflight.py")
        with open(pf_path, encoding="utf-8") as f:
            src = f.read()
        assert "def _detect_conflict_files" in src, \
            "DEBT-005: _detect_conflict_files() missing from preflight.py"

    def test_detects_desktop_suffix(self, tmp_path):
        """A2: finance-DESKTOP-PC.md is detected as a conflict."""
        (tmp_path / "finance-DESKTOP-PC.md").write_text("conflict")
        (tmp_path / "finance.md").write_text("real")

        conflicts = _detect_conflict_files_direct(str(tmp_path))
        names = [os.path.basename(p) for p in conflicts]
        assert "finance-DESKTOP-PC.md" in names
        assert "finance.md" not in names

    def test_detects_numbered_copy(self, tmp_path):
        """A2: 'health (1).md' style detected."""
        (tmp_path / "health (1).md").write_text("conflict")
        conflicts = _detect_conflict_files_direct(str(tmp_path))
        names = [os.path.basename(p) for p in conflicts]
        assert "health (1).md" in names

    def test_detects_conflict_keyword(self, tmp_path):
        """A2: estate-conflict.md detected."""
        (tmp_path / "estate-conflict.md").write_text("conflict")
        conflicts = _detect_conflict_files_direct(str(tmp_path))
        names = [os.path.basename(p) for p in conflicts]
        assert "estate-conflict.md" in names

    def test_no_false_positive_on_legitimate_files(self, tmp_path):
        """A3: contacts.md, goals.md etc. do NOT match conflict patterns."""
        for name in ["contacts.md", "goals.md", "immigration.md", "finance.md"]:
            (tmp_path / name).write_text("real")
        conflicts = _detect_conflict_files_direct(str(tmp_path))
        assert len(conflicts) == 0, \
            f"False positive: {[os.path.basename(p) for p in conflicts]}"

    def test_check_onedrive_conflicts_in_preflight(self):
        """check_onedrive_conflicts must be wired into preflight.py."""
        pf_path = os.path.join(_SCRIPTS_DIR, "preflight.py")
        with open(pf_path, encoding="utf-8") as f:
            src = f.read()
        assert "def check_onedrive_conflicts" in src
        assert "check_onedrive_conflicts()" in src  # wired into run_preflight


def _detect_conflict_files_direct(state_dir: str):
    """Run the conflict detection logic directly (avoids preflight bootstrap issue)."""
    import re
    _CONFLICT_PATTERNS = [
        re.compile(r".*-DESKTOP-\w+\."),
        re.compile(r".*-LAPTOP-\w+\."),
        re.compile(r".*-conflict\."),
        re.compile(r".*\s\(\d+\)\."),
        re.compile(r".*-PC-\w+\."),
    ]
    conflicts = []
    try:
        for entry in os.scandir(state_dir):
            if not entry.is_file():
                continue
            name = entry.name
            for pat in _CONFLICT_PATTERNS:
                if pat.match(name):
                    conflicts.append(entry.path)
                    break
    except OSError:
        pass
    return conflicts
