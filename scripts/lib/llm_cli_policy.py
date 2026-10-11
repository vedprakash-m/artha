"""
scripts/lib/llm_cli_policy.py — rules for launching an LLM CLI as a subprocess (WP-01)
=====================================================================================
re-artha §6.6, RA-18, §12.2 item 1.  The free-form Q&A bridge used to start
Copilot, Gemini and Claude CLIs from the Artha workspace with flags that skip every
permission prompt (``--yolo``, ``--dangerously-skip-permissions``) and with models
hard-coded in the source.  A prompt that carries private context plus a CLI that
may run tools without asking is exactly the combination the Rule of Two forbids.

* :func:`assert_no_permission_bypass` rejects any argument that disables approval.
* :func:`model_args` takes the model from the environment
  (``ARTHA_LLM_MODEL_<CLI>``), never from source; absent means "CLI default".
"""
from __future__ import annotations

import os
from typing import Iterable

FORBIDDEN_FLAGS = frozenset({
    "--yolo",
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--allow-all-tools",
    "--allow-all-paths",
    "--allow-all",
    "--auto-approve",
    "--approval-mode=yolo",
    "--sandbox=none",
})


class PermissionBypassError(ValueError):
    """Raised when a launch would disable the CLI's approval prompts."""


def find_bypass(args: Iterable[str]) -> list[str]:
    bad: list[str] = []
    for arg in args:
        token = str(arg).strip().lower()
        if token in FORBIDDEN_FLAGS or token.split("=", 1)[0] in FORBIDDEN_FLAGS:
            bad.append(str(arg))
    return bad


def assert_no_permission_bypass(args: Iterable[str]) -> None:
    bad = find_bypass(args)
    if bad:
        raise PermissionBypassError(f"permission-bypass flag(s) not allowed: {', '.join(bad)}")


def model_args(cli: str, environ: dict[str, str] | None = None) -> list[str]:
    """``["--model", <name>]`` from ``ARTHA_LLM_MODEL_<CLI>``; empty when unset."""
    env = os.environ if environ is None else environ
    name = (env.get(f"ARTHA_LLM_MODEL_{cli.upper().replace('-', '_')}") or "").strip()
    return ["--model", name] if name else []
