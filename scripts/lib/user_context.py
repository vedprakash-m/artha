#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/lib/user_context.py — Single loader for config/user_context.yaml.

ARCHITECTURE INVARIANT: This is the SOLE source of truth for user_context.yaml.
Do NOT load user_context.yaml in any other module — always import from here.

Supports cache invalidation for --apply-suggestions (which mutates the YAML).

Ref: specs/action-convert.md §4.3.1 CONSTRAINT 1

Exports:
    load_user_context(artha_dir: Path) -> dict
    invalidate_user_context_cache() -> None
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

# Module-level cache — same pattern as _CACHED_ROUTING in action_composer.py
_CACHED_UC: dict[str, Any] | None = None
_CACHED_UC_PATH: Path | None = None


def invalidate_user_context_cache() -> None:
    """Invalidate the cached user_context.yaml data.

    Call after --apply-suggestions mutates user_context.yaml so the next
    load_user_context() call reads fresh data from disk.
    """
    global _CACHED_UC, _CACHED_UC_PATH  # noqa: PLW0603
    _CACHED_UC = None
    _CACHED_UC_PATH = None


def load_user_context(artha_dir: Path) -> dict[str, Any]:
    """Load config/user_context.yaml, cached across calls within a process.

    Uses deep copy on return so callers cannot mutate the shared cache.
    Returns {} on any error — never raises.

    Args:
        artha_dir: Path to the Artha workspace root directory.

    Returns:
        Parsed YAML dict, or {} if file is absent or unparseable.
    """
    global _CACHED_UC, _CACHED_UC_PATH  # noqa: PLW0603
    config_path = artha_dir / "config" / "user_context.yaml"

    if _CACHED_UC is not None and _CACHED_UC_PATH == config_path:
        return copy.deepcopy(_CACHED_UC)

    try:
        import yaml  # type: ignore[import]
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            _CACHED_UC = raw
            _CACHED_UC_PATH = config_path
            return copy.deepcopy(_CACHED_UC)
    except Exception:  # noqa: BLE001
        pass

    # Return empty dict on failure; do NOT cache failure so next call retries
    return {}
