#!/usr/bin/env python3
"""delegation.py — Delegation protocol for Artha subagent handoffs (AR-7).

Provides structured handoff composition and delegation decision logic for
offloading isolated, parallel, or multi-step tasks to subagents, while
staying within safe token budgets.

Usage:
    from delegation import should_delegate, compose_handoff, DelegationRequest

    if should_delegate(estimated_steps=7, is_parallel=False, is_isolated=True):
        req = compose_handoff(
            task="Summarize all briefings from last month",
            ctx="User is reviewing Q1 goals.",
            relevant_state=["state/goals.yaml"],
            budget=10,
        )

Config flag: harness.agentic.delegation.enabled (default: true)

Ref: specs/agentic-reloaded.md Phase AR-7
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────

# Minimum estimated steps before delegation is preferred
_DELEGATION_STEP_THRESHOLD = 5
# Maximum characters of context to include in a handoff (avoids token waste)
_MAX_CONTEXT_CHARS = 500
# Maximum summary length from subagent to inject back into main session
_MAX_SUMMARY_CHARS = 500


@dataclass
class DelegationRequest:
    """A structured request to hand off to a subagent.

    Attributes:
        task: The precise task description for the subagent.
        context_excerpt: Compressed, minimal context (≤ _MAX_CONTEXT_CHARS chars).
        budget: Maximum tool calls the subagent is allowed.
        agent: Named agent to invoke ("Explore" for read-only research).
        output_max_chars: Suggested maximum for the subagent's reply.
        relevant_state: State file paths the subagent should read.
    """

    task: str
    context_excerpt: str = ""
    budget: int = 10
    agent: str = "Explore"
    output_max_chars: int = _MAX_SUMMARY_CHARS
    relevant_state: list[str] = field(default_factory=list)

    def to_prompt(self) -> str:
        """Render as a structured subagent prompt string."""
        parts = [f"## Delegated Task\n{self.task}"]
        if self.context_excerpt:
            parts.append(f"## Context\n{self.context_excerpt}")
        if self.relevant_state:
            paths = "\n".join(f"- {p}" for p in self.relevant_state)
            parts.append(f"## Relevant State Files (read these)\n{paths}")
        parts.append(
            f"## Constraints\n"
            f"- Budget: ≤ {self.budget} tool calls.\n"
            f"- Reply must be ≤ {self.output_max_chars} characters, plain text.\n"
            f"- Do NOT make any write operations."
        )
        return "\n\n".join(parts)


@dataclass
class DelegationResult:
    """Result returned by a subagent after handling a DelegationRequest.

    Attributes:
        summary: The subagent's response (≤ _MAX_SUMMARY_CHARS chars).
        tool_calls_used: Number of tool calls the subagent made (if reported).
        procedure_candidate: True if this task is worth storing as a learned procedure.
    """

    summary: str
    tool_calls_used: int | None = None
    procedure_candidate: bool = False

    @property
    def is_truncated(self) -> bool:
        return len(self.summary) >= _MAX_SUMMARY_CHARS


def should_delegate(
    estimated_steps: int,
    is_parallel: bool = False,
    is_isolated: bool = False,
) -> bool:
    """Decide whether to delegate a task to a subagent.

    Delegation is preferred when:
    - The sub-task requires 5+ tool calls (complexity threshold), OR
    - The sub-task can be parallelised with other work, OR
    - The sub-task is completely isolated (no shared state reads/writes).

    Args:
        estimated_steps: Expected number of tool calls required.
        is_parallel: Whether this task can run concurrently with others.
        is_isolated: Whether this task touches no shared state.

    Returns:
        True if delegation is recommended, False for inline execution.
    """
    return estimated_steps >= _DELEGATION_STEP_THRESHOLD or is_parallel or is_isolated


def compose_handoff(
    task: str,
    ctx: str,
    *,
    relevant_state: list[str] | None = None,
    budget: int = 10,
    agent: str = "Explore",
) -> DelegationRequest:
    """Build a compressed handoff extracting only context the subagent needs.

    The context excerpt is trimmed to _MAX_CONTEXT_CHARS characters so that
    the subagent's token budget is spent on doing work, not reading history.

    Args:
        task: Clear, precise task description for the subagent.
        ctx: Full context string (will be compressed to key sentences).
        relevant_state: Optional list of state file paths the subagent needs.
        budget: Maximum tool calls the subagent may use (default: 10).
        agent: Named agent profile ("Explore" for research tasks).

    Returns:
        DelegationRequest ready for prompt rendering or direct invocation.
    """
    # Compress context: take up to _MAX_CONTEXT_CHARS from the start,
    # trying to preserve sentence boundaries.
    excerpt = _compress_context(ctx, max_chars=_MAX_CONTEXT_CHARS)

    return DelegationRequest(
        task=task,
        context_excerpt=excerpt,
        budget=min(budget, _get_max_budget()),
        agent=agent,
        output_max_chars=_MAX_SUMMARY_CHARS,
        relevant_state=relevant_state or [],
    )


def evaluate_for_procedure(result: DelegationResult, task: str) -> bool:
    """Mark result as a procedure candidate if the task was multi-step and succeeded.

    This feeds AR-5: if a delegation succeeded and was non-trivial, the calling
    code can suggest the orchestration pattern be stored as a learned procedure.

    Args:
        result: The DelegationResult to evaluate.
        task: The original task description.

    Returns:
        True if the task pattern is worth storing as a procedure.
    """
    word_count = len(task.split())
    # Heuristic: task > 8 words (non-trivial) and summary is substantive
    return word_count >= 8 and len(result.summary) > 50


def _compress_context(text: str, max_chars: int = _MAX_CONTEXT_CHARS) -> str:
    """Trim text to max_chars, cutting at the last sentence boundary."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    # Try to cut at the last sentence end
    for sep in (". ", ".\n", "! ", "? "):
        idx = truncated.rfind(sep)
        if idx > max_chars // 2:
            return truncated[: idx + 1].rstrip()
    return truncated.rstrip() + "…"


