"""
tests/unit/test_health_check_updater.py — Unit tests for scripts/health_check_updater.py

Coverage:
  - update_channel_health_md: does nothing when health-check.md does not exist
  - update_channel_health_md: creates new channel block when section exists but channel absent
  - update_channel_health_md: updates existing channel block in-place
  - update_channel_health_md: sets healthy=true / healthy=false correctly
  - update_channel_health_md: optional last_push field written when provided
  - update_channel_health_md: optional push_count_today field written when provided
  - update_channel_health_md: appends section when no Channel Health section exists
  - update_channel_health_md: preserves other content in the file
  - update_channel_health_md: silent OSError does not propagate to caller
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_health_md_with_section(channel: str = "gmail") -> str:
    """Returns a health-check.md snippet containing the Channel Health section."""
    return textwrap.dedent(f"""\
        # Health Check

        Some preamble.

        ## Channel Health (Structured)
        ```yaml
        channel_health:
          {channel}:
            last_check: "2025-01-01T00:00:00+00:00"
            healthy: true
        ```

        ## Other Section
        Extra content.
    """)


def _minimal_health_md_without_section() -> str:
    return textwrap.dedent("""\
        # Health Check

        Some preamble.

        ## Other Section
        Extra content.
    """)


# ---------------------------------------------------------------------------
# File-not-found guard
# ---------------------------------------------------------------------------

class TestMissingFile:
    def test_no_crash_when_file_absent(self, tmp_path):
        """Should silently do nothing if health-check.md doesn't exist."""
        missing_dir = tmp_path / "state"
        missing_dir.mkdir()
        with patch("health_check_updater.STATE_DIR", missing_dir):
            # Must not raise
            import health_check_updater
            health_check_updater.update_channel_health_md("gmail", True)

    def test_returns_none_when_file_absent(self, tmp_path):
        missing_dir = tmp_path / "state"
        missing_dir.mkdir()
        with patch("health_check_updater.STATE_DIR", missing_dir):
            import health_check_updater
            result = health_check_updater.update_channel_health_md("gmail", True)
            assert result is None


# ---------------------------------------------------------------------------
# Updating existing channel block
# ---------------------------------------------------------------------------

class TestUpdateExistingChannel:
    def _run(self, tmp_path, initial_content, **kwargs):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        health_md = state_dir / "health-check.md"
        health_md.write_text(initial_content, encoding="utf-8")

        import health_check_updater
        with patch("health_check_updater.STATE_DIR", state_dir):
            health_check_updater.update_channel_health_md("gmail", **kwargs)

        return health_md.read_text(encoding="utf-8")

    def test_healthy_true_written(self, tmp_path):
        content = _minimal_health_md_with_section("gmail")
        result = self._run(tmp_path, content, healthy=True)
        assert "healthy: true" in result

    def test_healthy_false_written(self, tmp_path):
        content = _minimal_health_md_with_section("gmail")
        result = self._run(tmp_path, content, healthy=False)
        assert "healthy: false" in result

    def test_last_push_written_when_provided(self, tmp_path):
        content = _minimal_health_md_with_section("gmail")
        result = self._run(tmp_path, content, healthy=True, last_push="2025-06-01T12:00:00+00:00")
        assert "last_push" in result
        assert "2025-06-01T12:00:00+00:00" in result

    def test_last_push_absent_when_not_provided(self, tmp_path):
        content = _minimal_health_md_with_section("gmail")
        result = self._run(tmp_path, content, healthy=True)
        assert "last_push" not in result

    def test_push_count_today_written_when_provided(self, tmp_path):
        content = _minimal_health_md_with_section("gmail")
        result = self._run(tmp_path, content, healthy=True, push_count_today=5)
        assert "push_count_today: 5" in result

    def test_last_check_updated(self, tmp_path):
        """last_check should be updated to current time (not the old static value)."""
        content = _minimal_health_md_with_section("gmail")
        result = self._run(tmp_path, content, healthy=True)
        assert "last_check" in result
        # Original static timestamp should be replaced
        assert "2025-01-01T00:00:00" not in result

    def test_other_content_preserved(self, tmp_path):
        content = _minimal_health_md_with_section("gmail")
        result = self._run(tmp_path, content, healthy=True)
        assert "## Other Section" in result
        assert "Extra content." in result


# ---------------------------------------------------------------------------
# Adding new channel to existing section
# ---------------------------------------------------------------------------

class TestAddNewChannel:
    def test_new_channel_added_to_existing_section(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        health_md = state_dir / "health-check.md"
        # Section exists but only has 'gmail' — we add 'slack'
        health_md.write_text(_minimal_health_md_with_section("gmail"), encoding="utf-8")

        import health_check_updater
        with patch("health_check_updater.STATE_DIR", state_dir):
            health_check_updater.update_channel_health_md("slack", True)

        result = health_md.read_text(encoding="utf-8")
        assert "slack:" in result
        assert "gmail:" in result  # existing preserved


# ---------------------------------------------------------------------------
# Appending section when absent
# ---------------------------------------------------------------------------

class TestAppendSection:
    def test_section_appended_when_missing(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        health_md = state_dir / "health-check.md"
        health_md.write_text(_minimal_health_md_without_section(), encoding="utf-8")

        import health_check_updater
        with patch("health_check_updater.STATE_DIR", state_dir):
            health_check_updater.update_channel_health_md("gmail", True)

        result = health_md.read_text(encoding="utf-8")
        assert "## Channel Health (Structured)" in result
        assert "channel_health:" in result
        assert "gmail:" in result

    def test_original_content_preserved_after_append(self, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        health_md = state_dir / "health-check.md"
        health_md.write_text(_minimal_health_md_without_section(), encoding="utf-8")

        import health_check_updater
        with patch("health_check_updater.STATE_DIR", state_dir):
            health_check_updater.update_channel_health_md("gmail", True)

        result = health_md.read_text(encoding="utf-8")
        assert "Some preamble." in result
        assert "## Other Section" in result


# ---------------------------------------------------------------------------
# Silent OSError
# ---------------------------------------------------------------------------

class TestSilentOsError:
    def test_oserror_on_write_does_not_propagate(self, tmp_path):
        """WS-6 target: OSError on write_text is caught silently."""
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        health_md = state_dir / "health-check.md"
        health_md.write_text(_minimal_health_md_with_section("gmail"), encoding="utf-8")

        import health_check_updater
        with patch("health_check_updater.STATE_DIR", state_dir):
            with patch.object(Path, "write_text", side_effect=OSError("disk full")):
                # Must not raise — silent catch
                health_check_updater.update_channel_health_md("gmail", True)
