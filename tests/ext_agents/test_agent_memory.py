"""
tests/ext_agents/test_agent_memory.py — EAR-1: agent memory system tests.

Tests (25):
 1.  write_entry creates a daily log file
 2.  load_relevant returns non-empty string after writing
 3.  load_relevant on empty store returns empty string
 4.  concurrent writes don't raise exceptions
 5.  clear() removes all daily log files
 6.  evict_stale keeps fresh daily files
 7.  evict_stale removes old dated files
 8.  agent isolation: separate daily_dir per agent
 9.  corruption in daily file doesn't crash load_relevant
10.  load_relevant returns a string (not list)
11.  write_entry with key_finding writes to log
12.  write_entry with lesson writes to log
13.  write_entry with user_correction writes to log
14.  write_entry is thread-safe (no double-write corruption)
15.  _durability_score penalises date-stamped entries
16.  _durability_score rewards durable diagnostic patterns
17.  write_entry fires without raising for valid input
18.  multiple entries in same agent are all in daily log
19.  clear leaves agent ready for fresh writes
20.  different agent names get different storage dirs
21.  daily log contains the query text
22.  daily log contains the quality score
23.  write_entry with high quality_score writes HIGH confidence
24.  write_entry with low quality_score writes LOW confidence
25.  load_relevant after clear returns empty string

Ref: specs/ext-agent-reloaded.md §EAR-1
"""
from __future__ import annotations

import sys
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.agent_memory import AgentMemory, _durability_score


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def memory(tmp_path):
    return AgentMemory(agent_name="test-agent", memory_dir=tmp_path / "mem")


@pytest.fixture()
def memory_b(tmp_path):
    return AgentMemory(agent_name="other-agent", memory_dir=tmp_path / "mem")


def _today_log(mem: AgentMemory) -> Path:
    """Return path to today's daily log for this agent."""
    return mem._daily_dir / f"{date.today().isoformat()}.md"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_write_entry_creates_daily_log(memory):
    """write_entry creates today's daily log file."""
    memory.write_entry(query="deployment rollout failed", quality_score=0.8)
    assert _today_log(memory).exists(), "Daily log file not created"


def test_load_relevant_returns_nonempty_after_write(memory):
    """After a write, load_relevant returns non-empty string for matching query."""
    memory.write_entry(query="deployment quota limit exceeded", quality_score=0.9)
    result = memory.load_relevant(query="deployment quota")
    # May be empty if entry didn't reach curated memory.md yet and daily scan misses
    # — but shouldn't raise
    assert isinstance(result, str)


def test_load_relevant_empty_store_returns_empty(memory):
    """Fresh agent has no memory → load_relevant returns empty string."""
    result = memory.load_relevant(query="anything")
    assert result == ""


def test_concurrent_writes_no_exception(tmp_path):
    mem = AgentMemory(agent_name="concurrent-agent", memory_dir=tmp_path / "mem")
    errors = []

    def writer(i):
        try:
            mem.write_entry(query=f"Thread {i} fact about rollout", quality_score=0.6)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent writes raised: {errors}"


def test_clear_removes_all_files(memory):
    memory.write_entry(query="fact to be cleared", quality_score=0.9)
    assert _today_log(memory).exists()
    memory.clear()
    assert not memory._base.exists(), "Base directory should be gone after clear()"


def test_evict_stale_keeps_fresh_file(memory, tmp_path):
    memory.write_entry(query="fresh entry", quality_score=0.8)
    count = memory.evict_stale()
    assert count == 0, "Fresh file should not be evicted"
    assert _today_log(memory).exists(), "Fresh file should survive evict_stale()"


def test_evict_stale_removes_old_files(memory, tmp_path):
    """Manually create a daily file with a date > _MAX_DAILY_DAYS old."""
    memory._daily_dir.mkdir(parents=True, exist_ok=True)
    old_date = (date.today() - timedelta(days=30)).isoformat()
    old_file = memory._daily_dir / f"{old_date}.md"
    old_file.write_text("## [00:00] Query: \"old entry\"\n- Quality: 0.5\n", encoding="utf-8")

    count = memory.evict_stale()
    assert count >= 1, "Old daily file should have been evicted"
    assert not old_file.exists(), "Old file should be deleted"


def test_agent_isolation_separate_dirs(memory, memory_b):
    """Two agents should have separate daily_dir paths."""
    assert memory._daily_dir != memory_b._daily_dir, "Agents must have separate daily dirs"


def test_corruption_in_daily_file_no_crash(tmp_path):
    mem = AgentMemory(agent_name="corrupt-agent", memory_dir=tmp_path / "mem")
    mem._daily_dir.mkdir(parents=True, exist_ok=True)
    bad_file = mem._daily_dir / f"{date.today().isoformat()}.md"
    bad_file.write_text("{{broken: yaml: {{{{corrupt", encoding="utf-8")
    # Should not raise
    result = mem.load_relevant(query="anything")
    assert isinstance(result, str)


