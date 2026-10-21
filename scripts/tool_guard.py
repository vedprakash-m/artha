#!/usr/bin/env python3
"""
scripts/tool_guard.py — PreToolUse hook: defence in depth, not the security boundary
=====================================================================================
re-artha §6.6.6 / RA-18.  Reads the hook JSON from stdin and exits 2 (deny, reason on
stderr) for exactly three things:

1. launching a model CLI with a permission-bypassing flag;
2. ``vault.py decrypt`` (writes plaintext State Files to disk; use ``vault.py read <domain>``);
3. reading or writing a plaintext copy of a vault-protected State File.

Everything else exits 0.  A guard that crashes or cannot parse its input exits 0 too:
hooks never replace code-level permissions, and a broken hook must not lock the user out.
Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
_FALLBACK_DOMAINS = (
    "immigration", "finance", "insurance", "estate", "health", "audit", "vehicle", "contacts",
    "occasions", "transactions", "kids", "employment",
)
_BYPASS = re.compile(
    r"(--dangerously-skip-permissions|--yolo\b|--allow-all(-tools|-paths)?\b|--permission-mode[ =]+bypassPermissions"
    r"|--dangerously-bypass-approvals-and-sandbox)")
_LAUNCHER = re.compile(r"\b(claude|gemini|copilot|codex|opencode)\b")
_DECRYPT = re.compile(r"vault\.py\s+decrypt\b")


def _domains() -> tuple[str, ...]:
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from foundation import get_sensitive_domains  # noqa: PLC0415
        return tuple(sorted(get_sensitive_domains())) or _FALLBACK_DOMAINS
    except Exception:  # noqa: BLE001
        return _FALLBACK_DOMAINS


def _plaintext_state(text: str, domains: tuple[str, ...]) -> str | None:
    pattern = re.compile(r"(?:^|[/\s\"'=])state/(" + "|".join(map(re.escape, domains)) + r")\.md\b(?!\.age)")
    match = pattern.search(text)
    return match.group(1) if match else None


_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\b", re.DOTALL)
_SPLIT = re.compile(r"\n|;|&&|\|\|?")
_TEXT_ONLY = {"echo", "printf"}


def _segments(command: str) -> list[tuple[str, str]]:
    """(program, segment) for each simple command; heredoc bodies are data, not commands."""
    stripped = _HEREDOC.sub("", command)
    out: list[tuple[str, str]] = []
    for part in _SPLIT.split(stripped):
        words = part.split()
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", words[0]):
            words.pop(0)
        if words:
            out.append((words[0].rsplit("/", 1)[-1], part))
    return out


def _bash_reason(command: str, allow_decrypt: bool, domains: tuple[str, ...]) -> str | None:
    for program, segment in _segments(command):
        if program in _TEXT_ONLY:
            continue
        if _BYPASS.search(segment) and _LAUNCHER.search(segment) and program not in {"grep", "rg", "git"}:
            return "launching a model CLI with a permission-bypassing flag is not allowed (re-artha §6.6)"
        if program.startswith("python") and _DECRYPT.search(segment) and not allow_decrypt:
            return "`vault.py decrypt` writes plaintext to disk; use `vault.py read <domain>` (re-artha §10.6)"
        found = _plaintext_state(segment, domains)
        if found:
            return f"state/{found}.md is vault-protected; read it with `vault.py read {found}`"
    return None


def decide(payload: dict[str, Any], *, allow_decrypt: bool = False,
           domains: tuple[str, ...] | None = None) -> str | None:
    """Return a denial reason, or None to allow."""
    tool = str(payload.get("tool_name", ""))
    args = payload.get("tool_input") or {}
    if not isinstance(args, dict):
        return None
    domains = domains if domains is not None else _domains()
    if tool == "Bash":
        return _bash_reason(str(args.get("command", "")), allow_decrypt, domains)
    if tool in {"Read", "Edit", "Write", "NotebookEdit", "Grep", "Glob"}:
        target = " ".join(str(args.get(k, "")) for k in ("file_path", "path", "pattern", "notebook_path"))
        found = _plaintext_state(target, domains)
        if found:
            return f"state/{found}.md is vault-protected; read it with `vault.py read {found}`"
    return None


def main(stdin: Any = None) -> int:
    try:
        payload = json.loads((stdin or sys.stdin).read() or "{}")
        reason = decide(payload, allow_decrypt=os.environ.get("ARTHA_ALLOW_VAULT_DECRYPT") == "1")
    except Exception:  # noqa: BLE001 — a broken guard must never lock the user out
        return 0
    if reason:
        print(f"[tool_guard] denied: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
