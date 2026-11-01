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
BASH = shutil.which("bash")


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
    env["PYTHONUTF8"] = "1"
    if sys.platform == "darwin" and CLT.is_dir() and "DEVELOPER_DIR" not in env:
        env["DEVELOPER_DIR"] = str(CLT)
    return env


def make(tree: Path, target: str, *, skip_coverage: bool = False,
         **variables: str) -> subprocess.CompletedProcess[str]:
    # Make interprets backslashes in file names before passing recipes to sh.
    args = ["make", "-s", "-f", MAKEFILE.as_posix(), "-C", tree.as_posix(),
            target, f'PYTHON="{Path(sys.executable).as_posix()}"']
    if skip_coverage:
        args += ["-o", "coverage"]
    args += [f"{k}={v}" for k, v in variables.items()]
    env = _env()
    for name in tuple(env):
        if name.startswith("COV_CORE_") or name == "COVERAGE_PROCESS_START":
            env.pop(name)
    env["COVERAGE_FILE"] = str(tree / ".coverage")
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          timeout=120, env=env)


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

@pytest.mark.parametrize(
    ("line_rate", "branch_rate", "expected"),
    [("0.85", "0.85", 0), ("0.8499", "0.85", 1), ("0.85", "0.8499", 1)],
)
def test_default_coverage_checker_enforces_owner_85_percent_floor(
    tmp_path, line_rate, branch_rate, expected,
):
    report = tmp_path / "coverage.xml"
    report.write_text(f'<coverage line-rate="{line_rate}" branch-rate="{branch_rate}"/>')
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "check_core_coverage.py"), str(report)],
        capture_output=True, text=True, encoding="utf-8", timeout=30, env=_env(),
    )
    assert result.returncode == expected, result.stdout + result.stderr


