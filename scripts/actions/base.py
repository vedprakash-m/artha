#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/actions/base.py — ActionHandler Protocol and core dataclasses.

Defines the canonical types for the Artha Action Bus:
  - DomainSignal: immutable detection from skills/domain reasoning
  - ActionProposal: immutable proposal for human review
  - ActionResult: outcome of action execution
  - ActionHandler: Protocol that every handler module must satisfy

Architecture mirrors ConnectorHandler (scripts/connectors/base.py) —
structural subtyping (duck typing) applies; no subclassing required.

DESIGN INVARIANTS (enforced by dataclass properties and Protocol):
  1. DomainSignal and ActionProposal are frozen — immutable after creation.
  2. ActionResult is mutable (execution may fold in partial results).
  3. ActionHandler.execute() is the ONLY write path — all other methods
     (validate, dry_run, health_check) must be side-effect free.
  4. No external API calls in validate() — must be instant.
  5. Handlers must not catch and swallow exceptions — callers (executor)
     manage timeout and exception handling.

Ref: specs/act.md §4.1, §4.2
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DomainSignal:
    """An actionable detection produced by a skill or domain reasoning step.

    Signals are produced by deterministic skills during Steps 8–11 of the
    catch-up workflow — NOT by LLM inference on raw email content.
    This eliminates prompt injection risk at the action composition layer.

    Frozen: immutable after creation. Hashable (can be used as dict key).

    Fields:
        signal_type:  Standardised event type (e.g. "bill_due", "birthday_approaching").
        domain:       Originating Artha domain (e.g. "finance", "kids", "social").
        entity:       Human-readable name of the subject (e.g. "Metro Electric", "Rahul").
        urgency:      0–3; 0=informational, 3=emergency. Matches URGENCY scale.
        impact:       0–3; 0=minor, 3=critical. Matches IMPACT scale.
        source:       Originating skill/step (e.g. "skill:bill_due_tracker").
        metadata:     Signal-specific payload (amounts, dates, thread_ids, etc.).
        detected_at:  ISO-8601 UTC timestamp of detection.
    """
    signal_type: str
    domain: str
    entity: str
    urgency: int
    impact: int
    source: str
    metadata: Dict[str, Any]
    detected_at: str

    def __post_init__(self) -> None:
        """DEBT-012: Validate structural invariants at construction time."""
        if not isinstance(self.signal_type, str) or not self.signal_type.strip():
            raise ValueError(
                f"DomainSignal.signal_type must be a non-empty string; got {self.signal_type!r}"
            )
        if not isinstance(self.domain, str) or not self.domain.strip():
            raise ValueError(
                f"DomainSignal.domain must be a non-empty string; got {self.domain!r}"
            )
        if not isinstance(self.urgency, int) or not (0 <= self.urgency <= 3):
            raise ValueError(
                f"DomainSignal.urgency must be int 0–3; got {self.urgency!r}"
            )
        if not isinstance(self.impact, int) or not (0 <= self.impact <= 3):
            raise ValueError(
                f"DomainSignal.impact must be int 0–3; got {self.impact!r}"
            )
        if not isinstance(self.metadata, dict):
            raise ValueError(
                f"DomainSignal.metadata must be a dict; got {type(self.metadata).__name__}"
            )


@dataclass(frozen=True)
class ActionProposal:
    """An immutable proposal for a human-reviewable action.

    Created by ActionComposer or ActionExecutor.propose(). Persisted in
    the SQLite queue (state/actions.db) with status=PENDING.

    Frozen: all fields are set at creation; modifications produce a new
    ActionProposal (no in-place mutation). The approval/rejection/execution
    lifecycle is managed by ActionQueue status transitions, not by mutating
    the proposal itself.

    Fields:
        id:             UUIDv4 string (no sequential IDs — avoids info leakage).
        action_type:    Registry key (e.g. "email_send", "calendar_create").
        domain:         Originating Artha domain.
        title:          ≤120 char human-readable summary for approval UX.
        description:    Extended context shown in approval UX.
        parameters:     Handler-specific execution parameters.
        friction:       "low" | "standard" | "high" review level.
        min_trust:      Minimum trust level required (0 | 1 | 2).
        sensitivity:    "standard" | "high" | "critical" data sensitivity.
        reversible:     True if an undo action can be executed.
        undo_window_sec: Seconds after execution during which undo is valid.
                         None means irreversible (no undo window).
        expires_at:     ISO-8601 UTC; action auto-expires if not approved.
        source_step:    Catch-up step that originated this proposal.
        source_skill:   Skill name if skill-originated (None otherwise).
        linked_oi:      OI-NNN reference if related to an open item.
    """
    id: str
    action_type: str
    domain: str
    title: str
    description: str
    parameters: Dict[str, Any]
    friction: str                        # "low" | "standard" | "high"
    min_trust: int                       # 0 | 1 | 2
    sensitivity: str                     # "standard" | "high" | "critical"
    reversible: bool
    undo_window_sec: int | None
    expires_at: str | None
    source_step: str | None = None
    source_skill: str | None = None
    linked_oi: str | None = None


