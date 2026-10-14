from __future__ import annotations

import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from catchup_core import extractor_claude as ec


def test_argv_disables_every_capability():
    argv = ec.build_argv({})
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--tools") + 1] == ""
    for flag in ("--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--model" not in argv


def test_model_comes_from_environment_only():
    argv = ec.build_argv({"ARTHA_LLM_MODEL_CLAUDE": "sonnet"})
    assert argv[argv.index("--model") + 1] == "sonnet"


def test_bypass_flag_cannot_be_smuggled_in_via_binary_name():
    from lib.llm_cli_policy import PermissionBypassError
    with pytest.raises(PermissionBypassError):
        ec.build_argv({"ARTHA_CLAUDE_BIN": "--dangerously-skip-permissions"})


def test_runs_in_empty_dir_with_scrubbed_environment():
    seen = {}

    def runner(argv, **kw):
        seen.update(kw)
        seen["cwd_empty"] = list(Path(kw["cwd"]).iterdir()) == []
        return SimpleNamespace(returncode=0, stdout='{"ok": true}')

    env = {"HOME": "/h", "PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": "tok", "OPENAI_API_KEY": "other", "ARTHA_ANYTHING": "x"}
    assert ec.extract("prompt", environ=env, runner=runner) == '{"ok": true}'
    assert seen["cwd_empty"] is True
    assert seen["input"] == "prompt"
    assert set(seen["env"]) == {"HOME", "PATH", "CLAUDE_CODE_OAUTH_TOKEN"}
    assert not Path(seen["cwd"]).exists()


def test_nonzero_exit_fails_closed():
    runner = lambda argv, **kw: SimpleNamespace(returncode=2, stdout="")  # noqa: E731
    with pytest.raises(RuntimeError, match="exited 2"):
        ec.extract("p", environ={}, runner=runner)


def test_main_reports_failure_without_leaking_detail(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("private text"))

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("claude", 1)

    monkeypatch.setattr(ec, "extract", boom)
    assert ec.main() == 1
    err = capsys.readouterr().err
    assert "private text" not in err and "TimeoutExpired" in err


def test_main_success(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("p"))
    monkeypatch.setattr(ec, "extract", lambda prompt: "{}")
    assert ec.main() == 0
    assert capsys.readouterr().out == "{}"