def test_make_default_coverage_floors_are_85_percent(tmp_path):
    result = subprocess.run(
        ["make", "-n", "-f", MAKEFILE.as_posix(), "-C", tmp_path.as_posix(),
         "diff-coverage", "coverage-harness", "PYTHON=python"],
        capture_output=True, text=True, encoding="utf-8", timeout=30, env=_env(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--minimum=85 --branch-minimum=85" in result.stdout
    assert "--fail-under=85" in result.stdout
    assert result.stdout.count("--cov-fail-under=85") == 2
    assert "--cov=catchup_core.session_lock" in result.stdout
    assert "--cov=catchup_core.privacy_history" in result.stdout
    assert "--cov=catchup_core.storage_adoption" in result.stdout
    assert "tests/catchup_core/test_storage_adoption.py" in result.stdout
    assert "tests/catchup_core/test_privacy_anchor_capacity.py" in result.stdout


@pytest.mark.parametrize("untracked", [True, False])
def test_diff_gate_refuses_untracked_core_without_index_changes(tree, untracked):
    for args in (["git", "init", "-q"], ["git", "add", "."]):
        subprocess.run(args, cwd=tree, check=True, capture_output=True, env=_env())
    core = tree / "scripts/catchup_core"
    core.mkdir()
    (core / "native module.py").write_text("VALUE = 1\n")
    (tree / ".gitignore").write_text("scripts/catchup_core/private/\n")
    (core / "private").mkdir()
    (core / "private/payload.py").write_text("ignored\n")
    if not untracked:
        subprocess.run(["git", "add", "scripts/catchup_core/native module.py"],
                       cwd=tree, check=True, capture_output=True, env=_env())
    index = (tree / ".git/index").read_bytes()
    stub = tree / "python_stub.py"
    stub.write_text("import sys\nprint('DIFF_COVER_REACHED', sys.argv[1:])\n")
    result = make(tree, "diff-coverage", skip_coverage=True,
                  PYTHON=f'"{Path(sys.executable).as_posix()}" "{stub.as_posix()}"')
    assert (tree / ".git/index").read_bytes() == index
    output = result.stdout + result.stderr
    if untracked:
        assert result.returncode != 0, output
        assert "git add" in output and "native module.py" in output
        assert "DIFF_COVER_REACHED" not in output
    else:
        assert result.returncode == 0, output
        assert "DIFF_COVER_REACHED" in output


@pytest.mark.parametrize("hits", [0, 1])
def test_first_main_push_changed_line_gate_uses_empty_tree(tmp_path, hits):
    import yaml
    pytest.importorskip("diff_cover")
    core = tmp_path / "scripts/catchup_core"
    core.mkdir(parents=True)
    (core / "added.py").write_text("VALUE = 1\n")
    env = _env()
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid"})
    for args in (["init", "-q", "-b", "main"], ["add", "."],
                 ["-c", "core.hooksPath=.git/no-hooks", "-c", "commit.gpgsign=false",
                  "commit", "-qm", "initial"], ["update-ref", "refs/remotes/origin/main", "HEAD"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, env=env)
    (tmp_path / "coverage.xml").write_text(
        '<coverage><sources><source>.</source></sources><packages><package name="core">'
        '<classes><class filename="scripts/catchup_core/added.py" name="added"><lines>'
        f'<line number="1" hits="{hits}"/>'
        '</lines></class></classes></package></packages></coverage>')
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    step = next(step for step in workflow["jobs"]["core-quality"]["steps"]
                if step.get("name", "").startswith("Changed-line coverage"))
    script = step["run"].replace("make diff-coverage",
        f'make -s -f "{MAKEFILE.as_posix()}" -o coverage diff-coverage')
    script = script.replace("PYTHON=python", f'PYTHON="{Path(sys.executable).as_posix()}"')
    env["DIFF_BASE"] = "0" * 40
    index = (tmp_path / ".git/index").read_bytes()
    result = subprocess.run([BASH, "-e", "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert (tmp_path / ".git/index").read_bytes() == index
    assert (result.returncode == 0) == bool(hits), result.stdout + result.stderr
    assert "scripts/catchup_core/added.py" in result.stdout + result.stderr
    assert "No lines with coverage information" not in result.stdout + result.stderr


@pytest.mark.parametrize("job", ["tests", "core-windows"])
def test_ci_native_encryption_jobs_install_age_before_tests(job):
    import yaml
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"][job]["steps"]
    test_step = next(index for index, step in enumerate(steps)
                     if "tools/run_tests_isolated.py" in step.get("run", ""))
    installs = [step for step in steps[:test_step] if "install" in step.get("name", "").lower()
                and "age" in step.get("run", "")]
    if job == "core-windows":
        assert any("choco install age" in step["run"] for step in installs)
    else:
        assert any("apt-get install" in step["run"] and "age" in step["run"]
                   and "Linux" in step.get("if", "") for step in installs)
        assert any("brew install age" in step["run"] and "macOS" in step.get("if", "")
                   for step in installs)


def test_harness_help_labels_combined_floor():
    line = next(line for line in MAKEFILE.read_text().splitlines()
                if line.startswith("coverage-harness:"))
    assert "combined" in line.lower()
    assert "Line+branch floor" not in line


@pytest.mark.parametrize("python_change", [True, False])
def test_diff_gate_rejects_empty_matches_but_allows_docs_only(tree, python_change):
    pytest.importorskip("diff_cover")
    core = tree / "scripts/catchup_core"
    core.mkdir()
    (core / "changed.py").write_text("VALUE = 1\n")
    (tree / "README.md").write_text("before\n")
    env = _env()
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    for args in (["init", "-q", "-b", "main"], ["add", "."],
                 ["-c", "core.hooksPath=.git/no-hooks", "-c", "commit.gpgsign=false",
                  "commit", "-qm", "base"], ["update-ref", "refs/remotes/origin/main", "HEAD"]):
        subprocess.run(["git", *args], cwd=tree, check=True, capture_output=True, env=env)
    target = core / "changed.py" if python_change else tree / "README.md"
    target.write_text("VALUE = 2\n" if python_change else "after\n")
    (tree / "coverage.xml").write_text(
        '<coverage><sources><source>/deleted/checkout/scripts/catchup_core</source></sources>'
        '<packages><package name="core"><classes><class filename="changed.py" name="changed">'
        '<lines><line number="1" hits="1"/></lines></class></classes></package></packages></coverage>')
    index = (tree / ".git/index").read_bytes()
    result = make(tree, "diff-coverage", skip_coverage=True)
    assert (result.returncode != 0) == python_change, result.stdout + result.stderr
    assert (tree / ".git/index").read_bytes() == index
    if python_change:
        assert "no matching coverage" in (result.stdout + result.stderr).lower()


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


def test_coverage_canary_does_not_overwrite_parent_coverage_data(tmp_path, monkeypatch):
    pytest.importorskip("pytest_cov")
    parent_data = tmp_path / "parent.coverage"
    parent_data.write_bytes(b"synthetic parent coverage bytes must remain unchanged")
    monkeypatch.setenv("COVERAGE_FILE", str(parent_data))
    directory = tmp_path / "canary"
    directory.mkdir()
    tree = _coverage_tree(directory, tested=True)
    result = make(tree, "coverage", CORE_PKG="pkg", CORE_TESTS="pkgtests", COV_MIN="90")
    assert parent_data.read_bytes() == b"synthetic parent coverage bytes must remain unchanged"
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tree / ".coverage").is_file()


@pytest.mark.parametrize("name", ["checkout", "checkout with spaces"])
def test_harness_xml_survives_disposable_source_cleanup(tmp_path, name):
    pytest.importorskip("pytest_cov")
    directory = tmp_path / name
    directory.mkdir()
    tree = _coverage_tree(directory, tested=True)
    (tree / "tools").mkdir()
    shutil.copy(REPO / "tools" / "run_tests_isolated.py", tree / "tools" / "run_tests_isolated.py")
    shutil.copy(REPO / "tools" / "coverage-posix.toml", tree / "tools" / "coverage-posix.toml")
    for args in (["git", "init", "-q"], ["git", "add", "."]):
        subprocess.run(args, cwd=tree, check=True, capture_output=True, env=_env())
    result = make(tree, "coverage-harness", HARNESS_MODULES="pkg", HARNESS_TESTS="pkgtests")
    assert result.returncode == 0, result.stdout + result.stderr
    report = tree / "coverage-harness.xml"
    assert report.is_file(), result.stdout + result.stderr
    import xml.etree.ElementTree as ET
    root = ET.parse(report).getroot()
    assert float(root.attrib["line-rate"]) == 1.0
    assert float(root.attrib["branch-rate"]) == 1.0


def _harness_dry_run(**variables: str) -> str:
    args = ["make", "-n", "-f", MAKEFILE.as_posix(), "-C", REPO.as_posix(), "coverage-harness",
            *(f"{k}={v}" for k, v in variables.items())]
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          timeout=60, env=_env(), check=True).stdout


def test_harness_measures_windows_code_only_on_windows():
    windows = _harness_dry_run(OS="Windows_NT")
    posix = _harness_dry_run(OS="")
    assert "--cov=catchup_core.quarantine_fs_windows" in windows
    assert "test_quarantine_fs_windows.py" in windows
    assert "--cov-config=pyproject.toml" in windows
    assert "--cov=catchup_core.quarantine_fs_windows" not in posix
    assert "test_quarantine_fs_windows.py" not in posix
    assert "--cov-config=tools/coverage-posix.toml" in posix
    assert "--cov-fail-under=85" in posix and "--cov-fail-under=85" in windows


def test_posix_harness_config_excludes_only_windows_guarded_blocks():
    import tomllib
    config = tomllib.loads((REPO / "tools" / "coverage-posix.toml").read_text())["tool"]["coverage"]
    shared = tomllib.loads((REPO / "pyproject.toml").read_text())["tool"]["coverage"]
    excluded = config["report"]["exclude_lines"]
    assert set(shared["report"]["exclude_lines"]) <= set(excluded)
    extra = [pattern for pattern in excluded if pattern not in shared["report"]["exclude_lines"]]
    assert extra and all("win32" in pattern or "'nt'" in pattern or '"nt"' in pattern or "nt[" in pattern
                         for pattern in extra)
    assert config["run"] == shared["run"]
