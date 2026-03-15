#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/schemas/briefing.py — Pydantic schemas for Artha briefing output.

Phase 5 of the Deep Agents Architecture adoption (specs/deep-agents.md §5 Phase 5).

These schemas are used in Step 11b to validate the AI-generated briefing and
produce a structured JSON artifact at ``tmp/briefing_structured.json``.

Validation is always non-blocking: failures are logged to state/audit.md but
the briefing is presented to the user regardless.

Ref: specs/deep-agents.md Phase 5 | Artha.core.md Step 11b
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class AlertItem(BaseModel):
    """A single alert surfaced in the briefing."""

    severity: Literal["critical", "urgent", "standard", "info"]
    domain: str = Field(min_length=1, max_length=50)
    description: str = Field(min_length=1, max_length=200)
    score: int | None = Field(
        default=None,
        description="U×I×A score (0–27)",
        ge=0,
        le=27,
    )

    @field_validator("domain")
    @classmethod
    def domain_lowercase(cls, v: str) -> str:
        return v.lower().strip()


class DomainSummary(BaseModel):
    """Briefing contribution from a single domain."""

    domain: str = Field(min_length=1, max_length=50)
    bullet_points: Annotated[list[str], Field(max_length=5)] = Field(
        default_factory=list,
        description="1–5 bullet points for this domain's briefing section",
    )
    alert_count: int = Field(default=0, ge=0)

    @field_validator("domain")
    @classmethod
    def domain_lowercase(cls, v: str) -> str:
        return v.lower().strip()


class BriefingOutput(BaseModel):
    """Full structured representation of a standard Artha briefing.

    Written to ``tmp/briefing_structured.json`` after Step 11 synthesis.
    Consumed by: channel_push.py, /diff command, dashboard rebuild (Step 8h).
    """

    one_thing: str = Field(
        min_length=1,
        max_length=300,
        description="The single most important action or insight",
    )
    critical_alerts: list[AlertItem] = Field(
        default_factory=list,
        description="🔴 Critical alerts (deadline ≤7 days or immediate action required)",
    )
    urgent_alerts: list[AlertItem] = Field(
        default_factory=list,
        description="🟠 Urgent alerts (8–30 days or significant impact)",
    )
    domain_summaries: list[DomainSummary] = Field(
        default_factory=list,
        description="Per-domain briefing contributions",
    )
    open_items_added: int = Field(default=0, ge=0)
    open_items_closed: int = Field(default=0, ge=0)
    fna: str | None = Field(
        default=None,
        max_length=300,
        description="Fastest Next Action for the session",
    )
    pii_footer: str = Field(
        default="",
        description="PII guard footer line (e.g. '🔒 PII: N scanned · N redacted · N patterns')",
    )
    briefing_format: Literal["flash", "standard", "digest", "deep"] = Field(
        default="standard",
    )

    @field_validator("pii_footer")
    @classmethod
    def pii_footer_present(cls, v: str) -> str:
        # Warn but don't reject — validation is non-blocking
        return v

    def all_alerts(self) -> list[AlertItem]:
        """Return all alerts sorted by severity (critical first)."""
        severity_order = {"critical": 0, "urgent": 1, "standard": 2, "info": 3}
        return sorted(
            self.critical_alerts + self.urgent_alerts,
            key=lambda a: severity_order.get(a.severity, 9),
        )


class FlashBriefingOutput(BaseModel):
    """Compact structured representation of a flash briefing.

    Used when briefing_format == "flash" (session gap < 4 hours).
    """

    one_thing: str = Field(min_length=1, max_length=200)
    top_alert: AlertItem | None = Field(
        default=None,
        description="Single highest-priority alert (or None if no alerts)",
    )
    domain_summaries: list[DomainSummary] = Field(
        default_factory=list,
        description="Abbreviated per-domain summaries (≤2 bullets each)",
    )
    open_items_added: int = Field(default=0, ge=0)
    pii_footer: str = Field(default="")
    briefing_format: Literal["flash"] = Field(default="flash")
