"""WP-00 canary tests: each ``make`` check must fail when its check fails.

The checks run against a throw-away mini tree so that a real defect in the
repository can never mask (or be masked by) a canary.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MAKEFILE = REPO / "Makefile"
RUFF = shutil.which("ruff")


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tools").mkdir()
    shutil.copy(REPO / "tools" / "import_check.py", tmp_path / "tools" / "import_check.py")
    (tmp_path / "artha.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["F"]\n')
    (tmp_path / "scripts" / "ok.py").write_text("VALUE = 1\n")
    return tmp_path


CLT = Path("/Library/Developer/CommandLineTools")


def _env() -> dict[str, str]:
    """Environment for ``make``.

    On macOS ``/usr/bin/make`` is a shim for the selected developer directory;
    when the Xcode licence is not accepted it refuses to run (re-artha D-01).
    Pointing DEVELOPER_DIR at the Command Line Tools avoids that without
    touching system settings.
    """
    env = dict(os.environ)
    if sys.platform == "darwin" and CLT.is_dir() and "DEVELOPER_DIR" not in env:
        env["DEVELOPER_DIR"] = str(CLT)
    return env


def make(tree: Path, target: str, **variables: str) -> subprocess.CompletedProcess[str]:
    args = ["make", "-s", "-f", str(MAKEFILE), "-C", str(tree), target, f"PYTHON={sys.executable}"]
    args += [f"{k}={v}" for k, v in variables.items()]
    return subprocess.run(args, capture_output=True, text=True, timeout=120, env=_env())


def test_lint_passes_on_clean_tree(tree):
    assert make(tree, "lint").returncode == 0


def test_lint_fails_on_syntax_error(tree):
    (tree / "scripts" / "bad.py").write_text("def broken(:\n")
    result = make(tree, "lint")
    assert result.returncode != 0


def test_lint_fails_on_syntax_error_in_tests(tree):
    (tree / "tests" / "test_bad.py").write_text("class :\n")
    assert make(tree, "lint").returncode != 0


def test_import_check_passes_on_clean_tree(tree):
    assert make(tree, "import-check").returncode == 0


def test_import_check_fails_on_dual_import(tree):
    (tree / "scripts" / "dual.py").write_text("from scripts.ok import VALUE\n")
    result = make(tree, "import-check")
    assert result.returncode != 0
    assert "dual.py" in result.stdout + result.stderr


def test_import_check_fails_on_unparsable_file(tree):
    (tree / "scripts" / "bad.py").write_text("def broken(:\n")
    result = make(tree, "import-check")
    assert result.returncode != 0
    assert "bad.py" in result.stdout + result.stderr


def test_import_check_fails_when_no_python_files(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tools").mkdir()
    shutil.copy(REPO / "tools" / "import_check.py", tmp_path / "tools" / "import_check.py")
    assert make(tmp_path, "import-check").returncode != 0


@pytest.mark.skipif(RUFF is None, reason="ruff not installed")
def test_ruff_passes_on_clean_tree(tree):
    assert make(tree, "ruff").returncode == 0


@pytest.mark.skipif(RUFF is None, reason="ruff not installed")
def test_ruff_fails_on_violation(tree):
    (tree / "scripts" / "dirty.py").write_text("import os\n")
    assert make(tree, "ruff").returncode != 0


def test_ruff_fails_when_tool_missing(tree):
    result = make(tree, "ruff", RUFF="definitely-not-a-real-ruff-binary")
    assert result.returncode != 0
    assert "not installed" in result.stdout + result.stderr


# --- lint-state-writes ratchet ------------------------------------------------

import importlib.util  # noqa: E402


def _load_state_write_tool():
    spec = importlib.util.spec_from_file_location("lint_state_writes", REPO / "tools" / "lint_state_writes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_state_write_scan_counts_direct_writes(tmp_path):
    tool = _load_state_write_tool()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.py").write_text("p.write_text('x')\nq.write_text('y')\n")
    (tmp_path / "scripts" / "b.py").write_text("p.write_text('x')  # tmp/ cache\n")
    (tmp_path / "scripts" / "test_c.py").write_text("p.write_text('x')\n")
    assert tool.scan(tmp_path) == {"scripts/a.py": 2}


def test_state_write_regression_detected():
    tool = _load_state_write_tool()
    regressions, _ = tool.compare({"scripts/a.py": 3, "scripts/new.py": 1}, {"scripts/a.py": 2})
    assert len(regressions) == 2


def test_state_write_improvement_not_a_failure():
    tool = _load_state_write_tool()
    regressions, improvements = tool.compare({"scripts/a.py": 1}, {"scripts/a.py": 2, "scripts/gone.py": 1})
    assert regressions == []
    assert len(improvements) == 2


def test_state_write_cli_fails_on_new_write_and_missing_baseline(tmp_path):
    tool = _load_state_write_tool()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.py").write_text("p.write_text('x')\n")
    assert tool.main(["lint_state_writes.py", str(tmp_path)]) == 1  # a.py not in baseline


def test_repository_state_writes_do_not_regress():
    tool = _load_state_write_tool()
    assert tool.main(["lint_state_writes.py", str(REPO)]) == 0


# --- coverage floor (WP00.AC10) ---------------------------------------------------

def _coverage_tree(tmp_path: Path, *, tested: bool) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "def used(x):\n    return x + 1\n\n\ndef unused(x):\n    if x:\n        return 1\n    return 2\n")
    tests = tmp_path / "pkgtests"
    tests.mkdir()
    body = "from pkg import used\n\n\ndef test_used():\n    assert used(1) == 2\n"
    if tested:
        body += "\n\ndef test_unused():\n    from pkg import unused\n    assert unused(1) == 1 and unused(0) == 2\n"
    (tests / "test_pkg.py").write_text(body)
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths = []\n")
    return tmp_path


def test_coverage_gate_fails_below_floor(tmp_path):
    pytest.importorskip("pytest_cov")
    tree = _coverage_tree(tmp_path, tested=False)
    result = make(tree, "coverage", CORE_PKG="pkg", CORE_TESTS="pkgtests", COV_MIN="90")
    assert result.returncode != 0


def test_coverage_gate_passes_at_full_coverage(tmp_path):
    pytest.importorskip("pytest_cov")
    tree = _coverage_tree(tmp_path, tested=True)
    result = make(tree, "coverage", CORE_PKG="pkg", CORE_TESTS="pkgtests", COV_MIN="90")
    assert result.returncode == 0, result.stdout + result.stderr
