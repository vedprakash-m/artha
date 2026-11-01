"""WP-01 / 12.2 item 1: no script launches a permission-bypassing CLI (the channel bridge itself is gone)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import llm_cli_policy as policy  # noqa: E402


def test_policy_flags_every_known_bypass():
    for flag in ("--yolo", "--dangerously-skip-permissions", "--allow-all-tools", "--auto-approve"):
        with pytest.raises(policy.PermissionBypassError):
            policy.assert_no_permission_bypass(["-p", "x", flag])


def test_policy_flags_equals_form_and_case():
    assert policy.find_bypass(["--YOLO"]) == ["--YOLO"]
    assert policy.find_bypass(["--approval-mode=yolo"])


def test_policy_allows_ordinary_args():
    policy.assert_no_permission_bypass(["-s", "--no-custom-instructions", "--model", "x"])


def test_model_comes_from_environment_only():
    assert policy.model_args("copilot", {}) == []
    assert policy.model_args("claude", {"ARTHA_LLM_MODEL_CLAUDE": "m1"}) == ["--model", "m1"]


BYPASS_ALLOWED_IN = {"lib/llm_cli_policy.py", "tool_guard.py"}      # the policy that *detects* them, and the hook that denies them


def test_no_script_launches_a_model_cli_with_a_permission_bypass():
    """With the channel Q&A bridge retired (re-artha 6.11), nothing under scripts/ may carry a bypass flag."""
    offenders = []
    for path in SCRIPTS.rglob("*.py"):
        rel = path.relative_to(SCRIPTS).as_posix()
        if rel in BYPASS_ALLOWED_IN or "__pycache__" in rel:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for flag in ("--dangerously-skip-permissions", "--yolo", "--allow-all-tools", "--auto-approve"):
            if flag in text:
                offenders.append(f"{rel}: {flag}")
    assert offenders == []
