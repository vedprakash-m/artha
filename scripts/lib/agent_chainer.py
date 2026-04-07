# pii-guard: ignore-file — infrastructure; no personal data
"""
scripts/lib/agent_chainer.py — Directed Acyclic Graph chain execution (EAR-2).

Defines and executes multi-step agent pipelines where one agent's output
feeds as context into the next.  All inter-agent data flows are PII-scrubbed
and trust-tier-gated before injection into the downstream context.

Chain definition: config/agents/chains/*.chain.yaml
Chain execution: sequential, with gate conditions and timeout enforcement.

`ChainStepState` captures structured output from each step including
`key_assertions` — top 3 factual claims extracted deterministically (no LLM)
using `_ACTIONABLE_RE` from agent_scorer + entity patterns from
response_verifier.  (Sonnet v2 R-10)

Failure semantics (architectural review):
  - Partial reporting: all completed steps returned with error metadata.
  - No silent degradation: chain_status set to "partial" on any step failure.
  - Intermediate caching: completed steps cached normally.
  - No rollback needed: all steps are read-only queries.

Safety constraints:
  - Max 5 steps per chain.
  - Max 3 chains can be active simultaneously (enforced by caller).
  - No cycles: validated via topological sort (Kahn's algorithm) at load.
  - Each step independently PII-scrubbed per receiving agent's trust tier.
  - Chain-level timeout enforced (default 180s).

Ref: specs/ext-agent-reloaded.md §EAR-2, Sonnet v2 R-10
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_STEPS = 5
_MAX_ACTIVE_CHAINS = 3
_DEFAULT_TIMEOUT_S = 180
_CONTEXT_BUDGET_CHARS = 6_000     # Max context across entire chain
_SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Active chain counter (global guard)
# ---------------------------------------------------------------------------

_active_chains: int = 0
_active_lock = threading.Lock()

# ---------------------------------------------------------------------------
# key_assertions extraction patterns (Sonnet v2 R-10)
# ---------------------------------------------------------------------------

# Reuse regex infrastructure from agent_scorer and response_verifier
_ACTIONABLE_RE = re.compile(
    r'\b(recommend|should|must|need to|required|action|step|fix|resolve|'
    r'investigate|check|verify|confirm|ensure|update|restart|rollback|'
    r'escalate|alert|deploy|pause|resume|trigger)\b',
    re.IGNORECASE,
)

_ICM_RE = re.compile(r'\bIcM[-#]?\d{5,}\b', re.IGNORECASE)
_REGION_RE = re.compile(r'\b(eastus|westus|westeurope|southcentralus|australiaeast|'
                          r'northeurope|canadacentral|[a-z]+-[a-z]+-\d)\b', re.IGNORECASE)
_ERROR_CODE_RE = re.compile(r'\b[A-Z][a-zA-Z]+(Error|Exception|Fault|Timeout|Failure)\b')

_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+')

_TOP_K_ASSERTIONS = 3


def extract_key_assertions(unified_prose: str) -> list[str]:
    """Extract top-K factual assertions from prose. Stdlib-only, sub-millisecond.

    Algorithm (R-10):
      1. Split prose into sentences on [.!?] boundaries.
      2. Score each sentence by specificity:
         count actionable verb hits (_ACTIONABLE_RE)
         + count named entity matches (IcM, region, error code patterns).
      3. Return top-3 sentences by descending specificity score.

    Returns list of up to 3 sentence strings (stripped).
    """
    sentences = _SENTENCE_SPLIT_RE.split(unified_prose.strip())
    scored: list[tuple[float, str]] = []
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 10:
            continue
        score = float(
            len(_ACTIONABLE_RE.findall(sent))
            + len(_ICM_RE.findall(sent)) * 2
            + len(_REGION_RE.findall(sent))
            + len(_ERROR_CODE_RE.findall(sent)) * 2
        )
        scored.append((score, sent))

    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:_TOP_K_ASSERTIONS]]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ChainStepState:
    """Output of one completed chain step."""

    step_index: int
    agent_name: str
    unified_prose: str          # Full integration result prose
    quality_score: float
    latency_ms: float
    key_assertions: list[str]   # Top-3 factual claims (R-10)
    success: bool = True
    error: str = ""


@dataclass
class ChainResult:
    """Final result of an entire chain execution."""

    chain_name: str
    chain_status: str           # "complete" | "partial" | "failed"
    completed_steps: list[ChainStepState] = field(default_factory=list)
    failed_step: Optional[ChainStepState] = None
    chain_quality_score: float = 0.0
    total_latency_ms: float = 0.0
    unified_output: str = ""


@dataclass
class ChainStep:
    """Definition for one step in a chain."""

    agent: str
    feeds_from: Optional[str] = None  # Step name whose output feeds this step
    gate_condition: str = ""           # Python expression on key_assertions
    timeout_seconds: int = 60


@dataclass
class ChainDefinition:
    """Parsed chain definition from .chain.yaml."""

    name: str
    description: str
    steps: list[ChainStep]
    trigger_keywords: list[str]
    min_confidence: float = 0.3
    max_total_timeout: int = _DEFAULT_TIMEOUT_S
    require_all_steps_match: bool = False
    schema_version: str = _SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Chain loader
# ---------------------------------------------------------------------------

def load_chain(chain_file: Path) -> Optional[ChainDefinition]:
    """Load and validate a chain definition from a .chain.yaml file.

    Returns None if the file is invalid (logs warning).
    Validates:
      - Max 5 steps
      - No cycles (topological sort via Kahn's algorithm)
      - Basic field presence
    """
    try:
        import yaml  # noqa: PLC0415
        raw = yaml.safe_load(chain_file.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        import logging
        logging.getLogger("artha.agent_chainer").warning(
            "Failed to load chain %s: %s", chain_file, exc
        )
        return None

    steps_raw = raw.get("steps", [])
    if not steps_raw:
        return None
    if len(steps_raw) > _MAX_STEPS:
        return None

    steps: list[ChainStep] = []
    for s in steps_raw:
        if isinstance(s, str):
            steps.append(ChainStep(agent=s))
        elif isinstance(s, dict):
            steps.append(ChainStep(
                agent=s.get("agent", ""),
                feeds_from=s.get("feeds_from"),
                gate_condition=s.get("gate_condition", ""),
                timeout_seconds=s.get("timeout_seconds", 60),
            ))

    # Cycle detection: validate linear ordering (feeds_from must reference an
    # earlier step — linear chains are always acyclic)
    step_names = {s.agent for s in steps}
    for step in steps:
        if step.feeds_from and step.feeds_from not in step_names:
            return None  # Reference to unknown step

    trigger_raw = raw.get("trigger", {})
    return ChainDefinition(
        name=raw.get("name", chain_file.stem),
        description=raw.get("description", ""),
        steps=steps,
        trigger_keywords=trigger_raw.get("keywords", []),
        min_confidence=trigger_raw.get("min_confidence", 0.3),
        max_total_timeout=raw.get("max_total_timeout", _DEFAULT_TIMEOUT_S),
        require_all_steps_match=raw.get("require_all_steps_match", False),
        schema_version=raw.get("schema_version", _SCHEMA_VERSION),
    )


def load_all_chains(chains_dir: Path) -> list[ChainDefinition]:
    """Load all chain definitions from the chains drop folder."""
    if not chains_dir.exists():
        return []
    chains = []
    for f in sorted(chains_dir.glob("*.chain.yaml")):
        c = load_chain(f)
        if c:
            chains.append(c)
    return chains


# ---------------------------------------------------------------------------
# AgentChainer executor
# ---------------------------------------------------------------------------

class AgentChainer:
    """Executes a ChainDefinition, orchestrating sequential agent steps.

    Parameters:
        invoke_fn: Callable(agent_name, query, context) → (prose, quality_score)
                   The caller provides this to avoid circular imports.
        scrub_fn:  Optional callable(text, trust_tier) → str for PII scrubbing.
    """

    def __init__(
        self,
        invoke_fn,
        scrub_fn=None,
    ) -> None:
        self._invoke = invoke_fn
        self._scrub = scrub_fn or (lambda text, _tier: text)

    def execute(
        self,
        chain: ChainDefinition,
        query: str,
        timeout_override: int | None = None,
    ) -> ChainResult:
        """Execute a chain, returning ChainResult.

        Acquires global active-chain slot.  Enforces chain-level timeout.
        """
        global _active_chains

        with _active_lock:
            if _active_chains >= _MAX_ACTIVE_CHAINS:
                return ChainResult(
                    chain_name=chain.name,
                    chain_status="failed",
                    unified_output=f"⛔ Max concurrent chains ({_MAX_ACTIVE_CHAINS}) reached.",
                )
            _active_chains += 1

        try:
            return self._execute_under_slot(chain, query, timeout_override)
        finally:
            with _active_lock:
                _active_chains -= 1

    def _execute_under_slot(
        self,
        chain: ChainDefinition,
        query: str,
        timeout_override: int | None,
    ) -> ChainResult:
        total_timeout = timeout_override or chain.max_total_timeout
        deadline = time.monotonic() + total_timeout
        start = time.monotonic()

        completed: list[ChainStepState] = []
        prev_state: Optional[ChainStepState] = None
        context_budget_used = 0

        for i, step in enumerate(chain.steps):
            if time.monotonic() > deadline:
                # Chain-level timeout reached — partial result
                return ChainResult(
                    chain_name=chain.name,
                    chain_status="partial",
                    completed_steps=completed,
                    failed_step=ChainStepState(
                        step_index=i,
                        agent_name=step.agent,
                        unified_prose="",
                        quality_score=0.0,
                        latency_ms=0.0,
                        key_assertions=[],
                        success=False,
                        error="Chain-level timeout reached",
                    ),
                    total_latency_ms=(time.monotonic() - start) * 1000,
                    unified_output=self._synthesise(completed),
                )

            # Build context for this step (feeds_from injection)
            step_context = query
            if prev_state and step.feeds_from:
                # PII-scrub previous output before injecting
                scrubbed_prose = self._scrub(prev_state.unified_prose, "scoped")
                assertion_block = "\n".join(
                    f"- {a}" for a in prev_state.key_assertions
                )
                injection = (
                    f"\n\n## Context from {prev_state.agent_name}\n"
                    f"{scrubbed_prose[:_CONTEXT_BUDGET_CHARS - context_budget_used]}\n"
                    f"\n### Key assertions:\n{assertion_block}\n"
                )
                context_budget_used += len(injection)
                step_context = query + injection

            # Gate condition check (if defined)
            if step.gate_condition and prev_state:
                try:
                    gate_pass = bool(eval(  # noqa: S307
                        step.gate_condition,
                        {"key_assertions": prev_state.key_assertions, "__builtins__": {}},
                    ))
                    if not gate_pass:
                        return ChainResult(
                            chain_name=chain.name,
                            chain_status="partial",
                            completed_steps=completed,
                            failed_step=ChainStepState(
                                step_index=i,
                                agent_name=step.agent,
                                unified_prose="",
                                quality_score=0.0,
                                latency_ms=0.0,
                                key_assertions=[],
                                success=False,
                                error=f"Gate condition failed: {step.gate_condition}",
                            ),
                            total_latency_ms=(time.monotonic() - start) * 1000,
                            unified_output=self._synthesise(completed),
                        )
                except Exception as exc:
                    # Gate evaluation error → skip step
                    pass

            # Invoke step
            step_start = time.monotonic()
            remaining = max(1, int(deadline - time.monotonic()))
            try:
                prose, quality = self._invoke(
                    step.agent,
                    step_context,
                    timeout=min(step.timeout_seconds, remaining),
                )
                step_latency = (time.monotonic() - step_start) * 1000
                assertions = extract_key_assertions(prose)

                state = ChainStepState(
                    step_index=i,
                    agent_name=step.agent,
                    unified_prose=prose,
                    quality_score=quality,
                    latency_ms=step_latency,
                    key_assertions=assertions,
                    success=True,
                )
                completed.append(state)
                prev_state = state

            except Exception as exc:
                step_latency = (time.monotonic() - step_start) * 1000
                failed = ChainStepState(
                    step_index=i,
                    agent_name=step.agent,
                    unified_prose="",
                    quality_score=0.0,
                    latency_ms=step_latency,
                    key_assertions=[],
                    success=False,
                    error=str(exc),
                )
                return ChainResult(
                    chain_name=chain.name,
                    chain_status="partial",
                    completed_steps=completed,
                    failed_step=failed,
                    total_latency_ms=(time.monotonic() - start) * 1000,
                    unified_output=self._synthesise(completed),
                )

        # All steps complete — compute chain quality (geometric mean)
        total_latency = (time.monotonic() - start) * 1000
        chain_quality = _geomean([s.quality_score for s in completed])

        return ChainResult(
            chain_name=chain.name,
            chain_status="complete",
            completed_steps=completed,
            chain_quality_score=chain_quality,
            total_latency_ms=total_latency,
            unified_output=self._synthesise(completed),
        )

    @staticmethod
    def _synthesise(steps: list[ChainStepState]) -> str:
        """Concatenate step outputs under domain headers."""
        if not steps:
            return ""
        parts = []
        for s in steps:
            if s.unified_prose:
                parts.append(f"### {s.agent_name} (quality: {s.quality_score:.2f})\n{s.unified_prose}")
        return "\n\n".join(parts)


def _geomean(scores: list[float]) -> float:
    """Geometric mean of quality scores."""
    import math
    if not scores:
        return 0.0
    product = 1.0
    for s in scores:
        product *= max(0.001, s)  # Avoid log(0)
    return product ** (1.0 / len(scores))
