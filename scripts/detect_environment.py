#!/usr/bin/env python3
"""
detect_environment.py — Artha runtime environment detection
============================================================
Probes the runtime environment and returns a JSON capability manifest.
This is a pure sensing module — it reports facts, makes no decisions.

Usage:
  python scripts/detect_environment.py           # prints JSON manifest
  python scripts/detect_environment.py --debug   # includes raw detection signals
  python scripts/detect_environment.py --no-cache  # force fresh probe (ignores cache)

Output JSON structure:
  {
    "environment": "local_mac" | "local_windows" | "local_linux" | "cowork_vm" | "unknown",
    "capabilities": {
      "filesystem_writable": bool,
      "age_installed": bool,
      "keyring_functional": bool,
      "network_google": bool,
      "network_microsoft": bool,
      "network_apple": bool
    },
    "degradations": ["vault_decrypt_unavailable", ...],
    "probed_at": "<ISO timestamp>"
  }

Cache: tmp/.env_manifest.json — 5-minute TTL. Cold probe cost: ~2s worst case.

Ref: specs/vm-hardening.md Phase 1
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ARTHA_DIR = Path(__file__).resolve().parent.parent
_STATE_DIR  = _ARTHA_DIR / "state"
_TMP_DIR    = _ARTHA_DIR / "tmp"
_CACHE_FILE = _TMP_DIR / ".env_manifest.json"
_CACHE_TTL_SECONDS = 300  # 5 minutes

_NETWORK_TIMEOUT = 3  # seconds, per TCP probe

# Cowork VM detection signals
_COWORK_MARKER_PATH = "/var/cowork"
_COWORK_ENV_VAR     = "COWORK_SESSION_ID"


# ---------------------------------------------------------------------------
# Data contract
# ---------------------------------------------------------------------------

@dataclass
class EnvironmentManifest:
    """Immutable snapshot of runtime capabilities."""
    environment: str          # "local_mac" | "local_windows" | "local_linux" | "cowork_vm" | "unknown"
    capabilities: dict        # capability flags
    degradations: list        # human-readable degradation strings
    detection_signals: dict   # raw probe values (populated in debug mode)
    probed_at: str            # ISO timestamp

    def to_dict(self, include_signals: bool = False) -> dict:
        d = {
            "environment":  self.environment,
            "capabilities": self.capabilities,
            "degradations": self.degradations,
            "probed_at":    self.probed_at,
        }
        if include_signals:
            d["detection_signals"] = self.detection_signals
        return d


# ---------------------------------------------------------------------------
# Probes — each returns (result: bool, raw_value: str)
# ---------------------------------------------------------------------------

def _probe_cowork_marker() -> tuple[bool, str]:
    """Check for Cowork VM marker directory or env var."""
    if os.path.isdir(_COWORK_MARKER_PATH):
        return True, f"dir:{_COWORK_MARKER_PATH}"
    session_id = os.environ.get(_COWORK_ENV_VAR, "")
    if session_id:
        return True, f"env:{_COWORK_ENV_VAR}={session_id[:8]}..."
    return False, "absent"


def _probe_filesystem_writable() -> tuple[bool, str]:
    """Check if state/ is writable via a transient write probe."""
    probe_path = _STATE_DIR / ".env_write_probe"
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        probe_path.write_text("probe", encoding="utf-8")
        # Cleanup is best-effort — some mounts (e.g. Cowork VM FUSE mounts)
        # allow writes but not deletes. A delete failure does NOT mean the
        # filesystem is read-only; Artha's state updates use write_text on
        # existing files, which works fine on such mounts.
        try:
            probe_path.unlink()
        except (OSError, PermissionError):
            pass
        return True, "writable"
    except (OSError, PermissionError) as exc:
        return False, f"read_only:{exc.__class__.__name__}"


def _probe_age_installed() -> tuple[bool, str]:
    """Check if the 'age' encryption tool is on PATH."""
    path = shutil.which("age")
    return bool(path), path or "not_found"


def _probe_keyring_functional() -> tuple[bool, str]:
    """Check if keyring has a working backend (import + read attempt)."""
    try:
        import keyring as _kr
        # A None result is fine (key not stored) — what we detect is a backend failure
        _kr.get_password("artha-env-probe", "detect_environment")
        return True, "functional"
    except ImportError:
        return False, "keyring_not_installed"
    except Exception as exc:
        return False, f"backend_error:{exc.__class__.__name__}"


def _probe_network(host: str, port: int) -> tuple[bool, str]:
    """TCP SYN probe. No data sent, no credentials transmitted."""
    try:
        with socket.create_connection((host, port), timeout=_NETWORK_TIMEOUT):
            return True, f"reachable:{host}:{port}"
    except (OSError, socket.timeout) as exc:
        return False, f"blocked:{exc.__class__.__name__}"


# ---------------------------------------------------------------------------
# Classification — defensive: ambiguity defaults to strict mode
# ---------------------------------------------------------------------------

def _classify_environment(signals: dict) -> str:
    """Classify the runtime environment from probe signals."""
    if signals.get("cowork_marker"):
        return "cowork_vm"

    sys_platform = signals.get("platform", "")

    if sys_platform == "Darwin":
        return "local_mac"
    elif sys_platform == "Windows":
        return "local_windows"
    elif sys_platform == "Linux":
        if not signals.get("filesystem_writable"):
            return "cowork_vm"   # Strong signal: read-only FS + Linux
        if (signals.get("age_installed")
                and signals.get("keyring_functional")
                and signals.get("filesystem_writable")):
            return "local_linux"
        return "unknown"         # Defensive: don't auto-downgrade
    else:
        return "unknown"


def _build_degradations(capabilities: dict) -> list[str]:
    """Translate False capability flags into human-readable degradation strings."""
    degradations: list[str] = []
    if not capabilities.get("filesystem_writable"):
        degradations.append("vault_decrypt_unavailable")
        degradations.append("state_writes_disabled")
        degradations.append("audit_log_disabled")
    if not capabilities.get("age_installed"):
        degradations.append("encrypted_state_inaccessible")
    if not capabilities.get("keyring_functional"):
        degradations.append("credential_store_unavailable")
    if not capabilities.get("network_microsoft"):
        degradations.append("outlook_mail_unavailable")
        degradations.append("ms_todo_sync_unavailable")
    if not capabilities.get("network_apple"):
        degradations.append("icloud_mail_unavailable")
        degradations.append("icloud_calendar_unavailable")
    return degradations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect(skip_network: bool = False) -> EnvironmentManifest:
    """
    Run all environment probes and return an EnvironmentManifest.

    Idempotent and side-effect-free (write probe file is created and immediately
    removed). Safe to call in any context.

    Args:
        skip_network: Skip TCP connectivity probes (faster, for unit tests).
                      When skipped, network capabilities optimistically report True.
    """
    from datetime import datetime, timezone as _tz
    probed_at = datetime.now(_tz.utc).isoformat()

    # --- Run probes ---
    cowork_marker, cowork_raw = _probe_cowork_marker()
    fs_writable,   fs_raw     = _probe_filesystem_writable()
    age_installed, age_raw    = _probe_age_installed()
    keyring_ok,    keyring_raw = _probe_keyring_functional()

    if skip_network:
        net_google, net_google_raw = True,  "skipped"
        net_ms,     net_ms_raw     = True,  "skipped"
        net_apple,  net_apple_raw  = True,  "skipped"
    else:
        net_google, net_google_raw = _probe_network("gmail.googleapis.com", 443)
        net_ms,     net_ms_raw     = _probe_network("graph.microsoft.com",  443)
        net_apple,  net_apple_raw  = _probe_network("imap.mail.me.com",     993)

    # --- Signals for classification ---
    signals = {
        "cowork_marker":       cowork_marker,
        "filesystem_writable": fs_writable,
        "age_installed":       age_installed,
        "keyring_functional":  keyring_ok,
        "network_google":      net_google,
        "network_microsoft":   net_ms,
        "network_apple":       net_apple,
        "platform":            platform.system(),
    }

    environment = _classify_environment(signals)

    capabilities = {
        "filesystem_writable": fs_writable,
        "age_installed":       age_installed,
        "keyring_functional":  keyring_ok,
        "network_google":      net_google,
        "network_microsoft":   net_ms,
        "network_apple":       net_apple,
    }

    detection_signals = {
        "cowork_marker_raw":     cowork_raw,
        "filesystem_raw":        fs_raw,
        "age_raw":               age_raw,
        "keyring_raw":           keyring_raw,
        "network_google_raw":    net_google_raw,
        "network_microsoft_raw": net_ms_raw,
        "network_apple_raw":     net_apple_raw,
        "platform_raw":          platform.platform(),
    }

    return EnvironmentManifest(
        environment=environment,
        capabilities=capabilities,
        degradations=_build_degradations(capabilities),
        detection_signals=detection_signals,
        probed_at=probed_at,
    )


def detect_cached(
    force_refresh: bool = False,
    skip_network: bool = False,
) -> EnvironmentManifest:
    """
    Return a cached manifest if fresh (< 5 min), otherwise re-probe.
    Cache stored in tmp/.env_manifest.json.
    """
    if not force_refresh and _CACHE_FILE.exists():
        try:
            cache_mtime = _CACHE_FILE.stat().st_mtime
            if (time.time() - cache_mtime) < _CACHE_TTL_SECONDS:
                data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
                return EnvironmentManifest(
                    environment=data["environment"],
                    capabilities=data["capabilities"],
                    degradations=data["degradations"],
                    detection_signals=data.get("detection_signals", {}),
                    probed_at=data.get("probed_at", ""),
                )
        except (json.JSONDecodeError, KeyError, OSError):
            pass  # Stale or corrupt — fall through to fresh probe

    manifest = detect(skip_network=skip_network)

    # Write cache (non-critical: failures are silently ignored)
    try:
        _TMP_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps(manifest.to_dict(include_signals=True), indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass

    return manifest


def detect_json(debug: bool = False, skip_network: bool = False) -> str:
    """CLI entry point — returns manifest as JSON string (always fresh probe).

    Output is compact (no indentation) when stdout is not a TTY (i.e. piped
    to a script or tool), and pretty-printed when writing to an interactive
    terminal.  Pass --pretty to force indented output regardless.
    """
    manifest = detect_cached(force_refresh=True, skip_network=skip_network)
    indent = 2 if sys.stdout.isatty() else None
    sep = None if indent else (',', ':')
    return json.dumps(manifest.to_dict(include_signals=debug), indent=indent, separators=sep)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect Artha runtime environment and print capability manifest as JSON."
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Include raw detection signals in output",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Force indented (pretty-print) JSON output even when piped",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Force fresh probe, ignoring cached result",
    )
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="Skip network connectivity probes (faster, skips TCP probes)",
    )
    args = parser.parse_args()

    if args.no_cache:
        manifest = detect(skip_network=args.skip_network)
        indent = 2 if (args.pretty or sys.stdout.isatty()) else None
        sep = None if indent else (',', ':')
        print(json.dumps(manifest.to_dict(include_signals=args.debug), indent=indent, separators=sep))
    else:
        output = detect_json(debug=args.debug, skip_network=args.skip_network)
        if args.pretty and '\n' not in output:
            # Re-indent if compact form was returned
            output = json.dumps(json.loads(output), indent=2)
        print(output)


if __name__ == "__main__":
    main()
