"""
tests/ext_agents/test_health_shard.py — EAR health shard tests.

Tests (8):
 1. append() creates shard file
 2. aggregate() returns AgentHealthSummary
 3. consecutive_failures increments correctly
 4. mean_quality_score reflects actual values
 5. multiple agents use separate shards
 6. aggregate() on empty shard returns zero-values
 7. append() is thread-safe (no corruption)
 8. list_agents() returns set of known agents

Ref: specs/ext-agent-reloaded.md §R-6, R-14
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.health_shard import HealthShard


@pytest.fixture()
def shard(tmp_path):
    return HealthShard(shard_dir=tmp_path / "health")


def test_append_creates_file(shard, tmp_path):
    shard.append("agent-x", success=True, quality_score=0.8, latency_ms=50)
    files = list((tmp_path / "health").glob("*.jsonl"))
    assert files, "No shard file created"


def test_aggregate_returns_summary(shard):
    shard.append("agent-y", success=True, quality_score=0.8, latency_ms=100)
    summary = shard.aggregate("agent-y")
    assert summary is not None
    assert hasattr(summary, "mean_quality_score")


def test_consecutive_failures_increments(shard):
    shard.append("fail-agent", success=True, quality_score=0.9, latency_ms=10)
    shard.append("fail-agent", success=False, quality_score=0.0, latency_ms=20)
    shard.append("fail-agent", success=False, quality_score=0.0, latency_ms=20)
    summary = shard.aggregate("fail-agent")
    assert summary.consecutive_failures == 2


def test_mean_quality_score(shard):
    shard.append("quality-agent", success=True, quality_score=0.6, latency_ms=10)
    shard.append("quality-agent", success=True, quality_score=0.8, latency_ms=10)
    summary = shard.aggregate("quality-agent")
    assert abs(summary.mean_quality_score - 0.7) < 0.01


def test_separate_shards_per_agent(shard, tmp_path):
    shard.append("agent-a", success=True, quality_score=0.9, latency_ms=10)
    shard.append("agent-b", success=True, quality_score=0.5, latency_ms=10)
    summary_a = shard.aggregate("agent-a")
    summary_b = shard.aggregate("agent-b")
    assert abs(summary_a.mean_quality_score - 0.9) < 0.01
    assert abs(summary_b.mean_quality_score - 0.5) < 0.01


def test_aggregate_empty_shard(shard):
    summary = shard.aggregate("nonexistent-agent")
    assert summary is not None
    assert summary.mean_quality_score == 0.0
    assert summary.consecutive_failures == 0


def test_append_thread_safe(shard):
    errors = []

    def writer():
        try:
            shard.append("threaded-agent", success=True, quality_score=0.7, latency_ms=30)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    summary = shard.aggregate("threaded-agent")
    assert summary.total_invocations == 20


def test_list_agents_returns_all(shard):
    shard.append("agentA", success=True, quality_score=0.9, latency_ms=5)
    shard.append("agentB", success=True, quality_score=0.8, latency_ms=5)
    agents = shard.list_agents()
    assert "agentA" in agents
    assert "agentB" in agents
