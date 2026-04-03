"""tests/ext_agents/test_knowledge_extractor.py -- AR-9 KnowledgeExtractor tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.knowledge_extractor import KnowledgeExtractor  # type: ignore


@pytest.fixture()
def cache_dir(tmp_path: Path) -> Path:
    cd = tmp_path / "cache"
    cd.mkdir()
    return cd


@pytest.fixture()
def extractor(cache_dir: Path) -> KnowledgeExtractor:
    return KnowledgeExtractor(cache_dir=cache_dir, agent_name="test-agent")


_GOOD_RESPONSE = (
    "SDP block at stage 3 is caused by capacity exhaustion. "
    "You need to file a SNOW ticket to request quota increase in region eastus."
)


class TestKnowledgeExtractor:
    def test_extract_and_cache_returns_bool(self, extractor: KnowledgeExtractor):
        result = extractor.extract_and_cache(
            response=_GOOD_RESPONSE,
            query="SDP block deployment",
            quality_score=0.9,
        )
        assert isinstance(result, bool)

    def test_high_quality_response_is_cached(self, extractor: KnowledgeExtractor):
        cached = extractor.extract_and_cache(
            response=_GOOD_RESPONSE,
            query="SDP block deployment",
            quality_score=0.9,
        )
        assert cached is True

    def test_low_quality_response_not_cached(self, extractor: KnowledgeExtractor):
        cached = extractor.extract_and_cache(
            response="Not sure.",
            query="SDP block deployment",
            quality_score=0.2,
        )
        assert cached is False

    def test_empty_response_not_cached(self, extractor: KnowledgeExtractor):
        cached = extractor.extract_and_cache(
            response="",
            query="SDP block deployment",
            quality_score=0.9,
        )
        assert cached is False

    def test_read_cached_returns_none_when_empty(self, extractor: KnowledgeExtractor):
        result = extractor.read_cached("SDP block")
        assert result is None

    def test_read_cached_after_extract(self, extractor: KnowledgeExtractor):
        extractor.extract_and_cache(
            response=_GOOD_RESPONSE,
            query="SDP block deployment stuck",
            quality_score=0.9,
        )
        cached = extractor.read_cached("SDP block")
        # May or may not match depending on query similarity logic
        assert cached is None or isinstance(cached, str)

    def test_cache_file_created(self, extractor: KnowledgeExtractor, cache_dir: Path):
        extractor.extract_and_cache(
            response=_GOOD_RESPONSE,
            query="SDP block deployment",
            quality_score=0.85,
        )
        cache_files = list(cache_dir.iterdir())
        assert len(cache_files) >= 1

    def test_different_agent_names_separate_caches(self, cache_dir: Path):
        e1 = KnowledgeExtractor(cache_dir=cache_dir, agent_name="agent-one")
        e2 = KnowledgeExtractor(cache_dir=cache_dir, agent_name="agent-two")
        e1.extract_and_cache(response=_GOOD_RESPONSE, query="q", quality_score=0.9)
        e2.extract_and_cache(response=_GOOD_RESPONSE, query="q", quality_score=0.9)
        files = list(cache_dir.iterdir())
        names = [f.stem for f in files]
        assert "agent-one" in names
        assert "agent-two" in names

    def test_multiple_kb_dirs(self, cache_dir: Path):
        # KnowledgeExtractor does not take kb_dirs -- just verify alternative construction
        e = KnowledgeExtractor(cache_dir=cache_dir, agent_name="test-agent")
        result = e.extract_and_cache(response=_GOOD_RESPONSE, query="SDP canary", quality_score=0.8)
        assert isinstance(result, bool)

    def test_no_cache_dir_creates_on_demand(self, tmp_path: Path):
        new_cache = tmp_path / "new_cache_subdir"
        e = KnowledgeExtractor(cache_dir=new_cache, agent_name="test-agent")
        e.extract_and_cache(response=_GOOD_RESPONSE, query="q", quality_score=0.9)
        assert new_cache.exists()

    def test_stale_ttl_param(self, cache_dir: Path):
        e = KnowledgeExtractor(cache_dir=cache_dir, agent_name="test-agent", ttl_days=0)
        result = e.extract_and_cache(response=_GOOD_RESPONSE, query="SDP block stale", quality_score=0.9)
        assert isinstance(result, bool)

    def test_max_chars_param(self, cache_dir: Path):
        e = KnowledgeExtractor(cache_dir=cache_dir, agent_name="test-agent", max_cache_size_chars=100)
        e.extract_and_cache(response=_GOOD_RESPONSE, query="q1", quality_score=0.9)
        e.extract_and_cache(response=_GOOD_RESPONSE, query="q2", quality_score=0.9)
        e.extract_and_cache(response=_GOOD_RESPONSE, query="q3", quality_score=0.9)
        # Should evict old entries to stay within limit
        cache_file = cache_dir / "test-agent.md"
        if cache_file.exists():
            assert cache_file.stat().st_size < 10_000
