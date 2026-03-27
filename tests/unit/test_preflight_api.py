"""tests/unit/test_preflight_api.py — T5-21..25: preflight.api_checks tests."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ARTHA_ROOT  = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _ARTHA_ROOT / "scripts"
for _p in [str(_ARTHA_ROOT), str(_SCRIPTS_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import preflight as pf


# ---------------------------------------------------------------------------
# T5-21..23: check_script_health
# ---------------------------------------------------------------------------

class TestCheckScriptHealth:
    def test_returns_check_result(self):
        result = pf.check_script_health("action_bridge.py", ["--health"])
        assert isinstance(result, pf.CheckResult)
        assert result.severity in ("P0", "P1")

    def test_subprocess_exit0_passes(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = pf.check_script_health("action_bridge.py", ["--health"])
            assert isinstance(result, pf.CheckResult)

    def test_subprocess_exit1_records_failure(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error output")
            result = pf.check_script_health("action_bridge.py", ["--health"])
            assert isinstance(result, pf.CheckResult)


# ---------------------------------------------------------------------------
# T5-24..25: check_pii_guard
# ---------------------------------------------------------------------------

class TestCheckPiiGuard:
    def test_returns_check_result(self):
        result = pf.check_pii_guard()
        assert isinstance(result, pf.CheckResult)
        assert result.severity in ("P0", "P1")

    def test_missing_module_reports_failure(self):
        import builtins
        original_import = builtins.__import__

        def _import_error(name, *args, **kwargs):
            if name == "pii_guard":
                raise ImportError("no module named pii_guard")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_import_error):
            result = pf.check_pii_guard()
            assert isinstance(result, pf.CheckResult)
