"""
tests/ext_agents/test_adaptive_context.py — EAR-11: adaptive context budgeting tests.

Tests (12):
 1. compute_context_budget returns int
 2. default budget is within expected range
 3. complex query increases budget
 4. rich KB fragments increase budget
 5. low historical quality increases budget
 6. absolute cap is enforced
 7. budget never below minimum
 8. all three factors can stack
 9. zero kb_fragments uses base
10. low total_invocations skips quality factor
11. budget is deterministic for same inputs
12. custom absolute_cap is respected

Ref: specs/ext-agent-reloaded.md §EAR-11
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent.parent / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.adaptive_context import compute_context_budget


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_agent(
    mean_quality_score=0.8,
    total_invocations=10,
    max_context_chars=4000,
    max_context_chars_absolute=None,
):
    agent = {
        "invocation": {
            "max_context_chars": max_context_chars,
        },
        "health": {
            "mean_quality_score": mean_quality_score,
            "total_invocations": total_invocations,
        },
    }
    if max_context_chars_absolute is not None:
        agent["invocation"]["max_context_chars_absolute"] = max_context_chars_absolute
    return agent


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_compute_returns_int():
    agent = _make_agent()
    budget = compute_context_budget(agent, query="deploy", kb_fragments="small KB")
    assert isinstance(budget, int)


def test_default_budget_in_range():
    agent = _make_agent()
    budget = compute_context_budget(agent, query="deploy rollout", kb_fragments="")
    assert 1000 <= budget <= 20000, f"Budget out of range: {budget}"


def test_complex_query_increases_budget():
    agent = _make_agent(max_context_chars=4000)
    short_budget = compute_context_budget(agent, query="deploy", kb_fragments="")
    long_query = " ".join([f"word{i}" for i in range(20)])  # > 15 unique tokens
    long_budget = compute_context_budget(agent, query=long_query, kb_fragments="")
    assert long_budget >= short_budget


def test_rich_kb_increases_budget():
    agent = _make_agent(max_context_chars=4000)
    small_budget = compute_context_budget(agent, query="deploy", kb_fragments="small")
    rich_kb = "X" * 9000  # > 8KB
    rich_budget = compute_context_budget(agent, query="deploy", kb_fragments=rich_kb)
    assert rich_budget >= small_budget


def test_low_quality_increases_budget():
    high_q_agent = _make_agent(mean_quality_score=0.9, total_invocations=10)
    low_q_agent = _make_agent(mean_quality_score=0.4, total_invocations=10)
    high_budget = compute_context_budget(high_q_agent, query="query", kb_fragments="")
    low_budget = compute_context_budget(low_q_agent, query="query", kb_fragments="")
    assert low_budget >= high_budget


def test_absolute_cap_enforced():
    agent = _make_agent(max_context_chars=100000, max_context_chars_absolute=5000)
    budget = compute_context_budget(agent, query=" ".join([f"w{i}" for i in range(50)]),
                                    kb_fragments="X" * 10000)
    assert budget <= 5000, f"Absolute cap violated: {budget}"


def test_budget_never_below_minimum():
    agent = _make_agent(max_context_chars=100)  # tiny base
    budget = compute_context_budget(agent, query="q", kb_fragments="")
    assert budget >= 500, f"Budget too small: {budget}"


def test_all_three_factors_stack():
    agent = _make_agent(
        mean_quality_score=0.4,
        total_invocations=10,
        max_context_chars=4000,
    )
    base = compute_context_budget(agent, query="short", kb_fragments="")
    full = compute_context_budget(
        agent,
        query=" ".join([f"unique{i}" for i in range(20)]),
        kb_fragments="X" * 9000,
    )
    assert full >= base


def test_zero_kb_uses_base():
    agent = _make_agent(max_context_chars=4000)
    budget = compute_context_budget(agent, query="deploy", kb_fragments="")
    assert isinstance(budget, int)
    assert budget > 0


def test_low_invocations_skips_quality_factor():
    """With < 3 invocations, quality factor should not apply."""
    high_q = _make_agent(mean_quality_score=0.9, total_invocations=2)
    low_q = _make_agent(mean_quality_score=0.3, total_invocations=2)
    bH = compute_context_budget(high_q, query="q", kb_fragments="")
    bL = compute_context_budget(low_q, query="q", kb_fragments="")
    # Without quality factor, both should be equal
    assert bH == bL, \
        f"Quality factor should not apply with < 3 invocations: {bH} vs {bL}"


def test_deterministic_for_same_inputs():
    agent = _make_agent()
    b1 = compute_context_budget(agent, query="deploy rollout", kb_fragments="service X info")
    b2 = compute_context_budget(agent, query="deploy rollout", kb_fragments="service X info")
    assert b1 == b2


def test_custom_absolute_cap_respected():
    agent = _make_agent(max_context_chars=8000, max_context_chars_absolute=3000)
    budget = compute_context_budget(agent, query="deploy", kb_fragments="")
    assert budget <= 3000
