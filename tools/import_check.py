#!/usr/bin/env python3
"""Fail on ``from scripts.X import ...`` inside ``scripts/`` (dual-import bug).

Unlike the shell one-liner it replaces, this check

* fails on any file that cannot be parsed,
* fails when it finds no Python files at all (an empty scan is not a pass),
* never swallows its own errors.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def scan(root: Path) -> tuple[list[str], list[str], int]:
    hits: list[str] = []
    errors: list[str] = []
    count = 0
    for path in sorted(root.rglob("*.py")):
        parts = set(path.parts)
        if "__pycache__" in parts or ".archive" in parts:
            continue
        count += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
            errors.append(f"{path}: cannot parse ({exc})")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("scripts."):
                hits.append(f"{path}:{node.lineno}: from {node.module}")
    return hits, errors, count


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("scripts")
    hits, errors, count = scan(root)
    if count == 0:
        print(f"ERROR: no Python files found under {root}/ — refusing to report a clean scan")
        return 1
    if errors:
        print("ERROR: files that could not be parsed:")
        print("\n".join(errors))
    if hits:
        print("ERROR: 'from scripts.X' imports found inside scripts/:")
        print("\n".join(hits))
        print("Use 'from X import ...' instead (scripts/ is on sys.path).")
    if errors or hits:
        return 1
    print(f"✓ No dual-import patterns ({count} files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
