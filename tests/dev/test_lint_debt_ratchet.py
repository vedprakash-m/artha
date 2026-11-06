"""Audit M3 (2026-11-05): lint-suppression and broad-except debt may only go down.

Counts are lines in tracked public `scripts/**/*.py` (private Work sources are not tracked). When a count
drops, lower its ceiling in the same change so the gain is kept.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CEILINGS = {"noqa": 1032, "type: ignore": 336, "broad except": 1022}
PATTERNS = {"noqa": re.compile(r"noqa"), "type: ignore": re.compile(r"type: ignore"),
            "broad except": re.compile(r"except Exception\b|except:")}


def _tracked_scripts() -> list[Path]:
    try:
        listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "scripts/*.py"], capture_output=True,
                                text=True, check=True, timeout=30).stdout.split()
    except (OSError, subprocess.SubprocessError):
        # Isolated test workspaces contain only tracked files and may have no git metadata.
        return sorted(path for path in (ROOT / "scripts").rglob("*.py") if "__pycache__" not in path.parts)
    return [ROOT / name for name in listed]


def test_lint_debt_does_not_grow():
    counts = dict.fromkeys(PATTERNS, 0)
    for path in _tracked_scripts():
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    counts[name] += 1
    grown = {name: (counts[name], CEILINGS[name]) for name in CEILINGS if counts[name] > CEILINGS[name]}
    assert not grown, f"lint debt grew (count, ceiling): {grown}"
