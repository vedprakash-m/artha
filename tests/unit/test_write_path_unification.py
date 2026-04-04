"""tests/unit/test_write_path_unification.py — Integration tests for Wave 0 write-path unification.

Verifies that all former state-bypass writers now route through state_writer.write()
or state_writer.write_atomic(), and that middleware is invoked for state files.

Coverage (specs/agent-fw.md §7, test_write_path_unification.py):
  - calendar_writer routes through state_writer.write()
  - trust_enforcer routes through state_writer.write()
  - fact_extractor routes through state_writer.write()
  - checkpoint.write_checkpoint routes through state_writer.write_atomic()
  - Middleware is invoked for state file writes (calendar, trust, fact)
  - Middleware is NOT invoked for write_atomic (checkpoint)
  - Fails loudly (TypeError) when state_writer is unavailable (fail-safe, not fail-silent)
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# calendar_writer
# ---------------------------------------------------------------------------


class TestCalendarWriterUnification:
    def test_uses_state_write_when_available(self, tmp_path):
        """calendar_writer.run() must call _state_write, not CALENDAR_FILE.write_text."""
        import calendar_writer as cw

        mock_write = MagicMock(return_value=MagicMock(success=True))
        with (
            patch.object(cw, "_state_write", mock_write),
            patch.object(cw, "CALENDAR_FILE", tmp_path / "calendar.md"),
            patch.object(cw, "STATE_DIR", tmp_path),
        ):
            # Provide minimal calendar.md so the writer has something to update
            (tmp_path / "calendar.md").write_text("# Calendar\n", encoding="utf-8")
            # Run with a JSONL payload that has a calendar event
            jsonl = json.dumps({
                "source": "google_calendar",
                "kind": "calendar_event",
                "date": "2026-05-01",
                "title": "Test Event",
                "time": "09:00",
                "duration_min": 30,
                "calendar": "primary",
                "attendees": [],
                "location": "",
                "description": "",
            }) + "\n"

            import io
            import sys
            old_stdin = sys.stdin
            sys.stdin = io.StringIO(jsonl)
            try:
                cw.run()
            finally:
                sys.stdin = old_stdin

        mock_write.assert_called()
        call_kwargs = mock_write.call_args
        assert call_kwargs is not None
        # Verify domain= kwarg was passed
        _, kwargs = call_kwargs
        assert kwargs.get("domain") == "calendar"

    def test_fails_loudly_when_state_write_none(self, tmp_path):
        """Post-Wave-0: fallback direct writes are removed.  When _state_write
        is None (import failed), calendar_writer raises TypeError immediately.
        Fail-loud is safer than silent middleware bypass.
        """
        import calendar_writer as cw
        import io
        import sys
        calendar_file = tmp_path / "calendar.md"
        calendar_file.write_text("# Calendar\n", encoding="utf-8")

        with (
            patch.object(cw, "_state_write", None),
            patch.object(cw, "CALENDAR_FILE", calendar_file),
            patch.object(cw, "STATE_DIR", tmp_path),
        ):
            jsonl = json.dumps({
                "source": "google_calendar",
                "kind": "calendar_event",
                "date": "2026-05-01",
                "title": "Fallback Event",
                "time": "10:00",
                "duration_min": 60,
                "calendar": "primary",
                "attendees": [],
                "location": "",
                "description": "",
            }) + "\n"

            old_stdin = sys.stdin
            sys.stdin = io.StringIO(jsonl)
            try:
                with pytest.raises(TypeError):
                    cw.run()
            finally:
                sys.stdin = old_stdin


# ---------------------------------------------------------------------------
# trust_enforcer
# ---------------------------------------------------------------------------


class TestTrustEnforcerUnification:
    def _make_health_check(self, tmp_path: Path) -> Path:
        hc = tmp_path / "state" / "health-check.md"
        hc.parent.mkdir(parents=True, exist_ok=True)
        hc.write_text(textwrap.dedent("""\
            # Health Check

            ```yaml
            autonomy:
              trust_level: 2
              trust_level_since: 2026-01-01
              days_at_level: 30
              acceptance_rate_90d: 0.9
              critical_false_positives: 0
              pre_approved_categories: []
              last_demotion: null
              last_elevation: 2026-01-01
            ```
        """), encoding="utf-8")
        return hc

    def test_reset_trust_uses_state_write(self, tmp_path):
        from trust_enforcer import TrustEnforcer
        self._make_health_check(tmp_path)
        enforcer = TrustEnforcer(tmp_path)

        import trust_enforcer as te
        mock_write = MagicMock(return_value=MagicMock(success=True))
        with patch.object(te, "_state_write", mock_write):
            enforcer.apply_demotion()

        mock_write.assert_called_once()
        _, kwargs = mock_write.call_args
        assert kwargs.get("domain") == "health_check"

    def test_update_autonomy_uses_state_write(self, tmp_path):
        from trust_enforcer import TrustEnforcer
        self._make_health_check(tmp_path)
        enforcer = TrustEnforcer(tmp_path)

        import trust_enforcer as te
        mock_write = MagicMock(return_value=MagicMock(success=True))
        with patch.object(te, "_state_write", mock_write):
            enforcer.update_autonomy_block({"days_at_level": 31})

        mock_write.assert_called_once()
        _, kwargs = mock_write.call_args
        assert kwargs.get("domain") == "health_check"

    def test_fails_loudly_when_state_write_none(self, tmp_path):
        """Post-Wave-0: fallback direct writes are removed.  When _state_write
        is None, trust_enforcer raises TypeError — fail-loud by design.
        """
        from trust_enforcer import TrustEnforcer
        import trust_enforcer as te
        self._make_health_check(tmp_path)
        enforcer = TrustEnforcer(tmp_path)

        with patch.object(te, "_state_write", None):
            with pytest.raises(TypeError):
                enforcer.update_autonomy_block({"days_at_level": 99})


# ---------------------------------------------------------------------------
# fact_extractor
# ---------------------------------------------------------------------------


class TestFactExtractorUnification:
    def test_persist_facts_uses_state_write(self, tmp_path):
        """persist_facts() must call _state_write for state/memory.md."""
        import fact_extractor as fe

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        expected_memory_path = state_dir / "memory.md"

        mock_write = MagicMock(return_value=MagicMock(success=True))

        # Build a minimal fact to persist
        fact = fe.Fact(
            id="test-fact-001",
            type="preference",
            domain="finance",
            statement="Test fact statement.",
            source="session-test",
        )

        with patch("fact_extractor._load_harness_flag", return_value=True), \
             patch.object(fe, "_state_write", mock_write):
            fe.persist_facts([fact], tmp_path)

        mock_write.assert_called_once()
        pos_args, kwargs = mock_write.call_args
        assert pos_args[0] == expected_memory_path
        assert kwargs.get("domain") == "memory"

    def test_fails_loudly_when_state_write_none(self, tmp_path):
        """Post-Wave-0: fallback direct writes are removed.  When _state_write
        is None, fact_extractor raises TypeError — fail-loud by design.
        """
        import fact_extractor as fe

        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        fact = fe.Fact(
            id="fallback-fact-001",
            type="pattern",
            domain="health",
            statement="Fallback test fact.",
            source="session-test",
        )

        with patch("fact_extractor._load_harness_flag", return_value=True), \
             patch.object(fe, "_state_write", None), \
             pytest.raises(TypeError):
            fe.persist_facts([fact], tmp_path)


# ---------------------------------------------------------------------------
# checkpoint — write_atomic
# ---------------------------------------------------------------------------


class TestCheckpointUnification:
    def test_write_checkpoint_uses_write_atomic(self, tmp_path):
        """write_checkpoint must call _write_atomic, NOT path.write_text."""
        import checkpoint

        mock_atomic = MagicMock(return_value=MagicMock(success=True))
        with (
            patch.object(checkpoint, "_write_atomic", mock_atomic),
            patch("checkpoint._is_enabled", return_value=True),
        ):
            checkpoint.write_checkpoint(tmp_path, 4, email_count=10)

        mock_atomic.assert_called_once()
        pos_args = mock_atomic.call_args[0]
        target_path = pos_args[0]
        assert "tmp" in str(target_path)
        assert ".checkpoint.json" in str(target_path)

    def test_checkpoint_does_not_call_state_write(self, tmp_path):
        """write_checkpoint must use write_atomic (not write) — no middleware."""
        import checkpoint
        from lib import state_writer

        mock_write = MagicMock(return_value=MagicMock(success=True))
        mock_atomic = MagicMock(return_value=MagicMock(success=True))
        with (
            patch.object(state_writer, "write", mock_write),
            patch.object(checkpoint, "_write_atomic", mock_atomic),
            patch("checkpoint._is_enabled", return_value=True),
        ):
            checkpoint.write_checkpoint(tmp_path, 5)

        mock_write.assert_not_called()
        mock_atomic.assert_called_once()

    def test_fails_loudly_when_write_atomic_none(self, tmp_path):
        """Post-Wave-0: fallback direct writes are removed.  When _write_atomic
        is None, checkpoint.write_checkpoint raises TypeError — fail-loud by design.
        """
        import checkpoint
        with (
            patch.object(checkpoint, "_write_atomic", None),
            patch("checkpoint._is_enabled", return_value=True),
            pytest.raises(TypeError),
        ):
            checkpoint.write_checkpoint(tmp_path, 3)


# ---------------------------------------------------------------------------
# Middleware invocation for state writes
# ---------------------------------------------------------------------------


class TestMiddlewareInvokedForStateWrites:
    """Verify state_writer.write() triggers middleware before_write/after_write."""

    def test_middleware_before_write_called_on_state_write(self, tmp_path):
        from lib.state_writer import write
        from middleware import _PassthroughMiddleware

        mock_mw = MagicMock(spec=_PassthroughMiddleware)
        mock_mw.before_write.return_value = "content"
        mock_mw.after_write.return_value = None

        with patch("lib.state_writer._build_middleware_stack", return_value=mock_mw):
            target = tmp_path / "state.md"
            write(target, "content", domain="test", source="test", snapshot=False)

        mock_mw.before_write.assert_called_once()
        args = mock_mw.before_write.call_args[0]
        assert args[0] == "test"  # domain

    def test_write_atomic_does_not_call_middleware(self, tmp_path):
        """write_atomic must bypass middleware entirely."""
        from lib.state_writer import write_atomic

        with patch("lib.state_writer._build_middleware_stack") as mock_build:
            target = tmp_path / "tmp" / "checkpoint.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(target, '{}')

        mock_build.assert_not_called()
