"""WP-00: the suite must run against a clean copy of the checkout, never live data."""
from __future__ import annotations

import importlib.util
import os
import stat
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
    procedure = checkout / "state" / "learned_procedures" / "synthetic.md"
    procedure.parent.mkdir(parents=True)
    procedure.write_text("synthetic procedure\n")
    runner.build_workspace(checkout, dest, env=_git_env())
    assert (dest / "scripts" / "tracked.py").read_text() == "X = 99\n"
    assert (dest / "scripts" / "new_untracked.py").exists()
    assert (dest / "state" / "templates" / "t.md").exists()
    assert (dest / "state" / "learned_procedures" / "synthetic.md").exists()
    assert not (dest / "state" / "finance.md").exists()
    assert not (dest / "config" / "user_profile.yaml").exists()
    assert (dest / runner.MARKER).exists()
    for name in ("state", "briefings", "tmp", "backups"):
        assert (dest / name).is_dir()


def test_synthetic_only_workspace_excludes_every_state_file(checkout, tmp_path):
    procedure = checkout / "state" / "learned_procedures" / "synthetic.md"
    procedure.parent.mkdir(parents=True)
    procedure.write_text("SYNTHETIC_PROCEDURE_CANARY\n")
    nested = checkout / "state" / "nested" / "synthetic.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("SYNTHETIC_STATE_CANARY\n")
    dest = tmp_path / "ws"

    runner.build_workspace(checkout, dest, env=_git_env(), synthetic_only=True)

    assert (dest / "state").is_dir()
    assert not [path for path in (dest / "state").rglob("*") if path.is_file()]
    assert (dest / "scripts" / "tracked.py").read_text() == "X = 99\n"


def test_synthetic_only_excludes_normalized_case_insensitive_state_paths(checkout, tmp_path, monkeypatch):
    state = checkout / "state"
    state.mkdir(exist_ok=True)
    (state / "finance.md").write_text("SYNTHETIC_STATE_CANARY\n")
    (state / "nested.txt").write_text("SYNTHETIC_STATE_ALIAS_CANARY\n")
    aliases = [
        "STATE/finance.md",
        "./State/finance.md",
        "nested/../state/nested.txt",
        r".\STATE\nested.txt",
    ]
    monkeypatch.setattr(runner, "_git_files", lambda *_args: [*aliases, "scripts/tracked.py"])
    checked_sources = []
    original_lstat = Path.lstat

    def track_source_check(path):
        if checkout in path.parents and path not in (checkout, checkout / "scripts"):
            checked_sources.append(path)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", track_source_check)
    dest = tmp_path / "ws"

    runner.build_workspace(checkout, dest, env=_git_env(), synthetic_only=True)

    assert checked_sources == [checkout / "scripts" / "tracked.py"]
    assert not [path for path in (dest / "state").rglob("*") if path.is_file()]
    assert (dest / "scripts" / "tracked.py").read_text() == "X = 99\n"


@pytest.mark.parametrize(("path", "expected"), [
    (r"C:\state\finance.md", None),
    ("/state/finance.md", None),
    ("../state/finance.md", None),
    ("folder/...", ("folder",)),
    ("folder/..  ", ()),
    ("folder/.. /..  ", None),
    ("folder/.. ./state/finance.md", ("state", "finance.md")),
    (".. ./state/finance.md", None),
])
def test_synthetic_path_parts_normalize_roots_and_traversal(path, expected):
    assert runner._synthetic_path_parts(path) == expected


def test_synthetic_only_rejects_windows_dot_space_alias_of_state(checkout, monkeypatch):
    monkeypatch.setattr(runner, "_git_files", lambda *_args: ["scripts/.. ./state/finance.md"])

    assert not runner._wanted("scripts/.. ./state/finance.md", synthetic_only=True)