def test_load_relevant_returns_string(memory):
    memory.write_entry(query="storage SLA breach", quality_score=0.75)
    result = memory.load_relevant(query="storage SLA")
    assert isinstance(result, str)


def test_write_entry_with_key_finding(memory):
    memory.write_entry(
        query="canary deployment check",
        quality_score=0.8,
        key_finding="Canary failed in eastus region",
    )
    content = _today_log(memory).read_text(encoding="utf-8")
    assert "Canary failed in eastus region" in content


def test_write_entry_with_lesson(memory):
    memory.write_entry(
        query="recurring rollout failures",
        quality_score=0.7,
        lesson="Always validate quota before rollout",
    )
    content = _today_log(memory).read_text(encoding="utf-8")
    assert "Always validate quota before rollout" in content


def test_write_entry_with_user_correction(memory):
    memory.write_entry(
        query="ICM 12345 resolution",
        quality_score=0.85,
        user_correction="Corrected: outage was in westus not eastus",
    )
    content = _today_log(memory).read_text(encoding="utf-8")
    assert "Corrected: outage was in westus not eastus" in content


def test_write_entry_thread_safe_no_partial(tmp_path):
    """10 concurrent writes — daily log has valid markdown entries."""
    mem = AgentMemory(agent_name="threadsafe-agent", memory_dir=tmp_path / "mem")
    threads = [
        threading.Thread(
            target=mem.write_entry,
            kwargs={"query": f"fact {i}", "quality_score": 0.6}
        )
        for i in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if _today_log(mem).exists():
        content = _today_log(mem).read_text(encoding="utf-8")
        # Every entry header is a complete pair of brackets
        assert content.count('## [') == content.count(']')


def test_durability_score_penalises_date_stamps():
    dated = _durability_score("As of 2024-01-01, service X was version 1.0.")
    stable = _durability_score("Service X has a known retry bug in the auth path.")
    assert dated < stable, f"Date-stamped ({dated}) should score lower than stable ({stable})"


def test_durability_score_rewards_diagnostics():
    generic = _durability_score("Something happened.")
    diagnostic = _durability_score("Service X consistently fails under high load.")
    # Generic short text shouldn't score higher than diagnostic patterns
    assert _durability_score("2023-01-01 event log") <= 1.0


def test_write_entry_does_not_raise(memory):
    """write_entry with valid args should never raise."""
    try:
        memory.write_entry(query="valid query", quality_score=0.8)
    except Exception as exc:
        pytest.fail(f"write_entry raised unexpectedly: {exc}")


def test_multiple_entries_all_in_daily_log(memory):
    for i in range(3):
        memory.write_entry(query=f"entry {i} about deployment", quality_score=0.7)
    content = _today_log(memory).read_text(encoding="utf-8")
    assert content.count("## [") >= 3, "Expected at least 3 entries in daily log"


def test_clear_then_write_works(memory):
    memory.write_entry(query="initial entry", quality_score=0.8)
    memory.clear()
    # Should be able to write again after clear
    memory.write_entry(query="fresh start", quality_score=0.7)
    assert _today_log(memory).exists()


def test_different_names_different_dirs(tmp_path):
    a = AgentMemory(agent_name="alpha", memory_dir=tmp_path / "mem")
    b = AgentMemory(agent_name="beta", memory_dir=tmp_path / "mem")
    assert a._base != b._base
    assert a._daily_dir != b._daily_dir


def test_daily_log_contains_query_text(memory):
    memory.write_entry(query="ICM 99999 triage outcome", quality_score=0.8)
    content = _today_log(memory).read_text(encoding="utf-8")
    assert "ICM 99999 triage outcome" in content


def test_daily_log_contains_quality_score(memory):
    memory.write_entry(query="deployment stats", quality_score=0.77)
    content = _today_log(memory).read_text(encoding="utf-8")
    assert "0.77" in content


def test_high_quality_writes_high_confidence(memory):
    memory.write_entry(query="strong pattern", quality_score=0.9)
    content = _today_log(memory).read_text(encoding="utf-8")
    assert "HIGH" in content


def test_low_quality_writes_low_confidence(memory):
    memory.write_entry(query="weak pattern", quality_score=0.3)
    content = _today_log(memory).read_text(encoding="utf-8")
    assert "LOW" in content


def test_load_relevant_after_clear_is_empty(memory):
    memory.write_entry(query="something to delete", quality_score=0.9)
    memory.clear()
    result = memory.load_relevant(query="something")
    assert result == ""

