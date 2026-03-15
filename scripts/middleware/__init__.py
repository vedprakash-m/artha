#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/middleware/__init__.py — Composable state-write middleware for Artha.

Provides a Protocol-based middleware stack for intercepting all state file
writes.  Cross-cutting concerns (PII guard, write guard, verification, audit
logging, rate limiting) are expressed as independent, stackable middleware
objects rather than inline Artha.core.md workflow steps.

Phase 4 of the Deep Agents Architecture adoption (specs/deep-agents.md §5 Phase 4).

Usage:
    from middleware import compose_middleware
    from middleware.pii_middleware import PIIMiddleware
    from middleware.write_guard import WriteGuardMiddleware
    from middleware.write_verify import WriteVerifyMiddleware
    from middleware.audit_middleware import AuditMiddleware

    stack = compose_middleware([
        PIIMiddleware(),
        WriteGuardMiddleware(),
        AuditMiddleware(),
    ])

    # Before a state write:
    approved_content = stack.before_write("finance", current, proposed)
    if approved_content is None:
        # Write is blocked
        return

    # Perform the actual write here...

    # After the write:
    stack.after_write("finance", path_to_file)

Config flag: harness.middleware.enabled (default: true)
When disabled, all writes pass through unmodified (before_write returns
proposed unchanged, after_write is a no-op).

Ref: specs/deep-agents.md Phase 4
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StateMiddleware(Protocol):
    """Protocol for state file read/write interceptors.

    Implementations must be stateless (or idempotent) so that the same
    instance can be safely reused across multiple write operations.
    """

    def before_write(
        self,
        domain: str,
        current_content: str,
        proposed_content: str,
        ctx: Any | None = None,
    ) -> str | None:
        """Called before a state file write.

        Args:
            domain: The domain being written (e.g. "finance").
            current_content: The existing file content (empty string if new).
            proposed_content: The content about to be written.
            ctx: Optional ``ArthaContext`` runtime context.  Existing
                middleware implementors that do not accept this parameter
                are unaffected — the argument defaults to ``None`` and is
                never required.  New middleware may inspect ``ctx.pressure``
                or ``ctx.is_degraded`` for context-aware gating.

        Returns:
            The (possibly modified) content to write, or ``None`` to **block**
            the write entirely.  A return value of ``None`` signals that the
            middleware has vetoed the operation.
        """
        ...  # pragma: no cover

    def after_write(self, domain: str, file_path: Path) -> None:
        """Called after a state file write succeeds.

        Args:
            domain: The domain that was written.
            file_path: Path to the file that was written.
        """
        ...  # pragma: no cover


class _PassthroughMiddleware:
    """No-op middleware used when the middleware feature flag is disabled."""

    def before_write(
        self,
        domain: str,
        current_content: str,
        proposed_content: str,
        ctx: Any | None = None,
    ) -> str | None:
        return proposed_content

    def after_write(self, domain: str, file_path: Path) -> None:
        pass


class _ComposedMiddleware:
    """A middleware that chains multiple middlewares into a single pipeline.

    ``before_write`` runs left-to-right: each middleware can modify or veto
    the content before passing it to the next.  As soon as any middleware
    returns ``None``, the chain short-circuits and the write is blocked.

    ``after_write`` runs right-to-left: post-write hooks fire in reverse
    order so that audit logging (first in the before chain) fires last in
    the after chain — creating a natural bracket pattern.
    """

    def __init__(self, middlewares: list) -> None:
        self._middlewares = middlewares

    def before_write(
        self,
        domain: str,
        current_content: str,
        proposed_content: str,
    ) -> str | None:
        content = proposed_content
        for mw in self._middlewares:
            result = mw.before_write(domain, current_content, content)
            if result is None:
                return None  # blocked
            content = result
        return content

    def after_write(self, domain: str, file_path: Path) -> None:
        for mw in reversed(self._middlewares):
            try:
                mw.after_write(domain, file_path)
            except Exception:  # noqa: BLE001
                # after_write must never propagate exceptions — degraded but safe
                pass


def compose_middleware(middlewares: list) -> "_ComposedMiddleware | _PassthroughMiddleware":
    """Chain multiple middleware objects into a single pipeline.

    Execution order:
    - ``before_write``: left-to-right (PII → WriteGuard → Audit)
    - ``after_write``: right-to-left (Audit → WriteVerify → Log)

    Args:
        middlewares: Ordered list of StateMiddleware implementors.

    Returns:
        A composed middleware object with the same interface as
        ``StateMiddleware``.  When the feature flag is disabled, returns a
        passthrough that never blocks writes.
    """
    from context_offloader import load_harness_flag  # noqa: PLC0415

    if not load_harness_flag("middleware.enabled"):
        return _PassthroughMiddleware()

    return _ComposedMiddleware(middlewares)


__all__ = [
    "StateMiddleware",
    "compose_middleware",
    "_PassthroughMiddleware",
    "_ComposedMiddleware",
]
