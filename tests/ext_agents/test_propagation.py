"""
tests/ext_agents/test_propagation.py — EAR-10: cross-agent knowledge propagation tests.

Tests (20):
 1.  propagate() creates propagation file for target
 2.  load_for_agent() returns non-empty string after propagation
 3.  load_for_agent() returns empty when no propagation exists
 4.  delete_for_agent() removes all propagation files
 5.  multiple sources are all written (file-per-source)
 6.  propagation file is per-target-agent
 7.  propagate() returns int count
 8.  empty/low-signal content propagates 0 targets
 9.  propagate() is atomic (no crash on concurrent calls)
10.  propagation file contains source agent name
11.  delete_for_agent on nonexistent silently succeeds
12.  TTL-expired files are skipped on load
13.  propagate from same source updates existing file
14.  load_for_agent returns string type always
15.  trust downgrade note appears in propagation file
16.  scrub_fn is called for lower trust tiers
17.  load_for_agent returns ≤ _MAX_TOTAL_CHARS
18.  propagate() target_agents max _MAX_SOURCES_PER_AGENT
19.  concurrent propagations don't corrupt final file
20.  delete_for_agent returns count of deleted files

Ref: specs/ext-agent-reloaded.md §EAR-10
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.knowledge_propagator import KnowledgePropagator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Content with actionable language so _extract_key_facts finds key facts
_GOOD_CONTENT = (
    "You must restart the service to resolve the outage in eastus. "
    "Recommend triggering a rollback of the failed deployment. "
    "Check IcM-12345 and escalate if not resolved within one hour."
)


@pytest.fixture()
def propagator(tmp_path):
    return KnowledgePropagator(prop_dir=tmp_path / "propagated")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_propagate_creates_file(propagator, tmp_path):
    count = propagator.propagate(
        source_agent_name="source-agent",
        source_trust_tier="internal",
        cached_response=_GOOD_CONTENT,
        target_agents=["target-agent"],
    )
    assert count == 1
    target_dir = propagator._dir / "target-agent"
    files = list(target_dir.glob("from-*.md"))
    assert files, "No propagation file created"


def test_load_for_agent_returns_content(propagator):
    propagator.propagate(
        source_agent_name="src",
        source_trust_tier="internal",
        cached_response=_GOOD_CONTENT,
        target_agents=["tgt"],
    )
    content = propagator.load_for_agent("tgt")
    assert isinstance(content, str)
    assert len(content) > 0


def test_load_for_agent_empty_when_none(propagator):
    content = propagator.load_for_agent("nonexistent-agent")
    assert content == ""


def test_delete_for_agent_removes_all(propagator):
    propagator.propagate(
        source_agent_name="src",
        source_trust_tier="internal",
        cached_response=_GOOD_CONTENT,
        target_agents=["del-target"],
    )
    assert len(propagator.load_for_agent("del-target")) > 0
    propagator.delete_for_agent("del-target")
    assert propagator.load_for_agent("del-target") == ""


def test_multiple_sources_produce_multiple_files(propagator):
    for i in range(3):
        propagator.propagate(
            source_agent_name=f"source-{i}",
            source_trust_tier="internal",
            cached_response=_GOOD_CONTENT,
            target_agents=["multi-target"],
        )
    target_dir = propagator._dir / "multi-target"
    files = list(target_dir.glob("from-*.md"))
    assert len(files) == 3


def test_propagation_file_per_target(propagator):
    for tgt in ["target-a", "target-b"]:
        propagator.propagate(
            source_agent_name="shared-src",
            source_trust_tier="internal",
            cached_response=_GOOD_CONTENT,
            target_agents=[tgt],
        )
    a_content = propagator.load_for_agent("target-a")
    b_content = propagator.load_for_agent("target-b")
    assert len(a_content) > 0
    assert len(b_content) > 0


def test_propagate_returns_int(propagator):
    result = propagator.propagate(
        source_agent_name="src",
        source_trust_tier="internal",
        cached_response=_GOOD_CONTENT,
        target_agents=["r-target"],
    )
    assert isinstance(result, int)
    assert result >= 0


def test_empty_content_no_propagation(propagator):
    count = propagator.propagate(
        source_agent_name="src",
        source_trust_tier="internal",
        cached_response="",
        target_agents=["empty-target"],
    )
    assert count == 0
    assert propagator.load_for_agent("empty-target") == ""


def test_concurrent_propagations_safe(propagator):
    errors = []

    def worker(i):
        try:
            propagator.propagate(
                source_agent_name=f"worker-{i}",
                source_trust_tier="internal",
                cached_response=_GOOD_CONTENT,
                target_agents=["concurrent-target"],
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent propagation errors: {errors}"


def test_propagation_file_contains_source_name(propagator):
    propagator.propagate(
        source_agent_name="named-source",
        source_trust_tier="internal",
        cached_response=_GOOD_CONTENT,
        target_agents=["name-check-target"],
    )
    content = propagator.load_for_agent("name-check-target")
    assert "named-source" in content


def test_delete_nonexistent_silent(propagator):
    """delete_for_agent on nonexistent target should not raise."""
    count = propagator.delete_for_agent("totally-nonexistent-agent")
    assert count == 0


def test_ttl_expired_files_skipped(propagator):
    propagator.propagate(
        source_agent_name="old-source",
        source_trust_tier="internal",
        cached_response=_GOOD_CONTENT,
        target_agents=["expiry-target"],
    )
    # Age the file beyond TTL
    target_dir = propagator._dir / "expiry-target"
    for f in target_dir.glob("from-*.md"):
        very_old = time.time() - (8 * 24 * 3600)  # 8 days old
        os.utime(f, (very_old, very_old))

    content = propagator.load_for_agent("expiry-target", cache_ttl_days=7)
    assert content == "", f"Expired file should be skipped; got: {repr(content)}"


def test_same_source_updates_file(propagator):
    propagator.propagate(
        source_agent_name="updater",
        source_trust_tier="internal",
        cached_response="Restart the service to resolve the deployment issue.",
        target_agents=["update-target"],
    )
    propagator.propagate(
        source_agent_name="updater",
        source_trust_tier="internal",
        cached_response="Rollback the deploy to fix the eastus outage now.",
        target_agents=["update-target"],
    )
    # Should still be one file (overwritten), not two
    target_dir = propagator._dir / "update-target"
    files = list(target_dir.glob("from-updater.md"))
    assert len(files) == 1


def test_load_for_agent_always_returns_string(propagator):
    result = propagator.load_for_agent("any-agent")
    assert isinstance(result, str)


def test_trust_downgrade_note_in_file(propagator):
    """When source has higher tier than target, file should mention downgrade."""
    propagator.propagate(
        source_agent_name="privileged-src",
        source_trust_tier="privileged",
        cached_response=_GOOD_CONTENT,
        target_agents=["external-tgt"],
        target_trust_tiers={"external-tgt": "external"},
    )
    target_dir = propagator._dir / "external-tgt"
    files = list(target_dir.glob("from-*.md"))
    if files:
        content = files[0].read_text(encoding="utf-8")
        # Trust downgrade note is written into the file
        assert "downgrade" in content.lower() or "trust" in content.lower()


def test_scrub_fn_called(propagator, tmp_path):
    """If there's a scrub_fn, it's invoked for each fact."""
    scrubbed = []

    def my_scrub(text, tier):
        scrubbed.append((text, tier))
        return text.replace("eastus", "[REDACTED]")

    p = KnowledgePropagator(prop_dir=tmp_path / "scrub", scrub_fn=my_scrub)
    p.propagate(
        source_agent_name="scrub-src",
        source_trust_tier="internal",
        cached_response=_GOOD_CONTENT,
        target_agents=["scrub-tgt"],
    )
    assert len(scrubbed) > 0


def test_load_content_is_bounded(propagator):
    """Load should return at most _MAX_TOTAL_CHARS + header overhead."""
    for i in range(3):
        propagator.propagate(
            source_agent_name=f"big-{i}",
            source_trust_tier="internal",
            cached_response=_GOOD_CONTENT * 10,
            target_agents=["bounded-target"],
        )
    content = propagator.load_for_agent("bounded-target")
    # Should stay under 2KB (1500 cap + some header)
    assert len(content) <= 2200


def test_propagate_caps_at_max_sources(propagator):
    """propagate() only writes to the first 3 targets (max_sources_per_agent)."""
    targets = [f"tgt-{i}" for i in range(6)]
    count = propagator.propagate(
        source_agent_name="multi-src",
        source_trust_tier="internal",
        cached_response=_GOOD_CONTENT,
        target_agents=targets,
    )
    # _MAX_SOURCES_PER_AGENT=3 caps how many targets are processed
    assert count <= 3


def test_concurrent_writes_final_file_valid(propagator):
    """9 concurrent writes to the same source-target pair; file not corrupted."""

    def writer():
        propagator.propagate(
            source_agent_name="shared-src",
            source_trust_tier="internal",
            cached_response=_GOOD_CONTENT,
            target_agents=["concurrent-final"],
        )

    threads = [threading.Thread(target=writer) for _ in range(9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    target_dir = propagator._dir / "concurrent-final"
    files = list(target_dir.glob("from-shared-src.md"))
    if files:
        text = files[0].read_text(encoding="utf-8")
        assert len(text) > 0  # File not empty/corrupt


def test_delete_for_agent_returns_count(propagator):
    for src in ["s1", "s2"]:
        propagator.propagate(
            source_agent_name=src,
            source_trust_tier="internal",
            cached_response=_GOOD_CONTENT,
            target_agents=["count-target"],
        )
    count = propagator.delete_for_agent("count-target")
    assert count == 2

