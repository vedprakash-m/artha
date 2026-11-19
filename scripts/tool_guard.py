#!/usr/bin/env python3
"""
scripts/tool_guard.py — PreToolUse hook: defence in depth, not the security boundary
=====================================================================================
re-artha §6.6.6 / RA-18.  Reads the hook JSON from stdin and exits 2 (deny, reason on
stderr) for exactly three things:

1. launching a model CLI with a permission-bypassing flag;
2. ``vault.py decrypt`` (writes plaintext State Files to disk; use ``vault.py read <domain>``);
3. reading or writing a plaintext copy of a vault-protected State File.

Everything else exits 0. Invalid or unavailable mandatory hook inputs are denied.
Code-level permissions remain the authority; this hook is defence in depth.
Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from typing import Any

_FALLBACK_DOMAINS = (
    "immigration", "finance", "insurance", "estate", "health", "audit", "vehicle", "contacts",
    "occasions", "transactions", "kids", "employment", "career_search", "business",
    "caregiving", "work-career",
)
_BYPASS = re.compile(
    r"(--dangerously-skip-permissions|--yolo\b|--allow-all(-tools|-paths)?\b|--permission-mode[ =]+bypassPermissions"
    r"|--dangerously-bypass-approvals-and-sandbox)")
_LAUNCHER = re.compile(r"\b(claude|gemini|copilot|codex|opencode)\b")
_DECRYPT = re.compile(r"vault\.py[\"']?\s+decrypt\b")
_FILE_TOOLS = frozenset({
    "read", "view", "edit", "write", "create", "notebookedit", "grep", "rg", "glob",
    "read_file", "write_file", "edit_file", "create_file",
    "replace_string_in_file", "read_workspace_file",
})
_PATH_FIELDS = (
    "file_path", "filePath", "path", "paths", "notebook_path",
    "directory", "directoryPath",
)


def _domains() -> tuple[str, ...]:
    # Keep this hook side-effect free. Importing foundation re-executes the interpreter
    # and may consume the hook's stdin more than once on configured installations.
    return tuple(_FALLBACK_DOMAINS)


def _plaintext_state(text: str, domains: tuple[str, ...]) -> str | None:
    pattern = re.compile(
        r"(?:^|[/\s\"'=])state/(?:[A-Za-z0-9_.-]+/)*("
        + "|".join(map(re.escape, domains))
        + r")\.md\b(?!\.age)"
    )
    match = pattern.search(text.replace("\\", "/"))
    return match.group(1) if match else None


_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\s*\1\b", re.DOTALL)
_SPLIT = re.compile(r"\n|;|&&|\|\|?")
_TEXT_ONLY = {"echo", "printf"}


def _segments(command: str) -> list[tuple[str, str]]:
    """(program, segment) for each simple command; heredoc bodies are data, not commands."""
    stripped = _HEREDOC.sub("", command)
    out: list[tuple[str, str]] = []
    for part in _SPLIT.split(stripped):
        try:
            words = shlex.split(part, posix=False)
        except ValueError:
            words = part.split()
        while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=\S*", words[0]):
            words.pop(0)
        if words and words[0] == '&':
            words.pop(0)
        if words:
            program = words[0].strip('"\'').replace('\\', '/').rsplit('/', 1)[-1].lower()
            out.append((program, part.replace('\\', '/')))
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


class GuardInputError(ValueError):
    """The host hook payload is not structured enough to authorize a decision."""


def decide(payload: dict[str, Any], *, allow_decrypt: bool = False,
           domains: tuple[str, ...] | None = None) -> str | None:
    """Return a denial reason, or None to allow."""
    if not isinstance(payload, dict):
        raise GuardInputError("hook payload must be an object")
    raw_tool = payload.get("tool_name", payload.get("toolName"))
    if not isinstance(raw_tool, str):
        raise GuardInputError("hook payload needs a string tool name")
    tool = raw_tool.strip()
    if not tool:
        raise GuardInputError("hook payload needs a tool name")
    args = payload.get("tool_input", payload.get("toolArgs"))
    if args is None:
        args = {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            raise GuardInputError("tool arguments are not valid JSON") from None
    if not isinstance(args, dict):
        raise GuardInputError("tool arguments must be an object")
    domains = domains if domains is not None else _domains()
    normalized = tool.lower()
    if normalized in {"bash", "powershell", "run_in_terminal", "shell", "terminal"}:
        command = args.get("command")
        if not isinstance(command, str):
            raise GuardInputError("shell hook needs a string command")
        return _bash_reason(command, allow_decrypt, domains)
    if normalized in _FILE_TOOLS:
        targets: list[str] = []
        for key in _PATH_FIELDS:
            value = args.get(key)
            if isinstance(value, str):
                targets.append(value)
            elif isinstance(value, list | tuple):
                if any(not isinstance(item, str) for item in value):
                    raise GuardInputError(f"{key} must contain only string paths")
                targets.extend(value)
        found = next(
            (domain for target in targets if (domain := _plaintext_state(target, domains))),
            None,
        )
        if found:
            return f"state/{found}.md is vault-protected; read it with `vault.py read {found}`"
    return None


def main(stdin: Any = None) -> int:
    payload: dict[str, Any] = {}
    try:
        payload = json.loads((stdin or sys.stdin).read() or "{}")
        if not isinstance(payload, dict):
            raise GuardInputError("hook payload must be an object")
        reason = decide(payload, allow_decrypt=os.environ.get("ARTHA_ALLOW_VAULT_DECRYPT") == "1")
    except (GuardInputError, json.JSONDecodeError):
        reason = "the required tool guard input could not be validated; the tool call was denied"
    except Exception as exc:  # noqa: BLE001 — mandatory guard failure still denies
        reason = f"the required tool guard failed safely ({type(exc).__name__})"
    if reason:
        print(f"[tool_guard] denied: {reason}", file=sys.stderr)
        if 'toolName' in payload:
            print(json.dumps({'permissionDecision': 'deny', 'permissionDecisionReason': reason}))
            return 0
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
