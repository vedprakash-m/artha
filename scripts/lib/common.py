# pii-guard: ignore-file — shared infrastructure; no personal data
"""
scripts/lib/common.py — Canonical ARTHA_DIR and shared path constants.

All scripts that need ARTHA_DIR should import from here rather than
independently re-deriving it so that path logic is a single source of truth.

Usage:
    from scripts.lib.common import ARTHA_DIR, SCRIPTS_DIR, STATE_DIR, CONFIG_DIR
    from scripts.lib.common import parse_frontmatter

Ref: remediation.md §8, Issue M11, Issue M13, standardization.md §3.1
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# Canonical project root — two levels up from scripts/lib/common.py
ARTHA_DIR: Path = Path(__file__).resolve().parent.parent.parent

SCRIPTS_DIR: Path = ARTHA_DIR / "scripts"
STATE_DIR:   Path = ARTHA_DIR / "state"
CONFIG_DIR:  Path = ARTHA_DIR / "config"
TMP_DIR:     Path = ARTHA_DIR / "tmp"


def parse_frontmatter(path: Path) -> dict[str, Any]:
    """Extract YAML frontmatter from a markdown file. Returns {} on failure.

    Public accessor for kb_lint.py and other cross-domain tools.
    The private _parse_frontmatter in dq_gate.py is kept intact for test
    compatibility; this function has identical behaviour.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    try:
        import yaml  # noqa: PLC0415
        result = yaml.safe_load(fm_text)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def update_channel_health_md(
    channel: str,
    healthy: bool,
    last_push: str | None = None,
    push_count_today: int | None = None,
) -> None:
    """Deprecated — import from health_check_updater instead."""
    from health_check_updater import update_channel_health_md as _fn
    _fn(channel, healthy, last_push=last_push, push_count_today=push_count_today)
