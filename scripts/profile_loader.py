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
_SCHEMA_PATH = _ARTHA_DIR / "config" / "user_profile.schema.json"


def _validate_against_schema(data: dict) -> list[str]:
    """Validate profile data against user_profile.schema.json.
    Returns list of validation error messages (empty if valid).
    """
    if not _SCHEMA_PATH.exists():
        return []  # schema not present — skip validation
    try:
        import json
        import jsonschema  # noqa: PLC0415
    except ImportError:
        return []  # jsonschema not installed — skip silently
    try:
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            schema = json.load(f)
        jsonschema.validate(instance=data, schema=schema)
        return []
    except jsonschema.ValidationError as exc:
        return [f"Profile validation error at {'.'.join(str(p) for p in exc.absolute_path)}: {exc.message}"]
    except Exception as exc:
        return [f"Schema validation failed: {exc}"]


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
    Validates against JSON schema if available.
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
    data = load_profile()
    errors = _validate_against_schema(data)
    if errors:
        print("WARNING: Profile validation issues:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
    return data


def artha_dir() -> Path:
    """Return the resolved Artha root directory."""
    return _ARTHA_DIR


# ── Domain registry helpers ───────────────────────────────────────────────────

_REGISTRY_PATH = _ARTHA_DIR / "config" / "domain_registry.yaml"


@lru_cache(maxsize=1)
def domain_registry() -> dict:
    """Return the full domain registry dict (schema_version + domains map).

    Loads config/domain_registry.yaml. Returns empty dict when file is absent
    (e.g., first-run before bootstrap). Result is cached for the process lifetime;
    call domain_registry.cache_clear() after editing the registry on disk.
    """
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return {}
    with open(_REGISTRY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def available_domains(*, household_type: str | None = None) -> list[dict]:
    """Return list of domain dicts that are applicable to this household.

    Each item is the registry entry merged with its key name under "name":
        {"name": "immigration", "label": "...", "always_load": True, ...}

    Args:
        household_type: Optional override. If None, reads from profile.
            When neither is available, all household_types are included.

    Filtering rules:
        - Domains with phase "phase_2" or later are excluded until implemented.
        - household_types list in registry: if present, domain is included only
          when the household_type matches one of the listed values.
        - Domains with enabled_by_default=True have their profile 'enabled'
          flag defaulted to True when the profile omits them.
    """
    registry = domain_registry()
    domains_map: dict = registry.get("domains", {})
    if not domains_map:
        # Fall back to profile-only enabled list
        return [{"name": n} for n in enabled_domains()]

    ht = household_type or get("household.type")

    result = []
    for name, cfg in domains_map.items():
        if not isinstance(cfg, dict):
            continue
        phase = cfg.get("phase", "existing")
        # Only include currently implemented phases
        if phase not in ("existing", "phase_1a", "phase_1b"):
            continue
        # Household type filter
        allowed_households: list = cfg.get("household_types", [])
        if ht and allowed_households and ht not in allowed_households:
            continue
        result.append({"name": name, **cfg})

    return result


def toggle_domain(domain_name: str, *, enabled: bool) -> bool:
    """Enable or disable a domain in user_profile.yaml.

    Updates the ``domains.<domain_name>.enabled`` flag in-place and flushes
    the profile cache so subsequent calls to enabled_domains() reflect the
    change immediately.

    Args:
        domain_name: Domain key as used in domain_registry.yaml (e.g. "pets").
        enabled: True to enable, False to disable.

    Returns:
        True if the profile was written successfully, False on error.

    Raises:
        FileNotFoundError: If user_profile.yaml does not exist (never bootstrapped).
    """
    if not _PROFILE_PATH.exists():
        raise FileNotFoundError(f"Profile not found: {_PROFILE_PATH}")
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return False

    with open(_PROFILE_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if "domains" not in data or not isinstance(data["domains"], dict):
        data["domains"] = {}
    if domain_name not in data["domains"] or not isinstance(data["domains"][domain_name], dict):
        data["domains"][domain_name] = {}

    data["domains"][domain_name]["enabled"] = enabled

    with open(_PROFILE_PATH, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # Flush cache so callers see the updated state immediately
    load_profile.cache_clear()
    return True


# ── Household helpers ─────────────────────────────────────────────────────────

#: Valid household type identifiers
HOUSEHOLD_TYPES = ("single", "couple", "family", "multi_gen", "roommates")


def household_type() -> str | None:
    """Return the household type from the profile, or None if not set.

    Valid values: ``single``, ``couple``, ``family``, ``multi_gen``, ``roommates``
    """
    return get("household.type")


def is_single_person_mode() -> bool:
    """Return True when Artha should operate in single-person mode.

    Single-person mode is active when:
    - ``household.type`` is ``"single"``, OR
    - ``household.single_person_mode`` is explicitly ``True``

    In single-person mode:
    - Spouse/partner references are suppressed from briefings
    - ``kids`` domain is excluded unless explicitly enabled
    - "family perspective" language is replaced with "personal" tone
    """
    explicit = get("household.single_person_mode")
    if explicit is True:
        return True
    return household_type() == "single"
