"""The state-write gate must report its result on legacy Windows consoles."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("baseline", "source", "expected_code", "message"),
    [
        ({}, "", 0, "No new direct"),
        ({"scripts/example.py": 1}, "", 0, "counts dropped"),
        ({}, "p.write_text('x')\n", 1, "new direct"),
    ],
)
@pytest.mark.parametrize("encoding", ["cp1252", "cp437"])
def test_state_write_gate_reports_on_legacy_console(tmp_path, baseline, source, expected_code, message, encoding):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "example.py").write_text(source, encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING=f"{encoding}:strict", PYTHONUTF8="0", PYTHONDONTWRITEBYTECODE="1")
    command = (
        "import sys; from pathlib import Path; import tools.lint_state_writes as lint; "
        "lint.BASELINE = Path(sys.argv[1]); "
        "sys.exit(lint.main(['lint_state_writes.py', sys.argv[2]]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", command, str(baseline_path), str(tmp_path)],
        cwd=REPO, env=env, capture_output=True, encoding=encoding, check=False,
    )
    assert result.returncode == expected_code, result.stderr
    assert message in result.stdout
    assert not result.stderr
