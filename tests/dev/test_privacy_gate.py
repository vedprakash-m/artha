"""WP-00 privacy gate canaries (WP00.AC7-AC9): real hooks in throw-away repos.

All secret-like strings are assembled at runtime so that this file itself never
matches a scanner pattern.
"""
from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / ".githooks"
GITLEAKS = shutil.which("gitleaks")
BASH = shutil.which("bash")

AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
GH_TOKEN = "ghp" + "_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


@pytest.fixture(autouse=True)
def isolated_privacy_data(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTHA_LOCAL_DIR", str(tmp_path / "local"))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))


def _env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    clt = Path("/Library/Developer/CommandLineTools")
    if sys.platform == "darwin" and clt.is_dir():
        env["DEVELOPER_DIR"] = str(clt)
    env.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        "PYTHONUTF8": "1",
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
                                encoding="utf-8", env=_env(self.extra_env))
        if check and result.returncode != 0:
            raise AssertionError(f"git {args} failed: {out(result)}")
        return result

    def write(self, rel: str, content: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    def commit(self, message: str = "c", check: bool = True) -> subprocess.CompletedProcess[str]:
        self.git("add", "-A")
        return self.git("commit", "-m", message, check=check)


@pytest.fixture
def stub_gitleaks(tmp_path):
    """A gitleaks that always passes: the regex layers are what most tests exercise,
    and CI test runners do not install gitleaks (the privacy-gate job does)."""
    script = tmp_path / "gitleaks-stub.py"
    script.write_text(
        f"#!{sys.executable}\nprint('[]')\n", encoding="utf-8", newline="\n",
    )
    if os.name == "nt":
        launcher = script.with_suffix(".cmd")
        launcher.write_text(
            f'@"{sys.executable}" "{script}" %*\n',
            encoding="utf-8", newline="\n",
        )
        return str(launcher)
    script.chmod(0o755)
    return str(script)


@pytest.fixture
def sandbox(tmp_path, stub_gitleaks):
    root = tmp_path / "work"
    (root / ".githooks").mkdir(parents=True)
    (root / "scripts").mkdir()
    for name in ("privacy_scan.py", "pre-commit", "pre-push"):
        shutil.copy(HOOKS / name, root / ".githooks" / name)
        if name != "privacy_scan.py":
            # Git runs the real hook body with this test's interpreter, not a
            # system python3 alias (which may be the Windows Store launcher).
            hook = root / ".githooks" / name
            hook.rename(hook.with_suffix(".py"))
            hook.write_text(
                "#!/bin/sh\n"
                f"exec {shlex.quote(Path(sys.executable).as_posix())} "
                f'".githooks/{name}.py" "$@"\n',
                encoding="utf-8", newline="\n",
            )
        (root / ".githooks" / name).chmod(
            (root / ".githooks" / name).stat().st_mode | stat.S_IXUSR)
    shutil.copy(REPO / "scripts" / "pii_guard.py", root / "scripts" / "pii_guard.py")
    shutil.copy(REPO / ".gitleaks.toml", root / ".gitleaks.toml")
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
    sandbox.git("-c", "core.hooksPath=.git/disabled-hooks", "commit", "-q", "-m", "leak")
    (sandbox.root / "src" / "leak.txt").unlink()
    sandbox.git("add", "-A")
    sandbox.git("-c", "core.hooksPath=.git/disabled-hooks", "commit", "-q", "-m", "remove")
    sandbox.write("src/ok.txt", "fine\n")
    sandbox.git("add", "-A")
    sandbox.git("-c", "core.hooksPath=.git/disabled-hooks", "commit", "-q", "-m", "tip")

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
    sandbox.git("-c", "core.hooksPath=.git/disabled-hooks", "commit", "-q", "-m", "old leak")
    (sandbox.root / "old.txt").unlink()
    sandbox.write("new.txt", "ok\n")
    sandbox.git("add", "-A")
    sandbox.git("-c", "core.hooksPath=.git/disabled-hooks", "commit", "-q", "-m", "clean tip")
    assert sandbox.git("push", "origin", "main", check=False).returncode != 0


def test_push_refuses_commits_authored_with_a_personal_email(sandbox, remote):
    sandbox.commit("base")
    sandbox.write("src/a.txt", "fine\n")
    sandbox.git("add", "-A")
    sandbox.extra_env = {"GIT_AUTHOR_EMAIL": "person@example.com", "GIT_COMMITTER_EMAIL": "person@example.com"}
    sandbox.git("commit", "-q", "-m", "personal")
    sandbox.extra_env = {}
    result = sandbox.git("push", "origin", "main", check=False)
    assert result.returncode != 0
    assert "not a GitHub noreply address" in out(result)


def test_push_allows_commits_authored_with_a_github_noreply_email(sandbox, remote):
    sandbox.commit("base")
    sandbox.write("src/a.txt", "fine\n")
    sandbox.git("add", "-A")
    sandbox.extra_env = {"GIT_AUTHOR_EMAIL": "handle@users.noreply.github.com",
                         "GIT_COMMITTER_EMAIL": "handle@users.noreply.github.com"}
    sandbox.git("commit", "-q", "-m", "noreply")
    sandbox.extra_env = {}
    assert sandbox.git("push", "origin", "main", check=False).returncode == 0


def test_range_scan_unit_reports_missing_git_repo(tmp_path):
    sys.path.insert(0, str(HOOKS))
    import privacy_scan  # noqa: PLC0415
    findings = privacy_scan.scan_range(tmp_path, ["HEAD"])
    assert findings and "fail closed" in findings[0].reason


@pytest.mark.parametrize("blocked", [False, True])
def test_scanner_cli_reports_utf8_when_stdout_defaults_to_cp1252(blocked):
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(HOOKS)!r})\n"
        "import privacy_scan\n"
        f"findings = [privacy_scan.Finding('docs/café.md', 'synthetic denial')] if {blocked!r} else []\n"
        "privacy_scan.scan_tracked = lambda repo: findings\n"
        "sys.exit(privacy_scan.main(['--tracked']))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, timeout=30,
        env=_env({"PYTHONIOENCODING": "cp1252"}),
    )
    assert result.returncode == int(blocked), result.stderr.decode("utf-8")
    output = result.stdout.decode("utf-8")
    assert ("⛔" if blocked else "✓") in output
    if blocked:
        assert "docs/café.md" in output and "synthetic denial" in output


