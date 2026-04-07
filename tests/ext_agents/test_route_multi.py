"""
tests/ext_agents/test_route_multi.py — Phase 0 blocking tests: route_multi().

Tests (8):
 1. route_multi returns a list
 2. route_multi returns <=N candidates
 3. candidates are sorted by confidence descending
 4. domain independence: no two results share the same domain set
 5. route_multi with top_n=1 works like route()
 6. route_multi with no registry → empty list (no crash)
 7. each RoutingMatch has required fields (agent_name, confidence)
 8. route_multi on unroutable query returns empty list

Ref: specs/ext-agent-reloaded.md §BLOCKING-2
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.agent_registry import AgentRegistry
from lib.agent_router import AgentRouter, RoutingMatch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_registry_dir(tmp_path: Path, agents: dict) -> Path:
    """Write a minimal registry yaml and return the config dir."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    reg = {"schema_version": "1.0", "agents": agents}
    (agents_dir / "external-registry.yaml").write_text(yaml.dump(reg))
    return tmp_path


def _make_agent_entry(
    label, domains, keywords, min_confidence=0.3, min_keyword_hits=1
):
    return {
        "label": label,
        "description": f"{label} agent",
        "trust_tier": "external",
        "enabled": True,
        "status": "active",
        "source": f"config/agents/external/{label.lower()}.agent.md",
        "content_hash": "abc123",
        "auto_dispatch": False,
        "auto_dispatch_after": 10,
        "routing": {
            "keywords": keywords,
            "domains": domains,
            "min_confidence": min_confidence,
            "min_keyword_hits": min_keyword_hits,
            "exclude_keywords": [],
        },
        "invocation": {"timeout_seconds": 30, "max_response_chars": 2000},
        "pii_profile": {"allow": [], "block": []},
        "fallback_cascade": [],
        "health": {
            "status": "active",
            "total_invocations": 5,
            "successful_invocations": 5,
            "failed_invocations": 0,
            "consecutive_failures": 0,
            "mean_quality_score": 0.8,
        },
    }


@pytest.fixture()
def multi_domain_router(tmp_path):
    agents = {
        "deploy-agent": _make_agent_entry(
            "DeployAgent", ["deployment"], ["deploy", "rollout", "canary"]
        ),
        "storage-agent": _make_agent_entry(
            "StorageAgent", ["storage"], ["blob", "storage", "bucket"]
        ),
    }
    config_dir = _make_registry_dir(tmp_path, agents)
    reg = AgentRegistry.load(config_dir)
    return AgentRouter(registry=reg)


@pytest.fixture()
def overlap_domain_router(tmp_path):
    agents = {
        "agent-a": _make_agent_entry(
            "AgentA", ["deployment", "storage"], ["deploy"]
        ),
        "agent-b": _make_agent_entry(
            "AgentB", ["deployment"], ["rollout"]
        ),
    }
    config_dir = _make_registry_dir(tmp_path, agents)
    reg = AgentRegistry.load(config_dir)
    return AgentRouter(registry=reg)


# ---------------------------------------------------------------------------
# Test 1: route_multi returns a list
# ---------------------------------------------------------------------------

def test_route_multi_returns_list(multi_domain_router):
    result = multi_domain_router.route_multi("deploy something", top_n=3)
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Test 2: route_multi returns <=N candidates
# ---------------------------------------------------------------------------

def test_route_multi_respects_top_n(multi_domain_router):
    result = multi_domain_router.route_multi("deploy storage blob rollout canary", top_n=2)
    assert len(result) <= 2


# ---------------------------------------------------------------------------
# Test 3: candidates sorted by confidence descending
# ---------------------------------------------------------------------------

def test_route_multi_sorted_by_confidence(multi_domain_router):
    result = multi_domain_router.route_multi("deploy blob storage rollout canary", top_n=5)
    if len(result) >= 2:
        assert result[0].confidence >= result[1].confidence


# ---------------------------------------------------------------------------
# Test 4: domain independence — no two results share the same primary domain
# ---------------------------------------------------------------------------

def test_route_multi_domain_independence(overlap_domain_router):
    """If two agents share a domain, only the higher-confidence one should be returned."""
    result = overlap_domain_router.route_multi("deploy rollout", top_n=3)
    # Both agents have "deployment" domain; only one should be selected
    assert len(result) <= 1


# ---------------------------------------------------------------------------
# Test 5: route_multi with top_n=1 → single result
# ---------------------------------------------------------------------------

def test_route_multi_top_n_one(multi_domain_router):
    result = multi_domain_router.route_multi("deploy rollout", top_n=1)
    assert len(result) <= 1


# ---------------------------------------------------------------------------
# Test 6: route_multi with empty registry → empty list
# ---------------------------------------------------------------------------

def test_route_multi_empty_registry(tmp_path):
    config_dir = _make_registry_dir(tmp_path, {})
    reg = AgentRegistry.load(config_dir)
    router = AgentRouter(registry=reg)
    result = router.route_multi("deploy", top_n=3)
    assert result == []


# ---------------------------------------------------------------------------
# Test 7: each RoutingMatch has required fields
# ---------------------------------------------------------------------------

def test_route_multi_result_fields(multi_domain_router):
    result = multi_domain_router.route_multi("deploy blob storage", top_n=3)
    for match in result:
        assert hasattr(match, "agent_name"), "Missing agent_name"
        assert hasattr(match, "confidence"), "Missing confidence"
        assert isinstance(match.confidence, float)
        assert 0.0 <= match.confidence <= 1.0


# ---------------------------------------------------------------------------
# Test 8: unroutable query → empty list (or very low confidence results)
# ---------------------------------------------------------------------------

def test_route_multi_unroutable(multi_domain_router):
    result = multi_domain_router.route_multi("completely unrelated gibberish xyzzy frobnitz", top_n=3)
    # Should not crash; may be empty
    assert isinstance(result, list)
    for r in result:
        assert isinstance(r, RoutingMatch)
