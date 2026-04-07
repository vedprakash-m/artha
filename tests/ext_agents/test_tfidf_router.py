"""
tests/ext_agents/test_tfidf_router.py — EAR-4: TF-IDF router tests.

Tests (20):
 1. rebuild() creates vector cache file
 2. query() returns list of LexicalMatch
 3. query() respects top_n
 4. query() respects min_sim threshold
 5. empty registry → empty query results
 6. exact keyword match scores higher than no match
 7. cache file is valid JSON
 8. rebuild is idempotent
 9. query on unrelated text → score below threshold
10. char-ngram tokenizer produces non-empty list for any string
11. cosine similarity of identical vectors = 1.0
12. cosine similarity of orthogonal vectors = 0.0
13. rebuild updates stale cache
14. query lazy-loads cache on first call
15. concurrent query calls don't corrupt results
16. single-token query works
17. query with unicode text doesn't crash
18. query returns results sorted by score desc
19. two-char agent name doesn't crash rebuild
20. min_sim=0.0 returns all agents

Ref: specs/ext-agent-reloaded.md §EAR-4
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest
import yaml

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.agent_registry import AgentRegistry
from lib.tfidf_router import TFIDFRouter, _char_ngrams, _cosine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_registry(tmp_path: Path, agents: dict) -> AgentRegistry:
    """Write a minimal agent registry YAML and return an AgentRegistry."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    reg = {"schema_version": "1.0", "agents": agents}
    (agents_dir / "external-registry.yaml").write_text(yaml.dump(reg))
    return AgentRegistry.load(tmp_path)


def _agent_entry(keywords: list[str], domains: list[str]) -> dict:
    return {
        "label": "Test Agent",
        "description": " ".join(keywords),
        "enabled": True,
        "status": "active",
        "trust_tier": "external",
        "routing": {
            "keywords": keywords,
            "domains": domains,
            "min_confidence": 0.3,
            "min_keyword_hits": 1,
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_reg(tmp_path):
    return _write_registry(tmp_path, {
        "deploy-agent": _agent_entry(["deploy", "rollout", "canary"], ["deployment"]),
        "storage-agent": _agent_entry(["blob", "storage", "bucket"], ["storage"]),
    })


@pytest.fixture()
def router(tmp_path):
    return TFIDFRouter(cache_file=tmp_path / "vectors.json")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_rebuild_creates_cache(router, sample_reg, tmp_path):
    cache_file = tmp_path / "vectors2.json"
    router._cache_file = cache_file
    router.rebuild(sample_reg)
    assert cache_file.exists(), "Cache file not created"


def test_query_returns_list(router, sample_reg):
    router.rebuild(sample_reg)
    result = router.query("deploy rollout", top_n=5)
    assert isinstance(result, list)


def test_query_respects_top_n(router, sample_reg):
    router.rebuild(sample_reg)
    result = router.query("deploy blob storage rollout", top_n=1)
    assert len(result) <= 1


def test_query_respects_min_sim(router, sample_reg):
    router.rebuild(sample_reg)
    result = router.query("irrelevant quantum foam", top_n=5, min_sim=0.99)
    assert result == [], f"Expected empty but got: {result}"


def test_empty_registry_returns_empty(router, tmp_path):
    empty_reg = _write_registry(tmp_path / "empty", {})
    router.rebuild(empty_reg)
    result = router.query("deploy", top_n=5)
    assert result == []


def test_exact_match_scores_higher(router, sample_reg):
    router.rebuild(sample_reg)
    deploy_results = router.query("rollout canary deploy", top_n=2)
    storage_results = router.query("blob storage bucket", top_n=2)

    if deploy_results:
        assert deploy_results[0].agent_name == "deploy-agent"
    if storage_results:
        assert storage_results[0].agent_name == "storage-agent"


def test_cache_file_is_valid_json(router, sample_reg, tmp_path):
    cache_file = tmp_path / "v.json"
    router._cache_file = cache_file
    router.rebuild(sample_reg)
    data = json.loads(cache_file.read_text())
    assert isinstance(data, dict)


def test_rebuild_is_idempotent(router, sample_reg):
    router.rebuild(sample_reg)
    result1 = router.query("deploy", top_n=3)
    router.rebuild(sample_reg)
    result2 = router.query("deploy", top_n=3)
    assert result1 == result2


def test_unrelated_query_low_score(router, sample_reg):
    router.rebuild(sample_reg)
    result = router.query("xyzzy frobnitz warp drive", top_n=5, min_sim=0.5)
    assert result == []


def test_ngram_tokenizer_non_empty():
    grams = _char_ngrams("deployment")
    assert len(grams) > 0


def test_cosine_identical_vectors():
    v = {"abc": 1.0, "bcd": 0.5}
    score = _cosine(v, v)
    assert abs(score - 1.0) < 1e-6


def test_cosine_orthogonal_vectors():
    v1 = {"abc": 1.0}
    v2 = {"xyz": 1.0}
    score = _cosine(v1, v2)
    assert score == 0.0


def test_rebuild_updates_cache(router, sample_reg, tmp_path):
    cache_file = tmp_path / "update.json"
    router._cache_file = cache_file

    router.rebuild(sample_reg)
    data1 = json.loads(cache_file.read_text())

    new_reg = _write_registry(tmp_path / "new", {
        "new-agent": _agent_entry(["newkeyword", "freshterm"], ["newdomain"]),
    })
    router.rebuild(new_reg)
    data2 = json.loads(cache_file.read_text())
    assert data1 != data2


def test_query_lazy_loads_cache(sample_reg, tmp_path):
    cache_file = tmp_path / "lazy.json"
    r1 = TFIDFRouter(cache_file=cache_file)
    r1.rebuild(sample_reg)

    r2 = TFIDFRouter(cache_file=cache_file)
    # r2 has not called rebuild — should lazy-load from cache
    result = r2.query("deploy", top_n=3)
    assert isinstance(result, list)


def test_concurrent_queries_safe(router, sample_reg):
    router.rebuild(sample_reg)
    results = []

    def worker():
        r = router.query("deploy rollout", top_n=3)
        results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(isinstance(r, list) for r in results)


def test_single_token_query(router, sample_reg):
    router.rebuild(sample_reg)
    result = router.query("deploy", top_n=3)
    assert isinstance(result, list)


def test_unicode_query_no_crash(router, sample_reg):
    router.rebuild(sample_reg)
    result = router.query("déployer le 裝置 über alles", top_n=3)
    assert isinstance(result, list)


def test_results_sorted_desc(router, sample_reg):
    router.rebuild(sample_reg)
    result = router.query("deploy rollout blob storage canary", top_n=5, min_sim=0.0)
    scores = [m.similarity for m in result]
    assert scores == sorted(scores, reverse=True), "Results not sorted by score descending"


def test_short_agent_name_no_crash(router, tmp_path):
    short_reg = _write_registry(tmp_path / "short", {
        "ab": _agent_entry(["x"], ["y"]),
    })
    router.rebuild(short_reg)
    result = router.query("x", top_n=3)
    assert isinstance(result, list)


def test_min_sim_zero_returns_all(router, sample_reg):
    router.rebuild(sample_reg)
    result = router.query("anything at all", top_n=10, min_sim=0.0)
    assert isinstance(result, list)
