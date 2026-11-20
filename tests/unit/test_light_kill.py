"""Light mode LF-05 AC3: a real child process killed at each publication step leaves a whole file."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
OLD = "---\nschema_version: '1.0'\ndomain: goals\nlast_updated: '2026-11-19'\n---\nold body\n"
NEW = OLD.replace("old body", "new body")

# The child patches os.fsync / os.replace, then exits abruptly (no cleanup) at the requested step.
PRELUDE = """
import os, sys
sys.path.insert(0, os.environ["KILL_SCRIPTS"])
from pathlib import Path
stage, target = sys.argv[1], Path(sys.argv[2])
NEW = os.environ["KILL_NEW"]
real_fsync, real_replace = os.fsync, os.replace

def fsync(fd):
    if stage == "before_fsync":
        os._exit(9)
    return real_fsync(fd)

def replace(src, dst, *args, **kwargs):
    publishing = Path(dst) == target
    if stage == "before_replace" and publishing:
        os._exit(9)
    result = real_replace(src, dst, *args, **kwargs)
    if stage == "after_replace" and publishing:
        os._exit(9)
    return result

os.fsync, os.replace = fsync, replace
"""

PLAIN = PRELUDE + """
from lib.state_writer import read_versioned, write
_, token = read_versioned(target)
write(target, NEW, domain="goals", source="kill-test", expected_token=token, pii_check=False, snapshot=False)
"""

ENCRYPTED = PRELUDE + """
from catchup_core.vault import AgeVault
vault = AgeVault(target.parent, identity=lambda: os.environ["KILL_KEY"], recipient=os.environ["KILL_RECIPIENT"])
_, revision = vault.read_versioned("goals")
vault.write("goals", NEW, expected_revision=revision)
"""

STAGES = [("before_fsync", OLD), ("before_replace", OLD), ("after_replace", NEW)]


def _run(script: str, stage: str, target: Path, tmp_path: Path, **extra: str) -> None:
    env = {
        "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
        "HOME": str(tmp_path),
        "ARTHA_LOCAL_DIR": str(tmp_path / "local"),
        "KILL_SCRIPTS": str(SCRIPTS),
        "KILL_NEW": NEW,
        **extra,
    }
    proc = subprocess.run(
        [sys.executable, "-c", script, stage, str(target)],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 9, proc.stderr[-600:]


@pytest.mark.parametrize("stage,expected", STAGES)
def test_plain_state_write_killed_at_each_step_leaves_a_whole_file(tmp_path, stage, expected):
    target = tmp_path / "goals.md"
    target.write_text(OLD, encoding="utf-8")
    _run(PLAIN, stage, target, tmp_path)
    assert target.read_text(encoding="utf-8") == expected


@pytest.mark.skipif(
    shutil.which("age") is None or shutil.which("age-keygen") is None, reason="age is not installed"
)
@pytest.mark.parametrize("stage,expected", STAGES)
def test_encrypted_write_killed_at_each_step_leaves_a_whole_readable_file(tmp_path, stage, expected):
    from catchup_core.vault import AgeVault

    generated = subprocess.run(["age-keygen"], capture_output=True, text=True, check=True)
    key = next(line for line in generated.stdout.splitlines() if line.startswith("AGE-SECRET-KEY-"))
    recipient = next(
        line.split(": ", 1)[1] for line in generated.stderr.splitlines() if line.startswith("Public key:")
    )
    vault = AgeVault(tmp_path, identity=lambda: key, recipient=recipient)
    vault.write("goals", OLD)
    _run(ENCRYPTED, stage, tmp_path / "goals.md.age", tmp_path, KILL_KEY=key, KILL_RECIPIENT=recipient)
    assert vault.read("goals") == expected
