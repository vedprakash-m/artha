#!/usr/bin/env python3
"""Run the test suite against a clean copy of the checkout (re-artha §11.7).

Why: the pre-existing suite reads and writes ``state/``, ``briefings/``,
``tmp/`` and ``backups/`` under the repository root.  Run in place it modifies
live Artha data (observed 2026-10-11: test briefings appended to the real
archive, a test checkpoint left in ``state/``).  This runner copies exactly what
CI would see — tracked files plus untracked-but-not-ignored files, with their
current working-tree content — into a temporary workspace with empty data
directories, and runs pytest there.

Usage: python tools/run_tests_isolated.py [pytest args...]
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MARKER = ".artha-test-workspace"
EMPTY_DIRS = ("state", "briefings", "summaries", "tmp", "backups", "output", "scratch")
# Never copied even when tracked: live data directories keep only their committed templates.
_DATA_PREFIXES = ("briefings/", "summaries/", "tmp/", "backups/", "output/")
_STATE_KEEP = ("state/templates/", "state/learned_procedures/", "state/.gitkeep")


def _git_files(root: Path, env: dict[str, str]) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=str(root), capture_output=True, env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files failed: {result.stderr.decode(errors='replace').strip()}")
    return [p for p in result.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def _wanted(rel: str) -> bool:
    if rel.startswith(_DATA_PREFIXES):
        return rel.endswith(".gitkeep")
    if rel.startswith("state/"):
        return rel.startswith(_STATE_KEEP)
    return not rel.startswith((".git/", ".venv/", ".archive/"))


def build_workspace(root: Path, dest: Path, env: dict[str, str] | None = None) -> int:
    env = env or _git_env()
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for rel in _git_files(root, env):
        if not _wanted(rel):
            continue
        src = root / rel
        if not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied += 1
    for name in EMPTY_DIRS:
        (dest / name).mkdir(exist_ok=True)
    # Same runtime directories the CI workflow creates before the suite.
    for name in ("output/career", "briefings/career"):
        (dest / name).mkdir(parents=True, exist_ok=True)
    (dest / MARKER).write_text("isolated test workspace — safe to delete\n")
    return copied


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    clt = Path("/Library/Developer/CommandLineTools")
    if sys.platform == "darwin" and (clt / "usr/bin/git").exists():
        env.setdefault("DEVELOPER_DIR", str(clt))
    return env


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    dest = Path(tempfile.mkdtemp(prefix="artha-ws-"))
    try:
        copied = build_workspace(root, dest)
        print(f"isolated workspace: {dest} ({copied} files)", file=sys.stderr)
        args = argv or ["tests", "-q", "--tb=short"]
        env = _git_env()
        env["ARTHA_NO_REEXEC"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
            cwd=str(dest), env=env,
        ).returncode
    finally:
        shutil.rmtree(dest, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
