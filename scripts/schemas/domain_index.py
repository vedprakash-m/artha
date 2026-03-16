#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/schemas/domain_index.py — Pydantic schema for domain index validation.

Validates the structured domain metadata produced by ``domain_index.build_domain_index()``.

Phase 5 of the Deep Agents Architecture adoption (specs/deep-agents.md §5 Phase 5).

Ref: specs/deep-agents.md Phase 2 + Phase 5
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DomainIndexEntry(BaseModel):
    """Metadata for a single domain in the progressive disclosure index."""

    domain: str = Field(min_length=1, max_length=50)
    status: Literal["ACTIVE", "STALE", "ARCHIVE", "UNKNOWN"] = Field(
        default="UNKNOWN",
    )
    last_activity_days: int | None = Field(
        default=None,
        ge=0,
        description="Days since last_activity (None if unknown)",
    )
    alerts: int = Field(default=0, ge=0)
    src: str = Field(default="", description="Relative path to source file")
    last_updated: str | None = Field(
        default=None,
        description="ISO date string of last state file update",
    )


class DomainIndexCard(BaseModel):
    """Full domain index produced by build_domain_index().

    Contains a compact text card for context injection and a dict of
    structured entries for programmatic use.
    """

    card_text: str = Field(
        description="Human-readable index card for AI context injection",
    )
    entries: dict[str, DomainIndexEntry] = Field(
        default_factory=dict,
        description="Structured metadata keyed by domain name",
    )
    total_domains: int = Field(default=0, ge=0)
    active_count: int = Field(default=0, ge=0)
    stale_count: int = Field(default=0, ge=0)
    archive_count: int = Field(default=0, ge=0)

    @classmethod
    def from_index_data(cls, card_text: str, index_data: dict) -> "DomainIndexCard":
        """Factory: build from domain_index.build_domain_index() output."""
        entries = {
            name: DomainIndexEntry(domain=name, **{
                k: v for k, v in meta.items()
                if k in DomainIndexEntry.model_fields
            })
            for name, meta in index_data.items()
        }
        status_counts: dict[str, int] = {}
        for entry in entries.values():
            status_counts[entry.status] = status_counts.get(entry.status, 0) + 1

        return cls(
            card_text=card_text,
            entries=entries,
            total_domains=len(entries),
            active_count=status_counts.get("ACTIVE", 0),
            stale_count=status_counts.get("STALE", 0),
            archive_count=status_counts.get("ARCHIVE", 0),
        )
