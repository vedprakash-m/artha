"""AR-9 External Agent Composition — Invocation Layer (§4.5).

Implements the AgentProvider ABC and VSCodeAgentProvider.

Note: VSCodeAgentProvider.invoke() is designed to be called by an LLM agent
(GitHub Copilot in agent mode) which has access to the runSubagent tool.
In scripted/test contexts use MockAgentProvider instead.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lib.agent_registry import ExternalAgent

# ---------------------------------------------------------------------------
# Result & Error types
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """Returned by AgentProvider.invoke() on success."""

    agent_name: str
    response: str
    invoked_at: datetime
    latency_ms: float = 0.0
    retried: bool = False
    truncated: bool = False
    fallback_used: bool = False
    fallback_type: str | None = None


class InvocationError(RuntimeError):
    """Raised by AgentProvider.invoke() on failure.

    Attributes:
        reason: One of 'timeout', 'unavailable', 'budget_exceeded',
                'injection_blocked', 'response_empty', 'provider_error'.
        retried: Whether a retry was attempted before this error.
    """

    def __init__(self, reason: str, message: str, retried: bool = False) -> None:
        super().__init__(message)
        self.reason = reason
        self.retried = retried


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class AgentProvider(ABC):
    """Abstract base for agent invocation providers.

    V1 concrete implementation: VSCodeAgentProvider.
    Future: RawLlmProvider for direct LLM invocation outside VS Code.
    """

    @abstractmethod
    def invoke(self, agent: "ExternalAgent", prompt: str) -> AgentResult:
        """Invoke the agent and return a result.

        Raises:
            InvocationError: on timeout, unavailability, or other failure.
        """


# ---------------------------------------------------------------------------
# V1 Provider — VS Code runSubagent protocol
# ---------------------------------------------------------------------------

class VSCodeAgentProvider(AgentProvider):
    """Invokes external agents via the VS Code runSubagent protocol.

    When running inside GitHub Copilot agent mode, the LLM model has access
    to the runSubagent() tool.  This class structures the call and wraps the
    result in AgentResult.

    Timeout and retry semantics (spec §4.5):
    - Timeout: agent.invocation.timeout_seconds (default 60s)
    - Retry:   1 retry with 1.5× timeout on transient failures (timeout only)
    - No retry on: injection_blocked, empty response, budget exceeded
    """

    def invoke(self, agent: "ExternalAgent", prompt: str) -> AgentResult:
        """Invoke the agent.

        In production this method is executed by the LLM which calls
        runSubagent() directly.  The Python implementation here serves as a
        documented contract and for testing via MockAgentProvider subclass.
        """
        timeout = 60
        if agent.invocation:
            timeout = agent.invocation.timeout_seconds

        start = time.monotonic()
        invoked_at = datetime.now(timezone.utc)

        try:
            response = self._run_subagent(agent, prompt, timeout)
        except _TransientError:
            # One retry with 1.5× timeout (spec §4.5)
            retry_timeout = int(timeout * 1.5)
            try:
                response = self._run_subagent(agent, prompt, retry_timeout)
            except _TransientError as exc:
                latency_ms = (time.monotonic() - start) * 1000
                raise InvocationError(
                    reason="timeout",
                    message=f"{agent.name}: timed out after {retry_timeout}s",
                    retried=True,
                ) from exc
        except _PermanentError as exc:
            latency_ms = (time.monotonic() - start) * 1000
            raise InvocationError(
                reason=exc.reason,
                message=str(exc),
            ) from exc

        latency_ms = (time.monotonic() - start) * 1000

        if not response or not response.strip():
            raise InvocationError(
                reason="response_empty",
                message=f"{agent.name}: returned an empty response",
            )

        # Truncate if response exceeds per-agent limit
        max_chars = 5000
        if agent.invocation:
            max_chars = agent.invocation.max_response_chars
        truncated = False
        if len(response) > max_chars:
            response = response[:max_chars]
            truncated = True

        return AgentResult(
            agent_name=agent.name,
            response=response,
            invoked_at=invoked_at,
            latency_ms=latency_ms,
            truncated=truncated,
        )

    # ------------------------------------------------------------------
    # Internal — separated to allow MockAgentProvider to override
    # ------------------------------------------------------------------

    def _run_subagent(
        self,
        agent: "ExternalAgent",
        prompt: str,
        timeout: int,  # noqa: ARG002 – documented but not enforced in Python layer
    ) -> str:
        """Call the underlying runSubagent tool.

        NOTE: This is overridden in tests via MockAgentProvider.
        In production the LLM executes this by calling the runSubagent MCP
        tool (see AGENTS.md for available agents list).

        The call signature matches the runSubagent tool contract:
            agentName: agent.name (the .agent.md file stem)
            description: short context for the invocation
            prompt: the composed delegation prompt
        """
        # In pure-Python execution this would raise immediately.
        # The LLM agent replaces this with an actual runSubagent call.
        raise _PermanentError(
            reason="provider_error",
            message=(
                f"{agent.name}: VSCodeAgentProvider._run_subagent() must be "
                "overridden or called from inside a VS Code agent session."
            ),
        )


# ---------------------------------------------------------------------------
# Mock provider for tests
# ---------------------------------------------------------------------------

@dataclass
class MockAgentProvider(AgentProvider):
    """In-process provider for unit tests.

    Responses are configured up-front via add_response() / set_failure().
    """

    _responses: dict[str, str] = field(default_factory=dict, repr=False)
    _failure: InvocationError | None = field(default=None, repr=False)
    _latency_ms: float = 10.0
    _call_log: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def add_response(self, agent_name: str, response: str) -> None:
        self._responses[agent_name] = response

    def set_failure(self, error: InvocationError) -> None:
        self._failure = error

    def clear_failure(self) -> None:
        self._failure = None

    @property
    def call_log(self) -> list[tuple[str, str]]:
        """List of (agent_name, prompt) pairs in invocation order."""
        return list(self._call_log)

    def invoke(self, agent: "ExternalAgent", prompt: str) -> AgentResult:
        self._call_log.append((agent.name, prompt))
        if self._failure is not None:
            raise self._failure
        response = self._responses.get(agent.name, "Mock response.")
        return AgentResult(
            agent_name=agent.name,
            response=response,
            invoked_at=datetime.now(timezone.utc),
            latency_ms=self._latency_ms,
        )


# ---------------------------------------------------------------------------
# Internal exception helpers (never surfaced to callers)
# ---------------------------------------------------------------------------

class _TransientError(Exception):
    """Timeout or recoverable failure — triggers one retry."""


class _PermanentError(Exception):
    """Non-recoverable failure — no retry."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# ---------------------------------------------------------------------------
