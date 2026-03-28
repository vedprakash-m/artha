"""Centralized YAML config loader with process-lifetime cache.

Usage:
    from lib.config_loader import load_config
    cfg = load_config("connectors")  # loads config/connectors.yaml

All canonical config file names are listed in ``_CONFIG_FILES`` below.

**Cache contract:** ``load_config()`` returns a shallow copy of the cached
dict on every call, so callers may mutate their copy without corrupting the
cache.  Returns empty dict on error (logged, never raises).  Cache lifetime
equals the process lifetime — one CLI invocation.

**Test isolation:** Use the ``invalidate()`` function (or the ``autouse``
fixture added to ``tests/conftest.py``) to clear cache state between tests.

**Infrastructure exclusion:** ``pipeline.py``'s ``load_connectors_config()``
and ``action_executor.py``'s ``_derive_action_map()`` are NOT migrated to
this loader — they retain frozen fallback dicts for safety-critical routing.
This loader is for best-effort configuration where empty-dict degradation
is acceptable.

Ref: specs/pay-debt-reloaded.md §4 WS-2
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from foundation import get_config

log = logging.getLogger("artha.config_loader")

# Canonical config file names → filenames in config/
_CONFIG_FILES: dict[str, str] = {
    "user_profile":   "user_profile.yaml",
    "connectors":     "connectors.yaml",
    "artha_config":   "artha_config.yaml",
    "channels":       "channels.yaml",
    "actions":        "actions.yaml",
    "domain_registry":"domain_registry.yaml",
    "skills":         "skills.yaml",
    "patterns":       "patterns.yaml",
    "routing":        "routing.yaml",
    "signal_routing": "signal_routing.yaml",
}


@lru_cache(maxsize=16)
def _load_config_cached(name: str, _config_dir: str | None = None) -> dict[str, Any]:
    """Internal: load YAML and cache the result dict.

    Called by load_config() which wraps each call in dict() to return a
    distinct shallow copy.  Do not call directly.
    """
    filename = _CONFIG_FILES.get(name)
    if filename is None:
        log.warning("config_loader: unknown config name: %s", name)
        return {}
    config_dir = _config_dir or str(get_config()["CONFIG_DIR"])
    path = Path(config_dir) / filename
    if not path.exists():
        log.debug("config_loader: config file not found: %s", path)
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return dict(data) if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("config_loader: failed to load %s: %s", path, exc)
        return {}


def load_config(name: str, _config_dir: str | None = None) -> dict[str, Any]:
    """Load and cache a config file by canonical name.

    Returns a shallow copy of the cached dict on each call, so callers
    may mutate their copy without corrupting the cache.  Returns empty
    dict on error (logged, never raises).  Config is cached for the
    lifetime of the process.

    ``_config_dir`` is exposed as a cache-key parameter so that test
    fixtures pointing at a temporary directory produce a distinct cache
    entry instead of silently returning stale data from the default
    ``CONFIG_DIR``.  Production callers should omit it.
    """
    return dict(_load_config_cached(name, _config_dir))


def invalidate(name: str | None = None) -> None:
    """Clear cache for one config or all configs.

    Primarily for test fixtures.  ``lru_cache`` does not support per-key
    invalidation, so both ``invalidate(name)`` and ``invalidate()`` clear
    the entire cache and let callers re-populate on next access.
    """
    _load_config_cached.cache_clear()