def test_synthetic_only_skips_missing_source_leaf(checkout, tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_git_files", lambda *_args: ["scripts/missing.py"])
    dest = tmp_path / "ws"

    copied = runner.build_workspace(checkout, dest, env=_git_env(), synthetic_only=True)

    assert copied == 0
    assert not (dest / "scripts" / "missing.py").exists()


def test_synthetic_only_rejects_source_symlink_before_copy(checkout, tmp_path, monkeypatch):
    source = checkout / "scripts" / "linked.py"
    source.write_text("SYNTHETIC_LINK_TARGET\n")
    monkeypatch.setattr(runner, "_git_files", lambda *_args: ["scripts/linked.py"])
    original_lstat = Path.lstat

    def linked_source_lstat(path):
        if path == source:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", linked_source_lstat)
    dest = tmp_path / "ws"

    with pytest.raises(ValueError, match="linked path refused"):
        runner.build_workspace(checkout, dest, env=_git_env(), synthetic_only=True)

    assert not (dest / "scripts" / "linked.py").exists()


def test_synthetic_only_rejects_real_file_symlink_to_state(checkout, tmp_path):
    source = checkout / "scripts" / "linked.py"
    target = checkout / "state" / "finance.md"
    try:
        source.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"OS file symlinks unavailable: {type(exc).__name__}")
    assert "scripts/linked.py" in runner._git_files(checkout, _git_env())
    dest = tmp_path / "ws"

    with pytest.raises(ValueError, match="linked path refused"):
        runner.build_workspace(checkout, dest, env=_git_env(), synthetic_only=True)

    assert not (dest / "scripts" / "linked.py").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions are platform-specific")
def test_synthetic_only_rejects_real_junction_to_state(checkout, tmp_path, monkeypatch):
    junction = checkout / "scripts" / "linked_state"
    env = _git_env()
    env.update(ARTHA_TEST_JUNCTION=str(junction), ARTHA_TEST_JUNCTION_TARGET=str(checkout / "state"))
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:ARTHA_TEST_JUNCTION "
             "-Target $env:ARTHA_TEST_JUNCTION_TARGET | Out-Null"],
            env=env, capture_output=True, check=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"OS junctions unavailable: {type(exc).__name__}")
    monkeypatch.setattr(runner, "_git_files", lambda *_args: ["scripts/linked_state/finance.md"])
    dest = tmp_path / "ws"

    with pytest.raises(ValueError, match="linked path refused"):
        runner.build_workspace(checkout, dest, env=env, synthetic_only=True)

    assert not (dest / "scripts" / "linked_state" / "finance.md").exists()


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
    build_options = {}

    def build(root, dest, *, synthetic_only=False):
        dest.mkdir(parents=True, exist_ok=True)
        build_options["synthetic_only"] = synthetic_only
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
    assert runner.main(["--synthetic-only", "tests"]) == 0
    assert build_options["synthetic_only"] is True
    assert not workspace.exists()


def test_main_surfaces_workspace_cleanup_failure(tmp_path, monkeypatch):
    workspace = tmp_path / "isolated"
    workspace.mkdir()
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(workspace))
    monkeypatch.setattr(
        runner, "build_workspace",
        lambda root, dest, **kwargs: dest.mkdir(parents=True, exist_ok=True) or 0,
    )
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    def remove(path, *, ignore_errors=False, onerror=None):
        if not ignore_errors:
            raise PermissionError("synthetic cleanup failure")

    monkeypatch.setattr(runner.shutil, "rmtree", remove)
    with pytest.raises(PermissionError, match="synthetic cleanup failure"):
        runner.main(["--synthetic-only", "tests"])


@pytest.mark.skipif(os.name != "nt", reason="Windows trailing-dot paths are platform-specific")
def test_main_removes_workspace_containing_a_trailing_dot_file(tmp_path, monkeypatch):
    workspace = tmp_path / "isolated"
    workspace.mkdir()
    unusual = Path("\\\\?\\" + str(workspace / "trailing."))
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(workspace))
    monkeypatch.setattr(
        runner, "build_workspace",
        lambda root, dest, **kwargs: dest.mkdir(parents=True, exist_ok=True) or 0,
    )

    def execute(*args, **kwargs):
        unusual.write_text("synthetic")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", execute)
    try:
        assert runner.main(["--synthetic-only", "tests"]) == 0
        assert not workspace.exists()
    finally:
        unusual.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only deletion is platform-specific")
