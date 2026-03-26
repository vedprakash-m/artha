"""
tests/work/test_work_objects.py — Canonical Work Object validation tests.

Validates scripts/schemas/work_objects.py (§8.6):
  - All 6 dataclasses instantiate correctly
  - Enum values are exhaustive and stable
  - Source domain tracking works
  - Serialization to dict is consistent
  - Default values are safe
  - Type discipline on required fields

Run: pytest tests/work/test_work_objects.py -v
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from schemas.work_objects import (  # type: ignore
    ObjectStatus,
    CommitmentStatus,
    DecisionOutcome,
    StakeholderRecency,
    WorkMeeting,
    WorkDecision,
    WorkCommitment,
    WorkStakeholder,
    WorkArtifact,
    WorkSource,
    VisibilityEvent,
    VISIBILITY_EVENT_TYPES,
)


# ===========================================================================
# Enum completeness tests
# ===========================================================================

class TestEnumCompleteness:

    def test_object_status_has_required_variants(self):
        assert ObjectStatus.ACTIVE
        assert ObjectStatus.STALE
        assert ObjectStatus.CLOSED
        # Must have at least 3 values
        assert len(ObjectStatus) >= 3

    def test_commitment_status_variants(self):
        statuses = {s.value for s in CommitmentStatus}
        # Must have an open state
        assert any("open" in v.lower() for v in statuses)
        # Must have terminal states (delivered, dropped, or deferred)
        assert any(
            "delivered" in v.lower() or "dropped" in v.lower() or "deferred" in v.lower()
            for v in statuses
        )

    def test_decision_outcome_variants(self):
        outcomes = {s.value for s in DecisionOutcome}
        # Must have a pending/undecided state
        assert any("pending" in v.lower() for v in outcomes)
        # Must have resolved states
        assert any(
            "positive" in v.lower() or "negative" in v.lower() or "neutral" in v.lower()
            for v in outcomes
        )

    def test_stakeholder_recency_variants(self):
        recencies = {s.value for s in StakeholderRecency}
        # Must have fresh/active markers
        assert any("recent" in v.lower() or "fresh" in v.lower() or "active" in v.lower() for v in recencies)
        # Must have stale/cold markers
        assert any("stale" in v.lower() or "cold" in v.lower() or "old" in v.lower() for v in recencies)


# ===========================================================================
# WorkMeeting tests
# ===========================================================================

class TestWorkMeeting:
    """WorkMeeting required fields: event_id, title, start_dt, end_dt, duration_minutes"""

    def _make(self, **kwargs):
        defaults = dict(
            event_id="MTG-001",
            title="Architecture Review",
            start_dt="2026-03-24T14:00:00Z",
            end_dt="2026-03-24T15:00:00Z",
            duration_minutes=60,
            source_domains=["work-calendar"],
        )
        defaults.update(kwargs)
        return WorkMeeting(**defaults)

    def test_instantiation(self):
        m = self._make()
        assert m.event_id == "MTG-001"
        assert m.title == "Architecture Review"
        assert m.duration_minutes == 60

    def test_default_status_is_active(self):
        m = self._make()
        assert m.status == ObjectStatus.ACTIVE

    def test_source_domains_is_list(self):
        m = self._make(source_domains=["work-calendar", "work-comms"])
        assert len(m.source_domains) == 2
        assert "work-calendar" in m.source_domains

    def test_duration_minutes_stored(self):
        m = self._make(duration_minutes=90)
        assert m.duration_minutes == 90

    def test_empty_attendee_ids_allowed(self):
        # Focus blocks / personal blocks can have empty attendee list
        m = self._make(attendee_ids=[])
        assert m.attendee_ids == []

    def test_multiple_attendee_ids(self):
        ids = ["alice", "bob", "carol"]
        m = self._make(attendee_ids=ids)
        assert len(m.attendee_ids) == 3
        assert "alice" in m.attendee_ids

    def test_is_recurring_default_false(self):
        m = self._make()
        assert m.is_recurring is False

    def test_series_id_optional(self):
        m = self._make(series_id="SER-042", is_recurring=True)
        assert m.series_id == "SER-042"


# ===========================================================================
# WorkDecision tests
# ===========================================================================

class TestWorkDecision:
    """WorkDecision required fields: decision_id, context, decision_text, decided_at"""

    def _make(self, **kwargs):
        defaults = dict(
            decision_id="DEC-001",
            context="Choice between phased vs. full fleet cutover",
            decision_text="Rollout sequencing for Platform A — go phased",
            decided_at="2026-03-24T14:00:00Z",
            source_domains=["work-projects"],
        )
        defaults.update(kwargs)
        return WorkDecision(**defaults)

    def test_instantiation(self):
        d = self._make()
        assert d.decision_id == "DEC-001"
        assert "phased" in d.decision_text

    def test_default_outcome_is_pending(self):
        d = self._make()
        assert d.outcome == DecisionOutcome.PENDING

    def test_multiple_source_domains(self):
        d = self._make(source_domains=["work-projects", "work-people", "work-notes"])
        assert len(d.source_domains) == 3

    def test_empty_context_allowed(self):
        d = self._make(context="")
        assert d.context == ""

    def test_outcome_can_be_set(self):
        d = self._make(outcome=DecisionOutcome.POSITIVE)
        assert d.outcome == DecisionOutcome.POSITIVE


# ===========================================================================
# WorkCommitment tests
# ===========================================================================

class TestWorkCommitment:
    """WorkCommitment required fields: commitment_id, title, made_at"""

    def _make(self, **kwargs):
        defaults = dict(
            commitment_id="COM-001",
            title="Deliver Platform A Plan formal signoffs by April 15",
            made_at="2026-03-24T14:00:00Z",
            source_domains=["work-performance"],
        )
        defaults.update(kwargs)
        return WorkCommitment(**defaults)

    def test_instantiation(self):
        c = self._make()
        assert c.commitment_id == "COM-001"
        assert "Platform A" in c.title

    def test_default_status_is_open(self):
        c = self._make()
        assert c.status == CommitmentStatus.OPEN

    def test_due_date_optional_string(self):
        c = self._make(due_date="2026-04-15")
        assert c.due_date == "2026-04-15"

    def test_due_date_defaults_none(self):
        c = self._make()
        assert c.due_date is None

    def test_status_can_be_delivered(self):
        c = self._make(status=CommitmentStatus.DELIVERED)
        assert c.status == CommitmentStatus.DELIVERED


# ===========================================================================
# WorkStakeholder tests
# ===========================================================================

class TestWorkStakeholder:
    """WorkStakeholder required fields: stakeholder_id, display_name"""

    def _make(self, **kwargs):
        defaults = dict(
            stakeholder_id="STK-001",
            display_name="Jane Kim",
            org_context="Senior Director, Engineering",
            source_domains=["work-people"],
        )
        defaults.update(kwargs)
        return WorkStakeholder(**defaults)

    def test_instantiation(self):
        s = self._make()
        assert s.stakeholder_id == "STK-001"
        assert s.display_name == "Jane Kim"

    def test_recency_default_is_cold(self):
        s = self._make()
        assert isinstance(s.recency, StakeholderRecency)
        assert s.recency == StakeholderRecency.COLD

    def test_is_manager_flag(self):
        s = self._make(is_manager=True)
        assert s.is_manager is True

    def test_is_manager_default_false(self):
        s = self._make()
        assert s.is_manager is False

    def test_collab_frequency_tracking(self):
        s = self._make(collab_frequency="weekly")
        assert s.collab_frequency == "weekly"

    def test_influence_on_goals_is_list(self):
        s = self._make(influence_on_goals=["GOAL-01", "GOAL-02"])
        assert len(s.influence_on_goals) == 2


# ===========================================================================
# WorkArtifact tests
# ===========================================================================

class TestWorkArtifact:
    """WorkArtifact required fields: artifact_id, title, artifact_type, last_modified"""

    def _make(self, **kwargs):
        defaults = dict(
            artifact_id="ART-001",
            title="202603-01 – Platform A LT Update.pptx",
            artifact_type="deck",
            last_modified="2026-03-01T00:00:00Z",
            source_domains=["work-notes"],
        )
        defaults.update(kwargs)
        return WorkArtifact(**defaults)

    def test_instantiation(self):
        a = self._make()
        assert a.artifact_id == "ART-001"
        assert "Platform A" in a.title

    def test_artifact_type_stored(self):
        a = self._make(artifact_type="document")
        assert a.artifact_type == "document"

    def test_link_default_none(self):
        a = self._make()
        assert a.link is None or isinstance(a.link, str)

    def test_modified_by_self_flag(self):
        a = self._make(modified_by_self=True)
        assert a.modified_by_self is True

    def test_project_context_optional(self):
        a = self._make(project_context="Platform A")
        assert a.project_context == "Platform A"


# ===========================================================================
# WorkSource tests
# ===========================================================================

class TestWorkSource:
    """WorkSource required fields: source_id, title, url, answers"""

    def _make(self, **kwargs):
        defaults = dict(
            source_id="SRC-001",
            title="Platform A Dashboard",
            url="https://example.com/platform-a",
            answers="What is the current Platform A buildout status?",
            source_domains=["work-sources"],
        )
        defaults.update(kwargs)
        return WorkSource(**defaults)

    def test_instantiation(self):
        s = self._make()
        assert s.source_id == "SRC-001"
        assert s.title == "Platform A Dashboard"

    def test_url_stored_as_string(self):
        s = self._make()
        assert isinstance(s.url, str)
        assert s.url.startswith("https://")

    def test_answers_field(self):
        s = self._make()
        assert "Platform A" in s.answers

    def test_source_type_default(self):
        s = self._make()
        assert isinstance(s.source_type, str)

    def test_tags_is_list(self):
        s = self._make(tags=["platform-a", "ramp", "buildout"])
        assert len(s.tags) == 3


# ===========================================================================
# Cross-object consistency tests
# ===========================================================================

class TestCrossObjectConsistency:

    _TS = "2026-03-24T14:00:00Z"

    def test_all_objects_have_last_updated(self):
        """Every canonical work object must track when it was last updated."""
        ts = self._TS
        objects = [
            WorkMeeting(
                event_id="M1", title="T",
                start_dt=ts, end_dt=ts, duration_minutes=0,
                source_domains=[], last_updated=ts,
            ),
            WorkDecision(
                decision_id="D1", context="C", decision_text="T",
                decided_at=ts, source_domains=[], last_updated=ts,
            ),
            WorkCommitment(
                commitment_id="C1", title="D",
                made_at=ts, source_domains=[], last_updated=ts,
            ),
            WorkStakeholder(
                stakeholder_id="S1", display_name="Alice",
                source_domains=[], last_updated=ts,
            ),
            WorkArtifact(
                artifact_id="A1", title="doc.docx",
                artifact_type="document", last_modified=ts,
                source_domains=[], last_updated=ts,
            ),
            WorkSource(
                source_id="SRC1", title="Dashboard",
                url="https://example.com", answers="Why?",
                source_domains=[], last_updated=ts,
            ),
        ]
        for obj in objects:
            assert hasattr(obj, "last_updated"), f"{type(obj).__name__} missing last_updated"
            assert obj.last_updated == ts

    def test_all_objects_have_source_domains(self):
        """Every canonical work object must record which domain(s) produced it."""
        ts = self._TS
        objects = [
            WorkMeeting(
                event_id="M1", title="T",
                start_dt=ts, end_dt=ts, duration_minutes=0,
                source_domains=["work-calendar"],
            ),
            WorkDecision(
                decision_id="D1", context="C", decision_text="T",
                decided_at=ts, source_domains=["work-projects"],
            ),
        ]
        for obj in objects:
            assert hasattr(obj, "source_domains")
            assert isinstance(obj.source_domains, list)

    def test_all_objects_have_status(self):
        """Objects with lifecycle state must have a status field."""
        ts = self._TS
        objects = [
            WorkMeeting(
                event_id="M1", title="T",
                start_dt=ts, end_dt=ts, duration_minutes=0,
                source_domains=[],
            ),
            WorkDecision(
                decision_id="D1", context="C", decision_text="T",
                decided_at=ts, source_domains=[],
            ),
            WorkStakeholder(
                stakeholder_id="S1", display_name="Alice",
                source_domains=[],
            ),
        ]
        for obj in objects:
            assert hasattr(obj, "status"), f"{type(obj).__name__} missing status"

    def test_all_objects_serialisable_to_dict(self):
        """asdict() must not raise — all field types must be dataclass-compatible."""
        ts = self._TS
        objects = [
            WorkMeeting(
                event_id="M1", title="T",
                start_dt=ts, end_dt=ts, duration_minutes=30,
                source_domains=["work-calendar"],
            ),
            WorkDecision(
                decision_id="D1", context="C", decision_text="T",
                decided_at=ts, source_domains=["work-projects"],
            ),
            WorkCommitment(
                commitment_id="C1", title="D",
                made_at=ts, source_domains=["work-performance"],
            ),
            WorkStakeholder(
                stakeholder_id="S1", display_name="Alice",
                source_domains=["work-people"],
            ),
            WorkArtifact(
                artifact_id="A1", title="doc.docx",
                artifact_type="document", last_modified=ts,
                source_domains=["work-notes"],
            ),
            WorkSource(
                source_id="SRC1", title="Dashboard",
                url="https://example.com", answers="Why?",
                source_domains=["work-sources"],
            ),
        ]
        for obj in objects:
            d = asdict(obj)
            assert isinstance(d, dict)
            assert len(d) > 0


# ===========================================================================
# VisibilityEvent tests (§8.6 v2.3.0)
# ===========================================================================

class TestVisibilityEvent:
    """VisibilityEvent dataclass and VISIBILITY_EVENT_TYPES frozenset."""

    def _make(self, **kwargs) -> VisibilityEvent:
        defaults = dict(
            date="2026-03-24",
            stakeholder_alias="mgr_alias_01",
            event_type="replied",
            context="Replied to thread on Platform A status",
            source_domain="work-comms",
        )
        defaults.update(kwargs)
        return VisibilityEvent(**defaults)

    def test_instantiation(self):
        ev = self._make()
        assert ev.date == "2026-03-24"
        assert ev.stakeholder_alias == "mgr_alias_01"
        assert ev.event_type == "replied"

    def test_event_types_frozenset_contains_required_values(self):
        required = {"replied", "at_mentioned", "cited_doc", "invited_to_meeting", "presented_about"}
        assert required.issubset(VISIBILITY_EVENT_TYPES)

    def test_event_types_is_frozenset(self):
        assert isinstance(VISIBILITY_EVENT_TYPES, frozenset)

    def test_stakeholder_field_on_work_stakeholder_defaults_empty(self):
        ts = "2026-03-24T00:00:00Z"
        s = WorkStakeholder(
            stakeholder_id="S1",
            display_name="Alice",
            source_domains=[],
            last_updated=ts,
        )
        assert hasattr(s, "visibility_events")
        assert isinstance(s.visibility_events, list)
        assert s.visibility_events == []

    def test_stakeholder_accepts_visibility_events(self):
        ts = "2026-03-24T00:00:00Z"
        ev = self._make()
        s = WorkStakeholder(
            stakeholder_id="S1",
            display_name="Alice",
            source_domains=[],
            last_updated=ts,
            visibility_events=[ev],
        )
        assert len(s.visibility_events) == 1
        assert s.visibility_events[0].event_type == "replied"

    def test_visibility_event_serialisable(self):
        ev = self._make()
        d = asdict(ev)
        assert d["date"] == "2026-03-24"
        assert d["event_type"] == "replied"
