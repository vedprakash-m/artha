#!/usr/bin/env python3
"""Small, explicit Light-mode cutover decision."""
from __future__ import annotations

import re
import socket
import sys
from pathlib import Path

_CUTOVER_LINE = re.compile(r"^cutover:\s*(true|false)\s*(?:#.*)?$", re.IGNORECASE | re.MULTILINE)


def host_id(name: str | None = None) -> str:
    """Short, stable laptop name used for markers, manifests and conflict names."""
    return (name if name is not None else socket.gethostname()).split(".", 1)[0].strip()


def configured_cutover(root: Path) -> bool:
    """Read the top-level ``cutover`` setting without a YAML dependency (hooks have none).

    A missing, unreadable or unrecognised value is False: it can never enable the Engine.
    """
    try:
        text = (Path(root) / "config" / "catchup_core.yaml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    match = _CUTOVER_LINE.search(text)
    return bool(match) and match.group(1).lower() == "true"


def effective_cutover(*, configured: bool, environment: str | None) -> bool:
    """A runtime environment variable may force cutover off, never on."""
    return configured is True and environment != "0"


def describe_cutover(*, configured: bool, environment: str | None) -> dict[str, object]:
    """Describe each input and the resulting local decision."""
    enabled = effective_cutover(configured=configured, environment=environment)
    if not configured and environment == "1":
        detail = "Light mode (config is false; ARTHA_CUTOVER=1 was ignored)"
    elif environment == "0":
        detail = "Light mode (ARTHA_CUTOVER=0 forced off)"
    elif configured:
        detail = "Authoritative Engine mode (config is true)"
    else:
        detail = "Light mode (config is false)"
    return {
        "enabled": enabled,
        "mode": "authoritative" if enabled else "light",
        "configured": configured is True,
        "environment": environment,
        "detail": detail,
    }


def require_owner_terminal(command: str) -> bool:
    """Owner-only Light-mode commands refuse to run without an interactive terminal."""
    if sys.stdin.isatty():
        return True
    print(f"{command} needs an owner terminal (interactive); an agent session cannot run it", file=sys.stderr)
    return False
