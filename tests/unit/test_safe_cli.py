"""Synthetic safe_cli containment tests; no external executable is started."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import safe_cli


@pytest.fixture(autouse=True)
def _synthetic_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(safe_cli, "_AUDIT_LOG", tmp_path / "audit.md")


@pytest.mark.parametrize("alias", ["copilot", "gh", "powershell", "unknown"])
def test_only_supported_typed_aliases_are_invokable(alias):
    with patch.object(safe_cli.subprocess, "run", side_effect=AssertionError("external CLI started")):
        assert safe_cli.main(["safe_cli.py", alias, "synthetic query"]) == 2


def test_extra_arguments_are_rejected_before_any_scanner_or_executable():
    with patch.object(safe_cli, "pii_scan", side_effect=AssertionError), \
         patch.object(safe_cli.subprocess, "run", side_effect=AssertionError):
        assert safe_cli.main(["safe_cli.py", "gemini", "query", "--file", "secret.txt"]) == 2


def test_gemini_uses_exact_typed_argv_minimal_environment_and_bounds():
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="synthetic result\n", stderr="")

    with patch.object(safe_cli, "pii_scan", return_value=(False, {})), \
         patch.object(safe_cli.shutil, "which", return_value=r"C:\Tools\gemini.exe"), \
         patch.dict(safe_cli.os.environ, {"PATH": "synthetic-path", "GITHUB_TOKEN": "secret"}, clear=True), \
         patch.object(safe_cli.subprocess, "run", side_effect=run):
        assert safe_cli.main(["safe_cli.py", "gemini", "bounded synthetic query"]) == 0

    assert seen["argv"] == [r"C:\Tools\gemini.exe", "-p", "bounded synthetic query"]
    assert seen["kwargs"]["cwd"] == str(safe_cli._ARTHA_DIR)
    assert seen["kwargs"]["timeout"] == safe_cli._TIMEOUT_SECONDS
    assert seen["kwargs"]["capture_output"] is True
    assert seen["kwargs"]["text"] is True
    assert seen["kwargs"]["env"]["PATH"] == "synthetic-path"
    assert "GITHUB_TOKEN" not in seen["kwargs"]["env"]


def test_pii_or_scanner_failure_blocks_before_executable_lookup():
    with patch.object(safe_cli, "pii_scan", return_value=(True, {"SSN": 1})), \
         patch.object(safe_cli.shutil, "which", side_effect=AssertionError):
        assert safe_cli.main(["safe_cli.py", "gemini", "synthetic sensitive query"]) == 1

    with patch.object(safe_cli, "pii_scan", side_effect=RuntimeError("scanner failed")), \
         patch.object(safe_cli.shutil, "which", side_effect=AssertionError):
        assert safe_cli.main(["safe_cli.py", "gemini", "synthetic query"]) == 1


def test_oversized_query_and_output_are_refused():
    with patch.object(safe_cli, "pii_scan", side_effect=AssertionError):
        assert safe_cli.main(["safe_cli.py", "gemini", "x" * (safe_cli._MAX_QUERY_CHARS + 1)]) == 1

    result = SimpleNamespace(returncode=0, stdout="x" * (safe_cli._MAX_OUTPUT_BYTES + 1), stderr="")
    with patch.object(safe_cli, "pii_scan", return_value=(False, {})), \
         patch.object(safe_cli.shutil, "which", return_value="gemini"), \
         patch.object(safe_cli.subprocess, "run", return_value=result):
        assert safe_cli.main(["safe_cli.py", "gemini", "query"]) == 1


@pytest.mark.parametrize("error,expected", [
    (safe_cli.subprocess.TimeoutExpired("gemini", 1), 124),
    (OSError("synthetic launch failure"), 2),
])
def test_process_lifetime_and_launch_failures_are_fixed_denials(error, expected):
    with patch.object(safe_cli, "pii_scan", return_value=(False, {})), \
         patch.object(safe_cli.shutil, "which", return_value="gemini"), \
         patch.object(safe_cli.subprocess, "run", side_effect=error):
        assert safe_cli.main(["safe_cli.py", "gemini", "query"]) == expected
