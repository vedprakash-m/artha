# pii-guard: ignore-file — shared infrastructure; no personal data
"""
scripts/lib/common.py — Canonical ARTHA_DIR and shared path constants.

All scripts that need ARTHA_DIR should import from here rather than
independently re-deriving it so that path logic is a single source of truth.

Usage:
    from scripts.lib.common import ARTHA_DIR, SCRIPTS_DIR, STATE_DIR, CONFIG_DIR

Ref: remediation.md §8, Issue M11, Issue M13, standardization.md §3.1
"""
from __future__ import annotations

from pathlib import Path

# Canonical project root — two levels up from scripts/lib/common.py
ARTHA_DIR: Path = Path(__file__).resolve().parent.parent.parent

SCRIPTS_DIR: Path = ARTHA_DIR / "scripts"
STATE_DIR:   Path = ARTHA_DIR / "state"
CONFIG_DIR:  Path = ARTHA_DIR / "config"
TMP_DIR:     Path = ARTHA_DIR / "tmp"
