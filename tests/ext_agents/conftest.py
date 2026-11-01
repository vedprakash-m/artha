"""tests/ext_agents/conftest.py — Shared fixtures for AR-9 tests."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

# Ensure scripts/ is on path (mirrors root conftest.py)
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Shared agent entry data — matches the dict structure expected by _parse_agent()
# ---------------------------------------------------------------------------

SAMPLE_AGENT_ENTRY = {
    "label": "Test Agent",
    "description": "A test agent for unit tests.",
    "trust_tier": "external",
    "enabled": True,
    "status": "active",
    "source": "config/agents/external/test-agent.agent.md",
    "content_hash": "abc123def456",
    "auto_dispatch": False,
    "auto_dispatch_after": 10,
    "routing": {
        "keywords": ["deployment stuck", "SDP block", "canary"],
        "domains": ["deployment", "storage"],
        "min_confidence": 0.6,
        "min_keyword_hits": 1,
        "exclude_keywords": ["personal", "family"],
    },
    "invocation": {
        "timeout_seconds": 60,
        "max_response_chars": 2000,
    },
    "pii_profile": {"allow": ["REGION", "HOSTNAME"], "block": []},
    "fallback_cascade": [{"type": "kb"}, {"type": "kusto"}],
    "health": {
        "status": "active",
        "total_invocations": 0,
        "successful_invocations": 0,
        "failed_invocations": 0,
        "consecutive_failures": 0,
        "mean_quality_score": 0.0,
    },
}

SAMPLE_AGENT_MD = textwrap.dedent("""\
    ---
    name: test-agent
    label: Test Agent
    description: A test agent for unit tests.
    trust_tier: external
    enabled: true
    status: active
    source: config/agents/external/test-agent.agent.md
    content_hash: abc123def456
    auto_dispatch: false
    auto_dispatch_after: 10
    routing:
      keywords:
        - deployment stuck
        - SDP block
        - canary
      domains:
        - deployment
        - storage
      min_confidence: 0.6
      min_keyword_hits: 1
      exclude_keywords:
        - personal
        - family
    invocation:
      timeout_seconds: 60
      max_response_chars: 2000
    pii_profile:
      allow:
        - REGION
        - HOSTNAME
      block: []
    fallback_cascade:
      - type: kb
      - type: kusto
    ---

    # Test Agent

    This is a test agent body.
""")


def make_test_agent(name: str = "test-agent"):
    """Create an ExternalAgent from SAMPLE_AGENT_ENTRY using the real parser."""
    from lib.agent_registry import _parse_agent  # type: ignore
    return _parse_agent(name, SAMPLE_AGENT_ENTRY)


@pytest.fixture()
def tmp_registry_dir(tmp_path: Path) -> Path:
    """Temp config_dir with an empty registry at agents/external-registry.yaml."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "external-registry.yaml").write_text(
        "schema_version: '1.0'\nagents: {}\n", encoding="utf-8"
    )
    return tmp_path


@pytest.fixture()
def populated_registry_dir(tmp_path: Path) -> Path:
    """Temp config_dir with one agent already in the registry."""
    import yaml  # type: ignore

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    reg = {"schema_version": "1.0", "agents": {"test-agent": SAMPLE_AGENT_ENTRY}}
    (agents_dir / "external-registry.yaml").write_text(yaml.dump(reg), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def sample_agent_md_file(tmp_path: Path) -> Path:
    """Write SAMPLE_AGENT_MD to a temp .agent.md file and return its path."""
    p = tmp_path / "test-agent.agent.md"
    p.write_text(SAMPLE_AGENT_MD, encoding="utf-8")
    return p


@pytest.fixture()
def mock_provider():
    """Return a MockAgentProvider with a canned response for 'test-agent'."""
    from lib.agent_invoker import MockAgentProvider  # type: ignore

    provider = MockAgentProvider()
    provider.add_response(
        "test-agent",
        "Deployment is stuck in SDP stage 3 due to capacity constraints.",
    )
    return provider
