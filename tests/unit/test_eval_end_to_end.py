"""tests/unit/test_eval_end_to_end.py — End-to-end subprocess test for eval_runner.py.

Runs eval_runner.py --summary as a subprocess and verifies output.
Ref: specs/eval.md EV-16, T-EV-16-01
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
_ARTHA_DIR = Path(__file__).resolve().parent.parent.parent


# ===========================================================================
# T-EV-16-01: eval_runner.py --summary exits 0 and prints dashboard header
# ===========================================================================

def test_eval_runner_summary_exits_cleanly():
    """T-EV-16-01: eval_runner.py --summary must exit 0 and print dashboard header."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "eval_runner.py"), "--summary"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(_ARTHA_DIR),
    )
    assert result.returncode == 0, (
        f"eval_runner.py --summary exited {result.returncode}\n"
        f"stdout: {result.stdout[:500]}\n"
        f"stderr: {result.stderr[:500]}"
    )
    combined = result.stdout + result.stderr
    assert "ARTHA EVAL DASHBOARD" in combined, (
        f"Dashboard header 'ARTHA EVAL DASHBOARD' not found in output:\n{combined[:800]}"
    )
