"""tests/ext_agents/test_safety_invariants.py — Critical safety invariant tests.

Covers the five safety invariants that protect against regressions:

  S-1  Template injection: user query with braces must not crash prompt composer
  S-2  Atomic writes: cache writes must not corrupt data on interruption
  S-3  PII boundary: scrubber must fail-safe when pii_guard crashes
  S-4  PII boundary: scrubber must block in strict mode when guard missing
  S-5  Quality score clamping: out-of-range scores must be clamped to [0, 1]
  S-6  Query truncation: very long queries must be capped
  S-7  Registry resilience: malformed entries must not crash registry load
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from lib.prompt_composer import PromptComposer  # type: ignore
from lib.context_scrubber import ContextScrubber  # type: ignore
from lib.knowledge_extractor import KnowledgeExtractor  # type: ignore
from lib.agent_health import AgentHealthTracker  # type: ignore
from lib.agent_registry import AgentRegistry  # type: ignore
from .conftest import SAMPLE_AGENT_ENTRY, make_test_agent


# ---------------------------------------------------------------------------
# S-1: Prompt composer — brace-containing queries must not crash
# ---------------------------------------------------------------------------

class TestPromptComposerBraceSafety:
    """Queries containing Python format-string metacharacters."""

    @pytest.fixture()
    def composer(self):
        return PromptComposer(make_test_agent())

    def test_query_with_single_brace(self, composer):
        result = composer.compose("How do I use {0} in Python?", [])
        assert "{0}" in result.prompt

    def test_query_with_named_brace(self, composer):
        result = composer.compose("Explain {user_question} interpolation", [])
        assert "{user_question}" in result.prompt

    def test_query_with_nested_braces(self, composer):
        result = composer.compose("Fix {{double}} and {single}", [])
        assert "{{double}}" in result.prompt
        assert "{single}" in result.prompt

    def test_query_with_lone_closing_brace(self, composer):
        result = composer.compose("JSON like } this", [])
        assert "}" in result.prompt

    def test_query_with_format_spec(self, composer):
        result = composer.compose("Use {:.2f} format", [])
        assert "{:.2f}" in result.prompt


# ---------------------------------------------------------------------------
# S-2: Knowledge extractor — atomic writes
# ---------------------------------------------------------------------------

class TestKnowledgeExtractorAtomicWrites:
    """Cache writes via temp+rename — original file preserved on failure."""

    @pytest.fixture()
    def extractor(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        return KnowledgeExtractor(
            cache_dir=cache_dir,
            agent_name="test-agent",
            min_quality=0.5,
        )

    def test_cache_file_created(self, extractor, tmp_path):
        ok = extractor.extract_and_cache(
            response="The root cause is X. The fix is Y.",
            query="Why is deployment stuck?",
            quality_score=0.9,
        )
        assert ok
        cache_file = tmp_path / "cache" / "test-agent.md"
        assert cache_file.exists()
        content = cache_file.read_text(encoding="utf-8")
        assert "root cause" in content

    def test_existing_cache_preserved_on_write_error(self, extractor, tmp_path):
        """If atomic write fails, old content must survive."""
        cache_file = tmp_path / "cache" / "test-agent.md"
        cache_file.write_text("original content", encoding="utf-8")

        with patch("lib.knowledge_extractor.os.replace", side_effect=OSError("disk full")):
            ok = extractor.extract_and_cache(
                response="New content that should not clobber old.",
                query="test query",
                quality_score=0.9,
            )

        assert not ok
        assert cache_file.read_text(encoding="utf-8") == "original content"

    def test_no_temp_files_left_on_failure(self, extractor, tmp_path):
        """Temp files must be cleaned up after failed write."""
        cache_dir = tmp_path / "cache"
        before = set(cache_dir.iterdir())

        with patch("lib.knowledge_extractor.os.replace", side_effect=OSError("nope")):
            extractor.extract_and_cache(
                response="Test data for temp file cleanup verification.",
                query="cleanup test",
                quality_score=0.9,
            )

        after = set(cache_dir.iterdir())
        new_files = after - before
        # No .tmp files should remain
        assert not any(f.suffix == ".tmp" for f in new_files)


# ---------------------------------------------------------------------------
# S-3: Context scrubber — pii_guard exception must not leak PII
# ---------------------------------------------------------------------------

class TestContextScrubberPiiGuardSafety:
    """PII guard failures must trigger fail-safe, not pass-through."""

    def test_strict_mode_blocks_on_guard_exception(self):
        scrubber = ContextScrubber(strict_mode=True)
        mock_guard = MagicMock()
        mock_guard.filter_text.side_effect = RuntimeError("guard crashed")

        with patch("lib.context_scrubber._get_pii_guard", return_value=mock_guard):
            result = scrubber.scrub("My SSN is 123-45-6789")

        assert result.blocked is True
        assert result.scrubbed_text == ""

    def test_nonstrict_mode_passes_on_guard_exception(self):
        scrubber = ContextScrubber(strict_mode=False)
        mock_guard = MagicMock()
        mock_guard.filter_text.side_effect = RuntimeError("guard crashed")

        with patch("lib.context_scrubber._get_pii_guard", return_value=mock_guard):
            result = scrubber.scrub("Some text without PII")

        # Non-strict: pass through (degraded), but NOT blocked
        assert result.blocked is False
        assert result.scrubbed_text == "Some text without PII"


# ---------------------------------------------------------------------------
# S-4: Context scrubber — missing pii_guard in strict mode blocks fragments
# ---------------------------------------------------------------------------

class TestContextScrubberMissingGuard:
    """When pii_guard.py is missing, strict mode must block, not pass through."""

    def test_strict_mode_blocks_when_guard_unavailable(self):
        scrubber = ContextScrubber(strict_mode=True)

        with patch("lib.context_scrubber._get_pii_guard", return_value=None):
            result = scrubber.scrub("Potentially sensitive content")

        assert result.blocked is True
        assert result.scrubbed_text == ""

    def test_nonstrict_mode_passes_when_guard_unavailable(self):
        scrubber = ContextScrubber(strict_mode=False)

        with patch("lib.context_scrubber._get_pii_guard", return_value=None):
            result = scrubber.scrub("Some content")

        assert result.blocked is False
        assert result.scrubbed_text == "Some content"


# ---------------------------------------------------------------------------
# S-5: Quality score clamping
# ---------------------------------------------------------------------------

class TestQualityScoreClamping:
    """Out-of-range quality scores must be clamped to [0.0, 1.0]."""

    @pytest.fixture()
    def tracker(self, tmp_registry_dir):
        reg = AgentRegistry.load(tmp_registry_dir)
        agent = make_test_agent()
        reg.register(agent)
        return AgentHealthTracker(registry=reg)

    def test_score_above_one_clamped(self, tracker):
        tracker.record_invocation("test-agent", success=True, latency_ms=100, quality_score=1.5)
        agent = tracker._registry.get("test-agent")
        assert agent.health.mean_quality_score <= 1.0

    def test_score_below_zero_clamped(self, tracker):
        tracker.record_invocation("test-agent", success=True, latency_ms=100, quality_score=-0.3)
        agent = tracker._registry.get("test-agent")
        assert agent.health.mean_quality_score >= 0.0


# ---------------------------------------------------------------------------
# S-6: Query truncation
# ---------------------------------------------------------------------------

class TestQueryTruncation:
    """Very long queries must be capped in the delegation prompt."""

    @pytest.fixture()
    def composer(self):
        return PromptComposer(make_test_agent())

    def test_long_query_truncated(self, composer):
        long_query = "x" * 20_000
        result = composer.compose(long_query, [])
        # Prompt should not contain the full 20K chars
        assert len(result.prompt) < 15_000
        assert "truncated" in result.prompt.lower()


# ---------------------------------------------------------------------------
# S-7: Registry resilience — malformed entries
# ---------------------------------------------------------------------------

class TestRegistryResilience:
    """Malformed registry entries must be skipped, not crash the load."""

    def test_malformed_entry_skipped(self, tmp_path):
        import yaml

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        reg_data = {
            "schema_version": "1.0",
            "agents": {
                "good-agent": {
                    "label": "Good",
                    "description": "Works",
                    "trust_tier": "external",
                    "routing": {"keywords": ["test"], "min_keyword_hits": 1},
                },
                "bad-agent": "this is a string, not a dict",
                "broken-agent": {"routing": {"min_confidence": "not-a-number"}},
            },
        }
        (agents_dir / "external-registry.yaml").write_text(
            yaml.dump(reg_data), encoding="utf-8"
        )

        reg = AgentRegistry.load(tmp_path)
        # Good agent loaded, broken ones skipped
        assert reg.get("good-agent") is not None
        assert reg.get("bad-agent") is None
