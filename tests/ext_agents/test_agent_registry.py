"""tests/ext_agents/test_agent_registry.py — AR-9 AgentRegistry tests."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from lib.agent_registry import AgentRegistry, _parse_agent  # type: ignore
from .conftest import SAMPLE_AGENT_ENTRY, make_test_agent


class TestExternalAgent:
    def test_parse_agent_creates_object(self):
        agent = make_test_agent()
        assert agent.name == "test-agent"
        assert agent.trust_tier == "external"
        assert agent.enabled is True

    def test_agent_has_required_fields(self):
        agent = make_test_agent()
        assert agent.label == "Test Agent"
        assert agent.description is not None
        assert agent.health is not None

    def test_keywords_in_routing(self):
        agent = make_test_agent()
        assert isinstance(agent.routing.keywords, list)

    def test_disabled_agent_not_active(self):
        entry = copy.deepcopy(SAMPLE_AGENT_ENTRY)
        entry["enabled"] = False
        agent = _parse_agent("test-agent", entry)
        assert not agent.enabled


class TestAgentRegistryLoad:
    def test_load_empty_registry(self, tmp_registry_dir: Path):
        reg = AgentRegistry.load(tmp_registry_dir)
        agents = reg.active_agents()
        assert agents == []

    def test_load_populated_registry(self, populated_registry_dir: Path):
        reg = AgentRegistry.load(populated_registry_dir)
        agents = reg.active_agents()
        assert len(agents) == 1
        assert agents[0].name == "test-agent"


class TestAgentRegistryCRUD:
    def test_register_new_agent(self, tmp_registry_dir: Path):
        reg = AgentRegistry.load(tmp_registry_dir)
        agent = make_test_agent()
        reg.register(agent)
        assert reg.get("test-agent") is not None

    def test_register_duplicate_raises_or_overwrites(self, tmp_registry_dir: Path):
        reg = AgentRegistry.load(tmp_registry_dir)
        agent = make_test_agent()
        reg.register(agent)
        # Second register: should either overwrite or raise ValueError
        try:
            reg.register(agent)
        except ValueError:
            pass  # acceptable

    def test_retire_disables_agent(self, populated_registry_dir: Path):
        reg = AgentRegistry.load(populated_registry_dir)
        reg.retire("test-agent")
        agent = reg.get("test-agent")
        assert agent is not None
        assert agent.enabled is False

    def test_reinstate_enables_agent(self, populated_registry_dir: Path):
        reg = AgentRegistry.load(populated_registry_dir)
        reg.retire("test-agent")
        reg.reinstate("test-agent")
        agent = reg.get("test-agent")
        assert agent is not None
        assert agent.enabled is True

    def test_get_agent_missing_returns_none(self, tmp_registry_dir: Path):
        reg = AgentRegistry.load(tmp_registry_dir)
        assert reg.get("does-not-exist") is None

    def test_active_agents_excludes_retired(self, populated_registry_dir: Path):
        reg = AgentRegistry.load(populated_registry_dir)
        reg.retire("test-agent")
        assert reg.active_agents() == []


class TestAgentRegistryPersistence:
    def test_save_and_reload(self, tmp_registry_dir: Path):
        reg = AgentRegistry.load(tmp_registry_dir)
        agent = make_test_agent()
        reg.register(agent)
        reg.save()

        reg2 = AgentRegistry.load(tmp_registry_dir)
        assert reg2.get("test-agent") is not None


class TestShadowModeField:
    """Gap 1: shadow_mode field (DD-15 / Appendix A)."""

    def test_shadow_mode_defaults_false(self):
        agent = make_test_agent()
        assert agent.shadow_mode is False

    def test_shadow_mode_parsed_from_entry(self):
        import copy
        entry = copy.deepcopy(SAMPLE_AGENT_ENTRY)
        entry["shadow_mode"] = True
        agent = _parse_agent("test-agent", entry)
        assert agent.shadow_mode is True

    def test_shadow_mode_roundtrips_via_save_load(self, tmp_registry_dir: Path):
        import copy
        from lib.agent_registry import _parse_agent  # type: ignore

        entry = copy.deepcopy(SAMPLE_AGENT_ENTRY)
        entry["shadow_mode"] = True
        agent = _parse_agent("shadow-agent", entry)

        reg = AgentRegistry.load(tmp_registry_dir)
        reg.register(agent)
        reg.save()

        reg2 = AgentRegistry.load(tmp_registry_dir)
        reloaded = reg2.get("shadow-agent")
        assert reloaded is not None
        assert reloaded.shadow_mode is True

    def test_shadow_mode_false_roundtrips(self, tmp_registry_dir: Path):
        agent = make_test_agent()
        assert agent.shadow_mode is False

        reg = AgentRegistry.load(tmp_registry_dir)
        reg.register(agent)
        reg.save()

        reg2 = AgentRegistry.load(tmp_registry_dir)
        reloaded = reg2.get("test-agent")
        assert reloaded is not None
        assert reloaded.shadow_mode is False

    def test_shadow_mode_absent_in_yaml_defaults_false(self, tmp_registry_dir: Path):
        """Old registry YAML without shadow_mode field should load cleanly."""
        import yaml  # type: ignore
        agents_dir = tmp_registry_dir / "agents"
        agents_dir.mkdir(exist_ok=True)
        # Write registry without shadow_mode key
        reg_data = {
            "schema_version": "1.0",
            "agents": {
                "old-agent": {
                    "label": "Old Agent",
                    "description": "Legacy entry",
                    "trust_tier": "external",
                    "enabled": True,
                    "status": "active",
                    "source": "",
                    "content_hash": "abc",
                    "auto_dispatch": False,
                    "auto_dispatch_after": 10,
                    "routing": {"keywords": ["foo"], "min_confidence": 0.3},
                    "health": {"status": "active"},
                }
            },
        }
        (agents_dir / "external-registry.yaml").write_text(
            yaml.dump(reg_data), encoding="utf-8"
        )
        reg = AgentRegistry.load(tmp_registry_dir)
        agent = reg.get("old-agent")
        assert agent is not None
        assert agent.shadow_mode is False


class TestRegisteredAtField:
    """Gap 2: registered_at field (Appendix A)."""

    def test_registered_at_defaults_none(self):
        agent = make_test_agent()
        assert agent.registered_at is None

    def test_registered_at_parsed_from_entry(self):
        import copy
        entry = copy.deepcopy(SAMPLE_AGENT_ENTRY)
        entry["registered_at"] = "2026-04-02T00:00:00+00:00"
        agent = _parse_agent("test-agent", entry)
        assert agent.registered_at == "2026-04-02T00:00:00+00:00"

    def test_registered_at_roundtrips_via_save_load(self, tmp_registry_dir: Path):
        import copy
        ts = "2026-04-02T12:34:56+00:00"
        entry = copy.deepcopy(SAMPLE_AGENT_ENTRY)
        entry["registered_at"] = ts
        agent = _parse_agent("ts-agent", entry)

        reg = AgentRegistry.load(tmp_registry_dir)
        reg.register(agent)
        reg.save()

        reg2 = AgentRegistry.load(tmp_registry_dir)
        reloaded = reg2.get("ts-agent")
        assert reloaded is not None
        assert reloaded.registered_at == ts

    def test_registered_at_none_roundtrips(self, tmp_registry_dir: Path):
        agent = make_test_agent()
        reg = AgentRegistry.load(tmp_registry_dir)
        reg.register(agent)
        reg.save()

        reg2 = AgentRegistry.load(tmp_registry_dir)
        reloaded = reg2.get("test-agent")
        assert reloaded is not None
        assert reloaded.registered_at is None
