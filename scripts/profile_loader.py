#!/usr/bin/env python3
"""
profile_loader.py — Singleton profile access for Artha scripts.

Reads config/user_profile.yaml and exposes the data via a simple dot-notation API.
All scripts that need personal configuration import from this module instead of
hardcoding values.

Usage:
    from scripts.profile_loader import get, children, has_profile, enabled_domains

    name = get("family.primary_user.name", "User")
    kids = children()
    if has_profile():
        ...

The profile is cached after first load. Call reload_profile() after editing
user_profile.yaml (e.g., after /bootstrap populates new fields).
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

_ARTHA_DIR = Path(__file__).resolve().parent.parent
_PROFILE_PATH = _ARTHA_DIR / "config" / "user_profile.yaml"


@lru_cache(maxsize=1)
def load_profile() -> dict:
    """Load user_profile.yaml. Returns empty dict if file does not exist."""
    if not _PROFILE_PATH.exists():
        return {}
    try:
        import yaml  # noqa: PLC0415 — imported here so stdlib-only scripts can still import the module header
    except ImportError:
        # yaml not available (pre-venv context). Return empty — caller should
        # handle gracefully or the script will re-exec inside the venv first.
        return {}
    with open(_PROFILE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def reload_profile() -> dict:
    """Clear cache and reload from disk. Call after profile edits (e.g., /bootstrap)."""
    load_profile.cache_clear()
    return load_profile()


def get(key_path: str, default: Any = None) -> Any:
    """Dot-notation access to nested profile fields.

    Examples:
        get("family.primary_user.name", "User")
        get("location.timezone", "America/Los_Angeles")
        get("integrations.gmail.enabled", False)
    """
    data = load_profile()
    for key in key_path.split("."):
        if isinstance(data, dict):
            data = data.get(key)
        else:
            return default
        if data is None:
            return default
    return data


def children() -> list:
    """Return list of children dicts, or empty list if none configured."""
    return get("family.children", [])


def enabled_domains() -> list:
    """Return list of domain names that are enabled in the profile."""
    domains = get("domains", {})
    return [
        name
        for name, cfg in domains.items()
        if isinstance(cfg, dict) and cfg.get("enabled", False)
    ]


def has_profile() -> bool:
    """Check if user_profile.yaml exists on disk."""
    return _PROFILE_PATH.exists()


def schema_version() -> str:
    """Return profile schema version, or '0.0' if no profile present."""
    return get("schema_version", "0.0")


def require_profile() -> dict:
    """Load profile or exit with a clear setup message if missing.

    Call this at the top of any script that strictly requires the profile.
    """
    if not has_profile():
        print(
            "ERROR: config/user_profile.yaml not found.\n"
            "Run '/bootstrap' to create your profile, or copy\n"
            "config/user_profile.example.yaml to config/user_profile.yaml\n"
            "and fill in your details.",
            file=sys.stderr,
        )
        sys.exit(1)
    return load_profile()


def artha_dir() -> Path:
    """Return the resolved Artha root directory."""
    return _ARTHA_DIR
