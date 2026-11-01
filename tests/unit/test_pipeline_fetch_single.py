"""Unit tests for pipeline.fetch_single() — WS-1C."""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts/ is importable (conftest also sets ARTHA_NO_REEXEC before this)
_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg_with(connector_name: str, handler: str = "connectors.google_email", **extra) -> dict:
    """Build a minimal connectors config that contains one connector."""
    return {
        "connectors": {
            connector_name: {
                "enabled": True,
                "fetch": {"handler": handler, **extra},
                "retry": {"max_attempts": 1, "base_delay_seconds": 0.0},
            }
        }
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFetchSingleUnknownConnector:
    """Connector name not present in config → returns error, no crash."""

    def test_unknown_connector_returns_error(self):
        import pipeline  # lazy import: conftest sets ARTHA_NO_REEXEC before tests run
        cfg = {"connectors": {}}

        lines, elapsed, error = pipeline.fetch_single("nonexistent", cfg)

        assert lines == []
        assert elapsed == 0.0
        assert error is not None
        assert "nonexistent" in error
        assert "not found" in error


class TestFetchSingleBlockedModule:
    """Handler path not on the security allowlist → ImportError surfaced as error tuple."""

    def test_blocked_module_returns_error_string(self):
        import pipeline
        cfg = _cfg_with("evil_conn", handler="connectors.evil_module")

        lines, elapsed, error = pipeline.fetch_single("evil_conn", cfg)

        assert lines == []
        assert error is not None
        assert "allowlist" in error or "evil_module" in error


class TestFetchSingleAuthCtx:
    """Provided auth_ctx is forwarded; load_auth_context is NOT called."""

    def test_provided_auth_ctx_bypasses_load(self):
        import pipeline
        cfg = _cfg_with("outlook_calendar", handler="connectors.msgraph_calendar")
        fake_auth = {"access_token": "test-token"}

        mock_handler = MagicMock()
        mock_handler.fetch.return_value = iter([])

        with patch.object(pipeline, "_load_handler", return_value=mock_handler), \
             patch.object(pipeline, "load_auth_context") as mock_auth_load:
            lines, elapsed, error = pipeline.fetch_single(
                "outlook_calendar", cfg, auth_ctx=fake_auth
            )

        mock_auth_load.assert_not_called()
        call_kwargs = mock_handler.fetch.call_args.kwargs
        assert call_kwargs["auth_context"] == fake_auth
        assert error is None


class TestFetchSingleSinceDefault:
    """since=None → defaults to approximately 48 hours ago (within 5s tolerance)."""

    def test_since_defaults_to_48h_ago(self):
        import pipeline
        cfg = _cfg_with("outlook_email", handler="connectors.msgraph_email")

        captured_since: list[str] = []

        def _fake_fetch_one(name, handler, auth_ctx, fetch_cfg, retry_cfg, *, since):
            captured_since.append(since)
            return (name, [], 0.0, None)

        with patch.object(pipeline, "_load_handler", return_value=MagicMock()), \
             patch.object(pipeline, "load_auth_context", return_value={}), \
             patch.object(pipeline, "_fetch_one", side_effect=_fake_fetch_one):
            pipeline.fetch_single("outlook_email", cfg)

        assert len(captured_since) == 1
        since_dt = datetime.fromisoformat(captured_since[0].replace("Z", "+00:00"))
        expected = datetime.now(timezone.utc) - timedelta(hours=48)
        delta = abs((since_dt - expected).total_seconds())
        assert delta < 5, f"since defaulted to {captured_since[0]!r}, expected ~48h ago"


class TestFetchSingleExtraFetchOverrides:
    """extra_fetch kwargs override YAML connector config values."""

    def test_extra_fetch_overrides_connector_cfg(self):
        import pipeline
        # Connector YAML has max_results=100 and window_days=3 in fetch config
        cfg = _cfg_with("outlook_calendar", handler="connectors.msgraph_calendar",
                        max_results=100, window_days=3)

        captured_fetch_cfg: list[dict] = []

        def _fake_fetch_one(name, handler, auth_ctx, fetch_cfg, retry_cfg, *, since):
            captured_fetch_cfg.append(dict(fetch_cfg))
            return (name, [], 0.0, None)

        with patch.object(pipeline, "_load_handler", return_value=MagicMock()), \
             patch.object(pipeline, "load_auth_context", return_value={}), \
             patch.object(pipeline, "_fetch_one", side_effect=_fake_fetch_one):
            pipeline.fetch_single(
                "outlook_calendar", cfg,
                max_results=50,   # caller override: 100 → 50
                window_days=7,    # caller override: 3 → 7
            )

        assert len(captured_fetch_cfg) == 1
        merged = captured_fetch_cfg[0]
        assert merged["max_results"] == 50, "caller max_results should override YAML"
        assert merged["window_days"] == 7, "caller window_days should override YAML"