# EA-5b: Fallback cascade
# ---------------------------------------------------------------------------

def invoke_with_fallback(
    agent: "ExternalAgent",
    prompt: str,
    provider: AgentProvider,
    cache_dir: str | None = None,
) -> AgentResult:
    """Invoke *agent* with automatic fallback on failure (spec EA-5b).

    Execution order
    ---------------
    1. Call ``provider.invoke(agent, prompt)`` — the primary path.
    2. On ``InvocationError``, walk ``agent.fallback_cascade`` in order.
       Each fallback entry is handled according to its ``type``:

       - ``"kb"``           — read the cached knowledge summary from *cache_dir*
                              and return it as a synthetic ``AgentResult``.
       - ``"cowork"``       — return a brief advisory message pointing the user
                              to Copilot Cowork for manual retrieval.
       - ``"investigation"``— return a brief advisory message to investigate
                              the agent availability.
       - Any other type     — skipped (logged but not fatal).

    3. If all fallbacks are exhausted, re-raise the original ``InvocationError``.

    Parameters
    ----------
    agent:
        The ``ExternalAgent`` to invoke.
    prompt:
        The rendered delegation prompt.
    provider:
        ``AgentProvider`` instance (``MockAgentProvider`` in tests).
    cache_dir:
        Path to the knowledge cache directory (e.g. ``tmp/ext-agent-cache``).
        Required for ``"kb"`` fallback; ignored otherwise.

    Returns
    -------
    ``AgentResult`` — either from the primary invocation or a fallback.
    The result will have ``fallback_used=True`` and ``fallback_type`` set when
    a fallback was used.

    Raises
    ------
    ``InvocationError`` — when both the primary invocation and all configured
    fallbacks are exhausted.
    """
    from datetime import datetime, timezone  # already imported at top level, but safe to re-import

    try:
        return provider.invoke(agent, prompt)
    except InvocationError as primary_err:
        fallback_cascade = getattr(agent, "fallback_cascade", []) or []

        for level, fb_entry in enumerate(fallback_cascade):
            fb_type: str = getattr(fb_entry, "type", None) or (
                fb_entry.get("type") if isinstance(fb_entry, dict) else None
            ) or ""

            if fb_type == "kb":
                kb_response = _kb_fallback(agent, cache_dir)
                if kb_response is not None:
                    return AgentResult(
                        agent_name=agent.name,
                        response=kb_response,
                        invoked_at=datetime.now(timezone.utc),
                        fallback_used=True,
                        fallback_type="kb",
                    )
                # KB cache miss — continue to next fallback

            elif fb_type == "cowork":
                return AgentResult(
                    agent_name=agent.name,
                    response=(
                        f"Agent '{agent.name}' is unavailable. "
                        "Retrieve context manually via Copilot Cowork: "
                        "https://m365.cloud.microsoft/chat/"
                    ),
                    invoked_at=datetime.now(timezone.utc),
                    fallback_used=True,
                    fallback_type="cowork",
                )

            elif fb_type == "investigation":
                return AgentResult(
                    agent_name=agent.name,
                    response=(
                        f"Agent '{agent.name}' is unavailable. "
                        "Investigate agent availability before retrying."
                    ),
                    invoked_at=datetime.now(timezone.utc),
                    fallback_used=True,
                    fallback_type="investigation",
                )
            # else: unknown type — skip and try next entry

        # All fallbacks exhausted
        raise primary_err


def _kb_fallback(agent: "ExternalAgent", cache_dir: str | None) -> str | None:
    """Return cached knowledge summary for *agent*, or ``None`` if unavailable."""
    if not cache_dir:
        return None
    try:
        import pathlib
        cache_path = pathlib.Path(cache_dir) / f"{agent.name}.md"
        if cache_path.exists() and cache_path.stat().st_size > 0:
            return cache_path.read_text(encoding="utf-8")
    except OSError:
        pass
    return None
