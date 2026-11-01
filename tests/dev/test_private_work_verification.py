"""Metadata-only local evidence for the explicitly reviewed private Work selection."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("private_runner", REPO / "tools/run_tests_isolated.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def source(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    env = runner._git_env()
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    subprocess.run(["git", "init", "-q"], cwd=root, env=env, check=True, capture_output=True)
    (root / ".gitignore").write_text("scripts/work*\ntests/work/\nscratch/\n")
    (root / "public.py").write_text("PUBLIC = 1\n")
    subprocess.run(["git", "add", ".gitignore", "public.py"], cwd=root, env=env,
                   check=True, capture_output=True)
    for rel in runner.PRIVATE_WORK_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthetic code\n")
    (root / runner.PRIVATE_WORK_TEST).write_text(
        "def test_synthetic():\n    print('WORK_PAYLOAD_SENTINEL')\n    assert True\n")
    (root / "scripts/work/unreviewed.py").write_text("SECRET = 'DO NOT COPY'\n")
    (root / "tests/work/conftest.py").write_text("raise RuntimeError('private conftest')\n")
    workspace_parent = tmp_path / "external-workspaces"
    workspace_parent.mkdir()
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(workspace_parent))
    return root


def test_default_does_not_copy_private_source(source, tmp_path):
    dest = tmp_path / "default"
    runner.build_workspace(source, dest)
    assert (dest / "public.py").exists()
    assert not (dest / runner.PRIVATE_WORK_TEST).exists()
    assert not (dest / "scripts/work_loop.py").exists()


def test_opt_in_copies_only_reviewed_private_files_and_binds_public_bytes(source, tmp_path):
    dest = tmp_path / "isolated"
    manifest = runner._private_manifest(source)
    runner._build_private_workspace(source, dest, manifest)
    assert (dest / "public.py").read_bytes() == (source / "public.py").read_bytes()
    for rel in runner.PRIVATE_WORK_FILES:
        assert (dest / rel).read_bytes() == (source / rel).read_bytes()
    assert not (dest / "tests/work/conftest.py").exists()
    assert not (dest / "scripts/work/unreviewed.py").exists()
    assert manifest["public.py"] == hashlib.sha256((dest / "public.py").read_bytes()).hexdigest()


@pytest.mark.parametrize("kind", ["missing", "private-change", "public-change", "symlink"])
def test_source_missing_changed_or_linked_refused(source, tmp_path, kind):
    manifest = runner._private_manifest(source)
    path = source / ("public.py" if kind == "public-change" else runner.PRIVATE_WORK_FILES[0])
    if kind == "missing":
        path.unlink()
    elif kind == "symlink":
        path.unlink()
        try:
            path.symlink_to(source / "public.py")
        except OSError:
            pytest.skip("host cannot create symlinks")
    else:
        path.write_text("changed\n")
    with pytest.raises(ValueError):
        runner._build_private_workspace(source, tmp_path / "isolated", manifest)


def test_missing_private_file_refused_before_build(source):
    (source / runner.PRIVATE_WORK_FILES[0]).unlink()
    with pytest.raises(ValueError):
        runner._private_manifest(source)


@pytest.mark.parametrize("rel", ["../outside.py", str(REPO / "absolute.py")])
def test_unsafe_source_names_refused(source, rel):
    with pytest.raises(ValueError):
        runner._source_hash(source, rel)


def test_git_enumeration_failure_is_bounded_and_no_evidence(source, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"WORK_PAYLOAD_SENTINEL"))
    evidence = tmp_path / "git-error.json"
    assert runner.verify_private_work(source, evidence, []) == 2
    assert not evidence.exists()
    assert "WORK_PAYLOAD_SENTINEL" not in "".join(capsys.readouterr())


@pytest.mark.parametrize("kind", ["inside", "relative", "existing", "missing-parent", "traversal", "suffix"])
def test_unsafe_evidence_destinations_refused(source, tmp_path, kind):
    paths = {
        "inside": source / "evidence.json",
        "relative": Path("evidence.json"),
        "existing": tmp_path / "existing.json",
        "missing-parent": tmp_path / "absent/evidence.json",
        "traversal": tmp_path / "source/../evidence.json",
        "suffix": tmp_path / "evidence.py",
    }
    paths["existing"].write_text("keep")
    with pytest.raises(ValueError):
        runner._evidence_path(paths[kind], source)
    assert paths["existing"].read_text() == "keep"


def test_symlink_evidence_parent_refused(source, tmp_path):
    link = tmp_path / "linked"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("host cannot create symlinks")
    with pytest.raises(ValueError):
        runner._evidence_path(link / "evidence.json", source)


@pytest.mark.parametrize("env_name", ["OneDrive", "OneDriveConsumer", "OneDriveCommercial"])
def test_evidence_under_known_sync_environment_root_refused(source, tmp_path, monkeypatch, env_name):
    sync = tmp_path / "company-sync"
    sync.mkdir()
    monkeypatch.setenv(env_name, str(sync))
    with pytest.raises(ValueError):
        runner._evidence_path(sync / "record.json", source)


@pytest.mark.parametrize("name", ["OneDrive", "OneDrive - Employer", "CloudStorage"])
def test_evidence_under_sync_ancestor_refused(source, tmp_path, name):
    folder = tmp_path / name / "nested"
    folder.mkdir(parents=True)
    with pytest.raises(ValueError):
        runner._evidence_path(folder / "record.json", source)


def test_appdata_session_evidence_destination_remains_allowed(source, tmp_path):
    folder = tmp_path / "AppData" / "Roaming" / "copilot" / "session"
    folder.mkdir(parents=True)
    path = folder / "record.json"
    assert runner._evidence_path(path, source) == path.resolve()


def test_inaccessible_source_lstat_is_not_silently_false(source, monkeypatch):
    original = Path.lstat
    blocked = source / runner.PRIVATE_WORK_FILES[0]

    def lstat(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("inaccessible source")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(PermissionError):
        runner._private_manifest(source)


def test_source_reparse_point_refused_even_when_not_reported_as_symlink(source, monkeypatch):
    from types import SimpleNamespace
    import stat

    original = Path.lstat
    blocked = source / runner.PRIVATE_WORK_FILES[0]

    def lstat(path, *args, **kwargs):
        if path == blocked:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError):
        runner._private_manifest(source)


@pytest.mark.parametrize("kind", ["inaccessible", "reparse-parent"])
def test_evidence_metadata_access_fails_closed(source, tmp_path, monkeypatch, kind):
    from types import SimpleNamespace
    import stat

    original = Path.lstat
    folder = tmp_path / "external"
    folder.mkdir()

    def lstat(path, *args, **kwargs):
        if path == folder:
            if kind == "inaccessible":
                raise PermissionError("inaccessible evidence parent")
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(PermissionError if kind == "inaccessible" else ValueError):
        runner._evidence_path(folder / "record.json", source)


def test_nonregular_source_refused(source):
    path = source / runner.PRIVATE_WORK_FILES[0]
    path.unlink()
    path.mkdir()
    with pytest.raises(ValueError):
        runner._private_manifest(source)


def test_non_directory_ancestor_refused(source, monkeypatch):
    from types import SimpleNamespace
    import stat

    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == source:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError):
        runner._private_manifest(source)


def test_relative_sync_environment_root_refused(source, tmp_path, monkeypatch):
    monkeypatch.setenv("OneDrive", "relative-sync")
    with pytest.raises(ValueError):
        runner._evidence_path(tmp_path / "evidence.json", source)


def test_non_directory_workspace_parent_refused(source, tmp_path, monkeypatch):
    parent = tmp_path / "not-a-directory"
    parent.write_text("regular file")
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(parent))
    evidence = tmp_path / "record.json"
    assert runner.verify_private_work(source, evidence, []) == 2
    assert json.loads(evidence.read_text())["outcome"] == "refused"


@pytest.mark.parametrize("exit_code", [0, 1, 5, -9])
def test_outcome_exit_arguments_fingerprints_and_no_payload(source, tmp_path, monkeypatch, capsys, exit_code):
    evidence = tmp_path / "evidence.json"
    original = runner.subprocess.run
    monkeypatch.setenv("PYTEST_ADDOPTS", "--junitxml=UNSAFE_OUTPUT")
    monkeypatch.setenv("PYTEST_PLUGINS", "unreviewed_plugin")

    def execute(args, **kwargs):
        if args[0] == "git":
            return original(args, **kwargs)
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        assert "PYTEST_PLUGINS" not in kwargs["env"]
        assert Path(kwargs["cwd"]).joinpath(runner.PRIVATE_WORK_TEST).exists()
        return subprocess.CompletedProcess(args, exit_code)

    monkeypatch.setattr(runner.subprocess, "run", execute)
    args = [runner.PRIVATE_WORK_TEST, "-q"]
    before_index = (source / ".git/index").read_bytes() if (source / ".git/index").exists() else None
    result = runner.verify_private_work(source, evidence, args)
    assert result == exit_code
    body = evidence.read_text()
    record = json.loads(body)
    assert record["outcome"] == ("passed" if exit_code == 0 else "failed")
    assert record["returncode"] == exit_code
    assert record["verification_exitcode"] == exit_code
    assert record["pytest_args"] == args
    assert record["command"] == [Path(sys.executable).name, "-m", "pytest",
                                 "-p", "no:cacheprovider", *args]
    assert record["runtime"]["python_version"] == sys.version
    assert record["runtime"]["executable_sha256"]
    assert runner.PRIVATE_WORK_TEST in record["sources"]
    assert str(source) not in body
    assert "WORK_PAYLOAD_SENTINEL" not in body + "".join(capsys.readouterr())
    assert before_index == ((source / ".git/index").read_bytes() if (source / ".git/index").exists() else None)
    assert not list(source.parent.joinpath("external-workspaces").iterdir())


def test_real_failed_test_output_is_not_emitted(source, tmp_path, capsys):
    (source / runner.PRIVATE_WORK_TEST).write_text(
        "def test_synthetic():\n    print('WORK_PAYLOAD_SENTINEL')\n"
        "    raise AssertionError('WORK_PAYLOAD_SENTINEL')\n")
    evidence = tmp_path / "failed.json"
    assert runner.verify_private_work(source, evidence, [runner.PRIVATE_WORK_TEST, "-q"]) == 1
    assert json.loads(evidence.read_text())["outcome"] == "failed"
    assert "WORK_PAYLOAD_SENTINEL" not in "".join(capsys.readouterr()) + evidence.read_text()


@pytest.mark.parametrize("copy_changed", [False, True])
def test_source_changed_during_test_cannot_produce_success(source, tmp_path, monkeypatch, copy_changed):
    original = runner.subprocess.run

    def execute(args, **kwargs):
        if args[0] == "git":
            return original(args, **kwargs)
        code = Path(kwargs["cwd"]) if copy_changed else source
        (code / "public.py").write_text("changed during test\n")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(runner.subprocess, "run", execute)
    evidence = tmp_path / "changed.json"
    assert runner.verify_private_work(source, evidence, [runner.PRIVATE_WORK_TEST]) == 2
    assert json.loads(evidence.read_text())["outcome"] == "refused"


@pytest.mark.parametrize("kind", ["onedrive-temp", "environment-sync", "checkout", "linked-parent"])
def test_unsafe_workspace_parent_refused_before_any_source_copy(source, tmp_path, monkeypatch, kind):
    parent = tmp_path / ("OneDrive" if kind == "onedrive-temp" else "external-workspaces")
    parent.mkdir(exist_ok=True)
    if kind == "environment-sync":
        monkeypatch.setenv("OneDriveCommercial", str(parent))
    elif kind == "checkout":
        parent = source
    elif kind == "linked-parent":
        link = tmp_path / "linked-workspaces"
        try:
            link.symlink_to(parent, target_is_directory=True)
        except OSError:
            pytest.skip("host cannot create symlinks")
        parent = link
    monkeypatch.setenv("TEMP", str(parent))
    monkeypatch.setattr(runner.tempfile, "gettempdir", lambda: str(parent))
    copied = []
    monkeypatch.setattr(runner, "_build_private_workspace", lambda *args: copied.append(args))
    evidence = tmp_path / "unsafe-workspace.json"
    assert runner.verify_private_work(source, evidence, []) == 2
    assert json.loads(evidence.read_text())["outcome"] == "refused"
    assert not copied
    assert not list(parent.glob("artha-private-*"))


def test_external_default_workspace_does_not_depend_on_checkout_scratch_ignore(source, tmp_path, monkeypatch):
    (source / ".gitignore").write_text("scripts/work*\ntests/work/\n")
    evidence = tmp_path / "external.json"
    original = runner.subprocess.run

    def execute(args, **kwargs):
        if args[0] == "git":
            return original(args, **kwargs)
        workspace = Path(kwargs["cwd"]).parent
        assert workspace.parent == Path(runner.tempfile.gettempdir())
        assert not workspace.is_relative_to(source)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.verify_private_work(source, evidence, []) == 0
    assert not (source / "scratch").exists()
    assert not list(source.parent.joinpath("external-workspaces").iterdir())


def test_cleanup_failure_is_nonzero_and_never_success_shaped(source, tmp_path, monkeypatch, capsys):
    evidence = tmp_path / "cleanup.json"
    original_run = runner.subprocess.run
    original_cleanup = runner.shutil.rmtree
    retained = []

    def execute(args, **kwargs):
        if args[0] == "git":
            return original_run(args, **kwargs)
        return subprocess.CompletedProcess(args, 0)

    def cleanup(path):
        retained.append(path)
        raise OSError("WORK_PAYLOAD_SENTINEL")

    monkeypatch.setattr(runner.subprocess, "run", execute)
    monkeypatch.setattr(runner.shutil, "rmtree", cleanup)
    try:
        assert runner.verify_private_work(source, evidence, []) == 2
        record = json.loads(evidence.read_text())
        assert record["outcome"] == "cleanup-failed"
        assert record["returncode"] == 0
        assert record["verification_exitcode"] == 2
        assert retained[0].exists()
        assert "WORK_PAYLOAD_SENTINEL" not in evidence.read_text() + "".join(capsys.readouterr())
    finally:
        for path in retained:
            original_cleanup(path)


def test_new_source_during_copy_is_not_unfingerprinted(source, tmp_path, monkeypatch):
    manifest = runner._private_manifest(source)
    original = runner.build_workspace

    def build(root, dest):
        (root / "late.py").write_text("LATE = 1\n")
        return original(root, dest)

    monkeypatch.setattr(runner, "build_workspace", build)
    with pytest.raises(ValueError):
        runner._build_private_workspace(source, tmp_path / "isolated", manifest)


def test_child_launch_error_records_refusal_not_success(source, tmp_path, monkeypatch):
    original = runner.subprocess.run

    def execute(args, **kwargs):
        if args[0] == "git":
            return original(args, **kwargs)
        raise OSError("WORK_PAYLOAD_SENTINEL")

    monkeypatch.setattr(runner.subprocess, "run", execute)
    evidence = tmp_path / "error.json"
    assert runner.verify_private_work(source, evidence, []) == 2
    record = json.loads(evidence.read_text())
    assert record["outcome"] == "refused"
    assert record["returncode"] is None
    assert "WORK_PAYLOAD_SENTINEL" not in evidence.read_text()


def test_evidence_created_during_run_is_not_overwritten(source, tmp_path, monkeypatch):
    evidence = tmp_path / "race.json"
    original = runner.subprocess.run

    def execute(args, **kwargs):
        if args[0] == "git":
            return original(args, **kwargs)
        evidence.write_text("KEEP")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.verify_private_work(source, evidence, []) == 2
    assert evidence.read_text() == "KEEP"


@pytest.mark.parametrize("args", [["--private-work"], ["--private-work", "--evidence"]])
def test_main_requires_explicit_evidence_destination(args):
    assert runner.main(args) == 2


def test_main_routes_only_explicit_opt_in(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(runner, "verify_private_work",
                        lambda *args: calls.append(args) or 7)
    path = tmp_path / "record.json"
    assert runner.main(["--private-work", "--evidence", str(path)]) == 7
    assert calls == [(REPO, path, [])]


@pytest.mark.parametrize("args", [["tests"], ["--junitxml=secret.xml"], [runner.__name__]])
def test_unreviewed_selection_or_output_option_refused(source, tmp_path, args):
    evidence = tmp_path / "evidence.json"
    assert runner.verify_private_work(source, evidence, args) == 2
    assert not evidence.exists()
