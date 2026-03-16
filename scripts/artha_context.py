#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/artha_context.py — Typed runtime context for Artha workflows.

Carries runtime state through the catch-up workflow. Constructed by
``build_context()`` at the start of a session. Updated by preflight,
pipeline, and middleware as the workflow progresses.

The LLM never sees this object directly.  It informs code-side decisions
(middleware gating, eviction tiers, checkpoint logic).  Consumers inspect
it for ``pressure``, ``is_degraded``, ``preflight_passed``, and
``steps_executed`` — the AI sees only the *effects* of these flags through
workflow file instructions.

Inspired by Pydantic AI's ``RunContextWrapper[T]`` and the OpenAI Agents SDK
``RunContextWrapper`` — a typed context object injected into every tool call,
cleanly separating "what the code knows" from "what the LLM sees".

Phase 3 of the Agentic Intelligence Improvement Plan (specs/agentic-improve.md).

Usage:
    from artha_context import build_context, ArthaContext, ContextPressure

    ctx = build_context(
        command="/catch-up",
        artha_dir=Path("."),
        env_manifest=manifest.to_dict(),
        preflight_results=check_results,
    )

Config flag: harness.agentic.context.enabled (default: true)
When disabled, ``build_context()`` returns a default context with
conservative (safe) defaults.

Ref: specs/agentic-improve.md Phase 3
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from pydantic import BaseModel, Field
    _PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYDANTIC_AVAILABLE = False
    BaseModel = object  # type: ignore[assignment,misc]

    class Field:  # type: ignore[no-redef]
        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            return kwargs.get("default", None)


try:
    from context_offloader import load_harness_flag as _load_harness_flag
except ImportError:  # pragma: no cover
    def _load_harness_flag(path: str, default: bool = True) -> bool:  # type: ignore[misc]
        return default


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ContextPressure(str, Enum):
    """Estimated token-pressure level for the current session."""
    GREEN = "green"        # < 50% of 200K context window
    YELLOW = "yellow"      # 50–70% — switch to flash compression
    RED = "red"            # 70–85% — P0 domains only
    CRITICAL = "critical"  # > 85% — emergency eviction mode


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class ConnectorStatus(BaseModel):
    """Per-connector health snapshot."""
    name: str
    online: bool
    last_error: str | None = None


# ---------------------------------------------------------------------------
# ArthaContext
# ---------------------------------------------------------------------------


class ArthaContext(BaseModel):
    """Typed runtime context for a single Artha session.

    Constructed once per session by ``build_context()``.  Middleware and
    scripts may read from it freely; they should not mutate it directly —
    use ``model_copy(update={...})`` to derive an updated context.

    Fields intentionally limited to what code actually needs to inspect.
    The LLM never receives a serialisation of this object in its context
    window.
    """

    # Workflow identity
    command: str = "unknown"
    artha_dir: str = ""

    # Environment
    environment: str = "local_mac"  # from detect_environment.py
    is_degraded: bool = False       # True if filesystem_writable=False or degradations present
    degradations: list[str] = []    # human-readable degradation strings

    # Preflight
    preflight_passed: bool = True

    # Connectors (populated after Step 4)
    connectors: list[ConnectorStatus] = []

    # Context pressure
    pressure: ContextPressure = ContextPressure.GREEN

    # Active domains (populated after Step 6 routing)
    active_domains: list[str] = []

    # Agentic capabilities (AR-4)
    session_recall_available: bool = False  # True when session_search.py found prior context

    # Workflow progress (step numbers appended as each step completes)
    steps_executed: list[int] = []

    # Session timing
    session_start: datetime = None  # type: ignore[assignment]

    if _PYDANTIC_AVAILABLE:
        model_config = {"arbitrary_types_allowed": True}

    def __init__(self, **data: Any) -> None:
        if "session_start" not in data or data.get("session_start") is None:
            data["session_start"] = datetime.now(timezone.utc)
        super().__init__(**data)

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def connectors_online(self) -> list[str]:
        """Names of connectors that are currently online."""
        return [c.name for c in self.connectors if c.online]

    @property
    def connectors_offline(self) -> list[str]:
        """Names of connectors that are currently offline."""
        return [c.name for c in self.connectors if not c.online]

    def health_summary(self) -> dict[str, Any]:
        """Compact serialisation suitable for inclusion in harness_metrics."""
        return {
            "command": self.command,
            "environment": self.environment,
            "pressure": self.pressure.value if isinstance(self.pressure, ContextPressure) else str(self.pressure),
            "preflight_passed": self.preflight_passed,
            "is_degraded": self.is_degraded,
            "connectors_online": self.connectors_online,
            "connectors_offline": self.connectors_offline,
            "active_domains": self.active_domains,
            "steps_executed": self.steps_executed,
        }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_context(
    command: str,
    artha_dir: Path | str | None = None,
    env_manifest: dict[str, Any] | None = None,
    preflight_results: list[Any] | None = None,
) -> ArthaContext:
    """Construct an ``ArthaContext`` from available session data.

    Args:
        command: The Artha command being executed (e.g. ``"/catch-up"``).
        artha_dir: Artha project root (defaults to ``ARTHA_DIR`` constant).
        env_manifest: Output dict from ``detect_environment.py``
            ``EnvironmentManifest.to_dict()``.  Missing keys use safe defaults.
        preflight_results: List of ``CheckResult`` objects from
            ``preflight.py``.  Any P0 failure sets ``preflight_passed=False``.

    Returns:
        ``ArthaContext`` with fields populated from the supplied data.
        All fields have safe defaults when data is absent, ensuring this
        function never raises for partial input.
    """
    # Feature flag check — return conservative defaults when disabled
    if not _load_harness_flag("agentic.context.enabled"):
        return ArthaContext(command=command, artha_dir=str(artha_dir or ""))

    env = env_manifest or {}
    capabilities = env.get("capabilities", {})
    degradations: list[str] = env.get("degradations", [])
    environment: str = env.get("environment", "local_mac")

    is_degraded = (
        not capabilities.get("filesystem_writable", True)
        or len(degradations) > 0
    )

    preflight_passed = True
    if preflight_results:
        for result in preflight_results:
            # CheckResult namedtuple / dataclass: has severity + ok / passed attributes
            severity = getattr(result, "severity", None) or getattr(result, "level", None)
            ok = getattr(result, "ok", None)
            if ok is None:
                ok = getattr(result, "passed", True)
            if severity == "P0" and not ok:
                preflight_passed = False
                break

    return ArthaContext(
        command=command,
        artha_dir=str(artha_dir) if artha_dir else "",
        environment=environment,
        is_degraded=is_degraded,
        degradations=degradations,
        preflight_passed=preflight_passed,
    )