@pytest.fixture
def privacy_scanner(monkeypatch):
    monkeypatch.syspath_prepend(str(HOOKS))
    import privacy_scan
    return privacy_scan


@pytest.mark.parametrize("content", [f"key = {AWS_KEY}\n", "contact = Synthetic Person\n"])
def test_tracked_scan_blocks_untracked_nonignored_content(sandbox, monkeypatch, content, privacy_scanner):
    privacy_scan = privacy_scanner
    deny = privacy_scan.Denylist()
    deny.add("name", "Synthetic Person")
    monkeypatch.setattr(privacy_scan, "denylist_for", lambda repo: deny)
    monkeypatch.setenv("ARTHA_GITLEAKS", sandbox.extra_env["ARTHA_GITLEAKS"])
    sandbox.write("scripts/new module.py", content)
    findings = privacy_scan.scan_tracked(sandbox.root, guard=sandbox.root / "scripts/pii_guard.py")
    assert any(finding.path == "scripts/new module.py" for finding in findings)


def test_tracked_scan_gitleaks_receives_selected_bytes_only(sandbox, monkeypatch, privacy_scanner):
    privacy_scan = privacy_scanner
    monkeypatch.setattr(privacy_scan, "denylist_for", lambda repo: privacy_scan.Denylist())
    sandbox.write(".gitignore", "private/\n")
    sandbox.write("private/payload.py", "must never be scanned\n")
    sandbox.write("scripts/tracked.py", "old bytes\n")
    sandbox.git("add", "scripts/tracked.py")
    sandbox.write("scripts/tracked.py", "current bytes\n")
    raw = b"new bytes\xff\n"
    (sandbox.root / "scripts/new module.py").write_bytes(raw)
    calls = []
    expected = {rel: (sandbox.root / rel).read_bytes() for rel in
                sandbox.git("ls-files", "-co", "--exclude-standard", "-z").stdout.split("\0")
                if rel and rel not in {"scripts/pii_guard.py"}}
    config_checks = []
    original_config = privacy_scan._gitleaks_config
    original_open = Path.open

    def config_check(repo):
        config_checks.append(repo)
        return original_config(repo)

    def read_only_open(path, mode="r", *args, **kwargs):
        assert not any(flag in mode for flag in "wax+"), "scanner opened a writable payload file"
        return original_open(path, mode, *args, **kwargs)

    def scanner(args, **kwargs):
        calls.append(kwargs["input"])
        assert args[1] == "stdin"
        assert kwargs.get("text", False) is False
        assert "-c" in args and "-r" in args and args[args.index("-r") + 1] == "-"
        if kwargs["input"] == raw:
            return subprocess.CompletedProcess(args, 1, b'[{"File":"stdin","RuleID":"canary"}]', b"")
        return subprocess.CompletedProcess(args, 0, b"[]", b"")

    def reject_write(*args, **kwargs):
        pytest.fail("scanner must not create temporary files or write payloads")

    original = subprocess.run
    monkeypatch.setattr(privacy_scan.subprocess, "run", lambda args, **kwargs:
                        scanner(args, **kwargs) if args[0] == sandbox.extra_env["ARTHA_GITLEAKS"]
                        else original(args, **kwargs))
    monkeypatch.setenv("ARTHA_GITLEAKS", sandbox.extra_env["ARTHA_GITLEAKS"])
    monkeypatch.setattr(tempfile, "TemporaryDirectory", reject_write)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", reject_write)
    monkeypatch.setattr(tempfile, "mkstemp", reject_write)
    monkeypatch.setattr(Path, "write_bytes", reject_write)
    monkeypatch.setattr(Path, "write_text", reject_write)
    monkeypatch.setattr(Path, "open", read_only_open)
    monkeypatch.setattr(privacy_scan, "_gitleaks_config", config_check)
    findings = privacy_scan.scan_tracked(sandbox.root, guard=sandbox.root / "scripts/pii_guard.py")
    assert config_checks == [sandbox.root]
    assert sorted(calls) == sorted(expected.values())
    assert b"current bytes\n" in calls and raw in calls and b"old bytes\n" not in calls
    assert any("canary" in finding.reason and finding.path == "scripts/new module.py"
               for finding in findings)


