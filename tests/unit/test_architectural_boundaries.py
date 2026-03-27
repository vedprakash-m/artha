"""tests/unit/test_architectural_boundaries.py — Phase 1: DAG enforcement.

Verifies no cross-domain imports between work/, channel/, and preflight/
subpackages using AST analysis of import statements.

Ref: specs/pay-debt.md §3.1, §5.2 T1-10
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Root of the scripts directory
SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"

# Domain subpackages that must NOT import each other
DOMAIN_PACKAGES = {"work", "channel", "preflight"}


def _get_imports(source_path: Path) -> list[str]:
    """Return all module names imported in a Python file."""
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.append(node.module)
    return imported


def _package_of(module_name: str) -> str | None:
    """Return the domain package name if the module is in a domain subpackage."""
    # e.g. "work.helpers" → "work", "channel.router" → "channel"
    parts = module_name.split(".")
    if parts[0] in DOMAIN_PACKAGES:
        return parts[0]
    return None


def test_no_cross_domain_imports():
    """No domain subpackage (work/, channel/, preflight/) imports another.

    Architectural boundary rule from specs/pay-debt.md §3.1:
      work/* must NOT import channel/*, channel/* must NOT import work/*, etc.
    """
    violations: list[str] = []

    for pkg in DOMAIN_PACKAGES:
        pkg_dir = SCRIPTS_DIR / pkg
        if not pkg_dir.exists():
            continue  # Package doesn't exist yet — skip (will matter post-Phase 3/4/5)

        for py_file in pkg_dir.rglob("*.py"):
            imports = _get_imports(py_file)
            for imp in imports:
                target_pkg = _package_of(imp)
                if target_pkg is not None and target_pkg != pkg:
                    rel = py_file.relative_to(SCRIPTS_DIR)
                    violations.append(
                        f"{rel} imports {imp!r} (cross-domain: {pkg} → {target_pkg})"
                    )

    assert violations == [], (
        "Cross-domain imports detected (violates DAG from specs/pay-debt.md §3.1):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )
