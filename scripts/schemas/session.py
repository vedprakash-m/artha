#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/schemas/session.py — Pydantic schema for session summary validation.

Provides ``SessionSummarySchema`` for validating the output of
``session_summarizer.create_session_summary()``.

Phase 5 of the Deep Agents Architecture adoption (specs/deep-agents.md §5 Phase 5).

Ref: specs/deep-agents.md Phase 3 + Phase 5
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class SessionSummarySchema(BaseModel):
    """Validated schema for a session summary written to tmp/session_history_N.md.

    Used in Step 11b (and after domain deep-dives) to validate that the
    session_summarizer produced a structurally correct summary before it
    replaces conversation history in context.
    """

    session_intent: str = Field(min_length=1, max_length=200)
    command_executed: str = Field(min_length=1, max_length=100)
    key_findings: Annotated[list[str], Field(max_length=5)] = Field(
        default_factory=list,
        description="Top findings; max 5 entries, each ≤200 chars",
    )
    state_mutations: list[str] = Field(
        default_factory=list,
        description="State files modified (file paths)",
    )
    open_threads: list[str] = Field(
        default_factory=list,
        description="Unresolved items for future commands",
    )
    next_suggested: str = Field(default="", max_length=200)
    timestamp: str = Field(
        description="ISO-8601 timestamp of summarization",
    )
    context_before_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    context_after_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    trigger_reason: Literal["post_command", "proactive_threshold"] = Field(
        default="post_command",
    )

    @field_validator("key_findings", mode="before")
    @classmethod
    def truncate_findings(cls, findings: list) -> list:
        """Silently cap findings at 5 and truncate each to 200 chars.

        Runs in 'before' mode so it executes before pydantic's max_length
        constraint is evaluated, allowing graceful truncation instead of rejection.
        """
        if not isinstance(findings, list):
            return findings
        return [str(f)[:200] for f in findings[:5]]

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        # Loose check: must start with a 4-digit year
        if not v or not v[:4].isdigit():
            raise ValueError(f"Invalid ISO-8601 timestamp: {v!r}")
        return v
