"""tests/unit/test_artha_context.py — Unit tests for scripts/artha_context.py

Phase 3 verification suite (specs/agentic-improve.md).

Coverage:
  - ArthaContext construction with defaults
  - build_context() from env_manifest and preflight results
  - Accessor properties (connectors_online, connectors_offline)
  - health_summary() serialisation
  - Middleware backward compatibility (ctx=None default)
  - Feature flag disabled returns safe defaults
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest adds scripts/ to sys.path
from artha_context import (
    ArthaContext,
    ConnectorStatus,
    ContextPressure,
    build_context,
)


# ---------------------------------------------------------------------------
# ArthaContext construction
# ---------------------------------------------------------------------------


class TestArthaContextDefaults:
    def test_default_command_is_unknown(self):
        ctx = ArthaContext()
        assert ctx.command == "unknown"

    def test_default_environment_is_local_mac(self):
        ctx = ArthaContext()
        assert ctx.environment == "local_mac"

    def test_default_pressure_is_green(self):
        ctx = ArthaContext()
        assert ctx.pressure == ContextPressure.GREEN

    def test_default_preflight_passed_is_true(self):
        ctx = ArthaContext()
        assert ctx.preflight_passed is True

    def test_default_is_degraded_is_false(self):
        ctx = ArthaContext()
        assert ctx.is_degraded is False

    def test_session_start_is_set_automatically(self):
        ctx = ArthaContext()
        assert ctx.session_start is not None
        assert isinstance(ctx.session_start, datetime)

    def test_empty_lists_for_collections(self):
        ctx = ArthaContext()
        assert ctx.connectors == []
        assert ctx.active_domains == []
        assert ctx.steps_executed == []
        assert ctx.degradations == []


class TestArthaContextCustomFields:
    def test_command_stored(self):
        ctx = ArthaContext(command="/catch-up")
        assert ctx.command == "/catch-up"

    def test_is_degraded_stored(self):
        ctx = ArthaContext(is_degraded=True, degradations=["vault_decrypt_unavailable"])
        assert ctx.is_degraded is True
        assert "vault_decrypt_unavailable" in ctx.degradations

    def test_pressure_enum_stored(self):
        ctx = ArthaContext(pressure=ContextPressure.RED)
        assert ctx.pressure == ContextPressure.RED

    def test_connectors_stored(self):
        connectors = [
            ConnectorStatus(name="gmail", online=True),
            ConnectorStatus(name="outlook", online=False, last_error="network blocked"),
        ]
        ctx = ArthaContext(connectors=connectors)
        assert len(ctx.connectors) == 2


# ---------------------------------------------------------------------------
# Accessor properties
# ---------------------------------------------------------------------------


class TestConnectorAccessors:
    def _ctx_with_connectors(self) -> ArthaContext:
        return ArthaContext(connectors=[
            ConnectorStatus(name="gmail", online=True),
            ConnectorStatus(name="gcal", online=True),
            ConnectorStatus(name="outlook", online=False),
        ])

    def test_connectors_online_returns_online_names(self):
        ctx = self._ctx_with_connectors()
        assert set(ctx.connectors_online) == {"gmail", "gcal"}

    def test_connectors_offline_returns_offline_names(self):
        ctx = self._ctx_with_connectors()
        assert ctx.connectors_offline == ["outlook"]

    def test_all_online_no_offline(self):
        ctx = ArthaContext(connectors=[
            ConnectorStatus(name="gmail", online=True),
        ])
        assert ctx.connectors_offline == []

    def test_all_offline_no_online(self):
        ctx = ArthaContext(connectors=[
            ConnectorStatus(name="outlook", online=False),
        ])
        assert ctx.connectors_online == []

    def test_empty_connectors(self):
        ctx = ArthaContext()
        assert ctx.connectors_online == []
        assert ctx.connectors_offline == []


# ---------------------------------------------------------------------------
# health_summary() serialisation
# ---------------------------------------------------------------------------


class TestHealthSummary:
    def test_health_summary_is_dict(self):
        ctx = ArthaContext(command="/status")
        summary = ctx.health_summary()
        assert isinstance(summary, dict)

    def test_health_summary_has_required_keys(self):
        ctx = ArthaContext()
        summary = ctx.health_summary()
        required = {
            "command", "environment", "pressure", "preflight_passed",
            "is_degraded", "connectors_online", "connectors_offline",
            "active_domains", "steps_executed",
        }
        assert required <= set(summary.keys())

    def test_health_summary_pressure_is_string(self):
        ctx = ArthaContext(pressure=ContextPressure.YELLOW)
        summary = ctx.health_summary()
        assert summary["pressure"] == "yellow"

    def test_health_summary_is_json_serialisable(self):
        import json
        ctx = ArthaContext(
            command="/catch-up",
            connectors=[ConnectorStatus(name="gmail", online=True)],
            active_domains=["finance", "immigration"],
            steps_executed=[0, 1, 2],
        )
        summary = ctx.health_summary()
        json_str = json.dumps(summary)
        parsed = json.loads(json_str)
        assert parsed["command"] == "/catch-up"


# ---------------------------------------------------------------------------
# build_context()
# ---------------------------------------------------------------------------


class TestBuildContextDefaults:
    def test_default_context_green_pressure(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("unknown")
        assert ctx.pressure == ContextPressure.GREEN

    def test_default_context_not_degraded(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("/status")
        assert ctx.is_degraded is False

    def test_default_context_preflight_passed(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("/catch-up")
        assert ctx.preflight_passed is True

    def test_command_stored(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("/catch-up deep")
        assert "/catch-up" in ctx.command


class TestBuildContextFromEnvManifest:
    def _local_manifest(self) -> dict:
        return {
            "environment": "local_mac",
            "capabilities": {"filesystem_writable": True, "age_installed": True},
            "degradations": [],
        }

    def _vm_manifest(self) -> dict:
        return {
            "environment": "cowork_vm",
            "capabilities": {"filesystem_writable": False, "age_installed": False},
            "degradations": ["vault_decrypt_unavailable", "state_readonly"],
        }

    def test_local_manifest_not_degraded(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("/catch-up", env_manifest=self._local_manifest())
        assert ctx.is_degraded is False
        assert ctx.environment == "local_mac"

    def test_vm_manifest_is_degraded(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("/catch-up", env_manifest=self._vm_manifest())
        assert ctx.is_degraded is True
        assert ctx.environment == "cowork_vm"
        assert "vault_decrypt_unavailable" in ctx.degradations

    def test_vm_manifest_degradations_populated(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("/catch-up", env_manifest=self._vm_manifest())
        assert len(ctx.degradations) == 2

    def test_empty_manifest_safe_defaults(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("/catch-up", env_manifest={})
        assert ctx.environment == "local_mac"
        assert ctx.is_degraded is False


class TestBuildContextFromPreflightResults:
    """Test preflight_results → preflight_passed mapping."""

    def _p0_fail(self):
        """Simulate a P0 preflight failure."""
        class MockCheck:
            severity = "P0"
            ok = False
        return MockCheck()

    def _p0_pass(self):
        class MockCheck:
            severity = "P0"
            ok = True
        return MockCheck()

    def _p1_warn(self):
        class MockCheck:
            severity = "P1"
            ok = False  # non-blocking
        return MockCheck()

    def test_no_results_preflight_passed(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("/catch-up", preflight_results=None)
        assert ctx.preflight_passed is True

    def test_all_p0_pass_preflight_passed(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("/catch-up", preflight_results=[self._p0_pass()])
        assert ctx.preflight_passed is True

    def test_p0_fail_sets_preflight_failed(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("/catch-up", preflight_results=[self._p0_fail()])
        assert ctx.preflight_passed is False

    def test_p1_warning_does_not_fail_preflight(self):
        with patch("artha_context._load_harness_flag", return_value=True):
            ctx = build_context("/catch-up", preflight_results=[self._p1_warn()])
        assert ctx.preflight_passed is True


class TestBuildContextFeatureFlag:
    def test_flag_disabled_returns_safe_defaults(self):
        """When flag disabled, build_context returns context with safe defaults."""
        with patch("artha_context._load_harness_flag", return_value=False):
            ctx = build_context("/catch-up")
        # Returned context has command set but conservative defaults
        assert ctx.command == "/catch-up"
        assert ctx.pressure == ContextPressure.GREEN
        assert ctx.preflight_passed is True


# ---------------------------------------------------------------------------
# Middleware backward compatibility
# ---------------------------------------------------------------------------


class TestMiddlewareBackwardCompat:
    """Verify existing middleware callers work without ctx parameter."""

    def test_passthrough_middleware_works_without_ctx(self):
        from middleware import _PassthroughMiddleware
        mw = _PassthroughMiddleware()
        result = mw.before_write("finance", "old content", "new content")
        assert result == "new content"

    def test_passthrough_middleware_works_with_ctx_none(self):
        from middleware import _PassthroughMiddleware
        mw = _PassthroughMiddleware()
        result = mw.before_write("finance", "old", "new", ctx=None)
        assert result == "new"

    def test_passthrough_middleware_works_with_artha_context(self):
        from middleware import _PassthroughMiddleware
        mw = _PassthroughMiddleware()
        ctx = ArthaContext(command="/catch-up", pressure=ContextPressure.YELLOW)
        result = mw.before_write("finance", "old", "new", ctx=ctx)
        assert result == "new"

    def test_after_write_unchanged(self, tmp_path):
        from middleware import _PassthroughMiddleware
        mw = _PassthroughMiddleware()
        fake_path = tmp_path / "finance.md"
        # Should not raise
        mw.after_write("finance", fake_path)