def _load_harness_flag(path: str, default: Any = True) -> Any:
    """Read a dotted harness config path from artha_config.yaml."""
    try:
        import yaml
        cfg_path = Path(__file__).resolve().parents[1] / "config" / "artha_config.yaml"
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        node: Any = raw.get("harness", {})
        for key in path.split("."):
            if not isinstance(node, dict):
                return default
            node = node.get(key, default)
        return node
    except Exception:
        return default


def _get_max_budget() -> int:
    """Return configured max delegation budget (default: 20)."""
    raw = _load_harness_flag("agentic.delegation.max_budget", 20)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 20


def is_delegation_enabled() -> bool:
    """Check if delegation is enabled in config."""
    return bool(_load_harness_flag("agentic.delegation.enabled", True))


# ── CLI / smoke-test entry point ──────────────────────────────────────────────

def _main() -> None:
    task = "Summarize all immigration briefings from the last 90 days and flag any form deadlines"
    ctx = (
        "User is on H-1B visa. Immigration domain is active. Last briefing: 2026-03-14. "
        "No pending OI items. Goals include GC timeline tracking."
    )
    print("=== Delegation Decision ===")
    print(f"should_delegate(steps=7): {should_delegate(7)}")
    print(f"should_delegate(steps=2, parallel=True): {should_delegate(2, is_parallel=True)}")
    print(f"should_delegate(steps=2, isolated=True): {should_delegate(2, is_isolated=True)}")
    print(f"should_delegate(steps=3): {should_delegate(3)}")
    print()
    req = compose_handoff(task, ctx, relevant_state=["state/immigration.yaml"], budget=8)
    print("=== Composed Handoff ===")
    print(req.to_prompt())
    print()
    result = DelegationResult(
        summary="Found 3 briefings. Next deadline: I-485 biometrics by 2026-04-01.",
        tool_calls_used=4,
    )
    print("=== Procedure Candidate? ===")
    print(evaluate_for_procedure(result, task))


if __name__ == "__main__":
    _main()
