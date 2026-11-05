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
