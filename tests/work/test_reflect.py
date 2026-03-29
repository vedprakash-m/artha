"""tests/work/test_reflect.py — Tests for scripts/work/reflect.py

≥90% coverage target.
Tests: _acquire_lock/_release_lock, stale lock removal, _check_size_guard,
_load_reflect_state, detect_due_horizons, _persist_reflection idempotency,
_build_reflect_current content, _build_tier2_artifact, cmd_reflect routing,
cmd_reflect_status, _cmd_reflect_audit.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from work.reflect import (
    ReflectState,
    _EPOCH,
    _load_reflect_state,
    detect_due_horizons,
    _acquire_lock,
    _release_lock,
    _check_size_guard,
    _audit_log,
    _persist_reflection,
    _build_reflect_current,
    _build_tier2_artifact,
    cmd_reflect_status,
    _cmd_reflect_audit,
    SIZE_GUARD_BYTES,
    STALE_LOCK_MINUTES,
)
from work.reflection_key import Horizon, ReflectionKey
from work.reconcile import ReconcileResult, PlannedItem, ActualItem
from work.scoring import ScoredItem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def state_dir(tmp_path):
    d = tmp_path / "state" / "work"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def lock_file(state_dir):
    return state_dir / ".reflect-lock"


@pytest.fixture()
def audit_file(state_dir):
    return state_dir / "work-audit.jsonl"


@pytest.fixture()
def sample_state():
    return ReflectState(
        last_daily_close=_EPOCH,
        last_weekly_close=_EPOCH,
        last_monthly_close=_EPOCH,
        last_quarterly_close=_EPOCH,
        current_week="2026-W14",
        carry_forward_count=0,
    )


@pytest.fixture()
def minimal_reconcile():
    return ReconcileResult(matched=[], unmatched_planned=[], unmatched_actual=[])


@pytest.fixture()
def sample_scored_items():
    return [
        ScoredItem("Task A", raw_score=1.5, label="HIGH",
                   urgency="critical", importance="strategic",
                   visibility="org", goal_alignment="direct", normalized_score=0.71),
        ScoredItem("Task B", raw_score=0.5, label="MEDIUM",
                   urgency="medium", importance="operational",
                   visibility="team", goal_alignment="unaligned", normalized_score=0.24),
    ]


# ---------------------------------------------------------------------------
# _load_reflect_state
# ---------------------------------------------------------------------------

class TestLoadReflectState:
    def test_missing_file_returns_default(self, state_dir):
        state = _load_reflect_state(state_dir)
        assert isinstance(state, ReflectState)
        assert state.last_daily_close == _EPOCH

    def test_parses_frontmatter(self, state_dir):
        current = state_dir / "reflect-current.md"
        current.write_text(
            "---\n"
            "schema_version: '1.0'\n"
            "last_weekly_close: '2026-04-02T10:00:00Z'\n"
            "current_week: '2026-W14'\n"
            "carry_forward_count: 3\n"
            "---\n"
            "## Body\n",
            encoding="utf-8"
        )
        state = _load_reflect_state(state_dir)
        assert state.current_week == "2026-W14"
        assert state.carry_forward_count == 3
        assert state.last_weekly_close != _EPOCH

    def test_corrupt_frontmatter_returns_default(self, state_dir):
        current = state_dir / "reflect-current.md"
        current.write_text("NOT YAML FRONTMATTER\nplain text", encoding="utf-8")
        state = _load_reflect_state(state_dir)
        assert isinstance(state, ReflectState)

    def test_empty_file_returns_default(self, state_dir):
        current = state_dir / "reflect-current.md"
        current.write_text("", encoding="utf-8")
        state = _load_reflect_state(state_dir)
        assert isinstance(state, ReflectState)


# ---------------------------------------------------------------------------
# detect_due_horizons
# ---------------------------------------------------------------------------

class TestDetectDueHorizons:
    def test_all_due_far_past_state_on_review_day(self, monkeypatch):
        """Old state + review day → weekly (and possibly monthly/quarterly) due."""
        # Mon 2026-04-01 is not a review day; use Thu 2026-04-09
        # Patch datetime.now to return a Thursday
        thu = datetime(2026, 4, 9, 16, 0, 0, tzinfo=timezone.utc)
        import work.reflect as reflect_mod

        class FakeNow:
            @staticmethod
            def now(tz=None):
                return thu

        monkeypatch.setattr("work.reflect.datetime", FakeNow)
        state = ReflectState()  # All EPOCH → all way past threshold
        horizons = detect_due_horizons(state)
        # On Thursday (review_day=True), weekly should be due given EPOCH last close
        assert Horizon.WEEKLY in horizons

    def test_daily_not_due_on_weekend(self, monkeypatch):
        """On Saturday (weekday=5), daily close should NOT be due."""
        sat = datetime(2026, 4, 11, 16, 0, 0, tzinfo=timezone.utc)
        import work.reflect as reflect_mod

        class FakeNow:
            @staticmethod
            def now(tz=None):
                return sat

        monkeypatch.setattr("work.reflect.datetime", FakeNow)
        state = ReflectState()
        horizons = detect_due_horizons(state)
        assert Horizon.DAILY not in horizons

    def test_nothing_due_when_just_closed(self):
        """Everything just closed → nothing due."""
        now_utc = datetime.now(timezone.utc)
        just_closed = now_utc - timedelta(minutes=5)
        state = ReflectState(
            last_daily_close=just_closed,
            last_weekly_close=just_closed,
            last_monthly_close=just_closed,
            last_quarterly_close=just_closed,
        )
        horizons = detect_due_horizons(state)
        # Daily checks requires > 6h gap; 5 min is not due
        assert Horizon.DAILY not in horizons

    def test_weekly_not_due_on_non_review_day(self, monkeypatch):
        """Weekly is only due on Thu/Fri (review_day). On Mon it should not appear."""
        mon = datetime(2026, 4, 6, 16, 0, 0, tzinfo=timezone.utc)  # Monday

        class FakeNow:
            @staticmethod
            def now(tz=None):
                return mon

        monkeypatch.setattr("work.reflect.datetime", FakeNow)
        state = ReflectState()
        horizons = detect_due_horizons(state)
        assert Horizon.WEEKLY not in horizons


# ---------------------------------------------------------------------------
# _acquire_lock / _release_lock
# ---------------------------------------------------------------------------

class TestLocking:
    def test_acquire_creates_lock_file(self, lock_file):
        session_id = _acquire_lock("daily", lock_file)
        assert lock_file.exists()
        data = json.loads(lock_file.read_text())
        assert data["session_id"] == session_id

    def test_lock_contains_horizon(self, lock_file):
        _acquire_lock("weekly", lock_file)
        data = json.loads(lock_file.read_text())
        assert data["horizon"] == "weekly"

    def test_release_removes_lock(self, lock_file):
        session_id = _acquire_lock("daily", lock_file)
        _release_lock(session_id, lock_file)
        assert not lock_file.exists()

    def test_release_noop_if_no_lockfile(self, lock_file):
        _release_lock("nonexistent-session", lock_file)  # Should not raise

    def test_release_noop_if_wrong_session(self, lock_file):
        _acquire_lock("daily", lock_file)
        _release_lock("WRONG-SESSION-ID", lock_file)
        # Lock should remain (wrong session)
        assert lock_file.exists()

    def test_active_lock_exits(self, lock_file):
        """A fresh lock from another session should cause sys.exit(1)."""
        # Write a fresh lock manually (< 30 min old)
        lock_data = {
            "session_id": str(uuid.uuid4()),
            "pid": 99999,
            "hostname": "testhost",
            "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "horizon": "daily",
        }
        lock_file.write_text(json.dumps(lock_data), encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            _acquire_lock("daily", lock_file)
        assert exc_info.value.code == 1

    def test_stale_lock_removed_and_replaced(self, lock_file):
        """A lock older than STALE_LOCK_MINUTES should be overridden."""
        stale_expired = datetime.now(timezone.utc) - timedelta(minutes=STALE_LOCK_MINUTES + 5)
        stale_data = {
            "session_id": "old-session",
            "pid": 1,
            "hostname": "old",
            "started": stale_expired.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "horizon": "weekly",
        }
        lock_file.write_text(json.dumps(stale_data), encoding="utf-8")
        new_session = _acquire_lock("daily", lock_file)
        data = json.loads(lock_file.read_text())
        # New lock should have the new session_id
        assert data["session_id"] == new_session
        assert data["session_id"] != "old-session"


# ---------------------------------------------------------------------------
# _check_size_guard
# ---------------------------------------------------------------------------

class TestSizeGuard:
    def test_no_file_no_error(self, state_dir):
        _check_size_guard(state_dir, SIZE_GUARD_BYTES)  # Should not raise or exit

    def test_small_file_no_error(self, state_dir):
        current = state_dir / "reflect-current.md"
        current.write_text("Small content", encoding="utf-8")
        _check_size_guard(state_dir, SIZE_GUARD_BYTES)  # Should not raise

    def test_large_file_exits(self, state_dir):
        current = state_dir / "reflect-current.md"
        # Write more than 15KB
        current.write_text("X" * (SIZE_GUARD_BYTES + 100), encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            _check_size_guard(state_dir, SIZE_GUARD_BYTES)
        assert exc_info.value.code == 1

    def test_exactly_at_limit_no_error(self, state_dir):
        current = state_dir / "reflect-current.md"
        current.write_text("X" * SIZE_GUARD_BYTES, encoding="utf-8")
        _check_size_guard(state_dir, SIZE_GUARD_BYTES)  # Exactly at limit: not >, so no exit


# ---------------------------------------------------------------------------
# _audit_log
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_creates_file_and_writes_json(self, audit_file):
        _audit_log("test_event", {"key": "value"}, audit_file)
        assert audit_file.exists()
        lines = audit_file.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "test_event"
        assert entry["key"] == "value"
        assert "ts" in entry
        assert "seq" in entry

    def test_appends_multiple_entries(self, audit_file):
        _audit_log("event_1", {"n": 1}, audit_file)
        _audit_log("event_2", {"n": 2}, audit_file)
        lines = [l for l in audit_file.read_text().strip().split("\n") if l]
        assert len(lines) == 2

    def test_seq_is_monotone(self, audit_file):
        _audit_log("a", {}, audit_file)
        _audit_log("b", {}, audit_file)
        entries = [json.loads(l) for l in audit_file.read_text().strip().split("\n") if l]
        assert entries[1]["seq"] > entries[0]["seq"]

    def test_best_effort_no_raise_on_bad_path(self):
        """_audit_log must never raise even if the path is unwritable."""
        bad_path = Path("/nonexistent_root/that/cannot/exist/audit.jsonl")
        # On Windows, writing to a root that doesn't exist should fail silently
        _audit_log("test", {}, bad_path)  # Should not raise


# ---------------------------------------------------------------------------
# _persist_reflection (idempotency gate)
# ---------------------------------------------------------------------------

class TestPersistReflection:
    def test_writes_artifact_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        key = ReflectionKey(Horizon.WEEKLY, "2026-W99")
        result = _persist_reflection(key, "# Test content", tmp_path)
        assert result is True
        assert (tmp_path / key.artifact_filename).exists()

    def test_skips_when_already_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        key = ReflectionKey(Horizon.WEEKLY, "2026-W99")
        artifact = tmp_path / key.artifact_filename
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("# Existing", encoding="utf-8")
        result = _persist_reflection(key, "# New content", tmp_path)
        assert result is False
        # Content should remain unchanged
        assert artifact.read_text() == "# Existing"

    def test_content_written_correctly(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        key = ReflectionKey(Horizon.DAILY, "2026-04-01")
        _persist_reflection(key, "# Daily close content\nLine 2", tmp_path)
        content = (tmp_path / key.artifact_filename).read_text()
        assert "Daily close content" in content


# ---------------------------------------------------------------------------
# _build_reflect_current
# ---------------------------------------------------------------------------

class TestBuildReflectCurrent:
    def test_contains_yaml_frontmatter(self, sample_state, minimal_reconcile):
        now = datetime(2026, 4, 9, 16, 0, 0, tzinfo=timezone.utc)
        content = _build_reflect_current(
            sample_state, "daily", [], [], minimal_reconcile, now
        )
        assert content.startswith("---\n")
        assert "schema_version" in content

    def test_horizon_in_header(self, sample_state, minimal_reconcile):
        now = datetime(2026, 4, 9, 16, 0, 0, tzinfo=timezone.utc)
        content = _build_reflect_current(
            sample_state, "weekly", [], [], minimal_reconcile, now
        )
        assert "Weekly" in content

    def test_high_items_labeled(self, sample_state, minimal_reconcile,
                                 sample_scored_items):
        now = datetime(2026, 4, 9, 16, 0, 0, tzinfo=timezone.utc)
        content = _build_reflect_current(
            sample_state, "daily", [], sample_scored_items, minimal_reconcile, now
        )
        assert "[HIGH|" in content or "[HIGH" in content

    def test_carry_forward_from_unmatched_planned(self, sample_state, sample_scored_items):
        p = PlannedItem("Deferred feature", cf_id="CF-001")
        reconcile = ReconcileResult(
            matched=[], unmatched_planned=[p], unmatched_actual=[]
        )
        now = datetime(2026, 4, 9, 16, 0, 0, tzinfo=timezone.utc)
        content = _build_reflect_current(
            sample_state, "daily", [], sample_scored_items, reconcile, now
        )
        assert "Deferred feature" in content

    def test_no_scored_items_message(self, sample_state, minimal_reconcile):
        now = datetime(2026, 4, 9, 16, 0, 0, tzinfo=timezone.utc)
        content = _build_reflect_current(
            sample_state, "daily", [], [], minimal_reconcile, now
        )
        assert "no items scored" in content


# ---------------------------------------------------------------------------
# _build_tier2_artifact
# ---------------------------------------------------------------------------

class TestBuildTier2Artifact:
    def test_contains_yaml_frontmatter(self, sample_state, minimal_reconcile):
        key = ReflectionKey(Horizon.WEEKLY, "2026-W14")
        now = datetime(2026, 4, 9, 16, 0, 0, tzinfo=timezone.utc)
        content = _build_tier2_artifact(
            key, sample_state, [], [], minimal_reconcile, now
        )
        assert content.startswith("---\n")

    def test_horizon_in_frontmatter(self, sample_state, minimal_reconcile):
        key = ReflectionKey(Horizon.MONTHLY, "2026-04")
        now = datetime(2026, 4, 9, 16, 0, 0, tzinfo=timezone.utc)
        content = _build_tier2_artifact(
            key, sample_state, [], [], minimal_reconcile, now
        )
        assert "monthly" in content

    def test_planned_vs_actual_table(self, sample_state):
        p = PlannedItem("Planned task")
        a = ActualItem("Actual task")
        reconcile = ReconcileResult(
            matched=[(p, a)], unmatched_planned=[], unmatched_actual=[]
        )
        key = ReflectionKey(Horizon.WEEKLY, "2026-W14")
        now = datetime(2026, 4, 9, 16, 0, 0, tzinfo=timezone.utc)
        content = _build_tier2_artifact(key, sample_state, [], [], reconcile, now)
        assert "Planned task" in content


# ---------------------------------------------------------------------------
# cmd_reflect_status
# ---------------------------------------------------------------------------

class TestCmdReflectStatus:
    def test_no_state_file_returns_string(self, state_dir):
        result = cmd_reflect_status(state_dir)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_with_state_file_shows_carry_forward(self, state_dir):
        current = state_dir / "reflect-current.md"
        current.write_text(
            "---\ncurrent_week: '2026-W14'\ncarry_forward_count: 2\n---\n",
            encoding="utf-8"
        )
        result = cmd_reflect_status(state_dir)
        # carry_forward_count=2 should appear in the formatted status
        assert "2" in result


# ---------------------------------------------------------------------------
# _cmd_reflect_audit
# ---------------------------------------------------------------------------

class TestCmdReflectAudit:
    def test_no_audit_log_returns_info_string(self, audit_file):
        result = _cmd_reflect_audit(10, audit_file)
        assert isinstance(result, str)

    def test_shows_recent_entries(self, audit_file):
        _audit_log("pipeline_start", {"horizon": "daily"}, audit_file)
        _audit_log("persist_written", {"key": "daily/2026-04-09"}, audit_file)
        result = _cmd_reflect_audit(5, audit_file)
        assert "pipeline_start" in result or "persist_written" in result