def test_main_removes_workspace_containing_a_read_only_file(tmp_path, monkeypatch):
    workspace = tmp_path / "isolated"
    workspace.mkdir()
    read_only = workspace / "read-only"
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(workspace))
    monkeypatch.setattr(
        runner, "build_workspace",
        lambda root, dest, **kwargs: dest.mkdir(parents=True, exist_ok=True) or 0,
    )

    def execute(*args, **kwargs):
        read_only.write_text("synthetic")
        read_only.chmod(stat.S_IREAD)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", execute)
    try:
        assert runner.main(["--synthetic-only", "tests"]) == 0
        assert not workspace.exists()
    finally:
        if read_only.exists():
            read_only.chmod(stat.S_IWRITE)
            read_only.unlink()


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only deletion is platform-specific")
def test_workspace_cleanup_does_not_change_external_hardlink_permissions(tmp_path):
    workspace = tmp_path / "isolated"
    workspace.mkdir()
    original = tmp_path / "outside"
    original.write_text("synthetic")
    link = workspace / "linked"
    try:
        os.link(original, link)
    except OSError as exc:
        pytest.skip(f"OS hardlinks unavailable: {type(exc).__name__}")
    original.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            runner._remove_workspace(workspace)
        assert not original.stat().st_mode & stat.S_IWRITE
    finally:
        original.chmod(stat.S_IWRITE)
        if link.exists():
            link.unlink()


@pytest.mark.skipif(os.name != "nt", reason="Windows cleanup callback is platform-specific")
@pytest.mark.parametrize(
    ("operation", "error"),
    [(os.rmdir, PermissionError("directory denied")), (os.unlink, FileNotFoundError("file missing"))],
)
def test_workspace_cleanup_propagates_unrelated_errors(tmp_path, monkeypatch, operation, error):
    def remove(path, *, onerror):
        onerror(operation, str(path), (type(error), error, None))

    monkeypatch.setattr(runner.shutil, "rmtree", remove)
    with pytest.raises(type(error)) as raised:
        runner._remove_workspace(tmp_path / "workspace")
    assert raised.value is error


def test_synthetic_only_rejects_private_work_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        runner, "verify_private_work",
        lambda *args: pytest.fail("private-work verification must not run"),
    )

    assert runner.main(["--private-work", "--synthetic-only"]) == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_synthetic_only_rejects_duplicate_mode(capsys):
    assert runner.main(["--synthetic-only", "--synthetic-only"]) == 2
    assert "usage: [--synthetic-only]" in capsys.readouterr().err


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


def test_local_work_refuses_ci_and_conflicting_flags(monkeypatch, capsys):
    monkeypatch.setenv("CI", "true")
    assert runner.main(["--local-work"]) == 2
    assert "local-only" in capsys.readouterr().err
    monkeypatch.delenv("CI", raising=False)
    assert runner.main(["--local-work", "--private-work"]) == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_merge_windows_coverage_verifies_shards_and_normalizes_paths(tmp_path):
    import sqlite3

    merger = _load("merge_windows_coverage", REPO / "tools" / "merge_windows_coverage.py")
    db = tmp_path / ".coverage.shard1"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE file (id integer primary key, path text, unique (path))")
        conn.execute(
            "INSERT INTO file VALUES (1, ?)",
            (r"C:\Users\runneradmin\AppData\Local\Temp\artha-ws-abc\checkout\scripts\catchup_core\storage_adoption.py",),
        )
    assert merger.normalize_coverage_db(db, REPO) == 1
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT path FROM file WHERE id = 1").fetchone()[0]
    assert row == str((REPO / "scripts" / "catchup_core" / "storage_adoption.py").resolve())

    manifest = {"commit": "abc", "run_id": "10", "run_attempt": "1", "shard": 1}
    manifest2 = {"commit": "abc", "run_id": "10", "run_attempt": "1", "shard": 2}
    merger.verify_shards([manifest, manifest2], [{"t1"}, {"t2"}], {"t1", "t2"}, commit="abc", run_id="10", run_attempt="1")
    with pytest.raises(ValueError, match="zero tests"):
        merger.verify_shards([manifest, manifest2], [{"t1"}, set()], {"t1", "t2"}, commit="abc", run_id="10", run_attempt="1")
    with pytest.raises(ValueError, match="overlap"):
        merger.verify_shards([manifest, manifest2], [{"t1"}, {"t1", "t2"}], {"t1", "t2"}, commit="abc", run_id="10", run_attempt="1")
    with pytest.raises(ValueError, match="provenance"):
        merger.verify_shards([manifest], [{"t1"}], {"t1"}, commit="other", run_id="10", run_attempt="1")
