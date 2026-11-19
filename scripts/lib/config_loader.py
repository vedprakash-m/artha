"""Centralized YAML config loader with isolated, revision-aware caching.

Usage:
    from lib.config_loader import load_config
    cfg = load_config("connectors")  # loads config/connectors.yaml

All canonical config file names are listed in ``_CONFIG_FILES`` below.

**Cache contract:** ``load_config()`` returns a deep copy of the cached
dict on every call. Parsed values are keyed by the exact file bytes, so
process-lifetime tools observe a relevant configuration revision without
sharing mutable nested values. Returns empty dict on error (logged, never raises).
``strict=True`` propagates read/parse errors and rejects non-mapping roots for
health checks that must distinguish unreadable configuration from disabled features.
Strict and best-effort reads use separate cache entries; exceptions are not cached.

**Test isolation:** Use the ``invalidate()`` function (or the ``autouse``
fixture added to ``tests/conftest.py``) to clear cache state between tests.

**Infrastructure exclusion:** ``pipeline.py``'s ``load_connectors_config()``
and ``action_executor.py``'s ``_derive_action_map()`` are NOT migrated to
this loader — they retain frozen fallback dicts for safety-critical routing.
Existing callers default to best-effort configuration where empty-dict degradation
is acceptable; prerequisite checks opt into strict reads.

Ref: specs/pay-debt-reloaded.md §4 WS-2
"""
from __future__ import annotations

import copy
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
    "guardrails":     "guardrails.yaml",
}


@lru_cache(maxsize=16)
def _load_config_cached(
    name: str, _config_dir: str | None, strict: bool, source: bytes | None,
) -> dict[str, Any]:
    """Internal: load YAML and cache the result dict.

    Called by load_config() which wraps each call in dict() to return a
    distinct shallow copy.  Do not call directly.
    """
    filename = _CONFIG_FILES.get(name)
    if filename is None:
        if strict:
            raise ValueError("unknown configuration name")
        log.warning("config_loader: unknown config name: %s", name)
        return {}
    config_dir = _config_dir or str(get_config()["CONFIG_DIR"])
    path = Path(config_dir) / filename
    if source is None:
        if strict:
            raise FileNotFoundError(path)
        log.debug("config_loader: config file not found: %s", path)
        return {}
    try:
        data = yaml.safe_load(source.decode("utf-8"))
        if strict and not isinstance(data, dict):
            raise ValueError("configuration root must be a mapping")
        return dict(data) if isinstance(data, dict) else {}
    except Exception as exc:
        if strict:
            raise
        log.warning("config_loader: failed to load %s: %s", path, exc)
        return {}


def load_config(
    name: str, _config_dir: str | None = None, *, strict: bool = False,
) -> dict[str, Any]:
    """Load and cache a config file by canonical name.

    Returns a deep copy of the cached dict on each call, so callers
    may mutate nested values without corrupting the cache. Returns empty
    dict on error (logged, never raises).  Config is cached for the
    lifetime of the process.
    With ``strict=True``, read/parse errors propagate and non-mapping roots raise
    ``ValueError``. Successful strict reads retain caching and shallow-copy behavior.

    Exact source bytes participate in the cache key, so a relevant file
    revision is parsed automatically. ``_config_dir`` is also a cache key so test
    fixtures pointing at a temporary directory produce a distinct cache
    entry instead of silently returning stale data from the default
    ``CONFIG_DIR``.  Production callers should omit it.
    """
    filename = _CONFIG_FILES.get(name)
    if filename is None:
        return copy.deepcopy(_load_config_cached(name, _config_dir, strict, None))
    config_dir = _config_dir or str(get_config()["CONFIG_DIR"])
    path = Path(config_dir) / filename
    try:
        with open(path, "rb") as stream:
            source = stream.read()
    except FileNotFoundError:
        source = None
    except Exception:
        if strict:
            raise
        log.warning("config_loader: failed to read %s", path)
        return {}
    return copy.deepcopy(_load_config_cached(name, _config_dir, strict, source))


def invalidate(name: str | None = None) -> None:
    """Clear cache for one config or all configs.

    Primarily for test fixtures.  ``lru_cache`` does not support per-key
    invalidation, so both ``invalidate(name)`` and ``invalidate()`` clear
    the entire cache and let callers re-populate on next access.
    """
    _load_config_cached.cache_clear()