@pytest.mark.parametrize("rel", [
    "tests/unit/test_backup.py", "tests/unit/test_vault.py", "tests/unit/test_pii_guard.py",
    "tests/dev/test_privacy_gate.py", "scripts/pii_guard.py",
])
def test_stdin_exemptions_are_exact_reviewed_paths(sandbox, monkeypatch, privacy_scanner, rel):
    raw = b"SYNTHETIC_SCANNER_CANARY"
    sandbox.write(rel, raw.decode())
    sandbox.write("nested/" + rel, raw.decode())
    sandbox.write("scripts/backup.py", raw.decode())
    calls = []
    original = subprocess.run

    def scanner(args, **kwargs):
        calls.append(kwargs.get("input"))
        return subprocess.CompletedProcess(args, 1, b'[{"File":"stdin","RuleID":"canary"}]', b"")

    monkeypatch.setattr(privacy_scanner.subprocess, "run", lambda args, **kwargs:
                        scanner(args, **kwargs) if args[0] == sandbox.extra_env["ARTHA_GITLEAKS"]
                        else original(args, **kwargs))
    monkeypatch.setenv("ARTHA_GITLEAKS", sandbox.extra_env["ARTHA_GITLEAKS"])
    monkeypatch.setattr(privacy_scanner, "denylist_for", lambda repo: privacy_scanner.Denylist())
    findings = privacy_scanner.scan_tracked(sandbox.root)
    assert any(finding.path == "nested/" + rel for finding in findings)
    assert any(finding.path == "scripts/backup.py" for finding in findings)
    assert not any(finding.path == rel for finding in findings)
    assert calls.count(raw) == 2


@pytest.mark.parametrize("config", [None, "broken = [", "broad", "extra"])
def test_tracked_scan_rejects_missing_malformed_or_broadened_policy(
    sandbox, monkeypatch, privacy_scanner, config,
):
    policy = sandbox.root / ".gitleaks.toml"
    if config is None:
        policy.unlink()
    elif config == "broad":
        policy.write_text(policy.read_text().replace("^tests/", "tests/"))
    elif config == "extra":
        policy.write_text(policy.read_text() + '\n[[allowlists]]\npaths = [".*"]\n')
    else:
        policy.write_text(config)
    monkeypatch.setenv("ARTHA_GITLEAKS", sandbox.extra_env["ARTHA_GITLEAKS"])
    findings = privacy_scanner.scan_tracked(sandbox.root)
    assert any("fail closed" in finding.reason and "config" in finding.reason for finding in findings)


