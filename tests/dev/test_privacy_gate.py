"""WP-00 privacy gate canaries (WP00.AC7-AC9): real hooks in throw-away repos.

All secret-like strings are assembled at runtime so that this file itself never
matches a scanner pattern.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / ".githooks"
GITLEAKS = shutil.which("gitleaks")

AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
GH_TOKEN = "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    clt = Path("/Library/Developer/CommandLineTools")
    if sys.platform == "darwin" and clt.is_dir():
        env["DEVELOPER_DIR"] = str(clt)
    env.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
    })
    env.update(extra or {})
    return env


def out(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout or "") + (result.stderr or "")


class Sandbox:
    def __init__(self, root: Path, extra_env: dict[str, str] | None = None):
        self.root = root
        self.extra_env = extra_env or {}

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True,
                                env=_env(self.extra_env))
        if check and result.returncode != 0:
            raise AssertionError(f"git {args} failed: {out(result)}")
        return result

    def write(self, rel: str, content: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, message: str = "c", check: bool = True) -> subprocess.CompletedProcess[str]:
        self.git("add", "-A")
        return self.git("commit", "-m", message, check=check)


@pytest.fixture
def stub_gitleaks(tmp_path):
    """A gitleaks that always passes: the regex layers are what most tests exercise,
    and CI test runners do not install gitleaks (the privacy-gate job does)."""
    path = tmp_path / "gitleaks-stub"
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def sandbox(tmp_path, stub_gitleaks):
    root = tmp_path / "work"
    (root / ".githooks").mkdir(parents=True)
    (root / "scripts").mkdir()
    for name in ("privacy_scan.py", "pre-commit", "pre-push"):
        shutil.copy(HOOKS / name, root / ".githooks" / name)
        (root / ".githooks" / name).chmod(
            (root / ".githooks" / name).stat().st_mode | stat.S_IXUSR)
    shutil.copy(REPO / "scripts" / "pii_guard.py", root / "scripts" / "pii_guard.py")
    sb = Sandbox(root, {"ARTHA_GITLEAKS": stub_gitleaks})
    sb.git("init", "-q", "-b", "main")
    sb.git("config", "core.hooksPath", ".githooks")
    sb.git("config", "commit.gpgsign", "false")
    sb.write("README.md", "# safe\n")
    return sb


def test_clean_commit_passes(sandbox):
    result = sandbox.commit("clean")
    assert result.returncode == 0


@pytest.mark.parametrize("rel", [
    "deep/er/google-token.json",
    "x/y/my_creds.json",
    "-Some Host.artha-decrypted",
    "nested/dir/export.jsonl",
    "data/local.db",
])
def test_forbidden_names_blocked_at_any_depth(sandbox, rel):
    sandbox.write(rel, "{}\n")
    result = sandbox.commit("bad", check=False)
    assert result.returncode != 0
    assert "BLOCKED" in out(result)


def test_exempt_fixture_path_allowed_unlisted_fixture_blocked(sandbox):
    sandbox.write("tests/fixtures/kids/emails.jsonl", "{}\n")
    assert sandbox.commit("ok", check=False).returncode == 0
    sandbox.write("tests/fixtures/kids_school/emails.jsonl", "{}\n")
    assert sandbox.commit("not listed", check=False).returncode != 0


def test_secret_in_renamed_file_blocked_by_content(sandbox):
    sandbox.write("notes/innocent.cfg", f"key = {AWS_KEY}\n")
    result = sandbox.commit("secret", check=False)
    assert result.returncode != 0
    assert "AWS access key" in out(result)


def test_pii_in_markdown_blocked(sandbox):
    sandbox.write("docs/note.md", "ssn 123-" + "45-" + "6789\n")
    assert sandbox.commit("pii", check=False).returncode != 0


def test_scanner_unavailable_fails_closed(sandbox):
    (sandbox.root / "scripts" / "pii_guard.py").unlink()
    sandbox.write("docs/note.md", "harmless text\n")
    sandbox.git("add", "-A")
    result = sandbox.git("commit", "-m", "x", check=False)
    assert result.returncode != 0
    assert "fail closed" in out(result)


def test_missing_gitleaks_fails_closed(tmp_path, sandbox):
    sandbox.extra_env["ARTHA_GITLEAKS"] = str(tmp_path / "no-such-gitleaks")
    sandbox.write("docs/a.txt", "hello\n")
    result = sandbox.commit("x", check=False)
    assert result.returncode != 0
    assert "gitleaks not installed" in out(result)


@pytest.fixture
def remote(tmp_path, sandbox):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, env=_env())
    sandbox.git("remote", "add", "origin", str(bare))
    return bare


def test_push_scans_every_commit_not_just_tip(sandbox, remote):
    sandbox.commit("base")
    sandbox.git("push", "-q", "origin", "main")
    # Commit the secret with hooks off (simulates an old/unhooked commit), remove it later.
    sandbox.write("src/leak.txt", f"token={GH_TOKEN}\n")
    sandbox.git("add", "-A")
    sandbox.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "leak")
    (sandbox.root / "src" / "leak.txt").unlink()
    sandbox.git("add", "-A")
    sandbox.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "remove")
    sandbox.write("src/ok.txt", "fine\n")
    sandbox.git("add", "-A")
    sandbox.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "tip")

    result = sandbox.git("push", "origin", "main", check=False)
    assert result.returncode != 0
    assert "src/leak.txt" in out(result)
    assert "GitHub token" in out(result)


def test_clean_push_succeeds(sandbox, remote):
    sandbox.commit("base")
    sandbox.write("src/a.txt", "fine\n")
    sandbox.commit("more")
    assert sandbox.git("push", "origin", "main", check=False).returncode == 0


def test_first_push_audits_whole_history(sandbox, remote):
    sandbox.write("old.txt", f"k={AWS_KEY}\n")
    sandbox.git("add", "-A")
    sandbox.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "old leak")
    (sandbox.root / "old.txt").unlink()
    sandbox.write("new.txt", "ok\n")
    sandbox.git("add", "-A")
    sandbox.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "clean tip")
    assert sandbox.git("push", "origin", "main", check=False).returncode != 0


def test_range_scan_unit_reports_missing_git_repo(tmp_path):
    sys.path.insert(0, str(HOOKS))
    import privacy_scan  # noqa: PLC0415
    findings = privacy_scan.scan_range(tmp_path, ["HEAD"])
    assert findings and "fail closed" in findings[0].reason


@pytest.mark.skipif(GITLEAKS is None, reason="gitleaks not installed")
def test_repository_gitleaks_config_is_valid(tmp_path):
    """A malformed .gitleaks.toml exits 1 like a finding; it must be caught here."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = subprocess.run(
        [GITLEAKS, "dir", "--no-banner", "-l", "error", "-c", str(REPO / ".gitleaks.toml"), str(empty)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(GITLEAKS is None, reason="gitleaks not installed")
def test_gitleaks_config_error_is_reported_as_scanner_error_not_a_leak(tmp_path, sandbox):
    sandbox.extra_env["ARTHA_GITLEAKS"] = GITLEAKS
    (sandbox.root / ".gitleaks.toml").write_text("this is [not valid toml\n")
    sandbox.write("docs/a.txt", "hello\n")
    result = sandbox.commit("x", check=False)
    assert result.returncode != 0
    assert "scanner error" in out(result)


def test_household_first_names_are_denylisted_but_the_owner_handle_is_not(tmp_path):
    """A first name alone identifies a spouse/child/sibling; the owner's own name is public by design."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ps_under_test", HOOKS / "privacy_scan.py")
    ps = importlib.util.module_from_spec(spec)
    sys.modules["ps_under_test"] = ps
    spec.loader.exec_module(ps)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "user_profile.yaml").write_text(
        "family:\n"
        "  primary_user:\n    name: Zed Ownerson\n"
        "  spouse:\n    name: Quillan Ownerson\n"
        "  children:\n    - name: Brindle Ownerson\n      age: 9\n"
        "      school:\n        name: Riverbend Elementary\n"
        "  siblings:\n    - name: Corvane Ownerson\n"
    )
    deny = ps.load_denylist(tmp_path)
    hits = lambda text: deny.match(text)  # noqa: E731
    assert hits("Brindle has a recital")                    # child, first name only
    assert hits("call Quillan tomorrow")                    # spouse
    assert hits("Corvane lives abroad")                     # sibling
    assert not hits("Zed wrote this")                       # owner first name is not household-private
    assert hits("Zed Ownerson")                             # full name still is
    assert not hits("Riverbend is a place")                 # a school's name is not a person
