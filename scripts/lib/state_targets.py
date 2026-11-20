#!/usr/bin/env python3
"""Registered Markdown State File targets used by Light mode."""
from __future__ import annotations

import re
from pathlib import Path

_PATH_ENTRY = re.compile(
    r"""^\s*-\s+path:\s*["']([^"']+)["']\s*(?:#.*)?$""",
    re.MULTILINE,
)


def registered_state_paths(root: Path) -> tuple[Path, ...]:
    """Return exact registered paths without requiring PyYAML in hook processes."""
    registry = Path(root) / "config" / "state_registry.yaml"
    if not registry.is_file():
        return ()
    paths: list[Path] = []
    for match in _PATH_ENTRY.finditer(registry.read_text(encoding="utf-8")):
        value = match.group(1)
        if "*" not in value and "?" not in value and not value.startswith("~"):
            paths.append(Path(root) / value)
    return tuple(dict.fromkeys(paths))


def light_markdown_targets(root: Path) -> tuple[Path, ...]:
    """Return the explicit personal Markdown allowlist used by Light mode."""
    root = Path(root)
    allowlist = root / "config" / "light_mode.yaml"
    if allowlist.is_file():
        values = []
        for line in allowlist.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^\s*-\s+(state/[^\s#]+\.md(?:\.age)?)\s*(?:#.*)?$", line)
            if match:
                value = match.group(1)
                if value.startswith("state/work/"):
                    continue
                values.append(root / value)
        return tuple(sorted(dict.fromkeys(values), key=lambda path: path.as_posix()))

    # Fail closed to the prior narrow core rather than silently covering all State Files.
    names = (
        "goals.md", "open_items.md", "calendar.md", "health-check.md",
        "memory.md", "decisions.md", "scenarios.md", "planning_signals.md",
    )
    targets = []
    for name in names:
        target = root / "state" / name
        if target in registered_state_paths(root):
            targets.append(target)
    return tuple(targets)


def is_light_markdown_target(path: Path, root: Path) -> bool:
    """Return whether *path* is on the registry-derived personal Markdown allowlist."""
    try:
        candidate = Path(path).resolve()
    except OSError:
        candidate = Path(path).absolute()
    return candidate in {target.resolve() for target in light_markdown_targets(Path(root))}


def relative_name(path: Path, root: Path) -> str:
    """Return a stable forward-slash workspace path."""
    return Path(path).absolute().relative_to(Path(root).absolute()).as_posix()
