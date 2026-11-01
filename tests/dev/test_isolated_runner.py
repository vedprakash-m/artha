"""WP-00: the suite must run against a clean copy of the checkout, never live data."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load("run_tests_isolated", REPO / "tools" / "run_tests_isolated.py")
sys.path.insert(0, str(REPO / "tests"))
import _isolation as iso  # noqa: E402


def _git_env():
    env = dict(os.environ)
    clt = Path("/Library/Developer/CommandLineTools")
    if sys.platform == "darwin" and clt.is_dir():
        env["DEVELOPER_DIR"] = str(clt)
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    return env


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    env = _git_env()

    def git(*a):
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, env=env)

    git("init", "-q", "-b", "main")
    (root / ".gitignore").write_text("state/*.md\nconfig/user_profile.yaml\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "tracked.py").write_text("X = 1\n")
    (root / "state").mkdir()
    (root / "state" / "templates").mkdir()
    (root / "state" / "templates" / "t.md").write_text("template\n")
    git("add", "-A")
    git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    (root / "scripts" / "new_untracked.py").write_text("Y = 2\n")          # visible to CI after add
    (root / "state" / "finance.md").write_text("PERSONAL\n")                # ignored
    (root / "config").mkdir()
    (root / "config" / "user_profile.yaml").write_text("name: X\n")        # ignored
    (root / "scripts" / "tracked.py").write_text("X = 99\n")                # modified, tracked
    return root


def test_workspace_has_working_tree_content_but_no_ignored_files(checkout, tmp_path):
    dest = tmp_path / "ws"
    runner.build_workspace(checkout, dest, env=_git_env())
    assert (dest / "scripts" / "tracked.py").read_text() == "X = 99\n"
    assert (dest / "scripts" / "new_untracked.py").exists()
    assert (dest / "state" / "templates" / "t.md").exists()
    assert not (dest / "state" / "finance.md").exists()
    assert not (dest / "config" / "user_profile.yaml").exists()
    assert (dest / runner.MARKER).exists()
    for name in ("state", "briefings", "tmp", "backups"):
        assert (dest / name).is_dir()


def test_workspace_never_contains_git_dir(checkout, tmp_path):
    dest = tmp_path / "ws"
    runner.build_workspace(checkout, dest, env=_git_env())
    assert not (dest / ".git").exists()


@pytest.mark.parametrize("source_args", [["--cov=scripts/catchup_core"],
                                        ["--cov=scripts/catchup_core", "--cov=other"]])
def test_isolated_xml_matches_changed_lines_after_cleanup(tmp_path, source_args):
    pytest.importorskip("pytest_cov")
    pytest.importorskip("diff_cover")
    root = tmp_path / "origin with spaces"
    core = root / "scripts/catchup_core"
    core.mkdir(parents=True)
    (root / "other").mkdir()
    (root / "other/__init__.py").write_text("OTHER = 1\n")
    (core / "__init__.py").write_text("")
    (core / "changed.py").write_text("def unused():\n    return 1\n")
    (root / "test_sample.py").write_text("def test_sample():\n    assert True\n")
    (root / "pyproject.toml").write_text(
        '[tool.coverage.run]\nbranch = true\nsource = ["scripts/catchup_core"]\n')
    (root / "tools").mkdir()
    shutil_source = REPO / "tools/run_tests_isolated.py"
    (root / "tools/run_tests_isolated.py").write_bytes(shutil_source.read_bytes())
    env = _git_env()
    for args in (["init", "-q", "-b", "main"], ["add", "."],
                 ["-c", "core.hooksPath=.git/no-hooks", "-c", "commit.gpgsign=false",
                  "commit", "-qm", "base"], ["update-ref", "refs/remotes/origin/main", "HEAD"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)
    (core / "changed.py").write_text("def unused():\n    return 2\n")
    report = tmp_path / "export with spaces.xml"
    result = subprocess.run(
        [sys.executable, str(root / "tools/run_tests_isolated.py"), "test_sample.py", "-q",
         *source_args, "--cov-report=xml:" + str(report)],
        cwd=root, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result = subprocess.run(
        [sys.executable, "-m", "diff_cover.diff_cover_tool", str(report),
         "--compare-branch=origin/main", "--include=scripts/catchup_core/*", "--fail-under=85"],
        cwd=root, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "scripts/catchup_core/changed.py" in result.stdout + result.stderr
    assert "No lines with coverage information" not in result.stdout + result.stderr


@pytest.mark.parametrize("checkout_path,sources", [
    ("/deleted/checkout with spaces", ["/deleted/checkout with spaces/scripts/catchup_core",
                                      "/deleted/checkout with spaces/other"]),
    (r"C:\deleted\checkout with spaces", [r"C:\deleted\checkout with spaces\scripts\catchup_core",
                                          r"C:\deleted\checkout with spaces\other"]),
    ("/deleted/checkout", ["scripts/catchup_core", "other"]),
])
def test_xml_normalization_changes_only_sources(tmp_path, checkout_path, sources):
    report = tmp_path / "report.xml"
    root = ET.Element("coverage", {"line-rate": "0.5", "branch-rate": "0.25"})
    source_nodes = ET.SubElement(root, "sources")
    for source in sources:
        ET.SubElement(source_nodes, "source").text = source
    package = ET.SubElement(ET.SubElement(root, "packages"), "package", {"name": "core"})
    ET.SubElement(package, "class", {"filename": "__init__.py", "name": "init"})
    original_packages = ET.tostring(package)
    ET.ElementTree(root).write(report)
    unrelated = tmp_path / "unrequested.xml"
    unrelated.write_bytes(report.read_bytes())
    original = unrelated.read_bytes()
    assert runner.normalize_coverage_xml(report, Path(checkout_path), tmp_path) == 2
    normalized = ET.parse(report).getroot()
    assert [node.text for node in normalized.findall("./sources/source")] == [
        (tmp_path / "scripts/catchup_core").as_posix(), (tmp_path / "other").as_posix()]
    assert normalized.attrib == root.attrib
    assert ET.tostring(normalized.find("./packages/package")) == original_packages
    assert unrelated.read_bytes() == original


@pytest.mark.parametrize("body,error", [
    ("not XML", ET.ParseError),
    ("<coverage/>", ValueError),
    ("<coverage><sources><source/></sources></coverage>", ValueError),
    ("<coverage><sources><source>../outside</source></sources></coverage>", ValueError),
    ("<coverage><sources><source>/outside</source></sources></coverage>", ValueError),
    ('<coverage><sources><source>C:\\checkout\\..\\outside</source></sources></coverage>', ValueError),
])
def test_xml_normalization_errors_leave_report_unchanged(tmp_path, body, error):
    report = tmp_path / "report.xml"
    report.write_text(body)
    original = report.read_bytes()
    checkout_path = Path(r"C:\checkout") if "C:" in body else tmp_path / "checkout"
    with pytest.raises(error):
        runner.normalize_coverage_xml(report, checkout_path, tmp_path)
    assert report.read_bytes() == original


def test_xml_report_options_preserve_absolute_outputs_and_ignore_other_formats(tmp_path):
    report = tmp_path / "with spaces.xml"
    checkout_path = tmp_path / "checkout"
    assert runner._xml_reports([
        "--cov-report=term-missing", "--cov-report=xml:" + str(report),
        "--cov-report", "xml:" + str(report), "--cov-report=html:other", "--cov-report=xml",
    ], checkout_path) == [report, checkout_path / "coverage.xml"]


def test_test_process_temp_paths_stay_inside_the_isolated_workspace(tmp_path):
    dest = tmp_path / "ws"
    env = runner._test_env(dest)
    test_temp = dest / ".pytest-tmp"
    assert test_temp.is_dir()
    assert all(Path(env[key]) == test_temp for key in ("TMP", "TEMP", "TMPDIR"))


def test_main_keeps_machine_local_test_data_outside_the_copied_code_root(tmp_path, monkeypatch):
    workspace = tmp_path / "isolated"
    workspace.mkdir()
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(workspace))

    def build(root, dest):
        dest.mkdir(parents=True, exist_ok=True)
        return 0

    def execute(argv, *, cwd, env):
        code_root = Path(cwd)
        for key in ("TMP", "TEMP", "TMPDIR"):
            local = Path(env[key])
            assert local.is_relative_to(workspace)
            assert not local.is_relative_to(code_root)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner, "build_workspace", build)
    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.main(["tests"]) == 0
    assert not workspace.exists()


def test_inplace_refused_when_live_data_present(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "finance.md").write_text("x")
    assert iso.live_data_present(tmp_path) is True
    assert iso.inplace_run_allowed(tmp_path, ["tests/unit/test_x.py"], environ={}) is False


def test_inplace_allowed_for_hermetic_dirs_or_clean_checkout_or_marker(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "finance.md").write_text("x")
    assert iso.inplace_run_allowed(tmp_path, ["tests/dev/test_a.py", "tests/catchup_core/t.py"], environ={})
    assert iso.inplace_run_allowed(tmp_path, ["tests/unit/test_x.py"],
                                   environ={"ARTHA_ALLOW_INPLACE_TESTS": "1"})
    clean = tmp_path / "clean"
    (clean / "state" / "templates").mkdir(parents=True)
    (clean / "state" / "templates" / "t.md").write_text("tpl")
    assert iso.live_data_present(clean) is False
    assert iso.inplace_run_allowed(clean, ["tests/unit/test_x.py"], environ={})
    marked = tmp_path / "marked"
    (marked / "state").mkdir(parents=True)
    (marked / "state" / "finance.md").write_text("x")
    (marked / runner.MARKER).write_text("")
    assert iso.inplace_run_allowed(marked, ["tests/unit/test_x.py"], environ={})
