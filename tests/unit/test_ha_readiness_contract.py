"""HA preflight must distinguish disabled configuration from unavailable checks."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(params=["package", "script"])
def preflight(request, monkeypatch, tmp_path):
    if request.param == "package":
        import preflight as module
    else:
        name = "_preflight_ha_contract_test"
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / "preflight.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ARTHA_DIR", str(tmp_path))
    return module


@pytest.fixture
def dependencies(monkeypatch):
    import keyring
    from connectors import homeassistant
    credential = Mock(return_value="synthetic-token")
    provider = Mock(return_value=True)
    monkeypatch.setattr(keyring, "get_password", credential)
    monkeypatch.setattr(homeassistant, "health_check", provider)
    return credential, provider


def write_config(tmp_path, text):
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    (config / "connectors.yaml").write_text(text, encoding="utf-8")


@pytest.mark.parametrize("text", [
    None, "", "[", "[]", "1", "connectors: []",
    "connectors: {homeassistant: []}",
    "connectors: {homeassistant: null}",
    'connectors: {homeassistant: {enabled: "false"}}',
    "connectors: {homeassistant: {enabled: 0}}",
    "connectors: {homeassistant: {enabled: true, fetch: []}}",
    "connectors: {homeassistant: {enabled: true, fetch: {ha_url: []}}}",
    'connectors: {homeassistant: {enabled: true, fetch: {ha_url: "  "}}}',
])
def test_unknown_or_invalid_config_is_not_a_successful_skip(
    preflight, dependencies, tmp_path, text,
):
    if text is not None:
        write_config(tmp_path, text)
    result = preflight.check_ha_connectivity()
    assert result.severity == "P1"
    assert not result.passed
    dependencies[0].assert_not_called()
    dependencies[1].assert_not_called()


@pytest.mark.parametrize("unreadable", ["permission", "encoding", "yaml"])
def test_config_errors_remain_visible_without_exposing_contents(
    preflight, dependencies, tmp_path, monkeypatch, unreadable,
):
    import builtins
    write_config(tmp_path, "[synthetic-private-marker")
    target = tmp_path / "config" / "connectors.yaml"
    if unreadable == "encoding":
        target.write_bytes(b"\xff")
    elif unreadable == "permission":
        original = builtins.open
        def denied(path, *args, **kwargs):
            if Path(path) == target:
                raise PermissionError("synthetic-private-marker")
            return original(path, *args, **kwargs)
        monkeypatch.setattr(builtins, "open", denied)
    result = preflight.check_ha_connectivity()
    assert not result.passed and result.severity == "P1"
    assert "synthetic-private-marker" not in result.message
    dependencies[0].assert_not_called()
    dependencies[1].assert_not_called()


@pytest.mark.parametrize("text", [
    "connectors: {}",
    "connectors: {homeassistant: {enabled: false}}",
])
def test_valid_disabled_or_absent_connector_remains_an_explicit_skip(
    preflight, dependencies, tmp_path, text,
):
    write_config(tmp_path, text)
    result = preflight.check_ha_connectivity()
    assert result.passed and result.severity == "P1"
    dependencies[0].assert_not_called()
    dependencies[1].assert_not_called()


@pytest.mark.parametrize("outcome", [True, False, "LAN gate", "not reachable", "Cannot connect", "unexpected"])
def test_only_verified_connectivity_passes(preflight, dependencies, tmp_path, outcome):
    write_config(
        tmp_path, "connectors: {homeassistant: {enabled: true, "
        "fetch: {handler: connectors.homeassistant, ha_url: 'http://ha.invalid:8123'}}}",
    )
    if isinstance(outcome, bool):
        dependencies[1].return_value = outcome
    else:
        dependencies[1].side_effect = RuntimeError(outcome)
    result = preflight.check_ha_connectivity()
    assert result.passed is (outcome is True)
    assert result.severity == "P1"
    assert dependencies[1].call_args.kwargs == {"ha_url": "http://ha.invalid:8123"}


@pytest.mark.parametrize("unavailable", [False, True])
def test_missing_or_unavailable_credential_cannot_probe(
    preflight, dependencies, tmp_path, unavailable,
):
    write_config(
        tmp_path, "connectors: {homeassistant: {enabled: true, "
        "fetch: {ha_url: 'http://ha.invalid:8123'}}}",
    )
    if unavailable:
        dependencies[0].side_effect = RuntimeError("synthetic backend unavailable")
    else:
        dependencies[0].return_value = None
    result = preflight.check_ha_connectivity()
    assert not result.passed and result.severity == "P1"
    dependencies[1].assert_not_called()
    if unavailable:
        assert "token store unavailable" in result.message
        assert "synthetic backend" not in result.message