@pytest.mark.parametrize(("code", "report"), [
    (0, b""), (0, b"not json"), (0, b"{}"), (1, b"[1]"), (1, b"[]"),
    (0, b'[{"File":"stdin","RuleID":"canary"}]'), (2, b"[]"),
    (1, b'[{"File":"stdin"}]'), (1, b'[{"RuleID":"PAYLOAD\\nSECRET"}]'),
])
def test_stdin_scanner_failures_are_closed_without_payload(
    sandbox, monkeypatch, privacy_scanner, code, report,
):
    original = subprocess.run
    monkeypatch.setattr(privacy_scanner.subprocess, "run", lambda args, **kwargs:
                        subprocess.CompletedProcess(args, code, report, b"PRIVATE_PAYLOAD")
                        if args[0] == sandbox.extra_env["ARTHA_GITLEAKS"] else original(args, **kwargs))
    monkeypatch.setenv("ARTHA_GITLEAKS", sandbox.extra_env["ARTHA_GITLEAKS"])
    findings = privacy_scanner.scan_tracked(sandbox.root)
    assert findings and any("fail closed" in finding.reason for finding in findings)
    assert all("PRIVATE_PAYLOAD" not in finding.render() and "PAYLOAD" not in finding.reason
               for finding in findings)


@pytest.mark.parametrize("failure", ["missing", "launch", "timeout"])
def test_stdin_launch_failures_attach_selected_path(sandbox, monkeypatch, privacy_scanner, failure):
    sandbox.write("scripts/selected.py", "safe bytes\n")
    original = subprocess.run

    def scanner(args, **kwargs):
        if args[0] == sandbox.extra_env["ARTHA_GITLEAKS"]:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 1, output=b"PRIVATE_PAYLOAD")
            raise OSError("PRIVATE_PAYLOAD")
        return original(args, **kwargs)

    monkeypatch.setattr(privacy_scanner.subprocess, "run", scanner)
    monkeypatch.setenv("ARTHA_GITLEAKS", sandbox.extra_env["ARTHA_GITLEAKS"]
                       if failure != "missing" else str(sandbox.root / "missing-scanner"))
    findings = privacy_scanner.scan_tracked(sandbox.root)
    assert any(finding.path == "scripts/selected.py" and "fail closed" in finding.reason
               for finding in findings)
    assert all("PRIVATE_PAYLOAD" not in finding.render() for finding in findings)


@pytest.mark.skipif(GITLEAKS is None, reason="gitleaks not installed")
def test_real_gitleaks_path_allowlist_is_anchored(sandbox, privacy_scanner):
    for rel in ("tests/unit/test_backup.py", "nested/tests/unit/test_backup.py",
                "tests/unit/test_backup.py.copy"):
        sandbox.write(rel, f'token = "{GH_TOKEN}"\n')
    sandbox.git("add", "-A")
    findings = privacy_scanner.run_gitleaks(sandbox.root, staged=True, binary=GITLEAKS)
    assert {finding.path for finding in findings} == {
        "nested/tests/unit/test_backup.py", "tests/unit/test_backup.py.copy",
    }


def _hidden_leak_history(sandbox):
    sandbox.write("old.cfg", f"key={AWS_KEY}\n")
    sandbox.git("add", "-A")
    sandbox.git("-c", "core.hooksPath=.git/disabled-hooks", "commit", "-qm", "old leak")
    (sandbox.root / "old.cfg").unlink()
    sandbox.git("add", "-A")
    sandbox.git("-c", "core.hooksPath=.git/disabled-hooks", "commit", "-qm", "clean tip")
    sandbox.git("update-ref", "refs/remotes/origin/main", "HEAD")
    return sandbox.git("rev-parse", "HEAD").stdout.strip()


