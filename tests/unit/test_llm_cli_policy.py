"""WP-01 / §12.2 item 1: the channel Q&A bridge launches no permission-bypassing CLI."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import llm_cli_policy as policy  # noqa: E402
from channel import llm_bridge  # noqa: E402

HARD_CODED_MODELS = ("gpt-5.4-mini", "gemini-3.1-flash", "gemini-2.5-pro", "claude-sonnet-4-6", "haiku")


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


@pytest.fixture
def all_clis(monkeypatch):
    monkeypatch.setattr(llm_bridge.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(llm_bridge, "_which_copilot", lambda: "/usr/bin/copilot")


def test_detected_clis_have_no_bypass_flags_or_hard_coded_models(all_clis, monkeypatch):
    for var in ("ARTHA_LLM_MODEL_COPILOT", "ARTHA_LLM_MODEL_GEMINI", "ARTHA_LLM_MODEL_CLAUDE"):
        monkeypatch.delenv(var, raising=False)
    single = llm_bridge._detect_llm_cli()
    clis = llm_bridge._detect_all_llm_clis()
    assert single and clis
    all_args = list(single[1]) + [a for _n, _e, args in clis for a in args]
    assert policy.find_bypass(all_args) == []
    for model in HARD_CODED_MODELS:
        assert model not in all_args


def test_configured_model_is_honoured(all_clis, monkeypatch):
    monkeypatch.setenv("ARTHA_LLM_MODEL_CLAUDE", "my-model")
    monkeypatch.delenv("ARTHA_LLM_MODEL_COPILOT", raising=False)
    monkeypatch.delenv("ARTHA_LLM_MODEL_GEMINI", raising=False)
    clis = {n: args for n, _e, args in llm_bridge._detect_all_llm_clis()}
    assert clis["claude"][-2:] == ["--model", "my-model"]


def test_launch_with_bypass_flag_is_refused_before_spawn(monkeypatch):
    spawned = []

    async def fake_spawn(*a, **k):
        spawned.append(a)
        raise AssertionError("must not spawn")
    monkeypatch.setattr(llm_bridge.asyncio, "create_subprocess_shell", fake_spawn)
    with pytest.raises(llm_bridge.LLMUnavailableError):
        asyncio.run(llm_bridge._call_single_llm("claude", "/usr/bin/claude", ["--dangerously-skip-permissions"],
                                                "prompt", "answer"))
    assert spawned == []


def test_cli_is_not_started_inside_the_artha_workspace(monkeypatch):
    seen = {}

    class _Proc:
        returncode = 0

        async def communicate(self):
            return b"answer", b""

    async def fake_spawn(cmd, **kw):
        seen["cwd"] = kw.get("cwd")
        return _Proc()
    monkeypatch.setattr(llm_bridge.asyncio, "create_subprocess_shell", fake_spawn)
    out = asyncio.run(llm_bridge._call_single_llm("claude", "/usr/bin/claude", ["-s"], "prompt", "answer"))
    assert out
    assert Path(seen["cwd"]).resolve() != llm_bridge._ARTHA_DIR.resolve()
    assert llm_bridge._ARTHA_DIR.resolve() not in Path(seen["cwd"]).resolve().parents
