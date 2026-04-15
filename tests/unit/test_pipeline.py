"""Unit tests for scripts/pipeline.py — connector pipeline orchestrator."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ is importable
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_cfg():
    """Minimal connectors config dict."""
    return {
        "connectors": {
            "test_email": {
                "type": "email",
                "enabled": True,
                "fetch": {
                    "handler": "connectors.google_email",
                    "max_results": 50,
                },
                "retry": {
                    "max_attempts": 2,
                    "base_delay_seconds": 0.01,
                    "backoff_multiplier": 1,
                    "max_delay_seconds": 0.1,
                },
            },
            "disabled_cal": {
                "type": "calendar",
                "enabled": False,
                "fetch": {"handler": "connectors.google_calendar"},
            },
        }
    }


@pytest.fixture
def empty_cfg():
    return {"connectors": {}}


# ── _normalize_connectors ────────────────────────────────────────────────────

class TestNormalizeConnectors:
    def test_dict_format(self, sample_cfg):
        from pipeline import _normalize_connectors

        result = _normalize_connectors(sample_cfg)
        assert isinstance(result, list)
        assert len(result) == 2
        names = {c["name"] for c in result}
        assert names == {"test_email", "disabled_cal"}

    def test_list_format_passthrough(self):
        from pipeline import _normalize_connectors

        cfg = {"connectors": [{"name": "a"}, {"name": "b"}]}
        result = _normalize_connectors(cfg)
        assert result == [{"name": "a"}, {"name": "b"}]

    def test_empty(self, empty_cfg):
        from pipeline import _normalize_connectors

        assert _normalize_connectors(empty_cfg) == []


# ── _enabled_connectors ─────────────────────────────────────────────────────

class TestEnabledConnectors:
    def test_excludes_disabled(self, sample_cfg):
        from pipeline import _enabled_connectors

        result = _enabled_connectors(sample_cfg, None)
        names = [c["name"] for c in result]
        assert "test_email" in names
        assert "disabled_cal" not in names

    def test_source_filter(self, sample_cfg):
        from pipeline import _enabled_connectors

        result = _enabled_connectors(sample_cfg, ["disabled_cal"])
        # disabled_cal is disabled, so even filter won't include it
        assert len(result) == 0

    def test_source_filter_match(self, sample_cfg):
        from pipeline import _enabled_connectors

        result = _enabled_connectors(sample_cfg, ["test_email"])
        assert len(result) == 1
        assert result[0]["name"] == "test_email"

    def test_no_filter_returns_all_enabled(self, sample_cfg):
        from pipeline import _enabled_connectors

        result = _enabled_connectors(sample_cfg, None)
        assert len(result) == 1


# ── Platform gating (run_on) ────────────────────────────────────────────────

class TestPlatformGating:
    """Test that run_on field gates connectors by platform."""

    def _cfg(self, *connectors):
        return {"connectors": {c["name"]: c for c in connectors}}

    def _conn(self, name, run_on="all", enabled=True):
        c = {"name": name, "type": "email", "enabled": enabled,
             "fetch": {"handler": "connectors.google_email"}}
        if run_on != "all":
            c["run_on"] = run_on
        return c

    @patch("platform.system", return_value="Darwin")
    def test_darwin_only_runs_on_mac(self, _mock):
        from pipeline import _enabled_connectors
        cfg = self._cfg(self._conn("mac_only", run_on="darwin"))
        assert len(_enabled_connectors(cfg, None)) == 1

    @patch("platform.system", return_value="Windows")
    def test_darwin_only_skipped_on_windows(self, _mock):
        from pipeline import _enabled_connectors
        cfg = self._cfg(self._conn("mac_only", run_on="darwin"))
        assert len(_enabled_connectors(cfg, None)) == 0

    @patch("platform.system", return_value="Windows")
    def test_windows_only_runs_on_windows(self, _mock):
        from pipeline import _enabled_connectors
        cfg = self._cfg(self._conn("win_only", run_on="windows"))
        assert len(_enabled_connectors(cfg, None)) == 1

    @patch("platform.system", return_value="Darwin")
    def test_windows_only_skipped_on_mac(self, _mock):
        from pipeline import _enabled_connectors
        cfg = self._cfg(self._conn("win_only", run_on="windows"))
        assert len(_enabled_connectors(cfg, None)) == 0

    @patch("platform.system", return_value="Darwin")
    def test_all_runs_everywhere(self, _mock):
        from pipeline import _enabled_connectors
        cfg = self._cfg(self._conn("universal", run_on="all"))
        assert len(_enabled_connectors(cfg, None)) == 1

    @patch("platform.system", return_value="Darwin")
    def test_missing_run_on_defaults_to_all(self, _mock):
        from pipeline import _enabled_connectors
        # No run_on field at all — must still be included
        c = {"name": "legacy", "type": "email", "enabled": True,
             "fetch": {"handler": "connectors.google_email"}}
        cfg = {"connectors": {"legacy": c}}
        assert len(_enabled_connectors(cfg, None)) == 1

    @patch("platform.system", return_value="Darwin")
    def test_list_run_on_match(self, _mock):
        from pipeline import _enabled_connectors
        cfg = self._cfg(self._conn("multi", run_on=["darwin", "windows"]))
        assert len(_enabled_connectors(cfg, None)) == 1

    @patch("platform.system", return_value="Linux")
    def test_list_run_on_no_match(self, _mock):
        from pipeline import _enabled_connectors
        cfg = self._cfg(self._conn("multi", run_on=["darwin", "windows"]))
        assert len(_enabled_connectors(cfg, None)) == 0

    @patch("platform.system", return_value="Darwin")
    def test_mixed_connectors_filtered(self, _mock):
        from pipeline import _enabled_connectors
        cfg = self._cfg(
            self._conn("gmail", run_on="all"),
            self._conn("imessage", run_on="darwin"),
            self._conn("workiq", run_on="windows"),
        )
        result = _enabled_connectors(cfg, None)
        names = [c["name"] for c in result]
        assert "gmail" in names
        assert "imessage" in names
        assert "workiq" not in names


# ── _load_handler ────────────────────────────────────────────────────────────

class TestLoadHandler:
    def test_valid_dot_notation(self):
        from pipeline import _load_handler

        # google_email is in the allowlist and should be importable
        mod = _load_handler("connectors.google_email")
        assert mod is not None

    def test_filesystem_path(self):
        from pipeline import _load_handler

        mod = _load_handler("scripts/connectors/google_email.py")
        assert mod is not None

    def test_disallowed_module_raises(self):
        from pipeline import _load_handler

        with pytest.raises(ImportError, match="not in the connector allowlist"):
            _load_handler("connectors.nonexistent_42")


# ── run_pipeline ─────────────────────────────────────────────────────────────

class TestRunPipeline:
    def test_no_connectors_returns_zero(self, empty_cfg):
        from pipeline import run_pipeline

        assert run_pipeline(empty_cfg, since="2026-01-01T00:00:00Z") == 0

    @patch("pipeline._load_handler")
    @patch("pipeline.load_auth_context")
    @patch("pipeline.with_retry")
    def test_happy_path_returns_zero(self, mock_retry, mock_auth, mock_handler, sample_cfg):
        from pipeline import run_pipeline

        mock_auth.return_value = {"token": "fake"}
        mock_retry.return_value = 5

        result = run_pipeline(sample_cfg, since="2026-01-01T00:00:00Z")
        assert result == 0
        mock_retry.assert_called_once()

    @patch("pipeline._load_handler")
    def test_handler_import_error_counts_as_error(self, mock_handler, sample_cfg):
        from pipeline import run_pipeline

        mock_handler.side_effect = ImportError("no module")
        result = run_pipeline(sample_cfg, since="2026-01-01T00:00:00Z")
        assert result == 3

    @patch("pipeline._load_handler")
    @patch("pipeline.load_auth_context")
    def test_auth_error_counts_as_error(self, mock_auth, mock_handler, sample_cfg):
        from pipeline import run_pipeline

        mock_auth.side_effect = RuntimeError("auth failed")
        result = run_pipeline(sample_cfg, since="2026-01-01T00:00:00Z")
        assert result == 3

    @patch("pipeline._load_handler")
    @patch("pipeline.load_auth_context")
    def test_dry_run_skips_fetch(self, mock_auth, mock_handler, sample_cfg):
        from pipeline import run_pipeline

        mock_auth.return_value = {"token": "fake"}
        result = run_pipeline(sample_cfg, since="2026-01-01T00:00:00Z", dry_run=True)
        assert result == 0


# ── run_health_checks ────────────────────────────────────────────────────────

class TestRunHealthChecks:
    def test_no_connectors_returns_zero(self, empty_cfg):
        from pipeline import run_health_checks

        assert run_health_checks(empty_cfg) == 0

    @patch("pipeline._load_handler")
    @patch("pipeline.load_auth_context")
    def test_healthy_returns_zero(self, mock_auth, mock_handler, sample_cfg):
        from pipeline import run_health_checks

        handler_mod = MagicMock()
        handler_mod.health_check.return_value = True
        mock_handler.return_value = handler_mod
        mock_auth.return_value = {"token": "fake"}

        result = run_health_checks(sample_cfg)
        assert result == 0

    @patch("pipeline._load_handler")
    @patch("pipeline.load_auth_context")
    def test_unhealthy_returns_one(self, mock_auth, mock_handler, sample_cfg):
        from pipeline import run_health_checks

        handler_mod = MagicMock()
        handler_mod.health_check.return_value = False
        mock_handler.return_value = handler_mod
        mock_auth.return_value = {"token": "fake"}

        result = run_health_checks(sample_cfg)
        assert result == 1

    @patch("pipeline._load_handler")
    @patch("pipeline.load_auth_context")
    def test_healthy_connector_always_printed_without_verbose(self, mock_auth, mock_handler, sample_cfg, capsys):
        """A passing connector must appear in stderr even without --verbose.

        Rationale: silence is ambiguous in automated health gates (preflight).
        check_script_health() captures stderr to use as note text, so a silent
        success falls back to 'OK' with no connector name visible.
        """
        from pipeline import run_health_checks

        handler_mod = MagicMock()
        handler_mod.health_check.return_value = True
        mock_handler.return_value = handler_mod
        mock_auth.return_value = {"token": "fake"}

        run_health_checks(sample_cfg, verbose=False)
        captured = capsys.readouterr()
        assert "[health] ✓ test_email" in captured.err

    @patch("pipeline._load_handler")
    @patch("pipeline.load_auth_context")
    def test_summary_line_always_printed_on_success(self, mock_auth, mock_handler, sample_cfg, capsys):
        """The 'All N connectors healthy' summary must appear without --verbose."""
        from pipeline import run_health_checks

        handler_mod = MagicMock()
        handler_mod.health_check.return_value = True
        mock_handler.return_value = handler_mod
        mock_auth.return_value = {"token": "fake"}

        run_health_checks(sample_cfg, verbose=False)
        captured = capsys.readouterr()
        assert "connectors healthy" in captured.err

    @patch("pipeline._load_handler")
    def test_health_check_false_skips_connector(self, mock_handler, capsys):
        """health_check: false must skip the connector without attempting to load its handler.

        Regression test for: when health_check=False (bool), the code previously fell
        through to fetch.handler ('mcp'), which is not a Python module, causing a
        security allowlist error and exit 1.
        """
        from pipeline import run_health_checks

        cfg = {"connectors": {"slack": {
            "enabled": True,
            "run_on": "all",
            "fetch": {"handler": "mcp"},  # not a real Python module
            "health_check": False,
        }}}

        result = run_health_checks(cfg)
        assert result == 0
        mock_handler.assert_not_called()
        captured = capsys.readouterr()
        assert "SKIP slack" in captured.err


# ── Subprocess smoke test ────────────────────────────────────────────────────
# These tests run pipeline.py as a *subprocess* — the exact invocation path
# that preflight.py uses.  This catches import-path bugs (Bug 3) that only
# manifest when sys.path differs from an in-process import.

class TestPipelineSubprocess:
    """Verify pipeline.py is importable and runnable as a subprocess."""

    def test_pipeline_imports_cleanly_as_subprocess(self):
        """All connector handler modules must import without error."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-c", "import pipeline; print('ok')"],
            capture_output=True, text=True,
            cwd=str(_SCRIPTS),
        )
        assert result.returncode == 0, f"pipeline import failed: {result.stderr}"
        assert "ok" in result.stdout

    def test_all_connector_modules_importable(self):
        """Each connector module in the allowlist must import without error."""
        import subprocess
        from pipeline import _ALLOWED_MODULES

        for mod_name in sorted(_ALLOWED_MODULES):
            result = subprocess.run(
                [sys.executable, "-c", f"import {mod_name}; print('ok')"],
                capture_output=True, text=True,
                cwd=str(_SCRIPTS),
            )
            assert result.returncode == 0, (
                f"{mod_name} failed to import as subprocess: {result.stderr}"
            )


