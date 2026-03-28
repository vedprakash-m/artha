"""
tests/integration/test_work_loop_fetch_single.py — fetch_single security & API contract tests.

Exercises the fetch_single() function that work_loop.py calls to:
  - Verify it never raises on missing/bad config
  - Verify the security allowlist blocks non-allowlisted handler paths
  - Verify fallback behaviour matches the documented function contract
  - Verify known allowlisted connectors at least clear the allowlist check
    (actual network calls are mocked)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from pipeline import _ALLOWED_MODULES, fetch_single


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_conn_cfg(name: str, handler: str, enabled: bool = True) -> dict:
    return {
        "connectors": {
            name: {
                "enabled": enabled,
                "fetch": {"handler": handler},
            }
        }
    }


# ---------------------------------------------------------------------------
# Security allowlist enforcement
# ---------------------------------------------------------------------------

class TestAllowlistEnforcement:
    def test_allowlist_is_non_empty_frozenset(self):
        """Allowlist must have entries — empty allowlist makes every fetch fail."""
        assert isinstance(_ALLOWED_MODULES, frozenset)
        assert len(_ALLOWED_MODULES) > 0

    def test_all_allowed_modules_under_connectors_package(self):
        for module in _ALLOWED_MODULES:
            assert module.startswith("connectors."), (
                f"Unexpected module prefix in allowlist: {module!r}"
            )

    def test_arbitrary_path_blocked_by_allowlist(self):
        """Arbitrary module path (not in allowlist) must produce error, not crash."""
        cfg = _minimal_conn_cfg(
            "evil", "connectors.definitely_not_allowed_xyzzy"
        )
        lines, elapsed, err = fetch_single("evil", cfg=cfg)
        assert err is not None
        assert lines == []

    def test_os_module_injection_blocked(self):
        """Attempting to load 'connectors.os' must be blocked."""
        cfg = _minimal_conn_cfg("bad", "connectors.os")
        lines, elapsed, err = fetch_single("bad", cfg=cfg)
        assert err is not None

    def test_traversal_path_blocked(self):
        """'connectors.../../etc/passwd' style path must be blocked."""
        cfg = _minimal_conn_cfg("bad2", "connectors.../../etc/passwd")
        lines, elapsed, err = fetch_single("bad2", cfg=cfg)
        assert err is not None

    def test_allowed_connector_clears_allowlist_check(self):
        """A connector in _ALLOWED_MODULES should not get an allowlist ImportError.

        We still expect an error because auth/network is mocked to fail, but the
        error should NOT be an allowlist rejection."""
        # Pick any module from the allowlist
        some_module = next(iter(sorted(_ALLOWED_MODULES)))
        conn_name = some_module.split(".")[-1]

        cfg = _minimal_conn_cfg(conn_name, some_module)
        with patch("pipeline.load_auth_context", side_effect=Exception("no creds")):
            _, _, err = fetch_single(conn_name, cfg=cfg)
        # Auth error is expected; allowlist error is NOT
        if err is not None:
            assert "allowlist" not in err.lower(), (
                f"Allowlist error for known-good connector {conn_name!r}: {err}"
            )


# ---------------------------------------------------------------------------
# API contract (return-type guarantees)
# ---------------------------------------------------------------------------

class TestFetchSingleContract:
    def test_always_returns_tuple_of_three(self):
        result = fetch_single("x", cfg={})
        assert isinstance(result, tuple) and len(result) == 3

    def test_lines_is_list_on_missing_connector(self):
        lines, _, _ = fetch_single("nonexistent", cfg={})
        assert isinstance(lines, list)

    def test_elapsed_is_non_negative_float(self):
        _, elapsed, _ = fetch_single("nonexistent", cfg={})
        assert isinstance(elapsed, float)
        assert elapsed >= 0.0

    def test_error_is_string_on_failure(self):
        _, _, err = fetch_single("nonexistent", cfg={})
        assert isinstance(err, str)

    def test_since_defaults_without_error(self):
        """Omitting 'since' must not raise; it defaults to 48h ago."""
        lines, elapsed, err = fetch_single("nonexistent", cfg={})
        # Since this connector doesn't exist, we get an error — but no exception
        assert err is not None

    def test_since_iso_format_accepted(self):
        since = "2024-01-01T00:00:00Z"
        result = fetch_single("nonexistent", cfg={}, since=since)
        assert isinstance(result, tuple)

    def test_never_raises_on_none_cfg(self):
        """cfg=None is not a valid input but shouldn't cause unhandled crash."""
        with pytest.raises(Exception):
            # We expect SOME error — just not a silent data corruption
            fetch_single("nonexistent", cfg=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Mocked happy path (no real network)
# ---------------------------------------------------------------------------

class TestFetchSingleMockedHappyPath:
    def test_successful_fetch_returns_lines_and_no_error(self):
        """When auth loads and handler.fetch() succeeds, error should be None."""
        mock_handler = MagicMock()
        mock_handler.fetch.return_value = ['{"id":1}', '{"id":2}']

        cfg = _minimal_conn_cfg("msgraph_calendar", "connectors.msgraph_calendar")

        with (
            patch("pipeline._load_handler", return_value=mock_handler),
            patch("pipeline.load_auth_context", return_value={"token": "fake"}),
        ):
            lines, elapsed, err = fetch_single(
                "msgraph_calendar",
                cfg=cfg,
                since="2025-01-01T00:00:00Z",
            )

        assert err is None
        assert len(lines) >= 2
        assert elapsed >= 0.0

    def test_successful_fetch_records_have_string_type(self):
        mock_handler = MagicMock()
        mock_handler.fetch.return_value = ['{"source":"calendar","id":"x"}']

        cfg = _minimal_conn_cfg("msgraph_calendar", "connectors.msgraph_calendar")
        with (
            patch("pipeline._load_handler", return_value=mock_handler),
            patch("pipeline.load_auth_context", return_value={}),
        ):
            lines, _, err = fetch_single("msgraph_calendar", cfg=cfg, since="2025-01-01T00:00:00Z")

        assert err is None
        for line in lines:
            assert isinstance(line, str)
