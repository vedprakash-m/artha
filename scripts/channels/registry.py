# pii-guard: ignore-file — registry infrastructure; no personal data
"""
scripts/channels/registry.py — Channel registry loader.

Mirrors pipeline.py/_load_handler and load_connectors_config patterns exactly.
Handles: config loading, adapter path validation, adapter instantiation.

Security: adapter paths are validated to be under scripts/channels/ before
importlib loading — prevents arbitrary module execution via config injection.

Ref: specs/conversational-bridge.md §4 (Registry Loader Contract)
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

# Ensure Artha root is on sys.path for relative imports
_ARTHA_DIR = Path(__file__).resolve().parent.parent.parent
if str(_ARTHA_DIR) not in sys.path:
    sys.path.insert(0, str(_ARTHA_DIR))

# Security: only allow adapter modules under this prefix
_ALLOWED_PREFIX = "scripts/channels/"


def load_adapter_module(adapter_path: str):
    """Dynamically import a channel adapter module.

    Mirrors pipeline.py/_load_handler pattern exactly.

    Security: validates adapter_path starts with scripts/channels/ before loading.
    This prevents config-injection attacks where a malicious channels.yaml
    could point to an arbitrary module path.

    Args:
        adapter_path: Relative path like "scripts/channels/telegram.py"

    Returns:
        The imported module object.

    Raises:
        ValueError: if path is outside the allowed prefix
        ImportError: if the module cannot be found or imported
    """
    # Normalize: strip leading ./ or .\, unify separators
    norm = adapter_path.replace("\\", "/").lstrip("./")
    if not norm.startswith(_ALLOWED_PREFIX.lstrip("./")):
        raise ValueError(
            f"Channel adapter must be under {_ALLOWED_PREFIX}: {adapter_path!r}. "
            "Only adapters in scripts/channels/ are permitted."
        )
    module_name = norm.replace("/", ".").removesuffix(".py")
    return importlib.import_module(module_name)


def load_channels_config() -> dict[str, Any]:
    """Load config/channels.yaml.

    Mirrors pipeline.py/load_connectors_config behavior:
    - Returns a safe empty/disabled config if channels.yaml does not exist
    - Never raises on missing file (channels are optional)

    Returns:
        Parsed YAML dict. Always has at least:
        {"defaults": {"push_enabled": False}, "channels": {}}
    """
    _SAFE_DEFAULT: dict[str, Any] = {
        "defaults": {"push_enabled": False},
        "channels": {},
    }

    from lib.common import CONFIG_DIR
    channels_file = CONFIG_DIR / "channels.yaml"

    if not channels_file.exists():
        return _SAFE_DEFAULT

    try:
        import yaml
    except ImportError:
        return _SAFE_DEFAULT  # pyyaml not installed — channels unavailable

    try:
        with open(channels_file, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else _SAFE_DEFAULT
    except Exception:
        return _SAFE_DEFAULT


def create_adapter_from_config(channel_name: str, channel_cfg: dict[str, Any]):
    """Create an adapter instance from a channel config entry.

    Loads the adapter module, calls its create_adapter() factory, and returns
    the resulting adapter object.

    Args:
        channel_name: e.g. "telegram", "discord"
        channel_cfg:  Dict from channels.yaml → channels → {name}

    Returns:
        An object implementing the ChannelAdapter protocol.

    Raises:
        ValueError: if adapter path validation fails
        ImportError: if adapter module cannot be imported
        RuntimeError: if create_adapter() fails (e.g., missing credentials)
    """
    adapter_path = channel_cfg.get("adapter", f"scripts/channels/{channel_name}.py")
    auth_cfg = channel_cfg.get("auth", {})
    credential_key = auth_cfg.get(
        "credential_key", f"artha-{channel_name}-bot-token"
    )
    retry_cfg = channel_cfg.get("retry", {})

    module = load_adapter_module(adapter_path)
    return module.create_adapter(
        credential_key=credential_key,
        retry_max=retry_cfg.get("max_retries", 3),
        retry_base_delay=retry_cfg.get("base_delay", 2.0),
        retry_max_delay=retry_cfg.get("max_delay", 30.0),
    )


def iter_enabled_channels(config: dict[str, Any]):
    """Yield (channel_name, channel_cfg) for all enabled channels.

    Args:
        config: Full channels.yaml dict from load_channels_config()

    Yields:
        Tuples of (str, dict) for enabled channels.
    """
    for name, cfg in config.get("channels", {}).items():
        if isinstance(cfg, dict) and cfg.get("enabled", False):
            yield name, cfg
