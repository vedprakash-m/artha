"""Keep payload parameters out of Windows' bounded test-status environment."""
from pathlib import Path

import pytest


@pytest.fixture
def parameter_id(pytestconfig):
    conftest_path = Path(__file__).resolve().parents[1] / "conftest.py"
    plugin = next(
        plugin for plugin in pytestconfig.pluginmanager.get_plugins()
        if getattr(plugin, "__file__", None) == str(conftest_path)
    )
    hook = getattr(plugin, "pytest_make_parametrize_id", None)
    assert callable(hook), "large payload parameters need bounded pytest IDs"
    return hook


@pytest.mark.parametrize("value", ["sensitive-" * 4000, b"sensitive-" * 4000])
def test_large_payload_ids_are_bounded_deterministic_and_hide_content(parameter_id, value):
    label = parameter_id(value, "payload")
    assert len(label) <= 80
    assert label == parameter_id(value, "payload")
    assert "sensitive" not in label
    assert label != parameter_id(value + value[:1], "payload")
    assert label != parameter_id(value, "other")


@pytest.mark.parametrize("value", ["short", b"short", 42, None, "x" * 128])
def test_ordinary_parameter_ids_keep_pytest_defaults(parameter_id, value):
    assert parameter_id(value, "payload") is None


def test_bytes_and_text_ids_are_distinct_and_surrogates_supported(parameter_id):
    assert parameter_id("x" * 129, "payload") != parameter_id(b"x" * 129, "payload")
    assert len(parameter_id("\ud800" * 129, "payload")) <= 80
