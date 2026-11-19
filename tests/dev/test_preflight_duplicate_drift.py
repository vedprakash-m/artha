"""RF-17: the CLI is thin; every preflight check has one package owner."""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _functions(path: Path) -> set[str]:
    return {
        node.name
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
    }


def test_script_is_a_thin_entry_point_without_check_definitions():
    assert _functions(SCRIPTS / "preflight.py") == set()


def test_every_package_function_has_one_owner():
    owners: dict[str, list[Path]] = defaultdict(list)
    for path in sorted((SCRIPTS / "preflight").glob("*.py")):
        for name in _functions(path):
            owners[name].append(path)
    duplicates = {
        name: paths
        for name, paths in owners.items()
        if len(paths) > 1
    }
    assert duplicates == {}, duplicates