@dataclass
class ActionResult:
    """Outcome of action execution.

    Mutable: handlers may update fields after construction (e.g. to add
    partial result data).

    Fields:
        status:          "success" | "failure" | "partial".
        message:         Human-readable outcome ≤300 chars.
        data:            Handler-specific result data (e.g. message_id, event_id).
        reversible:      Whether this specific execution was reversible.
        reverse_action:  Pre-built undo proposal (None if irreversible).
        undo_deadline:   ISO-8601 UTC deadline for undo eligibility.
                         Computed as executed_at + undo_window_sec at execution time.
                         Stored in data dict under key "undo_deadline" for persistence.
    """
    status: str                           # "success" | "failure" | "partial"
    message: str                          # ≤300 chars
    data: Dict[str, Any] | None
    reversible: bool
    reverse_action: ActionProposal | None
    undo_deadline: str | None = None


# ---------------------------------------------------------------------------
# ActionHandler Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ActionHandler(Protocol):
    """Protocol for Artha action handler modules.

    Every module in scripts/actions/ must expose these four top-level
    functions. Structural subtyping applies — no subclassing required.
    This mirrors ConnectorHandler in scripts/connectors/base.py.

    SAFETY CONTRACT (must be preserved in all implementations):
      - validate():     side-effect free; no external API calls.
      - dry_run():      read-only; may call read-only external APIs only.
      - execute():      THE WRITE PATH — called only after approval + trust + PII checks.
      - health_check(): no side effects; returns False on any auth/connectivity failure.

    Exceptions in execute() are caught by ActionExecutor and logged as
    ActionResult(status="failure"). Handlers must never silently swallow
    exceptions — let them propagate to the executor.
    """

    def validate(self, proposal: ActionProposal) -> tuple[bool, str]:
        """Pre-execution parameter validation.

        Checks that all required parameters are present, have correct types,
        and that the action is structurally sound. Does NOT call external APIs.
        Called BEFORE enqueueing to provide early feedback on bad proposals.

        Returns:
            (True, "") if valid.
            (False, "Human-readable reason") if invalid.
        """
        ...

    def dry_run(self, proposal: ActionProposal) -> ActionResult:
        """Simulate execution without side effects.

        Returns an ActionResult describing what WOULD happen if the proposal
        were executed. External API calls are permitted only if read-only
        (e.g. Gmail draft creation, Calendar event preview, payment preview).

        Used for:
          - User preview in approval UX ("Here's the exact email that will be sent")
          - Test suites (all handler tests use dry_run by default)

        Must return ActionResult(status="success") if the proposal is
        structurally valid, even if the handler has not been tested live.
        Must return ActionResult(status="failure") only if the simulation
        itself encounters an error.
        """
        ...

    def execute(self, proposal: ActionProposal) -> ActionResult:
        """Execute the action. THIS IS THE WRITE PATH.

        Called ONLY by ActionExecutor.approve() after:
          1. User approval (or auto-approval at Trust Level 2 for non-floor actions)
          2. Trust Enforcer gate passes
          3. PII firewall re-scan passes
          4. Rate limiter check passes
          5. Read-only environment check passes

        Must be idempotent where possible: same proposal.id → same result.
        Use proposal.id as the idempotency key for external APIs.

        Timeout: enforced by ActionExecutor (default 30s).
        Exceptions: propagate to ActionExecutor; do NOT catch and swallow.

        Returns:
            ActionResult with status="success" | "failure" | "partial".
        """
        ...

    def health_check(self) -> bool:
        """Verify that handler's external dependencies are operational.

        Called during preflight (Step 0c). If this returns False, the
        action type is disabled for the current session with a P1 warning
        (non-blocking). The catch-up continues without this action handler.

        Must not have side effects. Must complete within 5 seconds.
        Returns True if handler is ready to execute; False otherwise.
        """
        ...

    def build_reverse_proposal(
        self, original: ActionProposal, result: "ActionResult"
    ) -> "ActionProposal | None":
        """Build an undo proposal for a successfully executed action.

        Called by ActionExecutor.undo() if the handler has this method.
        May return None if the action is not reversible (e.g., instruction_sheet).

        The reverse proposal is returned as a new ActionProposal in PENDING
        state with action_type and parameters set to undo the original.

        Args:
            original: The successfully executed proposal.
            result:   The ActionResult from execute(), which may contain
                      execution-specific data needed for undo (e.g. message_id).

        Returns:
            A new ActionProposal representing the undo action, or None.
        """
        ...


# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

# Valid status values for ActionQueue state machine (§2.4)
VALID_STATUSES = frozenset({
    "pending", "modifying", "approved", "rejected",
    "deferred", "executing", "succeeded", "failed",
    "expired", "cancelled",
})

# Terminal states: transitions out of these are bugs
TERMINAL_STATUSES = frozenset({
    "rejected", "expired", "succeeded", "failed", "cancelled",
})

# Valid friction levels
VALID_FRICTION = frozenset({"low", "standard", "high"})

# Valid sensitivity levels
VALID_SENSITIVITY = frozenset({"standard", "high", "critical"})

# Valid result statuses
VALID_RESULT_STATUSES = frozenset({"success", "failure", "partial"})

# Maximum title length (enforced at proposal creation)
MAX_TITLE_LENGTH = 120

# Maximum result message length (enforced at execution)
MAX_RESULT_MESSAGE_LENGTH = 300

# §2.5.2 Per-action-type required payload fields.
# Keys are action_type prefixes (matched with str.startswith).
# Each entry: list of required parameter keys.
_ACTION_REQUIRED_FIELDS: dict[str, list[str]] = {
    "scheduling": ["recipient", "datetime", "intent"],
    "financial":  ["payee", "amount", "category", "due_date"],
    "communications": ["recipient", "channel", "subject", "intent"],
}


def validate_proposal_fields(proposal: ActionProposal) -> tuple[bool, str]:
    """Validate ActionProposal field invariants.

    This is a shared pre-check used by ActionExecutor before enqueueing.
    Individual handlers also run their own parameter-level validation.

    Returns (True, "") if all invariants hold; (False, reason) otherwise.
    """
    if not proposal.id:
        return False, "proposal.id is required"
    if not proposal.action_type:
        return False, "proposal.action_type is required"
    if not proposal.domain:
        return False, "proposal.domain is required"
    if not proposal.title:
        return False, "proposal.title is required"
    if len(proposal.title) > MAX_TITLE_LENGTH:
        return False, f"proposal.title exceeds {MAX_TITLE_LENGTH} chars"
    if proposal.friction not in VALID_FRICTION:
        return False, f"proposal.friction must be one of {VALID_FRICTION}"
    if proposal.min_trust not in (0, 1, 2):
        return False, "proposal.min_trust must be 0, 1, or 2"
    if proposal.sensitivity not in VALID_SENSITIVITY:
        return False, f"proposal.sensitivity must be one of {VALID_SENSITIVITY}"
    if proposal.undo_window_sec is not None and proposal.undo_window_sec < 0:
        return False, "proposal.undo_window_sec must be non-negative"
    # §2.5.2 Per-action-type required payload field check
    for prefix, required in _ACTION_REQUIRED_FIELDS.items():
        if proposal.action_type.startswith(prefix):
            params = proposal.parameters or {}
            missing = [f for f in required if f not in params or params[f] is None]
            if missing:
                return False, (
                    f"action_type '{proposal.action_type}' missing required "
                    f"parameter(s): {', '.join(missing)}"
                )
            break
    return True, ""


__all__ = [
    "DomainSignal",
    "ActionProposal",
    "ActionResult",
    "ActionHandler",
    "VALID_STATUSES",
    "TERMINAL_STATUSES",
    "VALID_FRICTION",
    "VALID_SENSITIVITY",
    "VALID_RESULT_STATUSES",
    "MAX_TITLE_LENGTH",
    "MAX_RESULT_MESSAGE_LENGTH",
    "_ACTION_REQUIRED_FIELDS",
    "validate_proposal_fields",
]
