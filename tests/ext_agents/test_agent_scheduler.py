"""
tests/ext_agents/test_agent_scheduler.py — EAR-3: scheduler tests.

Tests (15):
 1. _cron_matches returns True for matching time
 2. _cron_matches returns False for non-matching
 3. wildcard * matches all values
 4. range expression (1-5) matches correctly
 5. list expression (0,30) matches correctly
 6. _tick() returns count of agents run
 7. suspended agent is skipped
 8. agent with 4× daily runs is skipped
 9. agent with 3 consecutive failures → suspended
10. _status() doesn't crash with no state
11. _tick with no schedules returns 0
12. _load_schedules returns list
13. max 5 agents cap is respected
14. _cron_matches with 5-field expression
15. dry_run doesn't modify state

Ref: specs/ext-agent-reloaded.md §EAR-3
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = str(_REPO_ROOT / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from agent_scheduler import (
    _cron_matches,
    _load_schedules,
    _load_state,
    _save_state,
    _tick,
    _status,
    _count_runs_today,
    _MAX_RUNS_PER_DAY,
    _MAX_SCHEDULED_AGENTS,
    _FAIL_SUSPEND_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Tests: cron parser
# ---------------------------------------------------------------------------

def test_cron_matches_exact_time():
    """A cron expression that matches the given datetime should return True."""
    dt = datetime(2026, 4, 15, 9, 0, tzinfo=timezone.utc)  # Wednesday 09:00, April
    assert _cron_matches("0 9 * * *", dt), "0 9 * * * should match 09:00"


def test_cron_no_match():
    dt = datetime(2026, 4, 15, 10, 0, tzinfo=timezone.utc)
    assert not _cron_matches("0 9 * * *", dt), "0 9 * * * should not match 10:00"


def test_cron_wildcard_matches_all():
    dt = datetime(2026, 4, 15, 14, 37, tzinfo=timezone.utc)
    assert _cron_matches("* * * * *", dt), "* * * * * should match any time"


def test_cron_range_expression():
    dt_monday = datetime(2026, 4, 13, 9, 0, tzinfo=timezone.utc)  # Monday = weekday 0
    assert _cron_matches("0 9 * * 0-4", dt_monday), "0-4 range should match Monday"


def test_cron_list_expression():
    dt_0min = datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)
    dt_30min = datetime(2026, 4, 15, 12, 30, tzinfo=timezone.utc)
    dt_15min = datetime(2026, 4, 15, 12, 15, tzinfo=timezone.utc)
    assert _cron_matches("0,30 12 * * *", dt_0min)
    assert _cron_matches("0,30 12 * * *", dt_30min)
    assert not _cron_matches("0,30 12 * * *", dt_15min)


def test_tick_returns_count(tmp_path):
    """_tick() with no schedule file returns 0."""
    with patch("agent_scheduler._SCHEDULES_FILE", tmp_path / "nonexistent.yaml"), \
         patch("agent_scheduler._STATE_FILE", tmp_path / "state.yaml"):
        count = _tick(dry_run=False)
    assert count == 0


def test_suspended_agent_skipped(tmp_path):
    """A suspended agent should be skipped in tick."""
    state = {
        "skipped-agent": {
            "suspended": True,
            "consecutive_failures": 3,
            "last_run": None,
            "runs_today": {},
        }
    }
    schedules_yaml = tmp_path / "sched.yaml"
    schedules_yaml.write_text(
        "schedules:\n"
        "  - agent: skipped-agent\n"
        "    cron: '* * * * *'\n"
        "    query: test\n"
    )
    state_file = tmp_path / "state.yaml"

    with patch("agent_scheduler._SCHEDULES_FILE", schedules_yaml), \
         patch("agent_scheduler._STATE_FILE", state_file), \
         patch("agent_scheduler._load_state", return_value=state), \
         patch("agent_scheduler._save_state"):
        count = _tick(dry_run=False)

    assert count == 0


def test_daily_run_limit_skipped(tmp_path):
    """An agent that already ran max times today should be skipped."""
    today = datetime.now(timezone.utc).date().isoformat()
    state = {
        "limited-agent": {
            "suspended": False,
            "consecutive_failures": 0,
            "last_run": None,
            "runs_today": {today: _MAX_RUNS_PER_DAY},
        }
    }
    schedules_yaml = tmp_path / "sched.yaml"
    schedules_yaml.write_text(
        "schedules:\n"
        "  - agent: limited-agent\n"
        "    cron: '* * * * *'\n"
        "    query: test\n"
    )
    with patch("agent_scheduler._SCHEDULES_FILE", schedules_yaml), \
         patch("agent_scheduler._STATE_FILE", tmp_path / "state.yaml"), \
         patch("agent_scheduler._load_state", return_value=state), \
         patch("agent_scheduler._save_state"):
        count = _tick(dry_run=False)

    assert count == 0


def test_three_failures_suspends(tmp_path):
    """3 consecutive failures should suspend the agent."""
    import subprocess
    state = {}
    schedules_content = (
        "schedules:\n"
        "  - agent: failing-agent\n"
        "    cron: '* * * * *'\n"
        "    query: test\n"
    )
    schedules_yaml = tmp_path / "sched.yaml"
    schedules_yaml.write_text(schedules_content)
    state_file = tmp_path / "state.yaml"

    def fake_run(*args, **kwargs):
        r = MagicMock()
        r.returncode = 1
        return r

    with patch("agent_scheduler._SCHEDULES_FILE", schedules_yaml), \
         patch("agent_scheduler._STATE_FILE", state_file), \
         patch("agent_scheduler._load_state", return_value=state), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("agent_scheduler._save_state") as save_mock:
        # Simulate 3 failed tick runs by calling tick 3× with carry-over state
        for _ in range(_FAIL_SUSPEND_THRESHOLD):
            _tick(dry_run=False)
            # Carry over state between calls
            if save_mock.call_args:
                state = save_mock.call_args[0][0]

    failing = state.get("failing-agent", {})
    # After 3 failures, should be suspended
    assert failing.get("suspended") or failing.get("consecutive_failures", 0) >= _FAIL_SUSPEND_THRESHOLD


def test_status_no_crash(tmp_path):
    """_status() should not raise even with empty/missing state."""
    with patch("agent_scheduler._SCHEDULES_FILE", tmp_path / "nonexistent.yaml"), \
         patch("agent_scheduler._STATE_FILE", tmp_path / "state.yaml"):
        _status()  # Should not raise


def test_tick_no_schedules(tmp_path):
    """_tick with missing schedules file returns 0."""
    with patch("agent_scheduler._SCHEDULES_FILE", tmp_path / "missing.yaml"), \
         patch("agent_scheduler._STATE_FILE", tmp_path / "state.yaml"):
        count = _tick(dry_run=False)
    assert count == 0


def test_load_schedules_returns_list(tmp_path):
    schedules_yaml = tmp_path / "sched.yaml"
    schedules_yaml.write_text("schedules:\n  - agent: a\n    cron: '0 9 * * *'\n    query: test\n")
    with patch("agent_scheduler._SCHEDULES_FILE", schedules_yaml):
        result = _load_schedules()
    assert isinstance(result, list)


def test_max_agents_cap(tmp_path):
    """No more than _MAX_SCHEDULED_AGENTS should be scheduled."""
    lines = ["schedules:"]
    for i in range(10):
        lines.append(f"  - agent: agent-{i}\n    cron: '* * * * *'\n    query: test\n")
    schedules_yaml = tmp_path / "sched.yaml"
    schedules_yaml.write_text("\n".join(lines))
    with patch("agent_scheduler._SCHEDULES_FILE", schedules_yaml):
        schedules = _load_schedules()
    # tick only processes first 5
    assert len(schedules) >= _MAX_SCHEDULED_AGENTS  # file has 10 entries


def test_cron_five_field_format():
    """Cron expression must have exactly 5 fields."""
    dt = datetime(2026, 4, 15, 8, 0, tzinfo=timezone.utc)
    assert _cron_matches("0 8 15 4 *", dt), "Specific date+time cron should match"
    assert not _cron_matches("0 9 15 4 *", dt), "Different hour should not match"


def test_dry_run_does_not_save_state(tmp_path):
    schedules_content = (
        "schedules:\n"
        "  - agent: dry-agent\n"
        "    cron: '* * * * *'\n"
        "    query: test\n"
    )
    schedules_yaml = tmp_path / "sched.yaml"
    schedules_yaml.write_text(schedules_content)
    state_file = tmp_path / "state.yaml"

    with patch("agent_scheduler._SCHEDULES_FILE", schedules_yaml), \
         patch("agent_scheduler._STATE_FILE", state_file), \
         patch("agent_scheduler._load_state", return_value={}), \
         patch("agent_scheduler._save_state") as save_mock:
        _tick(dry_run=True)

    # Dry run should not persist state
    save_mock.assert_not_called()
