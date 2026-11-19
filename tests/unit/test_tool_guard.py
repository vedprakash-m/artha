"""PreToolUse guard: denies exactly the three spec'd things (re-artha RA-18)."""
from __future__ import annotations

import io
import json

import pytest

import tool_guard as tg  # noqa: E402

D = ("finance", "health")


def bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def test_permission_bypassing_launches_are_denied():
    for cmd in ["claude --dangerously-skip-permissions -p hi", "gemini --yolo", "copilot --allow-all-tools",
                "codex --dangerously-bypass-approvals-and-sandbox exec x",
                "claude --permission-mode bypassPermissions"]:
        assert tg.decide(bash(cmd), domains=D), cmd


def test_flag_text_without_a_launcher_is_not_a_launch():
    assert tg.decide(bash("grep -- '--yolo' notes.txt"), domains=D) is None


def test_vault_decrypt_denied_unless_explicitly_allowed():
    assert tg.decide(bash("python3 scripts/vault.py decrypt"), domains=D)
    assert tg.decide(bash("python3 scripts/vault.py decrypt"), allow_decrypt=True, domains=D) is None
    assert tg.decide(bash("python3 scripts/vault.py read finance"), domains=D) is None


def test_plaintext_sensitive_state_is_denied_across_tools_but_other_state_is_not():
    assert tg.decide(bash("cat state/finance.md"), domains=D)
    assert tg.decide({"tool_name": "Read", "tool_input": {"file_path": "/x/Artha/state/health.md"}}, domains=D)
    assert tg.decide({"tool_name": "Grep", "tool_input": {"pattern": "x", "path": "state/finance.md"}}, domains=D)
    assert tg.decide(bash("cat state/goals.md"), domains=D) is None
    assert tg.decide(bash("cat state/finance.md.age | wc -c"), domains=D) is None   # ciphertext is fine
    assert tg.decide({"tool_name": "Read", "tool_input": {"file_path": "state/finance_notes.md"}}, domains=D) is None


def test_main_exit_codes_and_fail_closed():
    assert tg.main(io.StringIO(json.dumps(bash("cat state/finance.md")))) == 2
    assert tg.main(io.StringIO(json.dumps(bash("ls")))) == 0
    assert tg.main(io.StringIO("not json")) == 2
    with pytest.raises(tg.GuardInputError):
        tg.decide({"tool_name": "Bash", "tool_input": "weird"})


def test_text_that_merely_mentions_a_forbidden_command_is_not_a_command():
    heredoc = "python3 - <<'EOF'\nprint('python3 scripts/vault.py decrypt')\nEOF"
    assert tg.decide(bash(heredoc), domains=D) is None
    assert tg.decide(bash("grep -rn 'vault.py decrypt' config"), domains=D) is None
    assert tg.decide(bash("echo 'cat state/finance.md'"), domains=D) is None
    assert tg.decide(bash("git commit -m 'stop using --yolo with claude'"), domains=D) is None


def test_a_forbidden_command_after_a_separator_is_still_found():
    assert tg.decide(bash("ls && python3 scripts/vault.py decrypt"), domains=D)
    assert tg.decide(bash("FOO=1 python3 scripts/vault.py decrypt"), domains=D)
    assert tg.decide(bash("ls; cat state/finance.md"), domains=D)
    assert tg.decide(bash("cd x\nclaude --dangerously-skip-permissions"), domains=D)


def test_guard_does_not_import_foundation_after_consuming_host_input(monkeypatch):
    imported = []
    original_import = __import__

    def checked_import(name, *args, **kwargs):
        if name == "foundation":
            imported.append(name)
            raise AssertionError("foundation re-exec boundary reached")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", checked_import)
    assert tg.decide(bash("ls")) is None
    assert imported == []


def test_nested_sensitive_state_paths_are_denied():
    assert tg.decide(
        {"toolName": "read_file", "toolArgs": {"filePath": r"C:\Artha\state\work\work-career.md"}},
        domains=("work-career",),
    )


@pytest.mark.parametrize("payload", [
    {"tool_name": "view", "tool_input": {"path": r"C:\Artha\state\finance.md"}},
    {"toolName": "view", "toolArgs": {"path": "/x/Artha/state/health.md"}},
    {"tool_name": "create", "tool_input": {"path": "state/finance.md", "content": "synthetic"}},
    {"toolName": "create", "toolArgs": {"filePath": r"C:\Artha\state\health.md"}},
    {"tool_name": "rg", "tool_input": {"pattern": "synthetic", "paths": "state/finance.md"}},
    {"tool_name": "rg", "tool_input": {"pattern": "synthetic", "paths": ["scripts", "state/health.md"]}},
    {"toolName": "grep", "toolArgs": {"pattern": "synthetic", "paths": [r"C:\Artha\state\finance.md"]}},
])
def test_actual_host_file_tool_shapes_deny_sensitive_state_paths(payload):
    assert tg.decide(payload, domains=D)


@pytest.mark.parametrize("payload", [
    {"tool_name": "view", "tool_input": {"path": "state/goals.md"}},
    {"tool_name": "create", "tool_input": {"path": "docs/new.md", "content": "synthetic"}},
    {"tool_name": "rg", "tool_input": {"pattern": "finance", "paths": ["scripts"]}},
    {"toolName": "grep", "toolArgs": {"pattern": "health", "paths": ["tests"]}},
])
def test_actual_host_file_tool_shapes_allow_non_sensitive_paths(payload):
    assert tg.decide(payload, domains=D) is None
