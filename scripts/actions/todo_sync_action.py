#!/usr/bin/env python3
# pii-guard: ignore-file — handler; PII guard applied by ActionExecutor before this runs
"""
scripts/actions/todo_sync_action.py — Push/pull open_items.md ↔ Microsoft To Do.

Wraps the existing todo_sync.py module.  Executes push, pull, or both modes.
Conforms to ActionHandler Protocol.

SAFETY:
  - dry_run: calls todo_sync in --dry-run mode, returning a diff summary.
  - execute: calls todo_sync in the requested mode.
  - Not reversible: sync operations are idempotent; undo is not meaningful.
  - autonomy_floor: false — can be auto-executed at Trust Level 2.

Ref: specs/act.md §8.5
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from actions.base import ActionProposal, ActionResult

_TODO_SYNC_SCRIPT = str(Path(__file__).resolve().parent.parent / "todo_sync.py")

_VALID_MODES = frozenset({"push", "pull", "both"})


# ---------------------------------------------------------------------------
# Protocol functions
# ---------------------------------------------------------------------------

def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Validate mode parameter."""
    params = proposal.parameters
    mode = params.get("mode", "both")

    if mode not in _VALID_MODES:
        return False, (
            f"Invalid mode: '{mode}'. Must be one of: {sorted(_VALID_MODES)}"
        )

    return True, ""


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Run todo_sync.py --dry-run to preview what would be pushed/pulled."""
    params = proposal.parameters
    mode = params.get("mode", "both")

    modes_to_run = _modes_to_flags(mode)
    output_parts: list[str] = []

    for flag in modes_to_run:
        result = _run_todo_sync([flag, "--dry-run"])
        output_parts.append(f"[{flag}]\n{result}")

    preview = "\n\n".join(output_parts) or "(nothing to sync)"

    return ActionResult(
        status="success",
        message=f"Preview: todo_sync --dry-run (mode={mode})",
        data={
            "preview_mode": True,
            "mode": mode,
            "dry_run_output": preview,
        },
        reversible=False,
        reverse_action=None,
    )


def execute(proposal: ActionProposal) -> ActionResult:
    """Run todo_sync.py in the requested mode."""
    params = proposal.parameters
    mode = params.get("mode", "both")
    modes_to_run = _modes_to_flags(mode)

    results: dict[str, str] = {}
    errors: list[str] = []

    for flag in modes_to_run:
        try:
            out = _run_todo_sync([flag])
            results[flag] = out
        except subprocess.CalledProcessError as e:
            errors.append(f"{flag}: {e.stderr or e.stdout or str(e)}")
        except Exception as e:
            errors.append(f"{flag}: {e}")

    if errors and not results:
        return ActionResult(
            status="failure",
            message=f"todo_sync failed: {'; '.join(errors)}",
            data={"errors": errors, "mode": mode},
            reversible=False,
            reverse_action=None,
        )

    summary = "; ".join(f"{flag}: ok" for flag in results.keys())
    if errors:
        summary += f"; PARTIAL ERRORS: {'; '.join(errors)}"

    return ActionResult(
        status="success",
        message=f"✅ todo_sync completed (mode={mode}): {summary}",
        data={
            "mode": mode,
            "results": results,
            "errors": errors,
        },
        reversible=False,
        reverse_action=None,
    )


def build_reverse_proposal(
    original: ActionProposal,
    result_data: dict[str, Any],
) -> ActionProposal:
    """todo_sync does not support undo (sync operations are idempotent)."""
    raise NotImplementedError("todo_sync_action does not support undo")


def health_check() -> bool:
    """Run todo_sync --status to verify MS Graph connectivity."""
    try:
        result = subprocess.run(
            [sys.executable, _TODO_SYNC_SCRIPT, "--status"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _modes_to_flags(mode: str) -> list[str]:
    if mode == "both":
        return ["--pull", "--push"]
    return [f"--{mode}"]


def _run_todo_sync(extra_args: list[str]) -> str:
    """Run the todo_sync.py script as a subprocess and return combined output."""
    cmd = [sys.executable, _TODO_SYNC_SCRIPT] + extra_args
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd,
            output=result.stdout,
            stderr=result.stderr,
        )
    return (result.stdout + result.stderr).strip()
