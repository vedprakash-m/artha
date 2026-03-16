#!/usr/bin/env python3
# pii-guard: ignore-file — infrastructure module, no personal data
"""
scripts/schemas/__init__.py — Pydantic schema exports for Artha structured output.

Phase 5 of the Deep Agents Architecture adoption (specs/deep-agents.md §5 Phase 5).

Exposes all schema classes for import convenience:
    from schemas import BriefingOutput, AlertItem, DomainSummary
    from schemas import FlashBriefingOutput, SessionSummarySchema, DomainIndexCard

Ref: specs/deep-agents.md Phase 5
"""
from __future__ import annotations

from schemas.briefing import AlertItem, BriefingOutput, DomainSummary, FlashBriefingOutput
from schemas.domain_index import DomainIndexCard, DomainIndexEntry
from schemas.session import SessionSummarySchema

__all__ = [
    "AlertItem",
    "BriefingOutput",
    "DomainSummary",
    "FlashBriefingOutput",
    "DomainIndexCard",
    "DomainIndexEntry",
    "SessionSummarySchema",
]
