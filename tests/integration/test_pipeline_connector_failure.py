"""
tests/integration/test_pipeline_connector_failure.py — Pipeline failure-mode tests.

Exercises: run_pipeline() and fetch_single() error paths to verify that:
  - fetch_single() returns an error tuple (not raises) for unknown connectors
  - fetch_single() returns an error tuple when auth fails, not raises
  - run_pipeline() returns exit code 3 when ≥1 connector fails
  - run_pipeline() returns exit code 0 when no connectors are enabled
  - Security allowlist: fetch_single() with a non-allowlisted handler name
    returns an ImportError message in the error field
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from pipeline import fetch_single, run_pipeline


# ---------------------------------------------------------------------------
# fetch_single — error tuples
# ---------------------------------------------------------------------------

class TestFetchSingleErrors:
    """fetch_single must never raise — it returns ([], 0.0, <error_str>)."""

    def test_unknown_connector_returns_error_not_raises(self):
        lines, elapsed, err = fetch_single(
            "totally_nonexistent_connector_xyz",
            cfg={},
        )
        assert lines == []
        assert elapsed == 0.0
        assert err is not None
        assert "not found" in err.lower() or "nonexistent" in err.lower()

    def test_unknown_connector_error_contains_name(self):
        lines, elapsed, err = fetch_single("my_fake_connector", cfg={})
        assert err is not None
        assert "my_fake_connector" in err

    def test_empty_cfg_unknown_connector_returns_error(self):
        lines, elapsed, err = fetch_single("test_conn", cfg={})
        assert err is not None
        assert isinstance(lines, list)
        assert isinstance(elapsed, float)

    def test_non_allowlisted_handler_returns_import_error(self):
        """fetch_single must not load arbitrary handler paths."""
        cfg = {
            "connectors": {
                "evil_conn": {
                    "enabled": True,
                    "fetch": {"handler": "connectors.evil_module__not_in_allowlist"},
                }
            }
        }
        lines, elapsed, err = fetch_single("evil_conn", cfg=cfg)
        assert err is not None
        # Error message must reference the handler or allowlist
        assert "allowlist" in err.lower() or "import" in err.lower() or "evil" in err.lower()

    def test_return_type_is_always_tuple_of_three(self):
        result = fetch_single("nonexistent", cfg={})
        assert isinstance(result, tuple)
        assert len(result) == 3
        lines, elapsed, err = result
        assert isinstance(lines, list)
        assert isinstance(elapsed, float)
        assert err is None or isinstance(err, str)


# ---------------------------------------------------------------------------
# fetch_single — auth failure
# ---------------------------------------------------------------------------

class TestFetchSingleAuthFailure:
    def test_auth_failure_returns_error_not_raises(self):
        """If load_auth_context raises, fetch_single should return error string."""
        cfg = {
            "connectors": {
                "msgraph_calendar": {
                    "enabled": True,
                    "fetch": {"handler": "connectors.msgraph_calendar"},
                }
            }
        }
        with patch("pipeline.load_auth_context", side_effect=Exception("auth exploded")):
            lines, elapsed, err = fetch_single("msgraph_calendar", cfg=cfg)
        assert err is not None
        assert "auth" in err.lower() or "exploded" in err.lower()


# ---------------------------------------------------------------------------
# run_pipeline — exit codes
# ---------------------------------------------------------------------------

class TestRunPipelineExitCodes:
    def test_returns_0_when_no_connectors(self):
        """Empty or all-disabled config → no work → exit 0."""
        rc = run_pipeline({}, since="2025-01-01T00:00:00Z")
        assert rc == 0

    def test_returns_0_for_all_disabled(self):
        cfg = {
            "connectors": {
                "test_conn": {
                    "enabled": False,
                    "fetch": {"handler": "connectors.google_email"},
                }
            }
        }
        rc = run_pipeline(cfg, since="2025-01-01T00:00:00Z")
        assert rc == 0

    def test_returns_3_when_handler_not_in_allowlist(self):
        """Connector with non-allowlisted handler → ImportError → error_count > 0 → rc=3."""
        cfg = {
            "connectors": {
                "unauthorized_conn": {
                    "enabled": True,
                    "fetch": {"handler": "connectors.unauthorized_module_xyz"},
                }
            }
        }
        rc = run_pipeline(cfg, since="2025-01-01T00:00:00Z")
        assert rc == 3

    def test_returns_3_when_auth_fails_for_all_connectors(self):
        """All connectors fail auth → partial-failure exit code."""
        cfg = {
            "connectors": {
                "msgraph_calendar": {
                    "enabled": True,
                    "fetch": {"handler": "connectors.msgraph_calendar"},
                }
            }
        }
        with patch("pipeline.load_auth_context", side_effect=Exception("no creds")):
            rc = run_pipeline(cfg, since="2025-01-01T00:00:00Z")
        assert rc == 3

    def test_source_filter_skips_other_connectors(self):
        """source_filter=[] → no connectors match → treated as empty → rc=0."""
        cfg = {
            "connectors": {
                "msgraph_calendar": {
                    "enabled": True,
                    "fetch": {"handler": "connectors.msgraph_calendar"},
                }
            }
        }
        rc = run_pipeline(cfg, since="2025-01-01T00:00:00Z", source_filter=["nonexistent_only"])
        assert rc == 0

    def test_returns_int(self):
        rc = run_pipeline({}, since="2025-01-01T00:00:00Z")
        assert isinstance(rc, int)
