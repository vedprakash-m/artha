"""tests/ext_agents/test_preflight_ext_agent.py — EA-11b: check_ext_agent_health tests."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Ensure scripts/ is on path
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from preflight.integration_checks import check_ext_agent_health  # type: ignore
from lib.agent_registry import AgentRegistry  # type: ignore

from .conftest import SAMPLE_AGENT_ENTRY


def _write_artha_config(tmp_path: Path, enabled: bool = True) -> Path:
    """Write a minimal artha_config.yaml that enables external agents."""
    cfg = {
        "harness": {
            "agentic": {
                "external_agents": {
                    "enabled": enabled,
                }
            }
        }
    }
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    p = cfg_dir / "artha_config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


def _write_registry(tmp_path: Path, agents: dict) -> Path:
    """Write an external-registry.yaml under tmp_path/config/agents/."""
    agents_dir = tmp_path / "config" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    reg = {"schema_version": "1.0", "agents": agents}
    (agents_dir / "external-registry.yaml").write_text(yaml.dump(reg), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# EA-11b tests
# ---------------------------------------------------------------------------


class TestCheckExtAgentHealth:
    def test_all_healthy_passes(self, tmp_path: Path):
        """When all agents have status 'active', check passes."""
        _write_artha_config(tmp_path, enabled=True)
        entry = {**SAMPLE_AGENT_ENTRY}
        entry["health"] = {**SAMPLE_AGENT_ENTRY["health"], "status": "active"}
        _write_registry(tmp_path, {"test-agent": entry})

        with (
            patch("preflight.integration_checks.ARTHA_DIR", str(tmp_path)),
            patch("preflight.integration_checks.SCRIPTS_DIR", _SCRIPTS_DIR),
        ):
            result = check_ext_agent_health()

        assert result.passed, f"Expected pass but got: {result.message}"
        assert result.severity == "P1"

    def test_degraded_agent_reported(self, tmp_path: Path):
        """When an agent is degraded, check fails with agent name in message."""
        _write_artha_config(tmp_path, enabled=True)
        entry = {**SAMPLE_AGENT_ENTRY}
        entry["health"] = {**SAMPLE_AGENT_ENTRY["health"], "status": "degraded"}
        _write_registry(tmp_path, {"test-agent": entry})

        with (
            patch("preflight.integration_checks.ARTHA_DIR", str(tmp_path)),
            patch("preflight.integration_checks.SCRIPTS_DIR", _SCRIPTS_DIR),
        ):
            result = check_ext_agent_health()

        assert not result.passed, "Expected check to fail for degraded agent"
        assert "test-agent" in result.message or "degraded" in result.message
        assert result.severity == "P1"