def test_new_ref_push_scans_history_even_when_origin_contains_tip(sandbox):
    tip = _hidden_leak_history(sandbox)
    result = subprocess.run(
        [sys.executable, str(sandbox.root / ".githooks/pre-push.py"), "origin"],
        input=f"refs/heads/main {tip} refs/heads/new {'0' * 40}\n", cwd=sandbox.root,
        env=_env(sandbox.extra_env), capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode != 0, out(result)
    assert "old.cfg" in out(result)


@pytest.mark.parametrize("before", ["0" * 40, "f" * 40])
def test_ci_range_first_push_and_missing_base_fail_closed(sandbox, before):
    import yaml
    tip = _hidden_leak_history(sandbox)
    workflow = yaml.safe_load((REPO / ".github/workflows/pii-check.yml").read_text())
    steps = next(iter(workflow["jobs"].values()))["steps"]
    script = next(step["run"] for step in steps if "--range" in step.get("run", ""))
    for expression, value in {
        "github.event_name": "push", "github.base_ref": "main",
        "github.event.before": before, "github.sha": tip,
    }.items():
        script = script.replace("${{ " + expression + " }}", value)
    script = script.replace("python .githooks",
                            shlex.quote(Path(sys.executable).as_posix()) + " .githooks")
    result = subprocess.run([BASH, "-e", "-c", script], cwd=sandbox.root,
                            env=_env(sandbox.extra_env), capture_output=True, text=True,
                            encoding="utf-8")
    assert result.returncode != 0, out(result)
    assert ("old.cfg" if before == "0" * 40 else "fail closed") in out(result)


def test_push_missing_nonzero_base_fails_closed(sandbox):
    tip = _hidden_leak_history(sandbox)
    result = subprocess.run(
        [sys.executable, str(sandbox.root / ".githooks/pre-push.py"), "origin"],
        input=f"refs/heads/main {tip} refs/heads/main {'f' * 40}\n", cwd=sandbox.root,
        env=_env(sandbox.extra_env), capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode != 0 and "fail closed" in out(result)


def test_tracked_scan_missing_gitleaks_fails_closed(sandbox, monkeypatch, tmp_path, privacy_scanner):
    privacy_scan = privacy_scanner
    monkeypatch.setattr(privacy_scan, "denylist_for", lambda repo: privacy_scan.Denylist())
    monkeypatch.setenv("ARTHA_GITLEAKS", str(tmp_path / "missing"))
    findings = privacy_scan.scan_tracked(sandbox.root, guard=sandbox.root / "scripts/pii_guard.py")
    assert any("gitleaks not installed" in finding.reason for finding in findings)


def test_tracked_scan_refuses_links_into_ignored_payloads(sandbox, monkeypatch, privacy_scanner):
    monkeypatch.setattr(privacy_scanner, "denylist_for", lambda repo: privacy_scanner.Denylist())
    sandbox.write(".gitignore", "private/\n")
    sandbox.write("private/payload.py", "must not be read\n")
    try:
        (sandbox.root / "linked.py").symlink_to(sandbox.root / "private/payload.py")
    except OSError:
        pytest.skip("symlink creation unavailable")
    findings = privacy_scanner.scan_tracked(sandbox.root)
    assert any("linked path: linked.py" in finding.reason for finding in findings)


@pytest.mark.skipif(GITLEAKS is None, reason="gitleaks not installed")
def test_tracked_scan_real_gitleaks_detects_untracked_token(sandbox, monkeypatch, privacy_scanner):
    monkeypatch.setattr(privacy_scanner, "denylist_for", lambda repo: privacy_scanner.Denylist())
    monkeypatch.setenv("ARTHA_GITLEAKS", GITLEAKS)
    sandbox.write("scripts/new.py", f'token = "{GH_TOKEN}"\n')
    findings = privacy_scanner.scan_tracked(sandbox.root, guard=sandbox.root / "scripts/pii_guard.py")
    assert any("github-pat" in finding.reason and "new.py" in finding.path for finding in findings)


@pytest.mark.skipif(GITLEAKS is None, reason="gitleaks not installed")
def test_repository_gitleaks_config_is_valid(tmp_path):
    """A malformed .gitleaks.toml exits 1 like a finding; it must be caught here."""
    empty = tmp_path / "empty"
    empty.mkdir()
    result = subprocess.run(
        [GITLEAKS, "dir", "--no-banner", "-l", "error", "-c", str(REPO / ".gitleaks.toml"), str(empty)],
        capture_output=True, text=True, encoding="utf-8",
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
