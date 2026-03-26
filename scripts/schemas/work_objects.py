"""
scripts/schemas/work_objects.py — Canonical Work Object dataclasses.

Implements §8.6: the six canonical objects that all domain processors
produce and all workflows consume. Objects carry provenance (source_domains,
last_updated) and a staleness marker per the lifecycle rules in §8.6.

Objects:
  WorkMeeting      — calendar event enriched with context
  WorkDecision     — structured decision record
  WorkCommitment   — tracked promise, from creation to closure
  WorkStakeholder  — person with org context + relationship state
  WorkArtifact     — work product (doc, PR, design, deck, etc.)
  WorkSource       — data reference (dashboard, query, report)

All objects are plain dataclasses — no ORM, no heavy dependencies.
Serialisation: use dataclasses.asdict() for JSON / YAML output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

class ObjectStatus(str, Enum):
    FRESH  = "fresh"
    STALE  = "stale"   # older than domain TTL; deprioritised in workflow output
    ACTIVE = "active"
    CLOSED = "closed"
    AT_RISK = "at_risk"


class CommitmentStatus(str, Enum):
    OPEN      = "open"
    DELIVERED = "delivered"
    DEFERRED  = "deferred"
    DROPPED   = "dropped"


class DecisionOutcome(str, Enum):
    PENDING  = "pending"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL  = "neutral"


class StakeholderRecency(str, Enum):
    RECENT = "recent"   # observed within the last 30 days
    STALE  = "stale"    # 31-90 days
    COLD   = "cold"     # >90 days


#: Valid event types for VisibilityEvent (§8.6, v2.3.0)
VISIBILITY_EVENT_TYPES = frozenset({
    "replied",
    "at_mentioned",
    "cited_doc",
    "invited_to_meeting",
    "presented_about",
})


@dataclass
class VisibilityEvent:
    """
    An immutable record of a senior stakeholder engaging with the user's work.

    Spec §8.6 lifecycle rule #6: visibility events are append-only and must
    never be edited or deleted — they form the evidentiary record for promotion
    narratives and Connect cycles.

    event_type options: replied | at_mentioned | cited_doc |
                        invited_to_meeting | presented_about
    context: max 100 chars, must be PII-sanitized before storage
    source_domain: the domain that captured this event (e.g. work-comms, work-calendar)
    """
    date: str                          # YYYY-MM-DD
    stakeholder_alias: str             # internal alias — NOT a real email or name
    event_type: str                    # see VISIBILITY_EVENT_TYPES
    context: str                       # max 100 chars, PII-sanitized
    source_domain: str                 # domain that captured the event


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# § 8.6 — Canonical Work Objects
# ---------------------------------------------------------------------------

@dataclass
class WorkMeeting:
    """
    A calendar event enriched with attendees, recurrence context,
    prep state, and carry-forward items.

    The recurring meeting series — not the individual instance — is
    the canonical unit. Individual instances reference series_id.
    """
    event_id: str                          # provider-specific unique ID
    title: str                             # redacted per §9.7
    start_dt: str                          # ISO-8601
    end_dt: str                            # ISO-8601
    duration_minutes: int
    is_recurring: bool = False
    series_id: Optional[str] = None        # links instances to their series
    instance_number: Optional[int] = None  # ordinal within the series
    attendee_ids: list[str] = field(default_factory=list)   # StakeholderIDs
    is_teams: bool = False
    readiness_score: Optional[int] = None  # 0-100 per §7.9
    open_threads: list[str] = field(default_factory=list)   # carry-forward items
    prep_state: str = "not_started"        # not_started | in_progress | ready
    source_domains: list[str] = field(default_factory=lambda: ["work-calendar"])
    last_updated: str = field(default_factory=_utcnow)
    status: ObjectStatus = ObjectStatus.ACTIVE


@dataclass
class WorkDecision:
    """
    A structured record of what was decided, when, by whom,
    with what evidence, and what outcome.

    Decisions are logged to state/work/work-decisions.md
    for pattern matching and career evidence.
    """
    decision_id: str                       # YYYY-MM-DD-NNN format
    context: str                           # brief description (redacted)
    decision_text: str                     # what was decided
    decided_at: str                        # ISO-8601
    decided_by: Optional[str] = None       # stakeholder ID or alias
    evidence_domains: list[str] = field(default_factory=list)
    outcome: DecisionOutcome = DecisionOutcome.PENDING
    pattern: Optional[str] = None         # escalation | timeline-push | architecture | …
    linked_meeting_id: Optional[str] = None
    source_domains: list[str] = field(default_factory=lambda: ["work-decisions"])
    last_updated: str = field(default_factory=_utcnow)
    status: ObjectStatus = ObjectStatus.ACTIVE


@dataclass
class WorkCommitment:
    """
    A promise made to or by the user — deliverables, follow-ups,
    deadlines, and closure state.

    Commitments track from creation to resolution.
    Overdue open commitments surface in /work sprint (§7.6, §11.5).
    """
    commitment_id: str
    title: str                             # redacted
    made_at: str                           # ISO-8601
    made_in: Optional[str] = None         # meeting ID or context description
    owner: str = "user"                    # user | manager | mutual
    due_date: Optional[str] = None         # YYYY-MM-DD or None
    status: CommitmentStatus = CommitmentStatus.OPEN
    last_referenced: Optional[str] = None  # ISO-8601
    linked_work_item: Optional[str] = None # ADO/Jira item ID if tracked
    source_domains: list[str] = field(default_factory=lambda: ["work-performance", "work-notes"])
    last_updated: str = field(default_factory=_utcnow)


@dataclass
class WorkStakeholder:
    """
    A person with org context, collaboration history, communication
    patterns, influence weight, and relationship trajectory.

    Richer than a flat people-cache entry — tracks the relationship
    rather than just the person.

    v2.3.0: visibility_events[] added (§8.6, Phase 2 Item 20).
    Append-only per lifecycle rule #6 — records of when senior people
    engaged with the user's work.  Feeds /work promo-case and /work connect-prep.
    """
    stakeholder_id: str                    # internal alias — NOT a real email
    display_name: str                      # redacted if needed
    org_context: Optional[str] = None      # team/org (not personal details)
    seniority_tier: Optional[str] = None   # ic | manager | director | vp | cvp | partner
    is_manager: bool = False
    is_skip: bool = False
    collab_frequency: str = "unknown"      # daily | weekly | monthly | rare
    last_interaction_at: Optional[str] = None  # ISO-8601
    recency: StakeholderRecency = StakeholderRecency.COLD
    influence_on_goals: list[str] = field(default_factory=list)  # goal IDs
    recommendation: Optional[str] = None   # e.g. "invite to architecture review"
    visibility_events: list[VisibilityEvent] = field(default_factory=list)  # §8.6 v2.3.0
    source_domains: list[str] = field(default_factory=lambda: ["work-people"])
    last_updated: str = field(default_factory=_utcnow)
    status: ObjectStatus = ObjectStatus.ACTIVE


@dataclass
class WorkArtifact:
    """
    A work product — document, PR, design, deck, wiki page —
    with authorship, recency, and project linkage.
    """
    artifact_id: str
    title: str                             # redacted
    artifact_type: str                     # document | pr | design | deck | wiki | other
    last_modified: str                     # ISO-8601
    modified_by_self: bool = False
    has_collaborators: bool = False
    project_context: Optional[str] = None  # inferred from title
    link: Optional[str] = None
    linked_meeting_id: Optional[str] = None
    source_domains: list[str] = field(default_factory=lambda: ["work-notes"])
    last_updated: str = field(default_factory=_utcnow)
    status: ObjectStatus = ObjectStatus.ACTIVE


@dataclass
class WorkSource:
    """
    A data reference — dashboard, query, report, portal —
    with provenance, purpose, and recency.

    Feeds /work decide with "where to find the data" and
    eliminates the "I know I saw that dashboard somewhere" problem (§7.7).
    """
    source_id: str
    title: str
    url: str
    answers: str                           # what question this source answers
    source_type: str = "other"             # dashboard | kusto-query | power-bi |
                                           # sharepoint-list | wiki | portal | other
    shared_by: Optional[str] = None        # stakeholder ID
    first_seen: Optional[str] = None       # YYYY-MM-DD
    last_referenced: Optional[str] = None  # YYYY-MM-DD
    tags: list[str] = field(default_factory=list)
    linked_projects: list[str] = field(default_factory=list)
    source_domains: list[str] = field(default_factory=lambda: ["work-sources"])
    last_updated: str = field(default_factory=_utcnow)
    status: ObjectStatus = ObjectStatus.ACTIVE
