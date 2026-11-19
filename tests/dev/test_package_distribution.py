"""The public checkout must build without private packages or missing core resources."""
from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
PACKAGES = CONFIG["tool"]["setuptools"]["packages"]


@pytest.mark.parametrize("package", PACKAGES)
def test_declared_package_exists_in_the_isolated_public_checkout(package):
    directory = ROOT.joinpath(*package.split("."))
    assert (directory / "__init__.py").is_file(), f"Missing public package: {package}"


def test_public_python_subpackages_are_included():
    actual = {
        ".".join(path.parent.relative_to(ROOT).parts)
        for path in (ROOT / "scripts").rglob("__init__.py")
    }
    assert actual <= set(PACKAGES), f"Unpackaged public modules: {sorted(actual - set(PACKAGES))}"


def test_core_runtime_resources_are_included():
    core = ROOT / "scripts" / "catchup_core"
    patterns = CONFIG["tool"]["setuptools"]["package-data"]["scripts.catchup_core"]
    resources = [
        path.relative_to(core).as_posix()
        for folder in ("migrations", "packs", "prompts")
        for path in (core / folder).iterdir()
        if path.is_file() and path.suffix in (".sql", ".yaml", ".md")
    ]
    assert resources
    missing = [name for name in resources if not any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)]
    assert not missing, f"Missing core package data: {missing}"


def test_chromium_reader_is_pinned_to_an_immutable_commit():
    import re

    dependency = next(
        item for item in CONFIG["project"]["optional-dependencies"]["messaging"]
        if item.startswith("ccl_chromium_reader @ ")
    )
    assert re.search(r"\.git@[0-9a-f]{40}$", dependency), dependency


def test_whatsapp_connector_imports_without_optional_chromium_reader():
    import builtins

    blocked = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("ccl_chromium_reader"):
            raise ImportError("optional dependency unavailable")
        return blocked(name, *args, **kwargs)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(builtins, "__import__", guarded_import)
        from connectors import whatsapp_local

        assert list(
            whatsapp_local._fetch_windows_idb(
                since_dt=whatsapp_local.datetime.now(whatsapp_local.timezone.utc),
                max_results=1,
                source_tag="whatsapp",
                include_groups=True,
            )
        ) == []


def test_whatsapp_health_degrades_without_optional_chromium_reader(monkeypatch):
    from connectors import whatsapp_local

    monkeypatch.setattr(whatsapp_local.platform, "system", lambda: "Windows")
    monkeypatch.setattr(whatsapp_local, "_WIN_IDB_DIR", Path("synthetic-indexeddb"))
    monkeypatch.setitem(__import__("sys").modules, "ccl_chromium_reader", None)
    assert whatsapp_local.health_check({}) is False
