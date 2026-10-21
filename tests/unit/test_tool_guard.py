"""PreToolUse guard: denies exactly the three spec'd things (re-artha RA-18)."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
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


def test_main_exit_codes_and_fail_open():
    assert tg.main(io.StringIO(json.dumps(bash("cat state/finance.md")))) == 2
    assert tg.main(io.StringIO(json.dumps(bash("ls")))) == 0
    assert tg.main(io.StringIO("not json")) == 0
    assert tg.decide({"tool_name": "Bash", "tool_input": "weird"}) is None
