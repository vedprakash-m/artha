"""Extractor command backed by the Claude CLI with every capability switched off (re-artha §6.6, D-23).

Used as ``ARTHA_EXTRACTOR_CMD="python3 <repo>/scripts/catchup_core/extractor_claude.py"``.  Reads the
extraction prompt on stdin and prints the model's answer on stdout, which is the contract of
:func:`catchup_core.runtime.command_extractor`.

The extractor reads untrusted content *and* private data, so under the Rule of Two it must hold
neither tools nor the ability to change anything.  The launch therefore:

* disables all tools, skills, MCP servers, hooks and settings files;
* runs in a fresh empty directory, so no project instructions or files are in reach;
* does not persist the session;
* refuses (via ``llm_cli_policy``) any permission-bypass flag, and takes the model only from
  ``ARTHA_LLM_MODEL_CLAUDE``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.llm_cli_policy import assert_no_permission_bypass, model_args

__all__ = ["build_argv", "extract", "main"]

TIMEOUT_SEC = 110.0
# Only what the CLI needs to start and authenticate; nothing Artha-specific or from other services.
_PASS_ENV = ("HOME", "PATH", "USER", "LANG", "LC_ALL", "TMPDIR", "SHELL",
             "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR")


def build_argv(environ: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    argv = [
        env.get("ARTHA_CLAUDE_BIN", "claude"), "-p",
        "--tools", "",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--setting-sources", "",
        "--no-session-persistence",
        "--output-format", "text",
        *model_args("claude", dict(env)),
    ]
    assert_no_permission_bypass(argv)
    return argv


def extract(prompt: str, *, environ: Mapping[str, str] | None = None,
            runner: Callable[..., Any] = subprocess.run) -> str:
    env = os.environ if environ is None else environ
    clean_env = {k: env[k] for k in _PASS_ENV if k in env}
    with tempfile.TemporaryDirectory(prefix="artha-extract-") as empty:
        proc = runner(build_argv(env), input=prompt, capture_output=True, text=True,
                      timeout=TIMEOUT_SEC, check=False, cwd=empty, env=clean_env)
    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}")
    return str(proc.stdout)


def main() -> int:
    try:
        sys.stdout.write(extract(sys.stdin.read()))
    except Exception as exc:  # the caller treats any non-zero exit as a failed extraction
        sys.stderr.write(f"extractor failed: {type(exc).__name__}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
