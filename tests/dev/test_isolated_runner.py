"""WP-00: the suite must run against a clean copy of the checkout, never live data."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

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