# ---------------------------------------------------------------------------
# DEBT-SYNC-003: _FALLBACK_HANDLER_MAP parity test
# ---------------------------------------------------------------------------

class TestFallbackHandlerMapParity:
    """Verify _FALLBACK_HANDLER_MAP and _ALLOWED_MODULES stay in sync (DEBT-SYNC-003).

    If a developer adds an entry to the fallback map but forgets to add it to the
    allowlist (or vice versa), the security boundary becomes inconsistent.
    """

    def test_fallback_map_keys_subset_of_allowed_modules(self):
        """Every _FALLBACK_HANDLER_MAP value must appear in _ALLOWED_MODULES."""
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        from pipeline import _FALLBACK_HANDLER_MAP, _ALLOWED_MODULES  # type: ignore[import]

        for connector_type, module_path in _FALLBACK_HANDLER_MAP.items():
            assert module_path in _ALLOWED_MODULES, (
                f"_FALLBACK_HANDLER_MAP entry '{connector_type}' → '{module_path}' "
                "is NOT in _ALLOWED_MODULES — security allowlist is out of sync (DEBT-SYNC-003). "
                "Add it to both or neither."
            )

    def test_allowed_modules_subset_of_fallback_map_values(self):
        """Every _ALLOWED_MODULES entry must appear in _FALLBACK_HANDLER_MAP values."""
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        from pipeline import _FALLBACK_HANDLER_MAP, _ALLOWED_MODULES  # type: ignore[import]

        fallback_values = set(_FALLBACK_HANDLER_MAP.values())
        orphan_modules = _ALLOWED_MODULES - fallback_values
        assert not orphan_modules, (
            f"_ALLOWED_MODULES contains entries absent from _FALLBACK_HANDLER_MAP.values(): "
            f"{orphan_modules} — parity broken (DEBT-SYNC-003). "
            "Remove from allowlist or add to fallback map."
        )

    def test_fallback_handler_map_no_duplicate_values(self):
        """_FALLBACK_HANDLER_MAP values must be unique (no two connector types → same module)."""
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        from pipeline import _FALLBACK_HANDLER_MAP  # type: ignore[import]

        values = list(_FALLBACK_HANDLER_MAP.values())
        seen: set[str] = set()
        dupes: list[str] = []
        for v in values:
            if v in seen:
                dupes.append(v)
            seen.add(v)
        assert not dupes, (
            f"_FALLBACK_HANDLER_MAP contains duplicate module paths: {dupes} (DEBT-SYNC-003)"
        )
