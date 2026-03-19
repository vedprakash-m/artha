"""
scripts/schemas/action.py — Pydantic schemas for ActionProposal and ActionResult.

Used for:
  - Validation of action parameters at the API boundary
  - Structured output from the briefing synthesis step
  - Cross-channel serialization (Telegram inline keyboards, email digest)

These schemas complement (not replace) the frozen dataclasses in
scripts/actions/base.py.  The dataclasses are the internal runtime
representation; these schemas are the external boundary validation layer.

Ref: specs/act.md §14
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    from pydantic import BaseModel, Field, field_validator, model_validator
    _PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PYDANTIC_AVAILABLE = False
    BaseModel = object  # type: ignore[misc,assignment]
    Field = lambda *a, **kw: None  # type: ignore[misc]

from actions.base import VALID_FRICTION, VALID_SENSITIVITY, VALID_STATUSES, MAX_TITLE_LENGTH


if _PYDANTIC_AVAILABLE:
    class ActionProposalSchema(BaseModel):
        """Pydantic validation schema for ActionProposal serialisation."""

        id: str = Field(..., description="UUIDv4 identifier")
        action_type: str = Field(..., description="Registry key (e.g. 'email_send')")
        domain: str = Field(..., description="Originating Artha domain")
        title: str = Field(
            ...,
            max_length=MAX_TITLE_LENGTH,
            description="Human-readable summary ≤120 chars",
        )
        description: str = Field(default="", description="Extended context for approval UX")
        parameters: Dict[str, Any] = Field(default_factory=dict)
        friction: str = Field(default="standard", description="low | standard | high")
        min_trust: int = Field(default=1, ge=0, le=2)
        sensitivity: str = Field(default="standard", description="standard | high | critical")
        reversible: bool = Field(default=False)
        undo_window_sec: Optional[int] = Field(default=None, ge=0)
        expires_at: Optional[str] = Field(default=None, description="ISO-8601 UTC")
        source_step: Optional[str] = Field(default=None)
        source_skill: Optional[str] = Field(default=None)
        linked_oi: Optional[str] = Field(default=None, pattern=r"OI-\d+")

        @field_validator("friction")
        @classmethod
        def validate_friction(cls, v: str) -> str:
            if v not in VALID_FRICTION:
                raise ValueError(f"friction must be one of {VALID_FRICTION}")
            return v

        @field_validator("sensitivity")
        @classmethod
        def validate_sensitivity(cls, v: str) -> str:
            if v not in VALID_SENSITIVITY:
                raise ValueError(f"sensitivity must be one of {VALID_SENSITIVITY}")
            return v

        @field_validator("title")
        @classmethod
        def validate_title_nonempty(cls, v: str) -> str:
            if not v.strip():
                raise ValueError("title must not be empty")
            return v.strip()

    class ActionResultSchema(BaseModel):
        """Pydantic validation schema for ActionResult serialisation."""

        status: str = Field(..., description="success | failure | partial")
        message: str = Field(..., max_length=300, description="Human-readable outcome")
        data: Optional[Dict[str, Any]] = Field(default=None)
        reversible: bool = Field(default=False)
        undo_deadline: Optional[str] = Field(default=None, description="ISO-8601 UTC")

        @field_validator("status")
        @classmethod
        def validate_status(cls, v: str) -> str:
            if v not in ("success", "failure", "partial"):
                raise ValueError("status must be 'success', 'failure', or 'partial'")
            return v

    class ActionQueueEntrySchema(BaseModel):
        """Schema for a serialised queue entry (as returned to channel adapters)."""

        id: str
        action_type: str
        domain: str
        title: str
        friction: str
        status: str
        created_at: str
        updated_at: str
        expires_at: Optional[str] = None
        result_status: Optional[str] = None
        result_message: Optional[str] = None

        @field_validator("status")
        @classmethod
        def validate_status(cls, v: str) -> str:
            if v not in VALID_STATUSES:
                raise ValueError(f"status must be one of {VALID_STATUSES}")
            return v

else:
    # Pydantic not available — provide stub classes that pass through
    class ActionProposalSchema:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class ActionResultSchema:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)

    class ActionQueueEntrySchema:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            for k, v in kwargs.items():
                setattr(self, k, v)


__all__ = [
    "ActionProposalSchema",
    "ActionResultSchema",
    "ActionQueueEntrySchema",
    "_PYDANTIC_AVAILABLE",
]
