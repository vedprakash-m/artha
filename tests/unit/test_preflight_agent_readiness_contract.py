"""Unknown external-agent readiness must not be reported as a successful skip."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from lib.agent_registry import AgentRegistry


@pytest.fixture(params=["package", "script"])
def preflight(request, monkeypatch, tmp_path):
    if request.param == "package":
        import preflight.integration_checks as module
    else:
        name = "_preflight_agent_readiness_script"
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / "preflight.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ARTHA_DIR", str(tmp_path))
    monkeypatch.setattr(module, "SCRIPTS_DIR", str(SCRIPTS))
    return module


def write_config(tmp_path, text):
    path = tmp_path / "config" / "artha_config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def enable_external_agents(tmp_path):
    write_config(
        tmp_path, "harness:\n  agentic:\n    external_agents:\n"
        "      enabled: true\n      discovery:\n        enabled: true\n",
    )
    registry = tmp_path / "config" / "agents" / "external-registry.yaml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    return registry


@pytest.mark.parametrize("check_name", ["check_ext_agent_discovery", "check_ext_agent_health"])
@pytest.mark.parametrize("config", [None, "", "[", "[]", "harness: []",
                                    "harness: {agentic: {external_agents: {enabled: 'false'}}}",
                                    "harness: {agentic: {external_agents: {enabled: true, discovery: []}}}"])
def test_unknown_or_invalid_feature_config_is_not_a_successful_skip(
    preflight, tmp_path, config, check_name,
):
    if config is not None:
        write_config(tmp_path, config)
    result = getattr(preflight, check_name)()
    assert result.severity == "P1"
    assert not result.passed


@pytest.mark.parametrize("check_name", ["check_ext_agent_discovery", "check_ext_agent_health"])
def test_valid_disabled_feature_remains_an_explicit_skip(preflight, tmp_path, check_name):
    write_config(tmp_path, "harness:\n  agentic:\n    external_agents:\n      enabled: false\n")
    result = getattr(preflight, check_name)()
    assert result.severity == "P1"
    assert result.passed
    assert "not enabled" in result.message.lower()


def test_explicitly_disabled_discovery_remains_an_explicit_skip(preflight, tmp_path):
    write_config(
        tmp_path,
        "harness:\n  agentic:\n    external_agents:\n"
        "      enabled: true\n      discovery:\n        enabled: false\n",
    )
    result = preflight.check_ext_agent_discovery()
    assert result.passed
    assert "discovery disabled" in result.message.lower()


@pytest.mark.parametrize("check_name", ["check_ext_agent_discovery", "check_ext_agent_health"])
@pytest.mark.parametrize(
    "registry_content",
    ["[", "[]", "{}", "agents: []", "agents: {bad-agent: []}",
     "agents: {bad-agent: {routing: {min_confidence: invalid}}}"],
)
def test_existing_malformed_registry_is_not_a_successful_empty_result(
    preflight, tmp_path, check_name, registry_content,
):
    registry = enable_external_agents(tmp_path)
    registry.write_text(registry_content, encoding="utf-8")
    result = getattr(preflight, check_name)()
    assert result.severity == "P1"
    assert not result.passed
    assert "registry" in result.message.lower()


@pytest.mark.parametrize("check_name", ["check_ext_agent_discovery", "check_ext_agent_health"])
def test_missing_registry_keeps_existing_empty_optional_behavior(preflight, tmp_path, check_name):
    enable_external_agents(tmp_path)
    result = getattr(preflight, check_name)()
    assert result.severity == "P1"
    assert result.passed


def test_strict_registry_load_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        AgentRegistry.load(tmp_path, strict=True)


@pytest.mark.parametrize("check_name", ["check_ext_agent_discovery", "check_ext_agent_health"])
def test_valid_empty_registry_is_healthy(preflight, tmp_path, check_name):
    registry = enable_external_agents(tmp_path)
    registry.write_text("schema_version: '1.0'\nagents: {}\n", encoding="utf-8")
    result = getattr(preflight, check_name)()
    assert result.severity == "P1"
    assert result.passed
