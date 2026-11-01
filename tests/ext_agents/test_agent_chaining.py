"""
tests/ext_agents/test_agent_chaining.py — EAR-2: agent chaining DAG tests.

Tests (30):
 1.  execute() returns ChainResult
 2.  linear chain: each step's agent receives a query
 3.  ChainResult.completed_steps is a list
 4.  key_assertions extracted from prose (list of strings)
 5.  ChainResult.unified_output is a string
 6.  gate condition failure halts chain (chain_status="partial")
 7.  empty chain steps → completed_steps is empty
 8.  ChainResult.chain_quality_score ≥ 0
 9.  load_chain() parses valid YAML → ChainDefinition
10.  load_chain() missing file returns None (not raises)
11.  extract_key_assertions returns ≤ 3 items
12.  ChainStepState has agent_name field
13.  ChainStepState has latency_ms field
14.  ChainStepState has quality_score field
15.  ChainResult.total_latency_ms ≥ 0
16.  execute() respects chain deadline (no crash on timeout)
17.  _active_chains is a module-level int
18.  chain_status="complete" on full success
19.  extract_key_assertions returns list
20.  extract_key_assertions handles empty input
21.  single step chain produces one completed_step
22.  chain loaded from valid YAML has name and steps
23.  step with empty output → empty key_assertions
24.  extract_key_assertions caps at 3 items
25.  gate condition is per-step
26.  completed_steps includes agent names
27.  execute() does not raise on step exception
28.  unified_prose in step state is a string
29.  load_all_chains returns list
30.  partial result has completed_steps tracked

Ref: specs/ext-agent-reloaded.md §EAR-2, R-10
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.agent_chainer import (
    AgentChainer,
    ChainDefinition,
    ChainResult,
    ChainStep,
    ChainStepState,
    extract_key_assertions,
    load_chain,
    load_all_chains,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_invoke(agent_name, query, timeout=60):
    """Minimal invoke_fn: returns (prose, quality)."""
    return f"Response from {agent_name}.", 0.8


def _raise_invoke(agent_name, query, timeout=60):
    raise RuntimeError(f"{agent_name} crashed")


def _make_chain(*step_agents: str, gate_condition: str = "") -> ChainDefinition:
    """Build a ChainDefinition from a list of agent names."""
    steps = []
    for i, agent in enumerate(step_agents):
        feeds = step_agents[i - 1] if i > 0 else None
        steps.append(ChainStep(agent=agent, feeds_from=feeds, gate_condition=gate_condition))
    return ChainDefinition(
        name="test-chain",
        description="Test chain",
        steps=steps,
        trigger_keywords=[],
    )


@pytest.fixture()
def chainer():
    return AgentChainer(invoke_fn=_ok_invoke)


@pytest.fixture()
def two_step(tmp_path) -> Path:
    """Write a valid two-step chain YAML and return its path."""
    chain_yaml = {
        "name": "two-step",
        "description": "Test chain",
        "steps": [
            {"agent": "agent-a"},
            {"agent": "agent-b", "feeds_from": "agent-a"},
        ],
        "trigger": {"keywords": ["deploy"], "min_confidence": 0.3},
    }
    p = tmp_path / "two-step.chain.yaml"
    p.write_text(yaml.dump(chain_yaml))
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_execute_returns_chain_result(chainer):
    chain = _make_chain("agent-a", "agent-b")
    result = chainer.execute(chain, query="test query")
    assert isinstance(result, ChainResult)


def test_linear_chain_each_step_invoked(chainer):
    invoked = []

    def track_invoke(agent_name, query, timeout=60):
        invoked.append(agent_name)
        return f"Output from {agent_name}.", 0.8

    c = AgentChainer(invoke_fn=track_invoke)
    chain = _make_chain("agent-a", "agent-b")
    c.execute(chain, query="initial query")
    assert "agent-a" in invoked
    assert "agent-b" in invoked


def test_chain_result_completed_steps_is_list(chainer):
    chain = _make_chain("agent-a", "agent-b")
    result = chainer.execute(chain, query="q")
    assert isinstance(result.completed_steps, list)


def test_key_assertions_extracted():
    prose = (
        "The deployment failed at step 3. "
        "Restart the service immediately. "
        "The quota was exceeded in eastus. "
        "Contact the oncall engineer now."
    )
    assertions = extract_key_assertions(prose)
    assert isinstance(assertions, list)
    assert 1 <= len(assertions) <= 3


def test_chain_result_unified_output_is_string(chainer):
    chain = _make_chain("agent-a")
    result = chainer.execute(chain, query="q")
    assert isinstance(result.unified_output, str)


def test_gate_condition_failure_halts(chainer):
    """gate_condition that always fails (len == -1) → partial result."""
    chain = ChainDefinition(
        name="gated",
        description="",
        steps=[
            ChainStep(agent="agent-a"),
            ChainStep(
                agent="agent-b",
                feeds_from="agent-a",
                gate_condition="'__never__' in key_assertions",  # always False
            ),
        ],
        trigger_keywords=[],
    )
    result = chainer.execute(chain, query="q")
    assert result.chain_status == "partial", f"Expected partial, got {result.chain_status}"


def test_empty_steps_completed_empty(chainer):
    chain = ChainDefinition(name="empty", description="", steps=[], trigger_keywords=[])
    result = chainer.execute(chain, query="q")
    assert result.completed_steps == []


def test_chain_quality_score_nonnegative(chainer):
    chain = _make_chain("agent-a")
    result = chainer.execute(chain, query="q")
    assert result.chain_quality_score >= 0.0


def test_load_chain_parses_yaml(two_step):
    cfg = load_chain(two_step)
    assert cfg is not None, "Expected ChainDefinition, got None"
    assert isinstance(cfg, ChainDefinition)
    assert cfg.name == "two-step"
    assert len(cfg.steps) == 2


def test_load_chain_missing_file_returns_none():
    result = load_chain(Path("/nonexistent/chain.yaml"))
    assert result is None, "Expected None for missing file"


def test_extract_key_assertions_max_three():
    prose = ". ".join([f"Action item number {i}" for i in range(20)])
    assertions = extract_key_assertions(prose)
    assert len(assertions) <= 3


def test_step_state_has_agent_name(chainer):
    chain = _make_chain("agent-x")
    result = chainer.execute(chain, query="q")
    for step in result.completed_steps:
        assert hasattr(step, "agent_name")
        assert isinstance(step.agent_name, str)


def test_step_state_has_latency_ms(chainer):
    chain = _make_chain("agent-x")
    result = chainer.execute(chain, query="q")
    for step in result.completed_steps:
        assert hasattr(step, "latency_ms")
        assert step.latency_ms >= 0.0


def test_step_state_has_quality_score(chainer):
    chain = _make_chain("agent-x")
    result = chainer.execute(chain, query="q")
    for step in result.completed_steps:
        assert hasattr(step, "quality_score")
        assert isinstance(step.quality_score, float)


def test_chain_result_total_latency_nonnegative(chainer):
    chain = _make_chain("agent-a", "agent-b")
    result = chainer.execute(chain, query="q")
    assert result.total_latency_ms >= 0.0


def test_execute_no_crash_on_timeout(chainer):
    """Pass a very short timeout_override — should not raise."""
    chain = _make_chain("agent-a", "agent-b")
    result = chainer.execute(chain, query="q", timeout_override=1)
    assert isinstance(result, ChainResult)


def test_active_chains_is_int():
    from lib.agent_chainer import _active_chains
    assert isinstance(_active_chains, int)


def test_chain_status_complete_on_success(chainer):
    chain = _make_chain("agent-a")
    result = chainer.execute(chain, query="q")
    assert result.chain_status == "complete"


def test_extract_key_assertions_returns_list():
    assertions = extract_key_assertions("Deploy immediately. Rollback staging.")
    assert isinstance(assertions, list)


def test_extract_key_assertions_empty_input():
    assertions = extract_key_assertions("")
    assert assertions == []


def test_single_step_chain(chainer):
    chain = _make_chain("agent-solo")
    result = chainer.execute(chain, query="q")
    assert len(result.completed_steps) == 1
    assert result.completed_steps[0].agent_name == "agent-solo"


def test_load_chain_has_name_and_steps(two_step):
    cfg = load_chain(two_step)
    assert cfg.name is not None
    assert len(cfg.steps) > 0
    for step in cfg.steps:
        assert isinstance(step.agent, str)


def test_step_empty_output_empty_assertions():
    def empty_invoke(agent_name, query, timeout=60):
        return "", 0.5

    c = AgentChainer(invoke_fn=empty_invoke)
    chain = _make_chain("quiet-agent")
    result = c.execute(chain, query="q")
    for step in result.completed_steps:
        assert step.key_assertions == []


def test_extract_key_assertions_cap():
    prose = ". ".join([f"Important action number {i}" for i in range(20)])
    assertions = extract_key_assertions(prose)
    assert len(assertions) <= 3


def test_gate_condition_is_per_step():
    """Only the step with gate_condition should be halted; previous steps complete."""
    invoked = {"agent-a": False, "agent-b": False}

    def track(agent_name, query, timeout=60):
        invoked[agent_name] = True
        return f"Output from {agent_name}.", 0.8

    c = AgentChainer(invoke_fn=track)
    chain = ChainDefinition(
        name="gated",
        description="",
        steps=[
            ChainStep(agent="agent-a"),
            ChainStep(
                agent="agent-b",
                feeds_from="agent-a",
                gate_condition="'__never__' in key_assertions",  # always False
            ),
        ],
        trigger_keywords=[],
    )
    result = c.execute(chain, query="q")
    # agent-a should have run, agent-b should be gated out
    assert invoked["agent-a"], "agent-a should have been invoked"
    completed_names = [s.agent_name for s in result.completed_steps]
    assert "agent-a" in completed_names


def test_completed_steps_include_agent_names(chainer):
    chain = _make_chain("agent-a", "agent-b")
    result = chainer.execute(chain, query="q")
    names = [s.agent_name for s in result.completed_steps]
    assert "agent-a" in names
    assert "agent-b" in names


def test_execute_does_not_raise_on_step_failure():
    c = AgentChainer(invoke_fn=_raise_invoke)
    chain = _make_chain("bad-agent")
    result = c.execute(chain, query="q")
    assert isinstance(result, ChainResult)


def test_unified_prose_is_string(chainer):
    chain = _make_chain("agent-a")
    result = chainer.execute(chain, query="q")
    for step in result.completed_steps:
        assert isinstance(step.unified_prose, str)


def test_load_all_chains_returns_list(two_step):
    chains_dir = two_step.parent
    chains = load_all_chains(chains_dir)
    assert isinstance(chains, list)
    assert any(c.name == "two-step" for c in chains)


def test_partial_result_has_completed_steps():
    """On step failure, completed_steps holds the successful preceding steps."""
    step_0_done = False

    def mixed_invoke(agent_name, query, timeout=60):
        nonlocal step_0_done
        if agent_name == "step-0":
            step_0_done = True
            return "Step 0 output.", 0.8
        raise RuntimeError("Step 1 crashed")

    c = AgentChainer(invoke_fn=mixed_invoke)
    chain = _make_chain("step-0", "step-1")
    result = c.execute(chain, query="q")
    assert result.chain_status in ("partial", "failed")
    names = [s.agent_name for s in result.completed_steps]
    assert "step-0" in names


def test_partial_result_completed_steps():
    """A gate-blocked chain has completed_steps only up to the gate."""
    def gated_invoke(agent_name, query, timeout=60):
        return f"Output from {agent_name}.", 0.7

    c = AgentChainer(invoke_fn=gated_invoke)
    chain = ChainDefinition(
        name="gate-partial",
        description="",
        steps=[
            ChainStep(agent="step-0"),
            ChainStep(
                agent="step-1",
                feeds_from="step-0",
                gate_condition="'__impossible__' in key_assertions",  # always False
            ),
            ChainStep(agent="step-2", feeds_from="step-1"),
        ],
        trigger_keywords=[],
    )
    result = c.execute(chain, query="q")
    assert result.chain_status == "partial"
    names = [s.agent_name for s in result.completed_steps]
    assert "step-0" in names
    assert "step-2" not in names
